from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import topics as topics_router
from packages.ai.paper import pipelines as pipelines_module
from packages.domain.schemas import PaperCreate
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.repositories import PaperRepository


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


def test_ingest_external_entries_persists_openalex_metadata_and_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(pipelines_module, "_bg_auto_link", lambda paper_ids: None)
    monkeypatch.setattr(pipelines_module, "LLMClient", lambda: object())
    monkeypatch.setattr(pipelines_module, "VisionPdfReader", lambda: object())
    monkeypatch.setattr(pipelines_module, "PdfTextExtractor", lambda: object())

    pipeline = pipelines_module.PaperPipelines()
    entry = {
        "title": "Graph Retrieval for Research Agents",
        "abstract": "A high-quality OpenAlex candidate.",
        "publication_year": 2025,
        "publication_date": "2025-02-10",
        "citation_count": 42,
        "venue": "Conference on Neural Information Processing Systems",
        "venue_type": "conference",
        "venue_tier": "ccf_a",
        "authors": ["Alice", "Bob"],
        "openalex_id": "https://openalex.org/W123",
        "source_url": "https://openalex.org/W123",
        "source": "openalex",
    }

    first = pipeline.ingest_external_entries([entry], query="graph retrieval")
    second = pipeline.ingest_external_entries([entry], query="graph retrieval")

    assert first["requested"] == 1
    assert first["ingested"] == 1
    assert first["duplicates"] == 0
    assert first["papers"][0]["arxiv_id"].startswith("ext-")
    assert second["ingested"] == 0
    assert second["duplicates"] == 1

    with session_scope() as session:
        paper = PaperRepository(session).get_by_arxiv_id(first["papers"][0]["arxiv_id"])
        assert paper is not None
        assert paper.metadata_json["openalex_id"] == "https://openalex.org/W123"
        assert paper.metadata_json["venue_tier"] == "ccf_a"
        assert paper.metadata_json["venue"] == "Conference on Neural Information Processing Systems"


def test_search_external_literature_route_filters_dates_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    app = FastAPI()
    app.include_router(topics_router.router)
    client = TestClient(app)

    monkeypatch.setattr(
        topics_router.research_tool_runtime,
        "_search_literature",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            summary="ok",
            data={
                "papers": [
                    {
                        "title": "Older but Influential",
                        "publication_year": 2024,
                        "publication_date": "2024-01-01",
                        "citation_count": 200,
                        "source": "openalex",
                    },
                    {
                        "title": "Newer Candidate",
                        "publication_year": 2025,
                        "publication_date": "2025-03-01",
                        "citation_count": 50,
                        "source": "openalex",
                    },
                    {
                        "title": "Too Old",
                        "publication_year": 2023,
                        "publication_date": "2023-05-01",
                        "citation_count": 999,
                        "source": "openalex",
                    },
                ],
                "count": 3,
                "query": "agents",
                "source_scope": "hybrid",
                "source_counts": {"openalex": 3, "arxiv": 0},
                "filters": {
                    "venue_tier": "ccf_a",
                    "venue_type": "all",
                    "venue_names": [],
                    "from_year": 2024,
                },
                "skipped_sources": [],
            },
        ),
    )

    response = client.post(
        "/ingest/literature/search",
        json={
            "query": "agents",
            "max_results": 10,
            "source_scope": "hybrid",
            "sort_mode": "impact",
            "venue_tier": "ccf_a",
            "venue_type": "all",
            "from_year": 2024,
            "date_from": "2024-01-01",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert [paper["title"] for paper in payload["papers"]] == [
        "Older but Influential",
        "Newer Candidate",
    ]


def test_ingest_arxiv_ids_short_circuits_existing_base_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(pipelines_module, "_bg_auto_link", lambda paper_ids: None)
    monkeypatch.setattr(pipelines_module, "LLMClient", lambda: object())
    monkeypatch.setattr(pipelines_module, "VisionPdfReader", lambda: object())
    monkeypatch.setattr(pipelines_module, "PdfTextExtractor", lambda: object())

    with session_scope() as session:
        PaperRepository(session).upsert_paper(
            PaperCreate(
                arxiv_id="2411.11904v3",
                title="Existing versioned paper",
                abstract="cached",
                metadata={},
            )
        )

    pipeline = pipelines_module.PaperPipelines()
    fetch_calls: list[list[str]] = []
    monkeypatch.setattr(
        pipeline.arxiv,
        "fetch_by_ids",
        lambda arxiv_ids: fetch_calls.append(list(arxiv_ids)) or [],
    )

    result = pipeline.ingest_arxiv_ids(["2411.11904"])

    assert result == {
        "requested": 1,
        "found": 1,
        "ingested": 0,
        "duplicates": 1,
        "missing_ids": [],
        "papers": [],
    }
    assert fetch_calls == []


def test_ingest_arxiv_ids_async_starts_background_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    app = FastAPI()
    app.include_router(topics_router.router)
    client = TestClient(app)

    submit_calls: list[dict] = []

    def _fake_submit(task_type, title, fn, *args, **kwargs):
        submit_calls.append(
            {
                "task_type": task_type,
                "title": title,
                "fn": fn,
                "args": args,
                "kwargs": kwargs,
            }
        )
        return "task-ingest-arxiv-ids"

    monkeypatch.setattr(topics_router.global_tracker, "submit", _fake_submit)

    response = client.post(
        "/ingest/arxiv-ids-async",
        json={
            "arxiv_ids": ["2411.11904", "2504.02647"],
            "topic_id": "folder-1",
            "download_pdf": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-ingest-arxiv-ids",
        "status": "running",
        "message": "arXiv ID 导入任务已启动",
    }
    assert len(submit_calls) == 1
    assert submit_calls[0]["task_type"] == "ingest_arxiv_ids"
    assert submit_calls[0]["kwargs"]["total"] == 100
    assert submit_calls[0]["kwargs"]["metadata"] == {
        "source": "ingest",
        "topic_id": "folder-1",
        "download_pdf": True,
    }
    assert "2411.11904" in submit_calls[0]["title"]
