from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apps.api.deps as api_deps
import packages.storage.db as db
import packages.timezone as timezone_utils
from packages.agent.workspace.workspace_executor import (
    WorkspaceAccessError,
    ensure_workspace_operation_allowed,
    get_assistant_exec_policy,
)
from packages.domain.enums import ReadStatus
from packages.storage.db import Base, session_scope
from packages.storage.models import Paper
from packages.storage.paper_repository import PaperRepository


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


def _seed_paper(
    *,
    arxiv_id: str,
    title: str,
    abstract: str,
    created_at: datetime,
    embedding: list[float] | None,
) -> None:
    with session_scope() as session:
        session.add(
            Paper(
                arxiv_id=arxiv_id,
                title=title,
                abstract=abstract,
                embedding=embedding,
                metadata_json={},
                read_status=ReadStatus.unread,
                created_at=created_at,
                updated_at=created_at,
            )
        )


def test_ttl_cache_get_removes_expired_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = api_deps.TTLCache()
    cache._store["expired"] = (100.0, {"stale": True})
    monkeypatch.setattr(api_deps.time, "time", lambda: 101.0)

    assert cache.get("expired") is None
    assert "expired" not in cache._store


def test_default_assistant_exec_policy_is_not_full_auto() -> None:
    policy = get_assistant_exec_policy()

    assert policy["command_execution"] == "allowlist"
    assert policy["approval_mode"] == "on_request"


def test_default_command_allowlist_allows_read_only_git_status() -> None:
    ensure_workspace_operation_allowed("run_workspace_command", command="git status --short")


def test_default_command_allowlist_rejects_shell_control_operator() -> None:
    with pytest.raises(WorkspaceAccessError, match="允许列表"):
        ensure_workspace_operation_allowed(
            "run_workspace_command", command="git status && git push"
        )


def test_default_command_allowlist_rejects_interpreters_and_package_managers() -> None:
    for command in ["python -c 'print(1)'", "node -e 'console.log(1)'", "npm run build"]:
        with pytest.raises(WorkspaceAccessError, match="允许列表"):
            ensure_workspace_operation_allowed("run_workspace_command", command=command)


def test_default_command_allowlist_uses_token_boundaries() -> None:
    with pytest.raises(WorkspaceAccessError, match="允许列表"):
        ensure_workspace_operation_allowed("run_workspace_command", command="git-malicious status")


def test_default_command_allowlist_rejects_git_push() -> None:
    with pytest.raises(WorkspaceAccessError, match="允许列表"):
        ensure_workspace_operation_allowed("run_workspace_command", command="git push")


def test_folder_stats_groups_dates_without_rounding_timezone_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(
        timezone_utils,
        "get_settings",
        lambda: SimpleNamespace(user_timezone="Asia/Kathmandu"),
    )
    monkeypatch.setattr(
        timezone_utils,
        "user_today_start_utc",
        lambda: datetime(2026, 1, 3, 18, 15),
    )

    _seed_paper(
        arxiv_id="2601.00001",
        title="Late UTC paper",
        abstract="A",
        created_at=datetime(2026, 1, 1, 18, 30),
        embedding=None,
    )
    _seed_paper(
        arxiv_id="2601.00002",
        title="Earlier UTC paper",
        abstract="B",
        created_at=datetime(2026, 1, 1, 17, 0),
        embedding=None,
    )

    with session_scope() as session:
        stats = PaperRepository(session).folder_stats()

    by_date = {item["date"]: item["count"] for item in stats["by_date"]}
    assert by_date["2026-01-02"] == 1
    assert by_date["2026-01-01"] == 1


def test_semantic_candidates_scan_beyond_recent_500(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)

    base_time = datetime.now(UTC) - timedelta(days=1)
    _seed_paper(
        arxiv_id="2599.99999",
        title="Best historical match",
        abstract="old but relevant",
        created_at=base_time,
        embedding=[1.0, 0.0],
    )
    for index in range(500):
        _seed_paper(
            arxiv_id=f"2602.{index:05d}",
            title=f"Recent paper {index}",
            abstract="recent but unrelated",
            created_at=base_time + timedelta(minutes=index + 1),
            embedding=[0.0, 1.0],
        )

    with session_scope() as session:
        title = PaperRepository(session).semantic_candidates([1.0, 0.0], limit=1)[0].title

    assert title == "Best historical match"
