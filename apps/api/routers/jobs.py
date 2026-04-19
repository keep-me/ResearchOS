"""定时任务与动作记录路由。"""

import logging
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from packages.ai.project.aris_smoke_service import build_aris_smoke_report_path, run_aris_smoke
from packages.ai.ops.daily_runner import run_daily_brief, run_daily_ingest
from packages.domain.enums import ActionType, ReadStatus
from packages.domain.task_tracker import global_tracker
from packages.storage.db import session_scope
from packages.storage.models import CollectionAction, Paper
from packages.storage.repositories import PaperRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _trim_text(value: str | None, max_length: int = 80) -> str:
    text = (value or "").strip().replace("\n", " ")
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _fallback_action_title(action: CollectionAction) -> str:
    label_map = {
        ActionType.initial_import.value: "初始导入",
        ActionType.manual_collect.value: "收集",
        ActionType.auto_collect.value: "自动收集",
        ActionType.agent_collect.value: "Agent 收集",
        ActionType.subscription_ingest.value: "订阅抓取",
        ActionType.reference_import.value: "参考文献导入",
    }
    label = label_map.get(str(action.action_type), "动作")
    count = action.paper_count or 0
    if count > 0:
        return f"{label}（{count} 篇）"
    return label


def _extract_title_suffix(value: str | None, max_length: int = 60) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    for separator in ("：", ":", "？", "?"):
        if separator not in text:
            continue
        suffix = text.rsplit(separator, 1)[-1].strip()
        if suffix:
            return _trim_text(suffix, max_length)
    return None


def _format_action_title(action: CollectionAction, source_paper: Paper | None = None) -> str:
    action_type = str(action.action_type)
    query = (action.query or "").strip()
    paper_count = action.paper_count or 0

    if action_type == ActionType.initial_import.value:
        return f"初始导入（{paper_count} 篇）"

    if action_type == ActionType.reference_import.value:
        if source_paper and source_paper.title:
            return f"参考文献导入：{_trim_text(source_paper.title, 60)}"
        suffix_title = _extract_title_suffix(action.title, 60)
        if suffix_title:
            return f"参考文献导入：{suffix_title}"
        return f"参考文献导入（{paper_count} 篇）"

    if query.startswith("id_list:"):
        ids = [item.strip() for item in query.removeprefix("id_list:").split(",") if item.strip()]
        if ids:
            preview = ", ".join(ids[:3])
            suffix = " 等" if len(ids) > 3 else ""
            return f"按ID导入：{preview}{suffix}"
        return "按ID导入"

    if query:
        label_map = {
            ActionType.manual_collect.value: "收集",
            ActionType.auto_collect.value: "自动收集",
            ActionType.agent_collect.value: "Agent 收集",
            ActionType.subscription_ingest.value: "订阅抓取",
        }
        label = label_map.get(action_type, "收集")
        return f"{label}：{_trim_text(query, 80)}"

    if action.title:
        clean_title = _trim_text(action.title, 80)
        if clean_title:
            return clean_title

    return _fallback_action_title(action)


def _serialize_action(action: CollectionAction, source_paper: Paper | None = None) -> dict:
    return {
        "id": action.id,
        "action_type": action.action_type,
        "title": _format_action_title(action, source_paper),
        "query": action.query,
        "topic_id": action.topic_id,
        "paper_count": action.paper_count,
        "created_at": action.created_at.isoformat() if action.created_at else None,
    }


