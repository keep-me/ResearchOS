from __future__ import annotations


def normalize_paper_content_source(value: str | None) -> str:
    raw = str(value or "auto").strip().lower()
    if raw in {"markdown", "md", "ocr", "mineru"}:
        return "markdown"
    if raw in {"pdf", "arxiv_source", "direct"}:
        return "pdf"
    return "auto"


def resolve_effective_paper_content_source(
    requested_source: str | None,
    evidence_source: str | None = None,
) -> str:
    evidence_text = str(evidence_source or "").strip().lower()
    if evidence_text:
        if any(token in evidence_text for token in ("ocr", "markdown", "mineru")):
            return "markdown"
        if "pdf" in evidence_text:
            return "pdf"

    normalized = normalize_paper_content_source(requested_source)
    if normalized == "auto":
        return "pdf"
    return normalized


def paper_content_source_label(value: str | None) -> str:
    return "Markdown" if normalize_paper_content_source(value) == "markdown" else "PDF"


def prefers_markdown_content(value: str | None) -> bool:
    return normalize_paper_content_source(value) != "pdf"
