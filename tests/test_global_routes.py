from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import agent as agent_router
from apps.api.routers import global_routes
from apps.api.routers import session_runtime as session_runtime_router
from packages.agent import (
    global_bus,
    session_bus,
    session_instance,
    session_lifecycle,
)
from packages.agent.session.session_lifecycle import acquire_prompt_instance
from packages.agent.session.session_runtime import ensure_session_record
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
    app.include_router(global_routes.router)
    app.include_router(session_runtime_router.router)
    app.include_router(agent_router.router)
    return app


def _reset_runtime_state() -> None:
    global_bus.reset_for_tests()
    session_bus.reset_for_tests()
    session_lifecycle.reset_for_tests()


def test_global_health_reports_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    client = TestClient(_build_app())

    resp = client.get("/global/health")

    assert resp.status_code == 200
    assert resp.json()["healthy"] is True
    assert isinstance(resp.json()["version"], str)
    assert resp.json()["version"]


def test_global_event_stream_mirrors_global_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    async def _run() -> tuple[str, str]:
        disconnected = {"value": False}

        async def _is_disconnected() -> bool:
            return disconnected["value"]

        stream = global_routes._iter_global_event_stream(_is_disconnected, heartbeat_interval=1.0)
        first = await asyncio.wait_for(anext(stream), timeout=1.0)
        global_bus.publish_event(
            "D:/workspace/demo",
            {
                "type": "session.status",
                "properties": {"sessionID": "demo", "status": {"type": "busy"}},
            },
        )
        second = await asyncio.wait_for(anext(stream), timeout=1.0)
        disconnected["value"] = True
        await stream.aclose()
        return first, second

    first, second = asyncio.run(_run())
    assert '"type": "server.connected"' in first
    assert '"type": "session.status"' in second
    assert '"directory": "D:/workspace/demo"' in second


def test_global_event_stream_serializes_session_bus_events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    workspace_dir = str(tmp_path.resolve())
    ensure_session_record(
        session_id="global_bus_session",
        directory=workspace_dir,
        workspace_path=workspace_dir,
        mode="build",
    )

    async def _run() -> tuple[str, str]:
        disconnected = {"value": False}

        async def _is_disconnected() -> bool:
            return disconnected["value"]

        stream = global_routes._iter_global_event_stream(_is_disconnected, heartbeat_interval=1.0)
        first = await asyncio.wait_for(anext(stream), timeout=1.0)
        session_bus.publish(
            session_bus.SessionBusEvent.IDLE,
            {
                "sessionID": "global_bus_session",
                "status": {"type": "idle"},
            },
        )
        second = await asyncio.wait_for(anext(stream), timeout=1.0)
        disconnected["value"] = True
        await stream.aclose()
        return first, second

    first, second = asyncio.run(_run())
    assert '"type": "server.connected"' in first
    assert '"type": "session.idle"' in second
    assert '"sessionID": "global_bus_session"' in second
    assert f'"directory": {json.dumps(workspace_dir)}' in second


