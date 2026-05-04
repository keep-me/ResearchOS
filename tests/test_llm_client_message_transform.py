from __future__ import annotations

import re
from types import SimpleNamespace

import packages.integrations.llm_client as llm_client_module
from packages.integrations.llm_client import LLMClient, LLMConfig, ResolvedModelTarget


def _target(*, provider: str, model: str, base_url: str = "") -> ResolvedModelTarget:
    return ResolvedModelTarget(
        provider=provider,
        api_key=None,
        base_url=base_url,
        model=model,
        variant=None,
        stage="rag",
    )


def test_build_openai_chat_messages_filters_empty_anthropic_messages() -> None:
    messages = [
        {"role": "system", "content": ""},
        {"role": "assistant", "content": "", "reasoning_content": ""},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call:anthropic 1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "continue"},
    ]

    result = LLMClient._build_openai_chat_messages(
        messages,
        resolved=_target(provider="anthropic", model="claude-sonnet-4-5"),
        include_reasoning_content=True,
    )

    assert [item["role"] for item in result] == ["assistant", "user"]
    assert result[0]["tool_calls"][0]["id"] == "call_anthropic_1"


def test_build_openai_chat_messages_normalizes_claude_tool_call_ids() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call:bad id",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call:bad id",
            "name": "read",
            "content": '{"ok":true}',
        },
    ]

    result = LLMClient._build_openai_chat_messages(
        messages,
        resolved=_target(provider="anthropic", model="claude-3-7-sonnet"),
        include_reasoning_content=True,
    )

    assert result[0]["tool_calls"][0]["id"] == "call_bad_id"
    assert result[1]["tool_call_id"] == "call_bad_id"


def test_build_openai_chat_messages_normalizes_mistral_tool_ids_and_sequence() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tool-call-1234",
                    "type": "function",
                    "function": {"name": "grep", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tool-call-1234",
            "name": "grep",
            "content": '{"ok":true}',
        },
        {"role": "user", "content": "continue"},
    ]

    result = LLMClient._build_openai_chat_messages(
        messages,
        resolved=_target(provider="openai", model="mistral-large"),
        include_reasoning_content=False,
    )

    assert [item["role"] for item in result] == ["assistant", "tool", "assistant", "user"]
    assert result[2]["content"] == "Done."

    assistant_tool_id = result[0]["tool_calls"][0]["id"]
    tool_result_id = result[1]["tool_call_id"]
    assert assistant_tool_id == tool_result_id
    assert len(assistant_tool_id) == 9
    assert re.fullmatch(r"[A-Za-z0-9]{9}", assistant_tool_id)


def test_build_openai_chat_messages_preserves_structured_user_text_and_image_parts() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图并总结"},
                {
                    "type": "file",
                    "url": "https://example.com/cat.png",
                    "filename": "cat.png",
                    "mime": "image/png",
                },
                {
                    "type": "file",
                    "url": "data:text/plain;base64,5Lit5paH",
                    "filename": "note.txt",
                    "mime": "text/plain",
                },
                {
                    "type": "file",
                    "url": "https://example.com/doc.pdf",
                    "filename": "doc.pdf",
                    "mime": "application/pdf",
                },
            ],
        }
    ]

    result = LLMClient._build_openai_chat_messages(
        messages,
        resolved=_target(provider="openai", model="gpt-4o"),
        include_reasoning_content=False,
    )

    assert result == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图并总结"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/cat.png"},
                },
                {"type": "text", "text": "中文"},
                {"type": "text", "text": "[Attached application/pdf: doc.pdf]"},
            ],
        }
    ]


def test_build_responses_input_replays_openai_reasoning_metadata() -> None:
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "第一步结果",
            "reasoning_content": "先做第一步分析。\n再做第二步分析。",
            "reasoning_parts": [
                {
                    "id": "rs_123:0",
                    "text": "先做第一步分析。",
                    "metadata": {
                        "openai": {
                            "itemId": "rs_123",
                            "reasoningEncryptedContent": "enc-123",
                        }
                    },
                },
                {
                    "id": "rs_123:1",
                    "text": "再做第二步分析。",
                    "metadata": {
                        "openai": {
                            "itemId": "rs_123",
                            "reasoningEncryptedContent": "enc-123",
                        }
                    },
                },
            ],
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages)

    assert result == [
        {"role": "user", "content": "继续"},
        {
            "type": "reasoning",
            "id": "rs_123",
            "encrypted_content": "enc-123",
            "summary": [
                {"type": "summary_text", "text": "先做第一步分析。"},
                {"type": "summary_text", "text": "再做第二步分析。"},
            ],
        },
        {"role": "assistant", "content": [{"type": "output_text", "text": "第一步结果"}]},
    ]


