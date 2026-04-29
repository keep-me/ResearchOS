from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.agent import session_message_v2
from packages.agent.session.session_runtime import append_session_message, ensure_session_record
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


def test_message_v2_page_stream_get_and_parts(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)
    ensure_session_record("message_v2_page_session")

    first = append_session_message(
        session_id="message_v2_page_session",
        role="user",
        content="first",
        message_id="msg_1",
    )
    second = append_session_message(
        session_id="message_v2_page_session",
        role="assistant",
        content="second",
        parent_id="msg_1",
        message_id="msg_2",
        meta={"finish": "stop"},
    )
    third = append_session_message(
        session_id="message_v2_page_session",
        role="user",
        content="third",
        message_id="msg_3",
    )

    page = session_message_v2.page("message_v2_page_session", 2)
    page_ids = [item["info"]["id"] for item in page["items"]]
    assert page_ids == ["msg_2", "msg_3"]
    assert page["more"] is True
    assert session_message_v2.cursor_decode(page["cursor"]) == {
        "id": "msg_2",
        "time": second["info"]["time"]["created"],
    }

    next_page = session_message_v2.page("message_v2_page_session", 2, before=page["cursor"])
    assert [item["info"]["id"] for item in next_page["items"]] == ["msg_1"]
    assert next_page["more"] is False

    streamed_ids = [item["info"]["id"] for item in session_message_v2.stream("message_v2_page_session")]
    assert streamed_ids == ["msg_3", "msg_2", "msg_1"]

    fetched = session_message_v2.get("message_v2_page_session", "msg_2")
    assert fetched["info"]["id"] == "msg_2"
    assert fetched["info"]["parentID"] == "msg_1"
    assert session_message_v2.parts("msg_1")[0]["text"] == "first"
    assert first["parts"][0]["messageID"] == "msg_1"
    assert third["parts"][0]["messageID"] == "msg_3"


def test_message_v2_filter_compacted_and_to_model_messages():
    newest_user = {
        "info": {"id": "u_latest", "role": "user", "sessionID": "session_a"},
        "parts": [{"id": "p_latest", "type": "text", "text": "latest question"}],
    }
    assistant_summary = {
        "info": {
            "id": "a_summary",
            "role": "assistant",
            "sessionID": "session_a",
            "parentID": "u_compact",
            "summary": True,
            "finish": "stop",
        },
        "parts": [{"id": "p_summary", "type": "text", "text": "summary answer"}],
    }
    compacted_user = {
        "info": {"id": "u_compact", "role": "user", "sessionID": "session_a"},
        "parts": [{"id": "p_compaction", "type": "compaction", "auto": True}],
    }
    old_assistant = {
        "info": {"id": "a_old", "role": "assistant", "sessionID": "session_a", "parentID": "u_old"},
        "parts": [{"id": "p_old", "type": "text", "text": "should be hidden"}],
    }

    filtered = session_message_v2.filter_compacted([newest_user, assistant_summary, compacted_user, old_assistant])
    assert [item["info"]["id"] for item in filtered] == ["u_compact", "a_summary", "u_latest"]

    converted = session_message_v2.to_model_messages(
        [
            {
                "info": {
                    "id": "u_model",
                    "role": "user",
                    "sessionID": "session_b",
                    "variant": "medium",
                },
                "parts": [
                    {"id": "u_text", "type": "text", "text": "read this"},
                    {
                        "id": "u_file",
                        "type": "file",
                        "url": "https://example.com/paper.pdf",
                        "filename": "paper.pdf",
                        "mime": "application/pdf",
                    },
                ],
            },
            {
                "info": {
                    "id": "a_model",
                    "role": "assistant",
                    "sessionID": "session_b",
                    "parentID": "u_model",
                    "providerMetadata": {"response_id": "resp_1"},
                },
                "parts": [
                    {"id": "step_1", "type": "step-start", "step": 1},
                    {"id": "r_1", "type": "reasoning", "text": "think"},
                    {"id": "t_1", "type": "text", "text": "answer"},
                    {
                        "id": "tool_1",
                        "type": "tool",
                        "tool": "bash",
                        "callID": "call_1",
                        "summary": "ran command",
                        "data": {"stdout": "done"},
                        "state": {
                            "status": "completed",
                            "input": {"command": "pwd"},
                            "raw": "{\"command\":\"pwd\"}",
                        },
                    },
                ],
            },
        ],
        {"providerID": "openai", "modelID": "gpt-5"},
        strip_media=True,
    )

    assert converted[0]["role"] == "user"
    assert isinstance(converted[0]["content"], list)
    assert converted[0]["content"][1]["text"].startswith("[Attached application/pdf")
    assert converted[1]["role"] == "assistant"
    assert converted[1]["content"] == "answer"
    assert converted[1]["reasoning_content"] == "think"
    assert converted[1]["tool_calls"][0]["function"]["name"] == "bash"
    assert converted[2]["role"] == "tool"
    payload = json.loads(converted[2]["content"])
    assert payload["success"] is True
    assert payload["data"]["stdout"] == "done"


