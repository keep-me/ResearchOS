"""Error normalization helpers for the native session runtime."""

from __future__ import annotations

from typing import Any

from packages.integrations import llm_provider_error


def _message_from_error(error: Any) -> str:
    if isinstance(error, dict):
        for key in ("message", "detail", "error"):
            value = error.get(key)
            if isinstance(value, dict):
                nested = value.get("message") or value.get("detail")
                if nested:
                    return str(nested).strip()
            if value:
                return str(value).strip()
        return ""
    return str(error or "").strip()


def normalize_error(error: Any) -> dict[str, object]:
    text = _message_from_error(error) or "Unknown error"
    lowered = text.lower()
    error_name = str(getattr(error, "name", "") or getattr(error.__class__, "__name__", "")).strip()

    if (
        "会话已中止" in text
        or "aborted" in lowered
        or "cancelled" in lowered
        or error_name in {"AbortError", "CancelledError"}
    ):
        return {
            "name": "AbortedError",
            "message": text,
            "isRetryable": False,
        }

    return llm_provider_error.to_session_error_payload(error)
