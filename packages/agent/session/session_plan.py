"""OpenCode-like plan mode helpers for ResearchOS sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import re
from typing import Any

from packages.agent.tools.apply_patch_runtime import patch_paths
from packages.agent.workspace.workspace_executor import local_shell_command_to_string
from packages.config import get_settings
from packages.storage.db import session_scope
from packages.storage.repositories import AgentProjectRepository

PLAN_MODE_EDIT_TOOLS = {
    "write",
    "edit",
    "multiedit",
    "apply_patch",
    "write_workspace_file",
    "replace_workspace_text",
}

PLAN_MODE_SPECIAL_TOOLS = {
    "plan_exit",
}

PLAN_MODE_READ_TOOLS = {
    "list",
    "ls",
    "glob",
    "grep",
    "read",
    "bash",
    "task",
    "webfetch",
    "websearch",
    "codesearch",
    "inspect_workspace",
    "read_workspace_file",
    "get_workspace_task_status",
    "question",
    "skill",
}

PLAN_MODE_ALLOWED_TOOLS = PLAN_MODE_READ_TOOLS | PLAN_MODE_EDIT_TOOLS | PLAN_MODE_SPECIAL_TOOLS


@dataclass(frozen=True)
class SessionPlanInfo:
    path: str
    relative_path: str | None
    storage: str
    exists: bool | None
    workspace_path: str | None = None
    workspace_server_id: str | None = None


def _materialize_local_plan_parent(info: SessionPlanInfo | None) -> SessionPlanInfo | None:
    if info is None or info.storage != "local":
        return info
    try:
        plan_path = Path(info.path)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        return replace(info, exists=plan_path.exists())
    except OSError:
        return info


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_remote_workspace(workspace_server_id: str | None) -> bool:
    normalized = _clean(workspace_server_id).lower()
    return normalized not in {"", "local"}


def _data_dir() -> Path:
    settings = get_settings()
    database_url = _clean(getattr(settings, "database_url", ""))
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        raw = database_url[len(prefix):]
        if raw:
            try:
                return Path(raw).expanduser().resolve().parent
            except OSError:
                return Path(raw).expanduser().parent
    try:
        return Path(settings.pdf_storage_root).expanduser().resolve().parent
    except OSError:
        return Path(settings.pdf_storage_root).expanduser().parent


def _local_path(value: Any, *, base: str | None = None) -> str | None:
    raw = _clean(value)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() and base:
        path = Path(base).expanduser() / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _remote_path(value: Any, *, base: str | None = None) -> str | None:
    raw = _clean(value).replace("\\", "/")
    if not raw:
        return None
    candidate = PurePosixPath(raw)
    if not raw.startswith("/") and base:
        base_path = PurePosixPath(_clean(base).replace("\\", "/"))
        candidate = base_path / candidate
    normalized = str(candidate)
    if raw.startswith("/") and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _project_row(project_id: str | None) -> dict[str, Any] | None:
    normalized_project_id = _clean(project_id)
    if not normalized_project_id:
        return None
    with session_scope() as session:
        row = AgentProjectRepository(session).get_by_id(normalized_project_id)
        if row is None:
            return None
        return {
            "worktree": _clean(getattr(row, "worktree", None)) or None,
            "vcs": _clean(getattr(row, "vcs", None)) or None,
        }


def _plan_filename(session_payload: dict[str, Any]) -> str | None:
    slug = _clean(session_payload.get("slug") or session_payload.get("id"))
    if not slug:
        return None
    time_payload = session_payload.get("time") if isinstance(session_payload.get("time"), dict) else {}
    created = int(time_payload.get("created") or 0)
    return f"{created}-{slug}.md" if created > 0 else f"{slug}.md"


def resolve_session_plan_info(session_payload: dict[str, Any] | None) -> SessionPlanInfo | None:
    if not isinstance(session_payload, dict):
        return None
    filename = _plan_filename(session_payload)
    if not filename:
        return None

    workspace_server_id = _clean(session_payload.get("workspace_server_id")) or None
    workspace_path = _clean(session_payload.get("workspace_path")) or None
    directory = _clean(session_payload.get("directory")) or workspace_path

    if _is_remote_workspace(workspace_server_id):
        remote_root = _remote_path(workspace_path or directory)
        if not remote_root:
            return None
        relative_path = str(PurePosixPath(".opencode") / "plans" / filename)
        return SessionPlanInfo(
            path=str(PurePosixPath(remote_root) / relative_path),
            relative_path=relative_path,
            storage="remote",
            exists=None,
            workspace_path=remote_root,
            workspace_server_id=workspace_server_id,
        )

    project = _project_row(session_payload.get("projectID"))
    worktree = _local_path(
        project.get("worktree") if isinstance(project, dict) else None,
    ) or _local_path(directory) or _local_path(workspace_path)
    if worktree is None:
        return None

    vcs = bool(project.get("vcs")) if isinstance(project, dict) else False
    if not vcs:
        try:
            vcs = (Path(worktree) / ".git").exists()
        except OSError:
            vcs = False

    base_dir = (Path(worktree) / ".opencode" / "plans") if vcs else (_data_dir() / "plans")
    plan_path = base_dir / filename
    relative_path: str | None = None
    try:
        relative_path = plan_path.relative_to(Path(worktree)).as_posix()
    except ValueError:
        relative_path = None

    return SessionPlanInfo(
        path=str(plan_path),
        relative_path=relative_path,
        storage="local",
        exists=plan_path.exists(),
        workspace_path=worktree,
        workspace_server_id=None,
    )


def plan_exit_confirmation_text(session_payload: dict[str, Any] | None) -> str:
    info = resolve_session_plan_info(session_payload)
    if info is None:
        return "计划已完成。切换到 build 模式并开始按计划实施吗？"
    return f"计划文件 {info.path} 已完成。切换到 build 模式并开始按计划实施吗？"


def plan_exit_followup_text(session_payload: dict[str, Any] | None) -> str:
    info = resolve_session_plan_info(session_payload)
    if info is None:
        return "The plan has been approved. Switch to build mode, you can now edit files, and execute the plan."
    return (
        f"The plan at {info.path} has been approved. "
        "Switch to build mode, you can now edit files, and execute the plan."
    )


def build_plan_mode_reminder(session_payload: dict[str, Any] | None) -> str:
    info = _materialize_local_plan_parent(resolve_session_plan_info(session_payload))
    if info is None:
        return (
            "<system-reminder>\n"
            "Plan mode is active. Stay read-only, do not edit files or run mutating commands, "
            "and ask the user for clarification when necessary.\n"
            "When the plan is ready, call plan_exit to request approval before implementation.\n"
            "</system-reminder>"
        )

    if info.storage == "remote":
        plan_file_text = (
            f"Plan file path: {info.path}\n"
            f"Use write_workspace_file to create it or replace_workspace_text to refine it.\n"
            f"workspace_path={info.workspace_path}\n"
            f"relative_path={info.relative_path}"
        )
    elif info.exists:
        plan_file_text = (
            f"A plan file already exists at {info.path}. "
            "You may update only this file using write, edit, or apply_patch."
        )
    else:
        plan_file_text = (
            f"No plan file exists yet. Create the plan at {info.path} using write, "
            "then refine only that file with edit or apply_patch."
        )

    return (
        "<system-reminder>\n"
        "Plan mode is active. The user asked you to analyze and plan before execution.\n\n"
        "CRITICAL: you are in a READ-ONLY phase. Do not edit files, change configs, run mutating commands, make commits, "
        "or otherwise modify the system.\n"
        "The only allowed write target is the plan file below.\n\n"
        f"{plan_file_text}\n\n"
        "Plan File Guidelines:\n"
        "- The plan file should contain only your final recommended approach, not all alternatives considered.\n"
        "- Keep it comprehensive yet concise: detailed enough to execute effectively, without unnecessary verbosity.\n"
        "- Include the critical files to modify and how you will verify the changes end-to-end.\n\n"
        "Plan Workflow:\n"
        "Phase 1. Initial Understanding\n"
        "- Gain a comprehensive understanding of the user's request by reading code and gathering context.\n"
        "- Focus on understanding the request, the relevant code paths, and any constraints.\n"
        "- If the task spans multiple areas, use the task tool with subagent_type=explore to investigate in parallel.\n"
        "- Prefer the minimum number of explore subagents necessary; typically 1, at most 3 when scope is uncertain.\n"
        "- After exploring the code, use the question tool to clarify ambiguities up front.\n\n"
        "Phase 2. Design\n"
        "- Design an implementation approach based on the user's intent and the evidence from Phase 1.\n"
        "- When useful, use the task tool with subagent_type=general to validate or compare approaches.\n"
        "- Provide enough context in those subagent prompts for them to reason about concrete files and constraints.\n\n"
        "Phase 3. Review\n"
        "- Re-read the critical files identified by exploration and design.\n"
        "- Ensure the plan aligns with the user's original request.\n"
        "- Use the question tool for any remaining requirement or tradeoff clarification.\n\n"
        "Phase 4. Final Plan File\n"
        "- Write the final implementation plan into the plan file.\n"
        "- Keep all plan edits confined to that single file.\n"
        "- Include the key files to modify and a verification section for end-to-end testing.\n\n"
        "Phase 5. Call plan_exit\n"
        "- At the very end of your turn, once you have asked any needed questions and are satisfied with the plan file, "
        "you should always call plan_exit.\n"
        "- Your turn should end only by asking the user a question or by calling plan_exit.\n"
        "- Do not stop after writing the plan file without calling plan_exit.\n\n"
        "If the user does not approve the plan, keep refining the plan file instead of executing.\n"
        "</system-reminder>"
    )


def _local_targets_for_call(call_name: str, arguments: dict[str, Any], session_payload: dict[str, Any]) -> list[str]:
    base = _local_path(session_payload.get("workspace_path") or session_payload.get("directory"))
    targets: list[str] = []
    if call_name in {"write", "edit"}:
        target = _local_path(arguments.get("file_path"), base=base)
        if target:
            targets.append(target)
    elif call_name == "multiedit":
        outer = _clean(arguments.get("file_path"))
        for item in arguments.get("edits") or []:
            if not isinstance(item, dict):
                continue
            target = _local_path(item.get("file_path") or outer, base=base)
            if target:
                targets.append(target)
    elif call_name == "apply_patch":
        try:
            for item in patch_paths(_clean(arguments.get("patchText"))):
                target = _local_path(item, base=base)
                if target:
                    targets.append(target)
        except Exception:
            return []
    elif call_name in {"write_workspace_file", "replace_workspace_text"}:
        workspace = _local_path(arguments.get("workspace_path"), base=base) or base
        relative_path = _clean(arguments.get("relative_path"))
        target = _local_path(relative_path, base=workspace) if workspace and relative_path else None
        if target:
            targets.append(target)
    return list(dict.fromkeys(targets))


def _remote_targets_for_call(call_name: str, arguments: dict[str, Any], session_payload: dict[str, Any]) -> list[str]:
    base = _remote_path(session_payload.get("workspace_path") or session_payload.get("directory"))
    targets: list[str] = []
    if call_name in {"write_workspace_file", "replace_workspace_text"}:
        workspace = _remote_path(arguments.get("workspace_path"), base=base) or base
        relative_path = _clean(arguments.get("relative_path"))
        target = _remote_path(relative_path, base=workspace) if workspace and relative_path else None
        if target:
            targets.append(target)
    return list(dict.fromkeys(targets))


def _targets_for_call(call_name: str, arguments: dict[str, Any], session_payload: dict[str, Any], info: SessionPlanInfo) -> list[str]:
    if info.storage == "remote":
        return _remote_targets_for_call(call_name, arguments, session_payload)
    return _local_targets_for_call(call_name, arguments, session_payload)


_PLAN_READ_ONLY_BASH_DENY_PATTERNS = [
    r"(^|[\s;&|])(rm|del|rmdir|mv|move|cp|copy|rename|ren|touch|mkdir|md)\b",
    r"(^|[\s;&|])(set-content|add-content|out-file|new-item|remove-item|move-item|copy-item|rename-item|clear-content)\b",
    r"(^|[\s;&|])(git\s+(add|commit|push|pull|merge|rebase|checkout|switch|restore|reset|clean|stash|apply|am|cherry-pick|revert))\b",
    r"(^|[\s;&|])(npm|pnpm|yarn|bun|pip|uv|poetry)\s+(install|add|remove|update|upgrade|uninstall|sync)\b",
    r"(^|[\s;&|])(pytest|nosetests|tox|nox|ruff|eslint|prettier|black|isort|mypy|tsc)\b",
    r"(^|[\s;&|])(python|py)\s+-m\s+pytest\b",
    r"(?:^|[\s;&|])(apply_patch|patch)\b",
    r"(?:^|[\s;&|])(sed|perl)\s+.*\s-i\b",
    r"(?:^|[\s;&|])(echo|write-output|printf)\b.*(?:>|>>)",
    r"(?:^|[\s;&|])(tee)\b",
    r"(?:^|[\s;&|])(curl|wget)\b.*(?:>|>>)",
]

_PLAN_READ_ONLY_BASH_ALLOW_PREFIXES = (
    "pwd",
    "ls",
    "dir",
    "cat ",
    "type ",
    "rg ",
    "findstr ",
    "where ",
    "which ",
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git rev-parse",
    "git remote",
    "git ls-files",
    "git grep",
    "get-location",
    "get-childitem",
    "gci",
    "get-content",
    "gc ",
    "select-string",
)


def _plan_read_only_shell_violation(call_name: str, arguments: dict[str, Any]) -> str | None:
    command_text = ""
    if call_name == "bash":
        command_text = _clean(arguments.get("command"))
    elif call_name == "local_shell":
        action = arguments.get("action") if isinstance(arguments.get("action"), dict) else {}
        command_text = local_shell_command_to_string(action.get("command"))
    elif call_name == "run_workspace_command":
        command_text = _clean(arguments.get("command"))

    normalized = command_text.strip().lower()
    if not normalized:
        return "Plan mode only allows read-only shell inspection commands."

    for pattern in _PLAN_READ_ONLY_BASH_DENY_PATTERNS:
        if re.search(pattern, normalized):
            return "Plan mode only allows read-only shell inspection commands."

    segments = [segment.strip() for segment in re.split(r"&&|\|\||;|\r?\n", normalized) if segment.strip()]
    if not segments:
        return "Plan mode only allows read-only shell inspection commands."

    for segment in segments:
        pipeline_parts = [part.strip() for part in segment.split("|") if part.strip()]
        if not pipeline_parts:
            return "Plan mode only allows read-only shell inspection commands."
        for part in pipeline_parts:
            if any(part == prefix or part.startswith(f"{prefix} ") for prefix in _PLAN_READ_ONLY_BASH_ALLOW_PREFIXES):
                continue
            return "Plan mode only allows read-only shell inspection commands."
    return None


def targets_plan_file_only(
    call_name: str,
    arguments: dict[str, Any],
    session_payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(session_payload, dict):
        return False
    info = resolve_session_plan_info(session_payload)
    if info is None:
        return False
    if call_name not in PLAN_MODE_EDIT_TOOLS:
        return False
    targets = _targets_for_call(call_name, arguments, session_payload, info)
    return bool(targets) and all(target == info.path for target in targets)


def check_plan_mode_tool_access(
    call_name: str,
    arguments: dict[str, Any],
    session_payload: dict[str, Any] | None,
    *,
    allow_in_read_only: bool,
) -> str | None:
    if not isinstance(session_payload, dict):
        return None
    if _clean(session_payload.get("mode")).lower() != "plan":
        return None
    if call_name in {"bash", "local_shell", "run_workspace_command"}:
        return _plan_read_only_shell_violation(call_name, arguments)
    if allow_in_read_only or call_name in PLAN_MODE_SPECIAL_TOOLS:
        return None

    info = resolve_session_plan_info(session_payload)
    if info is None:
        return "Plan mode is active. Only read-only tools are allowed until the plan is approved."

    if targets_plan_file_only(call_name, arguments, session_payload):
        return None

    return f"Plan mode is active. The only writable target is the plan file: {info.path}"

