from __future__ import annotations

from types import SimpleNamespace

from packages.integrations import llm_provider_probe
from packages.integrations.llm_provider_http import ProviderHTTPError
from packages.integrations.llm_provider_schema import (
    ResolvedEmbeddingConfig,
    ResolvedModelTarget,
)


def _target(
    *,
    provider: str = "openai",
    api_key: str = "test-key",
    base_url: str = "https://api.openai.com/v1",
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
    api_key: str = "embed-key",
    base_url: str = "https://api.openai.com/v1",
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


def test_probe_openai_chat_reports_responses_success(monkeypatch) -> None:  # noqa: ANN001
    class _FakeClient:
        def _apply_variant_to_responses_kwargs(self, kwargs, target):  # noqa: ANN001
            return None

        def _extract_responses_text_and_reasoning(self, response):  # noqa: ANN001
            return ("OK", "")

        def _should_try_raw_openai_http_fallback(self, target, exc):  # noqa: ANN001
            return False

        def _apply_variant_to_chat_kwargs(self, kwargs, target):  # noqa: ANN001
            return None

    fake_sdk = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: object()),
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: None)),
    )
    monkeypatch.setattr(llm_provider_probe.llm_provider_registry, "get_openai_client", lambda *args, **kwargs: fake_sdk)

    result = llm_provider_probe.probe_openai_chat(_FakeClient(), _target())

    assert result["ok"] is True
    assert result["transport"] == "responses"
    assert result["preview"] == "OK"


def test_probe_embedding_openai_compatible_reports_dimension() -> None:
    class _FakeClient:
        def _embed_openai_compatible_or_raise(self, text, cfg, embedding_cfg):  # noqa: ANN001
            return ([0.1, 0.2, 0.3], embedding_cfg.model, embedding_cfg.base_url)

    result = llm_provider_probe.probe_embedding_openai_compatible(
        _FakeClient(),
        cfg=SimpleNamespace(),
        embedding_cfg=_embedding(),
    )

    assert result["ok"] is True
    assert result["transport"] == "embeddings"
    assert result["dimension"] == 3


def test_probe_openai_compatible_reports_normalized_failure_payload(monkeypatch) -> None:
    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise ProviderHTTPError(
                "rate limit exceeded",
                status_code=429,
                response_body='{"error":{"message":"rate limit exceeded","type":"rate_limit_error","code":"rate_limit"}}',
                response_headers={"retry-after": "3"},
                metadata={
                    "provider": "openai",
                    "transport": "chat.completions(raw-http)",
                    "gateway": "edge-gateway",
                    "bucket": "chat-runtime",
                    "url": "https://api.openai.com/v1/chat/completions",
                },
            )

    monkeypatch.setattr(
        llm_provider_probe.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=_BrokenChatCompletions())
        ),
    )

    class _FakeClient:
        def _apply_variant_to_chat_kwargs(self, kwargs, target):  # noqa: ANN001
            return None

    result = llm_provider_probe.probe_openai_compatible_chat(_FakeClient(), _target())

    assert result["ok"] is False
    assert result["message"] == "rate limit exceeded"
    assert result["transport"] == "chat.completions(raw-http)"
    assert result["gateway"] == "edge-gateway"
    assert result["bucket"] == "chat-runtime"
    assert result["url"] == "https://api.openai.com/v1/chat/completions"
    assert result["error"]["providerID"] == "openai"
    assert result["error"]["statusCode"] == 429
    assert result["error"]["responseHeaders"]["retry-after"] == "3"


