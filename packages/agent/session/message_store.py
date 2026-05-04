"""MessageV2 storage facade for agent sessions."""

from __future__ import annotations

from typing import Any

from packages.agent import session_store

DB_WRITE_LOCK = session_store.DB_WRITE_LOCK

ensure_session_record = session_store.ensure_session_record
get_session_record = session_store.get_session_record
get_session_message_by_id = session_store.get_session_message_by_id
list_session_messages = session_store.list_session_messages
list_sessions = session_store.list_sessions
update_message_parts = session_store.update_message_parts
_serialize_part_row = session_store._serialize_part_row
_part_sort_key = session_store._part_sort_key


def delete_session_message(session_id: str | None, message_id: str) -> bool:
    from packages.agent.session.session_runtime import (
        delete_session_message as _delete_session_message,
    )

    return _delete_session_message(session_id, message_id)


def load_message_parts(message: dict[str, Any] | None) -> list[dict[str, Any]]:
    parts = (message or {}).get("parts")
    return [dict(part) for part in parts] if isinstance(parts, list) else []


__all__ = [
    "DB_WRITE_LOCK",
    "delete_session_message",
    "ensure_session_record",
    "get_session_message_by_id",
    "get_session_record",
    "list_session_messages",
    "list_sessions",
    "load_message_parts",
    "update_message_parts",
]
