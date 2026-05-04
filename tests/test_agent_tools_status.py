from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.agent.tools.research_tool_runtime import _get_system_status
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.repositories import PipelineRunRepository


def _configure_test_db() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_get_system_status_serializes_latest_runs_inside_session() -> None:
    _configure_test_db()

    with session_scope() as session:
        PipelineRunRepository(session).start("daily_brief")

    result = _get_system_status()

    assert result.success is True
    assert result.data["pipeline_run_count"] == 1
    assert result.data["latest_runs"][0]["pipeline_name"] == "daily_brief"
    assert result.data["latest_runs"][0]["created_at"]
