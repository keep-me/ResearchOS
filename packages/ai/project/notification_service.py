from __future__ import annotations

import logging
import threading
from html import escape
from typing import Any

from packages.config import get_settings
from packages.domain.task_tracker import global_tracker
from packages.integrations.email_service import EmailService
from packages.integrations.feishu_service import FeishuNotificationService
from packages.storage.db import session_scope
from packages.storage.repositories import EmailConfigRepository, FeishuConfigRepository, ProjectRepository

logger = logging.getLogger(__name__)

_EMAIL_EVENT_SUBJECTS = {
    "paused": "等待审批",
    "approved": "审批通过",
    "rejected": "审批拒绝",
    "succeeded": "执行完成",
    "failed": "执行失败",
    "cancelled": "执行取消",
}

_FEISHU_EVENT_COLORS = {
    "paused": "yellow",
    "approved": "green",
    "rejected": "red",
    "succeeded": "green",
    "failed": "red",
    "cancelled": "grey",
}

_INTERACTIVE_WAITERS: set[str] = set()
_INTERACTIVE_WAITERS_LOCK = threading.Lock()


def notify_project_run_status(run_id: str, event: str) -> dict[str, Any]:
    normalized_event = str(event or "").strip().lower()
    if not run_id or not normalized_event:
        return {"sent": False, "reason": "missing_run_or_event"}

    with session_scope() as session:
        project_repo = ProjectRepository(session)
        email_repo = EmailConfigRepository(session)
        feishu_repo = FeishuConfigRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            return {"sent": False, "reason": "run_not_found"}

        project = project_repo.get_project(run.project_id)
        metadata = dict(run.metadata_json or {})
        email_config = email_repo.get_active()
        feishu_config = feishu_repo.get_active()
        recipients = _resolve_recipients(metadata)
        project_name = project.name if project else "ResearchOS 项目"
        run_title = run.title or getattr(run.workflow_type, "value", run.workflow_type)
        workflow_label = str(run.workflow_type.value if hasattr(run.workflow_type, "value") else run.workflow_type)

        email_result = {"sent": False, "reason": "email_not_configured"}
        if email_config is not None and recipients:
            subject = _build_subject(project_name, str(run_title), normalized_event)
            html_content = _build_html_body(
                project_name=project_name,
                run=run,
                event=normalized_event,
            )
            text_content = _build_text_body(
                project_name=project_name,
                run=run,
                event=normalized_event,
            )
            email_sent = EmailService(email_config).send_email(
                to_emails=recipients,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
            )
            email_result = {
                "sent": bool(email_sent),
                "recipients": recipients,
            }
        elif not recipients:
            email_result = {"sent": False, "reason": "recipients_missing"}

        feishu_result = {"sent": False, "reason": "feishu_not_configured"}
        if feishu_config is not None:
            service = FeishuNotificationService(
                mode=str(feishu_config.mode or "off"),
                webhook_url=feishu_config.webhook_url,
                webhook_secret=feishu_config.webhook_secret,
                bridge_url=feishu_config.bridge_url,
                timeout_seconds=int(feishu_config.timeout_seconds or 300),
                timeout_action=str(getattr(feishu_config, "timeout_action", "approve") or "approve"),
            )
            title, body, options = _build_feishu_message(
                project_name=project_name,
                workflow_label=workflow_label,
                run=run,
                event=normalized_event,
                metadata=metadata,
            )
            feishu_result = service.send_event(
                event_type="checkpoint" if normalized_event == "paused" else normalized_event,
                title=title,
                body=body,
                color=_FEISHU_EVENT_COLORS.get(normalized_event, "blue"),
                options=options,
                context={
                    "run_id": str(run.id),
                    "project_id": str(run.project_id),
                    "event": normalized_event,
                    "checkpoint_requested_at": metadata.get("checkpoint_requested_at"),
                },
            )
            if (
                normalized_event == "paused"
                and options
                and service.mode == "interactive"
                and service.bridge_url
                and bool(feishu_result.get("sent"))
                and bool(feishu_result.get("bridge_sent"))
            ):
                waiter_started = _start_interactive_checkpoint_waiter(
                    run_id=str(run.id),
                    metadata=metadata,
                    service=service,
                )
                feishu_result["waiter_started"] = waiter_started

        return {
            "sent": bool(email_result.get("sent") or feishu_result.get("sent")),
            "event": normalized_event,
            "email": email_result,
            "feishu": feishu_result,
        }


def _resolve_recipients(metadata: dict[str, Any]) -> list[str]:
    recipients: list[str] = []
    raw = metadata.get("notification_recipients")
    if isinstance(raw, list):
        recipients.extend(str(item or "").strip() for item in raw)
    elif isinstance(raw, str):
        recipients.extend(part.strip() for part in raw.replace(";", ",").split(","))

    settings_default = str(get_settings().notify_default_to or "").strip()
    if settings_default:
        recipients.extend(part.strip() for part in settings_default.replace(";", ",").split(","))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in recipients:
        if not item or "@" not in item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(item)
    return deduped


