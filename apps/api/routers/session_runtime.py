"""Session runtime routes for the native assistant backend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from packages.agent.runtime.agent_service import (
    StreamPersistenceConfig,
    respond_action,
    stream_chat,
)
from packages.agent.runtime.permission_next import get_pending, list_pending
from packages.agent.session.session_compaction import summarize_session
from packages.agent.session.session_instance import current_project_info
from packages.agent.session.session_lifecycle import get_prompt_instance, list_session_statuses
from packages.agent.session.session_runtime import (
    append_session_message,
    build_user_message_meta,
    cleanup_reverted_session,
    delete_session,
    delete_session_message,
    delete_session_message_part,
    ensure_session_record,
    fork_session,
    get_latest_user_message_id,
    get_session_diff,
    get_session_record,
    list_session_messages,
    list_sessions,
    request_session_abort,
    resolve_default_model_identity,
    revert_session,
    unrevert_session,
)
from packages.domain.assistant_schemas import (
    AssistantSessionDiffEntry,
    AssistantSessionForkRequest,
    AssistantSessionInfo,
    AssistantSessionRevertRequest,
    AssistantSessionStateResponse,
    session_diff_entries_from_value,
    session_info_from_record,
    session_state_from_values,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}


def _aborted_permission_stream():
    yield 'event: error\ndata: {"message":"会话已中止"}\n\n'
    yield "event: done\ndata: {}\n\n"


def _session_ended_with_abort(session_id: str) -> bool:
    history = list_session_messages(session_id, limit=20)
    for message in reversed(history):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if str(info.get("role") or "") != "assistant":
            continue
        error = info.get("error") if isinstance(info.get("error"), dict) else {}
        return (
            str(info.get("finish") or "").strip() == "aborted"
            and str(error.get("message") or "").strip() == "会话已中止"
        )
    return False


class SessionCreateRequest(BaseModel):
    id: str | None = None
    directory: str | None = None
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    title: str | None = None
    mode: str = "build"
    agent_backend_id: str | None = None


class SessionPromptRequest(BaseModel):
    parts: list[dict[str, Any]] = Field(default_factory=list)
    display_text: str | None = None
    mode: str = "build"
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    agent_backend_id: str | None = None
    agent: str | None = None
    model: dict[str, Any] | None = None
    noReply: bool = False
    tools: dict[str, bool] | None = None
    system: str | None = None
    format: dict[str, Any] | None = None
    variant: str | None = None
    reasoning_level: str | None = None
    active_skill_ids: list[str] = Field(default_factory=list)
    mounted_paper_ids: list[str] = Field(default_factory=list)
    mounted_primary_paper_id: str | None = None


class PermissionReplyRequest(BaseModel):
    response: str = Field(min_length=1)
    message: str | None = None
    answers: list[list[str]] | None = None


class SessionSummarizeRequest(BaseModel):
    providerID: str
    modelID: str


def _close_stream(raw_stream: Iterator[str]) -> None:
    close = getattr(raw_stream, "close", None)
    if callable(close):
        close()


def _consume_stream(raw_stream: Iterator[str], *, session_id: str, operation: str) -> None:
    try:
        for _ in raw_stream:
            pass
    except Exception:
        logger.exception("Detached session stream failed: %s (%s)", session_id, operation)
    finally:
        _close_stream(raw_stream)


def _schedule_detached_stream(
    raw_stream: Iterator[str], *, session_id: str, operation: str
) -> None:
    asyncio.create_task(
        asyncio.to_thread(
            _consume_stream,
            raw_stream,
            session_id=session_id,
            operation=operation,
        )
    )


def _assistant_meta_from_session(session_record: dict) -> dict:
    mode = str(session_record.get("mode") or "build")
    return {
        **resolve_default_model_identity(None),
        "mode": mode,
        "agent": mode,
        "cwd": session_record.get("workspace_path") or session_record.get("directory"),
        "root": session_record.get("directory"),
        "variant": None,
        "tokens": {
            "total": None,
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        },
        "cost": 0.0,
    }


def _assert_session_idle(session_id: str) -> None:
    if get_prompt_instance(session_id) is not None:
        raise HTTPException(status_code=400, detail="session is busy")


def _message_text(parts: list[dict[str, Any]]) -> str:
    return "".join(
        str(part.get("text") or "") for part in parts if str(part.get("type") or "") == "text"
    )


def _resolve_workspace_binding(
    *,
    session_record: dict[str, Any] | None,
    workspace_path: str | None = None,
    directory: str | None = None,
) -> tuple[str, str]:
    requested_workspace = str(workspace_path or "").strip()
    requested_directory = str(directory or "").strip()
    record_workspace = str((session_record or {}).get("workspace_path") or "").strip()
    record_directory = str((session_record or {}).get("directory") or "").strip()
    resolved_workspace = (
        requested_workspace or requested_directory or record_workspace or record_directory
    )
    resolved_directory = (
        requested_directory or requested_workspace or record_directory or record_workspace
    )
    if not resolved_workspace or not resolved_directory:
        raise HTTPException(status_code=400, detail="当前未绑定工作区，请先导入或选择目录")
    return resolved_directory, resolved_workspace


def _prepare_prompt_stream(
    session_id: str,
    body: SessionPromptRequest,
) -> tuple[dict, Iterator[str], dict]:
    session_payload = get_session_record(session_id)
    directory, workspace_path = _resolve_workspace_binding(
        session_record=session_payload,
        workspace_path=body.workspace_path,
    )
    if session_payload is None:
        session_payload = ensure_session_record(
            session_id,
            directory=directory,
            workspace_path=workspace_path,
            workspace_server_id=body.workspace_server_id,
            mode=body.mode,
            agent_backend_id=body.agent_backend_id,
        )
    elif (
        (workspace_path != str(session_payload.get("workspace_path") or "").strip())
        or (
            body.workspace_server_id
            and body.workspace_server_id != session_payload.get("workspace_server_id")
        )
        or (body.mode and body.mode != session_payload.get("mode"))
        or (
            body.agent_backend_id
            and body.agent_backend_id != session_payload.get("agent_backend_id")
        )
    ):
        session_payload = ensure_session_record(
            session_id,
            directory=directory,
            workspace_path=workspace_path,
            workspace_server_id=body.workspace_server_id
            or session_payload.get("workspace_server_id"),
            mode=body.mode or session_payload.get("mode"),
            agent_backend_id=body.agent_backend_id or session_payload.get("agent_backend_id"),
        )
    if session_payload.get("revert"):
        cleanup_reverted_session(session_payload["id"])
        session_payload = get_session_record(session_payload["id"]) or session_payload

    reasoning_level = str(body.reasoning_level or body.variant or "default")
    explicit_variant = str(body.reasoning_level or body.variant or "").strip() or None

    user_message = append_session_message(
        session_id=session_payload["id"],
        role="user",
        content=_message_text(body.parts),
        meta=build_user_message_meta(
            agent=body.agent or body.mode,
            model=body.model,
            format=body.format,
            tools=body.tools,
            system=body.system,
            variant=explicit_variant,
            active_skill_ids=body.active_skill_ids if body.active_skill_ids else None,
            mounted_paper_ids=body.mounted_paper_ids if body.mounted_paper_ids else None,
            mounted_primary_paper_id=body.mounted_primary_paper_id,
            reasoning_level=reasoning_level,
            display_text=body.display_text,
            fallback_agent=body.mode,
        )
        or None,
        parts=body.parts,
    )
    if body.noReply:
        return session_payload, iter(()), user_message

    persistence = StreamPersistenceConfig(
        session_id=session_payload["id"],
        parent_id=str((user_message.get("info") or {}).get("id") or ""),
        assistant_meta={
            **resolve_default_model_identity(reasoning_level),
            "mode": body.mode,
            "agent": body.mode,
            "cwd": workspace_path,
            "root": session_payload["directory"],
            "variant": reasoning_level,
            "tokens": {
                "total": None,
                "input": 0,
                "output": 0,
                "reasoning": 0,
                "cache": {"read": 0, "write": 0},
            },
            "cost": 0.0,
        },
    )
    raw_stream = stream_chat(
        [],
        session_id=session_payload["id"],
        agent_backend_id=body.agent_backend_id,
        mode=body.mode,
        workspace_path=workspace_path,
        workspace_server_id=body.workspace_server_id or session_payload.get("workspace_server_id"),
        reasoning_level=reasoning_level,
        active_skill_ids=body.active_skill_ids,
        mounted_paper_ids=body.mounted_paper_ids,
        mounted_primary_paper_id=body.mounted_primary_paper_id,
        persistence=persistence,
    )
    return session_payload, raw_stream, user_message


def _prepare_permission_stream(
    session_id: str,
    permission_id: str,
    body: PermissionReplyRequest,
) -> tuple[dict, Iterator[str], bool]:
    session_record = get_session_record(session_id)
    if session_record is None:
        raise HTTPException(status_code=404, detail="session not found")
    pending = get_pending(permission_id)
    if pending is None or pending.session_id != session_id:
        if _session_ended_with_abort(session_id):
            return session_record, _aborted_permission_stream(), False
        raise HTTPException(status_code=404, detail="permission not found")
    assistant_message_id = (
        str(((pending.tool or {}) if pending is not None else {}).get("messageID") or "").strip()
        or None
    )
    persistence = StreamPersistenceConfig(
        session_id=session_id,
        parent_id=get_latest_user_message_id(session_id),
        assistant_meta=_assistant_meta_from_session(session_record),
        assistant_message_id=assistant_message_id,
    )
    raw_stream = respond_action(
        permission_id,
        body.response,
        body.message,
        body.answers,
        persistence=persistence,
    )
    return session_record, raw_stream, True


@router.get("/project/current")
def get_current_project(directory: str = Query(default_factory=lambda: str(Path.cwd()))) -> dict:
    return current_project_info(directory)


@router.post("/session")
def create_session(body: SessionCreateRequest) -> AssistantSessionInfo:
    existing = get_session_record(body.id) if body.id else None
    directory, workspace_path = _resolve_workspace_binding(
        session_record=existing,
        workspace_path=body.workspace_path,
        directory=body.directory,
    )
    payload = ensure_session_record(
        body.id or f"session_{uuid4().hex[:12]}",
        directory=directory,
        workspace_path=workspace_path,
        workspace_server_id=body.workspace_server_id,
        title=body.title,
        mode=body.mode,
        agent_backend_id=body.agent_backend_id,
    )
    return session_info_from_record(payload)


@router.get("/session")
def list_session_route(
    directory: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[AssistantSessionInfo]:
    return [
        session_info_from_record(item)
        for item in list_sessions(directory=directory, limit=limit, archived=False)
    ]


@router.get("/session/status")
def get_session_status_map(directory: str | None = Query(default=None)) -> dict[str, dict]:
    statuses = list_session_statuses()
    if not directory:
        return statuses
    normalized = str(directory or "").strip()
    if not normalized:
        return statuses
    filtered: dict[str, dict] = {}
    for session_id, status in statuses.items():
        record = get_session_record(session_id) or {}
        session_directory = str(
            record.get("directory") or record.get("workspace_path") or ""
        ).strip()
        if session_directory == normalized:
            filtered[session_id] = status
    return filtered


@router.get("/session/{session_id}")
def get_session(session_id: str) -> AssistantSessionInfo:
    payload = get_session_record(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session_info_from_record(payload)


@router.get("/session/{session_id}/state")
def get_session_state(session_id: str) -> AssistantSessionStateResponse:
    payload = get_session_record(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session_state_from_values(
        payload,
        messages=list_session_messages(session_id, limit=2000),
        permissions=list_pending(session_id),
        status=list_session_statuses().get(session_id, {"type": "idle"}),
    )


@router.delete("/session/{session_id}")
def delete_session_route(session_id: str) -> bool:
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return True


@router.get("/session/{session_id}/message")
def get_session_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
) -> list[dict]:
    return list_session_messages(session_id, limit=limit)


@router.post("/session/{session_id}/message")
def prompt_session(session_id: str, body: SessionPromptRequest):
    session_payload, raw_stream, user_message = _prepare_prompt_stream(session_id, body)
    if body.noReply:
        return user_message
    return StreamingResponse(
        raw_stream,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/session/{session_id}/message/detached")
async def prompt_session_detached(session_id: str, body: SessionPromptRequest) -> dict:
    if body.noReply:
        raise HTTPException(status_code=400, detail="detached prompt does not support noReply")
    session_payload, raw_stream, _ = _prepare_prompt_stream(session_id, body)
    _schedule_detached_stream(raw_stream, session_id=session_payload["id"], operation="prompt")
    return {
        "accepted": True,
        "session_id": session_payload["id"],
    }


@router.delete("/session/{session_id}/message/{message_id}")
def delete_message(session_id: str, message_id: str) -> bool:
    _assert_session_idle(session_id)
    if not delete_session_message(session_id, message_id):
        raise HTTPException(status_code=404, detail="message not found")
    return True


@router.delete("/session/{session_id}/message/{message_id}/part/{part_id}")
def delete_message_part(session_id: str, message_id: str, part_id: str) -> bool:
    _assert_session_idle(session_id)
    if not delete_session_message_part(session_id, message_id, part_id):
        raise HTTPException(status_code=404, detail="part not found")
    return True


@router.post("/session/{session_id}/fork")
def fork_session_route(session_id: str, body: AssistantSessionForkRequest) -> AssistantSessionInfo:
    payload = get_session_record(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return session_info_from_record(fork_session(session_id, body.message_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/session/{session_id}/diff")
def session_diff(session_id: str) -> list[AssistantSessionDiffEntry]:
    payload = get_session_record(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session_diff_entries_from_value(get_session_diff(session_id))


@router.post("/session/{session_id}/revert")
def session_revert(session_id: str, body: AssistantSessionRevertRequest) -> AssistantSessionInfo:
    _assert_session_idle(session_id)
    payload = get_session_record(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return session_info_from_record(revert_session(session_id, body.message_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/session/{session_id}/unrevert")
def session_unrevert(session_id: str) -> AssistantSessionInfo:
    _assert_session_idle(session_id)
    payload = get_session_record(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return session_info_from_record(unrevert_session(session_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/session/{session_id}/permissions")
def session_permissions(session_id: str) -> list[dict]:
    if get_session_record(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    return list_pending(session_id)


@router.post("/session/{session_id}/permissions/{permission_id}")
def reply_permission_route(session_id: str, permission_id: str, body: PermissionReplyRequest):
    _, raw_stream, accepted = _prepare_permission_stream(session_id, permission_id, body)
    if not accepted:
        return StreamingResponse(
            raw_stream,
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    return StreamingResponse(
        raw_stream,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/session/{session_id}/permissions/{permission_id}/detached")
async def reply_permission_route_detached(
    session_id: str, permission_id: str, body: PermissionReplyRequest
) -> dict:
    _, raw_stream, accepted = _prepare_permission_stream(session_id, permission_id, body)
    if not accepted:
        return {
            "accepted": False,
            "session_id": session_id,
            "permission_id": permission_id,
        }
    _schedule_detached_stream(raw_stream, session_id=session_id, operation="permission")
    return {
        "accepted": True,
        "session_id": session_id,
        "permission_id": permission_id,
    }


@router.post("/session/{session_id}/abort")
def abort_session_route(session_id: str) -> bool:
    if get_session_record(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    request_session_abort(session_id)
    return True


@router.post("/session/{session_id}/summarize")
def summarize_session_route(session_id: str, body: SessionSummarizeRequest) -> bool:
    if get_session_record(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    summarize_session(
        session_id,
        provider_id=body.providerID,
        model_id=body.modelID,
        auto=False,
        overflow=False,
    )
    return True
