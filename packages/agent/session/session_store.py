"""DB-backed session/message persistence helpers."""

from __future__ import annotations

import copy
import hashlib
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from packages.agent.runtime.agent_backends import DEFAULT_AGENT_BACKEND_ID
from packages.path_utils import normalize_local_path_string, path_name_string
from packages.storage.db import session_scope
from packages.storage.repositories import (
    AgentProjectRepository,
    AgentSessionMessageRepository,
    AgentSessionPartRepository,
    AgentSessionRepository,
)

DB_WRITE_LOCK = threading.RLock()


def _session_agent_backend_id(value: str | None, *, default: str = DEFAULT_AGENT_BACKEND_ID) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "researchos_native":
        return default
    return raw


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _session_id(value: str | None) -> str:
    normalized = _clean_text(value)
    return normalized or "default"


def _message_id(value: str | None) -> str:
    normalized = _clean_text(value)
    return normalized or f"message_{uuid4().hex}"


def _is_remote_workspace(workspace_server_id: str | None) -> bool:
    normalized = _clean_text(workspace_server_id).lower()
    return normalized not in {"", "local"}


def _normalize_path(value: str | None, *, remote: bool = False) -> str:
    raw = _clean_text(value)
    if not raw:
        return ""
    if remote:
        return raw
    return normalize_local_path_string(raw)


def _project_key(directory: str, workspace_server_id: str | None) -> str:
    server_id = _clean_text(workspace_server_id).lower() or "local"
    return f"{server_id}::{directory or 'global'}"


def _project_id_for(directory: str, workspace_server_id: str | None) -> str:
    if not directory:
        return "global"
    digest = hashlib.sha1(_project_key(directory, workspace_server_id).encode("utf-8")).hexdigest()[:16]
    return f"project_{digest}"


def _session_title(session_id: str, title: str | None, directory: str) -> str:
    explicit = _clean_text(title)
    if explicit:
        return explicit[:256]
    if directory:
        candidate = path_name_string(directory)
        candidate = _clean_text(candidate)
        if candidate:
            return candidate[:256]
    return session_id[:256]


def _to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def _ensure_project(
    *,
    directory: str,
    workspace_server_id: str | None,
) -> tuple[str, str]:
    if not directory:
        return "global", ""
    project_id = _project_id_for(directory, workspace_server_id)
    with session_scope() as session:
        repo = AgentProjectRepository(session)
        row = repo.get_by_id(project_id) or repo.get_by_worktree(directory)
        if row is None:
            repo.upsert(
                project_id=project_id,
                worktree=directory,
                name=path_name_string(directory),
                sandboxes=[directory],
            )
        else:
            project_id = row.id
            repo.upsert(
                project_id=project_id,
                worktree=directory,
                name=row.name or path_name_string(directory),
                sandboxes=list(dict.fromkeys([directory, *(row.sandboxes_json or [])])),
                initialized_at=row.initialized_at,
                vcs=row.vcs,
                icon_url=row.icon_url,
                icon_color=row.icon_color,
                commands_json=row.commands_json,
            )
    return project_id, directory


def _session_summary(row) -> dict[str, Any] | None:  # noqa: ANN001
    has_value = any(
        item is not None
        for item in (row.summary_additions, row.summary_deletions, row.summary_files, row.summary_diffs)
    )
    if not has_value:
        return None
    return {
        "additions": int(row.summary_additions or 0),
        "deletions": int(row.summary_deletions or 0),
        "files": int(row.summary_files or 0),
        "diffs": copy.deepcopy(row.summary_diffs or []),
    }


def _serialize_session_row(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row.id,
        "parentID": row.parent_id,
        "projectID": row.project_id,
        "workspaceID": row.workspace_id,
        "directory": row.directory,
        "workspace_path": row.workspace_path,
        "workspace_server_id": row.workspace_server_id,
        "mode": row.mode,
        "agent_backend_id": _session_agent_backend_id(getattr(row, "backend_id", None)),
        "title": row.title,
        "slug": row.slug,
        "version": row.version,
        "permission": copy.deepcopy(row.permission_json),
        "revert": copy.deepcopy(row.revert_json),
        "summary": _session_summary(row),
        "share": {"url": row.share_url} if _clean_text(row.share_url) else None,
        "compactingAt": _to_ms(row.compacting_at),
        "archivedAt": _to_ms(row.archived_at),
        "time": {
            "created": _to_ms(row.created_at),
            "updated": _to_ms(row.updated_at),
        },
    }


