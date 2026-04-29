from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import date, datetime
from queue import Queue
from uuid import UUID

from packages.ai.research.brief_service import DailyBriefService
from packages.ai.paper.external_paper_preview_service import ExternalPaperPreviewService
from packages.ai.paper.figure_service import FigureService
from packages.ai.research.graph_service import GraphService
from packages.ai.research.keyword_service import KeywordService
from packages.ai.paper.paper_analysis_service import PaperAnalysisService
from packages.ai.paper.pipelines import PaperPipelines
from packages.ai.research.rag_service import RAGService
from packages.ai.research.reasoning_service import ReasoningService
from packages.ai.research.research_wiki_service import ResearchWikiService
from packages.ai.research.research_venue_catalog import (
    classify_venue_type,
    matches_venue_filter,
    venue_tier_for_name,
)
from packages.agent.tools.tool_runtime import AgentToolContext, ToolProgress, ToolResult
from packages.ai.research.writing_service import WritingService
from packages.config import get_settings
from packages.integrations.arxiv_client import ArxivClient
from packages.integrations.openalex_client import OpenAlexClient
from packages.storage.db import check_db_connection, session_scope
from packages.storage.models import Paper, PipelineRun, TopicSubscription
from packages.storage.repositories import PaperRepository, PipelineRunRepository, TopicRepository
from sqlalchemy import func, select

logger = logging.getLogger(__name__)


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _require_paper_id(paper_id: str) -> tuple[UUID | None, ToolResult | None]:
    pid = _parse_uuid(paper_id)
    if pid is None:
        return None, ToolResult(success=False, summary="无效的论文 ID")
    with session_scope() as session:
        try:
            PaperRepository(session).get_by_id(pid)
        except ValueError:
            return None, ToolResult(success=False, summary="论文不存在")
    return pid, None


def _truncate_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _compact_list(value: object, limit: int) -> list:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _resolve_project_id(
    project_id: str | None = None,
    *,
    context: AgentToolContext | None = None,
) -> tuple[str | None, ToolResult | None]:
    try:
        resolved = ResearchWikiService().resolve_project_id(project_id=project_id, context=context)
    except ValueError as exc:
        return None, ToolResult(success=False, summary=str(exc))
    return resolved, None


def _paper_to_dict(paper: Paper) -> dict:
    metadata = dict(paper.metadata_json or {})
    return {
        "id": str(paper.id),
        "title": paper.title,
        "arxiv_id": paper.arxiv_id,
        "abstract": paper.abstract or "",
        "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
        "read_status": paper.read_status.value,
        "pdf_path": paper.pdf_path,
        "favorited": bool(getattr(paper, "favorited", False)),
        "categories": metadata.get("categories", []),
        "authors": metadata.get("authors", []),
        "keywords": metadata.get("keywords", []),
        "title_zh": metadata.get("title_zh") or "",
        "abstract_zh": metadata.get("abstract_zh") or "",
        "citation_count": metadata.get("citation_count") or metadata.get("citations") or 0,
        "cited_by_count": metadata.get("cited_by_count") or 0,
        "venue": metadata.get("venue") or metadata.get("citation_venue") or "",
        "venue_type": metadata.get("venue_type") or "",
        "venue_tier": metadata.get("venue_tier") or "",
        "source_url": metadata.get("source_url") or "",
        "pdf_url": metadata.get("pdf_url") or "",
        "skim_report": metadata.get("skim_report"),
        "deep_report": metadata.get("deep_report"),
        "analysis_rounds": metadata.get("analysis_rounds"),
        "mineru_ocr": metadata.get("mineru_ocr"),
        "embedding_status": metadata.get("embedding_status") or {},
        "created_at": paper.created_at.isoformat() if getattr(paper, "created_at", None) else None,
    }


def _paper_figure_items(paper_id: UUID, *, limit: int | None = 6) -> list[dict]:
    items = FigureService.get_paper_analyses(paper_id)
    normalized: list[dict] = []
    paper_id_text = str(paper_id)
    for item in items:
        payload = dict(item)
        if payload.get("has_image") and payload.get("id"):
            payload["image_url"] = f"/papers/{paper_id_text}/figures/{payload['id']}/image"
        else:
            payload["image_url"] = None
        normalized.append(payload)
    if limit is None or limit <= 0:
        return normalized
    return normalized[:limit]


def _paper_figure_refs(figures: list[dict], *, limit: int | None = 4) -> list[dict]:
    visible = figures if limit is None or limit <= 0 else figures[:limit]
    refs: list[dict] = []
    for item in visible:
        refs.append(
            {
                "id": item.get("id"),
                "figure_label": item.get("figure_label"),
                "page_number": item.get("page_number"),
                "image_type": item.get("image_type"),
                "caption": item.get("caption"),
                "image_url": item.get("image_url"),
            }
        )
    return refs


def _paper_saved_analysis_flags(payload: dict) -> dict[str, bool]:
    return {
        "has_skim_report": isinstance(payload.get("skim_report"), dict) and bool(payload.get("skim_report")),
        "has_deep_report": isinstance(payload.get("deep_report"), dict) and bool(payload.get("deep_report")),
        "has_analysis_rounds": isinstance(payload.get("analysis_rounds"), dict) and bool(payload.get("analysis_rounds")),
    }


