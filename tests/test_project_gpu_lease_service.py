from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.project.gpu_lease_service import (
    acquire_gpu_lease,
    list_active_gpu_leases,
    reconcile_gpu_leases,
    release_gpu_lease,
)
from packages.storage import db
from packages.storage.db import Base


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


def test_gpu_lease_service_acquire_conflict_and_release(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)

    lease = acquire_gpu_lease(
        workspace_server_id="ssh-main",
        gpu_index=1,
        project_id="project-a",
        run_id="run-a",
        remote_session_name="aris-run-a",
        holder_title="Run A",
        metadata={"source": "test"},
    )
    assert lease["active"] is True
    assert lease["gpu_index"] == 1

    with pytest.raises(ValueError):
        acquire_gpu_lease(
            workspace_server_id="ssh-main",
            gpu_index=1,
            project_id="project-b",
            run_id="run-b",
            remote_session_name="aris-run-b",
            holder_title="Run B",
        )

    active = list_active_gpu_leases("ssh-main")
    assert len(active) == 1
    assert active[0]["run_id"] == "run-a"

    released = release_gpu_lease(
        workspace_server_id="ssh-main",
        gpu_index=1,
        run_id="run-a",
        remote_session_name="aris-run-a",
        reason="completed",
    )
    assert released is not None
    assert released["active"] is False
    assert released["release_reason"] == "completed"
    assert list_active_gpu_leases("ssh-main") == []


def test_gpu_lease_service_reconcile_releases_missing_sessions(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)

    acquire_gpu_lease(
        workspace_server_id="ssh-main",
        gpu_index=0,
        run_id="run-0",
        remote_session_name="aris-run-0",
        holder_title="Run 0",
    )
    acquire_gpu_lease(
        workspace_server_id="ssh-main",
        gpu_index=1,
        run_id="run-1",
        remote_session_name="aris-run-1",
        holder_title="Run 1",
    )

    state = reconcile_gpu_leases(
        workspace_server_id="ssh-main",
        active_session_names=["aris-run-1"],
    )

    assert len(state["released"]) == 1
    assert state["released"][0]["gpu_index"] == 0
    assert len(state["active"]) == 1
    assert state["active"][0]["gpu_index"] == 1
