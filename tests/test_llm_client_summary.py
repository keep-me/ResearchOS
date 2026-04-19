from types import SimpleNamespace

from packages.integrations.llm_client import LLMClient, LLMConfig, LLMResult
from packages.integrations.llm_provider_schema import ResolvedModelTarget
from packages.integrations import llm_provider_summary


def _config() -> LLMConfig:
    return LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5-mini",
        model_deep="gpt-5.2",
        model_vision="gpt-4o",
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="text-embedding-3-small",
        model_fallback="gpt-4o-mini",
    )


def _target(
    *,
    provider: str = "openai",
    api_key: str | None = "test-key",
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-5.2",
) -> ResolvedModelTarget:
    return ResolvedModelTarget(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        variant=None,
        stage="rag",
    )


def test_provider_summary_openai_responses_success(monkeypatch) -> None:
    client = LLMClient()
    captured: dict[str, object] = {}

    class _FakeResponses:
        def create(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return {"id": "resp_1"}

    monkeypatch.setattr(
        llm_provider_summary.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: SimpleNamespace(responses=_FakeResponses()),
    )
    monkeypatch.setattr(client, "_apply_variant_to_responses_kwargs", lambda kwargs, _resolved: kwargs.setdefault("variant_applied", True))
    monkeypatch.setattr(client, "_extract_responses_text_and_reasoning", lambda _response: ("summary text", "reasoning text"))
    monkeypatch.setattr(client, "_extract_responses_usage", lambda _response: (11, 7))
    monkeypatch.setattr(client, "_estimate_cost", lambda **_kwargs: (0.1, 0.2))

    result = llm_provider_summary.call_openai_responses(
        client,
        LLMResult,
        prompt="hello",
        stage="rag",
        cfg=_config(),
        target=_target(),
        max_tokens=321,
        request_timeout=9.0,
    )

    assert result.content == "summary text"
    assert result.reasoning_content == "reasoning text"
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert captured["kwargs"] == {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        "store": False,
        "max_output_tokens": 321,
        "variant_applied": True,
    }


def test_provider_summary_openai_responses_falls_back_to_chat_compatible(monkeypatch) -> None:
    client = LLMClient()
    fallback = LLMResult(content="chat-fallback")

    class _BrokenResponses:
        def create(self, **_kwargs):
            raise RuntimeError("responses unavailable")

    monkeypatch.setattr(
        llm_provider_summary.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: SimpleNamespace(responses=_BrokenResponses()),
    )
    monkeypatch.setattr(client, "_apply_variant_to_responses_kwargs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_should_try_raw_openai_http_fallback", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(client, "_call_openai_compatible", lambda *_args, **_kwargs: fallback)

    result = llm_provider_summary.call_openai_responses(
        client,
        LLMResult,
        prompt="hello",
        stage="rag",
        cfg=_config(),
        target=_target(),
    )

    assert result is fallback


def test_provider_summary_openai_compatible_prefers_raw_http_fallback(monkeypatch) -> None:
    client = LLMClient()
    raw_result = LLMResult(content="raw-http-result")

    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("sdk blocked")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_BrokenChatCompletions()),
    )
    monkeypatch.setattr(
        llm_provider_summary.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: fake_client,
    )
    monkeypatch.setattr(client, "_apply_variant_to_chat_kwargs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_should_try_raw_openai_http_fallback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(client, "_call_openai_chat_raw_http", lambda **_kwargs: (raw_result, []))
    monkeypatch.setattr(client, "_pseudo_summary", lambda *_args, **_kwargs: LLMResult(content="pseudo"))

    result = llm_provider_summary.call_openai_compatible(
        client,
        LLMResult,
        prompt="hello",
        stage="rag",
        cfg=_config(),
        target=_target(provider="zhipu", base_url="https://open.bigmodel.cn/api/paas/v4/", model="glm-4.7"),
    )

    assert result is raw_result


def test_provider_summary_openai_compatible_falls_back_to_responses_when_legacy_chat_rejected(monkeypatch) -> None:
    client = LLMClient()
    captured: dict[str, object] = {}

    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise RuntimeError(
                "Unsupported legacy protocol: /v1/chat/completions is not supported. Please use /v1/responses."
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_BrokenChatCompletions()))
    monkeypatch.setattr(
        llm_provider_summary.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: fake_client,
    )
    monkeypatch.setattr(client, "_apply_variant_to_chat_kwargs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_should_try_openai_responses_fallback", lambda *_args, **_kwargs: True)

    def _fallback(*_args, **kwargs):
        captured.update(kwargs)
        return LLMResult(content="responses-result")

    monkeypatch.setattr(client, "_call_openai_responses", _fallback)

    result = llm_provider_summary.call_openai_compatible(
        client,
        LLMResult,
        prompt="hello",
        stage="rag",
        cfg=_config(),
        target=_target(provider="custom", base_url="https://compat.example/v1", model="qwen-plus"),
    )

    assert result.content == "responses-result"
    assert captured["allow_compatible_fallback"] is False


