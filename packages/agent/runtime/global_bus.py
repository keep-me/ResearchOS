"""Global event bus mirroring OpenCode's GlobalBus event channel."""

from __future__ import annotations

import copy
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GlobalEventEnvelope:
    type: str
    directory: str | None
    payload: Any


Subscription = Callable[[GlobalEventEnvelope], None]

_LOCK = threading.RLock()
_SUBSCRIPTIONS: list[Subscription] = []


def publish_event(directory: str | None, payload: Any) -> GlobalEventEnvelope:
    envelope = GlobalEventEnvelope(
        type="event",
        directory=str(directory or "").strip() or None,
        payload=copy.deepcopy(payload),
    )
    with _LOCK:
        subscribers = list(_SUBSCRIPTIONS)
    for callback in subscribers:
        try:
            callback(envelope)
        except Exception:  # pragma: no cover - defensive path
            logger.exception("Global bus subscriber failed")
    return envelope


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


def reset_for_tests() -> None:
    with _LOCK:
        _SUBSCRIPTIONS.clear()
