from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.agent import mounted_paper_context
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.models import Paper


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


def test_resolve_research_skill_ids_auto_enables_project_research_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mounted_paper_context,
        "list_local_skills",
        lambda: [
            {"id": "project:researchos-paper-skim", "relative_path": "researchos-paper-skim"},
            {
                "id": "project:researchos-paper-three-round",
                "relative_path": "researchos-paper-three-round",
            },
            {"id": "project:researchos-daily-brief", "relative_path": "researchos-daily-brief"},
        ],
    )

    resolved = mounted_paper_context.resolve_research_skill_ids(
        ["manual:keep-me"],
        ["paper-1"],
    )

    assert resolved == [
        "manual:keep-me",
        "project:researchos-paper-skim",
        "project:researchos-paper-three-round",
        "project:researchos-daily-brief",
    ]


def test_build_mounted_papers_prompt_includes_pdf_and_existing_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = str(uuid4())
    with session_scope() as session:
        session.add(
            Paper(
                id=paper_id,
                arxiv_id="2501.01234",
                title="Mounted Paper",
                abstract="This paper studies multimodal encoders.",
                pdf_path="data/papers/mounted-paper.pdf",
                metadata_json={
                    "authors": ["Alice", "Bob"],
                    "keywords": ["multimodal", "vision encoder"],
                    "source_url": "https://arxiv.org/abs/2501.01234",
                    "skim_report": {"summary_md": "已有粗读"},
                    "deep_report": {"deep_dive_md": "已有精读"},
                    "analysis_rounds": {
                        "round_1": {"markdown": "第一轮分析"},
                        "final_notes": {"markdown": "最终总结"},
                    },
                },
            )
        )
        session.flush()

    monkeypatch.setattr(
        mounted_paper_context,
        "_paper_figure_summary",
        lambda current_paper_id: "图表分析：3 项" if current_paper_id == paper_id else None,
    )

    prompt = mounted_paper_context.build_mounted_papers_prompt(
        [paper_id], mounted_primary_paper_id=paper_id
    )

    assert "Primary paper ID" in prompt
    assert "Mounted Paper [primary]" in prompt
    assert "本地 PDF：data/papers/mounted-paper.pdf" in prompt
    assert "已有分析：粗读、精读、三轮分析(round_1, final_notes)" in prompt
    assert "不是把全文、摘要和分析结果整批注入上下文" in prompt
    assert "按 paper_id 调用论文详情、粗读、精读、图表分析、三轮分析或 PDF/OCR 读取工具" in prompt
    assert "第一轮分析" not in prompt
    assert "最终总结" not in prompt
    assert "图表分析：3 项" in prompt
    assert "Alice、Bob" in prompt
    assert "vision encoder" in prompt
