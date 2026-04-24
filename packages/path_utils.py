from __future__ import annotations

import ntpath
import os
import posixpath
import re
from pathlib import Path

_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def is_windows_absolute_path(value: object) -> bool:
    raw = str(value or "").strip()
    return bool(_WINDOWS_ABSOLUTE_RE.match(raw)) or raw.startswith("\\\\")


def is_foreign_windows_path(value: object) -> bool:
    return os.name != "nt" and is_windows_absolute_path(value)


def normalize_local_path_string(value: object, *, resolve: bool = True) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if is_foreign_windows_path(raw):
        return raw
    path = Path(raw).expanduser()
    if resolve:
        try:
            path = path.resolve()
        except OSError:
            return raw
    return str(path)


def path_name_string(value: object) -> str:
    raw = str(value or "").strip().rstrip("/\\")
    if not raw:
        return ""
    if is_windows_absolute_path(raw):
        return ntpath.basename(raw)
    return Path(raw).name


def parent_path_string(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if is_windows_absolute_path(raw):
        return ntpath.dirname(raw.rstrip("/\\"))
    return str(Path(raw).expanduser().parent)


def join_path_string(base: object, *parts: object, resolve: bool = False) -> str:
    base_text = str(base or "").strip()
    clean_parts = [str(part).strip("/\\") for part in parts if str(part or "").strip()]
    if not base_text:
        return ntpath.join(*clean_parts) if clean_parts else ""
    if is_windows_absolute_path(base_text):
        if "/" in base_text and "\\" not in base_text:
            return posixpath.join(base_text.rstrip("/"), *clean_parts)
        return ntpath.join(base_text, *clean_parts)
    path = Path(base_text).expanduser()
    for part in clean_parts:
        path = path / part
    if resolve:
        try:
            path = path.resolve()
        except OSError:
            pass
    return str(path)


def sqlite_url_for_path(path_value: object) -> str:
    path_text = str(path_value or "").strip()
    if is_windows_absolute_path(path_text):
        return "sqlite:///" + path_text.replace("\\", "/")
    return "sqlite:///" + Path(path_text).expanduser().resolve().as_posix()


def local_relative_path(root: object, target: object) -> str:
    root_text = str(root or "").strip()
    target_text = str(target or "").strip()
    if is_windows_absolute_path(root_text) or is_windows_absolute_path(target_text):
        root_norm = root_text.replace("\\", "/").rstrip("/")
        target_norm = target_text.replace("\\", "/")
        if target_norm == root_norm:
            return "."
        prefix = f"{root_norm}/"
        if target_norm.startswith(prefix):
            return target_norm[len(prefix):]
        raise ValueError(f"{target_text!r} is not under {root_text!r}")
    return Path(target_text).relative_to(Path(root_text)).as_posix()
