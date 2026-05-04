"""Shared tool execution runtime aligned with the canonical tool registry."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    success: bool
    data: dict = field(default_factory=dict)
    summary: str = ""
    internal_data: dict = field(default_factory=dict)


@dataclass
class ToolProgress:
    message: str
    current: int = 0
    total: int = 0


@dataclass
class AgentToolContext:
    session_id: str | None = None
    mode: str = "build"
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    runtime_options: Any | None = None


def execute_tool_stream(
    name: str,
    arguments: dict,
    context: AgentToolContext | None = None,
) -> Iterator[ToolProgress | ToolResult]:
    """Execute a registered tool and yield progress / result events."""
    from packages.agent import tool_registry
    from packages.agent.session.session_plan import check_plan_mode_tool_access
    from packages.agent.session.session_runtime import get_session_record

    handler = tool_registry.resolve_tool_handler(name)
    if handler is None:
        yield ToolResult(success=False, summary=f"未知工具：{name}")
        return

    try:
        kwargs = dict(arguments or {})
        if context is not None:
            session_payload = get_session_record(context.session_id) if context.session_id else None
            if not isinstance(session_payload, dict):
                session_payload = {
                    "id": context.session_id,
                    "mode": context.mode,
                    "workspace_path": context.workspace_path,
                    "workspace_server_id": context.workspace_server_id,
                    "directory": context.workspace_path,
                    "slug": context.session_id,
                    "time": {},
                }
            else:
                if context.mode:
                    session_payload["mode"] = context.mode
                if (
                    context.workspace_path
                    and not str(session_payload.get("workspace_path") or "").strip()
                ):
                    session_payload["workspace_path"] = context.workspace_path
                if (
                    context.workspace_server_id
                    and not str(session_payload.get("workspace_server_id") or "").strip()
                ):
                    session_payload["workspace_server_id"] = context.workspace_server_id
            violation = check_plan_mode_tool_access(
                name,
                kwargs,
                session_payload,
                allow_in_read_only=tool_registry.tool_spec(name).allow_in_read_only,
            )
            if violation:
                yield ToolResult(success=False, summary=violation)
                return
        signature = inspect.signature(handler)
        if "context" in signature.parameters:
            kwargs["context"] = context or AgentToolContext()
        result = handler(**kwargs)
        if isinstance(result, Iterator):
            yield from result
            return
        yield result
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception("Tool %s failed: %s", name, exc)
        yield ToolResult(success=False, summary=str(exc))
