from __future__ import annotations

import json
import sys
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
    acp_service,
    agent_service,
    permission_next,
    session_bus,
    session_plan,
    skill_tool_runtime,
    tool_registry,
)
from packages.agent import (
    session_runtime as session_runtime_module,
)
from packages.agent.session.session_bus import SessionBusEvent
from packages.agent.session.session_runtime import ensure_session_record, get_session_record
from packages.agent.tools.tool_registry import get_openai_tools
from packages.agent.tools.tool_runtime import AgentToolContext, ToolResult, execute_tool_stream
from packages.integrations.llm_client import StreamEvent
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.repositories import AgentPendingActionRepository
from tests.fixtures.mock_acp_http_permission_server import serve_mock_acp_http_permission_server


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


def _configure_acp_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, command: str, args: list[str]
) -> None:
    registry_path = tmp_path / "assistant_acp_registry.json"
    monkeypatch.setattr(acp_service, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(acp_service, "_REGISTRY_PATH", registry_path)
    acp_service.get_acp_registry_service.cache_clear()
    service = acp_service.get_acp_registry_service()
    service.update_config(
        {
            "default_server": "mock-stdio",
            "servers": {
                "mock-stdio": {
                    "label": "Mock ACP Permission",
                    "transport": "stdio",
                    "command": command,
                    "args": args,
                    "cwd": str(tmp_path),
                    "enabled": True,
                    "timeout_sec": 30,
                }
            },
        }
    )
    service.connect_server("mock-stdio")


def _configure_http_acp_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, url: str
) -> None:
    registry_path = tmp_path / "assistant_acp_registry.json"
    monkeypatch.setattr(acp_service, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(acp_service, "_REGISTRY_PATH", registry_path)
    acp_service.get_acp_registry_service.cache_clear()
    service = acp_service.get_acp_registry_service()
    service.update_config(
        {
            "default_server": "mock-http",
            "servers": {
                "mock-http": {
                    "label": "Mock ACP HTTP Permission",
                    "transport": "http",
                    "url": url,
                    "enabled": True,
                    "timeout_sec": 30,
                }
            },
        }
    )
    service.connect_server("mock-http")


def _test_exec_policy() -> dict:
    return {
        "workspace_access": "read_write",
        "command_execution": "full",
        "approval_mode": "on_request",
        "allowed_command_prefixes": [],
    }


class FakePermissionLLM:
    seen_tools: list[str] = []

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(
        self,
        messages,
        tools=None,
        variant_override=None,
        session_cache_key=None,
        model_override=None,
    ):  # noqa: ANN001, ANN201
        del variant_override, session_cache_key, model_override
        FakePermissionLLM.seen_tools = [item["function"]["name"] for item in (tools or [])]
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if tool_messages:
            payload = json.loads(str(tool_messages[-1].get("content") or "{}"))
            data = payload.get("data") or {}
            if data.get("rejected"):
                note = str(data.get("message") or "").strip()
                text = f"收到拒绝反馈：{note}" if note else "收到拒绝反馈。"
            elif data.get("denied"):
                text = "该工具被权限规则阻止。"
            else:
                text = "工具已执行完成。"
            yield StreamEvent(type="text_delta", content=text)
            yield StreamEvent(type="usage", model="fake-model", input_tokens=12, output_tokens=6)
            return

        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_perm_1",
            tool_name="bash",
            tool_arguments=json.dumps({"command": "pytest -q"}, ensure_ascii=False),
        )
        yield StreamEvent(type="usage", model="fake-model", input_tokens=12, output_tokens=0)


class FakeDoublePermissionLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(
        self,
        messages,
        tools=None,
        variant_override=None,
        session_cache_key=None,
        model_override=None,
    ):  # noqa: ANN001, ANN201
        del messages, tools, variant_override, session_cache_key, model_override
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_perm_1",
            tool_name="bash",
            tool_arguments=json.dumps({"command": "pytest -q"}, ensure_ascii=False),
        )
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_perm_2",
            tool_name="bash",
            tool_arguments=json.dumps(
                {"command": "pytest -q tests/test_agent_permission_next.py"}, ensure_ascii=False
            ),
        )
        yield StreamEvent(type="usage", model="fake-model", input_tokens=12, output_tokens=0)


class FakeQuestionLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(
        self,
        messages,
        tools=None,
        variant_override=None,
        session_cache_key=None,
        model_override=None,
    ):  # noqa: ANN001, ANN201
        del tools, variant_override, session_cache_key, model_override
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if tool_messages:
            payload = json.loads(str(tool_messages[-1].get("content") or "{}"))
            data = payload.get("data") or {}
            answers = data.get("answers") or []
            first_answer = (
                ", ".join(answers[0]) if answers and isinstance(answers[0], list) else "未回答"
            )
            yield StreamEvent(type="text_delta", content=f"收到你的选择：{first_answer}")
            yield StreamEvent(type="usage", model="fake-model", input_tokens=8, output_tokens=6)
            return

        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_question_1",
            tool_name="question",
            tool_arguments=json.dumps(
                {
                    "questions": [
                        {
                            "header": "实现策略",
                            "question": "这一步你更希望我怎么推进？",
                            "options": [
                                {
                                    "label": "保守实现 (Recommended)",
                                    "description": "尽量少改，先把核心闭环接通。",
                                },
                                {
                                    "label": "彻底重构",
                                    "description": "一次性把相关层全部收平。",
                                },
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        yield StreamEvent(type="usage", model="fake-model", input_tokens=12, output_tokens=0)


def _fake_execute_tool_stream(name: str, arguments: dict, context=None):  # noqa: ANN001, ANN201
    del context
    yield ToolResult(
        success=True,
        summary=f"{name} 执行成功",
        data={"command": arguments.get("command")},
    )


def _patch_permission_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    FakePermissionLLM.seen_tools = []
    monkeypatch.setattr(agent_service, "LLMClient", FakePermissionLLM)
    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)
    monkeypatch.setattr(permission_next, "get_assistant_exec_policy", _test_exec_policy)


def _all_text_parts(parts: list[dict]) -> str:
    return "\n".join(str(part.get("text") or "") for part in parts if part.get("type") == "text")


def _clear_pending_permissions() -> None:
    for item in permission_next.list_pending():
        request_id = str(item.get("id") or "").strip()
        if request_id:
            permission_next.delete_pending_action_state(request_id)


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    _clear_pending_permissions()
    yield
    _clear_pending_permissions()


def test_permission_deny_rule_disables_tool_exposure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)

    session = ensure_session_record(
        session_id="perm_disable_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    permission_next.store_project_rules(
        session["projectID"],
        [{"permission": "bash", "pattern": "*", "action": "deny"}],
    )

    ruleset = permission_next.effective_ruleset(
        get_session_record("perm_disable_session"), _test_exec_policy()
    )
    disabled_tools = permission_next.disabled(["bash", "read", "websearch"], ruleset)
    tools = get_openai_tools("build", disabled_tools=disabled_tools)
    tool_names = {item["function"]["name"] for item in tools}

    assert "bash" in disabled_tools
    assert "bash" not in tool_names
    assert "read" in tool_names


def test_get_openai_tools_defaults_to_opencode_core_set():
    tool_names = {item["function"]["name"] for item in get_openai_tools("build")}

    assert {
        "apply_patch",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "question",
        "skill",
        "task",
        "todowrite",
        "webfetch",
        "websearch",
        "codesearch",
    }.issubset(tool_names)
    assert "list" not in tool_names
    assert "multiedit" not in tool_names
    assert "todoread" not in tool_names
    assert "search_web" not in tool_names
    assert "search_papers" not in tool_names
    assert "get_system_status" not in tool_names
    assert "writing_assist" in tool_names
    assert "list_local_skills" not in tool_names
    assert "read_local_skill" not in tool_names
    assert "ls" not in tool_names


def test_build_turn_tools_exposes_plan_file_tools_in_plan_mode():
    class FakeClaudeLLM:
        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    tools = tool_registry.build_turn_tools(FakeClaudeLLM(), mode="plan")
    function_names = {
        str(
            ((tool.get("function") or {}) if isinstance(tool.get("function"), dict) else {}).get(
                "name"
            )
            or ""
        )
        for tool in tools
        if str(tool.get("type") or "") == "function"
    }

    assert "plan_exit" in function_names
    assert "write" in function_names
    assert "edit" in function_names
    assert "question" in function_names
    assert "bash" in function_names
    assert "task" in function_names
    assert "webfetch" in function_names
    assert "websearch" in function_names
    assert "codesearch" in function_names


def test_get_openai_tools_allows_explicit_extension_opt_in():
    tool_names = {
        item["function"]["name"]
        for item in get_openai_tools(
            "build",
            enabled_tools={"search_web", "search_papers", "list_local_skills", "list"},
        )
    }

    assert "search_web" in tool_names
    assert "search_papers" in tool_names
    assert "list_local_skills" in tool_names
    assert "list" in tool_names


def test_get_openai_tools_remote_workspace_still_filters_local_only_hidden_tools():
    tool_names = {
        item["function"]["name"]
        for item in get_openai_tools(
            "build",
            workspace_server_id="ssh-dev",
            enabled_tools={"bash", "list", "search_papers"},
        )
    }

    assert "search_papers" in tool_names
    assert "bash" not in tool_names
    assert "list" not in tool_names


def test_execute_tool_stream_skill_returns_opencode_style_skill_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Demo Skill\n\nFollow the bundled workflow.\n", encoding="utf-8")
    helper_file = skill_dir / "scripts" / "helper.sh"
    helper_file.parent.mkdir()
    helper_file.write_text("echo demo\n", encoding="utf-8")

    monkeypatch.setattr(
        skill_tool_runtime,
        "get_local_skill_detail",
        lambda name, max_chars=12000: {
            "id": f"project:{name}",
            "name": "demo-skill",
            "path": str(skill_dir),
            "entry_file": str(skill_file),
            "content": skill_file.read_text(encoding="utf-8")[:max_chars],
            "truncated": False,
        },
    )

    events = list(execute_tool_stream("skill", {"name": "demo-skill"}))
    assert len(events) == 1
    result = events[0]
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.summary == "已加载 skill：demo-skill"
    assert '<skill_content name="demo-skill">' in result.data["output"]
    assert "# Skill: demo-skill" in result.data["output"]
    assert "Follow the bundled workflow." in result.data["output"]
    assert "<skill_files>" in result.data["output"]
    assert str(helper_file.resolve()) in result.data["output"]


def test_execute_tool_stream_apply_patch_updates_and_moves_files(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("hello\nworld\n", encoding="utf-8")
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: source.txt",
            "*** Move to: target.txt",
            "@@",
            "-hello",
            "+hi",
            " world",
            "*** End Patch",
        ]
    )

    events = list(
        execute_tool_stream(
            "apply_patch",
            {"patchText": patch},
            context=AgentToolContext(session_id="patch-tool-session", workspace_path=str(tmp_path)),
        )
    )

    assert len(events) == 1
    result = events[0]
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert source.exists() is False
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "hi\nworld\n"
    assert "Success. Updated the following files:" in result.summary
    assert "M target.txt" in result.summary
    assert len(result.internal_data["patches"]) == 2


def test_authorize_apply_patch_extracts_file_patterns(tmp_path: Path):
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: alpha.txt",
            "*** Move to: beta.txt",
            "@@",
            "-old",
            "+new",
            "*** End Patch",
        ]
    )

    decision = permission_next.authorize_tool_call(
        SimpleNamespace(id="call_patch_1", name="apply_patch", arguments={"patchText": patch}),
        {
            "id": "patch_perm_session",
            "projectID": "global",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
        },
        {"workspace_access": "read_write", "command_execution": "full", "approval_mode": "off"},
        create_pending_request=False,
    )

    assert decision.status == "allow"
    assert decision.permission == "edit"
    assert decision.patterns == [
        str((tmp_path / "alpha.txt").resolve()),
        str((tmp_path / "beta.txt").resolve()),
    ]


