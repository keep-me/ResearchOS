from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import agent as agent_router
from apps.api.routers import session_runtime as session_runtime_router
from packages.agent import (
    agent_service,
    global_bus,
    session_bus,
    session_lifecycle,
)
from packages.agent import (
    session_runtime as session_runtime_module,
)
from packages.agent.session.session_bus import SessionBusEvent
from packages.agent.session.session_lifecycle import (
    acquire_prompt_instance,
    claim_prompt_callback,
    drain_prompt_callbacks,
    finish_prompt_instance,
    pause_prompt_instance,
    register_prompt_waiter,
    wait_for_prompt_completion,
)
from packages.agent.session.session_runtime import (
    append_session_message,
    ensure_session_record,
    load_agent_messages,
    persist_assistant_message,
    set_session_status,
    wrap_stream_with_persistence,
)
from packages.integrations.llm_client import StreamEvent
from packages.storage import db
from packages.storage.db import Base


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(session_runtime_router.router)
    app.include_router(agent_router.router)
    return app


def _reset_runtime_state() -> None:
    global_bus.reset_for_tests()
    session_bus.reset_for_tests()
    session_lifecycle.reset_for_tests()


def _wrap_persisted_if_needed(chunks: list[str], kwargs: dict) -> object:
    persistence = kwargs.get("persistence")
    if persistence is None:
        return iter(chunks)
    return wrap_stream_with_persistence(
        iter(chunks),
        session_id=persistence.session_id,
        parent_id=persistence.parent_id,
        assistant_meta=None,
        assistant_message_id=persistence.assistant_message_id,
    )


def _message_text(message: dict[str, object]) -> str:
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, dict) and str(part.get("type") or "") == "text"
    )


REMOVED_NATIVE_ROUTE = pytest.mark.skip(
    reason="部分 native 专项回归尚未恢复；仅保留当前未纳入双栈修复范围的用例。"
)


class FakeLifecycleLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del messages, tools, max_tokens, variant_override, model_override, session_cache_key
        yield StreamEvent(type="text_delta", content="生命周期测试回复。")
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6)


class FakeUnconfiguredLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = ""


class FakeCliAgentService:
    def get_config(self, agent_backend_id: str) -> dict[str, str]:
        return {"label": f"Fake CLI {agent_backend_id}"}

    def get_runtime_config(self, agent_backend_id: str) -> dict[str, str]:
        return {"id": agent_backend_id}

    def execute_prompt(self, *_args, **_kwargs) -> dict[str, object]:
        return {
            "content": "CLI 已执行完成。",
            "execution_mode": "cli",
            "duration_ms": 12,
            "workspace_server_id": None,
            "fallback_reason": None,
        }


class FakeBudgetHardStopLLM:
    calls: list[list[dict]] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del tools, max_tokens, variant_override, model_override, session_cache_key
        FakeBudgetHardStopLLM.calls.append(list(messages))
        last_user = next(
            (
                str(item.get("content") or "")
                for item in reversed(messages)
                if str(item.get("role") or "") == "user"
            ),
            "",
        )
        if last_user == agent_service.STEP_LIMIT_SUMMARY_PROMPT:
            yield StreamEvent(
                type="text_delta",
                content="预算总结：第一步检索已完成，第二个工具调用因预算上限被跳过。",
            )
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=6, output_tokens=16
            )
            return
        if any(
            str(item.get("role") or "") == "assistant"
            and "CRITICAL - MAXIMUM STEPS REACHED" in str(item.get("content") or "")
            for item in messages
        ):
            yield StreamEvent(type="text_delta", content="我继续检查一下。")
            yield StreamEvent(
                type="tool_call",
                tool_call_id="call_budget_2",
                tool_name="bash",
                tool_arguments='{"command":"echo second"}',
            )
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=11, output_tokens=4
            )
            return
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_budget_1",
            tool_name="bash",
            tool_arguments='{"command":"echo first"}',
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=0)


class FakeRepeatedToolTurnLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del tools, max_tokens, variant_override, model_override, session_cache_key
        has_first_result = any(
            str(item.get("role") or "") == "tool"
            and "第一次执行成功" in str(item.get("content") or "")
            for item in messages
        )
        if has_first_result:
            yield StreamEvent(
                type="tool_call",
                tool_call_id="call_repeat_2",
                tool_name="bash",
                tool_arguments='{"command":"echo repeat"}',
            )
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=11, output_tokens=2
            )
            return
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_repeat_1",
            tool_name="bash",
            tool_arguments='{"command":"echo repeat"}',
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=0)


class FakeQueuedPromptLLM:
    calls: list[list[dict]] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del tools, max_tokens, variant_override, model_override, session_cache_key
        FakeQueuedPromptLLM.calls.append(list(messages))
        last_user = next(
            (
                str(item.get("content") or "")
                for item in reversed(messages)
                if str(item.get("role") or "") == "user"
            ),
            "",
        )
        if last_user == "first":
            time.sleep(0.35)
            yield StreamEvent(type="text_delta", content="first-done")
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6
            )
            return

        has_first = any(
            str(item.get("role") or "") == "assistant"
            and "first-done" in str(item.get("content") or "")
            for item in messages
        )
        content = "second-saw-first" if has_first else "second-stale"
        yield StreamEvent(type="text_delta", content=content)
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6)


class FakeQueuedFifoLLM:
    calls: list[str] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del tools, max_tokens, variant_override, model_override, session_cache_key
        last_user = next(
            (
                str(item.get("content") or "")
                for item in reversed(messages)
                if str(item.get("role") or "") == "user"
            ),
            "",
        )
        FakeQueuedFifoLLM.calls.append(last_user)
        if last_user == "first":
            time.sleep(0.75)
            yield StreamEvent(type="text_delta", content="first-done")
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6
            )
            return
        if last_user == "second":
            time.sleep(0.2)
            has_first = any(
                str(item.get("role") or "") == "assistant"
                and "first-done" in str(item.get("content") or "")
                for item in messages
            )
            content = "second-saw-first" if has_first else "second-stale"
            yield StreamEvent(type="text_delta", content=content)
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6
            )
            return
        has_first = any(
            str(item.get("role") or "") == "assistant"
            and "first-done" in str(item.get("content") or "")
            for item in messages
        )
        has_second_user = any(
            str(item.get("role") or "") == "user" and str(item.get("content") or "") == "second"
            for item in messages
        )
        content = (
            "third-saw-first-and-second-user" if has_first and has_second_user else "third-stale"
        )
        yield StreamEvent(type="text_delta", content=content)
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6)


class FakeQueuedErrorLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del tools, max_tokens, variant_override, model_override, session_cache_key
        last_user = next(
            (
                str(item.get("content") or "")
                for item in reversed(messages)
                if str(item.get("role") or "") == "user"
            ),
            "",
        )
        if last_user == "first":
            time.sleep(0.35)
            raise RuntimeError("first failed")
        yield StreamEvent(type="text_delta", content="should-not-run")
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6)


class FakeProviderExecutedBuiltinLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-5.2")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del messages, tools, max_tokens, variant_override, model_override, session_cache_key
        yield StreamEvent(
            type="tool_call",
            tool_call_id="ws_builtin_1",
            tool_name="web_search",
            tool_arguments='{"action":{"type":"search","query":"OpenAI"}}',
            provider_executed=True,
        )
        yield StreamEvent(
            type="tool_result",
            tool_call_id="ws_builtin_1",
            tool_name="web_search",
            provider_executed=True,
            tool_success=True,
            tool_summary="web_search completed",
            tool_result={"status": "completed"},
        )
        yield StreamEvent(type="text_delta", content="搜索已完成。")
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6)


class FakeProviderExecutedToolOnlyLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-5.2")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del messages, tools, max_tokens, variant_override, model_override, session_cache_key
        yield StreamEvent(
            type="tool_call",
            tool_call_id="ws_builtin_tool_only_1",
            tool_name="web_search",
            tool_arguments='{"action":{"type":"search","query":"MinerU"}}',
            provider_executed=True,
        )
        yield StreamEvent(
            type="tool_result",
            tool_call_id="ws_builtin_tool_only_1",
            tool_name="web_search",
            provider_executed=True,
            tool_success=True,
            tool_summary="已找到 MinerU 相关资料",
            tool_result={"status": "completed", "items": 3},
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=12, output_tokens=5)


class FakeProviderExecutedToolPreambleLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-5.2")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del messages, tools, max_tokens, variant_override, model_override, session_cache_key
        yield StreamEvent(type="text_delta", content="我先帮你查一下相关资料。")
        yield StreamEvent(
            type="tool_call",
            tool_call_id="ws_builtin_preamble_1",
            tool_name="web_search",
            tool_arguments='{"action":{"type":"search","query":"MinerU"}}',
            provider_executed=True,
        )
        yield StreamEvent(
            type="tool_result",
            tool_call_id="ws_builtin_preamble_1",
            tool_name="web_search",
            provider_executed=True,
            tool_success=True,
            tool_summary="已找到 MinerU 相关资料",
            tool_result={"status": "completed", "items": 3},
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=14, output_tokens=6)


def _fake_delta_stream_chat(*_args, **kwargs):
    return _wrap_persisted_if_needed(
        [
            'event: assistant_message_id\ndata: {"message_id":"message_delta_stream_1"}\n\n',
            'event: session_step_start\ndata: {"step":1}\n\n',
            'event: reasoning_delta\ndata: {"content":"先梳理"}\n\n',
            'event: reasoning_delta\ndata: {"content":"上下文。"}\n\n',
            'event: text_delta\ndata: {"content":"这是"}\n\n',
            'event: text_delta\ndata: {"content":"最终回答。"}\n\n',
            'event: usage\ndata: {"model":"fake-chat-model","input_tokens":10,"output_tokens":6,"reasoning_tokens":8}\n\n',
            (
                "event: session_step_finish\ndata: "
                '{"step":1,"reason":"stop","usage":{"input_tokens":10,"output_tokens":6,"reasoning_tokens":8},"cost":0}\n\n'
            ),
            "event: done\ndata: {}\n\n",
        ],
        kwargs,
    )


