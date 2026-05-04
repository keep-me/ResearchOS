"""Session-scoped event bus mirroring OpenCode-style runtime events."""

from __future__ import annotations

import copy
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from packages.agent import global_bus

logger = logging.getLogger(__name__)


class SessionBusEvent:
    STATUS = "session.status"
    IDLE = "session.idle"
    MESSAGE_UPDATED = "session.message.updated"
    PART_UPDATED = "session.message.part.updated"
    PART_DELTA = "session.message.part.delta"
    PART_DELETED = "session.message.part.deleted"
    MESSAGE_DELETED = "session.message.deleted"
    ERROR = "session.error"
    PROMPT_STARTED = "session.prompt.started"
    PROMPT_FINISHED = "session.prompt.finished"
    PROMPT_PAUSED = "session.prompt.paused"
    PROMPT_QUEUED = "session.prompt.queued"
    STEP_STARTED = "session.step.started"
    STEP_FINISHED = "session.step.finished"


@dataclass(frozen=True)
class SessionEvent:
    type: str
    properties: dict[str, Any]


Subscription = Callable[[SessionEvent], None]
Predicate = Callable[[SessionEvent], bool]

_LOCK = threading.RLock()
_SUBSCRIPTIONS: list[Subscription] = []


def publish(event_type: str, properties: dict[str, Any] | None = None) -> SessionEvent:
    payload = SessionEvent(
        type=str(event_type or "").strip(),
        properties=copy.deepcopy(properties or {}),
    )
    with _LOCK:
        subscribers = list(_SUBSCRIPTIONS)
    for callback in subscribers:
        try:
            callback(payload)
        except Exception:  # pragma: no cover - defensive path
            logger.exception("Session bus subscriber failed")
    session_id = str(payload.properties.get("sessionID") or "").strip() or None
    if session_id:
        try:
            from packages.agent.session.session_runtime import get_session_record

            record = get_session_record(session_id) or {}
            directory = (
                str(record.get("directory") or record.get("workspace_path") or "").strip() or None
            )
        except Exception:  # pragma: no cover - defensive path
            directory = None
        global_bus.publish_event(
            directory,
            {
                "type": payload.type,
                "properties": copy.deepcopy(payload.properties),
            },
        )
    return payload


def subscribe_all(callback: Subscription) -> Callable[[], None]:
    with _LOCK:
        _SUBSCRIPTIONS.append(callback)

    def _unsubscribe() -> None:
        with _LOCK:
            try:
                _SUBSCRIPTIONS.remove(callback)
            except ValueError:
                return

    return _unsubscribe


def subscribe(event_type: str, callback: Subscription) -> Callable[[], None]:
    normalized = str(event_type or "").strip()

    def _filtered(event: SessionEvent) -> None:
        if event.type != normalized:
            return
        callback(event)

    return subscribe_all(_filtered)


def wait_for(
    event_type: str,
    *,
    predicate: Predicate | None = None,
    timeout_ms: int | None = None,
) -> SessionEvent | None:
    matched: list[SessionEvent] = []
    done = threading.Event()

    def _handle(event: SessionEvent) -> None:
        if predicate is not None and not predicate(event):
            return
        matched.append(event)
        done.set()

    unsubscribe = subscribe(event_type, _handle)
    try:
        timeout = None if timeout_ms is None else max(timeout_ms, 0) / 1000
        done.wait(timeout=timeout)
    finally:
        unsubscribe()
    return matched[0] if matched else None


def reset_for_tests() -> None:
    with _LOCK:
        _SUBSCRIPTIONS.clear()
