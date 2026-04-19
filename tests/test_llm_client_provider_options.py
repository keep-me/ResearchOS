from __future__ import annotations

import io
import urllib.error

from packages.integrations.llm_client import LLMClient, ResolvedModelTarget
from packages.integrations.llm_provider_http import ProviderHTTPError


def _target(
    *,
    provider: str,
    model: str,
    base_url: str = "",
    variant: str | None = None,
    stage: str = "rag",
) -> ResolvedModelTarget:
    return ResolvedModelTarget(
        provider=provider,
        api_key="test-key",
        base_url=base_url,
        model=model,
        variant=variant,
        stage=stage,
    )


def test_apply_variant_to_responses_kwargs_aligns_openai_gpt5_defaults() -> None:
    client = LLMClient()
    kwargs = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
    }

    client._apply_variant_to_responses_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="gpt-5.2",
            base_url="https://api.openai.com/v1",
        ),
        session_cache_key="session-cache-1",
    )

    assert kwargs["store"] is False
    assert kwargs["prompt_cache_key"] == "session-cache-1"
    assert kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert kwargs["include"] == ["reasoning.encrypted_content"]
    assert kwargs["text"] == {"verbosity": "low"}


def test_apply_variant_to_chat_kwargs_aligns_openai_gpt5_defaults() -> None:
    client = LLMClient()
    kwargs = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="gpt-5.2",
            base_url="https://api.openai.com/v1",
        ),
        session_cache_key="session-cache-2",
    )

    assert kwargs["store"] is False
    assert kwargs["prompt_cache_key"] == "session-cache-2"
    assert kwargs["reasoning_effort"] == "medium"
    assert kwargs["extra_body"]["reasoning_summary"] == "auto"
    assert kwargs["extra_body"]["include"] == ["reasoning.encrypted_content"]


def test_apply_variant_to_responses_kwargs_sets_openrouter_prompt_cache_key() -> None:
    client = LLMClient()
    kwargs = {
        "model": "openai/gpt-4.1-mini",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
    }

    client._apply_variant_to_responses_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="openai/gpt-4.1-mini",
            base_url="https://openrouter.ai/api/v1",
        ),
        session_cache_key="session-cache-openrouter",
    )

    assert kwargs["prompt_cache_key"] == "session-cache-openrouter"


def test_apply_variant_to_chat_kwargs_sets_openrouter_prompt_cache_key() -> None:
    client = LLMClient()
    kwargs = {
        "model": "openai/gpt-4.1-mini",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="openai/gpt-4.1-mini",
            base_url="https://openrouter.ai/api/v1",
        ),
        session_cache_key="session-cache-openrouter",
    )

    assert kwargs["prompt_cache_key"] == "session-cache-openrouter"


def test_apply_variant_to_responses_kwargs_sets_venice_prompt_cache_key() -> None:
    client = LLMClient()
    kwargs = {
        "model": "llama-3.3-70b",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
    }

    client._apply_variant_to_responses_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="llama-3.3-70b",
            base_url="https://api.venice.ai/api/v1",
        ),
        session_cache_key="session-cache-venice",
    )

    assert kwargs["promptCacheKey"] == "session-cache-venice"


def test_apply_variant_to_chat_kwargs_sets_venice_prompt_cache_key() -> None:
    client = LLMClient()
    kwargs = {
        "model": "llama-3.3-70b",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="llama-3.3-70b",
            base_url="https://api.venice.ai/api/v1",
        ),
        session_cache_key="session-cache-venice",
    )

    assert kwargs["promptCacheKey"] == "session-cache-venice"


def test_apply_variant_to_chat_kwargs_uses_google_thinking_config_for_gemini() -> None:
    client = LLMClient()
    kwargs = {
        "model": "gemini-3-flash",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="gemini-3-flash",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
    )

    assert "reasoning_effort" not in kwargs
    assert kwargs["temperature"] == 1.0
    assert kwargs["top_p"] == 0.95
    assert kwargs["extra_body"]["google"]["thinking_config"] == {
        "include_thoughts": True,
        "thinking_level": "high",
    }


