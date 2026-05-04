"""Prompt instance ownership, waiters, and abort state."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PromptWaiter:
    session_id: str
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


@dataclass
class PromptCallback:
    session_id: str
    payload: dict[str, Any] | None = None
    items: list[str] = field(default_factory=list)
    outcome: str = "pending"
    closed: bool = False
    error: str | None = None
    result: dict[str, Any] | None = None
    control: Any | None = None
    condition: threading.Condition = field(default_factory=threading.Condition)

    def push(self, item: str) -> None:
        with self.condition:
            self.items.append(str(item))
            self.condition.notify_all()

    def close(
        self,
        error: str | None = None,
        *,
        outcome: str | None = None,
        result: dict[str, Any] | None = None,
        control: Any | None = None,
    ) -> None:
        with self.condition:
            self.closed = True
            if outcome in {"resolved", "rejected"}:
                self.outcome = outcome
            elif self.outcome == "pending":
                self.outcome = "rejected" if str(error or "").strip() else "resolved"
            normalized_error = str(error).strip() if error is not None else ""
            self.error = normalized_error or self.error
            self.result = copy.deepcopy(result) if isinstance(result, dict) else result
            self.control = copy.deepcopy(control) if control is not None else self.control
            self.condition.notify_all()

    def resolve(
        self,
        *,
        result: dict[str, Any] | None = None,
        control: Any | None = None,
    ) -> None:
        self.close(outcome="resolved", result=result, control=control)

    def reject(self, reason: str | None = None, *, control: Any | None = None) -> None:
        self.close(reason, outcome="rejected", control=control)

    def wait_closed(self, *, timeout_ms: int | None = None) -> bool:
        deadline = None if timeout_ms is None else time.monotonic() + max(timeout_ms, 0) / 1000
        with self.condition:
            while not self.closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self.condition.wait(timeout=remaining)
            return True

    def iter_items(self):
        index = 0
        while True:
            with self.condition:
                while index >= len(self.items) and not self.closed:
                    self.condition.wait(timeout=0.1)
                if index < len(self.items):
                    item = self.items[index]
                    index += 1
                    yield item
                    continue
                if self.closed:
                    return


@dataclass
class PromptInstance:
    session_id: str
    owner_id: str
    created_at_ms: int
    loop_kind: str = "prompt"
    running: bool = False


@dataclass
class PromptSessionState:
    condition: threading.Condition
    instance: PromptInstance | None = None
    waiters: list[PromptWaiter] = field(default_factory=list)
    callbacks: list[PromptCallback] = field(default_factory=list)
    status: dict[str, Any] | None = None
    aborted: bool = False


_LOCK = threading.RLock()
_SESSION_DIRECTORIES: dict[str, str] = {}
_ABORTED_SESSION_IDS: set[str] = set()
_DISPOSED_SESSION_IDS: set[str] = set()
_DIRECTORY_STATE_GETTER = None


def _session_directory(session_id: str | None) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        return str(Path.cwd())
    with _LOCK:
        mapped = _SESSION_DIRECTORIES.get(sid)
    if mapped:
        return mapped
    try:
        from packages.agent.session.session_runtime import get_session_record

        record = get_session_record(sid) or {}
    except Exception:
        record = {}
    directory = str(record.get("directory") or record.get("workspace_path") or "").strip()
    if directory:
        return directory
    return str(Path.cwd())


def _directory_sessions(directory: str) -> dict[str, PromptSessionState]:
    global _DIRECTORY_STATE_GETTER
    if _DIRECTORY_STATE_GETTER is None:
        from packages.agent.session.session_instance import Instance

        def _init_sessions() -> dict[str, PromptSessionState]:
            return {}

        _DIRECTORY_STATE_GETTER = Instance.state(
            _init_sessions,
            _dispose_directory_sessions,
        )
    from packages.agent.session.session_instance import Instance

    return Instance.provide(
        directory=directory,
        fn=_DIRECTORY_STATE_GETTER,
    )


def _state(
    session_id: str,
    *,
    create: bool = True,
    revive: bool = False,
) -> PromptSessionState | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    directory = _session_directory(sid)
    sessions = _directory_sessions(directory)
    with _LOCK:
        if revive:
            _DISPOSED_SESSION_IDS.discard(sid)
        elif sid in _DISPOSED_SESSION_IDS and sid not in sessions:
            return None
        state = sessions.get(sid)
        if state is None and create:
            if sid in _DISPOSED_SESSION_IDS and not revive:
                return None
            state = PromptSessionState(condition=threading.Condition(_LOCK))
            sessions[sid] = state
            _SESSION_DIRECTORIES[sid] = directory
        elif state is not None:
            _SESSION_DIRECTORIES[sid] = directory
        return state


def _new_prompt_instance(
    session_id: str, *, loop_kind: str = "prompt", running: bool = True
) -> PromptInstance:
    return PromptInstance(
        session_id=session_id,
        owner_id=f"{loop_kind}_{time.time_ns()}",
        created_at_ms=int(time.time() * 1000),
        loop_kind=loop_kind,
        running=running,
    )


def _dispose_prompt_session_locked(
    session_id: str,
    state: PromptSessionState,
    *,
    error: str | None = None,
) -> None:
    _DISPOSED_SESSION_IDS.add(session_id)
    callbacks = list(state.callbacks)
    waiters = list(state.waiters)
    state.callbacks = []
    state.waiters = []
    state.instance = None
    state.status = None
    state.aborted = False
    _SESSION_DIRECTORIES.pop(session_id, None)
    for waiter in waiters:
        waiter.result = None
        waiter.event.set()
    for callback in callbacks:
        callback.reject(error or "session disposed")
    state.condition.notify_all()


def _dispose_directory_sessions(current: dict[str, PromptSessionState]) -> None:
    with _LOCK:
        for session_id, state in list(current.items()):
            _dispose_prompt_session_locked(session_id, state)
        current.clear()


def _resolve_waiters_locked(
    state: PromptSessionState, result: dict[str, Any] | None = None
) -> None:
    waiters = list(state.waiters)
    if waiters:
        resolved = copy.deepcopy(result) if isinstance(result, dict) else result
        for waiter in waiters:
            waiter.result = copy.deepcopy(resolved) if isinstance(resolved, dict) else resolved
            waiter.event.set()
        state.waiters = []


def _finish_active_loop_locked(
    state: PromptSessionState, *, result: dict[str, Any] | None = None
) -> None:
    state.instance = None
    _resolve_waiters_locked(state, result)
    state.condition.notify_all()


def _pause_active_loop_locked(state: PromptSessionState, *, loop_kind: str = "prompt") -> None:
    if state.instance is None:
        return
    state.instance.loop_kind = loop_kind
    state.instance.running = False


def _start_callback_loop_locked(
    state: PromptSessionState, session_id: str
) -> PromptCallback | None:
    if not state.callbacks:
        return None
    if state.instance is None:
        state.instance = _new_prompt_instance(session_id, loop_kind="prompt", running=True)
    else:
        state.instance.loop_kind = "prompt"
        state.instance.running = True
    return state.callbacks.pop(0)


def acquire_prompt_instance(
    session_id: str,
    *,
    wait: bool = False,
    timeout_ms: int | None = None,
) -> PromptInstance | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    deadline = None if timeout_ms is None else time.monotonic() + max(timeout_ms, 0) / 1000
    state = _state(sid, revive=True)
    assert state is not None
    condition = state.condition
    with condition:
        _ABORTED_SESSION_IDS.discard(sid)
        state.aborted = False
        while state.instance is not None:
            if not wait:
                return None
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return None
            condition.wait(timeout=remaining)
        instance = _new_prompt_instance(sid, loop_kind="prompt", running=False)
        state.instance = instance
        return instance


def get_prompt_instance(session_id: str | None) -> PromptInstance | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    state = _state(sid, create=False)
    if state is None:
        return None
    with _LOCK:
        return state.instance


def mark_prompt_instance_running(session_id: str | None, *, loop_kind: str = "prompt") -> None:
    sid = str(session_id or "").strip()
    state = _state(sid, create=False)
    if state is None:
        return
    with _LOCK:
        if state.instance is None:
            state.instance = _new_prompt_instance(sid, loop_kind=loop_kind, running=True)
            return
        state.instance.loop_kind = loop_kind
        state.instance.running = True


def finish_prompt_instance(session_id: str, *, result: dict[str, Any] | None = None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    state = _state(sid)
    if state is None:
        return
    condition = state.condition
    with condition:
        _finish_active_loop_locked(state, result=result)


def pause_prompt_instance(session_id: str, *, loop_kind: str = "prompt") -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    state = _state(sid, create=False)
    if state is None:
        return
    with _LOCK:
        _pause_active_loop_locked(state, loop_kind=loop_kind)


def register_prompt_waiter(session_id: str) -> PromptWaiter:
    sid = str(session_id or "").strip()
    waiter = PromptWaiter(session_id=sid)
    state = _state(sid)
    if state is None:
        waiter.event.set()
        return waiter
    with _LOCK:
        state.waiters.append(waiter)
    return waiter


def wait_for_prompt_completion(
    waiter: PromptWaiter, *, timeout_ms: int | None = None
) -> dict[str, Any] | None:
    timeout = None if timeout_ms is None else max(timeout_ms, 0) / 1000
    waiter.event.wait(timeout=timeout)
    return copy.deepcopy(waiter.result) if isinstance(waiter.result, dict) else waiter.result


def queue_prompt_callback(
    session_id: str,
    *,
    payload: dict[str, Any] | None = None,
    front: bool = False,
) -> PromptCallback:
    sid = str(session_id or "").strip()
    callback = PromptCallback(
        session_id=sid,
        payload=copy.deepcopy(payload) if isinstance(payload, dict) else None,
    )
    state = _state(sid)
    if state is None:
        callback.reject("session disposed")
        return callback
    with _LOCK:
        if front:
            state.callbacks.insert(0, callback)
        else:
            state.callbacks.append(callback)
    return callback


def pop_prompt_callback(session_id: str) -> PromptCallback | None:
    sid = str(session_id or "").strip()
    state = _state(sid, create=False)
    if state is None:
        return None
    with _LOCK:
        if not state.callbacks:
            return None
        callback = state.callbacks.pop(0)
        return callback


def claim_prompt_callback(session_id: str) -> tuple[str, PromptCallback | None]:
    sid = str(session_id or "").strip()
    state = _state(sid, create=False)
    if state is None:
        return "empty", None
    with _LOCK:
        if state.instance is not None and state.instance.running:
            return "running", None
        callback = _start_callback_loop_locked(state, sid)
        if callback is None:
            return "empty", None
        return "started", callback


def reject_prompt_callbacks(
    callbacks: list[PromptCallback],
    reason: str | None = None,
    *,
    control: Any | None = None,
) -> list[PromptCallback]:
    rejected: list[PromptCallback] = []
    for callback in callbacks:
        if not isinstance(callback, PromptCallback):
            continue
        callback.reject(reason, control=control)
        rejected.append(callback)
    return rejected


def queued_prompt_callbacks(session_id: str) -> list[PromptCallback]:
    sid = str(session_id or "").strip()
    state = _state(sid, create=False)
    if state is None:
        return []
    with _LOCK:
        return list(state.callbacks)


def drain_prompt_callbacks(session_id: str) -> list[PromptCallback]:
    sid = str(session_id or "").strip()
    state = _state(sid, create=False)
    if state is None:
        return []
    with _LOCK:
        callbacks = list(state.callbacks)
        state.callbacks = []
        return callbacks


def list_prompt_session_ids() -> list[str]:
    with _LOCK:
        session_ids = list(_SESSION_DIRECTORIES)
    active: list[str] = []
    for sid in session_ids:
        state = _state(sid, create=False)
        if state is None:
            continue
        with _LOCK:
            if state.instance is not None or bool(state.callbacks):
                active.append(sid)
    return sorted(set(active))


def set_session_status(session_id: str | None, status: dict[str, Any] | None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    normalized = copy.deepcopy(status or {})
    state = _state(sid)
    if state is None:
        return
    with _LOCK:
        if str(normalized.get("type") or "") == "idle":
            state.status = None
        else:
            state.status = normalized


def get_session_status(session_id: str | None) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    state = _state(sid, create=False)
    if state is None:
        return {"type": "idle"}
    with _LOCK:
        if state.status is not None:
            return copy.deepcopy(state.status)
    return {"type": "idle"}


def list_session_statuses() -> dict[str, dict[str, Any]]:
    with _LOCK:
        session_ids = list(_SESSION_DIRECTORIES)
    statuses: dict[str, dict[str, Any]] = {}
    for sid in session_ids:
        state = _state(sid, create=False)
        if state is None:
            continue
        with _LOCK:
            if state.status is not None:
                statuses[sid] = copy.deepcopy(state.status)
    return statuses


def dispose_session(session_id: str | None, *, error: str | None = None) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    directory = _session_directory(sid)
    sessions = _directory_sessions(directory)
    with _LOCK:
        state = sessions.pop(sid, None)
        if state is None:
            return False
        _dispose_prompt_session_locked(sid, state, error=error)
        return True


def dispose_sessions(session_ids: list[str], *, error: str | None = None) -> list[str]:
    disposed: list[str] = []
    for session_id in session_ids:
        if dispose_session(session_id, error=error):
            disposed.append(str(session_id))
    return disposed


def request_session_abort(session_id: str | None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    state = _state(sid, create=False)
    with _LOCK:
        _ABORTED_SESSION_IDS.add(sid)
        if state is not None:
            state.aborted = True


def clear_session_abort(session_id: str | None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    state = _state(sid, create=False)
    with _LOCK:
        _ABORTED_SESSION_IDS.discard(sid)
        if state is not None:
            state.aborted = False


def is_session_aborted(session_id: str | None) -> bool:
    sid = str(session_id or "").strip()
    with _LOCK:
        if sid in _ABORTED_SESSION_IDS:
            return True
    state = _state(sid, create=False)
    if state is None:
        return False
    with _LOCK:
        return state.aborted


def reset_for_tests() -> None:
    with _LOCK:
        _SESSION_DIRECTORIES.clear()
        _ABORTED_SESSION_IDS.clear()
        _DISPOSED_SESSION_IDS.clear()