def ensure_session_record(
    session_id: str | None,
    *,
    directory: str | None = None,
    workspace_path: str | None = None,
    workspace_server_id: str | None = None,
    title: str | None = None,
    mode: str | None = None,
    agent_backend_id: str | None = None,
    parent_id: str | None = None,
    permission: list[dict] | None = None,
) -> dict[str, Any]:
    sid = _session_id(session_id)
    with session_scope() as session:
        repo = AgentSessionRepository(session)
        row = repo.get_by_id(sid)
        if row is not None and all(
            value is None
            for value in (
                directory,
                workspace_path,
                workspace_server_id,
                title,
                mode,
                agent_backend_id,
                parent_id,
                permission,
            )
        ):
            return _serialize_session_row(row)
        fallback_directory = row.directory if row is not None else str(Path.cwd())
        fallback_workspace_path = row.workspace_path if row is not None else fallback_directory
        fallback_server_id = row.workspace_server_id if row is not None else None
        fallback_backend_id = getattr(row, "backend_id", None) if row is not None else DEFAULT_AGENT_BACKEND_ID
        resolved_server_id = _clean_text(workspace_server_id) or fallback_server_id
        resolved_backend_id = _session_agent_backend_id(agent_backend_id or fallback_backend_id)
        remote = _is_remote_workspace(resolved_server_id)
        resolved_directory = _normalize_path(directory or workspace_path or fallback_directory, remote=remote)
        resolved_workspace_path = _normalize_path(
            workspace_path or directory or fallback_workspace_path or resolved_directory,
            remote=remote,
        )
        project_id, resolved_directory = _ensure_project(
            directory=resolved_directory or resolved_workspace_path,
            workspace_server_id=resolved_server_id,
        )
        if row is None:
            try:
                row = repo.create(
                    session_id=sid,
                    project_id=project_id,
                    directory=resolved_directory or resolved_workspace_path,
                    workspace_path=resolved_workspace_path or resolved_directory,
                    workspace_server_id=resolved_server_id,
                    title=_session_title(sid, title, resolved_directory or resolved_workspace_path),
                    slug=sid,
                    mode=_clean_text(mode) or "build",
                    backend_id=resolved_backend_id,
                    parent_id=_clean_text(parent_id) or None,
                    permission_json=copy.deepcopy(permission) if permission is not None else None,
                )
            except IntegrityError:
                session.rollback()
                row = repo.get_by_id(sid)
                if row is None:
                    raise
                updated = repo.update(
                    sid,
                    project_id=project_id,
                    directory=resolved_directory or row.directory,
                    workspace_path=resolved_workspace_path or row.workspace_path,
                    workspace_server_id=resolved_server_id or row.workspace_server_id,
                    title=_session_title(sid, title or row.title, resolved_directory or row.directory),
                    slug=row.slug or sid,
                    mode=_clean_text(mode) or row.mode,
                    backend_id=resolved_backend_id,
                    permission_json=copy.deepcopy(permission) if permission is not None else ...,
                )
                row = updated or row
                repo.touch(sid)
        else:
            target_directory = resolved_directory or row.directory
            target_workspace_path = resolved_workspace_path or row.workspace_path
            target_workspace_server_id = resolved_server_id or row.workspace_server_id
            target_title = _session_title(sid, title or row.title, resolved_directory or row.directory)
            target_mode = _clean_text(mode) or row.mode
            target_backend_id = resolved_backend_id
            target_permission = copy.deepcopy(permission) if permission is not None else row.permission_json
            if (
                row.project_id == project_id
                and row.directory == target_directory
                and row.workspace_path == target_workspace_path
                and row.workspace_server_id == target_workspace_server_id
                and row.title == target_title
                and row.mode == target_mode
                and _session_agent_backend_id(getattr(row, "backend_id", None)) == target_backend_id
                and row.permission_json == target_permission
            ):
                return _serialize_session_row(row)
            updated = repo.update(
                sid,
                project_id=project_id,
                directory=target_directory,
                workspace_path=target_workspace_path,
                workspace_server_id=target_workspace_server_id,
                title=target_title,
                slug=row.slug or sid,
                mode=target_mode,
                backend_id=target_backend_id,
                permission_json=copy.deepcopy(permission) if permission is not None else ...,
            )
            row = updated or row
            repo.touch(sid)
        return _serialize_session_row(row)