def test_build_responses_input_preserves_empty_reasoning_metadata() -> None:
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "继续执行",
            "reasoning_parts": [
                {
                    "id": "rs_empty:0",
                    "text": "",
                    "metadata": {
                        "openai": {
                            "itemId": "rs_empty",
                            "reasoningEncryptedContent": "enc-empty",
                        }
                    },
                }
            ],
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages)

    assert result == [
        {"role": "user", "content": "继续"},
        {
            "type": "reasoning",
            "id": "rs_empty",
            "encrypted_content": "enc-empty",
            "summary": [],
        },
        {"role": "assistant", "content": [{"type": "output_text", "text": "继续执行"}]},
    ]


def test_build_responses_input_supports_structured_user_text_and_file_parts() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请看附件"},
                {
                    "type": "file",
                    "url": "data:text/plain;base64,5Lit5paH",
                    "filename": "note.txt",
                    "mime": "text/plain",
                },
                {
                    "type": "file",
                    "url": "https://example.com/cat.png",
                    "filename": "cat.png",
                    "mime": "image/png",
                },
                {
                    "type": "file",
                    "url": "https://example.com/doc.pdf",
                    "filename": "doc.pdf",
                    "mime": "application/pdf",
                },
            ],
        }
    ]

    result = LLMClient._build_responses_input_from_messages(messages)

    assert result == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "请看附件"},
                {"type": "input_text", "text": "中文"},
                {"type": "input_image", "image_url": "https://example.com/cat.png"},
                {"type": "input_file", "file_url": "https://example.com/doc.pdf"},
            ],
        }
    ]


def test_build_responses_input_replays_openai_assistant_text_item_ids() -> None:
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "第一段第二段",
            "text_parts": [
                {
                    "id": "msg_1",
                    "text": "第一段",
                    "metadata": {"openai": {"itemId": "msg_1"}},
                },
                {
                    "id": "msg_2",
                    "text": "第二段",
                    "metadata": {"openai": {"itemId": "msg_2"}},
                },
            ],
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages)

    assert result == [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "第一段"}],
            "id": "msg_1",
        },
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "第二段"}],
            "id": "msg_2",
        },
    ]


def test_build_responses_input_replays_openai_tool_call_item_ids() -> None:
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_meta_1",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": '{"command":"ls"}',
                    },
                    "metadata": {"openai": {"itemId": "fc_item_1"}},
                }
            ],
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages)

    assert result == [
        {"role": "user", "content": "继续"},
        {
            "type": "function_call",
            "call_id": "call_meta_1",
            "name": "bash",
            "arguments": '{"command":"ls"}',
            "id": "fc_item_1",
        },
    ]


def test_build_responses_input_skips_provider_executed_tool_history_when_store_false() -> None:
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "ws_call_1",
                    "type": "function",
                    "provider_executed": True,
                    "function": {
                        "name": "web_search",
                        "arguments": '{"action":{"type":"search","query":"OpenAI"}}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "ws_call_1",
            "name": "web_search",
            "provider_executed": True,
            "content": '{"success": true, "summary": "web_search completed", "data": {"status": "completed"}}',
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages, store=False)

    assert result == [{"role": "user", "content": "继续"}]


def test_build_responses_input_replays_provider_executed_tool_result_as_item_reference_when_store_true() -> (
    None
):
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "ws_call_1",
                    "type": "function",
                    "provider_executed": True,
                    "function": {
                        "name": "web_search",
                        "arguments": '{"action":{"type":"search","query":"OpenAI"}}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "ws_call_1",
            "name": "web_search",
            "provider_executed": True,
            "content": '{"success": true, "summary": "web_search completed", "data": {"status": "completed"}}',
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages, store=True)

    assert result == [
        {"role": "user", "content": "继续"},
        {"type": "item_reference", "id": "ws_call_1"},
    ]


