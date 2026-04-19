from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from packages.ai.paper.document_context import PaperDocumentContext
from packages.ai.paper.paper_evidence import PreparedPaperEvidence, load_prepared_paper_evidence


class _StubExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def extract_text(self, pdf_path: str, max_pages: int = 0) -> str:
        self.calls += 1
        return f"PDF正文::{Path(pdf_path).name}::{max_pages}"


class _StubVision:
    def __init__(self) -> None:
        self.calls = 0

    def extract_page_descriptions(self, pdf_path: str, max_pages: int = 0) -> str:
        self.calls += 1
        return f"页面视觉::{Path(pdf_path).name}::{max_pages}"


def test_load_prepared_paper_evidence_reuses_process_cache_for_pdf(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 cache")
    extractor = _StubExtractor()
    vision = _StubVision()

    first = load_prepared_paper_evidence(
        paper_id=uuid4(),
        pdf_path=str(pdf_path),
        content_source="pdf",
        pdf_extractor=extractor,
        pdf_text_pages=8,
        pdf_text_chars=8000,
        vision_reader=vision,
        vision_pages=4,
    )
    second = load_prepared_paper_evidence(
        paper_id=uuid4(),
        pdf_path=str(pdf_path),
        content_source="pdf",
        pdf_extractor=extractor,
        pdf_text_pages=8,
        pdf_text_chars=4000,
        vision_reader=vision,
        vision_pages=4,
    )

    assert extractor.calls == 1
    assert vision.calls == 1
    assert first.source == second.source
    assert "PDF正文::paper.pdf::8" in first.raw_excerpt


def test_load_prepared_paper_evidence_rough_mode_keeps_raw_excerpt(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 rough")
    extractor = _StubExtractor()
    vision = _StubVision()

    rough = load_prepared_paper_evidence(
        paper_id=uuid4(),
        pdf_path=str(pdf_path),
        content_source="pdf",
        evidence_mode="rough",
        pdf_extractor=extractor,
        pdf_text_pages=6,
        pdf_text_chars=5000,
        vision_reader=vision,
        vision_pages=0,
    )

    assert rough.document_context is None
    assert rough.round_context_builder is None
    assert rough.targeted_context_builder is None
    assert "PDF正文::paper.pdf::6" in rough.raw_excerpt
    assert "PDF正文::paper.pdf::6" in rough.build_analysis_context(max_chars=2000)


def test_prepared_paper_evidence_unbounded_mode_keeps_full_structured_context():
    markdown = """
# Abstract
This paper introduces the task and motivation.

# Method
The method section explains encoder fusion, prompt tuning, and optimization details.

## Experiment
The experiment section contains benchmarks, ablations, and error analysis.

Table 1: Main benchmark results across datasets.
| Method | CIDEr |
| --- | --- |
| Ours | 134.2 |
""".strip()
    context = PaperDocumentContext.from_markdown(markdown, source="OCR Markdown")
    evidence = PreparedPaperEvidence(
        source="OCR Markdown",
        raw_excerpt=markdown,
        document_context=context,
    )

    rendered = evidence.build_analysis_context(max_chars=0)

    assert "[章节 | Method]" in rendered
    assert "encoder fusion" in rendered
    assert "[章节 | Experiment]" in rendered
    assert "benchmarks, ablations, and error analysis" in rendered
    assert "[表格证据 | Table 1: Main benchmark results across datasets.]" in rendered
    assert "..." not in rendered
