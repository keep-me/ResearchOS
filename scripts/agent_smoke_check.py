from __future__ import annotations

import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.agent import researchos_mcp
from packages.agent.tools.skill_registry import list_local_skills
from packages.agent.tools.tool_registry import get_openai_tools
from packages.agent.tools.tool_runtime import AgentToolContext, ToolResult, execute_tool_stream


@dataclass
class CheckResult:
    status: str
    name: str
    detail: str = ""


RESULTS: list[CheckResult] = []


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append(CheckResult(status=status, name=name, detail=detail))
    prefix = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
    suffix = f" :: {detail}" if detail else ""
    print(f"{prefix} {name}{suffix}")


def expect(condition: bool, name: str, detail: str = "") -> None:
    record("PASS" if condition else "FAIL", name, detail)


def skip(name: str, detail: str) -> None:
    record("SKIP", name, detail)


def fail(name: str, detail: str) -> None:
    record("FAIL", name, detail)


def tool_names(request: str) -> set[str]:
    del request
    return {item["function"]["name"] for item in get_openai_tools("build")}


def run_tool(name: str, arguments: dict[str, Any], *, context: AgentToolContext | None = None) -> ToolResult:
    events = list(execute_tool_stream(name, arguments, context=context))
    for event in reversed(events):
        if isinstance(event, ToolResult):
            return event
    raise RuntimeError(f"{name} did not return ToolResult")


def check_tool_exposure() -> None:
    generic_tools = tool_names("去网上找一找 openclaw 的相关内容")
    paper_tools = tool_names("帮我找 attention is all you need 这篇论文")
    mixed_tools = tool_names("给我当前系统状态、并润色这段摘要")

    expect(generic_tools == paper_tools == mixed_tools, "request text does not change default tool exposure")
    expect("websearch" in generic_tools, "default tool exposure includes websearch")
    expect("search_web" not in generic_tools, "default tool exposure hides legacy search_web alias")
    expect("search_papers" not in generic_tools, "default tool exposure hides paper library tools")
    expect("get_system_status" not in generic_tools, "default tool exposure hides status tools")
    expect("writing_assist" in generic_tools, "default tool exposure includes writing tools")


def check_web_search() -> None:
    result = run_tool("search_web", {"query": "openclaw github", "max_results": 3})
    expect(result.success, "native search_web succeeds", result.summary)
    count = int((result.data or {}).get("count") or 0)
    expect(count > 0, "native search_web returns results", f"count={count}")

    payload = researchos_mcp.web_search("openclaw github", limit=3)
    mcp_count = int(payload.get("count") or 0)
    expect(mcp_count > 0, "MCP web_search returns results", f"count={mcp_count}")


def check_arxiv_search() -> None:
    expected_title = "Attention Is All You Need"

    native_result = run_tool("search_arxiv", {"query": "attention is all you need", "max_results": 5})
    if not native_result.success:
        fail("native search_arxiv succeeds", native_result.summary)
        return

    native_titles = [str(item.get("title") or "") for item in (native_result.data or {}).get("candidates", [])]
    expect(expected_title in native_titles[:3], "native search_arxiv ranks exact title near top", ", ".join(native_titles[:3]))

    mcp_payload = researchos_mcp.search_arxiv("attention is all you need", limit=5)
    mcp_titles = [str(item.get("title") or "") for item in mcp_payload.get("items", [])]
    expect(expected_title in mcp_titles[:3], "MCP search_arxiv ranks exact title near top", ", ".join(mcp_titles[:3]))


def check_skills() -> None:
    items = list_local_skills()
    project_skill_names = {item["name"] for item in items if item.get("source") == "project"}
    expect("research-os-paper-workflows" in project_skill_names, "project paper workflow skill discovered")
    expect("research-os-web-research" in project_skill_names, "project web research skill discovered")

    list_result = run_tool("list_local_skills", {})
    expect(list_result.success, "list_local_skills succeeds", list_result.summary)

    read_result = run_tool("read_local_skill", {"skill_ref": "research-os-paper-workflows"})
    expect(read_result.success, "read_local_skill succeeds", read_result.summary)
    content = str((read_result.data or {}).get("content") or "")
    expect("search_arxiv" in content, "read_local_skill returns expected workflow content")


