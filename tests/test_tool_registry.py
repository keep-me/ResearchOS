from __future__ import annotations

from pathlib import Path

from packages.agent import (
    researchos_mcp_registry,
    session_plan,
    tool_registry,
)
from packages.agent.tools.tool_runtime import AgentToolContext, ToolResult, execute_tool_stream
from packages.agent.tools.tool_schema import ToolDef, ToolSpec


def test_tool_registry_custom_registration_exposes_and_executes_tool():
    tool_registry.reset_custom_tools()

    def _custom_echo(value: str, **_kwargs):  # noqa: ANN003
        return ToolResult(success=True, summary="custom ok", data={"value": value})

    tool_registry.register_tool(
        ToolDef(
            name="custom_echo",
            description="Custom echo tool.",
            parameters={
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
                "required": ["value"],
            },
        ),
        handler=_custom_echo,
    )

    try:
        definition = tool_registry.get_tool_definition("custom_echo")
        assert definition is not None
        assert definition.description == "Custom echo tool."

        tool_names = {
            item["function"]["name"]
            for item in tool_registry.get_openai_tools("build", enabled_tools={"custom_echo"})
        }
        assert "custom_echo" in tool_names

        events = list(execute_tool_stream("custom_echo", {"value": "hello"}))
        assert len(events) == 1
        assert isinstance(events[0], ToolResult)
        assert events[0].success is True
        assert events[0].summary == "custom ok"
        assert events[0].data["value"] == "hello"
    finally:
        tool_registry.reset_custom_tools()


def test_tool_registry_builtin_definition_carries_permission_spec():
    definition = tool_registry.get_tool_definition("bash")

    assert definition is not None
    assert tool_registry.tool_source("bash") == "builtin"
    assert definition.spec.permission == "bash"
    assert definition.spec.managed_permission is True
    assert definition.spec.default_local_enabled is True
    assert definition.spec.allow_in_read_only is False
    assert tool_registry.tool_permission("bash") == "bash"
    assert tool_registry.manages_tool("bash") is True


def test_tool_registry_research_tools_share_the_catalog_surface() -> None:
    research_definition = tool_registry.get_tool_definition("search_papers")
    literature_definition = tool_registry.get_tool_definition("search_literature")
    research_wiki_definition = tool_registry.get_tool_definition("research_wiki_query")
    compat_definition = tool_registry.get_tool_definition("local_shell")

    assert research_definition is not None
    assert literature_definition is not None
    assert research_wiki_definition is not None
    assert compat_definition is not None
    assert tool_registry.tool_source("search_papers") == "builtin"
    assert tool_registry.tool_source("search_literature") == "builtin"
    assert tool_registry.tool_source("research_wiki_query") == "builtin"
    assert tool_registry.tool_source("local_shell") == "compat"


def test_tool_registry_default_workspace_exposure_comes_from_tool_definition_spec():
    local_tools = tool_registry.default_tool_names_for_workspace(None)
    remote_tools = tool_registry.default_tool_names_for_workspace("ssh-remote")

    assert "bash" in local_tools
    assert "apply_patch" in local_tools
    assert "search_literature" in local_tools
    assert "inspect_workspace" not in local_tools

    assert "inspect_workspace" in remote_tools
    assert "read_workspace_file" in remote_tools
    assert "search_literature" in remote_tools
    assert "bash" not in remote_tools


def test_researchos_public_bridge_hides_legacy_tools_but_keeps_new_preview_tools() -> None:
    names = researchos_mcp_registry.bridge_tool_names()

    assert "preview_external_paper_head" in names
    assert "preview_external_paper_section" in names
    assert "paper_search" not in names
    assert "paper_detail" not in names


