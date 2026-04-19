from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project import run_action_service as action_service
from packages.ai.project.amadeus_compat import build_action_log_path, build_action_result_path
from packages.domain.enums import ProjectRunActionType, ProjectRunStatus, ProjectWorkflowType
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


def _seed_action(tmp_path: Path, *, remote: bool = False) -> tuple[str, str, str, str]:
    with db.session_scope() as session:
        repo = ProjectRepository(session)
        if remote:
            project = repo.create_project(
                name="Action Remote Test",
                description="remote action regression",
                workspace_server_id="ssh-main",
                remote_workdir="/srv/research/action-remote-test",
            )
            run_root = "/srv/research/action-remote-test/.auto-researcher/aris-runs"
        else:
            local_root = tmp_path / "action-local-test"
            project = repo.create_project(
                name="Action Local Test",
                description="local action regression",
                workdir=str(local_root),
            )
            run_root = str(local_root / ".auto-researcher" / "aris-runs")

        run = repo.create_run(
            project_id=project.id,
            workflow_type=ProjectWorkflowType.literature_review,
            title="action run",
            prompt="continue the current literature review",
            status=ProjectRunStatus.running,
            active_phase="completed",
            summary="workflow finished",
            workspace_server_id=project.workspace_server_id,
            workdir=project.workdir,
            remote_workdir=project.remote_workdir,
            run_directory=f"{run_root}/run-001",
            log_path=f"{run_root}/run-001/run.log",
            executor_model="mock-executor",
            reviewer_model="mock-reviewer",
            metadata={"workflow_output_markdown": "# Existing output"},
        )
        action = repo.create_run_action(
            run_id=run.id,
            action_type=ProjectRunActionType.review,
            prompt="tighten the structure and surface blockers",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
        )
        return project.id, run.id, action.id, run.run_directory or ""


class _TrackerStub:
    def __init__(self) -> None:
        self.submits: list[dict] = []
        self.metadata_updates: list[tuple[str, dict]] = []
        self.results: list[tuple[str, dict]] = []

    def submit(self, task_type, title, fn, *args, **kwargs):
        payload = {
            "task_type": task_type,
            "title": title,
            "fn": fn,
            "args": args,
            "kwargs": kwargs,
        }
        self.submits.append(payload)
        return kwargs.get("task_id") or "task-action-stub"

    def set_metadata(self, task_id: str, metadata: dict | None = None, **extra):
        merged = dict(metadata or {})
        merged.update(extra)
        self.metadata_updates.append((task_id, merged))

    def set_result(self, task_id: str, result):
        self.results.append((task_id, result))

    def is_cancel_requested(self, _task_id: str) -> bool:
        return False


def test_submit_project_run_action_registers_tracker_metadata(monkeypatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    project_id, run_id, action_id, run_directory = _seed_action(tmp_path, remote=False)
    tracker = _TrackerStub()
    monkeypatch.setattr(action_service, "global_tracker", tracker)

    task_id = action_service.submit_project_run_action(action_id)

    assert tracker.submits
    submit = tracker.submits[0]
    metadata = submit["kwargs"]["metadata"]
    assert task_id == submit["kwargs"]["task_id"]
    assert metadata["source"] == "project"
    assert metadata["project_id"] == project_id
    assert metadata["run_id"] == run_id
    assert metadata["action_id"] == action_id
    assert metadata["workspace_path"] == run_directory
    assert metadata["run_directory"] == run_directory
    assert metadata["workspace_server_id"] == "local"
    assert metadata["result_path"].endswith(f"{action_id}.md")
    assert metadata["log_path"].endswith(f"{action_id}.log")

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        action = repo.get_run_action(action_id)
        assert action is not None
        assert action.task_id == task_id
        assert action.status == ProjectRunStatus.running
        assert action.result_path == build_action_result_path(run_directory, action_id, remote=False)
        assert action.log_path == build_action_log_path(run_directory, action_id, remote=False)


def test_run_project_run_action_writes_remote_files_and_updates_task_metadata(monkeypatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    project_id, run_id, action_id, run_directory = _seed_action(tmp_path, remote=True)
    tracker = _TrackerStub()
    monkeypatch.setattr(action_service, "global_tracker", tracker)

    captured_writes: list[dict] = []

    def _fake_remote_write_file(server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True):
        captured_writes.append(
            {
                "server_entry": server_entry,
                "path": path,
                "relative_path": relative_path,
                "content": content,
                "create_dirs": create_dirs,
                "overwrite": overwrite,
            }
        )
        return {"relative_path": relative_path, "size_bytes": len(content.encode("utf-8"))}

    submitted_run_ids: list[str] = []

    def _fake_submit_project_run(run_id: str):
        submitted_run_ids.append(run_id)
        with db.session_scope() as session:
            repo = ProjectRepository(session)
            run = repo.get_run(run_id)
            assert run is not None
            repo.update_run(
                run_id,
                task_id="task-child-run-1",
                status=ProjectRunStatus.running,
                active_phase="queued",
            )
        return "task-child-run-1"

    monkeypatch.setattr(action_service, "get_workspace_server_entry", lambda server_id: {"id": server_id})
    monkeypatch.setattr(action_service, "remote_write_file", _fake_remote_write_file)
    monkeypatch.setattr(action_service, "submit_project_run", _fake_submit_project_run)

    task_id = action_service.submit_project_run_action(action_id)
    result = action_service.run_project_run_action(action_id)

    assert result["run_id"] == run_id
    assert result["spawned_run_id"] in submitted_run_ids
    assert result["spawned_workflow_type"] == ProjectWorkflowType.research_review.value
    assert result["result_path"].endswith(f"{action_id}.md")
    assert len(captured_writes) == 2
    assert captured_writes[0]["path"] == "/srv/research/action-remote-test"
    assert captured_writes[0]["relative_path"] == f".auto-researcher/aris-runs/run-001/actions/{action_id}.md"
    assert captured_writes[1]["relative_path"] == f".auto-researcher/aris-runs/run-001/actions/{action_id}.log"

    assert tracker.metadata_updates
    updated_task_id, metadata = tracker.metadata_updates[-1]
    assert updated_task_id == task_id
    assert metadata["project_id"] == project_id
    assert metadata["run_id"] == run_id
    assert metadata["action_id"] == action_id
    assert metadata["workspace_path"] == run_directory
    assert metadata["workspace_server_id"] == "ssh-main"
    assert metadata["spawned_run_id"] == result["spawned_run_id"]
    assert len(metadata["artifact_refs"]) == 2
    assert metadata["artifact_refs"][0]["relative_path"] == f"actions/{action_id}.md"
    assert metadata["artifact_refs"][1]["relative_path"] == f"actions/{action_id}.log"

    assert tracker.results
    assert tracker.results[-1][0] == task_id
    assert tracker.results[-1][1]["artifact_refs"][0]["relative_path"] == f"actions/{action_id}.md"

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        action = repo.get_run_action(action_id)
        assert action is not None
        assert action.status == ProjectRunStatus.succeeded
        assert action.result_path == build_action_result_path(run_directory, action_id, remote=True)
        assert action.log_path == build_action_log_path(run_directory, action_id, remote=True)
        assert len((action.metadata_json or {}).get("artifact_refs") or []) == 2
        assert (action.metadata_json or {}).get("source_skill") == "research-review"
        assert (action.metadata_json or {}).get("spawned_run_id") == result["spawned_run_id"]
        spawned = repo.get_run(str((action.metadata_json or {}).get("spawned_run_id") or ""))
        assert spawned is not None
        assert spawned.workflow_type == ProjectWorkflowType.research_review
