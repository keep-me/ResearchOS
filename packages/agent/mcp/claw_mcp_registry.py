from __future__ import annotations

from packages.agent.tools.tool_registry import get_tool_definition, tool_registry_names
from packages.agent.tools.tool_schema import ToolDef

CLAW_MCP_SERVER_NAME = "ResearchOS"

CLAW_CONTEXT_SESSION_ID_ENV = "RESEARCHOS_AGENT_SESSION_ID"
CLAW_CONTEXT_MODE_ENV = "RESEARCHOS_AGENT_MODE"
CLAW_CONTEXT_WORKSPACE_PATH_ENV = "RESEARCHOS_AGENT_WORKSPACE_PATH"
CLAW_CONTEXT_WORKSPACE_SERVER_ID_ENV = "RESEARCHOS_AGENT_WORKSPACE_SERVER_ID"

CLAW_REMOTE_GENERIC_TOOL_NAMES = {
    "list",
    "ls",
    "glob",
    "grep",
    "read",
    "write",
    "edit",
    "multiedit",
    "apply_patch",
    "bash",
    "inspect_workspace",
    "read_workspace_file",
    "write_workspace_file",
    "replace_workspace_text",
    "run_workspace_command",
}

CLAW_DYNAMIC_TOOL_EXCLUDES = {
    "local_shell",
    "plan_exit",
    "question",
}

CLAW_LEGACY_MCP_TOOL_NAMES = {
    "paper_search",
    "paper_detail",
    "paper_import_arxiv",
    "paper_import_pdf",
    "paper_skim",
    "paper_deep_read",
    "paper_reasoning",
    "paper_embed",
    "paper_extract_figures",
    "paper_figures",
    "task_list",
    "task_status",
    "paper_library_overview",
}

CLAW_MANUAL_MCP_TOOL_NAMES: tuple[str, ...] = (
    "web_search",
    "search_arxiv",
    "search_papers",
    "get_paper_detail",
    "get_paper_analysis",
    "get_similar_papers",
    "get_citation_tree",
    "get_timeline",
    "research_kg_status",
    "build_research_kg",
    "graph_rag_query",
    "list_topics",
    "get_system_status",
    "search_literature",
    "preview_external_paper_head",
    "preview_external_paper_section",
    "ingest_external_literature",
    "ingest_arxiv",
    "skim_paper",
    "deep_read_paper",
    "analyze_paper_rounds",
    "embed_paper",
    "generate_wiki",
    "research_wiki_init",
    "research_wiki_stats",
    "research_wiki_query",
    "research_wiki_update_node",
    "generate_daily_brief",
    "manage_subscription",
    "suggest_keywords",
    "reasoning_analysis",
    "identify_research_gaps",
    "writing_assist",
    "analyze_figures",
)


def normalize_mcp_name(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in str(value or "")
    )


def mcp_qualified_tool_name(tool_name: str, server_name: str = CLAW_MCP_SERVER_NAME) -> str:
    return f"mcp__{normalize_mcp_name(server_name)}__{normalize_mcp_name(tool_name)}"


def iter_dynamic_bridge_tool_defs(
    *,
    existing_names: set[str] | None = None,
) -> list[ToolDef]:
    existing = {str(item).strip() for item in (existing_names or set()) if str(item).strip()}
    items: list[ToolDef] = []
    for tool_name in sorted(tool_registry_names()):
        if (
            tool_name in CLAW_DYNAMIC_TOOL_EXCLUDES
            or tool_name in CLAW_LEGACY_MCP_TOOL_NAMES
            or tool_name in existing
        ):
            continue
        definition = get_tool_definition(tool_name)
        if definition is None:
            continue
        items.append(definition)
    return items


def bridge_tool_names(
    *,
    existing_names: set[str] | None = None,
) -> list[str]:
    existing = {str(item).strip() for item in (existing_names or set()) if str(item).strip()}
    names = [item for item in CLAW_MANUAL_MCP_TOOL_NAMES if item and item not in existing]
    names.extend(
        tool.name for tool in iter_dynamic_bridge_tool_defs(existing_names=existing | set(names))
    )
    return names


def bridge_qualified_tool_names(
    *,
    server_name: str = CLAW_MCP_SERVER_NAME,
    existing_names: set[str] | None = None,
) -> list[str]:
    return [
        mcp_qualified_tool_name(tool_name, server_name)
        for tool_name in bridge_tool_names(existing_names=existing_names)
    ]


def filter_public_tool_names(tool_names: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    allowed = set(bridge_tool_names())
    return [name for name in tool_names if str(name or "").strip() in allowed]