def test_build_responses_input_replays_reasoning_as_item_reference_when_store_true() -> None:
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "第一步结果",
            "reasoning_parts": [
                {
                    "id": "rs_123:0",
                    "text": "先做第一步分析。",
                    "metadata": {
                        "openai": {
                            "itemId": "rs_123",
                            "reasoningEncryptedContent": "enc-123",
                        }
                    },
                }
            ],
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages, store=True)

    assert result == [
        {"role": "user", "content": "继续"},
        {"type": "item_reference", "id": "rs_123"},
        {"role": "assistant", "content": [{"type": "output_text", "text": "第一步结果"}]},
    ]


def test_build_responses_input_replays_local_shell_call_and_output() -> None:
    messages = [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "ls_call_1",
                    "type": "function",
                    "function": {
                        "name": "local_shell",
                        "arguments": '{"action":{"type":"exec","command":["pwd"],"workingDirectory":"/tmp"}}',
                    },
                    "metadata": {"openai": {"itemId": "ls_item_1"}},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "ls_call_1",
            "name": "local_shell",
            "content": '{"success":true,"summary":"ok","data":{"output":"/tmp\\n"}}',
        },
    ]

    result = LLMClient._build_responses_input_from_messages(messages, store=False)

    assert result == [
        {"role": "user", "content": "继续"},
        {
            "type": "local_shell_call",
            "call_id": "ls_call_1",
            "id": "ls_item_1",
            "action": {
                "type": "exec",
                "command": ["pwd"],
                "working_directory": "/tmp",
            },
        },
        {
            "type": "local_shell_call_output",
            "call_id": "ls_call_1",
            "output": "/tmp\n",
        },
    ]


def test_normalize_responses_tools_preserves_openai_provider_defined_builtin_tools() -> None:
    tools = [
        {
            "type": "provider-defined",
            "id": "openai.web_search",
            "args": {
                "filters": {"allowedDomains": ["openai.com"]},
                "searchContextSize": "high",
                "userLocation": {"type": "approximate", "country": "US"},
            },
        },
        {
            "type": "provider-defined",
            "id": "openai.code_interpreter",
            "args": {
                "container": {"fileIds": ["file-1"]},
            },
        },
        {
            "type": "provider-defined",
            "id": "openai.local_shell",
            "args": {},
        },
    ]

    result = LLMClient._normalize_responses_tools(tools)

    assert result == [
        {
            "type": "web_search",
            "filters": {"allowed_domains": ["openai.com"]},
            "search_context_size": "high",
            "user_location": {"type": "approximate", "country": "US"},
        },
        {
            "type": "code_interpreter",
            "container": {"type": "auto", "file_ids": ["file-1"]},
        },
        {
            "type": "local_shell",
        },
    ]


def test_normalize_openai_chat_tools_drops_provider_defined_builtin_tools() -> None:
    tools = [
        {
            "type": "provider-defined",
            "id": "openai.web_search",
            "args": {},
        },
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "run shell",
                "parameters": {},
            },
        },
    ]

    result = LLMClient._normalize_openai_chat_tools(tools)

    assert result == [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "run shell",
                "parameters": {},
            },
        }
    ]