def _paper_to_search_item(paper: Paper) -> dict:
    metadata = dict(paper.metadata_json or {})
    item = {
        "id": str(paper.id),
        "title": paper.title,
        "arxiv_id": paper.arxiv_id,
        "abstract_preview": _truncate_text(paper.abstract, 480),
        "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
        "read_status": paper.read_status.value,
        "has_pdf": bool(paper.pdf_path),
        "favorited": bool(getattr(paper, "favorited", False)),
        "categories": _compact_list(metadata.get("categories"), 6),
        "authors": _compact_list(metadata.get("authors"), 8),
        "keywords": _compact_list(metadata.get("keywords"), 8),
        "title_zh": metadata.get("title_zh") or "",
        "abstract_zh_preview": _truncate_text(metadata.get("abstract_zh"), 280),
        "citation_count": metadata.get("citation_count") or metadata.get("citations") or 0,
        "cited_by_count": metadata.get("cited_by_count") or 0,
        "venue": metadata.get("venue") or metadata.get("citation_venue") or "",
        "venue_type": metadata.get("venue_type") or "",
        "venue_tier": metadata.get("venue_tier") or "",
        "source_url": metadata.get("source_url") or "",
        "pdf_url": metadata.get("pdf_url") or "",
        "has_ocr": bool(metadata.get("mineru_ocr")),
        "has_embedding": paper.embedding is not None or bool(metadata.get("embedding_status")),
        "created_at": paper.created_at.isoformat() if getattr(paper, "created_at", None) else None,
    }
    item.update(
        _paper_saved_analysis_flags(
            {
                "skim_report": metadata.get("skim_report"),
                "deep_report": metadata.get("deep_report"),
                "analysis_rounds": metadata.get("analysis_rounds"),
            }
        )
    )
    return item


