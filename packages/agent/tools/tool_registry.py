"""Canonical OpenCode-style tool registry for local builtin and custom tools."""

from __future__ import annotations

import copy
import importlib
import threading
from collections.abc import Callable
from typing import Any

from packages.agent.runtime.agent_runtime_state import normalize_mode
from packages.agent.session.session_plan import PLAN_MODE_ALLOWED_TOOLS
from packages.agent.tools.tool_catalog import TOOL_REGISTRY
from packages.agent.tools.tool_exposure import (
    function_tool_name,
    is_official_openai_target,
    prefer_apply_patch_tool,
)
from packages.agent.tools.tool_schema import ToolDef, ToolSpec

_LOCK = threading.RLock()
_INITIALIZED = False
_CATALOG_ORDER: list[str] = []
_CATALOG_DEFS: dict[str, ToolDef] = {}
_CATALOG_HANDLERS: dict[str, Callable[..., Any]] = {}
_CUSTOM_DEFS: dict[str, ToolDef] = {}
_CUSTOM_HANDLERS: dict[str, Callable[..., Any]] = {}
_DEFAULT_OPT_IN_TOOL_NAMES = {
    "search_papers",
    "get_system_status",
    "list_local_skills",
    "read_local_skill",
}

READ_ONLY_MODES = {"plan"}

_COMPAT_TOOL_DEFS: dict[str, ToolDef] = {
    "local_shell": ToolDef(
        name="local_shell",
        description="Provider-defined local shell bridge.",
        parameters={"type": "object", "properties": {}},
        spec=ToolSpec(permission="bash", managed_permission=True),
        handler="_local_shell_command",
    ),
}


def _ensure_initialized() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _LOCK:
        if _INITIALIZED:
            return
        _CATALOG_ORDER[:] = []
        _CATALOG_DEFS.clear()
        for tool in TOOL_REGISTRY:
            _CATALOG_ORDER.append(tool.name)
            _CATALOG_DEFS[tool.name] = copy.deepcopy(tool)
        _INITIALIZED = True


def _all_definitions() -> list[ToolDef]:
    _ensure_initialized()
    with _LOCK:
        ordered: list[ToolDef] = []
        seen: set[str] = set()
        for name in _CATALOG_ORDER:
            tool = _CUSTOM_DEFS.get(name) or _CATALOG_DEFS.get(name)
            if tool is None:
                continue
            ordered.append(copy.deepcopy(tool))
            seen.add(name)
        for name, tool in _CUSTOM_DEFS.items():
            if name in seen:
                continue
            ordered.append(copy.deepcopy(tool))
        return ordered


def register_tool(
    tool: ToolDef,
    *,
    handler: Callable[..., Any] | None = None,
) -> ToolDef:
    _ensure_initialized()
    definition = copy.deepcopy(tool)
    with _LOCK:
        _CUSTOM_DEFS[definition.name] = definition
        if handler is not None:
            _CUSTOM_HANDLERS[definition.name] = handler
    return copy.deepcopy(definition)


def unregister_tool(name: str) -> bool:
    _ensure_initialized()
    normalized = str(name or "").strip()
    if not normalized:
        return False
    removed = False
    with _LOCK:
        if normalized in _CUSTOM_DEFS:
            _CUSTOM_DEFS.pop(normalized, None)
            removed = True
        if normalized in _CUSTOM_HANDLERS:
            _CUSTOM_HANDLERS.pop(normalized, None)
            removed = True
    return removed


def reset_custom_tools() -> None:
    _ensure_initialized()
    with _LOCK:
        _CUSTOM_DEFS.clear()
        _CUSTOM_HANDLERS.clear()


def _resolve_builtin_handler(name: str) -> Callable[..., Any] | None:
    _ensure_initialized()
    with _LOCK:
        cached = _CATALOG_HANDLERS.get(name)
        definition = copy.deepcopy(_CATALOG_DEFS.get(name) or _COMPAT_TOOL_DEFS.get(name))
    if callable(cached):
        return cached
    if not isinstance(definition, ToolDef):
        return None
    raw_handler = str(definition.handler or f"_{name}").strip()
    module_name = "packages.agent.tools.agent_tools"
    attr_name = raw_handler
    if ":" in raw_handler:
        module_name, attr_name = (segment.strip() for segment in raw_handler.split(":", 1))
    if not attr_name:
        return None
    try:
        handler_module = importlib.import_module(module_name)
    except Exception:
        return None
    handler = getattr(handler_module, attr_name, None)
    if not callable(handler):
        return None
    with _LOCK:
        _CATALOG_HANDLERS[name] = handler
    return handler


def resolve_tool_handler(name: str) -> Callable[..., Any] | None:
    _ensure_initialized()
    normalized = str(name or "").strip()
    if not normalized:
        return None
    with _LOCK:
        custom = _CUSTOM_HANDLERS.get(normalized)
    if callable(custom):
        return custom
    return _resolve_builtin_handler(normalized)


def is_remote_workspace_server(workspace_server_id: str | None) -> bool:
    normalized_server_id = str(workspace_server_id or "").strip().lower()
    return bool(normalized_server_id and normalized_server_id != "local")


def tool_spec(name: str) -> ToolSpec:
    normalized = str(name or "").strip()
    definition = get_tool_definition(normalized)
    if isinstance(definition, ToolDef) and isinstance(definition.spec, ToolSpec):
        return definition.spec
    return ToolSpec()


