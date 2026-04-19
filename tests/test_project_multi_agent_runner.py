from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project.amadeus_compat import build_run_directory, build_run_log_path
from packages.ai.project.checkpoint_service import apply_checkpoint_response
from packages.ai.project.multi_agent_runner import run_multi_agent_project_workflow
from packages.ai.project.workflow_catalog import build_run_orchestration, build_stage_trace
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.domain.task_tracker import TaskPausedError
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import ProjectRepository


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


def _seed_run(
    workflow_type: ProjectWorkflowType,
    selected_agent_id: str,
    *,
    workdir: str | None = None,
    metadata: dict | None = None,
) -> str:
    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name="Multi Agent Native Test",
            description="runner regression",
            workdir=workdir or "D:/tmp/researchos-multi-agent-native",
        )
        target = repo.ensure_default_target(project.id)
        assert target is not None

        orchestration = build_run_orchestration(
            workflow_type,
            None,
            target_id=target.id,
            workspace_server_id=target.workspace_server_id,
            reset_stage_status=True,
        )
        for stage in orchestration.get("stages") or []:
            if isinstance(stage, dict):
                stage["selected_agent_id"] = selected_agent_id

        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=workflow_type,
            title=f"{workflow_type.value} native role run",
            prompt="please execute this workflow and persist stage outputs",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            workdir=target.workdir,
            remote_workdir=target.remote_workdir,
            executor_model="mock-executor",
            reviewer_model="mock-reviewer",
            metadata={
                "orchestration": orchestration,
                "stage_trace": build_stage_trace(orchestration, reset=True),
                **(metadata or {}),
            },
        )
        run_directory = build_run_directory(target.workdir, run.id, remote=False)
        log_path = build_run_log_path(run_directory, remote=False)
        repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
        return run.id


class _FakeRemoteAttr:
    def __init__(self, path: Path):
        stat_result = path.stat()
        self.filename = path.name
        self.st_mode = stat_result.st_mode
        self.st_size = stat_result.st_size


class _FakeSFTP:
    def __init__(self, root: Path):
        self.root = root

    def _to_local(self, remote_path: str) -> Path:
        normalized = str(remote_path or "").replace("\\", "/").strip("/")
        if not normalized:
            return self.root
        return self.root.joinpath(*normalized.split("/"))

    def stat(self, path: str):
        local_path = self._to_local(path)
        if not local_path.exists():
            raise FileNotFoundError(path)
        stat_result = local_path.stat()
        return SimpleNamespace(st_mode=stat_result.st_mode, st_size=stat_result.st_size)

    def mkdir(self, path: str):
        self._to_local(path).mkdir(parents=True, exist_ok=True)

    def listdir_attr(self, path: str):
        local_path = self._to_local(path)
        return [_FakeRemoteAttr(child) for child in sorted(local_path.iterdir())]

    def file(self, path: str, mode: str):
        local_path = self._to_local(path)
        if any(flag in mode for flag in ("w", "a", "+")):
            local_path.parent.mkdir(parents=True, exist_ok=True)
        return open(local_path, mode)


class _FakeSSHSession:
    def __init__(self, root: Path):
        self.sftp = _FakeSFTP(root)


def _fake_open_ssh_session_factory(roots: dict[str, Path]):
    @contextmanager
    def _open(server_entry: dict):
        yield _FakeSSHSession(roots[str(server_entry["id"])])

    return _open


def _fake_build_remote_overview_factory(roots: dict[str, Path]):
    def _build(server_entry, requested_path, *, depth, max_entries):
        root = roots[str(server_entry["id"])]
        workspace_root = root.joinpath(*str(requested_path).strip("/").split("/")) if str(requested_path).strip("/") else root
        files: list[str] = []
        tree_lines = [requested_path]
        total_entries = 0
        if workspace_root.exists():
            for path in sorted(workspace_root.rglob("*")):
                relative = path.relative_to(workspace_root).as_posix()
                indent = "  " * max(0, len(path.relative_to(workspace_root).parts) - 1)
                tree_lines.append(f"{indent}- {path.name}{'/' if path.is_dir() else ''}")
                total_entries += 1
                if path.is_file() and len(files) < max_entries:
                    files.append(relative)
        return {
            "workspace_path": requested_path,
            "tree": "\n".join(tree_lines),
            "files": files[:max_entries],
            "exists": workspace_root.exists(),
            "total_entries": total_entries,
            "git": {"available": False, "is_repo": False},
        }

    return _build