def test_tool_registry_custom_tool_can_embed_permission_spec():
    tool_registry.reset_custom_tools()

    def _custom_echo(value: str, **_kwargs):  # noqa: ANN003
        return ToolResult(success=True, summary="custom ok", data={"value": value})

    tool_registry.register_tool(
        ToolDef(
            name="custom_governed",
            description="Custom tool with embedded spec.",
            parameters={
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
                "required": ["value"],
            },
            spec=ToolSpec(
                permission="custom.permission",
                managed_permission=True,
                default_local_enabled=True,
                allow_in_read_only=False,
            ),
        ),
        handler=_custom_echo,
    )

    try:
        definition = tool_registry.get_tool_definition("custom_governed")
        assert definition is not None
        assert definition.spec.permission == "custom.permission"
        assert tool_registry.tool_permission("custom_governed") == "custom.permission"
        assert tool_registry.manages_tool("custom_governed") is True

        build_tools = {item["function"]["name"] for item in tool_registry.get_openai_tools("build")}
        plan_tools = {item["function"]["name"] for item in tool_registry.get_openai_tools("plan")}

        assert "custom_governed" in build_tools
        assert "custom_governed" not in plan_tools
    finally:
        tool_registry.reset_custom_tools()


def test_tool_registry_builtin_handler_resolution_comes_from_tool_definition() -> None:
    ls_handler = tool_registry.resolve_tool_handler("ls")
    glob_handler = tool_registry.resolve_tool_handler("glob")
    grep_handler = tool_registry.resolve_tool_handler("grep")
    read_handler = tool_registry.resolve_tool_handler("read")
    task_handler = tool_registry.resolve_tool_handler("task")
    research_handler = tool_registry.resolve_tool_handler("search_papers")
    web_handler = tool_registry.resolve_tool_handler("webfetch")
    skill_handler = tool_registry.resolve_tool_handler("skill")
    todo_handler = tool_registry.resolve_tool_handler("todoread")

    assert callable(ls_handler)
    assert callable(glob_handler)
    assert callable(grep_handler)
    assert callable(read_handler)
    assert callable(task_handler)
    assert callable(research_handler)
    assert callable(web_handler)
    assert callable(skill_handler)
    assert callable(todo_handler)
    assert getattr(ls_handler, "__name__", "") == "_list_path_entries"
    assert getattr(glob_handler, "__name__", "") == "_glob_path_entries"
    assert getattr(grep_handler, "__name__", "") == "_grep_path_contents"
    assert getattr(read_handler, "__name__", "") == "_read_path"
    assert getattr(task_handler, "__name__", "") == "_task_subagent"
    assert getattr(research_handler, "__name__", "") == "_search_papers"
    assert getattr(web_handler, "__name__", "") == "_webfetch"
    assert getattr(skill_handler, "__name__", "") == "_load_skill"
    assert getattr(todo_handler, "__name__", "") == "_todo_read"
    assert (
        getattr(research_handler, "__module__", "") == "packages.agent.tools.research_tool_runtime"
    )
    assert getattr(web_handler, "__module__", "") == "packages.agent.tools.web_tool_runtime"
    assert getattr(skill_handler, "__module__", "") == "packages.agent.tools.skill_tool_runtime"
    assert getattr(todo_handler, "__module__", "") == "packages.agent.session.session_tool_runtime"


def test_all_default_local_build_tools_resolve_to_executable_handlers() -> None:
    visible_tool_names = [
        item["function"]["name"] for item in tool_registry.get_openai_tools("build")
    ]

    unresolved = []
    for name in visible_tool_names:
        handler = tool_registry.resolve_tool_handler(name)
        if not callable(handler):
            unresolved.append(name)

    assert unresolved == []


