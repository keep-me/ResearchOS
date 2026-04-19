from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from difflib import unified_diff
from pathlib import Path

from packages.config import get_settings
from packages.domain.task_tracker import global_tracker

DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}
SEARCH_DEPRIORITIZED_SEGMENTS = {
    "test",
    "tests",
    "docs",
    "reference",
    "tmp",
    "data",
    "logs",
    ".opencode",
    ".codex",
}
SEARCH_HARD_EXCLUDED_SEGMENTS = {
    "tmp",
    "logs",
    ".opencode",
    ".codex",
}
SEARCH_PREFERRED_SUBROOTS = (
    "packages",
    "apps",
    "frontend/src",
    "src-tauri/src",
    "scripts",
    "infra",
    "skills",
)
_LOCAL_SHELL_SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9_./:\\-]+$")
_IDENTIFIER_PATTERN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WILDCARD_INCLUDE_GLOBS = {"*", "*.*", "**", "**/*"}
DEFAULT_READ_LINE_LIMIT = 2000
MAX_READ_LINE_LENGTH = 2000
MAX_READ_LINE_SUFFIX = f"... (line truncated to {MAX_READ_LINE_LENGTH} chars)"

DEFAULT_COMMAND_ALLOWLIST = [
    "python",
    "python -m",
    "py",
    "pip",
    "pytest",
    "uv",
    "uv run",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "git status",
    "git diff",
    "git log",
    "git rev-parse",
    "git branch",
    "git checkout",
    "git switch",
    "git add",
    "git commit",
    "git fetch",
    "git pull",
    "git push",
    "Get-Location",
    "Get-ChildItem",
    "dir",
]

WORKSPACE_READ_TOOLS = {"inspect_workspace", "read_workspace_file"}
WORKSPACE_MUTATION_TOOLS = {
    "write_workspace_file",
    "replace_workspace_text",
    "run_workspace_command",
}
PATH_READ_TOOLS = {"ls", "glob", "grep", "read"}
PATH_MUTATION_TOOLS = {"write", "edit", "bash", "local_shell", "todowrite"}
WORKSPACE_AGENT_TOOLS = WORKSPACE_READ_TOOLS | WORKSPACE_MUTATION_TOOLS

WORKSPACE_ACCESS_LABELS = {
    "none": "禁止访问",
    "read": "只读",
    "read_write": "读写",
}
COMMAND_EXECUTION_LABELS = {
    "deny": "禁止执行",
    "allowlist": "白名单执行",
    "full": "完全执行",
}
APPROVAL_MODE_LABELS = {
    "always": "总是确认",
    "on_request": "仅写入/命令确认",
    "off": "自动执行",
}

VALID_WORKSPACE_ACCESS = set(WORKSPACE_ACCESS_LABELS)
VALID_COMMAND_EXECUTION = set(COMMAND_EXECUTION_LABELS)
VALID_APPROVAL_MODE = set(APPROVAL_MODE_LABELS)
TERMINAL_CWD_MARKER = "__RESEARCHOS_CWD__:"

DEFAULT_ASSISTANT_EXEC_POLICY = {
    "workspace_access": "read_write",
    "command_execution": "allowlist",
    "approval_mode": "on_request",
    "allowed_command_prefixes": DEFAULT_COMMAND_ALLOWLIST,
}


class WorkspaceAccessError(RuntimeError):
    pass


def default_projects_root() -> Path:
    root = _load_default_projects_root() or _builtin_projects_root()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _normalize_root_path(raw_path: str, *, require_exists: bool = True) -> Path:
    raw = (raw_path or "").strip()
    if not raw:
        raise WorkspaceAccessError("工作目录为空")
    resolved = Path(raw).expanduser().resolve()
    if require_exists and not resolved.exists():
        raise WorkspaceAccessError(f"工作目录不存在: {resolved}")
    if require_exists and not resolved.is_dir():
        raise WorkspaceAccessError(f"不是目录: {resolved}")
    return resolved


def _workspace_roots_store() -> Path:
    settings = get_settings()
    base_dir = settings.pdf_storage_root.parent.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "workspace_roots.json"


def _default_projects_root_store() -> Path:
    settings = get_settings()
    base_dir = settings.pdf_storage_root.parent.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "workspace_default_root.json"


def _assistant_exec_policy_store() -> Path:
    settings = get_settings()
    base_dir = settings.pdf_storage_root.parent.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "assistant_exec_policy.json"


def _hidden_config_roots_store() -> Path:
    settings = get_settings()
    base_dir = settings.pdf_storage_root.parent.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "workspace_hidden_roots.json"


def _builtin_projects_root() -> Path:
    settings = get_settings()
    return (settings.pdf_storage_root.parent.resolve() / "projects").resolve()


def _load_default_projects_root() -> Path | None:
    store = _default_projects_root_store()
    if not store.exists():
        return None
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    raw_path = None
    if isinstance(payload, str):
        raw_path = payload
    elif isinstance(payload, dict):
        raw_path = payload.get("path")
    if raw_path is None:
        return None

    try:
        return _normalize_root_path(str(raw_path), require_exists=False)
    except WorkspaceAccessError:
        return None