def get_session_record(session_id: str | None) -> dict[str, Any] | None:
    sid = _session_id(session_id)
    with session_scope() as session:
        row = AgentSessionRepository(session).get_by_id(sid)
        if row is None:
            return None
        return _serialize_session_row(row)


def list_sessions(
    *,
    directory: str | None = None,
    roots: bool = False,
    limit: int = 50,
    archived: bool | None = False,
) -> list[dict[str, Any]]:
    normalized_directory = _normalize_path(directory) if _clean_text(directory) else None
    with session_scope() as session:
        rows = AgentSessionRepository(session).list_all(
            directory=normalized_directory,
            roots=roots,
            limit=max(int(limit or 0), 1),
            archived=archived,
        )
        return [_serialize_session_row(row) for row in rows]


def delete_session_record(session_id: str | None) -> bool:
    sid = _session_id(session_id)
    with session_scope() as session:
        return AgentSessionRepository(session).delete(sid)


def _default_tokens() -> dict[str, Any]:
    return {
        "total": None,
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }


def _normalize_tokens(value: Any) -> dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    cache_payload = payload.get("cache") if isinstance(payload.get("cache"), dict) else {}
    total = payload.get("total")
    return {
        "total": int(total) if total not in {None, ""} else None,
        "input": int(payload.get("input") or 0),
        "output": int(payload.get("output") or 0),
        "reasoning": int(payload.get("reasoning") or 0),
        "cache": {
            "read": int(cache_payload.get("read") or 0),
            "write": int(cache_payload.get("write") or 0),
        },
    }


