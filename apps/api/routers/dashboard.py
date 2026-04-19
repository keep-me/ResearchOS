"""Dashboard aggregation routes for the ResearchOS home page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from apps.api.deps import cache, graph_service
from apps.api.routers.projects import _serialize_project_summary, _serialize_run_summary
from apps.api.routers.topics import _topic_dict
from apps.api.task_token_usage import enrich_tasks_with_token_usage
from packages.agent.runtime.acp_service import get_acp_registry_service
from packages.ai.research.arxiv_trend_service import ArxivTrendService
from packages.ai.research.recommendation_service import TrendService
from packages.domain.enums import ReadStatus
from packages.domain.task_tracker import global_tracker
from packages.storage.models import AnalysisReport, Citation, Paper, PaperTopic, TopicSubscription
from packages.storage.db import session_scope
from packages.storage.repository_facades import PaperDataFacade, ProjectDataFacade, TopicDataFacade

router = APIRouter()


def _get_arxiv_trend() -> dict:
    cache_key = "dashboard_arxiv_trend_cs_recent_non_empty_v6"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = ArxivTrendService().today_snapshot(sample_limit=1000, fallback_days=7)
    cache.set(cache_key, result, ttl=60 * 60 if result.get("available") else 5 * 60)
    return result


def _read_status_value(paper) -> str:
    status = getattr(paper, "read_status", None)
    return str(getattr(status, "value", status or ReadStatus.unread.value))


def _is_recent(created_at: datetime | None, threshold: datetime) -> bool:
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at >= threshold


def _local_citation_edge_count(session, paper_ids: set[str]) -> int:
    if not paper_ids:
        return 0
    return int(
        session.execute(
            select(func.count(Citation.id)).where(
                or_(
                    Citation.source_paper_id.in_(paper_ids),
                    Citation.target_paper_id.in_(paper_ids),
                )
            )
        ).scalar()
        or 0
    )


def _build_topic_card(
    label: str,
    kind: str,
    members: list[tuple[object, AnalysisReport | None, float]],
    citation_edge_count: int,
) -> dict:
    paper_count = len(members)
    deep_read = sum(1 for paper, _report, _score in members if _read_status_value(paper) == ReadStatus.deep_read.value)
    skimmed = sum(1 for paper, _report, _score in members if _read_status_value(paper) == ReadStatus.skimmed.value)
    unread = max(0, paper_count - deep_read - skimmed)
    weighted_completion = round(((deep_read + skimmed * 0.5) / max(1, paper_count)) * 100)

    recent_threshold = datetime.now(UTC) - timedelta(days=30)
    active_30d = sum(
        1
        for paper, _report, _score in members
        if _is_recent(getattr(paper, "created_at", None), recent_threshold)
    )

    return {
        "label": label,
        "kind": kind,
        "paper_count": paper_count,
        "citation_count": citation_edge_count,
        "active_30d": active_30d,
        "progress": {
            "deep_read": deep_read,
            "skimmed": skimmed,
            "unread": unread,
            "completion_pct": weighted_completion,
        },
    }


def _library_focus_snapshot(session, paper_repo) -> dict:
    topic_cards = []
    topics = session.execute(
        select(TopicSubscription).order_by(TopicSubscription.created_at.desc())
    ).scalars().all()
    topic_ids = [topic.id for topic in topics]
    topic_member_rows = []
    if topic_ids:
        topic_member_rows = session.execute(
            select(PaperTopic.topic_id, Paper, AnalysisReport)
            .join(Paper, PaperTopic.paper_id == Paper.id)
            .outerjoin(AnalysisReport, AnalysisReport.paper_id == Paper.id)
            .where(PaperTopic.topic_id.in_(topic_ids))
        ).all()
    members_by_topic: dict[str, list[tuple[object, AnalysisReport | None, float]]] = {}
    for topic_id, paper, report in topic_member_rows:
        score = float(report.skim_score or 0) if report else 0.0
        members_by_topic.setdefault(str(topic_id), []).append((paper, report, score))

    for topic in topics:
        members = sorted(members_by_topic.get(str(topic.id), []), key=lambda row: row[2], reverse=True)
        topic_paper_ids = {str(paper.id) for paper, _report, _score in members}
        topic_cards.append(
            _build_topic_card(
                topic.name,
                getattr(topic, "kind", "subscription"),
                members,
                _local_citation_edge_count(session, topic_paper_ids),
            )
        )

    return {
        "window_label": "全库主题",
        "paper_count": paper_repo.count_all(),
        "topic_cards": topic_cards,
        "keywords": paper_repo.keyword_facets(limit=10),
    }


@router.get("/dashboard/home")
def dashboard_home(
    project_limit: int = Query(default=4, ge=1, le=20),
    task_limit: int = Query(default=8, ge=1, le=50),
) -> dict:
    """Aggregated home-page snapshot.

    The frontend home page needs a small cross-domain summary. Keeping the
    aggregation here avoids a burst of parallel browser requests on first load.
    """

    cache_key = f"dashboard_home_v8_{project_limit}_{task_limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    today = TrendService().get_today_summary()
    tasks = enrich_tasks_with_token_usage(global_tracker.list_tasks(limit=max(task_limit, 1)))
    graph = graph_service.library_overview()
    arxiv_trend = _get_arxiv_trend()

    with session_scope() as session:
        paper_repo = PaperDataFacade.from_session(session).papers
        project_repo = ProjectDataFacade.from_session(session).projects
        topic_repo = TopicDataFacade.from_session(session).topics

        folders = paper_repo.folder_stats()
        library_focus = _library_focus_snapshot(session, paper_repo)
        topics = [_topic_dict(topic, session) for topic in topic_repo.list_topics()]
        projects = []
        for project in project_repo.list_projects()[: max(1, project_limit)]:
            runs = project_repo.list_runs(project.id, limit=1)
            latest_run = runs[0] if runs else None
            active_task_count = sum(
                1
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("project_id") or "").strip() == project.id
                and not bool(task.get("finished"))
            )
            projects.append(
                {
                    **_serialize_project_summary(project, project_repo),
                    "latest_run": _serialize_run_summary(latest_run) if latest_run is not None else None,
                    "active_task_count": active_task_count,
                }
            )

    try:
        acp = get_acp_registry_service().get_backend_summary()
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        acp = {
            "chat_ready": False,
            "chat_status_label": f"ACP 摘要获取失败: {exc}",
        }

    result = {
        "today": today,
        "folders": folders,
        "projects": projects,
        "tasks": tasks[: max(1, task_limit)],
        "graph": graph,
        "arxiv_trend": arxiv_trend,
        "library_focus": library_focus,
        "topics": topics,
        "acp": acp,
    }
    cache.set(cache_key, result, ttl=30)
    return result
