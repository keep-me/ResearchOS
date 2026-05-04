"""SSE event parsing and formatting for agent session streams."""

from __future__ import annotations

import copy
import json
from typing import Any


def parse_sse_event(raw: str) -> tuple[str, dict[str, Any]] | None:
    event_name = ""
    payload = ""
    for line in str(raw or "").splitlines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
    if not event_name:
        return None
    try:
        data = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        data = {"raw": payload}
    return event_name, data if isinstance(data, dict) else {}


def coerce_runtime_event(raw: Any) -> tuple[str, dict[str, Any]] | None:
    event_name = str(getattr(raw, "event", "") or "").strip()
    if event_name:
        data = getattr(raw, "data", {})
        return event_name, copy.deepcopy(data) if isinstance(data, dict) else {}
    return parse_sse_event(str(raw or ""))


def format_sse_event(event_name: str, data: dict[str, Any] | None = None) -> str:
    payload = data if isinstance(data, dict) else {}
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["coerce_runtime_event", "format_sse_event", "parse_sse_event"]
