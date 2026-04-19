from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class AssistantSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AssistantSessionForkRequest(AssistantSchema):
    message_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("message_id", "messageID"),
    )


class AssistantSessionRevertRequest(AssistantSchema):
    message_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("message_id", "messageID"),
    )


class AssistantSessionDiffEntry(AssistantSchema):
    file: str | None = None
    path: str | None = None
    status: str | None = None
    before: str | None = None
    after: str | None = None
    exists_before: bool | None = None
    exists_after: bool | None = None
    additions: int | None = None
    deletions: int | None = None
    workspace_path: str | None = None
    workspace_server_id: str | None = None


class AssistantSessionRevertInfo(AssistantSchema):
    message_id: str | None = None
    snapshot: str | None = None
    diffs: list[AssistantSessionDiffEntry] = Field(default_factory=list)


class AssistantSessionSummary(AssistantSchema):
    additions: int = 0
    deletions: int = 0
    files: int = 0
    diffs: list[AssistantSessionDiffEntry] = Field(default_factory=list)


class AssistantSessionTime(AssistantSchema):
    created: int = 0
    updated: int = 0
    compacting: int | None = None
    archived: int | None = None


class AssistantSessionInfo(AssistantSchema):
    id: str
    slug: str | None = None
    projectID: str | None = None
    workspaceID: str | None = None
    directory: str
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    parentID: str | None = None
    title: str
    version: str | None = None
    permission: list[Any] | None = None
    summary: AssistantSessionSummary | None = None
    revert: AssistantSessionRevertInfo | None = None
    time: AssistantSessionTime
    mode: str | None = None
    agent_backend_id: str | None = None


class AssistantConversationSummary(AssistantSchema):
    id: str
    title: str
    created_at: int
    updated_at: int


class AssistantConversationListResponse(AssistantSchema):
    conversations: list[AssistantConversationSummary] = Field(default_factory=list)


class AssistantConversationInfo(AssistantSchema):
    id: str
    title: str
    created_at: int


class AssistantConversationMessage(AssistantSchema):
    id: str
    role: str
    content: str
    created_at: int


class AssistantConversationMessagesResponse(AssistantSchema):
    conversation: AssistantConversationInfo
    messages: list[AssistantConversationMessage] = Field(default_factory=list)


class AssistantSessionStateResponse(AssistantSchema):
    session: AssistantSessionInfo
    messages: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[dict[str, Any]] = Field(default_factory=list)
    status: dict[str, Any] = Field(default_factory=lambda: {"type": "idle"})


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def session_diff_entry_from_value(value: Any) -> AssistantSessionDiffEntry:
    source = dict(value or {}) if isinstance(value, dict) else {}
    return AssistantSessionDiffEntry(
        file=_clean_text(source.get("file")),
        path=_clean_text(source.get("path")),
        status=_clean_text(source.get("status")),
        before=None if source.get("before") is None else str(source.get("before")),
        after=None if source.get("after") is None else str(source.get("after")),
        exists_before=source.get("exists_before") if isinstance(source.get("exists_before"), bool) else None,
        exists_after=source.get("exists_after") if isinstance(source.get("exists_after"), bool) else None,
        additions=int(source.get("additions")) if source.get("additions") not in {None, ""} else None,
        deletions=int(source.get("deletions")) if source.get("deletions") not in {None, ""} else None,
        workspace_path=_clean_text(source.get("workspace_path")),
        workspace_server_id=_clean_text(source.get("workspace_server_id")),
    )


def session_diff_entries_from_value(value: Any) -> list[AssistantSessionDiffEntry]:
    if not isinstance(value, list):
        return []
    items = [
        session_diff_entry_from_value(item)
        for item in value
        if isinstance(item, dict)
    ]
    return [item for item in items if item.file or item.path]


def session_revert_info_from_value(value: Any) -> AssistantSessionRevertInfo | None:
    if not isinstance(value, dict):
        return None
    message_id = _clean_text(value.get("message_id") or value.get("messageID"))
    snapshot = _clean_text(value.get("snapshot"))
    diffs = session_diff_entries_from_value(value.get("diffs"))
    if not message_id and not snapshot and not diffs:
        return None
    return AssistantSessionRevertInfo(
        message_id=message_id,
        snapshot=snapshot,
        diffs=diffs,
    )


