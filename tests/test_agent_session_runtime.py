from __future__ import annotations

import copy
import json
import threading
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
    session_lifecycle,
    session_message_v2,
    session_plan,
)
from packages.agent import (
    session_runtime as session_runtime_module,
)
from packages.agent.runtime.agent_runtime_state import ensure_session, get_todos, update_todos
from packages.agent.session.session_runtime import (
    append_session_message,
    ensure_session_record,
    get_session_turn_state,
    list_session_messages,
    load_agent_messages,
    request_session_abort,
    wrap_stream_with_persistence,
)
from packages.integrations.llm_client import StreamEvent
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import AgentProjectRepository, AgentSessionMessageRepository

REMOVED_NATIVE_ROUTE = pytest.mark.skip(
    reason="部分 native 专项回归尚未恢复；仅保留当前未纳入双栈修复范围的用例。"
)


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


def _wrap_persisted_if_needed(chunks: list[str], kwargs: dict) -> object:
    persistence = kwargs.get("persistence")
    if persistence is None:
        return iter(chunks)
    return wrap_stream_with_persistence(
        iter(chunks),
        session_id=persistence.session_id,
        parent_id=persistence.parent_id,
        assistant_meta=copy.deepcopy(persistence.assistant_meta),
        assistant_message_id=persistence.assistant_message_id,
    )


def _fake_stream_chat(*_args, **kwargs):
    return _wrap_persisted_if_needed(
        [
            'event: text_delta\ndata: {"content":"已完成测试回复。"}\n\n',
            "event: done\ndata: {}\n\n",
        ],
        kwargs,
    )


def _fake_reasoning_stream_chat(*_args, **kwargs):
    return _wrap_persisted_if_needed(
        [
            'event: session_step_start\ndata: {"step":1}\n\n',
            'event: reasoning_delta\ndata: {"content":"先梳理已知条件。"}\n\n',
            'event: text_delta\ndata: {"content":"这是最终回答。"}\n\n',
            'event: usage\ndata: {"model":"fake-reasoning","input_tokens":10,"output_tokens":6,"reasoning_tokens":8}\n\n',
            (
                "event: session_step_finish\ndata: "
                '{"step":1,"reason":"stop","usage":{"input_tokens":10,"output_tokens":6,"reasoning_tokens":8},"cost":0}\n\n'
            ),
            "event: done\ndata: {}\n\n",
        ],
        kwargs,
    )


def _fake_tool_abort_stream_chat(*_args, **kwargs):
    request_session_abort("session_abort_tool_test")
    return _wrap_persisted_if_needed(
        [
            'event: session_step_start\ndata: {"step":1}\n\n',
            'event: tool_start\ndata: {"id":"call_abort_tool_1","name":"bash","args":{"command":"ls"}}\n\n',
            "event: done\ndata: {}\n\n",
        ],
        kwargs,
    )


def test_normalize_messages_injects_hard_output_constraint_prompt(monkeypatch: pytest.MonkeyPatch):
    from packages.agent import agent_service

    monkeypatch.setattr(agent_service, "list_workspace_roots", lambda: [])
    monkeypatch.setattr(agent_service, "get_todos", lambda _session_id: [])
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "default"}
    )
    monkeypatch.setattr(agent_service, "get_local_skill_detail", lambda *_args, **_kwargs: None)

    normalized = agent_service._normalize_messages(
        [
            {
                "role": "user",
                "content": "请用简体中文回答：用不超过120字，总结把 callback/persistence 从 SSE wrapper 往 processor 内收的两个主要好处。",
            }
        ],
        agent_service.AgentRuntimeOptions(session_id="constraint_prompt_session", mode="build"),
    )

    system_messages = [
        str(message.get("content") or "")
        for message in normalized
        if str(message.get("role") or "") == "system"
    ]
    assert any("硬约束" in message and "不超过120字" in message for message in system_messages)


def test_normalize_messages_includes_opencode_provider_environment_and_skills_sections(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.agent import agent_service

    monkeypatch.setattr(
        agent_service,
        "_resolve_current_model_identity",
        lambda _options: {"providerID": "openai", "modelID": "gpt-5"},
    )
    monkeypatch.setattr(agent_service, "list_workspace_roots", lambda: [])
    monkeypatch.setattr(agent_service, "get_todos", lambda _session_id: [])
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "default"}
    )
    monkeypatch.setattr(agent_service, "get_local_skill_detail", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent_service,
        "list_local_skills",
        lambda: [{"id": "skill.alpha", "name": "skill.alpha", "description": "Alpha workflow"}],
    )
    monkeypatch.setattr(
        agent_service,
        "get_session_record",
        lambda _session_id: {"mode": "build", "permission": None},
    )

    normalized = agent_service._normalize_messages(
        [{"role": "user", "content": "continue"}],
        agent_service.AgentRuntimeOptions(
            session_id="opencode_prompt_session",
            mode="build",
            active_skill_ids=["skill.alpha"],
        ),
    )

    system_messages = [
        str(message.get("content") or "")
        for message in normalized
        if str(message.get("role") or "") == "system"
    ]
    assert any("You are OpenCode" in message for message in system_messages)
    assert any(
        "<env>" in message and "Workspace root folder" in message for message in system_messages
    )
    assert any("Skills are optional workflow templates" in message for message in system_messages)
    assert not any("当前待办" in message for message in system_messages)
    assert not any("当前已保存工作区" in message for message in system_messages)
    assert not any("当前权限" in message for message in system_messages)


def test_prepare_loop_messages_injects_opencode_plan_reminder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    session = ensure_session_record(
        session_id="plan_reminder_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="plan",
    )
    plan_info = session_plan.resolve_session_plan_info(session)
    assert plan_info is not None

    prepared = agent_service._prepare_loop_messages(
        [{"role": "user", "content": "先分析，不要执行"}],
        agent_service.AgentRuntimeOptions(session_id="plan_reminder_session", mode="plan"),
        current_step=0,
        max_steps=3,
    )

    assert len(prepared) == 1
    assert prepared[0]["role"] == "user"
    assert "Plan mode is active" in str(prepared[0]["content"])
    assert "The only allowed write target is the plan file below." in str(prepared[0]["content"])
    assert "question tool" in str(prepared[0]["content"])
    assert plan_info.path in str(prepared[0]["content"])
    assert "plan_exit" in str(prepared[0]["content"])


def test_build_plan_mode_reminder_materializes_local_plan_parent_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    session = ensure_session_record(
        session_id="plan_parent_materialize_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="plan",
    )
    plan_info = session_plan.resolve_session_plan_info(session)
    assert plan_info is not None
    plan_parent = Path(plan_info.path).parent
    assert not plan_parent.exists()

    reminder = session_plan.build_plan_mode_reminder(session)

    assert plan_parent.is_dir()
    assert plan_info.path in reminder


