from __future__ import annotations

from packages.agent.tools.tool_runtime import AgentToolContext
from packages.agent.workspace.workspace_executor import WorkspaceAccessError


def context_workspace(context: AgentToolContext | None) -> str | None:
    if context is None:
        return None
    value = str(context.workspace_path or "").strip()
    return value or None


def context_workspace_server_id(context: AgentToolContext | None) -> str | None:
    if context is None:
        return None
    value = str(context.workspace_server_id or "").strip()
    if not value or value.lower() == "local":
        return None
    return value


def resolve_remote_server_entry(context: AgentToolContext | None) -> dict | None:
    server_id = context_workspace_server_id(context)
    if not server_id:
        return None
    try:
        from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry
    except Exception as exc:  # pragma: no cover - import path is stable in app runtime
        raise WorkspaceAccessError(f"无法加载 SSH 工作区支持：{exc}") from exc

    try:
        return get_workspace_server_entry(server_id)
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        raise WorkspaceAccessError(str(detail or exc)) from exc


def context_session_id(context: AgentToolContext | None) -> str:
    if context is None:
        return "default"
    value = str(context.session_id or "").strip()
    return value or "default"

