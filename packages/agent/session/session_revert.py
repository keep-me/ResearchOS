"""Patch diff and revert helpers for persisted agent sessions."""

from __future__ import annotations

import copy
import logging
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from typing import Any

from packages.agent import (
    session_snapshot,
    session_store,
)
from packages.storage.db import session_scope
from packages.storage.repositories import (
    AgentPendingActionRepository,
    AgentSessionMessageRepository,
    AgentSessionRepository,
)

logger = logging.getLogger(__name__)


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def session_id(value: str | None) -> str:
    normalized = clean_text(value)
    return normalized or "default"


def is_remote_workspace(workspace_server_id: str | None) -> bool:
    normalized = clean_text(workspace_server_id).lower()
    return normalized not in {"", "local"}


def normalize_path(value: str | None, *, remote: bool = False) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if remote:
        return raw
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return raw


def normalize_remote_path(value: str | None) -> str:
    raw = clean_text(value).replace("\\", "/")
    if not raw:
        return ""
    normalized = str(PurePosixPath(raw))
    if raw.startswith("/") and not normalized.startswith("/"):
        return f"/{normalized}"
    return normalized


def diff_counts(before: str, after: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in unified_diff((before or "").splitlines(), (after or "").splitlines(), lineterm=""):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def path_is_absolute(value: str | None, *, remote: bool) -> bool:
    raw = clean_text(value)
    if not raw:
        return False
    if remote:
        return raw.startswith("/")
    return Path(raw).is_absolute()


def diff_exists(diff: dict[str, Any], *, before: bool) -> bool | None:
    key = "exists_before" if before else "exists_after"
    value = diff.get(key)
    if isinstance(value, bool):
        return value
    if value is not None:
        return bool(value)
    status = clean_text(diff.get("status")).lower()
    if before:
        if status in {"modified", "deleted"}:
            return True
        if status == "added":
            return False
        return None
    if status in {"modified", "added"}:
        return True
    if status == "deleted":
        return False
    return None


def resolved_diff_path(diff: dict[str, Any]) -> str:
    workspace_server_id = clean_text(diff.get("workspace_server_id"))
    remote = is_remote_workspace(workspace_server_id)
    path_value = clean_text(diff.get("path"))
    if path_value:
        return normalize_remote_path(path_value) if remote else normalize_path(path_value)

    file_value = clean_text(diff.get("file"))
    if not file_value:
        return ""

    workspace_path = clean_text(diff.get("workspace_path"))
    if remote:
        normalized_file = normalize_remote_path(file_value)
        if path_is_absolute(normalized_file, remote=True) or not workspace_path:
            return normalized_file
        return normalize_remote_path(f"{workspace_path.rstrip('/')}/{normalized_file.lstrip('/')}")

    if not path_is_absolute(file_value, remote=False) and workspace_path:
        return normalize_path(str(Path(workspace_path) / file_value))
    return normalize_path(file_value)


def diff_identity(diff: dict[str, Any]) -> str:
    resolved_path = resolved_diff_path(diff)
    if resolved_path:
        prefix = (
            "remote"
            if is_remote_workspace(clean_text(diff.get("workspace_server_id")))
            else "local"
        )
        return f"{prefix}::{resolved_path}"
    file_value = clean_text(diff.get("file"))
    if not file_value:
        return ""
    prefix = (
        "remote" if is_remote_workspace(clean_text(diff.get("workspace_server_id"))) else "local"
    )
    return f"{prefix}::{file_value}"


def merge_diff_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(existing)

    for key in ("path", "workspace_path", "workspace_server_id"):
        if not clean_text(result.get(key)) and clean_text(incoming.get(key)):
            result[key] = copy.deepcopy(incoming.get(key))

    existing_file = clean_text(result.get("file"))
    incoming_file = clean_text(incoming.get("file"))
    if not existing_file:
        if incoming_file:
            result["file"] = copy.deepcopy(incoming.get("file"))
    elif (
        incoming_file
        and path_is_absolute(existing_file, remote=False)
        and not path_is_absolute(incoming_file, remote=False)
    ):
        result["file"] = copy.deepcopy(incoming.get("file"))

    if "before" not in result and "before" in incoming:
        result["before"] = copy.deepcopy(incoming.get("before"))
    if "exists_before" not in result and "exists_before" in incoming:
        result["exists_before"] = copy.deepcopy(incoming.get("exists_before"))

    if "after" in incoming:
        result["after"] = copy.deepcopy(incoming.get("after"))
    if "exists_after" in incoming:
        result["exists_after"] = copy.deepcopy(incoming.get("exists_after"))

    exists_before = diff_exists(result, before=True)
    exists_after = diff_exists(result, before=False)
    if exists_before is not None:
        result["exists_before"] = exists_before
    if exists_after is not None:
        result["exists_after"] = exists_after

    before_text = str(result.get("before") or "")
    after_text = str(result.get("after") or "")
    additions, deletions = diff_counts(before_text, after_text)
    result["additions"] = additions
    result["deletions"] = deletions

    status = "modified"
    if exists_before is False and exists_after is True:
        status = "added"
    elif exists_before is True and exists_after is False:
        status = "deleted"
    result["status"] = status
    return result


def aggregate_diffs(diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw_diff in diffs:
        if not isinstance(raw_diff, dict):
            continue
        diff = copy.deepcopy(raw_diff)
        diff.pop("id", None)
        diff.pop("type", None)
        diff.pop("_order", None)
        identity = diff_identity(diff)
        if not identity:
            continue
        if identity not in merged:
            merged[identity] = diff
            order.append(identity)
            continue
        merged[identity] = merge_diff_records(merged[identity], diff)
    return [merged[item] for item in order]


def rewrite_file_from_diff(diff: dict[str, Any], *, before: bool) -> None:
    workspace_server_id = clean_text(diff.get("workspace_server_id"))
    file_path = clean_text(diff.get("path")) or clean_text(diff.get("file"))
    if not file_path:
        return
    content_key = "before" if before else "after"
    exists = diff_exists(diff, before=before)
    if is_remote_workspace(workspace_server_id):
        from packages.agent.workspace.workspace_remote import remote_restore_file
        from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry

        remote_restore_file(
            get_workspace_server_entry(workspace_server_id),
            path=file_path,
            content=str(diff.get(content_key) or "") if exists else None,
        )
        return
    target = Path(file_path)
    if exists:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(diff.get(content_key) or ""), encoding="utf-8")
        return
    if target.exists() and target.is_file():
        target.unlink()


def patch_diffs_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for part in message.get("parts") or []:
        if not isinstance(part, dict) or str(part.get("type") or "") != "patch":
            continue
        if isinstance(part.get("diffs"), list):
            diffs.extend(copy.deepcopy(part["diffs"]))
            continue
        patches = part.get("patches")
        if isinstance(patches, list):
            diffs.extend(copy.deepcopy(patches))
            continue
        direct_path = clean_text(part.get("path")) or clean_text(part.get("file"))
        if direct_path:
            diff_payload = copy.deepcopy(part)
            diff_payload.pop("id", None)
            diff_payload.pop("type", None)
            if not clean_text(diff_payload.get("file")):
                diff_payload["file"] = direct_path
            diffs.append(diff_payload)
            continue
        snapshot_hash = clean_text(part.get("hash"))
        workspace_path = clean_text(part.get("workspace_path"))
        files = [str(item) for item in (part.get("files") or []) if clean_text(item)]
        if snapshot_hash and workspace_path:
            try:
                diffs.extend(
                    session_snapshot.diff_current_full(
                        workspace_path,
                        snapshot_hash,
                        files=files or None,
                    )
                )
            except Exception:
                logger.exception("Failed to resolve patch diffs for %s", workspace_path)
    return diffs


def collect_revert_diffs(session_id_value: str, message_id: str) -> list[dict[str, Any]]:
    history = session_store.list_session_messages(session_id_value, limit=5000)
    start_collect = False
    diffs: list[dict[str, Any]] = []
    for message in history:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        current_id = str(info.get("id") or "")
        if current_id == message_id:
            start_collect = True
            continue
        if not start_collect:
            continue
        diffs.extend(patch_diffs_from_message(message))
    return aggregate_diffs(diffs)


def cleanup_reverted_session(session_id_value: str | None) -> None:
    sid = session_id(session_id_value)
    record = session_store.get_session_record(sid)
    if record is None or not isinstance(record.get("revert"), dict):
        return
    with session_scope() as session:
        AgentSessionMessageRepository(session).delete_by_session(sid)
        pending_repo = AgentPendingActionRepository(session)
        pending_rows = pending_repo.list_by_session(sid)
        pending_repo.delete_by_ids([row.id for row in pending_rows])
        AgentSessionRepository(session).update(
            sid,
            revert_json=None,
            summary_additions=None,
            summary_deletions=None,
            summary_files=None,
            summary_diffs=None,
        )


def get_session_diff(session_id_value: str | None) -> list[dict[str, Any]]:
    sid = session_id(session_id_value)
    record = session_store.get_session_record(sid)
    if record is None:
        return []
    if isinstance(record.get("revert"), dict):
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        return copy.deepcopy(summary.get("diffs") or [])
    history = session_store.list_session_messages(sid, limit=5000)
    diffs: list[dict[str, Any]] = []
    for message in history:
        diffs.extend(patch_diffs_from_message(message))
    return aggregate_diffs(diffs)


def revert_session(session_id_value: str | None, message_id: str) -> dict[str, Any]:
    sid = session_id(session_id_value)
    record = session_store.get_session_record(sid)
    if record is None:
        raise ValueError("session not found")
    diffs = collect_revert_diffs(sid, message_id)
    for diff in reversed(diffs):
        rewrite_file_from_diff(diff, before=True)
    summary = {
        "additions": sum(int(item.get("additions") or 0) for item in diffs),
        "deletions": sum(int(item.get("deletions") or 0) for item in diffs),
        "files": len(diffs),
        "diffs": copy.deepcopy(diffs),
    }
    revert_payload = {
        "messageID": message_id,
        "snapshot": next(
            (
                clean_text(part.get("hash"))
                for message in session_store.list_session_messages(sid, limit=5000)
                for part in (message.get("parts") or [])
                if str(part.get("type") or "") == "patch" and clean_text(part.get("hash"))
            ),
            None,
        ),
        "diffs": copy.deepcopy(diffs),
    }
    with session_scope() as session:
        AgentSessionRepository(session).update(
            sid,
            revert_json=revert_payload,
            summary_additions=summary["additions"],
            summary_deletions=summary["deletions"],
            summary_files=summary["files"],
            summary_diffs=summary["diffs"],
        )
    return session_store.get_session_record(sid) or record


def unrevert_session(session_id_value: str | None) -> dict[str, Any]:
    sid = session_id(session_id_value)
    record = session_store.get_session_record(sid)
    if record is None:
        raise ValueError("session not found")
    revert_payload = record.get("revert") if isinstance(record.get("revert"), dict) else {}
    diffs = copy.deepcopy(revert_payload.get("diffs") or [])
    for diff in diffs:
        rewrite_file_from_diff(diff, before=False)
    with session_scope() as session:
        AgentSessionRepository(session).update(
            sid,
            revert_json=None,
            summary_additions=None,
            summary_deletions=None,
            summary_files=None,
            summary_diffs=None,
        )
    return session_store.get_session_record(sid) or record
