"""主题订阅 & 论文摄入路由
"""

import logging
import uuid as _uuid
from datetime import date, datetime, timedelta
from typing import Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from apps.api.deps import cache, pipelines
from packages.agent import research_tool_runtime
from packages.domain.exceptions import NotFoundError
from packages.domain.enums import ActionType
from packages.domain.schemas import (
    ArxivIdIngestReq,
    ExternalLiteratureIngestReq,
    ExternalLiteratureSearchReq,
    PaperAutoClassifyReq,
    ReferenceImportReq,
    SuggestKeywordsReq,
    TopicCreate,
    TopicUpdate,
)
from packages.domain.task_tracker import global_tracker
from packages.storage.db import session_scope
from packages.storage.repository_facades import TopicDataFacade

logger = logging.getLogger(__name__)

router = APIRouter()
ARXIV_SORT_PATTERN = "^(submittedDate|relevance|lastUpdatedDate|impact)$"
TOPIC_SOURCE_PATTERN = "^(arxiv|openalex|manual|hybrid)$"
TOPIC_SEARCH_FIELD_PATTERN = "^(all|title|keywords|authors|arxiv_id)$"
TOPIC_PRIORITY_PATTERN = "^(relevance|time|impact)$"


def _invalidate_topic_related_cache() -> None:
    cache.invalidate("folder_stats")
    cache.invalidate_prefix("dashboard_home_")
    cache.invalidate_prefix("graph_")

def _topic_data(session):
    return TopicDataFacade.from_session(session)



def _resolve_topic_priority_sort(topic) -> str:
    priority_mode = str(getattr(topic, "priority_mode", "") or "").strip().lower()
    if priority_mode == "impact":
        return "impact"
    if priority_mode == "relevance":
        return "relevance"
    if priority_mode == "time":
        return "submittedDate"
    return str(getattr(topic, "sort_by", "submittedDate") or "submittedDate")


def _resolve_topic_date_range(topic) -> tuple[date | None, date | None]:
    start = getattr(topic, "date_filter_start", None)
    end = getattr(topic, "date_filter_end", None)
    if start is not None or end is not None:
        return start, end
    if getattr(topic, "enable_date_filter", False):
        from packages.timezone import user_now

        end = user_now().date()
        start = end - timedelta(days=max(1, getattr(topic, "date_filter_days", 7)) - 1)
        return start, end
    return None, None


def _validate_date_range(
    *,
    enable_date_filter: bool | None,
    date_filter_start: date | None,
    date_filter_end: date | None,
) -> None:
    if date_filter_start is not None or date_filter_end is not None or enable_date_filter:
        if (date_filter_start is None) != (date_filter_end is None):
            raise HTTPException(status_code=400, detail="date filter requires both start and end dates")
        if date_filter_start and date_filter_end and date_filter_start > date_filter_end:
            raise HTTPException(status_code=400, detail="date filter start must be on or before end")


def _parse_publication_date(value: str | None, publication_year: int | None = None) -> date | None:
    raw = str(value or "").strip()
    if raw:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if fmt == "%Y-%m":
                    return date(parsed.year, parsed.month, 1)
                if fmt == "%Y":
                    return date(parsed.year, 1, 1)
                return parsed.date()
            except ValueError:
                continue
    if publication_year is not None:
        try:
            return date(int(publication_year), 1, 1)
        except (TypeError, ValueError):
            return None
    return None


def _matches_publication_window(item: dict, date_from: date | None, date_to: date | None) -> bool:
    if date_from is None and date_to is None:
        return True
    publication_date = _parse_publication_date(
        item.get("publication_date"),
        item.get("publication_year"),
    )
    if publication_date is None:
        return False
    if date_from is not None and publication_date < date_from:
        return False
    if date_to is not None and publication_date > date_to:
        return False
    return True


def _sort_external_papers(papers: list[dict], sort_mode: str) -> list[dict]:
    normalized = str(sort_mode or "relevance").strip().lower()
    if normalized == "impact":
        return sorted(
            papers,
            key=lambda item: (
                int(item.get("citation_count") or 0),
                _parse_publication_date(item.get("publication_date"), item.get("publication_year")) or date(1900, 1, 1),
            ),
            reverse=True,
        )
    if normalized == "time":
        return sorted(
            papers,
            key=lambda item: _parse_publication_date(item.get("publication_date"), item.get("publication_year")) or date(1900, 1, 1),
            reverse=True,
        )
    return papers


