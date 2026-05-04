from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.agent.workspace.workspace_remote import build_remote_overview, remote_read_file
from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry

_AUDIT_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
}
_AUDIT_TEXT_SUFFIXES = {
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".md",
    ".tex",
    ".txt",
    ".log",
    ".out",
    ".err",
    ".sh",
}
_AUDIT_TRACKER_NAMES = {
    "EXPERIMENT_TRACKER.md",
    "EXPERIMENT_LOG.md",
    "NARRATIVE_REPORT.md",
    "PAPER_PLAN.md",
    "AUTO_REVIEW.md",
    "IDEA_REPORT.md",
}
_AUDIT_CLAIM_FILE_NAMES = {
    "NARRATIVE_REPORT.md",
    "PAPER_PLAN.md",
    "AUTO_REVIEW.md",
    "IDEA_REPORT.md",
}
_AUDIT_EVAL_MARKERS = ("eval", "metric", "benchmark", "test")
_AUDIT_RESULT_DIR_MARKERS = (
    "result",
    "results",
    "output",
    "outputs",
    "log",
    "logs",
    "metric",
    "metrics",
    "report",
    "reports",
)
_AUDIT_CONFIG_MARKERS = ("config", "configs", "conf", "setting", "settings")
_AUDIT_CATEGORY_LIMITS = {
    "evaluation_scripts": 8,
    "result_files": 10,
    "experiment_trackers": 6,
    "paper_claims": 8,
    "config_files": 8,
}