def test_chat_stream_openai_responses_emits_reasoning_metadata(monkeypatch) -> None:  # noqa: ANN001
    class _FakeResponses:
        @staticmethod
        def create(**kwargs):  # noqa: ANN003, ANN201
            assert kwargs["input"] == [{"role": "user", "content": "继续"}]
            return {
                "output": [
                    {
                        "type": "reasoning",
                        "id": "rs_meta",
                        "encrypted_content": "enc-meta",
                        "summary": [],
                    },
                    {
                        "type": "message",
                        "id": "msg_meta",
                        "content": [{"type": "output_text", "text": "最终回答"}],
                    },
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5.2",
        model_deep="gpt-5.2",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="gpt-5.2",
    )

    events = list(
        client._chat_stream_openai_responses(
            [{"role": "user", "content": "继续"}],
            None,
            256,
            cfg,
            target=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
    )

    assert events[0].type == "reasoning_delta"
    assert events[0].content == ""
    assert events[0].part_id == "rs_meta:0"
    assert events[0].metadata == {
        "openai": {
            "itemId": "rs_meta",
            "reasoningEncryptedContent": "enc-meta",
        }
    }
    assert events[1].type == "text_delta"
    assert events[1].content == "最终回答"
    assert events[1].part_id == "msg_meta"
    assert events[1].metadata == {"openai": {"itemId": "msg_meta"}}


def test_chat_stream_openai_responses_emits_tool_call_metadata(monkeypatch) -> None:  # noqa: ANN001
    class _FakeResponses:
        @staticmethod
        def create(**kwargs):  # noqa: ANN003, ANN201
            assert kwargs["input"] == [{"role": "user", "content": "继续"}]
            return {
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_meta",
                        "call_id": "call_fc_meta",
                        "name": "bash",
                        "arguments": '{"command":"ls"}',
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 0},
            }

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5.2",
        model_deep="gpt-5.2",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="gpt-5.2",
    )

    events = list(
        client._chat_stream_openai_responses(
            [{"role": "user", "content": "继续"}],
            None,
            256,
            cfg,
            target=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
    )

    assert events[0].type == "tool_call"
    assert events[0].tool_call_id == "call_fc_meta"
    assert events[0].tool_name == "bash"
    assert events[0].tool_arguments == '{"command":"ls"}'
    assert events[0].metadata == {"openai": {"itemId": "fc_meta"}}


def test_chat_stream_openai_responses_emits_provider_executed_builtin_tool_events(
    monkeypatch,
) -> None:  # noqa: ANN001
    class _FakeResponses:
        @staticmethod
        def create(**kwargs):  # noqa: ANN003, ANN201
            assert kwargs["input"] == [{"role": "user", "content": "继续"}]
            return {
                "output": [
                    {
                        "type": "web_search_call",
                        "id": "ws_1",
                        "status": "completed",
                        "action": {"type": "search", "query": "OpenAI"},
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 0},
            }

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5.2",
        model_deep="gpt-5.2",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="gpt-5.2",
    )

    events = list(
        client._chat_stream_openai_responses(
            [{"role": "user", "content": "继续"}],
            None,
            256,
            cfg,
            target=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
    )

    assert events[0].type == "tool_call"
    assert events[0].tool_call_id == "ws_1"
    assert events[0].tool_name == "web_search"
    assert events[0].provider_executed is True
    assert events[1].type == "tool_result"
    assert events[1].tool_call_id == "ws_1"
    assert events[1].tool_name == "web_search"
    assert events[1].provider_executed is True
    assert events[1].tool_success is True
    assert events[1].tool_result == {"status": "completed"}


def test_chat_stream_openai_responses_adds_builtin_include_fields(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    class _FakeResponses:
        @staticmethod
        def create(**kwargs):  # noqa: ANN003, ANN201
            captured.update(kwargs)
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "最终回答"}],
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5.2",
        model_deep="gpt-5.2",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="gpt-5.2",
    )

    list(
        client._chat_stream_openai_responses(
            [{"role": "user", "content": "继续"}],
            [
                {"type": "provider-defined", "id": "openai.web_search", "args": {}},
                {"type": "provider-defined", "id": "openai.code_interpreter", "args": {}},
            ],
            256,
            cfg,
            target=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
    )

    assert "web_search_call.action.sources" in captured["include"]
    assert "code_interpreter_call.outputs" in captured["include"]


def test_chat_stream_openai_compatible_strips_provider_defined_tools(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    class _FakeChatCompletions:
        @staticmethod
        def create(**kwargs):  # noqa: ANN003, ANN201
            captured.update(kwargs)
            return []

    class _FakeClient:
        chat = type("Chat", (), {"completions": _FakeChatCompletions()})()

    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5.2",
        model_deep="gpt-5.2",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="gpt-5.2",
    )

    events = list(
        client._chat_stream_openai_compatible(
            [{"role": "user", "content": "继续"}],
            [
                {"type": "provider-defined", "id": "openai.web_search", "args": {}},
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "description": "run shell",
                        "parameters": {},
                    },
                },
            ],
            256,
            cfg,
            target=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
    )

    assert captured["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "run shell",
                "parameters": {},
            },
        }
    ]
    assert events[-1].type == "done"


