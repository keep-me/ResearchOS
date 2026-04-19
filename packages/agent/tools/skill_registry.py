from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SKILL_ROOTS = (
    ("codex", Path.home() / ".codex" / "skills"),
    ("agents", Path.home() / ".agents" / "skills"),
    ("project", PROJECT_ROOT / ".opencode" / "skills"),
    ("project", PROJECT_ROOT / ".claude" / "skills"),
    ("project", PROJECT_ROOT / ".agents" / "skills"),
    ("project", PROJECT_ROOT / "skills"),
)

FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n?", re.DOTALL)


def list_local_skills() -> list[dict]:
    items: list[dict] = []
    seen_paths: set[str] = set()

    for source, root in SKILL_ROOTS:
        if not root.exists():
            continue
        for skill_file in _iter_skill_files(root):
            skill_dir = skill_file.parent
            key = str(skill_dir.resolve()).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            items.append(_build_skill_item(source, root, skill_file))

    items.sort(key=lambda item: (item["source"], item["system"], item["name"].lower()))
    return items


def list_skill_scan_roots() -> list[dict]:
    roots: list[dict] = []
    for source, root in SKILL_ROOTS:
        resolved = root.expanduser().resolve()
        roots.append(
            {
                "source": source,
                "path": str(resolved),
                "exists": resolved.exists(),
            }
        )
    return roots


def get_local_skill_detail(skill_ref: str, max_chars: int = 12000) -> dict | None:
    reference = str(skill_ref or "").strip()
    if not reference:
        return None

    items = list_local_skills()
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


def _iter_skill_files(root: Path):
    candidates: list[Path] = []
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if item.name.lower() != "skill.md":
            continue
        candidates.append(item)
    candidates.sort(key=lambda path: str(path).lower())
    return candidates


def _build_skill_item(source: str, root: Path, skill_file: Path) -> dict:
    text = skill_file.read_text(encoding="utf-8", errors="replace")
    meta = _parse_frontmatter(text)
    relative_dir = skill_file.parent.relative_to(root).as_posix()
    name = str(meta.get("name") or skill_file.parent.name).strip() or skill_file.parent.name
    description = str(meta.get("description") or _extract_summary(text) or "").strip()
    system = ".system" in skill_file.parts
    return {
        "id": f"{source}:{relative_dir}",
        "name": name,
        "description": description,
        "path": str(skill_file.parent.resolve()),
        "entry_file": str(skill_file.resolve()),
        "source": source,
        "relative_path": relative_dir,
        "system": system,
    }


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        values[key.strip()] = raw_value.strip().strip("'\"")
    return values


def _extract_summary(text: str) -> str:
    body = FRONTMATTER_RE.sub("", text, count=1)
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-") or line.startswith("```"):
            continue
        return line
    return ""