def test_prepare_loop_messages_injects_build_switch_reminder_after_plan(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.agent import agent_service

    monkeypatch.setattr(
        agent_service,
        "list_session_messages",
        lambda *_args, **_kwargs: [
            {
                "info": {
                    "role": "assistant",
                    "mode": "plan",
                }
            }
        ],
    )

    prepared = agent_service._prepare_loop_messages(
        [{"role": "user", "content": "开始实现"}],
        agent_service.AgentRuntimeOptions(session_id="build_switch_session", mode="build"),
        current_step=0,
        max_steps=3,
    )

    assert len(prepared) == 1
    assert prepared[0]["role"] == "user"
    assert "operational mode has changed from plan to build" in str(prepared[0]["content"])


def test_session_message_route_passes_agent_backend_and_active_skill_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    captured: dict[str, object] = {}

    def _fake_stream_chat(_messages, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return _wrap_persisted_if_needed(
            [
                'event: text_delta\ndata: {"content":"route ok"}\n\n',
                "event: done\ndata: {}\n\n",
            ],
            kwargs,
        )

    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_stream_chat)

    created = client.post(
        "/session",
        json={
            "id": "session_route_backend_forward_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/session/session_route_backend_forward_session/message",
        json={
            "parts": [{"type": "text", "text": "continue"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "agent_backend_id": "custom_acp",
            "active_skill_ids": ["skill.alpha", "skill.beta"],
            "reasoning_level": "high",
        },
    )

    assert response.status_code == 200
    assert "route ok" in response.text
    assert captured["agent_backend_id"] == "custom_acp"
    assert captured["active_skill_ids"] == ["skill.alpha", "skill.beta"]
    assert captured["reasoning_level"] == "high"

    history = client.get("/session/session_route_backend_forward_session/message").json()
    assert history[0]["info"]["activeSkillIDs"] == ["skill.alpha", "skill.beta"]


def test_session_create_and_prompt_route_persist_backend_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_backend_persistence_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
            "agent_backend_id": "claw",
        },
    )

    assert created.status_code == 200
    assert created.json()["agent_backend_id"] == "claw"

    captured: dict[str, object] = {}

    def _fake_stream_chat(_messages, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return _wrap_persisted_if_needed(
            [
                'event: text_delta\ndata: {"content":"route ok"}\n\n',
                "event: done\ndata: {}\n\n",
            ],
            kwargs,
        )

    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_stream_chat)

    response = client.post(
        "/session/session_backend_persistence_session/message",
        json={
            "parts": [{"type": "text", "text": "continue"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert response.status_code == 200
    assert captured["agent_backend_id"] is None

    session_payload = client.get("/session/session_backend_persistence_session").json()
    assert session_payload["agent_backend_id"] == "claw"


class FakeConstraintRepairLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del max_tokens, variant_override, model_override, session_cache_key
        if tools is None and any(
            "你负责压缩回答" in str(item.get("content") or "") for item in messages
        ):
            yield StreamEvent(type="text_delta", content="解耦传输层，集中持久化。")
            return
        yield StreamEvent(
            type="text_delta", content="这是一个明显超过二十字限制的回答，用来触发压缩修复流程。"
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=8, output_tokens=12)


@REMOVED_NATIVE_ROUTE
def test_stream_chat_repairs_explicit_length_constraint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    monkeypatch.setattr(agent_service, "LLMClient", FakeConstraintRepairLLM)

    body = "".join(
        agent_service.stream_chat(
            [{"role": "user", "content": "请用不超过20字总结本次改动。"}],
            session_id="constraint_repair_session",
            mode="build",
            workspace_path=str(tmp_path),
        )
    )

    text = ""
    for chunk in body.split("\n\n"):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines or lines[0] != "event: text_delta":
            continue
        payload = json.loads(lines[1].split(":", 1)[1].strip())
        text += str(payload.get("content") or "")

    assert text == "解耦传输层，集中持久化。"
    assert len(text) <= 20


@REMOVED_NATIVE_ROUTE
def test_stream_chat_injects_opencode_max_steps_prompt_on_last_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    FakeMaxStepsPromptLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeMaxStepsPromptLLM)
    monkeypatch.setattr(agent_service, "_get_max_tool_steps", lambda: 2)
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"}
    )

    def _fake_execute_tool_stream(_name, _arguments, context=None):  # noqa: ANN001, ANN202
        del context
        yield agent_service.ToolResult(
            success=True,
            summary="命令执行成功",
            data={"stdout": "step"},
        )

    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)

    body = "".join(
        agent_service.stream_chat(
            [{"role": "user", "content": "继续处理"}],
            session_id="max_steps_prompt_session",
            mode="build",
            workspace_path=str(tmp_path),
        )
    )

    assert "已达到最大步数，停止继续调用工具。" in body
    assert len(FakeMaxStepsPromptLLM.calls) == 2
    assert not any(
        "CRITICAL - MAXIMUM STEPS REACHED" in str(item.get("content") or "")
        for item in FakeMaxStepsPromptLLM.calls[0]
        if str(item.get("role") or "") == "assistant"
    )
    assert any(
        "CRITICAL - MAXIMUM STEPS REACHED" in str(item.get("content") or "")
        for item in FakeMaxStepsPromptLLM.calls[1]
        if str(item.get("role") or "") == "assistant"
    )


@REMOVED_NATIVE_ROUTE
def test_session_prompt_route_reloads_latest_transcript_between_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())
    FakeReloadBetweenStepsLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeReloadBetweenStepsLLM)
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"}
    )

    def _fake_execute_tool_stream(_name, _arguments, context=None):  # noqa: ANN001, ANN202
        del context
        append_session_message(
            session_id="reload_between_steps_session",
            role="user",
            content="第二条用户消息",
        )
        yield agent_service.ToolResult(
            success=True,
            summary="命令执行成功",
            data={"stdout": "reload"},
        )

    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)

    created = client.post(
        "/session",
        json={
            "id": "reload_between_steps_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/session/reload_between_steps_session/message",
        json={
            "parts": [{"type": "text", "text": "第一条用户消息"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )

    assert response.status_code == 200
    assert "看到了新的用户消息。" in response.text
    assert len(FakeReloadBetweenStepsLLM.calls) == 2
    assert not any(
        "第二条用户消息" in str(item.get("content") or "")
        for item in FakeReloadBetweenStepsLLM.calls[0]
        if str(item.get("role") or "") == "user"
    )
    assert any(
        "第二条用户消息" in str(item.get("content") or "")
        for item in FakeReloadBetweenStepsLLM.calls[1]
        if str(item.get("role") or "") == "user"
    )


@REMOVED_NATIVE_ROUTE
def test_plan_exit_confirmation_switches_to_build_and_reloads_transcript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    session_lifecycle.reset_for_tests()
    client = TestClient(_build_app())
    FakePlanModeTransitionLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakePlanModeTransitionLLM)
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"}
    )

    created = client.post(
        "/session",
        json={
            "id": "plan_exit_transition_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "plan",
        },
    )
    assert created.status_code == 200
    session_payload = created.json()
    plan_info = session_plan.resolve_session_plan_info(session_payload)
    assert plan_info is not None
    Path(plan_info.path).parent.mkdir(parents=True, exist_ok=True)
    Path(plan_info.path).write_text("# Plan\n\n- Implement it\n", encoding="utf-8")

    prompt_resp = client.post(
        "/session/plan_exit_transition_session/message",
        json={
            "parts": [{"type": "text", "text": "先分析，不要执行"}],
            "mode": "plan",
            "workspace_path": str(tmp_path),
        },
    )

    assert prompt_resp.status_code == 200
    assert "action_confirm" in prompt_resp.text

    permissions = client.get("/session/plan_exit_transition_session/permissions").json()
    assert len(permissions) == 1
    assert permissions[0]["permission"] == "plan"
    metadata = (
        permissions[0].get("metadata") if isinstance(permissions[0].get("metadata"), dict) else {}
    )
    assert "切换到 build 模式" in str(metadata.get("title") or "")

    permission_id = permissions[0]["id"]
    reply_resp = client.post(
        f"/session/plan_exit_transition_session/permissions/{permission_id}",
        json={"response": "once"},
    )

    assert reply_resp.status_code == 200
    assert "开始执行计划" in reply_resp.text

    reloaded_session = client.get("/session/plan_exit_transition_session").json()
    assert reloaded_session["mode"] == "build"

    history = client.get("/session/plan_exit_transition_session/message").json()
    assert any(
        msg["info"]["role"] == "user"
        and "approved" in "".join(str(part.get("text") or "") for part in msg.get("parts") or [])
        for msg in history
    )
    assert any(
        msg["info"]["role"] == "assistant"
        and str(msg["info"].get("mode") or "") == "build"
        and "开始执行计划"
        in "".join(str(part.get("text") or "") for part in msg.get("parts") or [])
        for msg in history
    )
    assert len(FakePlanModeTransitionLLM.calls) >= 2
    assert any(
        str(item.get("role") or "") == "user"
        and "has been approved" in str(item.get("content") or "")
        for item in FakePlanModeTransitionLLM.calls[1]
    )


class FakeToolContinuationLLM:
    calls: list[list[dict]] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return type("ModelTarget", (), {"provider": "openai", "model": "gpt-4o"})()

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
        FakeToolContinuationLLM.calls.append(copy.deepcopy(messages))
        has_tool_result = any(
            str(item.get("role") or "") == "tool"
            and "命令执行成功" in str(item.get("content") or "")
            for item in messages
        )
        if not has_tool_result:
            yield StreamEvent(
                type="tool_call",
                tool_call_id="call_continue_tool_1",
                tool_name="bash",
                tool_arguments='{"command":"echo ok"}',
            )
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=10, output_tokens=4
            )
            return

        yield StreamEvent(type="text_delta", content="工具后继续完成。")
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=12, output_tokens=8)


class FakeLocalShellContinuationLLM:
    calls: list[list[dict]] = []
    working_directory: str = ""

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return type(
            "ModelTarget",
            (),
            {
                "provider": "openai",
                "model": "gpt-5.2",
                "base_url": "https://api.openai.com/v1",
            },
        )()

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
        FakeLocalShellContinuationLLM.calls.append(copy.deepcopy(messages))
        has_tool_result = any(
            str(item.get("role") or "") == "tool"
            and "local-shell-ok" in str(item.get("content") or "")
            for item in messages
        )
        if not has_tool_result:
            yield StreamEvent(
                type="tool_call",
                tool_call_id="call_local_shell_continue_1",
                tool_name="local_shell",
                tool_arguments=json.dumps(
                    {
                        "action": {
                            "type": "exec",
                            "command": ["Write-Output", "local-shell-ok"],
                            "workingDirectory": FakeLocalShellContinuationLLM.working_directory,
                        }
                    },
                    ensure_ascii=False,
                ),
            )
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=10, output_tokens=4
            )
            return

        yield StreamEvent(type="text_delta", content="local_shell 后继续完成。")
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=12, output_tokens=8)


class FakeBoundaryLifecycleLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
        yield StreamEvent(type="reasoning_delta", content="先分析")
        yield StreamEvent(type="reasoning_delta", content="上下文。")
        yield StreamEvent(type="text_delta", content="这是")
        yield StreamEvent(type="text_delta", content="最终回答。")
        yield StreamEvent(
            type="usage",
            model="fake-chat-model",
            input_tokens=10,
            output_tokens=6,
            reasoning_tokens=8,
        )


class FakeReasoningAsciiSpacingLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
        yield StreamEvent(type="reasoning_delta", content="First, we")
        yield StreamEvent(type="reasoning_delta", content="inspect")
        yield StreamEvent(type="reasoning_delta", content="the")
        yield StreamEvent(type="reasoning_delta", content="repo.")
        yield StreamEvent(type="text_delta", content="done")
        yield StreamEvent(
            type="usage",
            model="fake-chat-model",
            input_tokens=10,
            output_tokens=6,
            reasoning_tokens=8,
        )


class FakeReasoningMetadataLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
            type="reasoning_delta",
            content="",
            part_id="rs_agent:0",
            metadata={
                "openai": {
                    "itemId": "rs_agent",
                    "reasoningEncryptedContent": "enc-agent",
                }
            },
        )
        yield StreamEvent(type="text_delta", content="最终回答。")
        yield StreamEvent(
            type="usage",
            model="fake-chat-model",
            input_tokens=10,
            output_tokens=6,
            reasoning_tokens=8,
        )


class FakeTextMetadataLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
            type="text_delta",
            content="第一段",
            part_id="msg_text_1",
            metadata={"openai": {"itemId": "msg_text_1"}},
        )
        yield StreamEvent(
            type="text_delta",
            content="第二段",
            part_id="msg_text_2",
            metadata={"openai": {"itemId": "msg_text_2"}},
        )
        yield StreamEvent(
            type="usage",
            model="fake-chat-model",
            input_tokens=10,
            output_tokens=6,
            reasoning_tokens=0,
        )


class FakeToolCallMetadataLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
            tool_call_id="call_tool_meta_1",
            tool_name="bash",
            tool_arguments='{"command":"ls"}',
            metadata={"openai": {"itemId": "fc_meta_1"}},
        )
        yield StreamEvent(
            type="usage",
            model="fake-chat-model",
            input_tokens=10,
            output_tokens=0,
            reasoning_tokens=0,
        )


class FakeMirroredReasoningToolLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
        mirrored = "I need to inspect the repo before calling a tool."
        yield StreamEvent(type="reasoning_delta", content=mirrored)
        yield StreamEvent(type="text_delta", content=mirrored)
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_mirrored_reasoning_1",
            tool_name="grep",
            tool_arguments='{"pattern":"needle"}',
        )
        yield StreamEvent(
            type="usage",
            model="fake-chat-model",
            input_tokens=10,
            output_tokens=0,
            reasoning_tokens=0,
        )


class FakeMaxStepsPromptLLM:
    calls: list[list[dict]] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
        FakeMaxStepsPromptLLM.calls.append(copy.deepcopy(messages))
        if any(
            str(item.get("role") or "") == "assistant"
            and "CRITICAL - MAXIMUM STEPS REACHED" in str(item.get("content") or "")
            for item in messages
        ):
            yield StreamEvent(type="text_delta", content="已达到最大步数，停止继续调用工具。")
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6
            )
            return
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_max_steps_1",
            tool_name="bash",
            tool_arguments='{"command":"echo step"}',
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=0)


class FakeReloadBetweenStepsLLM:
    calls: list[list[dict]] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
        FakeReloadBetweenStepsLLM.calls.append(copy.deepcopy(messages))
        if any(
            str(item.get("role") or "") == "user"
            and "第二条用户消息" in str(item.get("content") or "")
            for item in messages
        ):
            yield StreamEvent(type="text_delta", content="看到了新的用户消息。")
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=12, output_tokens=8
            )
            return
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_reload_turn_1",
            tool_name="bash",
            tool_arguments='{"command":"echo reload"}',
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=0)


class FakePlanModeTransitionLLM:
    calls: list[list[dict]] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

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
        FakePlanModeTransitionLLM.calls.append(copy.deepcopy(messages))
        if any(
            str(item.get("role") or "") == "user"
            and "has been approved" in str(item.get("content") or "")
            for item in messages
        ):
            yield StreamEvent(type="text_delta", content="开始执行计划。")
            yield StreamEvent(
                type="usage", model="fake-chat-model", input_tokens=14, output_tokens=6
            )
            return
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_plan_exit_1",
            tool_name="plan_exit",
            tool_arguments="{}",
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=0)


def test_project_and_session_routes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    project_resp = client.get("/project/current", params={"directory": str(tmp_path)})
    assert project_resp.status_code == 200
    project = project_resp.json()
    assert project["worktree"] == str(tmp_path)
    assert str(project["id"]).startswith("project_")
    assert project["name"] == tmp_path.name

    create_resp = client.post(
        "/session",
        json={
            "id": "session_runtime_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["id"] == "session_runtime_test"
    assert created["directory"] == str(tmp_path)
    assert created["mode"] == "build"

    list_resp = client.get("/session", params={"directory": str(tmp_path)})
    assert list_resp.status_code == 200
    sessions = list_resp.json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "session_runtime_test"

    session_lifecycle.set_session_status("session_runtime_test", {"type": "busy"})
    status_resp = client.get("/session/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["session_runtime_test"] == {"type": "busy"}


def test_session_create_requires_workspace_binding(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/session",
        json={
            "id": "session_without_workspace",
            "mode": "build",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "当前未绑定工作区，请先导入或选择目录"


def test_session_prompt_requires_workspace_for_new_session(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/session/session_without_workspace/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "当前未绑定工作区，请先导入或选择目录"


def test_session_prompt_reuses_existing_workspace_when_request_omits_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "existing_workspace_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    resp = client.post(
        "/session/existing_workspace_session/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
        },
    )

    assert resp.status_code == 200
    assert "已完成测试回复" in resp.text


def test_agent_chat_requires_workspace_binding(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/agent/chat",
        json={
            "messages": [{"role": "user", "content": "继续"}],
            "session_id": "agent_without_workspace",
            "mode": "build",
            "active_skill_ids": [],
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "当前未绑定工作区，请先导入或选择目录"


def test_session_delete_message_route_respects_busy_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    session_lifecycle.reset_for_tests()
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "delete_message_busy_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = append_session_message(
        session_id="delete_message_busy_session",
        role="user",
        content="继续",
    )
    owner = session_lifecycle.acquire_prompt_instance("delete_message_busy_session", wait=False)
    assert owner is not None

    try:
        deleted = client.delete(
            f"/session/delete_message_busy_session/message/{user_message['info']['id']}"
        )
        assert deleted.status_code == 400
        assert deleted.json()["detail"] == "session is busy"
    finally:
        session_lifecycle.finish_prompt_instance("delete_message_busy_session")
        session_lifecycle.reset_for_tests()


def test_session_delete_message_and_part_routes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "delete_message_part_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = append_session_message(
        session_id="delete_message_part_session",
        role="user",
        content="继续",
    )
    assistant_message = append_session_message(
        session_id="delete_message_part_session",
        role="assistant",
        content="第一段第二段",
        parent_id=str(user_message["info"]["id"]),
        meta={"providerID": "openai", "modelID": "gpt-4o", "finish": "stop"},
        parts=[
            {"id": "part_delete_text_1", "type": "text", "text": "第一段"},
            {"id": "part_delete_text_2", "type": "text", "text": "第二段"},
        ],
    )

    deleted_part = client.delete(
        f"/session/delete_message_part_session/message/{assistant_message['info']['id']}/part/part_delete_text_1"
    )
    assert deleted_part.status_code == 200
    assert deleted_part.json() is True

    history = client.get("/session/delete_message_part_session/message").json()
    assert len(history) == 2
    remaining_parts = history[1]["parts"]
    assert len(remaining_parts) == 1
    assert remaining_parts[0]["id"] == "part_delete_text_2"
    assert remaining_parts[0]["text"] == "第二段"

    messages = load_agent_messages("delete_message_part_session")
    assert messages == [
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "第二段"},
    ]

    deleted_message = client.delete(
        f"/session/delete_message_part_session/message/{assistant_message['info']['id']}"
    )
    assert deleted_message.status_code == 200
    assert deleted_message.json() is True

    final_history = client.get("/session/delete_message_part_session/message").json()
    assert len(final_history) == 1
    assert final_history[0]["info"]["id"] == user_message["info"]["id"]


def test_load_agent_messages_preserves_user_file_parts_system_and_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)

    ensure_session_record(
        session_id="session_history_user_file_test",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    append_session_message(
        session_id="session_history_user_file_test",
        role="user",
        content="请看附件",
        meta={
            "system": "只输出摘要",
            "tools": {"bash": False},
        },
        parts=[
            {"type": "text", "text": "请看附件"},
            {
                "type": "file",
                "url": "https://example.com/figure.png",
                "filename": "figure.png",
                "mime": "image/png",
            },
        ],
    )

    messages = load_agent_messages("session_history_user_file_test")
    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请看附件"},
                {
                    "type": "file",
                    "url": "https://example.com/figure.png",
                    "filename": "figure.png",
                    "mime": "image/png",
                },
            ],
            "tools": {"bash": False},
            "system": "只输出摘要",
        }
    ]


def test_agent_runtime_state_persists_todos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)

    state = ensure_session(
        "todo_session",
        mode="plan",
        workspace_path=str(tmp_path),
        workspace_server_id="local",
    )
    assert state.session_id == "todo_session"
    assert state.mode == "plan"

    updated = update_todos(
        "todo_session",
        [
            {"content": "梳理 session model", "status": "in_progress", "priority": "high"},
            {"content": "补路由测试", "status": "pending", "priority": "medium"},
        ],
    )
    assert len(updated) == 2
    assert updated[0]["content"] == "梳理 session model"

    todos = get_todos("todo_session")
    assert [item["content"] for item in todos] == ["梳理 session model", "补路由测试"]

    state_again = ensure_session("todo_session")
    assert [item.content for item in state_again.todos] == ["梳理 session model", "补路由测试"]


def test_agent_chat_persists_into_new_session_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(agent_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_build_app())

    resp = client.post(
        "/agent/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "请检查这个 session 是否会持久化",
                }
            ],
            "session_id": "compat_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
        },
    )
    assert resp.status_code == 200
    assert "已完成测试回复" in resp.text

    messages_resp = client.get("/agent/conversations/compat_session")
    assert messages_resp.status_code == 200
    items = messages_resp.json()["messages"]
    assert len(items) == 2
    assert items[0]["role"] == "user"
    assert items[0]["content"] == "请检查这个 session 是否会持久化"
    assert items[1]["role"] == "assistant"
    assert items[1]["content"] == "已完成测试回复。"


