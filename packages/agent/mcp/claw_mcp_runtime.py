from __future__ import annotations

import fnmatch
import keyword
import os
import posixpath
import re
import tempfile
from dataclasses import dataclass
from inspect import Parameter, Signature
from pathlib import Path, PurePosixPath
from typing import Any

import mcp.types as mcp_types

from packages.agent.tools.apply_patch_runtime import derive_new_contents_from_chunks, parse_patch
from packages.agent.mcp.claw_mcp_registry import (
    CLAW_CONTEXT_MODE_ENV,
    CLAW_CONTEXT_SESSION_ID_ENV,
    CLAW_CONTEXT_WORKSPACE_PATH_ENV,
    CLAW_CONTEXT_WORKSPACE_SERVER_ID_ENV,
    CLAW_REMOTE_GENERIC_TOOL_NAMES,
    iter_dynamic_bridge_tool_defs,
)
from packages.agent.tools.tool_runtime import AgentToolContext, ToolProgress, ToolResult, execute_tool_stream

_REMOTE_DEFAULT_IGNORES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}


@dataclass(frozen=True)
class RemotePathRef:
    workspace_root: str
    target_path: str
    relative_path: str | None


def bridge_tool_context(*, env: dict[str, str] | None = None) -> AgentToolContext:
    source = env or os.environ
    return AgentToolContext(
        session_id=str(source.get(CLAW_CONTEXT_SESSION_ID_ENV) or "").strip() or None,
        mode=str(source.get(CLAW_CONTEXT_MODE_ENV) or "build").strip() or "build",
        workspace_path=str(source.get(CLAW_CONTEXT_WORKSPACE_PATH_ENV) or "").strip() or None,
        workspace_server_id=(
            str(source.get(CLAW_CONTEXT_WORKSPACE_SERVER_ID_ENV) or "").strip() or None
        ),
    )


def tool_annotations(definition: Any) -> mcp_types.ToolAnnotations:
    permission = str(getattr(getattr(definition, "spec", None), "permission", "") or "").strip().lower()
    requires_confirm = bool(getattr(definition, "requires_confirm", False))
    read_only = not requires_confirm and permission not in {"bash", "edit", "todowrite", "task"}
    destructive = requires_confirm or permission in {"edit", "bash"}
    open_world = permission in {"websearch", "webfetch", "codesearch"}
    return mcp_types.ToolAnnotations(
        title=str(getattr(definition, "name", "") or ""),
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=not destructive,
        openWorldHint=open_world,
    )


def _python_annotation_type(schema: dict[str, Any]) -> Any:
    schema_type = str((schema or {}).get("type") or "").strip().lower()
    if schema_type == "string":
        return str
    elif schema_type == "integer":
        return int
    elif schema_type == "number":
        return float
    elif schema_type == "boolean":
        return bool
    elif schema_type == "array":
        return list
    elif schema_type == "object":
        return dict
    return Any


