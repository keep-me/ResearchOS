from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project import execution_service as project_execution_service
from packages.ai.project.checkpoint_service import CHECKPOINT_PENDING_MESSAGE
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.domain.task_tracker import global_tracker
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


def _seed_run(workflow_type: ProjectWorkflowType, metadata: dict | None = None) -> str:
    with db.session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name=f"Execution Service {workflow_type.value}",
            description="routing test",
            workdir="D:/tmp/researchos-execution-test",
        )
        target = repo.ensure_default_target(project.id)
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id if target else None,
            workflow_type=workflow_type,
            title="routing run",
            prompt="test routing",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id if target else None,
            workdir=target.workdir if target else project.workdir,
            remote_workdir=target.remote_workdir if target else None,
            metadata=metadata or {},
        )
        return run.id


def test_submit_project_run_prefers_native_for_active_workflows(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(ProjectWorkflowType.literature_review)

    calls = {"native": 0, "multi": 0}

    def _native(_run_id: str, *, resume_stage_id: str | None = None):
        calls["native"] += 1
        assert resume_stage_id is None
        return "native-task-id"

    def _multi(_run_id: str):
        calls["multi"] += 1
        return "multi-task-id"

    monkeypatch.setattr(project_execution_service, "submit_native_project_run", _native)
    monkeypatch.setattr(project_execution_service, "submit_multi_agent_project_run", _multi)

    task_id = project_execution_service.submit_project_run(run_id)
    assert task_id == "native-task-id"
    assert calls["native"] == 1
    assert calls["multi"] == 0


def test_submit_project_run_keeps_native_for_active_workflows_even_with_role_overrides(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.literature_review,
        metadata={
            "orchestration": {
                "stages": [
                    {"id": "collect_context", "selected_agent_id": "codex"},
                ]
            }
        },
    )

    calls = {"native": 0, "multi": 0}

    def _native(_run_id: str, *, resume_stage_id: str | None = None):
        calls["native"] += 1
        assert resume_stage_id is None
        return "native-task-id"

    def _multi(_run_id: str):
        calls["multi"] += 1
        return "multi-task-id"

    monkeypatch.setattr(project_execution_service, "submit_native_project_run", _native)
    monkeypatch.setattr(project_execution_service, "submit_multi_agent_project_run", _multi)

    task_id = project_execution_service.submit_project_run(run_id)
    assert task_id == "native-task-id"
    assert calls["native"] == 1
    assert calls["multi"] == 0


def test_submit_project_run_uses_multi_agent_for_planned_workflows(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(ProjectWorkflowType.init_repo)

    calls = {"native": 0, "multi": 0}

    def _native(_run_id: str, *, resume_stage_id: str | None = None):
        calls["native"] += 1
        assert resume_stage_id is None
        return "native-task-id"

    def _multi(_run_id: str, *, resume_stage_id: str | None = None):
        calls["multi"] += 1
        assert resume_stage_id is None
        return "multi-task-id"

    monkeypatch.setattr(project_execution_service, "submit_native_project_run", _native)
    monkeypatch.setattr(project_execution_service, "submit_multi_agent_project_run", _multi)

    task_id = project_execution_service.submit_project_run(run_id)
    assert task_id == "multi-task-id"
    assert calls["native"] == 0
    assert calls["multi"] == 1


def test_submit_project_run_resumes_multi_agent_from_stage_checkpoint(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.paper_plan,
        metadata={
            "human_checkpoint_enabled": True,
            "checkpoint_state": "approved",
            "checkpoint_resume_stage_id": "outline_manuscript",
            "checkpoint_resume_stage_label": "生成提纲",
        },
    )

    calls: list[tuple[str, str | None]] = []

    def _multi(_run_id: str, *, resume_stage_id: str | None = None):
        calls.append((_run_id, resume_stage_id))
        return "multi-task-resume"

    monkeypatch.setattr(
        project_execution_service,
        "submit_native_project_run",
        lambda _run_id, *, resume_stage_id=None: "unused",
    )
    monkeypatch.setattr(project_execution_service, "submit_multi_agent_project_run", _multi)

    task_id = project_execution_service.submit_project_run(run_id)

    assert task_id == "multi-task-resume"
    assert calls == [(run_id, "outline_manuscript")]


def test_submit_project_run_pauses_for_preflight_checkpoint(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.literature_review,
        metadata={
            "human_checkpoint_enabled": True,
            "notification_recipients": ["reviewer@example.com"],
        },
    )

    calls = {"native": 0, "multi": 0, "notify": 0}

    monkeypatch.setattr(
        project_execution_service,
        "submit_native_project_run",
        lambda _run_id, *, resume_stage_id=None: calls.__setitem__("native", calls["native"] + 1),
    )
    monkeypatch.setattr(
        project_execution_service,
        "submit_multi_agent_project_run",
        lambda _run_id: calls.__setitem__("multi", calls["multi"] + 1),
    )
    monkeypatch.setattr(
        "packages.ai.project.checkpoint_service.notify_project_run_status",
        lambda run_id, event: (
            calls.__setitem__("notify", calls["notify"] + 1) or {"sent": True, "event": event}
        ),
    )

    task_id = project_execution_service.submit_project_run(run_id)

    with db.session_scope() as session:
        repo = ProjectRepository(session)
        run = repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        assert run.status == ProjectRunStatus.paused
        assert run.active_phase == "awaiting_checkpoint"
        assert metadata["checkpoint_state"] == "pending"
        assert metadata["pending_checkpoint"]["status"] == "pending"
        assert metadata["notification_recipients"] == ["reviewer@example.com"]

    task = global_tracker.get_task(task_id)
    assert task is not None
    assert task["status"] == "paused"
    assert task["message"] == CHECKPOINT_PENDING_MESSAGE
    assert calls["native"] == 0
    assert calls["multi"] == 0
    assert calls["notify"] == 1


def test_submit_project_run_dispatches_after_checkpoint_approval(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.literature_review,
        metadata={
            "human_checkpoint_enabled": True,
            "checkpoint_state": "approved",
            "notification_recipients": ["reviewer@example.com"],
        },
    )

    calls = {"native": 0}

    def _native(_run_id: str, *, resume_stage_id: str | None = None):
        calls["native"] += 1
        assert resume_stage_id is None
        return "native-task-approved"

    monkeypatch.setattr(project_execution_service, "submit_native_project_run", _native)
    monkeypatch.setattr(
        project_execution_service, "submit_multi_agent_project_run", lambda _run_id: "unused"
    )

    task_id = project_execution_service.submit_project_run(run_id)

    assert task_id == "native-task-approved"
    assert calls["native"] == 1


def test_submit_project_run_resumes_from_stage_checkpoint(monkeypatch):
    _configure_test_db(monkeypatch)
    run_id = _seed_run(
        ProjectWorkflowType.full_pipeline,
        metadata={
            "human_checkpoint_enabled": True,
            "checkpoint_state": "approved",
            "checkpoint_resume_stage_id": "implement_and_run",
            "checkpoint_resume_stage_label": "实现与实验",
        },
    )

    calls: list[tuple[str, str | None]] = []

    def _native(_run_id: str, *, resume_stage_id: str | None = None):
        calls.append((_run_id, resume_stage_id))
        return "native-task-resume"

    monkeypatch.setattr(project_execution_service, "submit_native_project_run", _native)
    monkeypatch.setattr(
        project_execution_service, "submit_multi_agent_project_run", lambda _run_id: "unused"
    )

    task_id = project_execution_service.submit_project_run(run_id)

    assert task_id == "native-task-resume"
    assert calls == [(run_id, "implement_and_run")]
