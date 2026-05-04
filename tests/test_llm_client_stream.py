from types import SimpleNamespace

from packages.integrations import llm_provider_stream
from packages.integrations.llm_client import LLMClient, LLMConfig, LLMResult, StreamEvent
from packages.integrations.llm_provider_http import ProviderHTTPError
from packages.integrations.llm_provider_schema import ResolvedModelTarget


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


def test_provider_stream_openai_responses_falls_back_to_compatible(monkeypatch) -> None:
    client = LLMClient()

    class _BrokenResponses:
        def create(self, **_kwargs):
            raise RuntimeError("responses unavailable")

    monkeypatch.setattr(
        client,
        "_chat_stream_openai_compatible",
        lambda *args, **kwargs: iter(
            [StreamEvent(type="text_delta", content="fallback"), StreamEvent(type="done")]
        ),
    )

    events = list(
        llm_provider_stream.stream_openai_responses(
            client,
            StreamEvent,
            sdk_client=SimpleNamespace(responses=_BrokenResponses()),
            messages=[{"role": "user", "content": "继续"}],
            tools=None,
            max_tokens=256,
            cfg=_config(),
            target=_target(),
        )
    )

    assert [event.type for event in events] == ["text_delta", "done"]
    assert events[0].content == "fallback"


def test_provider_stream_openai_responses_preserves_attempt_chain_when_fallback_errors(
    monkeypatch,
) -> None:
    client = LLMClient()

    class _BrokenResponses:
        def create(self, **_kwargs):
            raise ProviderHTTPError(
                "responses unavailable",
                status_code=400,
                metadata={
                    "provider": "openai",
                    "transport": "responses",
                    "url": "https://api.openai.com/v1/responses",
                },
            )

    monkeypatch.setattr(
        client,
        "_chat_stream_openai_compatible",
        lambda *args, **kwargs: iter(
            [
                StreamEvent(
                    type="error",
                    content="chat blocked",
                    metadata={
                        "name": "APIError",
                        "isRetryable": True,
                        "statusCode": 503,
                        "providerID": "openai",
                        "transport": "chat.completions(raw-http)",
                        "transportKind": "chat.completions",
                        "bucket": "chat-runtime",
                    },
                )
            ]
        ),
    )

    events = list(
        llm_provider_stream.stream_openai_responses(
            client,
            StreamEvent,
            sdk_client=SimpleNamespace(responses=_BrokenResponses()),
            messages=[{"role": "user", "content": "继续"}],
            tools=None,
            max_tokens=256,
            cfg=_config(),
            target=_target(),
        )
    )

    assert [event.type for event in events] == ["error"]
    attempts = events[0].metadata["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["transport"] == "responses"
    assert attempts[0]["message"] == "responses unavailable"
    assert attempts[1]["transport"] == "chat.completions(raw-http)"
    assert attempts[1]["message"] == "chat blocked"


def test_provider_stream_openai_compatible_prefers_raw_http_fallback(monkeypatch) -> None:
    client = LLMClient()

    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("sdk blocked")

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=_BrokenChatCompletions()))
    monkeypatch.setattr(
        client, "_should_try_raw_openai_http_fallback", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        llm_provider_stream,
        "_yield_raw_openai_chat_stream",
        lambda *args, **kwargs: iter(
            [
                StreamEvent(type="reasoning_delta", content="raw reasoning"),
                StreamEvent(type="text_delta", content="raw text"),
                StreamEvent(
                    type="tool_call",
                    tool_call_id="call_1",
                    tool_name="bash",
                    tool_arguments='{"command":"dir"}',
                ),
                StreamEvent(type="usage", input_tokens=8, output_tokens=5, reasoning_tokens=2),
                StreamEvent(type="done"),
            ]
        ),
    )

    events = list(
        llm_provider_stream.stream_openai_compatible(
            client,
            StreamEvent,
            sdk_client=sdk_client,
            messages=[{"role": "user", "content": "继续"}],
            tools=None,
            max_tokens=256,
            cfg=_config(),
            target=_target(
                provider="zhipu", base_url="https://open.bigmodel.cn/api/paas/v4/", model="glm-4.7"
            ),
        )
    )

    assert [event.type for event in events] == [
        "reasoning_delta",
        "text_delta",
        "tool_call",
        "usage",
        "done",
    ]
    assert events[0].content == "raw reasoning"
    assert events[1].content == "raw text"
    assert events[2].tool_name == "bash"
    assert events[3].input_tokens == 8


