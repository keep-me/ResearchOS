"""Agent 对话路由。"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from packages.agent.runtime.agent_service import (
    StreamPersistenceConfig,
    confirm_action,
    get_pending_action,
    reject_action,
    stream_chat,
)
from packages.agent.runtime.permission_next import get_pending as get_pending_permission
from packages.agent.session.session_runtime import (
    cleanup_reverted_session,
    delete_session,
    ensure_session_record,
    get_latest_user_message_id,
    get_session_record,
    list_session_messages,
    list_sessions,
    resolve_default_model_identity,
    sync_external_transcript,
)
from packages.domain.assistant_schemas import (
    AssistantConversationListResponse,
    AssistantConversationMessagesResponse,
    conversation_list_from_records,
    conversation_messages_from_values,
)
from packages.domain.schemas import AgentChatRequest

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}


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


def _resolve_workspace_binding(
    *,
    session_record: dict | None,
    workspace_path: str | None = None,
) -> tuple[str, str]:
    requested_workspace = str(workspace_path or "").strip()
    record_workspace = str((session_record or {}).get("workspace_path") or "").strip()
    record_directory = str((session_record or {}).get("directory") or "").strip()
    resolved_workspace = requested_workspace or record_workspace or record_directory
    resolved_directory = resolved_workspace or record_directory
    if not resolved_workspace or not resolved_directory:
        raise HTTPException(status_code=400, detail="当前未绑定工作区，请先导入或选择目录")
    return resolved_directory, resolved_workspace


@router.post("/agent/chat")
async def agent_chat(req: AgentChatRequest):
    """Agent 对话，返回 SSE 流。"""
    messages = [message.model_dump(exclude_none=True) for message in req.messages]
    existing_session = get_session_record(req.session_id)
    directory, workspace_path = _resolve_workspace_binding(
        session_record=existing_session,
        workspace_path=req.workspace_path,
    )
    session_payload = ensure_session_record(
        session_id=req.session_id,
        directory=directory,
        workspace_path=workspace_path,
        workspace_server_id=req.workspace_server_id,
        mode=req.mode,
        agent_backend_id=req.agent_backend_id,
    )
    if session_payload.get("revert"):
        cleanup_reverted_session(session_payload["id"])
        session_payload = get_session_record(session_payload["id"]) or session_payload
    sync_external_transcript(
        session_payload["id"],
        messages,
        reasoning_level=req.reasoning_level,
        active_skill_ids=req.active_skill_ids,
        mode=req.mode,
    )
    persisted_messages = list_session_messages(session_payload["id"], limit=2000)
    parent_id: str | None = None
    if persisted_messages:
        last_info = persisted_messages[-1]["info"]
        if str(last_info.get("role") or "") == "user":
            parent_id = str(last_info["id"])
    assistant_meta = {
        **resolve_default_model_identity(req.reasoning_level),
        "mode": req.mode,
        "agent": req.mode,
        "cwd": workspace_path,
        "root": session_payload["directory"],
        "variant": req.reasoning_level,
        "tokens": {
            "total": None,
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        },
        "cost": 0.0,
    }
    persistence = StreamPersistenceConfig(
        session_id=session_payload["id"],
        parent_id=parent_id,
        assistant_meta=assistant_meta,
    )
    raw_stream = stream_chat(
        messages,
        confirmed_action_id=req.confirmed_action_id,
        session_id=session_payload["id"],
        agent_backend_id=req.agent_backend_id,
        mode=req.mode,
        workspace_path=workspace_path,
        workspace_server_id=req.workspace_server_id,
        reasoning_level=req.reasoning_level,
        active_skill_ids=req.active_skill_ids,
        persistence=persistence,
    )
    return StreamingResponse(
        raw_stream,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/agent/confirm/{action_id}")
async def agent_confirm(action_id: str):
    """确认一个待执行动作。"""
    pending = get_pending_permission(action_id)
    pending_session_id = pending.session_id if pending is not None else None
    assistant_message_id = (
        str(((pending.tool or {}) if pending is not None else {}).get("messageID") or "").strip()
        or None
    )
    if pending_session_id is None:
        legacy_pending = get_pending_action(action_id)
        pending_session_id = (
            legacy_pending.options.session_id if legacy_pending is not None else None
        )
        assistant_message_id = (
            str(
                ((legacy_pending.permission_request or {}).get("tool") or {}).get("messageID") or ""
            ).strip()
            or None
            if legacy_pending is not None
            else None
        )
    if pending_session_id is None:
        raise HTTPException(status_code=404, detail="待确认动作不存在")
    session_record = get_session_record(pending_session_id)
    if session_record is None:
        raise HTTPException(status_code=404, detail="session not found")
    persistence = StreamPersistenceConfig(
        session_id=pending_session_id,
        parent_id=get_latest_user_message_id(pending_session_id),
        assistant_meta=_assistant_meta_from_session(session_record),
        assistant_message_id=assistant_message_id,
    )
    raw_stream = confirm_action(
        action_id,
        persistence=persistence,
    )
    return StreamingResponse(
        raw_stream,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/agent/reject/{action_id}")
async def agent_reject(action_id: str):
    """拒绝一个待执行动作。"""
    pending = get_pending_permission(action_id)
    pending_session_id = pending.session_id if pending is not None else None
    assistant_message_id = (
        str(((pending.tool or {}) if pending is not None else {}).get("messageID") or "").strip()
        or None
    )
    if pending_session_id is None:
        legacy_pending = get_pending_action(action_id)
        pending_session_id = (
            legacy_pending.options.session_id if legacy_pending is not None else None
        )
        assistant_message_id = (
            str(
                ((legacy_pending.permission_request or {}).get("tool") or {}).get("messageID") or ""
            ).strip()
            or None
            if legacy_pending is not None
            else None
        )
    if pending_session_id is None:
        raise HTTPException(status_code=404, detail="待确认动作不存在")
    session_record = get_session_record(pending_session_id)
    if session_record is None:
        raise HTTPException(status_code=404, detail="session not found")
    persistence = StreamPersistenceConfig(
        session_id=pending_session_id,
        parent_id=get_latest_user_message_id(pending_session_id),
        assistant_meta=_assistant_meta_from_session(session_record),
        assistant_message_id=assistant_message_id,
    )
    raw_stream = reject_action(
        action_id,
        persistence=persistence,
    )
    return StreamingResponse(
        raw_stream,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/agent/conversations")
def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
) -> AssistantConversationListResponse:
    """列出对话会话。"""
    conversations = list_sessions(limit=limit, archived=False)
    return conversation_list_from_records(conversations)


@router.get("/agent/conversations/{conversation_id}")
def get_conversation_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> AssistantConversationMessagesResponse:
    """获取某个对话会话的消息。"""
    conversation = get_session_record(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")

    messages = list_session_messages(conversation_id, limit=limit)
    return conversation_messages_from_values(conversation, messages)


@router.delete("/agent/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    """删除一个对话会话。"""
    deleted = delete_session(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="对话不存在")
    return {"deleted": conversation_id}
