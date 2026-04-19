from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from apps.api.routers.papers import analyze_paper_rounds
from packages.ai.research.reasoning_service import ReasoningService
from packages.domain.schemas import PaperCreate
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import PaperRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


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
                arxiv_id="2603.66666",
                title="ChainSync",
                abstract="Paper-domain detail and reasoning levels should stay synchronized.",
                metadata={},
            )
        )
        return paper.id


def test_reasoning_service_syncs_variant_to_detail_level(monkeypatch):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient._config",
        lambda self: SimpleNamespace(provider="mock", model_deep="mock-deep"),
    )

    def _fake_complete(
        self,
        prompt,
        *,
        model_override,
        variant_override,
        max_tokens,
        request_timeout,
        max_wait_seconds,
        max_retries=None,
    ):
        seen["variant_override"] = variant_override
        seen["max_tokens"] = max_tokens
        return LLMResult(
            content='{"reasoning_steps":[{"step":"问题理解","thinking":"同步测试","conclusion":"一致"}]}',
            parsed_json={
                "reasoning_steps": [
                    {
                        "step": "问题理解",
                        "thinking": "同步测试",
                        "conclusion": "一致",
                    }
                ]
            },
        )

    monkeypatch.setattr(ReasoningService, "_complete_json_with_deadline", _fake_complete)

    result = ReasoningService().analyze(
        UUID(paper_id),
        detail_level="medium",
        reasoning_level="xhigh",
    )

    assert result["paper_id"] == paper_id
    assert seen["variant_override"] == "medium"
    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        stored = dict(paper.metadata_json or {}).get("reasoning_chain") or {}

    assert isinstance(stored.get("reasoning_steps"), list)
    assert stored["reasoning_steps"][0]["step"] == "问题理解"


def test_analyze_paper_rounds_syncs_retry_metadata(monkeypatch):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    submit_calls: list[dict] = []
    retry_calls: list[dict] = []

    def _fake_submit(*, task_type, title, fn, total, metadata=None):
        submit_calls.append(
            {
                "task_type": task_type,
                "title": title,
                "total": total,
                "metadata": dict(metadata or {}),
            }
        )
        return "task-paper-analysis"

    def _fake_register_retry(task_id, callback, label, metadata=None):
        retry_calls.append(
            {
                "task_id": task_id,
                "label": label,
                "metadata": dict(metadata or {}),
            }
        )

    monkeypatch.setattr("apps.api.routers.papers.global_tracker.submit", _fake_submit)
    monkeypatch.setattr("apps.api.routers.papers.global_tracker.register_retry", _fake_register_retry)

    result = analyze_paper_rounds(
        UUID(paper_id),
        body={"detail_level": "medium", "reasoning_level": "xhigh", "evidence_mode": "rough"},
    )

    assert result["task_id"] == "task-paper-analysis"
    retry_metadata = submit_calls[0]["metadata"]["retry_metadata"]
    assert retry_metadata["detail_level"] == "medium"
    assert retry_metadata["reasoning_level"] == "medium"
    assert retry_metadata["evidence_mode"] == "rough"
    assert retry_calls[0]["metadata"]["detail_level"] == "medium"
    assert retry_calls[0]["metadata"]["reasoning_level"] == "medium"
    assert retry_calls[0]["metadata"]["evidence_mode"] == "rough"


def test_reasoning_service_prefers_mineru_ocr_context(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 ocr")
    captured: dict[str, object] = {}

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        paper.pdf_path = str(pdf_path)

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient._config",
        lambda self: SimpleNamespace(provider="mock", model_deep="mock-deep"),
    )
    monkeypatch.setattr(
        "packages.ai.paper.paper_evidence.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            build_analysis_context=lambda max_chars=0: "OCR正文 含公式与图表"
        ),
    )
    monkeypatch.setattr(
        "packages.ai.research.reasoning_service.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: (_ for _ in ()).throw(AssertionError("should not fallback")),
    )

    def _fake_complete(
        self,
        prompt,
        *,
        model_override,
        variant_override,
        max_tokens,
        request_timeout,
        max_wait_seconds,
        max_retries=None,
    ):
        captured["prompt"] = prompt
        return LLMResult(
            content='{"reasoning_steps":[{"step":"问题理解","thinking":"OCR测试","conclusion":"一致"}]}',
            parsed_json={
                "reasoning_steps": [
                    {
                        "step": "问题理解",
                        "thinking": "OCR测试",
                        "conclusion": "一致",
                    }
                ]
            },
        )

    monkeypatch.setattr(ReasoningService, "_complete_json_with_deadline", _fake_complete)

    ReasoningService().analyze(
        UUID(paper_id),
        detail_level="medium",
        reasoning_level="medium",
    )

    assert "OCR正文 含公式与图表" in str(captured["prompt"])


def test_reasoning_service_pdf_source_skips_markdown(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 pdf")
    captured: dict[str, object] = {}

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        paper.pdf_path = str(pdf_path)

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient._config",
        lambda self: SimpleNamespace(provider="mock", model_deep="mock-deep"),
    )
    monkeypatch.setattr(
        "packages.ai.paper.paper_evidence.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not read markdown")),
    )
    monkeypatch.setattr(
        "packages.ai.research.reasoning_service.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: "PDF正文",
    )

    def _fake_complete(
        self,
        prompt,
        *,
        model_override,
        variant_override,
        max_tokens,
        request_timeout,
        max_wait_seconds,
        max_retries=None,
    ):
        captured["prompt"] = prompt
        return LLMResult(
            content='{"reasoning_steps":[{"step":"问题理解","thinking":"PDF测试","conclusion":"一致"}]}',
            parsed_json={
                "reasoning_steps": [
                    {
                        "step": "问题理解",
                        "thinking": "PDF测试",
                        "conclusion": "一致",
                    }
                ]
            },
        )

    monkeypatch.setattr(ReasoningService, "_complete_json_with_deadline", _fake_complete)

    ReasoningService().analyze(
        UUID(paper_id),
        detail_level="medium",
        reasoning_level="medium",
        content_source="pdf",
    )

    assert "PDF正文" in str(captured["prompt"])


def test_reasoning_service_rough_mode_uses_smaller_budget(monkeypatch, tmp_path):
    _configure_test_db(monkeypatch)
    paper_id = _seed_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 rough")
    captured: dict[str, object] = {}

    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(UUID(paper_id))
        paper.pdf_path = str(pdf_path)

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient._config",
        lambda self: SimpleNamespace(provider="mock", model_deep="mock-deep"),
    )
    monkeypatch.setattr(
        "packages.ai.research.reasoning_service.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: "PDF正文 rough evidence",
    )

    def _fake_complete(
        self,
        prompt,
        *,
        model_override,
        variant_override,
        max_tokens,
        request_timeout,
        max_wait_seconds,
        max_retries=None,
    ):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        captured["request_timeout"] = request_timeout
        return LLMResult(
            content='{"reasoning_steps":[{"step":"问题理解","thinking":"粗略测试","conclusion":"一致"}]}',
            parsed_json={
                "reasoning_steps": [
                    {
                        "step": "问题理解",
                        "thinking": "粗略测试",
                        "conclusion": "一致",
                    }
                ]
            },
        )

    monkeypatch.setattr(ReasoningService, "_complete_json_with_deadline", _fake_complete)

    ReasoningService().analyze(
        UUID(paper_id),
        detail_level="high",
        reasoning_level="high",
        content_source="pdf",
        evidence_mode="rough",
    )

    assert "PDF正文 rough evidence" in str(captured["prompt"])
    assert int(captured["max_tokens"]) <= 2200
    assert int(captured["request_timeout"]) <= 150
