from __future__ import annotations

from pathlib import Path

from packages.agent.tools.skill_registry import (
    get_local_skill_detail,
    list_local_skills as scan_local_skills,
    list_skill_scan_roots,
)
from packages.agent.tools.tool_runtime import AgentToolContext, ToolResult


def _active_skill_ids(context: AgentToolContext | None) -> list[str]:
    runtime_options = getattr(context, "runtime_options", None) if context is not None else None
    return [
        str(item).strip()
        for item in (getattr(runtime_options, "active_skill_ids", None) or [])
        if str(item).strip()
    ]


def _accessible_skills(context: AgentToolContext | None) -> list[dict]:
    active_ids = _active_skill_ids(context)
    if not active_ids:
        return []
    skill_by_id = {
        str(item.get("id") or "").strip(): item
        for item in scan_local_skills()
        if str(item.get("id") or "").strip()
    }
    return [skill_by_id[skill_id] for skill_id in active_ids if skill_id in skill_by_id]


def _resolve_accessible_skill_detail(
    skill_ref: str,
    *,
    max_chars: int,
    context: AgentToolContext | None,
) -> dict | None:
    reference = str(skill_ref or "").strip()
    if not reference:
        return None

    items = _accessible_skills(context)
    if not items:
        return get_local_skill_detail(reference, max_chars=max_chars)
    target = reference.lower()
    exact_match: dict | None = None
    fuzzy_matches: list[dict] = []

    for item in items:
        candidates = {
            str(item.get("id") or "").strip().lower(),
            str(item.get("name") or "").strip().lower(),
            str(item.get("relative_path") or "").strip().lower(),
            str(item.get("path") or "").strip().lower(),
        }
        if target in candidates:
            exact_match = item
            break
        haystack = " ".join(candidates)
        if target and target in haystack:
            fuzzy_matches.append(item)

    match = exact_match or (fuzzy_matches[0] if fuzzy_matches else None)
    if match is None:
        return None

    entry_file = Path(str(match.get("entry_file") or "")).expanduser()
    text = entry_file.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {
        **match,
        "content": text[:max_chars],
        "truncated": truncated,
        "match_count": 1 if exact_match else len(fuzzy_matches),
        "matched_ids": [item["id"] for item in fuzzy_matches[:8]] if not exact_match else [match["id"]],
    }


def _list_local_skills(context: AgentToolContext | None = None) -> ToolResult:
    items = _accessible_skills(context)
    return ToolResult(
        success=True,
        data={
            "items": items,
            "count": len(items),
            "roots": list_skill_scan_roots(),
        },
        summary=f"当前共发现 {len(items)} 个本地 skills",
    )


def _load_skill(
    name: str,
    max_chars: int = 12000,
    context: AgentToolContext | None = None,
) -> ToolResult:
    item = _resolve_accessible_skill_detail(
        name,
        max_chars=max(2000, min(max_chars, 40000)),
        context=context,
    )
    if item is None:
        available = ", ".join(
            str(entry.get("name") or "")
            for entry in _accessible_skills(context)[:20]
            if str(entry.get("name") or "").strip()
        )
        suffix = f"。可用 skills：{available}" if available else ""
        return ToolResult(success=False, summary=f"未找到 skill：{name}{suffix}")

    skill_dir = Path(str(item.get("path") or "")).expanduser()
    sampled_files: list[str] = []
    if skill_dir.exists():
        for child in sorted(skill_dir.rglob("*"), key=lambda path: str(path).lower()):
            if len(sampled_files) >= 10:
                break
            if not child.is_file():
                continue
            if child.name.lower() == "skill.md":
                continue
            sampled_files.append(str(child.resolve()))

    output_lines = [
        f'<skill_content name="{item.get("name")}">',
        f"# Skill: {item.get('name')}",
        "",
        str(item.get("content") or "").strip(),
        "",
        f"Base directory for this skill: {skill_dir.resolve().as_uri() if skill_dir.exists() else str(skill_dir)}",
        "Relative paths in this skill (e.g., scripts/, references/, assets/) are relative to this base directory.",
        "Note: file list is sampled.",
        "",
        "<skill_files>",
        *[f"<file>{filepath}</file>" for filepath in sampled_files],
        "</skill_files>",
        "</skill_content>",
    ]
    return ToolResult(
        success=True,
        data={
            **item,
            "dir": str(skill_dir.resolve()) if skill_dir.exists() else str(skill_dir),
            "files": sampled_files,
            "output": "\n".join(output_lines),
        },
        summary=f"已加载 skill：{item.get('name')}",
    )


def _read_local_skill(
    skill_ref: str,
    max_chars: int = 12000,
    context: AgentToolContext | None = None,
) -> ToolResult:
    item = _resolve_accessible_skill_detail(
        skill_ref,
        max_chars=max(2000, min(max_chars, 40000)),
        context=context,
    )
    if item is None:
        return ToolResult(success=False, summary=f"未找到 skill：{skill_ref}")
    summary = f"已读取 skill：{item.get('name')}"
    if int(item.get("match_count") or 0) > 1:
        summary += f"（存在 {item.get('match_count')} 个匹配，已返回最接近的一项）"
    return ToolResult(success=True, data=item, summary=summary)

