"""OpenCode-like workspace snapshots backed by a dedicated git repository."""

from __future__ import annotations

import hashlib
import subprocess
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from typing import Any

from packages.config import get_settings


_IGNORED_FOLDERS = {
    "node_modules",
    "bower_components",
    ".pnpm-store",
    "vendor",
    ".npm",
    ".venv",
    "dist",
    "build",
    "out",
    ".next",
    "target",
    "bin",
    "obj",
    ".git",
    ".svn",
    ".hg",
    ".vscode",
    ".idea",
    ".turbo",
    ".output",
    "desktop",
    ".sst",
    ".cache",
    ".sandbox-home",
    ".sandbox-tmp",
    ".webkit-cache",
    "__pycache__",
    ".pytest_cache",
    "mypy_cache",
    ".history",
    ".gradle",
    "artifacts",
    "backups",
    "data",
    "deploy",
    "logs",
    "reference",
}
_IGNORED_PATTERNS = (
    "**/*.swp",
    "**/*.swo",
    "**/*.pyc",
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/logs/**",
    "**/tmp/**",
    "**/temp/**",
    "**/.tmp*/**",
    "**/*.log",
    "**/coverage/**",
    "**/.nyc_output/**",
)


def _relative_snapshot_path(root: Path, file_path: str | Path) -> str | None:
    try:
        relative = Path(file_path).expanduser().resolve().relative_to(root)
    except ValueError:
        return None
    return relative.as_posix()


def _snapshot_path_is_ignored(root: Path, file_path: str | Path) -> bool:
    relative = _relative_snapshot_path(root, file_path)
    if not relative:
        return True
    parts = [part for part in PurePosixPath(relative).parts if part not in {"", "."}]
    if any(part in _IGNORED_FOLDERS for part in parts):
        return True
    normalized = PurePosixPath(relative)
    return any(normalized.match(pattern) for pattern in _IGNORED_PATTERNS)


