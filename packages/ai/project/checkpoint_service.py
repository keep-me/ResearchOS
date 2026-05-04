from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from packages.ai.project.notification_service import notify_project_run_status
from packages.domain.enums import ProjectRunStatus
from packages.domain.task_tracker import global_tracker
from packages.storage.db import session_scope
from packages.storage.repositories import ProjectRepository

logger = logging.getLogger(__name__)

CHECKPOINT_ACTIVE_PHASE = "awaiting_checkpoint"
CHECKPOINT_PENDING_MESSAGE = "已创建运行，等待人工确认后开始执行。"
CHECKPOINT_APPROVED_MESSAGE = "人工确认已通过，正在启动工作流。"
CHECKPOINT_REJECTED_MESSAGE = "人工确认已拒绝，本次运行未启动。"
STAGE_CHECKPOINT_APPROVED_MESSAGE = "人工确认已通过，正在恢复后续阶段。"
STAGE_CHECKPOINT_REJECTED_MESSAGE = "阶段确认已拒绝，本次运行已停止。"


def normalize_notification_recipients(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, list):
        items.extend(str(item or "").strip() for item in value)
    elif isinstance(value, str):
        normalized = value.replace("；", ",").replace(";", ",").replace("\n", ",")
        items.extend(part.strip() for part in normalized.split(","))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or "@" not in item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(item)
    return deduped


def build_checkpoint_settings(
    metadata: dict[str, Any] | None,
    *,
    enabled: bool | None = None,
    auto_proceed: bool | None = None,
    notification_recipients: Any = None,
    reset_state: bool = False,
) -> dict[str, Any]:
    next_metadata = dict(metadata or {})
    if auto_proceed is not None:
        normalized_auto_proceed = bool(auto_proceed)
        next_metadata["auto_proceed"] = normalized_auto_proceed
        next_metadata["human_checkpoint_enabled"] = not normalized_auto_proceed
    elif enabled is not None:
        normalized_enabled = bool(enabled)
        next_metadata["human_checkpoint_enabled"] = normalized_enabled
        next_metadata["auto_proceed"] = not normalized_enabled
    elif "auto_proceed" in next_metadata:
        normalized_auto_proceed = bool(next_metadata.get("auto_proceed"))
        next_metadata["auto_proceed"] = normalized_auto_proceed
        next_metadata["human_checkpoint_enabled"] = not normalized_auto_proceed
    else:
        normalized_enabled = bool(next_metadata.get("human_checkpoint_enabled"))
        next_metadata["human_checkpoint_enabled"] = normalized_enabled
        next_metadata["auto_proceed"] = not normalized_enabled
    next_metadata["notification_recipients"] = normalize_notification_recipients(
        notification_recipients
        if notification_recipients is not None
        else next_metadata.get("notification_recipients")
    )
    if not bool(next_metadata.get("human_checkpoint_enabled")):
        next_metadata["human_checkpoint_enabled"] = False
        next_metadata["auto_proceed"] = True
        next_metadata["checkpoint_state"] = "disabled"
        next_metadata.pop("pending_checkpoint", None)
        next_metadata.pop("checkpoint_resume_stage_id", None)
        next_metadata.pop("checkpoint_resume_stage_label", None)
        return next_metadata

    if reset_state:
        next_metadata["checkpoint_state"] = "pending"
        next_metadata.pop("pending_checkpoint", None)
        next_metadata.pop("checkpoint_requested_at", None)
        next_metadata.pop("checkpoint_approved_at", None)
        next_metadata.pop("checkpoint_rejected_at", None)
        next_metadata.pop("checkpoint_response_comment", None)
        next_metadata.pop("checkpoint_resume_stage_id", None)
        next_metadata.pop("checkpoint_resume_stage_label", None)
        next_metadata.pop("last_checkpoint", None)
    else:
        state = str(next_metadata.get("checkpoint_state") or "").strip().lower()
        next_metadata["checkpoint_state"] = state or "pending"
    return next_metadata


def human_checkpoint_enabled(metadata: dict[str, Any] | None) -> bool:
    return bool((metadata or {}).get("human_checkpoint_enabled"))