def test_legacy_agent_chat_preserves_structured_user_parts_and_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    captured: dict[str, object] = {}

    class _FakeResolvedModelLLM:
        provider = "openai"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(provider="openai", model="gpt-5")

    monkeypatch.setattr(session_runtime_module, "LLMClient", _FakeResolvedModelLLM)

    def _fake_structured_stream_chat(messages, *_args, **kwargs):
        captured["messages"] = copy.deepcopy(messages)
        captured["kwargs"] = dict(kwargs)
        return _wrap_persisted_if_needed(
            [
                'event: text_delta\ndata: {"content":"已完成结构化回复。"}\n\n',
                "event: done\ndata: {}\n\n",
            ],
            kwargs,
        )

    monkeypatch.setattr(agent_router, "stream_chat", _fake_structured_stream_chat)
    client = TestClient(_build_app())

    resp = client.post(
        "/agent/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请结合附件总结"},
                        {
                            "type": "file",
                            "url": "https://example.com/figure.png",
                            "filename": "figure.png",
                            "mime": "image/png",
                        },
                    ],
                    "tools": {"bash": False, "webfetch": True},
                    "system": "只输出摘要",
                    "format": {"type": "text"},
                    "variant": "high",
                }
            ],
            "session_id": "legacy_structured_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
        },
    )
    assert resp.status_code == 200
    assert "已完成结构化回复。" in resp.text
    assert captured["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请结合附件总结"},
                {
                    "type": "file",
                    "url": "https://example.com/figure.png",
                    "filename": "figure.png",
                    "mime": "image/png",
                },
            ],
            "format": {"type": "text"},
            "tools": {"bash": False, "webfetch": True},
            "system": "只输出摘要",
            "variant": "high",
        }
    ]

    history = client.get("/session/legacy_structured_session/message").json()
    assert len(history) == 2
    user_message = history[0]
    assert user_message["info"]["role"] == "user"
    assert user_message["info"]["agent"] == "build"
    assert user_message["info"]["model"] == {"providerID": "openai", "modelID": "gpt-5"}
    assert user_message["info"]["format"] == {"type": "text"}
    assert user_message["info"]["tools"] == {"bash": False, "webfetch": True}
    assert user_message["info"]["system"] == "只输出摘要"
    assert user_message["info"]["variant"] == "high"
    assert user_message["parts"] == [
        {
            "id": user_message["parts"][0]["id"],
            "sessionID": "legacy_structured_session",
            "messageID": user_message["info"]["id"],
            "type": "text",
            "text": "请结合附件总结",
        },
        {
            "id": user_message["parts"][1]["id"],
            "sessionID": "legacy_structured_session",
            "messageID": user_message["info"]["id"],
            "type": "file",
            "content": "",
            "url": "https://example.com/figure.png",
            "filename": "figure.png",
            "mime": "image/png",
        },
    ]

    loaded = load_agent_messages("legacy_structured_session")
    assert loaded[0] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "请结合附件总结"},
            {
                "type": "file",
                "url": "https://example.com/figure.png",
                "filename": "figure.png",
                "mime": "image/png",
            },
        ],
        "format": {"type": "text"},
        "tools": {"bash": False, "webfetch": True},
        "system": "只输出摘要",
        "variant": "high",
    }
    assert loaded[1] == {"role": "assistant", "content": "已完成结构化回复。"}
    kwargs = captured["kwargs"]
    assert "request_message_id" not in kwargs
    assert kwargs["persistence"].parent_id == user_message["info"]["id"]


def test_legacy_agent_chat_persists_active_skill_ids_on_user_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(agent_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_build_app())

    resp = client.post(
        "/agent/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "请继续分析",
                }
            ],
            "session_id": "legacy_active_skill_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": ["skill.alpha", "skill.beta"],
        },
    )
    assert resp.status_code == 200

    history = client.get("/session/legacy_active_skill_session/message").json()
    assert history[0]["info"]["role"] == "user"
    assert history[0]["info"]["activeSkillIDs"] == ["skill.alpha", "skill.beta"]

    loaded = load_agent_messages("legacy_active_skill_session")
    assert loaded[0]["active_skill_ids"] == ["skill.alpha", "skill.beta"]
    assert loaded[0]["variant"] == "medium"


def test_get_session_turn_state_tracks_latest_pending_user():
    session_id = "session_turn_state_test"
    ensure_session_record(
        session_id, directory=str(Path.cwd()), workspace_path=str(Path.cwd()), mode="build"
    )
    first_user = append_session_message(
        session_id=session_id,
        role="user",
        content="first",
    )
    append_session_message(
        session_id=session_id,
        role="assistant",
        content="done",
        parent_id=str(first_user["info"]["id"]),
        meta={"finish": "stop"},
    )
    second_user = append_session_message(
        session_id=session_id,
        role="user",
        content="second",
    )

    pending_state = get_session_turn_state(session_id)
    assert pending_state is not None
    assert pending_state["request_message_id"] == second_user["info"]["id"]
    assert pending_state["has_pending_prompt"] is True

    append_session_message(
        session_id=session_id,
        role="assistant",
        content="done second",
        parent_id=str(second_user["info"]["id"]),
        meta={"finish": "stop"},
    )

    settled_state = get_session_turn_state(session_id)
    assert settled_state is not None
    assert settled_state["request_message_id"] == second_user["info"]["id"]
    assert settled_state["has_pending_prompt"] is False


