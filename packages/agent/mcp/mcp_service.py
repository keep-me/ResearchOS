"""Standalone MCP registry and connection manager for ResearchOS."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _ROOT / "data"
_REGISTRY_PATH = _DATA_DIR / "assistant_mcp_registry.json"
_CURSOR_MCP_PATH = _ROOT / ".cursor" / "mcp.json"


def _builtin_researchos_server() -> dict[str, Any]:
    script_path = _ROOT / "scripts" / "researchos_mcp_server.py"
    return {
        "name": "researchos",
        "label": "ResearchOS 内置 MCP",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script_path)],
        "cwd": str(_ROOT),
        "env": {
            "PYTHONPATH": str(_ROOT),
            "PYTHONUTF8": "1",
        },
        "url": None,
        "headers": {},
        "enabled": True,
        "builtin": True,
        "timeout_sec": 30,
    }


def _normalize_disconnect_error(message: str | None) -> str | None:
    text = str(message or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if "cancel scope" in lowered or "cancelled via cancel scope" in lowered:
        return None
    return text


@dataclass
class ManagedMcpConnection:
    name: str
    exit_stack: AsyncExitStack
    session: ClientSession
    transport: str
    tool_names: list[str]
    connected_at: float
    session_id: str | None = None

    async def close(self) -> None:
        await self.exit_stack.aclose()


class McpRegistryService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: dict[str, ManagedMcpConnection] = {}
        self._states: dict[str, dict[str, Any]] = {}

    def _ensure_store(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not _REGISTRY_PATH.exists():
            _REGISTRY_PATH.write_text(
                json.dumps({"version": 1, "servers": {}}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _normalize_server(self, name: str, raw: dict[str, Any], *, builtin: bool = False) -> dict[str, Any]:
        transport = str(raw.get("transport") or "stdio").strip().lower()
        if transport not in {"stdio", "http"}:
            raise ValueError(f"MCP 服务 {name} 的 transport 仅支持 stdio 或 http")

        server = {
            "name": name,
            "label": str(raw.get("label") or name).strip() or name,
            "transport": transport,
            "command": str(raw.get("command") or "").strip() or None,
            "args": [str(item) for item in (raw.get("args") or []) if str(item).strip()],
            "cwd": str(raw.get("cwd") or "").strip() or None,
            "env": {
                str(key): str(value)
                for key, value in (raw.get("env") or {}).items()
                if str(key).strip()
            },
            "url": str(raw.get("url") or "").strip() or None,
            "headers": {
                str(key): str(value)
                for key, value in (raw.get("headers") or {}).items()
                if str(key).strip()
            },
            "enabled": bool(raw.get("enabled", True)),
            "builtin": builtin or bool(raw.get("builtin")),
            "timeout_sec": max(5, min(int(raw.get("timeout_sec") or 30), 300)),
        }

        if server["transport"] == "stdio" and not server["command"]:
            raise ValueError(f"MCP 服务 {name} 缺少 command")
        if server["transport"] == "http" and not server["url"]:
            raise ValueError(f"MCP 服务 {name} 缺少 url")
        return server

    def _load_cursor_servers(self) -> dict[str, dict[str, Any]]:
        if not _CURSOR_MCP_PATH.exists():
            return {}
        try:
            payload = json.loads(_CURSOR_MCP_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        raw_servers = payload.get("mcpServers")
        if not isinstance(raw_servers, dict):
            return {}
        migrated: dict[str, dict[str, Any]] = {}
        for name, raw in raw_servers.items():
            if not isinstance(raw, dict):
                continue
            if raw.get("type") == "builtin":
                continue
            try:
                migrated[name] = self._normalize_server(name, raw)
            except ValueError:
                continue
        return migrated

    def _load_registry(self) -> dict[str, Any]:
        self._ensure_store()
        try:
            payload = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"version": 1, "servers": {}}
        if not isinstance(payload, dict):
            payload = {"version": 1, "servers": {}}

        raw_servers = payload.get("servers")
        servers: dict[str, dict[str, Any]] = {}
        if isinstance(raw_servers, dict):
            for name, raw in raw_servers.items():
                if not isinstance(raw, dict):
                    continue
                try:
                    servers[name] = self._normalize_server(name, raw)
                except ValueError:
                    continue

        if not servers:
            servers.update(self._load_cursor_servers())

        servers["researchos"] = self._normalize_server("researchos", _builtin_researchos_server(), builtin=True)
        return {"version": 1, "servers": servers}

    def _save_registry(self, config: dict[str, Any]) -> None:
        self._ensure_store()
        servers = config.get("servers") or {}
        persisted = {
            name: {
                key: value
                for key, value in server.items()
                if key not in {"name", "builtin"} and value is not None
            }
            for name, server in servers.items()
            if name != "researchos" and not bool(server.get("builtin"))
        }
        _REGISTRY_PATH.write_text(
            json.dumps({"version": 1, "servers": persisted}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_config(self) -> dict[str, Any]:
        return self._load_registry()

    async def update_config(self, next_config: dict[str, Any]) -> dict[str, Any]:
        raw_servers = next_config.get("servers") if isinstance(next_config, dict) else {}
        if not isinstance(raw_servers, dict):
            raise ValueError("MCP 配置格式不正确")

        normalized: dict[str, dict[str, Any]] = {}
        for name, raw in raw_servers.items():
            if name == "researchos":
                continue
            if not isinstance(raw, dict):
                continue
            normalized[name] = self._normalize_server(name, raw)

        merged = {"version": 1, "servers": normalized}
        merged["servers"]["researchos"] = self._normalize_server(
            "researchos",
            _builtin_researchos_server(),
            builtin=True,
        )
        self._save_registry(merged)

        async with self._lock:
            removed = [name for name in list(self._connections) if name not in merged["servers"]]
        for name in removed:
            await self.disconnect_server(name)
        return self.get_config()

    def _extract_tool_names(self, result: Any) -> list[str]:
        raw_tools = getattr(result, "tools", result)
        if not raw_tools:
            return []
        names: list[str] = []
        for item in raw_tools:
            name = getattr(item, "name", None)
            if name:
                names.append(str(name))
        return names

    async def list_servers(self) -> list[dict[str, Any]]:
        config = self.get_config()
        async with self._lock:
            connections = dict(self._connections)
            states = dict(self._states)

        items: list[dict[str, Any]] = []
        for name, server in sorted(config["servers"].items(), key=lambda item: (item[0] != "researchos", item[0])):
            connection = connections.get(name)
            state = states.get(name, {})
            connected = connection is not None
            items.append(
                {
                    **server,
                    "status": "connected" if connected else "disabled" if not server["enabled"] else "disconnected",
                    "connected": connected,
                    "tool_count": len(connection.tool_names if connection else state.get("tool_names", [])),
                    "tools": connection.tool_names if connection else state.get("tool_names", []),
                    "last_error": state.get("last_error"),
                    "last_connected_at": connection.connected_at if connection else state.get("last_connected_at"),
                    "last_disconnected_at": state.get("last_disconnected_at"),
                    "session_id": connection.session_id if connection else state.get("session_id"),
                }
            )
        return items

    async def runtime_snapshot(self) -> dict[str, Any]:
        items = await self.list_servers()
        connected_count = sum(1 for item in items if item["connected"])
        enabled_count = sum(1 for item in items if item["enabled"])
        return {
            "available": True,
            "connected_count": connected_count,
            "enabled_count": enabled_count,
            "server_count": len(items),
            "builtin_count": sum(1 for item in items if item["builtin"]),
            "message": "已启用",
        }

    async def connect_server(self, name: str) -> dict[str, Any]:
        config = self.get_config()
        server = config["servers"].get(name)
        if not server:
            raise ValueError(f"未找到 MCP 服务：{name}")
        if not server["enabled"]:
            raise ValueError(f"MCP 服务已禁用：{name}")

        async with self._lock:
            existing = self._connections.get(name)
        if existing is not None:
            items = await self.list_servers()
            return next(item for item in items if item["name"] == name)

        exit_stack = AsyncExitStack()
        try:
            if server["transport"] == "stdio":
                params = StdioServerParameters(
                    command=str(server["command"]),
                    args=list(server.get("args") or []),
                    env={**os.environ, **(server.get("env") or {})},
                    cwd=server.get("cwd") or None,
                    encoding="utf-8",
                    encoding_error_handler="replace",
                )
                read_stream, write_stream = await exit_stack.enter_async_context(stdio_client(params))
                session_id = None
            else:
                read_stream, write_stream, session_id_getter = await exit_stack.enter_async_context(
                    streamablehttp_client(
                        str(server["url"]),
                        headers=dict(server.get("headers") or {}),
                        timeout=server.get("timeout_sec") or 30,
                        sse_read_timeout=max(60, (server.get("timeout_sec") or 30) * 5),
                    )
                )
                session_id = session_id_getter() if callable(session_id_getter) else None

            session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = self._extract_tool_names(tools_result)
            connection = ManagedMcpConnection(
                name=name,
                exit_stack=exit_stack,
                session=session,
                transport=server["transport"],
                tool_names=tool_names,
                connected_at=time.time(),
                session_id=session_id,
            )
        except Exception:
            await exit_stack.aclose()
            raise

        async with self._lock:
            self._connections[name] = connection
            self._states[name] = {
                "tool_names": tool_names,
                "last_connected_at": connection.connected_at,
                "last_disconnected_at": None,
                "last_error": None,
                "session_id": session_id,
            }
        items = await self.list_servers()
        return next(item for item in items if item["name"] == name)

    async def disconnect_server(self, name: str) -> dict[str, Any]:
        async with self._lock:
            connection = self._connections.pop(name, None)
        close_error: str | None = None
        if connection is not None:
            try:
                await connection.close()
            except BaseException as exc:
                close_error = _normalize_disconnect_error(str(exc or "").strip() or "断开连接时出现异常")
        async with self._lock:
            state = dict(self._states.get(name, {}))
            state["last_disconnected_at"] = time.time()
            state["last_error"] = close_error
            self._states[name] = state
        items = await self.list_servers()
        for item in items:
            if item["name"] == name:
                return item
        raise ValueError(f"未找到 MCP 服务：{name}")

    async def close_all(self) -> None:
        async with self._lock:
            names = list(self._connections.keys())
        for name in names:
            await self.disconnect_server(name)


_service = McpRegistryService()


def get_mcp_registry_service() -> McpRegistryService:
    return _service