def test_tool_registry_glob_and_grep_are_executable_for_local_workspace(tmp_path) -> None:
    alpha = tmp_path / "alpha.py"
    beta = tmp_path / "nested" / "beta.py"
    beta.parent.mkdir(parents=True, exist_ok=True)
    alpha.write_text("print('alpha')\n", encoding="utf-8")
    beta.write_text("def build_plan_mode_reminder():\n    return 'ok'\n", encoding="utf-8")

    context = AgentToolContext(
        session_id="tool-registry-local-search",
        mode="build",
        workspace_path=str(tmp_path),
    )

    glob_events = list(
        execute_tool_stream(
            "glob",
            {"pattern": "**/*.py", "path": str(tmp_path)},
            context=context,
        )
    )
    grep_events = list(
        execute_tool_stream(
            "grep",
            {"pattern": "build_plan_mode_reminder", "path": str(tmp_path)},
            context=context,
        )
    )

    assert len(glob_events) == 1
    assert isinstance(glob_events[0], ToolResult)
    assert glob_events[0].success is True
    assert int((glob_events[0].data or {}).get("count") or 0) >= 2

    assert len(grep_events) == 1
    assert isinstance(grep_events[0], ToolResult)
    assert grep_events[0].success is True
    assert int((grep_events[0].data or {}).get("count") or 0) >= 1


def test_local_workspace_core_tools_execute_smoke(tmp_path) -> None:
    alpha = tmp_path / "alpha.py"
    nested = tmp_path / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    beta = nested / "beta.py"
    alpha.write_text("print('alpha')\n", encoding="utf-8")
    beta.write_text("def build_plan_mode_reminder():\n    return 'ok'\n", encoding="utf-8")

    context = AgentToolContext(
        session_id="tool-registry-local-core-smoke",
        mode="build",
        workspace_path=str(tmp_path),
    )

    list_events = list(execute_tool_stream("list", {"path": str(tmp_path)}, context=context))
    glob_events = list(
        execute_tool_stream("glob", {"pattern": "**/*.py", "path": str(tmp_path)}, context=context)
    )
    grep_events = list(
        execute_tool_stream(
            "grep", {"pattern": "build_plan_mode_reminder", "path": str(tmp_path)}, context=context
        )
    )
    read_events = list(execute_tool_stream("read", {"file_path": str(beta)}, context=context))
    bash_events = list(
        execute_tool_stream(
            "bash",
            {"command": "pwd", "workdir": str(tmp_path)},
            context=context,
        )
    )

    assert len(list_events) == 1
    assert isinstance(list_events[0], ToolResult)
    assert list_events[0].success is True
    assert int((list_events[0].data or {}).get("total_entries") or 0) >= 2

    assert len(glob_events) == 1
    assert isinstance(glob_events[0], ToolResult)
    assert glob_events[0].success is True
    assert int((glob_events[0].data or {}).get("count") or 0) >= 2

    assert len(grep_events) == 1
    assert isinstance(grep_events[0], ToolResult)
    assert grep_events[0].success is True
    assert int((grep_events[0].data or {}).get("count") or 0) >= 1

    assert len(read_events) == 1
    assert isinstance(read_events[0], ToolResult)
    assert read_events[0].success is True
    assert "build_plan_mode_reminder" in str((read_events[0].data or {}).get("content") or "")

    assert len(bash_events) == 1
    assert isinstance(bash_events[0], ToolResult)
    assert bash_events[0].success is True
    assert str((bash_events[0].data or {}).get("cwd") or "")


def test_plan_mode_edit_can_materialize_missing_plan_file(tmp_path) -> None:
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    session_payload = {
        "id": "tool-registry-plan-edit-create",
        "slug": "tool-registry-plan-edit-create",
        "directory": str(tmp_path),
        "workspace_path": str(tmp_path),
        "mode": "plan",
        "time": {},
    }
    plan_info = session_plan.resolve_session_plan_info(session_payload)
    assert plan_info is not None
    assert plan_info.exists is False

    context = AgentToolContext(
        session_id="tool-registry-plan-edit-create",
        mode="plan",
        workspace_path=str(tmp_path),
    )
    events = list(
        execute_tool_stream(
            "edit",
            {
                "file_path": plan_info.path,
                "old_string": "",
                "new_string": "# Plan\n\n- Verify mode switch\n",
            },
            context=context,
        )
    )

    assert len(events) == 1
    assert isinstance(events[0], ToolResult)
    assert events[0].success is True
    assert Path(plan_info.path).read_text(encoding="utf-8") == "# Plan\n\n- Verify mode switch\n"
