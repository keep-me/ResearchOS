from __future__ import annotations

import hashlib
import threading

OPENAI_DEFAULT_TIMEOUT = 120
OPENAI_MAX_RETRIES = 1

_openai_clients: dict[str, object] = {}
_anthropic_clients: dict[str, object] = {}
_client_lock = threading.Lock()


def _cache_key(*parts: object) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def get_openai_client(
    api_key: str,
    base_url: str | None,
    timeout: float | None = None,
):
    from openai import OpenAI

    effective_timeout = float(timeout if timeout is not None else OPENAI_DEFAULT_TIMEOUT)
    cache_key = _cache_key(
        "openai",
        api_key,
        base_url,
        effective_timeout,
        OPENAI_MAX_RETRIES,
    )
    with _client_lock:
        if cache_key not in _openai_clients:
            _openai_clients[cache_key] = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=effective_timeout,
                max_retries=OPENAI_MAX_RETRIES,
            )
        return _openai_clients[cache_key]


def get_anthropic_client(
    api_key: str,
    base_url: str | None = None,
    timeout: float | None = None,
):
    from anthropic import Anthropic

    cache_key = _cache_key("anthropic", api_key, base_url, timeout)
    with _client_lock:
        if cache_key not in _anthropic_clients:
            kwargs = {
                "api_key": api_key,
                "base_url": base_url,
            }
            if timeout is not None:
                kwargs["timeout"] = float(timeout)
            _anthropic_clients[cache_key] = Anthropic(**kwargs)
        return _anthropic_clients[cache_key]


def reset_client_caches() -> None:
    with _client_lock:
        _openai_clients.clear()
        _anthropic_clients.clear()
