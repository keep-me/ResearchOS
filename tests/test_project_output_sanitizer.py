from packages.ai.project.output_sanitizer import (
    sanitize_project_artifact_preview_content,
    sanitize_project_markdown,
    sanitize_project_run_metadata,
)


def test_sanitize_project_markdown_removes_tool_trace_and_checkpoint_block():
    raw = """# Idea Discovery Report

## Executive Summary
调用外部 reviewer 进行 Round 1。to=mcp codex codex tool code syntax error: Expecting value: line 1 column 1 (char 0)

## Literature Landscape
Searching local paper library and arXiv tooling.# Phase 1 文献地形勘测

# Checkpoint
这是否符合你的理解？
如果你不回复，我将默认继续。

## Ranked Ideas
- idea 1
"""
    cleaned = sanitize_project_markdown(raw)

    assert "to=mcp" not in cleaned
    assert "tool code syntax error" not in cleaned
    assert "Searching local paper library and arXiv tooling" not in cleaned
    assert "# Checkpoint" not in cleaned
    assert "这是否符合你的理解" not in cleaned
    assert "## Ranked Ideas" in cleaned
    assert "# Phase 1 文献地形勘测" in cleaned


def test_sanitize_project_run_metadata_sanitizes_markdown_but_keeps_json_content():
    metadata = {
        "workflow_output_markdown": "## Executive Summary\nto=mcp codex tool code syntax error: boom\n",
        "stage_outputs": {
            "collect_context": {
                "content": "# Checkpoint\n如果你不回复，我将默认继续。\n\n## Next\nok\n",
            },
            "expand_directions": {
                "content": '{"ideas":[{"title":"Idea","content":"keep json"}]}',
            },
        },
    }

    cleaned = sanitize_project_run_metadata(metadata)

    assert "to=mcp" not in cleaned["workflow_output_markdown"]
    assert "# Checkpoint" not in cleaned["stage_outputs"]["collect_context"]["content"]
    assert cleaned["stage_outputs"]["expand_directions"]["content"] == metadata["stage_outputs"]["expand_directions"]["content"]


def test_sanitize_project_artifact_preview_only_for_aris_markdown():
    dirty = "# Report\n\nto=mcp codex tool code syntax error: boom\n"

    cleaned = sanitize_project_artifact_preview_content(
        ".auto-researcher/aris-runs/run-1/IDEA_REPORT.md",
        dirty,
    )
    untouched = sanitize_project_artifact_preview_content(
        "notes/manual.md",
        dirty,
    )

    assert "to=mcp" not in cleaned
    assert untouched == dirty
