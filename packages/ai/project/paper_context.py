"""Lightweight paper context helpers for project workflows."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import select

from packages.storage.models import AnalysisReport, Paper


def clean_text(value: Any, *, max_chars: int | None = None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def normalize_paper_ids(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values or []:
        value = clean_text(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _year_from_value(value: Any) -> int | None:
    if isinstance(value, date):
        return value.year
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(19|20)\d{2}", text)
    return int(match.group(0)) if match else None


def _metadata_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    result = [clean_text(item, max_chars=80) for item in value if clean_text(item)]
    return result[:limit]


def _analysis_round_labels(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("analysis_rounds")
    if not isinstance(raw, dict):
        return []
    labels: list[str] = []
    label_map = {
        "round_1": "round_1",
        "round_2": "round_2",
        "round_3": "round_3",
        "final_notes": "final_notes",
    }
    for key, label in label_map.items():
        payload = raw.get(key)
        if isinstance(payload, dict) and clean_text(payload.get("markdown")):
            labels.append(label)
    return labels


def paper_asset_status(
    paper: Paper, analysis_report: AnalysisReport | None = None
) -> dict[str, Any]:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    analysis_rounds = _analysis_round_labels(metadata)
    return {
        "pdf": bool(
            clean_text(getattr(paper, "pdf_path", None)) or clean_text(metadata.get("pdf_url"))
        ),
        "embedding": bool(getattr(paper, "embedding", None)),
        "skim": bool(analysis_report and clean_text(analysis_report.summary_md)),
        "deep": bool(analysis_report and clean_text(analysis_report.deep_dive_md)),
        "analysis_rounds": analysis_rounds,
    }


def load_analysis_reports(session, paper_ids: list[str]) -> dict[str, AnalysisReport]:
    ids = normalize_paper_ids(paper_ids)
    if not ids:
        return {}
    rows = session.execute(select(AnalysisReport).where(AnalysisReport.paper_id.in_(ids))).scalars()
    return {str(row.paper_id): row for row in rows}


def paper_ref_from_model(
    paper: Paper,
    *,
    ref_id: str,
    source: str,
    match_reason: str = "",
    selected: bool = False,
    project_linked: bool = False,
    analysis_report: AnalysisReport | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    publication_date = getattr(paper, "publication_date", None)
    return {
        "ref_id": clean_text(ref_id),
        "source": clean_text(source) or "paper_library",
        "status": "library",
        "paper_id": str(getattr(paper, "id", "") or ""),
        "title": clean_text(getattr(paper, "title", ""), max_chars=240),
        "arxiv_id": clean_text(getattr(paper, "arxiv_id", ""), max_chars=80),
        "authors": _metadata_list(metadata.get("authors")),
        "year": _year_from_value(publication_date),
        "publication_date": publication_date.isoformat()
        if hasattr(publication_date, "isoformat")
        else None,
        "citation_count": metadata.get("citation_count") or metadata.get("citationCount") or 0,
        "venue": clean_text(metadata.get("venue") or metadata.get("citation_venue"), max_chars=160)
        or None,
        "read_status": str(
            getattr(getattr(paper, "read_status", None), "value", getattr(paper, "read_status", ""))
            or ""
        ),
        "abstract_available": bool(clean_text(getattr(paper, "abstract", ""))),
        "pdf_url": clean_text(metadata.get("pdf_url"), max_chars=1000) or None,
        "source_url": clean_text(metadata.get("source_url"), max_chars=1000) or None,
        "asset_status": paper_asset_status(paper, analysis_report),
        "match_reason": clean_text(match_reason, max_chars=240),
        "selected": bool(selected),
        "project_linked": bool(project_linked),
        "note": clean_text(note, max_chars=240) or None,
        "importable": False,
        "linkable": True,
    }


def external_candidate_ref(
    *,
    ref_id: str,
    title: str,
    abstract: str = "",
    source: str = "external_candidate",
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    source_url: str | None = None,
    pdf_url: str | None = None,
    authors: list[str] | None = None,
    categories: list[str] | None = None,
    publication_date: str | None = None,
    publication_year: int | None = None,
    citation_count: int | None = None,
    venue: str | None = None,
    venue_type: str | None = None,
    venue_tier: str | None = None,
    match_reason: str = "",
) -> dict[str, Any]:
    external_id = clean_text(arxiv_id or openalex_id or source_url or title, max_chars=240)
    return {
        "ref_id": clean_text(ref_id),
        "source": clean_text(source) or "external_candidate",
        "status": "candidate",
        "external_id": external_id,
        "paper_id": None,
        "title": clean_text(title, max_chars=240),
        "abstract": clean_text(abstract, max_chars=4000),
        "abstract_available": bool(clean_text(abstract)),
        "authors": _metadata_list(authors or []),
        "categories": _metadata_list(categories or [], limit=12),
        "arxiv_id": clean_text(arxiv_id, max_chars=80) or None,
        "openalex_id": clean_text(openalex_id, max_chars=120) or None,
        "source_url": clean_text(source_url, max_chars=1000) or None,
        "pdf_url": clean_text(pdf_url, max_chars=1000) or None,
        "publication_date": clean_text(publication_date, max_chars=32) or None,
        "publication_year": publication_year,
        "year": publication_year or _year_from_value(publication_date),
        "citation_count": citation_count or 0,
        "venue": clean_text(venue, max_chars=160) or None,
        "venue_type": clean_text(venue_type, max_chars=80) or None,
        "venue_tier": clean_text(venue_tier, max_chars=80) or None,
        "match_reason": clean_text(match_reason, max_chars=240),
        "selected": False,
        "project_linked": False,
        "importable": True,
        "linkable": False,
        "asset_status": {
            "pdf": bool(clean_text(pdf_url)),
            "embedding": False,
            "skim": False,
            "deep": False,
            "analysis_rounds": [],
        },
    }


def workspace_pdf_ref(
    *, ref_id: str, path: str, title: str, match_reason: str = ""
) -> dict[str, Any]:
    return {
        "ref_id": clean_text(ref_id),
        "source": "workspace_pdf",
        "status": "candidate",
        "external_id": clean_text(path, max_chars=1000),
        "paper_id": None,
        "title": clean_text(title, max_chars=240),
        "path": clean_text(path, max_chars=1000),
        "abstract_available": False,
        "asset_status": {
            "pdf": True,
            "embedding": False,
            "skim": False,
            "deep": False,
            "analysis_rounds": [],
        },
        "match_reason": clean_text(match_reason, max_chars=240),
        "selected": False,
        "project_linked": False,
        "importable": False,
        "linkable": False,
    }


def _candidate_key(item: dict[str, Any]) -> str:
    for key in (
        "paper_id",
        "external_id",
        "arxiv_id",
        "openalex_id",
        "source_url",
        "path",
        "title",
    ):
        value = clean_text(item.get(key)).lower()
        if value:
            return f"{key}:{value}"
    return f"ref:{clean_text(item.get('ref_id')).lower()}"


def merge_refs(
    existing: list[dict[str, Any]] | None, incoming: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    key_to_index: dict[str, int] = {}
    for raw in [*(existing or []), *(incoming or [])]:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = _candidate_key(item)
        if key in key_to_index:
            current = merged[key_to_index[key]]
            current.update({k: v for k, v in item.items() if v not in (None, "", [])})
            if current.get("status") == "imported" or item.get("status") == "imported":
                current["status"] = "imported"
            continue
        key_to_index[key] = len(merged)
        merged.append(item)
    return merged


def format_ref_index_for_prompt(
    refs: list[dict[str, Any]] | None,
    *,
    empty_text: str,
    include_candidates: bool = False,
) -> str:
    items = [item for item in (refs or []) if isinstance(item, dict)]
    if not items:
        return empty_text
    lines = [
        "以下只是一组论文索引元信息，不包含论文正文或分析全文。",
        "如果某个阶段需要证据，请按 paper_id/ref_id 按需读取 skim、deep、三轮分析或 PDF/OCR 摘要；不要根据索引臆造论文结论。",
    ]
    for item in items:
        status = clean_text(item.get("status")) or "unknown"
        if not include_candidates and status == "candidate":
            continue
        ref_id = clean_text(item.get("ref_id")) or "-"
        title = clean_text(item.get("title"), max_chars=180) or "Untitled"
        source = clean_text(item.get("source")) or "unknown"
        paper_id = (
            clean_text(item.get("paper_id")) or clean_text(item.get("external_id")) or "unimported"
        )
        arxiv_id = clean_text(item.get("arxiv_id"))
        year = item.get("year") or item.get("publication_year") or ""
        assets = item.get("asset_status") if isinstance(item.get("asset_status"), dict) else {}
        asset_labels = []
        for key in ("pdf", "embedding", "skim", "deep"):
            if assets.get(key):
                asset_labels.append(key)
        rounds = (
            assets.get("analysis_rounds") if isinstance(assets.get("analysis_rounds"), list) else []
        )
        if rounds:
            asset_labels.append("analysis_rounds:" + ",".join(str(x) for x in rounds))
        meta = [
            f"source={source}",
            f"id={paper_id}",
            f"status={status}",
        ]
        if arxiv_id:
            meta.append(f"arxiv={arxiv_id}")
        if year:
            meta.append(f"year={year}")
        if asset_labels:
            meta.append("assets=" + "|".join(asset_labels))
        lines.append(f"- [{ref_id}] {title} ({'; '.join(meta)})")
    return "\n".join(lines).strip()


def build_on_demand_paper_analysis_context(
    session,
    paper_id: str,
    *,
    max_chars: int = 8000,
) -> str:
    paper = session.get(Paper, paper_id)
    if paper is None:
        return ""
    report = session.execute(
        select(AnalysisReport).where(AnalysisReport.paper_id == str(paper_id))
    ).scalar_one_or_none()
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    chunks = [
        f"标题: {clean_text(getattr(paper, 'title', ''))}",
        f"paper_id: {paper_id}",
        f"arXiv: {clean_text(getattr(paper, 'arxiv_id', ''))}",
    ]
    if report and clean_text(report.summary_md):
        chunks.append(f"[Skim]\n{str(report.summary_md).strip()}")
    if report and clean_text(report.deep_dive_md):
        chunks.append(f"[Deep Dive]\n{str(report.deep_dive_md).strip()}")
    rounds = metadata.get("analysis_rounds")
    if isinstance(rounds, dict):
        for key in ("round_1", "round_2", "round_3", "final_notes"):
            payload = rounds.get(key)
            markdown = (
                str(payload.get("markdown") or "").strip() if isinstance(payload, dict) else ""
            )
            if markdown:
                title = (
                    clean_text(payload.get("title") if isinstance(payload, dict) else key) or key
                )
                chunks.append(f"[{title}]\n{markdown}")
    return "\n\n".join(chunks).strip()[:max_chars]
