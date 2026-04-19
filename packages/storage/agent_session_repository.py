"""Agent conversation and persistent session repositories."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.storage.models import (
    AgentConversation,
    AgentMessage,
    AgentPendingAction,
    AgentPermissionRuleSet,
    AgentProject,
    AgentSession,
    AgentSessionMessage,
    AgentSessionPart,
    AgentSessionTodo,
)


class AgentConversationRepository:
    """Agent 对话会话 Repository"""

    def __init__(self, session: Session):
        self.session = session

    def create(self, user_id: str | None = None, title: str | None = None) -> AgentConversation:
        conv = AgentConversation(user_id=user_id, title=title)
        self.session.add(conv)
        self.session.flush()
        return conv

    def get_by_id(self, conv_id: str) -> AgentConversation | None:
        return self.session.get(AgentConversation, conv_id)

    def list_all(self, user_id: str | None = None, limit: int = 50) -> list[AgentConversation]:
        del user_id
        q = select(AgentConversation).order_by(AgentConversation.updated_at.desc()).limit(limit)
        return list(self.session.execute(q).scalars())

    def update_title(self, conv_id: str, title: str) -> AgentConversation | None:
        conv = self.get_by_id(conv_id)
        if conv:
            conv.title = title
            self.session.flush()
        return conv

    def delete(self, conv_id: str) -> bool:
        conv = self.get_by_id(conv_id)
        if conv:
            self.session.delete(conv)
            self.session.flush()
            return True
        return False


class AgentMessageRepository:
    """Agent 对话消息 Repository"""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        conversation_id: str,
        role: str,
        content: str,
        meta: dict | None = None,
    ) -> AgentMessage:
        msg = AgentMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            meta=meta,
        )
        self.session.add(msg)
        self.session.flush()
        return msg

    def list_by_conversation(self, conversation_id: str, limit: int = 100) -> list[AgentMessage]:
        q = (
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation_id)
            .order_by(AgentMessage.created_at.asc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def delete_by_conversation(self, conversation_id: str) -> int:
        q = delete(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
        result = self.session.execute(q)
        self.session.flush()
        return result.rowcount


class AgentProjectRepository:
    """OpenCode-like runtime project repository."""

    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, project_id: str) -> AgentProject | None:
        return self.session.get(AgentProject, project_id)

    def get_by_worktree(self, worktree: str) -> AgentProject | None:
        q = select(AgentProject).where(AgentProject.worktree == worktree)
        return self.session.execute(q).scalar_one_or_none()

    def list_all(self, limit: int = 200) -> list[AgentProject]:
        q = select(AgentProject).order_by(AgentProject.updated_at.desc()).limit(limit)
        return list(self.session.execute(q).scalars())

    def _assign_project_fields(
        self,
        row: AgentProject,
        *,
        worktree: str,
        vcs: str | None = None,
        name: str | None = None,
        icon_url: str | None = None,
        icon_color: str | None = None,
        commands_json: dict | None = None,
        sandboxes: list[str] | None = None,
        initialized_at: datetime | None = None,
    ) -> AgentProject:
        row.worktree = worktree
        row.vcs = vcs
        row.name = name
        row.icon_url = icon_url
        row.icon_color = icon_color
        row.commands_json = commands_json
        if sandboxes is not None:
            row.sandboxes_json = list(dict.fromkeys(sandboxes))
        if initialized_at is not None:
            row.initialized_at = initialized_at
        return row

    def upsert(
        self,
        *,
        project_id: str,
        worktree: str,
        vcs: str | None = None,
        name: str | None = None,
        icon_url: str | None = None,
        icon_color: str | None = None,
        commands_json: dict | None = None,
        sandboxes: list[str] | None = None,
        initialized_at: datetime | None = None,
    ) -> AgentProject:
        row = self.get_by_id(project_id) or self.get_by_worktree(worktree)
        if row is None:
            row = AgentProject(
                id=project_id,
                worktree=worktree,
                vcs=vcs,
                name=name,
                icon_url=icon_url,
                icon_color=icon_color,
                commands_json=commands_json,
                sandboxes_json=list(dict.fromkeys(sandboxes or [worktree])),
                initialized_at=initialized_at,
            )
            self.session.add(row)
        else:
            row = self._assign_project_fields(
                row,
                worktree=worktree,
                vcs=vcs,
                name=name,
                icon_url=icon_url,
                icon_color=icon_color,
                commands_json=commands_json,
                sandboxes=sandboxes,
                initialized_at=initialized_at,
            )
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            row = self.get_by_id(project_id) or self.get_by_worktree(worktree)
            if row is None:
                raise
            row = self._assign_project_fields(
                row,
                worktree=worktree,
                vcs=vcs,
                name=name,
                icon_url=icon_url,
                icon_color=icon_color,
                commands_json=commands_json,
                sandboxes=sandboxes,
                initialized_at=initialized_at,
            )
            self.session.flush()
        return row

    def add_sandbox(self, project_id: str, directory: str) -> AgentProject | None:
        row = self.get_by_id(project_id)
        if row is None:
            return None
        current = [str(item) for item in (row.sandboxes_json or []) if str(item).strip()]
        if directory not in current:
            current.append(directory)
        row.sandboxes_json = current
        self.session.flush()
        return row


class AgentPermissionRuleSetRepository:
    """Persisted OpenCode-like permission approvals keyed by project."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, project_id: str) -> AgentPermissionRuleSet | None:
        return self.session.get(AgentPermissionRuleSet, project_id)

    def get_ruleset(self, project_id: str) -> list[dict]:
        row = self.get(project_id)
        return list(row.data_json or []) if row is not None else []

    def replace(self, project_id: str, ruleset: list[dict]) -> AgentPermissionRuleSet:
        row = self.get(project_id)
        if row is None:
            row = AgentPermissionRuleSet(
                project_id=project_id,
                data_json=list(ruleset or []),
            )
            self.session.add(row)
        else:
            row.data_json = list(ruleset or [])
        self.session.flush()
        return row

    def append(self, project_id: str, rules: list[dict]) -> AgentPermissionRuleSet:
        existing = self.get_ruleset(project_id)
        existing.extend(list(rules or []))
        return self.replace(project_id, existing)


