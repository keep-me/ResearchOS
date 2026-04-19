from __future__ import annotations

import copy
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
    session_compaction,
)
from packages.agent.session.session_compaction import summarize_session
from packages.agent.session.session_runtime import append_session_message, list_session_messages
from packages.agent.tools.tool_runtime import ToolResult
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


class FakeCompactionLLM:
    calls: list[list[dict]] = []
    overflow_on_first_chat: bool = False
    normal_chat_calls: int = 0

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
        FakeCompactionLLM.calls.append(copy.deepcopy(messages))
        last_user = next(
            (
                str(item.get("content") or "")
                for item in reversed(messages)
                if str(item.get("role") or "") == "user"
            ),
            "",
        )
        if "Provide a detailed prompt for continuing our conversation above." in last_user:
            yield StreamEvent(type="text_delta", content="## Goal\n继续当前任务\n\n## Accomplished\n已完成压缩摘要。")
            yield StreamEvent(type="usage", model="fake-summary-model", input_tokens=44, output_tokens=12)
            return

        if FakeCompactionLLM.overflow_on_first_chat and FakeCompactionLLM.normal_chat_calls == 0:
            FakeCompactionLLM.normal_chat_calls += 1
            yield StreamEvent(type="error", content="maximum context length exceeded")
            return

        FakeCompactionLLM.normal_chat_calls += 1
        yield StreamEvent(type="text_delta", content="后续回复已使用压缩上下文。")
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=10, output_tokens=6)


class FakeToolStepCompactionLLM:
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
        FakeToolStepCompactionLLM.calls.append(copy.deepcopy(messages))
        last_user = next(
            (
                str(item.get("content") or "")
                for item in reversed(messages)
                if str(item.get("role") or "") == "user"
            ),
            "",
        )
        if "Provide a detailed prompt for continuing our conversation above." in last_user:
            yield StreamEvent(type="text_delta", content="## Goal\n继续执行\n\n## Accomplished\n已完成工具步骤并压缩。")
            yield StreamEvent(type="usage", model="fake-summary-model", input_tokens=48, output_tokens=16)
            return
        if "Continue if you have next steps" in last_user:
            yield StreamEvent(type="text_delta", content="压缩后继续完成任务。")
            yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=12, output_tokens=8)
            return

        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_step_compact_1",
            tool_name="bash",
            tool_arguments='{"command":"echo ok"}',
        )
        yield StreamEvent(type="usage", model="fake-chat-model", input_tokens=140000, output_tokens=30000)


def _patch_compaction_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeCompactionLLM.calls = []
    FakeCompactionLLM.overflow_on_first_chat = False
    FakeCompactionLLM.normal_chat_calls = 0
    monkeypatch.setattr(agent_service, "LLMClient", FakeCompactionLLM)
    monkeypatch.setattr(session_compaction, "LLMClient", FakeCompactionLLM)


def _patch_step_compaction_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeToolStepCompactionLLM.calls = []
    monkeypatch.setattr(agent_service, "LLMClient", FakeToolStepCompactionLLM)
    monkeypatch.setattr(session_compaction, "LLMClient", FakeToolStepCompactionLLM)
    monkeypatch.setattr(agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"})

    def _fake_execute_tool_stream(_name, _arguments, context=None):  # noqa: ANN001, ANN202
        del context
        yield ToolResult(success=True, summary="命令执行成功", data={"stdout": "ok"})

    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)


