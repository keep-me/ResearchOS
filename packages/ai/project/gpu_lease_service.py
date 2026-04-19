from __future__ import annotations

from typing import Any

from packages.storage.db import session_scope
from packages.storage.repositories import ProjectGpuLeaseRepository


def _serialize_lease(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "workspace_server_id": row.workspace_server_id,
        "gpu_index": row.gpu_index,
        "gpu_name": row.gpu_name,
        "active": bool(row.active),
        "project_id": row.project_id,
        "run_id": row.run_id,
        "task_id": row.task_id,
        "remote_session_name": row.remote_session_name,
        "holder_title": row.holder_title,
        "metadata": dict(row.metadata_json or {}),
        "release_reason": row.release_reason,
        "locked_at": row.locked_at.isoformat() if row.locked_at else None,
        "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        "released_at": row.released_at.isoformat() if row.released_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_active_gpu_leases(workspace_server_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = ProjectGpuLeaseRepository(session).list_leases(
            workspace_server_id=workspace_server_id,
            active_only=True,
        )
        return [_serialize_lease(row) for row in rows]


def acquire_gpu_lease(
    *,
    workspace_server_id: str,
    gpu_index: int,
    gpu_name: str | None = None,
    project_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    remote_session_name: str | None = None,
    holder_title: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        row = ProjectGpuLeaseRepository(session).acquire(
            workspace_server_id=workspace_server_id,
            gpu_index=gpu_index,
            gpu_name=gpu_name,
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            remote_session_name=remote_session_name,
            holder_title=holder_title,
            metadata=metadata,
        )
        return _serialize_lease(row)


def release_gpu_lease(
    *,
    workspace_server_id: str,
    gpu_index: int,
    run_id: str | None = None,
    remote_session_name: str | None = None,
    reason: str | None = None,
) -> dict[str, Any] | None:
    with session_scope() as session:
        row = ProjectGpuLeaseRepository(session).release(
            workspace_server_id=workspace_server_id,
            gpu_index=gpu_index,
            run_id=run_id,
            remote_session_name=remote_session_name,
            reason=reason,
        )
        return _serialize_lease(row) if row is not None else None


def touch_gpu_lease(
    *,
    workspace_server_id: str,
    gpu_index: int,
    metadata: dict | None = None,
) -> dict[str, Any] | None:
    with session_scope() as session:
        row = ProjectGpuLeaseRepository(session).touch(
            workspace_server_id=workspace_server_id,
            gpu_index=gpu_index,
            metadata=metadata,
        )
        return _serialize_lease(row) if row is not None else None


def reconcile_gpu_leases(
    *,
    workspace_server_id: str,
    active_session_names: list[str],
    reason: str = "remote_session_missing",
) -> dict[str, Any]:
    with session_scope() as session:
        repo = ProjectGpuLeaseRepository(session)
        released = repo.reconcile_missing_sessions(
            workspace_server_id=workspace_server_id,
            active_session_names=active_session_names,
            reason=reason,
        )
        active_rows = repo.list_leases(
            workspace_server_id=workspace_server_id,
            active_only=True,
        )
        return {
            "released": [_serialize_lease(row) for row in released],
            "active": [_serialize_lease(row) for row in active_rows],
        }
