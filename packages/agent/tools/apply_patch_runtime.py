from __future__ import annotations

from pathlib import Path
from typing import Any


def _strip_heredoc(value: str) -> str:
    text = str(value or "").strip()
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip()
    if not first.startswith("<<"):
        return text
    marker = first[2:].strip().strip("'\"")
    if not marker:
        return text
    if lines[-1].strip() != marker:
        return text
    return "\n".join(lines[1:-1])


def _parse_patch_header(lines: list[str], start_idx: int) -> dict[str, Any] | None:
    line = lines[start_idx]
    if line.startswith("*** Add File:"):
        file_path = line[len("*** Add File:") :].strip()
        return {"type": "add", "path": file_path, "next_idx": start_idx + 1} if file_path else None
    if line.startswith("*** Delete File:"):
        file_path = line[len("*** Delete File:") :].strip()
        return {"type": "delete", "path": file_path, "next_idx": start_idx + 1} if file_path else None
    if line.startswith("*** Update File:"):
        file_path = line[len("*** Update File:") :].strip()
        if not file_path:
            return None
        next_idx = start_idx + 1
        move_path: str | None = None
        if next_idx < len(lines) and lines[next_idx].startswith("*** Move to:"):
            move_path = lines[next_idx][len("*** Move to:") :].strip() or None
            next_idx += 1
        return {
            "type": "update",
            "path": file_path,
            "move_path": move_path,
            "next_idx": next_idx,
        }
    return None


def _parse_update_file_chunks(lines: list[str], start_idx: int) -> tuple[list[dict[str, Any]], int]:
    chunks: list[dict[str, Any]] = []
    index = start_idx
    while index < len(lines) and not lines[index].startswith("***"):
        if not lines[index].startswith("@@"):
            index += 1
            continue
        context = lines[index][2:].strip() or None
        index += 1
        old_lines: list[str] = []
        new_lines: list[str] = []
        is_end_of_file = False
        while index < len(lines) and not lines[index].startswith("@@") and not lines[index].startswith("***"):
            change_line = lines[index]
            if change_line == "*** End of File":
                is_end_of_file = True
                index += 1
                break
            if change_line.startswith(" "):
                content = change_line[1:]
                old_lines.append(content)
                new_lines.append(content)
            elif change_line.startswith("-"):
                old_lines.append(change_line[1:])
            elif change_line.startswith("+"):
                new_lines.append(change_line[1:])
            index += 1
        chunks.append(
            {
                "old_lines": old_lines,
                "new_lines": new_lines,
                "change_context": context,
                "is_end_of_file": is_end_of_file,
            }
        )
    return chunks, index


def _parse_add_file_content(lines: list[str], start_idx: int) -> tuple[str, int]:
    items: list[str] = []
    index = start_idx
    while index < len(lines) and not lines[index].startswith("***"):
        if lines[index].startswith("+"):
            items.append(lines[index][1:])
        index += 1
    return "\n".join(items), index


def parse_patch(patch_text: str) -> list[dict[str, Any]]:
    cleaned = _strip_heredoc(str(patch_text or "").strip())
    lines = cleaned.split("\n")
    begin_idx = next((idx for idx, line in enumerate(lines) if line.strip() == "*** Begin Patch"), -1)
    end_idx = next((idx for idx, line in enumerate(lines) if line.strip() == "*** End Patch"), -1)
    if begin_idx == -1 or end_idx == -1 or begin_idx >= end_idx:
        raise ValueError("Invalid patch format: missing Begin/End markers")

    hunks: list[dict[str, Any]] = []
    index = begin_idx + 1
    while index < end_idx:
        header = _parse_patch_header(lines, index)
        if header is None:
            index += 1
            continue
        if header["type"] == "add":
            content, next_idx = _parse_add_file_content(lines, header["next_idx"])
            hunks.append({"type": "add", "path": header["path"], "contents": content})
            index = next_idx
            continue
        if header["type"] == "delete":
            hunks.append({"type": "delete", "path": header["path"]})
            index = header["next_idx"]
            continue
        chunks, next_idx = _parse_update_file_chunks(lines, header["next_idx"])
        hunks.append(
            {
                "type": "update",
                "path": header["path"],
                "move_path": header["move_path"],
                "chunks": chunks,
            }
        )
        index = next_idx
    return hunks


