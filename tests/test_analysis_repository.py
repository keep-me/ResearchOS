from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.domain.schemas import SkimReport
from packages.storage.db import Base
from packages.storage.models import AnalysisReport
from packages.storage.repositories import AnalysisRepository


def _build_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_upsert_skim_uses_first_innovation_when_one_liner_missing():
    session_local = _build_session()
    paper_id = uuid4()

    with session_local() as session:
        repo = AnalysisRepository(session)
        repo.upsert_skim(
            paper_id,
            SkimReport(
                one_liner="",
                innovations=[
                    "提出统一训练策略，整合描述生成、自监督损失与在线数据整理。",
                    "在多语种视觉语义任务上取得稳健提升。",
                ],
                keywords=["vision-language"],
                title_zh="",
                abstract_zh="",
                relevance_score=0.62,
            ),
        )
        session.flush()

        stored = session.execute(
            select(AnalysisReport).where(AnalysisReport.paper_id == str(paper_id))
        ).scalar_one()

    assert stored.summary_md is not None
    assert "- One-liner: 提出统一训练策略，整合描述生成、自监督损失与在线数据整理。" in stored.summary_md
    assert stored.key_insights["one_liner"] == "提出统一训练策略，整合描述生成、自监督损失与在线数据整理。"
    assert stored.key_insights["skim_innovations"] == [
        "提出统一训练策略，整合描述生成、自监督损失与在线数据整理。",
        "在多语种视觉语义任务上取得稳健提升。",
    ]
