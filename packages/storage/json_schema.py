"""Helpers for versioning JSON payload columns without changing their shape."""

from __future__ import annotations

from typing import Any

DEFAULT_SCHEMA_VERSION = 1
SCHEMA_VERSION_KEY = "schema_version"


def with_schema_version(value: dict[str, Any] | None, *, version: int = DEFAULT_SCHEMA_VERSION) -> dict[str, Any]:
    payload = dict(value or {})
    payload.setdefault(SCHEMA_VERSION_KEY, version)
    return payload


def versioned_list(items: list[dict[str, Any]] | None, *, version: int = DEFAULT_SCHEMA_VERSION) -> list[dict[str, Any]]:
    return [with_schema_version(item, version=version) if isinstance(item, dict) else item for item in (items or [])]


__all__ = ["DEFAULT_SCHEMA_VERSION", "SCHEMA_VERSION_KEY", "versioned_list", "with_schema_version"]