def test_message_v2_from_error_adds_provider_context():
    payload = session_message_v2.from_error(
        {"name": "AuthError", "message": "missing key"},
        {"providerID": "openai"},
    )
    assert payload["name"] == "AuthError"
    assert payload["providerID"] == "openai"


def test_user_message_display_text_is_preserved_in_info(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)
    ensure_session_record("message_v2_display_text_session")

    user = append_session_message(
        session_id="message_v2_display_text_session",
        role="user",
        content="hidden context\n\n用户本轮问题：\n请总结导入的报告",
        message_id="msg_user_display",
        meta={"displayText": "请总结导入的报告"},
    )

    assert user["info"]["displayText"] == "请总结导入的报告"
    assert user["parts"][0]["text"].startswith("hidden context")


def test_message_v2_runtime_info_aligns_user_and_assistant_shapes(monkeypatch: pytest.MonkeyPatch):
    _configure_test_db(monkeypatch)
    ensure_session_record("message_v2_info_session")

    user = append_session_message(
        session_id="message_v2_info_session",
        role="user",
        content="请输出结构化结果",
        message_id="msg_user_info",
        meta={
            "agent": "build",
            "model": {"providerID": "openai", "modelID": "gpt-5"},
            "format": {"type": "json_schema", "schema": {"type": "object", "properties": {"answer": {"type": "string"}}}},
            "tools": {"bash": False},
            "system": "只返回 JSON",
            "variant": "high",
        },
    )
    assistant = append_session_message(
        session_id="message_v2_info_session",
        role="assistant",
        content="{}",
        parent_id="msg_user_info",
        message_id="msg_assistant_info",
        meta={
            "providerID": "openai",
            "modelID": "gpt-5",
            "mode": "build",
            "agent": "build",
            "cwd": "D:/workspace",
            "root": "D:/workspace",
            "tokens": {"input": 10, "output": 6, "reasoning": 2, "cache": {"read": 1, "write": 0}},
            "cost": 0.25,
            "finish": "stop",
            "completed": 1234567890,
            "structured": {"answer": "ok"},
        },
    )

    assert user["info"] == {
        "id": "msg_user_info",
        "sessionID": "message_v2_info_session",
        "role": "user",
        "time": {"created": user["info"]["time"]["created"]},
        "agent": "build",
        "model": {"providerID": "openai", "modelID": "gpt-5"},
        "format": {"type": "json_schema", "schema": {"type": "object", "properties": {"answer": {"type": "string"}}}},
        "tools": {"bash": False},
        "system": "只返回 JSON",
        "variant": "high",
    }
    assert assistant["info"] == {
        "id": "msg_assistant_info",
        "sessionID": "message_v2_info_session",
        "role": "assistant",
        "time": {
            "created": assistant["info"]["time"]["created"],
            "completed": 1234567890,
        },
        "parentID": "msg_user_info",
        "providerID": "openai",
        "modelID": "gpt-5",
        "mode": "build",
        "agent": "build",
        "path": {"cwd": "D:/workspace", "root": "D:/workspace"},
        "cost": 0.25,
        "tokens": {"total": None, "input": 10, "output": 6, "reasoning": 2, "cache": {"read": 1, "write": 0}},
        "structured": {"answer": "ok"},
        "finish": "stop",
    }