def test_chat_stream_openai_responses_preserves_output_annotations_metadata(monkeypatch) -> None:  # noqa: ANN001
    class _FakeResponses:
        @staticmethod
        def create(**kwargs):  # noqa: ANN003, ANN201
            assert kwargs["input"] == [{"role": "user", "content": "继续"}]
            return {
                "output": [
                    {
                        "type": "message",
                        "id": "msg_annotated",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "这里有引用",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://openai.com",
                                        "title": "OpenAI",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5.2",
        model_deep="gpt-5.2",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="gpt-5.2",
    )

    events = list(
        client._chat_stream_openai_responses(
            [{"role": "user", "content": "继续"}],
            None,
            256,
            cfg,
            target=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
    )

    assert events[0].type == "text_delta"
    assert events[0].metadata == {
        "openai": {
            "itemId": "msg_annotated",
            "annotations": [
                {
                    "type": "url_citation",
                    "url": "https://openai.com",
                    "title": "OpenAI",
                }
            ],
        }
    }


def test_chat_stream_openai_responses_reuses_previous_response_id_when_store_true(
    monkeypatch,
) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    class _FakeResponses:
        @staticmethod
        def create(**kwargs):  # noqa: ANN003, ANN201
            captured.update(kwargs)
            return {
                "id": "resp_current",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "最终回答"}],
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(
        llm_client_module, "_get_openai_client", lambda *args, **kwargs: _FakeClient()
    )

    original_apply = LLMClient._apply_variant_to_responses_kwargs

    def _force_store(self, kwargs, resolved, session_cache_key=None):  # noqa: ANN001, ANN202
        original_apply(self, kwargs, resolved, session_cache_key=session_cache_key)
        kwargs["store"] = True

    monkeypatch.setattr(LLMClient, "_apply_variant_to_responses_kwargs", _force_store)

    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5.2",
        model_deep="gpt-5.2",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="gpt-5.2",
    )

    events = list(
        client._chat_stream_openai_responses(
            [
                {"role": "user", "content": "继续"},
                {
                    "role": "assistant",
                    "content": "上轮回答",
                    "provider_metadata": {"openai": {"responseId": "resp_previous"}},
                },
            ],
            None,
            256,
            cfg,
            target=_target(
                provider="openai",
                model="gpt-5.2",
                base_url="https://api.openai.com/v1",
            ),
        )
    )

    assert captured["store"] is True
    assert captured["previous_response_id"] == "resp_previous"
    assert events[-2].type == "usage"
    assert events[-2].metadata == {"openai": {"responseId": "resp_current"}}


def test_extract_chat_reasoning_text_deduplicates_model_dump_mirror() -> None:
    payload = SimpleNamespace(
        reasoning_content="用户",
        model_dump=lambda: {"reasoning_content": "用户"},
    )

    assert LLMClient._extract_chat_reasoning_text(payload) == "用户"


def test_chat_stream_uses_openai_compatible_for_non_official_openai_targets(monkeypatch) -> None:  # noqa: ANN001
    client = LLMClient()
    cfg = LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_skim="qwen-plus",
        model_deep="qwen-plus",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="qwen-plus",
    )

    monkeypatch.setattr(LLMClient, "_config", lambda self: cfg)
    monkeypatch.setattr(
        LLMClient,
        "_resolve_model_target",
        lambda self, stage, model_override=None, variant_override=None, cfg=None: (
            ResolvedModelTarget(
                provider="openai",
                api_key="test-key",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                variant=None,
                stage="rag",
            )
        ),
    )

    called = {"responses": 0, "compatible": 0}

    def _fake_responses(
        self, messages, tools, max_tokens, cfg, target=None, session_cache_key=None
    ):  # noqa: ANN001, ANN202
        del messages, tools, max_tokens, cfg, target, session_cache_key
        called["responses"] += 1
        yield llm_client_module.StreamEvent(type="done")

    def _fake_compatible(
        self, messages, tools, max_tokens, cfg, target=None, session_cache_key=None
    ):  # noqa: ANN001, ANN202
        del messages, tools, max_tokens, cfg, target, session_cache_key
        called["compatible"] += 1
        yield llm_client_module.StreamEvent(type="done")

    monkeypatch.setattr(LLMClient, "_chat_stream_openai_responses", _fake_responses)
    monkeypatch.setattr(LLMClient, "_chat_stream_openai_compatible", _fake_compatible)

    events = list(
        client.chat_stream(
            [{"role": "user", "content": "继续"}],
            max_tokens=64,
        )
    )

    assert called == {"responses": 0, "compatible": 1}
    assert events[-1].type == "done"