def test_multi_agent_runner_executes_codex_role_without_cli(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(ProjectWorkflowType.full_pipeline, selected_agent_id="codex")

    def _fake_summarize(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        return LLMResult(content=f"stage={stage}\nsummary=ok")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )

    result = run_multi_agent_project_workflow(run_id)

    assert result["run_id"] == run_id
    assert result["workflow_type"] == ProjectWorkflowType.full_pipeline.value

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded

        metadata = dict(run.metadata_json or {})
        stage_outputs = metadata.get("stage_outputs") or {}
        assert stage_outputs
        assert all(str(item.get("agent_id")) == "codex" for item in stage_outputs.values())
        assert all(item.get("provider") for item in stage_outputs.values())
        assert all(item.get("command") is None for item in stage_outputs.values())
        for item in stage_outputs.values():
            if item.get("model_source") == "executor_model":
                assert item.get("model") == "mock-executor"
            if item.get("model_source") == "reviewer_model":
                assert item.get("model") == "mock-reviewer"
        trace = metadata.get("stage_trace") or []
        assert trace
        assert all(item.get("model_role") in {"executor", "reviewer"} for item in trace if isinstance(item, dict))


def test_multi_agent_runner_autoresearch_bootstrap_and_baseline(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.autoresearch_claude_code,
        selected_agent_id="claude_code",
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
        return LLMResult(content=f"stage={stage}\nnext=iteration_plan")

    def _fake_run_workspace_command(workspace_path: str, command: str, timeout_sec: int = 120):
        return {
            "workspace_path": workspace_path,
            "command": command,
            "shell_command": ["pwsh", "-NoLogo", "-Command", command],
            "exit_code": 0,
            "stdout": "baseline ok",
            "stderr": "",
            "success": True,
        }

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.run_workspace_command",
        _fake_run_workspace_command,
    )

    result = run_multi_agent_project_workflow(run_id)

    assert result["run_id"] == run_id
    assert result["workflow_type"] == ProjectWorkflowType.autoresearch_claude_code.value

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded

        metadata = dict(run.metadata_json or {})
        stage_outputs = metadata.get("stage_outputs") or {}
        assert "bootstrap_session" in stage_outputs
        assert "run_baseline" in stage_outputs
        assert "propose_iterations" in stage_outputs

        bootstrap = stage_outputs["bootstrap_session"]
        baseline = stage_outputs["run_baseline"]
        propose = stage_outputs["propose_iterations"]

        assert "autoresearch/session.json" in str(bootstrap.get("content") or "")
        assert str(baseline.get("provider")) == "researchos_autoresearch_local"
        assert str(baseline.get("command") or "").endswith("scripts/autoresearch_baseline.py")
        assert propose.get("provider")
        assert propose.get("model_role") == "executor"
        assert propose.get("model") == "mock-executor"


def test_multi_agent_runner_materializes_paper_write_workspace(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.paper_write,
        selected_agent_id="claude_code",
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
        return LLMResult(
            content=(
                "# Draft\n\n"
                "## Abstract\nA structured draft.\n\n"
                "## Method\nUse deterministic fixtures.\n\n"
                "## Experiments\nReport main results.\n"
            )
        )

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )

    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.paper_write.value
    assert (Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id / "paper" / "main.tex").exists()
    assert (Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id / "reports" / "PAPER_WRITE.md").exists()
    assert (Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id / "paper" / "references.bib").exists()


def test_multi_agent_runner_stage_checkpoint_resumes_standalone_workflow(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.paper_plan,
        selected_agent_id="claude_code",
        workdir=str(tmp_path),
        metadata={
            "human_checkpoint_enabled": True,
            "auto_proceed": False,
        },
    )
    with db.session_scope() as session:
        repo = ProjectRepository(session)
        repo.update_run(run_id, task_id="paper-plan-checkpoint-task")

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
        if stage.endswith("_collect_materials"):
            return LLMResult(content="# Materials\n\n- anchor observations\n- key claims")
        if stage.endswith("_outline_manuscript"):
            return LLMResult(content="# Outline\n\n## Introduction\n## Method\n## Experiments")
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )

    with pytest.raises(TaskPausedError):
        run_multi_agent_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert metadata["pending_checkpoint"]["resume_stage_id"] == "outline_manuscript"
        assert metadata["stage_outputs"]["collect_materials"]["content"].startswith("# Materials")

    apply_checkpoint_response(run_id, action="approve", comment="continue")
    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.paper_plan.value
    assert llm_stages.count("project_paper_plan_collect_materials") == 1
    assert llm_stages.count("project_paper_plan_outline_manuscript") == 1

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        metadata = dict(run.metadata_json or {})
        assert metadata["workflow_output_markdown"].startswith("# PAPER_PLAN")


