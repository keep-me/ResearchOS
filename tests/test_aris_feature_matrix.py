from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project import (
    multi_agent_runner as project_multi_agent_runner,
    run_action_service as project_run_action_service,
    workflow_runner as project_workflow_runner,
)
from packages.ai.project.amadeus_compat import (
    amadeus_action_label,
    build_action_log_path,
    build_action_result_path,
    build_run_directory,
    build_run_log_path,
)
from packages.ai.project.multi_agent_runner import (
    run_multi_agent_project_workflow,
    supports_multi_agent_project_workflow,
)
from packages.ai.project.workflow_catalog import (
    get_project_workflow_preset,
    is_active_project_workflow,
    list_project_workflow_presets,
)
from packages.ai.project.workflow_runner import run_project_workflow, supports_project_workflow
from packages.domain.enums import ProjectRunActionType, ProjectRunStatus, ProjectWorkflowType
from packages.domain.schemas import PaperCreate
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import PaperRepository, ProjectRepository


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _prepare_local_workspace(path: Path) -> None:
    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "notes").mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("# ARIS Smoke Workspace\n", encoding="utf-8")
    (path / "src" / "main.py").write_text("print('workspace ready')\n", encoding="utf-8")
    (path / "scripts" / "run_smoke.py").write_text("print('smoke run')\n", encoding="utf-8")
    (path / "notes" / "context.md").write_text("baseline context\n", encoding="utf-8")


def _seed_project_run(
    tmp_path: Path,
    workflow_type: ProjectWorkflowType,
) -> dict[str, str | bool | None]:
    remote = workflow_type == ProjectWorkflowType.monitor_experiment
    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        paper_repo = PaperRepository(session)

        if remote:
            workspace_path = f"/srv/research/{workflow_type.value}-smoke"
            project = project_repo.create_project(
                name=f"ARIS {workflow_type.value} Smoke",
                description="ARIS workflow smoke test",
                workspace_server_id="ssh-main",
                remote_workdir=workspace_path,
            )
        else:
            workspace_dir = tmp_path / workflow_type.value
            _prepare_local_workspace(workspace_dir)
            workspace_path = str(workspace_dir)
            project = project_repo.create_project(
                name=f"ARIS {workflow_type.value} Smoke",
                description="ARIS workflow smoke test",
                workdir=workspace_path,
            )

        target = project_repo.ensure_default_target(project.id)
        assert target is not None

        paper_a = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id=f"2603.{abs(hash((workflow_type.value, 'a'))) % 90000 + 10000}",
                title=f"{workflow_type.value} paper A",
                abstract="A deterministic paper abstract for ARIS smoke testing.",
                metadata={},
            )
        )
        paper_b = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id=f"2603.{abs(hash((workflow_type.value, 'b'))) % 90000 + 10000}",
                title=f"{workflow_type.value} paper B",
                abstract="Another deterministic paper abstract for ARIS smoke testing.",
                metadata={},
            )
        )
        project_repo.add_paper_to_project(project_id=project.id, paper_id=paper_a.id)
        project_repo.add_paper_to_project(project_id=project.id, paper_id=paper_b.id)

        if not remote:
            project_repo.create_repo(
                project_id=project.id,
                repo_url=f"https://github.com/example/{workflow_type.value}.git",
                local_path=workspace_path,
                is_workdir_repo=True,
            )

        metadata: dict[str, object] = {}
        if workflow_type in {ProjectWorkflowType.run_experiment, ProjectWorkflowType.full_pipeline}:
            metadata["execution_command"] = "python ./scripts/run_smoke.py"
            metadata["execution_timeout_sec"] = 120
        if workflow_type == ProjectWorkflowType.rebuttal:
            metadata["rebuttal_review_bundle"] = (
                "Reviewer 1:\n"
                "- Novelty is not clearly separated from the closest prior work.\n\n"
                "Reviewer 2:\n"
                "- The empirical evidence is promising but still limited.\n"
            )
            metadata["rebuttal_venue"] = "ICML"
            metadata["rebuttal_character_limit"] = 5000
            metadata["rebuttal_round"] = "initial"

        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=workflow_type,
            title=f"{workflow_type.value} smoke run",
            prompt=f"Please execute the {workflow_type.value} workflow and return a deterministic smoke output.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            workdir=target.workdir,
            remote_workdir=target.remote_workdir,
            max_iterations=3,
            executor_model="mock-executor",
            reviewer_model="mock-reviewer",
            metadata=metadata,
        )

        run_workspace_path = target.remote_workdir if target.workspace_server_id else target.workdir
        run_directory = build_run_directory(run_workspace_path, run.id, remote=bool(target.workspace_server_id))
        log_path = build_run_log_path(run_directory, remote=bool(target.workspace_server_id))
        project_repo.update_run(run.id, run_directory=run_directory, log_path=log_path, metadata=metadata)

        return {
            "project_id": project.id,
            "run_id": run.id,
            "workspace_path": run_workspace_path,
            "run_directory": run_directory,
            "remote": bool(target.workspace_server_id),
        }