def test_apply_variant_to_chat_kwargs_maps_gemini_25_high_to_budget() -> None:
    client = LLMClient()
    kwargs = {
        "model": "gemini-2.5-pro",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="gemini-2.5-pro",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            variant="high",
        ),
    )

    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"]["google"]["thinking_config"] == {
        "include_thoughts": True,
        "thinking_budget": 16_000,
    }


def test_apply_variant_to_chat_kwargs_enables_zhipu_thinking() -> None:
    client = LLMClient()
    kwargs = {
        "model": "glm-4.7",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="zhipu",
            model="glm-4.7",
            base_url="https://open.bigmodel.cn/api/paas/v4/",
        ),
    )

    assert kwargs["temperature"] == 1.0
    assert kwargs["extra_body"]["thinking"] == {
        "type": "enabled",
        "clear_thinking": False,
    }


def test_apply_variant_to_chat_kwargs_enables_dashscope_reasoning_and_qwen_sampling() -> None:
    client = LLMClient()
    kwargs = {
        "model": "qwen-plus",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    )

    assert kwargs["temperature"] == 0.55
    assert kwargs["top_p"] == 1.0
    assert kwargs["extra_body"]["enable_thinking"] is True


def test_apply_variant_to_chat_kwargs_sets_minimax_top_k() -> None:
    client = LLMClient()
    kwargs = {
        "model": "minimax-m2",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="minimax-m2",
            base_url="https://api.minimax.chat/v1",
        ),
    )

    assert kwargs["temperature"] == 1.0
    assert kwargs["top_p"] == 0.95
    assert kwargs["extra_body"]["top_k"] == 20


def test_apply_variant_to_responses_kwargs_uses_small_reasoning_for_skim_gpt5() -> None:
    client = LLMClient()
    kwargs = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
    }

    client._apply_variant_to_responses_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="gpt-5.2",
            base_url="https://api.openai.com/v1",
            stage="skim",
        ),
    )

    assert kwargs["store"] is False
    assert kwargs["reasoning"] == {"effort": "low", "summary": "auto"}
    assert kwargs["include"] == ["reasoning.encrypted_content"]


def test_apply_variant_to_chat_kwargs_uses_small_reasoning_for_skim_gpt5() -> None:
    client = LLMClient()
    kwargs = {
        "model": "gpt-5",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="gpt-5",
            base_url="https://api.openai.com/v1",
            stage="skim",
        ),
    )

    assert kwargs["store"] is False
    assert kwargs["reasoning_effort"] == "minimal"
    assert kwargs["extra_body"]["reasoning_summary"] == "auto"
    assert kwargs["extra_body"]["include"] == ["reasoning.encrypted_content"]


def test_apply_variant_to_chat_kwargs_uses_small_google_thinking_for_skim() -> None:
    client = LLMClient()
    kwargs = {
        "model": "gemini-3-flash",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="gemini-3-flash",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            stage="skim",
        ),
    )

    assert kwargs["extra_body"]["google"]["thinking_config"] == {
        "include_thoughts": True,
        "thinking_level": "minimal",
    }


def test_apply_variant_to_chat_kwargs_uses_small_openrouter_reasoning() -> None:
    client = LLMClient()
    kwargs = {
        "model": "openai/gpt-4.1-mini",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="openai/gpt-4.1-mini",
            base_url="https://openrouter.ai/api/v1",
            stage="skim",
        ),
    )

    assert kwargs["reasoning_effort"] == "minimal"


def test_apply_variant_to_chat_kwargs_uses_small_venice_disable_thinking() -> None:
    client = LLMClient()
    kwargs = {
        "model": "llama-3.3-70b",
        "messages": [{"role": "user", "content": "continue"}],
    }

    client._apply_variant_to_chat_kwargs(
        kwargs,
        _target(
            provider="openai",
            model="llama-3.3-70b",
            base_url="https://api.venice.ai/api/v1",
            stage="skim",
        ),
    )

    assert kwargs["extra_body"]["venice_parameters"] == {
        "disable_thinking": True,
    }


