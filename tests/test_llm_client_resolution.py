from __future__ import annotations

from types import SimpleNamespace

from packages.integrations.llm_client import LLMConfig
from packages.integrations.llm_provider_resolver import (
    parse_model_target,
    resolve_embedding_config,
    resolve_model_target,
    resolve_transport_base_url,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        zhipu_api_key="zhipu-key",
        openai_api_key="openai-key",
        anthropic_api_key="anthropic-key",
        openai_base_url="https://api.openai.com",
    )


def _config() -> LLMConfig:
    return LLMConfig(
        provider="openai",
        api_key="active-openai-key",
        api_base_url="https://primary.example.com",
        model_skim="primary-mini",
        model_deep="primary-deep",
        model_vision="primary-vision",
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="text-embedding-3-small",
        model_fallback="primary-fallback",
    )


def test_parse_model_target_understands_provider_and_variant_suffix() -> None:
    parsed = parse_model_target("openai/gpt-5.2/high")

    assert parsed is not None
    assert parsed.provider == "openai"
    assert parsed.model == "gpt-5.2"
    assert parsed.variant == "high"


def test_resolve_transport_base_url_normalizes_openai_and_zhipu() -> None:
    assert (
        resolve_transport_base_url("openai", "https://api.openai.com")
        == "https://api.openai.com/v1"
    )
    assert (
        resolve_transport_base_url("zhipu", "https://open.bigmodel.cn/api/paas/v4")
        == "https://open.bigmodel.cn/api/paas/v4/"
    )
    assert (
        resolve_transport_base_url("custom", "https://wlxctech.cn/codex")
        == "https://wlxctech.cn/codex"
    )


def test_resolve_embedding_config_infers_provider_from_embedding_base_url() -> None:
    cfg = _config()
    cfg.provider = "anthropic"
    cfg.embedding_api_base_url = "https://api.openai.com/v1"
    cfg.embedding_api_key = "embed-key"

    resolved = resolve_embedding_config(cfg)

    assert resolved.provider == "openai"
    assert resolved.api_key == "embed-key"
    assert resolved.base_url == "https://api.openai.com/v1"
    assert resolved.explicit_provider is False
    assert resolved.explicit_base_url is True


def test_resolve_embedding_config_infers_custom_provider_from_generic_http_base_url() -> None:
    cfg = _config()
    cfg.provider = "custom"
    cfg.embedding_api_base_url = "https://wlxctech.cn/codex"
    cfg.embedding_api_key = "embed-key"

    resolved = resolve_embedding_config(cfg)

    assert resolved.provider == "custom"
    assert resolved.api_key == "embed-key"
    assert resolved.base_url == "https://wlxctech.cn/codex"


def test_resolve_model_target_uses_engine_profile_runtime_config() -> None:
    resolved = resolve_model_target(
        "project_literature_review",
        "llmcfg:test:deep",
        cfg=_config(),
        settings=_settings(),
        engine_profile_resolver=lambda _profile_id: {
            "provider": "anthropic",
            "model": "claude-sonnet-test",
            "default_variant": "medium",
            "runtime_config": {
                "provider": "anthropic",
                "api_key": "profile-anthropic-key",
                "api_base_url": "https://api.anthropic.com",
                "model_skim": "claude-haiku-test",
                "model_deep": "claude-sonnet-test",
                "model_vision": "claude-vision-test",
                "embedding_provider": "openai",
                "embedding_api_key": "embed-key",
                "embedding_api_base_url": "https://embed.example.com",
                "model_embedding": "text-embedding-3-large",
                "model_fallback": "claude-fallback-test",
            },
        },
    )

    assert resolved.provider == "anthropic"
    assert resolved.api_key == "profile-anthropic-key"
    assert resolved.base_url == "https://api.anthropic.com"
    assert resolved.model == "claude-sonnet-test"
    assert resolved.variant == "medium"


def test_resolve_model_target_uses_provider_prefixed_override_with_default_provider_credentials() -> (
    None
):
    resolved = resolve_model_target(
        "build",
        "zhipu/glm-4.7/high",
        cfg=_config(),
        settings=_settings(),
        engine_profile_resolver=lambda _profile_id: None,
    )

    assert resolved.provider == "zhipu"
    assert resolved.api_key == "zhipu-key"
    assert resolved.base_url == "https://open.bigmodel.cn/api/paas/v4/"
    assert resolved.model == "glm-4.7"
    assert resolved.variant == "high"