def set_default_projects_root(root_path: str | None) -> Path:
    store = _default_projects_root_store()
    builtin_root = _builtin_projects_root()
    raw = str(root_path or "").strip()
    target = _normalize_root_path(raw, require_exists=False) if raw else builtin_root
    target.mkdir(parents=True, exist_ok=True)
    if str(target).lower() == str(builtin_root).lower():
        if store.exists():
            store.unlink(missing_ok=True)
        return builtin_root
    store.write_text(
        json.dumps({"path": str(target)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target.resolve()


def _configured_roots() -> list[Path]:
    settings = get_settings()
    roots: list[Path] = []
    for raw in str(settings.automation_allowed_roots or "").split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            roots.append(Path(value).expanduser().resolve())
        except OSError:
            continue
    return roots


def _load_custom_root_strings() -> list[str]:
    return [item["path"] for item in _load_custom_root_entries()]


def _save_custom_root_strings(values: list[str]) -> None:
    entries = [{"path": value, "title": None} for value in values]
    _save_custom_root_entries(entries)


def _normalize_hidden_root_values(values: object) -> list[str]:
    if not isinstance(values, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        raw_path = str(item or "").strip()
        if not raw_path:
            continue
        try:
            resolved = str(_normalize_root_path(raw_path, require_exists=False)).lower()
        except WorkspaceAccessError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)
    return normalized


def _load_hidden_config_roots() -> list[str]:
    store = _hidden_config_roots_store()
    if not store.exists():
        return []
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return _normalize_hidden_root_values(payload)


def _save_hidden_config_roots(values: list[str]) -> None:
    store = _hidden_config_roots_store()
    normalized = _normalize_hidden_root_values(values)
    store.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clean_root_title(value: object) -> str | None:
    if value is None:
        return None
    title = str(value).strip()
    return title or None


def _normalize_custom_root_entry(item: object) -> dict | None:
    if isinstance(item, str):
        path = item.strip()
        if not path:
            return None
        return {"path": path, "title": None}

    if isinstance(item, dict):
        path = str(item.get("path") or "").strip()
        if not path:
            return None
        return {
            "path": path,
            "title": _clean_root_title(item.get("title")),
        }

    return None


def _load_custom_root_entries() -> list[dict]:
    store = _workspace_roots_store()
    if not store.exists():
        return []
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []

    entries: list[dict] = []
    for item in payload:
        entry = _normalize_custom_root_entry(item)
        if entry is not None:
            entries.append(entry)
    return entries


def _save_custom_root_entries(entries: list[dict]) -> None:
    store = _workspace_roots_store()
    store.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_command_prefixes(values: object) -> list[str]:
    raw_items: list[str] = []
    if isinstance(values, str):
        for line in values.replace("\r", "\n").split("\n"):
            raw_items.extend(line.split(","))
    elif isinstance(values, (list, tuple, set)):
        for item in values:
            if isinstance(item, str):
                raw_items.append(item)

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        value = " ".join(item.strip().split())
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


def _sanitize_assistant_exec_policy(payload: dict | None) -> dict:
    raw = payload if isinstance(payload, dict) else {}

    workspace_access = str(raw.get("workspace_access") or "").strip()
    if workspace_access not in VALID_WORKSPACE_ACCESS:
        workspace_access = DEFAULT_ASSISTANT_EXEC_POLICY["workspace_access"]

    command_execution = str(raw.get("command_execution") or "").strip()
    if command_execution not in VALID_COMMAND_EXECUTION:
        command_execution = DEFAULT_ASSISTANT_EXEC_POLICY["command_execution"]

    approval_mode = str(raw.get("approval_mode") or "").strip()
    if approval_mode not in VALID_APPROVAL_MODE:
        approval_mode = DEFAULT_ASSISTANT_EXEC_POLICY["approval_mode"]

    allowed_command_prefixes = _normalize_command_prefixes(raw.get("allowed_command_prefixes"))
    if not allowed_command_prefixes:
        allowed_command_prefixes = list(DEFAULT_COMMAND_ALLOWLIST)

    return {
        "workspace_access": workspace_access,
        "command_execution": command_execution,
        "approval_mode": approval_mode,
        "allowed_command_prefixes": allowed_command_prefixes,
    }


def get_assistant_exec_policy() -> dict:
    store = _assistant_exec_policy_store()
    if not store.exists():
        return dict(DEFAULT_ASSISTANT_EXEC_POLICY)
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_ASSISTANT_EXEC_POLICY)
    return _sanitize_assistant_exec_policy(payload)


def update_assistant_exec_policy(values: dict) -> dict:
    current = get_assistant_exec_policy()
    merged = {**current, **(values or {})}
    policy = _sanitize_assistant_exec_policy(merged)
    store = _assistant_exec_policy_store()
    store.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    return policy


def summarize_assistant_exec_policy() -> dict:
    policy = get_assistant_exec_policy()
    return {
        **policy,
        "workspace_access_label": WORKSPACE_ACCESS_LABELS[policy["workspace_access"]],
        "command_execution_label": COMMAND_EXECUTION_LABELS[policy["command_execution"]],
        "approval_mode_label": APPROVAL_MODE_LABELS[policy["approval_mode"]],
    }


def should_confirm_workspace_action(tool_name: str) -> bool:
    policy = get_assistant_exec_policy()
    approval_mode = policy.get("approval_mode")
    if approval_mode == "off":
        return False
    if approval_mode == "always":
        return True
    return tool_name in (WORKSPACE_MUTATION_TOOLS | PATH_MUTATION_TOOLS)


def _command_matches_allowlist(command: str, prefixes: list[str]) -> bool:
    normalized_command = " ".join((command or "").strip().split()).lower()
    if not normalized_command:
        return False
    for prefix in prefixes:
        normalized_prefix = " ".join(prefix.strip().split()).lower()
        if not normalized_prefix:
            continue
        if normalized_command == normalized_prefix or normalized_command.startswith(f"{normalized_prefix} "):
            return True
    return False


def ensure_workspace_operation_allowed(
    tool_name: str,
    *,
    command: str | None = None,
) -> dict:
    policy = get_assistant_exec_policy()
    normalized_tool = str(tool_name or "").strip()
    if normalized_tool in {"run_workspace_command", "bash", "local_shell"} and not (command or "").strip():
        raise WorkspaceAccessError("命令为空")

    workspace_access = str(policy.get("workspace_access") or "none")
    command_execution = str(policy.get("command_execution") or "deny")
    allowed_prefixes = list(policy.get("allowed_command_prefixes") or [])

    if normalized_tool in (WORKSPACE_READ_TOOLS | PATH_READ_TOOLS):
        if workspace_access == "none":
            raise WorkspaceAccessError("当前权限禁止读取本地工作区或目录")
    elif normalized_tool in (WORKSPACE_MUTATION_TOOLS | PATH_MUTATION_TOOLS):
        if normalized_tool == "todowrite":
            if workspace_access == "none":
                raise WorkspaceAccessError("当前权限禁止写入待办或工作区状态")
        elif workspace_access != "read_write":
            raise WorkspaceAccessError("当前权限禁止修改本地文件或目录")

    if normalized_tool in {"run_workspace_command", "bash", "local_shell"}:
        if command_execution == "deny":
            raise WorkspaceAccessError("当前权限禁止执行命令")
        if command_execution == "allowlist" and not _command_matches_allowlist(command or "", allowed_prefixes):
            raise WorkspaceAccessError("该命令不在允许列表中")

    return policy


def allowed_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for root in _configured_roots():
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    for entry in _load_custom_root_entries():
        try:
            resolved = _normalize_root_path(str(entry.get("path") or ""))
        except WorkspaceAccessError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def list_workspace_roots() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for entry in _load_custom_root_entries():
        raw_path = str(entry.get("path") or "")
        title = _clean_root_title(entry.get("title"))
        try:
            root = _normalize_root_path(raw_path, require_exists=False)
            exists = root.exists()
        except WorkspaceAccessError:
            root = Path(raw_path).expanduser().resolve()
            exists = False
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "path": str(root),
                "title": title or root.name or str(root),
                "source": "custom",
                "removable": True,
                "exists": exists,
            }
        )
    return items


