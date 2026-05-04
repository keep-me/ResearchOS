"""Agent-domain runtime package with compatibility aliases for legacy imports."""

from __future__ import annotations

from importlib import import_module

_LEGACY_MODULES = {
    "acp_service": "packages.agent.runtime.acp_service",
    "agent_backends": "packages.agent.runtime.agent_backends",
    "agent_runtime_manager": "packages.agent.runtime.agent_runtime_manager",
    "agent_runtime_policy": "packages.agent.runtime.agent_runtime_policy",
    "agent_runtime_state": "packages.agent.runtime.agent_runtime_state",
    "agent_service": "packages.agent.runtime.agent_service",
    "agent_tools": "packages.agent.tools.agent_tools",
    "agent_transcript": "packages.agent.runtime.agent_transcript",
    "apply_patch_runtime": "packages.agent.tools.apply_patch_runtime",
    "claw_mcp_registry": "packages.agent.mcp.claw_mcp_registry",
    "claw_mcp_runtime": "packages.agent.mcp.claw_mcp_runtime",
    "claw_runtime_manager": "packages.agent.runtime.claw_runtime_manager",
    "cli_agent_service": "packages.agent.runtime.cli_agent_service",
    "global_bus": "packages.agent.runtime.global_bus",
    "mcp_service": "packages.agent.mcp.mcp_service",
    "mounted_paper_context": "packages.agent.tools.mounted_paper_context",
    "opencode_manager": "packages.agent.runtime.opencode_manager",
    "permission_next": "packages.agent.runtime.permission_next",
    "research_tool_catalog": "packages.agent.tools.research_tool_catalog",
    "research_tool_runtime": "packages.agent.tools.research_tool_runtime",
    "researchos_mcp": "packages.agent.mcp.researchos_mcp",
    "researchos_mcp_registry": "packages.agent.mcp.researchos_mcp_registry",
    "researchos_mcp_runtime": "packages.agent.mcp.researchos_mcp_runtime",
    "runtime_cleanup": "packages.agent.runtime.runtime_cleanup",
    "session_bus": "packages.agent.session.session_bus",
    "session_compaction": "packages.agent.session.session_compaction",
    "session_errors": "packages.agent.session.session_errors",
    "session_events": "packages.agent.session.session_events",
    "session_instance": "packages.agent.session.session_instance",
    "session_lifecycle": "packages.agent.session.session_lifecycle",
    "session_message_v2": "packages.agent.session.session_message_v2",
    "session_pending": "packages.agent.session.session_pending",
    "session_plan": "packages.agent.session.session_plan",
    "session_processor": "packages.agent.session.session_processor",
    "session_question": "packages.agent.session.session_question",
    "session_retry": "packages.agent.session.session_retry",
    "session_revert": "packages.agent.session.session_revert",
    "session_runtime": "packages.agent.session.session_runtime",
    "session_snapshot": "packages.agent.session.session_snapshot",
    "session_store": "packages.agent.session.session_store",
    "session_tool_runtime": "packages.agent.session.session_tool_runtime",
    "skill_registry": "packages.agent.tools.skill_registry",
    "skill_tool_runtime": "packages.agent.tools.skill_tool_runtime",
    "terminal_service": "packages.agent.workspace.terminal_service",
    "tool_catalog": "packages.agent.tools.tool_catalog",
    "tool_context": "packages.agent.tools.tool_context",
    "tool_exposure": "packages.agent.tools.tool_exposure",
    "tool_registry": "packages.agent.tools.tool_registry",
    "tool_runtime": "packages.agent.tools.tool_runtime",
    "tool_schema": "packages.agent.tools.tool_schema",
    "web_tool_runtime": "packages.agent.tools.web_tool_runtime",
    "workspace_executor": "packages.agent.workspace.workspace_executor",
    "workspace_remote": "packages.agent.workspace.workspace_remote",
    "workspace_server_registry": "packages.agent.workspace.workspace_server_registry",
}

__all__ = sorted(_LEGACY_MODULES)


def __getattr__(name: str):
    target = _LEGACY_MODULES.get(name)
    if target is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module = import_module(target)
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
