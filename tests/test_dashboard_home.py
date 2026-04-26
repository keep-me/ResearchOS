from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers.dashboard import _library_focus_snapshot
from packages.domain.schemas import PaperCreate
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.paper_repository import PaperRepository
from packages.storage.topic_repository import TopicRepository


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


def test_library_focus_snapshot_only_returns_folder_cards(monkeypatch):
    _configure_test_db(monkeypatch)

    with session_scope() as session:
        topic_repo = TopicRepository(session)
        folder = topic_repo.upsert_topic(
            name="多模态文件夹",
            kind="folder",
            query="",
            source="manual",
            enabled=False,
        )
        subscription = topic_repo.upsert_topic(
            name="多模态订阅",
            kind="subscription",
            query="multimodal",
            source="arxiv",
            enabled=True,
            default_folder_id=folder.id,
        )
        paper_repo = PaperRepository(session)
        paper = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id="2604.00001",
                title="Multimodal Test Paper",
                abstract="A test paper",
                publication_date=date(2026, 4, 26),
                metadata={"source": "arxiv"},
            )
        )
        paper_repo.link_to_topic(paper.id, folder.id)
        paper_repo.link_to_topic(paper.id, subscription.id)
        snapshot = _library_focus_snapshot(session, paper_repo)

    assert snapshot["window_label"] == "全库主题"
    assert [card["label"] for card in snapshot["topic_cards"]] == ["多模态文件夹"]
    assert [card["kind"] for card in snapshot["topic_cards"]] == ["folder"]
