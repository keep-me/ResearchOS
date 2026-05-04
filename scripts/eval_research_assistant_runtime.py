from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.routers import session_runtime as session_runtime_router
from packages.agent.workspace.workspace_executor import (
    get_assistant_exec_policy,
    update_assistant_exec_policy,
)


@dataclass
class EvalCase:
    name: str
    mode: str
    reasoning_level: str
    prompt: str
    session_id: str


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(session_runtime_router.router)
    return app


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in str(raw or "").split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        event_name = ""
        payload = {}
        for line in lines:
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                raw_data = line.split(":", 1)[1].strip()
                try:
                    payload = json.loads(raw_data) if raw_data else {}
                except json.JSONDecodeError:
                    payload = {"raw": raw_data}
        if event_name:
            items.append({"event": event_name, "data": payload})
    return items


def _text_from_parts(parts: list[dict[str, Any]] | None) -> str:
    return "".join(
        str(part.get("text") or "")
        for part in (parts or [])
        if str(part.get("type") or "") == "text"
    )


def _tool_parts(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(part) for part in (parts or []) if str(part.get("type") or "") == "tool"]


def _last_assistant_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if str(info.get("role") or "") == "assistant":
            return message
    return None


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in events:
        event_name = str(item.get("event") or "")
        counts[event_name] = counts.get(event_name, 0) + 1
    return counts


