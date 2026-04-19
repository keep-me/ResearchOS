"""Neutral runtime-manager entrypoint for the embedded assistant bridge."""

from __future__ import annotations

from packages.agent.runtime.claw_runtime_manager import (
    ClawRuntimeManager,
    get_claw_runtime_manager,
)

EmbeddedAgentRuntimeManager = ClawRuntimeManager


def get_agent_runtime_manager() -> EmbeddedAgentRuntimeManager:
    return get_claw_runtime_manager()


__all__ = [
    "EmbeddedAgentRuntimeManager",
    "get_agent_runtime_manager",
]

