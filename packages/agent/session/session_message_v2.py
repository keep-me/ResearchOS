"""OpenCode-style MessageV2 facade and part helpers for the native session runtime."""

from __future__ import annotations

import base64
import binascii
import copy
import json
from collections.abc import Iterable, Iterator
from typing import Any

from packages.agent.session.session_errors import normalize_error
from packages.storage.db import session_scope
from packages.storage.repositories import AgentSessionMessageRepository, AgentSessionPartRepository


_ATTACH_RIGHT_PUNCT = set("([{/\\")
_ATTACH_LEFT_PUNCT = set(",.!?;:%)]}/\\")
_WORD_GAP_AFTER_PUNCT = set(",.;:!?")


def is_media(mime: str) -> bool:
    normalized = str(mime or "").strip().lower()
    return normalized.startswith("image/") or normalized == "application/pdf"


def _is_cjk(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _should_insert_ascii_word_gap(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if previous[-1].isspace() or current[0].isspace():
        return False
    if previous[-1] in _WORD_GAP_AFTER_PUNCT and current[0].isascii() and current[0].isalnum():
        return True
    if previous[-1] in _ATTACH_RIGHT_PUNCT or current[0] in _ATTACH_LEFT_PUNCT:
        return False
    if _is_cjk(previous[-1]) or _is_cjk(current[0]):
        return False
    if not previous[-1].isascii() or not current[0].isascii():
        return False
    if not previous[-1].isalnum() or not current[0].isalnum():
        return False
    previous_compact = previous.strip()
    current_compact = current.strip()
    if not previous_compact or not current_compact:
        return False
    if previous_compact.endswith(("-", "_", "/")) or current_compact.startswith(("-", "_", "/")):
        return False
    return True


def append_reasoning_fragment(current: str, fragment: object) -> str:
    next_text = str(fragment or "")
    if not next_text:
        return str(current or "")
    current_text = str(current or "")
    if not current_text:
        return next_text
    return f"{current_text}{' ' if _should_insert_ascii_word_gap(current_text, next_text) else ''}{next_text}"


def merge_reasoning_fragments(fragments: Iterable[object]) -> str:
    merged = ""
    for fragment in fragments:
        merged = append_reasoning_fragment(merged, fragment)
    return merged


def text_part(
    *,
    part_id: str,
    session_id: str,
    message_id: str,
    text: str = "",
    metadata: dict[str, Any] | None = None,
    synthetic: bool | None = None,
    ignored: bool | None = None,
    time: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "text",
        "text": str(text or ""),
    }
    if isinstance(metadata, dict) and metadata:
        payload["metadata"] = copy.deepcopy(metadata)
    if synthetic is not None:
        payload["synthetic"] = bool(synthetic)
    if ignored is not None:
        payload["ignored"] = bool(ignored)
    if isinstance(time, dict) and time:
        payload["time"] = copy.deepcopy(time)
    return payload


def reasoning_part(
    *,
    part_id: str,
    session_id: str,
    message_id: str,
    text: str = "",
    metadata: dict[str, Any] | None = None,
    time: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "reasoning",
        "text": str(text or ""),
    }
    if isinstance(metadata, dict) and metadata:
        payload["metadata"] = copy.deepcopy(metadata)
    if isinstance(time, dict) and time:
        payload["time"] = copy.deepcopy(time)
    return payload


def tool_part(
    *,
    part_id: str,
    session_id: str,
    message_id: str,
    tool: str,
    call_id: str,
    state: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    provider_executed: bool | None = None,
    summary: Any = None,
    data: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "tool",
        "tool": str(tool or ""),
        "callID": str(call_id or ""),
        "state": copy.deepcopy(state or {}),
    }
    if isinstance(metadata, dict) and metadata:
        payload["metadata"] = copy.deepcopy(metadata)
    if provider_executed is not None:
        payload["providerExecuted"] = bool(provider_executed)
    if summary is not None:
        payload["summary"] = summary
    if data is not None:
        payload["data"] = copy.deepcopy(data)
    return payload


def retry_part(
    *,
    part_id: str,
    session_id: str,
    message_id: str,
    attempt: int,
    error: dict[str, Any],
    message: str | None = None,
    delay_ms: int = 0,
    time: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "retry",
        "attempt": int(attempt),
        "error": copy.deepcopy(error or {}),
        "delay_ms": int(delay_ms),
    }
    if message is not None:
        payload["message"] = str(message)
    if isinstance(time, dict) and time:
        payload["time"] = copy.deepcopy(time)
    return payload


def step_start_part(
    *,
    part_id: str,
    session_id: str,
    message_id: str,
    step: int,
    snapshot: str | None = None,
    time: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "step-start",
        "step": int(step),
    }
    if snapshot:
        payload["snapshot"] = snapshot
    if isinstance(time, dict) and time:
        payload["time"] = copy.deepcopy(time)
    return payload


def step_finish_part(
    *,
    part_id: str,
    session_id: str,
    message_id: str,
    step: int,
    reason: str,
    tokens: dict[str, Any],
    cost: Any = None,
    snapshot: str | None = None,
    time: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "step-finish",
        "step": int(step),
        "reason": str(reason or "stop"),
        "tokens": copy.deepcopy(tokens or {}),
        "cost": cost,
    }
    if snapshot:
        payload["snapshot"] = snapshot
    if isinstance(time, dict) and time:
        payload["time"] = copy.deepcopy(time)
    return payload


def patch_part(
    *,
    part_id: str,
    session_id: str,
    message_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "patch",
        **copy.deepcopy(payload or {}),
    }


def merge_error_meta(
    meta: dict[str, Any] | None,
    *,
    error: dict[str, Any],
    finish: str,
) -> dict[str, Any]:
    payload = copy.deepcopy(meta or {})
    payload["error"] = copy.deepcopy(error or {})
    payload["finish"] = str(finish or "error")
    return payload


def cursor_encode(input: dict[str, Any]) -> str:
    payload = {
        "id": str(input.get("id") or "").strip(),
        "time": int(input.get("time") or 0),
    }
    if not payload["id"] or payload["time"] <= 0:
        raise ValueError("invalid message cursor payload")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def cursor_decode(input: str) -> dict[str, Any]:
    raw = str(input or "").strip()
    if not raw:
        raise ValueError("cursor is required")
    padding = "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{raw}{padding}".encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid message cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid message cursor payload")
    return {
        "id": str(payload.get("id") or "").strip(),
        "time": int(payload.get("time") or 0),
    }


class cursor:
    encode = staticmethod(cursor_encode)
    decode = staticmethod(cursor_decode)


def _runtime():
    from packages.agent import session_runtime

    return session_runtime


def _older_message(message: dict[str, Any], before: dict[str, Any]) -> bool:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    time_payload = info.get("time") if isinstance(info.get("time"), dict) else {}
    message_time = int(time_payload.get("created") or 0)
    message_id = str(info.get("id") or "").strip()
    before_time = int(before.get("time") or 0)
    before_id = str(before.get("id") or "").strip()
    return message_time < before_time or (message_time == before_time and message_id < before_id)


def page(
    session_id: str,
    limit: int,
    before: str | None = None,
) -> dict[str, Any]:
    runtime = _runtime()
    normalized_limit = int(limit or 0)
    if normalized_limit <= 0:
        raise ValueError("limit must be a positive integer")
    sid = runtime._session_id(session_id)
    history = runtime.list_session_messages(sid, limit=5000)
    if not history:
        if runtime.get_session_record(sid) is None:
            raise ValueError(f"Session not found: {sid}")
        return {"items": [], "more": False}

    before_cursor = cursor_decode(before) if before else None
    descending: list[dict[str, Any]] = []
    for message in reversed(history):
        if before_cursor is not None and not _older_message(message, before_cursor):
            continue
        descending.append(copy.deepcopy(message))

    if not descending:
        return {"items": [], "more": False}

    more = len(descending) > normalized_limit
    current_page = descending[:normalized_limit]
    items = list(reversed(current_page))
    oldest = current_page[-1]
    oldest_info = oldest.get("info") if isinstance(oldest.get("info"), dict) else {}
    oldest_time = oldest_info.get("time") if isinstance(oldest_info.get("time"), dict) else {}
    payload: dict[str, Any] = {
        "items": items,
        "more": more,
    }
    if more:
        payload["cursor"] = cursor_encode(
            {
                "id": oldest_info.get("id"),
                "time": oldest_time.get("created"),
            }
        )
    return payload


def stream(session_id: str, *, size: int = 50) -> Iterator[dict[str, Any]]:
    before: str | None = None
    while True:
        next_page = page(session_id, size, before=before)
        items = list(next_page.get("items") or [])
        if not items:
            break
        for index in range(len(items) - 1, -1, -1):
            yield copy.deepcopy(items[index])
        if not next_page.get("more") or not next_page.get("cursor"):
            break
        before = str(next_page.get("cursor") or "").strip() or None


def parts(message_id: str, *, session_id: str | None = None) -> list[dict[str, Any]]:
    runtime = _runtime()
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return []
    if session_id is not None:
        _message, rows = runtime._load_message_parts(runtime._session_id(session_id), normalized_message_id)
        return copy.deepcopy(rows)
    with session_scope() as session:
        message_row = AgentSessionMessageRepository(session).get_by_id(normalized_message_id)
        if message_row is None:
            return []
        part_rows = AgentSessionPartRepository(session).list_by_message_ids([normalized_message_id])
        return [
            runtime._serialize_part_row(row)
            for row in sorted(part_rows, key=runtime._part_sort_key)
            if str(row.message_id or "") == normalized_message_id
        ]


def get(session_id: str, message_id: str) -> dict[str, Any]:
    runtime = _runtime()
    payload = runtime.get_session_message_by_id(session_id, message_id)
    if payload is None:
        raise ValueError(f"Message not found: {message_id}")
    return copy.deepcopy(payload)


def filter_compacted(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    completed: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        item = copy.deepcopy(message)
        result.append(item)
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        parts_list = item.get("parts") if isinstance(item.get("parts"), list) else []
        role = str(info.get("role") or "").strip()
        if (
            role == "user"
            and str(info.get("id") or "").strip() in completed
            and any(str(part.get("type") or "") == "compaction" for part in parts_list if isinstance(part, dict))
        ):
            break
        if role == "assistant" and info.get("summary") and info.get("finish") and not info.get("error"):
            parent_id = str(info.get("parentID") or "").strip()
            if parent_id:
                completed.add(parent_id)
    result.reverse()
    return result


def _assistant_text_parts(parts_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for part in parts_list:
        if str(part.get("type") or "") != "text":
            continue
        item = {
            "id": part.get("id"),
            "text": str(part.get("text") or ""),
        }
        if isinstance(part.get("metadata"), dict) and part["metadata"]:
            item["metadata"] = copy.deepcopy(part["metadata"])
        items.append(item)
    return items


def _assistant_reasoning_parts(parts_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for part in parts_list:
        if str(part.get("type") or "") != "reasoning":
            continue
        item = {
            "id": part.get("id"),
            "text": str(part.get("text") or ""),
        }
        if isinstance(part.get("metadata"), dict) and part["metadata"]:
            item["metadata"] = copy.deepcopy(part["metadata"])
        items.append(item)
    return items


def _assistant_content_from_text_parts(text_parts: list[dict[str, Any]]) -> str:
    if not text_parts:
        return ""
    if len(text_parts) == 1:
        return str(text_parts[0].get("text") or "")
    return "\n\n".join(str(item.get("text") or "") for item in text_parts)


def _tool_messages_from_parts(parts_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for part in parts_list:
        if str(part.get("type") or "") != "tool":
            continue
        state = dict(part.get("state") or {})
        status = str(state.get("status") or "").strip().lower()
        if status not in {"completed", "error"}:
            continue
        payload = {
            "success": status == "completed",
            "summary": part.get("summary"),
            "data": copy.deepcopy(part.get("data") or {}),
        }
        item = {
            "role": "tool",
            "tool_call_id": part.get("callID"),
            "name": part.get("tool"),
            "content": json.dumps(payload, ensure_ascii=False),
        }
        if part.get("providerExecuted"):
            item["provider_executed"] = True
        messages.append(item)
    return messages


def _canonicalize_tool_arguments(raw: Any, parsed: Any) -> str:
    if isinstance(parsed, dict):
        return json.dumps(parsed, ensure_ascii=False)
    text = str(raw or "").strip()
    if not text:
        return "{}"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(value, ensure_ascii=False)


def _tool_calls_from_parts(parts_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for part in parts_list:
        if str(part.get("type") or "") != "tool":
            continue
        state = dict(part.get("state") or {})
        call: dict[str, Any] = {
            "id": part.get("callID"),
            "type": "function",
            "function": {
                "name": part.get("tool"),
                "arguments": _canonicalize_tool_arguments(state.get("raw"), state.get("input")),
            },
        }
        if isinstance(part.get("metadata"), dict) and part["metadata"]:
            call["metadata"] = copy.deepcopy(part["metadata"])
        if part.get("providerExecuted"):
            call["provider_executed"] = True
        calls.append(call)
    return calls


def _split_assistant_segments(parts_list: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    indexes = [index for index, part in enumerate(parts_list) if str(part.get("type") or "") == "step-start"]
    if not indexes:
        return [parts_list]
    segments: list[list[dict[str, Any]]] = []
    for offset, start_index in enumerate(indexes):
        end_index = indexes[offset + 1] if offset + 1 < len(indexes) else len(parts_list)
        segments.append(parts_list[start_index:end_index])
    return segments


def _provider_matches_model(info: dict[str, Any], model: dict[str, Any] | None) -> bool:
    if not isinstance(model, dict):
        return False
    provider_id = str(model.get("providerID") or model.get("provider_id") or "").strip()
    model_id = str(model.get("modelID") or model.get("model_id") or model.get("id") or "").strip()
    if not provider_id or not model_id:
        return False
    return provider_id == str(info.get("providerID") or "").strip() and model_id == str(info.get("modelID") or "").strip()


def to_model_messages(
    input: list[dict[str, Any]],
    model: dict[str, Any] | None = None,
    *,
    strip_media: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in input or []:
        if not isinstance(message, dict):
            continue
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        parts_list = message.get("parts") if isinstance(message.get("parts"), list) else []
        if not parts_list:
            continue
        role = str(info.get("role") or "").strip()

        if role == "user":
            content: list[dict[str, Any]] = []
            text_only = True
            for part in parts_list:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "")
                if part_type == "text" and not part.get("ignored"):
                    content.append({"type": "text", "text": str(part.get("text") or "")})
                    continue
                if part_type == "file":
                    mime = str(part.get("mime") or "")
                    if strip_media and is_media(mime):
                        content.append(
                            {
                                "type": "text",
                                "text": f"[Attached {mime or 'file'}: {str(part.get('filename') or 'file')}]",
                            }
                        )
                    else:
                        content.append(
                            {
                                "type": "file",
                                "url": part.get("url"),
                                "filename": part.get("filename"),
                                "mime": mime,
                            }
                        )
                        text_only = False
                    continue
                if part_type == "compaction":
                    content.append({"type": "text", "text": "What did we do so far?"})
                    continue
                if part_type == "subtask":
                    content.append({"type": "text", "text": "The following tool was executed by the user"})

            if not content:
                continue
            payload: dict[str, Any] = {
                "role": "user",
                "content": content[0]["text"] if text_only and len(content) == 1 and content[0]["type"] == "text" else content,
            }
            if isinstance(info.get("tools"), dict):
                payload["tools"] = copy.deepcopy(info["tools"])
            if str(info.get("system") or "").strip():
                payload["system"] = str(info.get("system"))
            if str(info.get("variant") or "").strip():
                payload["variant"] = str(info.get("variant"))
            if isinstance(info.get("activeSkillIDs"), list):
                payload["active_skill_ids"] = [
                    str(item).strip()
                    for item in info.get("activeSkillIDs") or []
                    if str(item).strip()
                ]
            result.append(payload)
            continue

        if role != "assistant":
            continue

        if info.get("error"):
            error_name = str((info.get("error") or {}).get("name") or "").strip()
            has_nontrivial_parts = any(
                str(part.get("type") or "") not in {"step-start", "reasoning"}
                for part in parts_list
                if isinstance(part, dict)
            )
            if error_name != "AbortedError" or not has_nontrivial_parts:
                continue

        for segment in _split_assistant_segments(parts_list):
            text_parts = _assistant_text_parts(segment)
            reasoning_parts = _assistant_reasoning_parts(segment)
            tool_calls = _tool_calls_from_parts(segment)
            payload = {
                "role": "assistant",
                "content": _assistant_content_from_text_parts(text_parts),
            }
            if text_parts and (len(text_parts) > 1 or any(isinstance(item.get("metadata"), dict) for item in text_parts)):
                payload["text_parts"] = copy.deepcopy(text_parts)
            if reasoning_parts:
                payload["reasoning_content"] = merge_reasoning_fragments(
                    str(item.get("text") or "") for item in reasoning_parts
                )
                payload["reasoning_parts"] = copy.deepcopy(reasoning_parts)
            if tool_calls:
                payload["tool_calls"] = copy.deepcopy(tool_calls)
            if isinstance(info.get("providerMetadata"), dict) and info["providerMetadata"]:
                payload["provider_metadata"] = copy.deepcopy(info["providerMetadata"])
            if _provider_matches_model(info, model) and payload.get("text_parts"):
                payload["text_parts"] = copy.deepcopy(payload["text_parts"])
            result.append(payload)
            result.extend(_tool_messages_from_parts(segment))
    return result


def from_error(error: Any, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(error, dict) and str(error.get("name") or "").strip():
        payload = copy.deepcopy(error)
    else:
        payload = normalize_error(error)
    provider_id = str((ctx or {}).get("providerID") or (ctx or {}).get("provider_id") or "").strip()
    if payload.get("name") == "AuthError" and provider_id and not str(payload.get("providerID") or "").strip():
        payload["providerID"] = provider_id
    return payload