def build_dynamic_bridge_function(tool_name: str, parameters: dict[str, Any]) -> Any:
    schema = parameters if isinstance(parameters, dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required_names = {
        str(item).strip()
        for item in (schema.get("required") or [])
        if str(item).strip()
    }
    for raw_name in properties:
        name = str(raw_name)
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError(f"MCP bridge 暂不支持非标参数名: {name}")

    signature_params: list[Parameter] = []
    ordered_items = [
        *[(name, properties[name]) for name in properties if name in required_names],
        *[(name, properties[name]) for name in properties if name not in required_names],
    ]

    for raw_name, raw_schema in ordered_items:
        name = str(raw_name)
        field_schema = raw_schema if isinstance(raw_schema, dict) else {}
        required = name in required_names
        annotation_type = _python_annotation_type(field_schema)
        default_value = Parameter.empty
        if not required:
            default_value = field_schema.get("default")
            if default_value is None:
                default_value = None
        signature_params.append(
            Parameter(
                name=name,
                kind=Parameter.POSITIONAL_OR_KEYWORD,
                default=default_value,
                annotation=annotation_type,
            )
        )

    def _bridge_tool(**kwargs: Any) -> dict[str, Any]:
        return execute_bridge_tool(tool_name, kwargs)

    _bridge_tool.__name__ = f"bridge_{tool_name}"
    _bridge_tool.__signature__ = Signature(parameters=signature_params, return_annotation=dict[str, Any])
    _bridge_tool.__annotations__ = {param.name: Any for param in signature_params}
    _bridge_tool.__annotations__["return"] = dict[str, Any]
    return _bridge_tool


def serialize_tool_result(result: ToolResult) -> dict[str, Any]:
    payload = {
        "success": bool(result.success),
        "summary": str(result.summary or ""),
        "data": dict(result.data or {}),
    }
    internal_data = dict(result.internal_data or {})
    if internal_data:
        payload["internal_data"] = internal_data
        display_data = internal_data.get("display_data")
        if isinstance(display_data, dict):
            payload["display_data"] = display_data
    return payload


def remote_server_entry(context: AgentToolContext) -> dict[str, Any]:
    server_id = str(context.workspace_server_id or "").strip()
    if not server_id or server_id.lower() == "local":
        raise ValueError("当前没有绑定远端工作区服务器")
    from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry

    return get_workspace_server_entry(server_id)


def remote_path_display(workspace_root: str, target_path: str) -> dict[str, Any]:
    payload = {
        "workspace_path": workspace_root,
        "path": target_path,
    }
    if target_path != workspace_root:
        payload["relative_path"] = posixpath.relpath(target_path, workspace_root)
    return payload


def _bridge_local_path_to_relative(path_value: str | None) -> str:
    raw_value = str(path_value or "").strip()
    if not raw_value:
        return ""
    normalized = raw_value.replace("\\", "/").strip()
    if normalized in {".", "./"}:
        return ""
    try:
        candidate = Path(raw_value).expanduser()
        current_dir = Path.cwd().resolve()
        candidate_resolved = candidate.resolve(strict=False)
    except Exception:
        return normalized
    if not candidate.is_absolute():
        return normalized
    try:
        relative = candidate_resolved.relative_to(current_dir)
    except ValueError:
        return normalized
    relative_posix = PurePosixPath(*relative.parts).as_posix()
    return "" if relative_posix in {"", "."} else relative_posix


def resolve_remote_workspace_root(
    context: AgentToolContext,
    workspace_value: str | None = None,
) -> tuple[dict[str, Any], str]:
    from packages.agent.workspace.workspace_remote import open_ssh_session, resolve_remote_workspace_path

    server_entry = remote_server_entry(context)
    requested_workspace = str(context.workspace_path or "").strip()
    with open_ssh_session(server_entry) as session:
        workspace_root = resolve_remote_workspace_path(server_entry, requested_workspace, session)
        raw_workspace = _bridge_local_path_to_relative(workspace_value)
        if raw_workspace.startswith("/") or raw_workspace.startswith("~"):
            target_workspace = resolve_remote_workspace_path(server_entry, raw_workspace, session)
        elif raw_workspace:
            target_workspace = posixpath.normpath(posixpath.join(workspace_root, raw_workspace))
        else:
            target_workspace = workspace_root
    if target_workspace != workspace_root and not target_workspace.startswith(workspace_root.rstrip("/") + "/"):
        raise ValueError("远端路径必须位于当前工作区内")
    return server_entry, target_workspace


def resolve_remote_path_ref(
    context: AgentToolContext,
    path_value: str | None,
) -> tuple[dict[str, Any], RemotePathRef]:
    from packages.agent.workspace.workspace_remote import open_ssh_session, resolve_remote_workspace_path

    server_entry, workspace_root = resolve_remote_workspace_root(context)
    with open_ssh_session(server_entry) as session:
        raw_path = _bridge_local_path_to_relative(path_value)
        if not raw_path:
            target_path = workspace_root
        elif raw_path.startswith("/") or raw_path.startswith("~"):
            target_path = resolve_remote_workspace_path(server_entry, raw_path, session)
        else:
            target_path = posixpath.normpath(posixpath.join(workspace_root, raw_path))
    if target_path != workspace_root and not target_path.startswith(workspace_root.rstrip("/") + "/"):
        raise ValueError("远端路径必须位于当前工作区内")
    relative_path = None if target_path == workspace_root else posixpath.relpath(target_path, workspace_root)
    return server_entry, RemotePathRef(
        workspace_root=workspace_root,
        target_path=target_path,
        relative_path=relative_path,
    )


def remote_walk_entries(
    sftp,
    root_path: str,
    *,
    recursive: bool,
    max_depth: int,
    max_entries: int,
) -> list[tuple[str, Any, int]]:
    import stat

    items: list[tuple[str, Any, int]] = []

    def walk(current_path: str, depth: int) -> None:
        if len(items) >= max_entries:
            return
        if recursive and depth > max_depth:
            return
        try:
            children = sorted(
                sftp.listdir_attr(current_path),
                key=lambda item: (not stat.S_ISDIR(item.st_mode), item.filename.lower()),
            )
        except OSError:
            return
        for child in children:
            if len(items) >= max_entries:
                return
            name = str(child.filename or "")
            if not name or name in _REMOTE_DEFAULT_IGNORES:
                continue
            if name.startswith(".") and depth > 0:
                continue
            child_path = posixpath.join(current_path, name)
            items.append((child_path, child, depth))
            if recursive and stat.S_ISDIR(child.st_mode):
                walk(child_path, depth + 1)

    walk(root_path, 0)
    return items


def remote_read_full_text(server_entry: dict[str, Any], path_ref: RemotePathRef) -> tuple[str, int]:
    from packages.agent.workspace.workspace_remote import open_ssh_session, remote_is_dir, remote_stat

    with open_ssh_session(server_entry) as session:
        target_attr = remote_stat(session.sftp, path_ref.target_path)
        if target_attr is None:
            raise ValueError(f"文件不存在: {path_ref.relative_path or path_ref.target_path}")
        if remote_is_dir(target_attr):
            raise ValueError(f"不是文件: {path_ref.relative_path or path_ref.target_path}")
        with session.sftp.file(path_ref.target_path, "rb") as handle:
            content = handle.read().decode("utf-8", errors="replace")
        return content, int(target_attr.st_size)


def remote_list_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import open_ssh_session, remote_is_dir, remote_stat
    import stat

    try:
        server_entry, path_ref = resolve_remote_path_ref(context, str(arguments.get("path") or ""))
        recursive = bool(arguments.get("recursive"))
        max_depth = max(1, min(int(arguments.get("max_depth") or 2), 8))
        max_entries = max(20, min(int(arguments.get("max_entries") or 120), 400))
        with open_ssh_session(server_entry) as session:
            target_attr = remote_stat(session.sftp, path_ref.target_path)
            if target_attr is None:
                raise ValueError(f"路径不存在: {path_ref.target_path}")
            if not remote_is_dir(target_attr):
                raise ValueError(f"不是目录: {path_ref.target_path}")
            walked = remote_walk_entries(
                session.sftp,
                path_ref.target_path,
                recursive=recursive,
                max_depth=max_depth,
                max_entries=max_entries,
            )
        tree_lines = [path_ref.target_path]
        entries: list[dict[str, Any]] = []
        for child_path, child_attr, depth in walked:
            is_dir = stat.S_ISDIR(child_attr.st_mode)
            prefix = "  " * depth + "- "
            name = posixpath.basename(child_path)
            tree_lines.append(f"{prefix}{name}/" if is_dir else f"{prefix}{name}")
            entries.append(
                {
                    **remote_path_display(path_ref.workspace_root, child_path),
                    "is_dir": is_dir,
                    "size_bytes": None if is_dir else int(getattr(child_attr, "st_size", 0) or 0),
                }
            )
        payload = {
            **remote_path_display(path_ref.workspace_root, path_ref.target_path),
            "directory_path": path_ref.target_path,
            "tree": "\n".join(tree_lines),
            "entries": entries,
            "total_entries": len(entries),
            "truncated": len(entries) >= max_entries,
        }
        return ToolResult(success=True, data=payload, summary=f"已列出 {len(entries)} 个目录条目")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_read_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import open_ssh_session, remote_is_dir, remote_stat
    import stat

    try:
        server_entry, path_ref = resolve_remote_path_ref(context, str(arguments.get("file_path") or ""))
        max_chars = max(1000, min(int(arguments.get("max_chars") or 12000), 50000))
        offset = arguments.get("offset")
        limit = arguments.get("limit")
        with open_ssh_session(server_entry) as session:
            target_attr = remote_stat(session.sftp, path_ref.target_path)
            if target_attr is None:
                raise ValueError(f"文件不存在: {path_ref.target_path}")
            if remote_is_dir(target_attr):
                walked = remote_walk_entries(
                    session.sftp,
                    path_ref.target_path,
                    recursive=False,
                    max_depth=2,
                    max_entries=200,
                )
                tree_lines = [path_ref.target_path]
                entries: list[dict[str, Any]] = []
                for child_path, child_attr, depth in walked:
                    is_dir = stat.S_ISDIR(child_attr.st_mode)
                    prefix = "  " * depth + "- "
                    name = posixpath.basename(child_path)
                    tree_lines.append(f"{prefix}{name}/" if is_dir else f"{prefix}{name}")
                    entries.append(
                        {
                            **remote_path_display(path_ref.workspace_root, child_path),
                            "is_dir": is_dir,
                            "size_bytes": None if is_dir else int(getattr(child_attr, "st_size", 0) or 0),
                        }
                    )
                payload = {
                    **remote_path_display(path_ref.workspace_root, path_ref.target_path),
                    "directory_path": path_ref.target_path,
                    "tree": "\n".join(tree_lines),
                    "entries": entries,
                    "total_entries": len(entries),
                    "truncated": len(entries) >= 200,
                }
                return ToolResult(success=True, data=payload, summary=f"已读取目录 {path_ref.target_path}")
        content, size_bytes = remote_read_full_text(server_entry, path_ref)
        payload = {
            **remote_path_display(path_ref.workspace_root, path_ref.target_path),
            "size_bytes": size_bytes,
        }
        if offset is not None or limit is not None:
            normalized_offset = int(offset or 1)
            normalized_limit = int(limit or 200)
            if normalized_offset < 1:
                raise ValueError("offset 必须大于等于 1")
            if normalized_limit < 1:
                raise ValueError("limit 必须大于等于 1")
            lines = content.splitlines()
            total_lines = len(lines)
            if total_lines < normalized_offset and not (total_lines == 0 and normalized_offset == 1):
                raise ValueError(f"offset 超出文件范围: {normalized_offset} > {total_lines}")
            start_index = max(normalized_offset - 1, 0)
            sliced_lines = lines[start_index : start_index + normalized_limit]
            numbered_lines = [
                f"{line_number}: {text}"
                for line_number, text in enumerate(sliced_lines, start=normalized_offset)
            ]
            line_end = normalized_offset + len(sliced_lines) - 1 if sliced_lines else normalized_offset - 1
            truncated = start_index + len(sliced_lines) < total_lines
            payload.update(
                {
                    "content": "\n".join(numbered_lines),
                    "raw_content": "\n".join(sliced_lines),
                    "offset": normalized_offset,
                    "limit": normalized_limit,
                    "line_start": normalized_offset,
                    "line_end": line_end,
                    "total_lines": total_lines,
                    "next_offset": (line_end + 1) if truncated else None,
                    "truncated": truncated,
                }
            )
        else:
            payload.update({"content": content[:max_chars], "truncated": len(content) > max_chars})
        return ToolResult(success=True, data=payload, summary=f"已读取 {path_ref.target_path}")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_write_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import remote_write_file

    try:
        server_entry, path_ref = resolve_remote_path_ref(context, str(arguments.get("file_path") or ""))
        if not path_ref.relative_path:
            raise ValueError("远端写入必须指定工作区内文件路径")
        result = remote_write_file(
            server_entry,
            path=path_ref.workspace_root,
            relative_path=path_ref.relative_path,
            content=str(arguments.get("content") or ""),
            create_dirs=bool(arguments.get("create_dirs", True)),
            overwrite=bool(arguments.get("overwrite", True)),
        )
        result.update(remote_path_display(path_ref.workspace_root, path_ref.target_path))
        return ToolResult(success=True, data=result, summary=f"已写入 {path_ref.relative_path}")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_edit_impl(
    *,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
    context: AgentToolContext,
) -> dict[str, Any]:
    from packages.agent.workspace.workspace_remote import remote_write_file

    server_entry, path_ref = resolve_remote_path_ref(context, file_path)
    if not path_ref.relative_path:
        raise ValueError("远端编辑必须指定工作区内文件路径")
    original, size_bytes = remote_read_full_text(server_entry, path_ref)
    if old_string == new_string:
        raise ValueError("old_string 与 new_string 完全一致")
    if not old_string:
        updated = new_string
        matched_occurrences = 0
        replaced_occurrences = 0
    else:
        matched_occurrences = original.count(old_string)
        if matched_occurrences == 0:
            raise ValueError("未找到要替换的文本，请先读取文件并提供更精确的上下文")
        if matched_occurrences > 1 and not replace_all:
            raise ValueError(
                f"匹配到 {matched_occurrences} 处内容，替换存在歧义。请提供更精确的 old_string，或显式设置 replace_all=true"
            )
        updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)
        replaced_occurrences = matched_occurrences if replace_all else 1
    result = remote_write_file(
        server_entry,
        path=path_ref.workspace_root,
        relative_path=path_ref.relative_path,
        content=updated,
        create_dirs=True,
        overwrite=True,
    )
    result.update(remote_path_display(path_ref.workspace_root, path_ref.target_path))
    result["matched_occurrences"] = matched_occurrences
    result["replaced_occurrences"] = replaced_occurrences
    result["size_bytes"] = int(result.get("size_bytes") or size_bytes)
    return result


