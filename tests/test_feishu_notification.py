from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project import notification_service as project_notification_service
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.domain.task_tracker import global_tracker
from packages.integrations import feishu_service
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import FeishuConfigRepository, ProjectRepository


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def test_feishu_config_repository_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        repo = FeishuConfigRepository(session)
        assert repo.get_active() is None
        updated = repo.upsert_active(
            mode="push",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/demo",
            webhook_secret="secret-demo",
            bridge_url=None,
            timeout_seconds=45,
            timeout_action="wait",
        )
        updated_snapshot = {
            "mode": updated.mode,
            "webhook_url": updated.webhook_url,
            "webhook_secret": updated.webhook_secret,
            "timeout_seconds": updated.timeout_seconds,
            "timeout_action": updated.timeout_action,
        }
        current = repo.get_active()
        current_snapshot = None if current is None else {
            "mode": current.mode,
            "timeout_seconds": current.timeout_seconds,
            "timeout_action": current.timeout_action,
        }

    assert updated_snapshot == {
        "mode": "push",
        "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/demo",
        "webhook_secret": "secret-demo",
        "timeout_seconds": 45,
        "timeout_action": "wait",
    }
    assert current_snapshot == {
        "mode": "push",
        "timeout_seconds": 45,
        "timeout_action": "wait",
    }


def test_feishu_service_off_mode_short_circuits() -> None:
    service = feishu_service.FeishuNotificationService(mode="off")
    result = service.send_event(
        event_type="custom",
        title="noop",
        body="noop",
    )

    assert result == {"sent": False, "reason": "mode_off"}


def test_notify_project_run_status_sends_feishu_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="Checkpoint Project",
            description="notification test",
            workdir="D:/tmp/researchos-feishu-test",
        )
        target = project_repo.ensure_default_target(project.id)
        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.full_pipeline,
            prompt="continue the pipeline",
            title="Main Pipeline",
            status=ProjectRunStatus.paused,
            active_phase="awaiting_checkpoint",
            summary="waiting for checkpoint approval",
            workdir=project.workdir,
            metadata={
                "notification_recipients": ["reviewer@example.com"],
                "pending_checkpoint": {
                    "type": "stage_transition",
                    "completed_stage_id": "review_prior_work",
                    "completed_stage_label": "回顾已有工作",
                    "resume_stage_id": "implement_and_run",
                    "resume_stage_label": "实现与实验",
                    "stage_summary": "现有工作和数据依赖已经确认，可以进入实现阶段。",
                },
            },
        )
        FeishuConfigRepository(session).upsert_active(
            mode="push",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/demo",
            webhook_secret=None,
            bridge_url=None,
            timeout_seconds=300,
            timeout_action="approve",
        )
        run_id = run.id

    captured: dict[str, dict] = {}

    class FakeFeishuNotificationService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.mode = kwargs.get("mode")
            self.bridge_url = kwargs.get("bridge_url")

        def send_event(self, **kwargs):
            captured["event"] = kwargs
            return {"sent": True, "channel": "feishu"}

    monkeypatch.setattr(project_notification_service, "FeishuNotificationService", FakeFeishuNotificationService)

    result = project_notification_service.notify_project_run_status(run_id, "paused")
    assert result["sent"] is True
    assert captured["init"]["mode"] == "push"
    assert captured["event"]["event_type"] == "checkpoint"
    assert captured["event"]["options"] == ["approve", "reject"]
    assert "阶段确认" in captured["event"]["body"]
    assert "回顾已有工作" in captured["event"]["body"]
    assert "实现与实验" in captured["event"]["body"]


def test_notify_project_run_status_interactive_starts_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="Interactive Checkpoint Project",
            description="notification test",
            workdir="D:/tmp/researchos-feishu-interactive",
        )
        target = project_repo.ensure_default_target(project.id)
        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.literature_review,
            prompt="continue the review",
            title="Interactive Review",
            status=ProjectRunStatus.paused,
            active_phase="awaiting_checkpoint",
            summary="waiting for interactive checkpoint approval",
            workdir=project.workdir,
            metadata={
                "notification_recipients": ["reviewer@example.com"],
                "checkpoint_requested_at": "2026-03-18T11:00:00+00:00",
                "pending_checkpoint": {
                    "type": "preflight",
                    "status": "pending",
                    "requested_at": "2026-03-18T11:00:00+00:00",
                    "message": "等待飞书审批",
                },
            },
        )
        FeishuConfigRepository(session).upsert_active(
            mode="interactive",
            webhook_url=None,
            webhook_secret=None,
            bridge_url="http://127.0.0.1:9000",
            timeout_seconds=120,
            timeout_action="approve",
        )
        run_id = run.id

    captured: dict[str, dict] = {}

    class FakeFeishuNotificationService:
        def __init__(self, **kwargs):
            self.mode = kwargs["mode"]
            self.bridge_url = kwargs["bridge_url"]
            self.timeout_seconds = kwargs["timeout_seconds"]

        def send_event(self, **kwargs):
            captured["event"] = kwargs
            return {"sent": True, "bridge_sent": True}

    def _fake_start_waiter(*, run_id: str, metadata: dict, service) -> bool:
        captured["waiter"] = {
            "run_id": run_id,
            "requested_at": metadata.get("checkpoint_requested_at"),
            "timeout_seconds": service.timeout_seconds,
        }
        return True

    monkeypatch.setattr(project_notification_service, "FeishuNotificationService", FakeFeishuNotificationService)
    monkeypatch.setattr(project_notification_service, "_start_interactive_checkpoint_waiter", _fake_start_waiter)

    result = project_notification_service.notify_project_run_status(run_id, "paused")
    assert result["sent"] is True
    assert result["feishu"]["waiter_started"] is True
    assert captured["event"]["context"]["run_id"] == run_id
    assert captured["waiter"]["run_id"] == run_id
    assert captured["waiter"]["timeout_seconds"] == 120


