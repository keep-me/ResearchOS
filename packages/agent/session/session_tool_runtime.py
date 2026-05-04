from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from packages.agent.runtime.agent_runtime_state import (
    ensure_session,
    get_todos,
    normalize_mode,
    update_todos,
)
from packages.agent.session.session_plan import plan_exit_followup_text, resolve_session_plan_info
from packages.agent.session.session_runtime import (
    append_session_message,
    build_user_message_meta,
    ensure_session_record,
)
from packages.agent.tools.tool_context import context_session_id, context_workspace
from packages.agent.tools.tool_runtime import AgentToolContext, ToolResult
from packages.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _todo_read(*, context: AgentToolContext | None = None) -> ToolResult:
    state = ensure_session(
        context_session_id(context),
        mode=context.mode if context else "build",
        workspace_path=context_workspace(context),
    )
    todos = get_todos(state.session_id)
    open_count = sum(1 for item in todos if str(item.get("status") or "") != "completed")
    return ToolResult(
        success=True,
        data={
            "session_id": state.session_id,
            "todos": todos,
            "count": len(todos),
            "open_count": open_count,
        },
        summary=f"当前有 {open_count} 个未完成待办",
    )


def _todo_write(todos: list[dict], *, context: AgentToolContext | None = None) -> ToolResult:
    state = ensure_session(
        context_session_id(context),
        mode=context.mode if context else "build",
        workspace_path=context_workspace(context),
    )
    updated = update_todos(state.session_id, todos)
    open_count = sum(1 for item in updated if str(item.get("status") or "") != "completed")
    return ToolResult(
        success=True,
        data={
            "session_id": state.session_id,
            "todos": updated,
            "count": len(updated),
            "open_count": open_count,
        },
        summary=f"已更新待办列表，剩余 {open_count} 个未完成项",
    )


def _task_subagent(
    description: str,
    prompt: str,
    subagent_type: str = "general",
    task_id: str | None = None,
    *,
    context: AgentToolContext | None = None,
) -> ToolResult:
    child_id = str(task_id or "").strip() or f"{context_session_id(context)}:{uuid4().hex[:8]}"
    child_mode = normalize_mode(subagent_type)
    parent_workspace = context_workspace(context)
    state = ensure_session(
        child_id,
        mode=child_mode,
        workspace_path=parent_workspace,
    )

    system_prompt = (
        "你是 ResearchOS 的轻量子代理。请使用简体中文，直接给出可执行结论。"
        f" 当前模式为 {child_mode}。"
        " 如果是 plan 模式，重点输出分析与计划，不要假装已执行文件修改。"
        " 如果是 explore 模式，只做只读探索、定位实现与总结证据，不要产出执行性修改。"
        " 如果是 build/general 模式，优先产出下一步动作、潜在风险和建议工具。"
    )
    if state.workspace_path:
        system_prompt += f" 当前工作区：{state.workspace_path}。"

    llm = LLMClient()
    parts: list[str] = []
    try:
        for event in llm.chat_stream(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"子任务：{description}\n\n请处理以下请求：\n{prompt}",
                },
            ],
            tools=[],
            session_cache_key=child_id,
        ):
            if event.type == "text_delta" and event.content:
                parts.append(event.content)
            elif event.type == "error":
                return ToolResult(success=False, summary=event.content or "子任务执行失败")
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception("Subagent task failed: %s", exc)
        return ToolResult(success=False, summary=str(exc))

    content = "".join(parts).strip()
    return ToolResult(
        success=True,
        data={
            "task_id": child_id,
            "mode": child_mode,
            "description": description,
            "content": content,
        },
        summary=f"已生成子任务结果：{description}",
    )


def _plan_exit(*, context: AgentToolContext | None = None) -> ToolResult:
    session_id = context_session_id(context)
    session_payload = ensure_session_record(session_id)
    plan_info = resolve_session_plan_info(session_payload)
    if plan_info is None:
        return ToolResult(
            success=False, summary="当前 session 缺少计划文件上下文，无法退出 plan 模式"
        )

    if plan_info.storage == "local":
        try:
            plan_path = Path(plan_info.path)
            if not plan_path.exists():
                return ToolResult(success=False, summary=f"计划文件不存在：{plan_info.path}")
        except OSError as exc:
            return ToolResult(success=False, summary=f"无法访问计划文件：{exc}")

    updated_session = ensure_session_record(
        session_id,
        directory=str(session_payload.get("directory") or ""),
        workspace_path=str(session_payload.get("workspace_path") or ""),
        workspace_server_id=str(session_payload.get("workspace_server_id") or "") or None,
        title=str(session_payload.get("title") or "") or None,
        mode="build",
    )

    if context is not None and context.runtime_options is not None:
        try:
            context.runtime_options.mode = "build"
        except Exception:
            pass
        context.mode = "build"

    followup_text = plan_exit_followup_text(updated_session)
    append_session_message(
        session_id=session_id,
        role="user",
        content=followup_text,
        meta=build_user_message_meta(
            agent="build",
            fallback_agent="build",
        ),
    )
    return ToolResult(
        success=True,
        data={
            "mode": "build",
            "plan_path": plan_info.path,
        },
        summary="计划已获批准，正在切换到 build 模式继续执行",
    )


def _question(*, context: AgentToolContext | None = None, **_kwargs) -> ToolResult:
    del context
    return ToolResult(
        success=False,
        summary="question 工具应先暂停并等待用户回答，不应直接执行到本地 handler",
    )
