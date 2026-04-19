from __future__ import annotations

import json
from pathlib import Path

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
    session_lifecycle,
)
from packages.agent import session_snapshot
from packages.agent.session.session_runtime import request_session_abort, wrap_stream_with_persistence
from packages.agent.workspace.workspace_executor import WorkspaceAccessError
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
    session_lifecycle.reset_for_tests()


def _first_text_part(parts: list[dict]) -> str:
    for part in parts:
        if part.get("type") == "text":
            return str(part.get("text") or "")
    return ""


class FakeEditLLM:
    file_path: str = ""

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(self, messages, tools=None, variant_override=None, session_cache_key=None):  # noqa: ANN001, ANN201
        del tools, variant_override, session_cache_key
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if tool_messages:
            yield StreamEvent(type="text_delta", content="已完成修改。")
            yield StreamEvent(type="usage", model="fake-model", input_tokens=9, output_tokens=4)
            return

        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_edit_1",
            tool_name="edit",
            tool_arguments=json.dumps(
                {
                    "file_path": self.file_path,
                    "old_string": "before",
                    "new_string": "after",
                },
                ensure_ascii=False,
            ),
        )
        yield StreamEvent(type="usage", model="fake-model", input_tokens=9, output_tokens=0)


class FakePlainLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(self, messages, tools=None, variant_override=None, session_cache_key=None):  # noqa: ANN001, ANN201
        del messages, tools, variant_override, session_cache_key
        yield StreamEvent(type="text_delta", content="继续新的对话。")
        yield StreamEvent(type="usage", model="fake-model", input_tokens=5, output_tokens=3)


class FakeShellEditLLM:
    file_path: str = ""

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(self, messages, tools=None, variant_override=None, session_cache_key=None):  # noqa: ANN001, ANN201
        del tools, variant_override, session_cache_key
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if tool_messages:
            yield StreamEvent(type="text_delta", content="shell 修改已完成。")
            yield StreamEvent(type="usage", model="fake-model", input_tokens=11, output_tokens=5)
            return

        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_shell_1",
            tool_name="bash",
            tool_arguments=json.dumps(
                {
                    "command": "fake-shell-edit",
                },
                ensure_ascii=False,
            ),
        )
        yield StreamEvent(type="usage", model="fake-model", input_tokens=11, output_tokens=0)


class FakeAbortSnapshotStream:
    file_path: str = ""


class FakeRemoteEditLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def chat_stream(self, messages, tools=None, variant_override=None, session_cache_key=None):  # noqa: ANN001, ANN201
        del tools, variant_override, session_cache_key
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if tool_messages:
            yield StreamEvent(type="text_delta", content="远程修改已完成。")
            yield StreamEvent(type="usage", model="fake-model", input_tokens=10, output_tokens=4)
            return
        yield StreamEvent(
            type="tool_call",
            tool_call_id="call_remote_edit_1",
            tool_name="replace_workspace_text",
            tool_arguments=json.dumps(
                {
                    "workspace_path": "/remote/workspace",
                    "relative_path": "note.txt",
                    "search_text": "before",
                    "replace_text": "after",
                },
                ensure_ascii=False,
            ),
        )
        yield StreamEvent(type="usage", model="fake-model", input_tokens=10, output_tokens=0)


def _fake_shell_execute_tool_stream(name: str, arguments: dict, context=None):  # noqa: ANN001, ANN201
    del arguments, context
    assert name == "bash"
    Path(FakeShellEditLLM.file_path).write_text("after\n", encoding="utf-8")
    yield agent_service.ToolResult(
        success=True,
        summary="bash 执行成功",
        data={"command": "fake-shell-edit", "exit_code": 0},
    )


def _fake_abort_snapshot_stream_chat(*_args, **kwargs):
    workspace_path = str(kwargs.get("workspace_path") or "")
    session_id = str(kwargs.get("session_id") or "")
    snapshot_hash = session_snapshot.track(workspace_path)
    Path(FakeAbortSnapshotStream.file_path).write_text("after\n", encoding="utf-8")
    request_session_abort(session_id)
    chunks = [
        f'event: session_step_start\ndata: {json.dumps({"step": 1, "snapshot": snapshot_hash}, ensure_ascii=False)}\n\n',
        'event: done\ndata: {}\n\n',
    ]
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


