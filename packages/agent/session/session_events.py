"""Session event publishing helpers shared by agent runtimes."""

from __future__ import annotations

import copy
from typing import Any

from packages.agent import session_bus
from packages.agent.session.session_bus import SessionBusEvent


def publish_message_updated(message: dict[str, Any]) -> None:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    session_bus.publish(
        SessionBusEvent.MESSAGE_UPDATED,
        {
            "sessionID": info.get("sessionID"),
            "message": copy.deepcopy(message),
        },
    )


def publish_part_updated(session_id: str, part: dict[str, Any]) -> None:
    session_bus.publish(
        SessionBusEvent.PART_UPDATED,
        {
            "sessionID": session_id,
            "part": copy.deepcopy(part),
        },
    )


def publish_part_delta(
    session_id: str,
    message_id: str,
    part_id: str,
    *,
    field: str,
    delta: str,
) -> None:
    session_bus.publish(
        SessionBusEvent.PART_DELTA,
        {
            "sessionID": session_id,
            "messageID": message_id,
            "partID": part_id,
            "field": field,
            "delta": delta,
        },
    )


def publish_part_deleted(session_id: str, part_id: str) -> None:
    session_bus.publish(
        SessionBusEvent.PART_DELETED,
        {
            "sessionID": session_id,
            "partID": part_id,
        },
    )


def publish_message_deleted(session_id: str, message_id: str) -> None:
    session_bus.publish(
        SessionBusEvent.MESSAGE_DELETED,
        {
            "sessionID": session_id,
            "messageID": message_id,
        },
    )