def test_session_bus_publishes_status_and_message_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    ensure_session_record(
        session_id="bus_event_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    events = []
    first_step_started = threading.Event()

    def _record_event(event) -> None:  # noqa: ANN001
        events.append(event)
        if (
            event.type == SessionBusEvent.STEP_STARTED
            and str(event.properties.get("sessionID") or "") == "queued_prompt_session"
        ):
            first_step_started.set()

    unsubscribe = session_bus.subscribe_all(_record_event)
    try:
        set_session_status("bus_event_session", {"type": "busy"})
        user = append_session_message(
            session_id="bus_event_session",
            role="user",
            content="请继续",
        )
        persist_assistant_message(
            session_id="bus_event_session",
            parent_id=str(user["info"]["id"]),
            meta={"finish": "stop"},
            parts=[
                {"type": "step-start", "step": 1},
                {"type": "text", "text": "已完成"},
                {"type": "step-finish", "step": 1, "reason": "stop"},
            ],
        )
        set_session_status("bus_event_session", {"type": "idle"})
    finally:
        unsubscribe()

    event_types = [event.type for event in events]
    assert SessionBusEvent.STATUS in event_types
    assert event_types.count(SessionBusEvent.MESSAGE_UPDATED) >= 2
    assert SessionBusEvent.PART_UPDATED in event_types
    assert SessionBusEvent.IDLE in event_types


def test_session_bus_mirrors_events_to_global_bus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    ensure_session_record(
        session_id="global_bus_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    mirrored = []
    unsubscribe = global_bus.subscribe_all(lambda event: mirrored.append(event))
    try:
        set_session_status("global_bus_session", {"type": "busy"})
        user = append_session_message(
            session_id="global_bus_session",
            role="user",
            content="请继续",
        )
        persist_assistant_message(
            session_id="global_bus_session",
            parent_id=str(user["info"]["id"]),
            meta={"finish": "stop"},
            parts=[
                {"type": "step-start", "step": 1},
                {"type": "text", "text": "已完成"},
                {"type": "step-finish", "step": 1, "reason": "stop"},
            ],
        )
        set_session_status("global_bus_session", {"type": "idle"})
    finally:
        unsubscribe()

    assert mirrored
    assert all(event.type == "event" for event in mirrored)
    assert all(event.directory == str(tmp_path) for event in mirrored)
    payload_types = [
        str((event.payload or {}).get("type") or "") if isinstance(event.payload, dict) else ""
        for event in mirrored
    ]
    assert SessionBusEvent.STATUS in payload_types
    assert SessionBusEvent.MESSAGE_UPDATED in payload_types
    assert SessionBusEvent.PART_UPDATED in payload_types
    assert SessionBusEvent.IDLE in payload_types


def test_session_bus_subscribe_filters_by_event_type() -> None:
    _reset_runtime_state()

    received = []
    unsubscribe = session_bus.subscribe(
        SessionBusEvent.STATUS, lambda event: received.append(event)
    )
    try:
        session_bus.publish(
            SessionBusEvent.STATUS,
            {"sessionID": "bus_subscribe_session", "status": {"type": "busy"}},
        )
        session_bus.publish(SessionBusEvent.MESSAGE_UPDATED, {"sessionID": "bus_subscribe_session"})
    finally:
        unsubscribe()

    assert len(received) == 1
    assert received[0].type == SessionBusEvent.STATUS
    assert received[0].properties["status"] == {"type": "busy"}


def test_stream_active_uses_unified_lifecycle_helper(monkeypatch: pytest.MonkeyPatch):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    captured: dict[str, object] = {}

    def _fake_stream_lifecycle(self, run, *, config=None):  # noqa: ANN001, ANN202
        captured["assistant_message_id"] = self.assistant_message_id
        captured["config"] = config
        captured["run"] = run
        return iter(["event: done\ndata: {}\n\n"])

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_stream_lifecycle",
        _fake_stream_lifecycle,
    )

    processor = agent_service.SessionPromptProcessor(
        messages=[{"role": "user", "content": "continue"}],
        options=agent_service.AgentRuntimeOptions(
            session_id="unified_lifecycle_stream_session",
            mode="build",
        ),
        assistant_message_id="message_unified_lifecycle",
    )

    items = list(processor._stream_active())

    assert items == ["event: done\ndata: {}\n\n"]
    assert captured["assistant_message_id"] == "message_unified_lifecycle"
    assert captured["config"] is None
    assert callable(captured["run"])


def test_run_loop_delegates_to_session_processor_prompt_loop(monkeypatch: pytest.MonkeyPatch):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    captured: dict[str, object] = {}

    def _fake_stream_prompt_loop(config):  # noqa: ANN001, ANN202
        captured["config"] = config
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(
        agent_service,
        "_processor_stream_prompt_loop",
        _fake_stream_prompt_loop,
    )

    processor = agent_service.SessionPromptProcessor(
        messages=[{"role": "user", "content": "continue"}],
        options=agent_service.AgentRuntimeOptions(
            session_id="delegated_prompt_loop_session",
            mode="build",
        ),
        assistant_message_id="message_delegated_prompt_loop",
    )

    items = list(processor._run_loop([{"role": "user", "content": "continue"}]))

    assert items == ["event: done\ndata: {}\n\n"]
    config = captured["config"]
    assert config is not None
    assert config.assistant_message_id == "message_delegated_prompt_loop"
    assert config.step_index == 0
    assert config.max_steps > 0


def test_run_loop_scales_step_budget_with_reasoning_level(monkeypatch: pytest.MonkeyPatch):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    captured: list[tuple[str, int]] = []

    def _fake_stream_prompt_loop(config):  # noqa: ANN001, ANN202
        captured.append(
            (str(getattr(config.options, "reasoning_level", "")), int(config.max_steps))
        )
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(
        agent_service,
        "_processor_stream_prompt_loop",
        _fake_stream_prompt_loop,
    )

    class _Settings:
        agent_max_tool_steps = 20

    monkeypatch.setattr(agent_service, "get_settings", lambda: _Settings())

    for level in ("low", "medium", "high"):
        processor = agent_service.SessionPromptProcessor(
            messages=[{"role": "user", "content": "continue"}],
            options=agent_service.AgentRuntimeOptions(
                session_id=f"delegated_prompt_loop_budget_{level}",
                mode="build",
                reasoning_level=level,
            ),
            assistant_message_id=f"message_delegated_prompt_loop_{level}",
        )
        list(processor._run_loop([{"role": "user", "content": "continue"}]))

    assert captured == [("low", 10), ("medium", 20), ("high", 30)]


def test_fill_workspace_defaults_scales_search_profile_with_reasoning_level():
    from packages.agent import agent_service

    low_options = agent_service.AgentRuntimeOptions(
        session_id="search_profile_low_session",
        mode="build",
        workspace_path="/tmp/workspace",
        reasoning_level="low",
    )
    high_options = agent_service.AgentRuntimeOptions(
        session_id="search_profile_high_session",
        mode="build",
        workspace_path="/tmp/workspace",
        reasoning_level="high",
    )

    low_grep = agent_service._fill_workspace_defaults(
        agent_service.ToolCall(id="call_low_grep", name="grep", arguments={}),
        low_options,
    )
    high_read = agent_service._fill_workspace_defaults(
        agent_service.ToolCall(
            id="call_high_read", name="read", arguments={"file_path": "/tmp/workspace/demo.py"}
        ),
        high_options,
    )
    low_list = agent_service._fill_workspace_defaults(
        agent_service.ToolCall(id="call_low_list", name="list", arguments={}),
        low_options,
    )
    preserved = agent_service._fill_workspace_defaults(
        agent_service.ToolCall(
            id="call_preserved",
            name="grep",
            arguments={"limit": 7},
        ),
        high_options,
    )

    assert low_grep.arguments["limit"] == 20
    assert high_read.arguments["max_chars"] == 20000
    assert low_list.arguments["max_entries"] == 80
    assert preserved.arguments["limit"] == 7


def test_run_model_turn_events_delegates_to_session_processor_runtime(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    captured: dict[str, object] = {}

    def _fake_stream_model_turn(config, callbacks):  # noqa: ANN001, ANN202
        captured["config"] = config
        captured["callbacks"] = callbacks
        yield agent_service._prompt_event(
            "usage", {"model": "fake-model", "input_tokens": 1, "output_tokens": 2}
        )
        return agent_service.ModelTurnResult(status="continue", content="delegated")

    monkeypatch.setattr(
        agent_service,
        "_processor_stream_model_turn_events",
        _fake_stream_model_turn,
    )

    items = list(
        agent_service._run_model_turn_events(
            [{"role": "user", "content": "continue"}],
            agent_service.AgentRuntimeOptions(
                session_id="delegated_model_turn_session", mode="build"
            ),
        )
    )

    assert len(items) == 1
    assert items[0].event == "usage"
    config = captured["config"]
    assert config is not None
    assert config.options.session_id == "delegated_model_turn_session"
    assert isinstance(config.disabled_tools, set)
    assert callable(captured["callbacks"].build_turn_tools)


def test_process_tool_calls_delegates_to_session_processor_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="delegated_tool_call_processing_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    captured: dict[str, object] = {}

    def _fake_stream_tool_processing(config, callbacks):  # noqa: ANN001, ANN202
        captured["config"] = config
        captured["callbacks"] = callbacks
        yield agent_service._prompt_event(
            "tool_start", {"id": "call_delegate_1", "name": "bash", "args": {"command": "echo hi"}}
        )
        return "continue", config.messages, config.step_index + 1

    monkeypatch.setattr(
        agent_service,
        "_processor_stream_tool_call_processing",
        _fake_stream_tool_processing,
    )

    iterator = agent_service._process_tool_calls(
        messages=[{"role": "user", "content": "continue"}],
        tool_calls=[
            agent_service.ToolCall(
                id="call_delegate_1", name="bash", arguments={"command": "echo hi"}
            )
        ],
        step_index=2,
        options=agent_service.AgentRuntimeOptions(
            session_id="delegated_tool_call_processing_session",
            mode="build",
            workspace_path=str(tmp_path),
        ),
        assistant_message_id="message_delegate_1",
    )
    items: list[object] = []
    while True:
        try:
            items.append(next(iterator))
        except StopIteration as stop:
            result = stop.value
            break

    assert len(items) == 1
    assert items[0].event == "tool_start"
    assert result == ("continue", [{"role": "user", "content": "continue"}], 3)
    config = captured["config"]
    assert config is not None
    assert config.step_index == 2
    assert config.assistant_message_id == "message_delegate_1"
    assert callable(captured["callbacks"].execute_tool)


def test_session_bus_wait_for_returns_matching_event() -> None:
    _reset_runtime_state()

    def _publish_later() -> None:
        time.sleep(0.05)
        session_bus.publish(
            SessionBusEvent.STEP_STARTED,
            {"sessionID": "bus_wait_session", "step": 2},
        )

    thread = threading.Thread(target=_publish_later, daemon=True)
    thread.start()
    event = session_bus.wait_for(
        SessionBusEvent.STEP_STARTED,
        predicate=lambda item: str(item.properties.get("sessionID") or "") == "bus_wait_session",
        timeout_ms=1000,
    )
    thread.join(timeout=1)

    assert event is not None
    assert event.type == SessionBusEvent.STEP_STARTED
    assert event.properties["step"] == 2


def test_prompt_instance_manager_waits_for_release_and_resolves_waiter() -> None:
    _reset_runtime_state()

    instance = acquire_prompt_instance("lifecycle_wait_session", wait=False)
    assert instance is not None
    waiter = register_prompt_waiter("lifecycle_wait_session")
    assert waiter is not None

    def _release_later() -> None:
        time.sleep(0.1)
        finish_prompt_instance(
            "lifecycle_wait_session",
            result={"messageID": "message_done_1"},
        )

    thread = threading.Thread(target=_release_later, daemon=True)
    thread.start()

    next_instance = acquire_prompt_instance("lifecycle_wait_session", wait=True, timeout_ms=1000)
    assert next_instance is not None
    assert next_instance is not instance
    result = wait_for_prompt_completion(waiter, timeout_ms=1000)
    assert result == {"messageID": "message_done_1"}

    finish_prompt_instance("lifecycle_wait_session")
    thread.join(timeout=1)


def test_prompt_result_payload_includes_persisted_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    ensure_session_record(
        "prompt_result_payload_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    message = persist_assistant_message(
        session_id="prompt_result_payload_session",
        parent_id="message_user_1",
        message_id="message_assistant_1",
        meta={"finish": "stop"},
        parts=[{"id": "part_text_1", "type": "text", "text": "payload-ready"}],
    )

    payload = agent_service._prompt_result_payload(
        "prompt_result_payload_session", "message_assistant_1"
    )

    assert payload is not None
    assert payload["messageID"] == "message_assistant_1"
    assert payload["message"]["info"]["id"] == "message_assistant_1"
    assert payload["message"]["info"]["role"] == "assistant"
    assert payload["message"]["info"]["parentID"] == "message_user_1"
    assert payload["message"]["parts"][0]["text"] == "payload-ready"
    assert message["info"]["id"] == payload["message"]["info"]["id"]


def test_session_prompt_processor_publishes_prompt_and_step_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeLifecycleLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "prompt_lifecycle_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    events = []
    unsubscribe = session_bus.subscribe_all(lambda event: events.append(event))
    try:
        prompt_resp = client.post(
            "/session/prompt_lifecycle_session/message",
            json={
                "parts": [{"type": "text", "text": "继续"}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
    finally:
        unsubscribe()

    assert prompt_resp.status_code == 200
    assert "生命周期测试回复" in prompt_resp.text

    event_types = [event.type for event in events]
    assert SessionBusEvent.PROMPT_STARTED in event_types
    assert SessionBusEvent.STEP_STARTED in event_types
    assert SessionBusEvent.STEP_FINISHED in event_types
    assert SessionBusEvent.PROMPT_FINISHED in event_types
    assert SessionBusEvent.STATUS in event_types
    assert SessionBusEvent.MESSAGE_UPDATED in event_types
    assert SessionBusEvent.PART_UPDATED in event_types
    assert session_lifecycle.list_session_statuses() == {}


def test_native_prompt_persistence_is_owned_by_processor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeLifecycleLLM)

    def _unexpected_persist(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("native prompt should not fall back to wrap_stream_with_persistence")

    monkeypatch.setattr(session_runtime_module, "wrap_stream_with_persistence", _unexpected_persist)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "processor_owned_persistence_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/processor_owned_persistence_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "生命周期测试回复" in prompt_resp.text

    history = client.get("/session/processor_owned_persistence_session/message").json()
    assert len(history) == 2
    assistant_text = "".join(
        str(part.get("text") or "")
        for part in history[1]["parts"]
        if str(part.get("type") or "") == "text"
    )
    assert assistant_text == "生命周期测试回复。"


def test_native_prompt_persistence_does_not_reparse_raw_sse_in_processor_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeLifecycleLLM)

    def _unexpected_consume(self, raw):  # noqa: ANN001, ANN202
        raise AssertionError(f"native processor path should not call consume(raw): {raw}")

    monkeypatch.setattr(
        session_runtime_module.SessionStreamPersistence, "consume", _unexpected_consume
    )
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "processor_direct_event_persistence_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/processor_direct_event_persistence_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "生命周期测试回复" in prompt_resp.text

    history = client.get("/session/processor_direct_event_persistence_session/message").json()
    assert len(history) == 2
    assert history[1]["info"]["finish"] == "stop"


def test_native_prompt_processor_mutates_session_state_without_apply_event_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeLifecycleLLM)
    apply_calls: list[tuple[str, dict]] = []
    original_apply_event = session_runtime_module.SessionStreamPersistence.apply_event

    def _tracked_apply_event(self, event_name, data):  # noqa: ANN001, ANN202
        apply_calls.append((str(event_name), dict(data)))
        return original_apply_event(self, event_name, data)

    monkeypatch.setattr(
        session_runtime_module.SessionStreamPersistence, "apply_event", _tracked_apply_event
    )
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "processor_direct_prompt_event_mutation_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/processor_direct_prompt_event_mutation_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "生命周期测试回复" in prompt_resp.text

    history = client.get("/session/processor_direct_prompt_event_mutation_session/message").json()
    assert len(history) == 2
    assert history[1]["info"]["finish"] == "stop"
    assert apply_calls == []


def test_default_backend_routes_through_native_processor_without_cli_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeLifecycleLLM)

    def _unexpected_cli_service():  # noqa: ANN202
        raise AssertionError("default native stream should not invoke cli backend service")

    monkeypatch.setattr(agent_service, "get_cli_agent_service", _unexpected_cli_service)

    def _unexpected_persist(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("default native stream should not use wrap_stream_with_persistence")

    monkeypatch.setattr(session_runtime_module, "wrap_stream_with_persistence", _unexpected_persist)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "processor_owned_native_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/processor_owned_native_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "生命周期测试回复" in prompt_resp.text

    session_payload = client.get("/session/processor_owned_native_session").json()
    assert session_payload["agent_backend_id"] == "native"

    history = client.get("/session/processor_owned_native_session/message").json()
    assert len(history) == 2
    assert history[1]["info"]["finish"] == "stop"
    assert "生命周期测试回复" in _message_text(history[1])


def test_explicit_claw_backend_routes_through_cli_stream_without_wrapper_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    captured: dict[str, object] = {}

    class CapturingClawService(FakeCliAgentService):
        def get_config(self, agent_backend_id: str) -> dict[str, str]:
            captured["config_backend_id"] = agent_backend_id
            return super().get_config(agent_backend_id)

    monkeypatch.setattr(agent_service, "get_cli_agent_service", lambda: CapturingClawService())
    monkeypatch.setattr(
        agent_service,
        "get_claw_runtime_manager",
        lambda: SimpleNamespace(
            stream_prompt=lambda config, **_kwargs: iter(
                [
                    {
                        "event": "done",
                        "status": "ok",
                        "message": "CLI 已执行完成。",
                    }
                ]
            )
        ),
    )

    def _unexpected_persist(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("explicit claw stream should not use wrap_stream_with_persistence")

    monkeypatch.setattr(session_runtime_module, "wrap_stream_with_persistence", _unexpected_persist)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "processor_owned_claw_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
            "agent_backend_id": "claw",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/processor_owned_claw_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "agent_backend_id": "claw",
        },
    )

    assert prompt_resp.status_code == 200
    assert "CLI 已执行完成" in prompt_resp.text
    assert captured["config_backend_id"] == "claw"

    session_payload = client.get("/session/processor_owned_claw_session").json()
    assert session_payload["agent_backend_id"] == "claw"

    history = client.get("/session/processor_owned_claw_session/message").json()
    assert len(history) == 2
    assert history[1]["info"]["finish"] == "stop"
    assert "CLI 已执行完成" in _message_text(history[1])


def test_claw_stream_persists_tool_parts_from_cli_tool_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    monkeypatch.setattr(agent_service, "get_cli_agent_service", lambda: FakeCliAgentService())

    class TraceClawRuntime:
        def stream_prompt(self, config, **_kwargs):  # noqa: ANN001, ANN202
            assert config == {"id": "claw"}
            yield {
                "event": "tool_start",
                "id": "call_trace_1",
                "name": "mcp__ResearchOS__search_arxiv",
                "args": {"query": "siglip2"},
            }
            yield {
                "event": "tool_result",
                "id": "call_trace_1",
                "name": "mcp__ResearchOS__search_arxiv",
                "success": True,
                "summary": "已找到 1 篇候选论文",
                "data": {
                    "query": "siglip2",
                    "candidates": [
                        {
                            "arxiv_id": "2502.00001",
                            "title": "SigLIP 2",
                        }
                    ],
                },
            }
            yield {
                "event": "done",
                "status": "ok",
                "message": "工具调用已完成。",
            }

    monkeypatch.setattr(agent_service, "get_claw_runtime_manager", lambda: TraceClawRuntime())
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "claw_tool_trace_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
            "agent_backend_id": "claw",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/claw_tool_trace_session/message",
        json={
            "parts": [{"type": "text", "text": "找一下 SigLIP2 论文"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "agent_backend_id": "claw",
        },
    )
    assert prompt_resp.status_code == 200
    assert "工具调用已完成" in prompt_resp.text

    history = client.get("/session/claw_tool_trace_session/message").json()
    assert len(history) == 2
    assistant = history[1]
    tool_parts = [
        part
        for part in assistant.get("parts") or []
        if isinstance(part, dict) and str(part.get("type") or "") == "tool"
    ]
    assert len(tool_parts) == 1
    assert tool_parts[0]["tool"] == "mcp__ResearchOS__search_arxiv"
    assert tool_parts[0]["state"]["status"] == "completed"
    assert tool_parts[0]["data"]["query"] == "siglip2"
    assert tool_parts[0]["data"]["candidates"][0]["arxiv_id"] == "2502.00001"
    assert "工具调用已完成" in _message_text(assistant)


def test_session_prompt_route_loads_persisted_user_message_for_claw_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    captured: dict[str, object] = {}

    class CapturingClawRuntime:
        def stream_prompt(self, config, **kwargs):  # noqa: ANN001, ANN202
            assert config == {"id": "claw"}
            captured["agent_backend_id"] = "claw"
            captured["prompt"] = kwargs.get("prompt")
            captured["session_id"] = kwargs.get("session_id")
            yield {
                "event": "done",
                "status": "ok",
                "message": "CLI 已执行完成。",
            }

    monkeypatch.setattr(agent_service, "get_cli_agent_service", lambda: FakeCliAgentService())
    monkeypatch.setattr(agent_service, "get_claw_runtime_manager", lambda: CapturingClawRuntime())
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_prompt_claw_history_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
            "agent_backend_id": "claw",
        },
    )
    assert created.status_code == 200

    append_session_message(
        session_id="session_prompt_claw_history_session",
        role="user",
        content="之前我已经导入了 SigLIP 2 论文。",
    )
    append_session_message(
        session_id="session_prompt_claw_history_session",
        role="assistant",
        content="好的，我已经记住这篇论文。",
    )

    prompt_resp = client.post(
        "/session/session_prompt_claw_history_session/message",
        json={
            "parts": [
                {"type": "text", "text": "我引用了 SigLIP 2，请分析一下其架构图，并引用原图。"}
            ],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "agent_backend_id": "claw",
        },
    )

    assert prompt_resp.status_code == 200
    assert "CLI 已执行完成" in prompt_resp.text
    assert captured["agent_backend_id"] == "claw"
    assert captured["session_id"] == "session_prompt_claw_history_session"
    prompt = str(captured.get("prompt") or "")
    assert "之前我已经导入了 SigLIP 2 论文。" in prompt
    assert "好的，我已经记住这篇论文。" in prompt
    assert "我引用了 SigLIP 2，请分析一下其架构图，并引用原图。" in prompt
    assert "[User]\n你好" not in prompt


def test_legacy_native_backend_id_is_normalized_to_native(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeLifecycleLLM)

    def _unexpected_cli_service():  # noqa: ANN202
        raise AssertionError("legacy native alias should resolve to host runtime, not cli backend")

    monkeypatch.setattr(agent_service, "get_cli_agent_service", _unexpected_cli_service)

    client = TestClient(_build_app())

    response = client.post(
        "/agent/chat",
        json={
            "messages": [{"role": "user", "content": "继续"}],
            "session_id": "legacy_native_backend_alias_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
            "agent_backend_id": "researchos_native",
        },
    )

    assert response.status_code == 200
    assert "生命周期测试回复" in response.text

    session_payload = client.get("/session/legacy_native_backend_alias_session").json()
    assert session_payload["agent_backend_id"] == "native"


def test_session_prompt_route_uses_persisted_backend_when_request_omits_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    captured: dict[str, object] = {}

    class CapturingClawRuntime:
        def stream_prompt(self, config, **kwargs):  # noqa: ANN001, ANN202
            assert config == {"id": "claw"}
            captured["config"] = config
            captured["agent_backend_id"] = "claw"
            captured["prompt"] = kwargs.get("prompt")
            yield {
                "event": "done",
                "status": "ok",
                "message": "CLI 已执行完成。",
            }

    monkeypatch.setattr(agent_service, "get_cli_agent_service", lambda: FakeCliAgentService())
    monkeypatch.setattr(agent_service, "get_claw_runtime_manager", lambda: CapturingClawRuntime())
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "persisted_backend_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
            "agent_backend_id": "claw",
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/session/persisted_backend_session/message",
        json={
            "parts": [{"type": "text", "text": "继续分析"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert response.status_code == 200
    assert "CLI 已执行完成" in response.text
    assert captured["agent_backend_id"] == "claw"


def test_claw_paper_analysis_without_final_message_emits_fallback_text_and_persists_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    monkeypatch.setattr(
        agent_service,
        "get_cli_agent_service",
        lambda: SimpleNamespace(
            get_config=lambda backend_id: {"label": f"Fake CLI {backend_id}"},
            get_runtime_config=lambda backend_id: {"id": backend_id},
        ),
    )

    class _ToolOnlyClawRuntime:
        def stream_prompt(self, config, **kwargs):  # noqa: ANN001, ANN202
            assert config == {"id": "claw"}
            assert kwargs["session_id"] == "paper_analysis_claw_session"
            yield {
                "event": "tool_start",
                "id": "paper_tool_1",
                "name": "mcp__ResearchOS__get_paper_analysis",
                "args": {"paper_id": "paper_siglip2"},
            }
            yield {
                "event": "tool_result",
                "id": "paper_tool_1",
                "name": "mcp__ResearchOS__get_paper_analysis",
                "success": True,
                "summary": "已读取论文架构图并定位原图链接",
                "data": {
                    "paper_id": "paper_siglip2",
                    "figure_url": "https://example.com/fig1.png",
                },
            }
            yield {
                "event": "done",
                "status": "ok",
                "message": "",
            }

    monkeypatch.setattr(agent_service, "get_claw_runtime_manager", lambda: _ToolOnlyClawRuntime())
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "paper_analysis_claw_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
            "agent_backend_id": "claw",
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/session/paper_analysis_claw_session/message",
        json={
            "parts": [{"type": "text", "text": "请分析这篇论文的架构图并引用原图。"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "agent_backend_id": "claw",
            "mounted_paper_ids": ["paper_siglip2"],
            "mounted_primary_paper_id": "paper_siglip2",
        },
    )

    assert response.status_code == 200
    assert "本轮已完成以下工具调用" in response.text
    assert "已读取论文架构图并定位原图链接" in response.text

    history = client.get("/session/paper_analysis_claw_session/message").json()
    assert len(history) == 2
    assistant = history[1]
    assert "本轮已完成以下工具调用" in _message_text(assistant)
    tool_parts = [
        part
        for part in assistant.get("parts") or []
        if isinstance(part, dict) and str(part.get("type") or "") == "tool"
    ]
    assert len(tool_parts) == 1
    assert tool_parts[0]["tool"] == "mcp__ResearchOS__get_paper_analysis"
    assert tool_parts[0]["summary"] == "已读取论文架构图并定位原图链接"
    assert tool_parts[0]["data"]["paper_id"] == "paper_siglip2"


def test_cli_chat_prompt_reuses_shared_turn_context_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "cli_shared_turn_context_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )
    monkeypatch.setattr(
        agent_service,
        "_available_turn_function_tools",
        lambda *_args, **_kwargs: [
            "get_paper_detail",
            "analyze_figures",
            "get_paper_analysis",
            "search_arxiv",
        ],
    )

    prompt = agent_service._build_cli_chat_prompt(
        [{"role": "user", "content": "请结合原图解释这个 encoder 和 decoder 的交互。"}],
        agent_service.AgentRuntimeOptions(
            session_id="cli_shared_turn_context_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
            mounted_paper_ids=["paper_siglip2"],
            mounted_primary_paper_id="paper_siglip2",
        ),
        backend_label="Claw",
    )

    assert "以下是本轮系统上下文：" in prompt
    assert "Available function tools this turn:" in prompt
    assert "Mounted paper turn guidance:" in prompt
    assert "Inspect get_paper_detail first" in prompt
    assert "call analyze_figures before answering" in prompt


def test_cli_chat_prompt_includes_latest_user_system_and_output_constraint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "cli_prompt_shaping_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )
    monkeypatch.setattr(
        agent_service,
        "_available_turn_function_tools",
        lambda *_args, **_kwargs: ["get_paper_detail", "analyze_figures"],
    )

    prompt = agent_service._build_cli_chat_prompt(
        [
            {"role": "user", "content": "先看上下文。"},
            {
                "role": "user",
                "content": "请用不超过60字总结这个方法的主要贡献。",
                "system": "你是一个专门的论文助手，优先突出论文贡献和证据来源。",
            },
        ],
        agent_service.AgentRuntimeOptions(
            session_id="cli_prompt_shaping_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
        backend_label="Claw",
    )

    assert "以下是最后一条用户消息附带的额外系统指令" in prompt
    assert "你是一个专门的论文助手" in prompt
    assert "以下是最后一条用户消息附带的输出硬约束" in prompt
    assert "硬约束：最终回答必须不超过60字" in prompt


def test_cli_chat_prompt_renders_tool_chain_transcript_and_orphan_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "cli_tool_transcript_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )
    monkeypatch.setattr(
        agent_service,
        "_available_turn_function_tools",
        lambda *_args, **_kwargs: ["get_paper_detail", "analyze_figures"],
    )

    prompt = agent_service._build_cli_chat_prompt(
        [
            {"role": "user", "content": "请分析这篇论文的架构图。"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_paper_detail",
                        "type": "function",
                        "function": {
                            "name": "get_paper_detail",
                            "arguments": json.dumps(
                                {"paper_id": "paper_siglip2"}, ensure_ascii=False
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_paper_detail",
                "name": "get_paper_detail",
                "content": json.dumps(
                    {
                        "success": True,
                        "summary": "已读取论文基础信息",
                        "data": {"paper_id": "paper_siglip2", "has_figures": True},
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "role": "tool",
                "name": "analyze_figures",
                "content": json.dumps(
                    {
                        "success": True,
                        "summary": "已定位编码器和解码器交互图",
                        "data": {"figure_ref": "fig_3"},
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        agent_service.AgentRuntimeOptions(
            session_id="cli_tool_transcript_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
            mounted_paper_ids=["paper_siglip2"],
            mounted_primary_paper_id="paper_siglip2",
        ),
        backend_label="Claw",
    )

    assert "Called tool `get_paper_detail`." in prompt
    assert 'Arguments: {"paper_id":"paper_siglip2"}' in prompt
    assert "Result from `get_paper_detail` tool call." in prompt
    assert "Summary: 已读取论文基础信息" in prompt
    assert "Recovered result from an earlier `analyze_figures` tool call." in prompt
    assert "Summary: 已定位编码器和解码器交互图" in prompt


def test_cli_backend_stream_does_not_fall_back_to_wrapper_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "get_cli_agent_service", lambda: FakeCliAgentService())

    def _unexpected_persist(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("cli backend stream should not use wrap_stream_with_persistence")

    monkeypatch.setattr(session_runtime_module, "wrap_stream_with_persistence", _unexpected_persist)
    client = TestClient(_build_app())

    response = client.post(
        "/agent/chat",
        json={
            "messages": [{"role": "user", "content": "继续"}],
            "session_id": "cli_persistence_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
            "agent_backend_id": "fake_cli",
        },
    )

    assert response.status_code == 200
    assert "CLI 已执行完成" in response.text

    messages = client.get("/agent/conversations/cli_persistence_session").json()["messages"]
    assert len(messages) == 2
    assert messages[1]["role"] == "assistant"
    assert "CLI 已执行完成" in messages[1]["content"]


@REMOVED_NATIVE_ROUTE
def test_provider_executed_builtin_tools_do_not_trigger_local_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeProviderExecutedBuiltinLLM)
    executed = {"count": 0}

    def _unexpected_execute(*_args, **_kwargs):  # noqa: ANN001, ANN202
        executed["count"] += 1
        if False:
            yield None

    monkeypatch.setattr(agent_service, "execute_tool_stream", _unexpected_execute)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "provider_builtin_prompt_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/provider_builtin_prompt_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "搜索已完成" in prompt_resp.text
    assert executed["count"] == 0

    messages = load_agent_messages("provider_builtin_prompt_session")
    assert messages[1]["tool_calls"] == [
        {
            "id": "ws_builtin_1",
            "type": "function",
            "provider_executed": True,
            "function": {
                "name": "web_search",
                "arguments": '{"action": {"type": "search", "query": "OpenAI"}}',
            },
        }
    ]
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "ws_builtin_1"
    assert messages[2]["provider_executed"] is True


def test_provider_executed_tool_only_turn_gets_final_fallback_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeProviderExecutedToolOnlyLLM)

    def _unexpected_execute(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("provider executed tools should not hit local executor")
        if False:
            yield None

    monkeypatch.setattr(agent_service, "execute_tool_stream", _unexpected_execute)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "provider_tool_only_prompt_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/provider_tool_only_prompt_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "本轮已完成以下工具调用" in prompt_resp.text
    assert "web_search: 已找到 MinerU 相关资料" in prompt_resp.text

    messages = load_agent_messages("provider_tool_only_prompt_session")
    assert messages[1]["role"] == "assistant"
    assert "本轮已完成以下工具调用" in str(messages[1].get("content") or "")
    assert messages[2]["provider_executed"] is True


def test_provider_executed_tool_preamble_is_replaced_by_tool_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeProviderExecutedToolPreambleLLM)

    def _unexpected_execute(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("provider executed tools should not hit local executor")
        if False:
            yield None

    monkeypatch.setattr(agent_service, "execute_tool_stream", _unexpected_execute)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "provider_tool_preamble_prompt_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/provider_tool_preamble_prompt_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "本轮已完成以下工具调用" in prompt_resp.text
    assert "我先帮你查一下相关资料" not in prompt_resp.text

    messages = load_agent_messages("provider_tool_preamble_prompt_session")
    assert messages[1]["role"] == "assistant"
    assert "本轮已完成以下工具调用" in str(messages[1].get("content") or "")
    assert "我先帮你查一下相关资料" not in str(messages[1].get("content") or "")
    assert messages[2]["provider_executed"] is True


def test_native_prompt_hard_stops_local_tool_execution_on_reserved_summary_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    FakeBudgetHardStopLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeBudgetHardStopLLM)
    monkeypatch.setattr(agent_service, "_get_max_tool_steps", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"}
    )
    executed_commands: list[str] = []

    def _fake_execute_tool_stream(_name, arguments, context=None):  # noqa: ANN001, ANN202
        del context
        executed_commands.append(str((arguments or {}).get("command") or ""))
        yield agent_service.ToolResult(
            success=True,
            summary="第一步命令执行成功",
            data={"stdout": "first"},
        )

    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "budget_hard_stop_prompt_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/budget_hard_stop_prompt_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "预算总结：第一步检索已完成，第二个工具调用因预算上限被跳过。" in prompt_resp.text
    assert executed_commands == ["echo first"]

    messages = load_agent_messages("budget_hard_stop_prompt_session")
    skipped_results = [
        item
        for item in messages
        if str(item.get("role") or "") == "tool"
        and str(item.get("tool_call_id") or "") == "call_budget_2"
    ]
    assert len(skipped_results) == 1
    assert "未执行此工具调用" in str(skipped_results[0].get("content") or "")
    assert any(
        str(item.get("role") or "") == "assistant"
        and "预算总结：第一步检索已完成，第二个工具调用因预算上限被跳过。"
        in str(item.get("content") or "")
        for item in messages
    )


def test_native_prompt_hard_stops_repeated_identical_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeRepeatedToolTurnLLM)
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"}
    )
    executed_commands: list[str] = []

    def _fake_execute_tool_stream(_name, arguments, context=None):  # noqa: ANN001, ANN202
        del context
        executed_commands.append(str((arguments or {}).get("command") or ""))
        yield agent_service.ToolResult(
            success=True,
            summary="第一次执行成功",
            data={"stdout": "repeat"},
        )

    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "repeat_tool_prompt_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/repeat_tool_prompt_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "检测到模型连续重复请求相同工具调用" in prompt_resp.text
    assert "第一次执行成功" in prompt_resp.text
    assert executed_commands == ["echo repeat"]

    messages = load_agent_messages("repeat_tool_prompt_session")
    skipped_results = [
        item
        for item in messages
        if str(item.get("role") or "") == "tool"
        and str(item.get("tool_call_id") or "") == "call_repeat_2"
    ]
    assert len(skipped_results) == 1
    assert "未再次执行此工具调用" in str(skipped_results[0].get("content") or "")
    assert any(
        str(item.get("role") or "") == "assistant"
        and "检测到模型连续重复请求相同工具调用" in str(item.get("content") or "")
        and "第一次执行成功" in str(item.get("content") or "")
        for item in messages
    )


def test_session_prompt_no_reply_only_persists_user_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "prompt_no_reply_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    called = False

    def _unexpected_stream_chat(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal called
        called = True
        yield 'event: text_delta\ndata: {"content":"unexpected"}\n\n'
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(session_runtime_router, "stream_chat", _unexpected_stream_chat)

    response = client.post(
        "/session/prompt_no_reply_session/message",
        json={
            "parts": [{"type": "text", "text": "只保存，不回复"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "noReply": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["info"]["role"] == "user"
    assert payload["parts"][0]["text"] == "只保存，不回复"
    assert called is False
    assert session_lifecycle.list_session_statuses() == {}

    history = client.get("/session/prompt_no_reply_session/message").json()
    assert len(history) == 1
    assert history[0]["info"]["role"] == "user"
    assert history[0]["parts"][0]["text"] == "只保存，不回复"


def test_session_prompt_accepts_file_only_parts_and_persists_tool_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "prompt_file_only_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/session/prompt_file_only_session/message",
        json={
            "parts": [
                {
                    "type": "file",
                    "url": "https://example.com/cat.png",
                    "filename": "cat.png",
                    "mime": "image/png",
                }
            ],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "noReply": True,
            "tools": {
                "bash": False,
                "search_web": True,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["info"]["role"] == "user"
    assert payload["info"]["tools"] == {"bash": False, "search_web": True}
    assert payload["parts"] == [
        {
            "id": payload["parts"][0]["id"],
            "sessionID": "prompt_file_only_session",
            "messageID": payload["info"]["id"],
            "type": "file",
            "content": "",
            "url": "https://example.com/cat.png",
            "filename": "cat.png",
            "mime": "image/png",
        }
    ]

    messages = load_agent_messages("prompt_file_only_session")
    assert messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "file",
                    "url": "https://example.com/cat.png",
                    "filename": "cat.png",
                    "mime": "image/png",
                }
            ],
            "tools": {
                "bash": False,
                "search_web": True,
            },
        }
    ]


@REMOVED_NATIVE_ROUTE
def test_queued_prompt_waits_for_previous_run_and_reloads_latest_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    FakeQueuedPromptLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeQueuedPromptLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "queued_prompt_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    events = []
    first_step_started = threading.Event()

    def _record_event(event) -> None:  # noqa: ANN001
        events.append(event)
        if (
            event.type == SessionBusEvent.STEP_STARTED
            and str(event.properties.get("sessionID") or "") == "queued_prompt_session"
        ):
            first_step_started.set()

    unsubscribe = session_bus.subscribe_all(_record_event)
    results: dict[str, str] = {}

    def _run_first() -> None:
        response = client.post(
            "/session/queued_prompt_session/message",
            json={
                "parts": [{"type": "text", "text": "first"}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results["first"] = response.text

    def _run_second() -> None:
        response = client.post(
            "/session/queued_prompt_session/message",
            json={
                "parts": [{"type": "text", "text": "second"}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results["second"] = response.text

    try:
        first_thread = threading.Thread(target=_run_first, daemon=True)
        second_thread = threading.Thread(target=_run_second, daemon=True)
        first_thread.start()
        assert first_step_started.wait(timeout=2)
        second_thread.start()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
    finally:
        unsubscribe()

    assert "first-done" in results["first"]
    assert "second-saw-first" in results["second"]
    assert SessionBusEvent.PROMPT_QUEUED in [event.type for event in events]

    history = client.get("/session/queued_prompt_session/message").json()
    assert len(history) == 4
    assert [item["info"]["role"] for item in history] == ["user", "assistant", "user", "assistant"]
    first_assistant_text = "".join(
        str(part.get("text") or "")
        for part in history[1]["parts"]
        if str(part.get("type") or "") == "text"
    )
    second_assistant_text = "".join(
        str(part.get("text") or "")
        for part in history[3]["parts"]
        if str(part.get("type") or "") == "text"
    )
    assert first_assistant_text == "first-done"
    assert second_assistant_text == "second-saw-first"


@REMOVED_NATIVE_ROUTE
def test_queued_prompt_uses_callback_queue_resume_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    FakeQueuedPromptLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeQueuedPromptLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "queued_prompt_callback_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    queued_callback = threading.Event()
    original_queue_callback = agent_service.queue_prompt_callback

    def _queue_callback(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        queued_callback.set()
        return original_queue_callback(*args, **kwargs)

    monkeypatch.setattr(agent_service, "queue_prompt_callback", _queue_callback)

    first_step_started = threading.Event()
    unsubscribe = session_bus.subscribe_all(
        lambda event: (
            first_step_started.set()
            if (
                event.type == SessionBusEvent.STEP_STARTED
                and str(event.properties.get("sessionID") or "") == "queued_prompt_callback_session"
            )
            else None
        )
    )
    results: dict[str, str] = {}

    def _run_prompt(name: str) -> None:
        response = client.post(
            "/session/queued_prompt_callback_session/message",
            json={
                "parts": [{"type": "text", "text": name}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results[name] = response.text

    try:
        first_thread = threading.Thread(target=lambda: _run_prompt("first"), daemon=True)
        second_thread = threading.Thread(target=lambda: _run_prompt("second"), daemon=True)
        first_thread.start()
        assert first_step_started.wait(timeout=2)
        second_thread.start()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
    finally:
        unsubscribe()

    assert queued_callback.wait(timeout=1)
    assert "first-done" in results["first"]
    assert "second-saw-first" in results["second"]
    assert len(FakeQueuedPromptLLM.calls) == 2
    assert any(
        str(item.get("role") or "") == "assistant"
        and "first-done" in str(item.get("content") or "")
        for item in FakeQueuedPromptLLM.calls[1]
    )


@REMOVED_NATIVE_ROUTE
def test_queued_prompt_resume_existing_reuses_active_prompt_instance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    FakeQueuedPromptLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeQueuedPromptLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "queued_prompt_resume_existing_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    acquire_calls = 0
    original_acquire = agent_service.acquire_prompt_instance

    def _counting_acquire(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal acquire_calls
        acquire_calls += 1
        return original_acquire(*args, **kwargs)

    monkeypatch.setattr(agent_service, "acquire_prompt_instance", _counting_acquire)

    first_step_started = threading.Event()
    unsubscribe = session_bus.subscribe_all(
        lambda event: (
            first_step_started.set()
            if (
                event.type == SessionBusEvent.STEP_STARTED
                and str(event.properties.get("sessionID") or "")
                == "queued_prompt_resume_existing_session"
            )
            else None
        )
    )
    results: dict[str, str] = {}

    def _run_prompt(name: str) -> None:
        response = client.post(
            "/session/queued_prompt_resume_existing_session/message",
            json={
                "parts": [{"type": "text", "text": name}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results[name] = response.text

    try:
        first_thread = threading.Thread(target=lambda: _run_prompt("first"), daemon=True)
        second_thread = threading.Thread(target=lambda: _run_prompt("second"), daemon=True)
        first_thread.start()
        assert first_step_started.wait(timeout=2)
        second_thread.start()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
    finally:
        unsubscribe()

    assert "first-done" in results["first"]
    assert "second-saw-first" in results["second"]
    assert acquire_calls == 2


def test_resume_existing_requires_existing_active_instance():
    _reset_runtime_state()

    processor = agent_service.SessionPromptProcessor(
        messages=[],
        options=agent_service.AgentRuntimeOptions(
            session_id="missing_resume_owner_session",
            mode="build",
        ),
        step_index=0,
        assistant_message_id="message_missing_resume_owner",
        lifecycle_kind="resume",
        resume_existing=True,
    )

    with pytest.raises(RuntimeError, match="active prompt instance missing during resume_existing"):
        list(processor.stream())


@REMOVED_NATIVE_ROUTE
def test_queued_prompt_resume_restores_processor_from_minimal_callback_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    FakeQueuedPromptLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeQueuedPromptLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "queued_prompt_processor_reuse_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    from_payload_calls: list[dict] = []
    original_from_payload = agent_service.SessionPromptProcessor._from_callback_payload.__func__

    def _capturing_from_payload(cls, payload, *, resume_existing, manage_session_lifecycle):  # noqa: ANN001, ANN202
        from_payload_calls.append(dict(payload or {}))
        return original_from_payload(
            cls,
            payload,
            resume_existing=resume_existing,
            manage_session_lifecycle=manage_session_lifecycle,
        )

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_from_callback_payload",
        classmethod(_capturing_from_payload),
    )

    first_step_started = threading.Event()
    unsubscribe = session_bus.subscribe_all(
        lambda event: (
            first_step_started.set()
            if (
                event.type == SessionBusEvent.STEP_STARTED
                and str(event.properties.get("sessionID") or "")
                == "queued_prompt_processor_reuse_session"
            )
            else None
        )
    )
    results: dict[str, str] = {}

    def _run_prompt(name: str) -> None:
        response = client.post(
            "/session/queued_prompt_processor_reuse_session/message",
            json={
                "parts": [{"type": "text", "text": name}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results[name] = response.text

    try:
        first_thread = threading.Thread(target=lambda: _run_prompt("first"), daemon=True)
        second_thread = threading.Thread(target=lambda: _run_prompt("second"), daemon=True)
        first_thread.start()
        assert first_step_started.wait(timeout=2)
        second_thread.start()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
    finally:
        unsubscribe()

    assert "first-done" in results["first"]
    assert "second-saw-first" in results["second"]
    assert len(from_payload_calls) == 1
    queued_payload = from_payload_calls[0]
    assert queued_payload["session_id"] == "queued_prompt_processor_reuse_session"
    assert str(queued_payload.get("request_message_id") or "").strip()


def test_callback_payload_can_restore_request_cursor_from_session_turn_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    ensure_session_record(
        "queued_prompt_turn_state_restore_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="queued_prompt_turn_state_restore_session",
        role="user",
        content="follow up",
        meta={"variant": "medium", "activeSkillIDs": ["skill.alpha"]},
    )

    processor = agent_service.SessionPromptProcessor._from_callback_payload(
        {"session_id": "queued_prompt_turn_state_restore_session"},
        resume_existing=True,
        manage_session_lifecycle=False,
    )

    assert processor.options.session_id == "queued_prompt_turn_state_restore_session"
    assert processor.options.reasoning_level == "medium"
    assert processor.options.active_skill_ids == ["skill.alpha"]
    assert processor.persistence is not None
    assert processor.persistence.parent_id == user_message["info"]["id"]


def test_callback_payload_prefers_explicit_request_cursor_when_valid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    ensure_session_record(
        "queued_prompt_explicit_cursor_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    first_user = append_session_message(
        session_id="queued_prompt_explicit_cursor_session",
        role="user",
        content="first request",
        meta={"variant": "medium", "activeSkillIDs": ["skill.alpha"]},
    )
    append_session_message(
        session_id="queued_prompt_explicit_cursor_session",
        role="assistant",
        content="partial",
        parent_id=str(first_user["info"]["id"]),
        message_id="message_explicit_cursor",
        meta={"finish": "tool-calls"},
    )
    second_user = append_session_message(
        session_id="queued_prompt_explicit_cursor_session",
        role="user",
        content="second request",
        meta={"variant": "high", "activeSkillIDs": ["skill.beta"]},
    )

    processor = agent_service.SessionPromptProcessor._from_callback_payload(
        {
            "session_id": "queued_prompt_explicit_cursor_session",
            "request_message_id": str(first_user["info"]["id"]),
        },
        resume_existing=True,
        manage_session_lifecycle=False,
    )

    assert str(second_user["info"]["id"]) != str(first_user["info"]["id"])
    assert processor.options.session_id == "queued_prompt_explicit_cursor_session"
    assert processor.options.reasoning_level == "medium"
    assert processor.options.active_skill_ids == ["skill.alpha"]
    assert processor.persistence is not None
    assert processor.persistence.parent_id == first_user["info"]["id"]


def test_callback_payload_restore_ignores_legacy_runtime_fields_and_uses_session_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    ensure_session_record(
        "queued_prompt_strict_restore_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="queued_prompt_strict_restore_session",
        role="user",
        content="follow up",
        meta={"variant": "medium", "activeSkillIDs": ["skill.alpha"]},
    )
    append_session_message(
        session_id="queued_prompt_strict_restore_session",
        role="assistant",
        content="partial",
        parent_id=str(user_message["info"]["id"]),
        message_id="message_strict_restore",
        meta={"finish": "tool-calls"},
    )

    processor = agent_service.SessionPromptProcessor._from_callback_payload(
        {
            "session_id": "queued_prompt_strict_restore_session",
            "request_message_id": "legacy_request_should_be_ignored",
            "reasoning_level": "high",
            "active_skill_ids": ["skill.legacy"],
            "assistant_message_id": "message_legacy",
            "options": {
                "session_id": "legacy_session",
                "mode": "chat",
                "workspace_path": "D:/legacy",
                "reasoning_level": "high",
                "active_skill_ids": ["skill.legacy"],
            },
            "persistence": {
                "session_id": "legacy_session",
                "parent_id": "legacy_parent",
                "assistant_message_id": "message_legacy",
            },
        },
        resume_existing=True,
        manage_session_lifecycle=False,
    )

    assert processor.options.session_id == "queued_prompt_strict_restore_session"
    assert processor.options.reasoning_level == "medium"
    assert processor.options.active_skill_ids == ["skill.alpha"]
    assert processor.persistence is not None
    assert processor.persistence.parent_id == user_message["info"]["id"]
    assert processor.persistence.assistant_message_id == "message_strict_restore"


def test_system_prompt_messages_drop_local_mode_and_skill_adapter_noise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    ensure_session_record(
        "system_prompt_alignment_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "openai", "modelID": "gpt-5.2"},
    )
    monkeypatch.setattr(
        agent_service,
        "list_local_skills",
        lambda: [{"id": "skill.alpha", "name": "skill.alpha", "description": "alpha skill"}],
    )

    messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_alignment_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
            active_skill_ids=["skill.alpha"],
        )
    )

    joined = "\n\n".join(messages)
    assert "Default to concise Simplified Chinese" not in joined
    assert "Current mode is build" not in joined
    assert "User-selected skills for this session" not in joined
    assert "Skills are optional workflow templates" in joined
    assert "Working directory:" in joined


def test_system_prompt_tool_binding_prefers_apply_patch_for_gpt5(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_tool_binding_gpt5_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeGpt5LLM:
        provider = "openai"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="openai", base_url="https://api.openai.com/v1", model="gpt-5.2"
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeGpt5LLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "openai", "modelID": "gpt-5.2"},
    )

    messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_tool_binding_gpt5_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        )
    )

    joined = "\n\n".join(messages)
    assert "Available function tools this turn:" in joined
    assert "apply_patch" in joined
    assert "Do not call edit or write in this turn." in joined
    assert "Do not expose chain-of-thought or self-talk in user-visible text." in joined


def test_system_prompt_tool_binding_respects_user_tool_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_tool_binding_override_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )

    messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_tool_binding_override_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
        user_tools={"bash": False},
    )

    joined = "\n\n".join(messages)
    assert "Available function tools this turn:" in joined
    assert "Bash is not exposed in this turn." in joined


def test_system_prompt_tool_binding_describes_plan_mode_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_tool_binding_plan_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="plan",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )

    messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_tool_binding_plan_session",
            mode="plan",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        )
    )

    joined = "\n\n".join(messages)
    available_line = next(
        line
        for line in joined.splitlines()
        if line.startswith("Available function tools this turn:")
    )
    assert "Plan mode stays read-only except for the plan file tools listed above." in joined
    assert "Plan-mode control tools in this turn: question, plan_exit." in joined
    assert "Bash is not exposed in this turn." not in joined
    assert "In plan mode, bash may only be used for read-only inspection commands." in joined
    assert "subagent_type=explore" in joined
    assert "bash" in available_line
    assert "webfetch" in available_line
    assert "task" in available_line


def test_normalize_messages_keeps_latest_user_tool_binding_across_tool_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_tool_binding_resume_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )

    normalized = agent_service._normalize_messages(
        [
            {
                "role": "user",
                "content": "继续",
                "tools": {"bash": False},
            },
            {
                "role": "assistant",
                "content": "需要权限确认。",
            },
            {
                "role": "tool",
                "name": "question",
                "content": '{"status":"pending"}',
                "tool_call_id": "call_question_resume",
            },
        ],
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_tool_binding_resume_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
    )

    system_messages = [
        str(item.get("content") or "")
        for item in normalized
        if str(item.get("role") or "") == "system"
    ]
    joined = "\n\n".join(system_messages)
    assert "Bash is not exposed in this turn." in joined
    assert any(
        item.get("tools") == {"bash": False}
        for item in normalized
        if str(item.get("role") or "") == "user"
    )


def test_normalize_messages_recovers_orphan_tool_result_into_user_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "orphan_tool_result_recovery_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )

    normalized = agent_service._normalize_messages(
        [
            {"role": "user", "content": "继续总结"},
            {"role": "assistant", "content": "我先整理前面的结果。"},
            {
                "role": "tool",
                "name": "search_papers",
                "content": json.dumps(
                    {
                        "success": True,
                        "summary": "已找到 2 篇候选论文",
                        "data": {"count": 2, "top_ids": ["paper_1", "paper_2"]},
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        agent_service.AgentRuntimeOptions(
            session_id="orphan_tool_result_recovery_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
    )

    recovered = [
        item
        for item in normalized
        if str(item.get("role") or "") == "user"
        and "Recovered result from an earlier `search_papers` tool call."
        in str(item.get("content") or "")
    ]
    assert len(recovered) == 1
    assert "Summary: 已找到 2 篇候选论文" in str(recovered[0]["content"])
    assert '"count":2' in str(recovered[0]["content"])


def test_system_prompt_reasoning_profile_varies_with_reasoning_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_reasoning_profile_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )

    low_messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_reasoning_profile_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="low",
        )
    )
    high_messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_reasoning_profile_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="high",
        )
    )

    low_joined = "\n\n".join(low_messages)
    high_joined = "\n\n".join(high_messages)
    assert "Reasoning profile: low. Tool budget this turn: 10 steps." in low_joined
    assert "Prefer the shortest viable tool chain" in low_joined
    assert "do not use bash for exploration" in low_joined
    assert "Reasoning profile: high. Tool budget this turn: 30 steps." in high_joined
    assert "You may spend extra steps on validation" in high_joined
    assert "using bash for precise verification" in high_joined


def test_system_prompt_adds_repo_lookup_strategy_for_code_fact_queries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_repo_lookup_strategy_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )

    messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_repo_lookup_strategy_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
        latest_user_request=(
            "请先读取 apps/api/routers/session_runtime.py，"
            "然后说明 POST /session/{session_id}/message 如何把 mode 和 reasoning_level 传到后端。"
            "不要凭记忆回答。"
        ),
    )

    joined = "\n\n".join(messages)
    assert "Repository lookup strategy for this turn:" in joined
    assert "Do not narrate your internal thinking" in joined
    assert "Prefer production/source files over tests" in joined
    assert "read those files directly before using any other tool" in joined
    assert "Do not use glob after grep already returned the exact files you need" in joined
    assert "use grep results and read with offset/limit for local context" in joined
    assert "Do not use bash for ordinary repository inspection" in joined


def test_system_prompt_adds_academic_lookup_strategy_for_paper_queries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_academic_lookup_strategy_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )

    messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_academic_lookup_strategy_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
        latest_user_request="请帮我搜索多模态检索增强生成的最新论文，优先 arXiv 和本地论文库。",
    )

    joined = "\n\n".join(messages)
    assert "Academic lookup strategy for this turn:" in joined
    assert (
        "Use search_papers first for papers already in the local library. It returns a compact candidate list"
        in joined
    )
    assert (
        "Use search_literature for external paper discovery across arXiv, conferences, and journals"
        in joined
    )
    assert "Use search_arxiv for external paper discovery and arXiv candidate lookup." in joined
    assert (
        "Do not start with broad web search if local library or arXiv tools can answer the request directly."
        in joined
    )


def test_system_prompt_adds_figure_grounded_mounted_paper_guidance_without_academic_keywords(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ensure_session_record(
        "system_prompt_figure_grounded_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    class FakeClaudeLLM:
        provider = "anthropic"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    monkeypatch.setattr(agent_service, "LLMClient", FakeClaudeLLM)
    monkeypatch.setattr(agent_service, "list_local_skills", lambda: [])
    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "anthropic", "modelID": "claude-3-7-sonnet"},
    )
    monkeypatch.setattr(
        agent_service,
        "_available_turn_function_tools",
        lambda *_args, **_kwargs: ["get_paper_detail", "analyze_figures", "get_paper_analysis"],
    )

    messages = agent_service._build_system_prompt_messages(
        agent_service.AgentRuntimeOptions(
            session_id="system_prompt_figure_grounded_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
            mounted_paper_ids=["paper_siglip2"],
            mounted_primary_paper_id="paper_siglip2",
        ),
        latest_user_request="这个 encoder 和 decoder 是怎么交互的？请结合原图解释。",
    )

    joined = "\n\n".join(messages)
    assert "Mounted paper turn guidance:" in joined
    assert "This turn is figure-grounded on an already mounted local paper." in joined
    assert "Inspect get_paper_detail first" in joined
    assert "call analyze_figures before answering" in joined
    assert (
        "Do not call get_paper_analysis / analyze_paper_rounds just to recover figure refs"
        in joined
    )


@REMOVED_NATIVE_ROUTE
def test_active_prompt_handoff_runs_callback_loop_inline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    FakeQueuedPromptLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeQueuedPromptLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "queued_prompt_inline_handoff_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    loop_calls: list[str] = []
    original_run = agent_service.SessionPromptProcessor._run_callback_loop.__func__

    def _capturing_run(cls, session_id, callback, *, resume_existing=False):  # noqa: ANN001, ANN202
        loop_calls.append(str(session_id))
        return original_run(
            cls,
            session_id,
            callback,
            resume_existing=resume_existing,
        )

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_run_callback_loop",
        classmethod(_capturing_run),
    )

    first_step_started = threading.Event()
    unsubscribe = session_bus.subscribe_all(
        lambda event: (
            first_step_started.set()
            if (
                event.type == SessionBusEvent.STEP_STARTED
                and str(event.properties.get("sessionID") or "")
                == "queued_prompt_inline_handoff_session"
            )
            else None
        )
    )
    results: dict[str, str] = {}

    def _run_prompt(name: str) -> None:
        response = client.post(
            "/session/queued_prompt_inline_handoff_session/message",
            json={
                "parts": [{"type": "text", "text": name}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results[name] = response.text

    try:
        first_thread = threading.Thread(target=lambda: _run_prompt("first"), daemon=True)
        second_thread = threading.Thread(target=lambda: _run_prompt("second"), daemon=True)
        first_thread.start()
        assert first_step_started.wait(timeout=2)
        second_thread.start()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
    finally:
        unsubscribe()

    assert "first-done" in results["first"]
    assert "second-saw-first" in results["second"]
    assert loop_calls == ["queued_prompt_inline_handoff_session"]


@REMOVED_NATIVE_ROUTE
def test_queued_prompts_catch_up_in_single_session_loop_and_share_final_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    FakeQueuedFifoLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeQueuedFifoLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "queued_prompt_fifo_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    events = []
    first_step_started = threading.Event()
    second_prompt_queued = threading.Event()

    def _record_event(event) -> None:  # noqa: ANN001
        events.append(event)
        if (
            event.type == SessionBusEvent.STEP_STARTED
            and str(event.properties.get("sessionID") or "") == "queued_prompt_fifo_session"
        ):
            first_step_started.set()
        if (
            event.type == SessionBusEvent.PROMPT_QUEUED
            and str(event.properties.get("sessionID") or "") == "queued_prompt_fifo_session"
        ):
            second_prompt_queued.set()

    unsubscribe = session_bus.subscribe_all(_record_event)
    results: dict[str, str] = {}

    def _run_prompt(name: str) -> None:
        response = client.post(
            "/session/queued_prompt_fifo_session/message",
            json={
                "parts": [{"type": "text", "text": name}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results[name] = response.text

    try:
        first_thread = threading.Thread(target=lambda: _run_prompt("first"), daemon=True)
        second_thread = threading.Thread(target=lambda: _run_prompt("second"), daemon=True)
        third_thread = threading.Thread(target=lambda: _run_prompt("third"), daemon=True)
        first_thread.start()
        assert first_step_started.wait(timeout=5)
        second_thread.start()
        assert second_prompt_queued.wait(timeout=5)
        third_thread.start()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
        third_thread.join(timeout=5)
    finally:
        unsubscribe()

    assert "first-done" in results["first"]
    assert "third-saw-first-and-second-user" in results["second"]
    assert "third-saw-first-and-second-user" in results["third"]
    assert FakeQueuedFifoLLM.calls == ["first", "third"]
    assert [event.type for event in events].count(SessionBusEvent.PROMPT_QUEUED) >= 2
    assert [event.type for event in events].count(SessionBusEvent.STATUS) >= 1
    assert [event.type for event in events].count(SessionBusEvent.IDLE) == 1

    history = client.get("/session/queued_prompt_fifo_session/message").json()
    assert [item["info"]["role"] for item in history] == [
        "user",
        "assistant",
        "user",
        "user",
        "assistant",
    ]
    assert history[1]["info"]["parentID"] == history[0]["info"]["id"]
    assert history[4]["info"]["parentID"] == history[3]["info"]["id"]
    assistant_texts = [
        "".join(
            str(part.get("text") or "")
            for part in item["parts"]
            if str(part.get("type") or "") == "text"
        )
        for item in history
        if str(item["info"]["role"] or "") == "assistant"
    ]
    assert assistant_texts == ["first-done", "third-saw-first-and-second-user"]


@REMOVED_NATIVE_ROUTE
def test_queued_prompt_is_rejected_when_active_prompt_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(agent_service, "LLMClient", FakeQueuedErrorLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "queued_prompt_error_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    first_step_started = threading.Event()
    unsubscribe = session_bus.subscribe_all(
        lambda event: (
            first_step_started.set()
            if (
                event.type == SessionBusEvent.STEP_STARTED
                and str(event.properties.get("sessionID") or "") == "queued_prompt_error_session"
            )
            else None
        )
    )
    results: dict[str, str] = {}

    def _run_prompt(name: str) -> None:
        response = client.post(
            "/session/queued_prompt_error_session/message",
            json={
                "parts": [{"type": "text", "text": name}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
        results[name] = response.text

    try:
        first_thread = threading.Thread(target=lambda: _run_prompt("first"), daemon=True)
        second_thread = threading.Thread(target=lambda: _run_prompt("second"), daemon=True)
        first_thread.start()
        assert first_step_started.wait(timeout=2)
        second_thread.start()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
    finally:
        unsubscribe()

    assert "first failed" in results["first"]
    assert "first failed" in results["second"]
    assert "should-not-run" not in results["second"]
    assert "event: done" in results["second"]


def test_queued_prompt_handoff_resolves_all_waiters_from_single_session_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    _reset_runtime_state()
    session_id = "queued_prompt_single_runner_session"
    owner = acquire_prompt_instance(session_id, wait=False)
    assert owner is not None

    first_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "first"},
    )
    second_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "second"},
    )
    third_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "third"},
    )

    turn_states = [
        {
            "request_message_id": "request_first",
            "has_pending_prompt": True,
        },
        {
            "request_message_id": "request_first",
            "assistant_message_id": "message_first",
            "latest_finished_assistant_id": "message_first",
            "has_pending_prompt": False,
        },
    ]

    class _FakePromptStream:
        def __init__(self, items: list[str], control: agent_service.PromptStreamControl):
            self._iterator = iter(items)
            self._researchos_prompt_control = control

        def __iter__(self):
            return self

        def __next__(self) -> str:
            return next(self._iterator)

    processor_calls: list[str] = []
    rebound_labels: list[str] = []

    class _Runner:
        def __init__(self, label: str):
            self.label = label

        def _rebind_from_callback(self, callback, *, resume_existing, manage_session_lifecycle):  # noqa: ANN001, ANN202
            del resume_existing, manage_session_lifecycle
            label = str((callback.payload or {}).get("label") or "")
            rebound_labels.append(label)
            self.label = label
            return self

        def _stream_active(self):  # noqa: ANN202
            label = self.label
            return _FakePromptStream(
                [
                    f'event: assistant_message_id\ndata: {{"message_id":"message_{label}"}}\n\n',
                    f'event: text_delta\ndata: {{"content":"{label}-output"}}\n\n',
                    "event: done\ndata: {}\n\n",
                ],
                agent_service.PromptStreamControl(
                    saw_done=True,
                    assistant_message_id=f"message_{label}",
                ),
            )

    def _fake_processor_from_callback(cls, callback, *, resume_existing, manage_session_lifecycle):  # noqa: ANN001, ANN202
        del cls, resume_existing, manage_session_lifecycle
        label = str((callback.payload or {}).get("label") or "")
        processor_calls.append(label)
        return _Runner(label)

    def _fake_turn_state(_session_id: str):  # noqa: ANN001, ANN202
        if turn_states:
            return dict(turn_states.pop(0))
        return {
            "request_message_id": "request_first",
            "assistant_message_id": "message_first",
            "latest_finished_assistant_id": "message_first",
            "has_pending_prompt": False,
        }

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_processor_from_callback",
        classmethod(_fake_processor_from_callback),
    )
    monkeypatch.setattr(agent_service, "_session_loop_turn_state", _fake_turn_state)

    try:
        resumed = agent_service.SessionPromptProcessor._resume_queued_callbacks(
            session_id,
            resume_existing=True,
        )
        assert resumed is True

        deadline = time.time() + 5
        while time.time() < deadline and not third_callback.closed:
            time.sleep(0.05)

        assert processor_calls == ["first"]
        assert rebound_labels == []
        assert first_callback.closed is True
        assert second_callback.closed is True
        assert third_callback.closed is True
        assert first_callback.result == {"messageID": "message_first"}
        assert second_callback.result == {"messageID": "message_first"}
        assert third_callback.result == {"messageID": "message_first"}
        assert first_callback.control is not None
        assert second_callback.control is not None
        assert third_callback.control is not None
        assert first_callback.items == []
        assert second_callback.items == []
        assert third_callback.items == []
        first_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(first_callback)
        )
        second_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(second_callback)
        )
        third_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(third_callback)
        )
        assert any("first-output" in item for item in first_items)
        assert any("first-output" in item for item in second_items)
        assert any("first-output" in item for item in third_items)
        while (
            time.time() < deadline and session_lifecycle.get_prompt_instance(session_id) is not None
        ):
            time.sleep(0.05)
        assert session_lifecycle.get_prompt_instance(session_id) is None
    finally:
        session_lifecycle.drain_prompt_callbacks(session_id)
        finish_prompt_instance(session_id)


def test_queued_prompt_resume_does_not_start_parallel_loops(
    monkeypatch: pytest.MonkeyPatch,
):
    _reset_runtime_state()
    session_id = "queued_prompt_single_worker_session"
    owner = acquire_prompt_instance(session_id, wait=False)
    assert owner is not None

    first_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "first"},
    )
    second_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "second"},
    )

    turn_states = [
        {
            "request_message_id": "request_first",
            "has_pending_prompt": True,
        },
        {
            "request_message_id": "request_first",
            "assistant_message_id": "message_first",
            "latest_finished_assistant_id": "message_first",
            "has_pending_prompt": False,
        },
    ]

    class _FakePromptStream:
        def __init__(self, items: list[str], control: agent_service.PromptStreamControl):
            self._iterator = iter(items)
            self._researchos_prompt_control = control

        def __iter__(self):
            return self

        def __next__(self) -> str:
            return next(self._iterator)

    processor_calls: list[str] = []
    rebound_labels: list[str] = []
    loop_labels: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    class _Runner:
        def __init__(self, label: str):
            self.label = label

        def _rebind_from_callback(self, callback, *, resume_existing, manage_session_lifecycle):  # noqa: ANN001, ANN202
            del resume_existing, manage_session_lifecycle
            label = str((callback.payload or {}).get("label") or "")
            rebound_labels.append(label)
            self.label = label
            return self

        def _stream_active(self):  # noqa: ANN202
            label = self.label
            if label == "first":
                first_started.set()
                assert release_first.wait(timeout=5)
            return _FakePromptStream(
                [
                    f'event: assistant_message_id\ndata: {{"message_id":"message_{label}"}}\n\n',
                    f'event: text_delta\ndata: {{"content":"{label}-output"}}\n\n',
                    "event: done\ndata: {}\n\n",
                ],
                agent_service.PromptStreamControl(
                    saw_done=True,
                    assistant_message_id=f"message_{label}",
                ),
            )

    def _fake_processor_from_callback(cls, callback, *, resume_existing, manage_session_lifecycle):  # noqa: ANN001, ANN202
        del cls, resume_existing, manage_session_lifecycle
        label = str((callback.payload or {}).get("label") or "")
        processor_calls.append(label)
        return _Runner(label)

    def _fake_turn_state(_session_id: str):  # noqa: ANN001, ANN202
        if turn_states:
            return dict(turn_states.pop(0))
        return {
            "request_message_id": "request_first",
            "assistant_message_id": "message_first",
            "latest_finished_assistant_id": "message_first",
            "has_pending_prompt": False,
        }

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_processor_from_callback",
        classmethod(_fake_processor_from_callback),
    )
    monkeypatch.setattr(agent_service, "_session_loop_turn_state", _fake_turn_state)
    original_run = agent_service.SessionPromptProcessor._run_callback_loop.__func__

    def _capturing_run(cls, session_id, callback, *, resume_existing=False):  # noqa: ANN001, ANN202
        label = str((callback.payload or {}).get("label") or "")
        loop_labels.append(label)
        return original_run(
            cls,
            session_id,
            callback,
            resume_existing=resume_existing,
        )

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_run_callback_loop",
        classmethod(_capturing_run),
    )

    try:
        resume_results: dict[str, bool] = {}

        def _resume_first() -> None:
            resume_results["first"] = agent_service.SessionPromptProcessor._resume_queued_callbacks(
                session_id,
                resume_existing=True,
            )

        first_thread = threading.Thread(target=_resume_first, daemon=True)
        first_thread.start()
        assert first_started.wait(timeout=2)

        second_resumed = agent_service.SessionPromptProcessor._resume_queued_callbacks(
            session_id,
            resume_existing=True,
        )
        assert second_resumed is True

        release_first.set()
        first_thread.join(timeout=5)
        assert resume_results == {"first": True}
        assert second_callback.wait_closed(timeout_ms=5000) is True

        assert processor_calls == ["first"]
        assert rebound_labels == []
        assert loop_labels == ["first"]
        assert first_callback.outcome == "resolved"
        assert second_callback.outcome == "resolved"
        assert first_callback.items == []
        assert second_callback.items == []
        first_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(first_callback)
        )
        second_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(second_callback)
        )
        assert any("first-output" in item for item in first_items)
        assert any("first-output" in item for item in second_items)
    finally:
        session_lifecycle.drain_prompt_callbacks(session_id)
        finish_prompt_instance(session_id)


def test_callback_stream_uses_saved_control_to_complete_tail():
    _reset_runtime_state()
    callback = session_lifecycle.queue_prompt_callback("queued_callback_control_session")
    callback.push('event: text_delta\ndata: {"content":"partial"}\n\n')
    callback.reject(
        None,
        control=agent_service.PromptStreamControl(
            error_message="callback failed",
        ),
    )

    items = list(agent_service.SessionPromptProcessor._iter_callback_stream(callback))

    assert any("partial" in item for item in items)
    assert any("event: error" in item and "callback failed" in item for item in items)
    assert items[-1] == "event: done\ndata: {}\n\n"


def test_prompt_event_stream_driver_accepts_structured_prompt_event_without_sse_parse(
    monkeypatch: pytest.MonkeyPatch,
):
    _reset_runtime_state()

    def _unexpected_parse(_raw):  # noqa: ANN001, ANN202
        raise AssertionError("structured prompt event should not be reparsed from SSE")

    monkeypatch.setattr(agent_service, "_parse_sse_event", _unexpected_parse)

    control = agent_service.PromptStreamControl()
    driver = agent_service.PromptEventStreamDriver(
        control=control,
        session_id="structured_prompt_event_session",
        lifecycle_kind="prompt",
        step_index=0,
    )

    items = list(
        driver.emit_raw(
            agent_service.PromptEvent(
                "text_delta",
                {
                    "id": "part_structured",
                    "content": "structured-output",
                },
            )
        )
    )

    assert len(items) == 1
    assert "event: text_delta" in items[0]
    assert "structured-output" in items[0]
    assert control.text_parts[0]["id"] == "part_structured"
    assert control.text_parts[0]["text"] == "structured-output"


def test_prompt_event_stream_driver_is_pure_observer_without_synthetic_mutation():
    _reset_runtime_state()
    control = agent_service.PromptStreamControl()

    driver = agent_service.PromptEventStreamDriver(
        control=control,
        session_id="prompt_event_driver_mutate_session",
        lifecycle_kind="prompt",
        step_index=0,
    )

    skipped = list(
        driver.emit_raw(
            agent_service.PromptEvent(
                "text_delta",
                {
                    "id": "part_real",
                    "content": "real-output",
                },
            ),
        )
    )

    assert len(skipped) == 1
    assert "real-output" in skipped[0]
    assert control.text_parts[-1]["text"] == "real-output"

    emitted = list(
        driver.emit_raw(
            agent_service.PromptEvent(
                "text_delta",
                {
                    "id": "part_real_2",
                    "content": "real-output-2",
                },
            )
        )
    )

    assert len(emitted) == 1
    assert "real-output-2" in emitted[0]
    assert "synthetic-output" not in emitted[0]


def test_reject_queued_callbacks_replays_error_without_buffered_tail():
    _reset_runtime_state()
    callback = session_lifecycle.queue_prompt_callback("queued_callback_reject_replay_session")

    agent_service.SessionPromptProcessor._reject_queued_callbacks(
        "queued_callback_reject_replay_session",
        "reject replay",
    )

    assert callback.closed is True
    assert callback.outcome == "rejected"
    assert callback.items == []

    items = list(agent_service.SessionPromptProcessor._iter_callback_stream(callback))

    assert any("event: error" in item and "reject replay" in item for item in items)
    assert items[-1] == "event: done\ndata: {}\n\n"


def test_callback_stream_reconstructs_terminal_tail_from_resolved_callback():
    _reset_runtime_state()
    callback = session_lifecycle.queue_prompt_callback("queued_callback_resolved_tail_session")
    callback.resolve(
        result={"messageID": "message_resolved_tail"},
        control=agent_service.PromptStreamControl(
            saw_done=True,
            assistant_message_id="message_resolved_tail",
        ),
    )

    items = list(agent_service.SessionPromptProcessor._iter_callback_stream(callback))

    assert len(items) == 2
    assert "event: assistant_message_id" in items[0]
    assert "message_resolved_tail" in items[0]
    assert items[1] == "event: done\ndata: {}\n\n"


def test_callback_stream_replays_resolved_message_when_raw_items_absent():
    _reset_runtime_state()
    callback = session_lifecycle.queue_prompt_callback("queued_callback_result_message_session")
    callback.resolve(
        result={
            "messageID": "message_resolved_result",
            "message": {
                "info": {"id": "message_resolved_result", "role": "assistant"},
                "parts": [
                    {"id": "part_text_one", "type": "text", "text": "第一段"},
                    {"id": "part_text_two", "type": "text", "text": "第二段"},
                ],
            },
        },
        control=agent_service.PromptStreamControl(
            saw_done=True,
            assistant_message_id="message_resolved_result",
        ),
    )

    items = list(agent_service.SessionPromptProcessor._iter_callback_stream(callback))

    assert len(items) == 4
    assert "event: assistant_message_id" in items[0]
    assert "message_resolved_result" in items[0]
    assert "event: text_delta" in items[1] and "第一段" in items[1]
    assert "part_text_one" in items[1]
    assert "event: text_delta" in items[2] and "第二段" in items[2]
    assert "part_text_two" in items[2]
    assert items[3] == "event: done\ndata: {}\n\n"


def test_callback_stream_backfills_text_from_resolved_message_when_raw_tail_lacks_text():
    _reset_runtime_state()
    callback = session_lifecycle.queue_prompt_callback(
        "queued_callback_partial_result_message_session"
    )
    callback.push('event: assistant_message_id\ndata: {"message_id":"message_partial_result"}\n\n')
    callback.resolve(
        result={
            "messageID": "message_partial_result",
            "message": {
                "info": {"id": "message_partial_result", "role": "assistant"},
                "parts": [
                    {"id": "part_text_partial", "type": "text", "text": "补回正文"},
                ],
            },
        },
        control=agent_service.PromptStreamControl(
            saw_done=True,
            assistant_message_id="message_partial_result",
        ),
    )

    items = list(agent_service.SessionPromptProcessor._iter_callback_stream(callback))

    assert len(items) == 3
    assert "event: assistant_message_id" in items[0]
    assert "message_partial_result" in items[0]
    assert "event: text_delta" in items[1] and "补回正文" in items[1]
    assert "part_text_partial" in items[1]
    assert items[2] == "event: done\ndata: {}\n\n"


def test_callback_stream_reconstructs_action_confirm_from_saved_control():
    _reset_runtime_state()
    callback = session_lifecycle.queue_prompt_callback("queued_callback_pause_tail_session")
    callback.resolve(
        result={
            "messageID": "message_pause_tail",
            "message": {
                "info": {"id": "message_pause_tail", "role": "assistant"},
                "parts": [
                    {"id": "part_pause_text", "type": "text", "text": "先暂停一下"},
                ],
            },
        },
        control=agent_service.PromptStreamControl(
            saw_done=True,
            paused=True,
            assistant_message_id="message_pause_tail",
            action_confirm={
                "id": "permission_pause_tail",
                "tool": "bash",
                "description": "需要确认",
                "assistant_message_id": "message_pause_tail",
            },
        ),
    )

    items = list(agent_service.SessionPromptProcessor._iter_callback_stream(callback))

    assert len(items) == 4
    assert "event: assistant_message_id" in items[0]
    assert "message_pause_tail" in items[0]
    assert "event: text_delta" in items[1] and "先暂停一下" in items[1]
    assert "event: action_confirm" in items[2]
    assert "permission_pause_tail" in items[2]
    assert items[3] == "event: done\ndata: {}\n\n"


def test_prompt_callback_exposes_resolve_reject_outcomes():
    _reset_runtime_state()

    resolved = session_lifecycle.queue_prompt_callback("queued_callback_resolved_session")
    resolved.resolve(
        result={"messageID": "message_resolved"},
        control=agent_service.PromptStreamControl(
            saw_done=True,
            assistant_message_id="message_resolved",
        ),
    )
    assert resolved.wait_closed(timeout_ms=50) is True
    assert resolved.closed is True
    assert resolved.outcome == "resolved"
    assert resolved.error is None
    assert resolved.result == {"messageID": "message_resolved"}
    assert resolved.control is not None

    rejected = session_lifecycle.queue_prompt_callback("queued_callback_rejected_session")
    rejected.reject(
        "callback rejected",
        control=agent_service.PromptStreamControl(
            saw_done=True,
            error_message="callback rejected",
        ),
    )
    assert rejected.wait_closed(timeout_ms=50) is True
    assert rejected.closed is True
    assert rejected.outcome == "rejected"
    assert rejected.error == "callback rejected"
    assert rejected.result is None
    assert rejected.control is not None


def test_prompt_callback_loop_claim_is_single_owner():
    _reset_runtime_state()
    session_lifecycle.queue_prompt_callback(
        "queued_callback_loop_claim_session", payload={"label": "first"}
    )
    session_lifecycle.queue_prompt_callback(
        "queued_callback_loop_claim_session", payload={"label": "second"}
    )

    status, first = claim_prompt_callback("queued_callback_loop_claim_session")
    assert status == "started"
    assert first is not None
    assert str((first.payload or {}).get("label") or "") == "first"

    status_running, callback_running = claim_prompt_callback("queued_callback_loop_claim_session")
    assert status_running == "running"
    assert callback_running is None

    queued = session_lifecycle.queued_prompt_callbacks("queued_callback_loop_claim_session")
    assert len(queued) == 1
    assert str((queued[0].payload or {}).get("label") or "") == "second"


def test_callback_loop_claim_respects_active_prompt_instance_as_single_owner():
    _reset_runtime_state()
    owner = acquire_prompt_instance("queued_callback_prompt_owner_session", wait=False)
    assert owner is not None
    session_lifecycle.mark_prompt_instance_running(
        "queued_callback_prompt_owner_session", loop_kind="prompt"
    )
    callback = session_lifecycle.queue_prompt_callback(
        "queued_callback_prompt_owner_session",
        payload={"label": "next"},
    )

    status, claimed = claim_prompt_callback("queued_callback_prompt_owner_session")

    assert status == "running"
    assert claimed is None
    queued = session_lifecycle.queued_prompt_callbacks("queued_callback_prompt_owner_session")
    assert queued == [callback]


def test_callback_loop_claim_can_resume_paused_prompt_owner():
    _reset_runtime_state()
    owner = acquire_prompt_instance("queued_callback_paused_owner_session", wait=False)
    assert owner is not None
    pause_prompt_instance("queued_callback_paused_owner_session")

    callback = session_lifecycle.queue_prompt_callback(
        "queued_callback_paused_owner_session",
        payload={"label": "next"},
    )

    status, claimed = claim_prompt_callback("queued_callback_paused_owner_session")

    assert status == "started"
    assert claimed is callback
    instance = session_lifecycle.get_prompt_instance("queued_callback_paused_owner_session")
    assert instance is not None
    assert instance.loop_kind == "prompt"
    assert instance.running is True


def test_session_lifecycle_no_longer_exposes_callback_runner_helpers():
    _reset_runtime_state()

    assert hasattr(session_lifecycle, "claim_prompt_callback")
    assert hasattr(session_lifecycle, "queue_prompt_callback")
    assert hasattr(session_lifecycle, "pause_prompt_instance")
    assert hasattr(session_lifecycle, "claim_prompt_callback_loop") is False
    assert hasattr(session_lifecycle, "advance_prompt_callback_loop") is False
    assert hasattr(session_lifecycle, "release_prompt_callback_loop") is False
    assert hasattr(session_lifecycle, "settle_prompt_callback_loop") is False
    assert hasattr(session_lifecycle, "run_prompt_callback_loop") is False
    assert hasattr(session_lifecycle, "PromptCallbackLoopHooks") is False
    assert hasattr(session_lifecycle, "PromptSettlement") is False
    assert hasattr(session_lifecycle, "settle_prompt_instance") is False
    assert hasattr(session_lifecycle, "handoff_or_finish_prompt_instance") is False
    assert hasattr(session_lifecycle, "reject_callbacks_and_finish_prompt_instance") is False
    assert hasattr(session_lifecycle, "drain_callbacks_and_finish_prompt_instance") is False


def test_prompt_instance_handoff_claims_callback_before_finishing_owner():
    _reset_runtime_state()
    owner = acquire_prompt_instance("queued_callback_handoff_finish_session", wait=False)
    assert owner is not None
    waiter = register_prompt_waiter("queued_callback_handoff_finish_session")
    callback = session_lifecycle.queue_prompt_callback(
        "queued_callback_handoff_finish_session",
        payload={"label": "next"},
    )

    pause_prompt_instance("queued_callback_handoff_finish_session")
    status, handed_off = claim_prompt_callback("queued_callback_handoff_finish_session")

    assert status == "started"
    assert handed_off is callback
    assert (
        session_lifecycle.get_prompt_instance("queued_callback_handoff_finish_session") is not None
    )
    assert waiter.event.is_set() is False

    pause_prompt_instance("queued_callback_handoff_finish_session")
    finish_prompt_instance("queued_callback_handoff_finish_session")


def test_finish_prompt_instance_finishes_when_queue_empty():
    _reset_runtime_state()
    owner = acquire_prompt_instance("queued_callback_finish_only_session", wait=False)
    assert owner is not None
    waiter = register_prompt_waiter("queued_callback_finish_only_session")

    finish_prompt_instance(
        "queued_callback_finish_only_session",
        result={"messageID": "message_finish_only"},
    )

    assert session_lifecycle.get_prompt_instance("queued_callback_finish_only_session") is None
    assert wait_for_prompt_completion(waiter, timeout_ms=100) == {
        "messageID": "message_finish_only"
    }


def test_drain_prompt_callbacks_then_finish_prompt_instance_drains_queue_atomically():
    _reset_runtime_state()
    owner = acquire_prompt_instance("queued_callback_reject_finish_session", wait=False)
    assert owner is not None
    waiter = register_prompt_waiter("queued_callback_reject_finish_session")
    first = session_lifecycle.queue_prompt_callback(
        "queued_callback_reject_finish_session", payload={"label": "first"}
    )
    second = session_lifecycle.queue_prompt_callback(
        "queued_callback_reject_finish_session", payload={"label": "second"}
    )

    rejected = drain_prompt_callbacks("queued_callback_reject_finish_session")
    finish_prompt_instance(
        "queued_callback_reject_finish_session",
        result={"messageID": "message_reject_finish"},
    )

    assert rejected == [first, second]
    assert session_lifecycle.get_prompt_instance("queued_callback_reject_finish_session") is None
    assert session_lifecycle.queued_prompt_callbacks("queued_callback_reject_finish_session") == []
    assert wait_for_prompt_completion(waiter, timeout_ms=100) == {
        "messageID": "message_reject_finish"
    }


def test_queued_prompt_resolves_waiters_from_latest_finished_message_when_no_pending_turn(
    monkeypatch: pytest.MonkeyPatch,
):
    _reset_runtime_state()
    session_id = "queued_prompt_latest_result_session"
    owner = acquire_prompt_instance(session_id, wait=False)
    assert owner is not None

    first_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "first"},
    )
    second_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "second"},
    )
    third_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "third"},
    )
    monkeypatch.setattr(
        agent_service,
        "_session_loop_turn_state",
        lambda _session_id: {
            "request_message_id": "request_latest",
            "assistant_message_id": "message_latest",
            "latest_finished_assistant_id": "message_latest",
            "has_pending_prompt": False,
        },
    )
    monkeypatch.setattr(
        agent_service,
        "_prompt_result_payload",
        lambda _session_id, _message_id: {
            "messageID": "message_latest",
            "message": {
                "info": {"id": "message_latest", "role": "assistant"},
                "parts": [
                    {"id": "part_latest_text", "type": "text", "text": "latest-output"},
                ],
            },
        },
    )
    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_processor_from_callback",
        classmethod(
            lambda *_args, **_kwargs: pytest.fail("processor should not run when no pending turn")
        ),
    )

    try:
        resumed = agent_service.SessionPromptProcessor._resume_queued_callbacks(
            session_id,
            resume_existing=True,
        )
        assert resumed is True

        deadline = time.time() + 5
        while time.time() < deadline and not third_callback.closed:
            time.sleep(0.05)

        assert first_callback.closed is True
        assert second_callback.closed is True
        assert third_callback.closed is True
        assert first_callback.items == []
        assert second_callback.items == []
        assert third_callback.items == []
        assert first_callback.result == {
            "messageID": "message_latest",
            "message": {
                "info": {"id": "message_latest", "role": "assistant"},
                "parts": [{"id": "part_latest_text", "type": "text", "text": "latest-output"}],
            },
        }
        assert second_callback.result == first_callback.result
        assert third_callback.result == first_callback.result
        assert first_callback.control is None
        assert second_callback.control is None
        assert third_callback.control is None
        first_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(first_callback)
        )
        second_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(second_callback)
        )
        third_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(third_callback)
        )
        assert any("latest-output" in item for item in first_items)
        assert any("latest-output" in item for item in second_items)
        assert any("latest-output" in item for item in third_items)
        while (
            time.time() < deadline and session_lifecycle.get_prompt_instance(session_id) is not None
        ):
            time.sleep(0.05)
        assert session_lifecycle.get_prompt_instance(session_id) is None
    finally:
        session_lifecycle.drain_prompt_callbacks(session_id)
        finish_prompt_instance(session_id)