def test_remap_provider_options_namespace_aligns_gateway_upstream_slug() -> None:
    client = LLMClient()

    namespaced = client._remap_provider_options_namespace(
        _target(
            provider="gateway",
            model="amazon/nova-2-lite",
            stage="skim",
        ),
        {
            "gateway": {"caching": "auto"},
            "thinkingConfig": {"thinkingBudget": 0},
            "reasoningEffort": "low",
        },
    )

    assert namespaced == {
        "gateway": {"caching": "auto"},
        "bedrock": {
            "thinkingConfig": {"thinkingBudget": 0},
            "reasoningEffort": "low",
        },
    }


def test_raw_chat_http_payload_reuses_provider_option_builder() -> None:
    client = LLMClient()
    captured: dict[str, object] = {}

    def _fake_post(**kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    client._raw_openai_compatible_post = _fake_post  # type: ignore[method-assign]

    result, tool_calls = client._call_openai_chat_raw_http(
        messages=[{"role": "user", "content": "continue"}],
        resolved=_target(
            provider="openai",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        max_tokens=128,
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["temperature"] == 0.55
    assert payload["top_p"] == 1.0
    assert payload["extra_body"]["enable_thinking"] is True
    assert result.content == "ok"
    assert tool_calls == []


def test_raw_responses_http_payload_includes_prompt_cache_key() -> None:
    client = LLMClient()
    captured: dict[str, object] = {}

    def _fake_post(**kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    client._raw_openai_compatible_post = _fake_post  # type: ignore[method-assign]

    result = client._call_openai_responses_raw_http(
        prompt="continue",
        resolved=_target(
            provider="openai",
            model="gpt-5.2",
            base_url="https://api.openai.com/v1",
        ),
        max_tokens=128,
        session_cache_key="session-cache-raw",
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["prompt_cache_key"] == "session-cache-raw"
    assert payload["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert result.content == "ok"


def test_raw_http_transport_raises_structured_provider_error(monkeypatch) -> None:  # noqa: ANN001
    client = LLMClient()

    def _fail_urlopen(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise urllib.error.HTTPError(
            url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={"retry-after": "3"},
            fp=io.BytesIO(
                b'{"error":{"message":"rate limit exceeded","type":"rate_limit_error","code":"rate_limit"}}'
            ),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

    try:
        client._raw_openai_compatible_post(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="test-key",
            path="chat/completions",
            payload={"model": "qwen-plus", "messages": [{"role": "user", "content": "hi"}]},
        )
        raise AssertionError("expected ProviderHTTPError")
    except ProviderHTTPError as exc:
        assert exc.status_code == 429
        assert exc.response_headers == {"retry-after": "3"}
        assert exc.response_body
        assert "rate limit exceeded" in str(exc)


def test_raw_chat_http_error_carries_runtime_metadata(monkeypatch) -> None:  # noqa: ANN001
    client = LLMClient()

    def _fail_urlopen(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise urllib.error.HTTPError(
            url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"upstream temporarily unavailable"}}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

    try:
        client._call_openai_chat_raw_http(
            messages=[{"role": "user", "content": "hi"}],
            resolved=_target(
                provider="openai",
                model="qwen-plus",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
        )
        raise AssertionError("expected ProviderHTTPError")
    except ProviderHTTPError as exc:
        assert exc.metadata is not None
        assert exc.metadata["transport"] == "chat.completions(raw-http)"
        assert exc.metadata["bucket"] == "chat-runtime"
        assert exc.metadata["provider"] == "openai"
        assert exc.metadata["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def test_raw_responses_http_error_carries_runtime_metadata(monkeypatch) -> None:  # noqa: ANN001
    client = LLMClient()

    def _fail_urlopen(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise urllib.error.HTTPError(
            url="https://api.openai.com/v1/responses",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"server overloaded"}}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

    try:
        client._call_openai_responses_raw_http(
            prompt="hi",
            resolved=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
        raise AssertionError("expected ProviderHTTPError")
    except ProviderHTTPError as exc:
        assert exc.metadata is not None
        assert exc.metadata["transport"] == "responses(raw-http)"
        assert exc.metadata["bucket"] == "summary-runtime"
        assert exc.metadata["provider"] == "openai"
        assert exc.metadata["url"] == "https://api.openai.com/v1/responses"