def add_workspace_root(root_path: str, title: str | None = None) -> dict:
    resolved = _normalize_root_path(root_path, require_exists=False)
    if resolved.exists() and not resolved.is_dir():
        raise WorkspaceAccessError(f"不是目录: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    current_key = str(resolved).lower()
    entries = _load_custom_root_entries()
    next_title = _clean_root_title(title) or resolved.name or str(resolved)
    updated = False
    normalized_entries: list[dict] = []
    for entry in entries:
        raw_path = str(entry.get("path") or "")
        key = str(Path(raw_path).expanduser().resolve()).lower()
        if key == current_key:
            normalized_entries.append({"path": str(resolved), "title": next_title})
            updated = True
            continue
        normalized_entries.append(
            {
                "path": str(Path(raw_path).expanduser().resolve()),
                "title": _clean_root_title(entry.get("title")),
            }
        )
    if not updated:
        normalized_entries.append({"path": str(resolved), "title": next_title})

    deduped: list[dict] = []
    seen: set[str] = set()
    for entry in normalized_entries:
        raw_path = str(entry.get("path") or "")
        key = str(Path(raw_path).expanduser().resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "path": str(Path(raw_path).expanduser().resolve()),
                "title": _clean_root_title(entry.get("title")),
            }
        )
    _save_custom_root_entries(deduped)
    return {
        "path": str(resolved),
        "title": _clean_root_title(title) or resolved.name or str(resolved),
        "source": "custom",
        "removable": True,
        "exists": True,
    }


def remove_workspace_root(root_path: str) -> None:
    resolved = _normalize_root_path(root_path, require_exists=False)
    current_key = str(resolved).lower()
    existing = any(
        str(Path(str(entry.get("path") or "")).expanduser().resolve()).lower() == current_key
        for entry in _load_custom_root_entries()
    )
    if not existing:
        raise WorkspaceAccessError(f"未找到已保存工作区: {resolved}")

    remaining: list[dict] = []
    for entry in _load_custom_root_entries():
        raw_path = str(entry.get("path") or "")
        key = str(Path(raw_path).expanduser().resolve()).lower()
        if key == current_key:
            continue
        remaining.append(
            {
                "path": str(Path(raw_path).expanduser().resolve()),
                "title": _clean_root_title(entry.get("title")),
            }
        )
    _save_custom_root_entries(remaining)