def test_queued_prompt_pause_does_not_consume_later_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _reset_runtime_state()
    session_id = "queued_prompt_pause_session"
    owner = acquire_prompt_instance(session_id, wait=False)
    assert owner is not None

    first_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "first"},
    )
    second_callback = session_lifecycle.queue_prompt_callback(
        session_id,
        payload={"label": "second"},
    )
    monkeypatch.setattr(
        agent_service,
        "_session_loop_turn_state",
        lambda _session_id: {
            "request_message_id": "request_pause",
            "has_pending_prompt": True,
        },
    )

    class _FakePromptStream:
        def __init__(self, items: list[str], control: agent_service.PromptStreamControl):
            self._iterator = iter(items)
            self._researchos_prompt_control = control

        def __iter__(self):
            return self

        def __next__(self) -> str:
            return next(self._iterator)

    def _fake_processor_from_callback(cls, callback, *, resume_existing, manage_session_lifecycle):  # noqa: ANN001, ANN202
        del cls, resume_existing, manage_session_lifecycle
        label = str((callback.payload or {}).get("label") or "")

        class _FakeProcessor:
            def _stream_active(self_nonlocal):  # noqa: ANN001, ANN202
                if label == "first":
                    return _FakePromptStream(
                        [
                            'event: assistant_message_id\ndata: {"message_id":"message_pause_first"}\n\n',
                            (
                                "event: action_confirm\ndata: "
                                '{"id":"permission_pause_1","assistant_message_id":"message_pause_first"}\n\n'
                            ),
                            "event: done\ndata: {}\n\n",
                        ],
                        agent_service.PromptStreamControl(
                            saw_done=True,
                            paused=True,
                            assistant_message_id="message_pause_first",
                        ),
                    )
                return _FakePromptStream(
                    [
                        'event: assistant_message_id\ndata: {"message_id":"message_pause_second"}\n\n',
                        'event: text_delta\ndata: {"content":"should-not-run"}\n\n',
                        "event: done\ndata: {}\n\n",
                    ],
                    agent_service.PromptStreamControl(
                        saw_done=True,
                        assistant_message_id="message_pause_second",
                    ),
                )

        return _FakeProcessor()

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "_processor_from_callback",
        classmethod(_fake_processor_from_callback),
    )

    try:
        resumed = agent_service.SessionPromptProcessor._resume_queued_callbacks(
            session_id,
            resume_existing=True,
        )
        assert resumed is True

        deadline = time.time() + 5
        while time.time() < deadline and not first_callback.closed:
            time.sleep(0.05)

        assert first_callback.closed is True
        assert first_callback.items == []
        assert first_callback.control is not None
        assert first_callback.control.paused is True
        assert first_callback.result == {"messageID": "message_pause_first"}
        first_items = list(
            agent_service.SessionPromptProcessor._iter_callback_stream(first_callback)
        )
        assert any("event: action_confirm" in item for item in first_items)
        assert second_callback.closed is False
        assert second_callback.items == []
        assert second_callback.control is None
        assert second_callback.result is None
        assert len(session_lifecycle.queued_prompt_callbacks(session_id)) == 1
        assert session_lifecycle.get_prompt_instance(session_id) is not None
    finally:
        session_lifecycle.drain_prompt_callbacks(session_id)
        finish_prompt_instance(session_id)


