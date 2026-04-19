"""Workspace server registry helpers shared by API routes and runtimes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from packages.agent.workspace.workspace_remote import DEFAULT_SSH_PORT, clean_text, mask_secret
from packages.config import get_settings

LOCAL_SERVER_ID = "local"


class WorkspaceServerRegistryError(RuntimeError):
    """Base error for workspace server registry operations."""


class WorkspaceServerValidationError(WorkspaceServerRegistryError):
    """Raised when server payload validation fails."""


class WorkspaceServerConflictError(WorkspaceServerRegistryError):
    """Raised when creating a server would overwrite an existing entry."""


class WorkspaceServerNotFoundError(WorkspaceServerRegistryError):
    """Raised when a server entry cannot be found."""


def _server_store_path() -> Path:
    settings = get_settings()
    base_dir = settings.pdf_storage_root.parent.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "assistant_workspace_servers.json"


def _slugify_server_id(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip().lower()).strip("-")
    return text or "server"


def _parse_ssh_target(raw_value: str, default_port: int = DEFAULT_SSH_PORT) -> tuple[str, int, str | None]:
    raw = clean_text(raw_value)
    if not raw:
        return "", default_port, None
    parsed_input = raw if "://" in raw else f"ssh://{raw}"
    parsed = urlparse(parsed_input)
    return (parsed.hostname or "").strip("[]"), parsed.port or default_port, clean_text(parsed.username) or None


def _merge_secret_value(incoming: str | None, existing: object = None) -> str:
    incoming_clean = clean_text(incoming)
    existing_clean = clean_text(existing)
    if incoming is None:
        return existing_clean
    if not incoming_clean:
        return existing_clean
    return incoming_clean


def _as_payload_dict(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return dict(payload)
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(exclude_none=False))
    return dict(vars(payload))


def _load_server_entries() -> list[dict[str, Any]]:
    store = _server_store_path()
    if not store.exists():
        return []
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    items: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        server_id = _slugify_server_id(
            str(item.get("id") or item.get("label") or item.get("host") or item.get("base_url") or "")
        )
        if not server_id or server_id == LOCAL_SERVER_ID:
            continue
        host = clean_text(item.get("host"))
        port = int(item.get("port") or DEFAULT_SSH_PORT)
        parsed_username = None
        if not host:
            host, port, parsed_username = _parse_ssh_target(str(item.get("base_url") or ""), port)
        username = clean_text(item.get("username")) or (parsed_username or "")
        if not host:
            continue
        items.append(
            {
                "id": server_id,
                "label": clean_text(item.get("label")) or server_id,
                "host": host,
                "port": port,
                "username": username,
                "password": clean_text(item.get("password")),
                "private_key": clean_text(item.get("private_key")),
                "passphrase": clean_text(item.get("passphrase")),
                "workspace_root": clean_text(item.get("workspace_root")),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return items


def _normalize_server_entry(
    payload: Mapping[str, Any] | Any,
    *,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = _as_payload_dict(payload)
    existing_payload = dict(existing or {})
    host, port, parsed_username = _parse_ssh_target(
        str(source.get("host") or source.get("base_url") or ""),
        int(source.get("port") or DEFAULT_SSH_PORT),
    )
    if not host:
        raise WorkspaceServerValidationError("SSH 主机不能为空")
    username = clean_text(source.get("username")) or parsed_username or clean_text(existing_payload.get("username"))
    if not username:
        raise WorkspaceServerValidationError("SSH 用户名不能为空")
    password = _merge_secret_value(source.get("password"), existing_payload.get("password"))
    private_key = _merge_secret_value(source.get("private_key"), existing_payload.get("private_key"))
    passphrase = _merge_secret_value(source.get("passphrase"), existing_payload.get("passphrase"))
    if not password and not private_key:
        raise WorkspaceServerValidationError("请提供 SSH 密码或私钥")
    workspace_root = clean_text(source.get("workspace_root"))
    if source.get("workspace_root") is None:
        workspace_root = clean_text(existing_payload.get("workspace_root"))
    return {
        "id": _slugify_server_id(str(source.get("id") or source.get("label") or host)),
        "label": clean_text(source.get("label")) or host,
        "host": host,
        "port": int(port or DEFAULT_SSH_PORT),
        "username": username,
        "password": password,
        "private_key": private_key,
        "passphrase": passphrase,
        "workspace_root": workspace_root,
        "enabled": bool(source.get("enabled", True)),
    }


def _save_server_entries(entries: list[dict[str, Any]]) -> None:
    store = _server_store_path()
    store.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _local_server_item() -> dict[str, Any]:
    return {
        "id": LOCAL_SERVER_ID,
        "label": "本地",
        "kind": "native",
        "available": True,
        "phase": "ready",
        "message": None,
        "editable": False,
        "removable": False,
        "auth_mode": "none",
        "enabled": True,
    }


def _serialize_server(entry: Mapping[str, Any]) -> dict[str, Any]:
    has_password = bool(clean_text(entry.get("password")))
    has_private_key = bool(clean_text(entry.get("private_key")))
    has_passphrase = bool(clean_text(entry.get("passphrase")))
    auth_mode = "private_key" if has_private_key else "password" if has_password else "none"
    host = clean_text(entry.get("host"))
    port = int(entry.get("port") or DEFAULT_SSH_PORT)
    enabled = bool(entry.get("enabled", True))
    workspace_root = clean_text(entry.get("workspace_root")) or None
    return {
        "id": entry["id"],
        "label": entry["label"],
        "kind": "ssh",
        "available": enabled,
        "phase": "ready" if enabled else "disabled",
        "message": None if workspace_root else "未配置远程工作区目录时，将优先使用请求中的远程路径。",
        "base_url": f"ssh://{host}:{port}",
        "host": host,
        "port": port,
        "username": clean_text(entry.get("username")) or None,
        "workspace_root": workspace_root,
        "has_password": has_password,
        "password_masked": mask_secret(entry.get("password")),
        "has_private_key": has_private_key,
        "private_key_masked": mask_secret(entry.get("private_key")),
        "has_passphrase": has_passphrase,
        "passphrase_masked": mask_secret(entry.get("passphrase")),
        "auth_mode": auth_mode,
        "editable": True,
        "removable": True,
        "enabled": enabled,
    }


def list_workspace_servers() -> list[dict[str, Any]]:
    return [_local_server_item(), *[_serialize_server(item) for item in _load_server_entries()]]


def get_workspace_server_entry(server_id: str) -> dict[str, Any]:
    normalized_id = _slugify_server_id(server_id)
    if not normalized_id or normalized_id == LOCAL_SERVER_ID:
        raise WorkspaceServerValidationError("请提供有效的远程服务器 ID")
    for item in _load_server_entries():
        if item["id"] == normalized_id:
            return item
    raise WorkspaceServerNotFoundError(f"未找到服务器: {server_id}")


def create_workspace_server(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    item = _normalize_server_entry(payload)
    if item["id"] == LOCAL_SERVER_ID:
        raise WorkspaceServerValidationError("local 是保留服务器 ID")
    entries = _load_server_entries()
    existing_ids = {entry["id"] for entry in entries}
    if item["id"] in existing_ids:
        raise WorkspaceServerConflictError(f"服务器 ID 已存在: {item['id']}")
    entries.append(item)
    _save_server_entries(entries)
    return _serialize_server(item)


def update_workspace_server(server_id: str, payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    normalized_id = _slugify_server_id(server_id)
    if normalized_id == LOCAL_SERVER_ID:
        raise WorkspaceServerValidationError("local 服务器不可编辑")
    entries = _load_server_entries()
    updated_item: dict[str, Any] | None = None
    for index, item in enumerate(entries):
        if item["id"] != normalized_id:
            continue
        normalized = _normalize_server_entry(payload, existing=item)
        normalized["id"] = normalized_id
        entries[index] = normalized
        updated_item = normalized
        break
    if updated_item is None:
        raise WorkspaceServerNotFoundError("未找到要更新的服务器")
    _save_server_entries(entries)
    return _serialize_server(updated_item)


def delete_workspace_server(server_id: str) -> str:
    normalized_id = _slugify_server_id(server_id)
    if normalized_id == LOCAL_SERVER_ID:
        raise WorkspaceServerValidationError("local 服务器不可删除")
    entries = _load_server_entries()
    remaining = [item for item in entries if item["id"] != normalized_id]
    if len(remaining) == len(entries):
        raise WorkspaceServerNotFoundError("未找到要删除的服务器")
    _save_server_entries(remaining)
    return normalized_id

