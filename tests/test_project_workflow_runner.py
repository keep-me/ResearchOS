import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project.checkpoint_service import apply_checkpoint_response
from packages.ai.project.amadeus_compat import build_run_directory, build_run_log_path
from packages.ai.project.gpu_lease_service import list_active_gpu_leases
from packages.ai.project.workflow_runner import (
    _build_literature_context_blocks,
    _build_writing_materials,
    _load_context,
    run_project_workflow,
)
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.domain.task_tracker import TaskPausedError
from packages.domain.schemas import PaperCreate
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import PaperRepository, ProjectRepository, ProjectResearchWikiRepository
from packages.agent.session.session_runtime import list_session_messages


def _configure_test_db(monkeypatch):
    import packages.storage.models  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)
    return session_local


def _seed_project_with_run(
    workflow_type: ProjectWorkflowType,
    *,
    workdir: str = "D:/tmp/researchos-workflow-runner",
) -> tuple[str, str]:
    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        paper_repo = PaperRepository(session)

        project = project_repo.create_project(
            name="Workflow Runner Test",
            description="A test project for workflow execution.",
            workdir=workdir,
        )
        paper_a = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id="2501.00001",
                title="Composable Research Agents",
                abstract="We study modular research agents and report strong performance on literature synthesis.",
                metadata={},
            )
        )
        paper_b = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id="2501.00002",
                title="Practical Auto-Science Loops",
                abstract="This work focuses on low-cost experiment loops and evaluation design for AI research systems.",
                metadata={},
            )
        )
        project_repo.add_paper_to_project(project_id=project.id, paper_id=paper_a.id)
        project_repo.add_paper_to_project(project_id=project.id, paper_id=paper_b.id)
        project_repo.create_repo(
            project_id=project.id,
            repo_url="https://github.com/example/research-workspace",
            local_path=f"{workdir}/research-workspace",
            is_workdir_repo=True,
        )
        run = project_repo.create_run(
            project_id=project.id,
            workflow_type=workflow_type,
            title=f"{workflow_type.value} run",
            prompt="Please focus on practical next steps for this project.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workdir=workdir,
        )
        run_directory = build_run_directory(workdir, run.id, remote=False)
        log_path = build_run_log_path(run_directory, remote=False)
        project_repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
        return project.id, run.id


def test_literature_review_workflow_persists_report(monkeypatch):
    _configure_test_db(monkeypatch)
    project_id, run_id = _seed_project_with_run(ProjectWorkflowType.literature_review)

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        assert stage == "project_literature_review"
        assert "Workflow Runner Test" in prompt
        return LLMResult(
            content=(
                "# Workflow Runner Test 文献综述\n\n"
                "## 项目背景与研究目标\n"
                "围绕自动科研流程建立更稳定的项目工作流。\n\n"
                "## 对本项目的下一步建议\n"
                "- 优先打通 literature_review 与 idea_discovery。\n"
            )
        )

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )

    result = run_project_workflow(run_id)

    assert result["run_id"] == run_id
    assert "文献综述" in result["markdown"]

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        reports = project_repo.list_project_reports(project_id)

        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# 文献综述报告")
        assert "# Workflow Runner Test 文献综述" in run.metadata_json["workflow_output_markdown"]
        assert run.metadata_json["generated_content_id"]
        assert run.result_path
        assert run.log_path
        assert Path(str(run.result_path)).exists()
        assert Path(str(run.log_path)).exists()
        assert any(
            str(item.get("path") or "").replace("\\", "/").endswith("reports/literature-review.md")
            for item in (run.metadata_json.get("artifact_refs") or [])
        )
        assert len(reports) == 1
        assert reports[0][0].title == "Workflow Runner Test 文献综述"


def test_literature_review_stage_checkpoint_resumes_without_regenerating_review(monkeypatch):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(ProjectWorkflowType.literature_review)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        repo.update_run(run_id, task_id="literature-review-checkpoint-task", metadata=metadata)

    summarize_stages: list[str] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        summarize_stages.append(stage)
        assert stage == "project_literature_review"
        return LLMResult(
            content=(
                "# Workflow Runner Test 文献综述\n\n"
                "## 当前研究脉络\n"
                "- 已完成阶段化 checkpoint 验证。\n"
            )
        )

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "deliver_review"
        assert metadata["stage_outputs"]["synthesize_evidence"]["content"].startswith("# Workflow Runner Test 文献综述")

    apply_checkpoint_response(run_id, action="approve", comment="deliver review")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.literature_review.value
    assert summarize_stages.count("project_literature_review") == 1

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# 文献综述报告")
        assert "# Workflow Runner Test 文献综述" in run.metadata_json["workflow_output_markdown"]
        assert run.result_path
        assert Path(str(run.result_path)).exists()


def test_idea_discovery_workflow_falls_back_without_model(monkeypatch):
    _configure_test_db(monkeypatch)
    project_id, run_id = _seed_project_with_run(ProjectWorkflowType.idea_discovery)

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_idea_discovery_literature":
            return LLMResult(content="# Landscape\n\n- baseline gap")
        if stage == "project_idea_discovery_novelty":
            return LLMResult(content="# Novelty\n\n- fallback path")
        if stage == "project_idea_discovery_review":
            return LLMResult(content="# Review\n\n- fallback review")
        raise AssertionError(f"unexpected stage: {stage}")

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        assert stage == "project_idea_discovery_ideas"
        assert "Landscape" in prompt
        return LLMResult(content="未配置模型，请先在系统设置中创建并激活 LLM 配置。")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.complete_json",
        _fake_complete_json,
    )

    result = run_project_workflow(run_id)

    assert result["run_id"] == run_id
    assert len(result["created_ideas"]) == 3

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        wiki_repo = ProjectResearchWikiRepository(session)
        run = project_repo.get_run(run_id)
        ideas = project_repo.list_ideas(project_id)
        wiki_nodes = wiki_repo.list_nodes(project_id)
        wiki_edges = wiki_repo.list_edges(project_id)

        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["llm_mode"] == "fallback"
        assert len(run.metadata_json["created_idea_ids"]) == 3
        assert len(ideas) == 3
        assert ideas[0].project_id == project_id
        assert sum(1 for node in wiki_nodes if node.node_type == "paper") == 2
        assert sum(1 for node in wiki_nodes if node.node_type == "idea") == 3
        assert len(wiki_edges) >= 3


def test_paper_writing_materials_include_previous_paper_workflow_outputs(monkeypatch):
    _configure_test_db(monkeypatch)
    project_id, run_id = _seed_project_with_run(ProjectWorkflowType.paper_writing)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project_repo.create_run(
            project_id=project_id,
            workflow_type=ProjectWorkflowType.paper_plan,
            title="paper_plan completed",
            prompt="build a manuscript outline",
            status=ProjectRunStatus.succeeded,
            active_phase="completed",
            summary="completed",
            metadata={
                "workflow_output_markdown": "# Paper Plan\n\n## Sections\n- Introduction\n- Method\n- Experiments\n",
            },
        )

    context = _load_context(run_id)
    materials = _build_writing_materials(context)

    assert "[既有论文流程产物]" in materials
    assert "paper_plan" in materials
    assert "Introduction" in materials