def test_session_prompt_route_streams_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_prompt_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_prompt_test/message",
        json={
            "parts": [{"type": "text", "text": "请给出一个简短总结"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "已完成测试回复" in prompt_resp.text

    history = client.get("/session/session_prompt_test/message").json()
    assert len(history) == 2
    assert history[0]["info"]["role"] == "user"
    assert history[1]["info"]["role"] == "assistant"


def test_session_prompt_route_persists_effective_reasoning_level_on_user_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_prompt_reasoning_variant_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_prompt_reasoning_variant_test/message",
        json={
            "parts": [{"type": "text", "text": "请给出一个简短总结"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "reasoning_level": "medium",
        },
    )
    assert prompt_resp.status_code == 200

    history = client.get("/session/session_prompt_reasoning_variant_test/message").json()
    assert history[0]["info"]["role"] == "user"
    assert history[0]["info"]["variant"] == "medium"


def test_session_prompt_route_persists_opencode_user_message_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_stream_chat)

    class _FakeResolvedModelLLM:
        provider = "openai"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(provider="openai", model="gpt-5.2")

    monkeypatch.setattr(session_runtime_module, "LLMClient", _FakeResolvedModelLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_prompt_user_info_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_prompt_user_info_test/message",
        json={
            "parts": [{"type": "text", "text": "请输出结构化总结"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
            "reasoning_level": "high",
            "format": {"type": "text"},
        },
    )
    assert prompt_resp.status_code == 200

    history = client.get("/session/session_prompt_user_info_test/message").json()
    user_message = history[0]
    assert user_message["info"]["agent"] == "build"
    assert user_message["info"]["model"] == {"providerID": "openai", "modelID": "gpt-5.2"}
    assert user_message["info"]["format"] == {"type": "text"}
    assert user_message["info"]["variant"] == "high"


def test_session_prompt_route_passes_persistence_into_stream_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    captured = {}

    class _FakeResolvedModelLLM:
        provider = "openai"

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(provider="openai", model="gpt-5.1")

    monkeypatch.setattr(session_runtime_module, "LLMClient", _FakeResolvedModelLLM)

    class _PersistedStream:
        _researchos_persisted = True

        def __iter__(self):
            yield 'event: text_delta\ndata: {"content":"inner-persisted"}\n\n'
            yield "event: done\ndata: {}\n\n"

    def _fake_persisted_stream_chat(*_args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        captured.update(kwargs)
        return _PersistedStream()

    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_persisted_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_prompt_persistence_inward_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_prompt_persistence_inward_test/message",
        json={
            "parts": [{"type": "text", "text": "继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "inner-persisted" in prompt_resp.text
    assert captured["persistence"].session_id == "session_prompt_persistence_inward_test"

    persistence = captured["persistence"]
    assert persistence.session_id == "session_prompt_persistence_inward_test"
    assert persistence.parent_id
    assert persistence.assistant_meta["providerID"] == "openai"
    assert persistence.assistant_meta["modelID"] == "gpt-5.1"
    assert persistence.assistant_meta["tokens"] == {
        "total": None,
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }
    assert persistence.assistant_meta["cost"] == 0.0
    assert persistence.assistant_meta["root"] == str(tmp_path)
    assert persistence.assistant_meta["cwd"] == str(tmp_path)


def test_session_prompt_persists_reasoning_parts_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_reasoning_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_reasoning_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_reasoning_test/message",
        json={
            "parts": [{"type": "text", "text": "请继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "这是最终回答" in prompt_resp.text

    history = client.get("/session/session_reasoning_test/message").json()
    assert len(history) == 2
    assistant = history[1]
    assert assistant["info"]["tokens"]["reasoning"] == 8
    assert any(
        part["type"] == "reasoning" and part["text"] == "先梳理已知条件。"
        for part in assistant["parts"]
    )
    finish_parts = [part for part in assistant["parts"] if part["type"] == "step-finish"]
    assert finish_parts
    assert finish_parts[0]["tokens"]["reasoning"] == 8


def test_session_prompt_abort_marks_inflight_tool_part_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_tool_abort_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_abort_tool_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_abort_tool_test/message",
        json={
            "parts": [{"type": "text", "text": "请执行命令"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "会话已中止" in prompt_resp.text

    history = client.get("/session/session_abort_tool_test/message").json()
    assistant = history[1]
    assert assistant["info"]["error"]["name"] == "AbortedError"
    assert assistant["info"]["finish"] == "aborted"
    tool_parts = [part for part in assistant["parts"] if part["type"] == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["tool"] == "bash"
    assert tool_parts[0]["state"]["status"] == "error"
    assert tool_parts[0]["state"]["input"] == {"command": "ls"}
    assert tool_parts[0]["state"]["error"] == "Tool execution aborted"


@REMOVED_NATIVE_ROUTE
def test_session_prompt_rolls_over_assistant_message_after_tool_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service
    from packages.agent.tools.tool_runtime import ToolResult

    _configure_test_db(monkeypatch)
    FakeToolContinuationLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeToolContinuationLLM)
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"}
    )

    def _fake_execute_tool_stream(_name, _arguments, context=None):  # noqa: ANN001, ANN202
        del context
        yield ToolResult(success=True, summary="命令执行成功", data={"stdout": "ok"})

    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_tool_rollover_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_tool_rollover_test/message",
        json={
            "parts": [{"type": "text", "text": "请先执行工具再继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "工具后继续完成" in prompt_resp.text

    history = client.get("/session/session_tool_rollover_test/message").json()
    assert len(history) == 3
    assert [item["info"]["role"] for item in history] == ["user", "assistant", "assistant"]

    first_assistant = history[1]
    second_assistant = history[2]
    assert first_assistant["info"]["finish"] == "tool-calls"
    assert first_assistant["info"]["parentID"] == history[0]["info"]["id"]
    assert any(
        part["type"] == "tool"
        and part["state"]["status"] == "completed"
        and part.get("summary") == "命令执行成功"
        for part in first_assistant["parts"]
    )
    assert second_assistant["info"]["finish"] == "stop"
    assert second_assistant["info"]["id"] != first_assistant["info"]["id"]
    assert second_assistant["info"]["parentID"] == history[0]["info"]["id"]
    assert any(
        part["type"] == "text" and part["text"] == "工具后继续完成。"
        for part in second_assistant["parts"]
    )

    continuation_messages = FakeToolContinuationLLM.calls[-1]
    assert any(
        str(item.get("role") or "") == "tool" and "命令执行成功" in str(item.get("content") or "")
        for item in continuation_messages
    )


@REMOVED_NATIVE_ROUTE
def test_session_prompt_executes_local_shell_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    FakeLocalShellContinuationLLM.calls = []
    FakeLocalShellContinuationLLM.working_directory = str(tmp_path)
    monkeypatch.setattr(agent_service, "LLMClient", FakeLocalShellContinuationLLM)
    monkeypatch.setattr(
        agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"}
    )
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_local_shell_continuation_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/session_local_shell_continuation_test/message",
        json={
            "parts": [{"type": "text", "text": "请执行 local shell 后继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "local_shell 后继续完成" in prompt_resp.text

    history = client.get("/session/session_local_shell_continuation_test/message").json()
    assert len(history) == 3
    first_assistant = history[1]
    second_assistant = history[2]

    assert first_assistant["info"]["finish"] == "tool-calls"
    tool_parts = [part for part in first_assistant["parts"] if part["type"] == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["tool"] == "local_shell"
    assert tool_parts[0]["state"]["status"] == "completed"
    assert tool_parts[0]["state"]["input"]["action"]["command"] == [
        "Write-Output",
        "local-shell-ok",
    ]

    assert second_assistant["info"]["finish"] == "stop"
    assert any(
        part["type"] == "text" and part["text"] == "local_shell 后继续完成。"
        for part in second_assistant["parts"]
    )

    continuation_messages = FakeLocalShellContinuationLLM.calls[-1]
    assert any(
        str(item.get("role") or "") == "tool" and "local-shell-ok" in str(item.get("content") or "")
        for item in continuation_messages
    )


def test_wrap_stream_persists_text_part_incrementally_before_stream_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="incremental_persist_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="incremental_persist_session",
        role="user",
        content="继续",
    )

    text_seen = threading.Event()
    allow_finish = threading.Event()
    outputs: list[str] = []

    def _raw_stream():  # noqa: ANN202
        yield 'event: assistant_message_id\ndata: {"message_id":"message_incremental_1"}\n\n'
        yield 'event: text_delta\ndata: {"content":"增量内容。"}\n\n'
        allow_finish.wait(timeout=5)
        yield "event: done\ndata: {}\n\n"

    def _consume() -> None:
        for item in wrap_stream_with_persistence(
            _raw_stream(),
            session_id="incremental_persist_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-4o", "finish": None},
        ):
            outputs.append(item)
            if "event: text_delta" in item:
                text_seen.set()

    thread = threading.Thread(target=_consume, daemon=True)
    thread.start()
    assert text_seen.wait(timeout=2)

    history = list_session_messages("incremental_persist_session", include_transient=True)
    assert len(history) == 2
    assistant = history[1]
    assert assistant["info"]["id"] == "message_incremental_1"
    assert any(
        part["type"] == "text" and part["text"] == "增量内容。" for part in assistant["parts"]
    )

    with db.session_scope() as session:
        row = AgentSessionMessageRepository(session).get_by_id("message_incremental_1")
        assert row is not None
        assert row.content == "增量内容。"
        assert (row.meta or {}).get("providerID") == "openai"

    allow_finish.set()
    thread.join(timeout=2)
    assert any("event: done" in item for item in outputs)


def test_wrap_stream_respects_explicit_text_and_reasoning_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="explicit_boundary_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="explicit_boundary_session",
        role="user",
        content="继续",
    )

    outputs = list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_boundary_1"}\n\n',
                    'event: reasoning-start\ndata: {"id":"part_reasoning_1"}\n\n',
                    'event: reasoning_delta\ndata: {"id":"part_reasoning_1","content":"先看上下文。"}\n\n',
                    'event: reasoning-end\ndata: {"id":"part_reasoning_1"}\n\n',
                    'event: text-start\ndata: {"id":"part_text_1"}\n\n',
                    'event: text_delta\ndata: {"id":"part_text_1","content":"第一段"}\n\n',
                    'event: text-end\ndata: {"id":"part_text_1"}\n\n',
                    'event: text-start\ndata: {"id":"part_text_2"}\n\n',
                    'event: text_delta\ndata: {"id":"part_text_2","content":"第二段"}\n\n',
                    'event: text-end\ndata: {"id":"part_text_2"}\n\n',
                    'event: usage\ndata: {"model":"fake-chat-model","input_tokens":10,"output_tokens":6,"reasoning_tokens":8}\n\n',
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="explicit_boundary_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-4o", "finish": None},
        )
    )

    assert any("event: reasoning-start" in item for item in outputs)
    assert any("event: text-start" in item for item in outputs)

    history = list_session_messages("explicit_boundary_session", include_transient=True)
    assert len(history) == 2
    assistant = history[1]
    reasoning_parts = [part for part in assistant["parts"] if part["type"] == "reasoning"]
    text_parts = [part for part in assistant["parts"] if part["type"] == "text"]

    assert len(reasoning_parts) == 1
    assert reasoning_parts[0]["id"] == "part_reasoning_1"
    assert reasoning_parts[0]["text"] == "先看上下文。"
    assert reasoning_parts[0]["time"]["start"] <= reasoning_parts[0]["time"]["end"]

    assert [part["text"] for part in text_parts] == ["第一段", "第二段"]
    assert [part["id"] for part in text_parts] == ["part_text_1", "part_text_2"]
    assert all(part["time"]["start"] <= part["time"]["end"] for part in text_parts)

    with db.session_scope() as session:
        row = AgentSessionMessageRepository(session).get_by_id("message_boundary_1")
        assert row is not None
        assert row.content == "第一段第二段"


def test_agent_project_repository_upsert_reuses_existing_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    worktree = str(tmp_path / "shared-worktree")
    Path(worktree).mkdir(parents=True, exist_ok=True)

    with db.session_scope() as session:
        repo = AgentProjectRepository(session)
        original = repo.upsert(
            project_id="project_existing",
            worktree=worktree,
            name="Original",
            sandboxes=[worktree],
        )
        reused = repo.upsert(
            project_id="project_duplicate",
            worktree=worktree,
            name="Updated",
            sandboxes=[worktree, str(tmp_path / "sandbox-b")],
        )
        assert reused.id == original.id
        assert reused.worktree == worktree
        assert reused.name == "Updated"
        assert reused.sandboxes_json == [worktree, str(tmp_path / "sandbox-b")]


def test_wrap_stream_persists_empty_reasoning_metadata_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="reasoning_metadata_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="reasoning_metadata_session",
        role="user",
        content="继续",
    )

    outputs = list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_reasoning_meta_1"}\n\n',
                    (
                        "event: reasoning-start\ndata: "
                        '{"id":"rs_meta:0","metadata":{"openai":{"itemId":"rs_meta","reasoningEncryptedContent":"enc-meta"}}}\n\n'
                    ),
                    (
                        "event: reasoning_delta\ndata: "
                        '{"id":"rs_meta:0","content":"","metadata":{"openai":{"itemId":"rs_meta","reasoningEncryptedContent":"enc-meta"}}}\n\n'
                    ),
                    'event: reasoning-end\ndata: {"id":"rs_meta:0"}\n\n',
                    'event: text-start\ndata: {"id":"part_text_meta"}\n\n',
                    'event: text_delta\ndata: {"id":"part_text_meta","content":"最终回答"}\n\n',
                    'event: text-end\ndata: {"id":"part_text_meta"}\n\n',
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="reasoning_metadata_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
        )
    )

    assert any("event: reasoning-start" in item for item in outputs)

    history = list_session_messages("reasoning_metadata_session", include_transient=True)
    assistant = history[1]
    reasoning_parts = [part for part in assistant["parts"] if part["type"] == "reasoning"]

    assert len(reasoning_parts) == 1
    assert reasoning_parts[0]["id"] == "rs_meta:0"
    assert reasoning_parts[0]["messageID"] == "message_reasoning_meta_1"
    assert reasoning_parts[0]["text"] == ""
    assert reasoning_parts[0]["metadata"] == {
        "openai": {
            "itemId": "rs_meta",
            "reasoningEncryptedContent": "enc-meta",
        }
    }
    assert reasoning_parts[0]["time"]["start"] <= reasoning_parts[0]["time"]["end"]


def test_wrap_stream_persists_reasoning_ascii_spacing_across_deltas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="reasoning_ascii_spacing_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="reasoning_ascii_spacing_session",
        role="user",
        content="继续",
    )

    list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_reasoning_ascii_1"}\n\n',
                    'event: reasoning-start\ndata: {"id":"part_reasoning_ascii_1"}\n\n',
                    'event: reasoning_delta\ndata: {"id":"part_reasoning_ascii_1","content":"First, we"}\n\n',
                    'event: reasoning_delta\ndata: {"id":"part_reasoning_ascii_1","content":"inspect"}\n\n',
                    'event: reasoning_delta\ndata: {"id":"part_reasoning_ascii_1","content":"the"}\n\n',
                    'event: reasoning_delta\ndata: {"id":"part_reasoning_ascii_1","content":"repo."}\n\n',
                    'event: reasoning-end\ndata: {"id":"part_reasoning_ascii_1"}\n\n',
                    'event: text-start\ndata: {"id":"part_text_ascii_1"}\n\n',
                    'event: text_delta\ndata: {"id":"part_text_ascii_1","content":"done"}\n\n',
                    'event: text-end\ndata: {"id":"part_text_ascii_1"}\n\n',
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="reasoning_ascii_spacing_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
        )
    )

    history = list_session_messages("reasoning_ascii_spacing_session", include_transient=True)
    assistant = history[1]
    reasoning_parts = [part for part in assistant["parts"] if part["type"] == "reasoning"]

    assert len(reasoning_parts) == 1
    assert reasoning_parts[0]["text"] == "First, we inspect the repo."


def test_wrap_stream_persists_text_metadata_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="text_metadata_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="text_metadata_session",
        role="user",
        content="继续",
    )

    list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_text_meta_1"}\n\n',
                    (
                        "event: text-start\ndata: "
                        '{"id":"msg_text_meta","metadata":{"openai":{"itemId":"msg_text_meta"}}}\n\n'
                    ),
                    (
                        "event: text_delta\ndata: "
                        '{"id":"msg_text_meta","content":"最终回答","metadata":{"openai":{"itemId":"msg_text_meta"}}}\n\n'
                    ),
                    'event: text-end\ndata: {"id":"msg_text_meta"}\n\n',
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="text_metadata_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
        )
    )

    history = list_session_messages("text_metadata_session", include_transient=True)
    assistant = history[1]
    text_parts = [part for part in assistant["parts"] if part["type"] == "text"]

    assert len(text_parts) == 1
    assert text_parts[0]["id"] == "msg_text_meta"
    assert text_parts[0]["text"] == "最终回答"
    assert text_parts[0]["metadata"] == {"openai": {"itemId": "msg_text_meta"}}


def test_wrap_stream_persists_tool_metadata_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="tool_metadata_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="tool_metadata_session",
        role="user",
        content="继续",
    )

    list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_tool_meta_1"}\n\n',
                    (
                        "event: tool_start\ndata: "
                        '{"id":"call_tool_meta_1","name":"bash","args":{"command":"ls"},'
                        '"metadata":{"openai":{"itemId":"fc_tool_meta_1"}}}\n\n'
                    ),
                    (
                        "event: tool_result\ndata: "
                        '{"id":"call_tool_meta_1","name":"bash","success":true,"summary":"bash 执行成功",'
                        '"data":{"command":"ls","exit_code":0},"metadata":{"openai":{"itemId":"fc_tool_meta_1"}}}\n\n'
                    ),
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="tool_metadata_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
        )
    )

    history = list_session_messages("tool_metadata_session", include_transient=True)
    assistant = history[1]
    tool_parts = [part for part in assistant["parts"] if part["type"] == "tool"]

    assert len(tool_parts) == 1
    assert tool_parts[0]["callID"] == "call_tool_meta_1"
    assert tool_parts[0]["metadata"] == {"openai": {"itemId": "fc_tool_meta_1"}}


def test_wrap_stream_merges_tool_display_data_into_persisted_tool_part(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="tool_display_data_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="tool_display_data_session",
        role="user",
        content="分析这篇论文",
    )

    list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_tool_display_1"}\n\n',
                    'event: tool_start\ndata: {"id":"call_tool_display_1","name":"get_paper_analysis","args":{"paper_id":"paper-1"}}\n\n',
                    (
                        "event: tool_result\ndata: "
                        '{"id":"call_tool_display_1","name":"get_paper_analysis","success":true,'
                        '"summary":"已读取论文三轮分析结果","data":{"paper_id":"paper-1","title":"Test Paper","figure_count":1},'
                        '"display_data":{"figures":[{"id":"fig-1","caption":"Figure 1","image_url":"/papers/paper-1/figures/fig-1/image"}]}}\n\n'
                    ),
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="tool_display_data_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
        )
    )

    history = list_session_messages("tool_display_data_session", include_transient=True)
    assistant = history[1]
    tool_parts = [part for part in assistant["parts"] if part["type"] == "tool"]

    assert len(tool_parts) == 1
    assert tool_parts[0]["data"]["paper_id"] == "paper-1"
    assert tool_parts[0]["data"]["figure_count"] == 1
    assert tool_parts[0]["data"]["figures"][0]["id"] == "fig-1"
    assert tool_parts[0]["state"]["metadata"]["figures"][0]["image_url"].endswith(
        "/papers/paper-1/figures/fig-1/image"
    )


def test_wrap_stream_persists_usage_provider_metadata_on_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="response_metadata_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="response_metadata_session",
        role="user",
        content="继续",
    )

    list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_response_meta_1"}\n\n',
                    'event: text_delta\ndata: {"content":"最终回答"}\n\n',
                    (
                        "event: usage\ndata: "
                        '{"model":"gpt-5.2","input_tokens":10,"output_tokens":6,"reasoning_tokens":8,'
                        '"metadata":{"openai":{"responseId":"resp_meta_1"}}}\n\n'
                    ),
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="response_metadata_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
        )
    )

    history = list_session_messages("response_metadata_session", include_transient=True)
    assistant = history[1]
    assert assistant["info"]["providerMetadata"] == {
        "openai": {
            "responseId": "resp_meta_1",
        }
    }

    messages = load_agent_messages("response_metadata_session")
    assert messages[1]["provider_metadata"] == {
        "openai": {
            "responseId": "resp_meta_1",
        }
    }