def test_await_interactive_checkpoint_reply_processes_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="Interactive Reply Project",
            description="notification test",
            workdir="D:/tmp/researchos-feishu-reply",
        )
        target = project_repo.ensure_default_target(project.id)
        run = project_repo.create_run(
            project_id=project.id,
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.literature_review,
            prompt="continue the review",
            title="Interactive Reply",
            status=ProjectRunStatus.paused,
            active_phase="awaiting_checkpoint",
            summary="waiting for interactive reply",
            task_id="interactive-feishu-task",
            workdir=project.workdir,
            metadata={
                "human_checkpoint_enabled": True,
                "checkpoint_state": "pending",
                "pending_checkpoint": {
                    "type": "preflight",
                    "status": "pending",
                    "requested_at": "2026-03-18T12:00:00+00:00",
                    "message": "等待飞书审批",
                },
            },
        )
        run_id = run.id

    global_tracker.start("interactive-feishu-task", "project_workflow", "interactive reply")

    messages: list[tuple[str, str]] = []
    handled: dict[str, str | None] = {}

    class FakeInteractiveService:
        timeout_seconds = 120

        def poll_reply(self):
            return {"ok": True, "reply": "approve: 继续执行"}

    monkeypatch.setattr(
        project_notification_service,
        "_append_checkpoint_log",
        lambda run_id, message, level="info": messages.append((level, message)),
    )

    def _fake_process_checkpoint_response(run_id: str, *, action: str, comment: str | None = None, response_source: str | None = None):
        handled["run_id"] = run_id
        handled["action"] = action
        handled["comment"] = comment
        handled["response_source"] = response_source
        return {"action": action}

    monkeypatch.setattr("packages.ai.project.checkpoint_service.process_checkpoint_response", _fake_process_checkpoint_response)

    project_notification_service._await_interactive_checkpoint_reply(
        run_id,
        f"{run_id}:2026-03-18T12:00:00+00:00",
        FakeInteractiveService(),
    )

    assert handled["run_id"] == run_id
    assert handled["action"] == "approve"
    assert handled["comment"] == "继续执行"
    assert handled["response_source"] == "feishu_interactive"
    assert any("已收到飞书审批结果" in message for _, message in messages)


def test_feishu_service_poll_reply_returns_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"timeout": True}).encode("utf-8")

    monkeypatch.setattr(feishu_service.request, "urlopen", lambda req, timeout=0: FakeResponse())

    service = feishu_service.FeishuNotificationService(
        mode="interactive",
        bridge_url="http://127.0.0.1:9000",
        timeout_seconds=45,
    )
    result = service.poll_reply()

    assert result["ok"] is True
    assert result["timeout"] is True
    assert result["reply"] is None


def test_interactive_timeout_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, str | None] = {}
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        project_notification_service,
        "_append_checkpoint_log",
        lambda run_id, message, level="info": messages.append((level, message)),
    )

    def _fake_process_checkpoint_response(run_id: str, *, action: str, comment: str | None = None, response_source: str | None = None):
        calls["run_id"] = run_id
        calls["action"] = action
        calls["comment"] = comment
        calls["response_source"] = response_source
        return {"action": action}

    monkeypatch.setattr("packages.ai.project.checkpoint_service.process_checkpoint_response", _fake_process_checkpoint_response)

    service = feishu_service.FeishuNotificationService(
        mode="interactive",
        bridge_url="http://127.0.0.1:9000",
        timeout_seconds=60,
        timeout_action="approve",
    )
    project_notification_service._handle_interactive_timeout("run-timeout-approve", service)

    assert calls["run_id"] == "run-timeout-approve"
    assert calls["action"] == "approve"
    assert calls["response_source"] == "feishu_timeout_approve"
    assert any("AUTO_PROCEED" not in msg and "自动批准继续" in msg for _, msg in messages)


def test_interactive_timeout_wait_keeps_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        project_notification_service,
        "_append_checkpoint_log",
        lambda run_id, message, level="info": messages.append((level, message)),
    )

    def _raise_if_called(*args, **kwargs):
        raise AssertionError("process_checkpoint_response should not be called in wait mode")

    monkeypatch.setattr("packages.ai.project.checkpoint_service.process_checkpoint_response", _raise_if_called)

    service = feishu_service.FeishuNotificationService(
        mode="interactive",
        bridge_url="http://127.0.0.1:9000",
        timeout_seconds=60,
        timeout_action="wait",
    )
    project_notification_service._handle_interactive_timeout("run-timeout-wait", service)

    assert any("保持暂停" in msg for _, msg in messages)


def test_feishu_service_marks_nonzero_code_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"code": 19024, "msg": "invalid webhook"}).encode("utf-8")

    monkeypatch.setattr(feishu_service.request, "urlopen", lambda req, timeout=0: FakeResponse())

    service = feishu_service.FeishuNotificationService(
        mode="push",
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/demo",
        timeout_seconds=77,
    )
    result = service.send_event(
        event_type="custom",
        title="Test",
        body="Test body",
    )

    assert result["sent"] is False
    assert str(result["reason"]).startswith("feishu_error:")
