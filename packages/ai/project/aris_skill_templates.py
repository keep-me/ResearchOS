from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_REFERENCE_ROOT = Path("D:/Desktop/Auto-claude-code-research-in-sleep-main/skills")


def aris_reference_root() -> Path:
    configured = str(os.getenv("RESEARCHOS_ARIS_REFERENCE_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _DEFAULT_REFERENCE_ROOT


def clear_aris_skill_template_cache() -> None:
    _load_aris_skill_template.cache_clear()


def load_aris_skill_template(skill_id: str) -> dict[str, Any] | None:
    normalized = str(skill_id or "").strip()
    if not normalized:
        return None
    return _load_aris_skill_template(normalized)


@lru_cache(maxsize=64)
def _load_aris_skill_template(skill_id: str) -> dict[str, Any] | None:
    skill_path = aris_reference_root() / skill_id / "SKILL.md"
    if not skill_path.exists():
        return None
    text = skill_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    return {
        "skill_id": skill_id,
        "path": str(skill_path),
        "frontmatter": frontmatter,
        "body": body.strip(),
        "full_text": text.strip(),
    }


def render_aris_skill_reference(skill_id: str) -> str:
    template = load_aris_skill_template(skill_id)
    if not template:
        return ""
    frontmatter = dict(template.get("frontmatter") or {})
    lines = [f"Reference skill: /{skill_id}"]
    description = str(frontmatter.get("description") or "").strip()
    argument_hint = str(frontmatter.get("argument-hint") or "").strip()
    allowed_tools = str(frontmatter.get("allowed-tools") or "").strip()
    if description:
        lines.append(f"Description: {description}")
    if argument_hint:
        lines.append(f"Argument hint: {argument_hint}")
    if allowed_tools:
        lines.append(f"Allowed tools: {allowed_tools}")
    body = str(template.get("body") or "").strip()
    if body:
        lines.extend(["", body])
    return "\n".join(lines).strip()


def render_aris_skill_bundle(skill_ids: list[str]) -> str:
    rendered: list[str] = []
    seen: set[str] = set()
    for skill_id in skill_ids:
        normalized = str(skill_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        text = render_aris_skill_reference(normalized)
        if text:
            rendered.append(text)
    return "\n\n".join(rendered).strip()


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    frontmatter_lines: list[str] = []
    body_start = 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = index + 1
            break
        frontmatter_lines.append(line)
    if body_start == 0:
        return {}, text
    frontmatter = _parse_frontmatter_lines(frontmatter_lines)
    body = "\n".join(lines[body_start:])
    return frontmatter, body


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        normalized_value = value.strip().strip('"').strip("'")
        if normalized_key:
            payload[normalized_key] = normalized_value
    return payload
