"""Retry helpers for transient LLM failures."""

from __future__ import annotations

from email.utils import parsedate_to_datetime
import time

from typing import Any

from packages.agent.session.session_errors import normalize_error

RETRY_INITIAL_DELAY = 2000
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY_NO_HEADERS = 30_000


def retryable(error_value: Any) -> str | None:
    error = normalize_error(error_value)
    message = str(error.get("message") or "").strip().lower()
    if "blocked" in message and "request" in message:
        return None
    if bool(error.get("isRetryable")):
        return "APIError: 上游模型暂时不可用，准备重试"
    return None


def delay(attempt: int, error_value: Any | None = None) -> int:
    clamped = max(int(attempt or 1), 1)
    error = normalize_error(error_value) if error_value is not None else None
    headers = (error or {}).get("responseHeaders") if isinstance(error, dict) else None
    if isinstance(headers, dict):
        retry_after_ms = str(headers.get("retry-after-ms") or "").strip()
        if retry_after_ms:
            try:
                parsed_ms = float(retry_after_ms)
            except (TypeError, ValueError):
                parsed_ms = None
            if parsed_ms is not None and parsed_ms >= 0:
                return int(parsed_ms)

        retry_after = str(headers.get("retry-after") or "").strip()
        if retry_after:
            try:
                parsed_seconds = float(retry_after)
            except (TypeError, ValueError):
                parsed_seconds = None
            if parsed_seconds is not None and parsed_seconds >= 0:
                return int(parsed_seconds * 1000 + 0.999)
            try:
                parsed_date = parsedate_to_datetime(retry_after)
                delta_ms = int((parsed_date.timestamp() - time.time()) * 1000 + 0.999)
            except Exception:
                delta_ms = None
            if delta_ms is not None and delta_ms > 0:
                return delta_ms

    return min(
        RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (clamped - 1)),
        RETRY_MAX_DELAY_NO_HEADERS,
    )


def sleep(session_id: str | None, delay_ms: int) -> bool:
    from packages.agent.session.session_lifecycle import is_session_aborted

    remaining = max(int(delay_ms or 0), 0)
    while remaining > 0:
        if is_session_aborted(session_id):
            return False
        interval = min(remaining, 50)
        time.sleep(interval / 1000)
        remaining -= interval
    return not is_session_aborted(session_id)