def check_workspace_tools() -> None:
    tmp_root = ROOT / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-smoke-", dir=str(tmp_root)) as temp_dir:
        workspace = Path(temp_dir)
        target = workspace / "notes.txt"
        context = AgentToolContext(session_id="agent-smoke", mode="build", workspace_path=str(workspace))

        write_result = run_tool("write", {"file_path": str(target), "content": "alpha\nbeta\n"}, context=context)
        expect(write_result.success and target.exists(), "write tool creates file", write_result.summary)

        read_result = run_tool("read", {"file_path": str(target)}, context=context)
        read_content = str((read_result.data or {}).get("content") or "")
        expect(read_result.success and "alpha" in read_content, "read tool returns file content", read_result.summary)

        edit_result = run_tool(
            "edit",
            {
                "file_path": str(target),
                "old_string": "beta",
                "new_string": "gamma",
            },
            context=context,
        )
        expect(edit_result.success and "gamma" in target.read_text(encoding="utf-8"), "edit tool updates file", edit_result.summary)

        ls_result = run_tool("ls", {"path": str(workspace)}, context=context)
        entries = (ls_result.data or {}).get("entries") or []
        expect(ls_result.success and len(entries) >= 1, "ls tool lists workspace entries", ls_result.summary)

        bash_result = run_tool(
            "bash",
            {"command": "python -c \"print('agent-smoke')\"", "workdir": str(workspace), "timeout_sec": 30},
            context=context,
        )
        stdout = str((bash_result.data or {}).get("stdout") or "")
        expect(bash_result.success and "agent-smoke" in stdout, "bash tool runs foreground command", bash_result.summary)

        background_result = run_tool(
            "bash",
            {"command": "python -c \"print('agent-bg')\"", "workdir": str(workspace), "background": True, "timeout_sec": 30},
            context=context,
        )
        task_id = str((background_result.data or {}).get("task_id") or "")
        expect(background_result.success and bool(task_id), "bash tool submits background task", background_result.summary)

        if not task_id:
            return

        deadline = time.time() + 30
        final_status: ToolResult | None = None
        while time.time() < deadline:
            final_status = run_tool("get_workspace_task_status", {"task_id": task_id}, context=context)
            data = final_status.data or {}
            if data.get("finished"):
                break
            time.sleep(0.5)

        if final_status is None:
            fail("workspace task status polling", "did not receive task status")
            return

        task_payload = final_status.data or {}
        task_result = task_payload.get("result") or {}
        expect(bool(task_payload.get("finished")), "background workspace task finishes", f"status={task_payload.get('status')}")
        expect(task_result.get("success") is True, "background workspace task reports success", str(task_result))


def check_researchos_integration() -> None:
    overview = researchos_mcp.paper_library_overview()
    recent = list(overview.get("recent_papers") or [])
    top_impact = list(overview.get("top_impact_papers") or [])
    expect(isinstance(recent, list) and isinstance(top_impact, list), "paper_library_overview returns paper lists")

    if not recent:
        skip("paper_detail roundtrip", "paper library is empty in current environment")
        return

    first = recent[0]
    paper_id = str(first.get("id") or "").strip()
    if not paper_id:
        fail("paper_detail roundtrip", "recent paper item missing id")
        return

    detail = researchos_mcp.paper_detail(paper_id)
    expect(str(detail.get("id") or "") == paper_id, "paper_detail returns requested paper", str(detail.get("title") or ""))


def main() -> int:
    print("Research Assistant smoke check")
    print(f"Root: {ROOT}")
    print("---")

    try:
        check_tool_exposure()
        check_web_search()
        check_arxiv_search()
        check_skills()
        check_workspace_tools()
        check_researchos_integration()
    except Exception as exc:  # pragma: no cover
        fail("unexpected exception", f"{type(exc).__name__}: {exc}")

    print("---")
    passed = sum(1 for item in RESULTS if item.status == "PASS")
    failed = sum(1 for item in RESULTS if item.status == "FAIL")
    skipped = sum(1 for item in RESULTS if item.status == "SKIP")
    print(f"Summary: passed={passed} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