def _normalize_output_format(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        normalized = _clean_text(value).lower()
        if normalized == "text":
            return {"type": "text"}
        return None
    if not isinstance(value, dict):
        return None
    output_type = _clean_text(value.get("type")).lower()
    if output_type == "text":
        return {"type": "text"}
    if output_type != "json_schema":
        return None
    payload: dict[str, Any] = {
        "type": "json_schema",
        "schema": copy.deepcopy(value.get("schema") or {}),
    }
    retry_count = value.get("retryCount")
    if retry_count is None:
        retry_count = value.get("retry_count")
    if retry_count not in {None, ""}:
        payload["retryCount"] = max(int(retry_count), 0)
    return payload


def _normalize_model_identity(
    value: Any,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any] | None:
    provider = ""
    model = ""
    if isinstance(value, dict):
        provider = _clean_text(value.get("providerID") or value.get("provider_id"))
        model = _clean_text(value.get("modelID") or value.get("model_id") or value.get("id"))
    elif isinstance(value, str):
        model = _clean_text(value)
    provider = provider or _clean_text(provider_id)
    model = model or _clean_text(model_id)
    if not provider and not model:
        return None
    return {
        "providerID": provider,
        "modelID": model,
    }


def _normalize_active_skill_ids(values: Any) -> list[str] | None:
    if not isinstance(values, list):
        return None
    items = [str(item).strip() for item in values if str(item).strip()]
    return items or None


def _normalize_mounted_paper_ids(values: Any) -> list[str] | None:
    if not isinstance(values, list):
        return None
    items = [str(item).strip() for item in values if str(item).strip()]
    return items or None


def _assistant_path(meta: dict[str, Any] | None) -> dict[str, str] | None:
    payload = dict(meta or {})
    raw_path = payload.get("path") if isinstance(payload.get("path"), dict) else {}
    cwd = _clean_text(raw_path.get("cwd")) or _clean_text(payload.get("cwd"))
    root = _clean_text(raw_path.get("root")) or _clean_text(payload.get("root"))
    if not cwd and root:
        cwd = root
    if not root and cwd:
        root = cwd
    if not cwd and not root:
        return None
    return {
        "cwd": cwd or root,
        "root": root or cwd,
    }


def _normalize_user_message_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(meta or {})
    normalized: dict[str, Any] = {}
    agent = _clean_text(payload.get("agent"))
    if agent:
        normalized["agent"] = agent
    model = _normalize_model_identity(
        payload.get("model"),
        provider_id=payload.get("providerID"),
        model_id=payload.get("modelID") or payload.get("model"),
    )
    if model is not None:
        normalized["model"] = model
    format_payload = _normalize_output_format(payload.get("format"))
    if format_payload is not None:
        normalized["format"] = format_payload
    if isinstance(payload.get("tools"), dict):
        normalized["tools"] = copy.deepcopy(payload["tools"])
    system = _clean_text(payload.get("system"))
    if system:
        normalized["system"] = system
    variant = _clean_text(payload.get("variant"))
    if variant:
        normalized["variant"] = variant
    active_skill_ids = _normalize_active_skill_ids(
        payload.get("activeSkillIDs")
        if isinstance(payload.get("activeSkillIDs"), list)
        else payload.get("active_skill_ids")
    )
    if active_skill_ids is not None:
        normalized["activeSkillIDs"] = active_skill_ids
    mounted_paper_ids = _normalize_mounted_paper_ids(
        payload.get("mountedPaperIDs")
        if isinstance(payload.get("mountedPaperIDs"), list)
        else payload.get("mounted_paper_ids")
    )
    if mounted_paper_ids is not None:
        normalized["mountedPaperIDs"] = mounted_paper_ids
    mounted_primary_paper_id = _clean_text(
        payload.get("mountedPrimaryPaperID")
        or payload.get("mounted_primary_paper_id")
    )
    if mounted_primary_paper_id:
        normalized["mountedPrimaryPaperID"] = mounted_primary_paper_id
    if isinstance(payload.get("summary"), dict) and payload["summary"]:
        normalized["summary"] = copy.deepcopy(payload["summary"])
    return normalized


def _normalize_assistant_message_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(meta or {})
    normalized: dict[str, Any] = {}
    model = _normalize_model_identity(
        payload.get("model"),
        provider_id=payload.get("providerID"),
        model_id=payload.get("modelID") or payload.get("model"),
    )
    if model is not None:
        normalized["providerID"] = model["providerID"]
        normalized["modelID"] = model["modelID"]
    mode = _clean_text(payload.get("mode")) or _clean_text(payload.get("agent"))
    if mode:
        normalized["mode"] = mode
    agent = _clean_text(payload.get("agent")) or mode
    if agent:
        normalized["agent"] = agent
    path = _assistant_path(payload)
    if path is not None:
        normalized["path"] = path
        normalized["cwd"] = path["cwd"]
        normalized["root"] = path["root"]
    normalized["cost"] = payload.get("cost") if payload.get("cost") is not None else 0.0
    normalized["tokens"] = _normalize_tokens(payload.get("tokens"))
    finish = _clean_text(payload.get("finish"))
    if finish:
        normalized["finish"] = finish
    elif "finish" in payload:
        normalized["finish"] = None
    if isinstance(payload.get("error"), dict):
        normalized["error"] = copy.deepcopy(payload["error"])
    if isinstance(payload.get("providerMetadata"), dict):
        normalized["providerMetadata"] = copy.deepcopy(payload["providerMetadata"])
    if payload.get("summary") is not None:
        normalized["summary"] = bool(payload.get("summary"))
    if "structured" in payload and payload.get("structured") is not None:
        normalized["structured"] = copy.deepcopy(payload.get("structured"))
    variant = _clean_text(payload.get("variant"))
    if variant:
        normalized["variant"] = variant
    completed = payload.get("completed")
    if completed is None and isinstance(payload.get("time"), dict):
        completed = payload["time"].get("completed")
    if completed not in {None, ""}:
        normalized["completed"] = int(completed)
    return normalized


def _normalize_message_meta(role: str, meta: dict[str, Any] | None) -> dict[str, Any]:
    normalized_role = _clean_text(role) or "user"
    if normalized_role == "assistant":
        return _normalize_assistant_message_meta(meta)
    return _normalize_user_message_meta(meta)


def _part_id(value: str | None) -> str:
    normalized = _clean_text(value)
    return normalized or f"part_{uuid4().hex}"


def _part_storage_payload(part: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    part_type = _clean_text(part.get("type")) or "text"
    content = ""
    data: dict[str, Any] = {}

    if part_type in {"text", "reasoning"}:
        content = str(part.get("text") or part.get("content") or "")
        if isinstance(part.get("metadata"), dict) and part["metadata"]:
            data["metadata"] = copy.deepcopy(part["metadata"])
        if isinstance(part.get("time"), dict) and part["time"]:
            data["time"] = copy.deepcopy(part["time"])
    elif part_type == "file":
        content = str(part.get("content") or "")
        for key in ("url", "filename", "mime"):
            if key in part:
                data[key] = part.get(key)
    elif part_type == "tool":
        content = str(part.get("content") or "")
        for key in ("tool", "callID", "summary", "data", "state", "metadata", "providerExecuted"):
            if key in part:
                data[key] = copy.deepcopy(part.get(key))
    elif part_type == "step-start":
        data = {
            "step": int(part.get("step") or 0),
            "snapshot": part.get("snapshot"),
            "time": copy.deepcopy(part.get("time") or {}),
        }
    elif part_type == "step-finish":
        data = {
            "step": int(part.get("step") or 0),
            "reason": part.get("reason"),
            "tokens": copy.deepcopy(part.get("tokens") or {}),
            "cost": part.get("cost"),
            "snapshot": part.get("snapshot"),
            "time": copy.deepcopy(part.get("time") or {}),
        }
    elif part_type == "retry":
        data = {
            "attempt": int(part.get("attempt") or 0),
            "message": part.get("message"),
            "delay_ms": int(part.get("delay_ms") or 0),
            "error": copy.deepcopy(part.get("error") or {}),
            "time": copy.deepcopy(part.get("time") or {}),
        }
    elif part_type == "patch":
        data = copy.deepcopy(
            {
                key: part.get(key)
                for key in (
                    "hash",
                    "files",
                    "workspace_path",
                    "workspace_server_id",
                    "diffs",
                    "patches",
                    "file",
                    "path",
                    "before",
                    "after",
                    "exists_before",
                    "exists_after",
                    "additions",
                    "deletions",
                    "status",
                )
                if key in part
            }
        )
    elif part_type == "compaction":
        data = copy.deepcopy({key: part.get(key) for key in ("auto", "overflow", "time") if key in part})
    else:
        content = str(part.get("content") or part.get("text") or "")
        data = copy.deepcopy(part.get("data") or {})

    return part_type, content, data or None


def _serialize_part_row(row) -> dict[str, Any]:  # noqa: ANN001
    data = dict(row.data_json or {})
    payload: dict[str, Any] = {
        "id": row.id,
        "sessionID": row.session_id,
        "messageID": row.message_id,
        "type": row.part_type,
    }
    if row.part_type in {"text", "reasoning"}:
        payload["text"] = row.content
        if isinstance(data.get("metadata"), dict) and data["metadata"]:
            payload["metadata"] = copy.deepcopy(data["metadata"])
        if isinstance(data.get("time"), dict) and data["time"]:
            payload["time"] = copy.deepcopy(data["time"])
    elif row.part_type == "file":
        payload["content"] = row.content
        for key in ("url", "filename", "mime"):
            if key in data:
                payload[key] = data.get(key)
    elif row.part_type == "tool":
        payload["content"] = row.content
        payload["tool"] = data.get("tool")
        payload["callID"] = data.get("callID")
        if "summary" in data:
            payload["summary"] = data.get("summary")
        if "data" in data:
            payload["data"] = copy.deepcopy(data.get("data"))
        if isinstance(data.get("state"), dict):
            payload["state"] = copy.deepcopy(data["state"])
        if isinstance(data.get("metadata"), dict) and data["metadata"]:
            payload["metadata"] = copy.deepcopy(data["metadata"])
        if data.get("providerExecuted"):
            payload["providerExecuted"] = True
    elif row.part_type == "step-start":
        payload["step"] = int(data.get("step") or 0)
        if "snapshot" in data:
            payload["snapshot"] = data.get("snapshot")
        if isinstance(data.get("time"), dict) and data["time"]:
            payload["time"] = copy.deepcopy(data["time"])
    elif row.part_type == "step-finish":
        payload["step"] = int(data.get("step") or 0)
        payload["reason"] = data.get("reason")
        payload["tokens"] = copy.deepcopy(data.get("tokens") or {})
        payload["cost"] = data.get("cost")
        if "snapshot" in data:
            payload["snapshot"] = data.get("snapshot")
        if isinstance(data.get("time"), dict) and data["time"]:
            payload["time"] = copy.deepcopy(data["time"])
    elif row.part_type == "retry":
        payload["attempt"] = int(data.get("attempt") or 0)
        payload["message"] = data.get("message")
        payload["delay_ms"] = int(data.get("delay_ms") or 0)
        payload["error"] = copy.deepcopy(data.get("error") or {})
        if isinstance(data.get("time"), dict) and data["time"]:
            payload["time"] = copy.deepcopy(data["time"])
    elif row.part_type in {"patch", "compaction"}:
        payload.update(copy.deepcopy(data))
    else:
        payload["content"] = row.content
        if data:
            payload["data"] = copy.deepcopy(data)
    return payload


def _part_sort_key(item) -> tuple[int, int, str]:  # noqa: ANN001
    data = dict(getattr(item, "data_json", None) or {})
    return (
        int(data.get("_order") or 0),
        _to_ms(getattr(item, "created_at", None)) or 0,
        str(getattr(item, "id", "") or ""),
    )


def _message_info(row, parts: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: ANN001
    del parts
    role = _clean_text(row.role) or "user"
    meta = _normalize_message_meta(role, dict(row.meta or {}))
    info: dict[str, Any] = {
        "id": row.id,
        "sessionID": row.session_id,
        "role": role,
        "time": {
            "created": _to_ms(row.created_at),
        },
    }

    if role == "assistant":
        if row.parent_id:
            info["parentID"] = row.parent_id
        provider_id = _clean_text(meta.get("providerID"))
        model_id = _clean_text(meta.get("modelID")) or _clean_text(row.model)
        if provider_id:
            info["providerID"] = provider_id
        if model_id:
            info["modelID"] = model_id
        mode = _clean_text(meta.get("mode")) or "build"
        info["mode"] = mode
        info["agent"] = _clean_text(meta.get("agent")) or mode
        path = _assistant_path(meta)
        if path is not None:
            info["path"] = copy.deepcopy(path)
        info["cost"] = meta.get("cost") if meta.get("cost") is not None else 0.0
        info["tokens"] = _normalize_tokens(meta.get("tokens"))
        finish = _clean_text(meta.get("finish"))
        if finish:
            info["finish"] = finish
        elif "finish" in meta:
            info["finish"] = None
        if isinstance(meta.get("error"), dict):
            info["error"] = copy.deepcopy(meta["error"])
        if isinstance(meta.get("providerMetadata"), dict):
            info["providerMetadata"] = copy.deepcopy(meta["providerMetadata"])
        if meta.get("summary") is not None:
            info["summary"] = bool(meta.get("summary"))
        if meta.get("structured") is not None:
            info["structured"] = copy.deepcopy(meta["structured"])
        variant = _clean_text(meta.get("variant"))
        if variant:
            info["variant"] = variant
        completed = meta.get("completed")
        if completed is None and (finish not in {"", "tool-calls", "unknown"} or isinstance(meta.get("error"), dict)):
            completed = _to_ms(row.updated_at)
        if completed is not None:
            info["time"]["completed"] = int(completed)
        return info

    model = _normalize_model_identity(
        meta.get("model"),
        provider_id=meta.get("providerID"),
        model_id=row.model,
    )
    if model is not None:
        info["model"] = model
    agent = _clean_text(meta.get("agent"))
    if agent:
        info["agent"] = agent
    format_payload = _normalize_output_format(meta.get("format"))
    if format_payload is not None:
        info["format"] = format_payload
    if isinstance(meta.get("summary"), dict):
        info["summary"] = copy.deepcopy(meta["summary"])
    if isinstance(meta.get("tools"), dict):
        info["tools"] = copy.deepcopy(meta["tools"])
    system = _clean_text(meta.get("system"))
    if system:
        info["system"] = system
    variant = _clean_text(meta.get("variant"))
    if variant:
        info["variant"] = variant
    if isinstance(meta.get("activeSkillIDs"), list):
        info["activeSkillIDs"] = copy.deepcopy(meta["activeSkillIDs"])
    if isinstance(meta.get("mountedPaperIDs"), list):
        info["mountedPaperIDs"] = copy.deepcopy(meta["mountedPaperIDs"])
    mounted_primary_paper_id = _clean_text(meta.get("mountedPrimaryPaperID"))
    if mounted_primary_paper_id:
        info["mountedPrimaryPaperID"] = mounted_primary_paper_id
    return info


def _serialize_message_row(row, parts: list[Any]) -> dict[str, Any]:
    serialized_parts = [_serialize_part_row(item) for item in parts]
    return {
        "info": _message_info(row, serialized_parts),
        "parts": serialized_parts,
    }


def aggregate_message_content(parts: list[dict[str, Any]], *, role: str) -> str:
    del role
    return "".join(str(part.get("text") or "") for part in parts if str(part.get("type") or "") == "text")


def _materialize_parts(
    *,
    session_id: str,
    message_id: str,
    parts: list[dict[str, Any]],
    created_at: datetime | None = None,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    base_created_at = created_at or _now_dt()
    for index, item in enumerate(parts):
        if not isinstance(item, dict):
            continue
        part_type, content, data = _part_storage_payload(item)
        payload_data = copy.deepcopy(data) if isinstance(data, dict) else {}
        payload_data["_order"] = index
        prepared.append(
            {
                "id": _part_id(item.get("id")),
                "type": part_type,
                "content": content,
                "data": payload_data or None,
                "created_at": base_created_at + timedelta(microseconds=index),
            }
        )
    with DB_WRITE_LOCK, session_scope() as session:
        repo = AgentSessionPartRepository(session)
        stored = repo.replace_for_message(
            session_id=session_id,
            message_id=message_id,
            parts=prepared,
        )
        return [_serialize_part_row(row) for row in sorted(stored, key=_part_sort_key)]


def append_session_message(
    *,
    session_id: str,
    role: str,
    content: str,
    parent_id: str | None = None,
    meta: dict | None = None,
    parts: list[dict] | None = None,
    message_id: str | None = None,
    message_type: str = "message",
) -> dict[str, Any]:
    session_payload = get_session_record(session_id) or ensure_session_record(session_id)
    normalized_role = _clean_text(role) or "user"
    normalized_meta = _normalize_message_meta(
        normalized_role,
        copy.deepcopy(meta) if isinstance(meta, dict) else None,
    )
    normalized_parts = copy.deepcopy(parts) if isinstance(parts, list) else None
    if normalized_parts is None:
        normalized_parts = []
        if normalized_role in {"user", "assistant"} and str(content or ""):
            normalized_parts.append({"type": "text", "text": str(content)})
    normalized_content = str(content or "")
    if not normalized_content:
        normalized_content = aggregate_message_content(normalized_parts, role=normalized_role)

    created_at = _now_dt()
    with DB_WRITE_LOCK:
        with session_scope() as session:
            repo = AgentSessionMessageRepository(session)
            row = repo.create(
                message_id=_message_id(message_id),
                session_id=session_payload["id"],
                role=normalized_role,
                content=normalized_content,
                parent_id=_clean_text(parent_id) or None,
                message_type=message_type,
                model=(
                    _clean_text((normalized_meta.get("model") or {}).get("modelID"))
                    or _clean_text(normalized_meta.get("modelID"))
                    or None
                ),
                meta=copy.deepcopy(normalized_meta) if normalized_meta else None,
                created_at=created_at,
                updated_at=created_at,
            )
            created_message_id = row.id
        serialized_parts = _materialize_parts(
            session_id=session_payload["id"],
            message_id=created_message_id,
            parts=normalized_parts,
            created_at=created_at,
        )
        with session_scope() as session:
            row = AgentSessionMessageRepository(session).get_by_id(created_message_id)
            assert row is not None
            AgentSessionRepository(session).touch(session_payload["id"])
            message = _serialize_message_row(row, [])
            message["parts"] = serialized_parts
    return message


def persist_assistant_message(
    *,
    session_id: str,
    parent_id: str | None = None,
    meta: dict | None = None,
    parts: list[dict] | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    normalized_parts = copy.deepcopy(parts) if isinstance(parts, list) else []
    content = aggregate_message_content(normalized_parts, role="assistant")
    return append_session_message(
        session_id=session_id,
        role="assistant",
        content=content,
        parent_id=parent_id,
        meta=meta,
        parts=normalized_parts,
        message_id=message_id,
    )


def list_session_messages(
    session_id: str | None,
    *,
    limit: int = 100,
    include_transient: bool = False,
) -> list[dict[str, Any]]:
    del include_transient
    sid = _session_id(session_id)
    with session_scope() as session:
        message_repo = AgentSessionMessageRepository(session)
        part_repo = AgentSessionPartRepository(session)
        rows = message_repo.list_by_session(sid, limit=max(limit, 1) * 8)
        if limit > 0 and len(rows) > limit:
            rows = rows[-limit:]
        part_rows = part_repo.list_by_message_ids([row.id for row in rows])
        part_map: dict[str, list[Any]] = {}
        for part in part_rows:
            part_map.setdefault(part.message_id, []).append(part)
        for key, items in part_map.items():
            part_map[key] = sorted(items, key=_part_sort_key)
        return [_serialize_message_row(row, part_map.get(row.id, [])) for row in rows]


def get_session_message_by_id(session_id: str | None, message_id: str) -> dict[str, Any] | None:
    sid = _session_id(session_id)
    normalized_message_id = _clean_text(message_id)
    if not normalized_message_id:
        return None
    for message in list_session_messages(sid, limit=5000):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if str(info.get("id") or "") == normalized_message_id:
            return copy.deepcopy(message)
    return None


def update_message_parts(
    *,
    session_id: str,
    message_id: str,
    parts: list[dict[str, Any]],
    meta: dict[str, Any] | object = ...,
    content: str | None = None,
) -> dict[str, Any]:
    with DB_WRITE_LOCK, session_scope() as session:
        message_repo = AgentSessionMessageRepository(session)
        part_repo = AgentSessionPartRepository(session)
        prepared: list[dict[str, Any]] = []
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            part_type, stored_content, data = _part_storage_payload(part)
            payload_data = copy.deepcopy(data) if isinstance(data, dict) else {}
            payload_data["_order"] = index
            prepared.append(
                {
                    "id": _part_id(part.get("id")),
                    "type": part_type,
                    "content": stored_content,
                    "data": payload_data or None,
                }
            )
        stored_parts = part_repo.replace_for_message(
            session_id=session_id,
            message_id=message_id,
            parts=prepared,
        )
        updated = message_repo.update(
            message_id,
            content=content if content is not None else aggregate_message_content(parts, role="assistant"),
            meta=copy.deepcopy(meta) if meta is not ... else ...,
        )
        if updated is None:
            raise RuntimeError(f"Session message not found: {message_id}")
        AgentSessionRepository(session).touch(session_id)
        return _serialize_message_row(updated, sorted(stored_parts, key=_part_sort_key))
