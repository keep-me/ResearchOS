"""Session compaction helpers used by the native prompt loop."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from packages.agent.runtime.agent_runtime_policy import (
    get_auto_compaction_input_tokens_threshold as _shared_get_auto_compaction_input_tokens_threshold,
)
from packages.agent.session.session_runtime import (
    append_session_message,
    build_user_message_meta,
    get_session_record,
    list_session_messages,
    load_agent_messages,
    persist_assistant_message,
)
from packages.integrations.llm_client import LLMClient

_SUMMARY_PROMPT = "Provide a detailed prompt for continuing our conversation above."
_CONTINUE_PROMPT = "Continue if you have next steps"


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _message_role(message: dict[str, Any]) -> str:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    return str(info.get("role") or "").strip()


def _message_id(message: dict[str, Any]) -> str | None:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    value = str(info.get("id") or "").strip()
    return value or None


def _latest_user_info(history: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(history):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if str(info.get("role") or "").strip() == "user":
            return copy.deepcopy(info)
    return {}


def _message_tokens(message: dict[str, Any]) -> int:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
    total = int(tokens.get("total") or 0)
    if total > 0:
        return total
    return (
        int(tokens.get("input") or 0)
        + int(tokens.get("output") or 0)
        + int(tokens.get("reasoning") or 0)
    )


def _message_text(parts: list[dict[str, Any]]) -> str:
    return "".join(
        str(part.get("text") or "") for part in parts if str(part.get("type") or "") == "text"
    )


def _clone_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cloned: list[dict[str, Any]] = []
    for part in copy.deepcopy(parts or []):
        if not isinstance(part, dict):
            continue
        part.pop("id", None)
        part.pop("sessionID", None)
        part.pop("messageID", None)
        cloned.append(part)
    return cloned


def _normalize_usage_tokens(usage: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(usage or {})
    input_tokens = int(payload.get("input_tokens") or 0)
    output_tokens = int(payload.get("output_tokens") or 0)
    reasoning_tokens = int(payload.get("reasoning_tokens") or 0)
    return {
        "total": input_tokens + output_tokens + reasoning_tokens,
        "input": input_tokens,
        "output": output_tokens,
        "reasoning": reasoning_tokens,
        "cache": {"read": 0, "write": 0},
    }


def _context_limit(provider_id: str | None, model_id: str | None) -> int:
    del provider_id, model_id
    return _shared_get_auto_compaction_input_tokens_threshold()


def _build_summary_messages(
    session_id: str,
    *,
    auto: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]] | None, str | None]:
    history = list_session_messages(session_id, limit=5000)
    prompt_messages = load_agent_messages(session_id)
    replay_parts: list[dict[str, Any]] | None = None
    replay_text: str | None = None
    latest_message = history[-1] if history else None

    if auto and latest_message is not None:
        latest_role = _message_role(latest_message)
        if latest_role == "user":
            if prompt_messages and str(prompt_messages[-1].get("role") or "") == "user":
                prompt_messages = prompt_messages[:-1]
            replay_parts = _clone_parts(latest_message.get("parts") or [])
            replay_text = _message_text(replay_parts)
        elif latest_role == "assistant":
            replay_parts = [{"type": "text", "text": _CONTINUE_PROMPT}]
            replay_text = _CONTINUE_PROMPT

    prompt_messages = [copy.deepcopy(item) for item in prompt_messages]
    prompt_messages.append({"role": "user", "content": _SUMMARY_PROMPT})
    return prompt_messages, latest_message, replay_parts, replay_text


def _generate_summary(
    session_id: str,
    *,
    provider_id: str,
    model_id: str,
    auto: bool,
) -> tuple[str, dict[str, Any], list[dict[str, Any]] | None, str | None]:
    messages, latest_message, replay_parts, replay_text = _build_summary_messages(
        session_id, auto=auto
    )
    del latest_message

    llm = LLMClient()
    if str(getattr(llm, "provider", "") or "").strip() in {"", "none"}:
        raise RuntimeError("当前未配置可用的研究助手模型，无法执行会话压缩。")

    text_chunks: list[str] = []
    usage: dict[str, Any] = {}
    for event in llm.chat_stream(
        messages,
        tools=None,
        max_tokens=4096,
        variant_override="low",
        model_override=model_id,
        session_cache_key=f"{session_id}:compaction",
    ):
        event_type = str(getattr(event, "type", "") or "").strip()
        if event_type == "text_delta" and getattr(event, "content", None):
            text_chunks.append(str(event.content))
        elif event_type == "usage":
            usage = {
                "model": getattr(event, "model", None) or model_id,
                "input_tokens": int(getattr(event, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(event, "output_tokens", 0) or 0),
                "reasoning_tokens": int(getattr(event, "reasoning_tokens", 0) or 0),
                "providerID": provider_id,
            }
        elif event_type == "error":
            raise RuntimeError(str(getattr(event, "content", "") or "会话压缩失败"))

    summary_text = "".join(text_chunks).strip()
    if not summary_text:
        raise RuntimeError("会话压缩失败：摘要模型未返回内容")
    if "model" not in usage:
        usage["model"] = model_id
    usage["providerID"] = provider_id
    return summary_text, usage, replay_parts, replay_text


def detect_overflow_error(message: str | None) -> bool:
    lowered = str(message or "").lower()
    markers = (
        "context length",
        "context window",
        "too many tokens",
        "maximum context",
        "max context",
        "maximum context length exceeded",
    )
    return any(marker in lowered for marker in markers)


def is_overflow_tokens(tokens: int | None, provider_id: str | None, model_id: str | None) -> bool:
    if isinstance(tokens, dict):
        payload = dict(tokens)
        value = int(
            payload.get("total")
            or int(payload.get("input") or 0)
            + int(payload.get("output") or 0)
            + int(payload.get("reasoning") or 0)
        )
    else:
        value = int(tokens or 0)
    if value <= 0:
        return False
    return value >= _context_limit(provider_id, model_id)


def latest_auto_compaction_target(session_id: str | None) -> dict | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    history = list_session_messages(sid, limit=5000)
    if not history or _message_role(history[-1]) != "user":
        return None
    for message in reversed(history[:-1]):
        if _message_role(message) != "assistant":
            continue
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if info.get("summary"):
            continue
        total_tokens = _message_tokens(message)
        provider_id = str(info.get("providerID") or "openai").strip() or "openai"
        model_id = str(info.get("modelID") or info.get("model") or "").strip() or "default"
        if is_overflow_tokens(total_tokens, provider_id, model_id):
            return {
                "providerID": provider_id,
                "modelID": model_id,
                "overflow": False,
            }
        break
    return None


def summarize_session(
    session_id: str,
    *,
    provider_id: str,
    model_id: str,
    auto: bool,
    overflow: bool,
) -> dict:
    session_record = get_session_record(session_id)
    if session_record is None:
        raise RuntimeError("session not found")

    summary_text, usage, replay_parts, replay_text = _generate_summary(
        session_id,
        provider_id=provider_id,
        model_id=model_id,
        auto=auto,
    )

    latest_history = list_session_messages(session_id, limit=5000)
    latest_user_info = _latest_user_info(latest_history)
    now = _now_ms()
    compaction_message = append_session_message(
        session_id=session_id,
        role="user",
        content="",
        meta=build_user_message_meta(
            agent=str(latest_user_info.get("agent") or "").strip() or None,
            model=latest_user_info.get("model")
            if isinstance(latest_user_info.get("model"), dict)
            else None,
            format=latest_user_info.get("format"),
            tools=latest_user_info.get("tools")
            if isinstance(latest_user_info.get("tools"), dict)
            else None,
            system=str(latest_user_info.get("system") or "").strip() or None,
            variant=str(latest_user_info.get("variant") or "").strip() or None,
            fallback_agent=str(session_record.get("mode") or "build"),
        )
        or None,
        parts=[
            {
                "type": "compaction",
                "auto": bool(auto),
                "overflow": bool(overflow),
                "time": {"start": now, "end": now},
            }
        ],
    )

    summary_message = persist_assistant_message(
        session_id=session_id,
        parent_id=_message_id(compaction_message),
        meta={
            "providerID": provider_id,
            "modelID": str(usage.get("model") or model_id),
            "mode": "compaction",
            "agent": "compaction",
            "cwd": session_record.get("workspace_path") or session_record.get("directory"),
            "root": session_record.get("directory"),
            "finish": "stop",
            "summary": True,
            "variant": str(latest_user_info.get("variant") or "").strip() or None,
            "tokens": _normalize_usage_tokens(usage),
            "cost": 0.0,
        },
        parts=[{"type": "text", "text": summary_text}],
    )

    replay_message = None
    if replay_parts and replay_text is not None:
        replay_message = append_session_message(
            session_id=session_id,
            role="user",
            content=replay_text,
            meta=build_user_message_meta(
                agent=str(latest_user_info.get("agent") or "").strip() or None,
                model=latest_user_info.get("model")
                if isinstance(latest_user_info.get("model"), dict)
                else None,
                format=latest_user_info.get("format"),
                tools=latest_user_info.get("tools")
                if isinstance(latest_user_info.get("tools"), dict)
                else None,
                system=str(latest_user_info.get("system") or "").strip() or None,
                variant=str(latest_user_info.get("variant") or "").strip() or None,
                fallback_agent=str(session_record.get("mode") or "build"),
            )
            or None,
            parts=replay_parts,
        )

    return {
        "compaction": compaction_message,
        "summary": summary_message,
        "replay": replay_message,
    }