def test_provider_stream_openai_compatible_falls_back_to_responses_when_legacy_chat_rejected(
    monkeypatch,
) -> None:
    client = LLMClient()
    captured: dict[str, object] = {}

    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise RuntimeError(
                "Unsupported legacy protocol: /v1/chat/completions is not supported. Please use /v1/responses."
            )

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=_BrokenChatCompletions()))
    monkeypatch.setattr(
        client, "_should_try_openai_responses_fallback", lambda *_args, **_kwargs: True
    )

    def _responses_stream(*args, **kwargs):
        captured.update(kwargs)
        return iter(
            [StreamEvent(type="text_delta", content="responses-ok"), StreamEvent(type="done")]
        )

    monkeypatch.setattr(client, "_chat_stream_openai_responses", _responses_stream)

    events = list(
        llm_provider_stream.stream_openai_compatible(
            client,
            StreamEvent,
            sdk_client=sdk_client,
            messages=[{"role": "user", "content": "继续"}],
            tools=None,
            max_tokens=256,
            cfg=_config(),
            target=_target(
                provider="custom", base_url="https://compat.example/v1", model="gpt-5.4"
            ),
        )
    )

    assert [event.type for event in events] == ["text_delta", "done"]
    assert events[0].content == "responses-ok"
    assert captured["allow_compatible_fallback"] is False
    assert captured["attempts"][0]["transport"] == "chat.completions"


def test_provider_stream_openai_compatible_adds_litellm_noop_tool_for_tool_history() -> None:
    client = LLMClient()
    captured: dict[str, object] = {}

    class _ChatCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return []

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=_ChatCompletions()))

    events = list(
        llm_provider_stream.stream_openai_compatible(
            client,
            StreamEvent,
            sdk_client=sdk_client,
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1", "function": {"name": "bash"}}],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "bash",
                    "content": '{"ok":true}',
                },
            ],
            tools=[],
            max_tokens=256,
            cfg=_config(),
            target=_target(base_url="https://litellm.example/v1"),
        )
    )

    assert [event.type for event in events] == ["done"]
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert tools[0]["function"]["name"] == "_noop"


def test_provider_stream_openai_compatible_repairs_tool_name_case_to_lowercase_match() -> None:
    client = LLMClient()

    chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call_1",
                            function=SimpleNamespace(name="Bash", arguments='{"command":"dir"}'),
                        )
                    ],
                )
            )
        ],
    )

    class _ChatCompletions:
        def create(self, **_kwargs):
            return [chunk]

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=_ChatCompletions()))

    events = list(
        llm_provider_stream.stream_openai_compatible(
            client,
            StreamEvent,
            sdk_client=sdk_client,
            messages=[{"role": "user", "content": "继续"}],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "bash", "description": "run bash", "parameters": {}},
                }
            ],
            max_tokens=256,
            cfg=_config(),
            target=_target(),
        )
    )

    assert [event.type for event in events] == ["tool_call", "done"]
    assert events[0].tool_name == "bash"


def test_provider_stream_openai_compatible_surfaces_structured_transport_error(monkeypatch) -> None:
    client = LLMClient()

    class _BrokenChatCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("sdk blocked")

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=_BrokenChatCompletions()))
    monkeypatch.setattr(
        client, "_should_try_raw_openai_http_fallback", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        llm_provider_stream,
        "_yield_raw_openai_chat_stream",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ProviderHTTPError(
                "rate limit exceeded",
                status_code=429,
                response_headers={"retry-after": "3"},
                response_body='{"error":{"message":"rate limit exceeded","type":"rate_limit_error","code":"rate_limit"}}',
                metadata={
                    "provider": "openai",
                    "transport": "chat.completions(raw-http)",
                    "gateway": "openai-gateway",
                    "bucket": "chat-runtime",
                    "url": "https://api.openai.com/v1/chat/completions",
                },
            )
        ),
    )

    events = list(
        llm_provider_stream.stream_openai_compatible(
            client,
            StreamEvent,
            sdk_client=sdk_client,
            messages=[{"role": "user", "content": "继续"}],
            tools=None,
            max_tokens=256,
            cfg=_config(),
            target=_target(),
        )
    )

    assert [event.type for event in events] == ["error"]
    assert events[0].content == "rate limit exceeded"
    assert events[0].metadata["statusCode"] == 429
    assert events[0].metadata["providerID"] == "openai"
    assert events[0].metadata["transport"] == "chat.completions(raw-http)"
    assert events[0].metadata["transportKind"] == "chat.completions"
    assert events[0].metadata["gateway"] == "openai-gateway"
    assert events[0].metadata["bucket"] == "chat-runtime"
    assert events[0].metadata["url"] == "https://api.openai.com/v1/chat/completions"
    assert events[0].metadata["responseHeaders"]["retry-after"] == "3"
    assert events[0].metadata["metadata"]["transport"] == "chat.completions(raw-http)"


def test_provider_stream_pseudo_replays_summary_text(monkeypatch) -> None:
    client = LLMClient()
    monkeypatch.setattr(
        client,
        "_pseudo_summary",
        lambda *args, **kwargs: LLMResult(content="pseudo text"),
    )

    events = list(
        llm_provider_stream.stream_pseudo(
            client,
            StreamEvent,
            messages=[{"role": "user", "content": "继续"}],
            cfg=_config(),
            target=_target(provider="none", api_key=None, base_url="", model=""),
        )
    )

    assert [event.type for event in events] == ["text_delta", "done"]
    assert events[0].content == "pseudo text"
