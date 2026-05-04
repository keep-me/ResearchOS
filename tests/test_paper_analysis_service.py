from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.paper.paper_analysis_service import PaperAnalysisService
from packages.domain.schemas import PaperCreate
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import PaperRepository


def _configure_test_db(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _seed_paper() -> str:
    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.upsert_paper(
            PaperCreate(
                arxiv_id="2603.55555",
                title="AnchorCoT",
                abstract="Anchor-guided process reward modeling for spatial reasoning.",
                metadata={},
            )
        )
        return paper.id


def test_paper_analysis_service_persists_round_bundle(monkeypatch):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    stage_calls: list[tuple[str, str | None]] = []

    def _fake_summarize_text(
        self,
        prompt,
        stage,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        stage_calls.append((stage, variant_override))
        return LLMResult(content=f"# {stage}\n\ncontent for {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize_text,
    )

    result = PaperAnalysisService().analyze(
        UUID(paper_id),
        detail_level="high",
        reasoning_level="xhigh",
    )

    assert result["paper_id"] == paper_id
    bundle = result["analysis_rounds"]
    assert bundle["detail_level"] == "high"
    assert bundle["reasoning_level"] == "high"
    assert "paper_round_1_overview" in bundle["round_1"]["markdown"]
    assert "paper_round_2_comprehension" in bundle["round_2"]["markdown"]
    assert "paper_round_3_deep_analysis" in bundle["round_3"]["markdown"]
    assert "paper_round_final_notes" in bundle["final_notes"]["markdown"]
    assert stage_calls == [
        ("paper_round_1_overview", "high"),
        ("paper_round_2_comprehension", "high"),
        ("paper_round_3_deep_analysis", "high"),
        ("paper_round_final_notes", "high"),
    ]

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        stored = dict(paper.metadata_json or {}).get("analysis_rounds")

    assert stored is not None
    assert stored["detail_level"] == "high"
    assert stored["reasoning_level"] == "high"
    assert stored["final_notes"]["title"] == "最终结构化笔记"


def test_paper_analysis_service_fails_on_provider_placeholder(monkeypatch):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()

    def _fake_summarize_text(
        self,
        prompt,
        stage,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        return LLMResult(content="模型服务暂不可用。(stage=paper_round_1_overview)")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize_text,
    )

    with pytest.raises(RuntimeError, match="模型服务暂不可用"):
        PaperAnalysisService().analyze(
            UUID(paper_id),
            detail_level="high",
            reasoning_level="high",
        )

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        stored = dict(paper.metadata_json or {}).get("analysis_rounds")

    assert stored is None


def test_paper_analysis_service_retries_transient_provider_errors(monkeypatch):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    attempts: dict[str, int] = {}

    def _fake_summarize_text(
        self,
        prompt,
        stage,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        del self, prompt, variant_override, max_tokens, request_timeout
        attempts[stage] = attempts.get(stage, 0) + 1
        if attempts[stage] == 1:
            return LLMResult(content=f"模型服务暂不可用。(stage={stage}) 请稍后重试")
        return LLMResult(content=f"# {stage}\n\nrecovered")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize_text,
    )

    result = PaperAnalysisService().analyze(
        UUID(paper_id),
        detail_level="high",
        reasoning_level="high",
    )

    assert result["paper_id"] == paper_id
    for stage in (
        "paper_round_1_overview",
        "paper_round_2_comprehension",
        "paper_round_3_deep_analysis",
        "paper_round_final_notes",
    ):
        assert attempts[stage] == 2


def test_paper_analysis_service_prefers_mineru_ocr_context(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 ocr")

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        paper.pdf_path = str(pdf_path)

    prompts: list[str] = []

    monkeypatch.setattr(
        "packages.ai.paper.paper_evidence.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            build_analysis_context=lambda max_chars=0: "OCR正文 含公式与图表"
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.paper_analysis_service.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: (_ for _ in ()).throw(
            AssertionError("should not fallback")
        ),
    )

    def _fake_summarize_text(
        self,
        prompt,
        stage,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        prompts.append(prompt)
        return LLMResult(content=f"# {stage}\n\ncontent for {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize_text,
    )

    PaperAnalysisService().analyze(
        UUID(paper_id),
        detail_level="medium",
        reasoning_level="medium",
    )

    assert prompts
    assert "OCR正文 含公式与图表" in prompts[0]


def test_paper_analysis_service_pdf_source_skips_markdown(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 pdf")

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        paper.pdf_path = str(pdf_path)

    prompts: list[str] = []

    monkeypatch.setattr(
        "packages.ai.paper.paper_evidence.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not read markdown")),
    )
    monkeypatch.setattr(
        "packages.ai.paper.paper_analysis_service.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: "PDF正文",
    )

    def _fake_summarize_text(
        self,
        prompt,
        stage,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        prompts.append(prompt)
        return LLMResult(content=f"# {stage}\n\ncontent for {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize_text,
    )

    PaperAnalysisService().analyze(
        UUID(paper_id),
        detail_level="medium",
        reasoning_level="medium",
        content_source="pdf",
    )

    assert prompts
    assert "PDF正文" in prompts[0]


def test_paper_analysis_service_uses_round_specific_evidence(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 round")
    prompts: list[str] = []
    round_calls: list[str] = []

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        paper.pdf_path = str(pdf_path)

    monkeypatch.setattr(
        "packages.ai.paper.paper_evidence.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            build_analysis_context=lambda max_chars=0: "OCR正文",
            build_round_context=lambda round_name, max_chars=0: (
                round_calls.append(round_name) or f"{round_name} 证据"
            ),
        ),
    )

    def _fake_summarize_text(
        self,
        prompt,
        stage,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        prompts.append(prompt)
        return LLMResult(content=f"# {stage}\n\ncontent for {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize_text,
    )

    PaperAnalysisService().analyze(
        UUID(paper_id),
        detail_level="medium",
        reasoning_level="medium",
    )

    assert round_calls == ["overview", "comprehension", "deep_analysis"]
    assert "[第 1 轮结构化证据包]\noverview 证据" in prompts[0]
    assert "[第 2 轮结构化证据包]\ncomprehension 证据" in prompts[1]
    assert "[第 3 轮结构化证据包]\ndeep_analysis 证据" in prompts[2]


def test_paper_analysis_service_rough_mode_uses_shared_excerpt(monkeypatch):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    prompts: list[str] = []
    pdf_path = "D:/tmp/rough-paper.pdf"

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        paper.pdf_path = pdf_path

    monkeypatch.setattr(
        "packages.ai.paper.paper_analysis_service.load_prepared_paper_evidence",
        lambda **kwargs: SimpleNamespace(
            source="PDF 文本",
            build_analysis_context=lambda max_chars=0: "粗略证据摘录",
            build_round_context=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("rough mode should skip round builder")
            ),
        ),
    )

    def _fake_summarize_text(
        self,
        prompt,
        stage,
        variant_override=None,
        max_tokens=None,
        request_timeout=None,
    ):
        prompts.append(prompt)
        return LLMResult(content=f"# {stage}\n\ncontent for {stage}")

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        _fake_summarize_text,
    )

    result = PaperAnalysisService().analyze(
        UUID(paper_id),
        detail_level="medium",
        reasoning_level="medium",
        evidence_mode="rough",
    )

    bundle = result["analysis_rounds"]
    assert bundle["evidence_mode"] == "rough"
    assert prompts
    assert "粗略证据摘录" in prompts[0]
