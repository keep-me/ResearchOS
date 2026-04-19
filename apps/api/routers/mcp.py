from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from packages.agent.mcp.mcp_service import get_mcp_registry_service
from packages.agent.mcp.researchos_mcp import server as researchos_mcp_server
from packages.agent.mcp.researchos_mcp_registry import filter_public_tool_names

router = APIRouter()

_MCP_CONFIG_MESSAGE = "ResearchOS 内置工具会在对话时自动提供给当前助手；这里仅管理扩展 MCP 配置。"


class McpServerConfigPayload(BaseModel):
    label: str | None = None
    transport: Literal["stdio", "http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    timeout_sec: int = Field(default=30, ge=5, le=300)


class McpConfigPayload(BaseModel):
    servers: dict[str, McpServerConfigPayload] = Field(default_factory=dict)


async def _researchos_builtin_status() -> dict[str, Any]:
    try:
        tools = await researchos_mcp_server.list_tools()
        tool_names = filter_public_tool_names(
            [getattr(tool, "name", "") for tool in tools if getattr(tool, "name", "")]
        )
        return {
            "ok": True,
            "tools": tool_names,
            "tool_count": len(tool_names),
            "message": None,
        }
    except Exception as exc:  # pragma: no cover - defensive health fallback
        return {
            "ok": False,
            "tools": [],
            "tool_count": 0,
            "message": str(exc),
        }


async def _build_mcp_view() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    config = get_mcp_registry_service().get_config()
    raw_servers = config.get("servers") if isinstance(config, dict) else {}
    servers = raw_servers if isinstance(raw_servers, dict) else {}
    builtin_status = await _researchos_builtin_status()

    items: list[dict[str, Any]] = []
    for name, raw in sorted(servers.items(), key=lambda item: (item[0] != "researchos", item[0])):
        if not isinstance(raw, dict):
            continue
        enabled = bool(raw.get("enabled", True))
        builtin = bool(raw.get("builtin")) or name == "researchos"
        item = {
            "name": name,
            "label": str(raw.get("label") or name).strip() or name,
            "transport": str(raw.get("transport") or "stdio").strip() or "stdio",
            "command": raw.get("command"),
            "args": list(raw.get("args") or []),
            "cwd": raw.get("cwd"),
            "env": dict(raw.get("env") or {}),
            "url": raw.get("url"),
            "headers": dict(raw.get("headers") or {}),
            "enabled": enabled,
            "builtin": builtin,
            "timeout_sec": int(raw.get("timeout_sec") or 30),
            "last_connected_at": None,
            "last_disconnected_at": None,
            "session_id": None,
        }
        if builtin:
            connected = enabled and bool(builtin_status.get("ok"))
            item.update(
                {
                    "status": "connected" if connected else "disabled",
                    "connected": connected,
                    "tool_count": int(builtin_status.get("tool_count") or 0),
                    "tools": list(builtin_status.get("tools") or []),
                    "last_error": builtin_status.get("message"),
                }
            )
        else:
            item.update(
                {
                    "status": "disabled" if not enabled else "disconnected",
                    "connected": False,
                    "tool_count": 0,
                    "tools": [],
                    "last_error": None,
                }
            )
        items.append(item)

    runtime = {
        "available": True,
        "connected_count": 1 if builtin_status.get("ok") else 0,
        "enabled_count": sum(1 for item in items if item.get("enabled")),
        "server_count": len(items),
        "builtin_count": sum(1 for item in items if item.get("builtin")),
        "builtin_ready": bool(builtin_status.get("ok")),
        "builtin_tool_count": int(builtin_status.get("tool_count") or 0),
        "configured_count": sum(1 for item in items if not item.get("builtin")),
        "message": _MCP_CONFIG_MESSAGE,
    }
    return runtime, items, config


@router.get("/mcp/runtime")
async def mcp_runtime() -> dict[str, Any]:
    runtime, _, _ = await _build_mcp_view()
    return runtime


@router.get("/mcp/servers")
async def list_mcp_servers() -> dict[str, Any]:
    _, items, _ = await _build_mcp_view()
    return {"items": items}


@router.post("/mcp/servers/{name}/connect")
async def connect_mcp_server(name: str) -> dict[str, Any]:
    raise HTTPException(
        status_code=400,
        detail=f"MCP 配置 {name} 不支持后端直连。ResearchOS 内置工具会在对话时自动提供给当前助手。",
    )


@router.post("/mcp/servers/{name}/disconnect")
async def disconnect_mcp_server(name: str) -> dict[str, Any]:
    raise HTTPException(
        status_code=400,
        detail=f"MCP 配置 {name} 不支持后端断开。ResearchOS 内置工具会在对话时自动提供给当前助手。",
    )


@router.get("/mcp/config")
async def get_mcp_config() -> dict[str, Any]:
    _, _, config = await _build_mcp_view()
    return config


@router.put("/mcp/config")
async def update_mcp_config(payload: McpConfigPayload) -> dict[str, Any]:
    try:
        config = await get_mcp_registry_service().update_config(payload.model_dump())
        return config
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

