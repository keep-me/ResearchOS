"""Agent 工作区管理路由。"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from packages.agent.workspace.workspace_remote import (
    DEFAULT_SSH_PORT,
    build_remote_overview,
    clean_text,
    probe_ssh,
    remote_git_branch,
    remote_git_commit,
    remote_git_discard,
    remote_git_diff,
    remote_git_init,
    remote_git_stage,
    remote_git_sync,
    remote_git_unstage,
    remote_read_file,
    remote_reveal,
    remote_terminal_result,
    remote_upload_file,
    remote_write_file,
)
from packages.agent.workspace.workspace_server_registry import (
    LOCAL_SERVER_ID,
    WorkspaceServerConflictError,
    WorkspaceServerNotFoundError,
    WorkspaceServerValidationError,
    create_workspace_server as _create_workspace_server_entry,
    delete_workspace_server as _delete_workspace_server_entry,
    get_workspace_server_entry as _get_workspace_server_entry,
    list_workspace_servers as _list_workspace_server_items,
    update_workspace_server as _update_workspace_server_entry,
)
from packages.agent.workspace.terminal_service import get_terminal_service
from packages.ai.project.output_sanitizer import (
    sanitize_project_artifact_preview_content,
    sanitize_project_run_metadata,
)
from packages.ai.project.report_formatter import build_workflow_report_markdown
from packages.agent.workspace.workspace_executor import (
    WorkspaceAccessError,
    ensure_workspace_operation_allowed,
    get_assistant_exec_policy,
    inspect_workspace,
    read_workspace_file,
    resolve_workspace_dir,
    resolve_workspace_file,
    run_workspace_command,
    write_workspace_file,
)
from packages.auth import auth_enabled, decode_request_token, extract_request_token_with_source
from packages.storage.db import session_scope
from packages.storage.repositories import ProjectRepository

router = APIRouter()

MAX_DIFF_CHARS = 120_000
RUN_PATH_PATTERN = re.compile(
    r"(?:^|/)\.auto-researcher/aris-runs/(?P<run_id>[^/]+)(?:/(?P<artifact>.+))?$",
    re.IGNORECASE,
)


class WorkspaceServerPayload(BaseModel):
    id: str | None = None
    label: str
    host: str | None = None
    port: int = Field(default=DEFAULT_SSH_PORT, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None
    workspace_root: str | None = None
    enabled: bool = True
    base_url: str | None = None
    api_token: str | None = None
    verify_tls: bool | None = True


class WorkspaceSshProbePayload(BaseModel):
    host: str
    port: int = Field(default=DEFAULT_SSH_PORT, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None
    workspace_root: str | None = None


class WorkspacePathPayload(BaseModel):
    path: str
    server_id: str = LOCAL_SERVER_ID


class WorkspaceGitBranchPayload(WorkspacePathPayload):
    branch_name: str
    checkout: bool = True


class WorkspaceGitFilePayload(WorkspacePathPayload):
    file_path: str | None = None


class WorkspaceGitCommitPayload(WorkspacePathPayload):
    message: str


class WorkspaceGitSyncPayload(WorkspacePathPayload):
    action: str


class WorkspaceTerminalPayload(WorkspacePathPayload):
    command: str
    timeout_sec: int = 240


class WorkspaceTerminalSessionPayload(WorkspacePathPayload):
    cols: int = Field(default=120, ge=40, le=320)
    rows: int = Field(default=32, ge=8, le=120)


class WorkspaceFileWritePayload(WorkspacePathPayload):
    relative_path: str
    content: str
    create_dirs: bool = True
    overwrite: bool = True


def _translate_server_registry_error(error: Exception) -> HTTPException:
    if isinstance(error, WorkspaceServerConflictError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, WorkspaceServerNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, WorkspaceServerValidationError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=400, detail=str(error or "远程服务器配置错误"))


def _list_servers() -> list[dict]:
    return _list_workspace_server_items()


def _find_server_entry(server_id: str) -> dict:
    try:
        return _get_workspace_server_entry(server_id)
    except Exception as exc:
        raise _translate_server_registry_error(exc) from exc


def get_workspace_server_entry(server_id: str) -> dict:
    """Internal helper for other routers/services that need SSH server config."""
    return _find_server_entry(server_id)


def _ensure_interactive_terminal_allowed() -> dict:
    policy = get_assistant_exec_policy()
    ensure_workspace_operation_allowed("write_workspace_file")
    if str(policy.get("command_execution") or "deny") != "full":
        raise WorkspaceAccessError("交互式终端仅在“完全执行”权限下可用")
    return policy


def _authenticate_terminal_websocket(websocket: WebSocket) -> dict | None:
    if not auth_enabled():
        return None

    token, token_source = extract_request_token_with_source(
        websocket.headers.get("authorization"),
        websocket.query_params.get("token"),
        allow_query_token=True,
    )
    if not token:
        raise WorkspaceAccessError("未认证，终端连接被拒绝")

    payload = decode_request_token(
        token,
        path=websocket.url.path,
        source=token_source,
    )
    if not payload:
        raise WorkspaceAccessError("终端连接令牌无效或已过期")
    return payload


def _serialize_terminal_session_snapshot(snapshot: dict) -> dict:
    return {
        "session_id": snapshot.get("session_id"),
        "server_id": snapshot.get("server_id"),
        "kind": snapshot.get("kind"),
        "workspace_path": snapshot.get("workspace_path"),
        "shell": snapshot.get("shell"),
        "cols": snapshot.get("cols"),
        "rows": snapshot.get("rows"),
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "closed": snapshot.get("closed"),
        "exit_code": snapshot.get("exit_code"),
        "error": snapshot.get("error"),
    }


def _translate_workspace_error(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    detail = str(error or "").strip() or "工作区操作失败"
    lower_detail = detail.lower()
    if "ssh protocol banner" in lower_detail or "protocol banner" in lower_detail:
        detail = (
            "SSH 握手失败：未能完成 SSH 协议标识读取。"
            "这可能是端口不对、服务端握手过慢，或链路被网关/防火墙中断。"
            "请先在终端手动执行一次 ssh 连接验证。"
        )
    return HTTPException(status_code=400, detail=detail)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    if not shutil.which("git"):
        raise WorkspaceAccessError("系统中未安装 Git，无法执行该操作")
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceAccessError(f"Git 命令执行超时: {' '.join(args)}") from exc


def _git_overview(workspace: Path) -> dict:
    if not shutil.which("git"):
        return {
            "available": False,
            "is_repo": False,
            "branch": None,
            "remotes": [],
            "entries": [],
            "changed_count": 0,
            "untracked_count": 0,
            "message": "系统中未安装 Git",
        }

    if not workspace.exists() or not workspace.is_dir():
        return {
            "available": True,
            "is_repo": False,
            "branch": None,
            "remotes": [],
            "entries": [],
            "changed_count": 0,
            "untracked_count": 0,
            "message": "当前目录不存在",
        }

    probe = _run_git(["rev-parse", "--is-inside-work-tree"], workspace)
    if probe.returncode != 0:
        return {
            "available": True,
            "is_repo": False,
            "branch": None,
            "remotes": [],
            "entries": [],
            "changed_count": 0,
            "untracked_count": 0,
            "message": "当前目录尚未初始化 Git 仓库",
        }

    branch = _run_git(["branch", "--show-current"], workspace).stdout.strip() or None
    remotes = [line.strip() for line in _run_git(["remote"], workspace).stdout.splitlines() if line.strip()]
    entries: list[dict] = []
    changed_count = 0
    untracked_count = 0
    for line in _run_git(["status", "--porcelain=v1"], workspace).stdout.splitlines():
      if len(line) < 3:
          continue
      code = line[:2]
      path = line[3:].strip()
      if " -> " in path:
          path = path.split(" -> ", 1)[1].strip()
      entries.append(
          {
              "path": path,
              "code": code,
              "index_status": code[0],
              "worktree_status": code[1],
          }
      )
      if code == "??":
          untracked_count += 1
      elif code.strip():
          changed_count += 1

    return {
        "available": True,
        "is_repo": True,
        "branch": branch,
        "remotes": remotes,
        "entries": entries,
        "changed_count": changed_count,
        "untracked_count": untracked_count,
        "message": None,
    }


def _trim_text(value: str, limit: int = MAX_DIFF_CHARS) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit] + "\n...[truncated]", True


def _ensure_git_repo(workspace: Path) -> dict:
    git = _git_overview(workspace)
    if not git["available"]:
        raise WorkspaceAccessError(git.get("message") or "系统中未安装 Git")
    if not git["is_repo"]:
        raise WorkspaceAccessError(git.get("message") or "当前目录尚未初始化 Git 仓库")
    return git


def _normalize_git_file_path(workspace: Path, file_path: str | None) -> str | None:
    raw_path = str(file_path or "").strip()
    if not raw_path:
        return None
    target = resolve_workspace_file(str(workspace), raw_path, create_workspace=False)
    return target.relative_to(workspace).as_posix()


def _serialize_git_result(result: subprocess.CompletedProcess[str]) -> dict:
    return {
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "exit_code": result.returncode,
    }


def _build_git_response(
    workspace: Path,
    *,
    action: str,
    result: subprocess.CompletedProcess[str],
    file_path: str | None = None,
) -> dict:
    if result.returncode != 0:
        raise WorkspaceAccessError(result.stderr.strip() or result.stdout.strip() or f"Git {action} 失败")
    return {
        "ok": True,
        "workspace_path": str(workspace),
        "action": action,
        "file_path": file_path,
        "result": _serialize_git_result(result),
        "git": _git_overview(workspace),
    }


def _run_git_unstage(workspace: Path, file_path: str | None = None) -> subprocess.CompletedProcess[str]:
    target = file_path or "."
    result = _run_git(["restore", "--staged", "--", target], workspace)
    if result.returncode == 0:
        return result
    stderr = result.stderr.lower()
    if "could not resolve head" in stderr or "unknown revision" in stderr or "did not match any file" in stderr:
        fallback = _run_git(["rm", "--cached", "-r", "--", target], workspace)
        if fallback.returncode == 0:
            return fallback
    return result


def _run_git_discard(workspace: Path, file_path: str) -> subprocess.CompletedProcess[str]:
    git = _ensure_git_repo(workspace)
    entry = next((item for item in git["entries"] if item.get("path") == file_path), None)
    if entry and str(entry.get("code") or "").strip() == "??":
        return _run_git(["clean", "-f", "--", file_path], workspace)

    result = _run_git(["restore", "--staged", "--worktree", "--", file_path], workspace)
    if result.returncode == 0:
        return result

    stderr = result.stderr.lower()
    if "could not resolve head" in stderr or "unknown revision" in stderr:
        unstaged = _run_git_unstage(workspace, file_path)
        if unstaged.returncode == 0:
            cleaned = _run_git(["clean", "-f", "--", file_path], workspace)
            if cleaned.returncode == 0:
                return cleaned
            return unstaged

    fallback = _run_git(["checkout", "--", file_path], workspace)
    if fallback.returncode == 0:
        return fallback
    return result


def _run_git_sync(workspace: Path, action: str) -> subprocess.CompletedProcess[str]:
    git = _ensure_git_repo(workspace)
    normalized = str(action or "").strip().lower()
    if normalized == "fetch":
        return _run_git(["fetch", "--all", "--prune"], workspace)
    if normalized == "pull":
        return _run_git(["pull", "--ff-only"], workspace)
    if normalized == "push":
        result = _run_git(["push"], workspace)
        combined = "\n".join(part for part in [result.stdout, result.stderr] if part).lower()
        if (
            result.returncode != 0
            and git.get("branch")
            and git.get("remotes")
            and ("set the remote as upstream" in combined or "no upstream branch" in combined)
        ):
            return _run_git(["push", "-u", str(git["remotes"][0]), str(git["branch"])], workspace)
        return result
    raise WorkspaceAccessError("不支持的 Git 同步动作")


def _normalize_preview_path(value: str | None) -> str:
    return str(value or "").replace("\\", "/").strip()


def _run_project_label(run) -> str:
    title = str(getattr(run, "title", "") or "").strip()
    if title:
        return title
    workflow_type = str(getattr(run, "workflow_type", "") or "").strip().replace("_", " ")
    return workflow_type or "研究流程"


def _run_workspace_root(run) -> str:
    if getattr(run, "workspace_server_id", None):
        return _normalize_preview_path(getattr(run, "remote_workdir", None))
    return _normalize_preview_path(getattr(run, "workdir", None))


def _collect_report_preview_candidates(run) -> set[str]:
    metadata = dict(getattr(run, "metadata_json", None) or {})
    roots = [
        _run_workspace_root(run).rstrip("/"),
        _normalize_preview_path(getattr(run, "run_directory", None)).rstrip("/"),
    ]
    candidates: set[str] = set()

    def _add(value: str | None) -> None:
        normalized = _normalize_preview_path(value).lstrip("/")
        if not normalized:
            return
        candidates.add(normalized)
        basename = normalized.rsplit("/", 1)[-1]
        if basename:
            candidates.add(basename)
        for root in roots:
            if root and normalized.startswith(f"{root.lstrip('/')}/"):
                relative = normalized[len(root.lstrip("/")) + 1 :].lstrip("/")
                if relative:
                    candidates.add(relative)

    _add(getattr(run, "result_path", None))
    for item in metadata.get("artifact_refs") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in {"report", "paper", "artifact"}:
            continue
        _add(item.get("path"))
        _add(item.get("relative_path"))
    return candidates


def _is_primary_run_report_request(run, relative_path: str | None) -> bool:
    requested = _normalize_preview_path(relative_path).lstrip("/")
    if not requested:
        return False
    for candidate in _collect_report_preview_candidates(run):
        normalized_candidate = _normalize_preview_path(candidate).lstrip("/")
        if not normalized_candidate:
            continue
        if (
            requested == normalized_candidate
            or requested.endswith(f"/{normalized_candidate}")
            or normalized_candidate.endswith(f"/{requested}")
        ):
            return True
    return False


def _resolve_workspace_preview_content(
    path: str | None,
    relative_path: str | None,
    content: str | None,
) -> str:
    sanitized = sanitize_project_artifact_preview_content(relative_path, content)
    match = RUN_PATH_PATTERN.search(_normalize_preview_path(relative_path)) or RUN_PATH_PATTERN.search(
        _normalize_preview_path(path)
    )
    if match is None:
        return sanitized

    run_id = str(match.group("run_id") or "").strip()
    if not run_id:
        return sanitized

    with session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        if run is None or not _is_primary_run_report_request(run, relative_path):
            return sanitized
        metadata = sanitize_project_run_metadata(dict(getattr(run, "metadata_json", None) or {}))
        formatted = build_workflow_report_markdown(
            workflow_type=str(getattr(run, "workflow_type", "") or ""),
            project_label=_run_project_label(run),
            prompt=getattr(run, "prompt", None),
            metadata=metadata,
        )
        return formatted or sanitized


@router.get("/agent/workspace/servers")
def list_workspace_servers() -> dict:
    return {"items": _list_servers()}


@router.post("/agent/workspace/servers")
def create_workspace_server(payload: WorkspaceServerPayload) -> dict:
    try:
        return {"item": _create_workspace_server_entry(payload)}
    except Exception as exc:
        raise _translate_server_registry_error(exc) from exc


@router.put("/agent/workspace/servers/{server_id}")
def update_workspace_server(server_id: str, payload: WorkspaceServerPayload) -> dict:
    try:
        return {"item": _update_workspace_server_entry(server_id, payload)}
    except Exception as exc:
        raise _translate_server_registry_error(exc) from exc


@router.delete("/agent/workspace/servers/{server_id}")
def delete_workspace_server(server_id: str) -> dict:
    try:
        return {"deleted": _delete_workspace_server_entry(server_id)}
    except Exception as exc:
        raise _translate_server_registry_error(exc) from exc


@router.post("/agent/workspace/ssh/probe")
def probe_workspace_ssh(payload: WorkspaceSshProbePayload) -> dict:
    return probe_ssh(
        {
            "host": payload.host,
            "port": payload.port,
            "username": clean_text(payload.username),
            "password": clean_text(payload.password),
            "private_key": clean_text(payload.private_key),
            "passphrase": clean_text(payload.passphrase),
            "workspace_root": clean_text(payload.workspace_root),
        }
    )


@router.get("/agent/workspace/overview")
def get_workspace_overview(
    path: str = Query(...),
    depth: int = Query(default=2, ge=0, le=6),
    max_entries: int = Query(default=0, ge=0, le=20000),
    server_id: str = Query(default=LOCAL_SERVER_ID),
) -> dict:
    try:
        ensure_workspace_operation_allowed("inspect_workspace")
        entry_limit = max_entries if max_entries > 0 else None
        if (server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            existed = Path(path).expanduser().resolve().exists()
            snapshot = inspect_workspace(path, max_depth=depth, max_entries=entry_limit)
            snapshot["exists"] = existed or Path(snapshot["workspace_path"]).exists()
            snapshot["git"] = _git_overview(Path(snapshot["workspace_path"]))
            return snapshot
        server_entry = _find_server_entry(server_id)
        return build_remote_overview(server_entry, path, depth=depth, max_entries=entry_limit)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/git/init")
def init_workspace_git(payload: WorkspacePathPayload) -> dict:
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command="git init")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(payload.path)
            result = _run_git(["init"], workspace)
            if result.returncode != 0:
                raise WorkspaceAccessError(result.stderr.strip() or "Git 初始化失败")
            return {
                "ok": True,
                "workspace_path": str(workspace),
                "result": {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "exit_code": result.returncode},
                "git": _git_overview(workspace),
            }
        server_entry = _find_server_entry(payload.server_id)
        return remote_git_init(server_entry, path=payload.path)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/git/branch")
def create_workspace_git_branch(payload: WorkspaceGitBranchPayload) -> dict:
    branch_name = payload.branch_name.strip()
    if not branch_name:
        raise HTTPException(status_code=400, detail="分支名不能为空")
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command=f"git checkout -b {branch_name}")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(payload.path)
            if not _git_overview(workspace)["is_repo"]:
                raise WorkspaceAccessError("当前目录尚未初始化 Git 仓库")
            exists_result = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"], workspace)
            created = exists_result.returncode != 0
            if created:
                command = ["checkout", "-b", branch_name] if payload.checkout else ["branch", branch_name]
            else:
                command = ["checkout", branch_name] if payload.checkout else ["branch", "--list", branch_name]
            result = _run_git(command, workspace)
            if result.returncode != 0:
                raise WorkspaceAccessError(result.stderr.strip() or "分支创建失败")
            return {
                "ok": True,
                "workspace_path": str(workspace),
                "branch": branch_name,
                "created": created,
                "checked_out": payload.checkout,
                "result": {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "exit_code": result.returncode},
                "git": _git_overview(workspace),
            }
        server_entry = _find_server_entry(payload.server_id)
        return remote_git_branch(server_entry, path=payload.path, branch_name=branch_name, checkout=payload.checkout)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.get("/agent/workspace/git/diff")
def get_workspace_git_diff(
    path: str = Query(...),
    file_path: str | None = Query(default=None),
    server_id: str = Query(default=LOCAL_SERVER_ID),
) -> dict:
    try:
        ensure_workspace_operation_allowed("inspect_workspace")
        if (server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(path, create=False)
            git = _git_overview(workspace)
            if not git["available"]:
                return {"workspace_path": str(workspace), "file_path": file_path, "diff": "", "truncated": False, "git": git, "message": git.get("message")}
            if not git["is_repo"]:
                return {"workspace_path": str(workspace), "file_path": file_path, "diff": "", "truncated": False, "git": git, "message": git.get("message")}
            args = ["diff", "--no-ext-diff"]
            if file_path:
                args.extend(["--", file_path])
            result = _run_git(args, workspace)
            diff_text, truncated = _trim_text(result.stdout)
            return {
                "workspace_path": str(workspace),
                "file_path": file_path,
                "diff": diff_text,
                "truncated": truncated,
                "git": _git_overview(workspace),
                "message": result.stderr.strip() or None,
            }
        server_entry = _find_server_entry(server_id)
        return remote_git_diff(server_entry, path=path, file_path=file_path, max_chars=MAX_DIFF_CHARS)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/git/stage")
def stage_workspace_git(payload: WorkspaceGitFilePayload) -> dict:
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command="git add")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(payload.path, create=False)
            _ensure_git_repo(workspace)
            normalized_file_path = _normalize_git_file_path(workspace, payload.file_path)
            command = ["add", "-A"] if not normalized_file_path else ["add", "--", normalized_file_path]
            result = _run_git(command, workspace)
            return _build_git_response(workspace, action="stage", result=result, file_path=normalized_file_path)
        server_entry = _find_server_entry(payload.server_id)
        return remote_git_stage(server_entry, path=payload.path, file_path=payload.file_path)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/git/unstage")
def unstage_workspace_git(payload: WorkspaceGitFilePayload) -> dict:
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command="git restore --staged")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(payload.path, create=False)
            _ensure_git_repo(workspace)
            normalized_file_path = _normalize_git_file_path(workspace, payload.file_path)
            result = _run_git_unstage(workspace, normalized_file_path)
            return _build_git_response(workspace, action="unstage", result=result, file_path=normalized_file_path)
        server_entry = _find_server_entry(payload.server_id)
        return remote_git_unstage(server_entry, path=payload.path, file_path=payload.file_path)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/git/discard")
def discard_workspace_git(payload: WorkspaceGitFilePayload) -> dict:
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command="git restore --worktree")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(payload.path, create=False)
            normalized_file_path = _normalize_git_file_path(workspace, payload.file_path)
            if not normalized_file_path:
                raise WorkspaceAccessError("请先选择要丢弃的文件")
            result = _run_git_discard(workspace, normalized_file_path)
            return _build_git_response(workspace, action="discard", result=result, file_path=normalized_file_path)
        server_entry = _find_server_entry(payload.server_id)
        return remote_git_discard(server_entry, path=payload.path, file_path=payload.file_path)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/git/commit")
def commit_workspace_git(payload: WorkspaceGitCommitPayload) -> dict:
    message = clean_text(payload.message)
    if not message:
        raise HTTPException(status_code=400, detail="提交说明不能为空")
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command="git commit")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(payload.path, create=False)
            _ensure_git_repo(workspace)
            result = _run_git(["commit", "-m", message], workspace)
            return _build_git_response(workspace, action="commit", result=result)
        server_entry = _find_server_entry(payload.server_id)
        return remote_git_commit(server_entry, path=payload.path, message=message)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/git/sync")
def sync_workspace_git(payload: WorkspaceGitSyncPayload) -> dict:
    action = clean_text(payload.action).lower()
    if action not in {"fetch", "pull", "push"}:
        raise HTTPException(status_code=400, detail="不支持的 Git 同步动作")
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command=f"git {action}")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(payload.path, create=False)
            result = _run_git_sync(workspace, action)
            return _build_git_response(workspace, action=action, result=result)
        server_entry = _find_server_entry(payload.server_id)
        return remote_git_sync(server_entry, path=payload.path, action=action)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/terminal/run")
def run_workspace_terminal(payload: WorkspaceTerminalPayload) -> dict:
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command=payload.command)
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            return run_workspace_command(payload.path, payload.command, timeout_sec=max(1, payload.timeout_sec))
        server_entry = _find_server_entry(payload.server_id)
        return remote_terminal_result(server_entry, path=payload.path, command=payload.command, timeout_sec=payload.timeout_sec)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/terminal/session")
def create_workspace_terminal_session(payload: WorkspaceTerminalSessionPayload) -> dict:
    try:
        _ensure_interactive_terminal_allowed()
        server_id = (payload.server_id or LOCAL_SERVER_ID).strip() or LOCAL_SERVER_ID
        server_entry = None if server_id == LOCAL_SERVER_ID else _find_server_entry(server_id)
        session = get_terminal_service().create_session(
            payload.path,
            server_id=server_id,
            server_entry=server_entry,
            cols=payload.cols,
            rows=payload.rows,
        )
        return {"session": _serialize_terminal_session_snapshot(session)}
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.delete("/agent/workspace/terminal/session/{session_id}", response_class=Response)
def close_workspace_terminal_session(session_id: str) -> Response:
    try:
        get_terminal_service().close_session(session_id)
        return Response(status_code=204)
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.websocket("/agent/workspace/terminal/session/{session_id}/ws")
async def workspace_terminal_session_ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    subscriber_id: str | None = None
    receive_task: asyncio.Task | None = None
    event_task: asyncio.Task | None = None
    session = None
    try:
        _authenticate_terminal_websocket(websocket)
        session = get_terminal_service().get_session(session_id)
        subscriber_id, snapshot = session.subscribe()
        await websocket.send_json({"type": "ready", "session": _serialize_terminal_session_snapshot(snapshot)})
        history = str(snapshot.get("history") or "")
        if history:
            await websocket.send_json({"type": "output", "data": history})
        error_text = str(snapshot.get("error") or "").strip()
        if error_text:
            await websocket.send_json({"type": "error", "message": error_text})
        if bool(snapshot.get("closed")):
            await websocket.send_json({"type": "exit", "exit_code": snapshot.get("exit_code")})
            return

        receive_task = asyncio.create_task(websocket.receive_text())
        event_task = asyncio.create_task(asyncio.to_thread(session.wait_for_event, subscriber_id, 0.25))
        while True:
            pending = {task for task in (receive_task, event_task) if task is not None}
            done, _pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

            if receive_task in done:
                try:
                    raw_message = receive_task.result()
                except WebSocketDisconnect:
                    break
                payload = json.loads(raw_message)
                message_type = str(payload.get("type") or "").strip().lower()
                if message_type == "input":
                    session.write(str(payload.get("data") or ""))
                elif message_type == "resize":
                    session.resize(int(payload.get("cols") or 0), int(payload.get("rows") or 0))
                elif message_type == "close":
                    get_terminal_service().close_session(session_id)
                    await websocket.close()
                    return
                elif message_type == "ping":
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json({"type": "error", "message": "不支持的终端消息类型"})
                receive_task = asyncio.create_task(websocket.receive_text())

            if event_task in done:
                event = event_task.result()
                event_task = asyncio.create_task(asyncio.to_thread(session.wait_for_event, subscriber_id, 0.25))
                if event is not None:
                    await websocket.send_json(event)
                    if event.get("type") == "exit":
                        break
    except WebSocketDisconnect:
        pass
    except Exception as error:
        detail = str(error or "").strip() or "终端连接异常"
        try:
            await websocket.send_json({"type": "error", "message": detail})
        except Exception:
            pass
        try:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=detail[:120])
        except Exception:
            pass
    finally:
        if session is not None and subscriber_id:
            session.unsubscribe(subscriber_id)
        for task in (receive_task, event_task):
            if task is not None:
                task.cancel()


@router.get("/agent/workspace/file")
def read_workspace_file_route(
    path: str = Query(...),
    relative_path: str = Query(...),
    max_chars: int = Query(default=120000, ge=1, le=500000),
    server_id: str = Query(default=LOCAL_SERVER_ID),
) -> dict:
    try:
        ensure_workspace_operation_allowed("read_workspace_file")
        if (server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            payload = read_workspace_file(path, relative_path, max_chars=max_chars)
            payload["content"] = _resolve_workspace_preview_content(path, relative_path, payload.get("content"))
            return payload
        server_entry = _find_server_entry(server_id)
        payload = remote_read_file(server_entry, path, relative_path, max_chars=max_chars)
        payload["content"] = _resolve_workspace_preview_content(path, relative_path, payload.get("content"))
        return payload
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.put("/agent/workspace/file")
def write_workspace_file_route(payload: WorkspaceFileWritePayload) -> dict:
    try:
        ensure_workspace_operation_allowed("write_workspace_file")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            return write_workspace_file(
                payload.path,
                payload.relative_path,
                payload.content,
                create_dirs=payload.create_dirs,
                overwrite=payload.overwrite,
            )
        server_entry = _find_server_entry(payload.server_id)
        return remote_write_file(
            server_entry,
            path=payload.path,
            relative_path=payload.relative_path,
            content=payload.content,
            create_dirs=payload.create_dirs,
            overwrite=payload.overwrite,
        )
    except Exception as error:
        raise _translate_workspace_error(error) from error


@router.post("/agent/workspace/upload")
async def upload_workspace_file(
    path: str = Form(...),
    server_id: str = Form(default=LOCAL_SERVER_ID),
    relative_path: str | None = Form(default=None),
    file: UploadFile = File(...),
) -> dict:
    try:
        ensure_workspace_operation_allowed("write_workspace_file")
        if (server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            workspace = resolve_workspace_dir(path)
            target_rel = (relative_path or file.filename or "upload.bin").strip()
            if not target_rel:
                target_rel = file.filename or "upload.bin"
            target = resolve_workspace_file(str(workspace), target_rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            content = await file.read()
            target.write_bytes(content)
            return {
                "workspace_path": str(workspace),
                "relative_path": str(target.relative_to(workspace)).replace("\\", "/"),
                "filename": file.filename or target.name,
                "mime_type": file.content_type or "application/octet-stream",
                "size_bytes": len(content),
                "created": not existed,
                "overwritten": existed,
            }
        server_entry = _find_server_entry(server_id)
        content = await file.read()
        return remote_upload_file(
            server_entry,
            path=path,
            relative_path=relative_path,
            filename=file.filename or "upload.bin",
            mime_type=file.content_type or "application/octet-stream",
            content=content,
        )
    except Exception as error:
        raise _translate_workspace_error(error) from error
    finally:
        await file.close()


@router.post("/agent/workspace/reveal")
def reveal_workspace(payload: WorkspacePathPayload) -> dict:
    try:
        ensure_workspace_operation_allowed("inspect_workspace")
        if (payload.server_id or LOCAL_SERVER_ID).strip() == LOCAL_SERVER_ID:
            target = Path(payload.path).expanduser().resolve()
            reveal_target = target
            select_target = False
            message: str | None = None

            if target.exists():
                if target.is_file():
                    reveal_target = target.parent
                    select_target = True
            else:
                parent = target.parent
                if parent.exists() and parent.is_dir():
                    reveal_target = parent
                    message = "目标文件不存在，已定位到父目录。"
                else:
                    return {
                        "path": str(target),
                        "opened": False,
                        "message": "目标路径不存在，未创建任何目录。",
                    }

            opened = False
            if shutil.which("explorer"):
                if select_target:
                    subprocess.Popen(["explorer", f"/select,{str(target)}"], shell=False)
                else:
                    subprocess.Popen(["explorer", str(reveal_target)], shell=False)
                opened = True
            else:
                message = message or "当前环境不支持自动打开资源管理器"
            return {"path": str(target), "opened": opened, "message": message}
        server_entry = _find_server_entry(payload.server_id)
        return remote_reveal(server_entry, path=payload.path)
    except Exception as error:
        raise _translate_workspace_error(error) from error