def test_session_summarize_route_persists_summary_and_reuses_compacted_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_compaction_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "compaction_route_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user = append_session_message(
        session_id="compaction_route_session",
        role="user",
        content="第一轮长上下文：这里有很多历史细节，需要后续压缩保留。",
    )
    append_session_message(
        session_id="compaction_route_session",
        role="assistant",
        content="第一轮回答：已经分析了目录、文件和实现思路。",
        parent_id=str(user["info"]["id"]),
        meta={"finish": "stop"},
    )

    summarize_resp = client.post(
        "/session/compaction_route_session/summarize",
        json={
            "providerID": "openai",
            "modelID": "gpt-4.1",
        },
    )
    assert summarize_resp.status_code == 200
    assert summarize_resp.json() is True

    next_prompt = client.post(
        "/session/compaction_route_session/message",
        json={
            "parts": [{"type": "text", "text": "继续下一步"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert next_prompt.status_code == 200
    assert "后续回复已使用压缩上下文" in next_prompt.text

    history = client.get("/session/compaction_route_session/message").json()
    assert len(history) == 6
    assert any(part["type"] == "compaction" for part in history[2]["parts"])
    assert history[3]["info"]["summary"] is True
    assert "## Goal" in history[3]["parts"][0]["text"]

    followup_messages = FakeCompactionLLM.calls[-1]
    assert any(
        str(item.get("role") or "") == "user" and "What did we do so far?" in str(item.get("content") or "")
        for item in followup_messages
    )
    assert any(
        str(item.get("role") or "") == "assistant" and "## Goal" in str(item.get("content") or "")
        for item in followup_messages
    )
    assert not any("第一轮长上下文" in str(item.get("content") or "") for item in followup_messages)


def test_auto_overflow_compaction_creates_replay_message_and_excludes_latest_prompt_from_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_compaction_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "compaction_overflow_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_one = append_session_message(
        session_id="compaction_overflow_session",
        role="user",
        content="前置背景：已经做了很多准备工作。",
    )
    append_session_message(
        session_id="compaction_overflow_session",
        role="assistant",
        content="前置回答：这里记录已经完成的工作。",
        parent_id=str(user_one["info"]["id"]),
        meta={"finish": "stop"},
    )
    append_session_message(
        session_id="compaction_overflow_session",
        role="user",
        content="请继续完成最后一步",
    )

    summarize_session(
        "compaction_overflow_session",
        provider_id="openai",
        model_id="gpt-4.1",
        auto=True,
        overflow=True,
    )

    history = list_session_messages("compaction_overflow_session", limit=20)
    assert len(history) == 6
    assert any(part["type"] == "compaction" for part in history[3]["parts"])
    assert history[4]["info"]["summary"] is True
    assert history[5]["info"]["role"] == "user"
    assert history[5]["parts"][0]["text"] == "请继续完成最后一步"

    summary_messages = FakeCompactionLLM.calls[0]
    assert not any("请继续完成最后一步" in str(item.get("content") or "") for item in summary_messages)


def test_auto_overflow_compaction_replay_preserves_user_message_meta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_compaction_runtime(monkeypatch)

    session_runtime_router.ensure_session_record(
        "compaction_replay_meta_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )

    user_one = append_session_message(
        session_id="compaction_replay_meta_session",
        role="user",
        content="前置背景",
        meta={
            "agent": "build",
            "model": {"providerID": "openai", "modelID": "gpt-5"},
            "format": {"type": "text"},
            "tools": {"bash": False},
            "system": "只输出摘要",
            "variant": "high",
        },
    )
    append_session_message(
        session_id="compaction_replay_meta_session",
        role="assistant",
        content="前置回答",
        parent_id=str(user_one["info"]["id"]),
        meta={"finish": "stop"},
    )
    append_session_message(
        session_id="compaction_replay_meta_session",
        role="user",
        content="请继续最后一步",
        meta={
            "agent": "build",
            "model": {"providerID": "openai", "modelID": "gpt-5"},
            "format": {"type": "text"},
            "tools": {"bash": False},
            "system": "只输出摘要",
            "variant": "high",
        },
    )

    summarize_session(
        "compaction_replay_meta_session",
        provider_id="openai",
        model_id="gpt-4.1",
        auto=True,
        overflow=True,
    )

    history = list_session_messages("compaction_replay_meta_session", limit=20)
    replay = history[-1]
    summary = history[-2]
    compaction = history[-3]

    assert replay["info"]["role"] == "user"
    assert replay["info"]["agent"] == "build"
    assert replay["info"]["model"] == {"providerID": "openai", "modelID": "gpt-5"}
    assert replay["info"]["format"] == {"type": "text"}
    assert replay["info"]["tools"] == {"bash": False}
    assert replay["info"]["system"] == "只输出摘要"
    assert replay["info"]["variant"] == "high"
    assert "parentID" not in replay["info"]

    assert summary["info"]["role"] == "assistant"
    assert summary["info"]["mode"] == "compaction"
    assert summary["info"]["agent"] == "compaction"
    assert summary["info"]["path"] == {"cwd": str(tmp_path), "root": str(tmp_path)}
    assert summary["info"]["summary"] is True

    assert compaction["info"]["role"] == "user"
    assert compaction["info"]["agent"] == "build"
    assert compaction["info"]["model"] == {"providerID": "openai", "modelID": "gpt-5"}


def test_preflight_auto_compaction_runs_before_answering_new_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_compaction_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "compaction_preflight_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_one = append_session_message(
        session_id="compaction_preflight_session",
        role="user",
        content="前序上下文：这里积累了很多长历史。",
    )
    append_session_message(
        session_id="compaction_preflight_session",
        role="assistant",
        content="前序回答：这里已经产生了很多 token。",
        parent_id=str(user_one["info"]["id"]),
        meta={
            "finish": "stop",
            "providerID": "openai",
            "modelID": "gpt-4o",
            "tokens": {
                "total": 160000,
                "input": 120000,
                "output": 40000,
                "reasoning": 0,
                "cache": {"read": 0, "write": 0},
            },
        },
    )

    prompt_resp = client.post(
        "/session/compaction_preflight_session/message",
        json={
            "parts": [{"type": "text", "text": "请继续当前任务"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "后续回复已使用压缩上下文" in prompt_resp.text

    history = client.get("/session/compaction_preflight_session/message").json()
    assert len(history) == 7
    assert history[3]["parts"][0]["type"] == "compaction"
    assert history[4]["info"]["summary"] is True
    assert history[5]["parts"][0]["text"] == "请继续当前任务"
    assert history[6]["info"]["parentID"] == history[5]["info"]["id"]

    final_prompt_messages = FakeCompactionLLM.calls[-1]
    assert any("What did we do so far?" in str(item.get("content") or "") for item in final_prompt_messages)
    assert any("## Goal" in str(item.get("content") or "") for item in final_prompt_messages)
    assert not any("前序上下文：这里积累了很多长历史。" in str(item.get("content") or "") for item in final_prompt_messages)


def test_context_overflow_error_triggers_auto_compaction_and_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_compaction_runtime(monkeypatch)
    FakeCompactionLLM.overflow_on_first_chat = True
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "compaction_error_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_one = append_session_message(
        session_id="compaction_error_session",
        role="user",
        content="已有背景：需要先保留上下文。",
    )
    append_session_message(
        session_id="compaction_error_session",
        role="assistant",
        content="已有回答：这里已经完成前置分析。",
        parent_id=str(user_one["info"]["id"]),
        meta={
            "finish": "stop",
            "providerID": "openai",
            "modelID": "gpt-4o",
            "tokens": {
                "total": 60000,
                "input": 45000,
                "output": 15000,
                "reasoning": 0,
                "cache": {"read": 0, "write": 0},
            },
        },
    )

    prompt_resp = client.post(
        "/session/compaction_error_session/message",
        json={
            "parts": [{"type": "text", "text": "请继续完成最后一步"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "maximum context length exceeded" not in prompt_resp.text
    assert "后续回复已使用压缩上下文" in prompt_resp.text

    history = client.get("/session/compaction_error_session/message").json()
    assert len(history) == 7
    assert history[3]["parts"][0]["type"] == "compaction"
    assert history[4]["info"]["summary"] is True
    assert history[5]["parts"][0]["text"] == "请继续完成最后一步"
    assert history[6]["info"]["parentID"] == history[5]["info"]["id"]


def test_standard_prompt_persists_step_lifecycle_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_compaction_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "step_part_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/step_part_session/message",
        json={
            "parts": [{"type": "text", "text": "请直接继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "后续回复已使用压缩上下文" in prompt_resp.text

    history = client.get("/session/step_part_session/message").json()
    assert len(history) == 2
    assistant_parts = history[1]["parts"]
    assert any(part["type"] == "step-start" for part in assistant_parts)
    assert any(part["type"] == "step-finish" for part in assistant_parts)


def test_post_step_auto_compaction_persists_completed_assistant_and_rolls_over_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_step_compaction_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "step_compaction_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/step_compaction_session/message",
        json={
            "parts": [{"type": "text", "text": "先执行工具再继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "压缩后继续完成任务" in prompt_resp.text

    history = client.get("/session/step_compaction_session/message").json()
    assert len(history) == 6

    first_assistant = history[1]
    assert first_assistant["info"]["finish"] == "tool-calls"
    assert any(
        part["type"] == "tool" and part["state"]["status"] == "completed" and part.get("summary") == "命令执行成功"
        for part in first_assistant["parts"]
    )

    assert history[2]["parts"][0]["type"] == "compaction"
    assert history[3]["info"]["summary"] is True
    assert "## Goal" in history[3]["parts"][0]["text"]
    assert history[4]["info"]["role"] == "user"
    assert "Continue if you have next steps" in history[4]["parts"][0]["text"]
    assert history[5]["info"]["role"] == "assistant"
    assert history[5]["info"]["parentID"] == history[4]["info"]["id"]
    assert history[5]["info"]["id"] != first_assistant["info"]["id"]

    summary_messages = next(
        messages
        for messages in FakeToolStepCompactionLLM.calls
        if any(
            str(item.get("role") or "") == "user"
            and "Provide a detailed prompt for continuing our conversation above." in str(item.get("content") or "")
            for item in messages
        )
    )
    assert any(
        str(item.get("role") or "") == "tool" and "命令执行成功" in str(item.get("content") or "")
        for item in summary_messages
    )

    continuation_messages = FakeToolStepCompactionLLM.calls[-1]
    assert any("## Goal" in str(item.get("content") or "") for item in continuation_messages)
    assert any("Continue if you have next steps" in str(item.get("content") or "") for item in continuation_messages)
    assert not any("先执行工具再继续" in str(item.get("content") or "") for item in continuation_messages)