class AgentPendingActionRepository:
    """Persisted pending confirmations keyed by action id."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, action_id: str) -> AgentPendingAction | None:
        return self.session.get(AgentPendingAction, action_id)

    def list_by_session(self, session_id: str, *, action_type: str | None = None) -> list[AgentPendingAction]:
        q = select(AgentPendingAction).where(AgentPendingAction.session_id == session_id)
        if action_type is not None:
            q = q.where(AgentPendingAction.action_type == action_type)
        q = q.order_by(AgentPendingAction.created_at.asc(), AgentPendingAction.id.asc())
        return list(self.session.execute(q).scalars())

    def list_all(self, *, action_type: str | None = None) -> list[AgentPendingAction]:
        q = select(AgentPendingAction)
        if action_type is not None:
            q = q.where(AgentPendingAction.action_type == action_type)
        q = q.order_by(AgentPendingAction.created_at.asc(), AgentPendingAction.id.asc())
        return list(self.session.execute(q).scalars())

    def upsert(
        self,
        *,
        action_id: str,
        session_id: str,
        project_id: str,
        action_type: str,
        permission_json: dict | None = None,
        continuation_json: dict | None = None,
    ) -> AgentPendingAction:
        row = self.get(action_id)
        if row is None:
            row = AgentPendingAction(
                id=action_id,
                session_id=session_id,
                project_id=project_id,
                action_type=action_type,
                permission_json=permission_json,
                continuation_json=continuation_json,
            )
            self.session.add(row)
        else:
            row.session_id = session_id
            row.project_id = project_id
            row.action_type = action_type
            row.permission_json = permission_json
            row.continuation_json = continuation_json
        self.session.flush()
        return row

    def delete(self, action_id: str) -> bool:
        row = self.get(action_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def delete_by_ids(self, action_ids: list[str]) -> int:
        if not action_ids:
            return 0
        q = delete(AgentPendingAction).where(AgentPendingAction.id.in_(action_ids))
        result = self.session.execute(q)
        self.session.flush()
        return result.rowcount or 0


class AgentSessionRepository:
    """Persistent runtime session repository."""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        session_id: str,
        project_id: str,
        directory: str,
        title: str,
        slug: str,
        user_id: str | None = None,
        parent_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        workspace_server_id: str | None = None,
        mode: str = "build",
        backend_id: str = "native",
        permission_json: list[dict] | None = None,
    ) -> AgentSession:
        row = AgentSession(
            id=session_id,
            slug=slug,
            project_id=project_id,
            directory=directory,
            title=title,
            user_id=user_id,
            parent_id=parent_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            workspace_server_id=workspace_server_id,
            mode=mode,
            backend_id=backend_id,
            permission_json=permission_json,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_id(self, session_id: str) -> AgentSession | None:
        return self.session.get(AgentSession, session_id)

    def list_all(
        self,
        *,
        directory: str | None = None,
        roots: bool = False,
        start: datetime | None = None,
        search: str | None = None,
        limit: int = 50,
        archived: bool | None = None,
    ) -> list[AgentSession]:
        q = select(AgentSession)
        if directory:
            q = q.where(AgentSession.directory == directory)
        if roots:
            q = q.where(AgentSession.parent_id.is_(None))
        if start is not None:
            q = q.where(AgentSession.updated_at >= start)
        if search:
            q = q.where(AgentSession.title.ilike(f"%{search}%"))
        if archived is True:
            q = q.where(AgentSession.archived_at.is_not(None))
        elif archived is False:
            q = q.where(AgentSession.archived_at.is_(None))
        q = q.order_by(AgentSession.updated_at.desc()).limit(limit)
        return list(self.session.execute(q).scalars())

    def update(
        self,
        session_id: str,
        *,
        title: str | None = None,
        slug: str | None = None,
        project_id: str | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        workspace_server_id: str | None = None,
        mode: str | None = None,
        backend_id: str | None | object = ...,
        permission_json: list[dict] | None | object = ...,
        revert_json: dict | None | object = ...,
        share_url: str | None | object = ...,
        archived_at: datetime | None | object = ...,
        compacting_at: datetime | None | object = ...,
        summary_additions: int | None | object = ...,
        summary_deletions: int | None | object = ...,
        summary_files: int | None | object = ...,
        summary_diffs: list[dict] | None | object = ...,
    ) -> AgentSession | None:
        row = self.get_by_id(session_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if slug is not None:
            row.slug = slug
        if project_id is not None:
            row.project_id = project_id
        if directory is not None:
            row.directory = directory
        if workspace_id is not None:
            row.workspace_id = workspace_id
        if workspace_path is not None:
            row.workspace_path = workspace_path
        if workspace_server_id is not None:
            row.workspace_server_id = workspace_server_id
        if mode is not None:
            row.mode = mode
        if backend_id is not ...:
            row.backend_id = str(backend_id or "").strip() or "native"
        if permission_json is not ...:
            row.permission_json = permission_json
        if revert_json is not ...:
            row.revert_json = revert_json
        if share_url is not ...:
            row.share_url = share_url
        if archived_at is not ...:
            row.archived_at = archived_at
        if compacting_at is not ...:
            row.compacting_at = compacting_at
        if summary_additions is not ...:
            row.summary_additions = summary_additions
        if summary_deletions is not ...:
            row.summary_deletions = summary_deletions
        if summary_files is not ...:
            row.summary_files = summary_files
        if summary_diffs is not ...:
            row.summary_diffs = summary_diffs
        self.session.flush()
        return row

    def touch(self, session_id: str) -> AgentSession | None:
        row = self.get_by_id(session_id)
        if row is None:
            return None
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete(self, session_id: str) -> bool:
        row = self.get_by_id(session_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True


class AgentSessionMessageRepository:
    """Message repository for persistent agent sessions."""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        parent_id: str | None = None,
        message_type: str = "message",
        model: str | None = None,
        meta: dict | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> AgentSessionMessage:
        row = AgentSessionMessage(
            id=message_id,
            session_id=session_id,
            role=role,
            content=content,
            parent_id=parent_id,
            message_type=message_type,
            model=model,
            meta=meta,
        )
        if created_at is not None:
            row.created_at = created_at
        if updated_at is not None:
            row.updated_at = updated_at
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_id(self, message_id: str) -> AgentSessionMessage | None:
        return self.session.get(AgentSessionMessage, message_id)

    def update_meta(self, message_id: str, meta: dict | None) -> AgentSessionMessage | None:
        row = self.get_by_id(message_id)
        if row is None:
            return None
        row.meta = meta
        self.session.flush()
        return row

    def update(
        self,
        message_id: str,
        *,
        content: str | None = None,
        parent_id: str | None | object = ...,
        meta: dict | None | object = ...,
    ) -> AgentSessionMessage | None:
        row = self.get_by_id(message_id)
        if row is None:
            return None
        if content is not None:
            row.content = content
        if parent_id is not ...:
            row.parent_id = parent_id
        if meta is not ...:
            row.meta = meta
        self.session.flush()
        return row

    def list_by_session(self, session_id: str, limit: int = 500) -> list[AgentSessionMessage]:
        q = (
            select(AgentSessionMessage)
            .where(AgentSessionMessage.session_id == session_id)
            .order_by(AgentSessionMessage.created_at.asc(), AgentSessionMessage.id.asc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def delete_by_session(self, session_id: str) -> int:
        q = delete(AgentSessionMessage).where(AgentSessionMessage.session_id == session_id)
        result = self.session.execute(q)
        self.session.flush()
        return result.rowcount or 0

    def delete_by_ids(self, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        q = delete(AgentSessionMessage).where(AgentSessionMessage.id.in_(message_ids))
        result = self.session.execute(q)
        self.session.flush()
        return result.rowcount or 0


class AgentSessionPartRepository:
    """Structured parts for agent session messages."""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        part_id: str,
        session_id: str,
        message_id: str,
        part_type: str,
        content: str = "",
        data_json: dict | None = None,
        created_at: datetime | None = None,
    ) -> AgentSessionPart:
        row = AgentSessionPart(
            id=part_id,
            session_id=session_id,
            message_id=message_id,
            part_type=part_type,
            content=content,
            data_json=data_json,
        )
        if created_at is not None:
            row.created_at = created_at
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_id(self, part_id: str) -> AgentSessionPart | None:
        return self.session.get(AgentSessionPart, part_id)

    def upsert(
        self,
        *,
        part_id: str,
        session_id: str,
        message_id: str,
        part_type: str,
        content: str = "",
        data_json: dict | None = None,
        created_at: datetime | None = None,
    ) -> AgentSessionPart:
        row = self.get_by_id(part_id)
        if row is None:
            return self.create(
                part_id=part_id,
                session_id=session_id,
                message_id=message_id,
                part_type=part_type,
                content=content,
                data_json=data_json,
                created_at=created_at,
            )
        row.session_id = session_id
        row.message_id = message_id
        row.part_type = part_type
        row.content = content
        row.data_json = data_json
        self.session.add(row)
        self.session.flush()
        return row

    def list_by_message_ids(self, message_ids: list[str]) -> list[AgentSessionPart]:
        if not message_ids:
            return []
        q = (
            select(AgentSessionPart)
            .where(AgentSessionPart.message_id.in_(message_ids))
            .order_by(AgentSessionPart.created_at.asc(), AgentSessionPart.id.asc())
        )
        return list(self.session.execute(q).scalars())

    def delete_by_ids(self, part_ids: list[str]) -> int:
        if not part_ids:
            return 0
        q = delete(AgentSessionPart).where(AgentSessionPart.id.in_(part_ids))
        result = self.session.execute(q)
        self.session.flush()
        return result.rowcount or 0

    def replace_for_message(
        self,
        *,
        session_id: str,
        message_id: str,
        parts: list[dict],
    ) -> list[AgentSessionPart]:
        existing_rows = {
            row.id: row
            for row in self.session.execute(
                select(AgentSessionPart).where(AgentSessionPart.message_id == message_id)
            ).scalars()
        }
        keep_ids = [str(item["id"]) for item in parts]
        delete_query = delete(AgentSessionPart).where(AgentSessionPart.message_id == message_id)
        if keep_ids:
            delete_query = delete_query.where(AgentSessionPart.id.not_in(keep_ids))
        self.session.execute(delete_query)
        created: list[AgentSessionPart] = []
        for item in parts:
            part_id = str(item["id"])
            row = existing_rows.get(part_id)
            if row is None:
                row = self.create(
                    part_id=part_id,
                    session_id=session_id,
                    message_id=message_id,
                    part_type=str(item.get("type") or "text"),
                    content=str(item.get("content") or ""),
                    data_json=item.get("data"),
                    created_at=item.get("created_at"),
                )
            else:
                row.session_id = session_id
                row.message_id = message_id
                row.part_type = str(item.get("type") or "text")
                row.content = str(item.get("content") or "")
                row.data_json = item.get("data")
                if item.get("created_at") is not None:
                    row.created_at = item["created_at"]
                self.session.add(row)
                self.session.flush()
            created.append(row)
        self.session.flush()
        return created


class AgentSessionTodoRepository:
    """Persistent todo storage for agent sessions."""

    def __init__(self, session: Session):
        self.session = session

    def list_for_session(self, session_id: str) -> list[AgentSessionTodo]:
        q = (
            select(AgentSessionTodo)
            .where(AgentSessionTodo.session_id == session_id)
            .order_by(AgentSessionTodo.position.asc(), AgentSessionTodo.created_at.asc())
        )
        return list(self.session.execute(q).scalars())

    def replace(self, session_id: str, todos: list[dict]) -> list[AgentSessionTodo]:
        self.session.execute(delete(AgentSessionTodo).where(AgentSessionTodo.session_id == session_id))
        created: list[AgentSessionTodo] = []
        for index, item in enumerate(todos or []):
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            row = AgentSessionTodo(
                id=str(item.get("id") or ""),
                session_id=session_id,
                content=content,
                status=str(item.get("status") or "pending"),
                priority=str(item.get("priority") or "medium"),
                position=index,
            )
            if not row.id:
                row.id = str(uuid4())
            self.session.add(row)
            created.append(row)
        self.session.flush()
        return created