def _is_invalid_analysis_markdown(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return True
    markers = (
        "当前模型未返回有效内容",
        "模型服务暂不可用",
        "模型鉴权失败",
        "未配置模型",
        "请稍后重试或检查 api 配置",
        "token unavailable",
        "令牌状态不可用",
        "unauthorized",
        "401",
    )
    return any(marker in lowered for marker in markers)


def _analysis_rounds_is_effective(bundle: dict) -> tuple[bool, str]:
    if not isinstance(bundle, dict) or not bundle:
        return False, "尚未生成三轮分析"
    final_notes = bundle.get("final_notes")
    if not isinstance(final_notes, dict):
        return False, "三轮分析结果缺少最终结构化笔记"
    markdown = str(final_notes.get("markdown") or "").strip()
    if _is_invalid_analysis_markdown(markdown):
        return False, "三轮分析生成失败：模型未返回有效内容，请检查模型配置后重试"
    return True, "已读取论文三轮分析结果"


def _stream_background_call(
    runner: Callable[[Callable[[str, int, int], None] | None], ToolResult],
    *,
    start_message: str,
) -> Iterator[ToolProgress | ToolResult]:
    queue: Queue[object] = Queue()
    sentinel = object()

    def _progress(message: str, current: int, total: int) -> None:
        queue.put(ToolProgress(message=message, current=current, total=total))

    def _worker() -> None:
        try:
            queue.put(ToolProgress(message=start_message, current=2, total=100))
            result = runner(_progress)
            queue.put(result)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.exception("Background research tool runner failed: %s", exc)
            queue.put(exc)
        finally:
            queue.put(sentinel)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    while True:
        item = queue.get()
        if item is sentinel:
            break
        if isinstance(item, ToolProgress):
            yield item
            continue
        if isinstance(item, ToolResult):
            yield item
            continue
        if isinstance(item, Exception):
            yield ToolResult(success=False, summary=str(item))
            break


def _clamp_int(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _normalize_source_scope(value: str | None) -> str:
    normalized = str(value or "hybrid").strip().lower()
    if normalized in {"hybrid", "arxiv", "openalex"}:
        return normalized
    return "hybrid"


def _normalize_venue_tier(value: str | None) -> str:
    normalized = str(value or "all").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"all", "ccf_a"}:
        return normalized
    return "all"


def _normalize_venue_type(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized in {"all", "conference", "journal"}:
        return normalized
    return "all"


def _normalize_venue_names(value: list[str] | None) -> list[str]:
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _external_publication_sort_key(item: dict) -> tuple[date, int]:
    raw = str(item.get("publication_date") or "").strip()
    if raw:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if fmt == "%Y":
                    return date(parsed.year, 1, 1), int(item.get("citation_count") or 0)
                if fmt == "%Y-%m":
                    return date(parsed.year, parsed.month, 1), int(item.get("citation_count") or 0)
                return parsed.date(), int(item.get("citation_count") or 0)
            except ValueError:
                continue
    try:
        year = int(item.get("publication_year") or 0)
    except (TypeError, ValueError):
        year = 0
    return date(max(1, year), 1, 1) if year > 0 else date(1900, 1, 1), int(item.get("citation_count") or 0)


def _sort_external_results(items: list[dict], sort_mode: str) -> list[dict]:
    normalized = str(sort_mode or "relevance").strip().lower()
    if normalized == "time":
        return sorted(items, key=lambda item: _external_publication_sort_key(item), reverse=True)
    if normalized == "impact":
        return sorted(
            items,
            key=lambda item: (
                int(item.get("citation_count") or 0),
                _external_publication_sort_key(item)[0],
            ),
            reverse=True,
        )
    return items


def _resolve_arxiv_sort_mode(sort_mode: str) -> str:
    normalized = str(sort_mode or "relevance").strip().lower()
    if normalized == "time":
        return "submittedDate"
    if normalized == "impact":
        return "impact"
    return "relevance"


def _openalex_item_matches_filters(
    item: dict,
    *,
    venue_tier: str,
    venue_type: str,
    venue_names: list[str],
    from_year: int | None,
) -> bool:
    year = item.get("publication_year")
    if from_year is not None:
        try:
            if int(year or 0) < from_year:
                return False
        except (TypeError, ValueError):
            return False
    return matches_venue_filter(
        item.get("venue"),
        raw_venue_type=item.get("venue_type"),
        venue_tier=venue_tier,
        venue_type=venue_type,
        venue_names=venue_names,
    )


def _paper_to_external_result(paper) -> dict:
    metadata = dict(paper.metadata or {})
    publication_date = paper.publication_date.isoformat() if paper.publication_date else None
    return {
        "title": paper.title,
        "abstract": paper.abstract or "",
        "publication_year": paper.publication_date.year if paper.publication_date else None,
        "publication_date": publication_date,
        "citation_count": metadata.get("citation_count") or 0,
        "venue": metadata.get("citation_venue") or "arXiv",
        "venue_type": "repository",
        "venue_tier": None,
        "authors": metadata.get("authors") or [],
        "categories": metadata.get("categories") or [],
        "arxiv_id": paper.arxiv_id,
        "source_url": f"https://arxiv.org/abs/{paper.arxiv_id}" if paper.arxiv_id else None,
        "pdf_url": f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf" if paper.arxiv_id else None,
        "source": "arxiv",
    }


def _dedupe_literature_items(items: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen_arxiv: set[str] = set()
    seen_openalex: set[str] = set()
    seen_titles: set[str] = set()

    for item in items:
        arxiv_id = str(item.get("arxiv_id") or "").strip().lower()
        openalex_id = str(item.get("openalex_id") or "").strip().lower()
        title_key = " ".join(str(item.get("title") or "").strip().lower().split())
        if arxiv_id and arxiv_id in seen_arxiv:
            continue
        if openalex_id and openalex_id in seen_openalex:
            continue
        if not arxiv_id and not openalex_id and title_key and title_key in seen_titles:
            continue
        if arxiv_id:
            seen_arxiv.add(arxiv_id)
        if openalex_id:
            seen_openalex.add(openalex_id)
        if title_key:
            seen_titles.add(title_key)
        deduped.append(item)
    return deduped


def _search_papers(keyword: str, limit: int = 20) -> ToolResult:
    with session_scope() as session:
        papers = PaperRepository(session).full_text_candidates(keyword.strip(), limit=max(1, min(limit, 20)))
        items = [_paper_to_search_item(paper) for paper in papers]
    return ToolResult(
        success=True,
        data={"papers": items, "count": len(items)},
        summary=f"找到 {len(items)} 篇匹配论文",
    )


def _get_paper_detail(paper_id: str) -> ToolResult:
    pid, err = _require_paper_id(paper_id)
    if err:
        return err
    assert pid is not None
    with session_scope() as session:
        paper = PaperRepository(session).get_by_id(pid)
        data = _paper_to_dict(paper)
        data["has_embedding"] = bool(paper.embedding)
        figures = _paper_figure_items(pid, limit=6)
        figure_count = len(FigureService.get_paper_analyses(pid))
    flags = _paper_saved_analysis_flags(data)
    data.pop("skim_report", None)
    data.pop("deep_report", None)
    data.pop("analysis_rounds", None)
    data.update(flags)
    data["figure_count"] = figure_count
    data["figure_refs"] = _paper_figure_refs(figures)
    return ToolResult(
        success=True,
        data=data,
        summary=f"已读取论文《{data['title']}》",
        internal_data={"display_data": {"figures": figures}},
    )


def _get_paper_analysis(paper_id: str) -> ToolResult:
    pid, err = _require_paper_id(paper_id)
    if err:
        return err
    assert pid is not None
    with session_scope() as session:
        paper = PaperRepository(session).get_by_id(pid)
        title = paper.title
        metadata = dict(paper.metadata_json or {})
    bundle = metadata.get("analysis_rounds") if isinstance(metadata.get("analysis_rounds"), dict) else {}
    ok, summary = _analysis_rounds_is_effective(bundle)
    return ToolResult(
        success=ok,
        data={
            "paper_id": str(pid),
            "title": title,
            "analysis_rounds": bundle,
        },
        summary=summary,
    )


def _paper_figures(paper_id: str) -> ToolResult:
    pid, err = _require_paper_id(paper_id)
    if err:
        return err
    assert pid is not None
    with session_scope() as session:
        paper = PaperRepository(session).get_by_id(pid)
        title = paper.title
    figure_items = _paper_figure_items(pid, limit=None)
    return ToolResult(
        success=True,
        data={
            "paper_id": str(pid),
            "title": title,
            "count": len(figure_items),
            "items": figure_items,
            "figures": figure_items,
            "figure_refs": _paper_figure_refs(figure_items, limit=None),
        },
        summary=f"已读取 {len(figure_items)} 个已提取图表",
        internal_data={"display_data": {"figures": figure_items}},
    )


def _get_similar_papers(paper_id: str, top_k: int = 5) -> ToolResult:
    pid, err = _require_paper_id(paper_id)
    if err:
        return err
    assert pid is not None
    with session_scope() as session:
        paper = PaperRepository(session).get_by_id(pid)
        if not paper.embedding:
            return ToolResult(success=False, summary="该论文尚未向量化，请先执行向量嵌入")

    ids = RAGService().similar_papers(pid, top_k=max(1, min(top_k, 20)))
    with session_scope() as session:
        repo = PaperRepository(session)
        items = []
        for other_id in ids:
            try:
                items.append(_paper_to_search_item(repo.get_by_id(other_id)))
            except ValueError:
                continue
    return ToolResult(
        success=True,
        data={"paper_id": paper_id, "items": items, "count": len(items)},
        summary=f"找到 {len(items)} 篇相似论文",
    )


def _get_citation_tree(paper_id: str, depth: int = 2) -> ToolResult:
    pid, err = _require_paper_id(paper_id)
    if err:
        return err
    assert pid is not None
    tree = GraphService().citation_tree(root_paper_id=str(pid), depth=max(1, min(depth, 4)))
    total_nodes = len(tree.get("nodes") or [])
    total_edges = len(tree.get("edges") or [])
    return ToolResult(
        success=True,
        data=tree,
        summary=f"引用树已生成，包含 {total_nodes} 个节点、{total_edges} 条边",
    )


def _get_timeline(keyword: str, limit: int = 100) -> ToolResult:
    timeline = GraphService().timeline(keyword=keyword.strip(), limit=max(5, min(limit, 200)))
    milestones = timeline.get("milestones") or []
    return ToolResult(
        success=True,
        data=timeline,
        summary=f"时间线已生成，包含 {len(milestones)} 个里程碑",
    )


def _list_topics() -> ToolResult:
    with session_scope() as session:
        topics = TopicRepository(session).list_topics(enabled_only=False, kind=None)
        items = [
            {
                "id": str(topic.id),
                "name": topic.name,
                "kind": topic.kind,
                "query": topic.query,
                "enabled": topic.enabled,
                "sort_by": topic.sort_by,
                "schedule_frequency": topic.schedule_frequency,
                "schedule_time_utc": topic.schedule_time_utc,
                "date_filter_start": topic.date_filter_start.isoformat() if topic.date_filter_start else None,
                "date_filter_end": topic.date_filter_end.isoformat() if topic.date_filter_end else None,
            }
            for topic in topics
        ]
    return ToolResult(success=True, data={"topics": items, "count": len(items)}, summary=f"共有 {len(items)} 个工作区/订阅")


def _get_system_status() -> ToolResult:
    db_ok = check_db_connection()
    with session_scope() as session:
        paper_count = int(session.scalar(select(func.count()).select_from(Paper)) or 0)
        topic_count = int(session.scalar(select(func.count()).select_from(TopicSubscription)) or 0)
        run_count = int(session.scalar(select(func.count()).select_from(PipelineRun)) or 0)
        latest_runs = [
            {
                "id": str(run.id),
                "pipeline_name": run.pipeline_name,
                "status": getattr(run.status, "value", str(run.status)),
                "created_at": run.created_at.isoformat(),
            }
            for run in PipelineRunRepository(session).list_latest(limit=5)
        ]

    return ToolResult(
        success=db_ok,
        data={
            "database_ok": db_ok,
            "paper_count": paper_count,
            "topic_count": topic_count,
            "pipeline_run_count": run_count,
            "latest_runs": latest_runs,
        },
        summary=f"数据库{'正常' if db_ok else '异常'}，已有 {paper_count} 篇论文、{topic_count} 个工作区/订阅",
    )


def _search_literature(
    query: str,
    max_results: int = 20,
    source_scope: str = "hybrid",
    venue_tier: str = "all",
    venue_type: str = "all",
    venue_names: list[str] | None = None,
    from_year: int | None = None,
    sort_mode: str = "relevance",
    date_from: date | None = None,
    date_to: date | None = None,
) -> ToolResult:
    cleaned_query = str(query or "").strip()
    if not cleaned_query:
        return ToolResult(success=False, summary="文献检索关键词不能为空")

    requested = _clamp_int(max_results, default=20, minimum=1, maximum=50)
    effective_scope = _normalize_source_scope(source_scope)
    effective_tier = _normalize_venue_tier(venue_tier)
    effective_type = _normalize_venue_type(venue_type)
    effective_venue_names = _normalize_venue_names(venue_names)
    effective_from_year = None
    if from_year is not None:
        try:
            effective_from_year = max(1900, min(int(from_year), 2100))
        except (TypeError, ValueError):
            effective_from_year = None

    results: list[dict] = []
    source_counts = {"openalex": 0, "arxiv": 0}
    skipped_sources: list[str] = []
    fetch_limit = max(requested * 3, 30)

    if effective_scope in {"hybrid", "openalex"}:
        settings = get_settings()
        client = OpenAlexClient(email=settings.openalex_email)
        try:
            openalex_items = client.search_works(cleaned_query, max_results=min(fetch_limit, 100))
        finally:
            with suppress(Exception):
                client.close()

        for item in openalex_items:
            normalized_type = classify_venue_type(item.get("venue_type"), item.get("venue"))
            annotated = {
                **item,
                "venue_type": normalized_type,
                "venue_tier": venue_tier_for_name(item.get("venue")),
            }
            if not _openalex_item_matches_filters(
                annotated,
                venue_tier=effective_tier,
                venue_type=effective_type,
                venue_names=effective_venue_names,
                from_year=effective_from_year,
            ):
                continue
            results.append(annotated)

    arxiv_requires_unsupported_filter = (
        effective_tier != "all"
        or effective_type != "all"
        or bool(effective_venue_names)
    )
    if effective_scope in {"hybrid", "arxiv"} and not arxiv_requires_unsupported_filter:
        arxiv_client = ArxivClient()
        if hasattr(arxiv_client, "fetch_latest"):
            arxiv_items = arxiv_client.fetch_latest(
                cleaned_query,
                max_results=min(fetch_limit, 50),
                sort_by=_resolve_arxiv_sort_mode(sort_mode),
                date_from=date_from,
                date_to=date_to,
                enrich_impact=(str(sort_mode or "").strip().lower() == "impact"),
            )
        else:  # pragma: no cover - compatibility for older fakes/extensions
            arxiv_items = arxiv_client.search_candidates(
                cleaned_query,
                max_results=min(fetch_limit, 50),
                fetch_limit=fetch_limit,
            )
        for paper in arxiv_items:
            annotated = _paper_to_external_result(paper)
            if effective_from_year is not None:
                try:
                    if int(annotated.get("publication_year") or 0) < effective_from_year:
                        continue
                except (TypeError, ValueError):
                    continue
            results.append(annotated)
    elif effective_scope in {"hybrid", "arxiv"}:
        skipped_sources.append("arxiv")

    deduped = _sort_external_results(_dedupe_literature_items(results), sort_mode)[:requested]
    for item in deduped:
        source_name = str(item.get("source") or "").strip().lower()
        if source_name in source_counts:
            source_counts[source_name] += 1

    filter_bits: list[str] = []
    if effective_tier != "all":
        filter_bits.append(f"tier={effective_tier}")
    if effective_type != "all":
        filter_bits.append(f"type={effective_type}")
    if effective_venue_names:
        filter_bits.append(f"venue={', '.join(effective_venue_names[:4])}")
    if effective_from_year is not None:
        filter_bits.append(f"from_year={effective_from_year}")
    if str(sort_mode or "").strip():
        filter_bits.append(f"sort={str(sort_mode).strip().lower()}")

    summary = f"外部文献检索完成，共找到 {len(deduped)} 篇结果"
    if filter_bits:
        summary += f"（过滤：{'；'.join(filter_bits)}）"
    if skipped_sources:
        summary += "；已跳过 arXiv venue 过滤不支持的结果"

    return ToolResult(
        success=True,
        data={
            "papers": deduped,
            "count": len(deduped),
            "query": cleaned_query,
            "source_scope": effective_scope,
            "source_counts": source_counts,
            "filters": {
                "venue_tier": effective_tier,
                "venue_type": effective_type,
                "venue_names": effective_venue_names,
                "from_year": effective_from_year,
            },
            "skipped_sources": skipped_sources,
        },
        summary=summary,
    )


def _preview_external_paper_head(arxiv_id: str) -> ToolResult:
    with ExternalPaperPreviewService() as service:
        payload = service.fetch_head(arxiv_id)
    section_count = int(payload.get("section_count") or 0)
    summary = f"已获取 arXiv:{payload['arxiv_id']} 的外部预读信息"
    if section_count > 0:
        summary += f"，包含 {section_count} 个章节标题"
    elif not payload.get("ar5iv_available"):
        summary += "，但暂未拿到章节目录"
    return ToolResult(success=True, data=payload, summary=summary)


def _preview_external_paper_section(arxiv_id: str, section_name: str) -> ToolResult:
    with ExternalPaperPreviewService() as service:
        payload = service.fetch_section(arxiv_id, section_name)
    matched = str(payload.get("matched_section") or section_name).strip()
    return ToolResult(
        success=True,
        data=payload,
        summary=f"已预读章节《{matched}》",
    )


def _ingest_external_literature(
    entries: list[dict],
    topic_id: str | None = None,
    query: str | None = None,
) -> ToolResult:
    normalized_entries = [dict(entry) for entry in (entries or []) if str((entry or {}).get("title") or "").strip()]
    if not normalized_entries:
        return ToolResult(success=False, summary="没有可导入的外部论文条目")
    result = PaperPipelines().ingest_external_entries(
        normalized_entries,
        topic_id=str(topic_id or "").strip() or None,
        query=str(query or "").strip() or None,
    )
    return ToolResult(
        success=True,
        data=result,
        summary=(
            f"已处理 {int(result.get('requested', 0) or 0)} 篇候选论文，"
            f"新增 {int(result.get('ingested', 0) or 0)} 篇，"
            f"重复 {int(result.get('duplicates', 0) or 0)} 篇"
        ),
    )


def _search_arxiv(query: str, max_results: int = 20) -> ToolResult:
    cleaned_query = query.strip()
    if not cleaned_query:
        return ToolResult(success=False, summary="arXiv 搜索关键词不能为空")

    papers = ArxivClient().search_candidates(
        cleaned_query,
        max_results=max(1, min(max_results, 50)),
    )
    items = [
        {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
            "categories": (paper.metadata or {}).get("categories", []),
            "authors": (paper.metadata or {}).get("authors", []),
        }
        for paper in papers
    ]
    return ToolResult(
        success=True,
        data={"candidates": items, "count": len(items), "query": cleaned_query},
        summary=f"arXiv 找到 {len(items)} 篇候选论文",
    )


def _ingest_arxiv(query: str, arxiv_ids: list[str]) -> ToolResult:
    ids = [str(item).strip() for item in arxiv_ids if str(item).strip()]
    if not ids:
        return ToolResult(success=False, summary="请先提供要导入的 arXiv id")
    result = PaperPipelines().ingest_arxiv_ids(ids)
    return ToolResult(
        success=True,
        data={"query": query, **result},
        summary=f"已处理 {result.get('requested', 0)} 个 arXiv id，成功导入 {result.get('ingested', 0)} 篇",
    )


def _skim_paper(paper_id: str) -> Iterator[ToolProgress | ToolResult]:
    pid, err = _require_paper_id(paper_id)
    if err:
        yield err
        return
    assert pid is not None

    def _runner(progress_callback: Callable[[str, int, int], None] | None) -> ToolResult:
        report = PaperPipelines().skim(pid, progress_callback=progress_callback)
        return ToolResult(
            success=True,
            data=report.model_dump(),
            summary=f"粗读完成：{report.one_liner[:120]}",
        )

    yield from _stream_background_call(_runner, start_message="正在启动粗读分析...")


def _deep_read_paper(paper_id: str) -> Iterator[ToolProgress | ToolResult]:
    pid, err = _require_paper_id(paper_id)
    if err:
        yield err
        return
    assert pid is not None

    def _runner(progress_callback: Callable[[str, int, int], None] | None) -> ToolResult:
        report = PaperPipelines().deep_dive(pid, progress_callback=progress_callback)
        return ToolResult(
            success=True,
            data=report.model_dump(),
            summary="精读分析完成",
        )

    yield from _stream_background_call(_runner, start_message="正在启动精读分析...")


def _analyze_paper_rounds(
    paper_id: str,
    detail_level: str = "medium",
    reasoning_level: str = "default",
) -> Iterator[ToolProgress | ToolResult]:
    pid, err = _require_paper_id(paper_id)
    if err:
        yield err
        return
    assert pid is not None

    def _runner(progress_callback: Callable[[str, int, int], None] | None) -> ToolResult:
        payload = PaperAnalysisService().analyze(
            pid,
            detail_level=detail_level,
            reasoning_level=reasoning_level,
            progress_callback=progress_callback,
        )
        bundle = dict(payload.get("analysis_rounds") or {})
        ok, summary = _analysis_rounds_is_effective(bundle)
        if ok:
            final_notes = bundle.get("final_notes") if isinstance(bundle, dict) else None
            summary = f"论文三轮分析完成：{str((final_notes or {}).get('title') or '最终结构化笔记')}"
        return ToolResult(
            success=ok,
            data=payload,
            summary=summary,
        )

    yield from _stream_background_call(_runner, start_message="正在启动论文三轮分析...")


def _embed_paper(paper_id: str) -> Iterator[ToolProgress | ToolResult]:
    pid, err = _require_paper_id(paper_id)
    if err:
        yield err
        return
    assert pid is not None

    def _runner(progress_callback: Callable[[str, int, int], None] | None) -> ToolResult:
        PaperPipelines().embed_paper(pid, progress_callback=progress_callback)
        return ToolResult(
            success=True,
            data={"paper_id": str(pid), "status": "embedded"},
            summary="向量嵌入完成",
        )

    yield from _stream_background_call(_runner, start_message="正在生成论文向量...")


def _generate_wiki(type: str, keyword_or_id: str) -> Iterator[ToolProgress | ToolResult]:
    mode = str(type).strip().lower()
    if mode not in {"topic", "paper"}:
        yield ToolResult(success=False, summary="type 必须是 topic 或 paper")
        return

    if mode == "topic":
        keyword = keyword_or_id.strip()
        if not keyword:
            yield ToolResult(success=False, summary="专题关键词不能为空")
            return

        def _runner(progress_callback: Callable[[str, int, int], None] | None) -> ToolResult:
            def _topic_progress(percent: float, message: str) -> None:
                if progress_callback:
                    progress_callback(message, int(max(1, min(99, percent * 100))), 100)

            result = GraphService().topic_wiki(keyword=keyword, limit=120, progress_callback=_topic_progress)
            result["title"] = result.get("title") or f"专题综述：{keyword}"
            return ToolResult(
                success=True,
                data=result,
                summary=f"专题综述已生成：{keyword}",
            )

        yield from _stream_background_call(_runner, start_message="正在生成专题综述...")
        return

    pid, err = _require_paper_id(keyword_or_id)
    if err:
        yield err
        return
    assert pid is not None
    yield ToolProgress(message="正在生成单篇论文综述...", current=10, total=100)
    result = GraphService().paper_wiki(str(pid))
    result["title"] = result.get("title") or "论文综述"
    yield ToolResult(success=True, data=result, summary="论文综述已生成")


def _generate_daily_brief(recipient: str = "") -> Iterator[ToolProgress | ToolResult]:
    recipient = recipient.strip()
    service = DailyBriefService()
    yield ToolProgress(message="正在生成研究简报...", current=15, total=100)
    html = service.build_html()
    yield ToolProgress(message="正在保存研究简报...", current=70, total=100)
    publish_result = service.publish(recipient or None)
    yield ToolResult(
        success=True,
        data={
            **publish_result,
            "title": "研究简报",
            "html": html,
        },
        summary="研究简报已生成并保存",
    )


def _research_wiki_init(
    project_id: str | None = None,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    resolved_project_id, err = _resolve_project_id(project_id, context=context)
    if err:
        return err
    assert resolved_project_id is not None
    payload = ResearchWikiService().initialize_project_wiki(resolved_project_id)
    stats = dict(payload.get("stats") or {})
    return ToolResult(
        success=True,
        data=payload,
        summary=(
            f"项目 research wiki 已初始化，"
            f"共 {int(stats.get('node_count', 0) or 0)} 个节点、"
            f"{int(stats.get('edge_count', 0) or 0)} 条边"
        ),
    )


def _research_wiki_stats(
    project_id: str | None = None,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    resolved_project_id, err = _resolve_project_id(project_id, context=context)
    if err:
        return err
    assert resolved_project_id is not None
    payload = ResearchWikiService().stats(resolved_project_id)
    return ToolResult(
        success=True,
        data=payload,
        summary=(
            f"当前 research wiki 包含 {int(payload.get('node_count', 0) or 0)} 个节点、"
            f"{int(payload.get('edge_count', 0) or 0)} 条边"
        ),
    )


def _research_wiki_query(
    project_id: str | None = None,
    query: str | None = None,
    limit: int = 5,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    resolved_project_id, err = _resolve_project_id(project_id, context=context)
    if err:
        return err
    assert resolved_project_id is not None
    payload = ResearchWikiService().build_query_pack(
        project_id=resolved_project_id,
        query=str(query or "").strip() or None,
        limit=max(1, min(int(limit or 5), 12)),
    )
    matched_count = len(payload.get("matched_nodes") or [])
    return ToolResult(
        success=True,
        data=payload,
        summary=f"已生成 research wiki 查询包，匹配 {matched_count} 个候选节点",
    )


def _research_wiki_update_node(
    project_id: str | None = None,
    node_id: str | None = None,
    node_key: str | None = None,
    node_type: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    body_md: str | None = None,
    status: str | None = None,
    source_paper_id: str | None = None,
    source_run_id: str | None = None,
    metadata: dict | None = None,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    resolved_project_id, err = _resolve_project_id(project_id, context=context)
    if err:
        return err
    assert resolved_project_id is not None
    payload = ResearchWikiService().update_node(
        project_id=resolved_project_id,
        node_id=node_id,
        node_key=node_key,
        node_type=node_type,
        title=title,
        summary=summary,
        body_md=body_md,
        status=status,
        source_paper_id=source_paper_id,
        source_run_id=source_run_id,
        metadata=dict(metadata or {}),
    )
    return ToolResult(
        success=True,
        data=payload,
        summary=f"research wiki 节点已更新：{payload.get('title') or payload.get('node_key')}",
    )


def _manage_subscription(
    topic_name: str,
    enabled: bool,
    schedule_frequency: str | None = None,
    schedule_time_beijing: int | None = None,
) -> ToolResult:
    with session_scope() as session:
        repo = TopicRepository(session)
        topic = repo.get_by_name(topic_name.strip())
        if topic is None:
            return ToolResult(success=False, summary=f"未找到主题：{topic_name}")

        topic.enabled = enabled
        if schedule_frequency:
            topic.schedule_frequency = schedule_frequency
        if schedule_time_beijing is not None:
            topic.schedule_time_utc = max(0, min(23, (int(schedule_time_beijing) - 8) % 24))
        session.flush()

        bj_hour = (topic.schedule_time_utc + 8) % 24
        summary = (
            f"{'已启用' if enabled else '已关闭'}订阅《{topic.name}》"
            f"，频率：{topic.schedule_frequency}，北京时间：{bj_hour:02d}:00"
        )
        data = {
            "topic_name": topic.name,
            "enabled": topic.enabled,
            "schedule_frequency": topic.schedule_frequency,
            "schedule_time_beijing": bj_hour,
        }
    return ToolResult(success=True, data=data, summary=summary)


def _suggest_keywords(
    description: str,
    source_scope: str = "hybrid",
    search_field: str = "all",
) -> ToolResult:
    suggestions = KeywordService().suggest(
        description.strip(),
        source_scope=source_scope,
        search_field=search_field,
    )
    return ToolResult(
        success=True,
        data={
            "suggestions": suggestions,
            "count": len(suggestions),
            "source_scope": _normalize_source_scope(source_scope),
            "search_field": str(search_field or "all").strip().lower() or "all",
        },
        summary=f"已生成 {len(suggestions)} 组关键词建议",
    )


def _reasoning_analysis(paper_id: str) -> Iterator[ToolProgress | ToolResult]:
    pid, err = _require_paper_id(paper_id)
    if err:
        yield err
        return
    assert pid is not None

    def _runner(progress_callback: Callable[[str, int, int], None] | None) -> ToolResult:
        result = ReasoningService().analyze(pid, progress_callback=progress_callback)
        return ToolResult(
            success=True,
            data=result,
            summary="推理链分析完成",
        )

    yield from _stream_background_call(_runner, start_message="正在启动推理链分析...")


def _identify_research_gaps(keyword: str, limit: int = 120) -> ToolResult:
    result = GraphService().detect_research_gaps(keyword.strip(), limit=max(10, min(limit, 200)))
    analysis = result.get("analysis") or {}
    gaps = analysis.get("research_gaps") or []
    summary = analysis.get("overall_summary") or f"已完成 {keyword} 的研究空白分析"
    return ToolResult(
        success=True,
        data=result,
        summary=f"{summary[:120]}（共 {len(gaps)} 个研究空白点）",
    )


def _writing_assist(action: str, text: str) -> ToolResult:
    result = WritingService().process(action=action, text=text)
    content = str(result.get("content") or "")
    return ToolResult(
        success=True,
        data={**result, "markdown": content, "title": result.get("label") or "写作助手结果"},
        summary=f"{result.get('label') or '写作处理'}已完成",
    )


def _analyze_figures(paper_id: str, max_figures: int = 10) -> Iterator[ToolProgress | ToolResult]:
    pid, err = _require_paper_id(paper_id)
    if err:
        yield err
        return
    assert pid is not None

    with session_scope() as session:
        paper = PaperRepository(session).get_by_id(pid)
        pdf_path = paper.pdf_path
        arxiv_id = paper.arxiv_id
        title = paper.title

    if not pdf_path:
        yield ToolResult(success=False, summary="这篇论文还没有 PDF 文件，无法提取图表")
        return

    service = FigureService()
    yield ToolProgress(message="正在提取图片与图表候选...", current=15, total=100)
    candidates = service.extract_paper_figure_candidates(
        paper_id=pid,
        pdf_path=pdf_path,
        max_figures=max(1, min(max_figures, 40)),
        arxiv_id=arxiv_id,
    )
    if not candidates:
        yield ToolResult(success=False, summary="没有提取到可分析的图表候选")
        return

    yield ToolProgress(message="正在分析图表内容...", current=55, total=100)
    service.analyze_paper_figures(
        paper_id=pid,
        pdf_path=pdf_path,
        max_figures=max(1, min(max_figures, 40)),
        arxiv_id=arxiv_id,
    )
    figure_items = _paper_figure_items(pid, limit=None)
    analyzed_count = sum(
        1 for item in figure_items if str(item.get("description") or "").strip()
    )

    markdown_lines = [f"# 图表分析：{title}", ""]
    for index, item in enumerate(figure_items, start=1):
        markdown_lines.extend(
            [
                f"## 图表 {index}",
                f"- 页码：{item.get('page_number')}",
                f"- 类型：{item.get('image_type') or 'figure'}",
                f"- 标题：{item.get('caption') or '未提供'}",
                "",
                str(item.get("description") or "").strip() or "暂无分析结果",
                "",
            ]
        )

    success = analyzed_count > 0
    if success:
        summary = f"已完成 {analyzed_count} / {len(figure_items)} 个图表分析"
    else:
        summary = "图表候选已提取，但当前没有生成可用的图表分析结果"

    yield ToolResult(
        success=success,
        data={
            "paper_id": str(pid),
            "items": figure_items,
            "count": len(figure_items),
            "analyzed_count": analyzed_count,
            "title": f"图表分析：{title}",
            "markdown": "\n".join(markdown_lines),
            "figure_refs": _paper_figure_refs(figure_items),
        },
        summary=summary,
        internal_data={"display_data": {"figures": figure_items}},
    )