def remote_edit_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    try:
        result = remote_edit_impl(
            file_path=str(arguments.get("file_path") or ""),
            old_string=str(arguments.get("old_string") or ""),
            new_string=str(arguments.get("new_string") or ""),
            replace_all=bool(arguments.get("replace_all")),
            context=context,
        )
        return ToolResult(success=True, data=result, summary=f"已编辑 {result.get('relative_path') or result.get('path') or ''}")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_multiedit_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    edits = [item for item in (arguments.get("edits") or []) if isinstance(item, dict)]
    default_file_path = str(arguments.get("file_path") or "")
    if not edits:
        return ToolResult(success=False, summary="multiedit 至少需要一条 edit")
    results: list[dict[str, Any]] = []
    touched: list[str] = []
    try:
        for index, edit in enumerate(edits, start=1):
            target_file = str(edit.get("file_path") or default_file_path or "")
            if not target_file:
                raise ValueError(f"第 {index} 条 edit 缺少 file_path")
            result = remote_edit_impl(
                file_path=target_file,
                old_string=str(edit.get("old_string") or ""),
                new_string=str(edit.get("new_string") or ""),
                replace_all=bool(edit.get("replace_all")),
                context=context,
            )
            resolved = str(result.get("relative_path") or result.get("path") or target_file)
            if resolved not in touched:
                touched.append(resolved)
            results.append(
                {
                    "index": index,
                    "file_path": resolved,
                    "replaced_occurrences": int(result.get("replaced_occurrences") or 0),
                    "changed": bool(result.get("changed")),
                }
            )
        total_replacements = sum(int(item.get("replaced_occurrences") or 0) for item in results)
        return ToolResult(
            success=True,
            data={
                "count": len(results),
                "files": touched,
                "results": results,
                "total_replacements": total_replacements,
            },
            summary=f"已完成 {len(results)} 条编辑，涉及 {len(touched)} 个文件",
        )
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_glob_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import open_ssh_session, remote_is_dir, remote_stat
    import stat

    try:
        pattern = str(arguments.get("pattern") or "").strip()
        if not pattern:
            raise ValueError("pattern 不能为空")
        server_entry, path_ref = resolve_remote_path_ref(context, str(arguments.get("path") or ""))
        limit = max(1, min(int(arguments.get("limit") or 40), 400))
        with open_ssh_session(server_entry) as session:
            target_attr = remote_stat(session.sftp, path_ref.target_path)
            if target_attr is None or not remote_is_dir(target_attr):
                raise ValueError(f"目录不存在: {path_ref.target_path}")
            walked = remote_walk_entries(
                session.sftp,
                path_ref.target_path,
                recursive=True,
                max_depth=16,
                max_entries=max(limit * 6, 600),
            )
        matches: list[dict[str, Any]] = []
        pure_pattern = pattern.replace("\\", "/")
        for child_path, child_attr, _depth in walked:
            relative_to_target = posixpath.relpath(child_path, path_ref.target_path)
            if not PurePosixPath(relative_to_target).match(pure_pattern):
                continue
            is_dir = stat.S_ISDIR(child_attr.st_mode)
            matches.append(
                {
                    **remote_path_display(path_ref.workspace_root, child_path),
                    "is_dir": is_dir,
                    "size_bytes": None if is_dir else int(getattr(child_attr, "st_size", 0) or 0),
                }
            )
            if len(matches) >= limit:
                break
        return ToolResult(
            success=True,
            data={
                **remote_path_display(path_ref.workspace_root, path_ref.target_path),
                "pattern": pure_pattern,
                "matches": matches,
                "count": len(matches),
                "truncated": len(matches) >= limit,
            },
            summary=f"glob 命中 {len(matches)} 项",
        )
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_grep_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import open_ssh_session, remote_is_dir, remote_stat
    import stat

    try:
        pattern = str(arguments.get("pattern") or "").strip()
        if not pattern:
            raise ValueError("pattern 不能为空")
        include = str(arguments.get("include") or "").strip() or None
        limit = max(1, min(int(arguments.get("limit") or 40), 400))
        regex = re.compile(pattern, re.IGNORECASE)
        server_entry, path_ref = resolve_remote_path_ref(context, str(arguments.get("path") or ""))
        with open_ssh_session(server_entry) as session:
            target_attr = remote_stat(session.sftp, path_ref.target_path)
            if target_attr is None or not remote_is_dir(target_attr):
                raise ValueError(f"目录不存在: {path_ref.target_path}")
            walked = remote_walk_entries(
                session.sftp,
                path_ref.target_path,
                recursive=True,
                max_depth=16,
                max_entries=max(limit * 8, 1200),
            )
            matches: list[dict[str, Any]] = []
            for child_path, child_attr, _depth in walked:
                if stat.S_ISDIR(child_attr.st_mode):
                    continue
                relative_to_target = posixpath.relpath(child_path, path_ref.target_path)
                if include and not (
                    fnmatch.fnmatch(posixpath.basename(child_path), include)
                    or fnmatch.fnmatch(relative_to_target, include)
                ):
                    continue
                try:
                    with session.sftp.file(child_path, "rb") as handle:
                        content = handle.read().decode("utf-8", errors="replace")
                except OSError:
                    continue
                for line_number, line in enumerate(content.splitlines(), start=1):
                    if not regex.search(line):
                        continue
                    matches.append(
                        {
                            **remote_path_display(path_ref.workspace_root, child_path),
                            "line": line_number,
                            "text": line[:600],
                        }
                    )
                    if len(matches) >= limit:
                        break
                if len(matches) >= limit:
                    break
        return ToolResult(
            success=True,
            data={
                **remote_path_display(path_ref.workspace_root, path_ref.target_path),
                "pattern": pattern,
                "include_glob": include,
                "matches": matches,
                "count": len(matches),
                "truncated": len(matches) >= limit,
            },
            summary=f"grep 命中 {len(matches)} 处",
        )
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_apply_patch_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import (
        open_ssh_session,
        remote_is_dir,
        remote_restore_file,
        remote_stat,
    )

    patch_text = str(arguments.get("patchText") or "")
    try:
        hunks = parse_patch(patch_text)
        if not hunks:
            raise ValueError("no hunks found")
    except Exception as exc:
        return ToolResult(success=False, summary=f"apply_patch verification failed: {exc}")

    try:
        server_entry = remote_server_entry(context)
        resolved: list[tuple[dict[str, Any], RemotePathRef, RemotePathRef | None]] = []
        for hunk in hunks:
            source_ref = str(hunk.get("path") or "").strip()
            if not source_ref:
                raise ValueError("patch 缺少文件路径")
            _server_entry, path_ref = resolve_remote_path_ref(context, source_ref)
            move_ref = str(hunk.get("move_path") or "").strip() or None
            move_path_ref = resolve_remote_path_ref(context, move_ref)[1] if move_ref else None
            resolved.append((hunk, path_ref, move_path_ref))

        summary_lines: list[str] = []
        touched_files: list[str] = []
        for hunk, path_ref, move_path_ref in resolved:
            hunk_type = str(hunk.get("type") or "")
            if hunk_type == "add":
                new_content = str(hunk.get("contents") or "")
                if not new_content.endswith("\n"):
                    new_content = f"{new_content}\n"
                with open_ssh_session(server_entry) as session:
                    target_attr = remote_stat(session.sftp, path_ref.target_path)
                    if target_attr is not None:
                        raise ValueError(f"文件已存在: {path_ref.target_path}")
                remote_restore_file(server_entry, path=path_ref.target_path, content=new_content)
                display = path_ref.relative_path or path_ref.target_path
                summary_lines.append(f"A {display}")
                touched_files.append(display)
                continue
            if hunk_type == "delete":
                with open_ssh_session(server_entry) as session:
                    target_attr = remote_stat(session.sftp, path_ref.target_path)
                    if target_attr is None or remote_is_dir(target_attr):
                        raise ValueError(f"文件不存在: {path_ref.target_path}")
                remote_restore_file(server_entry, path=path_ref.target_path, content=None)
                display = path_ref.relative_path or path_ref.target_path
                summary_lines.append(f"D {display}")
                touched_files.append(display)
                continue

            original_content, _size_bytes = remote_read_full_text(server_entry, path_ref)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                handle.write(original_content)
                temp_path = Path(handle.name)
            try:
                updated_content = str(
                    derive_new_contents_from_chunks(str(temp_path), list(hunk.get("chunks") or [])).get("content") or ""
                )
            finally:
                temp_path.unlink(missing_ok=True)

            target_ref = move_path_ref or path_ref
            remote_restore_file(server_entry, path=target_ref.target_path, content=updated_content)
            if move_path_ref is not None and move_path_ref.target_path != path_ref.target_path:
                remote_restore_file(server_entry, path=path_ref.target_path, content=None)
                display = move_path_ref.relative_path or move_path_ref.target_path
            else:
                display = path_ref.relative_path or path_ref.target_path
            summary_lines.append(f"M {display}")
            if display not in touched_files:
                touched_files.append(display)

        return ToolResult(
            success=True,
            data={
                "count": len(summary_lines),
                "files": touched_files,
                "summary": summary_lines,
            },
            summary="Success. Updated the following files:\n" + "\n".join(summary_lines),
        )
    except Exception as exc:
        return ToolResult(success=False, summary=f"apply_patch failed: {exc}")


