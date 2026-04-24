from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import topics as topics_router
from packages.ai.ops import daily_runner
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.repositories import TopicRepository


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


def test_topic_filters_persist_and_can_be_cleared(monkeypatch):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/topics",
        json={
            "name": "Top ML Venues",
            "kind": "subscription",
            "query": "graph agents",
            "source": "hybrid",
            "search_field": "all",
            "priority_mode": "impact",
            "venue_tier": "ccf_a",
            "venue_type": "conference",
            "venue_names": ["NeurIPS", "ICML"],
            "from_year": 2022,
            "enabled": True,
        },
    )

    assert created.status_code == 200
    payload = created.json()
    assert payload["venue_tier"] == "ccf_a"
    assert payload["venue_type"] == "conference"
    assert payload["venue_names"] == ["NeurIPS", "ICML"]
    assert payload["from_year"] == 2022

    updated = client.patch(
        f"/topics/{payload['id']}",
        json={
            "venue_type": "journal",
            "venue_names": ["JMLR"],
            "from_year": None,
        },
    )

    assert updated.status_code == 200
    updated_payload = updated.json()
    assert updated_payload["venue_tier"] == "ccf_a"
    assert updated_payload["venue_type"] == "journal"
    assert updated_payload["venue_names"] == ["JMLR"]
    assert updated_payload["from_year"] is None


def test_run_topic_ingest_uses_persisted_external_filters(monkeypatch):
    _configure_test_db(monkeypatch)
    captured: dict[str, object] = {}

    class _FakePipelines:
        def ingest_external_entries(self, entries, *, topic_id=None, action_type=None, query=None):
            captured["entries"] = entries
            captured["topic_id"] = topic_id
            captured["query"] = query
            return {
                "requested": len(entries),
                "ingested": len(entries),
                "papers": [{"id": "paper-1"}] if entries else [],
            }

        def ingest_arxiv_with_stats(self, **kwargs):  # pragma: no cover
            raise AssertionError("hybrid source should not use arxiv-only ingest path")

    monkeypatch.setattr(daily_runner, "PaperPipelines", _FakePipelines)
    monkeypatch.setattr(daily_runner, "_process_paper", lambda *args, **kwargs: {"success": True, "skim_score": None})
    monkeypatch.setattr(
        daily_runner.research_tool_runtime,
        "_search_literature",
        lambda query, **kwargs: (
            captured.update({"search_query": query, "search_kwargs": kwargs}) or
            SimpleNamespace(
                success=True,
                summary="ok",
                data={
                    "papers": [
                        {
                            "title": "Filtered Paper",
                            "publication_year": 2024,
                            "publication_date": "2024-06-01",
                            "source": "openalex",
                        }
                    ]
                },
            )
        ),
    )

    with session_scope() as session:
        topic = TopicRepository(session).upsert_topic(
            name="Hybrid Filtered Topic",
            kind="subscription",
            query="research agents",
            source="hybrid",
            venue_tier="ccf_a",
            venue_type="conference",
            venue_names=["NeurIPS", "ICML"],
            from_year=2021,
            enabled=True,
            max_results_per_run=5,
        )
        topic_id = topic.id

    result = daily_runner.run_topic_ingest(topic_id)

    assert result["status"] == "ok"
    assert captured["search_query"] == "research agents"
    assert captured["search_kwargs"] == {
        "max_results": 5,
        "source_scope": "hybrid",
        "venue_tier": "ccf_a",
        "venue_type": "conference",
        "venue_names": ["NeurIPS", "ICML"],
        "from_year": 2021,
        "sort_mode": "time",
        "date_from": None,
        "date_to": None,
    }
    assert captured["topic_id"] == topic_id
    assert captured["entries"][0]["title"] == "Filtered Paper"
