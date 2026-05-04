"""Shared runtime cleanup helpers for desktop/app shutdown paths."""

from __future__ import annotations

from packages.agent.mcp.mcp_service import get_mcp_registry_service
from packages.agent.runtime.acp_service import get_acp_registry_service
from packages.agent.runtime.opencode_manager import get_opencode_runtime_manager
from packages.agent.session.session_instance import dispose_all_instances
from packages.agent.workspace.terminal_service import get_terminal_service
from packages.ai.ops.idle_processor import stop_idle_processor


async def dispose_runtime_state() -> dict[str, object]:
    stop_idle_processor()
    await get_mcp_registry_service().close_all()
    get_acp_registry_service().close_all()
    get_terminal_service().dispose_all()

    runtime_manager = get_opencode_runtime_manager()
    runtime_snapshot = runtime_manager.snapshot() if hasattr(runtime_manager, "snapshot") else {}
    runtime_directory = str(runtime_snapshot.get("default_directory") or "").strip()
    runtime_manager.stop()

    disposed = dispose_all_instances(
        extra_directories=[runtime_directory] if runtime_directory else []
    )
    return {
        "runtime_directory": runtime_directory,
        "disposed_directories": disposed,
    }