def test_session_diff_revert_and_unrevert(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    FakeEditLLM.file_path = str(target)
    monkeypatch.setattr(agent_service, "LLMClient", FakeEditLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "revert_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt = client.post(
        "/session/revert_session/message",
        json={
            "parts": [{"type": "text", "text": "把 before 改成 after"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt.status_code == 200
    assert "已完成修改" in prompt.text
    assert target.read_text(encoding="utf-8") == "after\n"

    history = client.get("/session/revert_session/message").json()
    assert len(history) == 3
    assert [item["info"]["role"] for item in history] == ["user", "assistant", "assistant"]
    assert any(part["type"] == "patch" for part in history[1]["parts"])
    assert _first_text_part(history[2]["parts"]) == "已完成修改。"

    diffs = client.get("/session/revert_session/diff").json()
    assert len(diffs) == 1
    assert diffs[0]["file"].endswith("note.txt")
    assert diffs[0]["before"] == "before\n"
    assert diffs[0]["after"] == "after\n"
    assert diffs[0]["status"] == "modified"

    user_message_id = history[0]["info"]["id"]
    reverted = client.post(
        "/session/revert_session/revert",
        json={"message_id": user_message_id},
    )
    assert reverted.status_code == 200
    assert target.read_text(encoding="utf-8") == "before\n"
    reverted_session = reverted.json()
    assert reverted_session["revert"]["message_id"] == user_message_id
    assert reverted_session["summary"]["files"] == 1
    state_payload = client.get("/session/revert_session/state").json()
    assert state_payload["session"]["revert"]["message_id"] == user_message_id

    restored = client.post("/session/revert_session/unrevert")
    assert restored.status_code == 200
    assert target.read_text(encoding="utf-8") == "after\n"
    restored_session = restored.json()
    assert restored_session["revert"] is None


def test_revert_cleanup_runs_before_next_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    FakeEditLLM.file_path = str(target)
    monkeypatch.setattr(agent_service, "LLMClient", FakeEditLLM)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "revert_cleanup_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt = client.post(
        "/session/revert_cleanup_session/message",
        json={
            "parts": [{"type": "text", "text": "把 before 改成 after"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt.status_code == 200

    first_history = client.get("/session/revert_cleanup_session/message").json()
    user_message_id = first_history[0]["info"]["id"]
    reverted = client.post(
        "/session/revert_cleanup_session/revert",
        json={"messageID": user_message_id},
    )
    assert reverted.status_code == 200
    assert target.read_text(encoding="utf-8") == "before\n"

    monkeypatch.setattr(agent_service, "LLMClient", FakePlainLLM)
    next_prompt = client.post(
        "/session/revert_cleanup_session/message",
        json={
            "parts": [{"type": "text", "text": "开始新的任务"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert next_prompt.status_code == 200
    assert "继续新的对话" in next_prompt.text

    history = client.get("/session/revert_cleanup_session/message").json()
    assert len(history) == 2
    assert history[0]["parts"][0]["text"] == "开始新的任务"
    assert _first_text_part(history[1]["parts"]) == "继续新的对话。"
    assert client.get("/session/revert_cleanup_session").json()["revert"] is None
    assert client.get("/session/revert_cleanup_session/diff").json() == []


def test_snapshot_based_diff_and_revert_handles_external_file_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    FakeShellEditLLM.file_path = str(target)
    monkeypatch.setattr(agent_service, "LLMClient", FakeShellEditLLM)
    monkeypatch.setattr(agent_service, "execute_tool_stream", _fake_shell_execute_tool_stream)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "snapshot_revert_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt = client.post(
        "/session/snapshot_revert_session/message",
        json={
            "parts": [{"type": "text", "text": "通过 shell 把 before 改成 after"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt.status_code == 200
    assert "shell 修改已完成" in prompt.text
    assert target.read_text(encoding="utf-8") == "after\n"

    history = client.get("/session/snapshot_revert_session/message").json()
    assert len(history) == 3
    assert [item["info"]["role"] for item in history] == ["user", "assistant", "assistant"]
    assert any(part["type"] == "patch" and part.get("hash") for part in history[1]["parts"])
    assert _first_text_part(history[2]["parts"]) == "shell 修改已完成。"

    diffs = client.get("/session/snapshot_revert_session/diff").json()
    assert len(diffs) == 1
    assert diffs[0]["file"].endswith("note.txt")
    assert diffs[0]["before"] == "before\n"
    assert diffs[0]["after"] == "after\n"

    user_message_id = history[0]["info"]["id"]
    reverted = client.post(
        "/session/snapshot_revert_session/revert",
        json={"messageID": user_message_id},
    )
    assert reverted.status_code == 200
    assert target.read_text(encoding="utf-8") == "before\n"
    reverted_session = reverted.json()
    assert reverted_session["revert"]["snapshot"]

    restored = client.post("/session/snapshot_revert_session/unrevert")
    assert restored.status_code == 200
    assert target.read_text(encoding="utf-8") == "after\n"


def test_aborted_step_still_persists_snapshot_patch_and_can_revert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    FakeAbortSnapshotStream.file_path = str(target)
    monkeypatch.setattr(session_runtime_router, "stream_chat", _fake_abort_snapshot_stream_chat)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "snapshot_abort_revert_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt = client.post(
        "/session/snapshot_abort_revert_session/message",
        json={
            "parts": [{"type": "text", "text": "执行后立即中止"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt.status_code == 200
    assert "会话已中止" in prompt.text
    assert target.read_text(encoding="utf-8") == "after\n"

    history = client.get("/session/snapshot_abort_revert_session/message").json()
    assert len(history) == 2
    assistant = history[1]
    assert assistant["info"]["error"]["name"] == "AbortedError"
    assert any(part["type"] == "patch" and part.get("hash") for part in assistant["parts"])

    diffs = client.get("/session/snapshot_abort_revert_session/diff").json()
    assert len(diffs) == 1
    assert diffs[0]["before"] == "before\n"
    assert diffs[0]["after"] == "after\n"

    user_message_id = history[0]["info"]["id"]
    reverted = client.post(
        "/session/snapshot_abort_revert_session/revert",
        json={"messageID": user_message_id},
    )
    assert reverted.status_code == 200
    assert target.read_text(encoding="utf-8") == "before\n"

    restored = client.post("/session/snapshot_abort_revert_session/unrevert")
    assert restored.status_code == 200
    assert target.read_text(encoding="utf-8") == "after\n"


def test_remote_workspace_diff_revert_and_unrevert(
    monkeypatch: pytest.MonkeyPatch,
):
    _configure_test_db(monkeypatch)
    remote_files = {"/remote/workspace/note.txt": "before\n"}
    server_entry = {"id": "ssh-dev", "enabled": True, "workspace_root": "/remote/workspace"}

    def _remote_target(workspace_path: str, relative_path: str) -> str:
        return f"{workspace_path.rstrip('/')}/{relative_path.lstrip('/')}"

    def _fake_remote_read_file(_server_entry, requested_path: str, relative_path: str, *, max_chars: int):  # noqa: ANN001, ANN202
        target = _remote_target(requested_path, relative_path)
        if target not in remote_files:
            raise WorkspaceAccessError(f"文件不存在: {relative_path}")
        content = remote_files[target]
        return {
            "workspace_path": requested_path,
            "relative_path": relative_path,
            "content": content[:max_chars],
            "truncated": False,
            "size_bytes": len(content.encode("utf-8")),
        }

    def _fake_remote_write_file(_server_entry, *, path: str, relative_path: str, content: str, create_dirs=True, overwrite=True):  # noqa: ANN001, ANN202
        del create_dirs, overwrite
        target = _remote_target(path, relative_path)
        previous = remote_files.get(target)
        remote_files[target] = content
        return {
            "workspace_path": path,
            "relative_path": relative_path,
            "created": previous is None,
            "overwritten": previous is not None,
            "changed": previous != content,
            "size_bytes": len(content.encode("utf-8")),
            "previous_size_bytes": len(previous.encode("utf-8")) if previous is not None else 0,
            "line_count": content.count("\n") + (0 if not content else 1),
            "sha256": "remote",
            "preview": content,
            "diff_preview": "",
        }

    def _fake_remote_restore_file(_server_entry, *, path: str, content: str | None):  # noqa: ANN001, ANN202
        if content is None:
            remote_files.pop(path, None)
            return {"path": path, "exists": False, "deleted": True}
        remote_files[path] = content
        return {"path": path, "exists": True, "deleted": False}

    monkeypatch.setattr(agent_service, "LLMClient", FakeRemoteEditLLM)
    monkeypatch.setattr(agent_service, "get_assistant_exec_policy", lambda: {"approval_mode": "off"})
    monkeypatch.setattr("packages.agent.tools.agent_tools._resolve_remote_server_entry", lambda _context: server_entry)
    monkeypatch.setattr("packages.agent.workspace.workspace_remote.remote_read_file", _fake_remote_read_file)
    monkeypatch.setattr("packages.agent.workspace.workspace_remote.remote_write_file", _fake_remote_write_file)
    monkeypatch.setattr("packages.agent.workspace.workspace_remote.remote_restore_file", _fake_remote_restore_file)
    monkeypatch.setattr("packages.agent.workspace.workspace_server_registry.get_workspace_server_entry", lambda _server_id: server_entry)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "remote_revert_session",
            "directory": "/remote/workspace",
            "workspace_path": "/remote/workspace",
            "workspace_server_id": "ssh-dev",
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt = client.post(
        "/session/remote_revert_session/message",
        json={
            "parts": [{"type": "text", "text": "把远程 before 改成 after"}],
            "mode": "build",
            "workspace_path": "/remote/workspace",
            "workspace_server_id": "ssh-dev",
        },
    )
    assert prompt.status_code == 200
    assert "远程修改已完成" in prompt.text
    assert remote_files["/remote/workspace/note.txt"] == "after\n"

    history = client.get("/session/remote_revert_session/message").json()
    assert len(history) == 3
    assert any(part["type"] == "patch" for part in history[1]["parts"])

    diffs = client.get("/session/remote_revert_session/diff").json()
    assert len(diffs) == 1
    assert diffs[0]["file"] == "note.txt"
    assert diffs[0]["path"] == "/remote/workspace/note.txt"
    assert diffs[0]["before"] == "before\n"
    assert diffs[0]["after"] == "after\n"
    assert diffs[0]["workspace_server_id"] == "ssh-dev"

    user_message_id = history[0]["info"]["id"]
    reverted = client.post(
        "/session/remote_revert_session/revert",
        json={"messageID": user_message_id},
    )
    assert reverted.status_code == 200
    assert remote_files["/remote/workspace/note.txt"] == "before\n"

    restored = client.post("/session/remote_revert_session/unrevert")
    assert restored.status_code == 200
    assert remote_files["/remote/workspace/note.txt"] == "after\n"


def test_revert_aggregates_multiple_patch_steps_for_same_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "multi_step_revert_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = session_runtime_router.append_session_message(
        session_id="multi_step_revert_session",
        role="user",
        content="开始任务",
    )
    assistant_message_1 = session_runtime_router.append_session_message(
        session_id="multi_step_revert_session",
        role="assistant",
        content="",
        parent_id=str(user_message["info"]["id"]),
        parts=[
            {
                "type": "patch",
                "file": "note.txt",
                "path": str(target),
                "before": "before\n",
                "after": "middle\n",
                "exists_before": True,
                "exists_after": True,
                "additions": 1,
                "deletions": 1,
                "status": "modified",
                "workspace_path": str(tmp_path),
            }
        ],
    )
    assert assistant_message_1["info"]["id"]
    target.write_text("middle\n", encoding="utf-8")

    user_message_2 = session_runtime_router.append_session_message(
        session_id="multi_step_revert_session",
        role="user",
        content="继续任务",
    )
    assistant_message_2 = session_runtime_router.append_session_message(
        session_id="multi_step_revert_session",
        role="assistant",
        content="",
        parent_id=str(user_message_2["info"]["id"]),
        parts=[
            {
                "type": "patch",
                "file": "note.txt",
                "path": str(target),
                "before": "middle\n",
                "after": "after\n",
                "exists_before": True,
                "exists_after": True,
                "additions": 1,
                "deletions": 1,
                "status": "modified",
                "workspace_path": str(tmp_path),
            }
        ],
    )
    assert assistant_message_2["info"]["id"]
    target.write_text("after\n", encoding="utf-8")

    diffs = client.get("/session/multi_step_revert_session/diff").json()
    assert len(diffs) == 1
    assert diffs[0]["file"] == "note.txt"
    assert diffs[0]["before"] == "before\n"
    assert diffs[0]["after"] == "after\n"
    assert diffs[0]["status"] == "modified"

    reverted = client.post(
        "/session/multi_step_revert_session/revert",
        json={"messageID": str(user_message["info"]["id"])},
    )
    assert reverted.status_code == 200
    assert target.read_text(encoding="utf-8") == "before\n"

    restored = client.post("/session/multi_step_revert_session/unrevert")
    assert restored.status_code == 200
    assert target.read_text(encoding="utf-8") == "after\n"


def test_snapshot_patch_ignores_runtime_noise_files(tmp_path: Path):
    tracked = tmp_path / "note.txt"
    tracked.write_text("before\n", encoding="utf-8")
    runtime_log = tmp_path / "logs" / "playwright-backend.out.log"
    runtime_log.parent.mkdir(parents=True, exist_ok=True)
    runtime_log.write_text("first\n", encoding="utf-8")

    snapshot_hash = session_snapshot.track(str(tmp_path))
    assert snapshot_hash

    tracked.write_text("after\n", encoding="utf-8")
    runtime_log.write_text("second\n", encoding="utf-8")

    patch = session_snapshot.patch(str(tmp_path), snapshot_hash)
    assert patch["files"] == [str(tracked.resolve())]

    current_diff = session_snapshot.diff_current_full(str(tmp_path), snapshot_hash)
    assert len(current_diff) == 1
    assert current_diff[0]["file"] == str(tracked.resolve())


def test_snapshot_diff_full_ignores_runtime_noise_files(tmp_path: Path):
    tracked = tmp_path / "note.txt"
    tracked.write_text("before\n", encoding="utf-8")
    runtime_log = tmp_path / "logs" / "playwright-backend.out.log"
    runtime_log.parent.mkdir(parents=True, exist_ok=True)
    runtime_log.write_text("first\n", encoding="utf-8")

    before_hash = session_snapshot.track(str(tmp_path))
    assert before_hash

    tracked.write_text("after\n", encoding="utf-8")
    runtime_log.write_text("second\n", encoding="utf-8")

    after_hash = session_snapshot.track(str(tmp_path))
    assert after_hash

    diff = session_snapshot.diff_full(str(tmp_path), before_hash, after_hash)
    assert len(diff) == 1
    assert diff[0]["file"] == "note.txt"
    assert diff[0]["before"] == "before\n"
    assert diff[0]["after"] == "after\n"


def test_revert_and_unrevert_reject_busy_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "busy_revert_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    user_message = session_runtime_router.append_session_message(
        session_id="busy_revert_session",
        role="user",
        content="需要回退",
    )
    owner = session_lifecycle.acquire_prompt_instance("busy_revert_session", wait=False)
    assert owner is not None

    try:
        reverted = client.post(
            "/session/busy_revert_session/revert",
            json={"messageID": str(user_message["info"]["id"])},
        )
        assert reverted.status_code == 400
        assert reverted.json()["detail"] == "session is busy"

        restored = client.post("/session/busy_revert_session/unrevert")
        assert restored.status_code == 400
        assert restored.json()["detail"] == "session is busy"
    finally:
        session_lifecycle.finish_prompt_instance("busy_revert_session")
        _reset_runtime_state()

