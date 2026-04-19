from __future__ import annotations

from packages.integrations.llm_provider_dispatch import (
    resolve_chat_dispatch,
    resolve_chat_test_dispatch,
    resolve_embedding_dispatch,
    resolve_embedding_test_dispatch,
    resolve_summary_dispatch,
)
from packages.integrations.llm_provider_schema import (
    ResolvedEmbeddingConfig,
    ResolvedModelTarget,
)


def _target(
    *,
    provider: str,
    api_key: str | None = "test-key",
    base_url: str = "",
    model: str = "test-model",
) -> ResolvedModelTarget:
    return ResolvedModelTarget(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        variant=None,
        stage="rag",
    )


def _embedding(
    *,
    provider: str,
    api_key: str | None = "embed-key",
    base_url: str = "",
    model: str = "text-embedding-3-small",
) -> ResolvedEmbeddingConfig:
    return ResolvedEmbeddingConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        explicit_provider=False,
        explicit_api_key=False,
        explicit_base_url=False,
    )


def test_summary_dispatch_routes_match_current_provider_matrix() -> None:
    official_openai = resolve_summary_dispatch(
        _target(provider="openai", base_url="https://api.openai.com/v1", model="gpt-5.2")
    )
    zhipu = resolve_summary_dispatch(
        _target(provider="zhipu", base_url="https://open.bigmodel.cn/api/paas/v4/", model="glm-4.7")
    )
    disabled = resolve_summary_dispatch(_target(provider="none", api_key=None))

    assert official_openai.route == "openai-responses"
    assert zhipu.route == "openai-compatible"
    assert disabled.route == "pseudo"
    assert disabled.fallback_reason == "missing_active_config"


def test_chat_dispatch_distinguishes_official_openai_from_compatible_gateways() -> None:
    official_openai = resolve_chat_dispatch(
        _target(provider="openai", base_url="https://api.openai.com/v1", model="gpt-5.2")
    )
    dashscope = resolve_chat_dispatch(
        _target(
            provider="openai",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus",
        )
    )
    anthropic = resolve_chat_dispatch(
        _target(provider="anthropic", base_url="https://api.anthropic.com", model="claude-sonnet-4-5")
    )

    assert official_openai.route == "openai-responses"
    assert dashscope.route == "openai-compatible"
    assert anthropic.route == "anthropic"


def test_chat_test_dispatch_preserves_openai_zhipu_and_anthropic_routes() -> None:
    assert resolve_chat_test_dispatch(_target(provider="none", api_key=None)).route == "disabled"
    assert resolve_chat_test_dispatch(_target(provider="openai", api_key=None)).route == "missing_api_key"
    assert resolve_chat_test_dispatch(_target(provider="openai")).route == "openai"
    assert resolve_chat_test_dispatch(_target(provider="zhipu")).route == "openai-compatible"
    assert resolve_chat_test_dispatch(_target(provider="custom")).route == "openai-compatible"
    assert resolve_chat_test_dispatch(_target(provider="anthropic")).route == "anthropic"


def test_embedding_dispatch_reports_supported_and_fallback_routes() -> None:
    disabled = resolve_embedding_dispatch("none", _embedding(provider="openai"))
    supported = resolve_embedding_dispatch("openai", _embedding(provider="openai"))
    missing_key = resolve_embedding_dispatch("openai", _embedding(provider="openai", api_key=None))
    unsupported = resolve_embedding_dispatch("openai", _embedding(provider="anthropic"))

    assert disabled.route == "pseudo"
    assert disabled.fallback_reason == "missing_active_config"
    assert supported.route == "openai-compatible"
    assert missing_key.route == "pseudo"
    assert missing_key.fallback_reason == "missing_api_key"
    assert unsupported.route == "pseudo"
    assert unsupported.fallback_reason == "unsupported_provider"


def test_embedding_test_dispatch_tracks_disabled_and_unsupported_routes() -> None:
    assert resolve_embedding_test_dispatch("none", _embedding(provider="openai")).route == "disabled"
    assert resolve_embedding_test_dispatch("openai", _embedding(provider="anthropic")).route == "unsupported"
    assert resolve_embedding_test_dispatch(
        "openai",
        _embedding(provider="openai", api_key=None),
    ).route == "missing_api_key"
    assert resolve_embedding_test_dispatch("openai", _embedding(provider="zhipu")).route == "openai-compatible"
    assert resolve_embedding_test_dispatch("custom", _embedding(provider="custom")).route == "openai-compatible"