def _topic_dict(t, session=None) -> dict:
    date_filter_start, date_filter_end = _resolve_topic_date_range(t)
    d = {
        "id": t.id,
        "name": t.name,
        "query": t.query,
        "kind": getattr(t, "kind", "subscription"),
        "sort_by": getattr(t, "sort_by", "submittedDate"),
        "source": getattr(t, "source", "arxiv"),
        "search_field": getattr(t, "search_field", "all"),
        "priority_mode": getattr(t, "priority_mode", "time"),
        "venue_tier": getattr(t, "venue_tier", "all"),
        "venue_type": getattr(t, "venue_type", "all"),
        "venue_names": list(getattr(t, "venue_names_json", []) or []),
        "from_year": getattr(t, "from_year", None),
        "default_folder_id": getattr(t, "default_folder_id", None),
        "enabled": t.enabled,
        "max_results_per_run": t.max_results_per_run,
        "retry_limit": t.retry_limit,
        "schedule_frequency": getattr(t, "schedule_frequency", "daily"),
        "schedule_time_utc": getattr(t, "schedule_time_utc", 21),
        "enable_date_filter": getattr(t, "enable_date_filter", False),
        "date_filter_days": getattr(t, "date_filter_days", 7),
        "date_filter_start": date_filter_start.isoformat() if date_filter_start else None,
        "date_filter_end": date_filter_end.isoformat() if date_filter_end else None,
        "paper_count": 0,
        "last_run_at": None,
        "last_run_status": getattr(t, "last_run_status", None),
        "last_run_count": None,
        "last_run_error": getattr(t, "last_run_error", None),
    }
    if session is not None:
        from sqlalchemy import func, select
        from packages.storage.models import PaperTopic, CollectionAction

    # 论文计数
        cnt = session.scalar(
            select(func.count()).select_from(PaperTopic).where(PaperTopic.topic_id == t.id)
        )
        d["paper_count"] = cnt or 0
        last_run_at = getattr(t, "last_run_at", None)
        if last_run_at:
            d["last_run_at"] = last_run_at.isoformat()
            d["last_run_count"] = getattr(t, "last_run_count", None)
        else:
            last_action = session.execute(
                select(CollectionAction)
                .where(CollectionAction.topic_id == t.id)
                .order_by(CollectionAction.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if last_action:
                d["last_run_at"] = (
                    last_action.created_at.isoformat() if last_action.created_at else None
                )
                d["last_run_status"] = "ok"
                d["last_run_count"] = last_action.paper_count
    return d


@router.get("/topics")
def list_topics(
    enabled_only: bool = False,
    kind: str | None = Query(default=None, pattern="^(subscription|folder)$"),
) -> dict:
    with session_scope() as session:
        topics = _topic_data(session).topics.list_topics(enabled_only=enabled_only, kind=kind)
        return {"items": [_topic_dict(t, session) for t in topics]}


@router.post("/topics")
def upsert_topic(req: TopicCreate) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="topic name is required")
    kind = (req.kind or "subscription").strip().lower()
    query = (req.query or "").strip()
    if kind == "subscription" and not query:
        raise HTTPException(status_code=400, detail="subscription query is required")
    if req.source not in {"arxiv", "openalex", "manual", "hybrid"}:
        raise HTTPException(status_code=400, detail="invalid topic source")
    if req.search_field not in {"all", "title", "keywords", "authors", "arxiv_id"}:
        raise HTTPException(status_code=400, detail="invalid topic search field")
    if req.priority_mode not in {"relevance", "time", "impact"}:
        raise HTTPException(status_code=400, detail="invalid topic priority mode")
    _validate_date_range(
        enable_date_filter=req.enable_date_filter,
        date_filter_start=req.date_filter_start,
        date_filter_end=req.date_filter_end,
    )
    with session_scope() as session:
        topic = _topic_data(session).topics.upsert_topic(
            name=name,
            kind=kind,
            query=query,
            sort_by=req.sort_by,
            source=req.source,
            search_field=req.search_field,
            priority_mode=req.priority_mode,
            venue_tier=req.venue_tier,
            venue_type=req.venue_type,
            venue_names=req.venue_names,
            from_year=req.from_year,
            default_folder_id=req.default_folder_id,
            enabled=req.enabled,
            max_results_per_run=req.max_results_per_run,
            retry_limit=req.retry_limit,
            schedule_frequency=req.schedule_frequency,
            schedule_time_utc=req.schedule_time_utc,
            enable_date_filter=req.enable_date_filter,
            date_filter_days=req.date_filter_days,
            date_filter_start=req.date_filter_start,
            date_filter_end=req.date_filter_end,
        )
        _invalidate_topic_related_cache()
        return _topic_dict(topic, session)


@router.post("/topics/suggest-keywords")
def suggest_keywords(req: SuggestKeywordsReq) -> dict:
    from packages.ai.research.keyword_service import KeywordService

    description = req.description
    if not description.strip():
        raise HTTPException(400, "description is required")
    suggestions = KeywordService().suggest(
        description.strip(),
        source_scope=req.source_scope,
        search_field=req.search_field,
    )
    return {"suggestions": suggestions}


@router.patch("/topics/{topic_id}")
def update_topic(topic_id: str, req: TopicUpdate) -> dict:
    update_payload = req.model_dump(exclude_unset=True)
    name = req.name.strip() if req.name is not None else None
    if name is not None and not name:
        raise HTTPException(status_code=400, detail="topic name is required")
    kind = req.kind.strip().lower() if req.kind else None
    query = req.query.strip() if req.query is not None else None
    if kind == "subscription" and query is not None and not query:
        raise HTTPException(status_code=400, detail="subscription query is required")
    if req.source is not None and req.source not in {"arxiv", "openalex", "manual", "hybrid"}:
        raise HTTPException(status_code=400, detail="invalid topic source")
    if req.search_field is not None and req.search_field not in {"all", "title", "keywords", "authors", "arxiv_id"}:
        raise HTTPException(status_code=400, detail="invalid topic search field")
    if req.priority_mode is not None and req.priority_mode not in {"relevance", "time", "impact"}:
        raise HTTPException(status_code=400, detail="invalid topic priority mode")
    _validate_date_range(
        enable_date_filter=req.enable_date_filter,
        date_filter_start=req.date_filter_start,
        date_filter_end=req.date_filter_end,
    )
    with session_scope() as session:
        try:
            update_kwargs = {
                "name": name,
                "kind": kind,
                "query": query,
                "sort_by": req.sort_by,
                "source": req.source,
                "search_field": req.search_field,
                "priority_mode": req.priority_mode,
                "enabled": req.enabled,
                "max_results_per_run": req.max_results_per_run,
                "retry_limit": req.retry_limit,
                "schedule_frequency": req.schedule_frequency,
                "schedule_time_utc": req.schedule_time_utc,
                "enable_date_filter": req.enable_date_filter,
                "date_filter_days": req.date_filter_days,
                "date_filter_start": req.date_filter_start,
                "date_filter_end": req.date_filter_end,
            }
            if "venue_tier" in update_payload:
                update_kwargs["venue_tier"] = update_payload.get("venue_tier")
            if "venue_type" in update_payload:
                update_kwargs["venue_type"] = update_payload.get("venue_type")
            if "venue_names" in update_payload:
                update_kwargs["venue_names"] = update_payload.get("venue_names")
            if "from_year" in update_payload:
                update_kwargs["from_year"] = update_payload.get("from_year")
            if "default_folder_id" in update_payload:
                update_kwargs["default_folder_id"] = update_payload.get("default_folder_id")

            topic = _topic_data(session).topics.update_topic(
                topic_id,
                **update_kwargs,
            )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="topic name already exists") from exc
        except ValueError as exc:
            raise NotFoundError(str(exc)) from exc
        _invalidate_topic_related_cache()
        return _topic_dict(topic, session)


