import time

from packages.domain.task_tracker import TaskTracker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.storage import db
from packages.storage.db import Base


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


def test_finish_without_total_reports_full_progress():
    tracker = TaskTracker()
    tracker.start("task-1", "demo", "Demo Task", total=0)
    tracker.finish("task-1", success=True)

    task = tracker.get_task("task-1")

    assert task is not None
    assert task["finished"] is True
    assert task["status"] == "completed"
    assert task["progress_pct"] == 100
    assert task["current"] == 1
    assert task["total"] == 1


def test_list_tasks_returns_latest_first_and_supports_filter():
    tracker = TaskTracker()
    tracker.start("task-1", "import", "Import One", total=10)
    tracker.update("task-1", 4, "running", total=10)
    tracker.finish("task-1", success=True)
    time.sleep(0.01)
    tracker.start("task-2", "skim", "Skim One", total=100)
    tracker.update("task-2", 10, "queued", total=100)

    all_tasks = tracker.list_tasks(limit=10)
    import_tasks = tracker.list_tasks(task_type="import", limit=10)

    assert all_tasks[0]["task_id"] == "task-2"
    assert len(import_tasks) == 1
    assert import_tasks[0]["task_type"] == "import"


def test_finished_task_elapsed_seconds_stops_at_finish():
    tracker = TaskTracker()
    tracker.start("task-elapsed", "demo", "Elapsed Task", total=10)
    time.sleep(0.02)
    tracker.finish("task-elapsed", success=True)
    first = tracker.get_task("task-elapsed")
    time.sleep(0.05)
    second = tracker.get_task("task-elapsed")

    assert first is not None
    assert second is not None
    assert first["finished"] is True
    assert second["finished"] is True
    assert abs(float(first["elapsed_seconds"]) - float(second["elapsed_seconds"])) < 0.05


def test_task_tracker_supports_logs_and_retry_metadata():
    tracker = TaskTracker()
    tracker.start(
        "task-1",
        "paper_analysis_rounds",
        "Paper Analysis",
        total=100,
        metadata={
            "paper_id": "paper-1",
            "workspace_path": "/tmp/project/run-1",
            "workspace_server_id": "ssh-main",
        },
    )
    tracker.append_log("task-1", "round 1 started")
    tracker.append_log("task-1", "round 2 started", level="debug")

    calls = {"count": 0}

    def _retry():
        calls["count"] += 1
        return "task-2"

    tracker.register_retry(
        "task-1",
        _retry,
        label="重新分析",
        metadata={"paper_id": "paper-1", "detail_level": "high"},
    )

    task = tracker.get_task("task-1")
    logs = tracker.list_logs("task-1", limit=10)
    retry_payload = tracker.retry("task-1")

    assert task is not None
    assert task["paper_id"] == "paper-1"
    assert task["retry_supported"] is True
    assert task["retry_label"] == "重新分析"
    assert task["log_count"] == 2
    assert task["metadata"]["workspace_path"] == "/tmp/project/run-1"
    assert task["metadata"]["workspace_server_id"] == "ssh-main"
    assert [item["message"] for item in logs] == ["round 1 started", "round 2 started"]
    assert retry_payload["next_task_id"] == "task-2"
    assert retry_payload["retry_metadata"]["detail_level"] == "high"
    assert calls["count"] == 1


def test_submit_marks_failed_result_status_as_failed(monkeypatch):
    monkeypatch.setattr(TaskTracker, "_sync_task", lambda self, task: None)
    monkeypatch.setattr(TaskTracker, "_load_persisted_task", lambda self, task_id: None)
    tracker = TaskTracker()

    tracker.submit(
        task_type="fetch",
        title="Fetch Topic",
        fn=lambda progress_callback=None: {
            "status": "failed",
            "error": "arXiv rate limit",
            "inserted": 0,
        },
        task_id="task-failed-result",
    )

    task = None
    for _ in range(20):
        task = tracker.get_task("task-failed-result")
        if task and task["finished"]:
            break
        time.sleep(0.05)

    assert task is not None
    assert task["finished"] is True
    assert task["success"] is False
    assert task["status"] == "failed"
    assert task["error"] == "arXiv rate limit"
    assert tracker.get_result("task-failed-result")["status"] == "failed"


def test_task_tracker_persists_tasks_and_results(monkeypatch):
    _configure_test_db(monkeypatch)
    tracker = TaskTracker()
    tracker.start("task-1", "sync", "Sync Workspace", total=10, metadata={"project_id": "project-1"})
    tracker.update("task-1", 6, "syncing", total=10)
    tracker.append_log("task-1", "sync started")
    tracker.set_result("task-1", {"status": "ok"})
    tracker.finish("task-1", success=True)

    restored = TaskTracker()
    task = restored.get_task("task-1")

    assert task is not None
    assert task["project_id"] == "project-1"
    assert task["status"] == "completed"
    assert restored.get_result("task-1") == {"status": "ok"}
    assert restored.list_logs("task-1", limit=10)[0]["message"] == "sync started"


def test_task_tracker_bootstrap_marks_running_tasks_interrupted(monkeypatch):
    _configure_test_db(monkeypatch)
    tracker = TaskTracker()
    tracker.start("task-boot", "project_workflow", "Workflow", total=100)
    tracker.update("task-boot", 12, "running", total=100)

    reloaded = TaskTracker()
    reloaded.bootstrap_from_store()
    task = reloaded.get_task("task-boot")

    assert task is not None
    assert task["finished"] is True
    assert task["status"] == "failed"
    assert "服务重启" in str(task["error"])


def test_task_tracker_pause_persists_and_survives_bootstrap(monkeypatch):
    _configure_test_db(monkeypatch)
    tracker = TaskTracker()
    tracker.start("task-pause", "project_workflow", "Workflow", total=100)
    tracker.pause("task-pause", message="等待人工确认")

    paused = tracker.get_task("task-pause")
    assert paused is not None
    assert paused["status"] == "paused"
    assert paused["finished"] is False

    reloaded = TaskTracker()
    reloaded.bootstrap_from_store()
    restored = reloaded.get_task("task-pause")

    assert restored is not None
    assert restored["status"] == "paused"
    assert restored["finished"] is False

    cancelled = reloaded.request_cancel("task-pause")
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
