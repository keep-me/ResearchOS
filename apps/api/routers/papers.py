"""Paper management routes."""



import asyncio
import base64
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
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
    apply_external_resolution as _apply_external_resolution,
    ensure_paper_pdf as _ensure_paper_pdf_impl,
    extract_paper_figures_payload as _extract_paper_figures_payload_impl,
    has_real_arxiv_id as _has_real_arxiv_id,
    normalize_manual_paper_id as _normalize_manual_paper_id,
    replace_paper_pdf as _replace_paper_pdf_impl,
    resolve_external_pdf_source as _resolve_external_pdf_source,
    upload_paper_pdf as _upload_paper_pdf_impl,
)
from packages.ai.paper.paper_serializer import (
    attach_figure_image_urls as _attach_figure_image_urls,
    paper_ocr_status_payload as _paper_ocr_status_payload,
    utc_iso as _utc_iso,
)
from packages.config import get_settings
from packages.domain.task_tracker import global_tracker
from packages.domain.schemas import (
    AIExplainReq,
    PaperAutoClassifyReq,
    PaperBatchDeleteReq,
    PaperFigureAnalyzeReq,
    PaperFigureDeleteReq,
    PaperMetadataUpdateReq,
    PaperReaderNoteReq,
    PaperReaderQueryReq,
)
from packages.integrations.llm_client import LLMClient
from packages.storage.db import session_scope
from packages.storage.models import ImageAnalysis
from packages.storage.repository_facades import PaperDataFacade
from packages.storage.repositories import PaperRepository

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
        "tags": _normalize_reader_tags(raw.get("tags") if isinstance(raw.get("tags"), list) else []),
        "pinned": bool(raw.get("pinned")),
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

    analysis_rounds = metadata.get("analysis_rounds") if isinstance(metadata.get("analysis_rounds"), dict) else {}
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
    return any(marker in text for marker in markers) or "blocked" in lowered or "bad gateway" in lowered


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
            sort_by=sort_by if sort_by in ("created_at", "publication_date", "title", "impact") else "created_at",
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
        from packages.storage.models import AnalysisReport as AR
        from sqlalchemy import select as _sel

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
            from packages.storage.models import AnalysisReport
            from sqlalchemy import select as _select

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
            raise HTTPException(status_code=400, detail="Only folder topics can be linked to papers")

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
            raise HTTPException(status_code=400, detail="figure_id or image_base64 is required for figure query")

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
    if figure_id and (caption or description) and _should_fallback_reader_figure_to_text(result.content):
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
    effective_force = force or previous_status in {"failed", "success"}

    task_title = f"OCR 处理: {(title[:36] + '...') if len(title) > 36 else title}" if title else f"OCR 处理: {str(paper_id)[:8]}"

    def _run_task(progress_callback: Callable[[str, int, int], None] | None = None) -> dict:
        def _progress(message: str, current: int, total: int = 100) -> None:
            if progress_callback:
                progress_callback(message, current, total)

        def _start_pulse(message: str, *, start: int = 20, end: int = 88, step: int = 2, interval: float = 1.8):
            if not progress_callback:
                return None, None
            stop_event = threading.Event()

            def _runner():
                current = start
                while not stop_event.wait(interval):
                    _progress(message, current, 100)
                    if current < end:
                        current = min(end, current + step)

            thread = threading.Thread(target=_runner, daemon=True, name=f"ocr-progress-{str(paper_id)[:8]}")
            thread.start()
            return stop_event, thread

        _progress("准备 PDF 文件...", 5, 100)
        with session_scope() as session:
            repo = _paper_data(session).papers
            paper = repo.get_by_id(paper_id)
            pdf_path = _ensure_paper_pdf(session, repo, paper, paper_id)

        _progress("检查 MinerU 运行环境...", 12, 100)
        prepare_stop_event, prepare_pulse_thread = _start_pulse(
            "正在检查 / 下载 MinerU pipeline 模型...",
            start=18,
            end=40,
            step=2,
            interval=1.6,
        )
        try:
            MinerUOcrRuntime.prepare_runtime(progress_callback=_progress)
        finally:
            if prepare_stop_event is not None:
                prepare_stop_event.set()
            if prepare_pulse_thread is not None:
                prepare_pulse_thread.join(timeout=0.5)

        _progress("启动 MinerU 本地 OCR 处理...", 42, 100)
        stop_event, pulse_thread = _start_pulse("正在运行 MinerU pipeline，生成 Markdown / 图表结构...", start=48, end=86)
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
        status_code = 503 if detail.startswith("Figure extraction dependencies unavailable") else 502
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
    task_title = f"图表提取: {(title[:36] + '...') if len(title) > 36 else title}" if title else f"图表提取: {str(paper_id)[:8]}"

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

    from packages.ai.paper.figure_service import FigureService
    from packages.storage.db import session_scope
    from packages.storage.models import ImageAnalysis
    from sqlalchemy import select

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
                    raise HTTPException(status_code=502, detail=f"Failed to prepare PDF: {exc}") from exc
                source_arxiv_id = (
                    paper.arxiv_id
                    if _has_real_arxiv_id(getattr(paper, "arxiv_id", None))
                    else None
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
        content_source=content_source or "pdf",
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
    requested_content_source = normalize_paper_content_source(content_source or "pdf")
    normalized_evidence_mode = str(evidence_mode or "full").strip().lower() or "full"

    def _fn(progress_callback=None):
        return ReasoningService().analyze(
            paper_id,
            reasoning_level=reasoning_level,
            detail_level=detail_level,
            content_source=content_source or "pdf",
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
    global_tracker.append_log(task_id, f"请求来源: {paper_content_source_label(requested_content_source)}")
    global_tracker.append_log(task_id, f"证据模式: {'完整' if normalized_evidence_mode == 'full' else '粗略'}")
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
    content_source = str((body or {}).get("content_source") or "pdf")
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
    global_tracker.append_log(task_id, f"请求来源: {paper_content_source_label(requested_content_source)}")
    global_tracker.append_log(task_id, f"证据模式: {'完整' if normalized_evidence_mode == 'full' else '粗略'}")
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

