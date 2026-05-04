"""Pipeline / RAG / 任务追踪路由"""

import logging
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query

from apps.api.deps import get_paper_title, iso_dt, pipelines, rag_service
from apps.api.task_token_usage import enrich_task_with_token_usage, enrich_tasks_with_token_usage
from packages.ai.paper.content_source import (
    normalize_paper_content_source,
    paper_content_source_label,
)
from packages.domain.exceptions import NotFoundError
from packages.domain.schemas import AskRequest, AskResponse
from packages.domain.task_tracker import global_tracker
from packages.storage.db import session_scope
from packages.storage.repositories import PipelineRunRepository

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Pipeline ----------


def _paper_title_short(paper_id: UUID) -> str:
    return (get_paper_title(paper_id) or str(paper_id)[:8])[:30]


@router.post("/pipelines/skim/{paper_id}")
def run_skim(paper_id: UUID) -> dict:

    tid = f"skim_{paper_id.hex[:8]}_{uuid4().hex[:6]}"
    title = get_paper_title(paper_id) or str(paper_id)[:8]
    global_tracker.start(
        tid,
        "skim",
        f"粗读: {title[:30]}",
        total=100,
        metadata={"source": "paper", "source_id": str(paper_id), "paper_id": str(paper_id)},
    )
    try:
        skim = pipelines.skim(
            paper_id,
            progress_callback=lambda msg, cur, tot: global_tracker.update(tid, cur, msg, total=tot),
        )
        global_tracker.finish(tid, success=True)
        return skim.model_dump()
    except Exception as exc:
        global_tracker.finish(tid, success=False, error=str(exc)[:100])
        raise


@router.post("/pipelines/skim/{paper_id}/async")
def run_skim_async(paper_id: UUID) -> dict:
    """后台提交粗读任务，返回 task_id。"""

    title = _paper_title_short(paper_id)

    def _fn(progress_callback=None):
        skim = pipelines.skim(paper_id, progress_callback=progress_callback)
        return skim.model_dump()

    task_id = global_tracker.submit(
        task_type="skim",
        title=f"粗读: {title}",
        fn=_fn,
        total=100,
        metadata={"source": "paper", "source_id": str(paper_id), "paper_id": str(paper_id)},
    )
    return {"task_id": task_id, "status": "running", "message": "粗读任务已启动"}


@router.post("/pipelines/deep/{paper_id}")
def run_deep(
    paper_id: UUID,
    detail_level: Literal["low", "medium", "high"] = Query(default="medium"),
    content_source: str | None = Query(default=None),
    evidence_mode: str | None = Query(default=None),
) -> dict:

    tid = f"deep_{paper_id.hex[:8]}_{uuid4().hex[:6]}"
    title = get_paper_title(paper_id) or str(paper_id)[:8]
    global_tracker.start(
        tid,
        "deep_read",
        f"精读: {title[:30]}",
        total=100,
        metadata={"source": "paper", "source_id": str(paper_id), "paper_id": str(paper_id)},
    )
    try:
        deep = pipelines.deep_dive(
            paper_id,
            detail_level=detail_level,
            content_source=content_source or "auto",
            evidence_mode=evidence_mode or "full",
            progress_callback=lambda msg, cur, tot: global_tracker.update(tid, cur, msg, total=tot),
        )
        global_tracker.finish(tid, success=True)
        return deep.model_dump()
    except Exception as exc:
        global_tracker.finish(tid, success=False, error=str(exc)[:100])
        raise


