"""Paper management routes."""

import asyncio
import base64
import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from apps.api.deps import cache, get_paper_title, paper_list_response, rag_service
from packages.ai.paper.analysis_options import resolve_paper_analysis_levels
from packages.ai.paper.content_source import (
    normalize_paper_content_source,
    paper_content_source_label,
)
from packages.ai.paper.paper_ops_service import (
    FigureExtractionEmptyError,
    PaperPdfUnavailableError,
    PaperUploadNotFoundError,
    PaperUploadValidationError,
)
from packages.ai.paper.paper_ops_service import (
    apply_external_resolution as _apply_external_resolution,
)
from packages.ai.paper.paper_ops_service import (
    clear_pdf_derived_metadata as _clear_pdf_derived_metadata,
)
from packages.ai.paper.paper_ops_service import (
    ensure_paper_pdf as _ensure_paper_pdf_impl,
)
from packages.ai.paper.paper_ops_service import (
    extract_paper_figures_payload as _extract_paper_figures_payload_impl,
)
from packages.ai.paper.paper_ops_service import (
    has_real_arxiv_id as _has_real_arxiv_id,
)
from packages.ai.paper.paper_ops_service import (
    normalize_manual_paper_id as _normalize_manual_paper_id,
)
from packages.ai.paper.paper_ops_service import (
    replace_paper_pdf as _replace_paper_pdf_impl,
)
from packages.ai.paper.paper_ops_service import (
    resolve_external_pdf_source as _resolve_external_pdf_source,
)
from packages.ai.paper.paper_ops_service import (
    upload_paper_pdf as _upload_paper_pdf_impl,
)
from packages.ai.paper.paper_serializer import (
    attach_figure_image_urls as _attach_figure_image_urls,
)
from packages.ai.paper.paper_serializer import (
    paper_ocr_status_payload as _paper_ocr_status_payload,
)
from packages.ai.paper.paper_serializer import (
    utc_iso as _utc_iso,
)
from packages.config import get_settings
from packages.domain.schemas import (
    AIExplainReq,
    PaperAutoClassifyReq,
    PaperBatchDeleteReq,
    PaperFigureAnalyzeReq,
    PaperFigureDeleteReq,
    PaperMetadataUpdateReq,
    PaperReaderDocumentResp,
    PaperReaderNoteDraftReq,
    PaperReaderNoteReq,
    PaperReaderQueryReq,
)
from packages.domain.task_tracker import global_tracker
from packages.integrations.llm_client import LLMClient
from packages.storage.db import session_scope
from packages.storage.models import ImageAnalysis
from packages.storage.repositories import PaperRepository
from packages.storage.repository_facades import PaperDataFacade

router = APIRouter()
logger = logging.getLogger(__name__)


def _paper_data(session):
    return PaperDataFacade.from_session(session)


_PAPER_FIGURES_CACHE_TTL_SEC = 180
_FIGURE_IMAGE_CACHE_TTL_SEC = 86400


def _cache_key_paper_figures(paper_id: UUID | str) -> str:
    return f"paper_figures_{paper_id}"


def _invalidate_paper_figures_cache(paper_id: UUID | str) -> None:
    cache.invalidate(_cache_key_paper_figures(paper_id))


def _cache_paper_figures_items(paper_id: UUID, items: list[dict]) -> None:
    cache.set(
        _cache_key_paper_figures(paper_id),
        {"items": items},
        ttl=_PAPER_FIGURES_CACHE_TTL_SEC,
    )


def _is_reader_analysis_action(action: str) -> bool:
    return str(action or "").strip().lower() in {"analyze", "explain", "summarize"}


def _build_pdf_reader_ai_prompt(
    action: str,
    text: str,
    *,
    question: str | None = None,
) -> str:
    excerpt = text.strip()[:3000]
    if action == "translate":
        return (
            "你是论文阅读助手。请将下面的学术片段准确翻译为简体中文。\n"
            "要求：\n"
            "1. 只输出中文译文，不要添加英文改写、解释、前言或总结。\n"
            "2. 保留公式、符号、变量名、缩写和专有名词；必要时可在中文中保留原词。\n"
            "3. 如果原文是不完整片段，就按片段直译，不要自行补全文意。\n\n"
            f"原文片段：\n{excerpt}"
        )
    if _is_reader_analysis_action(action):
        return (
            "你是论文阅读助手。请用简体中文分析下面的论文片段。\n"
            "要求：\n"
            "1. 先用 1-2 句话概括这段内容在讲什么。\n"
            "2. 再分点提取关键方法、结论、假设、限制或上下文含义。\n"
            "3. 如果片段信息不完整，要明确说明你的判断范围。\n"
            "4. 除专有名词、公式和必要缩写外，不要使用英文整句。\n\n"
            f"论文片段：\n{excerpt}"
        )
    if action == "ask":
        user_question = (question or "").strip()
        if not user_question:
            raise HTTPException(status_code=400, detail="question is required for ask action")
        return (
            "你是论文阅读助手。请根据给定论文片段回答用户问题，输出必须为简体中文。\n"
            "要求：\n"
            "1. 优先基于片段内容回答，不要脱离原文随意发挥。\n"
            "2. 如果片段信息不足，请明确指出“根据当前片段无法完全确定”，再给出谨慎推断。\n"
            "3. 回答尽量直接，必要时可以分点说明。\n\n"
            f"用户问题：\n{user_question}\n\n"
            f"论文片段：\n{excerpt}"
        )
    raise HTTPException(status_code=400, detail="unsupported action")


_READER_NOTE_COLORS = {"amber", "blue", "emerald", "rose", "violet", "slate"}
_READER_NOTE_STATUSES = {"draft", "saved"}
_READER_NOTE_SOURCES = {"manual", "ai_draft"}
_READER_NOTE_ANCHOR_SOURCES = {"pdf_selection", "ocr_block"}


def _clean_reader_text(value: str | None, *, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_len]