def test_paper_writing_materializes_standard_workspace_artifacts(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    project_id, run_id = _seed_project_with_run(
        ProjectWorkflowType.paper_writing,
        workdir=str(tmp_path),
    )

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_paper_writing_plan":
            return LLMResult(
                content=(
                    "# PAPER_PLAN\n\n"
                    "## Claims-Evidence Matrix\n"
                    "- Claim 1 -> Experiment A\n"
                )
            )
        if stage == "project_paper_writing_figure":
            return LLMResult(
                content=(
                    "# FIGURE_PLAN\n\n"
                    "- Fig 1: Main comparison table\n"
                    "- Fig 2: Ablation plot\n"
                )
            )
        if stage == "project_paper_writing_write":
            return LLMResult(
                content=(
                    "# Draft\n\n"
                    "## Abstract\nA draft abstract.\n\n"
                    "## Method\nA deterministic method.\n\n"
                    "## Experiments\nA deterministic experiment plan.\n"
                )
            )
        if stage == "project_paper_writing_compile":
            return LLMResult(
                content=(
                    "# PAPER_COMPILE\n\n"
                    "- Status: pending manual compile\n"
                    "- Missing toolchain: latexmk\n"
                )
            )
        if stage.startswith("project_paper_writing_improve_review_"):
            return LLMResult(
                content=(
                    "# Review\n\n"
                    "Score: 7.2\n\n"
                    "- Clarify contribution and experiment details.\n"
                )
            )
        if stage.startswith("project_paper_writing_improve_revise_"):
            return LLMResult(
                content=(
                    "# Final Draft\n\n"
                    "## Introduction\nPolished introduction.\n\n"
                    "## Method\nPolished method.\n\n"
                    "## Conclusion\nPolished conclusion.\n"
                )
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._resolve_paper_compile_command",
        lambda context: "",
    )

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.paper_writing.value
    run_root = Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id
    assert (run_root / "reports" / "PAPER_PLAN.md").exists()
    assert (run_root / "figures" / "latex_includes.tex").exists()
    assert (run_root / "paper" / "main.tex").exists()
    assert (run_root / "paper" / "references.bib").exists()


def test_paper_writing_improvement_requires_explicit_scores(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.paper_writing,
        workdir=str(tmp_path),
    )

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_paper_writing_plan":
            return LLMResult(content="# PAPER_PLAN\n\n## Sections\n- Introduction\n- Method")
        if stage == "project_paper_writing_figure":
            return LLMResult(content="# FIGURE_PLAN\n\n- Fig 1: Main result")
        if stage == "project_paper_writing_write":
            return LLMResult(content="# Draft\n\n## Method\nDraft body.")
        if stage == "project_paper_writing_compile":
            return LLMResult(content="# PAPER_COMPILE\n\n- Status: pending manual compile")
        if stage == "project_paper_writing_improve_review_1":
            return LLMResult(
                content=(
                    "# Review\n\n"
                    "Verdict: READY\n\n"
                    "Weaknesses:\n"
                    "1. Tighten the abstract argument.\n"
                )
            )
        if stage == "project_paper_writing_improve_review_2":
            return LLMResult(
                content=(
                    "# Review\n\n"
                    "Minor revisions only.\n\n"
                    "1. Fix cross-reference formatting.\n"
                )
            )
        if stage.startswith("project_paper_writing_improve_revise_"):
            return LLMResult(content="# Final Draft\n\n## Conclusion\nPolished body.")
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._resolve_paper_compile_command",
        lambda context: "",
    )

    run_project_workflow(run_id)

    run_root = Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id
    progression = (run_root / "reports" / "paper-score-progression.md").read_text(encoding="utf-8")
    metadata_payload = json.loads((run_root / "paper" / "improvement-metadata.json").read_text(encoding="utf-8"))

    assert "| 1 | 内容评审 | N/A | ready |" in progression
    assert "| 2 | 修订后复审 | N/A | almost |" in progression
    assert metadata_payload["score_round_one"] is None
    assert metadata_payload["score_round_two"] is None
    assert metadata_payload["verdict_round_one"] == "ready"
    assert metadata_payload["verdict_round_two"] == "almost"
    assert metadata_payload["action_items_round_one"] == ["Tighten the abstract argument."]
    assert metadata_payload["action_items_round_two"] == ["Fix cross-reference formatting."]

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["paper_improvement_scores"]["round_1"] is None
        assert metadata["paper_improvement_scores"]["round_2"] is None
        assert metadata["paper_improvement_verdicts"]["round_1"] == "ready"
        assert metadata["paper_improvement_verdicts"]["round_2"] == "almost"


def test_full_pipeline_stage_checkpoint_resumes_without_repeating_review(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.full_pipeline,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        metadata["execution_command"] = "python train.py --epochs 1"
        repo.update_run(run_id, task_id="full-pipeline-checkpoint-task", metadata=metadata)

    llm_stages: list[str] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        llm_stages.append(stage)
        if stage == "project_full_pipeline_gate":
            return LLMResult(content="# IDEA_REPORT\n\n## Recommended Idea\n\nA structured top idea.")
        if stage == "project_full_pipeline_auto_review":
            return LLMResult(content="# AUTO_REVIEW\n\n- score: 6/10\n- verdict: almost")
        if stage == "project_full_pipeline_handoff":
            return LLMResult(content="# Final Report\n\nEverything is ready.")
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._inspect_workspace_payload",
        lambda context: {"workspace_path": str(tmp_path), "tree": "workspace", "message": None},
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._run_workspace_command_for_context",
        lambda context, command, timeout_sec=None: {
            "command": command,
            "success": True,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        },
    )

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["type"] == "stage_transition"
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "implement_and_run"
        assert metadata["stage_outputs"]["review_prior_work"]["content"].startswith("# IDEA_REPORT")

    apply_checkpoint_response(run_id, action="approve", comment="continue")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.full_pipeline.value
    assert llm_stages.count("project_full_pipeline_gate") == 1
    assert "project_full_pipeline_auto_review" in llm_stages
    assert "project_full_pipeline_handoff" in llm_stages

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# 科研流程交付报告")
        assert "# Final Report" in run.metadata_json["workflow_output_markdown"]


def test_paper_writing_stage_checkpoints_resume_without_regenerating_draft(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.paper_writing,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        repo.update_run(run_id, task_id="paper-writing-checkpoint-task", metadata=metadata)

    llm_stages: list[str] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        llm_stages.append(stage)
        if stage == "project_paper_writing_plan":
            return LLMResult(content="# PAPER_PLAN\n\n## Sections\n- Introduction\n- Method")
        if stage == "project_paper_writing_figure":
            return LLMResult(content="# FIGURE_PLAN\n\n- Fig 1: Main result")
        if stage == "project_paper_writing_write":
            return LLMResult(content="# Draft\n\n## Method\nDraft body.")
        if stage == "project_paper_writing_compile":
            return LLMResult(content="# PAPER_COMPILE\n\n- Status: pending manual compile")
        if stage.startswith("project_paper_writing_improve_review_"):
            return LLMResult(content="# Review\n\nScore: 7.5\n\n- Tighten narrative.")
        if stage.startswith("project_paper_writing_improve_revise_"):
            return LLMResult(content="# Final Draft\n\n## Conclusion\nPolished body.")
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._resolve_paper_compile_command",
        lambda context: "",
    )

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "design_figures"
        assert "gather_materials" in (metadata.get("stage_outputs") or {})

    apply_checkpoint_response(run_id, action="approve", comment="continue figure plan")

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "draft_sections"
        assert metadata["stage_outputs"]["design_figures"]["content"].startswith("# FIGURE_PLAN")

    apply_checkpoint_response(run_id, action="approve", comment="write draft")

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "compile_manuscript"
        assert metadata["stage_outputs"]["draft_sections"]["content"].startswith("# Draft")

    apply_checkpoint_response(run_id, action="approve", comment="compile it")

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "polish_manuscript"
        assert metadata["stage_outputs"]["compile_manuscript"]["content"].startswith("# PAPER_COMPILE")

    apply_checkpoint_response(run_id, action="approve", comment="polish it")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.paper_writing.value
    assert llm_stages.count("project_paper_writing_plan") == 1
    assert llm_stages.count("project_paper_writing_figure") == 1
    assert llm_stages.count("project_paper_writing_write") == 1
    assert llm_stages.count("project_paper_writing_compile") == 1
    assert llm_stages.count("project_paper_writing_improve_review_1") == 1
    assert llm_stages.count("project_paper_writing_improve_revise_1") == 1
    assert llm_stages.count("project_paper_writing_improve_review_2") == 1
    assert llm_stages.count("project_paper_writing_improve_revise_2") == 1

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# 论文写作报告")
        assert "# Final Draft" in run.metadata_json["workflow_output_markdown"]


