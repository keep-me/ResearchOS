from types import SimpleNamespace

import packages.integrations.llm_client as llm_client_module
from packages.integrations.llm_client import LLMClient, LLMConfig, LLMResult


def _config(provider: str = "openai", base_url: str = "https://api.openai.com/v1") -> LLMConfig:
    return LLMConfig(
        provider=provider,
        api_key="test-key",
        api_base_url=base_url,
        model_skim="gpt-5-mini",
        model_deep="gpt-5.2",
        model_vision="gpt-4o",
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="text-embedding-3-small",
        model_fallback="gpt-4o-mini",
    )


def test_vision_analyze_openai_responses_success(monkeypatch) -> None:
    class _FakeResponses:
        @staticmethod
        def create(**kwargs):
            assert kwargs["input"][0]["content"][0]["text"] == "看图"
            return {
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "vision ok"}]}
                ],
                "usage": {"input_tokens": 6, "output_tokens": 4},
            }

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(llm_client_module, "_load_active_config", lambda: _config())
    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    result = client.vision_analyze("ZmFrZQ==", "看图")

    assert result.content == "vision ok"
    assert result.input_tokens == 6
    assert result.output_tokens == 4


def test_vision_analyze_openai_falls_back_to_openai_compatible(monkeypatch) -> None:
    class _BrokenResponses:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("responses unavailable")

    class _FakeChatCompletions:
        @staticmethod
        def create(**kwargs):
            message = SimpleNamespace(content="fallback vision", reasoning_content="")
            usage = SimpleNamespace(prompt_tokens=7, completion_tokens=5)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    class _FakeClient:
        responses = _BrokenResponses()
        chat = SimpleNamespace(completions=_FakeChatCompletions())

    monkeypatch.setattr(llm_client_module, "_load_active_config", lambda: _config())
    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    result = client.vision_analyze("ZmFrZQ==", "看图")

    assert result.content == "fallback vision"
    assert result.input_tokens == 7
    assert result.output_tokens == 5


def test_vision_analyze_openai_empty_responses_falls_back_to_openai_compatible(monkeypatch) -> None:
    class _EmptyResponses:
        @staticmethod
        def create(**kwargs):
            assert kwargs["input"][0]["content"][0]["text"] == "看图"
            return {
                "output": [{"type": "message", "content": []}],
                "usage": {"input_tokens": 6, "output_tokens": 4},
            }

    class _FakeChatCompletions:
        @staticmethod
        def create(**kwargs):
            message = SimpleNamespace(content="fallback from empty", reasoning_content="")
            usage = SimpleNamespace(prompt_tokens=8, completion_tokens=3)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    class _FakeClient:
        responses = _EmptyResponses()
        chat = SimpleNamespace(completions=_FakeChatCompletions())

    monkeypatch.setattr(llm_client_module, "_load_active_config", lambda: _config())
    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    result = client.vision_analyze("ZmFrZQ==", "看图")

    assert result.content == "fallback from empty"
    assert result.input_tokens == 8
    assert result.output_tokens == 3


def test_vision_analyze_custom_empty_fallbacks_use_raw_http(monkeypatch) -> None:
    class _EmptyResponses:
        @staticmethod
        def create(**kwargs):
            return {
                "output": [{"type": "message", "content": []}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }

    class _EmptyChatCompletions:
        @staticmethod
        def create(**kwargs):
            message = SimpleNamespace(content="", reasoning_content="")
            usage = SimpleNamespace(prompt_tokens=7, completion_tokens=1)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    class _FakeClient:
        responses = _EmptyResponses()
        chat = SimpleNamespace(completions=_EmptyChatCompletions())

    cfg = _config(provider="custom", base_url="https://compat.example/v1")
    cfg.model_vision = "gpt-5.4"

    monkeypatch.setattr(llm_client_module, "_load_active_config", lambda: cfg)
    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )
    monkeypatch.setattr(
        LLMClient,
        "_call_openai_chat_raw_http",
        lambda self, **kwargs: (
            LLMResult(content="raw vision", input_tokens=11, output_tokens=6),
            [],
        ),
    )

    client = LLMClient()
    result = client.vision_analyze("ZmFrZQ==", "看图")

    assert result.content == "raw vision"
    assert result.input_tokens == 11
    assert result.output_tokens == 6


def test_vision_analyze_zhipu_uses_openai_compatible_path(monkeypatch) -> None:
    class _FakeChatCompletions:
        @staticmethod
        def create(**kwargs):
            message = SimpleNamespace(content="", reasoning_content="zhipu vision")
            usage = SimpleNamespace(prompt_tokens=9, completion_tokens=6)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    class _FakeClient:
        chat = SimpleNamespace(completions=_FakeChatCompletions())

    cfg = _config(provider="zhipu", base_url="https://open.bigmodel.cn/api/paas/v4/")
    cfg.model_skim = "glm-4v"
    cfg.model_deep = "glm-4v"
    cfg.model_vision = "glm-4v"

    monkeypatch.setattr(llm_client_module, "_load_active_config", lambda: cfg)
    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    result = client.vision_analyze("ZmFrZQ==", "看图")

    assert result.content == "zhipu vision"
    assert result.reasoning_content == "zhipu vision"


def test_vision_analyze_empty_across_all_fallbacks_returns_diagnostic_message(monkeypatch) -> None:
    class _EmptyResponses:
        @staticmethod
        def create(**kwargs):
            return {
                "output": [{"type": "message", "content": []}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }

    class _EmptyChatCompletions:
        @staticmethod
        def create(**kwargs):
            message = SimpleNamespace(content="", reasoning_content="")
            usage = SimpleNamespace(prompt_tokens=7, completion_tokens=1)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    class _FakeClient:
        responses = _EmptyResponses()
        chat = SimpleNamespace(completions=_EmptyChatCompletions())

    cfg = _config(provider="custom", base_url="https://gmncode.com/v1")
    cfg.model_vision = "gpt-5.4"

    monkeypatch.setattr(llm_client_module, "_load_active_config", lambda: cfg)
    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )
    monkeypatch.setattr(
        LLMClient,
        "_call_openai_chat_raw_http",
        lambda self, **kwargs: (LLMResult(content="", input_tokens=12, output_tokens=1), []),
    )

    client = LLMClient()
    result = client.vision_analyze("ZmFrZQ==", "看图")

    assert result.content
    assert "当前视觉模型不可用" in result.content
    assert "gpt-5.4" in result.content
    assert "gmncode.com" in result.content
    assert "空内容" in result.content