def remote_bash_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import remote_terminal_result

    try:
        if bool(arguments.get("background")):
            return ToolResult(success=False, summary="远程工作区暂不支持后台命令，请改为前台执行")
        workdir = str(arguments.get("workdir") or "").strip()
        server_entry, path_ref = resolve_remote_path_ref(context, workdir)
        result = remote_terminal_result(
            server_entry,
            path=path_ref.target_path,
            command=str(arguments.get("command") or ""),
            timeout_sec=max(5, min(int(arguments.get("timeout_sec") or 120), 4 * 3600)),
        )
        result.update(remote_path_display(path_ref.workspace_root, path_ref.target_path))
        success = bool(result.get("success"))
        return ToolResult(
            success=success,
            data=result,
            summary=f"命令执行{'成功' if success else '失败'}，退出码 {result.get('exit_code')}",
        )
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_inspect_workspace_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import build_remote_overview

    try:
        server_entry, workspace_root = resolve_remote_workspace_root(
            context,
            str(arguments.get("workspace_path") or ""),
        )
        payload = build_remote_overview(
            server_entry,
            workspace_root,
            depth=max(1, min(int(arguments.get("max_depth") or 2), 8)),
            max_entries=max(20, min(int(arguments.get("max_entries") or 120), 400)),
        )
        return ToolResult(success=True, data=payload, summary=f"已检查工作区 {payload.get('workspace_path')}")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_read_workspace_file_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import remote_read_file

    try:
        server_entry, workspace_root = resolve_remote_workspace_root(
            context,
            str(arguments.get("workspace_path") or ""),
        )
        payload = remote_read_file(
            server_entry,
            workspace_root,
            str(arguments.get("relative_path") or ""),
            max_chars=max(1000, min(int(arguments.get("max_chars") or 12000), 50000)),
        )
        return ToolResult(success=True, data=payload, summary=f"已读取 {payload.get('relative_path') or ''}")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_write_workspace_file_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import remote_write_file

    try:
        server_entry, workspace_root = resolve_remote_workspace_root(
            context,
            str(arguments.get("workspace_path") or ""),
        )
        payload = remote_write_file(
            server_entry,
            path=workspace_root,
            relative_path=str(arguments.get("relative_path") or ""),
            content=str(arguments.get("content") or ""),
            create_dirs=bool(arguments.get("create_dirs", True)),
            overwrite=bool(arguments.get("overwrite", True)),
        )
        return ToolResult(success=True, data=payload, summary=f"已写入 {payload.get('relative_path') or ''}")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_replace_workspace_text_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    try:
        _server_entry, workspace_root = resolve_remote_workspace_root(
            context,
            str(arguments.get("workspace_path") or ""),
        )
        target_path = posixpath.join(
            workspace_root,
            str(arguments.get("relative_path") or "").replace("\\", "/").strip(),
        )
        result = remote_edit_impl(
            file_path=target_path,
            old_string=str(arguments.get("search_text") or ""),
            new_string=str(arguments.get("replace_text") or ""),
            replace_all=bool(arguments.get("replace_all")),
            context=context,
        )
        payload = {
            "workspace_path": str(result.get("workspace_path") or workspace_root),
            "relative_path": str(result.get("relative_path") or ""),
            "changed": bool(result.get("changed")),
            "replace_all": bool(arguments.get("replace_all")),
            "matched_occurrences": int(result.get("matched_occurrences") or 0),
            "replaced_occurrences": int(result.get("replaced_occurrences") or 0),
            "size_bytes": int(result.get("size_bytes") or 0),
        }
        return ToolResult(success=True, data=payload, summary=f"已编辑 {payload.get('relative_path') or ''}")
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def remote_run_workspace_command_tool(arguments: dict[str, Any], context: AgentToolContext) -> ToolResult:
    from packages.agent.workspace.workspace_remote import remote_terminal_result

    try:
        if bool(arguments.get("background")):
            return ToolResult(success=False, summary="远程工作区暂不支持后台命令，请改为前台执行")
        server_entry, workspace_root = resolve_remote_workspace_root(
            context,
            str(arguments.get("workspace_path") or ""),
        )
        payload = remote_terminal_result(
            server_entry,
            path=workspace_root,
            command=str(arguments.get("command") or ""),
            timeout_sec=max(5, min(int(arguments.get("timeout_sec") or 120), 4 * 3600)),
        )
        return ToolResult(
            success=bool(payload.get("success")),
            data=payload,
            summary=f"命令执行{'成功' if payload.get('success') else '失败'}，退出码 {payload.get('exit_code')}",
        )
    except Exception as exc:
        return ToolResult(success=False, summary=str(exc))


