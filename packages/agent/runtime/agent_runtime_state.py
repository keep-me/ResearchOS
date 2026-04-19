"""Persistent agent session state used by the native ResearchOS assistant."""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.agent.session.session_runtime import (
    ensure_session_record,
    get_session_record,
    get_session_todos,
    replace_session_todos,
)

AGENT_MODES = {"build", "plan", "general", "explore"}


@dataclass
class AgentTodoItem:
    content: str
    status: str = "pending"
    priority: str = "medium"


@dataclass
class AgentSessionState:
    session_id: str
    mode: str = "build"
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    todos: list[AgentTodoItem] = field(default_factory=list)


def normalize_mode(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in AGENT_MODES else "build"


def ensure_session(
    session_id: str | None,
    *,
    mode: str | None = None,
    workspace_path: str | None = None,
    workspace_server_id: str | None = None,
) -> AgentSessionState:
    payload = ensure_session_record(
        session_id=session_id,
        workspace_path=workspace_path,
        workspace_server_id=workspace_server_id,
        mode=normalize_mode(mode),
    )
    todos = [
        AgentTodoItem(
            content=str(item.get("content") or "").strip(),
            status=str(item.get("status") or "pending").strip().lower() or "pending",
            priority=str(item.get("priority") or "medium").strip().lower() or "medium",
        )
        for item in get_session_todos(payload["id"])
        if str(item.get("content") or "").strip()
    ]
    return AgentSessionState(
        session_id=str(payload["id"]),
        mode=normalize_mode(payload.get("mode")),
        workspace_path=payload.get("workspace_path"),
        workspace_server_id=payload.get("workspace_server_id"),
        todos=todos,
    )


def get_session(session_id: str | None) -> AgentSessionState | None:
    sid = str(session_id or "").strip() or "default"
    payload = get_session_record(sid)
    if payload is None:
        return None
    todos = [
        AgentTodoItem(
            content=str(item.get("content") or "").strip(),
            status=str(item.get("status") or "pending").strip().lower() or "pending",
            priority=str(item.get("priority") or "medium").strip().lower() or "medium",
        )
        for item in get_session_todos(sid)
        if str(item.get("content") or "").strip()
    ]
    return AgentSessionState(
        session_id=sid,
        mode=normalize_mode(payload.get("mode")),
        workspace_path=payload.get("workspace_path"),
        workspace_server_id=payload.get("workspace_server_id"),
        todos=todos,
    )


def session_snapshot(session_id: str | None) -> dict | None:
    state = get_session(session_id)
    if state is None:
        return None
    return {
        "session_id": state.session_id,
        "mode": state.mode,
        "workspace_path": state.workspace_path,
        "workspace_server_id": state.workspace_server_id,
        "todos": [
            {
                "content": todo.content,
                "status": todo.status,
                "priority": todo.priority,
            }
            for todo in state.todos
        ],
    }


def get_todos(session_id: str | None) -> list[dict]:
    sid = ensure_session(session_id).session_id
    return get_session_todos(sid)


def update_todos(session_id: str | None, todos: list[dict]) -> list[dict]:
    state = ensure_session(session_id)
    normalized: list[AgentTodoItem] = []
    for item in todos or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        status = str(item.get("status") or "pending").strip().lower() or "pending"
        if status not in {"pending", "in_progress", "completed", "cancelled"}:
            status = "pending"
        priority = str(item.get("priority") or "medium").strip().lower() or "medium"
        if priority not in {"high", "medium", "low"}:
            priority = "medium"
        normalized.append(AgentTodoItem(content=content, status=status, priority=priority))
    return replace_session_todos(
        state.session_id,
        [
            {
                "content": todo.content,
                "status": todo.status,
                "priority": todo.priority,
            }
            for todo in normalized
        ],
    )