def _normalize_reader_tags(tags: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in tags or []:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(value[:32])
        if len(normalized) >= 12:
            break
    return normalized


def _reader_note_sort_ts(note: dict) -> float:
    for key in ("updated_at", "created_at"):
        raw = str(note.get(key) or "").strip()
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _sanitize_reader_note_color(value: str | None) -> str:
    color = str(value or "").strip().lower()
    return color if color in _READER_NOTE_COLORS else "amber"


def _sanitize_reader_note_status(value: str | None) -> str:
    status = str(value or "").strip().lower()
    return status if status in _READER_NOTE_STATUSES else "saved"


def _sanitize_reader_note_source(value: str | None) -> str:
    source = str(value or "").strip().lower()
    return source if source in _READER_NOTE_SOURCES else "manual"


def _sanitize_reader_note_anchor_source(value: str | None) -> str | None:
    source = str(value or "").strip().lower()
    return source if source in _READER_NOTE_ANCHOR_SOURCES else None


def _clean_reader_optional_id(value: str | None, *, max_len: int = 120) -> str | None:
    cleaned = _clean_reader_text(value, max_len=max_len)
    return cleaned or None


def _sort_reader_notes(notes: list[dict]) -> list[dict]:
    return sorted(
        notes,
        key=lambda item: (
            0 if item.get("pinned") else 1,
            -_reader_note_sort_ts(item),
            str(item.get("title") or "").lower(),
        ),
    )


def _normalize_reader_note_dict(raw: dict) -> dict | None:
    note_id = str(raw.get("id") or "").strip()
    if not note_id:
        return None
    kind = str(raw.get("kind") or "general").strip().lower()
    if kind not in {"general", "text", "figure"}:
        kind = "general"
    page_number = raw.get("page_number")
    try:
        page_number = int(page_number) if page_number is not None else None
    except (TypeError, ValueError):
        page_number = None
    if page_number is not None and page_number < 1:
        page_number = None
    note = {
        "id": note_id,
        "kind": kind,
        "title": _clean_reader_text(raw.get("title"), max_len=120),
        "content": _clean_reader_text(raw.get("content"), max_len=12000),
        "quote": _clean_reader_text(raw.get("quote"), max_len=2500),
        "page_number": page_number,
        "figure_id": str(raw.get("figure_id") or "").strip() or None,
        "color": _sanitize_reader_note_color(raw.get("color")),
        "tags": _normalize_reader_tags(
            raw.get("tags") if isinstance(raw.get("tags"), list) else []
        ),
        "pinned": bool(raw.get("pinned")),
        "status": _sanitize_reader_note_status(raw.get("status")),
        "source": _sanitize_reader_note_source(raw.get("source")),
        "anchor_source": _sanitize_reader_note_anchor_source(raw.get("anchor_source")),
        "anchor_id": _clean_reader_optional_id(raw.get("anchor_id")),
        "section_id": _clean_reader_optional_id(raw.get("section_id")),
        "section_title": _clean_reader_optional_id(raw.get("section_title"), max_len=160),
        "created_at": str(raw.get("created_at") or "").strip() or _utc_iso(),
        "updated_at": str(raw.get("updated_at") or "").strip() or _utc_iso(),
    }
    if not note["title"]:
        preview = note["quote"] or note["content"] or "未命名笔记"
        note["title"] = preview[:60]
    return note


def _reader_notes_from_metadata(metadata: dict | None) -> list[dict]:
    if not isinstance(metadata, dict):
        return []
    raw_items = metadata.get("reader_notes")
    if not isinstance(raw_items, list):
        return []
    notes: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        note = _normalize_reader_note_dict(raw)
        if note is not None:
            notes.append(note)
    return _sort_reader_notes(notes)


def _normalize_reader_document_bbox(raw_bbox: list[float] | None) -> dict[str, float] | None:
    if not isinstance(raw_bbox, list) or len(raw_bbox) < 4:
        return None
    try:
        x0, y0, x1, y1 = [float(raw_bbox[idx]) for idx in range(4)]
    except Exception:
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return {
        "x": x0,
        "y": y0,
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": x1 - x0,
        "height": y1 - y0,
    }


def _resolve_reader_structured_text(item: dict) -> str:
    raw_type = str(item.get("type") or "").strip().lower()
    if raw_type == "list":
        values = item.get("list_items")
        if isinstance(values, list):
            parts = [_clean_reader_text(entry, max_len=300) for entry in values]
            parts = [part for part in parts if part]
            if parts:
                return "\n".join(f"- {part}" for part in parts)[:12000]
        return _clean_reader_text(item.get("text"), max_len=12000)
    if raw_type in {"text", "aside_text"}:
        return _clean_reader_text(item.get("text"), max_len=12000)
    return ""


def _join_reader_structured_parts(value: Any) -> str:
    if isinstance(value, str):
        return _clean_reader_text(value, max_len=6000)
    if not isinstance(value, list):
        return ""
    parts = [_clean_reader_text(item, max_len=1200) for item in value]
    return " ".join(part for part in parts if part)[:6000]


def _resolve_reader_structured_visual_block(item: dict, paper_id: UUID) -> tuple[str, str, str]:
    from packages.ai.paper.figure_service import FigureService

    raw_type = str(item.get("type") or "").strip().lower()
    if raw_type == "equation":
        text = _clean_reader_text(item.get("text"), max_len=12000)
        return "equation", text, text

    if raw_type in {"image", "chart"}:
        caption = _join_reader_structured_parts(item.get("image_caption"))
        footnote = _join_reader_structured_parts(item.get("image_footnote"))
        img_path = _clean_reader_text(item.get("img_path"), max_len=500)
        image_url = _reader_ocr_asset_url(paper_id, img_path) if img_path else ""
        markdown_parts: list[str] = []
        if image_url:
            markdown_parts.append(f"![figure]({image_url})")
        if caption:
            markdown_parts.append(caption)
        if footnote and footnote.lower() != caption.lower():
            markdown_parts.append(footnote)
        text = caption or footnote or "图片"
        return "image", text, "\n\n".join(part for part in markdown_parts if part).strip()

    if raw_type == "table":
        caption = _join_reader_structured_parts(item.get("table_caption"))
        footnote = _join_reader_structured_parts(item.get("table_footnote"))
        table_body = FigureService._normalize_candidate_markdown(item.get("table_body"))
        img_path = _clean_reader_text(item.get("img_path"), max_len=500)
        image_url = _reader_ocr_asset_url(paper_id, img_path) if img_path else ""
        markdown_parts: list[str] = []
        if image_url:
            markdown_parts.append(f"![table]({image_url})")
        if caption:
            markdown_parts.append(caption)
        if table_body:
            markdown_parts.append(table_body)
        if footnote and footnote.lower() not in {caption.lower(), table_body.lower()}:
            markdown_parts.append(footnote)
        text = caption or "表格"
        return "table", text, "\n\n".join(part for part in markdown_parts if part).strip()

    return raw_type, "", ""


def _coerce_reader_text_level(value: Any) -> int | None:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    if level < 1:
        return None
    return min(level, 6)


def _looks_like_reader_heading(raw_type: str, text: str, text_level: int | None) -> bool:
    if raw_type != "text" or text_level is None:
        return False
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return len(normalized) <= 220


def _reader_default_section(section_order: int, page_number: int | None) -> dict[str, Any]:
    return {
        "id": f"section_{section_order}",
        "title": "前置信息",
        "level": 1,
        "order": section_order,
        "page_start": page_number,
    }


def _build_reader_structured_document(bundle) -> dict[str, Any] | None:  # noqa: ANN001
    from packages.ai.paper.figure_service import FigureService

    content_paths = FigureService._collect_mineru_structured_json_files(
        bundle.output_root, "_content_list.json"
    )
    if not content_paths:
        return None

    sections: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    section_order = 0
    block_order = 0

    for content_path in content_paths:
        try:
            payload = json.loads(content_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            raw_type = str(item.get("type") or "").strip().lower()
            if raw_type not in {
                "text",
                "aside_text",
                "list",
                "equation",
                "image",
                "table",
                "chart",
            }:
                continue
            if raw_type in {"text", "aside_text", "list"}:
                text = _resolve_reader_structured_text(item)
                block_type = "list" if raw_type == "list" else raw_type
                markdown = text
            else:
                block_type, text, markdown = _resolve_reader_structured_visual_block(
                    item, bundle.paper_id
                )
            if not text:
                continue
            try:
                page_number = int(item.get("page_idx") or 0) + 1
            except (TypeError, ValueError):
                page_number = None
            text_level = _coerce_reader_text_level(item.get("text_level"))
            is_heading = _looks_like_reader_heading(raw_type, text, text_level)

            if is_heading:
                current_section = {
                    "id": f"section_{section_order}",
                    "title": text,
                    "level": text_level or 1,
                    "order": section_order,
                    "page_start": page_number,
                }
                sections.append(current_section)
                section_order += 1
            elif current_section is None:
                current_section = _reader_default_section(section_order, page_number)
                sections.append(current_section)
                section_order += 1

            if is_heading:
                block_type = "heading"
                markdown = f"{'#' * max(1, text_level or 1)} {text}"

            blocks.append(
                {
                    "id": f"block_{block_order}",
                    "section_id": str(current_section["id"]),
                    "page_number": page_number,
                    "order": block_order,
                    "type": block_type,
                    "text": text,
                    "markdown": markdown,
                    "bbox": _normalize_reader_document_bbox(
                        FigureService._normalize_mineru_bbox_list(item.get("bbox"))
                    ),
                    "bbox_normalized": True,
                }
            )
            block_order += 1

    if not blocks:
        return None
    return {
        "available": True,
        "source": "mineru_structured",
        "markdown": str(getattr(bundle, "markdown_text", "") or ""),
        "sections": sections,
        "blocks": blocks,
    }


def _strip_reader_markdown_heading(title: str, body: str) -> str:
    lines = str(body or "").splitlines()
    if not lines:
        return ""
    normalized_title = re.sub(r"^#+\s*", "", str(title or "")).strip().lower()
    first_line = re.sub(r"^#+\s*", "", lines[0]).strip().lower()
    if normalized_title and first_line == normalized_title:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _reader_markdown_heading_level(title: str) -> int:
    stripped = str(title or "").strip()
    match = re.match(r"^(#{1,6})\s+", stripped)
    if match:
        return len(match.group(1))
    numeric = re.match(r"^(?:\d+(?:\.\d+)*)", stripped)
    if numeric:
        return min(max(1, numeric.group(0).count(".") + 1), 6)
    return 1


def _split_reader_markdown_paragraphs(text: str) -> list[str]:
    blocks = [
        part.strip() for part in re.split(r"\n\s*\n+", str(text or "").strip()) if part.strip()
    ]
    return blocks[:240]


def _build_reader_markdown_document(bundle) -> dict[str, Any] | None:  # noqa: ANN001
    from packages.ai.paper.mineru_runtime import MinerUOcrRuntime

    markdown = str(getattr(bundle, "markdown_text", "") or "").strip()
    if not markdown:
        return None

    raw_sections = MinerUOcrRuntime._split_markdown_sections(markdown)
    if not raw_sections:
        return None

    sections: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    block_order = 0
    for section_order, (raw_title, raw_body) in enumerate(raw_sections):
        title = re.sub(r"^#+\s*", "", str(raw_title or "")).strip() or f"章节 {section_order + 1}"
        level = _reader_markdown_heading_level(raw_title)
        section_id = f"section_{section_order}"
        sections.append(
            {
                "id": section_id,
                "title": title,
                "level": level,
                "order": section_order,
                "page_start": None,
            }
        )
        blocks.append(
            {
                "id": f"block_{block_order}",
                "section_id": section_id,
                "page_number": None,
                "order": block_order,
                "type": "heading",
                "text": title,
                "markdown": f"{'#' * max(1, level)} {title}",
                "bbox": None,
                "bbox_normalized": False,
            }
        )
        block_order += 1
        for paragraph in _split_reader_markdown_paragraphs(
            _strip_reader_markdown_heading(raw_title, raw_body)
        ):
            blocks.append(
                {
                    "id": f"block_{block_order}",
                    "section_id": section_id,
                    "page_number": None,
                    "order": block_order,
                    "type": "text",
                    "text": paragraph,
                    "markdown": paragraph,
                    "bbox": None,
                    "bbox_normalized": False,
                }
            )
            block_order += 1

    return {
        "available": True,
        "source": "mineru_markdown",
        "markdown": markdown,
        "sections": sections,
        "blocks": blocks,
    }


def _normalize_reader_ocr_asset_path(asset_path: str) -> tuple[str, list[str]]:
    normalized = str(asset_path or "").replace("\\", "/").lstrip("./").lstrip("/")
    parts = [part for part in normalized.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts), parts


def _reader_ocr_asset_url(paper_id: UUID, asset_path: str) -> str:
    normalized, parts = _normalize_reader_ocr_asset_path(asset_path)
    encoded = "/".join(quote(part) for part in parts)
    return f"/papers/{paper_id}/ocr/assets/{encoded}" if encoded else ""


def _is_path_inside(parent: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_reader_ocr_asset_path(bundle, asset_path: str) -> Path | None:  # noqa: ANN001
    normalized, parts = _normalize_reader_ocr_asset_path(asset_path)
    if not normalized or not parts:
        return None

    output_root = Path(bundle.output_root).resolve()
    candidate_bases: list[Path] = [output_root]
    for markdown_file in getattr(bundle, "markdown_files", []) or []:
        try:
            parent = Path(str(markdown_file)).expanduser().resolve(strict=False).parent
        except Exception:
            continue
        if _is_path_inside(output_root, parent) and parent not in candidate_bases:
            candidate_bases.append(parent)

    for base in candidate_bases:
        candidate = (base / Path(*parts)).resolve(strict=False)
        if _is_path_inside(output_root, candidate) and candidate.exists() and candidate.is_file():
            return candidate

    # MinerU often emits Markdown links like images/x.jpg relative to the
    # generated Markdown folder (.../auto/images), not the OCR output root.
    filename = parts[-1]
    for candidate in output_root.rglob(filename):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve(strict=False)
        if not _is_path_inside(output_root, resolved):
            continue
        relative = resolved.relative_to(output_root).as_posix()
        if relative == normalized or relative.endswith(f"/{normalized}"):
            return resolved
    return None


def _rewrite_reader_markdown_assets(markdown: str, paper_id: UUID) -> str:
    def _replace_link(match: re.Match[str]) -> str:
        prefix = match.group(1)
        raw_path = str(match.group(2) or "").strip()
        suffix = match.group(3)
        if not raw_path or re.match(r"^(?:[a-z]+:|/|#)", raw_path, flags=re.IGNORECASE):
            return match.group(0)
        resolved = _reader_ocr_asset_url(paper_id, raw_path)
        return f"{prefix}{resolved}{suffix}" if resolved else match.group(0)

    rewritten = re.sub(r"(!?\[[^\]]*\]\()([^) \t]+)([^)]*\))", _replace_link, str(markdown or ""))
    rewritten = re.sub(
        r'(<img[^>]+src=["\'])([^"\']+)(["\'])',
        lambda match: (
            match.group(0)
            if re.match(r"^(?:[a-z]+:|/|#)", str(match.group(2) or ""), flags=re.IGNORECASE)
            else f"{match.group(1)}{_reader_ocr_asset_url(paper_id, match.group(2))}{match.group(3)}"
        ),
        rewritten,
        flags=re.IGNORECASE,
    )
    return rewritten


def _build_reader_document_payload(
    paper_id: UUID, bundle
) -> dict[str, Any] | PaperReaderDocumentResp:  # noqa: ANN001
    if bundle is None:
        return PaperReaderDocumentResp(paper_id=str(paper_id))
    payload = _build_reader_structured_document(bundle) or _build_reader_markdown_document(bundle)
    if not payload:
        return PaperReaderDocumentResp(paper_id=str(paper_id))
    payload["markdown"] = _rewrite_reader_markdown_assets(
        str(payload.get("markdown") or ""), paper_id
    )
    payload["paper_id"] = str(paper_id)
    return payload


def _load_reader_document_bundle(session, repo: PaperRepository, paper, paper_id: UUID):  # noqa: ANN001
    from packages.ai.paper.mineru_runtime import MinerUOcrRuntime

    metadata = dict(getattr(paper, "metadata_json", None) or {})
    ocr_payload = metadata.get("mineru_ocr") if isinstance(metadata.get("mineru_ocr"), dict) else {}
    pdf_path = ""
    pdf_sha256 = str((ocr_payload or {}).get("pdf_sha256") or "").strip()

    try:
        pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)
        bundle = MinerUOcrRuntime.get_cached_bundle(paper_id, pdf_path)
        if bundle is not None:
            return bundle
        if not pdf_sha256 and Path(pdf_path).exists():
            pdf_sha256 = MinerUOcrRuntime._hash_file(Path(pdf_path))
    except HTTPException:
        pass
    except Exception as exc:
        logger.warning("Reader document cached bundle lookup failed for %s: %s", paper_id, exc)

    output_root_raw = str((ocr_payload or {}).get("output_root") or "").strip()
    if not output_root_raw:
        return None

    output_root = Path(output_root_raw).expanduser()
    if not output_root.exists() or not output_root.is_dir():
        return None

    manifest = MinerUOcrRuntime._read_manifest(output_root)
    merged_manifest = dict(manifest or {})
    for key, value in (ocr_payload or {}).items():
        merged_manifest.setdefault(str(key), value)

    status = str(merged_manifest.get("status") or "").strip().lower()
    has_outputs = MinerUOcrRuntime._has_outputs(output_root)
    if status not in {"success", "completed"} and not has_outputs:
        return None

    if not pdf_path:
        resolved_pdf = _resolve_stored_pdf_path(getattr(paper, "pdf_path", None))
        if resolved_pdf is not None:
            pdf_path = str(resolved_pdf)
    if not pdf_sha256:
        pdf_sha256 = str(merged_manifest.get("pdf_sha256") or "").strip()

    return MinerUOcrRuntime._build_bundle(
        paper_id=paper_id,
        pdf_path=pdf_path,
        pdf_sha256=pdf_sha256,
        output_root=output_root,
        extra_manifest=merged_manifest,
    )


def _build_reader_note_draft_prompt(
    *,
    title: str,
    section_title: str | None,
    text: str,
    quote: str | None,
    page_number: int | None,
) -> str:
    location = []
    if section_title:
        location.append(f"章节：{section_title}")
    if page_number:
        location.append(f"页码：第 {page_number} 页")
    location_text = "\n".join(location) if location else "章节：未定位"
    quote_text = str(quote or text).strip()[:2500]
    return (
        "你是论文精读助手。请基于给定论文片段，生成一条适合研究阅读器保存前查看的“研究笔记草稿”。\n"
        "只允许输出一个严格 JSON 对象，不要输出 Markdown 代码块，不要输出解释文字。\n"
        "字段如下：\n"
        '{"title":"8-24字标题","content":"Markdown 笔记正文","tags":["标签1","标签2"],"color":"amber"}\n'
        "要求：\n"
        "1. title 必须直接概括该段最值得记录的研究信息，不要写“这段讲了什么”“AI 批注”等空话。\n"
        "2. content 必须是一个 Markdown 字符串，使用 2-4 条 '-' 开头的短列表；优先覆盖：核心方法/结论、明确证据、局限或待核实点。\n"
        "3. 如果这段只是承接句、表格引用、实验提示或结论线索，就如实写成“线索/提示”，不要把它扩写成论文已经证明的结论。\n"
        "4. 不要写整篇总结，不要杜撰原文没有给出的实验结果、数值、动机或局限。\n"
        "5. tags 最多 4 个，尽量短；color 只能从 amber / blue / emerald / rose / violet / slate 中选择。\n"
        "6. 输出必须可直接被前端解析为 JSON，content 不能为空。\n\n"
        f"论文标题：{title[:300]}\n"
        f"{location_text}\n\n"
        f"片段原文：\n{quote_text}\n\n"
        f"补充上下文：\n{text[:4000]}"
    )


def _build_reader_note_draft_fallback_prompt(
    *,
    title: str,
    section_title: str | None,
    text: str,
    quote: str | None,
    page_number: int | None,
) -> str:
    location = []
    if section_title:
        location.append(f"章节：{section_title}")
    if page_number:
        location.append(f"页码：第 {page_number} 页")
    location_text = "\n".join(location) if location else "章节：未定位"
    quote_text = str(quote or text).strip()[:2500]
    return (
        "你是论文精读助手。请为下面的论文片段生成一条可直接保存的中文研究笔记草稿。\n"
        "不要输出 JSON，不要输出解释，只能严格按下面格式输出：\n"
        "标题：一句标题\n"
        "标签：标签1, 标签2\n"
        "颜色：amber\n"
        "正文：\n"
        "- 要点 1\n"
        "- 要点 2\n"
        "- 要点 3\n\n"
        "要求：\n"
        "1. 标题直接概括最值得记录的研究信息。\n"
        "2. 正文必须是 2-4 条简短要点，优先写方法、证据、限制、待核实点。\n"
        "3. 如果片段只是表格/图表/实验引用，只能写成线索，不能杜撰结论。\n"
        "4. 颜色只能从 amber / blue / emerald / rose / violet / slate 中选择。\n"
        "5. 标签最多 4 个，尽量短。\n\n"
        f"论文标题：{title[:300]}\n"
        f"{location_text}\n\n"
        f"片段原文：\n{quote_text}\n\n"
        f"补充上下文：\n{text[:4000]}"
    )


def _split_reader_note_text_tags(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,，/、;；]+", str(value))
    return _normalize_reader_tags([part.strip() for part in parts if str(part).strip()])


def _normalize_reader_note_markdown(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    lines: list[str] = []
    for raw_line in raw.splitlines():
        line = str(raw_line).strip()
        if not line:
            continue
        if re.match(r"^(?:标题|title|标签|tags|颜色|color)\s*[:：]", line, flags=re.IGNORECASE):
            continue
        line = re.sub(r"^\s*(?:[-*•]\s*|\d+[.)]\s*)", "", line).strip()
        line = re.sub(r"^(?:正文|内容)\s*[:：]\s*", "", line, flags=re.IGNORECASE).strip()
        if line:
            lines.append(line)

    if not lines:
        chunks = [
            chunk.strip() for chunk in re.split(r"(?<=[。！？!?；;])\s+|\n+", raw) if chunk.strip()
        ]
        lines.extend(chunks)

    normalized: list[str] = []
    seen: set[str] = set()
    for line in lines:
        compact = _clean_reader_text(line, max_len=220)
        lowered = compact.lower()
        if not compact or lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(compact)
        if len(normalized) >= 4:
            break
    return "\n".join(f"- {line}" for line in normalized)


def _parse_reader_note_text_payload(raw_text: str | None) -> dict[str, Any]:
    raw = str(raw_text or "").strip()
    if not raw:
        return {}

    title_match = re.search(
        r"^\s*(?:标题|title)\s*[:：]\s*(.+)$", raw, flags=re.IGNORECASE | re.MULTILINE
    )
    tags_match = re.search(
        r"^\s*(?:标签|tags)\s*[:：]\s*(.+)$", raw, flags=re.IGNORECASE | re.MULTILINE
    )
    color_match = re.search(
        r"^\s*(?:颜色|color)\s*[:：]\s*([a-zA-Z_]+)\s*$", raw, flags=re.IGNORECASE | re.MULTILINE
    )
    body_match = re.search(r"(?:正文|内容)\s*[:：]\s*(.+)$", raw, flags=re.IGNORECASE | re.DOTALL)

    body = body_match.group(1).strip() if body_match else raw
    if not body_match:
        body_lines = [
            line
            for line in raw.splitlines()
            if not re.match(
                r"^\s*(?:标题|title|标签|tags|颜色|color)\s*[:：]", line, flags=re.IGNORECASE
            )
        ]
        body = "\n".join(body_lines).strip()

    return {
        "title": _clean_reader_text(title_match.group(1) if title_match else "", max_len=120),
        "content": _normalize_reader_note_markdown(body),
        "tags": _split_reader_note_text_tags(tags_match.group(1) if tags_match else ""),
        "color": _sanitize_reader_note_color(color_match.group(1) if color_match else ""),
    }


def _derive_reader_note_title(
    *,
    title: str | None,
    quote: str,
    text: str,
    section_title: str | None,
) -> str:
    explicit = _clean_reader_text(title, max_len=120)
    if explicit:
        return explicit

    source = _clean_reader_text(quote or text, max_len=240)
    if not source:
        return _clean_reader_text(section_title, max_len=36) or "研究笔记"

    parts = [
        part.strip(" -:：") for part in re.split(r"[。！？!?；;:\n]", source) if part.strip(" -:：")
    ]
    candidate = parts[0] if parts else source
    candidate = re.sub(
        r"^(?:we|this paper|the authors?|本文|作者|该段|这一段)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    if section_title and len(candidate) < 8:
        candidate = f"{section_title} · {candidate}"
    return candidate[:60]


def _derive_reader_note_tags(
    *,
    title: str,
    content: str,
    quote: str,
    section_title: str | None,
) -> list[str]:
    corpus = " ".join(part for part in [title, content, quote, section_title or ""] if part).lower()
    rules = [
        (
            "方法",
            r"(method|framework|architecture|module|approach|fusion|training|objective|loss|算法|方法|模块|训练|目标函数)",
        ),
        (
            "结果",
            r"(result|improv|outperform|better|gain|benchmark|accuracy|retrieval|vqa|结果|提升|优于|性能|指标)",
        ),
        (
            "实验",
            r"(experiment|ablation|compare|comparison|baseline|table|figure|fig\.|实验|对比|表\s*\d|图\s*\d)",
        ),
        ("公式", r"(equation|formula|loss|theorem|proof|公式|定理|证明)"),
        ("数据", r"(dataset|corpus|data|benchmark|数据集|语料|样本)"),
        ("局限", r"(limit|weakness|future|todo|unclear|need verify|待核实|局限|不足|进一步)"),
        ("问题", r"(task|problem|challenge|goal|问题|任务|挑战|目标)"),
    ]
    tags: list[str] = []
    for label, pattern in rules:
        if re.search(pattern, corpus, flags=re.IGNORECASE):
            tags.append(label)
        if len(tags) >= 4:
            break
    return _normalize_reader_tags(tags)


def _derive_reader_note_color(*, content: str, tags: list[str]) -> str:
    corpus = " ".join([content, *tags]).lower()
    if re.search(
        r"(局限|不足|待核实|future|todo|unclear|weakness|limit)", corpus, flags=re.IGNORECASE
    ):
        return "rose"
    if re.search(
        r"(结果|实验|benchmark|result|improv|outperform|comparison|ablation)",
        corpus,
        flags=re.IGNORECASE,
    ):
        return "emerald"
    if re.search(r"(公式|theorem|proof|equation|formula)", corpus, flags=re.IGNORECASE):
        return "violet"
    if re.search(
        r"(方法|训练|module|method|framework|architecture|objective|loss)",
        corpus,
        flags=re.IGNORECASE,
    ):
        return "blue"
    if re.search(r"(数据|dataset|corpus|data)", corpus, flags=re.IGNORECASE):
        return "slate"
    return "amber"


def _looks_like_reader_note_placeholder(content: str, quote: str) -> bool:
    normalized = _clean_reader_text(content, max_len=600)
    normalized_quote = _clean_reader_text(quote, max_len=600)
    if not normalized:
        return True
    stripped = re.sub(r"^\s*-\s*该段核心信息[:：]\s*", "", normalized).strip()
    if stripped and normalized_quote and stripped == normalized_quote:
        return True
    return normalized in {"- 该段核心信息", "该段核心信息"}


def _build_reader_note_deterministic_fallback(
    *,
    quote: str,
    text: str,
    section_title: str | None,
    page_number: int | None,
) -> str:
    excerpt = _clean_reader_text(quote or text, max_len=220)
    bullets = [f"关键信息：{excerpt}"] if excerpt else []
    if section_title or page_number:
        location = section_title or "未定位"
        if page_number:
            location = f"{location} · 第 {page_number} 页"
        bullets.append(f"位置：{location}")
    if re.search(
        r"(table|figure|fig\.|表\s*\d|图\s*\d|comparison|ablation|benchmark)",
        quote or text,
        flags=re.IGNORECASE,
    ):
        bullets.append("阅读提示：该段更像图表或实验结果线索，建议结合对应表格/图像继续核对。")
    else:
        bullets.append("阅读提示：建议结合前后文继续核对证据范围、方法条件和适用边界。")
    return "\n".join(f"- {item}" for item in bullets if item)


def _build_reader_paper_context(session, repo: PaperRepository, paper, paper_id: UUID) -> str:  # noqa: ANN001
    from packages.ai.paper.figure_service import FigureService
    from packages.ai.paper.mineru_runtime import MinerUOcrRuntime
    from packages.ai.paper.pdf_parser import PdfTextExtractor

    metadata = dict(getattr(paper, "metadata_json", None) or {})
    title = str(getattr(paper, "title", "") or "").strip()
    abstract = str(getattr(paper, "abstract", "") or "").strip()
    title_zh = str(metadata.get("title_zh") or "").strip()
    abstract_zh = str(metadata.get("abstract_zh") or "").strip()
    keywords = [str(item).strip() for item in (metadata.get("keywords") or []) if str(item).strip()]

    analysis_rounds = (
        metadata.get("analysis_rounds") if isinstance(metadata.get("analysis_rounds"), dict) else {}
    )
    round_chunks: list[str] = []
    for key in ("round_1", "round_2", "round_3", "final_notes"):
        round_payload = analysis_rounds.get(key) if isinstance(analysis_rounds, dict) else None
        if not isinstance(round_payload, dict):
            continue
        title_hint = str(round_payload.get("title") or key).strip()
        markdown = str(round_payload.get("markdown") or "").strip()
        if markdown:
            round_chunks.append(f"[{title_hint}]\n{markdown[:4000]}")

    pdf_text = ""
    try:
        pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)
        ocr_bundle = MinerUOcrRuntime.get_cached_bundle(paper_id, pdf_path)
        if ocr_bundle is not None:
            pdf_text = ocr_bundle.build_analysis_context(max_chars=18000).strip()
        if not pdf_text:
            pdf_text = PdfTextExtractor().extract_text(pdf_path, max_pages=18).strip()
    except Exception as exc:
        logger.warning("Paper reader context fallback for %s: %s", paper_id, exc)

    figure_chunks: list[str] = []
    for item in FigureService.get_paper_analyses(paper_id)[:6]:
        caption = str(item.get("caption") or "").strip()
        description = str(item.get("description") or "").strip()
        if not caption and not description:
            continue
        figure_chunks.append(
            (
                f"第 {int(item.get('page_number') or 0)} 页 "
                f"{str(item.get('image_type') or 'figure').strip()}: "
                f"{caption[:500]}\n{description[:1600]}"
            ).strip()
        )

    round_context = "\n\n".join(round_chunks)
    figure_context = "\n\n".join(figure_chunks)

    sections = [
        f"论文标题：{title}" if title else "",
        f"中文标题：{title_zh}" if title_zh else "",
        f"摘要：{abstract[:3000]}" if abstract else "",
        f"中文摘要：{abstract_zh[:3000]}" if abstract_zh else "",
        f"关键词：{', '.join(keywords[:20])}" if keywords else "",
        f"已有分析：\n\n{round_context}" if round_context else "",
        f"图表要点：\n\n{figure_context}" if figure_context else "",
        f"论文正文摘录：\n\n{pdf_text[:18000]}" if pdf_text else "",
    ]
    return "\n\n".join(section for section in sections if section).strip()[:24000]


def _build_full_paper_reader_prompt(
    action: str,
    context: str,
    *,
    question: str | None = None,
) -> str:
    if _is_reader_analysis_action(action):
        return (
            "你是论文阅读助手。请基于给定的整篇论文上下文，用简体中文分析论文。\n"
            "要求：\n"
            "1. 按“研究问题 / 方法 / 实验与结果 / 局限与启发”四部分组织。\n"
            "2. 优先复用上下文中已有分析、图表结论和正文证据，不要补造不存在的实验结论。\n"
            "3. 如果上下文不完整，要明确指出缺失位置。\n\n"
            f"论文上下文：\n{context}"
        )
    if action == "ask":
        user_question = str(question or "").strip()
        if not user_question:
            raise HTTPException(status_code=400, detail="question is required for paper ask")
        return (
            "你是论文阅读助手。请基于给定的整篇论文上下文回答用户问题，输出必须为简体中文。\n"
            "要求：\n"
            "1. 优先依据上下文中的具体信息作答。\n"
            "2. 若证据不足，请明确说明“根据当前论文上下文无法完全确定”。\n"
            "3. 回答尽量结构化，必要时可分点。\n\n"
            f"用户问题：\n{user_question}\n\n"
            f"论文上下文：\n{context}"
        )
    raise HTTPException(status_code=400, detail="unsupported action")


def _build_figure_reader_prompt(
    action: str,
    *,
    caption: str,
    description: str,
    question: str | None = None,
) -> str:
    figure_context = "\n".join(
        item
        for item in (
            f"题注：{caption[:1000]}" if caption else "",
            f"已有解析：{description[:2000]}" if description else "",
        )
        if item
    ).strip()
    if _is_reader_analysis_action(action):
        return (
            "你是论文图表阅读助手。请结合图片内容与附加上下文，用简体中文分析这张图、表或框选区域。\n"
            "要求：\n"
            "1. 先判断这是什么类型的图表或区域内容，以及它主要表达什么。\n"
            "2. 提取关键趋势、对比关系、结构组成、主要数字或视觉证据。\n"
            "3. 再总结这部分内容对论文结论或方法理解的意义。\n"
            "4. 如果图片是局部截图、信息有限或无法辨认，请明确指出。\n\n"
            f"附加上下文：\n{figure_context or '无'}"
        )
    if action == "ask":
        user_question = str(question or "").strip()
        if not user_question:
            raise HTTPException(status_code=400, detail="question is required for figure ask")
        return (
            "你是论文图表阅读助手。请结合图片内容与附加上下文，用简体中文回答用户问题。\n"
            "要求：\n"
            "1. 以图像证据为主，必要时参考附加上下文。\n"
            "2. 如果图中无法直接判断，请明确指出。\n"
            "3. 回答尽量直接。\n\n"
            f"用户问题：\n{user_question}\n\n"
            f"附加上下文：\n{figure_context or '无'}"
        )
    raise HTTPException(status_code=400, detail="unsupported action")


def _build_figure_reader_text_fallback_prompt(
    action: str,
    *,
    caption: str,
    description: str,
    question: str | None = None,
) -> str:
    figure_context = "\n".join(
        item
        for item in (
            f"题注：{caption[:1000]}" if caption else "",
            f"已有解析：{description[:3000]}" if description else "",
        )
        if item
    ).strip()
    if _is_reader_analysis_action(action):
        return (
            "你是论文图表阅读助手。当前视觉模型不可用，只能基于图表题注与已有解析，用简体中文分析该图表。\n"
            "要求：\n"
            "1. 明确说明这是基于题注和已有解析的文本分析。\n"
            "2. 优先提炼图表表达的核心趋势、对比关系、结构含义或结论。\n"
            "3. 如果信息不足，要明确指出。\n\n"
            f"图表文本上下文：\n{figure_context or '无'}"
        )
    if action == "ask":
        user_question = str(question or "").strip()
        if not user_question:
            raise HTTPException(status_code=400, detail="question is required for figure ask")
        return (
            "你是论文图表阅读助手。当前视觉模型不可用，只能基于图表题注与已有解析回答用户问题，输出必须为简体中文。\n"
            "要求：\n"
            "1. 明确说明回答依据仅限题注与已有解析。\n"
            "2. 如果文本上下文不足以回答，请明确指出。\n"
            "3. 回答尽量直接。\n\n"
            f"用户问题：\n{user_question}\n\n"
            f"图表文本上下文：\n{figure_context or '无'}"
        )
    raise HTTPException(status_code=400, detail="unsupported action")


def _should_fallback_reader_figure_to_text(result_text: str | None) -> bool:
    text = str(result_text or "").strip()
    if not text:
        return True
    lowered = text.lower()
    markers = (
        "当前视觉模型不可用",
        "图像分析服务暂时不可用",
        "当前未配置可用的图像分析模型",
        "未配置图像分析模型",
    )
    return (
        any(marker in text for marker in markers)
        or "blocked" in lowered
        or "bad gateway" in lowered
    )


def _get_reader_figure_row(session, paper_id: UUID, figure_id: str) -> ImageAnalysis | None:  # noqa: ANN001
    from sqlalchemy import select

    return session.execute(
        select(ImageAnalysis).where(
            ImageAnalysis.id == str(figure_id),
            ImageAnalysis.paper_id == str(paper_id),
        )
    ).scalar_one_or_none()


class PaperTopicAssignReq(BaseModel):
    topic_id: str


class PaperSourceUpdateReq(BaseModel):
    source_url: str | None = None
    pdf_url: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None


def _cleanup_pdf_files(paths: list[str]) -> int:
    removed = 0
    for raw in sorted(set(p for p in paths if p)):
        try:
            p = Path(raw)
            if p.exists() and p.is_file():
                p.unlink()
                removed += 1
        except Exception:
            # Ignore file cleanup errors to avoid masking DB success.
            pass
    return removed


def _normalize_text_input(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or ""


def _delete_pdf_file(path: str | None) -> None:
    if not path:
        return
    try:
        target = Path(path)
        if target.exists() and target.is_file():
            target.unlink()
    except Exception:
        pass


def _looks_like_chinese(text: str | None) -> bool:
    return bool(text and re.search(r"[\u4e00-\u9fff]", text))


def _normalize_keyword_list(keywords: list[str] | None) -> list[str]:
    return PaperRepository._normalize_keywords(keywords)


def _metadata_citation_count(metadata: dict | None) -> int | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("citationCount", "citation_count"):
        raw = metadata.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _translate_paper_metadata(title: str, abstract: str) -> tuple[str, str]:
    clean_title = str(title or "").strip()
    clean_abstract = str(abstract or "").strip()
    title_zh = clean_title if _looks_like_chinese(clean_title) else ""
    abstract_zh = clean_abstract if _looks_like_chinese(clean_abstract) else ""

    needs_title = bool(clean_title and not title_zh)
    needs_abstract = bool(clean_abstract and not abstract_zh)
    if not needs_title and not needs_abstract:
        return title_zh, abstract_zh

    prompt = (
        "请将下面论文标题和摘要翻译成简体中文，只输出单个 JSON 对象，格式为："
        '{"title_zh":"...","abstract_zh":"..."}。\n'
        "要求：\n"
        "1. 保留模型名、数据集名、公式缩写和专有名词。\n"
        "2. 标题译文简洁准确，摘要译文完整自然。\n"
        "3. 没有内容时返回空字符串。\n\n"
        f"标题：{clean_title[:800]}\n\n"
        f"摘要：{clean_abstract[:6000]}"
    )
    result = LLMClient().complete_json(
        prompt,
        stage="paper_metadata_translate",
        max_tokens=2200,
        max_retries=1,
    )
    parsed = result.parsed_json or {}
    title_zh = str(parsed.get("title_zh", "") or "").strip() or title_zh
    abstract_zh = str(parsed.get("abstract_zh", "") or "").strip() or abstract_zh
    return title_zh[:500], abstract_zh[:3000]


def _resolve_stored_pdf_path(pdf_path: str | None) -> Path | None:
    if not pdf_path:
        return None

    raw = Path(str(pdf_path)).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        project_root = Path(__file__).resolve().parents[3]
        candidates.append(raw)
        candidates.append(Path.cwd() / raw)
        candidates.append(project_root / raw)

        pdf_root = Path(get_settings().pdf_storage_root).expanduser()
        if pdf_root.is_absolute():
            candidates.append(pdf_root.parent.parent / raw)
            candidates.append(pdf_root.parent / raw)
            candidates.append(pdf_root / raw)
            candidates.append(pdf_root / raw.name)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate.resolve()

    if raw.is_absolute():
        return raw.resolve(strict=False)
    return (Path(__file__).resolve().parents[3] / raw).resolve(strict=False)


def _ensure_paper_pdf(session, repo: PaperRepository, paper, paper_id: UUID) -> str:
    resolved_local_pdf = _resolve_stored_pdf_path(getattr(paper, "pdf_path", None))
    if resolved_local_pdf and resolved_local_pdf.exists():
        return str(resolved_local_pdf)
    try:
        return _ensure_paper_pdf_impl(session, repo, paper, paper_id)
    except PaperPdfUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/papers/folder-stats")
def paper_folder_stats() -> dict:
    """Return folder statistics for the paper library."""
    cached = cache.get("folder_stats")
    if cached is not None:
        return cached
    with session_scope() as session:
        repo = _paper_data(session).papers
        result = repo.folder_stats()
    cache.set("folder_stats", result, ttl=30)
    return result


@router.post("/papers/auto-classify")
def auto_classify_papers(body: PaperAutoClassifyReq) -> dict:
    """Auto-classify papers into folders by keyword and graph signals."""
    from packages.ai.paper.classification_service import PaperClassificationService

    result = PaperClassificationService().auto_classify(body)
    if result.get("linked_topics", 0) > 0 and not body.dry_run:
        cache.invalidate("folder_stats")
        cache.invalidate_prefix("graph_")
        cache.invalidate_prefix("dashboard_home_")
    return result


@router.post("/papers/batch-delete")
def batch_delete_papers(body: PaperBatchDeleteReq) -> dict:
    requested_ids = list(dict.fromkeys(str(pid) for pid in body.paper_ids if str(pid).strip()))
    if not requested_ids:
        return {
            "requested": 0,
            "deleted": 0,
            "deleted_ids": [],
            "missing_ids": [],
            "removed_pdf_files": 0,
        }

    with session_scope() as session:
        repo = _paper_data(session).papers
        deleted_ids, pdf_paths = repo.delete_by_ids(requested_ids)

    deleted_set = set(deleted_ids)
    missing_ids = [pid for pid in requested_ids if pid not in deleted_set]
    removed_files = _cleanup_pdf_files(pdf_paths) if body.delete_pdf_files else 0

    cache.invalidate("folder_stats")
    cache.invalidate_prefix("graph_")
    cache.invalidate_prefix("dashboard_home_")
    return {
        "requested": len(requested_ids),
        "deleted": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "missing_ids": missing_ids,
        "removed_pdf_files": removed_files,
    }


@router.post("/papers/upload-pdf")
async def upload_paper_pdf(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    arxiv_id: str = Form(default=""),
    topic_id: str = Form(default=""),
) -> dict:
    try:
        result = await asyncio.to_thread(
            _upload_paper_pdf_impl,
            file_obj=file.file,
            filename=file.filename or "paper.pdf",
            content_type=file.content_type,
            title=title,
            arxiv_id=arxiv_id,
            topic_id=topic_id,
        )
        cache.invalidate("folder_stats")
        cache.invalidate_prefix("graph_")
        cache.invalidate_prefix("dashboard_home_")
        return result
    except PaperUploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PaperUploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF 上传失败: {exc}") from exc
    finally:
        await file.close()


@router.patch("/papers/{paper_id}/source")
def update_paper_source(paper_id: UUID, body: PaperSourceUpdateReq) -> dict:
    old_pdf_path: str | None = None
    local_pdf_cleared = False

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        metadata = dict(paper.metadata_json or {})
        old_pdf_path = paper.pdf_path

        source_url = _normalize_text_input(body.source_url)
        pdf_url = _normalize_text_input(body.pdf_url)
        doi = _normalize_text_input(body.doi)
        raw_arxiv_id = _normalize_text_input(body.arxiv_id)
        normalized_arxiv_id = _normalize_manual_paper_id(raw_arxiv_id) if raw_arxiv_id else ""

        changed = False

        if body.source_url is not None:
            if source_url:
                if metadata.get("source_url") != source_url:
                    metadata["source_url"] = source_url
                    changed = True
                if "openalex.org" in source_url.lower():
                    metadata["import_source"] = "openalex"
                    metadata["openalex_id"] = source_url
                elif "semanticscholar.org" in source_url.lower():
                    metadata["import_source"] = "semantic_scholar"
            elif "source_url" in metadata:
                metadata.pop("source_url", None)
                changed = True

        if body.pdf_url is not None:
            if pdf_url:
                if metadata.get("pdf_url") != pdf_url:
                    metadata["pdf_url"] = pdf_url
                    changed = True
                if old_pdf_path:
                    paper.pdf_path = None
                    local_pdf_cleared = True
            else:
                if "pdf_url" in metadata:
                    metadata.pop("pdf_url", None)
                    changed = True
                if old_pdf_path:
                    paper.pdf_path = None
                    local_pdf_cleared = True

        if body.doi is not None:
            if doi:
                if metadata.get("doi") != doi:
                    metadata["doi"] = doi
                    changed = True
            elif "doi" in metadata:
                metadata.pop("doi", None)
                changed = True

        if body.source_url is not None and source_url:
            source_lower = source_url.lower()
            if (not pdf_url) and (source_lower.endswith(".pdf") or "/pdf/" in source_lower):
                if metadata.get("pdf_url") != source_url:
                    metadata["pdf_url"] = source_url
                    changed = True
                if old_pdf_path:
                    paper.pdf_path = None
                    local_pdf_cleared = True
            if "arxiv.org/" in source_lower and normalized_arxiv_id:
                if paper.arxiv_id != normalized_arxiv_id:
                    paper.arxiv_id = normalized_arxiv_id
                    changed = True
                if old_pdf_path:
                    paper.pdf_path = None
                    local_pdf_cleared = True

        if body.arxiv_id is not None and normalized_arxiv_id:
            if paper.arxiv_id != normalized_arxiv_id:
                paper.arxiv_id = normalized_arxiv_id
                changed = True
            metadata["arxiv_id"] = normalized_arxiv_id
            if old_pdf_path:
                paper.pdf_path = None
                local_pdf_cleared = True

        if local_pdf_cleared:
            metadata = _clear_pdf_derived_metadata(metadata)

        if changed or metadata != (paper.metadata_json or {}):
            paper.metadata_json = metadata

        session.flush()

        result = {
            "status": "updated",
            "paper_id": str(paper.id),
            "local_pdf_cleared": local_pdf_cleared,
            "metadata": paper.metadata_json,
            "arxiv_id": paper.arxiv_id,
            "pdf_path": paper.pdf_path,
        }

    if local_pdf_cleared:
        _delete_pdf_file(old_pdf_path)

    cache.invalidate("folder_stats")
    cache.invalidate_prefix("graph_")
    cache.invalidate_prefix("dashboard_home_")
    return result


@router.post("/papers/{paper_id}/upload-pdf")
async def replace_existing_paper_pdf(
    paper_id: UUID,
    file: UploadFile = File(...),
) -> dict:
    try:
        result = await asyncio.to_thread(
            _replace_paper_pdf_impl,
            paper_id=paper_id,
            file_obj=file.file,
            filename=file.filename or "paper.pdf",
            content_type=file.content_type,
        )
        cache.invalidate("folder_stats")
        cache.invalidate_prefix("graph_")
        cache.invalidate_prefix("dashboard_home_")
        return result
    except PaperUploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PaperUploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to upload PDF: {exc}") from exc
    finally:
        await file.close()


@router.get("/papers/latest")
def latest(
    limit: int = Query(default=50, ge=1, le=500),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    topic_id: str | None = Query(default=None),
    folder: str | None = Query(default=None),
    date: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    search: str | None = Query(default=None),
    keywords: list[str] = Query(default=[]),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        papers, total = repo.list_paginated(
            page=page,
            page_size=page_size,
            folder=folder,
            topic_id=topic_id,
            status=status,
            date_str=date,
            date_from=date_from,
            date_to=date_to,
            search=search.strip() if search else None,
            keywords=keywords,
            sort_by=sort_by
            if sort_by in ("created_at", "publication_date", "title", "impact")
            else "created_at",
            sort_order=sort_order if sort_order in ("asc", "desc") else "desc",
        )
        resp = paper_list_response(papers, repo)
        resp["total"] = total
        resp["page"] = page
        resp["page_size"] = page_size
        resp["total_pages"] = max(1, (total + page_size - 1) // page_size)
        return resp


@router.get("/papers/keywords")
def paper_keywords(
    status: str | None = Query(default=None),
    topic_id: str | None = Query(default=None),
    folder: str | None = Query(default=None),
    date: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        items = repo.keyword_facets(
            folder=folder,
            topic_id=topic_id,
            status=status,
            date_str=date,
            date_from=date_from,
            date_to=date_to,
            search=search.strip() if search else None,
            limit=limit,
        )
        return {"items": items}


@router.delete("/papers/{paper_id}")
def delete_paper(paper_id: UUID, delete_pdf: bool = Query(default=True)) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        deleted_ids, pdf_paths = repo.delete_by_ids([str(paper_id)])

    if not deleted_ids:
        raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")

    removed_files = _cleanup_pdf_files(pdf_paths) if delete_pdf else 0
    cache.invalidate("folder_stats")
    cache.invalidate_prefix("graph_")
    cache.invalidate_prefix("dashboard_home_")
    return {
        "deleted": deleted_ids[0],
        "removed_pdf_files": removed_files,
    }


@router.get("/papers/recommended")
def recommended_papers(top_k: int = Query(default=10, ge=1, le=50)) -> dict:
    from packages.ai.research.recommendation_service import RecommendationService

    return {"items": RecommendationService().recommend(top_k=top_k)}


@router.get("/papers/proxy-arxiv-pdf/{arxiv_id:path}")
async def proxy_arxiv_pdf(arxiv_id: str):
    """Proxy arXiv PDF requests to avoid browser CORS issues."""
    import httpx
    from fastapi.responses import Response

    clean_id = re.sub(r"v\d+$", "", arxiv_id.strip())
    arxiv_url = f"https://arxiv.org/pdf/{clean_id}.pdf"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(arxiv_url, follow_redirects=True)

        if response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"arXiv PDF not found: {clean_id}")
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"arXiv returned unexpected status: {response.status_code}",
            )

        return Response(
            content=response.content,
            media_type="application/pdf",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Content-Disposition": f'inline; filename="{clean_id}.pdf"',
                "Cache-Control": "public, max-age=3600",
            },
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="arXiv request timed out") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"arXiv request failed: {exc}") from exc


@router.get("/papers/{paper_id}")
def paper_detail(paper_id: UUID) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            p = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not _has_real_arxiv_id(p.arxiv_id) or not (p.metadata_json or {}).get("pdf_url"):
            resolved = _resolve_external_pdf_source(p)
            _apply_external_resolution(p, resolved)
            session.flush()
        assigned_topics = repo.get_topics_for_paper(str(p.id), kind="folder")
        topic_map = repo.get_topic_names_for_papers([str(p.id)], kind="folder")
        # 鏌ヨ宸叉湁鍒嗘瀽鎶ュ憡
        from sqlalchemy import select as _sel

        from packages.storage.models import AnalysisReport as AR

        ar = session.execute(_sel(AR).where(AR.paper_id == str(p.id))).scalar_one_or_none()
        skim_data = None
        deep_data = None
        if ar:
            if ar.summary_md:
                skim_data = {
                    "summary_md": ar.summary_md,
                    "skim_score": ar.skim_score,
                    "key_insights": ar.key_insights or {},
                }
            if ar.deep_dive_md:
                deep_data = {
                    "deep_dive_md": ar.deep_dive_md,
                    "key_insights": ar.key_insights or {},
                }
        return {
            "id": str(p.id),
            "title": p.title,
            "arxiv_id": p.arxiv_id,
            "abstract": p.abstract,
            "publication_date": str(p.publication_date) if p.publication_date else None,
            "read_status": p.read_status.value,
            "pdf_path": p.pdf_path,
            "favorited": getattr(p, "favorited", False),
            "categories": (p.metadata_json or {}).get("categories", []),
            "authors": (p.metadata_json or {}).get("authors", []),
            "keywords": (p.metadata_json or {}).get("keywords", []),
            "title_zh": (p.metadata_json or {}).get("title_zh", ""),
            "abstract_zh": (p.metadata_json or {}).get("abstract_zh", ""),
            "citation_count": _metadata_citation_count(p.metadata_json or {}),
            "topics": topic_map.get(str(p.id), []),
            "topic_details": [
                {
                    "id": topic.id,
                    "name": topic.name,
                    "kind": getattr(topic, "kind", "subscription"),
                    "query": topic.query,
                    "enabled": topic.enabled,
                }
                for topic in assigned_topics
            ],
            "metadata": p.metadata_json,
            "has_embedding": p.embedding is not None,
            "skim_report": skim_data,
            "deep_report": deep_data,
            "analysis_rounds": (p.metadata_json or {}).get("analysis_rounds"),
        }


@router.patch("/papers/{paper_id}/metadata")
def update_paper_metadata(paper_id: UUID, body: PaperMetadataUpdateReq) -> dict:
    title_input = _normalize_text_input(body.title)
    abstract_input = _normalize_text_input(body.abstract)
    title_zh_input = _normalize_text_input(body.title_zh)
    abstract_zh_input = _normalize_text_input(body.abstract_zh)
    keywords_input = _normalize_keyword_list(body.keywords) if body.keywords is not None else None

    if body.title is not None and not title_input:
        raise HTTPException(status_code=400, detail="title cannot be empty")

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        resolved_title = title_input if title_input is not None else paper.title
        resolved_abstract = abstract_input if abstract_input is not None else paper.abstract

    translated_title_zh = ""
    translated_abstract_zh = ""
    should_auto_translate = body.auto_translate and (
        (body.title is not None and body.title_zh is None)
        or (body.abstract is not None and body.abstract_zh is None)
    )
    if should_auto_translate:
        try:
            translated_title_zh, translated_abstract_zh = _translate_paper_metadata(
                resolved_title,
                resolved_abstract,
            )
        except Exception:
            translated_title_zh, translated_abstract_zh = "", ""

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        metadata = dict(paper.metadata_json or {})
        updated_analysis = False

        if title_input is not None:
            paper.title = title_input
        if abstract_input is not None:
            paper.abstract = abstract_input
        if keywords_input is not None:
            metadata["keywords"] = keywords_input
            updated_analysis = True
        if body.title_zh is not None:
            metadata["title_zh"] = title_zh_input or ""
            updated_analysis = True
        elif translated_title_zh:
            metadata["title_zh"] = translated_title_zh
            updated_analysis = True
        if body.abstract_zh is not None:
            metadata["abstract_zh"] = abstract_zh_input or ""
            updated_analysis = True
        elif translated_abstract_zh:
            metadata["abstract_zh"] = translated_abstract_zh
            updated_analysis = True
        paper.metadata_json = metadata

        if updated_analysis:
            from sqlalchemy import select as _select

            from packages.storage.models import AnalysisReport

            report = session.execute(
                _select(AnalysisReport).where(AnalysisReport.paper_id == str(paper.id))
            ).scalar_one_or_none()
            if report:
                key_insights = dict(report.key_insights or {})
                if keywords_input is not None:
                    key_insights["keywords"] = keywords_input
                if "title_zh" in metadata:
                    key_insights["title_zh"] = metadata.get("title_zh", "")
                if "abstract_zh" in metadata:
                    key_insights["abstract_zh"] = metadata.get("abstract_zh", "")
                report.key_insights = key_insights

        session.flush()

    cache.invalidate("folder_stats")
    cache.invalidate("today_summary")
    cache.invalidate_prefix("graph_")
    cache.invalidate_prefix("dashboard_home_")
    return paper_detail(paper_id)


@router.get("/papers/{paper_id}/topics")
def list_paper_topics(paper_id: UUID) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        items = repo.get_topics_for_paper(str(paper_id), kind="folder")
        return {
            "items": [
                {
                    "id": topic.id,
                    "name": topic.name,
                    "kind": getattr(topic, "kind", "subscription"),
                    "query": topic.query,
                    "enabled": topic.enabled,
                }
                for topic in items
            ]
        }


@router.post("/papers/{paper_id}/topics")
def assign_paper_topic(paper_id: UUID, body: PaperTopicAssignReq) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        from packages.storage.models import TopicSubscription

        topic = session.get(TopicSubscription, body.topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"topic {body.topic_id} not found")
        if getattr(topic, "kind", "subscription") != "folder":
            raise HTTPException(
                status_code=400, detail="Only folder topics can be linked to papers"
            )

        repo.link_to_topic(str(paper_id), body.topic_id)

    cache.invalidate("folder_stats")
    cache.invalidate_prefix("graph_")
    cache.invalidate_prefix("dashboard_home_")
    return {"paper_id": str(paper_id), "topic_id": body.topic_id, "status": "linked"}


@router.delete("/papers/{paper_id}/topics/{topic_id}")
def remove_paper_topic(paper_id: UUID, topic_id: str) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        removed = repo.unlink_from_topic(str(paper_id), topic_id)

    cache.invalidate("folder_stats")
    cache.invalidate_prefix("graph_")
    cache.invalidate_prefix("dashboard_home_")
    return {"paper_id": str(paper_id), "topic_id": topic_id, "removed": removed}


@router.patch("/papers/{paper_id}/favorite")
def toggle_favorite(paper_id: UUID) -> dict:
    """Toggle favorite state for a paper."""
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            p = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        current = getattr(p, "favorited", False)
        p.favorited = not current
        session.commit()
        cache.invalidate("folder_stats")
        cache.invalidate_prefix("dashboard_home_")
        return {"id": str(p.id), "favorited": p.favorited}


# ---------- PDF 鏈嶅姟 ----------


@router.post("/papers/{paper_id}/download-pdf")
def download_paper_pdf(paper_id: UUID) -> dict:
    """Download a paper PDF from arXiv or an external open-access source."""

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        existing = bool(_resolve_stored_pdf_path(getattr(paper, "pdf_path", None)))
        try:
            pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)
            return {"status": "exists" if existing else "downloaded", "pdf_path": pdf_path}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to prepare PDF: {exc}") from exc


@router.post("/papers/{paper_id}/download-pdf-async")
def download_paper_pdf_async(paper_id: UUID) -> dict:
    """Prepare a paper PDF in background and return task id."""

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        title = str(getattr(paper, "title", "") or "").strip()

    task_title = (
        f"下载 PDF: {(title[:36] + '...') if len(title) > 36 else title}"
        if title
        else f"下载 PDF: {str(paper_id)[:8]}"
    )

    def _run_task(progress_callback: Callable[[str, int, int], None] | None = None) -> dict:
        def _progress(message: str, current: int, total: int = 100) -> None:
            if progress_callback is not None:
                progress_callback(message, current, total)

        _progress("检查本地 PDF...", 5, 100)
        with session_scope() as session:
            repo = _paper_data(session).papers
            try:
                paper = repo.get_by_id(paper_id)
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc

            existing_path = _resolve_stored_pdf_path(getattr(paper, "pdf_path", None))
            if existing_path and existing_path.exists():
                _progress("本地 PDF 已存在", 100, 100)
                return {"status": "exists", "pdf_path": str(existing_path)}

            _progress("解析论文来源...", 20, 100)
            _progress("准备并下载 PDF...", 55, 100)
            try:
                pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)
            except HTTPException as exc:
                raise RuntimeError(str(exc.detail or exc)) from exc
            except Exception as exc:
                raise RuntimeError(f"Failed to prepare PDF: {exc}") from exc

        _progress("PDF 已就绪", 100, 100)
        return {"status": "downloaded", "pdf_path": pdf_path}

    task_id = global_tracker.submit(
        task_type="paper_pdf",
        title=task_title,
        fn=_run_task,
        total=100,
        metadata={"source": "paper", "source_id": str(paper_id), "paper_id": str(paper_id)},
    )
    return {"task_id": task_id, "status": "running", "message": "PDF 下载任务已启动"}


@router.get("/papers/{paper_id}/pdf")
def serve_paper_pdf(paper_id: UUID) -> FileResponse:
    """Serve a paper PDF from local storage, arXiv, or an external open-access source."""

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to prepare PDF: {exc}") from exc

    full_path = _resolve_stored_pdf_path(pdf_path) or Path(pdf_path)
    if full_path.exists():
        return FileResponse(
            path=str(full_path),
            media_type="application/pdf",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    raise HTTPException(status_code=404, detail="Prepared PDF file is missing on disk")


@router.post("/papers/{paper_id}/ai/explain")
def ai_explain_text(paper_id: UUID, body: AIExplainReq) -> dict:
    """Explain, translate, summarize, or answer questions about selected paper text."""
    text = body.text.strip()
    action = (body.action or "analyze").strip().lower()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if action not in {"analyze", "explain", "translate", "summarize", "ask"}:
        raise HTTPException(status_code=400, detail="unsupported action")
    prompt = _build_pdf_reader_ai_prompt(action, text, question=body.question)

    from packages.integrations.llm_client import LLMClient

    llm = LLMClient()
    result = llm.summarize_text(prompt, stage="rag", max_tokens=1024)
    llm.trace_result(
        result, stage="pdf_reader_ai", prompt_digest=f"{action}:{text[:80]}", paper_id=str(paper_id)
    )
    return {"action": action, "result": result.content}


@router.post("/papers/{paper_id}/reader/query")
def paper_reader_query(paper_id: UUID, body: PaperReaderQueryReq) -> dict:
    scope = str(body.scope or "selection").strip().lower()
    action = str(body.action or "analyze").strip().lower()
    if scope not in {"paper", "selection", "figure"}:
        raise HTTPException(status_code=400, detail="unsupported scope")
    if action not in {"analyze", "explain", "translate", "summarize", "ask"}:
        raise HTTPException(status_code=400, detail="unsupported action")

    if scope == "selection":
        text = str(body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required for selection query")
        prompt = _build_pdf_reader_ai_prompt(action, text, question=body.question)
        llm = LLMClient()
        result = llm.summarize_text(prompt, stage="paper_reader_selection", max_tokens=1200)
        llm.trace_result(
            result,
            stage="paper_reader_selection",
            prompt_digest=f"{action}:{text[:120]}",
            paper_id=str(paper_id),
        )
        return {
            "scope": scope,
            "action": action,
            "result": result.content,
            "text": text[:500],
            "page_number": body.page_number,
        }

    if scope == "paper":
        if action == "translate":
            raise HTTPException(status_code=400, detail="paper scope does not support translate")
        with session_scope() as session:
            repo = _paper_data(session).papers
            try:
                paper = repo.get_by_id(paper_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            context = _build_reader_paper_context(session, repo, paper, paper_id)
        if not context:
            raise HTTPException(status_code=400, detail="paper context is empty")
        prompt = _build_full_paper_reader_prompt(action, context, question=body.question)
        llm = LLMClient()
        result = llm.summarize_text(prompt, stage="paper_reader_paper", max_tokens=1600)
        llm.trace_result(
            result,
            stage="paper_reader_paper",
            prompt_digest=f"{action}:{str(body.question or '')[:120]}",
            paper_id=str(paper_id),
        )
        return {
            "scope": scope,
            "action": action,
            "result": result.content,
        }

    if action == "translate":
        raise HTTPException(status_code=400, detail="figure scope does not support translate")

    raw_image_base64 = str(body.image_base64 or "").strip()
    image_base64 = raw_image_base64
    if image_base64.startswith("data:") and "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1].strip()

    figure_id = str(body.figure_id or "").strip() or None
    page_number = body.page_number
    caption: str | None = None
    description = ""

    if image_base64:
        caption = f"第 {page_number} 页框选区域" if page_number else "框选区域"
        prompt = _build_figure_reader_prompt(
            action,
            caption=caption,
            description="",
            question=body.question,
        )
    else:
        if not figure_id:
            raise HTTPException(
                status_code=400, detail="figure_id or image_base64 is required for figure query"
            )

        with session_scope() as session:
            row = _get_reader_figure_row(session, paper_id, figure_id)
            if row is None:
                raise HTTPException(status_code=404, detail="figure not found")
            from packages.ai.paper.figure_service import FigureService

            image_path = FigureService.resolve_stored_image_path(row.image_path)
            if image_path is None or not image_path.exists():
                raise HTTPException(status_code=404, detail="figure image is unavailable")
            page_number = int(row.page_number or 0) or None
            caption = str(row.caption or "").strip() or None
            description = str(row.description or "").strip()
            prompt = _build_figure_reader_prompt(
                action,
                caption=str(caption or ""),
                description=description,
                question=body.question,
            )
            image_base64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")

    llm = LLMClient()
    result = llm.vision_analyze(
        image_base64=image_base64,
        prompt=prompt,
        stage="paper_reader_figure",
        max_tokens=1400,
    )
    llm.trace_result(
        result,
        stage="paper_reader_figure",
        prompt_digest=f"{action}:{figure_id or 'region'}:{str(body.question or '')[:120]}",
        paper_id=str(paper_id),
    )
    if (
        figure_id
        and (caption or description)
        and _should_fallback_reader_figure_to_text(result.content)
    ):
        fallback_prompt = _build_figure_reader_text_fallback_prompt(
            action,
            caption=str(caption or ""),
            description=description,
            question=body.question,
        )
        fallback = llm.summarize_text(
            fallback_prompt,
            stage="paper_reader_figure_fallback",
            max_tokens=1000,
        )
        llm.trace_result(
            fallback,
            stage="paper_reader_figure_fallback",
            prompt_digest=f"{action}:{figure_id}:text-fallback:{str(body.question or '')[:120]}",
            paper_id=str(paper_id),
        )
        return {
            "scope": scope,
            "action": action,
            "result": fallback.content,
            "figure_id": figure_id,
            "page_number": page_number,
            "caption": caption,
        }
    return {
        "scope": scope,
        "action": action,
        "result": result.content,
        "figure_id": figure_id,
        "page_number": page_number,
        "caption": caption,
    }


@router.get("/papers/{paper_id}/reader/document")
def get_paper_reader_document(paper_id: UUID) -> dict | PaperReaderDocumentResp:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            bundle = _load_reader_document_bundle(session, repo, paper, paper_id)
        except Exception as exc:
            logger.warning("Reader document unavailable for %s: %s", paper_id, exc)
            return PaperReaderDocumentResp(paper_id=str(paper_id))
    return _build_reader_document_payload(paper_id, bundle)


@router.get("/papers/{paper_id}/reader/notes")
def list_paper_reader_notes(paper_id: UUID) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"items": _reader_notes_from_metadata(paper.metadata_json or {})}


@router.put("/papers/{paper_id}/reader/notes")
def save_paper_reader_note(paper_id: UUID, body: PaperReaderNoteReq) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        metadata = dict(paper.metadata_json or {})
        notes = _reader_notes_from_metadata(metadata)
        note_id = str(body.id or "").strip() or f"note_{uuid4().hex[:12]}"
        existing = next((item for item in notes if str(item.get("id")) == note_id), None)
        created_at = str((existing or {}).get("created_at") or "").strip() or _utc_iso()
        content = _clean_reader_text(body.content, max_len=12000)
        quote = _clean_reader_text(body.quote, max_len=2500)
        title = _clean_reader_text(body.title, max_len=120)
        if not title and not content and not quote:
            raise HTTPException(status_code=400, detail="note is empty")
        note = _normalize_reader_note_dict(
            {
                "id": note_id,
                "kind": body.kind,
                "title": title or (quote or content or "未命名笔记")[:60],
                "content": content,
                "quote": quote,
                "page_number": body.page_number,
                "figure_id": body.figure_id,
                "color": body.color,
                "tags": body.tags,
                "pinned": body.pinned,
                "status": "saved",
                "source": (body.source or (existing or {}).get("source") or "manual"),
                "anchor_source": body.anchor_source
                if body.anchor_source is not None
                else (existing or {}).get("anchor_source"),
                "anchor_id": body.anchor_id
                if body.anchor_id is not None
                else (existing or {}).get("anchor_id"),
                "section_id": body.section_id
                if body.section_id is not None
                else (existing or {}).get("section_id"),
                "section_title": body.section_title
                if body.section_title is not None
                else (existing or {}).get("section_title"),
                "created_at": created_at,
                "updated_at": _utc_iso(),
            }
        )
        assert note is not None
        notes = [item for item in notes if str(item.get("id")) != note_id]
        notes.append(note)
        metadata["reader_notes"] = _sort_reader_notes(notes)
        paper.metadata_json = metadata
        session.flush()
        return {"item": note, "items": metadata["reader_notes"]}


@router.delete("/papers/{paper_id}/reader/notes/{note_id}")
def delete_paper_reader_note(paper_id: UUID, note_id: str) -> dict:
    target_id = str(note_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="note_id is required")

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        metadata = dict(paper.metadata_json or {})
        notes = _reader_notes_from_metadata(metadata)
        next_notes = [item for item in notes if str(item.get("id")) != target_id]
        if len(next_notes) == len(notes):
            raise HTTPException(status_code=404, detail="reader note not found")
        metadata["reader_notes"] = _sort_reader_notes(next_notes)
        paper.metadata_json = metadata
        session.flush()
        return {"deleted": target_id, "items": metadata["reader_notes"]}


@router.post("/papers/{paper_id}/reader/note-draft")
def generate_paper_reader_note_draft(paper_id: UUID, body: PaperReaderNoteDraftReq) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        title = str(getattr(paper, "title", "") or "").strip()

    text = _clean_reader_text(body.text, max_len=5000)
    quote = _clean_reader_text(body.quote or body.text, max_len=2500)
    if not text and not quote:
        raise HTTPException(status_code=400, detail="text is required")

    llm = LLMClient()
    result = llm.complete_json(
        _build_reader_note_draft_prompt(
            title=title,
            section_title=body.section_title,
            text=text,
            quote=quote,
            page_number=body.page_number,
        ),
        stage="paper_reader_note_draft",
        max_tokens=1000,
        max_retries=2,
    )
    parsed: dict[str, Any] = result.parsed_json or {}
    if not parsed:
        for candidate in (result.content, result.reasoning_content or ""):
            reparsed = LLMClient._try_parse_json(candidate)
            if isinstance(reparsed, dict) and reparsed:
                parsed = reparsed
                break

    structured_text = _parse_reader_note_text_payload(result.content)
    if not structured_text.get("content") and result.reasoning_content:
        reasoning_text = _parse_reader_note_text_payload(result.reasoning_content)
        if reasoning_text.get("content"):
            structured_text = reasoning_text

    content_value = parsed.get("content")
    if isinstance(content_value, list):
        content_lines = [_clean_reader_text(item, max_len=500) for item in content_value]
        content = "\n".join(f"- {line}" for line in content_lines if line)
    else:
        content = _normalize_reader_note_markdown(str(content_value or ""))

    title_value = (
        _clean_reader_text(parsed.get("title"), max_len=120)
        or str(structured_text.get("title") or "").strip()
    )
    tags_value = (
        parsed.get("tags") if isinstance(parsed.get("tags"), list) else structured_text.get("tags")
    )
    color_value = parsed.get("color") or structured_text.get("color")
    provider_error_message = (
        result.content if LLMClient._is_provider_error_text(result.content) else ""
    )

    needs_fallback = not content or _looks_like_reader_note_placeholder(content, quote or text)
    if needs_fallback:
        logger.info(
            "reader note draft: structured JSON unavailable, fallback summarize_text (paper_id=%s, page=%s)",
            paper_id,
            body.page_number,
        )
        fallback = llm.summarize_text(
            _build_reader_note_draft_fallback_prompt(
                title=title,
                section_title=body.section_title,
                text=text,
                quote=quote,
                page_number=body.page_number,
            ),
            stage="paper_reader_note_draft_fallback",
            max_tokens=700,
        )
        if LLMClient._is_provider_error_text(fallback.content):
            provider_error_message = fallback.content
            fallback_text = {}
        else:
            fallback_text = _parse_reader_note_text_payload(fallback.content)
        title_value = title_value or str(fallback_text.get("title") or "").strip()
        content = str(fallback_text.get("content") or "").strip() or content
        if not tags_value:
            tags_value = fallback_text.get("tags")
        if not color_value:
            color_value = fallback_text.get("color")

    if (
        not content or _looks_like_reader_note_placeholder(content, quote or text)
    ) and provider_error_message:
        raise HTTPException(status_code=503, detail=provider_error_message)

    final_title = _derive_reader_note_title(
        title=title_value,
        quote=quote,
        text=text,
        section_title=body.section_title,
    )
    final_content = content or _build_reader_note_deterministic_fallback(
        quote=quote,
        text=text,
        section_title=body.section_title,
        page_number=body.page_number,
    )
    final_tags = _normalize_reader_tags(tags_value if isinstance(tags_value, list) else [])
    if not final_tags:
        final_tags = _derive_reader_note_tags(
            title=final_title,
            content=final_content,
            quote=quote,
            section_title=body.section_title,
        )
    final_color = str(color_value or "").strip().lower()
    if final_color not in _READER_NOTE_COLORS:
        final_color = _derive_reader_note_color(content=final_content, tags=final_tags)

    note = _normalize_reader_note_dict(
        {
            "id": f"draft_{uuid4().hex[:12]}",
            "kind": "text",
            "title": final_title,
            "content": final_content,
            "quote": quote,
            "page_number": body.page_number,
            "figure_id": None,
            "color": final_color,
            "tags": final_tags,
            "pinned": False,
            "status": "draft",
            "source": "ai_draft",
            "anchor_source": body.anchor_source,
            "anchor_id": body.anchor_id,
            "section_id": body.section_id,
            "section_title": body.section_title,
            "created_at": _utc_iso(),
            "updated_at": _utc_iso(),
        }
    )
    assert note is not None
    return {"item": note}


# ---------- 鍥捐〃瑙ｈ ----------


@router.get("/papers/{paper_id}/ocr/status")
def get_paper_ocr_status(paper_id: UUID) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        metadata = dict(getattr(paper, "metadata_json", None) or {})
    return {"paper_id": str(paper_id), **_paper_ocr_status_payload(metadata)}


@router.get("/papers/{paper_id}/ocr/assets/{asset_path:path}")
def get_paper_ocr_asset(paper_id: UUID, asset_path: str):
    normalized = str(asset_path or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="asset_path is required")

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        bundle = _load_reader_document_bundle(session, repo, paper, paper_id)

    if bundle is None:
        raise HTTPException(status_code=404, detail="ocr bundle not found")

    candidate = _resolve_reader_ocr_asset_path(bundle, normalized)
    if candidate is None:
        raise HTTPException(status_code=404, detail="ocr asset not found")
    return FileResponse(candidate)


@router.post("/papers/{paper_id}/ocr/process-async")
def process_paper_ocr_async(
    paper_id: UUID,
    force: bool = Query(default=False),
) -> dict:
    from packages.ai.paper.mineru_runtime import MinerUOcrRuntime

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        title = str(getattr(paper, "title", "") or "").strip()
        metadata = dict(getattr(paper, "metadata_json", None) or {})

    previous_status = str(_paper_ocr_status_payload(metadata).get("status") or "").strip().lower()
    effective_force = force or previous_status == "failed"

    task_title = (
        f"OCR 处理: {(title[:36] + '...') if len(title) > 36 else title}"
        if title
        else f"OCR 处理: {str(paper_id)[:8]}"
    )

    def _run_task(progress_callback: Callable[[str, int, int], None] | None = None) -> dict:
        def _progress(message: str, current: int, total: int = 100) -> None:
            if progress_callback:
                progress_callback(message, current, total)

        def _start_pulse(
            message: str, *, start: int = 20, end: int = 88, step: int = 2, interval: float = 1.8
        ):
            if not progress_callback:
                return None, None
            stop_event = threading.Event()

            def _runner():
                current = start
                while not stop_event.wait(interval):
                    _progress(message, current, 100)
                    if current < end:
                        current = min(end, current + step)

            thread = threading.Thread(
                target=_runner, daemon=True, name=f"ocr-progress-{str(paper_id)[:8]}"
            )
            thread.start()
            return stop_event, thread

        _progress("准备 PDF 文件...", 5, 100)
        with session_scope() as session:
            repo = _paper_data(session).papers
            paper = repo.get_by_id(paper_id)
            pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)

        _progress("检查 MinerU API 配置...", 12, 100)
        _progress("启动 MinerU API OCR 处理...", 42, 100)
        stop_event, pulse_thread = _start_pulse(
            "正在调用 MinerU API，生成 Markdown / 图表结构...", start=48, end=86
        )
        try:
            bundle = MinerUOcrRuntime.ensure_bundle(paper_id, pdf_path, force=effective_force)
        finally:
            if stop_event is not None:
                stop_event.set()
            if pulse_thread is not None:
                pulse_thread.join(timeout=0.5)

        if bundle is None:
            with session_scope() as session:
                repo = _paper_data(session).papers
                paper = repo.get_by_id(paper_id)
                metadata = dict(getattr(paper, "metadata_json", None) or {})
            status_payload = _paper_ocr_status_payload(metadata)
            raise RuntimeError(status_payload.get("error") or "MinerU OCR 处理失败")

        _progress("读取 OCR 结果...", 92, 100)
        with session_scope() as session:
            repo = _paper_data(session).papers
            paper = repo.get_by_id(paper_id)
            metadata = dict(getattr(paper, "metadata_json", None) or {})
        _progress("OCR 处理完成", 100, 100)
        return {"paper_id": str(paper_id), **_paper_ocr_status_payload(metadata)}

    task_id = global_tracker.submit(
        task_type="paper_ocr",
        title=task_title,
        fn=_run_task,
        total=100,
    )
    return {
        "task_id": task_id,
        "status": "running",
        "message": "OCR 处理任务已启动",
    }


def _extract_paper_figures_payload(
    paper_id: UUID,
    max_figures: int,
    *,
    extract_mode: str | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict:
    _invalidate_paper_figures_cache(paper_id)
    try:
        payload = _extract_paper_figures_payload_impl(
            paper_id=paper_id,
            max_figures=max_figures,
            extract_mode=extract_mode,
            progress_callback=progress_callback,
        )
    except PaperPdfUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FigureExtractionEmptyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    items = _attach_figure_image_urls(paper_id, payload.get("items") or [])
    _cache_paper_figures_items(paper_id, items)
    return {**payload, "items": items}


@router.get("/papers/{paper_id}/figures")
def get_paper_figures(paper_id: UUID) -> dict:
    """Return extracted figure analyses for a paper."""
    cache_key = _cache_key_paper_figures(paper_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from packages.ai.paper.figure_service import FigureService

    items = _attach_figure_image_urls(paper_id, FigureService.get_paper_analyses(paper_id))
    payload = {"items": items}
    cache.set(cache_key, payload, ttl=_PAPER_FIGURES_CACHE_TTL_SEC)
    return payload


@router.post("/papers/{paper_id}/figures/extract")
def extract_paper_figures(
    paper_id: UUID,
    max_figures: int = Query(default=80, ge=1, le=200),
    extract_mode: str | None = Query(
        default=None,
        description="图表提取模式: arxiv_source / mineru",
    ),
) -> dict:
    """Extract all figures/tables from a paper PDF (synchronous)."""
    try:
        return _extract_paper_figures_payload(paper_id, max_figures, extract_mode=extract_mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except RuntimeError as exc:
        detail = str(exc)
        status_code = (
            503 if detail.startswith("Figure extraction dependencies unavailable") else 502
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Figure extraction failed: {exc}") from exc


@router.post("/papers/{paper_id}/figures/extract-async")
def extract_paper_figures_async(
    paper_id: UUID,
    max_figures: int = Query(default=80, ge=1, le=200),
    extract_mode: str | None = Query(
        default=None,
        description="图表提取模式: arxiv_source / mineru",
    ),
) -> dict:
    """Start figure/table extraction in background and return task id."""
    _invalidate_paper_figures_cache(paper_id)

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        title = str(getattr(paper, "title", "") or "").strip()
    task_title = (
        f"图表提取: {(title[:36] + '...') if len(title) > 36 else title}"
        if title
        else f"图表提取: {str(paper_id)[:8]}"
    )

    def _run_task(
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict:
        try:
            return _extract_paper_figures_payload(
                paper_id=paper_id,
                max_figures=max_figures,
                extract_mode=extract_mode,
                progress_callback=progress_callback,
            )
        except HTTPException as exc:
            raise RuntimeError(str(exc.detail or exc)) from exc
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    task_id = global_tracker.submit(
        task_type="figure_extract",
        title=task_title,
        fn=_run_task,
        total=100,
    )
    return {
        "task_id": task_id,
        "status": "running",
        "message": "图表提取任务已启动",
    }


@router.get("/papers/{paper_id}/figures/{figure_id}/image")
def get_figure_image(paper_id: UUID, figure_id: str):
    """Return a stored figure image file."""
    import mimetypes

    from sqlalchemy import select

    from packages.ai.paper.figure_service import FigureService
    from packages.storage.db import session_scope
    from packages.storage.models import ImageAnalysis

    with session_scope() as session:
        row = session.execute(
            select(ImageAnalysis).where(
                ImageAnalysis.id == figure_id,
                ImageAnalysis.paper_id == str(paper_id),
            )
        ).scalar_one_or_none()

        if not row or not row.image_path:
            raise HTTPException(status_code=404, detail="Figure image metadata not found")

        img_path = FigureService.resolve_stored_image_path(row.image_path)
        if img_path is None:
            raise HTTPException(status_code=404, detail="Stored figure image path is invalid")
        if not img_path.exists():
            raise HTTPException(status_code=404, detail="Stored figure image file is missing")

        media_type = mimetypes.guess_type(str(img_path))[0] or "application/octet-stream"
        return FileResponse(
            img_path,
            media_type=media_type,
            headers={"Cache-Control": f"public, max-age={_FIGURE_IMAGE_CACHE_TTL_SEC}, immutable"},
        )


@router.post("/papers/{paper_id}/figures/analyze")
def analyze_paper_figures(
    paper_id: UUID,
    max_figures: int = Query(default=10, ge=1, le=30),
    body: PaperFigureAnalyzeReq | None = None,
) -> dict:
    """Analyze selected figure candidates, or keep old auto mode when omitted."""
    _invalidate_paper_figures_cache(paper_id)
    from packages.ai.paper.figure_service import FigureService

    svc = FigureService()
    try:
        selected_ids = [
            str(figure_id).strip()
            for figure_id in (body.figure_ids if body else [])
            if str(figure_id).strip()
        ]
        if selected_ids:
            svc.analyze_selected_figures(paper_id, selected_ids)
        else:
            source_arxiv_id: str | None = None
            with session_scope() as session:
                repo = _paper_data(session).papers
                try:
                    paper = repo.get_by_id(paper_id)
                except ValueError as exc:
                    raise HTTPException(status_code=404, detail=str(exc)) from exc
                try:
                    pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=502, detail=f"Failed to prepare PDF: {exc}"
                    ) from exc
                source_arxiv_id = (
                    paper.arxiv_id if _has_real_arxiv_id(getattr(paper, "arxiv_id", None)) else None
                )

            svc.analyze_paper_figures(
                paper_id,
                pdf_path,
                max_figures,
                arxiv_id=source_arxiv_id,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to analyze figures: {exc}",
        ) from exc
    items = _attach_figure_image_urls(paper_id, FigureService.get_paper_analyses(paper_id))
    _cache_paper_figures_items(paper_id, items)
    return {"paper_id": str(paper_id), "count": len(items), "items": items}


@router.post("/papers/{paper_id}/figures/analyze-async")
def analyze_paper_figures_async(
    paper_id: UUID,
    max_figures: int = Query(default=10, ge=1, le=30),
    body: PaperFigureAnalyzeReq | None = None,
) -> dict:
    """Analyze selected figure candidates in background and return task id."""
    _invalidate_paper_figures_cache(paper_id)

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        title = str(getattr(paper, "title", "") or "").strip()

    selected_ids = [
        str(figure_id).strip()
        for figure_id in (body.figure_ids if body else [])
        if str(figure_id).strip()
    ]
    task_title = (
        f"图表分析: {(title[:28] + '...') if len(title) > 28 else title}"
        if title
        else f"图表分析: {str(paper_id)[:8]}"
    )

    def _run_task(progress_callback: Callable[[str, int, int], None] | None = None) -> dict:
        def _progress(message: str, current: int, total: int = 100) -> None:
            if progress_callback is not None:
                progress_callback(message, current, total)

        from packages.ai.paper.figure_service import FigureService

        svc = FigureService()
        _progress("准备图表候选...", 8, 100)
        try:
            if selected_ids:
                _progress(f"分析 {len(selected_ids)} 个已选图表...", 30, 100)
                svc.analyze_selected_figures(paper_id, selected_ids)
            else:
                source_arxiv_id: str | None = None
                with session_scope() as session:
                    repo = _paper_data(session).papers
                    try:
                        paper = repo.get_by_id(paper_id)
                    except ValueError as exc:
                        raise RuntimeError(str(exc)) from exc
                    _progress("准备 PDF 文件...", 22, 100)
                    try:
                        pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)
                    except HTTPException as exc:
                        raise RuntimeError(str(exc.detail or exc)) from exc
                    except Exception as exc:
                        raise RuntimeError(f"Failed to prepare PDF: {exc}") from exc
                    source_arxiv_id = (
                        paper.arxiv_id
                        if _has_real_arxiv_id(getattr(paper, "arxiv_id", None))
                        else None
                    )

                _progress("分析图表内容...", 58, 100)
                svc.analyze_paper_figures(
                    paper_id,
                    pdf_path,
                    max_figures,
                    arxiv_id=source_arxiv_id,
                )
        except FileNotFoundError as exc:
            raise RuntimeError(str(exc)) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to analyze figures: {exc}") from exc

        _progress("保存图表分析结果...", 92, 100)
        items = _attach_figure_image_urls(paper_id, FigureService.get_paper_analyses(paper_id))
        _cache_paper_figures_items(paper_id, items)
        _progress("图表分析完成", 100, 100)
        return {"paper_id": str(paper_id), "count": len(items), "items": items}

    task_id = global_tracker.submit(
        task_type="figure_analyze",
        title=task_title,
        fn=_run_task,
        total=100,
        metadata={
            "source": "paper",
            "source_id": str(paper_id),
            "paper_id": str(paper_id),
            "figure_count": len(selected_ids) if selected_ids else max_figures,
        },
    )
    return {"task_id": task_id, "status": "running", "message": "图表分析任务已启动"}


@router.delete("/papers/{paper_id}/figures/{figure_id}")
def delete_paper_figure(paper_id: UUID, figure_id: str) -> dict:
    from packages.ai.paper.figure_service import FigureService

    _invalidate_paper_figures_cache(paper_id)
    deleted = FigureService.delete_paper_figure(paper_id, figure_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Figure candidate not found")

    items = _attach_figure_image_urls(paper_id, FigureService.get_paper_analyses(paper_id))
    _cache_paper_figures_items(paper_id, items)
    return {"paper_id": str(paper_id), "deleted": figure_id, "count": len(items), "items": items}


@router.post("/papers/{paper_id}/figures/delete")
def delete_paper_figures(paper_id: UUID, body: PaperFigureDeleteReq) -> dict:
    from packages.ai.paper.figure_service import FigureService

    _invalidate_paper_figures_cache(paper_id)
    figure_ids = [str(fid).strip() for fid in body.figure_ids if str(fid).strip()]
    if not figure_ids:
        raise HTTPException(status_code=400, detail="figure_ids is required")

    deleted_ids = FigureService.delete_paper_figures(paper_id, figure_ids)
    items = _attach_figure_image_urls(paper_id, FigureService.get_paper_analyses(paper_id))
    _cache_paper_figures_items(paper_id, items)
    return {
        "paper_id": str(paper_id),
        "deleted_ids": deleted_ids,
        "deleted_count": len(deleted_ids),
        "count": len(items),
        "items": items,
    }


@router.get("/papers/{paper_id}/similar")
def similar(
    paper_id: UUID,
    top_k: int = Query(default=5, ge=1, le=20),
) -> dict:
    ids = rag_service.similar_papers(paper_id, top_k=top_k)
    items = []
    if ids:
        with session_scope() as session:
            repo = _paper_data(session).papers
            for pid in ids:
                try:
                    p = repo.get_by_id(pid)
                    items.append(
                        {
                            "id": str(p.id),
                            "title": p.title,
                            "arxiv_id": p.arxiv_id,
                            "read_status": p.read_status.value if p.read_status else "unread",
                        }
                    )
                except Exception:
                    items.append(
                        {
                            "id": str(pid),
                            "title": str(pid),
                            "arxiv_id": None,
                            "read_status": "unread",
                        }
                    )
    return {
        "paper_id": str(paper_id),
        "similar_ids": [str(x) for x in ids],
        "items": items,
    }


@router.post("/papers/{paper_id}/reasoning")
def paper_reasoning(
    paper_id: UUID,
    reasoning_level: Literal["default", "low", "medium", "high", "xhigh"] | None = Query(
        default=None,
    ),
    detail_level: Literal["low", "medium", "high"] | None = Query(default=None),
    content_source: str | None = Query(default=None),
    evidence_mode: str | None = Query(default=None),
) -> dict:
    """Return reasoning-chain analysis for the given paper."""
    from packages.ai.research.reasoning_service import ReasoningService

    detail_level, reasoning_level = resolve_paper_analysis_levels(detail_level, reasoning_level)

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ReasoningService().analyze(
        paper_id,
        reasoning_level=reasoning_level,
        detail_level=detail_level,
        content_source=content_source or "auto",
        evidence_mode=evidence_mode or "full",
    )


@router.post("/papers/{paper_id}/reasoning/async")
def paper_reasoning_async(
    paper_id: UUID,
    reasoning_level: Literal["default", "low", "medium", "high", "xhigh"] | None = Query(
        default=None,
    ),
    detail_level: Literal["low", "medium", "high"] | None = Query(default=None),
    content_source: str | None = Query(default=None),
    evidence_mode: str | None = Query(default=None),
) -> dict:
    """Submit reasoning-chain analysis as a background task."""
    from packages.ai.research.reasoning_service import ReasoningService

    detail_level, reasoning_level = resolve_paper_analysis_levels(detail_level, reasoning_level)

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    title = (get_paper_title(paper_id) or str(paper_id)[:8])[:30]
    requested_content_source = normalize_paper_content_source(content_source or "auto")
    normalized_evidence_mode = str(evidence_mode or "full").strip().lower() or "full"

    def _fn(progress_callback=None):
        return ReasoningService().analyze(
            paper_id,
            reasoning_level=reasoning_level,
            detail_level=detail_level,
            content_source=content_source or "auto",
            evidence_mode=normalized_evidence_mode,
            progress_callback=progress_callback,
        )

    task_id = global_tracker.submit(
        task_type="reasoning",
        title=f"推理链: {title}",
        fn=_fn,
        total=100,
        metadata={
            "source": "paper",
            "source_id": str(paper_id),
            "paper_id": str(paper_id),
            "detail_level": detail_level,
            "reasoning_level": reasoning_level,
            "content_source": requested_content_source,
            "evidence_mode": normalized_evidence_mode,
        },
    )
    global_tracker.append_log(
        task_id, f"请求来源: {paper_content_source_label(requested_content_source)}"
    )
    global_tracker.append_log(
        task_id, f"证据模式: {'完整' if normalized_evidence_mode == 'full' else '粗略'}"
    )
    return {"task_id": task_id, "status": "running", "message": "推理链分析任务已启动"}


@router.post("/papers/{paper_id}/analyze")
def analyze_paper_rounds(
    paper_id: UUID,
    body: dict | None = None,
) -> dict:
    from packages.ai.paper.paper_analysis_service import PaperAnalysisService

    detail_level, reasoning_level = resolve_paper_analysis_levels(
        (body or {}).get("detail_level"),
        (body or {}).get("reasoning_level"),
    )
    content_source = str((body or {}).get("content_source") or "auto")
    evidence_mode = str((body or {}).get("evidence_mode") or "full")
    requested_content_source = normalize_paper_content_source(content_source)
    normalized_evidence_mode = str(evidence_mode or "full").strip().lower() or "full"

    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    title = (get_paper_title(paper_id) or str(paper_id)[:8])[:40]

    def _fn(progress_callback=None):
        return PaperAnalysisService().analyze(
            paper_id,
            detail_level=detail_level,
            reasoning_level=reasoning_level,
            content_source=content_source,
            evidence_mode=normalized_evidence_mode,
            progress_callback=progress_callback,
        )

    task_id = global_tracker.submit(
        task_type="paper_analysis_rounds",
        title=f"三轮分析: {title}",
        fn=_fn,
        total=100,
        metadata={
            "source": "paper",
            "source_id": str(paper_id),
            "paper_id": str(paper_id),
            "retry_label": "重新分析",
            "retry_metadata": {
                "paper_id": str(paper_id),
                "detail_level": detail_level,
                "reasoning_level": reasoning_level,
                "content_source": requested_content_source,
                "evidence_mode": normalized_evidence_mode,
            },
            "detail_level": detail_level,
            "reasoning_level": reasoning_level,
            "content_source": requested_content_source,
            "evidence_mode": normalized_evidence_mode,
        },
    )
    global_tracker.append_log(
        task_id, f"请求来源: {paper_content_source_label(requested_content_source)}"
    )
    global_tracker.append_log(
        task_id, f"证据模式: {'完整' if normalized_evidence_mode == 'full' else '粗略'}"
    )
    global_tracker.register_retry(
        task_id,
        lambda: global_tracker.submit(
            task_type="paper_analysis_rounds",
            title=f"三轮分析: {title}",
            fn=_fn,
            total=100,
            metadata={
                "source": "paper",
                "source_id": str(paper_id),
                "paper_id": str(paper_id),
                "detail_level": detail_level,
                "reasoning_level": reasoning_level,
                "content_source": requested_content_source,
                "evidence_mode": normalized_evidence_mode,
            },
        ),
        label="重新分析",
        metadata={
            "paper_id": str(paper_id),
            "detail_level": detail_level,
            "reasoning_level": reasoning_level,
            "content_source": requested_content_source,
            "evidence_mode": normalized_evidence_mode,
        },
    )
    return {"task_id": task_id, "status": "running", "message": "论文三轮分析任务已启动"}


@router.get("/papers/{paper_id}/analysis")
def get_paper_analysis(paper_id: UUID) -> dict:
    with session_scope() as session:
        repo = _paper_data(session).papers
        try:
            paper = repo.get_by_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "item": (paper.metadata_json or {}).get("analysis_rounds") or {},
        }


@router.post("/papers/{paper_id}/analysis/retry")
def retry_paper_analysis(
    paper_id: UUID,
    body: dict | None = None,
) -> dict:
    return analyze_paper_rounds(paper_id, body=body)