@router.delete("/topics/{topic_id}")
def delete_topic(topic_id: str) -> dict:
    with session_scope() as session:
        _topic_data(session).topics.delete_topic(topic_id)
        _invalidate_topic_related_cache()
        return {"deleted": topic_id}


@router.post("/topics/{topic_id}/fetch")
def manual_fetch_topic(topic_id: str) -> dict:
    """手动触发单个订阅的论文抓取（后台执行，立即返回）"""
    from packages.ai.ops.daily_runner import run_topic_ingest
    from packages.storage.models import TopicSubscription

    with session_scope() as session:
        topic = session.get(TopicSubscription, topic_id)
        if not topic:
            raise NotFoundError("订阅不存在")
        if getattr(topic, "kind", "subscription") != "subscription":
            raise HTTPException(status_code=400, detail="该条目是手动文件夹，不能执行自动抓取")
        topic_name = topic.name

    task_prefix = f"fetch_{topic_id[:8]}_"
    for task in global_tracker.get_active():
        if (
            task.get("task_type") == "fetch"
            and str(task.get("task_id", "")).startswith(task_prefix)
            and not task.get("finished")
        ):
            return {
                "status": "already_running",
                "task_id": task["task_id"],
                "topic_id": topic_id,
                "topic_name": topic_name,
                "message": f"《{topic_name}》抓取任务已在运行",
            }

    def _fetch_fn(progress_callback=None):
        return run_topic_ingest(topic_id, progress_callback=progress_callback)

    task_id = global_tracker.submit(
        task_type="fetch",
        title=f"抓取: {topic_name[:30]}",
        fn=_fetch_fn,
        task_id=f"{task_prefix}{_uuid.uuid4().hex[:8]}",
    )
    return {
        "status": "started",
        "task_id": task_id,
        "topic_id": topic_id,
        "topic_name": topic_name,
        "message": f"《{topic_name}》抓取已在后台启动",
    }