def test_streaming_parts_publish_delta_events_with_stable_part_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_delta_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "delta_prompt_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    events = []
    unsubscribe = session_bus.subscribe_all(lambda event: events.append(event))
    try:
        prompt_resp = client.post(
            "/session/delta_prompt_session/message",
            json={
                "parts": [{"type": "text", "text": "继续"}],
                "mode": "build",
                "workspace_path": str(tmp_path),
            },
        )
    finally:
        unsubscribe()

    assert prompt_resp.status_code == 200
    assert "最终回答" in prompt_resp.text

    placeholder_parts = {
        str(event.properties["part"]["id"]): dict(event.properties["part"])
        for event in events
        if event.type == SessionBusEvent.PART_UPDATED
        and isinstance(event.properties.get("part"), dict)
        and str((event.properties.get("part") or {}).get("type") or "") in {"text", "reasoning"}
        and str((event.properties.get("part") or {}).get("text") or "") == ""
    }
    delta_by_part: dict[str, str] = defaultdict(str)
    for event in events:
        if event.type != SessionBusEvent.PART_DELTA:
            continue
        assert str(event.properties.get("messageID") or "").strip() != ""
        assert str(event.properties.get("field") or "") == "text"
        delta_by_part[str(event.properties.get("partID") or "")] += str(
            event.properties.get("delta") or ""
        )

    assert placeholder_parts
    assert delta_by_part

    history = client.get("/session/delta_prompt_session/message").json()
    assistant = history[1]
    assistant_parts = {
        str(part.get("id") or ""): part
        for part in assistant["parts"]
        if str(part.get("type") or "") in {"text", "reasoning"}
    }
    assert assistant_parts

    for part_id, delta_text in delta_by_part.items():
        assert part_id in placeholder_parts
        assert part_id in assistant_parts
        assert delta_text == str(assistant_parts[part_id].get("text") or "")