def patch_paths(patch_text: str) -> list[str]:
    values: list[str] = []
    for hunk in parse_patch(patch_text):
        path_value = str(hunk.get("path") or "").strip()
        if path_value:
            values.append(path_value)
        move_path = str(hunk.get("move_path") or "").strip()
        if move_path:
            values.append(move_path)
    return list(dict.fromkeys(values))


def derive_new_contents_from_chunks(file_path: str, chunks: list[dict[str, Any]]) -> dict[str, str]:
    original_content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    original_lines = original_content.split("\n")
    if original_lines and original_lines[-1] == "":
        original_lines.pop()
    replacements = _compute_replacements(original_lines, file_path, chunks)
    new_lines = _apply_replacements(original_lines, replacements)
    if not new_lines or new_lines[-1] != "":
        new_lines.append("")
    return {
        "content": "\n".join(new_lines),
    }


def _compute_replacements(
    original_lines: list[str],
    file_path: str,
    chunks: list[dict[str, Any]],
) -> list[tuple[int, int, list[str]]]:
    replacements: list[tuple[int, int, list[str]]] = []
    line_index = 0
    for chunk in chunks:
        context = str(chunk.get("change_context") or "").strip()
        if context:
            context_idx = _seek_sequence(original_lines, [context], line_index)
            if context_idx == -1:
                raise ValueError(f"Failed to find context '{context}' in {file_path}")
            line_index = context_idx + 1

        old_lines = [str(item) for item in (chunk.get("old_lines") or [])]
        new_lines = [str(item) for item in (chunk.get("new_lines") or [])]
        is_end_of_file = bool(chunk.get("is_end_of_file"))

        if not old_lines:
            insertion_idx = len(original_lines) - 1 if original_lines and original_lines[-1] == "" else len(original_lines)
            replacements.append((insertion_idx, 0, new_lines))
            continue

        pattern = list(old_lines)
        new_slice = list(new_lines)
        found = _seek_sequence(original_lines, pattern, line_index, eof=is_end_of_file)
        if found == -1 and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new_slice and new_slice[-1] == "":
                new_slice = new_slice[:-1]
            found = _seek_sequence(original_lines, pattern, line_index, eof=is_end_of_file)
        if found == -1:
            raise ValueError(f"Failed to find expected lines in {file_path}:\n" + "\n".join(old_lines))
        replacements.append((found, len(pattern), new_slice))
        line_index = found + len(pattern)

    replacements.sort(key=lambda item: item[0])
    return replacements


def _apply_replacements(lines: list[str], replacements: list[tuple[int, int, list[str]]]) -> list[str]:
    result = list(lines)
    for start_idx, old_len, new_segment in reversed(replacements):
        del result[start_idx : start_idx + old_len]
        for offset, line in enumerate(new_segment):
            result.insert(start_idx + offset, line)
    return result


def _normalize_unicode(value: str) -> str:
    return (
        value.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201A", "'")
        .replace("\u201B", "'")
        .replace("\u201C", '"')
        .replace("\u201D", '"')
        .replace("\u201E", '"')
        .replace("\u201F", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
        .replace("\u2026", "...")
        .replace("\u00A0", " ")
    )


def _try_match(
    lines: list[str],
    pattern: list[str],
    start_index: int,
    compare,
    *,
    eof: bool,
) -> int:
    if eof:
        from_end = len(lines) - len(pattern)
        if from_end >= start_index:
            if all(compare(lines[from_end + offset], pattern[offset]) for offset in range(len(pattern))):
                return from_end
    for index in range(start_index, len(lines) - len(pattern) + 1):
        if all(compare(lines[index + offset], pattern[offset]) for offset in range(len(pattern))):
            return index
    return -1


def _seek_sequence(lines: list[str], pattern: list[str], start_index: int, *, eof: bool = False) -> int:
    if not pattern:
        return -1
    exact = _try_match(lines, pattern, start_index, lambda left, right: left == right, eof=eof)
    if exact != -1:
        return exact
    rstrip = _try_match(lines, pattern, start_index, lambda left, right: left.rstrip() == right.rstrip(), eof=eof)
    if rstrip != -1:
        return rstrip
    trimmed = _try_match(lines, pattern, start_index, lambda left, right: left.strip() == right.strip(), eof=eof)
    if trimmed != -1:
        return trimmed
    return _try_match(
        lines,
        pattern,
        start_index,
        lambda left, right: _normalize_unicode(left.strip()) == _normalize_unicode(right.strip()),
        eof=eof,
    )
