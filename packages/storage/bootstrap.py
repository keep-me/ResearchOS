"""Explicit storage/runtime bootstrap helpers."""

from __future__ import annotations

import logging
import uuid as _uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from packages.storage.db import engine

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_LEGACY_RECONCILE_BASELINE = "20260412_0011_add_project_research_wiki"


def _alembic_config() -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str((_REPO_ROOT / "infra" / "migrations").resolve()))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    return config


def _user_tables() -> list[str]:
    inspector = inspect(engine)
    return sorted(name for name in inspector.get_table_names() if name != "alembic_version")


def _current_revision() -> str | None:
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()


def _upgrade_schema() -> None:
    command.upgrade(_alembic_config(), "head")


def _stamp_legacy_schema() -> None:
    command.stamp(_alembic_config(), _LEGACY_RECONCILE_BASELINE)


def _ensure_initial_import_action() -> None:
    inspector = inspect(engine)
    required_tables = {"papers", "collection_actions", "action_papers"}
    if not required_tables.issubset(set(inspector.get_table_names())):
        return

    with engine.begin() as conn:
        orphan_rows = conn.execute(
            text(
                "SELECT p.id FROM papers p "
                "WHERE p.id NOT IN (SELECT paper_id FROM action_papers)"
            )
        ).fetchall()
        if not orphan_rows:
            return

        action_id = _uuid.uuid4().hex[:36]
        conn.execute(
            text(
                "INSERT INTO collection_actions (id, action_type, title, paper_count, created_at) "
                "VALUES (:id, 'initial_import', :title, :cnt, CURRENT_TIMESTAMP)"
            ),
            {
                "id": action_id,
                "title": f"初始导入（{len(orphan_rows)} 篇）",
                "cnt": len(orphan_rows),
            },
        )
        for row in orphan_rows:
            conn.execute(
                text(
                    "INSERT INTO action_papers (id, action_id, paper_id) "
                    "VALUES (:id, :action_id, :paper_id)"
                ),
                {
                    "id": _uuid.uuid4().hex[:36],
                    "action_id": action_id,
                    "paper_id": row[0],
                },
            )
        logger.info(
            "Initialized %d orphan papers into initial_import action %s",
            len(orphan_rows),
            action_id,
        )


def bootstrap_storage() -> None:
    """Ensure the database schema is ready for runtime access."""

    tables = _user_tables()
    current_revision = _current_revision()
    if current_revision is None and tables:
        logger.warning(
            "Detected legacy database without alembic_version; stamping %s before reconcile upgrade",
            _LEGACY_RECONCILE_BASELINE,
        )
        _stamp_legacy_schema()

    _upgrade_schema()

    try:
        _ensure_initial_import_action()
    except Exception:
        logger.exception("Post-migration data backfill failed")


def bootstrap_api_runtime() -> None:
    """Prepare API runtime dependencies on process startup."""

    bootstrap_storage()
    from packages.domain.task_tracker import global_tracker

    global_tracker.bootstrap_from_store()
    logger.info("API runtime bootstrap completed")


def bootstrap_worker_runtime() -> None:
    """Prepare worker dependencies on process startup."""

    bootstrap_storage()
    logger.info("Worker runtime bootstrap completed")


def bootstrap_local_runtime() -> None:
    """Prepare local development storage state."""

    bootstrap_storage()
    logger.info("Local bootstrap completed")
