"""Task-tracker repository extracted from the monolithic repository module."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from packages.storage.json_schema import versioned_list, with_schema_version
from packages.storage.models import TaskLog, TaskRecord


class TaskRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.session.get(TaskRecord, task_id)

    def upsert_task(
        self,
        *,
        task_id: str,
        task_type: str,
        title: str,
        current: int,
        total: int,
        message: str,
        status: str,
        finished: bool,
        success: bool,
        error: str | None,
        result_json,
        cancel_requested: bool,
        cancelled: bool,
        progress_pct: float,
        source: str | None,
        source_id: str | None,
        project_id: str | None,
        paper_id: str | None,
        run_id: str | None,
        action_id: str | None,
        log_path: str | None,
        artifact_refs_json: list[dict] | None,
        metadata_json: dict | None,
        logs_json: list[dict] | None,
        retry_supported: bool,
        retry_label: str | None,
        retry_metadata_json: dict | None,
        started_at: datetime,
        updated_at: datetime,
        finished_at: datetime | None,
    ) -> TaskRecord:
        record = self.get_task(task_id)
        if record is None:
            record = TaskRecord(task_id=task_id)
            self.session.add(record)
        record.task_type = task_type
        record.title = title
        record.current = int(current or 0)
        record.total = int(total or 0)
        record.message = message or ""
        record.status = status or "running"
        record.finished = bool(finished)
        record.success = bool(success)
        record.error = error
        record.result_json = result_json
        record.cancel_requested = bool(cancel_requested)
        record.cancelled = bool(cancelled)
        record.progress_pct = float(progress_pct or 0.0)
        record.source = source
        record.source_id = source_id
        record.project_id = project_id
        record.paper_id = paper_id
        record.run_id = run_id
        record.action_id = action_id
        record.log_path = log_path
        record.artifact_refs_json = versioned_list(artifact_refs_json)
        record.metadata_json = with_schema_version(metadata_json)
        record.logs_json = versioned_list(logs_json)
        record.retry_supported = bool(retry_supported)
        record.retry_label = retry_label
        record.retry_metadata_json = with_schema_version(retry_metadata_json)
        record.started_at = started_at
        record.updated_at = updated_at
        record.finished_at = finished_at
        self.session.flush()
        self._replace_task_log_rows(record.task_id, record.logs_json)
        return record

    def _replace_task_log_rows(self, task_id: str, logs: list[dict]) -> None:
        self.session.execute(delete(TaskLog).where(TaskLog.task_id == task_id))
        for item in logs:
            if not isinstance(item, dict):
                continue
            self.session.add(
                TaskLog(
                    task_id=task_id,
                    level=str(item.get("level") or "info")[:32],
                    message=str(item.get("message") or item.get("text") or ""),
                    data_json=dict(item),
                )
            )
        self.session.flush()

    def list_tasks(self, task_type: str | None = None, limit: int = 100) -> list[TaskRecord]:
        query = select(TaskRecord)
        if task_type:
            query = query.where(TaskRecord.task_type == task_type)
        query = query.order_by(
            func.coalesce(TaskRecord.finished_at, TaskRecord.updated_at, TaskRecord.started_at).desc()
        ).limit(max(1, limit))
        rows = self.session.execute(query).scalars().all()
        return list(rows)

    def delete_task(self, task_id: str) -> bool:
        record = self.get_task(task_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def delete_tasks(self, task_ids: list[str]) -> int:
        normalized = [str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()]
        if not normalized:
            return 0
        statement = delete(TaskRecord).where(TaskRecord.task_id.in_(normalized))
        result = self.session.execute(statement)
        self.session.flush()
        return int(result.rowcount or 0)

    def mark_incomplete_as_interrupted(
        self,
        *,
        message: str = "任务在服务重启后中断，请手动重新运行。",
    ) -> int:
        now = datetime.now(UTC)
        statement = (
            update(TaskRecord)
            .where(TaskRecord.finished == False)  # noqa: E712
            .where(TaskRecord.status != "paused")
            .values(
                finished=True,
                success=False,
                status="failed",
                error=message,
                message=message,
                finished_at=now,
                updated_at=now,
            )
        )
        result = self.session.execute(statement)
        self.session.flush()
        return int(result.rowcount or 0)