def test_idea_discovery_stage_checkpoints_resume_without_repeating_previous_phases(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.idea_discovery,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        repo.update_run(run_id, task_id="idea-discovery-checkpoint-task", metadata=metadata)

    llm_stages: list[str] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        llm_stages.append(stage)
        if stage == "project_idea_discovery_literature":
            return LLMResult(content="# Landscape\n\n- gap 1\n- gap 2")
        if stage == "project_idea_discovery_novelty":
            return LLMResult(content="# Novelty\n\n- Idea 1: high")
        if stage == "project_idea_discovery_review":
            return LLMResult(content="# Review\n\n- Score: 7/10")
        raise AssertionError(f"unexpected summarize stage: {stage}")

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        llm_stages.append(stage)
        assert stage == "project_idea_discovery_ideas"
        payload = {
            "ideas": [
                {"title": "Idea 1", "content": "## Hypothesis\nTest idea 1", "paper_refs": ["P1"]},
                {"title": "Idea 2", "content": "## Hypothesis\nTest idea 2", "paper_refs": ["P2"]},
            ]
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _fake_complete_json)

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "expand_directions"
        assert metadata["stage_outputs"]["collect_context"]["content"].startswith("# Landscape")

    apply_checkpoint_response(run_id, action="approve", comment="generate ideas")

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "verify_novelty"
        assert "Idea 1" in metadata["stage_outputs"]["expand_directions"]["content"]

    apply_checkpoint_response(run_id, action="approve", comment="continue novelty")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.idea_discovery.value
    assert llm_stages.count("project_idea_discovery_literature") == 1
    assert llm_stages.count("project_idea_discovery_ideas") == 1
    assert llm_stages.count("project_idea_discovery_novelty") == 1
    assert llm_stages.count("project_idea_discovery_review") == 1

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# Idea Discovery Report")


def test_novelty_check_stage_checkpoint_resumes_without_repeating_compare(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.novelty_check,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        repo.update_run(run_id, task_id="novelty-check-checkpoint-task", metadata=metadata)

    summarize_stages: list[str] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        summarize_stages.append(stage)
        if stage == "project_novelty_check_compare":
            return LLMResult(content="# Compare\n\n- Closest prior work: AnchorCoT baseline")
        if stage == "project_novelty_check_report":
            return LLMResult(content="# Novelty Report\n\n- Novelty risk is manageable.")
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "issue_novelty_report"
        assert metadata["stage_outputs"]["compare_prior_work"]["content"].startswith("# Compare")

    apply_checkpoint_response(run_id, action="approve", comment="issue novelty report")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.novelty_check.value
    assert summarize_stages.count("project_novelty_check_compare") == 1
    assert summarize_stages.count("project_novelty_check_report") == 1

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# 查新评估报告")
        assert "# Novelty Report" in run.metadata_json["workflow_output_markdown"]


def test_research_review_stage_checkpoint_resumes_without_repeating_review(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.research_review,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        repo.update_run(run_id, task_id="research-review-checkpoint-task", metadata=metadata)

    summarize_stages: list[str] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        summarize_stages.append(stage)
        if stage == "project_research_review":
            return LLMResult(content="# Review\n\n- Main weakness: experiment table is incomplete.")
        if stage == "project_research_review_verdict":
            return LLMResult(content="# Verdict\n\n- Recommendation: weak accept with revisions.")
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "deliver_verdict"
        assert metadata["stage_outputs"]["review_submission"]["content"].startswith("# Review")

    apply_checkpoint_response(run_id, action="approve", comment="deliver verdict")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.research_review.value
    assert summarize_stages.count("project_research_review") == 1
    assert summarize_stages.count("project_research_review_verdict") == 1


def test_research_review_reviewer_uses_workspace_agent(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.research_review,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        repo.update_run(run_id, reviewer_model="reviewer-agent-test-model")

    stream_calls: list[dict] = []
    preserved_sessions: list[str] = []

    def _fake_stream_chat(_messages, **kwargs):
        stream_calls.append(dict(kwargs))
        session_id = str(kwargs.get("session_id") or "")
        preserved_sessions.append(session_id)
        history = list_session_messages(session_id, limit=20)
        assert history
        latest_user = history[-1]["info"]
        assert latest_user["role"] == "user"
        assert latest_user["tools"]["write_workspace_file"] is False
        assert latest_user["tools"]["run_workspace_command"] is False
        assert latest_user["tools"]["question"] is False
        assert "read-only reviewer" in str(latest_user.get("system") or "")
        if len(stream_calls) == 1:
            content = "# Reviewer Notes\n\n- 已直接检查工作区中的评审材料与产物。"
        else:
            content = "# Verdict\n\n- 建议接收后小修。"
        return iter(
            [
                'event: assistant_message_id\ndata: {"message_id":"message_agent"}\n\n',
                f'event: text_delta\ndata: {json.dumps({"content": content}, ensure_ascii=False)}\n\n',
                "event: done\ndata: {}\n\n",
            ]
        )

    def _unexpected_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        raise AssertionError(f"reviewer stage should not fall back to summarize_text: {stage}")

    monkeypatch.setattr("packages.ai.project.workflow_runner.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("packages.ai.project.workflow_runner.delete_session", lambda _session_id: True)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _unexpected_summarize)

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.research_review.value
    assert "建议接收后小修" in result["markdown"]
    assert len(stream_calls) == 2
    assert all(call["mode"] == "build" for call in stream_calls)
    assert all(call["workspace_path"] == str(tmp_path) for call in stream_calls)
    assert all(call["model_override"] == "reviewer-agent-test-model" for call in stream_calls)
    assert preserved_sessions

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# 研究评审报告")
        assert "# Verdict" in run.metadata_json["workflow_output_markdown"]


def test_auto_review_loop_review_cycle_uses_reviewer_workspace_agent(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.auto_review_loop,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        repo.update_run(run_id, reviewer_model="reviewer-agent-test-model")

    stream_calls: list[dict] = []

    def _fake_stream_chat(_messages, **kwargs):
        stream_calls.append(dict(kwargs))
        session_id = str(kwargs.get("session_id") or "")
        history = list_session_messages(session_id, limit=20)
        assert history
        latest_user = history[-1]["info"]
        assert latest_user["role"] == "user"
        assert latest_user["tools"]["write_workspace_file"] is False
        assert latest_user["tools"]["run_workspace_command"] is False
        assert latest_user["tools"]["question"] is False
        assert "read-only reviewer" in str(latest_user.get("system") or "")
        payload = {
            "score": 7,
            "continue": False,
            "summary": "ready",
            "verdict": "ready",
            "issues": [],
            "next_actions": [],
            "raw_review": "Agent reviewer raw response",
            "pending_experiments": [],
        }
        return iter(
            [
                'event: assistant_message_id\ndata: {"message_id":"message_agent"}\n\n',
                f'event: text_delta\ndata: {json.dumps({"content": json.dumps(payload, ensure_ascii=False)}, ensure_ascii=False)}\n\n',
                "event: done\ndata: {}\n\n",
            ]
        )

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_auto_review_loop_plan":
            return LLMResult(content="# Plan\n\n- objective: improve quality")
        if stage == "project_auto_review_loop_execute_1":
            return LLMResult(content="# Execute\n\n- updated analysis and artifacts")
        raise AssertionError(f"unexpected summarize stage: {stage}")

    def _unexpected_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        raise AssertionError(f"review cycle should not fall back to complete_json: {stage}")

    monkeypatch.setattr("packages.ai.project.workflow_runner.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("packages.ai.project.workflow_runner.delete_session", lambda _session_id: False)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _unexpected_complete_json)

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.auto_review_loop.value
    assert result["iterations"][0]["review"]["verdict"] == "ready"
    assert len(stream_calls) == 1
    assert stream_calls[0]["mode"] == "build"
    assert stream_calls[0]["workspace_path"] == str(tmp_path)
    assert stream_calls[0]["model_override"] == "reviewer-agent-test-model"

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["stage_outputs"]["review_cycle"]["model_role"] == "reviewer"
        assert run.metadata_json["stage_outputs"]["review_cycle"]["role_template_id"] == "claude_code"


def test_auto_review_loop_persists_aris_state_files(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.auto_review_loop,
        workdir=str(tmp_path),
    )

    review_calls = 0

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_auto_review_loop_plan":
            return LLMResult(content="# Plan\n\n- objective: improve quality")
        return LLMResult(content=f"# Execute\n\n- stage: {stage}")

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        nonlocal review_calls
        review_calls += 1
        payload = {
            "score": 6 if review_calls == 1 else 7,
            "continue": False,
            "summary": "looks almost ready",
            "verdict": "almost",
            "issues": ["tighten analysis"],
            "next_actions": ["final polish"],
            "raw_review": "Full reviewer raw response",
            "pending_experiments": [],
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _fake_complete_json)

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.auto_review_loop.value
    assert result["markdown"].startswith("# 自动评审循环报告")
    run_root = Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id
    assert (run_root / "AUTO_REVIEW.md").exists()
    assert (run_root / "REVIEW_STATE.json").exists()
    assert "Full reviewer raw response" in (run_root / "AUTO_REVIEW.md").read_text(encoding="utf-8")
    assert '"status": "completed"' in (run_root / "REVIEW_STATE.json").read_text(encoding="utf-8")
    assert '"threadId": "auto-review-' in (run_root / "REVIEW_STATE.json").read_text(encoding="utf-8")
    assert (run_root / "reports" / "auto-review-loop.md").read_text(encoding="utf-8").startswith("# 自动评审循环报告")


def test_literature_review_prompt_includes_library_and_workspace_pdf_matches(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    project_id, run_id = _seed_project_with_run(
        ProjectWorkflowType.literature_review,
        workdir=str(tmp_path),
    )

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    (papers_dir / "AnchorCoT_spatial_reasoning.pdf").write_bytes(b"%PDF-1.4 anchorcot")

    with db.session_scope() as session:
        paper_repo = PaperRepository(session)
        extra_paper = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id="2503.12345",
                title="AnchorCoT for Spatial Reasoning",
                abstract="Anchor-guided process reward modeling improves faithful spatial reasoning.",
                metadata={"citation_count": 42, "source": "paper_library"},
            )
        )
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        assert run is not None
        project_repo.update_run(run_id, prompt="AnchorCoT spatial reasoning")
        assert extra_paper.id

    context = _load_context(run_id)
    blocks = _build_literature_context_blocks(context)
    joined = "\n\n".join(body for _label, body in blocks)

    assert "AnchorCoT for Spatial Reasoning" in joined
    assert "AnchorCoT_spatial_reasoning.pdf" in joined
    assert "faithful spatial reasoning" not in joined
    assert "以下只是一组论文索引元信息" in joined
    with db.session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        assert run is not None
        candidates = (run.metadata_json or {}).get("literature_candidates") or []
        assert any(item.get("title") == "AnchorCoT for Spatial Reasoning" for item in candidates)

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        lambda self, prompt, stage, model_override=None, variant_override=None, max_tokens=None, request_timeout=None: LLMResult(
            content="# 文献综述\n\n- 已整合多源文献上下文。"
        ),
    )

    result = run_project_workflow(run_id)

    assert result["run_id"] == run_id
    assert "多源文献上下文" in result["markdown"]


def test_paper_writing_auto_detects_compile_command_and_writes_round_pdfs(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.paper_writing,
        workdir=str(tmp_path),
    )

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_paper_writing_plan":
            return LLMResult(content="# PAPER_PLAN\n\n## Sections\n- Introduction\n- Method")
        if stage == "project_paper_writing_figure":
            return LLMResult(content="# FIGURE_PLAN\n\n- Fig 1: Main result")
        if stage == "project_paper_writing_write":
            return LLMResult(content="# Draft\n\n## Method\nDraft body.")
        if stage.startswith("project_paper_writing_improve_review_"):
            return LLMResult(content="# Review\n\nScore: 7.8\n\n- Improve framing.")
        if stage.startswith("project_paper_writing_improve_revise_"):
            return LLMResult(content="# Final Draft\n\n## Method\nRevised body.\n\n## Conclusion\nReady.")
        raise AssertionError(f"unexpected stage: {stage}")

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        if command == "latexmk --version":
            return {
                "command": command,
                "success": True,
                "exit_code": 0,
                "stdout": "Latexmk, John Collins",
                "stderr": "",
            }
        if command in {"pdflatex --version", "bibtex --version"}:
            return {
                "command": command,
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "not needed",
            }
        if "latexmk -pdf" in command:
            pdf_path = tmp_path / ".auto-researcher" / "aris-runs" / run_id / "paper" / "build" / "main.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.4 test")
            return {
                "command": command,
                "success": True,
                "exit_code": 0,
                "stdout": "compiled",
                "stderr": "",
            }
        raise AssertionError(f"unexpected workspace command: {command}")

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._run_workspace_command_for_context",
        _fake_run_workspace_command,
    )

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.paper_writing.value
    run_root = Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id
    state_text = (run_root / "PAPER_IMPROVEMENT_STATE.json").read_text(encoding="utf-8")
    assert (run_root / "paper" / "main_round0_original.pdf").exists()
    assert (run_root / "paper" / "main_round1.pdf").exists()
    assert (run_root / "paper" / "main_round2.pdf").exists()
    assert (run_root / "reports" / "PAPER_COMPILE.md").read_text(encoding="utf-8").find("latexmk -pdf") >= 0
    assert (run_root / "reports" / "PAPER_COMPILE_round1.md").exists()
    assert (run_root / "reports" / "PAPER_COMPILE_round2.md").exists()
    assert '"threadId": "paper-improvement-' in state_text


def test_auto_review_loop_almost_verdict_pauses_and_resumes_next_round(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.auto_review_loop,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        repo.update_run(run_id, task_id="auto-review-almost-task", metadata=metadata)

    summarize_stages: list[str] = []
    review_calls = 0

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        summarize_stages.append(stage)
        if stage == "project_auto_review_loop_plan":
            return LLMResult(content="# Plan\n\n- objective: improve quality")
        return LLMResult(content=f"# Execute\n\n- stage: {stage}")

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        nonlocal review_calls
        review_calls += 1
        if review_calls == 1:
            payload = {
                "score": 6,
                "summary": "almost ready",
                "verdict": "almost",
                "issues": ["tighten analysis"],
                "next_actions": ["run final pass"],
                "raw_review": "Round 1 raw review",
                "pending_experiments": [],
            }
        else:
            payload = {
                "score": 7,
                "summary": "ready",
                "verdict": "ready",
                "issues": [],
                "next_actions": [],
                "raw_review": "Round 2 raw review",
                "pending_experiments": [],
            }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _fake_complete_json)

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "execute_cycle"
        assert metadata.get("checkpoint_resume_iteration") is None
        assert len(metadata.get("iterations") or []) == 0
        assert metadata["stage_outputs"]["plan_cycle"]["content"].startswith("# Plan")

    apply_checkpoint_response(run_id, action="approve", comment="start round 1")

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "execute_cycle"
        assert metadata["checkpoint_resume_iteration"] == 2
        assert len(metadata["iterations"]) == 1

    apply_checkpoint_response(run_id, action="approve", comment="continue round 2")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.auto_review_loop.value
    assert review_calls == 2
    assert summarize_stages.count("project_auto_review_loop_plan") == 1

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        metadata = dict(run.metadata_json or {})
        assert metadata["workflow_output_markdown"].startswith("# 自动评审循环报告")
        assert len(metadata["iterations"]) == 2
        assert metadata["iterations"][0]["review"]["verdict"] == "almost"
        assert metadata["iterations"][1]["review"]["verdict"] == "ready"


def test_run_experiment_stage_checkpoint_resumes_without_reinspecting_workspace(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.run_experiment,
        workdir=str(tmp_path),
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["human_checkpoint_enabled"] = True
        metadata["execution_command"] = "python train.py --epochs 1"
        repo.update_run(run_id, task_id="run-experiment-checkpoint-task", metadata=metadata)

    inspect_calls = 0
    execute_calls = 0
    summarize_stages: list[str] = []

    def _fake_inspect_workspace_payload(context, workspace_path_override=None):
        nonlocal inspect_calls
        inspect_calls += 1
        return {
            "workspace_path": workspace_path_override or str(tmp_path),
            "tree": "workspace\n- train.py",
            "message": "workspace ready",
        }

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        nonlocal execute_calls
        execute_calls += 1
        return {
            "command": command,
            "success": True,
            "exit_code": 0,
            "stdout": "train ok",
            "stderr": "",
        }

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        summarize_stages.append(stage)
        assert stage == "project_run_experiment_summary"
        return LLMResult(content="# 实验总结\n\n- 实验执行成功。")

    monkeypatch.setattr("packages.ai.project.workflow_runner._inspect_workspace_payload", _fake_inspect_workspace_payload)
    monkeypatch.setattr("packages.ai.project.workflow_runner._run_workspace_command_for_context", _fake_run_workspace_command)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)

    with pytest.raises(TaskPausedError):
        run_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "execute_experiment"
        assert metadata["stage_outputs"]["inspect_workspace"]["inspection"]["workspace_path"] == str(tmp_path)

    assert inspect_calls == 1
    assert execute_calls == 0

    apply_checkpoint_response(run_id, action="approve", comment="run experiment")
    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.run_experiment.value
    assert inspect_calls == 1
    assert execute_calls == 1
    assert summarize_stages.count("project_run_experiment_summary") == 1

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        assert run.metadata_json["workflow_output_markdown"].startswith("# 实验运行报告")
        assert "# 实验总结" in run.metadata_json["workflow_output_markdown"]


def test_run_experiment_local_wraps_command_with_claude_runtime_environment(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.run_experiment,
        workdir=str(tmp_path),
    )

    code_dir = tmp_path / "src" / "exp"
    code_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text(
        "## Local Environment\n"
        "- Activate: `conda activate anchorcot`\n"
        "- Code dir: `src/exp`\n",
        encoding="utf-8",
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["execution_command"] = "python train.py --epochs 1"
        repo.update_run(run_id, metadata=metadata)

    captured: dict[str, str | None] = {}

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        lambda self, prompt, stage, model_override=None, variant_override=None, max_tokens=None, request_timeout=None: LLMResult(
            content="# 实验总结\n\n- 本地实验已启动并完成。"
        ),
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._inspect_workspace_payload",
        lambda context, workspace_path_override=None: {
            "workspace_path": workspace_path_override or str(tmp_path),
            "tree": "workspace",
            "message": None,
        },
    )

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        captured["command"] = command
        captured["workspace_path_override"] = workspace_path_override
        return {
            "command": command,
            "success": True,
            "exit_code": 0,
            "stdout": "local run ok",
            "stderr": "",
        }

    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._run_workspace_command_for_context",
        _fake_run_workspace_command,
    )

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.run_experiment.value
    assert captured["command"] == "conda activate anchorcot && python train.py --epochs 1"
    assert Path(str(captured["workspace_path_override"])) == code_dir

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["execution_command"] == "python train.py --epochs 1"
        assert metadata["effective_execution_command"] == "conda activate anchorcot && python train.py --epochs 1"
        assert metadata["execution_workspace"] == str(code_dir)
        assert metadata["runtime_environment"]["code_dir"] == "src/exp"
        assert metadata["runtime_environment"]["command_workspace_path"] == str(code_dir)


def test_experiment_audit_workflow_persists_audit_artifacts(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    workspace = Path(tmp_path)
    (workspace / "results").mkdir(parents=True, exist_ok=True)
    (workspace / "configs").mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "sections").mkdir(parents=True, exist_ok=True)
    (workspace / "eval_metric.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def load_metrics():\n"
        "    return Path('results/metrics.json').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    (workspace / "results" / "metrics.json").write_text(
        json.dumps({"accuracy": 0.91, "normalized_score": 0.97}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (workspace / "EXPERIMENT_TRACKER.md").write_text(
        "# EXPERIMENT_TRACKER\n\n- main metric: accuracy=0.91\n- seeds: 1\n",
        encoding="utf-8",
    )
    (workspace / "paper" / "sections" / "experiments.tex").write_text(
        "\\section{Experiments}\nWe report 0.91 accuracy on the main setting.\n",
        encoding="utf-8",
    )
    (workspace / "configs" / "eval.yaml").write_text(
        "dataset: anchorcot-dev\nmetric: accuracy\nnormalization: per_task\n",
        encoding="utf-8",
    )

    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.experiment_audit,
        workdir=str(workspace),
    )

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        assert stage == "project_experiment_audit_review"
        assert "results/metrics.json" in prompt
        payload = {
            "overall_verdict": "WARN",
            "integrity_status": "warn",
            "evaluation_type": "mixed",
            "summary": "主结果存在，但单 seed 和归一化口径仍需补充说明。",
            "checks": {
                "gt_provenance": {
                    "status": "PASS",
                    "evidence": ["configs/eval.yaml:1"],
                    "details": "评测数据来源可定位。",
                },
                "score_normalization": {
                    "status": "WARN",
                    "evidence": ["results/metrics.json:3"],
                    "details": "normalized_score 的分母定义未说明。",
                },
                "result_existence": {
                    "status": "PASS",
                    "evidence": ["results/metrics.json:2"],
                    "details": "accuracy 指标文件存在。",
                },
                "dead_code": {
                    "status": "PASS",
                    "evidence": ["eval_metric.py:3"],
                    "details": "评测脚本实际读取结果文件。",
                },
                "scope": {
                    "status": "WARN",
                    "evidence": ["EXPERIMENT_TRACKER.md:4"],
                    "details": "当前只记录了单 seed。",
                },
                "eval_type": {
                    "status": "PASS",
                    "evidence": ["paper/sections/experiments.tex:2"],
                    "details": "属于 mixed 类型评测。",
                },
            },
            "action_items": ["补充至少 3 个 seed 的结果", "解释 normalized_score 的定义"],
            "claims": [
                {
                    "id": "C1",
                    "impact": "needs_qualifier",
                    "details": "泛化表述需要收窄到当前设置。",
                }
            ],
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _fake_complete_json)

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.experiment_audit.value
    assert result["overall_verdict"] == "WARN"
    assert result["integrity_status"] == "warn"
    assert result["evaluation_type"] == "mixed"
    assert result["markdown"].startswith("# Experiment Audit Report")

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        artifact_refs = list(metadata.get("artifact_refs") or [])

        assert run.status == ProjectRunStatus.succeeded
        assert metadata["overall_verdict"] == "WARN"
        assert metadata["integrity_status"] == "warn"
        assert metadata["evaluation_type"] == "mixed"
        assert "result_files" in metadata["audit_inventory"]
        assert any(str(item.get("relative_path") or "").replace("\\", "/").endswith("EXPERIMENT_AUDIT.md") for item in artifact_refs)
        assert any(str(item.get("relative_path") or "").replace("\\", "/").endswith("EXPERIMENT_AUDIT.json") for item in artifact_refs)
        assert any(str(item.get("relative_path") or "").replace("\\", "/").endswith("reports/experiment-audit.md") for item in artifact_refs)


def test_auto_review_loop_wraps_command_with_claude_runtime_environment(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.auto_review_loop,
        workdir=str(tmp_path),
    )

    code_dir = tmp_path / "iterations"
    code_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text(
        "## Local Environment\n"
        "- Activate: `conda activate review-env`\n"
        "- Code dir: `iterations`\n",
        encoding="utf-8",
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["execution_command"] = "python loop.py --round 1"
        repo.update_run(run_id, metadata=metadata)

    captured: dict[str, str | None] = {}

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_auto_review_loop_plan":
            return LLMResult(content="# Plan\n\n- objective: improve quality")
        raise AssertionError(f"unexpected summarize stage: {stage}")

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        payload = {
            "score": 7,
            "continue": False,
            "summary": "ready",
            "verdict": "ready",
            "issues": [],
            "next_actions": [],
            "raw_review": "looks good",
            "pending_experiments": [],
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        captured["command"] = command
        captured["workspace_path_override"] = workspace_path_override
        return {
            "command": command,
            "success": True,
            "exit_code": 0,
            "stdout": "review loop ok",
            "stderr": "",
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _fake_complete_json)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._run_workspace_command_for_context",
        _fake_run_workspace_command,
    )

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.auto_review_loop.value
    assert captured["command"] == "conda activate review-env && python loop.py --round 1"
    assert Path(str(captured["workspace_path_override"])) == code_dir

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["effective_execution_command"] == "conda activate review-env && python loop.py --round 1"
        assert metadata["execution_workspace"] == str(code_dir)
        assert metadata["iterations"][0]["execution"]["effective_command"] == "conda activate review-env && python loop.py --round 1"
        assert metadata["iterations"][0]["execution"]["command_workspace_path"] == str(code_dir)


def test_full_pipeline_wraps_command_with_claude_runtime_environment(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    _, run_id = _seed_project_with_run(
        ProjectWorkflowType.full_pipeline,
        workdir=str(tmp_path),
    )

    code_dir = tmp_path / "pipeline-src"
    code_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text(
        "## Local Environment\n"
        "- Activate: `conda activate pipeline-env`\n"
        "- Code dir: `pipeline-src`\n",
        encoding="utf-8",
    )

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        metadata["execution_command"] = "python train.py --epochs 1"
        repo.update_run(run_id, metadata=metadata)

    captured: dict[str, str | None] = {}

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        if stage == "project_full_pipeline_gate":
            return LLMResult(content="# IDEA_REPORT\n\n## Recommended Idea\n\nA structured top idea.")
        if stage == "project_full_pipeline_auto_review":
            return LLMResult(content="# AUTO_REVIEW\n\n- score: 7/10\n- verdict: ready")
        if stage == "project_full_pipeline_handoff":
            return LLMResult(content="# Final Report\n\nEverything is ready.")
        raise AssertionError(f"unexpected summarize stage: {stage}")

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        captured["command"] = command
        captured["workspace_path_override"] = workspace_path_override
        return {
            "command": command,
            "success": True,
            "exit_code": 0,
            "stdout": "pipeline ok",
            "stderr": "",
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._inspect_workspace_payload",
        lambda context, workspace_path_override=None: {
            "workspace_path": workspace_path_override or str(tmp_path),
            "tree": "workspace",
            "message": None,
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._run_workspace_command_for_context",
        _fake_run_workspace_command,
    )

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.full_pipeline.value
    assert captured["command"] == "conda activate pipeline-env && python train.py --epochs 1"
    assert Path(str(captured["workspace_path_override"])) == code_dir

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["effective_execution_command"] == "conda activate pipeline-env && python train.py --epochs 1"
        assert metadata["execution_workspace"] == str(code_dir)
        assert metadata["runtime_environment"]["code_dir"] == "pipeline-src"


def test_run_experiment_remote_wraps_command_with_claude_runtime_environment(monkeypatch):
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="Remote Runtime Environment Test",
            description="validate CLAUDE.md runtime env on remote launches",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/anchorcot",
        )
        target = project_repo.ensure_default_target(project.id)
        assert target is not None
        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.run_experiment,
            title="remote runtime env run",
            prompt="Launch a remote runtime environment experiment.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={"execution_command": "python train.py --epochs 1"},
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        project_repo.update_run(
            run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata={"execution_command": "python train.py --epochs 1"},
        )
        run_id = run.id

    captured: dict[str, str | None] = {}

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        lambda self, prompt, stage, model_override=None, variant_override=None, max_tokens=None, request_timeout=None: LLMResult(
            content="# 远程实验启动摘要\n\n- 实验已在后台启动。"
        ),
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._read_workspace_text_file",
        lambda context, relative_path, max_chars=20000: (
            "## Remote Server\n"
            "- Activate: `eval \"$(/opt/conda/bin/conda shell.bash hook)\" && conda activate anchorcot`\n"
            "- Code dir: `experiments/core`\n"
        ),
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.build_remote_overview",
        lambda server_entry, requested_path, *, depth, max_entries: {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- train.py\n",
            "files": ["train.py"],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_terminal_result",
        lambda server_entry, *, path, command, timeout_sec: {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "tester@gpu.example.com", command],
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "success": True,
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_probe_gpus",
        lambda server_entry, *, path: {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [],
            "reason": None,
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_prepare_run_environment",
        lambda server_entry, *, path, run_directory, session_name: {
            "workspace_path": path,
            "run_directory": run_directory,
            "execution_workspace": f"{run_directory}/workspace",
            "session_name": session_name,
            "isolation_mode": "git_worktree",
            "git_available": True,
            "git_branch": "main",
            "git_head": "abc123",
            "created_worktree": True,
            "prepare_steps": [],
        },
    )

    def _fake_launch_screen_job(
        server_entry,
        *,
        path,
        session_name,
        command,
        log_path,
        env_vars=None,
        timeout_sec=30,
    ):
        captured["path"] = path
        captured["command"] = command
        return {
            "workspace_path": path,
            "session_name": session_name,
            "log_path": log_path,
            "command": command,
            "env_vars": dict(env_vars or {}),
            "launch_command": f"screen -dmS {session_name} bash -lc 'cd {path} && {command}'",
            "already_running": False,
            "launched": True,
            "success": True,
            "stdout": "",
            "stderr": "",
            "sessions": [{"pid": 4312, "name": session_name, "state": "Detached"}],
            "screen_list_stdout": "",
            "screen_list_stderr": "",
        }

    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_launch_screen_job",
        _fake_launch_screen_job,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_list_screen_sessions",
        lambda server_entry, *, session_name=None, session_prefix=None: {
            "command": "screen -ls",
            "stdout": "4312.session (Detached)",
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": [{"pid": 4312, "name": session_name or "session", "state": "Detached"}],
            "session_count": 1,
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_capture_screen_session",
        lambda server_entry, *, session_name, lines=80: {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": "epoch=1",
            "stderr": "",
            "success": True,
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_write_file",
        lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        },
    )

    result = run_project_workflow(run_id)

    expected_command = (
        'eval "$(/opt/conda/bin/conda shell.bash hook)" && conda activate anchorcot && python train.py --epochs 1'
    )
    assert result["workflow_type"] == ProjectWorkflowType.run_experiment.value
    assert captured["command"] == expected_command
    assert str(captured["path"]).endswith("/workspace/experiments/core")

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["effective_execution_command"] == expected_command
        assert metadata["execution_workspace"].endswith("/workspace/experiments/core")
        assert metadata["runtime_environment"]["code_dir"] == "experiments/core"
        assert metadata["execution_result"]["runtime_environment"]["command_workspace_path"].endswith(
            "/workspace/experiments/core"
        )


def test_run_experiment_remote_launches_screen_session(monkeypatch):
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="Remote Experiment Test",
            description="validate remote screen launch path",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/anchorcot",
        )
        target = project_repo.ensure_default_target(project.id)
        assert target is not None
        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.run_experiment,
            title="remote experiment run",
            prompt="Launch a remote smoke experiment.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={
                "execution_command": "python train.py --epochs 1",
            },
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        project_repo.update_run(
            run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata={
                "execution_command": "python train.py --epochs 1",
            },
        )
        run_id = run.id

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        assert stage == "project_run_experiment_summary"
        assert "远程会话" in prompt
        return LLMResult(
            content=(
                "# 远程实验启动摘要\n\n"
                "- 实验已在后台启动\n"
                "- 建议后续通过 monitor_experiment 持续观察日志与指标\n"
            )
        )

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- train.py\n- configs/\n",
            "files": ["train.py", "configs/default.yaml"],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        }

    def _fake_remote_terminal_result(server_entry, *, path, command, timeout_sec):
        if command == "python --version":
            stdout = "Python 3.11.9"
        elif command == "git --version":
            stdout = "git version 2.45.0"
        elif command == "uv --version":
            stdout = "uv 0.6.0"
        else:
            stdout = ""
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_prepare_run_environment(server_entry, *, path, run_directory, session_name):
        return {
            "workspace_path": path,
            "run_directory": run_directory,
            "execution_workspace": f"{run_directory}/workspace",
            "session_name": session_name,
            "isolation_mode": "git_worktree",
            "git_available": True,
            "git_branch": "main",
            "git_head": "abc123",
            "created_worktree": True,
            "prepare_steps": [],
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {
                    "index": 0,
                    "name": "A100",
                    "memory_used_mb": 1200,
                    "memory_total_mb": 81920,
                    "utilization_gpu_pct": 40,
                },
                {
                    "index": 1,
                    "name": "A100",
                    "memory_used_mb": 120,
                    "memory_total_mb": 81920,
                    "utilization_gpu_pct": 2,
                },
            ],
            "reason": None,
        }

    def _fake_launch_screen_job(
        server_entry,
        *,
        path,
        session_name,
        command,
        log_path,
        env_vars=None,
        timeout_sec=30,
    ):
        return {
            "workspace_path": path,
            "session_name": session_name,
            "log_path": log_path,
            "command": command,
            "env_vars": dict(env_vars or {}),
            "launch_command": (
                f"screen -dmS {session_name} bash -lc "
                f"'cd {path} && export CUDA_VISIBLE_DEVICES={env_vars.get('CUDA_VISIBLE_DEVICES')} && {command}'"
            ),
            "already_running": False,
            "launched": True,
            "success": True,
            "stdout": "",
            "stderr": "",
            "sessions": [{"pid": 4312, "name": session_name, "state": "Detached"}],
            "screen_list_stdout": f"4312.{session_name} (Detached)",
            "screen_list_stderr": "",
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        name = session_name or "aris-run-default"
        return {
            "command": "screen -ls",
            "stdout": f"4312.{name} (Detached)",
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": [{"pid": 4312, "name": name, "state": "Detached"}],
            "session_count": 1,
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": "epoch=1 loss=0.12",
            "stderr": "",
            "success": True,
        }

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.build_remote_overview",
        _fake_build_remote_overview,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_terminal_result",
        _fake_remote_terminal_result,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_prepare_run_environment",
        _fake_prepare_run_environment,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_probe_gpus",
        _fake_probe_gpus,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_launch_screen_job",
        _fake_launch_screen_job,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_list_screen_sessions",
        _fake_list_screen_sessions,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_capture_screen_session",
        _fake_capture_screen_session,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_write_file",
        lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        },
    )

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.run_experiment.value
    assert result["remote_session_name"].startswith("aris-run-")
    assert result["remote_execution_workspace"].endswith("/workspace")
    assert result["remote_isolation_mode"] == "git_worktree"
    assert result["selected_gpu"]["index"] == 1

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        metadata = dict(run.metadata_json or {})
        assert metadata["remote_session_name"].startswith("aris-run-")
        assert metadata["remote_execution_workspace"].endswith("/workspace")
        assert metadata["remote_isolation_mode"] == "git_worktree"
        assert metadata["remote_launch_status"] == "running"
        assert metadata["selected_gpu"]["index"] == 1
        assert metadata["execution_result"]["selected_gpu"]["index"] == 1
        assert metadata["execution_result"]["mode"] == "remote_screen_launch"
        assert any(
            str(item.get("relative_path") or "").endswith("/reports/remote-launch.json")
            or str(item.get("relative_path") or "") == ".auto-researcher/aris-runs/{}/reports/remote-launch.json".format(run_id)
            for item in (metadata.get("artifact_refs") or [])
        )


def test_run_experiment_remote_avoids_gpu_leases_between_runs(monkeypatch):
    _configure_test_db(monkeypatch)

    run_ids: list[str] = []
    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        for idx in range(2):
            project = project_repo.create_project(
                name=f"Remote Lease Test {idx}",
                description="validate gpu lease coordination",
                workspace_server_id="ssh-main",
                remote_workdir=f"/srv/research/lease-test-{idx}",
            )
            target = project_repo.ensure_default_target(project.id)
            assert target is not None
            run = project_repo.create_run(
                project_id=project.id,
                target_id=target.id,
                workflow_type=ProjectWorkflowType.run_experiment,
                title=f"remote lease run {idx}",
                prompt="Launch a remote lease-coordinated experiment.",
                status=ProjectRunStatus.queued,
                active_phase="queued",
                summary="queued",
                workspace_server_id=target.workspace_server_id,
                remote_workdir=target.remote_workdir,
                metadata={
                    "execution_command": "python train.py --epochs 1",
                },
            )
            run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
            log_path = build_run_log_path(run_directory, remote=True)
            project_repo.update_run(
                run.id,
                run_directory=run_directory,
                log_path=log_path,
                metadata={
                    "execution_command": "python train.py --epochs 1",
                },
            )
            run_ids.append(run.id)

    active_sessions: set[str] = set()
    launch_records: list[dict] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        return LLMResult(content=f"# {stage}\n\n- ok\n")

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- train.py\n",
            "files": ["train.py"],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        }

    def _fake_remote_terminal_result(server_entry, *, path, command, timeout_sec):
        stdout = ""
        if command == "python --version":
            stdout = "Python 3.11.9"
        elif command == "git --version":
            stdout = "git version 2.45.0"
        elif command == "uv --version":
            stdout = "uv 0.6.0"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_prepare_run_environment(server_entry, *, path, run_directory, session_name):
        return {
            "workspace_path": path,
            "run_directory": run_directory,
            "execution_workspace": f"{run_directory}/workspace",
            "session_name": session_name,
            "isolation_mode": "git_worktree",
            "git_available": True,
            "git_branch": "main",
            "git_head": "abc123",
            "created_worktree": True,
            "prepare_steps": [],
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 200, "memory_total_mb": 81920, "utilization_gpu_pct": 9},
                {"index": 1, "name": "A100", "memory_used_mb": 120, "memory_total_mb": 81920, "utilization_gpu_pct": 3},
            ],
            "reason": None,
        }

    def _fake_launch_screen_job(
        server_entry,
        *,
        path,
        session_name,
        command,
        log_path,
        env_vars=None,
        timeout_sec=30,
    ):
        active_sessions.add(session_name)
        launch_records.append(
            {
                "session_name": session_name,
                "gpu": dict(env_vars or {}).get("CUDA_VISIBLE_DEVICES"),
                "path": path,
            }
        )
        return {
            "workspace_path": path,
            "session_name": session_name,
            "log_path": log_path,
            "command": command,
            "env_vars": dict(env_vars or {}),
            "launch_command": f"screen -dmS {session_name} bash -lc 'cd {path} && {command}'",
            "already_running": False,
            "launched": True,
            "success": True,
            "stdout": "",
            "stderr": "",
            "sessions": [{"pid": 5000 + len(active_sessions), "name": session_name, "state": "Detached"}],
            "screen_list_stdout": "",
            "screen_list_stderr": "",
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = []
        for idx, name in enumerate(sorted(active_sessions), start=1):
            if session_name and name != session_name:
                continue
            if session_prefix and not name.startswith(session_prefix):
                continue
            sessions.append({"pid": 6000 + idx, "name": name, "state": "Detached"})
        return {
            "command": "screen -ls",
            "stdout": "\n".join(f"{item['pid']}.{item['name']} (Detached)" for item in sessions),
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": sessions,
            "session_count": len(sessions),
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": "running",
            "stderr": "",
            "success": True,
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
    )
    monkeypatch.setattr("packages.ai.project.workflow_runner.build_remote_overview", _fake_build_remote_overview)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_terminal_result", _fake_remote_terminal_result)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_prepare_run_environment", _fake_prepare_run_environment)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_probe_gpus", _fake_probe_gpus)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_launch_screen_job", _fake_launch_screen_job)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_list_screen_sessions", _fake_list_screen_sessions)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_capture_screen_session", _fake_capture_screen_session)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_write_file",
        lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode('utf-8')),
        },
    )

    run_project_workflow(run_ids[0])
    run_project_workflow(run_ids[1])

    assert len(launch_records) == 2
    assert launch_records[0]["gpu"] == "1"
    assert launch_records[1]["gpu"] == "0"

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        first_run = project_repo.get_run(run_ids[0])
        second_run = project_repo.get_run(run_ids[1])
        assert first_run is not None and second_run is not None
        assert dict(first_run.metadata_json or {})["selected_gpu"]["index"] == 1
        assert dict(second_run.metadata_json or {})["selected_gpu"]["index"] == 0