def update_workspace_root(root_path: str, title: str) -> dict:
    resolved = _normalize_root_path(root_path, require_exists=False)
    if resolved.exists() and not resolved.is_dir():
        raise WorkspaceAccessError(f"不是目录: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    current_key = str(resolved).lower()
    normalized_title = _clean_root_title(title) or resolved.name or str(resolved)

    entries = _load_custom_root_entries()
    next_entries: list[dict] = []
    updated = False
    for entry in entries:
        raw_path = str(entry.get("path") or "")
        key = str(Path(raw_path).expanduser().resolve()).lower()
        if key == current_key:
            updated = True
            next_entries.append(
                {
                    "path": str(Path(raw_path).expanduser().resolve()),
                    "title": normalized_title,
                }
            )
            continue
        next_entries.append(
            {
                "path": str(Path(raw_path).expanduser().resolve()),
                "title": _clean_root_title(entry.get("title")),
            }
        )

    if not updated:
        next_entries.append(
            {
                "path": str(resolved),
                "title": normalized_title,
            }
        )

    _save_custom_root_entries(next_entries)
    return {
        "path": str(resolved),
        "title": normalized_title,
        "source": "custom",
        "removable": True,
        "exists": resolved.exists(),
    }


def resolve_workspace_dir(workspace_path: str, *, create: bool = True) -> Path:
    resolved = _normalize_root_path(workspace_path, require_exists=False)
    if resolved.exists() and not resolved.is_dir():
        raise WorkspaceAccessError(f"不是目录: {resolved}")
    if create and not resolved.exists():
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_workspace_file(
    workspace_path: str,
    relative_path: str,
    *,
    create_workspace: bool = True,
) -> Path:
    base = resolve_workspace_dir(workspace_path, create=create_workspace)
    rel = (relative_path or "").strip()
    if not rel:
        raise WorkspaceAccessError("文件路径为空")
    target = (base / rel).resolve()
    if not _is_relative_to(target, base):
        raise WorkspaceAccessError("文件路径越界")
    return target


def resolve_path_input(
    path_input: str,
    *,
    workspace_path: str | None = None,
    expect_dir: bool | None = None,
    create_dir: bool = False,
) -> Path:
    raw = str(path_input or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            if not (workspace_path or "").strip():
                raise WorkspaceAccessError("相对路径需要结合 workspace_path 使用")
            base = resolve_workspace_dir(workspace_path)
            resolved = (base / raw).resolve()
            if not _is_relative_to(resolved, base):
                raise WorkspaceAccessError("文件路径越界")
    elif (workspace_path or "").strip():
        resolved = resolve_workspace_dir(workspace_path)
    else:
        raise WorkspaceAccessError("路径为空")

    if expect_dir is True:
        if resolved.exists() and not resolved.is_dir():
            raise WorkspaceAccessError(f"不是目录: {resolved}")
        if create_dir:
            resolved.mkdir(parents=True, exist_ok=True)
    elif expect_dir is False and resolved.exists() and not resolved.is_file():
        raise WorkspaceAccessError(f"不是文件: {resolved}")

    return resolved


def _path_relative_to_workspace(target: Path, workspace_path: str | None) -> str | None:
    if not (workspace_path or "").strip():
        return None
    try:
        return target.relative_to(resolve_workspace_dir(workspace_path)).as_posix()
    except (ValueError, WorkspaceAccessError):
        return None


def _path_display(target: Path, workspace_path: str | None) -> dict:
    relative_path = _path_relative_to_workspace(target, workspace_path)
    payload = {
        "path": str(target),
        "name": target.name or str(target),
        "relative_path": relative_path,
    }
    if workspace_path:
        payload["workspace_path"] = str(resolve_workspace_dir(workspace_path))
    return payload


def _search_path_priority(root: Path, target: Path) -> tuple[int, int, str]:
    try:
        relative = target.relative_to(root)
        parts = relative.parts
    except ValueError:
        parts = target.parts

    penalty = 0
    for index, part in enumerate(parts[:-1]):
        lowered = part.lower()
        if lowered in SEARCH_DEPRIORITIZED_SEGMENTS:
            penalty += 100 + index * 5

    file_name = str(parts[-1] if parts else target.name).lower()
    if file_name.startswith("test_") or file_name.endswith("_test.py") or file_name.endswith(".spec.ts"):
        penalty += 80

    return penalty, len(parts), str(target).lower()


def _grep_line_priority(pattern: str, line_text: str) -> int:
    normalized_pattern = str(pattern or "").strip()
    normalized_line = str(line_text or "")
    if not normalized_pattern or not normalized_line:
        return 100
    if not _IDENTIFIER_PATTERN_RE.fullmatch(normalized_pattern):
        return 100

    escaped = re.escape(normalized_pattern)
    if re.search(rf"\b(def|class|function)\s+{escaped}\b", normalized_line):
        return 0
    if re.search(rf"\b(from|import)\b.*\b{escaped}\b", normalized_line):
        return 10
    if re.search(rf"\b{escaped}\b", normalized_line):
        return 20
    return 100


def _ripgrep_path() -> str | None:
    return shutil.which("rg")


def _search_excluded_segments_for_root(root: Path) -> set[str]:
    root_parts = {part.lower() for part in root.parts}
    if root_parts & SEARCH_HARD_EXCLUDED_SEGMENTS:
        return set()
    return set(SEARCH_HARD_EXCLUDED_SEGMENTS)


def _target_contains_segment(root: Path, target: Path, segments: set[str]) -> bool:
    if not segments:
        return False
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        parts = target.parts
    return any(str(part).lower() in segments for part in parts)


def _preferred_search_roots(root: Path) -> list[Path]:
    root_parts = {part.lower() for part in root.parts}
    if root_parts & {
        "packages",
        "apps",
        "frontend",
        "src-tauri",
        "scripts",
        "infra",
        "skills",
        "tests",
        "docs",
        "reference",
    }:
        return []

    candidates: list[Path] = []
    seen: set[str] = set()
    for relative in SEARCH_PREFERRED_SUBROOTS:
        candidate = (root / relative).resolve()
        if not candidate.exists() or not candidate.is_dir():
            continue
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _ripgrep_ignore_globs(root: Path) -> list[str]:
    globs: list[str] = []
    for name in sorted(DEFAULT_IGNORES):
        globs.extend(
            [
                f"!{name}",
                f"!{name}/**",
                f"!**/{name}",
                f"!**/{name}/**",
            ]
        )
    for name in sorted(_search_excluded_segments_for_root(root)):
        globs.extend(
            [
                f"!{name}",
                f"!{name}/**",
                f"!**/{name}",
                f"!**/{name}/**",
            ]
        )
    return globs


def _normalize_search_include_glob(include_glob: str | None) -> str:
    include = str(include_glob or "").strip()
    if include in _WILDCARD_INCLUDE_GLOBS:
        return ""
    return include


def _ripgrep_path_matches(
    root: Path,
    pattern: str,
    *,
    workspace_path: str | None,
    include_glob: str | None = None,
    limit: int = 100,
) -> list[dict] | None:
    executable = _ripgrep_path()
    if not executable:
        return None

    max_hits = max(1, min(limit, 500))
    candidate_limit = min(max_hits * 4, 2000)
    command = [
        executable,
        "--json",
        "--line-number",
        "--ignore-case",
        "--hidden",
        "--no-messages",
    ]
    for glob in _ripgrep_ignore_globs(root):
        command.extend(["--glob", glob])
    include = _normalize_search_include_glob(include_glob)
    if include:
        command.extend(["--glob", include])
    command.extend([pattern, str(root)])

    matches: list[dict] = []
    terminated_early = False
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root),
    )
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if str(payload.get("type") or "") != "match":
                continue
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            path_info = data.get("path") if isinstance(data.get("path"), dict) else {}
            raw_path = str(path_info.get("text") or "").strip()
            if not raw_path:
                continue
            target = Path(raw_path)
            if not target.is_absolute():
                target = (root / target).resolve()
            line_number = int(data.get("line_number") or 0)
            lines_info = data.get("lines") if isinstance(data.get("lines"), dict) else {}
            line_text = str(lines_info.get("text") or "").rstrip("\r\n")
            matches.append(
                {
                    **_path_display(target, workspace_path),
                    "line": line_number,
                    "text": line_text[:600],
                }
            )
            if len(matches) >= candidate_limit:
                terminated_early = True
                process.terminate()
                break
        stderr_text = ""
        if process.stderr is not None:
            stderr_text = process.stderr.read()
        return_code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    if terminated_early:
        return matches
    if return_code in {0, 1}:
        return matches
    if stderr_text:
        return None
    return matches


def _iter_searchable_files(root: Path) -> list[Path]:
    files: list[Path] = []
    excluded_segments = _search_excluded_segments_for_root(root)
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError:
            continue
        for child in children:
            if child.name in DEFAULT_IGNORES:
                continue
            if _target_contains_segment(root, child, excluded_segments):
                continue
            if child.is_dir():
                stack.append(child)
                continue
            files.append(child)
    files.sort(key=lambda item: _search_path_priority(root, item))
    return files


