"""Concrete builtin tool implementations for ResearchOS."""

from __future__ import annotations

import logging
import re
import subprocess
from difflib import unified_diff
from pathlib import Path
from typing import Any


from packages.agent.tools.apply_patch_runtime import derive_new_contents_from_chunks, parse_patch
from packages.agent.tools.tool_context import (
    context_workspace as _context_workspace,
    context_workspace_server_id as _context_workspace_server_id,
    resolve_remote_server_entry as _resolve_remote_server_entry,
)
from packages.agent.tools.tool_runtime import AgentToolContext, ToolResult
from packages.agent.workspace.workspace_executor import (
    WorkspaceAccessError,
    edit_path_file,
    ensure_workspace_operation_allowed,
    glob_path_entries,
    get_task_status,
    grep_path_contents,
    inspect_workspace,
    list_path_entries,
    read_path_file,
    read_workspace_file,
    resolve_path_input,
    resolve_workspace_file,
    run_local_shell_command,
    run_path_command,
    replace_workspace_text,
    run_workspace_command,
    submit_workspace_command,
    write_path_file,
    write_workspace_file,
)

logger = logging.getLogger(__name__)

_WEBFETCH_MAX_TEXT_CHARS = 50000
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_DROP_RE = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


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


def _build_patch_record(
    *,
    file_label: str,
    path: str,
    before: str,
    after: str,
    exists_before: bool,
    exists_after: bool,
    workspace_path: str | None,
    workspace_server_id: str | None,
) -> dict:
    additions, deletions = _diff_counts(before, after)
    status = "modified"
    if not exists_before and exists_after:
        status = "added"
    elif exists_before and not exists_after:
        status = "deleted"
    return {
        "file": file_label,
        "path": path,
        "before": before,
        "after": after,
        "exists_before": exists_before,
        "exists_after": exists_after,
        "additions": additions,
        "deletions": deletions,
        "status": status,
        "workspace_path": workspace_path,
        "workspace_server_id": workspace_server_id,
    }


def _remote_absolute_path(workspace_path: str, relative_path: str) -> str:
    base = str(workspace_path or "").replace("\\", "/").strip().rstrip("/")
    relative = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not base:
        return relative
    if not relative:
        return base
    return f"{base}/{relative}"


