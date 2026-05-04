from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.paper.pipelines import PaperPipelines
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


def _seed_paper(pdf_path: str) -> str:
    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.upsert_paper(
            PaperCreate(
                arxiv_id="2604.12345",
                title="OCR Deep Dive",
                abstract="A paper for testing OCR-first deep dive.",
                metadata={},
            )
        )
        paper.pdf_path = pdf_path
        return paper.id


def test_deep_dive_prefers_mineru_ocr_context(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 ocr")
    paper_id = _seed_paper(str(pdf_path))
    captured: dict[str, object] = {}
    focus_prompts: list[str] = []

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient._config",
        lambda self: SimpleNamespace(
            provider="mock",
            model_deep="mock-deep",
            model_fallback="mock-fallback",
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.paper_evidence.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            build_analysis_context=lambda max_chars=0: "OCR正文 含公式与图表"
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.VisionPdfReader.extract_page_descriptions",
        lambda self, pdf_path, max_pages=8: (_ for _ in ()).throw(
            AssertionError("should not fallback")
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: (_ for _ in ()).throw(
            AssertionError("should not fallback")
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.CostGuardService.choose_model",
        lambda self, stage, prompt, default_model, fallback_model: SimpleNamespace(
            chosen_model=default_model,
            note="test",
        ),
    )
    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        lambda self, prompt, stage, model_override=None, variant_override=None, max_tokens=None, request_timeout=None: (
            (
                focus_prompts.append(prompt),
                LLMResult(content="阶段分析"),
            )[1]
        ),
    )

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        max_tokens=None,
    ):
        captured["prompt"] = prompt
        return LLMResult(
            content='{"method_summary":"方法","experiments_summary":"实验","ablation_summary":"消融","reviewer_risks":["风险"]}',
            parsed_json={
                "method_summary": "方法",
                "experiments_summary": "实验",
                "ablation_summary": "消融",
                "reviewer_risks": ["风险"],
            },
        )

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.complete_json",
        _fake_complete_json,
    )

    result = PaperPipelines().deep_dive(UUID(paper_id), detail_level="medium")

    assert result.method_summary == "方法"
    assert len(focus_prompts) == 3
    assert "OCR正文 含公式与图表" in str(captured["prompt"])


def test_deep_dive_pdf_source_skips_cached_markdown(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 pdf")
    paper_id = _seed_paper(str(pdf_path))
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient._config",
        lambda self: SimpleNamespace(
            provider="mock",
            model_deep="mock-deep",
            model_fallback="mock-fallback",
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.paper_evidence.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not read markdown")),
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.VisionPdfReader.extract_page_descriptions",
        lambda self, pdf_path, max_pages=8: "视觉摘录",
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: "PDF正文",
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.CostGuardService.choose_model",
        lambda self, stage, prompt, default_model, fallback_model: SimpleNamespace(
            chosen_model=default_model,
            note="test",
        ),
    )
    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        lambda self, prompt, stage, model_override=None, variant_override=None, max_tokens=None, request_timeout=None: (
            LLMResult(
                content="阶段分析",
            )
        ),
    )

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        max_tokens=None,
    ):
        captured["prompt"] = prompt
        return LLMResult(
            content='{"method_summary":"方法","experiments_summary":"实验","ablation_summary":"消融","reviewer_risks":["风险"]}',
            parsed_json={
                "method_summary": "方法",
                "experiments_summary": "实验",
                "ablation_summary": "消融",
                "reviewer_risks": ["风险"],
            },
        )

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.complete_json",
        _fake_complete_json,
    )

    result = PaperPipelines().deep_dive(
        UUID(paper_id),
        detail_level="medium",
        content_source="pdf",
    )

    assert result.method_summary == "方法"
    assert "视觉摘录" in str(captured["prompt"])
    assert "PDF正文" in str(captured["prompt"])


def test_deep_dive_rough_mode_skips_focus_stages(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 rough")
    paper_id = _seed_paper(str(pdf_path))
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient._config",
        lambda self: SimpleNamespace(
            provider="mock",
            model_deep="mock-deep",
            model_fallback="mock-fallback",
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.VisionPdfReader.extract_page_descriptions",
        lambda self, pdf_path, max_pages=8: (_ for _ in ()).throw(
            AssertionError("rough mode should skip vision")
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: "PDF正文 rough evidence",
    )
    monkeypatch.setattr(
        "packages.ai.paper.pipelines.CostGuardService.choose_model",
        lambda self, stage, prompt, default_model, fallback_model: SimpleNamespace(
            chosen_model=default_model,
            note="test",
        ),
    )
    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.summarize_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("rough mode should skip focus stages")
        ),
    )

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        max_tokens=None,
    ):
        captured["prompt"] = prompt
        return LLMResult(
            content='{"method_summary":"方法","experiments_summary":"实验","ablation_summary":"消融","reviewer_risks":["风险"]}',
            parsed_json={
                "method_summary": "方法",
                "experiments_summary": "实验",
                "ablation_summary": "消融",
                "reviewer_risks": ["风险"],
            },
        )

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.complete_json",
        _fake_complete_json,
    )

    result = PaperPipelines().deep_dive(
        UUID(paper_id),
        detail_level="medium",
        content_source="pdf",
        evidence_mode="rough",
    )

    assert result.method_summary == "方法"
    assert "粗略证据摘录" in str(captured["prompt"])
    assert "PDF正文 rough evidence" in str(captured["prompt"])
