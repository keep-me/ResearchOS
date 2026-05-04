from __future__ import annotations

import json
import re
from typing import Any

_INLINE_REWRITE_PATTERNS = (
    (
        re.compile(r"^我先按 ARIS .*?landscape summary。", re.IGNORECASE | re.MULTILINE),
        "本节基于项目资料、Web 与 arXiv 检索整理文献地形勘测结果。",
    ),
    (
        re.compile(r"^我将按 ARIS .*?如有必要", re.IGNORECASE | re.MULTILINE),
        "本节汇总外部评审的主要意见与修复建议。",
    ),
)

_INLINE_TRACE_PATTERNS = (
    re.compile(r"to=mcp\s+[^\n\r#]{0,800}", re.IGNORECASE),
    re.compile(r"tool code syntax error:[^\n\r#]{0,400}", re.IGNORECASE),
    re.compile(r"Searching local paper library and arXiv tooling\.?", re.IGNORECASE),
    re.compile(r"继续追问 reviewer[^\n\r#]{0,500}", re.IGNORECASE),
    re.compile(r"再追问一轮[^\n\r#]{0,500}", re.IGNORECASE),
    re.compile(r"如果你要[^\n\r#]{0,600}", re.IGNORECASE),
)

_INTERACTIVE_LINE_PATTERNS = (
    re.compile(r"这是否符合你的理解", re.IGNORECASE),
    re.compile(r"如果你不回复", re.IGNORECASE),
    re.compile(r"要不要我在下一阶段", re.IGNORECASE),
    re.compile(r"默认按 top-ranked direction", re.IGNORECASE),
)

_CHECKPOINT_SECTION_PATTERN = re.compile(r"(?ims)^#{1,6}\s+Checkpoint\b.*?(?=^#{1,6}\s|\Z)")

_MARKDOWN_KEYS = {
    "workflow_output_markdown",
    "markdown",
}


def sanitize_project_markdown(markdown: str | None) -> str:
    text = str(markdown or "").replace("\r\n", "\n").strip()
    if not text:
        return ""

    for pattern, replacement in _INLINE_REWRITE_PATTERNS:
        text = pattern.sub(replacement, text)

    for pattern in _INLINE_TRACE_PATTERNS:
        text = pattern.sub("", text)

    text = _CHECKPOINT_SECTION_PATTERN.sub("", text)
    text = re.sub(r"([^#\n])(?=#{1,6}\s)", r"\1\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)

    cleaned_lines: list[str] = []
    last_blank = True
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if any(pattern.search(line) for pattern in _INTERACTIVE_LINE_PATTERNS):
            continue
        if not line.strip():
            if not last_blank:
                cleaned_lines.append("")
            last_blank = True
            continue
        cleaned_lines.append(line)
        last_blank = False

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def sanitize_project_run_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    return _sanitize_value(metadata, parent_key=None)


def sanitize_project_artifact_preview_content(
    relative_path: str | None, content: str | None
) -> str:
    text = str(content or "")
    if not _should_sanitize_artifact_path(relative_path):
        return text
    return sanitize_project_markdown(text)


def sanitize_project_artifact_content(
    path: str | None,
    content: str | None,
    *,
    kind: str | None = None,
) -> str:
    text = str(content or "")
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind in {"report", "paper"}:
        return sanitize_project_markdown(text)
    if _should_sanitize_artifact_path(path):
        return sanitize_project_markdown(text)
    return text


def _sanitize_value(value: Any, *, parent_key: str | None) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_value(item, parent_key=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, parent_key=parent_key) for item in value]
    if not isinstance(value, str):
        return value

    if parent_key in _MARKDOWN_KEYS:
        return sanitize_project_markdown(value)
    if parent_key == "content" and _should_sanitize_content(value):
        return sanitize_project_markdown(value)
    return value


def _should_sanitize_content(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _looks_like_json_document(text):
        return False
    if text.startswith("#") or "\n#" in text:
        return True
    if len(text) >= 160:
        return True
    return any(pattern.search(text) for pattern in _INLINE_TRACE_PATTERNS)


def _looks_like_json_document(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped:
        return False
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True


def _should_sanitize_artifact_path(path: str | None) -> bool:
    normalized = str(path or "").replace("\\", "/").strip().lower()
    if not normalized:
        return False
    if not normalized.endswith((".md", ".markdown")):
        return False
    return "/.auto-researcher/aris-runs/" in f"/{normalized.lstrip('/')}"