def test_multi_agent_runner_materializes_paper_improvement_with_explicit_review_parsing(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.paper_improvement,
        selected_agent_id="claude_code",
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
        if stage.endswith("_diagnose_draft"):
            return LLMResult(
                content=(
                    "# Review\n\n"
                    "Verdict: READY\n\n"
                    "Weaknesses:\n"
                    "1. Clarify the main contribution claim.\n"
                )
            )
        if stage.endswith("_revise_sections"):
            return LLMResult(content="# Revision Notes\n\n- Reframed introduction and claims.")
        if stage.endswith("_final_check"):
            return LLMResult(
                content=(
                    "# Final Check\n\n"
                    "Score: 7.8/10\n"
                    "Minor revisions only.\n\n"
                    "1. Fix bibliography formatting.\n"
                )
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )

    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.paper_improvement.value
    run_root = Path(tmp_path) / ".auto-researcher" / "aris-runs" / run_id
    progression = (run_root / "reports" / "paper-score-progression.md").read_text(encoding="utf-8")
    metadata_payload = json.loads((run_root / "paper" / "improvement-metadata.json").read_text(encoding="utf-8"))

    assert "| 1 | 内容评审 | N/A | ready |" in progression
    assert "| 2 | 修订后复审 | 7.8 | almost |" in progression
    assert metadata_payload["score_round_one"] is None
    assert metadata_payload["score_round_two"] == 7.8
    assert metadata_payload["verdict_round_one"] == "ready"
    assert metadata_payload["verdict_round_two"] == "almost"

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert metadata["paper_improvement_scores"]["round_1"] is None
        assert metadata["paper_improvement_scores"]["round_2"] == 7.8
        assert metadata["paper_improvement_verdicts"]["round_1"] == "ready"
        assert metadata["paper_improvement_verdicts"]["round_2"] == "almost"


def test_multi_agent_runner_materializes_experiment_audit_artifacts(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    workspace = Path(tmp_path)
    (workspace / "results").mkdir(parents=True, exist_ok=True)
    (workspace / "configs").mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "sections").mkdir(parents=True, exist_ok=True)
    (workspace / "eval_metric.py").write_text(
        "def main():\n"
        "    return 'results/metrics.json'\n",
        encoding="utf-8",
    )
    (workspace / "results" / "metrics.json").write_text(
        json.dumps({"accuracy": 0.89, "normalized_score": 0.96}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (workspace / "EXPERIMENT_TRACKER.md").write_text(
        "# EXPERIMENT_TRACKER\n\n- seeds: 1\n- accuracy: 0.89\n",
        encoding="utf-8",
    )
    (workspace / "paper" / "sections" / "experiments.tex").write_text(
        "\\section{Experiments}\nWe report 0.89 accuracy.\n",
        encoding="utf-8",
    )
    (workspace / "configs" / "eval.yaml").write_text(
        "dataset: anchorcot-dev\nmetric: accuracy\n",
        encoding="utf-8",
    )

    run_id = _seed_run(
        ProjectWorkflowType.experiment_audit,
        selected_agent_id="claude_code",
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
            "summary": "主要结果可用，但单 seed 和归一化说明不足。",
            "checks": {
                "gt_provenance": {"status": "PASS", "evidence": ["configs/eval.yaml:1"], "details": "数据来源已声明。"},
                "score_normalization": {"status": "WARN", "evidence": ["results/metrics.json:3"], "details": "normalized_score 口径不清晰。"},
                "result_existence": {"status": "PASS", "evidence": ["results/metrics.json:2"], "details": "主结果文件存在。"},
                "dead_code": {"status": "PASS", "evidence": ["eval_metric.py:1"], "details": "评测脚本参与执行链路。"},
                "scope": {"status": "WARN", "evidence": ["EXPERIMENT_TRACKER.md:3"], "details": "仍然只有单 seed。"},
                "eval_type": {"status": "PASS", "evidence": ["paper/sections/experiments.tex:2"], "details": "归类为 mixed。"},
            },
            "action_items": ["补多 seed", "补充 normalized_score 解释"],
            "claims": [{"id": "C1", "impact": "needs_qualifier", "details": "不要过度泛化结论。"}],
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.complete_json", _fake_complete_json)

    result = run_multi_agent_project_workflow(run_id)

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
        stage_outputs = metadata.get("stage_outputs") or {}
        artifact_refs = list(metadata.get("artifact_refs") or [])

        assert run.status == ProjectRunStatus.succeeded
        assert "results/metrics.json" in str(stage_outputs["collect_artifacts"].get("content") or "")
        assert str(stage_outputs["issue_audit_report"].get("content") or "").startswith("# Experiment Audit Report")
        assert metadata["overall_verdict"] == "WARN"
        assert metadata["integrity_status"] == "warn"
        assert any(str(item.get("relative_path") or "").replace("\\", "/").endswith("EXPERIMENT_AUDIT.md") for item in artifact_refs)
        assert any(str(item.get("relative_path") or "").replace("\\", "/").endswith("EXPERIMENT_AUDIT.json") for item in artifact_refs)
        assert any(str(item.get("relative_path") or "").replace("\\", "/").endswith("reports/experiment-audit.md") for item in artifact_refs)


def test_multi_agent_runner_paper_compile_collects_generated_pdf_artifact(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    workspace = Path(tmp_path)
    (workspace / "paper").mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nSmoke\n\\end{document}\n",
        encoding="utf-8",
    )

    run_id = _seed_run(
        ProjectWorkflowType.paper_compile,
        selected_agent_id="codex",
        workdir=str(workspace),
        metadata={
            "compile_command": (
                "New-Item -ItemType Directory -Force -Path paper | Out-Null; "
                "Set-Content -Path paper/main.pdf -Value \"pdf stub\"; "
                "Write-Output \"compile-ok\""
            ),
        },
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
        if stage.endswith("_summarize_compile"):
            return LLMResult(content="# Summary\n\n- compile finished\n- paper/main.pdf generated\n")
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )

    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.paper_compile.value
    assert (workspace / "paper" / "main.pdf").exists()

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        artifact_refs = list(metadata.get("artifact_refs") or [])
        assert any(str(item.get("relative_path") or "") == "paper/main.pdf" for item in artifact_refs)
        assert any(str(item.get("kind") or "") == "pdf" for item in artifact_refs)


def test_multi_agent_runner_sync_workspace_copies_files(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "src").mkdir(parents=True, exist_ok=True)
    (source_root / "src" / "main.py").write_text("print('sync ok')\n", encoding="utf-8")
    (source_root / "README.md").write_text("# Sync\n", encoding="utf-8")

    run_id = _seed_run(
        ProjectWorkflowType.sync_workspace,
        selected_agent_id="codex",
        workdir=str(source_root),
        metadata={
            "project_workspace_path": str(source_root),
            "target_workspace_path": str(target_root),
            "sync_strategy": "incremental_copy",
        },
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
        return LLMResult(content=f"# {stage}\n\n- ok\n")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )

    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.sync_workspace.value
    assert (target_root / "src" / "main.py").exists()
    assert (target_root / "README.md").exists()


def test_multi_agent_runner_sync_workspace_copies_remote_to_remote(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    remote_roots = {
        "ssh-source": tmp_path / "ssh-source",
        "ssh-target": tmp_path / "ssh-target",
    }
    source_root = remote_roots["ssh-source"] / "srv" / "project-source"
    target_root = remote_roots["ssh-target"] / "srv" / "project-target"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "src").mkdir(parents=True, exist_ok=True)
    (source_root / "src" / "train.py").write_text("print('remote sync ok')\n", encoding="utf-8")
    (source_root / "README.md").write_text("# Remote Sync\n", encoding="utf-8")

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name="Remote Sync Project",
            description="remote source to remote target",
            workspace_server_id="ssh-source",
            remote_workdir="/srv/project-source",
        )
        target = repo.create_target(
            project_id=project.id,
            label="GPU Workspace",
            workspace_server_id="ssh-target",
            remote_workdir="/srv/project-target",
            enabled=True,
            is_primary=False,
        )
        orchestration = build_run_orchestration(
            ProjectWorkflowType.sync_workspace,
            None,
            target_id=target.id,
            workspace_server_id=target.workspace_server_id,
            reset_stage_status=True,
        )
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.sync_workspace,
            title="remote to remote sync",
            prompt="sync remote workspace",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={
                "orchestration": orchestration,
                "stage_trace": build_stage_trace(orchestration, reset=True),
                "project_workspace_path": "/srv/project-source",
                "project_workspace_server_id": "ssh-source",
                "target_workspace_path": "/srv/project-target",
                "target_workspace_server_id": "ssh-target",
                "sync_strategy": "remote_bridge_copy",
            },
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
        run_id = run.id

    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.get_workspace_server_entry",
        lambda server_id: {"id": server_id, "host": f"{server_id}.example.com", "username": "tester", "enabled": True},
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.open_ssh_session",
        _fake_open_ssh_session_factory(remote_roots),
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.resolve_remote_workspace_path",
        lambda server_entry, requested_path, session: str(requested_path).replace("\\", "/"),
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.build_remote_overview",
        _fake_build_remote_overview_factory(remote_roots),
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_write_file",
        lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        },
    )

    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.sync_workspace.value
    assert (target_root / "src" / "train.py").exists()
    assert (target_root / "README.md").exists()
    assert (target_root / "src" / "train.py").read_text(encoding="utf-8") == "print('remote sync ok')\n"


def test_multi_agent_runner_monitor_experiment_collects_screen_state(monkeypatch):
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name="Monitor Remote Test",
            description="monitor remote session state",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/monitor-test",
        )
        target = repo.ensure_default_target(project.id)
        assert target is not None
        orchestration = build_run_orchestration(
            ProjectWorkflowType.monitor_experiment,
            None,
            target_id=target.id,
            workspace_server_id=target.workspace_server_id,
            reset_stage_status=True,
        )
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.monitor_experiment,
            title="monitor remote run",
            prompt="monitor the detached experiment",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={
                "orchestration": orchestration,
                "stage_trace": build_stage_trace(orchestration, reset=True),
                "remote_session_name": "aris-run-remote01",
            },
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
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
        return LLMResult(content=f"# {stage}\n\n- ok\n")

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- run.log\n- outputs/\n",
            "files": ["run.log", "outputs/metrics.json"],
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
            stdout = "tail: epoch=3 loss=0.08"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        name = session_name or "aris-run-remote01"
        return {
            "command": "screen -ls",
            "stdout": f"8123.{name} (Detached)",
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": [{"pid": 8123, "name": name, "state": "Detached"}],
            "session_count": 1,
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": "step=320 val_acc=0.91",
            "stderr": "",
            "success": True,
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
                    "memory_used_mb": 180,
                    "memory_total_mb": 81920,
                    "utilization_gpu_pct": 3,
                },
                {
                    "index": 1,
                    "name": "A100",
                    "memory_used_mb": 6200,
                    "memory_total_mb": 81920,
                    "utilization_gpu_pct": 88,
                },
            ],
            "reason": None,
        }

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize,
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
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
        "packages.ai.project.multi_agent_runner.build_remote_overview",
        _fake_build_remote_overview,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.build_remote_overview",
        _fake_build_remote_overview,
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.remote_terminal_result",
        _fake_remote_terminal_result,
    )
    monkeypatch.setattr(
        "packages.ai.project.workflow_runner.remote_terminal_result",
        _fake_remote_terminal_result,
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.remote_list_screen_sessions",
        _fake_list_screen_sessions,
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.remote_capture_screen_session",
        _fake_capture_screen_session,
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.remote_probe_gpus",
        _fake_probe_gpus,
    )
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.remote_read_file",
        lambda server_entry, requested_path, relative_path, *, max_chars: {
            "workspace_path": requested_path,
            "relative_path": relative_path,
            "content": json.dumps({"status": "running", "accuracy": 0.91})[:max_chars],
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

    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.monitor_experiment.value

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        metadata = dict(run.metadata_json or {})
        stage_outputs = metadata.get("stage_outputs") or {}
        inspect_stage = stage_outputs["inspect_runs"]
        collect_stage = stage_outputs["collect_signals"]
        assert "aris-run-remote01" in str(inspect_stage.get("content") or "")
        assert "Tracked Session" in str(collect_stage.get("content") or "")
        assert "Detached" in str(collect_stage.get("content") or "")
        assert "GPU 0" in str(collect_stage.get("content") or "")


def test_multi_agent_runner_monitor_experiment_collects_multiple_screen_sessions(monkeypatch):
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name="Monitor Remote Batch Test",
            description="monitor multiple remote sessions",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/monitor-batch-test",
        )
        target = repo.ensure_default_target(project.id)
        assert target is not None
        orchestration = build_run_orchestration(
            ProjectWorkflowType.monitor_experiment,
            None,
            target_id=target.id,
            workspace_server_id=target.workspace_server_id,
            reset_stage_status=True,
        )
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.monitor_experiment,
            title="monitor remote batch run",
            prompt="monitor detached batch experiments",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={
                "orchestration": orchestration,
                "stage_trace": build_stage_trace(orchestration, reset=True),
                "remote_session_name": "aris-run-batch01",
                "remote_session_names": ["aris-run-batch01-a", "aris-run-batch01-b"],
                "remote_experiments": [
                    {"name": "baseline", "remote_session_name": "aris-run-batch01-a"},
                    {"name": "improved", "remote_session_name": "aris-run-batch01-b"},
                ],
            },
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
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
        return LLMResult(content=f"# {stage}\n\n- ok\n")

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- run.log\n- outputs/\n",
            "files": ["run.log", "outputs/metrics.json"],
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
            stdout = "tail: step=88 val_acc=0.93"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = [
            {"pid": 9101, "name": "aris-run-batch01-a", "state": "Detached"},
            {"pid": 9102, "name": "aris-run-batch01-b", "state": "Detached"},
        ]
        if session_name:
            sessions = [item for item in sessions if item["name"] == session_name]
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
            "stdout": f"{session_name}: progress=ok",
            "stderr": "",
            "success": True,
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 180, "memory_total_mb": 81920, "utilization_gpu_pct": 3},
                {"index": 1, "name": "A100", "memory_used_mb": 220, "memory_total_mb": 81920, "utilization_gpu_pct": 5},
            ],
            "reason": None,
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
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
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.build_remote_overview", _fake_build_remote_overview)
    monkeypatch.setattr("packages.ai.project.workflow_runner.build_remote_overview", _fake_build_remote_overview)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_terminal_result", _fake_remote_terminal_result)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_terminal_result", _fake_remote_terminal_result)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_list_screen_sessions", _fake_list_screen_sessions)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_capture_screen_session", _fake_capture_screen_session)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_probe_gpus", _fake_probe_gpus)
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.remote_read_file",
        lambda server_entry, requested_path, relative_path, *, max_chars: {
            "workspace_path": requested_path,
            "relative_path": relative_path,
            "content": json.dumps({"status": "running", "accuracy": 0.93})[:max_chars],
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

    run_multi_agent_project_workflow(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        collect_stage = (metadata.get("stage_outputs") or {})["collect_signals"]
        assert "Tracked Sessions" in str(collect_stage.get("content") or "")
        assert "aris-run-batch01-a" in str(collect_stage.get("content") or "")
        assert "aris-run-batch01-b" in str(collect_stage.get("content") or "")


def test_multi_agent_runner_monitor_experiment_collects_structured_results(monkeypatch):
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name="Structured Monitor Test",
            description="collect result summaries and checkpoints",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/structured-monitor",
        )
        target = repo.ensure_default_target(project.id)
        assert target is not None
        orchestration = build_run_orchestration(
            ProjectWorkflowType.monitor_experiment,
            None,
            target_id=target.id,
            workspace_server_id=target.workspace_server_id,
            reset_stage_status=True,
        )
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.monitor_experiment,
            title="structured monitor run",
            prompt="collect structured experiment outputs",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={
                "orchestration": orchestration,
                "stage_trace": build_stage_trace(orchestration, reset=True),
                "remote_session_name": "aris-structured-monitor",
                "remote_session_names": ["aris-structured-monitor-baseline", "aris-structured-monitor-improved"],
                "remote_experiments": [
                    {"name": "baseline", "remote_session_name": "aris-structured-monitor-baseline"},
                    {"name": "improved", "remote_session_name": "aris-structured-monitor-improved"},
                ],
            },
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
        run_id = run.id

    file_payloads = {
        "outputs/baseline/results.json": json.dumps({"status": "done", "accuracy": 0.82, "loss": 0.43}),
        "outputs/improved/results.json": json.dumps({"status": "done", "accuracy": 0.87, "loss": 0.39}),
        "wandb/run-123/files/wandb-summary.json": json.dumps({"best_accuracy": 0.87, "best_loss": 0.39}),
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
        return LLMResult(content=f"# {stage}\n\n- ok\n")

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": (
                f"{requested_path}\n"
                "- outputs/\n"
                "  - baseline/\n"
                "    - results.json\n"
                "  - improved/\n"
                "    - results.json\n"
                "- checkpoints/\n"
                "  - epoch-3.ckpt\n"
                "- tensorboard/\n"
                "  - events.out.tfevents.123\n"
                "- wandb/\n"
                "  - run-123/\n"
                "    - files/\n"
                "      - wandb-summary.json\n"
            ),
            "files": [
                "outputs/baseline/results.json",
                "outputs/improved/results.json",
                "checkpoints/epoch-3.ckpt",
                "tensorboard/events.out.tfevents.123",
                "wandb/run-123/files/wandb-summary.json",
                "run.log",
            ],
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
            stdout = "tail: epoch=3 accuracy=0.87"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = [
            {"pid": 9101, "name": "aris-structured-monitor-baseline", "state": "Detached"},
            {"pid": 9102, "name": "aris-structured-monitor-improved", "state": "Detached"},
        ]
        if session_name:
            sessions = [item for item in sessions if item["name"] == session_name]
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
            "stdout": f"{session_name}: accuracy=0.87",
            "stderr": "",
            "success": True,
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 220, "memory_total_mb": 81920, "utilization_gpu_pct": 8},
            ],
            "reason": None,
        }

    monkeypatch.setattr("packages.integrations.llm_client.LLMClient.summarize_text", _fake_summarize)
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.get_workspace_server_entry",
        lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        },
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
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.build_remote_overview", _fake_build_remote_overview)
    monkeypatch.setattr("packages.ai.project.workflow_runner.build_remote_overview", _fake_build_remote_overview)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_terminal_result", _fake_remote_terminal_result)
    monkeypatch.setattr("packages.ai.project.workflow_runner.remote_terminal_result", _fake_remote_terminal_result)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_list_screen_sessions", _fake_list_screen_sessions)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_capture_screen_session", _fake_capture_screen_session)
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.remote_probe_gpus", _fake_probe_gpus)
    monkeypatch.setattr(
        "packages.ai.project.multi_agent_runner.remote_read_file",
        lambda server_entry, requested_path, relative_path, *, max_chars: {
            "workspace_path": requested_path,
            "relative_path": relative_path,
            "content": file_payloads[str(relative_path)][:max_chars],
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

    result = run_multi_agent_project_workflow(run_id)

    assert result["workflow_type"] == ProjectWorkflowType.monitor_experiment.value

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == ProjectRunStatus.succeeded
        metadata = dict(run.metadata_json or {})
        stage_outputs = metadata.get("stage_outputs") or {}
        collect_stage = stage_outputs["collect_signals"]
        content = str(collect_stage.get("content") or "")
        assert "Result Comparison" in content
        assert "TensorBoard" in content
        assert "Checkpoints" in content
        assert "Weights & Biases" in content
        assert "+0.05" in content or "+0.0500" in content
