from __future__ import annotations

from packages.agent.mcp.claw_mcp_runtime import (
    RemotePathRef,
    build_dynamic_bridge_function,
    bridge_tool_context,
    execute_bridge_tool,
    register_dynamic_bridge_tools,
    remote_path_display,
    remote_server_entry,
    resolve_remote_path_ref,
    resolve_remote_workspace_root,
    serialize_tool_result,
    tool_annotations,
)

agent_tool_context_from_env = bridge_tool_context

__all__ = [
    "RemotePathRef",
    "agent_tool_context_from_env",
    "bridge_tool_context",
    "build_dynamic_bridge_function",
    "execute_bridge_tool",
    "register_dynamic_bridge_tools",
    "remote_path_display",
    "remote_server_entry",
    "resolve_remote_path_ref",
    "resolve_remote_workspace_root",
    "serialize_tool_result",
    "tool_annotations",
]

