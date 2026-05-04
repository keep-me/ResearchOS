from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project.amadeus_compat import (
    build_remote_session_name,
    build_run_directory,
    build_run_log_path,
    build_run_workspace_path,
)
from packages.ai.project.multi_agent_runner import submit_multi_agent_project_run
from packages.ai.project.workflow_runner import submit_project_run
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import ProjectRepository


def _configure_test_db(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _seed_run(workflow_type: ProjectWorkflowType) -> str:
    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name=f"Submit Regression {workflow_type.value}",
            description="tracker metadata regression",
            workdir="D:/tmp/researchos-submit-regression",
        )
        target = repo.ensure_default_target(project.id)
        assert target is not None
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=workflow_type,
            title="submit regression run",
            prompt="ensure tracker metadata can be built after session closes",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            workdir=target.workdir,
            remote_workdir=target.remote_workdir,
            metadata={},
        )
        run_directory = build_run_directory(target.workdir, run.id, remote=False)
        log_path = build_run_log_path(run_directory, remote=False)
        repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
        return run.id


def _seed_remote_run(workflow_type: ProjectWorkflowType) -> str:
    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name=f"Submit Regression Remote {workflow_type.value}",
            description="tracker metadata regression remote",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/researchos-submit-regression",
        )
        target = repo.ensure_default_target(project.id)
        assert target is not None
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=workflow_type,
            title="submit regression remote run",
            prompt="ensure tracker metadata includes remote execution fields",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            remote_workdir=target.remote_workdir,
            metadata={},
        )
        run_directory = build_run_directory(target.remote_workdir, run.id, remote=True)
        log_path = build_run_log_path(run_directory, remote=True)
        repo.update_run(
            run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata={
                "remote_session_name": build_remote_session_name(run.id),
                "remote_execution_workspace": build_run_workspace_path(run_directory, remote=True),
                "remote_isolation_mode": "pending",
                "gpu_mode": "auto",
                "gpu_strategy": "least_used_free",
            },
        )
        return run.id


class _TrackerStub:
    def __init__(self) -> None:
        self.submits: list[dict] = []
        self.retries: list[dict] = []

    def submit(self, task_type, title, fn, *args, **kwargs):
        self.submits.append(
            {
                "task_type": task_type,
                "title": title,
                "fn": fn,
                "args": args,
                "kwargs": kwargs,
            }
        )
        return kwargs.get("task_id") or "task-stub"

    def register_retry(self, task_id, callback, label=None, metadata=None):
        self.retries.append(
            {
                "task_id": task_id,
                "callback": callback,
                "label": label,
                "metadata": metadata or {},
            }
        )


def test_submit_project_run_builds_tracker_metadata_without_detached_instance(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(ProjectWorkflowType.literature_review)
    tracker = _TrackerStub()
    monkeypatch.setattr("packages.ai.project.workflow_runner.global_tracker", tracker)

    task_id = submit_project_run(run_id)

    assert task_id
    assert tracker.submits
    submit = tracker.submits[0]
    assert submit["kwargs"]["metadata"]["run_id"] == run_id
    assert submit["kwargs"]["metadata"]["workspace_server_id"] == "local"
    assert submit["kwargs"]["metadata"]["log_path"]
    assert tracker.retries[0]["metadata"]["run_id"] == run_id


def test_submit_multi_agent_project_run_builds_tracker_metadata_without_detached_instance(
    monkeypatch,
):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(ProjectWorkflowType.init_repo)
    tracker = _TrackerStub()
    monkeypatch.setattr("packages.ai.project.multi_agent_runner.global_tracker", tracker)

    task_id = submit_multi_agent_project_run(run_id)

    assert task_id
    assert tracker.submits
    submit = tracker.submits[0]
    assert submit["kwargs"]["metadata"]["run_id"] == run_id
    assert submit["kwargs"]["metadata"]["workspace_server_id"] == "local"
    assert submit["kwargs"]["metadata"]["log_path"]
    assert tracker.retries[0]["metadata"]["run_id"] == run_id


def test_submit_project_run_includes_remote_execution_metadata(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_remote_run(ProjectWorkflowType.run_experiment)
    tracker = _TrackerStub()
    monkeypatch.setattr("packages.ai.project.workflow_runner.global_tracker", tracker)

    task_id = submit_project_run(run_id)

    assert task_id
    submit = tracker.submits[0]
    metadata = submit["kwargs"]["metadata"]
    assert metadata["workspace_server_id"] == "ssh-main"
    assert metadata["remote_session_name"] == build_remote_session_name(run_id)
    assert metadata["remote_execution_workspace"].endswith("/workspace")
    assert metadata["remote_isolation_mode"] == "pending"
    assert metadata["gpu_mode"] == "auto"
    assert metadata["gpu_strategy"] == "least_used_free"
