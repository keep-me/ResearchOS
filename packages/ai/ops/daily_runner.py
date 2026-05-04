"""
每日/每周定时任务编排 - 智能调度 + 精读限额
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from packages.agent import research_tool_runtime
from packages.ai.ops.rate_limiter import acquire_api
from packages.ai.paper.pipelines import PaperPipelines
from packages.ai.research.arxiv_trend_service import ArxivTrendService
from packages.ai.research.brief_service import DailyBriefService
from packages.ai.research.graph_service import GraphService
from packages.config import get_settings
from packages.domain.enums import ActionType
from packages.storage.db import session_scope
from packages.storage.models import TopicSubscription
from packages.storage.repositories import (
    PaperRepository,
    TopicRepository,
)

logger = logging.getLogger(__name__)


PAPER_CONCURRENCY = 3


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "too many requests" in message or "rate limit" in message


def _mark_topic_run(
    topic: TopicSubscription,
    *,
    status: str,
    count: int = 0,
    error: str | None = None,
) -> None:
    topic.last_run_at = datetime.now(UTC)
    topic.last_run_status = status
    topic.last_run_count = max(0, int(count or 0))
    topic.last_run_error = str(error or "")[:2000] if error else None


def _resolve_topic_priority_sort(topic: TopicSubscription) -> str:
    priority_mode = str(getattr(topic, "priority_mode", "") or "").strip().lower()
    if priority_mode == "impact":
        return "impact"
    if priority_mode == "relevance":
        return "relevance"
    if priority_mode == "time":
        return "submittedDate"
    return str(getattr(topic, "sort_by", "submittedDate") or "submittedDate")


def _topic_date_range(topic: TopicSubscription) -> tuple[date | None, date | None]:
    if not getattr(topic, "enable_date_filter", False):
        return None, None

    date_from = getattr(topic, "date_filter_start", None)
    date_to = getattr(topic, "date_filter_end", None)
    if date_from is not None and date_to is not None:
        return date_from, date_to

    from packages.timezone import user_now

    date_to = user_now().date()
    days = max(1, getattr(topic, "date_filter_days", 7))
    date_from = date_to - timedelta(days=days - 1)
    return date_from, date_to


def _matches_external_publication_window(
    item: dict,
    date_from: date | None,
    date_to: date | None,
) -> bool:
    if date_from is None and date_to is None:
        return True

    publication_date_raw = str(item.get("publication_date") or "").strip()
    publication_date_value: date | None = None
    if publication_date_raw:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                parsed = datetime.strptime(publication_date_raw, fmt)
                if fmt == "%Y-%m":
                    publication_date_value = date(parsed.year, parsed.month, 1)
                elif fmt == "%Y":
                    publication_date_value = date(parsed.year, 1, 1)
                else:
                    publication_date_value = parsed.date()
                break
            except ValueError:
                continue
    if publication_date_value is None and item.get("publication_year") is not None:
        try:
            publication_date_value = date(int(item["publication_year"]), 1, 1)
        except (TypeError, ValueError):
            publication_date_value = None
    if publication_date_value is None:
        return False
    if date_from is not None and publication_date_value < date_from:
        return False
    if date_to is not None and publication_date_value > date_to:
        return False
    return True


def _resolve_external_sort_mode(topic: TopicSubscription) -> str:
    priority_mode = str(getattr(topic, "priority_mode", "") or "").strip().lower()
    if priority_mode in {"relevance", "impact", "time"}:
        return priority_mode
    sort_by = str(getattr(topic, "sort_by", "") or "").strip()
    if sort_by == "submittedDate":
        return "time"
    if sort_by == "impact":
        return "impact"
    return "relevance"


def _process_paper(paper_id, force_deep: bool = False, deep_read_quota: int | None = None) -> dict:
    """
    单篇论文：embed + skim 并行，智能精读

    Args:
    paper_id: 论文 ID
    force_deep: 是否强制精读（忽略配额）
    deep_read_quota: 剩余精读配额（None 表示不限额）

    Returns:
        dict: 澶勭悊缁撴灉 {skim_score, deep_read, success}
    """
    settings = get_settings()
    pipelines = PaperPipelines()
    result = {
        "paper_id": str(paper_id)[:8],
        "skim_score": None,
        "deep_read": False,
        "success": False,
        "error": None,
    }

    skim_result = None
    with ThreadPoolExecutor(max_workers=2) as inner:
        fe = inner.submit(pipelines.embed_paper, paper_id)
        fs = inner.submit(pipelines.skim, paper_id)
        for fut in as_completed([fe, fs]):
            try:
                r = fut.result()
                if fut is fs:
                    skim_result = r
            except Exception as exc:
                label = "embed" if fut is fe else "skim"
                logger.warning(
                    "%s %s failed: %s",
                    label,
                    str(paper_id)[:8],
                    exc,
                )
                result["error"] = f"{label}: {exc}"

    # 检查粗读结果
    if skim_result and skim_result.relevance_score is not None:
        result["skim_score"] = skim_result.relevance_score
        result["success"] = True

    # 判断是否精读
    should_deep = False
    deep_reason = ""

    if force_deep:
        should_deep = True
        deep_reason = "强制精读"
    elif skim_result and skim_result.relevance_score >= settings.skim_score_threshold:
        # 检查精读配额
        if deep_read_quota is None or deep_read_quota > 0:
            should_deep = True
            deep_reason = f"高分论文 (分数={skim_result.relevance_score:.2f})"
        else:
            deep_reason = "精读配额已用尽"

    # 执行精读
    if should_deep:
        try:
            # 获取 API 许可
            if acquire_api("llm", timeout=30.0):
                pipelines.deep_dive(UUID(paper_id))
                result["deep_read"] = True
                logger.info("%s 精读完成 - %s", str(paper_id)[:8], deep_reason)
            else:
                logger.warning("skip deep read for %s due to llm permit timeout", str(paper_id)[:8])
        except Exception as exc:
            logger.warning(
                "deep_dive %s failed: %s",
                str(paper_id)[:8],
                exc,
            )
            result["error"] = f"deep: {exc}"

    return result


def run_topic_ingest(
    topic_id: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict:
    """
    Run one topic fetch + processing workflow with progress reporting.
    """
    pipelines = PaperPipelines()
    total_progress = 100

    def report(message: str, current: int) -> None:
        if progress_callback:
            progress_callback(message, max(0, min(current, total_progress)), total_progress)

    with session_scope() as session:
        topic = session.get(TopicSubscription, topic_id)
        if not topic:
            return {"topic_id": topic_id, "status": "not_found"}
        topic_name = topic.name
        report(f"正在抓取 {topic_name}", 5)

        max_deep_reads = getattr(topic, "max_deep_reads_per_run", 2)

        last_error: str | None = None
        ids: list[str] = []
        new_count = 0
        attempts = 0
        for _attempt in range(topic.retry_limit + 1):
            attempts += 1
            try:
                date_from, date_to = _topic_date_range(topic)
                source_scope = str(getattr(topic, "source", "arxiv") or "arxiv").strip().lower()
                venue_tier = (
                    str(getattr(topic, "venue_tier", "all") or "all").strip().lower() or "all"
                )
                venue_type = (
                    str(getattr(topic, "venue_type", "all") or "all").strip().lower() or "all"
                )
                venue_names = [
                    str(item).strip()
                    for item in (getattr(topic, "venue_names_json", []) or [])
                    if str(item).strip()
                ]
                stored_from_year = getattr(topic, "from_year", None)
                effective_from_year = date_from.year if date_from is not None else None
                if stored_from_year is not None:
                    try:
                        normalized_stored_year = int(stored_from_year)
                        effective_from_year = (
                            max(effective_from_year, normalized_stored_year)
                            if effective_from_year is not None
                            else normalized_stored_year
                        )
                    except (TypeError, ValueError):
                        pass
                if source_scope not in {"arxiv", "openalex", "hybrid"}:
                    source_scope = "arxiv"
                report(
                    f"正在请求 {source_scope}（第 {attempts}/{topic.retry_limit + 1} 次）",
                    10,
                )
                if source_scope == "arxiv":
                    result = pipelines.ingest_arxiv_with_stats(
                        query=topic.query,
                        max_results=topic.max_results_per_run,
                        topic_id=topic.id,
                        action_type=ActionType.auto_collect,
                        sort_by=_resolve_topic_priority_sort(topic),
                        date_from=date_from,
                        date_to=date_to,
                    )
                else:
                    search_result = research_tool_runtime._search_literature(
                        topic.query,
                        max_results=topic.max_results_per_run,
                        source_scope=source_scope,
                        venue_tier=venue_tier,
                        venue_type=venue_type,
                        venue_names=venue_names,
                        from_year=effective_from_year,
                        sort_mode=_resolve_external_sort_mode(topic),
                        date_from=date_from,
                        date_to=date_to,
                    )
                    if not search_result.success:
                        raise RuntimeError(search_result.summary)
                    external_entries = [
                        item
                        for item in (search_result.data or {}).get("papers", [])
                        if _matches_external_publication_window(item, date_from, date_to)
                    ]
                    ingest_result = pipelines.ingest_external_entries(
                        external_entries,
                        topic_id=topic.id,
                        action_type=ActionType.auto_collect,
                        query=topic.query,
                    )
                    result = {
                        "total_count": int(ingest_result.get("requested", 0)),
                        "inserted_ids": [
                            str(item.get("id"))
                            for item in (ingest_result.get("papers") or [])
                            if item.get("id")
                        ],
                        "new_count": int(ingest_result.get("ingested", 0)),
                    }
                ids = result["inserted_ids"]
                new_count = result["new_count"]
                last_error = None
                report(f"抓取完成，新增 {new_count} 篇论文", 35)
                break
            except Exception as exc:
                last_error = str(exc)
                if _is_rate_limit_error(exc):
                    logger.warning("topic [%s] stopped after rate limit: %s", topic_name, exc)
                    break

        if last_error is not None:
            _mark_topic_run(topic, status="failed", count=0, error=last_error)
            report("抓取失败", 100)
            return {
                "topic_id": topic_id,
                "topic_name": topic_name,
                "status": "failed",
                "attempts": attempts,
                "error": last_error,
                "inserted": 0,
            }

        if new_count == 0:
            _mark_topic_run(topic, status="no_new_papers", count=0)
            report("未发现新论文", 100)
            logger.info("topic [%s] no new papers (duplicates=%d)", topic_name, len(ids))
            return {
                "topic_id": topic_id,
                "topic_name": topic_name,
                "status": "no_new_papers",
                "inserted": 0,
                "new_count": 0,
                "total_count": len(ids),
            }

        repo = PaperRepository(session)
        unique = repo.list_by_ids(ids) if ids else []
        default_folder_id = str(getattr(topic, "default_folder_id", "") or "").strip()
        if default_folder_id:
            folder_topic = session.get(TopicSubscription, default_folder_id)
            if folder_topic and getattr(folder_topic, "kind", "subscription") == "folder":
                for paper in unique:
                    repo.link_to_topic(str(paper.id), default_folder_id)
        papers_data = [(str(p.id), p.title) for p in unique]

    logger.info(
        "topic [%s] fetched %d papers (%d new), deep-read quota=%d",
        topic_name,
        len(unique),
        new_count,
        max_deep_reads,
    )

    report(f"开始处理 {len(papers_data)} 篇新论文", 40)
    skim_results: list[dict] = []
    skim_done = 0

    with ThreadPoolExecutor(max_workers=PAPER_CONCURRENCY) as pool:
        futs = {
            pool.submit(_process_paper, paper_id, force_deep=False, deep_read_quota=0): paper_id
            for paper_id, _ in papers_data
        }
        for fut in as_completed(futs):
            try:
                result = fut.result()
                skim_results.append(result)
            except Exception as exc:
                paper_id = futs[fut]
                logger.warning("skim %s failed: %s", str(paper_id)[:8], exc)
            finally:
                skim_done += 1
                skim_progress = 40 + round((skim_done / max(1, len(papers_data))) * 35)
                report(f"正在处理论文 {skim_done}/{len(papers_data)}", skim_progress)

    report("正在选择高分论文进行精读", 78)
    scored_papers = [
        (r, paper_id)
        for r, (paper_id, _) in zip(skim_results, papers_data)
        if r["success"] and r["skim_score"] is not None
    ]
    scored_papers.sort(key=lambda x: x[0]["skim_score"], reverse=True)

    score_threshold = get_settings().skim_score_threshold
    deep_candidates = [
        (result, paper_id)
        for result, paper_id in scored_papers
        if result["skim_score"] >= score_threshold
    ][:max_deep_reads]
    deep_progress_total = max(1, len(deep_candidates))

    deep_read_count = 0
    deep_attempted = 0
    for i, (result, paper_id) in enumerate(scored_papers):
        if deep_read_count >= max_deep_reads:
            logger.info(
                "deep-read quota used up (%d/%d), skip remaining %d papers",
                deep_read_count,
                max_deep_reads,
                len(scored_papers) - i,
            )
            break

        if result["skim_score"] < score_threshold:
            logger.info(
                "skip deep read for %s due to low skim score %.2f",
                str(paper_id)[:8],
                result["skim_score"],
            )
            continue

        logger.info(
            "start deep read %d: %s (score=%.2f)",
            deep_read_count + 1,
            str(paper_id)[:50],
            result["skim_score"],
        )

        try:
            if acquire_api("llm", timeout=60.0):
                pipelines.deep_dive(UUID(paper_id))  # type: ignore[arg-type]
                deep_read_count += 1
                logger.info("deep read done (%d/%d)", deep_read_count, max_deep_reads)
            else:
                logger.warning("llm permit timeout, skip deep read")
        except Exception as exc:
            logger.warning("deep_dive %s failed: %s", str(paper_id)[:8], exc)
        finally:
            deep_attempted += 1
            if deep_candidates:
                completed = min(deep_attempted, deep_progress_total)
                deep_progress = 80 + round((completed / deep_progress_total) * 20)
                report(
                    f"正在精读高价值论文 {completed}/{deep_progress_total}",
                    deep_progress,
                )

    report("主题抓取完成", 100)
    with session_scope() as session:
        topic = session.get(TopicSubscription, topic_id)
        if topic:
            _mark_topic_run(topic, status="ok", count=len(ids))
    return {
        "topic_id": topic_id,
        "topic_name": topic_name,
        "status": "ok",
        "attempts": attempts,
        "inserted": len(ids),
        "skimmed": len(skim_results),
        "deep_read": deep_read_count,
        "processed": len(skim_results),
        "max_deep_reads": max_deep_reads,
    }


def run_daily_ingest() -> dict:
    """兼容旧调用：遍历所有 enabled 主题执行抓取"""
    with session_scope() as session:
        topic_repo = TopicRepository(session)
        topics = topic_repo.list_topics(enabled_only=True, kind="subscription")
        if not topics:
            topics = [
                topic_repo.upsert_topic(
                    name="default-ml",
                    query="cat:cs.LG OR cat:cs.CL",
                    enabled=True,
                    max_results_per_run=20,
                    retry_limit=2,
                )
            ]
        topic_ids = [t.id for t in topics]

    results = []
    for tid in topic_ids:
        results.append(run_topic_ingest(tid))

    total_inserted = sum(r.get("inserted", 0) for r in results)
    total_processed = sum(r.get("processed", 0) for r in results)
    return {
        "newly_inserted": total_inserted,
        "processed": total_processed,
        "topics": results,
    }


def run_daily_brief() -> dict:
    settings = get_settings()
    return DailyBriefService().publish(recipient=settings.notify_default_to)


def run_daily_arxiv_trends() -> dict:
    return ArxivTrendService().precompute_all_subdomains(
        sample_limit=160,
        fallback_days=7,
    )


def run_weekly_graph_maintenance() -> dict:
    with session_scope() as session:
        topics = TopicRepository(session).list_topics(enabled_only=True, kind="subscription")
    graph = GraphService()
    topic_results = []
    for t in topics:
        try:
            topic_results.append(
                graph.sync_citations_for_topic(
                    topic_id=t.id,
                    paper_limit=20,
                    edge_limit_per_paper=6,
                )
            )
        except Exception:
            logger.exception(
                "Failed to sync citations for topic %s",
                t.id,
            )
            continue
    incremental = graph.sync_incremental(paper_limit=50, edge_limit_per_paper=6)
    return {
        "topic_sync": topic_results,
        "incremental": incremental,
    }
