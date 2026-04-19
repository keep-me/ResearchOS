from __future__ import annotations

import sys
from types import SimpleNamespace

from packages.integrations import llm_provider_registry


def test_get_openai_client_reuses_cached_client(monkeypatch) -> None:  # noqa: ANN001
    created: list[dict[str, object]] = []

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))
    llm_provider_registry.reset_client_caches()

    first = llm_provider_registry.get_openai_client(
        "key-1",
        "https://api.openai.com/v1",
        timeout=30,
    )
    second = llm_provider_registry.get_openai_client(
        "key-1",
        "https://api.openai.com/v1",
        timeout=30,
    )
    third = llm_provider_registry.get_openai_client(
        "key-1",
        "https://api.openai.com/v1",
        timeout=31,
    )

    assert first is second
    assert first is not third
    assert created == [
        {
            "api_key": "key-1",
            "base_url": "https://api.openai.com/v1",
            "timeout": 30.0,
            "max_retries": 1,
        },
        {
            "api_key": "key-1",
            "base_url": "https://api.openai.com/v1",
            "timeout": 31.0,
            "max_retries": 1,
        },
    ]


def test_get_anthropic_client_reuses_cache_and_preserves_base_url(monkeypatch) -> None:  # noqa: ANN001
    created: list[dict[str, object]] = []

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_FakeAnthropic))
    llm_provider_registry.reset_client_caches()

    first = llm_provider_registry.get_anthropic_client(
        "anthropic-key",
        base_url="https://api.anthropic.com",
    )
    second = llm_provider_registry.get_anthropic_client(
        "anthropic-key",
        base_url="https://api.anthropic.com",
    )
    third = llm_provider_registry.get_anthropic_client(
        "anthropic-key",
        base_url="https://anthropic-proxy.example.com",
    )

    assert first is second
    assert first is not third
    assert created == [
        {
            "api_key": "anthropic-key",
            "base_url": "https://api.anthropic.com",
        },
        {
            "api_key": "anthropic-key",
            "base_url": "https://anthropic-proxy.example.com",
        },
    ]