def list_path_entries(
    *,
    path_input: str = "",
    workspace_path: str | None = None,
    recursive: bool = False,
    max_depth: int = 2,
    max_entries: int = 120,
) -> dict:
    root = resolve_path_input(
        path_input,
        workspace_path=workspace_path,
        expect_dir=True,
        create_dir=True,
    )
    tree_lines: list[str] = [str(root)]
    items: list[dict] = []
    entry_count = 0

    def walk(current: Path, depth: int) -> None:
        nonlocal entry_count
        if entry_count >= max_entries:
            return
        if recursive and depth > max_depth:
            return
        try:
            children = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError:
            return
        for child in children:
            if entry_count >= max_entries:
                return
            if child.name in DEFAULT_IGNORES:
                continue
            prefix = "  " * depth + "- "
            entry_count += 1
            entry = {
                **_path_display(child, workspace_path),
                "is_dir": child.is_dir(),
                "size_bytes": None if child.is_dir() else child.stat().st_size,
            }
            items.append(entry)
            tree_lines.append(f"{prefix}{child.name}/" if child.is_dir() else f"{prefix}{child.name}")
            if recursive and child.is_dir():
                walk(child, depth + 1)

    walk(root, 0)
    return {
        **_path_display(root, workspace_path),
        "directory_path": str(root),
        "tree": "\n".join(tree_lines),
        "entries": items,
        "total_entries": len(items),
        "truncated": len(items) >= max_entries,
    }


def glob_path_entries(
    pattern: str,
    *,
    path_input: str = "",
    workspace_path: str | None = None,
    limit: int = 100,
) -> dict:
    root = resolve_path_input(
        path_input,
        workspace_path=workspace_path,
        expect_dir=True,
        create_dir=True,
    )
    matches: list[dict] = []
    seen_paths: set[str] = set()
    normalized_pattern = str(pattern or "").strip()
    if not normalized_pattern:
        raise WorkspaceAccessError("pattern 不能为空")

    max_hits = max(1, min(limit, 500))
    search_roots = _preferred_search_roots(root)
    ordered_roots = [*search_roots, root] if search_roots else [root]
    excluded_segments = _search_excluded_segments_for_root(root)

    for current_root in ordered_roots:
        for target in sorted(current_root.glob(normalized_pattern), key=lambda item: _search_path_priority(root, item)):
            if len(matches) >= max_hits:
                break
            if target.name in DEFAULT_IGNORES:
                continue
            if _target_contains_segment(root, target, excluded_segments):
                continue
            key = str(target.resolve()).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            matches.append(
                {
                    **_path_display(target, workspace_path),
                    "is_dir": target.is_dir(),
                    "size_bytes": None if target.is_dir() else target.stat().st_size,
                }
            )
        if len(matches) >= max_hits:
            break

    return {
        **_path_display(root, workspace_path),
        "pattern": normalized_pattern,
        "matches": matches,
        "count": len(matches),
        "truncated": len(matches) >= max_hits,
    }


def grep_path_contents(
    pattern: str,
    *,
    path_input: str = "",
    workspace_path: str | None = None,
    include_glob: str | None = None,
    limit: int = 100,
) -> dict:
    root = resolve_path_input(
        path_input,
        workspace_path=workspace_path,
        expect_dir=True,
        create_dir=True,
    )
    normalized_pattern = str(pattern or "").strip()
    if not normalized_pattern:
        raise WorkspaceAccessError("pattern 不能为空")

    try:
        regex = re.compile(normalized_pattern, re.IGNORECASE)
    except re.error as exc:
        raise WorkspaceAccessError(f"无效正则: {exc}") from exc

    include = _normalize_search_include_glob(include_glob)
    max_hits = max(1, min(limit, 500))
    candidate_limit = min(max_hits * 4, 2000)
    matches: list[dict] = []
    seen_matches: set[tuple[str, int, str]] = set()
    search_roots = _preferred_search_roots(root)
    ordered_roots = [*search_roots, root] if search_roots else [root]
    identifier_lookup = bool(_IDENTIFIER_PATTERN_RE.fullmatch(normalized_pattern)) and not include
    excluded_segments = _search_excluded_segments_for_root(root)

    for current_root in ordered_roots:
        current_matches = _ripgrep_path_matches(
            current_root,
            normalized_pattern,
            workspace_path=workspace_path,
            include_glob=include,
            limit=max_hits,
        )
        if current_matches is None:
            current_matches = []
            for file_path in _iter_searchable_files(current_root):
                if include and not fnmatch.fnmatch(file_path.name, include) and not fnmatch.fnmatch(
                    file_path.relative_to(current_root).as_posix(),
                    include,
                ):
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if not regex.search(line):
                        continue
                    current_matches.append(
                        {
                            **_path_display(file_path, workspace_path),
                            "line": line_no,
                            "text": line[:600],
                        }
                    )
                    if len(current_matches) >= candidate_limit:
                        break
                if len(current_matches) >= candidate_limit:
                    break
        for item in current_matches:
            item_path = Path(str(item.get("path") or ""))
            if _target_contains_segment(root, item_path, excluded_segments):
                continue
            key = (
                str(item.get("path") or "").lower(),
                int(item.get("line") or 0),
                str(item.get("text") or ""),
            )
            if key in seen_matches:
                continue
            seen_matches.add(key)
            matches.append(item)
            if len(matches) >= candidate_limit:
                break
        if len(matches) >= candidate_limit:
            break
        if current_root != root and len(matches) >= max_hits:
            break
        if current_root != root and identifier_lookup and matches:
            break

    sorted_matches = sorted(
        matches,
        key=lambda item: (
            _search_path_priority(root, Path(str(item.get("path") or "")))[0],
            _grep_line_priority(normalized_pattern, str(item.get("text") or "")),
            str(item.get("relative_path") or item.get("path") or "").lower(),
            int(item.get("line") or 0),
        ),
    )
    truncated = len(sorted_matches) > max_hits
    final_matches = sorted_matches[:max_hits]

    return {
        **_path_display(root, workspace_path),
        "pattern": normalized_pattern,
        "include_glob": include or None,
        "matches": final_matches,
        "count": len(final_matches),
        "truncated": truncated,
    }