def session_summary_from_value(value: Any) -> AssistantSessionSummary | None:
    if not isinstance(value, dict):
        return None
    diffs = session_diff_entries_from_value(value.get("diffs"))
    additions = int(value.get("additions") or 0)
    deletions = int(value.get("deletions") or 0)
    files = int(value.get("files") or 0)
    if additions == 0 and deletions == 0 and files == 0 and not diffs:
        return None
    return AssistantSessionSummary(
        additions=additions,
        deletions=deletions,
        files=files,
        diffs=diffs,
    )


def session_info_from_record(record: dict[str, Any]) -> AssistantSessionInfo:
    source = dict(record or {})
    time_payload = source.get("time") if isinstance(source.get("time"), dict) else {}
    created = int(time_payload.get("created") or 0)
    updated = int(time_payload.get("updated") or created)
    compacting = int(time_payload.get("compacting")) if time_payload.get("compacting") not in {None, ""} else None
    archived = int(time_payload.get("archived")) if time_payload.get("archived") not in {None, ""} else None
    permission = source.get("permission") if isinstance(source.get("permission"), list) else None
    return AssistantSessionInfo(
        id=str(source.get("id") or ""),
        slug=_clean_text(source.get("slug")),
        projectID=_clean_text(source.get("projectID")),
        workspaceID=_clean_text(source.get("workspaceID")),
        directory=str(source.get("directory") or source.get("workspace_path") or ""),
        workspace_path=_clean_text(source.get("workspace_path")),
        workspace_server_id=_clean_text(source.get("workspace_server_id")),
        parentID=_clean_text(source.get("parentID")),
        title=str(source.get("title") or "新对话"),
        version=_clean_text(source.get("version")),
        permission=permission,
        summary=session_summary_from_value(source.get("summary")),
        revert=session_revert_info_from_value(source.get("revert")),
        time=AssistantSessionTime(
            created=created,
            updated=updated,
            compacting=compacting,
            archived=archived,
        ),
        mode=_clean_text(source.get("mode")),
        agent_backend_id=_clean_text(source.get("agent_backend_id")),
    )


def session_state_from_values(
    session_record: dict[str, Any],
    *,
    messages: list[dict[str, Any]],
    permissions: list[dict[str, Any]],
    status: dict[str, Any] | None = None,
) -> AssistantSessionStateResponse:
    return AssistantSessionStateResponse(
        session=session_info_from_record(session_record),
        messages=[dict(item) for item in messages if isinstance(item, dict)],
        permissions=[dict(item) for item in permissions if isinstance(item, dict)],
        status=dict(status or {"type": "idle"}),
    )


def conversation_list_from_records(records: list[dict[str, Any]]) -> AssistantConversationListResponse:
    conversations = [
        AssistantConversationSummary(
            id=str(record.get("id") or ""),
            title=str(record.get("title") or "未命名对话"),
            created_at=int(((record.get("time") or {}) if isinstance(record.get("time"), dict) else {}).get("created") or 0),
            updated_at=int(((record.get("time") or {}) if isinstance(record.get("time"), dict) else {}).get("updated") or 0),
        )
        for record in records
        if isinstance(record, dict) and str(record.get("id") or "").strip()
    ]
    return AssistantConversationListResponse(conversations=conversations)


def conversation_messages_from_values(
    conversation_record: dict[str, Any],
    messages: list[dict[str, Any]],
) -> AssistantConversationMessagesResponse:
    time_payload = conversation_record.get("time") if isinstance(conversation_record.get("time"), dict) else {}
    items = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        content = "".join(
            str(part.get("text") or part.get("content") or "")
            for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        )
        items.append(
            AssistantConversationMessage(
                id=str(info.get("id") or ""),
                role=str(info.get("role") or ""),
                content=content,
                created_at=int(((info.get("time") or {}) if isinstance(info.get("time"), dict) else {}).get("created") or 0),
            )
        )
    return AssistantConversationMessagesResponse(
        conversation=AssistantConversationInfo(
            id=str(conversation_record.get("id") or ""),
            title=str(conversation_record.get("title") or "未命名对话"),
            created_at=int(time_payload.get("created") or 0),
        ),
        messages=items,
    )
