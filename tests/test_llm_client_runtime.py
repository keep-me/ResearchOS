from __future__ import annotations

from types import SimpleNamespace

import pytest

from packages.integrations import llm_provider_runtime
from packages.integrations.llm_client import LLMConfig
from packages.integrations.llm_provider_http import ProviderHTTPError
from packages.integrations.llm_provider_schema import ResolvedEmbeddingConfig, ResolvedModelTarget


def _cfg(*, provider: str = "openai") -> LLMConfig:
    return LLMConfig(
        provider=provider,
        api_key=None,
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5-mini",
        model_deep="gpt-5.2",
        model_vision="gpt-4o",
        embedding_provider="openai",
        embedding_api_key=None,
        embedding_api_base_url="https://api.openai.com/v1",
        model_embedding="text-embedding-3-small",
        model_fallback="gpt-4o-mini",
    )


def _target(
    *,
    provider: str = "openai",
    api_key: str | None = None,
    base_url: str | None = "https://api.openai.com/v1",
    model: str = "gpt-5.2",
) -> ResolvedModelTarget:
    return ResolvedModelTarget(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        variant=None,
        stage="skim",
    )


def _embedding(
    *,
    provider: str = "openai",
    api_key: str | None = None,
    base_url: str | None = "https://api.openai.com/v1",
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


def test_runtime_chat_missing_api_key_returns_structured_error() -> None:
    class _FakeClient:
        def _resolve_model_target(self, *_args, **_kwargs):
            return _target(api_key=None)

        def _resolve_chat_test_dispatch(self, _target):
            return SimpleNamespace(route="missing_api_key")

    result = llm_provider_runtime.test_chat_config(_FakeClient(), _cfg())

    assert result["ok"] is False
    assert result["message"] == "缺少 API Key。"
    assert result["transport"] == "missing_api_key"
    assert result["error"]["name"] == "AuthError"
    assert result["error"]["statusCode"] == 401
    assert result["error"]["providerID"] == "openai"


def test_runtime_embedding_custom_routes_through_openai_compatible_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeClient:
        def _resolve_embedding_config(self, _cfg):
            return _embedding(
                provider="custom", api_key="embed-key", base_url="https://embed.example/v1"
            )

        def _resolve_embedding_test_dispatch(self, _provider, _embedding_cfg):
            return SimpleNamespace(route="openai-compatible")

    monkeypatch.setattr(
        llm_provider_runtime.llm_provider_probe,
        "probe_embedding_openai_compatible",
        lambda client, cfg, embedding_cfg: {
            "ok": True,
            "message": "连接成功",
            "provider": embedding_cfg.provider,
            "transport": "embeddings",
        },
    )

    result = llm_provider_runtime.test_embedding_config(_FakeClient(), _cfg(provider="custom"))

    assert result["ok"] is True
    assert result["message"] == "连接成功"
    assert result["transport"] == "embeddings"
    assert result["provider"] == "custom"


def test_runtime_vision_returns_diagnostic_message_for_blocked_gateway() -> None:
    class _FakeResult:
        def __init__(self, *, content: str) -> None:
            self.content = content

    class _FakeClient:
        def _config(self):
            return _cfg(provider="custom")

        def _resolve_model_target(self, *_args, **_kwargs):
            return ResolvedModelTarget(
                provider="openai",
                api_key="test-key",
                base_url="https://gmncode.com/v1",
                model="gpt-5.4",
                variant=None,
                stage="vision",
            )

        def _get_openai_client(self, *_args, **_kwargs):
            raise PermissionError("Your request was blocked.")

        def _vision_openai_compatible(self, **_kwargs):
            return None

        def _call_openai_chat_raw_http(self, **_kwargs):
            raise ProviderHTTPError(
                "Service temporarily unavailable",
                status_code=503,
                response_body="",
            )

    result = llm_provider_runtime.vision_analyze(
        _FakeClient(),
        _FakeResult,
        image_base64="ZmFrZQ==",
        prompt="describe image",
        stage="vision",
        max_tokens=80,
    )

    assert "当前视觉模型不可用" in result.content
    assert "gpt-5.4" in result.content
    assert "gmncode.com" in result.content
