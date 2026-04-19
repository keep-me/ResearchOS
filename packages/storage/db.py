"""Database engine and session management."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from packages.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()
_is_sqlite = settings.database_url.startswith("sqlite")
_is_sqlite_memory = ":memory:" in settings.database_url or settings.database_url.rstrip("/") == "sqlite:"

connect_args: dict = {}
if _is_sqlite:
    connect_args = {"check_same_thread": False, "timeout": 60}

poolclass = None
if _is_sqlite:
    # SQLite file databases are prone to "database is locked" under pooled
    # multi-connection write contention. Prefer opening short-lived connections.
    poolclass = StaticPool if _is_sqlite_memory else NullPool

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=connect_args,
    poolclass=poolclass,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore[no-redef]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=120000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64 MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional database session scope."""

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_db_connection() -> bool:
    """Return whether the database connection is healthy."""

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database connection check failed")
        return False