def _should_skip_audit_relative_path(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not normalized:
        return True
    parts = [part for part in normalized.split("/") if part]
    return any(part in _AUDIT_SKIP_DIR_NAMES for part in parts)


def _classify_audit_relative_path(relative_path: str) -> str | None:
    normalized = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not normalized or _should_skip_audit_relative_path(normalized):
        return None
    path_obj = Path(normalized)
    filename = path_obj.name
    suffix = path_obj.suffix.lower()
    lowered = normalized.lower()
    parts = lowered.split("/")
    stem = path_obj.stem.lower()

    if suffix not in _AUDIT_TEXT_SUFFIXES:
        return None
    if filename in _AUDIT_TRACKER_NAMES or lowered.endswith("/reports/experiment-summary.md"):
        return "experiment_trackers"
    if "/paper/sections/" in lowered or filename in _AUDIT_CLAIM_FILE_NAMES:
        return "paper_claims"
    if suffix in {".yaml", ".yml", ".toml"}:
        return "config_files"
    if suffix == ".json" and any(
        marker in stem or marker in parts for marker in _AUDIT_CONFIG_MARKERS
    ):
        return "config_files"
    if suffix == ".py" and any(
        marker in stem or marker in lowered for marker in _AUDIT_EVAL_MARKERS
    ):
        return "evaluation_scripts"
    if suffix in {".json", ".csv", ".md", ".txt", ".log", ".out", ".err"} and any(
        marker in lowered for marker in _AUDIT_RESULT_DIR_MARKERS
    ):
        return "result_files"
    return None


def _collect_local_audit_relative_paths(workspace_path: str) -> list[str]:
    root = Path(workspace_path).expanduser()
    if not root.exists() or not root.is_dir():
        return []
    items: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _classify_audit_relative_path(relative):
            items.append(relative)
    return items


def _collect_remote_audit_relative_paths(
    workspace_path: str,
    workspace_server_id: str,
) -> list[str]:
    server_entry = get_workspace_server_entry(workspace_server_id)
    overview = build_remote_overview(server_entry, workspace_path, depth=6, max_entries=400)
    return [
        str(relative_path)
        for relative_path in (overview.get("files") or [])
        if _classify_audit_relative_path(str(relative_path))
    ]


def _select_experiment_audit_files(relative_paths: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {
        "evaluation_scripts": [],
        "result_files": [],
        "experiment_trackers": [],
        "paper_claims": [],
        "config_files": [],
    }
    for relative_path in relative_paths:
        category = _classify_audit_relative_path(relative_path)
        if not category or category not in grouped:
            continue
        bucket = grouped[category]
        if len(bucket) >= _AUDIT_CATEGORY_LIMITS[category]:
            continue
        bucket.append(relative_path)
    return grouped


def _read_local_audit_file(absolute_path: Path, *, max_chars: int) -> dict[str, Any]:
    content = absolute_path.read_text(encoding="utf-8", errors="replace")
    return {
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
        "size_bytes": absolute_path.stat().st_size,
    }


def _read_audit_file(
    context: Any,
    *,
    workspace_path: str,
    relative_path: str,
    max_chars: int = 9000,
) -> dict[str, Any]:
    if context.run.workspace_server_id:
        server_entry = get_workspace_server_entry(context.run.workspace_server_id)
        payload = remote_read_file(
            server_entry,
            workspace_path,
            relative_path,
            max_chars=max_chars,
        )
        return {
            "content": str(payload.get("content") or ""),
            "truncated": bool(payload.get("truncated")),
            "size_bytes": int(payload.get("size_bytes") or 0),
        }
    absolute_path = Path(workspace_path).expanduser() / Path(relative_path)
    return _read_local_audit_file(absolute_path, max_chars=max_chars)


def _line_numbered_excerpt(text: str, *, max_lines: int = 220) -> str:
    lines = str(text or "").splitlines()
    visible = lines[:max_lines]
    numbered = "\n".join(f"{index + 1:04d}: {line}" for index, line in enumerate(visible))
    if len(lines) > max_lines:
        numbered += f"\n... [truncated {len(lines) - max_lines} more lines]"
    return numbered or "0001: "


def _experiment_audit_file_entries(
    context: Any,
    *,
    workspace_path: str,
    grouped_paths: dict[str, list[str]],
) -> list[dict[str, Any]]:
    order = [
        "evaluation_scripts",
        "result_files",
        "experiment_trackers",
        "paper_claims",
        "config_files",
    ]
    entries: list[dict[str, Any]] = []
    for category in order:
        for relative_path in grouped_paths.get(category) or []:
            payload = _read_audit_file(
                context,
                workspace_path=workspace_path,
                relative_path=relative_path,
                max_chars=9000,
            )
            entries.append(
                {
                    "category": category,
                    "relative_path": relative_path,
                    "size_bytes": int(payload.get("size_bytes") or 0),
                    "truncated": bool(payload.get("truncated")),
                    "content": str(payload.get("content") or ""),
                    "numbered_content": _line_numbered_excerpt(str(payload.get("content") or "")),
                }
            )
    return entries


def _build_experiment_audit_inventory_markdown(
    *,
    workspace_path: str,
    workspace_server_id: str | None,
    grouped_paths: dict[str, list[str]],
) -> str:
    label_map = {
        "evaluation_scripts": "Evaluation Scripts",
        "result_files": "Result Files",
        "experiment_trackers": "Experiment Trackers",
        "paper_claims": "Paper Claims",
        "config_files": "Config Files",
    }
    lines = [
        "# EXPERIMENT_AUDIT_INVENTORY",
        "",
        f"- Workspace: `{workspace_path}`",
        f"- Server: `{workspace_server_id or 'local'}`",
    ]
    total_files = 0
    for category, label in label_map.items():
        items = grouped_paths.get(category) or []
        total_files += len(items)
        lines.extend(["", f"## {label}", f"- Count: `{len(items)}`"])
        for relative_path in items:
            lines.append(f"- `{relative_path}`")
    lines.extend(["", f"- Total Files: `{total_files}`"])
    return "\n".join(lines).strip()


def _build_experiment_audit_prompt_bundle(
    *,
    inventory_markdown: str,
    file_entries: list[dict[str, Any]],
    max_chars: int = 28000,
) -> str:
    lines = [inventory_markdown, "", "## Raw File Snapshots"]
    current_length = sum(len(line) + 1 for line in lines)
    omitted = 0
    for entry in file_entries:
        section = "\n".join(
            [
                "",
                f"### [{entry['category']}] {entry['relative_path']}",
                f"- size_bytes: `{entry['size_bytes']}`",
                f"- truncated: `{entry['truncated']}`",
                "```text",
                str(entry.get("numbered_content") or ""),
                "```",
            ]
        )
        if current_length + len(section) > max_chars and current_length > len(inventory_markdown):
            omitted += 1
            continue
        lines.append(section)
        current_length += len(section)
    if omitted:
        lines.extend(["", f"> 其余 {omitted} 个文件因上下文预算被省略，但已计入 inventory。"])
    return "\n".join(lines).strip()


def _collect_experiment_audit_bundle(
    context: Any,
    *,
    workspace_path: str,
) -> dict[str, Any]:
    if context.run.workspace_server_id:
        relative_paths = _collect_remote_audit_relative_paths(
            workspace_path, context.run.workspace_server_id
        )
    else:
        relative_paths = _collect_local_audit_relative_paths(workspace_path)
    grouped_paths = _select_experiment_audit_files(relative_paths)
    file_entries = _experiment_audit_file_entries(
        context,
        workspace_path=workspace_path,
        grouped_paths=grouped_paths,
    )
    inventory_markdown = _build_experiment_audit_inventory_markdown(
        workspace_path=workspace_path,
        workspace_server_id=context.run.workspace_server_id,
        grouped_paths=grouped_paths,
    )
    total_files = sum(len(items) for items in grouped_paths.values())
    return {
        "workspace_path": workspace_path,
        "workspace_server_id": context.run.workspace_server_id,
        "inventory": grouped_paths,
        "inventory_markdown": inventory_markdown,
        "file_entries": file_entries,
        "prompt_bundle": _build_experiment_audit_prompt_bundle(
            inventory_markdown=inventory_markdown,
            file_entries=file_entries,
        ),
        "summary": (
            f"已收集 {total_files} 个实验审计候选文件："
            f"{len(grouped_paths.get('evaluation_scripts') or [])} 个评测脚本、"
            f"{len(grouped_paths.get('result_files') or [])} 个结果文件、"
            f"{len(grouped_paths.get('experiment_trackers') or [])} 个实验跟踪文件。"
        ),
    }