def execute_bridge_tool(tool_name: str, arguments: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    context = bridge_tool_context(env=env or os.environ)
    remote = bool(str(context.workspace_server_id or "").strip() and str(context.workspace_server_id or "").strip().lower() != "local")
    remote_overrides = {
        "list": remote_list_tool,
        "ls": remote_list_tool,
        "read": remote_read_tool,
        "write": remote_write_tool,
        "edit": remote_edit_tool,
        "multiedit": remote_multiedit_tool,
        "glob": remote_glob_tool,
        "grep": remote_grep_tool,
        "apply_patch": remote_apply_patch_tool,
        "bash": remote_bash_tool,
        "inspect_workspace": remote_inspect_workspace_tool,
        "read_workspace_file": remote_read_workspace_file_tool,
        "write_workspace_file": remote_write_workspace_file_tool,
        "replace_workspace_text": remote_replace_workspace_text_tool,
        "run_workspace_command": remote_run_workspace_command_tool,
    }
    if remote and tool_name in CLAW_REMOTE_GENERIC_TOOL_NAMES and tool_name in remote_overrides:
        return serialize_tool_result(remote_overrides[tool_name](arguments, context))

    final_result: ToolResult | None = None
    progress_messages: list[dict[str, Any]] = []
    for item in execute_tool_stream(tool_name, arguments, context=context):
        if isinstance(item, ToolProgress):
            progress_messages.append(
                {
                    "message": item.message,
                    "current": int(item.current or 0),
                    "total": int(item.total or 0),
                }
            )
            continue
        if isinstance(item, ToolResult):
            final_result = item
    if final_result is None:
        final_result = ToolResult(success=False, summary="工具没有返回最终结果")
    payload = serialize_tool_result(final_result)
    if progress_messages:
        payload["progress"] = progress_messages
    return payload


def register_dynamic_bridge_tools(server: Any, logger: Any) -> None:
    existing_names = set(getattr(getattr(server, "_tool_manager", None), "_tools", {}).keys())
    for definition in iter_dynamic_bridge_tool_defs(existing_names=existing_names):
        try:
            function = build_dynamic_bridge_function(str(definition.name), dict(definition.parameters or {}))
        except Exception:
            logger.exception("failed to build dynamic MCP bridge tool %s", definition.name)
            continue
        server.add_tool(
            function,
            name=str(definition.name),
            description=str(definition.description or ""),
            annotations=tool_annotations(definition),
            structured_output=True,
        )