def _build_subject(project_name: str, run_title: str, event: str) -> str:
    event_label = _EMAIL_EVENT_SUBJECTS.get(event, event or "状态更新")
    return f"[ResearchOS] {project_name} · {run_title} · {event_label}"


def _build_html_body(*, project_name: str, run, event: str) -> str:
    event_label = _EMAIL_EVENT_SUBJECTS.get(event, event or "状态更新")
    workflow_label = str(run.workflow_type.value if hasattr(run.workflow_type, "value") else run.workflow_type)
    prompt = escape(str(run.prompt or "")[:1200]) or "无"
    summary = escape(str(run.summary or "")[:1200]) or "无"
    workspace = escape(str(run.remote_workdir or run.workdir or run.run_directory or "")[:1200]) or "未记录"
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f6f7fb; color: #172033; }}
    .card {{ max-width: 720px; margin: 24px auto; background: #ffffff; border-radius: 18px; padding: 28px; border: 1px solid #e7eaf3; }}
    .tag {{ display: inline-block; padding: 6px 10px; border-radius: 999px; background: #eef3ff; color: #2a4fb8; font-size: 12px; }}
    .label {{ font-size: 12px; color: #667085; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 18px; }}
    .value {{ margin-top: 6px; white-space: pre-wrap; line-height: 1.7; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="tag">{escape(event_label)}</div>
    <h2 style="margin: 14px 0 0;">{escape(project_name)} / {escape(run.title or workflow_label)}</h2>
    <p style="margin: 10px 0 0; color: #475467;">当前运行状态已更新为：{escape(event_label)}</p>
    <div class="label">Workflow</div>
    <div class="value">{escape(workflow_label)}</div>
    <div class="label">摘要</div>
    <div class="value">{summary}</div>
    <div class="label">提示词</div>
    <div class="value">{prompt}</div>
    <div class="label">工作区</div>
    <div class="value">{workspace}</div>
  </div>
</body>
</html>
""".strip()


def _build_text_body(*, project_name: str, run, event: str) -> str:
    event_label = _EMAIL_EVENT_SUBJECTS.get(event, event or "状态更新")
    workflow_label = str(run.workflow_type.value if hasattr(run.workflow_type, "value") else run.workflow_type)
    return "\n".join(
        [
            f"项目: {project_name}",
            f"运行: {run.title or workflow_label}",
            f"状态: {event_label}",
            f"Workflow: {workflow_label}",
            f"摘要: {run.summary or '无'}",
            f"提示词: {run.prompt or '无'}",
            f"工作区: {run.remote_workdir or run.workdir or run.run_directory or '未记录'}",
        ]
    ).strip()


def _build_feishu_message(
    *,
    project_name: str,
    workflow_label: str,
    run,
    event: str,
    metadata: dict[str, Any],
) -> tuple[str, str, list[str] | None]:
    event_label = _EMAIL_EVENT_SUBJECTS.get(event, event or "状态更新")
    workspace = str(run.remote_workdir or run.workdir or run.run_directory or "未记录").strip() or "未记录"
    title = f"ResearchOS · {project_name} · {event_label}"
    pending = metadata.get("pending_checkpoint") if isinstance(metadata.get("pending_checkpoint"), dict) else {}
    checkpoint_type = str(pending.get("type") or "").strip().lower()
    body_lines = [
        f"**项目**：{project_name}",
        f"**运行**：{run.title or workflow_label}",
        f"**状态**：{event_label}",
        f"**Workflow**：{workflow_label}",
        f"**摘要**：{str(run.summary or '无')[:800]}",
        f"**工作区**：`{workspace[:1000]}`",
    ]
    options: list[str] | None = None
    if event == "paused":
        body_lines.append(f"**确认类型**：{'阶段确认' if checkpoint_type == 'stage_transition' else '运行前确认'}")
        if checkpoint_type == "stage_transition":
            completed = str(pending.get("completed_stage_label") or pending.get("completed_stage_id") or "当前阶段").strip()
            resume = str(pending.get("resume_stage_label") or pending.get("resume_stage_id") or "下一阶段").strip()
            body_lines.append(f"**阶段流转**：{completed} → {resume}")
            stage_summary = str(pending.get("stage_summary") or "").strip()
            if stage_summary:
                body_lines.append(f"**阶段摘要**：{stage_summary[:800]}")
        options = ["approve", "reject"]
    elif event in {"failed", "rejected", "cancelled"} and str(run.summary or "").strip():
        body_lines.append(f"**详情**：{str(run.summary)[:800]}")
    return title, "\n".join(body_lines), options


def _start_interactive_checkpoint_waiter(
    *,
    run_id: str,
    metadata: dict[str, Any],
    service: FeishuNotificationService,
) -> bool:
    waiter_key = _checkpoint_waiter_key(run_id, metadata)
    with _INTERACTIVE_WAITERS_LOCK:
        if waiter_key in _INTERACTIVE_WAITERS:
            return False
        _INTERACTIVE_WAITERS.add(waiter_key)

    _append_checkpoint_log(
        run_id,
        f"已发送飞书交互审批，等待桥接回复（超时 {service.timeout_seconds} 秒）。",
        level="info",
    )
    worker = threading.Thread(
        target=_await_interactive_checkpoint_reply,
        args=(run_id, waiter_key, service),
        daemon=True,
        name=f"feishu-checkpoint-{run_id[:8]}",
    )
    worker.start()
    return True


def _await_interactive_checkpoint_reply(
    run_id: str,
    waiter_key: str,
    service: FeishuNotificationService,
) -> None:
    try:
        result = service.poll_reply()
        if not result.get("ok"):
            reason = str(result.get("reason") or "bridge_unavailable")
            _append_checkpoint_log(run_id, f"飞书桥接轮询失败：{reason}", level="warning")
            return
        if result.get("timeout"):
            _handle_interactive_timeout(run_id, service)
            return

        action, comment = _normalize_checkpoint_reply(result.get("reply"))
        if not action:
            reply_preview = str(result.get("reply") or "").strip()[:120] or "空回复"
            _append_checkpoint_log(run_id, f"收到飞书回复但未识别为 approve / reject：{reply_preview}", level="warning")
            return

        _append_checkpoint_log(
            run_id,
            f"已收到飞书审批结果：{'批准继续' if action == 'approve' else '拒绝继续'}。",
            level="info",
        )
        try:
            from packages.ai.project.checkpoint_service import process_checkpoint_response

            process_checkpoint_response(
                run_id,
                action=action,
                comment=comment,
                response_source="feishu_interactive",
            )
        except ValueError as exc:
            _append_checkpoint_log(run_id, f"飞书审批结果未生效：{str(exc)[:180]}", level="warning")
        except Exception:
            logger.exception("failed to process interactive feishu checkpoint reply for run %s", run_id)
            _append_checkpoint_log(run_id, "飞书审批回执处理失败，请在项目页手动确认。", level="error")
    finally:
        with _INTERACTIVE_WAITERS_LOCK:
            _INTERACTIVE_WAITERS.discard(waiter_key)


def _checkpoint_waiter_key(run_id: str, metadata: dict[str, Any]) -> str:
    pending = metadata.get("pending_checkpoint") if isinstance(metadata.get("pending_checkpoint"), dict) else {}
    requested_at = str(pending.get("requested_at") or metadata.get("checkpoint_requested_at") or "").strip() or "pending"
    return f"{run_id}:{requested_at}"


def _append_checkpoint_log(run_id: str, message: str, *, level: str = "info") -> None:
    task_id = None
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is not None:
            task_id = str(run.task_id or "").strip() or None
    if task_id:
        global_tracker.append_log(task_id, message[:300], level=level)


def _handle_interactive_timeout(run_id: str, service: FeishuNotificationService) -> None:
    timeout_action = str(getattr(service, "timeout_action", "approve") or "approve").strip().lower() or "approve"
    if timeout_action == "wait":
        _append_checkpoint_log(run_id, "飞书交互审批等待超时，当前运行保持暂停，仍可在前端或后续消息中继续审批。", level="warning")
        return

    action = "approve" if timeout_action == "approve" else "reject"
    label = "自动批准继续" if action == "approve" else "自动拒绝继续"
    _append_checkpoint_log(run_id, f"飞书交互审批等待超时，已按超时策略执行：{label}。", level="warning")
    try:
        from packages.ai.project.checkpoint_service import process_checkpoint_response

        process_checkpoint_response(
            run_id,
            action=action,
            comment=f"Feishu interactive timeout -> {action}",
            response_source=f"feishu_timeout_{action}",
        )
    except ValueError as exc:
        _append_checkpoint_log(run_id, f"超时策略未生效：{str(exc)[:180]}", level="warning")
    except Exception:
        logger.exception("failed to apply timeout action %s for run %s", action, run_id)
        _append_checkpoint_log(run_id, "飞书超时策略执行失败，请在项目页手动确认。", level="error")


def _normalize_checkpoint_reply(reply: Any) -> tuple[str | None, str | None]:
    raw = str(reply or "").strip()
    if not raw:
        return None, None

    normalized = raw.lower().strip()
    separators = (":", "：", "-", " ", "\n")
    approve_tokens = {
        "approve", "approved", "yes", "y", "ok", "go", "continue", "同意", "通过", "批准", "继续",
    }
    reject_tokens = {
        "reject", "rejected", "no", "n", "stop", "cancel", "deny", "拒绝", "不同意", "停止", "取消",
    }

    if normalized in approve_tokens:
        return "approve", None
    if normalized in reject_tokens:
        return "reject", None

    for token in approve_tokens:
        for separator in separators:
            prefix = f"{token}{separator}"
            if normalized.startswith(prefix):
                return "approve", raw[len(prefix):].strip() or None

    for token in reject_tokens:
        for separator in separators:
            prefix = f"{token}{separator}"
            if normalized.startswith(prefix):
                return "reject", raw[len(prefix):].strip() or None

    return None, None