def _seed_run_action(tmp_path: Path, action_type: ProjectRunActionType) -> dict[str, str]:
    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        workspace_dir = tmp_path / f"action-{action_type.value}"
        _prepare_local_workspace(workspace_dir)

        project = project_repo.create_project(
            name=f"ARIS Action {action_type.value}",
            description="ARIS action smoke test",
            workdir=str(workspace_dir),
        )
        target = project_repo.ensure_default_target(project.id)
        assert target is not None

        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.literature_review,
            title="parent run",
            prompt="continue the current ARIS run",
            status=ProjectRunStatus.running,
            active_phase="completed",
            summary="workflow finished",
            workspace_server_id=target.workspace_server_id,
            workdir=target.workdir,
            remote_workdir=target.remote_workdir,
            reviewer_model="mock-reviewer",
            metadata={"workflow_output_markdown": "# Existing ARIS output\n"},
        )
        run_directory = build_run_directory(target.workdir, run.id, remote=False)
        log_path = build_run_log_path(run_directory, remote=False)
        project_repo.update_run(run.id, run_directory=run_directory, log_path=log_path)

        action = project_repo.create_run_action(
            run_id=run.id,
            action_type=action_type,
            prompt=f"Execute action {action_type.value} in a deterministic way.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
        )
        result_path = build_action_result_path(run_directory, action.id, remote=False)
        log_path = build_action_log_path(run_directory, action.id, remote=False)
        project_repo.update_run_action(action.id, result_path=result_path, log_path=log_path)
        return {
            "project_id": project.id,
            "run_id": run.id,
            "action_id": action.id,
            "workspace_path": str(workspace_dir),
            "run_directory": str(run_directory),
        }