def test_session_permission_once_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_once_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_once_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "event: action_confirm" in prompt_resp.text

    pending = client.get("/session/perm_once_session/permissions")
    assert pending.status_code == 200
    items = pending.json()
    assert len(items) == 1
    assert items[0]["permission"] == "bash"
    assert items[0]["tool"]["messageID"]

    paused_history = client.get("/session/perm_once_session/message").json()
    assert len(paused_history) == 2
    paused_assistant = paused_history[1]
    assert paused_assistant["info"]["finish"] is None
    paused_tool_parts = [part for part in paused_assistant["parts"] if part["type"] == "tool"]
    assert len(paused_tool_parts) == 1
    assert paused_tool_parts[0]["callID"] == "call_perm_1"
    assert paused_tool_parts[0]["state"]["status"] == "pending"
    assert paused_tool_parts[0]["state"]["input"] == {"command": "pytest -q"}
    assert paused_assistant["info"]["id"] == items[0]["tool"]["messageID"]

    reply_resp = client.post(
        f"/session/perm_once_session/permissions/{items[0]['id']}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text
    assert "已确认，开始执行" not in reply_resp.text
    assert client.get("/session/perm_once_session/permissions").json() == []

    history = client.get("/session/perm_once_session/message").json()
    assert len(history) == 3
    assert [item["info"]["role"] for item in history] == ["user", "assistant", "assistant"]
    first_assistant = history[1]
    second_assistant = history[2]
    assert first_assistant["info"]["id"] == paused_assistant["info"]["id"]
    assert first_assistant["info"]["finish"] == "tool-calls"
    assert second_assistant["info"]["finish"] == "stop"
    assert second_assistant["info"]["id"] != first_assistant["info"]["id"]
    tool_parts = [part for part in first_assistant["parts"] if part["type"] == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["state"]["status"] == "completed"
    finish_parts = [part for part in first_assistant["parts"] if part["type"] == "step-finish"]
    assert finish_parts
    assert "工具已执行完成" in _all_text_parts(second_assistant["parts"])


def test_session_question_flow_pauses_and_resumes_with_answers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(agent_service, "LLMClient", FakeQuestionLLM)
    monkeypatch.setattr(permission_next, "get_assistant_exec_policy", _test_exec_policy)

    def _guarded_execute_tool_stream(name: str, _arguments: dict, context=None):  # noqa: ANN001, ANN202
        del context
        if name == "question":
            pytest.fail("question tool should not execute local runtime directly")
        yield ToolResult(success=True, summary=f"{name} ok")

    monkeypatch.setattr(agent_service, "execute_tool_stream", _guarded_execute_tool_stream)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "question_pause_resume_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "plan",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/question_pause_resume_session/message",
        json={
            "parts": [{"type": "text", "text": "先分析方案，再继续"}],
            "mode": "plan",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "event: action_confirm" in prompt_resp.text

    pending = client.get("/session/question_pause_resume_session/permissions")
    assert pending.status_code == 200
    items = pending.json()
    assert len(items) == 1
    assert items[0]["permission"] == "question"
    assert items[0]["metadata"]["questions"][0]["header"] == "实现策略"

    reply_resp = client.post(
        f"/session/question_pause_resume_session/permissions/{items[0]['id']}",
        json={
            "response": "answer",
            "answers": [["保守实现 (Recommended)"]],
        },
    )
    assert reply_resp.status_code == 200
    assert "收到你的选择：保守实现 (Recommended)" in reply_resp.text
    assert client.get("/session/question_pause_resume_session/permissions").json() == []

    history = client.get("/session/question_pause_resume_session/message").json()
    assert len(history) == 3
    assert [item["info"]["role"] for item in history] == ["user", "assistant", "assistant"]
    first_assistant = history[1]
    second_assistant = history[2]
    assert first_assistant["info"]["id"] == items[0]["tool"]["messageID"]
    assert first_assistant["info"]["finish"] == "tool-calls"
    assert second_assistant["info"]["finish"] == "stop"
    assert second_assistant["info"]["id"] != first_assistant["info"]["id"]
    tool_parts = [part for part in first_assistant["parts"] if part["type"] == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["tool"] == "question"
    assert tool_parts[0]["state"]["status"] == "completed"
    assert tool_parts[0]["summary"] == "Asked 1 question"
    assert tool_parts[0]["data"]["answers"] == [["保守实现 (Recommended)"]]
    assert "收到你的选择：保守实现 (Recommended)" in _all_text_parts(second_assistant["parts"])


def test_native_permission_confirm_does_not_fall_back_to_wrapper_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_no_wrapper_confirm_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_no_wrapper_confirm_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/perm_no_wrapper_confirm_session/permissions").json()[0][
        "id"
    ]

    def _unexpected_persist(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("native confirm should not use wrap_stream_with_persistence")

    monkeypatch.setattr(session_runtime_module, "wrap_stream_with_persistence", _unexpected_persist)

    reply_resp = client.post(
        f"/session/perm_no_wrapper_confirm_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text
    assert "已确认，开始执行" not in reply_resp.text

    history = client.get("/session/perm_no_wrapper_confirm_session/message").json()
    assert len(history) == 3
    assert history[1]["info"]["finish"] == "tool-calls"
    assert history[2]["info"]["finish"] == "stop"
    assert history[2]["info"]["id"] != history[1]["info"]["id"]
    assert "工具已执行完成" in _all_text_parts(history[2]["parts"])


def test_native_permission_confirm_is_persisted_inside_native_resume_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_native_resume_persisted_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_native_resume_persisted_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/perm_native_resume_persisted_session/permissions").json()[
        0
    ]["id"]

    def _unexpected_inline_persist(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("native confirm should persist inside _respond_native_action_impl")

    monkeypatch.setattr(
        agent_service, "_persist_inline_stream_if_needed", _unexpected_inline_persist
    )

    reply_resp = client.post(
        f"/session/perm_native_resume_persisted_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text
    assert "已确认，开始执行" not in reply_resp.text

    history = client.get("/session/perm_native_resume_persisted_session/message").json()
    assert len(history) == 3
    assert history[1]["info"]["finish"] == "tool-calls"
    assert history[2]["info"]["finish"] == "stop"
    assert history[2]["info"]["id"] != history[1]["info"]["id"]
    assert "工具已执行完成" in _all_text_parts(history[2]["parts"])


def test_native_permission_confirm_does_not_use_apply_event_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_no_apply_event_bridge_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_no_apply_event_bridge_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/perm_no_apply_event_bridge_session/permissions").json()[0][
        "id"
    ]

    def _unexpected_apply_event(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("native confirm should not use SessionStreamPersistence.apply_event")

    monkeypatch.setattr(
        session_runtime_module.SessionStreamPersistence, "apply_event", _unexpected_apply_event
    )

    reply_resp = client.post(
        f"/session/perm_no_apply_event_bridge_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text
    assert "已确认，开始执行" not in reply_resp.text

    history = client.get("/session/perm_no_apply_event_bridge_session/message").json()
    assert len(history) == 3
    assert history[1]["info"]["finish"] == "tool-calls"
    assert history[2]["info"]["finish"] == "stop"
    assert history[2]["info"]["id"] != history[1]["info"]["id"]
    assert "工具已执行完成" in _all_text_parts(history[2]["parts"])


def test_native_permission_confirm_is_queued_into_session_callback_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_callback_loop_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_callback_loop_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/perm_callback_loop_session/permissions").json()[0]["id"]

    captured: dict[str, object] = {}
    original_queue = agent_service.queue_prompt_callback

    def _capture_queue(session_id: str, *, payload=None, front=False):  # noqa: ANN001, ANN202
        captured["session_id"] = session_id
        captured["payload"] = dict(payload or {})
        captured["front"] = front
        return original_queue(session_id, payload=payload, front=front)

    monkeypatch.setattr(agent_service, "queue_prompt_callback", _capture_queue)

    reply_resp = client.post(
        f"/session/perm_callback_loop_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text
    assert captured["session_id"] == "perm_callback_loop_session"
    assert captured["front"] is True
    assert captured["payload"] == {
        "kind": "permission",
        "session_id": "perm_callback_loop_session",
        "action_id": permission_id,
        "response": "once",
        "message": None,
    }


def test_native_permission_response_delegates_to_session_prompt_processor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_delegate_processor_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_delegate_processor_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/perm_delegate_processor_session/permissions").json()[0][
        "id"
    ]
    pending = agent_service.get_pending_action(permission_id)
    assert pending is not None

    captured: dict[str, object] = {}

    def _fake_stream_permission_response(
        cls,
        action_id: str,
        response: str,
        message: str | None,
        answers=None,
        *,
        pending,
        persistence=None,
        manage_session_lifecycle: bool = True,
    ):  # noqa: ANN001, ANN202
        captured["action_id"] = action_id
        captured["response"] = response
        captured["message"] = message
        captured["answers"] = answers
        captured["pending_action_id"] = pending.action_id
        captured["persistence"] = persistence
        captured["manage_session_lifecycle"] = manage_session_lifecycle
        yield 'event: text_delta\ndata: {"content":"delegated"}\n\n'
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(
        agent_service.SessionPromptProcessor,
        "stream_permission_response",
        classmethod(_fake_stream_permission_response),
    )

    persistence = agent_service._native_pending_persistence(pending)
    items = list(
        agent_service._respond_native_action_impl(
            permission_id,
            "once",
            None,
            pending=pending,
            persistence=persistence,
        )
    )

    assert (
        "".join(items)
        == 'event: text_delta\ndata: {"content":"delegated"}\n\nevent: done\ndata: {}\n\n'
    )
    assert captured["action_id"] == permission_id
    assert captured["response"] == "once"
    assert captured["message"] is None
    assert captured["pending_action_id"] == permission_id
    assert captured["persistence"] is persistence
    assert captured["manage_session_lifecycle"] is True


def test_native_permission_response_runtime_delegates_to_session_processor_helper(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.agent import agent_service

    _configure_test_db(monkeypatch)

    options = agent_service.AgentRuntimeOptions(
        session_id="perm_runtime_delegate_session",
        mode="build",
        workspace_path="D:/workspace",
    )
    pending = agent_service.PendingAction(
        action_id="perm_runtime_delegate",
        options=options,
        permission_request={
            "id": "perm_runtime_delegate",
            "tool": {
                "callID": "call_perm_runtime_delegate",
                "name": "bash",
                "arguments": {"command": "echo hi"},
                "messageID": "message_perm_runtime_delegate",
            },
        },
        continuation={"kind": "native_prompt", "messages": []},
    )
    resume_state = agent_service.session_pending.PendingResumeState(
        step_index=2,
        assistant_message_id="message_perm_runtime_delegate",
    )
    captured: dict[str, object] = {}

    def _fake_stream_permission_runtime(config, callbacks):  # noqa: ANN001, ANN202
        captured["config"] = config
        captured["callbacks"] = callbacks
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(
        agent_service,
        "_processor_stream_permission_response_runtime",
        _fake_stream_permission_runtime,
    )

    class _FakeControl:
        def absorb(self, _value):  # noqa: ANN001, ANN202
            return None

    class _FakeLifecycle:
        def __init__(self) -> None:
            self.control = _FakeControl()

        def emit_event(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            if False:
                yield ""

        def emit_done(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            if False:
                yield ""

        def observe_prompt_events(self, stream, **_kwargs):  # noqa: ANN001, ANN202
            yield from stream

        def observe_stream(self, stream, **_kwargs):  # noqa: ANN001, ANN202
            yield from stream

    processor = agent_service.SessionPromptProcessor(
        messages=[],
        options=options,
        step_index=2,
        assistant_message_id="message_perm_runtime_delegate",
        lifecycle_kind="resume",
        resume_existing=True,
    )

    items = list(
        processor._stream_permission_response(
            _FakeLifecycle(),
            action_id="perm_runtime_delegate",
            response="once",
            message=None,
            answers=None,
            pending=pending,
            resume_state=resume_state,
        )
    )

    assert items == ["event: done\ndata: {}\n\n"]
    config = captured["config"]
    assert config is not None
    assert config.action_id == "perm_runtime_delegate"
    assert config.response == "once"
    assert config.resume_state.assistant_message_id == "message_perm_runtime_delegate"
    assert config.resume_state.step_index == 2


def test_permission_callback_processor_reuses_stream_active_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)

    from packages.agent import agent_service

    options = agent_service.AgentRuntimeOptions(
        session_id="perm_callback_stream_active_session",
        mode="build",
        workspace_path=str(tmp_path),
    )
    pending = agent_service.PendingAction(
        action_id="perm_callback_stream_active",
        options=options,
        permission_request={
            "id": "perm_callback_stream_active",
            "tool": {
                "callID": "call_perm_callback_stream_active",
                "name": "bash",
                "arguments": {"command": "echo hi"},
                "messageID": "message_perm_callback_stream_active",
            },
        },
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(agent_service, "get_pending_action", lambda _action_id: pending)
    monkeypatch.setattr(
        agent_service,
        "_native_pending_resume_state",
        lambda _pending: agent_service.session_pending.PendingResumeState(
            request_message_id="request_perm_callback_stream_active",
            step_index=2,
            assistant_message_id="message_perm_callback_stream_active",
        ),
    )
    monkeypatch.setattr(
        agent_service,
        "_merge_pending_persistence",
        lambda _pending, _persistence=None: agent_service.StreamPersistenceConfig(
            session_id=options.session_id,
            parent_id="request_perm_callback_stream_active",
            assistant_meta={
                "mode": options.mode,
                "agent": options.mode,
                "cwd": str(tmp_path),
                "root": str(tmp_path),
                "variant": "default",
            },
            assistant_message_id="message_perm_callback_stream_active",
        ),
    )

    def _fake_stream_permission_runtime(config, callbacks):  # noqa: ANN001, ANN202
        captured["config"] = config
        captured["callbacks"] = callbacks
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(
        agent_service,
        "_processor_stream_permission_response_runtime",
        _fake_stream_permission_runtime,
    )

    callback = SimpleNamespace(
        session_id=options.session_id,
        payload={
            "kind": "permission",
            "session_id": options.session_id,
            "action_id": "perm_callback_stream_active",
            "response": "once",
            "message": "approved",
        },
    )

    processor = agent_service.SessionPromptProcessor._processor_from_callback(
        callback,
        resume_existing=True,
        manage_session_lifecycle=False,
    )

    assert processor.queued_permission_response is not None
    assert processor.queued_permission_response.action_id == "perm_callback_stream_active"

    items = list(processor._stream_active())

    assert items[-1] == "event: done\ndata: {}\n\n"
    config = captured["config"]
    assert config is not None
    assert config.action_id == "perm_callback_stream_active"
    assert config.response == "once"
    assert config.message == "approved"
    assert config.resume_state.assistant_message_id == "message_perm_callback_stream_active"


def test_aborted_native_permission_reply_cancels_on_same_resume_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_abort_resume_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_abort_resume_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/perm_abort_resume_session/permissions").json()[0]["id"]

    monkeypatch.setattr(
        agent_service,
        "execute_tool_stream",
        lambda *_args, **_kwargs: pytest.fail("aborted permission resume should not execute tool"),
    )

    abort_resp = client.post("/session/perm_abort_resume_session/abort")
    assert abort_resp.status_code == 200

    reply_resp = client.post(
        f"/session/perm_abort_resume_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "会话已中止" in reply_resp.text
    assert client.get("/session/perm_abort_resume_session/permissions").json() == []


def test_native_permission_confirm_resume_loads_persisted_session_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_resume_history_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_resume_history_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/perm_resume_history_session/permissions").json()[0]["id"]

    original_load_messages = agent_service.load_agent_messages
    load_calls: list[tuple[str | None, dict]] = []

    def _capture_load_messages(session_id, *args, **kwargs):  # noqa: ANN001, ANN202
        load_calls.append((session_id, dict(kwargs)))
        return original_load_messages(session_id, *args, **kwargs)

    monkeypatch.setattr(agent_service, "load_agent_messages", _capture_load_messages)

    reply_resp = client.post(
        f"/session/perm_resume_history_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text
    assert any(
        session_id == "perm_resume_history_session" and kwargs == {}
        for session_id, kwargs in load_calls
    )


def test_native_pending_action_persistence_omits_continuation_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_thin_pending_action_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_thin_pending_action_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200

    permission_id = client.get("/session/perm_thin_pending_action_session/permissions").json()[0][
        "id"
    ]
    with session_scope() as session:
        row = AgentPendingActionRepository(session).get(permission_id)
        assert row is not None
        continuation_json = dict(row.continuation_json or {})
        permission_json = dict(row.permission_json or {})

    assert "messages" not in continuation_json
    assert "tool_calls" not in continuation_json
    assert "step_index" not in continuation_json
    assert "assistant_message_id" not in continuation_json
    assert "step_snapshot" not in continuation_json
    assert "step_usage" not in continuation_json
    assert str((permission_json.get("tool") or {}).get("messageID") or "").strip()

    pending = agent_service.get_pending_action(permission_id)
    assert pending is not None
    assert pending.options.session_id == "perm_thin_pending_action_session"
    assert pending.kind == "native_prompt"
    assert pending.continuation is None
    assert str(
        ((pending.permission_request or {}).get("tool") or {}).get("messageID") or ""
    ).strip()


def test_native_pending_persistence_prefers_permission_request_parent_message(tmp_path: Path):
    session_id = "permission_parent_cursor_session"
    ensure_session_record(
        session_id,
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="build",
    )
    first_user = session_runtime_module.append_session_message(
        session_id=session_id,
        role="user",
        content="first",
    )
    assistant = session_runtime_module.append_session_message(
        session_id=session_id,
        role="assistant",
        content="partial",
        parent_id=str(first_user["info"]["id"]),
        meta={"finish": "tool-calls"},
    )
    second_user = session_runtime_module.append_session_message(
        session_id=session_id,
        role="user",
        content="later prompt",
    )

    pending = agent_service.PendingAction(
        action_id="permission_parent_cursor",
        options=agent_service.AgentRuntimeOptions(
            session_id=session_id,
            mode="build",
            workspace_path=str(tmp_path),
        ),
        permission_request={
            "tool": {
                "callID": "call_parent_cursor",
                "messageID": assistant["info"]["id"],
            },
            "metadata": {
                "tool": "bash",
                "arguments": {"command": "pytest -q"},
            },
        },
    )

    context = agent_service._native_pending_context(pending)
    persistence = agent_service._native_pending_persistence(pending)

    assert str(second_user["info"]["id"]) != str(first_user["info"]["id"])
    assert context["request_message_id"] == first_user["info"]["id"]
    assert persistence.parent_id == first_user["info"]["id"]


def test_native_pending_tool_calls_fall_back_to_permission_request_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)

    pending = agent_service.PendingAction(
        action_id="permission_tool_fallback",
        options=agent_service.AgentRuntimeOptions(
            session_id="permission_tool_fallback_session",
            mode="build",
            workspace_path=str(tmp_path),
        ),
        permission_request={
            "tool": {
                "callID": "call_fallback",
                "messageID": "message_fallback",
            },
            "metadata": {
                "tool": "write_workspace_file",
                "arguments": {
                    "relative_path": "notes.txt",
                    "content": "hello",
                },
            },
        },
    )

    calls = agent_service._native_pending_tool_calls(pending)
    assert len(calls) == 1
    assert calls[0].id == "call_fallback"
    assert calls[0].name == "write_workspace_file"
    assert calls[0].arguments["relative_path"] == "notes.txt"
    assert calls[0].arguments["workspace_path"] == str(tmp_path)


def test_native_permission_confirm_repause_keeps_session_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(agent_service, "LLMClient", FakeDoublePermissionLLM)
    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_execute_tool_stream)
    monkeypatch.setattr(permission_next, "get_assistant_exec_policy", _test_exec_policy)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_repause_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_repause_session/message",
        json={
            "parts": [{"type": "text", "text": "请连续运行两次测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "event: action_confirm" in prompt_resp.text
    permission_id = client.get("/session/perm_repause_session/permissions").json()[0]["id"]

    reply_resp = client.post(
        f"/session/perm_repause_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "event: action_confirm" in reply_resp.text
    assert "已确认，开始执行" not in reply_resp.text
    assert session_runtime_module.get_session_status("perm_repause_session")["type"] == "busy"

    pending = client.get("/session/perm_repause_session/permissions").json()
    assert len(pending) == 1

    history = client.get("/session/perm_repause_session/message").json()
    assert len(history) == 3
    first_assistant = history[1]
    second_assistant = history[2]
    assert first_assistant["info"]["finish"] == "tool-calls"
    assert second_assistant["info"]["finish"] is None
    assert second_assistant["info"]["id"] != first_assistant["info"]["id"]
    first_tool_parts = [part for part in first_assistant["parts"] if part["type"] == "tool"]
    assert len(first_tool_parts) == 1
    assert first_tool_parts[0]["state"]["status"] == "completed"
    second_tool_parts = [part for part in second_assistant["parts"] if part["type"] == "tool"]
    assert len(second_tool_parts) == 1
    assert second_tool_parts[0]["state"]["status"] == "pending"
    assert pending[0]["tool"]["messageID"] == second_assistant["info"]["id"]


def test_session_permission_persists_across_runtime_cache_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_persisted_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_persisted_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "event: action_confirm" in prompt_resp.text

    permission_id = client.get("/session/perm_persisted_session/permissions").json()[0]["id"]

    persisted = client.get("/session/perm_persisted_session/permissions")
    assert persisted.status_code == 200
    items = persisted.json()
    assert len(items) == 1
    assert items[0]["id"] == permission_id

    reply_resp = client.post(
        f"/session/perm_persisted_session/permissions/{permission_id}",
        json={"response": "once"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text
    assert "已确认，开始执行" not in reply_resp.text
    assert client.get("/session/perm_persisted_session/permissions").json() == []

    history = client.get("/session/perm_persisted_session/message").json()
    assert len(history) == 3
    assert history[1]["info"]["finish"] == "tool-calls"
    assert history[2]["info"]["finish"] == "stop"
    assert history[2]["info"]["id"] != history[1]["info"]["id"]
    assert "工具已执行完成" in _all_text_parts(history[2]["parts"])


def test_permission_always_persists_project_rule_and_skips_repeat_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_always_1",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_always_1/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert "event: action_confirm" in prompt_resp.text
    permission_id = client.get("/session/perm_always_1/permissions").json()[0]["id"]

    reply_resp = client.post(
        f"/session/perm_always_1/permissions/{permission_id}",
        json={"response": "always"},
    )
    assert reply_resp.status_code == 200
    assert "工具已执行完成" in reply_resp.text

    rules = permission_next.get_project_rules(get_session_record("perm_always_1")["projectID"])
    assert any(
        rule.get("permission") == "bash"
        and rule.get("pattern") == "pytest -q"
        and rule.get("action") == "allow"
        for rule in rules
    )

    created_again = client.post(
        "/session",
        json={
            "id": "perm_always_2",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created_again.status_code == 200

    second_prompt = client.post(
        "/session/perm_always_2/message",
        json={
            "parts": [{"type": "text", "text": "请再次运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert second_prompt.status_code == 200
    assert "event: action_confirm" not in second_prompt.text
    assert "工具已执行完成" in second_prompt.text
    assert client.get("/session/perm_always_2/permissions").json() == []


def test_session_permission_reject_resumes_with_feedback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "perm_reject_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/perm_reject_session/message",
        json={
            "parts": [{"type": "text", "text": "请运行测试"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert "event: action_confirm" in prompt_resp.text
    permission_id = client.get("/session/perm_reject_session/permissions").json()[0]["id"]

    reject_resp = client.post(
        f"/session/perm_reject_session/permissions/{permission_id}",
        json={"response": "reject", "message": "改用只读方案"},
    )
    assert reject_resp.status_code == 200
    assert "收到拒绝反馈：改用只读方案" in reject_resp.text
    assert "已取消该操作" not in reject_resp.text
    assert client.get("/session/perm_reject_session/permissions").json() == []

    history = client.get("/session/perm_reject_session/message").json()
    assert len(history) == 3
    assert history[1]["info"]["finish"] == "tool-calls"
    assert history[2]["info"]["finish"] == "stop"
    assert history[2]["info"]["id"] != history[1]["info"]["id"]
    assert "收到拒绝反馈：改用只读方案" in _all_text_parts(history[2]["parts"])
    tool_parts = [part for part in history[1]["parts"] if part["type"] == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["state"]["status"] == "error"
    assert tool_parts[0]["summary"] == "用户拒绝执行该操作：改用只读方案"


def test_legacy_confirm_route_persists_follow_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _patch_permission_runtime(monkeypatch)
    client = TestClient(_build_app())

    prompt_resp = client.post(
        "/agent/chat",
        json={
            "messages": [{"role": "user", "content": "请运行测试"}],
            "session_id": "legacy_perm_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
        },
    )
    assert prompt_resp.status_code == 200
    assert "event: action_confirm" in prompt_resp.text

    permission_id = client.get("/session/legacy_perm_session/permissions").json()[0]["id"]
    confirm_resp = client.post(f"/agent/confirm/{permission_id}")
    assert confirm_resp.status_code == 200
    assert "工具已执行完成" in confirm_resp.text

    messages = client.get("/agent/conversations/legacy_perm_session").json()["messages"]
    assert len(messages) == 3
    assert [message["role"] for message in messages] == ["user", "assistant", "assistant"]
    assert "工具已执行完成" in messages[2]["content"]


def test_custom_acp_permission_confirm_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _configure_acp_service(
        tmp_path,
        monkeypatch,
        command=sys.executable,
        args=[str(Path(__file__).parent / "fixtures" / "mock_acp_permission_server.py")],
    )
    monkeypatch.setattr(agent_service, "get_assistant_exec_policy", _test_exec_policy)
    client = TestClient(_build_app())

    prompt_resp = client.post(
        "/agent/chat",
        json={
            "messages": [{"role": "user", "content": "请继续处理"}],
            "session_id": "custom_acp_permission_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
            "agent_backend_id": "custom_acp",
        },
    )
    assert prompt_resp.status_code == 200
    assert "event: action_confirm" in prompt_resp.text
    assert "Permission required" in prompt_resp.text

    pending = client.get("/session/custom_acp_permission_session/permissions")
    assert pending.status_code == 200
    items = pending.json()
    assert len(items) == 1
    assert items[0]["permission"] == "acp_bash"

    confirm_resp = client.post(f"/agent/confirm/{items[0]['id']}")
    assert confirm_resp.status_code == 200
    assert "已确认，继续执行 ACP 权限请求" in confirm_resp.text
    assert "Permission outcome: allow_once" in confirm_resp.text

    messages = client.get("/agent/conversations/custom_acp_permission_session").json()["messages"]
    assert len(messages) == 2
    assert "Permission required" in messages[1]["content"]
    assert "Permission outcome: allow_once" in messages[1]["content"]


def test_custom_acp_abort_clears_paused_permission_and_publishes_aborted_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _configure_acp_service(
        tmp_path,
        monkeypatch,
        command=sys.executable,
        args=[str(Path(__file__).parent / "fixtures" / "mock_acp_permission_server.py")],
    )
    monkeypatch.setattr(agent_service, "get_assistant_exec_policy", _test_exec_policy)
    client = TestClient(_build_app())

    events: list[tuple[str, dict[str, object]]] = []
    unsubscribe = session_bus.subscribe_all(
        lambda event: events.append((event.type, dict(event.properties or {})))
    )
    try:
        prompt_resp = client.post(
            "/agent/chat",
            json={
                "messages": [{"role": "user", "content": "请暂停，等我手动中止"}],
                "session_id": "custom_acp_abort_session",
                "workspace_path": str(tmp_path),
                "mode": "build",
                "reasoning_level": "medium",
                "active_skill_ids": [],
                "agent_backend_id": "custom_acp",
            },
        )
        assert prompt_resp.status_code == 200
        assert "event: action_confirm" in prompt_resp.text

        pending = client.get("/session/custom_acp_abort_session/permissions")
        assert pending.status_code == 200
        items = pending.json()
        assert len(items) == 1

        abort_resp = client.post("/session/custom_acp_abort_session/abort")
        assert abort_resp.status_code == 200
        assert abort_resp.json() is True

        assert client.get("/session/custom_acp_abort_session/permissions").json() == []
        assert (
            session_runtime_module.get_session_status("custom_acp_abort_session")["type"] == "idle"
        )

        history = client.get("/session/custom_acp_abort_session/message").json()
        assistant = history[-1]
        assert assistant["info"]["role"] == "assistant"
        assert assistant["info"]["finish"] == "aborted"
        assert assistant["info"]["error"]["message"] == "会话已中止"

        assert any(
            event_type == SessionBusEvent.MESSAGE_UPDATED
            and str((properties.get("message") or {}).get("info", {}).get("finish") or "")
            == "aborted"
            for event_type, properties in events
        )
        assert any(
            event_type == SessionBusEvent.IDLE
            and str(properties.get("sessionID") or "") == "custom_acp_abort_session"
            for event_type, properties in events
        )
    finally:
        unsubscribe()


def test_custom_acp_confirm_does_not_fall_back_to_wrapper_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _configure_acp_service(
        tmp_path,
        monkeypatch,
        command=sys.executable,
        args=[str(Path(__file__).parent / "fixtures" / "mock_acp_permission_server.py")],
    )
    monkeypatch.setattr(agent_service, "get_assistant_exec_policy", _test_exec_policy)
    client = TestClient(_build_app())

    prompt_resp = client.post(
        "/agent/chat",
        json={
            "messages": [{"role": "user", "content": "请继续处理"}],
            "session_id": "custom_acp_no_wrapper_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
            "agent_backend_id": "custom_acp",
        },
    )
    assert prompt_resp.status_code == 200
    permission_id = client.get("/session/custom_acp_no_wrapper_session/permissions").json()[0]["id"]

    def _unexpected_persist(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("custom acp confirm should not use wrap_stream_with_persistence")

    monkeypatch.setattr(session_runtime_module, "wrap_stream_with_persistence", _unexpected_persist)

    confirm_resp = client.post(f"/agent/confirm/{permission_id}")
    assert confirm_resp.status_code == 200
    assert "已确认，继续执行 ACP 权限请求" in confirm_resp.text
    assert "Permission outcome: allow_once" in confirm_resp.text

    messages = client.get("/agent/conversations/custom_acp_no_wrapper_session").json()["messages"]
    assert len(messages) == 2
    assert "Permission outcome: allow_once" in messages[1]["content"]


def test_custom_acp_http_permission_confirm_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    with serve_mock_acp_http_permission_server() as server_url:
        _configure_http_acp_service(
            tmp_path,
            monkeypatch,
            url=server_url,
        )
        monkeypatch.setattr(agent_service, "get_assistant_exec_policy", _test_exec_policy)
        client = TestClient(_build_app())

        prompt_resp = client.post(
            "/agent/chat",
            json={
                "messages": [{"role": "user", "content": "请继续处理"}],
                "session_id": "custom_acp_http_permission_session",
                "workspace_path": str(tmp_path),
                "mode": "build",
                "reasoning_level": "medium",
                "active_skill_ids": [],
                "agent_backend_id": "custom_acp",
            },
        )
        assert prompt_resp.status_code == 200
        assert "event: action_confirm" in prompt_resp.text
        assert "Permission required" in prompt_resp.text

        pending = client.get("/session/custom_acp_http_permission_session/permissions")
        assert pending.status_code == 200
        items = pending.json()
        assert len(items) == 1
        assert items[0]["permission"] == "acp_bash"

        confirm_resp = client.post(f"/agent/confirm/{items[0]['id']}")
        assert confirm_resp.status_code == 200
        assert "已确认，继续执行 ACP 权限请求" in confirm_resp.text
        assert "Permission outcome: allow_once" in confirm_resp.text

        messages = client.get("/agent/conversations/custom_acp_http_permission_session").json()[
            "messages"
        ]
        assert len(messages) == 2
        assert "Permission required" in messages[1]["content"]
        assert "Permission outcome: allow_once" in messages[1]["content"]


def test_custom_acp_permission_auto_confirms_when_approval_is_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _configure_acp_service(
        tmp_path,
        monkeypatch,
        command=sys.executable,
        args=[str(Path(__file__).parent / "fixtures" / "mock_acp_permission_server.py")],
    )
    monkeypatch.setattr(
        agent_service,
        "get_assistant_exec_policy",
        lambda: {
            "workspace_access": "read_write",
            "command_execution": "full",
            "approval_mode": "off",
            "allowed_command_prefixes": [],
        },
    )
    client = TestClient(_build_app())

    prompt_resp = client.post(
        "/agent/chat",
        json={
            "messages": [{"role": "user", "content": "请继续处理"}],
            "session_id": "custom_acp_auto_confirm_session",
            "workspace_path": str(tmp_path),
            "mode": "build",
            "reasoning_level": "medium",
            "active_skill_ids": [],
            "agent_backend_id": "custom_acp",
        },
    )
    assert prompt_resp.status_code == 200
    assert "event: action_confirm" not in prompt_resp.text
    assert "Permission required" in prompt_resp.text
    assert "Permission outcome: allow_always" in prompt_resp.text

    pending = client.get("/session/custom_acp_auto_confirm_session/permissions")
    assert pending.status_code == 200
    assert pending.json() == []

    messages = client.get("/agent/conversations/custom_acp_auto_confirm_session").json()["messages"]
    assert len(messages) == 2
    assert "Permission required" in messages[1]["content"]
    assert "Permission outcome: allow_always" in messages[1]["content"]


def test_build_turn_tools_appends_official_openai_builtin_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    class FakeOfficialOpenAILLM:
        last_stage: str | None = None

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            FakeOfficialOpenAILLM.last_stage = _args[0] if _args else None
            return type(
                "ModelTarget",
                (),
                {
                    "provider": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-5.2",
                },
            )()

    monkeypatch.setattr(
        tool_registry,
        "get_openai_tools",
        lambda *_args, **_kwargs: [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "run shell",
                    "parameters": {},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "search web",
                    "parameters": {},
                },
            },
        ],
    )

    tools = agent_service._build_turn_tools(
        FakeOfficialOpenAILLM(),
        agent_service.AgentRuntimeOptions(
            session_id="builtin_tools_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
        disabled_tools=set(),
    )

    provider_defined_ids = {
        str(tool.get("id") or "")
        for tool in tools
        if str(tool.get("type") or "") == "provider-defined"
    }
    assert FakeOfficialOpenAILLM.last_stage == "chat"
    assert "openai.local_shell" in provider_defined_ids
    assert "openai.web_search" in provider_defined_ids


def test_build_turn_tools_prefers_apply_patch_for_gpt5_models():
    class FakeGpt5LLM:
        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="openai", base_url="https://api.openai.com/v1", model="gpt-5.2"
            )

    tools = tool_registry.build_turn_tools(FakeGpt5LLM(), mode="build")
    function_names = {
        str(
            ((tool.get("function") or {}) if isinstance(tool.get("function"), dict) else {}).get(
                "name"
            )
            or ""
        )
        for tool in tools
        if str(tool.get("type") or "") == "function"
    }

    assert "apply_patch" in function_names
    assert "edit" not in function_names
    assert "write" not in function_names


def test_build_turn_tools_prefers_edit_and_write_for_non_gpt5_models():
    class FakeClaudeLLM:
        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            return SimpleNamespace(
                provider="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-3-7-sonnet",
            )

    tools = tool_registry.build_turn_tools(FakeClaudeLLM(), mode="build")
    function_names = {
        str(
            ((tool.get("function") or {}) if isinstance(tool.get("function"), dict) else {}).get(
                "name"
            )
            or ""
        )
        for tool in tools
        if str(tool.get("type") or "") == "function"
    }

    assert "apply_patch" not in function_names
    assert "edit" in function_names
    assert "write" in function_names


def test_build_turn_tools_respects_latest_user_tool_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    class FakeOfficialOpenAILLM:
        last_stage: str | None = None

        def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
            FakeOfficialOpenAILLM.last_stage = _args[0] if _args else None
            return type(
                "ModelTarget",
                (),
                {
                    "provider": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-5.2",
                },
            )()

    captured: dict[str, set[str]] = {}

    def _fake_get_openai_tools(*_args, **kwargs):  # noqa: ANN001, ANN202
        disabled = set(kwargs.get("disabled_tools") or set())
        captured["disabled"] = disabled
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "search web",
                    "parameters": {},
                },
            }
        ]
        if "bash" not in disabled:
            tools.insert(
                0,
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "description": "run shell",
                        "parameters": {},
                    },
                },
            )
        return tools

    monkeypatch.setattr(tool_registry, "get_openai_tools", _fake_get_openai_tools)

    tools = agent_service._build_turn_tools(
        FakeOfficialOpenAILLM(),
        agent_service.AgentRuntimeOptions(
            session_id="builtin_tools_override_session",
            mode="build",
            workspace_path=str(tmp_path),
            reasoning_level="medium",
        ),
        disabled_tools=set(),
        user_tools={"bash": False, "search_web": True},
    )

    function_names = {
        str(
            ((tool.get("function") or {}) if isinstance(tool.get("function"), dict) else {}).get(
                "name"
            )
            or ""
        )
        for tool in tools
        if str(tool.get("type") or "") == "function"
    }
    provider_defined_ids = {
        str(tool.get("id") or "")
        for tool in tools
        if str(tool.get("type") or "") == "provider-defined"
    }
    assert captured["disabled"] == {"bash"}
    assert FakeOfficialOpenAILLM.last_stage == "chat"
    assert "bash" not in function_names
    assert "search_web" in function_names
    assert "openai.local_shell" not in provider_defined_ids
    assert "openai.web_search" in provider_defined_ids


def test_authorize_local_shell_respects_bash_permission_rules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)

    decision = permission_next.authorize_tool_call(
        agent_service.ToolCall(
            id="call_local_shell_perm_1",
            name="local_shell",
            arguments={
                "action": {
                    "type": "exec",
                    "command": ["pytest", "-q"],
                }
            },
        ),
        {
            "id": "local_shell_perm_session",
            "projectID": "global",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "permission": None,
        },
        policy=_test_exec_policy(),
        create_pending_request=False,
    )

    assert decision.status == "ask"
    assert decision.permission == "bash"
    assert decision.patterns == ["pytest -q", "pytest -q *"]


def test_plan_mode_authorize_tool_call_allows_only_plan_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)

    session = ensure_session_record(
        session_id="plan_mode_permission_session",
        directory=str(tmp_path),
        workspace_path=str(tmp_path),
        mode="plan",
    )
    plan_info = session_plan.resolve_session_plan_info(session)
    assert plan_info is not None

    denied = permission_next.authorize_tool_call(
        agent_service.ToolCall(
            id="call_plan_deny_1",
            name="write",
            arguments={"file_path": str(tmp_path / "notes.md"), "content": "# notes"},
        ),
        session,
        policy=_test_exec_policy(),
        create_pending_request=False,
    )

    assert denied.status == "deny"
    assert plan_info.path in str(denied.reason or "")

    allowed = permission_next.authorize_tool_call(
        agent_service.ToolCall(
            id="call_plan_allow_1",
            name="write",
            arguments={"file_path": plan_info.path, "content": "# plan"},
        ),
        session,
        policy=_test_exec_policy(),
        create_pending_request=False,
    )

    assert allowed.status == "ask"
    assert allowed.permission == "edit"


def test_execute_tool_stream_local_shell_returns_output(tmp_path: Path):
    events = list(
        execute_tool_stream(
            "local_shell",
            {
                "action": {
                    "type": "exec",
                    "command": ["Write-Output", "local-shell-ok"],
                    "workingDirectory": str(tmp_path),
                }
            },
            context=AgentToolContext(
                session_id="local_shell_exec_session",
                mode="build",
                workspace_path=str(tmp_path),
            ),
        )
    )

    assert len(events) == 1
    result = events[0]
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data["exit_code"] == 0
    assert "local-shell-ok" in result.data["output"]


def test_plan_mode_execute_tool_stream_allows_read_only_bash(tmp_path: Path):
    events = list(
        execute_tool_stream(
            "bash",
            {
                "command": "Get-Location",
                "workdir": str(tmp_path),
            },
            context=AgentToolContext(
                session_id="plan_bash_read_only_session",
                mode="plan",
                workspace_path=str(tmp_path),
            ),
        )
    )

    assert len(events) == 1
    result = events[0]
    assert isinstance(result, ToolResult)
    assert result.success is True


def test_plan_mode_execute_tool_stream_denies_mutating_bash(tmp_path: Path):
    events = list(
        execute_tool_stream(
            "bash",
            {
                "command": "New-Item notes.txt -ItemType File",
                "workdir": str(tmp_path),
            },
            context=AgentToolContext(
                session_id="plan_bash_mutating_session",
                mode="plan",
                workspace_path=str(tmp_path),
            ),
        )
    )

    assert len(events) == 1
    result = events[0]
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert "read-only shell inspection commands" in result.summary
