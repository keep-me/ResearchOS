from __future__ import annotations

from packages.integrations.llm_provider_policy import (
    is_anthropic_chat_target,
    is_mistral_chat_target,
    should_try_raw_openai_http_fallback,
    supports_chat_reasoning_content,
)
from packages.integrations.llm_provider_schema import ResolvedModelTarget


def _target(*, provider: str, model: str, base_url: str = "") -> ResolvedModelTarget:
    return ResolvedModelTarget(
        provider=provider,
        api_key="test-key",
        base_url=base_url,
        model=model,
        variant=None,
        stage="rag",
    )


def test_supports_chat_reasoning_content_for_zhipu_and_non_official_gateways() -> None:
    assert supports_chat_reasoning_content(
        _target(provider="zhipu", model="glm-4.7", base_url="https://open.bigmodel.cn/api/paas/v4/")
    )
    assert supports_chat_reasoning_content(
        _target(
            provider="openai",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    assert not supports_chat_reasoning_content(
        _target(provider="openai", model="gpt-5.2", base_url="https://api.openai.com/v1")
    )


def test_chat_target_detection_matches_anthropic_and_mistral_families() -> None:
    assert is_anthropic_chat_target(
        _target(provider="openai", model="claude-sonnet-4-5", base_url="https://api.anthropic.com")
    )
    assert is_mistral_chat_target(
        _target(provider="openai", model="devstral-small", base_url="https://api.mistral.ai/v1")
    )


def test_raw_http_fallback_only_applies_to_blocked_non_official_targets() -> None:
    blocked = RuntimeError("Your request was blocked by the upstream gateway")

    assert should_try_raw_openai_http_fallback(
        _target(
            provider="openai",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        blocked,
    )
    assert not should_try_raw_openai_http_fallback(
        _target(provider="openai", model="gpt-5.2", base_url="https://api.openai.com/v1"),
        blocked,
    )
    assert not should_try_raw_openai_http_fallback(
        _target(
            provider="openai",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        RuntimeError("rate limit"),
    )