def test_probe_openai_compatible_falls_back_to_responses_when_legacy_chat_rejected(monkeypatch) -> None:
    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise ProviderHTTPError(
                "Unsupported legacy protocol: /v1/chat/completions is not supported. Please use /v1/responses.",
                status_code=400,
            )

    monkeypatch.setattr(
        llm_provider_probe.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: SimpleNamespace(
            responses=SimpleNamespace(create=lambda **_kwargs: object()),
            chat=SimpleNamespace(completions=_BrokenChatCompletions()),
        ),
    )

    class _FakeClient:
        def _apply_variant_to_chat_kwargs(self, kwargs, target):  # noqa: ANN001
            return None

        def _apply_variant_to_responses_kwargs(self, kwargs, target):  # noqa: ANN001
            return None

        def _extract_responses_text_and_reasoning(self, response):  # noqa: ANN001
            return ("OK", "")

        def _should_try_openai_responses_fallback(self, target, exc):  # noqa: ANN001
            return True

        def _should_try_raw_openai_http_fallback(self, target, exc):  # noqa: ANN001
            return False

    result = llm_provider_probe.probe_openai_compatible_chat(
        _FakeClient(),
        _target(provider="custom", base_url="https://compat.example/v1", model="gpt-5.4"),
    )

    assert result["ok"] is True
    assert result["transport"] == "responses"
    assert result["preview"] == "OK"


def test_probe_openai_chat_reports_attempt_chain_across_responses_and_chat(monkeypatch) -> None:
    class _BrokenResponses:
        def create(self, **_kwargs):
            raise ProviderHTTPError(
                "responses blocked",
                status_code=400,
                metadata={
                    "provider": "openai",
                    "transport": "responses",
                    "url": "https://api.openai.com/v1/responses",
                },
            )

    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise ProviderHTTPError(
                "chat blocked",
                status_code=503,
                response_headers={"retry-after": "2"},
                metadata={
                    "provider": "openai",
                    "transport": "chat.completions",
                    "bucket": "chat-runtime",
                    "url": "https://api.openai.com/v1/chat/completions",
                },
            )

    monkeypatch.setattr(
        llm_provider_probe.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: SimpleNamespace(
            responses=_BrokenResponses(),
            chat=SimpleNamespace(completions=_BrokenChatCompletions()),
        ),
    )

    class _FakeClient:
        def _apply_variant_to_responses_kwargs(self, kwargs, target):  # noqa: ANN001
            return None

        def _apply_variant_to_chat_kwargs(self, kwargs, target):  # noqa: ANN001
            return None

        def _should_try_raw_openai_http_fallback(self, target, exc):  # noqa: ANN001
            return False

    result = llm_provider_probe.probe_openai_chat(_FakeClient(), _target())

    assert result["ok"] is False
    assert result["message"] == "chat blocked"
    assert result["transport"] == "chat.completions"
    attempts = result["error"]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["transport"] == "responses"
    assert attempts[0]["message"] == "responses blocked"
    assert attempts[1]["transport"] == "chat.completions"
    assert attempts[1]["message"] == "chat blocked"


def test_probe_anthropic_chat_reports_normalized_failure_payload(monkeypatch) -> None:
    class _BrokenMessages:
        def create(self, **_kwargs):
            raise ProviderHTTPError(
                "invalid x-api-key",
                status_code=401,
                response_body='{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}',
                response_headers={"x-request-id": "req_123"},
                metadata={
                    "provider": "anthropic",
                    "transport": "anthropic.messages",
                    "gateway": "anthropic-edge",
                    "bucket": "chat-runtime",
                    "url": "https://api.anthropic.com/v1/messages",
                },
            )

    monkeypatch.setattr(
        llm_provider_probe.llm_provider_registry,
        "get_anthropic_client",
        lambda *_args, **_kwargs: SimpleNamespace(messages=_BrokenMessages()),
    )

    result = llm_provider_probe.probe_anthropic_chat(
        SimpleNamespace(),
        cfg=SimpleNamespace(),
        target=_target(provider="anthropic", base_url="https://api.anthropic.com", model="claude-3-7-sonnet"),
    )

    assert result["ok"] is False
    assert result["message"] == "invalid x-api-key"
    assert result["transport"] == "anthropic.messages"
    assert result["gateway"] == "anthropic-edge"
    assert result["bucket"] == "chat-runtime"
    assert result["url"] == "https://api.anthropic.com/v1/messages"
    assert result["error"]["name"] == "AuthError"
    assert result["error"]["providerID"] == "anthropic"
    assert result["error"]["statusCode"] == 401
    assert result["error"]["responseHeaders"]["x-request-id"] == "req_123"