def test_provider_summary_anthropic_falls_back_to_pseudo(monkeypatch) -> None:
    client = LLMClient()
    pseudo = LLMResult(content="pseudo-fallback")

    class _BrokenMessages:
        def create(self, **_kwargs):
            raise RuntimeError("anthropic unavailable")

    monkeypatch.setattr(
        llm_provider_summary.llm_provider_registry,
        "get_anthropic_client",
        lambda *_args, **_kwargs: SimpleNamespace(messages=_BrokenMessages()),
    )
    monkeypatch.setattr(client, "_pseudo_summary", lambda *_args, **_kwargs: pseudo)

    result = llm_provider_summary.call_anthropic(
        client,
        LLMResult,
        prompt="hello",
        stage="rag",
        cfg=_config(),
        target=_target(provider="anthropic", base_url="https://api.anthropic.com", model="claude-sonnet-4-5"),
    )

    assert result is pseudo


def test_provider_summary_openai_compatible_uses_stream_fallback_when_message_empty(monkeypatch) -> None:
    client = LLMClient()

    class _FakeChatCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None, reasoning_content=None))],
                usage=SimpleNamespace(prompt_tokens=13, completion_tokens=8),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeChatCompletions()))
    monkeypatch.setattr(
        llm_provider_summary.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: fake_client,
    )
    monkeypatch.setattr(client, "_apply_variant_to_chat_kwargs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_coerce_openai_message_text", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(client, "_extract_chat_reasoning_text", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(client, "_extract_reasoning_tokens", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_estimate_cost", lambda **_kwargs: (0.1, 0.2))
    monkeypatch.setattr(
        client,
        "_chat_stream_openai_compatible",
        lambda *_args, **_kwargs: iter(
            [
                SimpleNamespace(type="text_delta", content="hello"),
                SimpleNamespace(type="text_delta", content=" world"),
                SimpleNamespace(type="usage", input_tokens=21, output_tokens=7, reasoning_tokens=3),
                SimpleNamespace(type="done", content=""),
            ]
        ),
    )

    result = llm_provider_summary.call_openai_compatible(
        client,
        LLMResult,
        prompt="hello",
        stage="rag",
        cfg=_config(),
        target=_target(provider="custom", base_url="https://custom.example/v1", model="gpt-5.4"),
    )

    assert result.content == "hello world"
    assert result.input_tokens == 21
    assert result.output_tokens == 7
    assert result.reasoning_tokens == 3


def test_provider_summary_openai_compatible_prefers_stream_for_custom_gpt5(monkeypatch) -> None:
    client = LLMClient()

    class _ShouldNotBeCalled:
        def create(self, **_kwargs):
            raise AssertionError("chat.completions.create should not be called for preferred stream path")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_ShouldNotBeCalled()))
    monkeypatch.setattr(
        llm_provider_summary.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: fake_client,
    )
    monkeypatch.setattr(client, "_apply_variant_to_chat_kwargs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_estimate_cost", lambda **_kwargs: (0.05, 0.15))
    monkeypatch.setattr(
        client,
        "_chat_stream_openai_compatible",
        lambda *_args, **_kwargs: iter(
            [
                SimpleNamespace(type="text_delta", content="ok"),
                SimpleNamespace(type="usage", input_tokens=9, output_tokens=2, reasoning_tokens=1),
                SimpleNamespace(type="done", content=""),
            ]
        ),
    )

    result = llm_provider_summary.call_openai_compatible(
        client,
        LLMResult,
        prompt="hello",
        stage="rag",
        cfg=_config(),
        target=_target(provider="custom", base_url="https://wlxctech.cn/codex", model="gpt-5.4"),
    )

    assert result.content == "ok"
    assert result.input_tokens == 9
    assert result.output_tokens == 2
