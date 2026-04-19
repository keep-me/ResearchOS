from __future__ import annotations

from types import SimpleNamespace

from packages.agent import agent_service


class _FakeClawRuntimeManager:
    def stream_prompt(self, config, **kwargs):  # noqa: ANN001
        assert config == {"id": "claw"}
        assert kwargs["prompt"] == "hello"
        yield {
            "request_id": "req_1",
            "event": "reasoning-start",
            "id": "reasoning_block_1",
        }
        yield {
            "request_id": "req_1",
            "event": "reasoning_delta",
            "id": "reasoning_block_1",
            "content": "first think",
        }
        yield {
            "request_id": "req_1",
            "event": "tool_start",
            "id": "tool_call_1",
            "name": "search_literature",
            "args": {"query": "agent"},
        }
        yield {
            "request_id": "req_1",
            "event": "tool_result",
            "id": "tool_call_1",
            "name": "search_literature",
            "success": True,
            "summary": "done",
            "data": {"papers": []},
        }
        yield {
            "request_id": "req_1",
            "event": "done",
            "status": "ok",
            "message": "final answer",
        }


def test_stream_claw_daemon_chat_preserves_bridge_event_ids(monkeypatch):
    monkeypatch.setattr(
        agent_service,
        "get_cli_agent_service",
        lambda: SimpleNamespace(get_runtime_config=lambda backend_id: {"id": backend_id}),
    )
    monkeypatch.setattr(
        agent_service,
        "get_claw_runtime_manager",
        lambda: _FakeClawRuntimeManager(),
    )

    options = agent_service.AgentRuntimeOptions(session_id="sess-1")
    events = [
        agent_service._parse_sse_event(item)
        for item in agent_service._stream_claw_daemon_chat(
            "hello",
            options,
            backend_label="Claw",
        )
    ]

    assert ("reasoning-start", {"id": "reasoning_block_1"}) in events
    assert (
        "reasoning_delta",
        {"id": "reasoning_block_1", "content": "first think"},
    ) in events
    assert (
        "tool_start",
        {"id": "tool_call_1", "name": "search_literature", "args": {"query": "agent"}},
    ) in events
    assert (
        "tool_result",
        {
            "id": "tool_call_1",
            "name": "search_literature",
            "success": True,
            "summary": "done",
            "data": {"papers": []},
        },
    ) in events


def test_stream_claw_daemon_chat_emits_fallback_text_when_done_message_is_empty(monkeypatch):
    monkeypatch.setattr(
        agent_service,
        "get_cli_agent_service",
        lambda: SimpleNamespace(get_runtime_config=lambda backend_id: {"id": backend_id}),
    )

    class _ToolOnlyClawRuntimeManager:
        def stream_prompt(self, config, **kwargs):  # noqa: ANN001, ANN202
            assert config == {"id": "claw"}
            assert kwargs["prompt"] == "hello"
            yield {
                "request_id": "req_2",
                "event": "tool_result",
                "id": "tool_call_2",
                "name": "analyze_paper",
                "success": True,
                "summary": "论文解析完成",
                "data": {"paper_id": "paper_1"},
            }
            yield {
                "request_id": "req_2",
                "event": "done",
                "status": "ok",
                "message": "",
            }

    monkeypatch.setattr(
        agent_service,
        "get_claw_runtime_manager",
        lambda: _ToolOnlyClawRuntimeManager(),
    )

    options = agent_service.AgentRuntimeOptions(session_id="sess-2")
    events = [
        agent_service._parse_sse_event(item)
        for item in agent_service._stream_claw_daemon_chat(
            "hello",
            options,
            backend_label="Claw",
        )
    ]

    assert (
        "text_delta",
        {"content": "本轮已完成以下工具调用：\n1. analyze_paper: 论文解析完成"},
    ) in events


def test_stream_claw_daemon_chat_uses_done_tool_results_for_fallback_text(monkeypatch):
    monkeypatch.setattr(
        agent_service,
        "get_cli_agent_service",
        lambda: SimpleNamespace(get_runtime_config=lambda backend_id: {"id": backend_id}),
    )

    class _DoneOnlyToolResultClawRuntimeManager:
        def stream_prompt(self, config, **kwargs):  # noqa: ANN001, ANN202
            assert config == {"id": "claw"}
            assert kwargs["prompt"] == "hello"
            yield {
                "request_id": "req_3",
                "event": "done",
                "status": "ok",
                "message": "",
                "tool_results": [
                    {
                        "tool_use_id": "tool_call_3",
                        "tool_name": "mcp__ResearchOS__get_paper_analysis",
                        "output": '{"summary":"已读取论文图表","data":{"paper_id":"paper_siglip2"}}',
                        "is_error": False,
                    }
                ],
            }

    monkeypatch.setattr(
        agent_service,
        "get_claw_runtime_manager",
        lambda: _DoneOnlyToolResultClawRuntimeManager(),
    )

    options = agent_service.AgentRuntimeOptions(session_id="sess-3")
    events = [
        agent_service._parse_sse_event(item)
        for item in agent_service._stream_claw_daemon_chat(
            "hello",
            options,
            backend_label="Claw",
        )
    ]

    assert (
        "text_delta",
        {"content": "本轮已完成以下工具调用：\n1. mcp__ResearchOS__get_paper_analysis: 已读取论文图表"},
    ) in events


def test_stream_claw_daemon_chat_appends_fallback_when_streamed_text_is_only_tool_preamble(monkeypatch):
    monkeypatch.setattr(
        agent_service,
        "get_cli_agent_service",
        lambda: SimpleNamespace(get_runtime_config=lambda backend_id: {"id": backend_id}),
    )

    class _PreambleOnlyClawRuntimeManager:
        def stream_prompt(self, config, **kwargs):  # noqa: ANN001, ANN202
            assert config == {"id": "claw"}
            assert kwargs["prompt"] == "hello"
            yield {
                "request_id": "req_4",
                "event": "text_delta",
                "content": "我先帮你查一下相关资料。",
            }
            yield {
                "request_id": "req_4",
                "event": "tool_result",
                "id": "tool_call_4",
                "name": "search_literature",
                "success": True,
                "summary": "已找到 8 篇相关论文",
                "data": {"papers": 8},
            }
            yield {
                "request_id": "req_4",
                "event": "done",
                "status": "ok",
                "message": "",
            }

    monkeypatch.setattr(
        agent_service,
        "get_claw_runtime_manager",
        lambda: _PreambleOnlyClawRuntimeManager(),
    )

    options = agent_service.AgentRuntimeOptions(session_id="sess-4")
    events = [
        agent_service._parse_sse_event(item)
        for item in agent_service._stream_claw_daemon_chat(
            "hello",
            options,
            backend_label="Claw",
        )
    ]

    assert ("text_delta", {"content": "我先帮你查一下相关资料。"}) in events
    assert (
        "text_delta",
        {"content": "\n\n本轮已完成以下工具调用：\n1. search_literature: 已找到 8 篇相关论文"},
    ) in events
