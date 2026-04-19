"""Shared agent backend identifiers and normalization helpers."""

from __future__ import annotations

NATIVE_AGENT_BACKEND_ID = "native"
DEFAULT_AGENT_BACKEND_ID = NATIVE_AGENT_BACKEND_ID
LEGACY_CLI_AGENT_BACKEND_ID = "claw"
CLAW_AGENT_BACKEND_ID = LEGACY_CLI_AGENT_BACKEND_ID
LEGACY_NATIVE_AGENT_BACKEND_IDS = frozenset({"native", "researchos_native"})


def normalize_agent_backend_id(
    value: str | None,
    *,
    default: str = DEFAULT_AGENT_BACKEND_ID,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    if raw in LEGACY_NATIVE_AGENT_BACKEND_IDS:
        return DEFAULT_AGENT_BACKEND_ID
    return raw


def is_native_agent_backend(value: str | None) -> bool:
    return normalize_agent_backend_id(value) == DEFAULT_AGENT_BACKEND_ID