def test_wrap_stream_persists_tool_input_lifecycle_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="tool_input_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="tool_input_session",
        role="user",
        content="继续",
    )

    list(
        wrap_stream_with_persistence(
            iter(
                [
                    'event: assistant_message_id\ndata: {"message_id":"message_tool_input_1"}\n\n',
                    'event: tool-input-start\ndata: {"id":"call_tool_input_1","toolName":"bash"}\n\n',
                    'event: tool-input-delta\ndata: {"id":"call_tool_input_1","delta":"{\\"command\\":\\"ls\\"}"}\n\n',
                    'event: tool-input-end\ndata: {"id":"call_tool_input_1"}\n\n',
                    (
                        "event: action_confirm\ndata: "
                        '{"id":"action_tool_input_1","call_id":"call_tool_input_1","tool":"bash",'
                        '"args":{"command":"ls"},"assistant_message_id":"message_tool_input_1"}\n\n'
                    ),
                    "event: done\ndata: {}\n\n",
                ]
            ),
            session_id="tool_input_session",
            parent_id=str(user_message["info"]["id"]),
            assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
        )
    )

    history = list_session_messages("tool_input_session", include_transient=True)
    assistant = history[1]
    tool_parts = [part for part in assistant["parts"] if part["type"] == "tool"]

    assert len(tool_parts) == 1
    assert tool_parts[0]["callID"] == "call_tool_input_1"
    assert tool_parts[0]["tool"] == "bash"
    assert tool_parts[0]["state"]["status"] == "pending"
    assert tool_parts[0]["state"]["input"] == {"command": "ls"}
    assert tool_parts[0]["state"]["raw"] == '{"command":"ls"}'


