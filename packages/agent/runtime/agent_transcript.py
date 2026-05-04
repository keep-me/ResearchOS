"""Shared transcript and tool-result recovery helpers for native/claw runtimes."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from packages.agent.runtime.agent_runtime_policy import (
    is_tool_progress_placeholder_text as _shared_is_tool_progress_placeholder_text,
)


def json_loads_maybe(payload: Any) -> Any | None:
    if isinstance(payload, (dict, list, int, float, bool)) or payload is None:
        return payload
    text = str(payload or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def summarize_tool_text(text: str, *, max_len: int = 180) -> str:
    compact = re.sub(r"\s+", " ", str(text or "").strip())
    if not compact:
        return ""
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 1]}…"


def serialize_tool_context_data(
    value: Any,
    *,
    max_len: int = 1200,
) -> str:
    if value is None:
        return ""
    if isinstance(value, str) and not value:
        return ""
    if isinstance(value, (list, dict)) and not value:
        return ""
    if isinstance(value, str):
        return summarize_tool_text(value, max_len=max_len)
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        raw = str(value)
    return summarize_tool_text(raw, max_len=max_len)


def _extract_tool_message_fields(
    message: dict[str, Any],
    *,
    stringify_message_content: Callable[[object], str],
) -> tuple[str, str, str, bool]:
    tool_name = str(message.get("name") or message.get("tool_name") or "tool").strip() or "tool"
    parsed = json_loads_maybe(message.get("content"))
    summary = ""
    details = ""
    is_error = False

    if isinstance(parsed, dict):
        summary = str(parsed.get("summary") or "").strip()
        details = serialize_tool_context_data(parsed.get("data"))
        success = parsed.get("success")
        if success is not None:
            is_error = not bool(success)
        else:
            is_error = bool(parsed.get("is_error"))
    else:
        details = serialize_tool_context_data(stringify_message_content(message.get("content")))

    return tool_name, summary, details, is_error


def format_tool_message_context(
    message: dict[str, Any],
    *,
    stringify_message_content: Callable[[object], str],
    recovered: bool = False,
) -> str:
    tool_name, summary, details, is_error = _extract_tool_message_fields(
        message,
        stringify_message_content=stringify_message_content,
    )
    status = "error output" if is_error else "result"
    header = (
        f"Recovered {status} from an earlier `{tool_name}` tool call."
        if recovered
        else f"{'Error output' if is_error else 'Result'} from `{tool_name}` tool call."
    )
    lines = [header]
    if summary:
        lines.append(f"Summary: {summary}")
    if details:
        lines.append(f"Data: {details}")
    return "\n".join(lines)


def format_orphan_tool_message_context(
    message: dict[str, Any],
    *,
    stringify_message_content: Callable[[object], str],
) -> str:
    return format_tool_message_context(
        message,
        stringify_message_content=stringify_message_content,
        recovered=True,
    )


def _extract_tool_call_arguments(raw_call: dict[str, Any]) -> str:
    function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
    raw_arguments = function.get("arguments")
    if raw_arguments is None:
        raw_arguments = raw_call.get("input")
    parsed = json_loads_maybe(raw_arguments)
    if parsed is None:
        parsed = raw_arguments
    return serialize_tool_context_data(parsed, max_len=400)


def _assistant_tool_call_blocks(message: dict[str, Any]) -> list[str]:
    raw_tool_calls = message.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []
    blocks: list[str] = []
    for raw_call in raw_tool_calls:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
        tool_name = (
            str(
                function.get("name") or raw_call.get("name") or raw_call.get("tool_name") or "tool"
            ).strip()
            or "tool"
        )
        arguments = _extract_tool_call_arguments(raw_call)
        lines = [f"Called tool `{tool_name}`."]
        if arguments:
            lines.append(f"Arguments: {arguments}")
        blocks.append("\n".join(lines))
    return blocks


def _tool_result_summary_line(item: dict[str, Any]) -> tuple[str, str]:
    tool_name = str(item.get("name") or item.get("tool_name") or "tool").strip() or "tool"
    success_value = item.get("success")
    if success_value is None:
        success_value = not bool(item.get("is_error"))
    summary = str(item.get("summary") or "").strip()
    if summary:
        return tool_name, summary

    raw_output = item.get("output")
    parsed_output = json_loads_maybe(raw_output)
    if isinstance(parsed_output, dict):
        summary = str(parsed_output.get("summary") or parsed_output.get("message") or "").strip()
        if not summary:
            summary = serialize_tool_context_data(parsed_output.get("data"), max_len=220)
        if not summary:
            summary = summarize_tool_text(str(raw_output or ""), max_len=220)
    elif raw_output is not None:
        summary = summarize_tool_text(str(raw_output), max_len=220)

    if not summary:
        summary = "工具调用完成" if bool(success_value) else "工具调用失败"
    return tool_name, summary


def format_tool_result_turn_summary(
    tool_results: list[dict[str, Any]],
    *,
    heading: str = "本轮已完成以下工具调用：",
    limit: int = 5,
) -> str:
    lines: list[str] = []
    for index, item in enumerate(tool_results[: max(limit, 0)], start=1):
        if not isinstance(item, dict):
            continue
        tool_name, summary = _tool_result_summary_line(item)
        lines.append(f"{index}. {tool_name}: {summary}")
    if not lines:
        return ""
    return "\n".join([heading, *lines])


def collect_tool_result_items_from_messages(
    messages: list[dict[str, Any]],
    *,
    limit: int = 5,
    include_skipped: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "tool":
            continue
        parsed = json_loads_maybe(message.get("content"))
        if not isinstance(parsed, dict):
            continue
        data = copy.deepcopy(parsed.get("data"))
        if not include_skipped and isinstance(data, dict) and bool(data.get("skipped")):
            continue
        items.append(
            {
                "id": str(message.get("tool_call_id") or "").strip() or None,
                "name": str(message.get("name") or message.get("tool_name") or "tool").strip()
                or "tool",
                "success": bool(parsed.get("success", not bool(parsed.get("is_error")))),
                "summary": str(parsed.get("summary") or "").strip(),
                "data": data,
            }
        )
        if limit > 0 and len(items) >= limit:
            break
    return items


@dataclass(frozen=True)
class ToolResultFollowupText:
    final_text: str
    summary_text: str
    synthesized: bool
    appended_summary: bool


def resolve_tool_result_followup_text(
    primary_text: str | None,
    tool_results: list[dict[str, Any]],
) -> ToolResultFollowupText:
    base_text = str(primary_text or "").strip()
    summary_text = format_tool_result_turn_summary(tool_results).strip()
    if not summary_text:
        return ToolResultFollowupText(
            final_text=base_text,
            summary_text="",
            synthesized=False,
            appended_summary=False,
        )
    if not base_text:
        return ToolResultFollowupText(
            final_text=summary_text,
            summary_text=summary_text,
            synthesized=True,
            appended_summary=False,
        )
    if _shared_is_tool_progress_placeholder_text(base_text) and summary_text not in base_text:
        return ToolResultFollowupText(
            final_text=f"{base_text.rstrip()}\n\n{summary_text}".strip(),
            summary_text=summary_text,
            synthesized=False,
            appended_summary=True,
        )
    return ToolResultFollowupText(
        final_text=base_text,
        summary_text=summary_text,
        synthesized=False,
        appended_summary=False,
    )


@dataclass(frozen=True)
class CliTranscript:
    entries: list[str]
    latest_user_text: str


def build_cli_chat_prompt_text(
    *,
    backend_label: str,
    mode_instruction: str,
    context_sections: list[str],
    transcript_entries: list[str],
    latest_user_system: str = "",
    latest_user_output_constraint: str = "",
) -> str:
    lines = [
        f"你现在是在 ResearchOS 研究助手中工作的 {backend_label}。",
        "请延续下面的对话继续回答最后一条用户消息。",
        "默认使用简体中文，回答风格简洁、可靠、可执行，不要输出额外前缀。",
        mode_instruction,
        "如果你修改了文件、运行了命令或完成了验证，请在回复里明确说明你实际做了什么。",
        "如果因为环境、权限、CLI 或远程依赖受阻，请明确给出真实阻塞点，不要编造成功。",
        "你当前能调用的工具都通过 ResearchOS MCP 暴露；如果工具名带 `mcp__ResearchOS__` 前缀，直接按描述调用即可。",
    ]
    if context_sections:
        lines.extend(
            [
                "",
                "以下是本轮系统上下文：",
                "\n\n".join(context_sections),
            ]
        )
    if latest_user_system:
        lines.extend(
            [
                "",
                "以下是最后一条用户消息附带的额外系统指令（必须继续遵守）：",
                latest_user_system,
            ]
        )
    if latest_user_output_constraint:
        lines.extend(
            [
                "",
                "以下是最后一条用户消息附带的输出硬约束（必须继续遵守）：",
                latest_user_output_constraint,
            ]
        )
    lines.extend(
        [
            "",
            "以下是当前对话：",
            "\n\n".join(transcript_entries) if transcript_entries else "[User]\n你好",
            "",
            "请直接继续回复最后一条 User 消息。",
        ]
    )
    return "\n".join(lines)


def build_cli_transcript(
    messages: list[dict[str, Any]],
    *,
    stringify_message_content: Callable[[object], str],
    extract_user_text_content: Callable[[object], str],
) -> CliTranscript:
    transcript: list[str] = []
    latest_user_text = ""
    seen_tool_call_ids: set[str] = set()
    role_map = {
        "user": "User",
        "assistant": "Assistant",
        "tool": "Tool",
    }

    for message in messages:
        role = str(message.get("role") or "user")
        if role not in {"user", "assistant", "tool"}:
            role = "user"

        if role == "assistant":
            raw_tool_calls = message.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for raw_call in raw_tool_calls:
                    if not isinstance(raw_call, dict):
                        continue
                    call_id = str(raw_call.get("id") or "").strip()
                    if call_id:
                        seen_tool_call_ids.add(call_id)

        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            rendered = (
                format_orphan_tool_message_context(
                    message,
                    stringify_message_content=stringify_message_content,
                )
                if not tool_call_id or tool_call_id not in seen_tool_call_ids
                else format_tool_message_context(
                    message,
                    stringify_message_content=stringify_message_content,
                )
            )
            if rendered:
                label = (
                    "User" if not tool_call_id or tool_call_id not in seen_tool_call_ids else "Tool"
                )
                transcript.append(f"[{label}]\n{rendered}")
            continue

        content = stringify_message_content(message.get("content")).strip()
        blocks: list[str] = []
        if content:
            blocks.append(content)
        if role == "assistant":
            blocks.extend(_assistant_tool_call_blocks(message))
        if not blocks:
            continue
        rendered_blocks = "\n\n".join(blocks)
        transcript.append(f"[{role_map[role]}]\n{rendered_blocks}")
        if role == "user":
            latest_user_text = extract_user_text_content(message.get("content")).strip() or content

    if not transcript:
        transcript.append("[User]\n你好")
    return CliTranscript(entries=copy.deepcopy(transcript), latest_user_text=latest_user_text)
