"""引用图谱 & 引用同步路由"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from apps.api.deps import cache, get_paper_title, graph_service
from packages.domain.task_tracker import global_tracker
from packages.storage.db import session_scope
from packages.storage.repositories import GeneratedContentRepository, TopicRepository

router = APIRouter()
logger = logging.getLogger(__name__)

_CITATION_TREE_CACHE_TTL_SEC = 180
_CITATION_DETAIL_CACHE_TTL_SEC = 3600
_TOPIC_CITATION_NETWORK_CACHE_TTL_SEC = 180


def _cache_key_citation_tree(paper_id: str, depth: int) -> str:
    return f"graph_citation_tree_{paper_id}_{depth}"


def _cache_key_citation_detail(paper_id: str) -> str:
    return f"graph_citation_detail_{paper_id}"


def _cache_key_topic_citation_network(topic_id: str) -> str:
    return f"graph_topic_citation_network_{topic_id}"


def _invalidate_citation_graph_caches() -> None:
    cache.invalidate_prefix("graph_citation_tree_")
    cache.invalidate_prefix("graph_citation_detail_")
    cache.invalidate_prefix("graph_topic_citation_network_")


# ---------- 引用同步 ----------
# 注意：固定路径必须在 {paper_id} 动态路径之前，否则会被错误匹配


@router.post("/citations/sync/incremental")
def sync_citations_incremental(
    paper_limit: int = Query(default=40, ge=1, le=200),
    edge_limit_per_paper: int = Query(default=6, ge=1, le=50),
) -> dict:
    """增量同步引用（后台执行）"""
    _invalidate_citation_graph_caches()

    def _fn(progress_callback=None):
        return graph_service.sync_incremental(
            paper_limit=paper_limit,
            edge_limit_per_paper=edge_limit_per_paper,
        )

    task_id = global_tracker.submit("citation_sync", "📊 增量引用同步", _fn)
    return {"task_id": task_id, "message": "增量引用同步已启动", "status": "running"}


@router.post("/citations/sync/topic/{topic_id}")
def sync_citations_for_topic(
    topic_id: str,
    paper_limit: int = Query(default=30, ge=1, le=200),
    edge_limit_per_paper: int = Query(default=6, ge=1, le=50),
) -> dict:
    """主题引用同步（后台执行）"""
    _invalidate_citation_graph_caches()
    topic_name = topic_id
    try:
        with session_scope() as session:
            topic = TopicRepository(session).get_by_id(topic_id)
            if topic:
                topic_name = topic.name
    except Exception:
        pass

    def _fn(progress_callback=None):
        return graph_service.sync_citations_for_topic(
            topic_id=topic_id,
            paper_limit=paper_limit,
            edge_limit_per_paper=edge_limit_per_paper,
        )

    task_id = global_tracker.submit("citation_sync", f"📊 主题引用同步: {topic_name}", _fn)
    return {"task_id": task_id, "message": f"主题引用同步已启动: {topic_name}", "status": "running"}


@router.post("/citations/sync/{paper_id}")
def sync_citations(
    paper_id: str,
    limit: int = Query(default=8, ge=1, le=50),
) -> dict:
    """单篇论文引用同步（后台执行）"""
    _invalidate_citation_graph_caches()
    paper_title = get_paper_title(UUID(paper_id)) or paper_id[:8]

    def _fn(progress_callback=None):
        return graph_service.sync_citations_for_paper(paper_id=paper_id, limit=limit)

    task_id = global_tracker.submit("citation_sync", f"📄 引用同步: {paper_title[:30]}", _fn)
    return {"task_id": task_id, "message": "论文引用同步已启动", "status": "running"}


# ---------- 图谱 ----------


@router.get("/graph/similarity-map")
def similarity_map(
    topic_id: str | None = None,
    limit: int = Query(default=200, ge=5, le=500),
) -> dict:
    """论文相似度 2D 散点图（UMAP 降维）"""
    normalized_topic_id = topic_id.strip() or None if isinstance(topic_id, str) else None
    try:
        return graph_service.similarity_map(topic_id=normalized_topic_id, limit=limit)
    except Exception as exc:
        logger.warning("similarity_map fallback triggered: %s", exc)
        return {
            "points": [],
            "total": 0,
            "message": "相似度地图暂时不可用，已返回空结果。",
        }


@router.get("/graph/citation-tree/{paper_id}")
def citation_tree(
    paper_id: str,
    depth: int = Query(default=2, ge=1, le=5),
) -> dict:
    cache_key = _cache_key_citation_tree(paper_id, depth)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = graph_service.citation_tree(root_paper_id=paper_id, depth=depth)
    cache.set(cache_key, result, ttl=_CITATION_TREE_CACHE_TTL_SEC)
    return result


@router.get("/graph/citation-detail/{paper_id}")
def citation_detail(
    paper_id: str,
    refresh: bool = Query(default=False),
) -> dict:
    """获取单篇论文的丰富引用详情（含参考文献和被引列表）"""
    cache_key = _cache_key_citation_detail(paper_id)
    if not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    else:
        cache.invalidate(cache_key)
    result = graph_service.citation_detail(paper_id=paper_id, force_refresh=refresh)
    cache.set(cache_key, result, ttl=_CITATION_DETAIL_CACHE_TTL_SEC)
    return result


@router.get("/graph/citation-network/topic/{topic_id}")
def topic_citation_network(topic_id: str) -> dict:
    """获取主题内论文的互引网络"""
    cache_key = _cache_key_topic_citation_network(topic_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = graph_service.topic_citation_network(topic_id=topic_id)
    cache.set(cache_key, result, ttl=_TOPIC_CITATION_NETWORK_CACHE_TTL_SEC)
    return result


@router.post("/graph/citation-network/topic/{topic_id}/deep-trace")
def topic_deep_trace(topic_id: str) -> dict:
    """对主题内论文执行深度溯源，拉取外部引用并进行共引分析"""
    _invalidate_citation_graph_caches()
    result = graph_service.topic_deep_trace(topic_id=topic_id)
    cache.set(
        _cache_key_topic_citation_network(topic_id),
        result,
        ttl=_TOPIC_CITATION_NETWORK_CACHE_TTL_SEC,
    )
    return result


@router.post("/graph/citation-detail/{paper_id}/async")
def citation_detail_async(
    paper_id: str,
    depth: int = Query(default=2, ge=1, le=5),
) -> dict:
    """单篇引文分析（后台任务）"""

    def _fn(progress_callback=None):
        if progress_callback:
            progress_callback("正在加载引用详情...", 20, 100)
        detail = graph_service.citation_detail(paper_id=paper_id)
        if progress_callback:
            progress_callback("正在构建引用树...", 62, 100)
        tree = graph_service.citation_tree(root_paper_id=paper_id, depth=depth)
        if progress_callback:
            progress_callback("引文分析完成", 100, 100)
        return {"detail": detail, "tree": tree}

    task_id = global_tracker.submit(
        task_type="citation_analysis",
        title=f"引文分析: {paper_id[:8]}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "引文分析任务已启动"}


@router.post("/graph/citation-network/topic/{topic_id}/async")
def topic_citation_network_async(topic_id: str) -> dict:
    """文件夹引文网络分析（后台任务）"""

    topic_name = topic_id
    try:
        with session_scope() as session:
            topic = TopicRepository(session).get_by_id(topic_id)
            if topic:
                topic_name = topic.name
    except Exception:
        pass

    def _fn(progress_callback=None):
        if progress_callback:
            progress_callback("正在加载文件夹引用网络...", 36, 100)
        result = graph_service.topic_citation_network(topic_id=topic_id)
        if progress_callback:
            progress_callback("文件夹引用网络已加载", 100, 100)
        return result

    task_id = global_tracker.submit(
        task_type="citation_analysis",
        title=f"引文分析(文件夹): {topic_name[:24]}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "文件夹引文分析任务已启动"}


@router.post("/graph/citation-network/topic/{topic_id}/deep-trace-async")
def topic_deep_trace_async(topic_id: str) -> dict:
    """文件夹深度溯源（后台任务）"""

    topic_name = topic_id
    try:
        with session_scope() as session:
            topic = TopicRepository(session).get_by_id(topic_id)
            if topic:
                topic_name = topic.name
    except Exception:
        pass

    def _fn(progress_callback=None):
        if progress_callback:
            progress_callback("正在执行深度溯源...", 30, 100)
        result = graph_service.topic_deep_trace(topic_id=topic_id)
        if progress_callback:
            progress_callback("深度溯源完成", 100, 100)
        return result

    task_id = global_tracker.submit(
        task_type="citation_analysis",
        title=f"深度溯源: {topic_name[:24]}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "深度溯源任务已启动"}


@router.post("/graph/insight/async")
def insight_async(
    keyword: str,
    limit: int = Query(default=120, ge=10, le=500),
) -> dict:
    """领域洞察（时间线 + 演化 + 质量 + 研究空白，后台任务）"""
    kw = keyword.strip()
    if not kw:
        raise HTTPException(status_code=400, detail="keyword is required")

    def _fn(progress_callback=None):
        if progress_callback:
            progress_callback("正在计算时间线...", 15, 100)
        timeline = graph_service.timeline(keyword=kw, limit=limit)

        if progress_callback:
            progress_callback("正在分析演化趋势...", 40, 100)
        evolution = graph_service.weekly_evolution(keyword=kw, limit=limit)

        if progress_callback:
            progress_callback("正在计算图谱质量...", 62, 100)
        quality = graph_service.quality_metrics(keyword=kw, limit=limit)

        if progress_callback:
            progress_callback("正在识别研究空白...", 82, 100)
        gaps = graph_service.detect_research_gaps(keyword=kw, limit=limit)

        insight_result = {
            "keyword": kw,
            "timeline": timeline,
            "evolution": evolution,
            "quality": quality,
            "gaps": gaps,
        }
        try:
            analysis = gaps.get("analysis", {}) if isinstance(gaps, dict) else {}
            summary = str(
                analysis.get("overall_summary", "") if isinstance(analysis, dict) else ""
            ).strip()
            if not summary:
                summary = f"领域洞察结果（关键词: {kw}）"
            with session_scope() as session:
                repo = GeneratedContentRepository(session)
                gc = repo.create(
                    content_type="graph_insight",
                    title=f"领域洞察: {kw[:80]}",
                    markdown=summary,
                    keyword=kw,
                    metadata_json=insight_result,
                )
                insight_result["content_id"] = gc.id
        except Exception as exc:
            logger.warning("save graph insight history failed: %s", exc)

        if progress_callback:
            progress_callback("领域洞察完成", 100, 100)
        return insight_result

    task_id = global_tracker.submit(
        task_type="insight",
        title=f"领域洞察: {kw[:32]}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "领域洞察任务已启动"}


@router.get("/graph/overview")
def graph_overview() -> dict:
    """全库引用概览 — 节点 + 边 + PageRank + 统计（60s 缓存）"""
    cached = cache.get("graph_overview")
    if cached is not None:
        return cached
    result = graph_service.library_overview()
    cache.set("graph_overview", result, ttl=60)
    return result


@router.get("/graph/bridges")
def graph_bridges() -> dict:
    """跨主题桥接论文（60s 缓存）"""
    cached = cache.get("graph_bridges")
    if cached is not None:
        return cached
    result = graph_service.cross_topic_bridges()
    cache.set("graph_bridges", result, ttl=60)
    return result


@router.get("/graph/frontier")
def graph_frontier(
    days: int = Query(default=90, ge=7, le=365),
) -> dict:
    """研究前沿检测（60s 缓存）"""
    cache_key = f"graph_frontier_{days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = graph_service.research_frontier(days=days)
    cache.set(cache_key, result, ttl=60)
    return result


@router.get("/graph/cocitation-clusters")
def graph_cocitation_clusters(
    min_cocite: int = Query(default=2, ge=1, le=10),
) -> dict:
    """共引聚类分析"""
    return graph_service.cocitation_clusters(min_cocite=min_cocite)


@router.post("/graph/auto-link")
def graph_auto_link(paper_ids: list[str]) -> dict:
    """手动触发引用自动关联"""
    _invalidate_citation_graph_caches()
    return graph_service.auto_link_citations(paper_ids)


@router.get("/graph/timeline")
def graph_timeline(
    keyword: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return graph_service.timeline(keyword=keyword, limit=limit)


@router.get("/graph/quality")
def graph_quality(
    keyword: str,
    limit: int = Query(default=120, ge=1, le=500),
) -> dict:
    return graph_service.quality_metrics(keyword=keyword, limit=limit)


@router.get("/graph/evolution/weekly")
def graph_weekly_evolution(
    keyword: str,
    limit: int = Query(default=160, ge=1, le=500),
) -> dict:
    return graph_service.weekly_evolution(keyword=keyword, limit=limit)


@router.get("/graph/survey")
def graph_survey(
    keyword: str,
    limit: int = Query(default=120, ge=1, le=500),
) -> dict:
    return graph_service.survey(keyword=keyword, limit=limit)


@router.get("/graph/research-gaps")
def graph_research_gaps(
    keyword: str,
    limit: int = Query(default=120, ge=1, le=500),
) -> dict:
    return graph_service.detect_research_gaps(keyword=keyword, limit=limit)