def read_path_file(
    file_path: str,
    *,
    workspace_path: str | None = None,
    max_chars: int = 12000,
    offset: int | None = None,
    limit: int | None = None,
) -> dict:
    target = resolve_path_input(file_path, workspace_path=workspace_path, expect_dir=None)
    if target.is_dir():
        return list_path_entries(
            path_input=str(target),
            workspace_path=workspace_path,
            recursive=False,
            max_entries=200,
        )
    if not target.exists():
        raise WorkspaceAccessError(f"文件不存在: {target}")
    if not target.is_file():
        raise WorkspaceAccessError(f"不是文件: {target}")
    content = target.read_text(encoding="utf-8", errors="replace")
    if offset is not None or limit is not None:
        normalized_offset = int(offset or 1)
        normalized_limit = int(limit or DEFAULT_READ_LINE_LIMIT)
        if normalized_offset < 1:
            raise WorkspaceAccessError("offset 必须大于等于 1")
        if normalized_limit < 1:
            raise WorkspaceAccessError("limit 必须大于等于 1")
        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines < normalized_offset and not (total_lines == 0 and normalized_offset == 1):
            raise WorkspaceAccessError(f"offset 超出文件范围: {normalized_offset} > {total_lines}")
        start_index = max(normalized_offset - 1, 0)
        sliced_lines = lines[start_index : start_index + normalized_limit]
        numbered_lines = [
            f"{line_number}: "
            + (
                text[:MAX_READ_LINE_LENGTH] + MAX_READ_LINE_SUFFIX
                if len(text) > MAX_READ_LINE_LENGTH
                else text
            )
            for line_number, text in enumerate(sliced_lines, start=normalized_offset)
        ]
        line_end = normalized_offset + len(sliced_lines) - 1 if sliced_lines else normalized_offset - 1
        truncated = start_index + len(sliced_lines) < total_lines
        return {
            **_path_display(target, workspace_path),
            "content": "\n".join(numbered_lines),
            "raw_content": "\n".join(sliced_lines),
            "offset": normalized_offset,
            "limit": normalized_limit,
            "line_start": normalized_offset,
            "line_end": line_end,
            "total_lines": total_lines,
            "next_offset": (line_end + 1) if truncated else None,
            "truncated": truncated,
            "size_bytes": target.stat().st_size,
        }
    truncated = len(content) > max_chars
    return {
        **_path_display(target, workspace_path),
        "content": content[:max_chars],
        "truncated": truncated,
        "size_bytes": target.stat().st_size,
    }


def write_path_file(
    file_path: str,
    content: str,
    *,
    workspace_path: str | None = None,
    create_dirs: bool = True,
    overwrite: bool = True,
) -> dict:
    target = resolve_path_input(file_path, workspace_path=workspace_path, expect_dir=False)
    existed = target.exists()
    if existed and target.is_dir():
        raise WorkspaceAccessError(f"目标是目录，无法写入文件: {target}")
    if existed and not overwrite:
        raise WorkspaceAccessError(f"文件已存在，未允许覆盖: {target}")

    if not target.parent.exists():
        if not create_dirs:
            raise WorkspaceAccessError(f"父目录不存在: {target.parent}")
        target.parent.mkdir(parents=True, exist_ok=True)

    previous_text = ""
    previous_size = 0
    changed = True
    if existed:
        previous_text = target.read_text(encoding="utf-8", errors="replace")
        previous_size = target.stat().st_size
        changed = previous_text != content

    _atomic_write_text(target, content)
    diff_preview = _build_diff_preview(previous_text, content) if existed and changed else ""
    return {
        **_path_display(target, workspace_path),
        "created": not existed,
        "overwritten": existed,
        "changed": changed,
        "size_bytes": target.stat().st_size,
        "previous_size_bytes": previous_size,
        "line_count": content.count("\n") + (0 if not content else 1),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "preview": _trim_output(content, max_chars=2400),
        "diff_preview": diff_preview,
    }


def edit_path_file(
    file_path: str,
    old_string: str,
    new_string: str,
    *,
    workspace_path: str | None = None,
    replace_all: bool = False,
) -> dict:
    target = resolve_path_input(file_path, workspace_path=workspace_path, expect_dir=False)
    if old_string == new_string:
        raise WorkspaceAccessError("old_string 与 new_string 完全一致")

    if not old_string:
        existed = target.exists()
        if existed and not target.is_file():
            raise WorkspaceAccessError(f"不是文件: {target}")
        original = target.read_text(encoding="utf-8", errors="replace") if existed else ""
        _atomic_write_text(target, new_string)
        return {
            **_path_display(target, workspace_path),
            "created": not existed,
            "changed": original != new_string,
            "replace_all": replace_all,
            "matched_occurrences": 0,
            "replaced_occurrences": 0,
            "size_bytes": target.stat().st_size,
            "line_count": new_string.count("\n") + (0 if not new_string else 1),
            "sha256": hashlib.sha256(new_string.encode("utf-8")).hexdigest(),
            "preview": _trim_output(new_string, max_chars=2400),
            "diff_preview": _build_diff_preview(original, new_string),
        }

    if not target.exists():
        raise WorkspaceAccessError(f"文件不存在: {target}")
    if not target.is_file():
        raise WorkspaceAccessError(f"不是文件: {target}")

    original = target.read_text(encoding="utf-8", errors="replace")
    match_count = original.count(old_string)
    if match_count == 0:
        raise WorkspaceAccessError("未找到要替换的文本，请先读取文件并提供更精确的上下文")
    if match_count > 1 and not replace_all:
        raise WorkspaceAccessError(
            f"匹配到 {match_count} 处内容，替换存在歧义。请提供更精确的 old_string，或显式设置 replace_all=true"
        )

    updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)
    _atomic_write_text(target, updated)
    return {
        **_path_display(target, workspace_path),
        "changed": updated != original,
        "replace_all": replace_all,
        "matched_occurrences": match_count,
        "replaced_occurrences": match_count if replace_all else 1,
        "size_bytes": target.stat().st_size,
        "line_count": updated.count("\n") + (0 if not updated else 1),
        "sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
        "preview": _trim_output(updated, max_chars=2400),
        "diff_preview": _build_diff_preview(original, updated),
    }


def run_path_command(
    command: str,
    *,
    workspace_path: str | None = None,
    workdir: str | None = None,
    timeout_sec: int = 120,
    background: bool = False,
) -> dict:
    base_dir = workdir or workspace_path or str(Path(__file__).resolve().parents[2])
    root = resolve_path_input(
        base_dir,
        workspace_path=workspace_path,
        expect_dir=True,
        create_dir=True,
    )
    if background:
        return submit_workspace_command(str(root), command, timeout_sec=timeout_sec)
    result = run_workspace_command(str(root), command, timeout_sec=timeout_sec)
    result["path"] = str(root)
    result["relative_path"] = _path_relative_to_workspace(root, workspace_path)
    return result