@router.get("/topics/{topic_id}/fetch-status")
def fetch_topic_status(topic_id: str) -> dict:
    """查询手动抓取任务状态。"""
    task_prefix = f"fetch_{topic_id[:8]}_"
    for task in global_tracker.get_active():
        if task.get("task_type") == "fetch" and str(task.get("task_id", "")).startswith(task_prefix):
            if not task["finished"]:
                return {"status": "running", **task}
            result = global_tracker.get_result(task["task_id"]) or {}
            if isinstance(result, dict) and result:
                resolved_status = result.get("status") or ("ok" if task["success"] else "failed")
                return {"status": resolved_status, **task, **result}
            return {"status": "ok" if task["success"] else "failed", **task}

    with session_scope() as session:
        from packages.storage.models import TopicSubscription

        topic = session.get(TopicSubscription, topic_id)
        topic_info = _topic_dict(topic, session) if topic else {}
    return {"topic": topic_info}


# ---------- 摄入 ----------


@router.post("/ingest/literature/search")
def search_external_literature(body: ExternalLiteratureSearchReq) -> dict:
    if body.date_from is not None and body.date_to is not None and body.date_from > body.date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")

    result = research_tool_runtime._search_literature(
        body.query,
        max_results=body.max_results,
        source_scope=body.source_scope,
        venue_tier=body.venue_tier,
        venue_type=body.venue_type,
        venue_names=body.venue_names,
        from_year=body.from_year,
        sort_mode=body.sort_mode,
        date_from=body.date_from,
        date_to=body.date_to,
    )
    if not result.success:
        raise HTTPException(status_code=400, detail=result.summary)

    payload = dict(result.data or {})
    papers = list(payload.get("papers") or [])
    papers = [
        item
        for item in papers
        if _matches_publication_window(item, body.date_from, body.date_to)
    ]
    papers = _sort_external_papers(papers, body.sort_mode)
    source_counts = {"openalex": 0, "arxiv": 0}
    for item in papers:
        source_name = str(item.get("source") or "").strip().lower()
        if source_name in source_counts:
            source_counts[source_name] += 1

    payload["papers"] = papers
    payload["count"] = len(papers)
    payload["source_counts"] = source_counts
    payload["sort_mode"] = body.sort_mode
    payload["summary"] = result.summary
    return payload


@router.post("/ingest/literature")
def ingest_external_literature(body: ExternalLiteratureIngestReq) -> dict:
    result = pipelines.ingest_external_entries(
        entries=[entry.model_dump() for entry in body.entries],
        topic_id=body.topic_id,
        action_type=ActionType.manual_collect,
    )

    auto_classified = 0
    inserted_ids = [str(paper.get("id")) for paper in result.get("papers", []) if paper.get("id")]
    if inserted_ids and not body.topic_id:
        try:
            from packages.ai.paper.classification_service import PaperClassificationService

            classify_result = PaperClassificationService().auto_classify(
                PaperAutoClassifyReq(
                    paper_ids=inserted_ids,
                    only_unclassified=False,
                    max_papers=len(inserted_ids),
                    max_topics_per_paper=2,
                    min_score=1.2,
                    use_graph=True,
                )
            )
            auto_classified = int(classify_result.get("classified_papers", 0))
        except Exception as exc:
            logger.warning("Auto classify after external ingest failed: %s", exc)

    return {
        **result,
        "classified": auto_classified,
    }


