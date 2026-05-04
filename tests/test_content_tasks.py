from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import content
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.repositories import GeneratedContentRepository


def _configure_test_db(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def test_run_paper_wiki_task_persists_generated_content(monkeypatch) -> None:
    _configure_test_db(monkeypatch)
    progress: list[tuple[str, int, int]] = []

    def fake_paper_wiki(*, paper_id: str) -> dict:
        return {
            "paper_id": paper_id,
            "title": "Paper Wiki Test",
            "markdown": "# Paper Wiki",
            "wiki_content": {"summary": "ok"},
            "graph": {
                "root": paper_id,
                "root_title": "Paper Wiki Test",
                "ancestors": [],
                "descendants": [],
                "nodes": [],
                "edge_count": 0,
            },
        }

    monkeypatch.setattr(content.graph_service, "paper_wiki", fake_paper_wiki)

    result = content._run_paper_wiki_task(
        "paper-123",
        progress_callback=lambda message, current, total: progress.append(
            (message, current, total)
        ),
    )

    assert result["paper_id"] == "paper-123"
    assert result["content_id"]
    assert progress[0] == ("正在生成论文综述...", 15, 100)
    assert progress[-1] == ("论文综述完成", 100, 100)

    with session_scope() as session:
        saved = GeneratedContentRepository(session).get_by_id(result["content_id"])
        assert saved.content_type == "paper_wiki"
        assert saved.paper_id == "paper-123"
        assert saved.markdown == "# Paper Wiki"
        assert saved.metadata_json["wiki_content"]["summary"] == "ok"


def test_start_paper_wiki_task_registers_tracker_job(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_submit(**kwargs) -> str:
        calls.append(kwargs)
        return "paper_wiki_test"

    monkeypatch.setattr(content.global_tracker, "submit", fake_submit)

    response = content.start_paper_wiki_task("paper-123")

    assert response == {"task_id": "paper_wiki_test", "status": "pending"}
    assert calls[0]["task_type"] == "paper_wiki"
    assert calls[0]["title"] == "论文综述: paper-12"
    assert calls[0]["fn"] is content._run_paper_wiki_task
    assert calls[0]["paper_id"] == "paper-123"
    assert calls[0]["metadata"]["paper_id"] == "paper-123"