def test_tool_input_delta_publishes_part_delta_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import session_bus

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="tool_input_delta_event_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    user_message = append_session_message(
        session_id="tool_input_delta_event_session",
        role="user",
        content="继续",
    )

    events = []
    unsubscribe = session_bus.subscribe_all(lambda event: events.append(event))
    try:
        list(
            wrap_stream_with_persistence(
                iter(
                    [
                        'event: assistant_message_id\ndata: {"message_id":"message_tool_input_delta_1"}\n\n',
                        'event: tool-input-start\ndata: {"id":"call_tool_input_delta_1","toolName":"bash"}\n\n',
                        'event: tool-input-delta\ndata: {"id":"call_tool_input_delta_1","delta":"{\\"command\\":\\"ls\\"}"}\n\n',
                        "event: done\ndata: {}\n\n",
                    ]
                ),
                session_id="tool_input_delta_event_session",
                parent_id=str(user_message["info"]["id"]),
                assistant_meta={"providerID": "openai", "modelID": "gpt-5.2", "finish": None},
            )
        )
    finally:
        unsubscribe()

    delta_events = [
        event for event in events if event.type == session_bus.SessionBusEvent.PART_DELTA
    ]
    assert len(delta_events) == 1
    assert delta_events[0].properties["sessionID"] == "tool_input_delta_event_session"
    assert delta_events[0].properties["messageID"] == "message_tool_input_delta_1"
    assert delta_events[0].properties["field"] == "state.raw"
    assert delta_events[0].properties["delta"] == '{"command":"ls"}'


def test_run_model_turn_drops_mirrored_reasoning_text_before_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="model_turn_mirrored_reasoning_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    monkeypatch.setattr(agent_service, "LLMClient", FakeMirroredReasoningToolLLM)

    stream = agent_service._run_model_turn(
        [{"role": "user", "content": "继续"}],
        agent_service.AgentRuntimeOptions(
            session_id="model_turn_mirrored_reasoning_session",
            workspace_path=str(tmp_path),
        ),
    )

    outputs: list[str] = []
    while True:
        try:
            outputs.append(next(stream))
        except StopIteration as stop:
            result = stop.value
            break

    assert result.status == "continue"
    assert result.content == ""
    assert result.reasoning_content == "I need to inspect the repo before calling a tool."
    assert result.tool_calls

    event_names = [
        item.splitlines()[0].split(":", 1)[1].strip()
        for item in outputs
        if item.startswith("event:")
    ]
    assert "reasoning_delta" in event_names
    assert "text_delta" not in event_names
    assert "tool-input-start" in event_names


def test_run_model_turn_emits_explicit_content_lifecycle_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="model_turn_boundary_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    monkeypatch.setattr(agent_service, "LLMClient", FakeBoundaryLifecycleLLM)

    stream = agent_service._run_model_turn(
        [{"role": "user", "content": "继续"}],
        agent_service.AgentRuntimeOptions(
            session_id="model_turn_boundary_session",
            workspace_path=str(tmp_path),
        ),
    )

    outputs: list[str] = []
    while True:
        try:
            outputs.append(next(stream))
        except StopIteration as stop:
            result = stop.value
            break

    assert result.status == "continue"
    assert result.content == "这是最终回答。"
    assert result.reasoning_content == "先分析上下文。"

    event_names = [
        item.splitlines()[0].split(":", 1)[1].strip()
        for item in outputs
        if item.startswith("event:")
    ]
    assert event_names == [
        "reasoning-start",
        "reasoning_delta",
        "reasoning_delta",
        "reasoning-end",
        "text-start",
        "text_delta",
        "text_delta",
        "text-end",
        "usage",
    ]
    event_payloads = [json.loads(item.splitlines()[1].split(":", 1)[1].strip()) for item in outputs]
    reasoning_part_id = event_payloads[0]["id"]
    text_part_id = event_payloads[4]["id"]
    assert reasoning_part_id.startswith("part_")
    assert text_part_id.startswith("part_")
    assert event_payloads[1]["id"] == reasoning_part_id
    assert event_payloads[2]["id"] == reasoning_part_id


def test_run_model_turn_reasoning_parts_keep_ascii_spacing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="model_turn_reasoning_ascii_spacing_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    monkeypatch.setattr(agent_service, "LLMClient", FakeReasoningAsciiSpacingLLM)

    stream = agent_service._run_model_turn(
        [{"role": "user", "content": "继续"}],
        agent_service.AgentRuntimeOptions(
            session_id="model_turn_reasoning_ascii_spacing_session",
            workspace_path=str(tmp_path),
        ),
    )

    while True:
        try:
            next(stream)
        except StopIteration as stop:
            result = stop.value
            break

    assert result.status == "continue"
    assert result.reasoning_content == "First, we inspect the repo."
    assert result.reasoning_parts[0]["text"] == "First, we inspect the repo."


def test_run_model_turn_preserves_reasoning_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="model_turn_reasoning_metadata_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    monkeypatch.setattr(agent_service, "LLMClient", FakeReasoningMetadataLLM)

    stream = agent_service._run_model_turn(
        [{"role": "user", "content": "继续"}],
        agent_service.AgentRuntimeOptions(
            session_id="model_turn_reasoning_metadata_session",
            workspace_path=str(tmp_path),
        ),
    )

    outputs: list[str] = []
    while True:
        try:
            outputs.append(next(stream))
        except StopIteration as stop:
            result = stop.value
            break

    assert result.status == "continue"
    assert result.content == "最终回答。"
    assert result.reasoning_content == ""
    assert result.reasoning_parts == [
        {
            "id": "rs_agent:0",
            "text": "",
            "metadata": {
                "openai": {
                    "itemId": "rs_agent",
                    "reasoningEncryptedContent": "enc-agent",
                }
            },
        }
    ]

    event_payloads = [json.loads(item.splitlines()[1].split(":", 1)[1].strip()) for item in outputs]
    assert event_payloads[0] == {
        "id": "rs_agent:0",
        "metadata": {
            "openai": {
                "itemId": "rs_agent",
                "reasoningEncryptedContent": "enc-agent",
            }
        },
    }
    assert event_payloads[1] == {
        "id": "rs_agent:0",
        "content": "",
        "metadata": {
            "openai": {
                "itemId": "rs_agent",
                "reasoningEncryptedContent": "enc-agent",
            }
        },
    }
    assert event_payloads[2] == {"id": "rs_agent:0"}
    text_part_id = event_payloads[3]["id"]
    assert text_part_id.startswith("part_")
    assert event_payloads[4]["id"] == text_part_id
    assert event_payloads[5]["id"] == text_part_id


def test_run_model_turn_preserves_text_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="model_turn_text_metadata_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    monkeypatch.setattr(agent_service, "LLMClient", FakeTextMetadataLLM)

    stream = agent_service._run_model_turn(
        [{"role": "user", "content": "继续"}],
        agent_service.AgentRuntimeOptions(
            session_id="model_turn_text_metadata_session",
            workspace_path=str(tmp_path),
        ),
    )

    outputs: list[str] = []
    while True:
        try:
            outputs.append(next(stream))
        except StopIteration as stop:
            result = stop.value
            break

    assert result.status == "continue"
    assert result.content == "第一段第二段"
    assert result.text_parts == [
        {
            "id": "msg_text_1",
            "text": "第一段",
            "metadata": {"openai": {"itemId": "msg_text_1"}},
        },
        {
            "id": "msg_text_2",
            "text": "第二段",
            "metadata": {"openai": {"itemId": "msg_text_2"}},
        },
    ]

    event_payloads = [json.loads(item.splitlines()[1].split(":", 1)[1].strip()) for item in outputs]
    assert event_payloads[0] == {
        "id": "msg_text_1",
        "metadata": {"openai": {"itemId": "msg_text_1"}},
    }
    assert event_payloads[1] == {
        "id": "msg_text_1",
        "content": "第一段",
        "metadata": {"openai": {"itemId": "msg_text_1"}},
    }
    assert event_payloads[3] == {
        "id": "msg_text_2",
        "metadata": {"openai": {"itemId": "msg_text_2"}},
    }
    assert event_payloads[4] == {
        "id": "msg_text_2",
        "content": "第二段",
        "metadata": {"openai": {"itemId": "msg_text_2"}},
    }