@router.post("/ingest/arxiv")
def ingest_arxiv(
    query: str,
    max_results: int = Query(default=20, ge=1, le=200),
    topic_id: str | None = None,
    days_back: int | None = Query(default=None, ge=1, le=3650),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    sort_by: str = Query(
        default="submittedDate", pattern=ARXIV_SORT_PATTERN
    ),
) -> dict:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")
    logger.info(
        "ArXiv ingest: query=%r max_results=%d sort=%s days_back=%s date_from=%s date_to=%s",
        query,
        max_results,
        sort_by,
        days_back,
        date_from,
        date_to,
    )
    count, inserted_ids, _ = pipelines.ingest_arxiv(
        query=query,
        max_results=max_results,
        topic_id=topic_id,
        sort_by=sort_by,
        days_back=days_back,
        date_from=date_from,
        date_to=date_to,
    )
    auto_classified = 0
    if inserted_ids and not topic_id:
        try:
            from packages.ai.paper.classification_service import PaperClassificationService

            classify_result = PaperClassificationService().auto_classify(
                PaperAutoClassifyReq(
                    paper_ids=inserted_ids,
                    only_unclassified=False,
                    max_papers=len(inserted_ids),
                    max_topics_per_paper=2,
                    min_score=1.2,
                    use_graph=True,
                )
            )
            auto_classified = int(classify_result.get("classified_papers", 0))
        except Exception as exc:
            logger.warning("Auto classify after ingest failed: %s", exc)
    # 查询插入论文的基本信息
    papers_info: list[dict] = []
    if inserted_ids:
        with session_scope() as session:
            repo = _topic_data(session).papers
            for pid in inserted_ids[:50]:
                try:
                    p = repo.get_by_id(UUID(pid))
                    papers_info.append(
                        {
                            "id": p.id,
                            "title": p.title,
                            "arxiv_id": p.arxiv_id,
                            "publication_date": p.publication_date.isoformat()
                            if p.publication_date
                            else None,
                        }
                    )
                except Exception:
                    pass
    return {"ingested": count, "classified": auto_classified, "papers": papers_info}


@router.post("/ingest/arxiv-ids")
def ingest_arxiv_ids(body: ArxivIdIngestReq) -> dict:
    return _ingest_arxiv_ids_result(body)


def _ingest_arxiv_ids_result(
    body: ArxivIdIngestReq,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict:
    def _progress(message: str, current: int, total: int = 100) -> None:
        if progress_callback is not None:
            progress_callback(message, current, total)

    _progress("准备 arXiv ID 导入...", 5, 100)
    _progress("正在查询 arXiv 元数据并检查重复...", 20, 100)
    result = pipelines.ingest_arxiv_ids(
        arxiv_ids=body.arxiv_ids,
        topic_id=body.topic_id,
        action_type=ActionType.manual_collect,
        download_pdf=body.download_pdf,
    )

    auto_classified = 0
    inserted_ids = [str(paper.get("id")) for paper in result.get("papers", []) if paper.get("id")]
    if inserted_ids and not body.topic_id:
        try:
            _progress("正在自动归类新导入论文...", 80, 100)
            from packages.ai.paper.classification_service import PaperClassificationService

            classify_result = PaperClassificationService().auto_classify(
                PaperAutoClassifyReq(
                    paper_ids=inserted_ids,
                    only_unclassified=False,
                    max_papers=len(inserted_ids),
                    max_topics_per_paper=2,
                    min_score=1.2,
                    use_graph=True,
                )
            )
            auto_classified = int(classify_result.get("classified_papers", 0))
        except Exception as exc:
            logger.warning("Auto classify after arXiv ID ingest failed: %s", exc)

    _progress("整理导入结果...", 95, 100)
    return {
        **result,
        "classified": auto_classified,
    }


@router.post("/ingest/arxiv-ids-async")
def ingest_arxiv_ids_async(body: ArxivIdIngestReq) -> dict:
    preview_ids = [str(arxiv_id or "").strip() for arxiv_id in body.arxiv_ids if str(arxiv_id or "").strip()]
    preview = ", ".join(preview_ids[:3])
    if len(preview_ids) > 3:
        preview += "..."
    title_suffix = preview or "未命名导入"
    task_id = global_tracker.submit(
        task_type="ingest_arxiv_ids",
        title=f"按 arXiv ID 导入: {title_suffix[:48]}",
        fn=lambda progress_callback=None: _ingest_arxiv_ids_result(
            body,
            progress_callback=progress_callback,
        ),
        total=100,
        metadata={"source": "ingest", "topic_id": body.topic_id or "", "download_pdf": bool(body.download_pdf)},
    )
    return {
        "task_id": task_id,
        "status": "running",
        "message": "arXiv ID 导入任务已启动",
    }


@router.post("/ingest/references")
def ingest_references(body: ReferenceImportReq) -> dict:
    """Import references asynchronously and return a task id."""
    from packages.ai.paper.reference_importer import ReferenceImporter

    importer = ReferenceImporter()
    task_id = importer.start_import(
        source_paper_id=body.source_paper_id,
        source_paper_title=body.source_paper_title,
        entries=[dict(e) for e in body.entries],
        topic_ids=body.topic_ids,
    )
    return {"task_id": task_id, "total": len(body.entries)}


@router.get("/ingest/references/status/{task_id}")
def ingest_references_status(task_id: str) -> dict:
    """Get reference import task status."""
    from packages.ai.paper.reference_importer import get_import_task

    task = get_import_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task
