"""Session pending-action persistence and resume helpers."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from packages.agent.runtime.permission_next import (
    delete_pending_action_state,
    load_pending_action_state,
    persist_pending_action_state,
    pop_pending_action_state,
)


@dataclass
class PendingAction:
    action_id: str
    options: Any
    permission_request: dict[str, Any] | None = None
    continuation: dict[str, Any] | None = None
    kind: str = ""

    def __post_init__(self) -> None:
        self.kind = _infer_pending_action_kind(
            self.permission_request,
            self.continuation,
            self.kind,
        )


@dataclass(frozen=True)
class PendingResumeState:
    request_message_id: str | None = None
    assistant_message_id: str | None = None
    step_index: int = 0
    step_snapshot: str | None = None
    step_usage: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "request_message_id": self.request_message_id,
            "assistant_message_id": self.assistant_message_id,
            "step_index": int(self.step_index or 0),
            "step_snapshot": self.step_snapshot,
            "step_usage": copy.deepcopy(self.step_usage) if isinstance(self.step_usage, dict) else self.step_usage,
        }


def _normalize_pending_action_kind(kind: Any) -> str:
    return str(kind or "").strip().lower()


def _infer_pending_action_kind(
    permission_request: dict[str, Any] | None,
    continuation: dict[str, Any] | None,
    explicit_kind: Any = None,
) -> str:
    normalized = _normalize_pending_action_kind(explicit_kind)
    if normalized:
        return normalized
    continuation_kind = (
        _normalize_pending_action_kind(continuation.get("kind"))
        if isinstance(continuation, dict)
        else ""
    )
    if continuation_kind:
        return continuation_kind
    if isinstance(permission_request, dict) and permission_request:
        return "native_prompt"
    return "confirm"


def pending_action_kind(state: PendingAction | None) -> str:
    if not isinstance(state, PendingAction):
        return ""
    return _infer_pending_action_kind(
        state.permission_request,
        state.continuation,
        state.kind,
    )


def is_acp_pending_action(state: PendingAction | None) -> bool:
    return pending_action_kind(state) == "acp_prompt"


def is_native_pending_action(state: PendingAction | None) -> bool:
    return pending_action_kind(state) == "native_prompt"


def pending_action_from_state(
    payload: dict[str, Any] | None,
    *,
    hydrate_options,
    options_cls: type[Any] | None = None,
) -> PendingAction | None:
    if not isinstance(payload, dict):
        return None
    session_id = str(payload.get("session_id") or "").strip()
    options_data = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    from_payload = getattr(options_cls, "from_payload", None) if options_cls is not None else None
    hydrated_options = (
        from_payload(
            {
                **options_data,
                "session_id": str(options_data.get("session_id") or session_id),
            }
        )
        if callable(from_payload)
        else None
    )
    if hydrated_options is None:
        if not session_id:
            return None
        hydrated_options = hydrate_options(session_id)
    return PendingAction(
        action_id=str(payload.get("id") or "").strip(),
        options=hydrated_options,
        permission_request=(
            copy.deepcopy(payload.get("permission_request"))
            if isinstance(payload.get("permission_request"), dict)
            else None
        ),
        continuation=(
            copy.deepcopy(payload.get("continuation"))
            if isinstance(payload.get("continuation"), dict)
            else None
        ),
        kind=_infer_pending_action_kind(
            payload.get("permission_request")
            if isinstance(payload.get("permission_request"), dict)
            else None,
            payload.get("continuation")
            if isinstance(payload.get("continuation"), dict)
            else None,
            payload.get("kind"),
        ),
    )


def store_pending_action(
    state: PendingAction,
    *,
    get_session_record,
) -> None:
    session_record = get_session_record(state.options.session_id) or {}
    project_id = str(
        (state.permission_request or {}).get("project_id")
        or session_record.get("projectID")
        or "global"
    )
    continuation_payload = (
        copy.deepcopy(state.continuation)
        if isinstance(state.continuation, dict)
        else None
    )
    if continuation_payload is None and pending_action_kind(state) not in {"", "confirm", "native_prompt"}:
        continuation_payload = {"kind": pending_action_kind(state)}
    persist_pending_action_state(
        action_id=state.action_id,
        session_id=state.options.session_id,
        project_id=project_id,
        action_type="permission" if state.permission_request else "confirm",
        permission_request=copy.deepcopy(state.permission_request) if state.permission_request else None,
        options_payload=state.options.to_payload(),
        continuation=continuation_payload,
    )


def hydrate_pending_action(
    action_id: str,
    *,
    hydrate_options,
    options_cls: type[Any] | None = None,
) -> PendingAction | None:
    payload = load_pending_action_state(action_id)
    return (
        pending_action_from_state(payload, hydrate_options=hydrate_options, options_cls=options_cls)
        if payload is not None
        else None
    )


def pop_pending_action(
    action_id: str,
    *,
    hydrate_options,
    options_cls: type[Any] | None = None,
) -> PendingAction | None:
    payload = pop_pending_action_state(action_id)
    return (
        pending_action_from_state(payload, hydrate_options=hydrate_options, options_cls=options_cls)
        if payload is not None
        else None
    )


def delete_pending_action(action_id: str) -> None:
    delete_pending_action_state(action_id)


def get_pending_action(
    action_id: str,
    *,
    hydrate_options,
    options_cls: type[Any] | None = None,
) -> PendingAction | None:
    return hydrate_pending_action(action_id, hydrate_options=hydrate_options, options_cls=options_cls)


def pending_permission_tool(pending: PendingAction) -> dict[str, Any]:
    request = pending.permission_request if isinstance(pending.permission_request, dict) else {}
    return dict(request.get("tool") or {}) if isinstance(request.get("tool"), dict) else {}


def native_pending_messages(
    pending: PendingAction,
    *,
    load_agent_messages,
    normalize_messages,
) -> list[dict[str, Any]]:
    session_id = str(pending.options.session_id or "").strip()
    if session_id:
        persisted = load_agent_messages(session_id)
        if persisted:
            return normalize_messages(persisted, pending.options)
    return []


def tokens_to_usage_payload(tokens: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(tokens, dict):
        return None
    input_tokens = int(tokens.get("input") or 0)
    output_tokens = int(tokens.get("output") or 0)
    reasoning_tokens = int(tokens.get("reasoning") or 0)
    if input_tokens <= 0 and output_tokens <= 0 and reasoning_tokens <= 0:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def native_pending_assistant_message(
    pending: PendingAction,
    *,
    get_session_message_by_id,
) -> tuple[str | None, dict[str, Any]]:
    session_id = str(pending.options.session_id or "").strip()
    tool = pending_permission_tool(pending)
    assistant_message_id = str(tool.get("messageID") or "").strip()
    if session_id and assistant_message_id:
        message = get_session_message_by_id(session_id, assistant_message_id) or {}
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        resolved_message_id = str(info.get("id") or assistant_message_id).strip() or None
        return resolved_message_id, message
    return assistant_message_id or None, {}


def native_pending_context(
    pending: PendingAction,
    *,
    get_session_message_by_id,
    get_latest_user_message_id=None,  # noqa: ANN001
) -> dict[str, Any]:
    return native_pending_resume_state(
        pending,
        get_session_message_by_id=get_session_message_by_id,
        get_latest_user_message_id=get_latest_user_message_id,
    ).to_payload()


def native_pending_resume_state(
    pending: PendingAction,
    *,
    get_session_message_by_id,
    get_latest_user_message_id=None,  # noqa: ANN001
) -> PendingResumeState:
    assistant_message_id, message = native_pending_assistant_message(
        pending,
        get_session_message_by_id=get_session_message_by_id,
    )
    session_id = str(pending.options.session_id or "").strip()
    request_message_id = None
    step_index = 0
    step_snapshot = None
    step_usage = None
    if isinstance(message, dict) and message:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        request_message_id = (
            str(info.get("parentID") or info.get("parentId") or "").strip() or None
        )
        if step_usage is None:
            step_usage = tokens_to_usage_payload(
                info.get("tokens") if isinstance(info.get("tokens"), dict) else None
            )
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        for part in parts:
            if not isinstance(part, dict) or str(part.get("type") or "") != "step-start":
                continue
            part_step = int(part.get("step") or 0)
            if part_step > 0:
                step_index = part_step - 1
            snapshot = str(part.get("snapshot") or "").strip()
            if snapshot:
                step_snapshot = snapshot
    if request_message_id is None and callable(get_latest_user_message_id):
        request_message_id = str(get_latest_user_message_id(session_id) or "").strip() or None
    return PendingResumeState(
        request_message_id=request_message_id,
        assistant_message_id=assistant_message_id,
        step_index=step_index,
        step_snapshot=step_snapshot,
        step_usage=step_usage,
    )


def native_pending_tool_calls(
    pending: PendingAction,
    *,
    get_session_message_by_id,
    parse_tool_call,
    fill_workspace_defaults,
    make_tool_call,
) -> list[Any]:
    session_id = str(pending.options.session_id or "").strip()
    assistant_message_id, message = native_pending_assistant_message(
        pending,
        get_session_message_by_id=get_session_message_by_id,
    )
    if session_id and assistant_message_id:
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        persisted_calls: list[Any] = []
        for part in parts:
            if not isinstance(part, dict) or str(part.get("type") or "") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            status = str(state.get("status") or "").strip().lower()
            if status in {"completed", "error"}:
                continue
            raw_arguments = ""
            if isinstance(state.get("raw"), str):
                raw_arguments = str(state.get("raw") or "")
            elif isinstance(state.get("input"), dict):
                raw_arguments = json.dumps(state.get("input") or {}, ensure_ascii=False)
            call = parse_tool_call(
                str(part.get("callID") or ""),
                str(part.get("tool") or ""),
                raw_arguments,
                metadata=part.get("metadata") if isinstance(part.get("metadata"), dict) else None,
                provider_executed=bool(part.get("providerExecuted")),
            )
            persisted_calls.append(fill_workspace_defaults(call, pending.options))
        if persisted_calls:
            return persisted_calls
    request = pending.permission_request if isinstance(pending.permission_request, dict) else {}
    metadata = dict(request.get("metadata") or {}) if isinstance(request.get("metadata"), dict) else {}
    tool = pending_permission_tool(pending)
    tool_name = str(metadata.get("tool") or "").strip()
    if not tool_name:
        return []
    arguments = dict(metadata.get("arguments") or {}) if isinstance(metadata.get("arguments"), dict) else {}
    return [
        fill_workspace_defaults(
            make_tool_call(
                id=str(tool.get("callID") or "").strip() or f"call_{uuid4().hex[:10]}",
                name=tool_name,
                arguments=arguments,
            ),
            pending.options,
        )
    ]


def native_pending_persistence(
    pending: PendingAction,
    *,
    get_session_record,
    get_latest_user_message_id,
    get_session_message_by_id,
    assistant_meta_factory,
    persistence_cls,
) -> Any:
    session_id = str(pending.options.session_id or "").strip()
    session_record = get_session_record(session_id) or {}
    pending_resume = native_pending_resume_state(
        pending,
        get_session_message_by_id=get_session_message_by_id,
        get_latest_user_message_id=get_latest_user_message_id,
    )
    return persistence_cls(
        session_id=session_id,
        parent_id=str(pending_resume.request_message_id or "").strip() or None,
        assistant_meta=assistant_meta_factory(
            pending.options,
            session_record=session_record,
        ),
        assistant_message_id=str(pending_resume.assistant_message_id or "").strip() or None,
    )