def _install_llm_and_workspace_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        stage_text = str(stage or "")
        if stage_text == "project_literature_review":
            content = (
                "# 项目级文献综述\n\n"
                "## 研究背景\n"
                "本次 smoke 测试验证 ARIS 文献综述链路可正常生成结构化输出。\n\n"
                "## 下一步建议\n"
                "- 保持文献输入与项目上下文联动。\n"
            )
        elif stage_text == "project_run_experiment_summary":
            content = (
                "# 实验总结\n\n"
                "- 命令执行成功\n"
                "- 已生成日志与总结报告\n"
                "- 建议继续扩展指标与误差分析\n"
            )
        elif stage_text == "project_paper_writing_plan":
            content = (
                "# PAPER_PLAN\n\n"
                "## Claims-Evidence Matrix\n"
                "Claim A -> Evidence A\n"
            )
        elif stage_text == "project_paper_writing_figure":
            content = (
                "# FIGURE_PLAN\n\n"
                "- Fig 1: Main comparison\n"
                "- Fig 2: Ablation\n"
            )
        elif stage_text == "project_paper_writing_write":
            content = (
                "# 论文草稿\n\n"
                "## Introduction\n"
                "ARIS smoke test validates end-to-end project writing.\n\n"
                "## Method\n"
                "Use deterministic fixtures for workflow validation.\n"
            )
        elif stage_text == "project_paper_writing_compile":
            content = (
                "# PAPER_COMPILE\n\n"
                "- Status: pending manual compile\n"
                "- Missing toolchain: latexmk\n"
            )
        elif stage_text.startswith("project_paper_writing_improve_review_"):
            content = (
                "# 审稿意见\n\n"
                "Score: 7.6\n\n"
                "- 需要继续补强叙事和实验细节。\n"
            )
        elif stage_text.startswith("project_paper_writing_improve_revise_"):
            content = (
                "# 论文草稿（润色版）\n\n"
                "## 引言\n"
                "本文展示 ARIS 工作流在 smoke 测试中的稳定输出。\n\n"
                "## 方法\n"
                "通过确定性桩函数验证写作链路、产物写回与摘要生成。\n"
            )
        elif stage_text == "project_full_pipeline_gate":
            content = "# IDEA_REPORT\n\n- 已归纳项目背景\n- 已给出推荐 idea"
        elif stage_text == "project_full_pipeline_auto_review":
            content = "# AUTO_REVIEW\n\n- 主流程可运行\n- 建议补充更细粒度实验指标"
        elif stage_text == "project_full_pipeline_handoff":
            content = "# 最终交付物\n\n- 形成最终报告\n- 给出下一轮计划"
        elif stage_text == "project_rebuttal_issue_board":
            content = (
                "# ISSUE_BOARD\n\n"
                "- R1-C1: novelty clarification\n"
                "- R2-C1: empirical support\n"
            )
        elif stage_text == "project_rebuttal_strategy":
            content = (
                "# STRATEGY_PLAN\n\n"
                "- shared theme: clarify exact delta to prior work\n"
                "- shared theme: narrow the empirical claim and cite existing evidence\n"
            )
        elif stage_text == "project_rebuttal_draft":
            content = (
                "# REBUTTAL_DRAFT\n\n"
                "We thank the reviewers and respond to each concern below.\n\n"
                "## Reviewer 1\n"
                "- We clarify the novelty boundary against the closest baseline.\n"
            )
        elif stage_text == "project_rebuttal_stress":
            content = (
                "# MCP_STRESS_TEST\n\n"
                "- tighten the baseline delta wording\n"
                "- avoid overclaiming on empirical generality\n"
            )
        elif stage_text == "project_rebuttal_finalize":
            content = (
                "# REBUTTAL_DRAFT\n\n"
                "We thank the reviewers and provide a grounded response for novelty and empirical support.\n"
            )
        elif stage_text.startswith("project_run_action_"):
            label = stage_text.removeprefix("project_run_action_")
            content = f"# 后续动作输出\n\n- action_type: {label}\n- status: completed\n- smoke: ok\n"
        else:
            prompt_digest = str(prompt or "").replace("\n", " ")[:80]
            content = f"# {stage_text}\n\n- smoke: ok\n- prompt: {prompt_digest}\n"
        return LLMResult(content=content)

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
        if str(stage or "") == "project_experiment_audit_review":
            payload = {
                "overall_verdict": "WARN",
                "integrity_status": "warn",
                "evaluation_type": "mixed",
                "summary": "ARIS smoke audit detected result evidence but still needs scope clarification.",
                "checks": {
                    "gt_provenance": {"status": "PASS", "evidence": ["configs/eval.yaml:1"], "details": "dataset origin declared."},
                    "score_normalization": {"status": "WARN", "evidence": ["results/metrics.json:2"], "details": "normalization note missing."},
                    "result_existence": {"status": "PASS", "evidence": ["results/metrics.json:1"], "details": "result file exists."},
                    "dead_code": {"status": "PASS", "evidence": ["scripts/run_smoke.py:1"], "details": "evaluation path reachable."},
                    "scope": {"status": "WARN", "evidence": ["notes/context.md:1"], "details": "scope statement is still broad."},
                    "eval_type": {"status": "PASS", "evidence": ["README.md:1"], "details": "classified as mixed."},
                },
                "action_items": ["clarify scope", "document normalization"],
                "claims": [{"id": "C1", "impact": "needs_qualifier", "details": "narrow the strongest claim"}],
            }
            return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)
        payload = {
            "ideas": [
                {
                    "title": "ARIS 想法 1",
                    "content": "围绕过程奖励做一个最小复现实验。",
                    "paper_refs": ["P1"],
                },
                {
                    "title": "ARIS 想法 2",
                    "content": "比较不同推理详略配置对实验稳定性的影响。",
                    "paper_refs": ["P2"],
                },
                {
                    "title": "ARIS 想法 3",
                    "content": "补一套自动评审与实验闭环的可视化面板。",
                    "paper_refs": ["P1", "P2"],
                },
            ]
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    def _fake_inspect_workspace(workspace_path: str, max_depth=2, max_entries=80):
        return {
            "workspace_path": workspace_path,
            "tree": "README.md\nsrc/\nscripts/\nnotes/\n",
            "files": ["README.md", "src/main.py", "scripts/run_smoke.py", "notes/context.md"],
            "dirs": ["src", "scripts", "notes"],
            "message": "workspace ready",
        }

    def _fake_run_workspace_command(workspace_path: str, command: str, timeout_sec: int = 120):
        workspace_dir = Path(workspace_path).expanduser()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        stdout = f"command executed: {command}"
        if "autoresearch_baseline.py" in command:
            reports_dir = workspace_dir / "autoresearch" / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "baseline_report.md").write_text(
                "# AutoResearch Baseline\n\n- status: completed\n",
                encoding="utf-8",
            )
            (reports_dir / "baseline_metrics.json").write_text(
                json.dumps({"status": "completed", "baseline_score": 0.0}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            stdout = "autoresearch baseline completed"
        return {
            "workspace_path": workspace_path,
            "command": command,
            "shell_command": ["pwsh", "-NoLogo", "-Command", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _fake_complete_json)
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner._resolve_paper_compile_command",
        lambda context: "",
    )
    monkeypatch.setattr(project_workflow_runner, "inspect_workspace", _fake_inspect_workspace)
    monkeypatch.setattr(project_workflow_runner, "run_workspace_command", _fake_run_workspace_command)
    monkeypatch.setattr(project_multi_agent_runner, "run_workspace_command", _fake_run_workspace_command)


def _stage_trace_by_id(metadata: dict) -> dict[str, dict]:
    return {
        str(item.get("stage_id")): item
        for item in (metadata.get("stage_trace") or [])
        if isinstance(item, dict) and item.get("stage_id")
    }


def _workflow_excerpt(result: dict) -> str:
    if result.get("summary"):
        return str(result["summary"])
    if result.get("markdown"):
        return str(result["markdown"]).replace("\n", " ")[:120]
    if result.get("created_ideas"):
        return f"created_ideas={len(result['created_ideas'])}"
    return "no-result"


def test_aris_catalog_covers_all_workflows_and_actions():
    presets = list_project_workflow_presets()
    preset_map = {item["workflow_type"]: item for item in presets}

    assert len(presets) == len(ProjectWorkflowType)
    assert set(preset_map) == {item.value for item in ProjectWorkflowType}

    for workflow_type in ProjectWorkflowType:
        preset = preset_map[workflow_type.value]
        assert preset["label"]
        assert preset["prefill_prompt"]
        assert preset["description"]
        assert preset["source_reference"] == "amadeus_aris"
        if preset["workflow_type"] in {
            ProjectWorkflowType.idea_discovery.value,
            ProjectWorkflowType.run_experiment.value,
            ProjectWorkflowType.auto_review_loop.value,
            ProjectWorkflowType.paper_writing.value,
            ProjectWorkflowType.rebuttal.value,
            ProjectWorkflowType.full_pipeline.value,
        }:
            assert preset["sample_prompt"]
        assert isinstance(preset["stages"], list) and preset["stages"]
        stage_ids = [str(item["id"]) for item in preset["stages"]]
        assert len(stage_ids) == len(set(stage_ids))
        for stage in preset["stages"]:
            assert stage["label"]
            assert stage["description"]
            assert stage["execution_target"] in {"local", "workspace_target", "ssh"}

    for action_type in ProjectRunActionType:
        assert amadeus_action_label(action_type)


@pytest.mark.parametrize("workflow_type", list(ProjectWorkflowType), ids=lambda item: item.value)
def test_aris_workflow_smoke_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workflow_type: ProjectWorkflowType,
):
    _configure_test_db(monkeypatch)
    _install_llm_and_workspace_stubs(monkeypatch)
    seeded = _seed_project_run(tmp_path, workflow_type)

    if supports_project_workflow(workflow_type):
        result = run_project_workflow(str(seeded["run_id"]))
        expected_executor = "native"
        assert is_active_project_workflow(workflow_type) is True
    else:
        assert supports_multi_agent_project_workflow(workflow_type) is True
        result = run_multi_agent_project_workflow(str(seeded["run_id"]))
        expected_executor = "multi_agent"

    preset = get_project_workflow_preset(workflow_type)
    assert preset is not None
    expected_stage_ids = [str(item["id"]) for item in preset["stages"]]

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(str(seeded["run_id"]))
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded

        metadata = dict(run.metadata_json or {})
        assert metadata.get("workflow_output_markdown")
        trace_map = _stage_trace_by_id(metadata)
        assert list(trace_map) == expected_stage_ids
        assert all(str(trace_map[stage_id].get("status")) == "completed" for stage_id in expected_stage_ids)
        assert all(str(trace_map[stage_id].get("model_role") or "").strip() in {"executor", "reviewer"} for stage_id in expected_stage_ids)
        for stage_id in expected_stage_ids:
            stage_trace = trace_map[stage_id]
            if stage_trace.get("model_source") == "executor_model":
                assert stage_trace.get("model") == "mock-executor"
            if stage_trace.get("model_source") == "reviewer_model":
                assert stage_trace.get("model") == "mock-reviewer"

        if expected_executor == "multi_agent":
            stage_outputs = metadata.get("stage_outputs") or {}
            assert set(stage_outputs) == set(expected_stage_ids)
        else:
            assert metadata.get("workflow_output_excerpt")

        if workflow_type == ProjectWorkflowType.idea_discovery:
            assert len(result["created_ideas"]) == 3
            assert len(metadata.get("created_idea_ids") or []) == 3

        if workflow_type in {
            ProjectWorkflowType.paper_plan,
            ProjectWorkflowType.paper_figure,
            ProjectWorkflowType.paper_write,
            ProjectWorkflowType.paper_compile,
            ProjectWorkflowType.experiment_audit,
            ProjectWorkflowType.run_experiment,
            ProjectWorkflowType.paper_writing,
            ProjectWorkflowType.rebuttal,
            ProjectWorkflowType.paper_improvement,
            ProjectWorkflowType.full_pipeline,
        }:
            artifact_refs = metadata.get("artifact_refs") or []
            assert artifact_refs

        if workflow_type == ProjectWorkflowType.init_repo:
            workspace_root = Path(str(seeded["workspace_path"]))
            assert (workspace_root / "README.md").exists()
            assert (workspace_root / "src" / "main.py").exists()
            assert (workspace_root / "scripts" / "run_smoke.ps1").exists()

        if workflow_type == ProjectWorkflowType.autoresearch_claude_code:
            workspace_root = Path(str(seeded["workspace_path"]))
            assert (workspace_root / "autoresearch" / "session.json").exists()
            assert (workspace_root / "autoresearch" / "reports" / "baseline_report.md").exists()

        if workflow_type == ProjectWorkflowType.monitor_experiment:
            assert run.workspace_server_id == "ssh-main"

    print(
        f"[ARIS workflow] {workflow_type.value} | executor={expected_executor} | "
        f"summary={_workflow_excerpt(result)}"
    )


@pytest.mark.parametrize("action_type", list(ProjectRunActionType), ids=lambda item: item.value)
def test_aris_action_smoke_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action_type: ProjectRunActionType,
):
    _configure_test_db(monkeypatch)
    _install_llm_and_workspace_stubs(monkeypatch)
    seeded = _seed_run_action(tmp_path, action_type)

    def _fake_submit_project_run(run_id: str):
        with db.session_scope() as session:
            repo = ProjectRepository(session)
            run = repo.get_run(run_id)
            assert run is not None
            repo.update_run(
                run_id,
                task_id=f"task-child-{action_type.value}",
                status=ProjectRunStatus.running,
                active_phase="queued",
            )
        return f"task-child-{action_type.value}"

    monkeypatch.setattr(project_run_action_service, "submit_project_run", _fake_submit_project_run)

    result = project_run_action_service.run_project_run_action(str(seeded["action_id"]))

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        action = repo.get_run_action(str(seeded["action_id"]))
        assert action is not None
        assert action.status == ProjectRunStatus.succeeded
        metadata = dict(action.metadata_json or {})
        artifact_refs = metadata.get("artifact_refs") or []
        assert len(artifact_refs) == 2
        assert metadata.get("spawned_run_id")
        spawned = repo.get_run(str(metadata.get("spawned_run_id")))
        assert spawned is not None

        result_path = Path(str(action.result_path))
        log_path = Path(str(action.log_path))
        assert result_path.exists()
        assert log_path.exists()

    print(
        f"[ARIS action] {action_type.value} | label={amadeus_action_label(action_type)} | "
        f"summary={_workflow_excerpt(result)}"
    )
