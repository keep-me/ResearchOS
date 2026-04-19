from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.deps import cache
from apps.api.routers import topics as topics_router
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


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(topics_router.router)
    return app


def test_topic_mutations_invalidate_folder_stats_cache(monkeypatch):
    _configure_test_db(monkeypatch)
    cache.invalidate("folder_stats")
    client = TestClient(_build_app())

    cache.set("folder_stats", {"stale": True}, ttl=60)
    created = client.post(
        "/topics",
        json={
            "name": "Vision Folder",
            "kind": "folder",
            "query": "",
            "source": "manual",
            "enabled": False,
        },
    )

    assert created.status_code == 200
    assert cache.get("folder_stats") is None

    topic_id = created.json()["id"]

    cache.set("folder_stats", {"stale": True}, ttl=60)
    updated = client.patch(
        f"/topics/{topic_id}",
        json={"name": "Renamed Vision Folder"},
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed Vision Folder"
    assert cache.get("folder_stats") is None

    cache.set("folder_stats", {"stale": True}, ttl=60)
    deleted = client.delete(f"/topics/{topic_id}")

    assert deleted.status_code == 200
    assert cache.get("folder_stats") is None