def normalize_local_shell_command_parts(command_parts: object) -> list[str]:
    if isinstance(command_parts, (list, tuple)):
        return [str(item) for item in command_parts]
    value = str(command_parts or "").strip()
    return [value] if value else []


def local_shell_command_to_string(command_parts: object) -> str:
    parts = normalize_local_shell_command_parts(command_parts)
    if not parts:
        return ""
    if os.name == "nt":
        rendered: list[str] = []
        for part in parts:
            if _LOCAL_SHELL_SAFE_ARG_RE.fullmatch(part):
                rendered.append(part)
            else:
                escaped = part.replace("'", "''")
                rendered.append(f"'{escaped}'")
        return " ".join(rendered)
    return shlex.join(parts)


def build_local_shell_command(command_parts: object) -> str:
    display = local_shell_command_to_string(command_parts)
    if not display:
        return ""
    if os.name == "nt":
        return f"& {display}"
    return display


def run_local_shell_command(
    command_parts: object,
    *,
    workspace_path: str | None = None,
    workdir: str | None = None,
    timeout_sec: int = 120,
    env: dict[str, str] | None = None,
) -> dict:
    parts = normalize_local_shell_command_parts(command_parts)
    command_display = local_shell_command_to_string(parts)
    command_script = build_local_shell_command(parts)
    if not command_script:
        raise WorkspaceAccessError("命令为空")

    base_dir = workdir or workspace_path or str(Path(__file__).resolve().parents[2])
    root = resolve_path_input(
        base_dir,
        workspace_path=workspace_path,
        expect_dir=True,
        create_dir=True,
    )

    merged_env = os.environ.copy()
    if isinstance(env, dict):
        for key, value in env.items():
            merged_env[str(key)] = str(value)

    completed = subprocess.run(
        _build_shell_command(command_script),
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=max(1, timeout_sec),
        shell=False,
        env=merged_env,
    )
    stdout = _trim_output(completed.stdout)
    stderr = _trim_output(completed.stderr)
    return {
        "path": str(root),
        "relative_path": _path_relative_to_workspace(root, workspace_path),
        "command": command_display,
        "shell_command": _build_shell_command(command_script),
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "success": completed.returncode == 0,
    }


