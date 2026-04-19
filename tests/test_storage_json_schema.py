from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select

from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.json_schema import with_schema_version
from packages.storage.models import TaskLog
from packages.storage.task_repository import TaskRepository


def _configure_test_db() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    db.engine = engine
    db.SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_with_schema_version_preserves_existing_version() -> None:
    assert with_schema_version({"schema_version": 3, "value": "x"}) == {
        "schema_version": 3,
        "value": "x",
    }
    assert with_schema_version({"value": "x"}) == {"schema_version": 1, "value": "x"}


def test_task_repository_writes_versioned_sidecar_log_rows() -> None:
    _configure_test_db()
    now = datetime.now(UTC)

    with session_scope() as session:
        repo = TaskRepository(session)
        repo.upsert_task(
            task_id="task_1",
            task_type="demo",
            title="Demo",
            current=1,
            total=2,
            message="running",
            status="running",
            finished=False,
            success=True,
            error=None,
            result_json=None,
            cancel_requested=False,
            cancelled=False,
            progress_pct=50,
            source=None,
            source_id=None,
            project_id=None,
            paper_id=None,
            run_id=None,
            action_id=None,
            log_path=None,
            artifact_refs_json=[{"path": "x"}],
            metadata_json={"owner": "test"},
            logs_json=[{"level": "info", "message": "hello"}],
            retry_supported=False,
            retry_label=None,
            retry_metadata_json={},
            started_at=now,
            updated_at=now,
            finished_at=None,
        )
        task = repo.get_task("task_1")
        assert task is not None
        assert task.metadata_json["schema_version"] == 1
        assert task.artifact_refs_json[0]["schema_version"] == 1
        assert task.logs_json[0]["schema_version"] == 1
        logs = session.execute(select(TaskLog).where(TaskLog.task_id == "task_1")).scalars().all()
        assert len(logs) == 1
        assert logs[0].level == "info"
        assert logs[0].message == "hello"