def _filter_snapshot_files(root: Path, file_paths: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for file_path in file_paths:
        absolute = str(Path(file_path).expanduser().resolve())
        if absolute in seen or _snapshot_path_is_ignored(root, absolute):
            continue
        seen.add(absolute)
        result.append(absolute)
    return result


def _snapshot_repo_root(workspace_root: str) -> Path:
    base = get_settings().brief_output_root.resolve().parent / "snapshots"
    workspace = str(Path(workspace_root).expanduser().resolve())
    digest = hashlib.sha1(workspace.encode("utf-8")).hexdigest()[:16]
    return base / digest


def _git_args(repo_dir: Path, workspace_root: Path, *args: str) -> list[str]:
    return [
        "git",
        "--git-dir",
        str(repo_dir),
        "--work-tree",
        str(workspace_root),
        *args,
    ]


def _run_git(
    repo_dir: Path,
    workspace_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _git_args(repo_dir, workspace_root, *args),
        cwd=str(workspace_root),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _ensure_repo(workspace_root: Path) -> Path:
    repo_dir = _snapshot_repo_root(str(workspace_root))
    repo_dir.mkdir(parents=True, exist_ok=True)
    head = repo_dir / "HEAD"
    if not head.exists():
        env = {
            **dict(__import__("os").environ),
            "GIT_DIR": str(repo_dir),
            "GIT_WORK_TREE": str(workspace_root),
        }
        subprocess.run(
            ["git", "init"],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        for key, value in (
            ("core.autocrlf", "false"),
            ("core.longpaths", "true"),
            ("core.symlinks", "true"),
            ("core.fsmonitor", "false"),
            ("core.quotepath", "false"),
        ):
            _run_git(repo_dir, workspace_root, "config", key, value, check=False)
    return repo_dir


def _ensure_local_workspace(workspace_root: str | None) -> Path | None:
    raw = str(workspace_root or "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None
    return root


def track(workspace_root: str | None) -> str | None:
    root = _ensure_local_workspace(workspace_root)
    if root is None:
        return None
    repo_dir = _ensure_repo(root)
    _run_git(repo_dir, root, "add", "-A", "--", ".", check=False)
    result = _run_git(repo_dir, root, "write-tree")
    value = result.stdout.strip()
    return value or None


def patch(workspace_root: str | None, snapshot_hash: str) -> dict[str, Any]:
    root = _ensure_local_workspace(workspace_root)
    if root is None:
        return {"hash": snapshot_hash, "files": []}
    repo_dir = _ensure_repo(root)
    _run_git(repo_dir, root, "add", "-A", "--", ".", check=False)
    result = _run_git(
        repo_dir,
        root,
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.longpaths=true",
        "-c",
        "core.symlinks=true",
        "-c",
        "core.quotepath=false",
        "diff",
        "--no-ext-diff",
        "--name-only",
        snapshot_hash,
        "--",
        ".",
        check=False,
    )
    if result.returncode != 0:
        return {"hash": snapshot_hash, "files": []}
    files = [
        str((root / line.strip()).resolve())
        for line in result.stdout.splitlines()
        if line.strip()
    ]
    return {"hash": snapshot_hash, "files": _filter_snapshot_files(root, files)}


def restore(workspace_root: str | None, snapshot_hash: str) -> None:
    root = _ensure_local_workspace(workspace_root)
    if root is None:
        return
    repo_dir = _ensure_repo(root)
    _run_git(
        repo_dir,
        root,
        "-c",
        "core.longpaths=true",
        "-c",
        "core.symlinks=true",
        "read-tree",
        snapshot_hash,
        check=False,
    )
    _run_git(
        repo_dir,
        root,
        "-c",
        "core.longpaths=true",
        "-c",
        "core.symlinks=true",
        "checkout-index",
        "-a",
        "-f",
        check=False,
    )


def revert(workspace_root: str | None, patches: list[dict[str, Any]]) -> None:
    root = _ensure_local_workspace(workspace_root)
    if root is None:
        return
    repo_dir = _ensure_repo(root)
    seen: set[str] = set()
    for item in patches:
        snapshot_hash = str(item.get("hash") or "").strip()
        if not snapshot_hash:
            continue
        for file_path in item.get("files") or []:
            absolute = Path(str(file_path)).expanduser().resolve()
            if _snapshot_path_is_ignored(root, absolute):
                continue
            key = str(absolute)
            if key in seen:
                continue
            relative = absolute.relative_to(root).as_posix()
            result = _run_git(
                repo_dir,
                root,
                "-c",
                "core.longpaths=true",
                "-c",
                "core.symlinks=true",
                "checkout",
                snapshot_hash,
                "--",
                relative,
                check=False,
            )
            if result.returncode != 0:
                exists = _run_git(
                    repo_dir,
                    root,
                    "-c",
                    "core.longpaths=true",
                    "-c",
                    "core.symlinks=true",
                    "ls-tree",
                    snapshot_hash,
                    "--",
                    relative,
                    check=False,
                )
                if not exists.stdout.strip() and absolute.exists() and absolute.is_file():
                    absolute.unlink()
            seen.add(key)


def diff(workspace_root: str | None, snapshot_hash: str) -> str:
    root = _ensure_local_workspace(workspace_root)
    if root is None:
        return ""
    repo_dir = _ensure_repo(root)
    _run_git(repo_dir, root, "add", "-A", "--", ".", check=False)
    result = _run_git(
        repo_dir,
        root,
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.longpaths=true",
        "-c",
        "core.symlinks=true",
        "-c",
        "core.quotepath=false",
        "diff",
        "--no-ext-diff",
        snapshot_hash,
        "--",
        ".",
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _diff_counts(before: str, after: str) -> tuple[int, int]:
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


def diff_current_full(
    workspace_root: str | None,
    snapshot_hash: str,
    *,
    files: list[str] | None = None,
) -> list[dict[str, Any]]:
    root = _ensure_local_workspace(workspace_root)
    if root is None:
        return []
    repo_dir = _ensure_repo(root)
    candidates = files or patch(str(root), snapshot_hash).get("files") or []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file_path in candidates:
        absolute = Path(str(file_path)).expanduser().resolve()
        if _snapshot_path_is_ignored(root, absolute):
            continue
        key = str(absolute)
        if key in seen:
            continue
        seen.add(key)
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError:
            continue

        previous = _run_git(
            repo_dir,
            root,
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.longpaths=true",
            "-c",
            "core.symlinks=true",
            "show",
            f"{snapshot_hash}:{relative}",
            check=False,
        )
        exists_before = previous.returncode == 0
        before = previous.stdout if exists_before else ""
        exists_after = absolute.exists() and absolute.is_file()
        after = absolute.read_text(encoding="utf-8", errors="replace") if exists_after else ""
        if exists_before == exists_after and before == after:
            continue
        additions, deletions = _diff_counts(before, after)
        status = "modified"
        if not exists_before and exists_after:
            status = "added"
        elif exists_before and not exists_after:
            status = "deleted"
        result.append(
            {
                "file": str(absolute),
                "before": before,
                "after": after,
                "additions": additions,
                "deletions": deletions,
                "status": status,
            }
        )
    return result


def diff_full(workspace_root: str | None, from_hash: str, to_hash: str) -> list[dict[str, Any]]:
    root = _ensure_local_workspace(workspace_root)
    if root is None:
        return []
    repo_dir = _ensure_repo(root)
    result: list[dict[str, Any]] = []

    statuses = _run_git(
        repo_dir,
        root,
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.longpaths=true",
        "-c",
        "core.symlinks=true",
        "-c",
        "core.quotepath=false",
        "diff",
        "--no-ext-diff",
        "--name-status",
        "--no-renames",
        from_hash,
        to_hash,
        "--",
        ".",
        check=False,
    )
    kinds: dict[str, str] = {}
    for line in statuses.stdout.splitlines():
        if not line.strip():
            continue
        code, _, file_name = line.partition("\t")
        if not code or not file_name:
            continue
        if _snapshot_path_is_ignored(root, root / file_name):
            continue
        kinds[file_name] = "added" if code.startswith("A") else "deleted" if code.startswith("D") else "modified"

    numstat = _run_git(
        repo_dir,
        root,
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.longpaths=true",
        "-c",
        "core.symlinks=true",
        "-c",
        "core.quotepath=false",
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--numstat",
        from_hash,
        to_hash,
        "--",
        ".",
        check=False,
    )
    for line in numstat.stdout.splitlines():
        if not line.strip():
            continue
        additions, deletions, file_name = line.split("\t", 2)
        if _snapshot_path_is_ignored(root, root / file_name):
            continue
        binary = additions == "-" and deletions == "-"
        before = ""
        after = ""
        if not binary:
            show_before = _run_git(
                repo_dir,
                root,
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.longpaths=true",
                "-c",
                "core.symlinks=true",
                "show",
                f"{from_hash}:{file_name}",
                check=False,
            )
            if show_before.returncode == 0:
                before = show_before.stdout
            show_after = _run_git(
                repo_dir,
                root,
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.longpaths=true",
                "-c",
                "core.symlinks=true",
                "show",
                f"{to_hash}:{file_name}",
                check=False,
            )
            if show_after.returncode == 0:
                after = show_after.stdout
        result.append(
            {
                "file": file_name,
                "before": before,
                "after": after,
                "additions": 0 if binary else int(additions),
                "deletions": 0 if binary else int(deletions),
                "status": kinds.get(file_name, "modified"),
            }
        )
    return result