def _tool_names_from_events(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in events:
        event_name = str(item.get("event") or "")
        if event_name == "tool-input-start":
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            name = str(data.get("toolName") or "").strip()
            if name:
                names.append(name)
        elif event_name == "tool_start":
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            name = str(data.get("name") or "").strip()
            if name:
                names.append(name)
    deduped: list[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    return deduped


def _tool_result_summaries(events: list[dict[str, Any]]) -> list[str]:
    results: list[str] = []
    for item in events:
        if str(item.get("event") or "") != "tool_result":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        summary = str(data.get("summary") or "").strip()
        if summary:
            results.append(summary)
    return results


def _reasoning_text(events: list[dict[str, Any]]) -> str:
    return "".join(
        str((item.get("data") or {}).get("content") or "")
        for item in events
        if str(item.get("event") or "") == "reasoning_delta" and isinstance(item.get("data"), dict)
    )


def _usage_payloads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in events:
        if str(item.get("event") or "") != "usage":
            continue
        data = item.get("data")
        if isinstance(data, dict):
            payloads.append(dict(data))
    return payloads


def _run_case(client: TestClient, workspace: str, case: EvalCase) -> dict[str, Any]:
    client.post(
        "/session",
        json={
            "id": case.session_id,
            "directory": workspace,
            "workspace_path": workspace,
            "mode": case.mode,
            "title": case.name,
        },
    )
    started = time.perf_counter()
    response = client.post(
        f"/session/{case.session_id}/message",
        json={
            "parts": [{"type": "text", "text": case.prompt}],
            "mode": case.mode,
            "workspace_path": workspace,
            "reasoning_level": case.reasoning_level,
        },
    )
    duration_sec = round(time.perf_counter() - started, 2)
    events = _parse_sse(response.text)
    state = client.get(f"/session/{case.session_id}/state").json()
    messages = list(state.get("messages") or [])
    pending = list(state.get("permissions") or [])
    session_payload = dict(state.get("session") or {})
    last_assistant = _last_assistant_message(messages) or {}
    info = last_assistant.get("info") if isinstance(last_assistant.get("info"), dict) else {}
    tool_parts = _tool_parts(
        last_assistant.get("parts") if isinstance(last_assistant.get("parts"), list) else []
    )
    answer_text = _text_from_parts(
        last_assistant.get("parts") if isinstance(last_assistant.get("parts"), list) else []
    )
    persisted_reasoning = str(last_assistant.get("reasoning_content") or "")
    return {
        "name": case.name,
        "session_id": case.session_id,
        "http_status": response.status_code,
        "duration_sec": duration_sec,
        "mode": case.mode,
        "reasoning_level": case.reasoning_level,
        "event_counts": _event_counts(events),
        "tool_names": _tool_names_from_events(events),
        "tool_result_summaries": _tool_result_summaries(events),
        "reasoning_chars_stream": len(_reasoning_text(events)),
        "reasoning_chars_persisted": len(persisted_reasoning),
        "usage_events": _usage_payloads(events),
        "pending_permissions": len(pending),
        "session_mode_after": str(session_payload.get("mode") or ""),
        "assistant_finish": str(info.get("finish") or ""),
        "assistant_mode": str(info.get("mode") or ""),
        "assistant_variant": str(info.get("variant") or ""),
        "assistant_tokens": dict(info.get("tokens") or {})
        if isinstance(info.get("tokens"), dict)
        else {},
        "assistant_text": answer_text,
        "assistant_text_preview": answer_text[:1200],
        "assistant_tool_parts": [
            {
                "tool": str(part.get("tool") or ""),
                "status": str(
                    ((part.get("state") or {}) if isinstance(part.get("state"), dict) else {}).get(
                        "status"
                    )
                    or ""
                ),
                "summary": str(part.get("summary") or ""),
            }
            for part in tool_parts
        ],
        "errors": [
            dict(item.get("data") or {})
            for item in events
            if str(item.get("event") or "") == "error" and isinstance(item.get("data"), dict)
        ],
        "raw_events": events,
    }


def _run_mode_switch_case(client: TestClient, workspace: str, session_id: str) -> dict[str, Any]:
    client.post(
        "/session",
        json={
            "id": session_id,
            "directory": workspace,
            "workspace_path": workspace,
            "mode": "build",
            "title": "mode_switch_probe",
        },
    )
    first = _run_case(
        client,
        workspace,
        EvalCase(
            name="mode_switch_plan_turn",
            mode="plan",
            reasoning_level="medium",
            session_id=session_id,
            prompt=(
                "请读取 packages/ai/session_plan.py 和 packages/ai/agent_service.py "
                "中与 plan mode reminder 相关的实现，然后给我一个 3 步检查计划。先不要改代码。"
            ),
        ),
    )
    second_started = time.perf_counter()
    second_response = client.post(
        f"/session/{session_id}/message",
        json={
            "parts": [
                {
                    "type": "text",
                    "text": "现在切回 build 模式，基于你刚才的规划，指出第一步应该直接修改哪个文件、为什么。不要真的改代码。",
                }
            ],
            "mode": "build",
            "workspace_path": workspace,
            "reasoning_level": "medium",
        },
    )
    second_duration = round(time.perf_counter() - second_started, 2)
    second_events = _parse_sse(second_response.text)
    state = client.get(f"/session/{session_id}/state").json()
    messages = list(state.get("messages") or [])
    assistants = [
        message
        for message in messages
        if str(
            ((message.get("info") or {}) if isinstance(message.get("info"), dict) else {}).get(
                "role"
            )
            or ""
        )
        == "assistant"
    ]
    latest = assistants[-1] if assistants else {}
    latest_info = latest.get("info") if isinstance(latest.get("info"), dict) else {}
    return {
        "first_turn": first,
        "second_turn": {
            "http_status": second_response.status_code,
            "duration_sec": second_duration,
            "event_counts": _event_counts(second_events),
            "tool_names": _tool_names_from_events(second_events),
            "reasoning_chars_stream": len(_reasoning_text(second_events)),
            "session_mode_after": str((state.get("session") or {}).get("mode") or ""),
            "assistant_mode": str(latest_info.get("mode") or ""),
            "assistant_variant": str(latest_info.get("variant") or ""),
            "assistant_finish": str(latest_info.get("finish") or ""),
            "assistant_text_preview": _text_from_parts(
                latest.get("parts") if isinstance(latest.get("parts"), list) else []
            )[:1200],
            "errors": [
                dict(item.get("data") or {})
                for item in second_events
                if str(item.get("event") or "") == "error" and isinstance(item.get("data"), dict)
            ],
        },
    }


def main() -> int:
    workspace = str(ROOT)
    app = _build_app()
    client = TestClient(app)

    timestamp = int(time.time())
    cases = [
        EvalCase(
            name="build_route_param_flow_medium",
            mode="build",
            reasoning_level="medium",
            session_id=f"eval_build_route_{timestamp}",
            prompt=(
                "请先读取 apps/api/routers/session_runtime.py，并用简体中文说明 "
                "POST /session/{session_id}/message 这个接口如何把 mode 和 reasoning_level "
                "传到后端运行链。不要凭记忆回答。"
            ),
        ),
        EvalCase(
            name="build_bash_git_medium",
            mode="build",
            reasoning_level="medium",
            session_id=f"eval_build_bash_{timestamp}",
            prompt=(
                "请先运行一个只读命令检查当前仓库 git 状态，然后总结主要变更集中在哪些目录。"
                "不要修改任何文件，也不要凭记忆回答。"
            ),
        ),
        EvalCase(
            name="plan_mode_medium",
            mode="plan",
            reasoning_level="medium",
            session_id=f"eval_plan_{timestamp}",
            prompt=(
                "请读取 packages/ai/session_plan.py 和 packages/ai/agent_service.py "
                "中与 plan mode reminder 相关的实现，然后给我一个 3 步检查计划。先不要改代码。"
            ),
        ),
        EvalCase(
            name="reasoning_compare_low",
            mode="build",
            reasoning_level="low",
            session_id=f"eval_reason_low_{timestamp}",
            prompt=(
                "请读取 packages/ai/session_processor.py 和 packages/ai/agent_service.py，"
                "然后分析它们在 prompt lifecycle 与 tool execution 之间的职责分工，"
                "并指出两个最关键的稳定性风险。不要改代码。"
            ),
        ),
        EvalCase(
            name="reasoning_compare_high",
            mode="build",
            reasoning_level="high",
            session_id=f"eval_reason_high_{timestamp}",
            prompt=(
                "请读取 packages/ai/session_processor.py 和 packages/ai/agent_service.py，"
                "然后分析它们在 prompt lifecycle 与 tool execution 之间的职责分工，"
                "并指出两个最关键的稳定性风险。不要改代码。"
            ),
        ),
        EvalCase(
            name="symbol_lookup_build_plan_mode_reminder",
            mode="build",
            reasoning_level="medium",
            session_id=f"eval_symbol_lookup_{timestamp}",
            prompt=(
                "在当前仓库中找到 `build_plan_mode_reminder` 的定义和调用点，"
                "读取相关文件后用简体中文总结它的注入位置、作用、以及何时生效。"
                "不要凭记忆回答。"
            ),
        ),
    ]

    original_policy = get_assistant_exec_policy()
    update_assistant_exec_policy(
        {
            "workspace_access": "read_write",
            "command_execution": "full",
            "approval_mode": "off",
        }
    )

    report: dict[str, Any] = {
        "workspace": workspace,
        "llm_note": (
            "当前实际运行使用数据库中激活的 LLM 配置；此前探针表明当前聊天链路为 "
            "OpenAI-compatible 网关，model 为 qwen3.5-plus，Responses 不兼容时回退到 chat.completions。"
        ),
        "tool_smoke_baseline": None,
        "cases": [],
        "mode_switch": None,
    }

    try:
        for case in cases:
            print(f"[RUN] {case.name} ({case.mode}, reasoning={case.reasoning_level})", flush=True)
            result = _run_case(client, workspace, case)
            report["cases"].append(result)
            print(
                json.dumps(
                    {
                        "session_id": result["session_id"],
                        "http_status": result["http_status"],
                        "duration_sec": result["duration_sec"],
                        "tool_names": result["tool_names"],
                        "reasoning_chars_stream": result["reasoning_chars_stream"],
                        "assistant_finish": result["assistant_finish"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        print("[RUN] mode_switch_plan_to_build", flush=True)
        report["mode_switch"] = _run_mode_switch_case(
            client,
            workspace,
            f"eval_mode_switch_{timestamp}",
        )
    finally:
        update_assistant_exec_policy(original_policy)

    output_dir = ROOT / "tmp"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"assistant_runtime_eval_{timestamp}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "report_path": str(output_path),
        "cases": [
            {
                "name": item["name"],
                "session_id": item["session_id"],
                "duration_sec": item["duration_sec"],
                "tool_names": item["tool_names"],
                "reasoning_chars_stream": item["reasoning_chars_stream"],
                "answer_chars": len(str(item["assistant_text"] or "")),
                "pending_permissions": item["pending_permissions"],
                "errors": item["errors"],
            }
            for item in report["cases"]
        ],
        "mode_switch": {
            "plan_session_mode_after": report["mode_switch"]["first_turn"]["session_mode_after"]
            if isinstance(report["mode_switch"], dict)
            else None,
            "build_session_mode_after": report["mode_switch"]["second_turn"]["session_mode_after"]
            if isinstance(report["mode_switch"], dict)
            else None,
        },
    }
    print("---")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