def _inspect_workspace(
    workspace_path: str,
    max_depth: int = 2,
    max_entries: int = 120,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("inspect_workspace")
        server_entry = _resolve_remote_server_entry(context)
        if server_entry is not None:
            from packages.agent.workspace.workspace_remote import build_remote_overview

            result = build_remote_overview(
                server_entry,
                workspace_path,
                depth=max(1, min(max_depth, 5)),
                max_entries=max(20, min(max_entries, 500)),
            )
        else:
            result = inspect_workspace(
                workspace_path,
                max_depth=max(1, min(max_depth, 5)),
                max_entries=max(20, min(max_entries, 500)),
            )
        summary = f"工作区检查完成，共返回 {result.get('total_entries', 0)} 个条目"
        return ToolResult(success=True, data=result, summary=summary)
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _read_workspace_file(
    workspace_path: str,
    relative_path: str,
    max_chars: int = 12000,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("read_workspace_file")
        server_entry = _resolve_remote_server_entry(context)
        if server_entry is not None:
            from packages.agent.workspace.workspace_remote import remote_read_file

            result = remote_read_file(
                server_entry,
                workspace_path,
                relative_path,
                max_chars=max(1000, min(max_chars, 50000)),
            )
        else:
            result = read_workspace_file(
                workspace_path,
                relative_path,
                max_chars=max(1000, min(max_chars, 50000)),
            )
        summary = f"已读取文件 {relative_path}"
        if result.get("truncated"):
            summary += "（内容已截断）"
        return ToolResult(success=True, data=result, summary=summary)
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _run_workspace_command(
    workspace_path: str,
    command: str,
    timeout_sec: int = 120,
    background: bool = False,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command=command)
        server_entry = _resolve_remote_server_entry(context)
        if server_entry is not None:
            if background:
                return ToolResult(success=False, summary="远程工作区暂不支持后台命令，请改为前台执行")
            from packages.agent.workspace.workspace_remote import remote_terminal_result

            result = remote_terminal_result(
                server_entry,
                path=workspace_path,
                command=command,
                timeout_sec=max(5, min(timeout_sec, 3600)),
            )
            success = bool(result.get("success"))
            summary = f"命令执行{'成功' if success else '失败'}，退出码 {result.get('exit_code')}"
            return ToolResult(success=success, data=result, summary=summary)
        if background:
            result = submit_workspace_command(
                workspace_path,
                command,
                timeout_sec=max(30, min(timeout_sec, 4 * 3600)),
            )
            return ToolResult(
                success=True,
                data=result,
                summary=f"后台任务已提交：{result['task_id']}",
            )

        result = run_workspace_command(
            workspace_path,
            command,
            timeout_sec=max(5, min(timeout_sec, 3600)),
        )
        success = bool(result.get("success"))
        summary = f"命令执行{'成功' if success else '失败'}，退出码 {result.get('exit_code')}"
        return ToolResult(success=success, data=result, summary=summary)
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, summary="命令执行超时")


def _get_workspace_task_status(task_id: str) -> ToolResult:
    status = get_task_status(task_id.strip())
    if status is None:
        return ToolResult(success=False, summary=f"未找到任务：{task_id}")
    summary = f"任务状态：{status.get('status')}"
    return ToolResult(success=True, data=status, summary=summary)


def _write_workspace_file(
    workspace_path: str,
    relative_path: str,
    content: str,
    create_dirs: bool = True,
    overwrite: bool = True,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("write_workspace_file")
        internal_patches: list[dict] = []
        server_entry = _resolve_remote_server_entry(context)
        if server_entry is not None:
            from packages.agent.workspace.workspace_remote import remote_read_file, remote_write_file

            before = ""
            exists_before = False
            try:
                existing = remote_read_file(
                    server_entry,
                    workspace_path,
                    relative_path,
                    max_chars=400000,
                )
                if not existing.get("truncated"):
                    before = str(existing.get("content") or "")
                    exists_before = True
            except Exception:
                exists_before = False

            result = remote_write_file(
                server_entry,
                path=workspace_path,
                relative_path=relative_path,
                content=content,
                create_dirs=create_dirs,
                overwrite=overwrite,
            )
        else:
            target = resolve_workspace_file(workspace_path, relative_path)
            exists_before = target.exists()
            before = target.read_text(encoding="utf-8", errors="replace") if exists_before else ""
            result = write_workspace_file(
                workspace_path,
                relative_path,
                content,
                create_dirs=create_dirs,
                overwrite=overwrite,
            )
        if bool(result.get("changed")):
            patch_path = (
                _remote_absolute_path(str(result.get("workspace_path") or workspace_path), str(result.get("relative_path") or relative_path))
                if server_entry is not None
                else str(result.get("path") or str(resolve_workspace_file(workspace_path, relative_path)))
            )
            internal_patches.append(
                _build_patch_record(
                    file_label=str(result.get("relative_path") or relative_path),
                    path=patch_path,
                    before=before,
                    after=content,
                    exists_before=exists_before,
                    exists_after=True,
                    workspace_path=str(result.get("workspace_path") or workspace_path),
                    workspace_server_id=_context_workspace_server_id(context),
                )
            )
        created = bool(result.get("created"))
        changed = bool(result.get("changed"))
        verb = "已创建" if created else "已写入"
        if not changed:
            verb += "（内容无变化）"
        return ToolResult(
            success=True,
            data=result,
            summary=f"{verb}文件 {relative_path}",
            internal_data={"patches": internal_patches} if internal_patches else {},
        )
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _replace_workspace_text(
    workspace_path: str,
    relative_path: str,
    search_text: str,
    replace_text: str,
    replace_all: bool = False,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("replace_workspace_text")
        internal_patches: list[dict] = []
        server_entry = _resolve_remote_server_entry(context)
        if server_entry is not None:
            from packages.agent.workspace.workspace_remote import remote_read_file, remote_write_file

            current = remote_read_file(
                server_entry,
                workspace_path,
                relative_path,
                max_chars=400000,
            )
            if current.get("truncated"):
                return ToolResult(success=False, summary="远程文件内容过长，请先缩小修改范围后再替换")
            original = str(current.get("content") or "")
            if not search_text:
                raise WorkspaceAccessError("search_text 不能为空")
            match_count = original.count(search_text)
            if match_count == 0:
                raise WorkspaceAccessError("未找到要替换的文本，请先读取文件并提供更精确的上下文")
            if match_count > 1 and not replace_all:
                raise WorkspaceAccessError(
                    f"匹配到 {match_count} 处内容，替换存在歧义。请提供更精确的 search_text，或显式设置 replace_all=true"
                )
            updated = original.replace(search_text, replace_text) if replace_all else original.replace(search_text, replace_text, 1)
            result = remote_write_file(
                server_entry,
                path=workspace_path,
                relative_path=relative_path,
                content=updated,
                create_dirs=True,
                overwrite=True,
            )
            result["replaced_occurrences"] = match_count if replace_all else 1
        else:
            target = resolve_workspace_file(workspace_path, relative_path)
            original = target.read_text(encoding="utf-8", errors="replace")
            result = replace_workspace_text(
                workspace_path,
                relative_path,
                search_text,
                replace_text,
                replace_all=replace_all,
            )
            updated = original.replace(search_text, replace_text) if replace_all else original.replace(search_text, replace_text, 1)
        if bool(result.get("changed")):
            patch_path = (
                _remote_absolute_path(str(result.get("workspace_path") or workspace_path), str(result.get("relative_path") or relative_path))
                if server_entry is not None
                else str(result.get("path") or str(resolve_workspace_file(workspace_path, relative_path)))
            )
            internal_patches.append(
                _build_patch_record(
                    file_label=str(result.get("relative_path") or relative_path),
                    path=patch_path,
                    before=original,
                    after=updated,
                    exists_before=True,
                    exists_after=True,
                    workspace_path=str(result.get("workspace_path") or workspace_path),
                    workspace_server_id=_context_workspace_server_id(context),
                )
            )
        count = int(result.get("replaced_occurrences") or 0)
        return ToolResult(
            success=True,
            data=result,
            summary=f"已修改文件 {relative_path}，替换 {count} 处文本",
            internal_data={"patches": internal_patches} if internal_patches else {},
        )
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _list_path_entries(
    path: str = "",
    recursive: bool = False,
    max_depth: int = 2,
    max_entries: int = 120,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        result = list_path_entries(
            path_input=path,
            workspace_path=_context_workspace(context),
            recursive=recursive,
            max_depth=max(1, min(max_depth, 8)),
            max_entries=max(20, min(max_entries, 400)),
        )
        return ToolResult(success=True, data=result, summary=f"已列出 {result.get('total_entries', 0)} 个目录条目")
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _glob_path_entries(
    pattern: str,
    path: str = "",
    limit: int = 40,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        result = glob_path_entries(
            pattern,
            path_input=path,
            workspace_path=_context_workspace(context),
            limit=max(1, min(limit, 400)),
        )
        summary = f"glob 命中 {result.get('count', 0)} 项"
        if bool(result.get("truncated")):
            summary += "（结果已截断，请缩小范围）"
        return ToolResult(success=True, data=result, summary=summary)
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _grep_path_contents(
    pattern: str,
    path: str = "",
    include: str | None = None,
    limit: int = 40,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        result = grep_path_contents(
            pattern,
            path_input=path,
            workspace_path=_context_workspace(context),
            include_glob=include,
            limit=max(1, min(limit, 400)),
        )
        summary = f"grep 命中 {result.get('count', 0)} 处"
        if bool(result.get("truncated")):
            summary += "（结果已截断，请缩小范围）"
        return ToolResult(success=True, data=result, summary=summary)
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _read_path(
    file_path: str,
    max_chars: int = 12000,
    offset: int | None = None,
    limit: int | None = None,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        result = read_path_file(
            file_path,
            workspace_path=_context_workspace(context),
            max_chars=max(1000, min(max_chars, 50000)),
            offset=offset,
            limit=limit,
        )
        target = result.get("relative_path") or result.get("path") or file_path
        return ToolResult(success=True, data=result, summary=f"已读取 {target}")
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _write_path(
    file_path: str,
    content: str,
    create_dirs: bool = True,
    overwrite: bool = True,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("write_workspace_file")
        workspace_path = _context_workspace(context)
        target = resolve_path_input(file_path, workspace_path=workspace_path, expect_dir=False)
        exists_before = target.exists()
        before = target.read_text(encoding="utf-8", errors="replace") if exists_before else ""
        result = write_path_file(
            file_path,
            content,
            workspace_path=workspace_path,
            create_dirs=create_dirs,
            overwrite=overwrite,
        )
        target = result.get("relative_path") or result.get("path") or file_path
        internal_patches = []
        if bool(result.get("changed")):
            internal_patches.append(
                _build_patch_record(
                    file_label=str(target),
                    path=str(result.get("path") or ""),
                    before=before,
                    after=content,
                    exists_before=exists_before,
                    exists_after=True,
                    workspace_path=workspace_path,
                    workspace_server_id=_context_workspace_server_id(context),
                )
            )
        return ToolResult(
            success=True,
            data=result,
            summary=f"已写入 {target}",
            internal_data={"patches": internal_patches} if internal_patches else {},
        )
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _edit_path(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("replace_workspace_text")
        workspace_path = _context_workspace(context)
        target_path = resolve_path_input(file_path, workspace_path=workspace_path, expect_dir=False)
        exists_before = target_path.exists()
        if exists_before and not target_path.is_file():
            raise WorkspaceAccessError(f"不是文件: {target_path}")
        original = target_path.read_text(encoding="utf-8", errors="replace") if exists_before else ""
        result = edit_path_file(
            file_path,
            old_string,
            new_string,
            workspace_path=workspace_path,
            replace_all=replace_all,
        )
        target = result.get("relative_path") or result.get("path") or file_path
        updated = Path(str(result.get("path") or target_path)).read_text(encoding="utf-8", errors="replace")
        internal_patches = []
        if bool(result.get("changed")):
            internal_patches.append(
                _build_patch_record(
                    file_label=str(target),
                    path=str(result.get("path") or ""),
                    before=original,
                    after=updated,
                    exists_before=exists_before,
                    exists_after=True,
                    workspace_path=workspace_path,
                    workspace_server_id=_context_workspace_server_id(context),
                )
            )
        return ToolResult(
            success=True,
            data=result,
            summary=f"已编辑 {target}",
            internal_data={"patches": internal_patches} if internal_patches else {},
        )
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))


def _multiedit_path(
    edits: list[dict],
    file_path: str = "",
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    normalized_edits = [item for item in (edits or []) if isinstance(item, dict)]
    if not normalized_edits:
        return ToolResult(success=False, summary="multiedit 至少需要一条 edit")

    workspace_path = _context_workspace(context)
    results: list[dict] = []
    touched: list[str] = []
    originals: dict[str, tuple[str, str]] = {}
    try:
        ensure_workspace_operation_allowed("replace_workspace_text")
        for index, edit in enumerate(normalized_edits, start=1):
            target_path = str(edit.get("file_path") or file_path or "").strip()
            if not target_path:
                raise WorkspaceAccessError(f"第 {index} 条 edit 缺少 file_path")
            resolved_before = resolve_path_input(target_path, workspace_path=workspace_path, expect_dir=False)
            resolved_key = str(resolved_before)
            if resolved_key not in originals:
                existed_before = resolved_before.exists()
                if existed_before and not resolved_before.is_file():
                    raise WorkspaceAccessError(f"不是文件: {resolved_before}")
                originals[resolved_key] = (
                    str(edit.get("file_path") or target_path),
                    resolved_before.read_text(encoding="utf-8", errors="replace") if existed_before else "",
                )
            result = edit_path_file(
                target_path,
                str(edit.get("old_string") or ""),
                str(edit.get("new_string") or ""),
                workspace_path=workspace_path,
                replace_all=bool(edit.get("replace_all")),
            )
            resolved_target = str(result.get("relative_path") or result.get("path") or target_path)
            if resolved_target not in touched:
                touched.append(resolved_target)
            results.append(
                {
                    "index": index,
                    "file_path": resolved_target,
                    "replaced_occurrences": int(result.get("replaced_occurrences") or 0),
                    "changed": bool(result.get("changed")),
                }
            )
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))

    total_replacements = sum(int(item.get("replaced_occurrences") or 0) for item in results)
    internal_patches: list[dict] = []
    for absolute_path, (display_path, before) in originals.items():
        final_text = Path(absolute_path).read_text(encoding="utf-8", errors="replace")
        if final_text == before:
            continue
        internal_patches.append(
            _build_patch_record(
                file_label=str(display_path),
                path=absolute_path,
                before=before,
                after=final_text,
                exists_before=True,
                exists_after=True,
                workspace_path=workspace_path,
                workspace_server_id=_context_workspace_server_id(context),
            )
        )
    return ToolResult(
        success=True,
        data={
            "count": len(results),
            "files": touched,
            "results": results,
            "total_replacements": total_replacements,
        },
        summary=f"已完成 {len(results)} 条编辑，涉及 {len(touched)} 个文件",
        internal_data={"patches": internal_patches} if internal_patches else {},
    )


def _apply_patch_text(
    patchText: str,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    workspace_path = _context_workspace(context)
    try:
        ensure_workspace_operation_allowed("replace_workspace_text")
        hunks = parse_patch(patchText)
        if not hunks:
            normalized = str(patchText or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            if normalized == "*** Begin Patch\n*** End Patch":
                raise WorkspaceAccessError("patch rejected: empty patch")
            raise WorkspaceAccessError("apply_patch verification failed: no hunks found")
    except (ValueError, WorkspaceAccessError) as exc:
        return ToolResult(success=False, summary=f"apply_patch verification failed: {exc}")

    changes: list[dict[str, Any]] = []
    try:
        for hunk in hunks:
            source_ref = str(hunk.get("path") or "").strip()
            if not source_ref:
                raise WorkspaceAccessError("patch 缺少文件路径")
            source_path = resolve_path_input(source_ref, workspace_path=workspace_path, expect_dir=False)
            if str(hunk.get("type") or "") == "add":
                if source_path.exists():
                    raise WorkspaceAccessError(f"文件已存在: {source_path}")
                new_content = str(hunk.get("contents") or "")
                if not new_content.endswith("\n"):
                    new_content = f"{new_content}\n"
                changes.append(
                    {
                        "type": "add",
                        "source_path": source_path,
                        "target_path": source_path,
                        "before": "",
                        "after": new_content,
                    }
                )
                continue
            if str(hunk.get("type") or "") == "delete":
                if not source_path.exists() or not source_path.is_file():
                    raise WorkspaceAccessError(f"文件不存在: {source_path}")
                changes.append(
                    {
                        "type": "delete",
                        "source_path": source_path,
                        "target_path": source_path,
                        "before": source_path.read_text(encoding="utf-8", errors="replace"),
                        "after": "",
                    }
                )
                continue

            if not source_path.exists() or not source_path.is_file():
                raise WorkspaceAccessError(f"Failed to read file to update: {source_path}")
            update = derive_new_contents_from_chunks(str(source_path), list(hunk.get("chunks") or []))
            move_ref = str(hunk.get("move_path") or "").strip() or None
            target_path = (
                resolve_path_input(move_ref, workspace_path=workspace_path, expect_dir=False)
                if move_ref
                else source_path
            )
            change_type = "move" if move_ref and target_path != source_path else "update"
            changes.append(
                {
                    "type": change_type,
                    "source_path": source_path,
                    "target_path": target_path,
                    "before": source_path.read_text(encoding="utf-8", errors="replace"),
                    "after": str(update.get("content") or ""),
                }
            )
    except (OSError, ValueError, WorkspaceAccessError) as exc:
        return ToolResult(success=False, summary=f"apply_patch verification failed: {exc}")

    summary_lines: list[str] = []
    touched_files: list[str] = []
    internal_patches: list[dict] = []
    workspace_server_id = _context_workspace_server_id(context)
    workspace_root = None
    if workspace_path:
        try:
            workspace_root = resolve_path_input("", workspace_path=workspace_path, expect_dir=True)
        except WorkspaceAccessError:
            workspace_root = None

    def _display(target: Path) -> str:
        if workspace_root is not None:
            try:
                return target.relative_to(workspace_root).as_posix()
            except ValueError:
                pass
        return str(target)

    try:
        for change in changes:
            change_type = str(change["type"])
            source_path = change["source_path"]
            target_path = change["target_path"]
            before = str(change["before"] or "")
            after = str(change["after"] or "")
            if change_type == "add":
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(after, encoding="utf-8")
                display = _display(target_path)
                summary_lines.append(f"A {display}")
                touched_files.append(display)
                internal_patches.append(
                    _build_patch_record(
                        file_label=display,
                        path=str(target_path),
                        before="",
                        after=after,
                        exists_before=False,
                        exists_after=True,
                        workspace_path=workspace_path,
                        workspace_server_id=workspace_server_id,
                    )
                )
                continue
            if change_type == "delete":
                if source_path.exists():
                    source_path.unlink()
                display = _display(source_path)
                summary_lines.append(f"D {display}")
                touched_files.append(display)
                internal_patches.append(
                    _build_patch_record(
                        file_label=display,
                        path=str(source_path),
                        before=before,
                        after="",
                        exists_before=True,
                        exists_after=False,
                        workspace_path=workspace_path,
                        workspace_server_id=workspace_server_id,
                    )
                )
                continue
            if change_type == "move":
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(after, encoding="utf-8")
                if source_path.exists() and source_path != target_path:
                    source_path.unlink()
                source_display = _display(source_path)
                target_display = _display(target_path)
                summary_lines.append(f"M {target_display}")
                touched_files.extend([item for item in (source_display, target_display) if item not in touched_files])
                internal_patches.append(
                    _build_patch_record(
                        file_label=source_display,
                        path=str(source_path),
                        before=before,
                        after="",
                        exists_before=True,
                        exists_after=False,
                        workspace_path=workspace_path,
                        workspace_server_id=workspace_server_id,
                    )
                )
                internal_patches.append(
                    _build_patch_record(
                        file_label=target_display,
                        path=str(target_path),
                        before="",
                        after=after,
                        exists_before=False,
                        exists_after=True,
                        workspace_path=workspace_path,
                        workspace_server_id=workspace_server_id,
                    )
                )
                continue
            source_path.write_text(after, encoding="utf-8")
            display = _display(source_path)
            summary_lines.append(f"M {display}")
            touched_files.append(display)
            internal_patches.append(
                _build_patch_record(
                    file_label=display,
                    path=str(source_path),
                    before=before,
                    after=after,
                    exists_before=True,
                    exists_after=True,
                    workspace_path=workspace_path,
                    workspace_server_id=workspace_server_id,
                )
            )
    except OSError as exc:
        return ToolResult(success=False, summary=f"apply_patch failed: {exc}")

    touched_files = list(dict.fromkeys(touched_files))
    return ToolResult(
        success=True,
        data={
            "count": len(summary_lines),
            "files": touched_files,
            "summary": summary_lines,
        },
        summary="Success. Updated the following files:\n" + "\n".join(summary_lines),
        internal_data={"patches": internal_patches} if internal_patches else {},
    )


def _local_shell_command(
    action: dict,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    if not isinstance(action, dict):
        return ToolResult(success=False, summary="local_shell 缺少 action")

    action_type = str(action.get("type") or "").strip().lower()
    if action_type != "exec":
        return ToolResult(success=False, summary=f"暂不支持的 local_shell action: {action_type or '[empty]'}")

    command_parts = action.get("command")
    if not isinstance(command_parts, list) or not command_parts:
        return ToolResult(success=False, summary="local_shell.command 不能为空")

    user = str(action.get("user") or "").strip()
    if user:
        return ToolResult(success=False, summary="当前 runtime 暂不支持 local_shell 指定 user 执行")

    working_directory = str(action.get("workingDirectory") or action.get("working_directory") or "").strip() or None
    timeout_ms = action.get("timeoutMs")
    if timeout_ms is None:
        timeout_ms = action.get("timeout_ms")
    try:
        timeout_sec = max(1, min(int(timeout_ms or 120000), 4 * 3600 * 1000)) / 1000
    except (TypeError, ValueError):
        timeout_sec = 120

    try:
        result = run_local_shell_command(
            command_parts,
            workspace_path=_context_workspace(context),
            workdir=working_directory,
            timeout_sec=timeout_sec,
            env=action.get("env") if isinstance(action.get("env"), dict) else None,
        )
        output_parts = [str(result.get("stdout") or "").strip(), str(result.get("stderr") or "").strip()]
        output = "\n".join(part for part in output_parts if part)
        exit_code = result.get("exit_code")
        if exit_code not in (None, 0):
            suffix = f"[exit_code={exit_code}]"
            output = f"{output}\n{suffix}" if output else suffix
        payload = {
            **result,
            "output": output,
        }
        success = bool(result.get("success"))
        summary = f"local_shell 执行{'成功' if success else '失败'}，退出码 {exit_code}"
        return ToolResult(success=success, data=payload, summary=summary)
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, summary="local_shell 执行超时")


def _bash_command(
    command: str,
    workdir: str | None = None,
    timeout_sec: int = 120,
    background: bool = False,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    try:
        ensure_workspace_operation_allowed("run_workspace_command", command=command)
        result = run_path_command(
            command,
            workspace_path=_context_workspace(context),
            workdir=workdir,
            timeout_sec=max(5, min(timeout_sec, 4 * 3600)),
            background=background,
        )
        if background:
            return ToolResult(success=True, data=result, summary=f"后台任务已提交：{result.get('task_id')}")
        success = bool(result.get("success"))
        if not success and str(result.get("error_code") or "") == "POWERSHELL_WRAPPER_PARSE":
            return ToolResult(
                success=False,
                data=result,
                summary="命令被 PowerShell 外层解析失败；请改用更简单的单行命令，或直接使用 read/glob/grep。",
            )
        return ToolResult(
            success=success,
            data=result,
            summary=f"命令执行{'成功' if success else '失败'}，退出码 {result.get('exit_code')}",
        )
    except WorkspaceAccessError as exc:
        return ToolResult(success=False, summary=str(exc))
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, summary="命令执行超时")