def test_run_model_turn_preserves_tool_call_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)
    ensure_session_record(
        session_id="model_turn_tool_metadata_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    monkeypatch.setattr(agent_service, "LLMClient", FakeToolCallMetadataLLM)

    stream = agent_service._run_model_turn(
        [{"role": "user", "content": "继续"}],
        agent_service.AgentRuntimeOptions(
            session_id="model_turn_tool_metadata_session",
            workspace_path=str(tmp_path),
        ),
    )

    outputs: list[str] = []
    while True:
        try:
            outputs.append(next(stream))
        except StopIteration as stop:
            result = stop.value
            break

    assert result.status == "continue"
    assert result.tool_calls == [
        agent_service.ToolCall(
            id="call_tool_meta_1",
            name="bash",
            arguments={"command": "ls"},
            metadata={"openai": {"itemId": "fc_meta_1"}},
        )
    ]

    event_names = [
        item.splitlines()[0].split(":", 1)[1].strip()
        for item in outputs
        if item.startswith("event:")
    ]
    assert event_names == [
        "tool-input-start",
        "tool-input-delta",
        "tool-input-end",
        "usage",
    ]
    event_payloads = [json.loads(item.splitlines()[1].split(":", 1)[1].strip()) for item in outputs]
    assert event_payloads[0] == {"id": "call_tool_meta_1", "toolName": "bash"}
    assert event_payloads[1] == {"id": "call_tool_meta_1", "delta": '{"command":"ls"}'}
    assert event_payloads[2] == {"id": "call_tool_meta_1"}


def test_session_fork_copies_messages_up_to_cutoff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "fork_source_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "title": "Research Plan",
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_one = append_session_message(
        session_id="fork_source_session",
        role="user",
        content="第一轮问题",
    )
    append_session_message(
        session_id="fork_source_session",
        role="assistant",
        content="第一轮回答",
        parent_id=str(user_one["info"]["id"]),
    )
    user_two = append_session_message(
        session_id="fork_source_session",
        role="user",
        content="第二轮问题",
    )
    append_session_message(
        session_id="fork_source_session",
        role="assistant",
        content="第二轮回答",
        parent_id=str(user_two["info"]["id"]),
    )

    fork_resp = client.post(
        "/session/fork_source_session/fork",
        json={"message_id": user_two["info"]["id"]},
    )
    assert fork_resp.status_code == 200
    forked = fork_resp.json()
    assert forked["id"] != "fork_source_session"
    assert forked["title"] == "Research Plan (fork #1)"
    assert forked["parentID"] is None

    history = client.get(f"/session/{forked['id']}/message").json()
    assert len(history) == 2
    assert history[0]["parts"][0]["text"] == "第一轮问题"
    assert history[1]["parts"][0]["text"] == "第一轮回答"
    assert history[1]["info"]["parentID"] == history[0]["info"]["id"]


def test_load_agent_messages_splits_assistant_steps_and_preserves_reasoning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_history_reasoning_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = append_session_message(
        session_id="session_history_reasoning_test",
        role="user",
        content="继续当前任务",
    )
    append_session_message(
        session_id="session_history_reasoning_test",
        role="assistant",
        content="第一步结果第二步结果",
        parent_id=str(user_message["info"]["id"]),
        meta={
            "providerID": "openai",
            "modelID": "gpt-4o",
            "finish": "stop",
        },
        parts=[
            {"type": "step-start", "step": 1},
            {"type": "reasoning", "text": "先做第一步分析。", "time": {"start": 1, "end": 2}},
            {"type": "text", "text": "第一步结果"},
            {"type": "step-start", "step": 2},
            {"type": "reasoning", "text": "再做第二步分析。", "time": {"start": 3, "end": 4}},
            {"type": "text", "text": "第二步结果"},
        ],
    )

    messages = load_agent_messages("session_history_reasoning_test")
    assert messages[0] == {"role": "user", "content": "继续当前任务"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "第一步结果"
    assert messages[1]["reasoning_content"] == "先做第一步分析。"
    assert messages[1]["reasoning_parts"][0]["text"] == "先做第一步分析。"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] == "第二步结果"
    assert messages[2]["reasoning_content"] == "再做第二步分析。"
    assert messages[2]["reasoning_parts"][0]["text"] == "再做第二步分析。"


def test_load_agent_messages_preserves_reasoning_metadata_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_history_reasoning_metadata_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = append_session_message(
        session_id="session_history_reasoning_metadata_test",
        role="user",
        content="继续当前任务",
    )
    append_session_message(
        session_id="session_history_reasoning_metadata_test",
        role="assistant",
        content="最终结果",
        parent_id=str(user_message["info"]["id"]),
        meta={
            "providerID": "openai",
            "modelID": "gpt-5.2",
            "finish": "stop",
        },
        parts=[
            {
                "type": "reasoning",
                "id": "rs_meta_1:0",
                "text": "先分析上下文。",
                "metadata": {
                    "openai": {
                        "itemId": "rs_meta_1",
                        "reasoningEncryptedContent": "enc-1",
                    }
                },
            },
            {
                "type": "reasoning",
                "id": "rs_meta_2:0",
                "text": "",
                "metadata": {
                    "openai": {
                        "itemId": "rs_meta_2",
                        "reasoningEncryptedContent": "enc-2",
                    }
                },
            },
            {"type": "text", "text": "最终结果"},
        ],
    )

    messages = load_agent_messages("session_history_reasoning_metadata_test")
    assert messages == [
        {"role": "user", "content": "继续当前任务"},
        {
            "role": "assistant",
            "content": "最终结果",
            "reasoning_content": "先分析上下文。",
            "reasoning_parts": [
                {
                    "id": "rs_meta_1:0",
                    "text": "先分析上下文。",
                    "metadata": {
                        "openai": {
                            "itemId": "rs_meta_1",
                            "reasoningEncryptedContent": "enc-1",
                        }
                    },
                },
                {
                    "id": "rs_meta_2:0",
                    "text": "",
                    "metadata": {
                        "openai": {
                            "itemId": "rs_meta_2",
                            "reasoningEncryptedContent": "enc-2",
                        }
                    },
                },
            ],
        },
    ]


def test_load_agent_messages_repairs_ascii_reasoning_spacing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_history_reasoning_ascii_spacing_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = append_session_message(
        session_id="session_history_reasoning_ascii_spacing_test",
        role="user",
        content="继续当前任务",
    )
    append_session_message(
        session_id="session_history_reasoning_ascii_spacing_test",
        role="assistant",
        content="done",
        parent_id=str(user_message["info"]["id"]),
        meta={
            "providerID": "openai",
            "modelID": "gpt-5.2",
            "finish": "stop",
        },
        parts=[
            {"type": "reasoning", "text": "I"},
            {"type": "reasoning", "text": "need"},
            {"type": "reasoning", "text": "to"},
            {"type": "reasoning", "text": "inspect"},
            {"type": "reasoning", "text": "the"},
            {"type": "reasoning", "text": "repo."},
            {"type": "reasoning", "text": "Next"},
            {"type": "reasoning", "text": "step"},
            {"type": "text", "text": "done"},
        ],
    )

    messages = load_agent_messages("session_history_reasoning_ascii_spacing_test")
    assert messages[1]["reasoning_content"] == "I need to inspect the repo. Next step"


def test_merge_reasoning_fragments_keeps_cjk_compact() -> None:
    assert session_message_v2.merge_reasoning_fragments(["先分析", "上下文。"]) == "先分析上下文。"


def test_merge_reasoning_fragments_keeps_ascii_words_separated_after_multiword_chunk() -> None:
    assert (
        session_message_v2.merge_reasoning_fragments(["First, we", "inspect", "the", "repo."])
        == "First, we inspect the repo."
    )


def test_load_agent_messages_preserves_text_metadata_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_history_text_metadata_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = append_session_message(
        session_id="session_history_text_metadata_test",
        role="user",
        content="继续当前任务",
    )
    append_session_message(
        session_id="session_history_text_metadata_test",
        role="assistant",
        content="第一段第二段",
        parent_id=str(user_message["info"]["id"]),
        meta={
            "providerID": "openai",
            "modelID": "gpt-5.2",
            "finish": "stop",
        },
        parts=[
            {
                "type": "text",
                "id": "msg_part_1",
                "text": "第一段",
                "metadata": {"openai": {"itemId": "msg_part_1"}},
            },
            {
                "type": "text",
                "id": "msg_part_2",
                "text": "第二段",
                "metadata": {"openai": {"itemId": "msg_part_2"}},
            },
        ],
    )

    messages = load_agent_messages("session_history_text_metadata_test")
    assert messages == [
        {"role": "user", "content": "继续当前任务"},
        {
            "role": "assistant",
            "content": "第一段\n\n第二段",
            "text_parts": [
                {
                    "id": "msg_part_1",
                    "text": "第一段",
                    "metadata": {"openai": {"itemId": "msg_part_1"}},
                },
                {
                    "id": "msg_part_2",
                    "text": "第二段",
                    "metadata": {"openai": {"itemId": "msg_part_2"}},
                },
            ],
        },
    ]


def test_load_agent_messages_reconstructs_tool_calls_and_tool_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "session_history_tool_test",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = append_session_message(
        session_id="session_history_tool_test",
        role="user",
        content="请运行命令",
    )
    append_session_message(
        session_id="session_history_tool_test",
        role="assistant",
        content="已执行命令",
        parent_id=str(user_message["info"]["id"]),
        meta={
            "providerID": "openai",
            "modelID": "gpt-4o",
            "finish": "stop",
        },
        parts=[
            {"type": "text", "text": "已执行命令"},
            {
                "type": "tool",
                "tool": "bash",
                "callID": "call_tool_history_1",
                "metadata": {"openai": {"itemId": "fc_tool_history_1"}},
                "summary": "bash 执行成功",
                "data": {"command": "ls", "exit_code": 0},
                "state": {
                    "status": "completed",
                    "input": {"command": "ls"},
                    "output": "ok",
                    "title": "bash 执行成功",
                    "metadata": {"command": "ls", "exit_code": 0},
                    "time": {"start": 1, "end": 2},
                },
            },
        ],
    )

    messages = load_agent_messages("session_history_tool_test")
    assert messages[0] == {"role": "user", "content": "请运行命令"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "已执行命令"
    assert messages[1]["tool_calls"] == [
        {
            "id": "call_tool_history_1",
            "type": "function",
            "function": {
                "name": "bash",
                "arguments": '{"command": "ls"}',
            },
            "metadata": {"openai": {"itemId": "fc_tool_history_1"}},
        }
    ]
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call_tool_history_1"
    assert messages[2]["name"] == "bash"
    assert json.loads(messages[2]["content"]) == {
        "success": True,
        "summary": "bash 执行成功",
        "data": {"command": "ls", "exit_code": 0},
    }