def inspect_workspace(
    workspace_path: str,
    *,
    max_depth: int = 2,
    max_entries: int | None = 120,
) -> dict:
    root = resolve_workspace_dir(workspace_path, create=False)
    tree_lines: list[str] = [str(root)]
    files: list[str] = []
    entry_count = 0
    entry_limit = max_entries if max_entries and max_entries > 0 else None

    if not root.exists():
        return {
            "workspace_path": str(root),
            "tree": "\n".join(tree_lines),
            "files": files,
            "total_entries": 0,
            "truncated": False,
        }

    def walk(current: Path, depth: int) -> None:
        nonlocal entry_count
        if depth > max_depth or (entry_limit is not None and entry_count >= entry_limit):
            return
        try:
            children = sorted(
                current.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        except OSError:
            return
        for child in children:
            if entry_limit is not None and entry_count >= entry_limit:
                return
            if child.name in DEFAULT_IGNORES:
                continue
            if child.name.startswith(".") and depth > 0:
                continue
            rel = child.relative_to(root).as_posix()
            prefix = "  " * depth + "- "
            if child.is_dir():
                tree_lines.append(f"{prefix}{child.name}/")
                entry_count += 1
                walk(child, depth + 1)
            else:
                tree_lines.append(f"{prefix}{child.name}")
                files.append(rel)
                entry_count += 1

    walk(root, 0)
    return {
        "workspace_path": str(root),
        "tree": "\n".join(tree_lines),
        "files": files if entry_limit is None else files[:entry_limit],
        "total_entries": entry_count,
        "truncated": bool(entry_limit is not None and entry_count >= entry_limit),
    }


def read_workspace_file(
    workspace_path: str,
    relative_path: str,
    *,
    max_chars: int = 12000,
) -> dict:
    target = resolve_workspace_file(workspace_path, relative_path, create_workspace=False)
    if not target.exists():
        raise WorkspaceAccessError(f"文件不存在: {relative_path}")
    if not target.is_file():
        raise WorkspaceAccessError(f"不是文件: {relative_path}")
    content = target.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > max_chars
    return {
        "workspace_path": str(resolve_workspace_dir(workspace_path, create=False)),
        "relative_path": relative_path,
        "content": content[:max_chars],
        "truncated": truncated,
        "size_bytes": target.stat().st_size,
    }


def write_workspace_file(
    workspace_path: str,
    relative_path: str,
    content: str,
    *,
    create_dirs: bool = True,
    overwrite: bool = True,
) -> dict:
    root = resolve_workspace_dir(workspace_path)
    target = resolve_workspace_file(workspace_path, relative_path)
    existed = target.exists()

    if existed and target.is_dir():
        try:
            next(target.iterdir())
        except StopIteration:
            target.rmdir()
            existed = False
        else:
            raise WorkspaceAccessError(f"目标是目录，无法写入文件: {relative_path}")
    if existed and not overwrite:
        raise WorkspaceAccessError(f"文件已存在，未允许覆盖: {relative_path}")

    parent = target.parent
    if not parent.exists():
        if not create_dirs:
            raise WorkspaceAccessError(f"父目录不存在: {parent}")
        parent.mkdir(parents=True, exist_ok=True)

    changed = True
    previous_size = 0
    if existed:
        previous_text = target.read_text(encoding="utf-8", errors="replace")
        changed = previous_text != content
        previous_size = target.stat().st_size

    _atomic_write_text(target, content)
    size_bytes = target.stat().st_size
    diff_preview = _build_diff_preview(previous_text, content) if existed and changed else ""

    return {
        "workspace_path": str(root),
        "relative_path": relative_path,
        "created": not existed,
        "overwritten": existed,
        "changed": changed,
        "size_bytes": size_bytes,
        "previous_size_bytes": previous_size,
        "line_count": content.count("\n") + (0 if not content else 1),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "preview": _trim_output(content, max_chars=2400),
        "diff_preview": diff_preview,
    }


def replace_workspace_text(
    workspace_path: str,
    relative_path: str,
    search_text: str,
    replace_text: str,
    *,
    replace_all: bool = False,
) -> dict:
    root = resolve_workspace_dir(workspace_path)
    target = resolve_workspace_file(workspace_path, relative_path)
    if not target.exists():
        raise WorkspaceAccessError(f"文件不存在: {relative_path}")
    if not target.is_file():
        raise WorkspaceAccessError(f"不是文件: {relative_path}")

    search_text = search_text or ""
    if not search_text:
        raise WorkspaceAccessError("search_text 不能为空")

    original = target.read_text(encoding="utf-8", errors="replace")
    match_count = original.count(search_text)
    if match_count == 0:
        raise WorkspaceAccessError("未找到要替换的文本，请先读取文件并提供更精确的上下文")
    if match_count > 1 and not replace_all:
        raise WorkspaceAccessError(
            f"匹配到 {match_count} 处内容，替换存在歧义。"
            " 请提供更精确的 search_text，或显式设置 replace_all=true"
        )

    if replace_all:
        updated = original.replace(search_text, replace_text)
        replaced_count = match_count
    else:
        updated = original.replace(search_text, replace_text, 1)
        replaced_count = 1

    changed = updated != original
    _atomic_write_text(target, updated)

    return {
        "workspace_path": str(root),
        "relative_path": relative_path,
        "changed": changed,
        "replace_all": replace_all,
        "matched_occurrences": match_count,
        "replaced_occurrences": replaced_count,
        "size_bytes": target.stat().st_size,
        "line_count": updated.count("\n") + (0 if not updated else 1),
        "sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
        "preview": _trim_output(updated, max_chars=2400),
        "diff_preview": _build_diff_preview(original, updated),
    }


def run_workspace_command(
    workspace_path: str,
    command: str,
    *,
    timeout_sec: int = 120,
) -> dict:
    root = resolve_workspace_dir(workspace_path)
    if not (command or "").strip():
        raise WorkspaceAccessError("命令为空")

    wrapped_command = _wrap_shell_command_for_cwd_capture(command)
    shell_cmd = _build_shell_command(wrapped_command)
    completed = subprocess.run(
        shell_cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=max(1, timeout_sec),
        shell=False,
    )
    raw_stdout, cwd = _extract_terminal_cwd(completed.stdout)
    stdout = _trim_output(raw_stdout)
    stderr = _trim_output(completed.stderr)
    result = {
        "workspace_path": str(root),
        "cwd": cwd or str(root),
        "command": command,
        "shell_command": shell_cmd,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "success": completed.returncode == 0,
    }
    if (
        os.name == "nt"
        and completed.returncode != 0
        and "scriptblock should only be specified as a value of the command parameter" in stderr.lower()
    ):
        result["error_code"] = "POWERSHELL_WRAPPER_PARSE"
        result["error_hint"] = (
            "PowerShell 外层解析了命令字符串。"
            "请优先改用 read/glob/grep 处理代码查询，或把命令改成更简单的单行脚本。"
        )
    return result


def submit_workspace_command(
    workspace_path: str,
    command: str,
    *,
    timeout_sec: int = 3600,
) -> dict:
    root = resolve_workspace_dir(workspace_path)
    title = f"工作区命令: {command[:48]}"

    def _runner(progress_callback=None):
        if progress_callback:
            progress_callback("正在启动命令...", 5, 100)
        result = run_workspace_command(
            str(root),
            command,
            timeout_sec=timeout_sec,
        )
        if progress_callback:
            progress_callback("命令已执行完成", 95, 100)
        return result

    task_id = global_tracker.submit(
        "workspace_command",
        title,
        _runner,
        total=100,
    )
    return {
        "task_id": task_id,
        "workspace_path": str(root),
        "command": command,
        "status": "running",
    }


def get_task_status(task_id: str) -> dict | None:
    task = global_tracker.get_task(task_id)
    if not task:
        return None
    result = global_tracker.get_result(task_id)
    if result is not None:
        task = {**task, "result": result}
    return task


def _trim_output(text: str, max_chars: int = 12000) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...[truncated]"


def _extract_terminal_cwd(text: str) -> tuple[str, str | None]:
    raw = text or ""
    if TERMINAL_CWD_MARKER not in raw:
        return raw, None

    lines = raw.splitlines()
    marker_index = -1
    cwd_value: str | None = None
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx].strip()
        if line.startswith(TERMINAL_CWD_MARKER):
            marker_index = idx
            cwd_value = line[len(TERMINAL_CWD_MARKER):].strip() or None
            break

    if marker_index < 0:
        return raw, None

    cleaned = lines[:marker_index] + lines[marker_index + 1:]
    return "\n".join(cleaned), cwd_value


def _build_diff_preview(before: str, after: str, *, max_lines: int = 220, max_chars: int = 6000) -> str:
    diff_lines = list(
        unified_diff(
            (before or "").splitlines(),
            (after or "").splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if not diff_lines:
        return ""
    preview = "\n".join(diff_lines[:max_lines])
    return _trim_output(preview, max_chars=max_chars)


def _build_shell_command(command: str) -> list[str]:
    if os.name == "nt":
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        return ["pwsh", "-NoLogo", "-EncodedCommand", encoded]
    return ["/bin/bash", "-lc", command]


def _wrap_shell_command_for_cwd_capture(command: str) -> str:
    normalized = (command or "").strip()
    if os.name == "nt":
        return (
            f"{normalized}; "
            "$__researchos_success = $?; "
            "$__researchos_exit = if ($global:LASTEXITCODE -ne $null) { [int]$global:LASTEXITCODE } "
            "elseif ($__researchos_success) { 0 } else { 1 }; "
            f"Write-Output ('{TERMINAL_CWD_MARKER}' + (Get-Location).Path); "
            "exit $__researchos_exit"
        )
    return (
        f"{normalized}; "
        "__researchos_exit=$?; "
        f"printf '%s%s\\n' '{TERMINAL_CWD_MARKER}' \"$PWD\"; "
        "exit $__researchos_exit"
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".research-os-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