def auto_proceed_enabled(metadata: dict[str, Any] | None) -> bool:
    payload = dict(metadata or {})
    if "auto_proceed" in payload:
        return bool(payload.get("auto_proceed"))
    return not bool(payload.get("human_checkpoint_enabled"))


def checkpoint_state(metadata: dict[str, Any] | None) -> str:
    state = str((metadata or {}).get("checkpoint_state") or "").strip().lower()
    if state:
        return state
    if human_checkpoint_enabled(metadata):
        return "pending"
    return "disabled"


def pending_checkpoint(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    value = (metadata or {}).get("pending_checkpoint")
    return dict(value) if isinstance(value, dict) else None


def checkpoint_resume_stage(metadata: dict[str, Any] | None) -> str | None:
    value = str((metadata or {}).get("checkpoint_resume_stage_id") or "").strip()
    return value or None


def checkpoint_resume_stage_label(metadata: dict[str, Any] | None) -> str | None:
    value = str((metadata or {}).get("checkpoint_resume_stage_label") or "").strip()
    return value or None


def should_pause_for_preflight(metadata: dict[str, Any] | None) -> bool:
    if not human_checkpoint_enabled(metadata):
        return False
    if checkpoint_resume_stage(metadata):
        return False
    return checkpoint_state(metadata) != "approved"


def mark_run_waiting_for_checkpoint(run_id: str, *, task_id: str) -> str:
    return _mark_run_waiting_for_checkpoint(
        run_id,
        task_id=task_id,
        checkpoint_payload={
            "type": "preflight",
            "label": "运行前确认",
            "message": CHECKPOINT_PENDING_MESSAGE,
        },
        summary_message=CHECKPOINT_PENDING_MESSAGE,
    )


def mark_run_waiting_for_stage_checkpoint(
    run_id: str,
    *,
    task_id: str,
    completed_stage_id: str,
    completed_stage_label: str | None,
    resume_stage_id: str,
    resume_stage_label: str | None,
    stage_summary: str | None = None,
) -> str:
    completed_label = (
        str(completed_stage_label or completed_stage_id or "当前阶段").strip() or "当前阶段"
    )
    resume_label = str(resume_stage_label or resume_stage_id or "下一阶段").strip() or "下一阶段"
    message = f"阶段“{completed_label}”已完成，等待人工确认后继续执行“{resume_label}”。"
    payload = {
        "type": "stage_transition",
        "label": f"阶段确认 · {completed_label}",
        "message": message,
        "completed_stage_id": str(completed_stage_id or "").strip() or None,
        "completed_stage_label": completed_label,
        "resume_stage_id": str(resume_stage_id or "").strip() or None,
        "resume_stage_label": resume_label,
        "stage_summary": str(stage_summary or "").strip()[:600] or None,
    }
    return _mark_run_waiting_for_checkpoint(
        run_id,
        task_id=task_id,
        checkpoint_payload=payload,
        summary_message=message,
    )


def _mark_run_waiting_for_checkpoint(
    run_id: str,
    *,
    task_id: str,
    checkpoint_payload: dict[str, Any],
    summary_message: str,
) -> str:
    send_notification = False
    normalized_type = str(checkpoint_payload.get("type") or "").strip().lower() or "preflight"
    normalized_resume_stage = str(checkpoint_payload.get("resume_stage_id") or "").strip() or None
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found")

        metadata = build_checkpoint_settings(run.metadata_json or {}, reset_state=False)
        current_pending = pending_checkpoint(metadata)
        if (
            str(run.status) == ProjectRunStatus.paused.value
            and current_pending
            and str(current_pending.get("status") or "").strip().lower() == "pending"
            and str(current_pending.get("type") or "").strip().lower() == normalized_type
            and str(current_pending.get("resume_stage_id") or "").strip()
            == str(normalized_resume_stage or "")
        ):
            _sync_checkpoint_task(run, metadata, task_id=run.task_id or task_id)
            return str(run.task_id or task_id)

        requested_at = datetime.now(UTC).isoformat()
        next_payload = dict(checkpoint_payload)
        next_payload["status"] = "pending"
        next_payload["requested_at"] = requested_at
        next_payload["notification_recipients"] = list(
            metadata.get("notification_recipients") or []
        )
        metadata["checkpoint_state"] = "pending"
        metadata["checkpoint_requested_at"] = requested_at
        metadata["pending_checkpoint"] = next_payload
        metadata["checkpoint_resume_stage_id"] = normalized_resume_stage
        metadata["checkpoint_resume_stage_label"] = (
            str(next_payload.get("resume_stage_label") or "").strip() or None
        )
        project_repo.update_run(
            run.id,
            task_id=task_id,
            status=ProjectRunStatus.paused,
            active_phase=CHECKPOINT_ACTIVE_PHASE,
            summary=summary_message,
            finished_at=None,
            metadata=metadata,
        )
        send_notification = True

    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found after checkpoint update")
        _sync_checkpoint_task(run, run.metadata_json or {}, task_id=task_id)

    if send_notification:
        try:
            notify_project_run_status(run_id, "paused")
        except Exception:
            logger.exception("failed to send checkpoint notification for run %s", run_id)
    return task_id


def apply_checkpoint_response(
    run_id: str,
    *,
    action: Literal["approve", "reject"],
    comment: str | None = None,
    response_source: str | None = None,
) -> dict[str, Any]:
    normalized_action = str(action).strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise ValueError("unsupported checkpoint action")
    note = str(comment or "").strip() or None
    normalized_source = str(response_source or "").strip().lower() or None

    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found")

        metadata = build_checkpoint_settings(run.metadata_json or {}, reset_state=False)
        current_pending = pending_checkpoint(metadata)
        if (
            not current_pending
            or str(current_pending.get("status") or "").strip().lower() != "pending"
        ):
            raise ValueError("当前运行没有待处理的人机确认")

        responded_at = datetime.now(UTC).isoformat()
        checkpoint_type = str(current_pending.get("type") or "").strip().lower() or "preflight"
        resume_stage_id = str(current_pending.get("resume_stage_id") or "").strip() or None
        resume_stage_label = str(current_pending.get("resume_stage_label") or "").strip() or None

        last_checkpoint = dict(current_pending)
        last_checkpoint["status"] = "approved" if normalized_action == "approve" else "rejected"
        last_checkpoint["responded_at"] = responded_at
        if note:
            last_checkpoint["comment"] = note
        if normalized_source:
            last_checkpoint["response_source"] = normalized_source

        metadata["last_checkpoint"] = last_checkpoint
        metadata["pending_checkpoint"] = None
        metadata["checkpoint_response_comment"] = note
        metadata["checkpoint_response_source"] = normalized_source
        task_id = str(run.task_id or "").strip() or None

        if normalized_action == "approve":
            metadata["checkpoint_state"] = "approved"
            metadata["checkpoint_approved_at"] = responded_at
            metadata["checkpoint_resume_stage_id"] = resume_stage_id
            metadata["checkpoint_resume_stage_label"] = resume_stage_label
            approved_message = (
                STAGE_CHECKPOINT_APPROVED_MESSAGE
                if checkpoint_type == "stage_transition"
                else CHECKPOINT_APPROVED_MESSAGE
            )
            project_repo.update_run(
                run.id,
                status=ProjectRunStatus.queued,
                active_phase="queued",
                summary=approved_message,
                finished_at=None,
                metadata=metadata,
            )
            if task_id:
                global_tracker.append_log(task_id, approved_message, level="info")
                global_tracker.set_metadata(
                    task_id,
                    metadata={
                        "checkpoint_state": "approved",
                        "checkpoint_type": checkpoint_type,
                        "checkpoint_resume_stage_id": resume_stage_id,
                        "checkpoint_resume_stage_label": resume_stage_label,
                    },
                )
        else:
            metadata["checkpoint_state"] = "rejected"
            metadata["checkpoint_rejected_at"] = responded_at
            metadata.pop("checkpoint_resume_stage_id", None)
            metadata.pop("checkpoint_resume_stage_label", None)
            rejected_message = (
                STAGE_CHECKPOINT_REJECTED_MESSAGE
                if checkpoint_type == "stage_transition"
                else CHECKPOINT_REJECTED_MESSAGE
            )
            project_repo.update_run(
                run.id,
                status=ProjectRunStatus.cancelled,
                active_phase="cancelled",
                summary=rejected_message,
                finished_at=datetime.now(UTC),
                metadata=metadata,
            )
            if task_id:
                global_tracker.cancel(task_id, error=rejected_message)
        return {
            "action": normalized_action,
            "task_id": task_id,
            "notify_event": "approved" if normalized_action == "approve" else "rejected",
            "checkpoint_type": checkpoint_type,
            "resume_stage_id": resume_stage_id,
            "response_source": normalized_source,
        }


def process_checkpoint_response(
    run_id: str,
    *,
    action: Literal["approve", "reject"],
    comment: str | None = None,
    response_source: str | None = None,
) -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise ValueError("unsupported checkpoint action")

    response = apply_checkpoint_response(
        run_id,
        action=normalized_action,  # type: ignore[arg-type]
        comment=comment,
        response_source=response_source,
    )

    if normalized_action == "approve":
        try:
            from packages.ai.project.execution_service import submit_project_run

            submit_project_run(run_id)
        except Exception as exc:
            failure_summary = f"执行器启动失败：{str(exc)[:180]}"
            with session_scope() as session:
                project_repo = ProjectRepository(session)
                run = project_repo.get_run(run_id)
                if run is not None:
                    metadata = dict(run.metadata_json or {})
                    metadata["error"] = str(exc)
                    project_repo.update_run(
                        run_id,
                        status=ProjectRunStatus.failed,
                        active_phase="failed",
                        summary=failure_summary,
                        finished_at=datetime.now(UTC),
                        metadata=metadata,
                    )
                    if run.task_id:
                        global_tracker.finish(run.task_id, success=False, error=failure_summary)
            try:
                notify_project_run_status(run_id, "failed")
            except Exception:
                logger.exception(
                    "failed to send failure notification after checkpoint submit error for run %s",
                    run_id,
                )
            raise

    try:
        notify_project_run_status(run_id, str(response.get("notify_event") or normalized_action))
    except Exception:
        logger.exception("failed to send checkpoint response notification for run %s", run_id)
    return response


def _sync_checkpoint_task(run, metadata: dict[str, Any], *, task_id: str) -> None:
    title = (
        str(
            run.title or getattr(run.workflow_type, "value", run.workflow_type) or "项目运行"
        ).strip()
        or "项目运行"
    )
    workspace_path = (
        str(run.run_directory or run.remote_workdir or run.workdir or "").strip() or None
    )
    pending = pending_checkpoint(metadata) or {}
    message = (
        str(pending.get("message") or CHECKPOINT_PENDING_MESSAGE).strip()
        or CHECKPOINT_PENDING_MESSAGE
    )
    tracker_metadata = {
        "source": "project",
        "source_id": str(run.id),
        "project_id": str(run.project_id),
        "run_id": str(run.id),
        "log_path": run.log_path,
        "workspace_server_id": run.workspace_server_id or "local",
        "workspace_path": workspace_path,
        "run_directory": run.run_directory,
        "executor_model": getattr(run, "executor_model", None),
        "reviewer_model": run.reviewer_model,
        "checkpoint_state": "pending",
        "checkpoint_required": True,
        "checkpoint_type": str(pending.get("type") or "preflight"),
        "checkpoint_resume_stage_id": str(metadata.get("checkpoint_resume_stage_id") or "").strip()
        or None,
        "checkpoint_resume_stage_label": str(
            metadata.get("checkpoint_resume_stage_label") or ""
        ).strip()
        or None,
        "notification_recipients": list(metadata.get("notification_recipients") or []),
    }
    global_tracker.start(task_id, "project_workflow", title, total=100, metadata=tracker_metadata)
    global_tracker.pause(task_id, message=message)
    global_tracker.append_log(task_id, message, level="info")