def test_run_experiment_remote_batch_launches_multiple_sessions(monkeypatch):
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="Remote Batch Experiment Test",
            description="validate remote batch fan-out path",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/anchorcot-batch",
        )
        target = project_repo.ensure_default_target(project.id)
        assert target is not None
        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.run_experiment,
            title="remote batch experiment run",
            prompt="Launch a remote batch experiment.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={
                "parallel_experiments": [
                    {"name": "baseline", "command": "python train.py --config baseline.yaml"},
                    {"name": "improved", "command": "python train.py --config improved.yaml"},
                ],
            },
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        project_repo.update_run(
            run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata={
                "parallel_experiments": [
                    {"name": "baseline", "command": "python train.py --config baseline.yaml"},
                    {"name": "improved", "command": "python train.py --config improved.yaml"},
                ],
            },
        )
        run_id = run.id

    active_sessions: set[str] = set()
    launch_records: list[dict] = []

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        assert "批量实验数: 2" in prompt
        return LLMResult(content="# 批量实验摘要\n\n- 两个实验均已启动\n")

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- train.py\n- configs/\n",
            "files": ["train.py", "configs/default.yaml"],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        }

    def _fake_remote_terminal_result(server_entry, *, path, command, timeout_sec):
        stdout = ""
        if command == "python --version":
            stdout = "Python 3.11.9"
        elif command == "git --version":
            stdout = "git version 2.45.0"
        elif command == "uv --version":
            stdout = "uv 0.6.0"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_prepare_run_environment(server_entry, *, path, run_directory, session_name):
        return {
            "workspace_path": path,
            "run_directory": run_directory,
            "execution_workspace": f"{run_directory}/workspace",
            "session_name": session_name,
            "isolation_mode": "git_worktree",
            "git_available": True,
            "git_branch": "main",
            "git_head": "abc123",
            "created_worktree": True,
            "prepare_steps": [],
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 220, "memory_total_mb": 81920, "utilization_gpu_pct": 9},
                {"index": 1, "name": "A100", "memory_used_mb": 120, "memory_total_mb": 81920, "utilization_gpu_pct": 3},
            ],
            "reason": None,
        }

    def _fake_launch_screen_job(
        server_entry,
        *,
        path,
        session_name,
        command,
        log_path,
        env_vars=None,
        timeout_sec=30,
    ):
        active_sessions.add(session_name)
        launch_records.append(
            {
                "session_name": session_name,
                "gpu": dict(env_vars or {}).get("CUDA_VISIBLE_DEVICES"),
                "path": path,
                "log_path": log_path,
                "command": command,
            }
        )
        return {
            "workspace_path": path,
            "session_name": session_name,
            "log_path": log_path,
            "command": command,
            "env_vars": dict(env_vars or {}),
            "launch_command": f"screen -dmS {session_name} bash -lc 'cd {path} && {command}'",
            "already_running": False,
            "launched": True,
            "success": True,
            "stdout": "",
            "stderr": "",
            "sessions": [{"pid": 7000 + len(active_sessions), "name": session_name, "state": "Detached"}],
            "screen_list_stdout": "",
            "screen_list_stderr": "",
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = []
        for idx, name in enumerate(sorted(active_sessions), start=1):
            if session_name and name != session_name:
                continue
            if session_prefix and not name.startswith(session_prefix):
                continue
            sessions.append({"pid": 7100 + idx, "name": name, "state": "Detached"})
        return {
            "command": "screen -ls",
            "stdout": "\n".join(f"{item['pid']}.{item['name']} (Detached)" for item in sessions),
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": sessions,
            "session_count": len(sessions),
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": f"{session_name}: step=12",
            "stderr": "",
            "success": True,
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
    )
    monkeypatch.setattr("packages.ai.project.workflow_runner.build_remote_overview", _fake_build_remote_overview)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_terminal_result", _fake_remote_terminal_result)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_prepare_run_environment", _fake_prepare_run_environment)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_probe_gpus", _fake_probe_gpus)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_launch_screen_job", _fake_launch_screen_job)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_list_screen_sessions", _fake_list_screen_sessions)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_capture_screen_session", _fake_capture_screen_session)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_write_file",
        lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        },
    )

    result = run_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.run_experiment.value
    assert len(result["experiments"]) == 2
    assert len(result["remote_session_names"]) == 2
    assert launch_records[0]["gpu"] == "1"
    assert launch_records[1]["gpu"] == "0"
    assert launch_records[0]["path"].endswith("/experiments/baseline/workspace")
    assert launch_records[1]["path"].endswith("/experiments/improved/workspace")

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.succeeded
        assert metadata["execution_result"]["mode"] == "remote_screen_batch_launch"
        assert len(metadata["remote_experiments"]) == 2
        assert metadata["remote_launch_status"] == "running"
        assert metadata["remote_experiments"][0]["selected_gpu"]["index"] == 1
        assert metadata["remote_experiments"][1]["selected_gpu"]["index"] == 0