def test_project_current_ensures_instance_project(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    client = TestClient(_build_app())
    workspace_dir = str(Path("D:/workspace/current-project"))

    resp = client.get("/project/current", params={"directory": workspace_dir})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["id"].startswith("project_")
    assert payload["worktree"] == workspace_dir
    assert payload["name"] == "current-project"
    assert payload["sandboxes"] == [workspace_dir]


def test_session_abort_route_delegates_to_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "abort_route_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    observed: list[str] = []
    monkeypatch.setattr(session_runtime_router, "request_session_abort", lambda session_id: observed.append(session_id))

    resp = client.post("/session/abort_route_session/abort")

    assert resp.status_code == 200
    assert resp.json() is True
    assert observed == ["abort_route_session"]


def test_instance_provide_and_state_follow_directory_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()

    session_instance.Instance.dispose_all(extra_directories=[str(tmp_path)])
    disposed: list[int] = []
    getter = session_instance.Instance.state(
        lambda: {"value": len(disposed) + 1},
        lambda state: disposed.append(int(state["value"])),
    )

    first = session_instance.Instance.provide(
        directory=str(tmp_path),
        fn=lambda: {
            "directory": session_instance.Instance.directory,
            "worktree": session_instance.Instance.worktree,
            "project": session_instance.Instance.project,
            "state": getter(),
            "contains": session_instance.Instance.containsPath(str(tmp_path / "notes.txt")),
        },
    )
    second = session_instance.Instance.provide(
        directory=str(tmp_path),
        fn=lambda: getter(),
    )

    assert first["directory"] == str(tmp_path.resolve())
    assert first["worktree"] == str(tmp_path.resolve())
    assert first["project"]["worktree"] == str(tmp_path.resolve())
    assert first["contains"] is True
    assert first["state"] is second

    reloaded = session_instance.Instance.reload(str(tmp_path))
    third = session_instance.Instance.provide(
        directory=str(tmp_path),
        fn=lambda: getter(),
    )

    assert reloaded["directory"] == str(tmp_path.resolve())
    assert reloaded["worktree"] == str(tmp_path.resolve())
    assert disposed == [1]
    assert third != first["state"]
    assert third["value"] == 2


def test_instance_reload_disposes_prompt_sessions_for_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    workspace_dir = str(tmp_path.resolve())

    ensure_session_record(
        session_id="reload_instance_session",
        directory=workspace_dir,
        workspace_path=workspace_dir,
        mode="build",
    )
    instance = acquire_prompt_instance("reload_instance_session", wait=False)
    assert instance is not None
    session_lifecycle.queue_prompt_callback("reload_instance_session", payload={"kind": "prompt"})

    reloaded = session_instance.Instance.reload(workspace_dir)

    assert reloaded["directory"] == workspace_dir
    assert session_lifecycle.get_prompt_instance("reload_instance_session") is None
    assert session_lifecycle.queued_prompt_callbacks("reload_instance_session") == []
    assert session_lifecycle.get_session_status("reload_instance_session") == {"type": "idle"}


def test_global_dispose_aborts_prompts_and_broadcasts(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    client = TestClient(_build_app())
    workspace_dir = str(Path("D:/workspace/dispose-instance"))

    stopped = {"idle": 0, "mcp": 0, "acp": 0, "opencode": 0}
    mirrored = []

    class _FakeMcp:
        async def close_all(self) -> None:
            stopped["mcp"] += 1

    class _FakeAcp:
        def close_all(self) -> None:
            stopped["acp"] += 1

    class _FakeOpenCodeManager:
        def snapshot(self) -> dict:
            return {"default_directory": workspace_dir}

        def stop(self) -> dict:
            stopped["opencode"] += 1
            return {"available": False}

    async def _fake_dispose_runtime_state() -> dict:
        stopped["idle"] += 1
        await _FakeMcp().close_all()
        _FakeAcp().close_all()
        _FakeOpenCodeManager().stop()
        session_lifecycle.dispose_session("dispose_session", error="session disposed")
        session_lifecycle.set_session_status("dispose_session", {"type": "idle"})
        global_bus.publish_event(
            workspace_dir,
            {
                "type": "server.instance.disposed",
                "properties": {
                    "directory": workspace_dir,
                    "session_ids": ["dispose_session"],
                },
            },
        )
        return {"disposed_directories": [workspace_dir]}

    monkeypatch.setattr(global_routes, "dispose_runtime_state", _fake_dispose_runtime_state)

    unsubscribe = global_bus.subscribe_all(lambda event: mirrored.append(event))
    try:
        created = client.post(
            "/session",
            json={
                "id": "dispose_session",
                "directory": workspace_dir,
                "workspace_path": workspace_dir,
                "mode": "build",
            },
        )
        assert created.status_code == 200

        instance = acquire_prompt_instance("dispose_session", wait=False)
        assert instance is not None
        session_lifecycle.queue_prompt_callback("dispose_session", payload={"kind": "prompt"})

        resp = client.post("/global/dispose")
    finally:
        unsubscribe()

    assert resp.status_code == 200
    assert resp.json() is True
    assert stopped == {"idle": 1, "mcp": 1, "acp": 1, "opencode": 1}
    assert session_lifecycle.get_session_status("dispose_session") == {"type": "idle"}
    assert session_lifecycle.get_prompt_instance("dispose_session") is None
    assert session_lifecycle.queued_prompt_callbacks("dispose_session") == []
    assert any(
        event.directory == workspace_dir
        and isinstance(event.payload, dict)
        and event.payload.get("type") == "server.instance.disposed"
        and isinstance(event.payload.get("properties"), dict)
        and event.payload["properties"].get("directory") == workspace_dir
        for event in mirrored
    )
    assert any(
        event.directory == "global"
        and isinstance(event.payload, dict)
        and event.payload.get("type") == "global.disposed"
        for event in mirrored
    )


def test_instance_dispose_and_reload_do_not_allow_stale_loop_to_restore_lifecycle_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    _reset_runtime_state()
    workspace_dir = str(tmp_path.resolve())

    ensure_session_record(
        session_id="dispose_reload_busy_session",
        directory=workspace_dir,
        workspace_path=workspace_dir,
        mode="build",
    )

    instance = acquire_prompt_instance("dispose_reload_busy_session", wait=False)
    assert instance is not None
    session_lifecycle.set_session_status("dispose_reload_busy_session", {"type": "busy"})
    queued = session_lifecycle.queue_prompt_callback("dispose_reload_busy_session", payload={"kind": "prompt"})
    assert queued.closed is False

    disposed = session_instance.Instance.dispose(workspace_dir)

    session_lifecycle.finish_prompt_instance("dispose_reload_busy_session", result={"messageID": "ghost_message"})
    session_lifecycle.set_session_status("dispose_reload_busy_session", {"type": "busy"})
    disposed_callback = session_lifecycle.queue_prompt_callback(
        "dispose_reload_busy_session",
        payload={"kind": "permission"},
    )

    assert disposed["directory"] == workspace_dir
    assert disposed["session_ids"] == ["dispose_reload_busy_session"]
    assert session_lifecycle.get_prompt_instance("dispose_reload_busy_session") is None
    assert session_lifecycle.get_session_status("dispose_reload_busy_session") == {"type": "idle"}
    assert session_lifecycle.queued_prompt_callbacks("dispose_reload_busy_session") == []
    assert disposed_callback.closed is True
    assert disposed_callback.outcome == "rejected"
    assert disposed_callback.error == "session disposed"

    reloaded = session_instance.Instance.reload(workspace_dir)
    revived = acquire_prompt_instance("dispose_reload_busy_session", wait=False)
    assert revived is not None
    session_lifecycle.set_session_status("dispose_reload_busy_session", {"type": "busy"})

    assert reloaded["directory"] == workspace_dir
    assert session_lifecycle.get_prompt_instance("dispose_reload_busy_session") is not None
    assert session_lifecycle.get_session_status("dispose_reload_busy_session") == {"type": "busy"}