def tool_permission(name: str) -> str:
    normalized = str(name or "").strip()
    spec = tool_spec(normalized)
    return str(spec.permission or normalized or "*")


def manages_tool(name: str) -> bool:
    return bool(tool_spec(name).managed_permission)


def tool_registry_names() -> set[str]:
    return {tool.name for tool in _all_definitions()}


def tool_source(name: str) -> str | None:
    normalized = str(name or "").strip()
    if not normalized:
        return None
    _ensure_initialized()
    with _LOCK:
        if normalized in _CUSTOM_DEFS:
            return "custom"
        if normalized in _CATALOG_DEFS:
            return "builtin"
        if normalized in _COMPAT_TOOL_DEFS:
            return "compat"
    return None


def get_tool_definition(name: str) -> ToolDef | None:
    normalized = str(name or "").strip()
    if not normalized:
        return None
    _ensure_initialized()
    with _LOCK:
        tool = (
            _CUSTOM_DEFS.get(normalized)
            or _CATALOG_DEFS.get(normalized)
            or _COMPAT_TOOL_DEFS.get(normalized)
        )
        return copy.deepcopy(tool) if tool is not None else None


def tool_allowed_in_mode(tool: ToolDef, mode: str) -> bool:
    normalized_mode = normalize_mode(mode)
    if normalized_mode not in READ_ONLY_MODES:
        return True
    return tool.name in PLAN_MODE_ALLOWED_TOOLS


def default_tool_names_for_workspace(workspace_server_id: str | None) -> set[str]:
    remote = is_remote_workspace_server(workspace_server_id)
    out: set[str] = set()
    for tool in _all_definitions():
        spec = tool_spec(tool.name)
        enabled = spec.default_remote_enabled if remote else spec.default_local_enabled
        if enabled and not (remote and spec.local_only):
            if tool.name in _DEFAULT_OPT_IN_TOOL_NAMES:
                continue
            out.add(tool.name)
    return out


def enabled_tool_names_for_workspace(
    enabled_tools: set[str] | None,
    workspace_server_id: str | None,
) -> set[str]:
    if not enabled_tools:
        return set()
    remote = is_remote_workspace_server(workspace_server_id)
    enabled = {str(name or "").strip() for name in enabled_tools if str(name or "").strip()}
    enabled &= tool_registry_names()
    out: set[str] = set()
    for name in enabled:
        spec = tool_spec(name)
        if not spec.allow_user_enable:
            continue
        if remote and spec.local_only:
            continue
        out.add(name)
    return out


def append_provider_defined_tool_once(tools: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    entry_id = str(entry.get("id") or "").strip()
    if not entry_id:
        return
    for tool in tools:
        if (
            isinstance(tool, dict)
            and str(tool.get("type") or "").strip() == "provider-defined"
            and str(tool.get("id") or "").strip() == entry_id
        ):
            return
    tools.append(copy.deepcopy(entry))


def _provider_defined_tools_for_names(names: set[str]) -> list[dict[str, Any]]:
    provider_defined: list[dict[str, Any]] = []
    for name in sorted(names):
        definition = get_tool_definition(name)
        if not isinstance(definition, ToolDef):
            continue
        for entry in definition.provider_tools or []:
            if isinstance(entry, dict):
                append_provider_defined_tool_once(provider_defined, entry)
    return provider_defined


def get_openai_tools(
    mode: str = "build",
    *,
    workspace_server_id: str | None = None,
    disabled_tools: set[str] | None = None,
    enabled_tools: set[str] | None = None,
) -> list[dict[str, Any]]:
    allowed_names = default_tool_names_for_workspace(workspace_server_id)
    allowed_names |= enabled_tool_names_for_workspace(enabled_tools, workspace_server_id)
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in _all_definitions()
        if tool_allowed_in_mode(tool, mode)
        and tool.name in allowed_names
        and (disabled_tools is None or tool.name not in disabled_tools)
    ]


def build_turn_tools(
    llm: Any,
    *,
    mode: str = "build",
    workspace_server_id: str | None = None,
    disabled_tools: set[str] | None = None,
    user_tools: dict[str, bool] | None = None,
    enabled_tools: set[str] | None = None,
    reasoning_level: str = "default",
) -> list[dict[str, Any]]:
    effective_disabled_tools = set(disabled_tools or set())
    explicitly_enabled_tools: set[str] = set(enabled_tools or set())
    if isinstance(user_tools, dict):
        for name, enabled in user_tools.items():
            normalized_name = str(name or "").strip()
            if not normalized_name:
                continue
            if enabled is False:
                effective_disabled_tools.add(normalized_name)
            elif enabled is True:
                explicitly_enabled_tools.add(normalized_name)

    tools = get_openai_tools(
        mode,
        workspace_server_id=workspace_server_id,
        disabled_tools=effective_disabled_tools,
        enabled_tools=explicitly_enabled_tools,
    )
    prefer_apply_patch = prefer_apply_patch_tool(llm, reasoning_level)
    if prefer_apply_patch is True:
        tools = [tool for tool in tools if function_tool_name(tool) not in {"edit", "write"}]
    elif prefer_apply_patch is False:
        tools = [tool for tool in tools if function_tool_name(tool) != "apply_patch"]
    if not is_official_openai_target(llm, reasoning_level):
        return tools

    provider_defined_tools = _provider_defined_tools_for_names(
        {function_tool_name(tool) for tool in tools if function_tool_name(tool)}
    )
    for entry in provider_defined_tools:
        append_provider_defined_tool_once(tools, entry)
    return tools