def _submit_aris_like_job(
    *,
    mode: Literal["quick", "full"],
    task_prefix: str,
    task_type: str,
    source: str,
    title_template: str,
    message_template: str,
    retry_label_template: str,
) -> dict:
    mode_label = "完整" if mode == "full" else "快速"
    task_id = f"{task_prefix}_{mode}_{_uuid.uuid4().hex[:8]}"
    report_path = build_aris_smoke_report_path(task_id, mode=mode)
    tracker_metadata = {
        "source": source,
        "retry_label": retry_label_template.format(mode_label=mode_label),
        "retry_metadata": {"mode": mode},
        "metadata": {
            "mode": mode,
            "report_path": str(report_path),
        },
    }

    def _fn(progress_callback=None):
        try:
            run_aris_smoke(
                mode=mode,
                progress_callback=progress_callback,
                log_callback=lambda message: global_tracker.append_log(task_id, message, level="info"),
                report_path=report_path,
            )
        finally:
            if report_path.exists():
                global_tracker.set_metadata(
                    task_id,
                    artifact_refs=[{"path": str(report_path), "relative_path": report_path.name, "kind": "json"}],
                    metadata={
                        "mode": mode,
                        "report_path": str(report_path),
                    },
                )
                try:
                    import json

                    payload = json.loads(report_path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        global_tracker.set_result(task_id, payload)
                        global_tracker.set_metadata(
                            task_id,
                            metadata={
                                "mode": mode,
                                "report_path": str(report_path),
                                "workflow_count": payload.get("workflow_count"),
                                "failed_workflow_count": payload.get("failed_workflow_count"),
                            },
                        )
                except Exception:
                    logger.debug("failed to load workflow regression report for task %s", task_id, exc_info=True)
        return global_tracker.get_result(task_id) or {}

    submitted_task_id = global_tracker.submit(
        task_type,
        title_template.format(mode_label=mode_label),
        _fn,
        task_id=task_id,
        total=100,
        metadata=tracker_metadata,
        on_retry=lambda: _submit_aris_like_job(
            mode=mode,
            task_prefix=task_prefix,
            task_type=task_type,
            source=source,
            title_template=title_template,
            message_template=message_template,
            retry_label_template=retry_label_template,
        ),
        retry_label=retry_label_template.format(mode_label=mode_label),
        retry_metadata={"mode": mode},
    )
    return {
        "task_id": submitted_task_id,
        "message": message_template.format(mode_label=mode_label),
        "status": "running",
    }


@router.post("/jobs/daily/run-once")
def run_daily_once() -> dict:
    """执行一次每日任务（收集 + 简报）。"""

    def _fn(progress_callback=None):
        if progress_callback:
            progress_callback("正在执行订阅收集...", 10, 100)
        ingest = run_daily_ingest()
        if progress_callback:
            progress_callback("正在生成每日简报...", 70, 100)
        brief = run_daily_brief()
        return {"ingest": ingest, "brief": brief}

    task_id = global_tracker.submit("daily_job", "每日任务执行", _fn)
    return {"task_id": task_id, "message": "每日任务已启动", "status": "running"}


@router.post("/jobs/workflow-regression/run-once")
@router.post("/jobs/aris-smoke/run-once", include_in_schema=False)
def run_workflow_regression_once(
    mode: Literal["quick", "full"] = Query(default="quick"),
) -> dict:
    """启动一次项目工作流回归检查，并写入任务中心。"""
    return _submit_aris_like_job(
        mode=mode,
        task_prefix="workflow_regression",
        task_type="workflow_regression",
        source="workflow_regression",
        title_template="项目工作流回归检查（{mode_label}）",
        message_template="项目工作流{mode_label}回归检查已启动",
        retry_label_template="重新运行项目工作流{mode_label}回归",
    )


def run_aris_smoke_once(mode: Literal["quick", "full"] = "quick") -> dict:
    """兼容旧测试/脚本的 ARIS smoke 任务入口。"""

    return _submit_aris_like_job(
        mode=mode,
        task_prefix="aris_smoke",
        task_type="aris_smoke",
        source="aris_smoke",
        title_template="ARIS Smoke 检查（{mode_label}）",
        message_template="ARIS Smoke {mode_label}检查已启动",
        retry_label_template="重新运行 ARIS Smoke {mode_label}检查",
    )


@router.post("/jobs/batch-process-unread")
def batch_process_unread(
    background_tasks: BackgroundTasks,
    max_papers: int = Query(default=50, ge=1, le=200),
) -> dict:
    """批量处理未读论文（embed + skim 并行）。"""
    import uuid
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from packages.ai.ops.daily_runner import PAPER_CONCURRENCY, _process_paper

    with session_scope() as session:
        repo = PaperRepository(session)
        unread = repo.list_by_read_status(ReadStatus.unread, limit=max_papers)
        target_ids: list[str] = []
        for paper in unread:
            needs_embed = paper.embedding is None
            needs_skim = paper.read_status == ReadStatus.unread
            if needs_embed or needs_skim:
                target_ids.append(paper.id)

    total = len(target_ids)
    if total == 0:
        return {"processed": 0, "total_unread": 0, "message": "没有需要处理的未读论文"}

    task_id = f"batch_unread_{uuid.uuid4().hex[:8]}"

    def _run_batch():
        processed = 0
        failed = 0
        try:
            global_tracker.start(task_id, "batch_process", f"批量处理未读论文（{total} 篇）", total=total)

            with ThreadPoolExecutor(max_workers=PAPER_CONCURRENCY) as pool:
                futures = {pool.submit(_process_paper, paper_id): paper_id for paper_id in target_ids}
                for future in as_completed(futures):
                    try:
                        future.result()
                        processed += 1
                        global_tracker.update(
                            task_id,
                            processed,
                            f"正在处理...（{processed}/{total}）",
                            total=total,
                        )
                    except Exception as exc:
                        failed += 1
                        logger.warning("batch process %s failed: %s", str(futures[future])[:8], exc)

            global_tracker.finish(task_id, success=True)
            logger.info("批量处理完成: %d 成功, %d 失败", processed, failed)
        except Exception as exc:
            global_tracker.finish(task_id, success=False, error=str(exc))
            logger.error("批量处理失败: %s", exc, exc_info=True)

    background_tasks.add_task(_run_batch)
    return {
        "task_id": task_id,
        "message": f"批量处理已启动（{total} 篇论文）",
        "status": "running",
    }


@router.get("/actions")
def list_actions(
    action_type: str | None = None,
    topic_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """列出论文入库动作记录。"""
    from packages.storage.repositories import ActionRepository

    with session_scope() as session:
        repo = ActionRepository(session)
        actions, total = repo.list_actions(
            action_type=action_type,
            topic_id=topic_id,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [
                _serialize_action(
                    action,
                    session.get(Paper, action.query)
                    if str(action.action_type) == ActionType.reference_import.value and action.query
                    else None,
                )
                for action in actions
            ],
            "total": total,
        }


@router.delete("/actions/{action_id}")
def delete_action(action_id: str) -> dict:
    """删除一条收集动作记录，不删除论文本身。"""
    from packages.storage.repositories import ActionRepository

    with session_scope() as session:
        repo = ActionRepository(session)
        deleted = repo.delete_action(action_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="动作记录不存在")
    return {"deleted": action_id}


@router.get("/actions/{action_id}")
def get_action_detail(action_id: str) -> dict:
    """获取动作详情。"""
    from packages.storage.repositories import ActionRepository

    with session_scope() as session:
        repo = ActionRepository(session)
        action = repo.get_action(action_id)
        if not action:
            raise HTTPException(status_code=404, detail="动作记录不存在")
        source_paper = (
            session.get(Paper, action.query)
            if str(action.action_type) == ActionType.reference_import.value and action.query
            else None
        )
        return _serialize_action(action, source_paper)


@router.get("/actions/{action_id}/papers")
def get_action_papers(
    action_id: str,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    """获取某次动作关联的论文列表。"""
    from packages.storage.repositories import ActionRepository

    with session_scope() as session:
        repo = ActionRepository(session)
        papers = repo.get_papers_by_action(action_id, limit=limit)
        return {
            "action_id": action_id,
            "items": [
                {
                    "id": paper.id,
                    "title": paper.title,
                    "arxiv_id": paper.arxiv_id,
                    "publication_date": paper.publication_date.isoformat()
                    if paper.publication_date
                    else None,
                    "read_status": paper.read_status,
                    "citation_count": (
                        (paper.metadata_json or {}).get("citation_count")
                        if (paper.metadata_json or {}).get("citation_count") is not None
                        else (paper.metadata_json or {}).get("citationCount")
                    ),
                }
                for paper in papers
            ],
        }
