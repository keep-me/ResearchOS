"""
统一任务追踪与执行框架 — 替代原来分散的 4 套任务系统

功能：
- 全局任务进度追踪（前端轮询可见）
- 后台任务提交与执行（线程池管理）
- 统一 start / update / finish 生命周期
- 线程安全 + 自动清理过期任务
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# 完成后保留 1 小时供任务后台查看
_FINISHED_TTL = 3600
_PULSE_IDLE_SECONDS = 3.0
_PULSE_INTERVAL_SECONDS = 2.0
_PULSE_STEP_PCT = 1.4
_PULSE_MAX_AHEAD_PCT = 15.0
_PULSE_ABSOLUTE_CAP_PCT = 97.0


class TaskCancelledError(RuntimeError):
    """Raised when a running task receives a cancel request."""


class TaskPausedError(RuntimeError):
    """Raised when a running task should transition into a paused state."""


_FAILED_RESULT_STATUSES = {"failed", "failure", "error"}


def _failed_result_error(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    status = str(result.get("status") or "").strip().lower()
    if status not in _FAILED_RESULT_STATUSES:
        return None
    error = result.get("error") or result.get("message") or status
    return str(error)[:200]


@dataclass
class TaskInfo:
    task_id: str
    task_type: str
    title: str
    current: int = 0
    total: int = 0
    message: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished: bool = False
    success: bool = True
    error: str | None = None
    result: Any = None
    finished_at: float | None = None
    cancel_requested: bool = False
    cancelled: bool = False
    paused: bool = False
    display_progress_pct: float = 0.0
    display_updated_at: float = field(default_factory=time.time)
    source: str | None = None
    source_id: str | None = None
    project_id: str | None = None
    paper_id: str | None = None
    run_id: str | None = None
    action_id: str | None = None
    log_path: str | None = None
    artifact_refs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    retry_supported: bool = False
    retry_label: str | None = None
    retry_metadata: dict[str, Any] = field(default_factory=dict)
    retry_handler: Callable[[], Any] | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        now = time.time()
        elapsed_end = self.finished_at if self.finished and self.finished_at else now
        elapsed = max(0.0, elapsed_end - self.started_at)
        effective_total = self.total if self.total > 0 else (1 if self.finished else 0)
        effective_current = self.current
        if self.finished and effective_total > 0 and not self.cancelled:
            effective_current = max(effective_current, effective_total)
        real_progress_pct = (
            round(effective_current / effective_total * 100) if effective_total > 0 else 0
        )

        if self.finished:
            if self.cancelled:
                progress_pct = float(max(0, min(real_progress_pct, 99)))
            else:
                progress_pct = float(
                    max(real_progress_pct, 100 if effective_total > 0 else real_progress_pct)
                )
        else:
            # Keep display progress monotonic and gently pulse during idle windows.
            display_pct = max(float(real_progress_pct), float(self.display_progress_pct))
            idle_seconds = max(0.0, now - self.updated_at)
            if idle_seconds >= _PULSE_IDLE_SECONDS:
                since_display = max(0.0, now - self.display_updated_at)
                pulse_delta = (since_display / _PULSE_INTERVAL_SECONDS) * _PULSE_STEP_PCT
                if pulse_delta > 0:
                    pulse_cap = _PULSE_ABSOLUTE_CAP_PCT
                    if effective_total > 0:
                        pulse_cap = min(
                            _PULSE_ABSOLUTE_CAP_PCT, float(real_progress_pct) + _PULSE_MAX_AHEAD_PCT
                        )
                    display_pct = min(pulse_cap, display_pct + pulse_delta)
            progress_pct = display_pct
            self.display_updated_at = now

        self.display_progress_pct = float(progress_pct)
        progress_pct_rounded = int(round(progress_pct))
        display_current = effective_current
        if not self.finished and effective_total > 0:
            pulsed_current = int(round((progress_pct_rounded / 100) * effective_total))
            display_current = max(effective_current, pulsed_current)
            if display_current >= effective_total:
                display_current = max(effective_total - 1, effective_current)
        elif self.finished:
            display_current = effective_current

        status = "running"
        if self.finished:
            if self.cancelled:
                status = "cancelled"
            else:
                status = "completed" if self.success else "failed"
        elif self.paused:
            status = "paused"
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "title": self.title,
            "current": display_current,
            "total": effective_total,
            "message": self.message,
            "elapsed_seconds": round(elapsed, 1),
            "progress_pct": progress_pct_rounded,
            "finished": self.finished,
            "success": self.success,
            "error": self.error,
            "has_result": self.result is not None,
            "cancel_requested": self.cancel_requested,
            "cancelled": self.cancelled,
            "paused": self.paused,
            "status": status,
            "progress": round(progress_pct_rounded / 100, 4),
            "actual_current": effective_current,
            "actual_progress_pct": int(real_progress_pct),
            "created_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "source": self.source,
            "source_id": self.source_id,
            "project_id": self.project_id,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "action_id": self.action_id,
            "log_path": self.log_path,
            "artifact_refs": list(self.artifact_refs),
            "metadata": dict(self.metadata),
            "retry_supported": bool(self.retry_supported or self.retry_handler),
            "retry_label": self.retry_label,
            "retry_metadata": dict(self.retry_metadata),
            "log_count": len(self.logs),
        }


class TaskTracker:
    """
    统一的全局任务追踪器（线程安全，纯内存）

    两种使用方式：
    1. 纯追踪：手动调用 start/update/finish 管理生命周期
    2. 提交执行：调用 submit() 自动在后台线程执行 + 追踪
    """

    def __init__(self):
        self._tasks: dict[str, TaskInfo] = {}
        self._lock = threading.Lock()

    def bootstrap_from_store(self) -> None:
        """在服务启动时恢复持久化状态，并标记重启中断的任务。"""
        try:
            from packages.storage.db import session_scope
            from packages.storage.repositories import TaskRepository

            with session_scope() as session:
                TaskRepository(session).mark_incomplete_as_interrupted()
        except Exception as exc:  # pragma: no cover - best effort bootstrap
            logger.debug("Task tracker bootstrap skipped: %s", exc)

    # ---------- 生命周期管理（纯追踪） ----------

    def start(
        self,
        task_id: str,
        task_type: str,
        title: str,
        total: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> TaskInfo:
        """注册一个任务，开始追踪"""
        task = TaskInfo(
            task_id=task_id,
            task_type=task_type,
            title=title,
            total=total,
        )
        task.paused = False
        self._apply_metadata(task, metadata or {})
        if total > 0:
            task.display_progress_pct = max(0.0, min(100.0, round((task.current / total) * 100, 2)))
        task.display_updated_at = time.time()
        with self._lock:
            self._cleanup()
            self._tasks[task_id] = task
        self._sync_task(task)
        return task

    def update(self, task_id: str, current: int, message: str = "", total: int | None = None):
        """更新任务进度"""
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                if task.finished:
                    return
                task.paused = False
                task.current = current
                task.message = message
                if total is not None:
                    task.total = total
                task.updated_at = time.time()
                if task.total > 0:
                    real_pct = max(0.0, min(100.0, (task.current / task.total) * 100))
                    task.display_progress_pct = max(task.display_progress_pct, real_pct)
                task.display_updated_at = task.updated_at
                task_snapshot = task
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def finish(self, task_id: str, success: bool = True, error: str | None = None):
        """标记任务完成"""
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            task = self._load_persisted_task(task_id)
        if task:
            if task.finished:
                return
            task.finished = True
            task.paused = False
            task.success = success
            task.error = error
            task.updated_at = time.time()
            task.finished_at = task.updated_at
            if task.total <= 0:
                task.total = 1
            task.current = task.total
            task.display_progress_pct = 100.0
            task.display_updated_at = task.updated_at
            task_snapshot = task
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id] = task_snapshot
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def request_cancel(self, task_id: str) -> dict | None:
        """请求终止任务（协作式取消）。"""
        task_snapshot: TaskInfo | None = None
        paused_snapshot = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                task = None
            else:
                if task.finished:
                    return task.to_dict()
                if task.paused:
                    paused_snapshot = True
                    task.cancel_requested = True
                else:
                    task.cancel_requested = True
                    task.message = "收到终止请求，正在停止任务..."
                    task.updated_at = time.time()
                    task.display_updated_at = task.updated_at
                task_snapshot = task
        if task_snapshot is not None and paused_snapshot:
            self.cancel(task_id, error="任务已终止")
            return self.get_task(task_id)
        if task_snapshot is not None:
            self._sync_task(task_snapshot)
            return task_snapshot.to_dict()

        persisted = self._load_persisted_task(task_id)
        if not persisted:
            return None
        if persisted.finished:
            return persisted.to_dict()
        if persisted.paused:
            self.cancel(task_id, error="任务已终止")
            return self.get_task(task_id)
        persisted.cancel_requested = True
        persisted.message = "收到终止请求，正在停止任务..."
        persisted.updated_at = time.time()
        persisted.display_updated_at = persisted.updated_at
        self._sync_task(persisted)
        return persisted.to_dict()

    def cancel(self, task_id: str, error: str = "任务已终止") -> None:
        """将任务标记为已终止。"""
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            task = self._load_persisted_task(task_id)
        if not task:
            return
        if task.finished and task.cancelled:
            return
        task.cancel_requested = True
        task.cancelled = True
        task.paused = False
        task.finished = True
        task.success = False
        task.error = error
        task.message = error
        task.updated_at = time.time()
        task.finished_at = task.updated_at
        if task.total <= 0:
            task.total = max(1, task.current)
        if task.total > 0 and task.current >= task.total:
            task.current = max(0, task.total - 1)
        if task.total > 0:
            real_pct = max(0.0, min(99.0, (task.current / task.total) * 100))
            task.display_progress_pct = max(min(task.display_progress_pct, 99.0), real_pct)
        task.display_updated_at = task.updated_at
        task_snapshot = task
        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is not None:
                self._tasks[task_id] = task_snapshot
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def is_cancel_requested(self, task_id: str) -> bool:
        """查询任务是否已请求取消。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            return bool(task.cancel_requested or task.cancelled)

    def pause(self, task_id: str, *, message: str = "任务已暂停") -> None:
        """将任务置为暂停状态。"""
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.finished:
                return
            task.paused = True
            task.message = str(message or "任务已暂停")
            task.updated_at = time.time()
            task.display_updated_at = task.updated_at
            task_snapshot = task
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def set_result(self, task_id: str, result: Any) -> None:
        """写入任务结果，便于任务后台或结果页读取。"""
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.result = result
                task.updated_at = time.time()
                task_snapshot = task
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def set_metadata(
        self, task_id: str, metadata: dict[str, Any] | None = None, **extra: Any
    ) -> None:
        """补充任务元数据，用于任务中心展示与跳转。"""
        merged = dict(metadata or {})
        merged.update(extra)
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            self._apply_metadata(task, merged)
            task.updated_at = time.time()
            task_snapshot = task
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def append_log(self, task_id: str, message: str, *, level: str = "info") -> None:
        """向任务附加日志预览。"""
        text = str(message or "").strip()
        if not text:
            return
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.logs.append(
                {
                    "timestamp": time.time(),
                    "level": str(level or "info").strip() or "info",
                    "message": text[:4000],
                }
            )
            if len(task.logs) > 200:
                task.logs = task.logs[-200:]
            task.updated_at = time.time()
            task_snapshot = task
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def list_logs(self, task_id: str, limit: int = 120) -> list[dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                task = None
            else:
                return list(task.logs[-max(1, limit) :])
        persisted = self._load_persisted_task(task_id)
        if not persisted:
            return []
        return list(persisted.logs[-max(1, limit) :])

    def register_retry(
        self,
        task_id: str,
        handler: Callable[[], Any] | None,
        *,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        task_snapshot: TaskInfo | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.retry_handler = handler
            task.retry_supported = bool(handler is not None or metadata)
            task.retry_label = str(label or "").strip() or None
            task.retry_metadata = dict(metadata or {})
            task.updated_at = time.time()
            task_snapshot = task
        if task_snapshot is not None:
            self._sync_task(task_snapshot)

    def retry(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            handler = task.retry_handler if task else None
            label = task.retry_label if task else None
            metadata = dict(task.retry_metadata) if task else {}
            action_id = task.action_id if task else None
            run_id = task.run_id if task else None
        if handler is None:
            persisted = self._load_persisted_task(task_id)
            if persisted is None:
                return None
            label = persisted.retry_label
            metadata = dict(persisted.retry_metadata)
            action_id = persisted.action_id
            run_id = persisted.run_id
            handler = self._build_retry_handler(
                retry_metadata=metadata,
                run_id=run_id,
                action_id=action_id,
            )
        if handler is None:
            return None
        result = handler()
        payload: dict[str, Any] = {
            "task_id": task_id,
            "triggered": True,
            "retry_label": label,
            "retry_metadata": metadata,
        }
        if isinstance(result, str):
            payload["next_task_id"] = result
        elif isinstance(result, dict):
            payload.update(result)
        elif result is not None:
            payload["result"] = result
        return payload

    # ---------- 提交执行（追踪 + 后台线程） ----------

    def submit(
        self,
        task_type: str,
        title: str,
        fn: Callable[..., Any],
        *args: Any,
        task_id: str | None = None,
        total: int = 100,
        metadata: dict[str, Any] | None = None,
        on_retry: Callable[[], Any] | None = None,
        retry_label: str | None = None,
        retry_metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """
        提交后台任务，自动追踪进度

        fn 可接收 progress_callback(message, current, total) 参数
        返回 task_id
        """
        task_id = task_id or f"{task_type}_{uuid.uuid4().hex[:8]}"
        self.start(task_id, task_type, title, total=total, metadata=metadata)
        if on_retry is not None:
            self.register_retry(
                task_id,
                on_retry,
                label=retry_label,
                metadata=retry_metadata,
            )
        self.append_log(task_id, f"任务已启动: {title}")

        def _progress(message: str, current: int, total_hint: int):
            if self.is_cancel_requested(task_id):
                raise TaskCancelledError("任务已终止")
            self.update(task_id, current, message, total=total_hint or total)
            self.append_log(task_id, message)

        def _run():
            try:
                result = fn(
                    *args,
                    progress_callback=_progress,
                    **kwargs,
                )
                if self.is_cancel_requested(task_id):
                    raise TaskCancelledError("任务已终止")
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task and not task.cancelled:
                        task.result = result
                failed_error = _failed_result_error(result)
                if failed_error:
                    self.finish(task_id, success=False, error=failed_error)
                    self.append_log(task_id, failed_error, level="error")
                    logger.info("Task %s finished with failed result: %s", task_id, title)
                else:
                    self.finish(task_id, success=True)
                    self.append_log(task_id, "任务执行完成", level="success")
                    logger.info("Task %s completed: %s", task_id, title)
            except TaskPausedError as exc:
                message = str(exc).strip() or "任务已暂停"
                self.pause(task_id, message=message[:200])
                self.append_log(task_id, message[:200], level="info")
                logger.info("Task %s paused: %s", task_id, title)
            except TaskCancelledError as exc:
                self.cancel(task_id, error=str(exc)[:200])
                self.append_log(task_id, str(exc)[:200], level="warning")
                logger.info("Task %s cancelled: %s", task_id, title)
            except Exception as exc:
                if self.is_cancel_requested(task_id):
                    self.cancel(task_id, error="任务已终止")
                    self.append_log(task_id, "任务已终止", level="warning")
                    logger.info("Task %s cancelled during failure path: %s", task_id, title)
                    return
                self.finish(task_id, success=False, error=str(exc)[:200])
                self.append_log(task_id, str(exc)[:200], level="error")
                logger.error("Task %s failed: %s - %s", task_id, title, exc)

        thread = threading.Thread(target=_run, daemon=True, name=f"task-{task_id}")
        thread.start()
        return task_id

    # ---------- 查询 ----------

    def get_active(self) -> list[dict]:
        """获取所有活跃任务（含刚完成的）"""
        return self.list_tasks(limit=200)

    def get_task(self, task_id: str) -> dict | None:
        """查询单个任务状态"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return task.to_dict()
        persisted = self._load_persisted_task(task_id)
        return persisted.to_dict() if persisted else None

    def get_result(self, task_id: str) -> Any | None:
        """获取已完成任务的结果"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return task.result
        persisted = self._load_persisted_task(task_id)
        return persisted.result if persisted else None

    def list_tasks(self, task_type: str | None = None, limit: int = 100) -> list[dict]:
        """列出最近任务，支持按类型筛选。"""
        with self._lock:
            self._cleanup()
            tasks = list(self._tasks.values())
        if task_type:
            tasks = [task for task in tasks if task.task_type == task_type]
        items = [task.to_dict() for task in tasks]
        seen = {item["task_id"] for item in items}
        for persisted in self._list_persisted_tasks(
            task_type=task_type, limit=max(limit * 3, limit)
        ):
            if persisted.task_id in seen:
                continue
            items.append(persisted.to_dict())
        items.sort(
            key=lambda item: (
                item.get("finished_at") or item.get("updated_at") or item.get("created_at") or 0
            ),
            reverse=True,
        )
        return items[: max(1, limit)]

    def forget_tasks(self, task_ids: list[str], *, delete_persisted: bool = True) -> int:
        normalized = [
            str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()
        ]
        if not normalized:
            return 0
        with self._lock:
            for task_id in normalized:
                self._tasks.pop(task_id, None)
        if not delete_persisted:
            return len(normalized)
        try:
            from packages.storage.db import session_scope
            from packages.storage.repositories import TaskRepository

            with session_scope() as session:
                return TaskRepository(session).delete_tasks(normalized)
        except Exception as exc:  # pragma: no cover - persistence is best effort
            logger.debug("task delete skipped: %s", exc)
            return len(normalized)

    def forget_task(self, task_id: str, *, delete_persisted: bool = True) -> bool:
        return self.forget_tasks([task_id], delete_persisted=delete_persisted) > 0

    # ---------- 内部清理 ----------

    def _cleanup(self):
        """清除完成超过 TTL 的任务"""
        now = time.time()
        expired = [
            tid
            for tid, t in self._tasks.items()
            if t.finished
            and (now - (t.finished_at or t.updated_at or t.started_at)) > _FINISHED_TTL
        ]
        for tid in expired:
            del self._tasks[tid]

    def _apply_metadata(self, task: TaskInfo, metadata: dict[str, Any]) -> None:
        if not metadata:
            return
        for key in (
            "source",
            "source_id",
            "project_id",
            "paper_id",
            "run_id",
            "action_id",
            "log_path",
            "retry_label",
        ):
            value = metadata.get(key)
            if value is not None:
                setattr(task, key, str(value).strip() or None)

        artifact_refs = metadata.get("artifact_refs")
        if isinstance(artifact_refs, list):
            task.artifact_refs = [item for item in artifact_refs if isinstance(item, dict)]

        retry_metadata = metadata.get("retry_metadata")
        if isinstance(retry_metadata, dict):
            task.retry_metadata = dict(retry_metadata)

        extra_metadata = metadata.get("metadata")
        if isinstance(extra_metadata, dict):
            task.metadata.update(extra_metadata)

        for key, value in metadata.items():
            if key in {
                "source",
                "source_id",
                "project_id",
                "paper_id",
                "run_id",
                "action_id",
                "log_path",
                "artifact_refs",
                "retry_label",
                "retry_metadata",
                "metadata",
            }:
                continue
            task.metadata[key] = value

    def _sync_task(self, task: TaskInfo) -> None:
        try:
            from packages.storage.db import session_scope
            from packages.storage.repositories import TaskRepository

            with session_scope() as session:
                TaskRepository(session).upsert_task(**self._task_payload(task))
        except Exception as exc:  # pragma: no cover - persistence is best effort
            logger.debug("task persistence skipped for %s: %s", task.task_id, exc)

    def _load_persisted_task(self, task_id: str) -> TaskInfo | None:
        try:
            from packages.storage.db import session_scope
            from packages.storage.repositories import TaskRepository

            with session_scope() as session:
                record = TaskRepository(session).get_task(task_id)
                if record is None:
                    return None
                return self._task_from_record(record)
        except Exception as exc:  # pragma: no cover - persistence is best effort
            logger.debug("task load skipped for %s: %s", task_id, exc)
            return None

    def _list_persisted_tasks(
        self, task_type: str | None = None, limit: int = 100
    ) -> list[TaskInfo]:
        try:
            from packages.storage.db import session_scope
            from packages.storage.repositories import TaskRepository

            with session_scope() as session:
                rows = TaskRepository(session).list_tasks(task_type=task_type, limit=limit)
                return [self._task_from_record(record) for record in rows]
        except Exception as exc:  # pragma: no cover - persistence is best effort
            logger.debug("task list skipped: %s", exc)
            return []

    def _task_payload(self, task: TaskInfo) -> dict[str, Any]:
        status = task.to_dict()
        return {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "title": task.title,
            "current": task.current,
            "total": task.total,
            "message": task.message,
            "status": str(status.get("status") or "running"),
            "finished": bool(task.finished),
            "success": bool(task.success),
            "error": task.error,
            "result_json": self._json_safe(task.result),
            "cancel_requested": bool(task.cancel_requested),
            "cancelled": bool(task.cancelled),
            "progress_pct": float(status.get("progress_pct") or 0.0),
            "source": task.source,
            "source_id": task.source_id,
            "project_id": task.project_id,
            "paper_id": task.paper_id,
            "run_id": task.run_id,
            "action_id": task.action_id,
            "log_path": task.log_path,
            "artifact_refs_json": list(task.artifact_refs),
            "metadata_json": dict(task.metadata),
            "logs_json": list(task.logs),
            "retry_supported": bool(task.retry_supported or task.retry_handler),
            "retry_label": task.retry_label,
            "retry_metadata_json": dict(task.retry_metadata),
            "started_at": self._timestamp_to_datetime(task.started_at),
            "updated_at": self._timestamp_to_datetime(task.updated_at),
            "finished_at": self._timestamp_to_datetime(task.finished_at)
            if task.finished_at
            else None,
        }

    def _task_from_record(self, record) -> TaskInfo:
        return TaskInfo(
            task_id=str(record.task_id),
            task_type=str(record.task_type or ""),
            title=str(record.title or ""),
            current=int(record.current or 0),
            total=int(record.total or 0),
            message=str(record.message or ""),
            started_at=record.started_at.timestamp() if record.started_at else time.time(),
            updated_at=record.updated_at.timestamp() if record.updated_at else time.time(),
            finished=bool(record.finished),
            success=bool(record.success),
            error=record.error,
            result=record.result_json,
            finished_at=record.finished_at.timestamp() if record.finished_at else None,
            cancel_requested=bool(record.cancel_requested),
            cancelled=bool(record.cancelled),
            paused=str(record.status or "").strip().lower() == "paused",
            display_progress_pct=float(record.progress_pct or 0.0),
            display_updated_at=record.updated_at.timestamp() if record.updated_at else time.time(),
            source=record.source,
            source_id=record.source_id,
            project_id=record.project_id,
            paper_id=record.paper_id,
            run_id=record.run_id,
            action_id=record.action_id,
            log_path=record.log_path,
            artifact_refs=list(record.artifact_refs_json or []),
            metadata=dict(record.metadata_json or {}),
            logs=list(record.logs_json or []),
            retry_supported=bool(record.retry_supported),
            retry_label=record.retry_label,
            retry_metadata=dict(record.retry_metadata_json or {}),
        )

    def _build_retry_handler(
        self,
        *,
        retry_metadata: dict[str, Any] | None,
        run_id: str | None,
        action_id: str | None,
    ) -> Callable[[], Any] | None:
        metadata = dict(retry_metadata or {})
        resolved_action_id = str(action_id or metadata.get("action_id") or "").strip()
        if resolved_action_id:
            from packages.ai.project.run_action_service import submit_project_run_action

            return lambda: submit_project_run_action(resolved_action_id)
        resolved_run_id = str(run_id or metadata.get("run_id") or "").strip()
        if resolved_run_id:
            from packages.ai.project.execution_service import submit_project_run

            return lambda: submit_project_run(resolved_run_id)
        return None

    @staticmethod
    def _timestamp_to_datetime(value: float | None) -> datetime:
        return datetime.fromtimestamp(value or time.time(), tz=UTC)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None:
            return None
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except Exception:
            return {"repr": str(value)}


# 全局单例 — 整个应用共享一个 tracker
global_tracker = TaskTracker()