def test_run_experiment_remote_batch_releases_failed_gpu_lease(monkeypatch):
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="Remote Batch Partial Failure Test",
            description="validate failed batch launch lease cleanup",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/anchorcot-batch-failure",
        )
        target = project_repo.ensure_default_target(project.id)
        assert target is not None
        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.run_experiment,
            title="remote partial batch experiment run",
            prompt="Launch a remote batch experiment with one failure.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={
                "parallel_experiments": [
                    {"name": "baseline", "command": "python train.py --config baseline.yaml"},
                    {"name": "broken", "command": "python train.py --config broken.yaml"},
                ],
            },
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        project_repo.update_run(
            run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata={
                "parallel_experiments": [
                    {"name": "baseline", "command": "python train.py --config baseline.yaml"},
                    {"name": "broken", "command": "python train.py --config broken.yaml"},
                ],
            },
        )
        run_id = run.id

    active_sessions: set[str] = set()

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        return LLMResult(content="# 批量实验摘要\n\n- 一个实验成功启动，一个实验启动失败\n")

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- train.py\n",
            "files": ["train.py"],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        }

    def _fake_remote_terminal_result(server_entry, *, path, command, timeout_sec):
        stdout = ""
        if command == "python --version":
            stdout = "Python 3.11.9"
        elif command == "git --version":
            stdout = "git version 2.45.0"
        elif command == "uv --version":
            stdout = "uv 0.6.0"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_prepare_run_environment(server_entry, *, path, run_directory, session_name):
        return {
            "workspace_path": path,
            "run_directory": run_directory,
            "execution_workspace": f"{run_directory}/workspace",
            "session_name": session_name,
            "isolation_mode": "git_worktree",
            "git_available": True,
            "git_branch": "main",
            "git_head": "abc123",
            "created_worktree": True,
            "prepare_steps": [],
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 200, "memory_total_mb": 81920, "utilization_gpu_pct": 9},
                {"index": 1, "name": "A100", "memory_used_mb": 120, "memory_total_mb": 81920, "utilization_gpu_pct": 3},
            ],
            "reason": None,
        }

    def _fake_launch_screen_job(
        server_entry,
        *,
        path,
        session_name,
        command,
        log_path,
        env_vars=None,
        timeout_sec=30,
    ):
        if "broken.yaml" in command:
            return {
                "workspace_path": path,
                "session_name": session_name,
                "log_path": log_path,
                "command": command,
                "env_vars": dict(env_vars or {}),
                "launch_command": f"screen -dmS {session_name} bash -lc 'cd {path} && {command}'",
                "already_running": False,
                "launched": False,
                "success": False,
                "stdout": "",
                "stderr": "screen launch failed",
                "sessions": [],
                "screen_list_stdout": "",
                "screen_list_stderr": "",
            }
        active_sessions.add(session_name)
        return {
            "workspace_path": path,
            "session_name": session_name,
            "log_path": log_path,
            "command": command,
            "env_vars": dict(env_vars or {}),
            "launch_command": f"screen -dmS {session_name} bash -lc 'cd {path} && {command}'",
            "already_running": False,
            "launched": True,
            "success": True,
            "stdout": "",
            "stderr": "",
            "sessions": [{"pid": 8100, "name": session_name, "state": "Detached"}],
            "screen_list_stdout": "",
            "screen_list_stderr": "",
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = []
        for idx, name in enumerate(sorted(active_sessions), start=1):
            if session_name and name != session_name:
                continue
            if session_prefix and not name.startswith(session_prefix):
                continue
            sessions.append({"pid": 8200 + idx, "name": name, "state": "Detached"})
        return {
            "command": "screen -ls",
            "stdout": "\n".join(f"{item['pid']}.{item['name']} (Detached)" for item in sessions),
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": sessions,
            "session_count": len(sessions),
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": "baseline: running",
            "stderr": "",
            "success": True,
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
    )
    monkeypatch.setattr("packages.ai.project.workflow_runner.build_remote_overview", _fake_build_remote_overview)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_terminal_result", _fake_remote_terminal_result)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_prepare_run_environment", _fake_prepare_run_environment)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_probe_gpus", _fake_probe_gpus)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_launch_screen_job", _fake_launch_screen_job)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_list_screen_sessions", _fake_list_screen_sessions)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_capture_screen_session", _fake_capture_screen_session)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_write_file",
        lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        },
    )

    result = run_project_workflow(run_id)

    assert len(result["experiments"]) == 2
    assert len(result["launch_failures"]) == 1

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.succeeded
        assert metadata["remote_launch_status"] == "partial_running"
        assert len(metadata["remote_launch_failures"]) == 1
        assert metadata["remote_experiments"][0]["status"] == "running"
        assert metadata["remote_experiments"][1]["status"] == "failed_to_launch"

    active_leases = list_active_gpu_leases("ssh-main")
    assert len(active_leases) == 1
    assert active_leases[0]["gpu_index"] == 1

