"""Persistent session runtime for the native ResearchOS assistant."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.config import get_settings
from packages.agent import session_events, session_revert
from packages.agent import (
    session_bus,
)
from packages.agent.session.session_bus import SessionBusEvent
from packages.agent.session import sse_events
from packages.agent import session_message_v2, session_store
from packages.agent.session.session_lifecycle import (
    clear_session_abort,
    finish_prompt_instance,
    get_prompt_instance,
    get_session_status as _get_session_status,
    is_session_aborted as _is_session_aborted,
    drain_prompt_callbacks,
    reject_prompt_callbacks,
    request_session_abort as _request_session_abort,
    set_session_status as _set_session_status,
)
from packages.integrations.llm_client import LLMClient
from packages.storage.db import session_scope
from packages.storage.repositories import (
    AgentSessionMessageRepository,
    AgentSessionRepository,
    AgentSessionTodoRepository,
)

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = "What did we do so far?"
_DB_WRITE_LOCK = session_store.DB_WRITE_LOCK

ensure_session_record = session_store.ensure_session_record
get_session_record = session_store.get_session_record
list_sessions = session_store.list_sessions
list_session_messages = session_store.list_session_messages
get_session_message_by_id = session_store.get_session_message_by_id
_serialize_part_row = session_store._serialize_part_row
_part_sort_key = session_store._part_sort_key


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now_ms() -> int:
    return int(_now_dt().timestamp() * 1000)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _is_remote_workspace(workspace_server_id: str | None) -> bool:
    return session_revert.is_remote_workspace(workspace_server_id)


def _normalize_path(value: str | None, *, remote: bool = False) -> str:
    return session_revert.normalize_path(value, remote=remote)


def _normalize_remote_path(value: str | None) -> str:
    return session_revert.normalize_remote_path(value)


def _diff_counts(before: str, after: str) -> tuple[int, int]:
    return session_revert.diff_counts(before, after)


def _path_is_absolute(value: str | None, *, remote: bool) -> bool:
    return session_revert.path_is_absolute(value, remote=remote)


def _diff_exists(diff: dict[str, Any], *, before: bool) -> bool | None:
    return session_revert.diff_exists(diff, before=before)


def _resolved_diff_path(diff: dict[str, Any]) -> str:
    return session_revert.resolved_diff_path(diff)


def _diff_identity(diff: dict[str, Any]) -> str:
    return session_revert.diff_identity(diff)


def _merge_diff_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    return session_revert.merge_diff_records(existing, incoming)


def _aggregate_diffs(diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return session_revert.aggregate_diffs(diffs)


def _session_id(value: str | None) -> str:
    normalized = _clean_text(value)
    return normalized or "default"


def is_session_aborted(session_id: str | None) -> bool:
    return _is_session_aborted(session_id)


def _message_id(value: str | None) -> str:
    normalized = _clean_text(value)
    return normalized or f"message_{uuid4().hex}"


def _default_tokens() -> dict[str, Any]:
    return {
        "total": None,
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }


def _normalize_tokens(value: Any) -> dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    cache_payload = payload.get("cache") if isinstance(payload.get("cache"), dict) else {}
    total = payload.get("total")
    return {
        "total": int(total) if total not in {None, ""} else None,
        "input": int(payload.get("input") or 0),
        "output": int(payload.get("output") or 0),
        "reasoning": int(payload.get("reasoning") or 0),
        "cache": {
            "read": int(cache_payload.get("read") or 0),
            "write": int(cache_payload.get("write") or 0),
        },
    }


def _normalize_output_format(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        normalized = _clean_text(value).lower()
        if normalized == "text":
            return {"type": "text"}
        return None
    if not isinstance(value, dict):
        return None
    output_type = _clean_text(value.get("type")).lower()
    if output_type == "text":
        return {"type": "text"}
    if output_type != "json_schema":
        return None
    payload: dict[str, Any] = {
        "type": "json_schema",
        "schema": copy.deepcopy(value.get("schema") or {}),
    }
    retry_count = value.get("retryCount")
    if retry_count is None:
        retry_count = value.get("retry_count")
    if retry_count not in {None, ""}:
        payload["retryCount"] = max(int(retry_count), 0)
    return payload


def resolve_default_model_identity(reasoning_level: str | None = None) -> dict[str, str]:
    provider = ""
    model = ""
    normalized_reasoning = _clean_text(reasoning_level).lower() or None
    try:
        llm = LLMClient()
        resolver = getattr(llm, "_resolve_model_target", None)
        if callable(resolver):
            target = resolver("rag", None, variant_override=normalized_reasoning)
            provider = _clean_text(getattr(target, "provider", None))
            model = _clean_text(getattr(target, "model", None))
        if not provider:
            provider = _clean_text(getattr(llm, "provider", None))
    except Exception:
        logger.debug("Failed to resolve default model identity", exc_info=True)

    if not provider or not model:
        settings = get_settings()
        provider = provider or _clean_text(getattr(settings, "llm_provider", None))
        model = model or _clean_text(getattr(settings, "llm_model_deep", None))

    return {
        "providerID": provider,
        "modelID": model,
    }


def _normalize_model_identity(
    value: Any,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    provider = ""
    model = ""
    if isinstance(value, dict):
        provider = _clean_text(value.get("providerID") or value.get("provider_id"))
        model = _clean_text(value.get("modelID") or value.get("model_id") or value.get("id"))
    elif isinstance(value, str):
        model = _clean_text(value)
    provider = provider or _clean_text(provider_id)
    model = model or _clean_text(model_id)
    if isinstance(default, dict):
        provider = provider or _clean_text(default.get("providerID"))
        model = model or _clean_text(default.get("modelID"))
    if not provider and not model:
        return None
    return {
        "providerID": provider,
        "modelID": model,
    }


def _normalize_active_skill_ids(values: Any) -> list[str] | None:
    if not isinstance(values, list):
        return None
    items = [str(item).strip() for item in values if str(item).strip()]
    return items or None


def _normalize_mounted_paper_ids(values: Any) -> list[str] | None:
    if not isinstance(values, list):
        return None
    items = [str(item).strip() for item in values if str(item).strip()]
    return items or None


def _assistant_path(meta: dict[str, Any] | None) -> dict[str, str] | None:
    payload = dict(meta or {})
    raw_path = payload.get("path") if isinstance(payload.get("path"), dict) else {}
    cwd = _clean_text(raw_path.get("cwd")) or _clean_text(payload.get("cwd"))
    root = _clean_text(raw_path.get("root")) or _clean_text(payload.get("root"))
    if not cwd and root:
        cwd = root
    if not root and cwd:
        root = cwd
    if not cwd and not root:
        return None
    return {
        "cwd": cwd or root,
        "root": root or cwd,
    }


def build_user_message_meta(
    *,
    agent: str | None = None,
    model: dict[str, Any] | None = None,
    format: Any = None,
    tools: dict[str, bool] | None = None,
    system: str | None = None,
    variant: str | None = None,
    active_skill_ids: list[str] | None = None,
    mounted_paper_ids: list[str] | None = None,
    mounted_primary_paper_id: str | None = None,
    reasoning_level: str | None = None,
    fallback_agent: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    normalized_agent = _clean_text(agent) or _clean_text(fallback_agent)
    if normalized_agent:
        payload["agent"] = normalized_agent
    normalized_model = _normalize_model_identity(
        model,
        default=resolve_default_model_identity(reasoning_level),
    )
    if normalized_model is not None:
        payload["model"] = normalized_model
    normalized_format = _normalize_output_format(format)
    if normalized_format is not None:
        payload["format"] = normalized_format
    if isinstance(tools, dict):
        payload["tools"] = copy.deepcopy(tools)
    normalized_system = _clean_text(system)
    if normalized_system:
        payload["system"] = normalized_system
    normalized_variant = _clean_text(variant)
    normalized_reasoning = _clean_text(reasoning_level).lower()
    if normalized_variant:
        payload["variant"] = normalized_variant
    elif normalized_reasoning and normalized_reasoning != "default":
        payload["variant"] = normalized_reasoning
    normalized_active_skill_ids = _normalize_active_skill_ids(active_skill_ids)
    if normalized_active_skill_ids is not None:
        payload["activeSkillIDs"] = normalized_active_skill_ids
    normalized_mounted_paper_ids = _normalize_mounted_paper_ids(mounted_paper_ids)
    if normalized_mounted_paper_ids is not None:
        payload["mountedPaperIDs"] = normalized_mounted_paper_ids
    normalized_primary_paper_id = _clean_text(mounted_primary_paper_id)
    if normalized_primary_paper_id:
        payload["mountedPrimaryPaperID"] = normalized_primary_paper_id
    return payload


def _normalize_user_message_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(meta or {})
    normalized: dict[str, Any] = {}
    agent = _clean_text(payload.get("agent"))
    if agent:
        normalized["agent"] = agent
    model = _normalize_model_identity(
        payload.get("model"),
        provider_id=payload.get("providerID"),
        model_id=payload.get("modelID") or payload.get("model"),
    )
    if model is not None:
        normalized["model"] = model
    format_payload = _normalize_output_format(payload.get("format"))
    if format_payload is not None:
        normalized["format"] = format_payload
    if isinstance(payload.get("tools"), dict):
        normalized["tools"] = copy.deepcopy(payload["tools"])
    system = _clean_text(payload.get("system"))
    if system:
        normalized["system"] = system
    variant = _clean_text(payload.get("variant"))
    if variant:
        normalized["variant"] = variant
    active_skill_ids = _normalize_active_skill_ids(
        payload.get("activeSkillIDs")
        if isinstance(payload.get("activeSkillIDs"), list)
        else payload.get("active_skill_ids")
    )
    if active_skill_ids is not None:
        normalized["activeSkillIDs"] = active_skill_ids
    mounted_paper_ids = _normalize_mounted_paper_ids(
        payload.get("mountedPaperIDs")
        if isinstance(payload.get("mountedPaperIDs"), list)
        else payload.get("mounted_paper_ids")
    )
    if mounted_paper_ids is not None:
        normalized["mountedPaperIDs"] = mounted_paper_ids
    mounted_primary_paper_id = _clean_text(
        payload.get("mountedPrimaryPaperID")
        or payload.get("mounted_primary_paper_id")
    )
    if mounted_primary_paper_id:
        normalized["mountedPrimaryPaperID"] = mounted_primary_paper_id
    if isinstance(payload.get("summary"), dict) and payload["summary"]:
        normalized["summary"] = copy.deepcopy(payload["summary"])
    return normalized


def _normalize_assistant_message_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(meta or {})
    normalized: dict[str, Any] = {}
    model = _normalize_model_identity(
        payload.get("model"),
        provider_id=payload.get("providerID"),
        model_id=payload.get("modelID") or payload.get("model"),
    )
    if model is not None:
        normalized["providerID"] = model["providerID"]
        normalized["modelID"] = model["modelID"]
    mode = _clean_text(payload.get("mode")) or _clean_text(payload.get("agent"))
    if mode:
        normalized["mode"] = mode
    agent = _clean_text(payload.get("agent")) or mode
    if agent:
        normalized["agent"] = agent
    path = _assistant_path(payload)
    if path is not None:
        normalized["path"] = path
        normalized["cwd"] = path["cwd"]
        normalized["root"] = path["root"]
    normalized["cost"] = payload.get("cost") if payload.get("cost") is not None else 0.0
    normalized["tokens"] = _normalize_tokens(payload.get("tokens"))
    finish = _clean_text(payload.get("finish"))
    if finish:
        normalized["finish"] = finish
    elif "finish" in payload:
        normalized["finish"] = None
    if isinstance(payload.get("error"), dict):
        normalized["error"] = copy.deepcopy(payload["error"])
    if isinstance(payload.get("providerMetadata"), dict):
        normalized["providerMetadata"] = copy.deepcopy(payload["providerMetadata"])
    if payload.get("summary") is not None:
        normalized["summary"] = bool(payload.get("summary"))
    if "structured" in payload and payload.get("structured") is not None:
        normalized["structured"] = copy.deepcopy(payload.get("structured"))
    variant = _clean_text(payload.get("variant"))
    if variant:
        normalized["variant"] = variant
    completed = payload.get("completed")
    if completed is None and isinstance(payload.get("time"), dict):
        completed = payload["time"].get("completed")
    if completed not in {None, ""}:
        normalized["completed"] = int(completed)
    return normalized


def _normalize_message_meta(role: str, meta: dict[str, Any] | None) -> dict[str, Any]:
    normalized_role = _clean_text(role) or "user"
    if normalized_role == "assistant":
        return _normalize_assistant_message_meta(meta)
    return _normalize_user_message_meta(meta)


def _message_meta_from_info(info: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(info or {})
    return _normalize_message_meta(str(payload.get("role") or "user"), payload)


def _part_id(value: str | None) -> str:
    normalized = _clean_text(value)
    return normalized or f"part_{uuid4().hex}"


def _project_key(directory: str, workspace_server_id: str | None) -> str:
    server_id = _clean_text(workspace_server_id).lower() or "local"
    return f"{server_id}::{directory or 'global'}"


def _project_id_for(directory: str, workspace_server_id: str | None) -> str:
    if not directory:
        return "global"
    digest = hashlib.sha1(_project_key(directory, workspace_server_id).encode("utf-8")).hexdigest()[:16]
    return f"project_{digest}"


def _session_title(session_id: str, title: str | None, directory: str) -> str:
    explicit = _clean_text(title)
    if explicit:
        return explicit[:256]
    if directory:
        if directory.startswith("/") and ":" not in directory:
            candidate = directory.rstrip("/").split("/")[-1]
        else:
            candidate = Path(directory).name
        candidate = _clean_text(candidate)
        if candidate:
            return candidate[:256]
    return session_id[:256]


def delete_session(session_id: str | None) -> bool:
    sid = _session_id(session_id)
    deleted = session_store.delete_session_record(sid)
    if deleted:
        clear_session_abort(sid)
        _set_session_status(sid, {"type": "idle"})
    return deleted


def _aggregate_message_content(parts: list[dict[str, Any]], *, role: str) -> str:
    del role
    return "".join(str(part.get("text") or "") for part in parts if str(part.get("type") or "") == "text")


def _publish_message_updated(message: dict[str, Any]) -> None:
    session_events.publish_message_updated(message)


def _publish_part_updated(session_id: str, part: dict[str, Any]) -> None:
    session_events.publish_part_updated(session_id, part)


def _publish_part_delta(
    session_id: str,
    message_id: str,
    part_id: str,
    *,
    field: str,
    delta: str,
) -> None:
    session_events.publish_part_delta(
        session_id,
        message_id,
        part_id,
        field=field,
        delta=delta,
    )


def _publish_part_deleted(session_id: str, part_id: str) -> None:
    session_events.publish_part_deleted(session_id, part_id)


def _publish_message_deleted(session_id: str, message_id: str) -> None:
    session_events.publish_message_deleted(session_id, message_id)


def append_session_message(
    *,
    session_id: str,
    role: str,
    content: str,
    parent_id: str | None = None,
    meta: dict | None = None,
    parts: list[dict] | None = None,
    message_id: str | None = None,
    message_type: str = "message",
) -> dict[str, Any]:
    message = session_store.append_session_message(
        session_id=session_id,
        role=role,
        content=content,
        parent_id=parent_id,
        meta=meta,
        parts=parts,
        message_id=message_id,
        message_type=message_type,
    )
    session_id_value = str((message.get("info") or {}).get("sessionID") or session_id or "").strip()
    _publish_message_updated(message)
    for part in message["parts"]:
        _publish_part_updated(session_id_value, part)
    return message


def persist_assistant_message(
    *,
    session_id: str,
    parent_id: str | None = None,
    meta: dict | None = None,
    parts: list[dict] | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    message = session_store.persist_assistant_message(
        session_id=session_id,
        parent_id=parent_id,
        meta=meta,
        parts=parts,
        message_id=message_id,
    )
    session_id_value = str((message.get("info") or {}).get("sessionID") or session_id or "").strip()
    _publish_message_updated(message)
    for part in message["parts"]:
        _publish_part_updated(session_id_value, part)
    return message


def _user_content_from_parts(parts: list[dict[str, Any]]) -> Any:
    if not parts:
        return ""
    if len(parts) == 1 and str(parts[0].get("type") or "") == "text":
        return str(parts[0].get("text") or "")
    content: list[dict[str, Any]] = []
    for part in parts:
        part_type = str(part.get("type") or "")
        if part_type == "text":
            content.append({"type": "text", "text": str(part.get("text") or "")})
        elif part_type == "file":
            content.append(
                {
                    "type": "file",
                    "url": part.get("url"),
                    "filename": part.get("filename"),
                    "mime": part.get("mime"),
                }
            )
    return content


def _assistant_text_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for part in parts:
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


def _assistant_reasoning_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for part in parts:
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


def _tool_messages_from_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for part in parts:
        if str(part.get("type") or "") != "tool":
            continue
        state = dict(part.get("state") or {})
        status = _clean_text(state.get("status"))
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
    if text:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return text
        return json.dumps(value, ensure_ascii=False)
    return "{}"


def _tool_calls_from_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for part in parts:
        if str(part.get("type") or "") != "tool":
            continue
        state = dict(part.get("state") or {})
        input_payload = state.get("input")
        raw_payload = state.get("raw")
        arguments = _canonicalize_tool_arguments(raw_payload, input_payload)
        call: dict[str, Any] = {
            "id": part.get("callID"),
            "type": "function",
            "function": {
                "name": part.get("tool"),
                "arguments": arguments,
            },
        }
        if isinstance(part.get("metadata"), dict) and part["metadata"]:
            call["metadata"] = copy.deepcopy(part["metadata"])
        if part.get("providerExecuted"):
            call["provider_executed"] = True
        calls.append(call)
    return calls


def _split_assistant_segments(parts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    step_indexes = [index for index, part in enumerate(parts) if str(part.get("type") or "") == "step-start"]
    if not step_indexes:
        return [parts]
    segments: list[list[dict[str, Any]]] = []
    for position, start_index in enumerate(step_indexes):
        end_index = step_indexes[position + 1] if position + 1 < len(step_indexes) else len(parts)
        segments.append(parts[start_index:end_index])
    return segments


def load_agent_messages(
    session_id: str | None,
    *,
    until_message_id: str | None = None,
    include_assistants_after_until_user: bool = False,
) -> list[dict[str, Any]]:
    sid = _session_id(session_id)
    history = list_session_messages(sid, limit=5000)
    result: list[dict[str, Any]] = []
    cutoff = _clean_text(until_message_id) or None
    past_until = False

    for message in history:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        role = _clean_text(info.get("role"))

        if past_until and role in {"user", "system"}:
            continue

        if any(str(part.get("type") or "") == "compaction" for part in parts):
            result = [{"role": "user", "content": _SUMMARY_PROMPT}]
            if cutoff and str(info.get("id") or "") == cutoff:
                if not include_assistants_after_until_user:
                    break
                past_until = True
            continue

        if role == "user":
            payload: dict[str, Any] = {
                "role": "user",
                "content": _user_content_from_parts(parts),
            }
            if isinstance(info.get("format"), dict):
                payload["format"] = copy.deepcopy(info["format"])
            if isinstance(info.get("tools"), dict):
                payload["tools"] = copy.deepcopy(info["tools"])
            if _clean_text(info.get("system")):
                payload["system"] = str(info.get("system"))
            if _clean_text(info.get("variant")):
                payload["variant"] = str(info.get("variant"))
            if isinstance(info.get("activeSkillIDs"), list):
                payload["active_skill_ids"] = [
                    str(item).strip()
                    for item in (info.get("activeSkillIDs") or [])
                    if str(item).strip()
                ]
            if isinstance(info.get("mountedPaperIDs"), list):
                payload["mounted_paper_ids"] = [
                    str(item).strip()
                    for item in (info.get("mountedPaperIDs") or [])
                    if str(item).strip()
                ]
            if _clean_text(info.get("mountedPrimaryPaperID")):
                payload["mounted_primary_paper_id"] = str(info.get("mountedPrimaryPaperID"))
            result.append(payload)
        elif role == "assistant":
            segments = _split_assistant_segments(parts)
            for segment in segments:
                text_parts = _assistant_text_parts(segment)
                reasoning_parts = _assistant_reasoning_parts(segment)
                tool_calls = _tool_calls_from_parts(segment)
                payload = {
                    "role": "assistant",
                    "content": _assistant_content_from_text_parts(text_parts),
                }
                if text_parts and (
                    len(text_parts) > 1 or any(isinstance(item.get("metadata"), dict) for item in text_parts)
                ):
                    payload["text_parts"] = copy.deepcopy(text_parts)
                if reasoning_parts:
                    payload["reasoning_content"] = session_message_v2.merge_reasoning_fragments(
                        str(item.get("text") or "") for item in reasoning_parts
                    )
                    payload["reasoning_parts"] = copy.deepcopy(reasoning_parts)
                if tool_calls:
                    payload["tool_calls"] = copy.deepcopy(tool_calls)
                if isinstance(info.get("providerMetadata"), dict) and info["providerMetadata"]:
                    payload["provider_metadata"] = copy.deepcopy(info["providerMetadata"])
                result.append(payload)
                result.extend(_tool_messages_from_parts(segment))

        if cutoff and str(info.get("id") or "") == cutoff:
            if not include_assistants_after_until_user:
                break
            past_until = True

    return result


def get_latest_user_message_id(session_id: str | None) -> str | None:
    history = list_session_messages(session_id, limit=5000)
    for message in reversed(history):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if str(info.get("role") or "") == "user":
            return str(info.get("id") or "")
    return None


def get_session_turn_state(session_id: str | None) -> dict[str, Any] | None:
    history = list_session_messages(session_id, limit=5000)
    if not history:
        return None

    latest_user_index: int | None = None
    latest_user_id: str | None = None
    latest_assistant_index: int | None = None
    latest_assistant_id: str | None = None
    latest_finished_assistant_index: int | None = None
    latest_finished_assistant_id: str | None = None
    latest_finished_assistant_finish: str | None = None

    for index, message in enumerate(history):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        role = str(info.get("role") or "").strip()
        if role == "user":
            latest_user_index = index
            latest_user_id = str(info.get("id") or "").strip() or None
            continue
        if role != "assistant":
            continue
        latest_assistant_index = index
        latest_assistant_id = str(info.get("id") or "").strip() or None
        finish = str(info.get("finish") or "").strip()
        if finish and finish not in {"tool-calls", "unknown"}:
            latest_finished_assistant_index = index
            latest_finished_assistant_id = latest_assistant_id
            latest_finished_assistant_finish = finish

    if latest_user_id is None:
        return None

    has_pending_prompt = True
    if (
        latest_assistant_index is not None
        and latest_user_index is not None
        and latest_assistant_index > latest_user_index
        and latest_finished_assistant_index == latest_assistant_index
    ):
        has_pending_prompt = False

    return {
        "request_message_id": latest_user_id,
        "assistant_message_id": latest_assistant_id,
        "latest_finished_assistant_id": latest_finished_assistant_id,
        "latest_finished_assistant_finish": latest_finished_assistant_finish,
        "has_pending_prompt": has_pending_prompt,
    }


def sync_external_transcript(
    session_id: str | None,
    messages: list[dict[str, Any]],
    *,
    reasoning_level: str | None = None,
    active_skill_ids: list[str] | None = None,
    mode: str | None = None,
) -> None:
    sid = _session_id(session_id)
    session_record = get_session_record(sid) or {}
    persisted = load_agent_messages(sid)
    normalized_reasoning_level = _clean_text(reasoning_level).lower() or None
    normalized_active_skill_ids = [
        str(item).strip()
        for item in (active_skill_ids or [])
        if str(item).strip()
    ]
    for index, item in enumerate(messages or []):
        if index < len(persisted) or not isinstance(item, dict):
            continue
        role = _clean_text(item.get("role")) or "user"
        content = item.get("content")
        meta: dict[str, Any] = {}
        if role == "user":
            meta = build_user_message_meta(
                agent=str(item.get("agent") or "").strip() or None,
                model=item.get("model") if isinstance(item.get("model"), dict) else None,
                format=item.get("format"),
                tools=item.get("tools") if isinstance(item.get("tools"), dict) else None,
                system=str(item.get("system") or "").strip() or None,
                variant=str(item.get("variant") or "").strip() or None,
                active_skill_ids=normalized_active_skill_ids or None,
                reasoning_level=normalized_reasoning_level,
                fallback_agent=(
                    _clean_text(mode)
                    or _clean_text(session_record.get("mode"))
                    or "build"
                ),
            )
        parts: list[dict[str, Any]] | None = None
        content_text = ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if _clean_text(part.get("type")) == "file":
                    parts.append(
                        {
                            "type": "file",
                            "url": part.get("url"),
                            "filename": part.get("filename"),
                            "mime": part.get("mime"),
                        }
                    )
                else:
                    text = str(part.get("text") or "")
                    content_text += text
                    parts.append({"type": "text", "text": text})
        else:
            content_text = str(content or "")
        append_session_message(
            session_id=sid,
            role=role,
            content=content_text,
            meta=meta or None,
            parts=parts,
        )


def get_session_todos(session_id: str | None) -> list[dict[str, Any]]:
    sid = _session_id(session_id)
    with session_scope() as session:
        rows = AgentSessionTodoRepository(session).list_for_session(sid)
        return [
            {
                "id": row.id,
                "content": row.content,
                "status": row.status,
                "priority": row.priority,
            }
            for row in rows
        ]


def replace_session_todos(session_id: str | None, todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sid = _session_id(session_id)
    ensure_session_record(sid)
    with _DB_WRITE_LOCK, session_scope() as session:
        rows = AgentSessionTodoRepository(session).replace(sid, todos or [])
        AgentSessionRepository(session).touch(sid)
        return [
            {
                "id": row.id,
                "content": row.content,
                "status": row.status,
                "priority": row.priority,
            }
            for row in rows
        ]


def _abort_pending_message_context(
    session_id: str,
    pending_permissions: list[dict[str, Any]],
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    turn_state = get_session_turn_state(session_id) or {}
    candidate_ids: list[str] = []
    for item in reversed(pending_permissions):
        tool = item.get("tool") if isinstance(item.get("tool"), dict) else {}
        message_id = _clean_text(tool.get("messageID"))
        if message_id and message_id not in candidate_ids:
            candidate_ids.append(message_id)
    for fallback_id in (
        turn_state.get("assistant_message_id"),
        turn_state.get("latest_finished_assistant_id"),
    ):
        message_id = _clean_text(fallback_id)
        if message_id and message_id not in candidate_ids:
            candidate_ids.append(message_id)

    for message_id in candidate_ids:
        message = get_session_message_by_id(session_id, message_id) or {}
        if not message:
            continue
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        parent_id = _clean_text(info.get("parentID") or info.get("parentId")) or None
        return message_id, parent_id, _message_meta_from_info(info) or None

    parent_id = _clean_text(turn_state.get("request_message_id")) or None
    first_candidate = candidate_ids[0] if candidate_ids else None
    return first_candidate, parent_id, None


def _finalize_paused_session_abort(session_id: str) -> bool:
    sid = _session_id(session_id)
    prompt_instance = get_prompt_instance(sid)
    if prompt_instance is not None and bool(prompt_instance.running):
        return False

    from packages.agent import agent_service
    from packages.agent.runtime.acp_service import get_acp_registry_service
    from packages.agent.runtime.permission_next import list_pending, reply as reply_permission
    from packages.agent.session.session_processor import SessionProcessor

    pending_permissions = list_pending(sid)
    if not pending_permissions:
        return False

    assistant_message_id, parent_id, assistant_meta = _abort_pending_message_context(
        sid,
        pending_permissions,
    )
    aborted_callbacks = drain_prompt_callbacks(sid)
    finish_prompt_instance(sid)
    reject_prompt_callbacks(aborted_callbacks, "会话已中止")

    acp_registry = get_acp_registry_service()
    for item in pending_permissions:
        action_id = _clean_text(item.get("id"))
        if not action_id:
            continue
        try:
            pending_action = agent_service.get_pending_action(action_id)
        except Exception:
            logger.exception("Failed to load pending action during abort finalization: %s", action_id)
            pending_action = None
        if pending_action is not None and agent_service.session_pending.is_acp_pending_action(pending_action):
            try:
                acp_registry.discard_pending_permission(action_id)
            except Exception:
                logger.exception("Failed to discard pending ACP permission during abort: %s", action_id)
        try:
            reply_permission(action_id, "reject", "会话已中止")
        except Exception:
            logger.exception("Failed to reject pending permission during abort finalization: %s", action_id)
        try:
            agent_service._delete_pending_action(action_id)
        except Exception:
            logger.exception("Failed to delete pending action during abort finalization: %s", action_id)

    processor = SessionProcessor(
        session_id=sid,
        parent_id=parent_id,
        assistant_meta=copy.deepcopy(assistant_meta) if isinstance(assistant_meta, dict) else None,
        assistant_message_id=assistant_message_id,
    )
    processor.apply_error_message("会话已中止")
    set_session_status(sid, {"type": "idle"})
    clear_session_abort(sid)
    return True


def request_session_abort(session_id: str | None) -> None:
    sid = _session_id(session_id)
    _request_session_abort(sid)
    try:
        from packages.agent.session.permission_flow import finalize_paused_session_abort

        finalize_paused_session_abort(sid)
    except Exception:
        logger.exception("Failed to finalize paused session abort for %s", sid)


def set_session_status(session_id: str | None, status: dict[str, Any] | None) -> None:
    sid = _session_id(session_id)
    normalized = copy.deepcopy(status or {})
    _set_session_status(sid, normalized)
    event_type = SessionBusEvent.IDLE if str(normalized.get("type") or "idle") == "idle" else SessionBusEvent.STATUS
    session_bus.publish(
        event_type,
        {
            "sessionID": sid,
            "status": normalized if event_type == SessionBusEvent.STATUS else {"type": "idle"},
        },
    )


def get_session_status(session_id: str | None) -> dict[str, Any]:
    return _get_session_status(_session_id(session_id))


def _rewrite_file_from_diff(diff: dict[str, Any], *, before: bool) -> None:
    session_revert.rewrite_file_from_diff(diff, before=before)


def _patch_diffs_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    return session_revert.patch_diffs_from_message(message)


def _collect_revert_diffs(session_id: str, message_id: str) -> list[dict[str, Any]]:
    return session_revert.collect_revert_diffs(session_id, message_id)


def cleanup_reverted_session(session_id: str | None) -> None:
    session_revert.cleanup_reverted_session(session_id)


def _update_message_parts(
    *,
    session_id: str,
    message_id: str,
    parts: list[dict[str, Any]],
    meta: dict[str, Any] | object = ...,
    content: str | None = None,
) -> dict[str, Any]:
    message = session_store.update_message_parts(
        session_id=session_id,
        message_id=message_id,
        parts=parts,
        meta=meta,
        content=content,
    )
    _publish_message_updated(message)
    for part in message["parts"]:
        _publish_part_updated(session_id, part)
    return message


def _load_message_parts(session_id: str, message_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    history = list_session_messages(session_id, limit=5000)
    for message in history:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if str(info.get("id") or "") == message_id:
            return message, copy.deepcopy(message.get("parts") or [])
    return None, []


def _upsert_text_like_part(
    parts: list[dict[str, Any]],
    *,
    part_id: str,
    part_type: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    for part in parts:
        if str(part.get("id") or "") == part_id:
            return part, False
    now = _now_ms()
    created = {
        "id": part_id,
        "type": part_type,
        "text": "",
        "time": {"start": now, "end": now},
    }
    if isinstance(metadata, dict) and metadata:
        created["metadata"] = copy.deepcopy(metadata)
    parts.append(created)
    return created, True


def _upsert_tool_part(
    parts: list[dict[str, Any]],
    *,
    call_id: str,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    provider_executed: bool | None = None,
) -> tuple[dict[str, Any], bool]:
    for part in parts:
        if str(part.get("type") or "") == "tool" and str(part.get("callID") or "") == call_id:
            if tool_name:
                part["tool"] = tool_name
            if isinstance(metadata, dict) and metadata:
                part["metadata"] = copy.deepcopy(metadata)
            if provider_executed:
                part["providerExecuted"] = True
            part.setdefault("state", {})
            return part, False
    now = _now_ms()
    created = {
        "id": _part_id(None),
        "type": "tool",
        "tool": tool_name,
        "callID": call_id,
        "content": "",
        "state": {
            "status": "pending",
            "time": {"start": now, "end": now},
        },
    }
    if isinstance(metadata, dict) and metadata:
        created["metadata"] = copy.deepcopy(metadata)
    if provider_executed:
        created["providerExecuted"] = True
    parts.append(created)
    return created, True


def _parse_json_maybe(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _merge_tokens(existing: dict[str, Any] | None, usage: dict[str, Any]) -> dict[str, Any]:
    current = copy.deepcopy(existing or {})
    current["input"] = int(usage.get("input_tokens") or current.get("input") or 0)
    current["output"] = int(usage.get("output_tokens") or current.get("output") or 0)
    current["reasoning"] = int(usage.get("reasoning_tokens") or current.get("reasoning") or 0)
    current["total"] = current["input"] + current["output"] + current["reasoning"]
    current.setdefault("cache", {"read": 0, "write": 0})
    return current


def _parse_sse_event(raw: str) -> tuple[str, dict[str, Any]] | None:
    return sse_events.parse_sse_event(raw)


def _coerce_runtime_event(raw: Any) -> tuple[str, dict[str, Any]] | None:
    return sse_events.coerce_runtime_event(raw)


def _format_sse_event(event_name: str, data: dict[str, Any]) -> str:
    return sse_events.format_sse_event(event_name, data)


def delete_session_message(session_id: str | None, message_id: str) -> bool:
    sid = _session_id(session_id)
    deleted = False
    with _DB_WRITE_LOCK, session_scope() as session:
        deleted = bool(AgentSessionMessageRepository(session).delete_by_ids([message_id]))
        if deleted:
            AgentSessionRepository(session).touch(sid)
    if deleted:
        _publish_message_deleted(sid, message_id)
    return deleted


def delete_session_message_part(session_id: str | None, message_id: str, part_id: str) -> bool:
    sid = _session_id(session_id)
    message, parts = _load_message_parts(sid, message_id)
    if message is None:
        return False
    remaining = [part for part in parts if str(part.get("id") or "") != part_id]
    if len(remaining) == len(parts):
        return False
    normalized_meta = _message_meta_from_info(message.get("info") if isinstance(message.get("info"), dict) else {})
    _update_message_parts(
        session_id=sid,
        message_id=message_id,
        parts=remaining,
        meta=normalized_meta or None,
    )
    _publish_part_deleted(sid, part_id)
    return True


def fork_session(session_id: str | None, message_id: str | None) -> dict[str, Any]:
    sid = _session_id(session_id)
    session_record = get_session_record(sid)
    if session_record is None:
        raise ValueError("session not found")
    history = list_session_messages(sid, limit=5000)
    cutoff_id = _clean_text(message_id) or None
    kept: list[dict[str, Any]] = []
    for message in history:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if cutoff_id and str(info.get("id") or "") == cutoff_id:
            break
        kept.append(message)

    existing = list_sessions(directory=session_record.get("directory"), limit=500)
    base_title = str(session_record.get("title") or "Session").strip() or "Session"
    fork_index = 1 + sum(1 for item in existing if str(item.get("title") or "").startswith(f"{base_title} (fork #"))
    forked = ensure_session_record(
        f"{sid}_fork_{uuid4().hex[:8]}",
        directory=session_record.get("directory"),
        workspace_path=session_record.get("workspace_path"),
        workspace_server_id=session_record.get("workspace_server_id"),
        title=f"{base_title} (fork #{fork_index})",
        mode=session_record.get("mode"),
    )

    parent_map: dict[str, str] = {}
    for message in kept:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        original_id = str(info.get("id") or "")
        new_parent = parent_map.get(str(info.get("parentID") or ""))
        cloned_parts = copy.deepcopy(message.get("parts") or [])
        for part in cloned_parts:
            if isinstance(part, dict):
                part.pop("id", None)
                part.pop("sessionID", None)
                part.pop("messageID", None)
        created = append_session_message(
            session_id=forked["id"],
            role=str(info.get("role") or "user"),
            content=_aggregate_message_content(message.get("parts") or [], role=str(info.get("role") or "user")),
            parent_id=new_parent,
            meta=_message_meta_from_info(info) or None,
            parts=cloned_parts,
        )
        parent_map[original_id] = str((created.get("info") or {}).get("id") or "")
    return get_session_record(forked["id"]) or forked


def get_session_diff(session_id: str | None) -> list[dict[str, Any]]:
    return session_revert.get_session_diff(session_id)


def revert_session(session_id: str | None, message_id: str) -> dict[str, Any]:
    return session_revert.revert_session(session_id, message_id)


def unrevert_session(session_id: str | None) -> dict[str, Any]:
    return session_revert.unrevert_session(session_id)


class _StreamPersistenceState:
    def __init__(
        self,
        *,
        session_id: str,
        parent_id: str | None,
        assistant_meta: dict[str, Any] | None,
        assistant_message_id: str | None,
    ) -> None:
        self.session_id = session_id
        self.parent_id = _clean_text(parent_id) or None
        self.assistant_meta = copy.deepcopy(assistant_meta or {})
        self.current_message_id = _clean_text(assistant_message_id) or None
        self.current_parent_id = self.parent_id
        self.current_message_meta: dict[str, Any] = copy.deepcopy(self.assistant_meta)
        self.current_message_meta.setdefault("finish", None)
        self.pending_finish: str | None = None
        self.pending_error: dict[str, Any] | None = None
        self.pending_step_snapshot: str | None = None
        self.seen_action_confirm = False
        self.seen_done = False
        self.open_text_part_id: str | None = None
        self.open_reasoning_part_id: str | None = None

    def _open_part_attr(self, part_type: str) -> str:
        return "open_reasoning_part_id" if part_type == "reasoning" else "open_text_part_id"

    def get_open_part_id(self, part_type: str) -> str | None:
        return _clean_text(getattr(self, self._open_part_attr(part_type), None)) or None

    def set_open_part_id(self, part_type: str, part_id: str | None) -> None:
        setattr(self, self._open_part_attr(part_type), _clean_text(part_id) or None)

    def clear_stream_parts(self) -> None:
        self.open_text_part_id = None
        self.open_reasoning_part_id = None

    def reserve_stream_part_id(self, part_type: str, explicit_id: str | None = None) -> str:
        part_id = _clean_text(explicit_id)
        if not part_id:
            part_id = self.get_open_part_id(part_type) or _part_id(None)
        self.set_open_part_id(part_type, part_id)
        other_part_type = "reasoning" if part_type == "text" else "text"
        if self.get_open_part_id(other_part_type):
            self.set_open_part_id(other_part_type, None)
        return part_id

    def close_stream_part(self, part_type: str, explicit_id: str | None = None) -> str | None:
        part_id = _clean_text(explicit_id) or self.get_open_part_id(part_type)
        if not part_id:
            return None
        if not explicit_id or part_id == self.get_open_part_id(part_type):
            self.set_open_part_id(part_type, None)
        return part_id

    def ensure_message(self) -> str:
        message_id = self.current_message_id or f"message_{uuid4().hex}"
        existing, _ = _load_message_parts(self.session_id, message_id)
        if existing is None:
            append_session_message(
                session_id=self.session_id,
                role="assistant",
                content="",
                parent_id=self.current_parent_id,
                meta=self.current_message_meta,
                parts=[],
                message_id=message_id,
            )
        self.current_message_id = message_id
        return message_id

    def load_parts(self) -> list[dict[str, Any]]:
        message_id = self.ensure_message()
        _, parts = _load_message_parts(self.session_id, message_id)
        return parts

    def save_parts(self, parts: list[dict[str, Any]], *, meta: dict[str, Any] | object = ...) -> dict[str, Any]:
        message_id = self.ensure_message()
        content = _aggregate_message_content(parts, role="assistant")
        if meta is not ...:
            self.current_message_meta = copy.deepcopy(meta)
        return _update_message_parts(
            session_id=self.session_id,
            message_id=message_id,
            parts=parts,
            meta=self.current_message_meta if meta is not ... else ...,
            content=content,
        )

    def commit_current_message(self, *, finish: str | None = None, meta: dict[str, Any] | None = None) -> None:
        if self.current_message_id is None:
            return
        message, parts = _load_message_parts(self.session_id, self.current_message_id)
        if message is None:
            return
        merged_meta = copy.deepcopy(self.current_message_meta)
        if isinstance(meta, dict):
            merged_meta.update(copy.deepcopy(meta))
        if finish is not None:
            merged_meta["finish"] = finish
        self.current_message_meta = merged_meta
        self.save_parts(parts, meta=merged_meta)

    def reset_current_message(self) -> None:
        if not self.current_message_id:
            return
        message, _parts = _load_message_parts(self.session_id, self.current_message_id)
        if message is not None:
            with session_scope() as session:
                AgentSessionMessageRepository(session).delete_by_ids([self.current_message_id])
            _publish_message_deleted(self.session_id, self.current_message_id)
        self.current_message_id = None
        self.pending_finish = None
        self.pending_error = None
        self.pending_step_snapshot = None
        self.seen_action_confirm = False
        self.current_message_meta = copy.deepcopy(self.assistant_meta)
        self.current_message_meta.setdefault("finish", None)
        self.clear_stream_parts()

    def roll_to_message(self, message_id: str | None) -> None:
        self.current_message_id = _clean_text(message_id) or None
        self.pending_finish = None
        self.pending_error = None
        self.pending_step_snapshot = None
        self.seen_action_confirm = False
        self.current_message_meta = copy.deepcopy(self.assistant_meta)
        self.current_message_meta.setdefault("finish", None)
        self.clear_stream_parts()


class _LegacySessionStreamPersistenceShim:
    # Legacy adapter: native runtime now uses SessionProcessor directly.
    def __init__(  # type: ignore[override]
        self,
        *,
        session_id: str,
        parent_id: str | None = None,
        assistant_meta: dict[str, Any] | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        from packages.agent.session.session_processor import SessionProcessor

        self._processor = SessionProcessor(
            session_id=session_id,
            parent_id=parent_id,
            assistant_meta=assistant_meta,
            assistant_message_id=assistant_message_id,
        )
        self.session_id = self._processor.session_id
        self.state = self._processor.state

    def __getattr__(self, name: str) -> Any:
        return getattr(self._processor, name)

    def start(self) -> None:  # type: ignore[override]
        self._processor.start()

    def set_assistant_message_id(self, message_id: str | None) -> None:  # type: ignore[override]
        self._processor.set_assistant_message_id(message_id)

    def set_parent_message_id(self, message_id: str | None) -> None:  # type: ignore[override]
        self._processor.set_parent_message_id(message_id)

    def reset_assistant_message(self) -> None:  # type: ignore[override]
        self._processor.reset_assistant_message()

    def begin_stream_part(  # type: ignore[override]
        self,
        part_type: str,
        *,
        part_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._processor.begin_stream_part(part_type, part_id=part_id, metadata=metadata)

    def append_stream_part_delta(  # type: ignore[override]
        self,
        part_type: str,
        *,
        part_id: str | None = None,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._processor.append_stream_part_delta(
            part_type,
            part_id=part_id,
            content=content,
            metadata=metadata,
        )

    def end_stream_part(self, part_type: str, *, part_id: str | None = None) -> None:  # type: ignore[override]
        self._processor.end_stream_part(part_type, part_id=part_id)

    def begin_tool_input(  # type: ignore[override]
        self,
        *,
        call_id: str,
        tool_name: str | None = None,
        provider_executed: bool = False,
    ) -> None:
        self._processor.begin_tool_input(
            call_id=call_id,
            tool_name=tool_name,
            provider_executed=provider_executed,
        )

    def append_tool_input_delta(  # type: ignore[override]
        self,
        *,
        call_id: str,
        delta: str,
        provider_executed: bool = False,
    ) -> None:
        self._processor.append_tool_input_delta(
            call_id=call_id,
            delta=delta,
            provider_executed=provider_executed,
        )

    def end_tool_input(self, *, call_id: str, provider_executed: bool = False) -> None:  # type: ignore[override]
        self._processor.end_tool_input(call_id=call_id, provider_executed=provider_executed)

    def begin_tool_call(  # type: ignore[override]
        self,
        *,
        call_id: str,
        name: str,
        args: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_executed: bool = False,
    ) -> None:
        self._processor.begin_tool_call(
            call_id=call_id,
            name=name,
            args=args,
            metadata=metadata,
            provider_executed=provider_executed,
        )

    def finish_tool_call(  # type: ignore[override]
        self,
        *,
        call_id: str,
        name: str | None = None,
        success: bool,
        summary: Any = None,
        data: Any = None,
        metadata: dict[str, Any] | None = None,
        provider_executed: bool = False,
    ) -> None:
        self._processor.finish_tool_call(
            call_id=call_id,
            name=name,
            success=success,
            summary=summary,
            data=data,
            metadata=metadata,
            provider_executed=provider_executed,
        )

    def apply_usage(self, usage: dict[str, Any]) -> None:  # type: ignore[override]
        self._processor.apply_usage(usage)

    def record_retry(self, data: dict[str, Any]) -> None:  # type: ignore[override]
        self._processor.record_retry(data)

    def begin_step(self, data: dict[str, Any]) -> None:  # type: ignore[override]
        self._processor.begin_step(data)

    def finish_step(self, data: dict[str, Any]) -> None:  # type: ignore[override]
        self._processor.finish_step(data)

    def append_patch(self, data: dict[str, Any]) -> None:  # type: ignore[override]
        self._processor.append_patch(data)

    def commit_assistant(self, meta: dict[str, Any] | None = None) -> None:  # type: ignore[override]
        self._processor.commit_assistant(meta)

    def record_action_confirm(self, data: dict[str, Any]) -> None:  # type: ignore[override]
        self._processor.record_action_confirm(data)

    def apply_error_message(self, message: str) -> None:  # type: ignore[override]
        self._processor.apply_error_message(message)

    def finalize_done(self) -> list[tuple[str, dict[str, Any]]]:  # type: ignore[override]
        return self._processor.finalize_done()

    def apply_event(self, event_name: str, data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:  # type: ignore[override]
        return self._processor.apply_event(event_name, data)

    def consume(self, raw: Any) -> list[str]:  # type: ignore[override]
        return self._processor.consume(raw)

    def stream(  # type: ignore[override]
        self,
        raw_stream: Iterator[Any],
        *,
        manage_lifecycle: bool = True,
        control: Any | None = None,
    ) -> Iterator[str]:
        yield from self._processor.stream(
            raw_stream,
            manage_lifecycle=manage_lifecycle,
            control=control,
        )

    def finalize(self, *, manage_lifecycle: bool = True, handed_off: bool = False) -> None:  # type: ignore[override]
        self._processor.finalize(
            manage_lifecycle=manage_lifecycle,
            handed_off=handed_off,
        )


class SessionStreamPersistence:
    """Legacy SSE wrapper backed directly by SessionProcessor."""

    def __init__(
        self,
        *,
        session_id: str,
        parent_id: str | None = None,
        assistant_meta: dict[str, Any] | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        from packages.agent.session.session_processor import SessionProcessor

        self._processor = SessionProcessor(
            session_id=session_id,
            parent_id=parent_id,
            assistant_meta=assistant_meta,
            assistant_message_id=assistant_message_id,
        )
        self.session_id = self._processor.session_id
        self.state = self._processor.state

    def __getattr__(self, name: str) -> Any:
        return getattr(self._processor, name)

    def start(self) -> None:
        self._processor.start()

    def consume(self, raw: Any) -> list[str]:
        return self._processor.consume(raw)

    def apply_event(self, event_name: str, data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return self._processor.apply_event(event_name, data)

    def stream(
        self,
        raw_stream: Iterator[Any],
        *,
        manage_lifecycle: bool = True,
        control: Any | None = None,
    ) -> Iterator[str]:
        yield from self._processor.stream(
            raw_stream,
            manage_lifecycle=manage_lifecycle,
            control=control,
        )

    def finalize(self, *, manage_lifecycle: bool = True, handed_off: bool = False) -> None:
        self._processor.finalize(
            manage_lifecycle=manage_lifecycle,
            handed_off=handed_off,
        )


def wrap_stream_with_persistence(
    raw_stream: Iterator[str],
    *,
    session_id: str,
    parent_id: str | None = None,
    assistant_meta: dict[str, Any] | None = None,
    assistant_message_id: str | None = None,
) -> Iterator[str]:
    prompt_control = getattr(raw_stream, "_researchos_prompt_control", None)
    processor_managed_lifecycle = prompt_control is not None
    persistence = SessionStreamPersistence(
        session_id=session_id,
        parent_id=parent_id,
        assistant_meta=assistant_meta,
        assistant_message_id=assistant_message_id,
    )
    yield from persistence.stream(
        raw_stream,
        manage_lifecycle=not processor_managed_lifecycle,
        control=prompt_control,
    )