@router.post("/pipelines/deep/{paper_id}/async")
def run_deep_async(
    paper_id: UUID,
    detail_level: Literal["low", "medium", "high"] = Query(default="medium"),
    content_source: str | None = Query(default=None),
    evidence_mode: str | None = Query(default=None),
) -> dict:
    """后台提交精读任务，返回 task_id。"""

    title = _paper_title_short(paper_id)
    requested_content_source = normalize_paper_content_source(content_source or "auto")
    normalized_evidence_mode = str(evidence_mode or "full").strip().lower() or "full"

    def _fn(progress_callback=None):
        deep = pipelines.deep_dive(
            paper_id,
            detail_level=detail_level,
            content_source=content_source or "auto",
            evidence_mode=evidence_mode or "full",
            progress_callback=progress_callback,
        )
        return deep.model_dump()

    task_id = global_tracker.submit(
        task_type="deep_read",
        title=f"精读: {title}",
        fn=_fn,
        total=100,
        metadata={
            "source": "paper",
            "source_id": str(paper_id),
            "paper_id": str(paper_id),
            "detail_level": detail_level,
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
    return {"task_id": task_id, "status": "running", "message": "精读任务已启动"}


@router.post("/pipelines/embed/{paper_id}")
def run_embed(paper_id: UUID) -> dict:

    tid = f"embed_{paper_id.hex[:8]}_{uuid4().hex[:6]}"
    title = get_paper_title(paper_id) or str(paper_id)[:8]
    global_tracker.start(
        tid,
        "embed",
        f"嵌入: {title[:30]}",
        total=100,
        metadata={"source": "paper", "source_id": str(paper_id), "paper_id": str(paper_id)},
    )
    try:
        pipelines.embed_paper(
            paper_id,
            progress_callback=lambda msg, cur, tot: global_tracker.update(tid, cur, msg, total=tot),
        )
        global_tracker.finish(tid, success=True)
        return {"status": "embedded", "paper_id": str(paper_id)}
    except Exception as exc:
        global_tracker.finish(tid, success=False, error=str(exc)[:100])
        raise


@router.post("/pipelines/embed/{paper_id}/async")
def run_embed_async(paper_id: UUID) -> dict:
    """后台提交向量化任务，返回 task_id。"""

    title = _paper_title_short(paper_id)

    def _fn(progress_callback=None):
        pipelines.embed_paper(paper_id, progress_callback=progress_callback)
        return {"status": "embedded", "paper_id": str(paper_id)}

    task_id = global_tracker.submit(
        task_type="embed",
        title=f"嵌入: {title}",
        fn=_fn,
        total=100,
        metadata={"source": "paper", "source_id": str(paper_id), "paper_id": str(paper_id)},
    )
    return {"task_id": task_id, "status": "running", "message": "向量化任务已启动"}


@router.get("/pipelines/runs")
def list_pipeline_runs(
    limit: int = Query(default=30, ge=1, le=200),
) -> dict:
    with session_scope() as session:
        runs = PipelineRunRepository(session).list_latest(limit=limit)
        return {
            "items": [
                {
                    "id": r.id,
                    "pipeline_name": r.pipeline_name,
                    "paper_id": r.paper_id,
                    "status": r.status.value,
                    "decision_note": r.decision_note,
                    "elapsed_ms": r.elapsed_ms,
                    "error_message": r.error_message,
                    "created_at": iso_dt(r.created_at),
                }
                for r in runs
            ]
        }


# ---------- RAG ----------


@router.post("/rag/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    logger.info("RAG ask: question=%r", req.question[:80])
    return rag_service.ask(req.question, top_k=req.top_k)


@router.post("/rag/ask-iterative")
def ask_iterative(
    req: AskRequest,
    max_rounds: int = Query(default=3, ge=1, le=5),
) -> dict:
    """多轮迭代 RAG"""
    logger.info("RAG iterative ask: question=%r max_rounds=%d", req.question[:80], max_rounds)
    resp = rag_service.ask_iterative(
        question=req.question,
        max_rounds=max_rounds,
        initial_top_k=req.top_k,
    )
    return resp.model_dump(mode="json")


# ---------- 任务追踪 ----------


@router.get("/tasks/active")
def get_active_tasks() -> dict:
    """获取全局进行中的任务列表（跨页面可见）"""

    return {"tasks": global_tracker.get_active()}


@router.get("/tasks")
def list_tasks(
    task_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """列出最近任务，用于任务后台页面。"""

    tasks = global_tracker.list_tasks(task_type=task_type, limit=limit)
    return {"tasks": enrich_tasks_with_token_usage(tasks)}


@router.post("/tasks/track")
def track_task(body: dict) -> dict:
    """前端通知后端创建/更新/完成一个全局可见任务"""

    action = body.get("action", "start")
    task_id = body.get("task_id", "")
    if action == "start":
        global_tracker.start(
            task_id=task_id,
            task_type=body.get("task_type", "batch"),
            title=body.get("title", ""),
            total=body.get("total", 0),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
        )
    elif action == "update":
        global_tracker.update(
            task_id=task_id,
            current=body.get("current", 0),
            message=body.get("message", ""),
            total=body.get("total"),
        )
        metadata = body.get("metadata")
        if isinstance(metadata, dict):
            global_tracker.set_metadata(task_id, metadata)
    elif action == "finish":
        global_tracker.finish(
            task_id=task_id,
            success=body.get("success", True),
            error=body.get("error"),
        )
    return {"ok": True}


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    """请求终止一个运行中任务。"""
    status = global_tracker.request_cancel(task_id)
    if not status:
        raise NotFoundError(f"Task {task_id} not found")
    return {"ok": True, "task_id": task_id, "status": status}


@router.get("/tasks/{task_id}")
def get_task_status(task_id: str) -> dict:
    """查询任务进度"""
    status = global_tracker.get_task(task_id)
    if not status:
        raise NotFoundError(f"Task {task_id} not found")
    with session_scope() as session:
        return enrich_task_with_token_usage(session, status)


@router.get("/tasks/{task_id}/logs")
def get_task_logs(
    task_id: str,
    limit: int = Query(default=120, ge=1, le=500),
) -> dict:
    status = global_tracker.get_task(task_id)
    if not status:
        raise NotFoundError(f"Task {task_id} not found")
    return {
        "task_id": task_id,
        "items": global_tracker.list_logs(task_id, limit=limit),
    }


@router.get("/tasks/{task_id}/result")
def get_task_result(task_id: str) -> dict:
    """获取已完成任务的结果"""
    status = global_tracker.get_task(task_id)
    if not status:
        raise NotFoundError(f"Task {task_id} not found")
    if not status.get("finished"):
        raise HTTPException(400, "Task not finished yet")
    result = global_tracker.get_result(task_id)
    return result or {}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str) -> dict:
    status = global_tracker.get_task(task_id)
    if not status:
        raise NotFoundError(f"Task {task_id} not found")
    payload = global_tracker.retry(task_id)
    if not payload:
        raise HTTPException(status_code=400, detail="当前任务不支持重试")
    return payload
