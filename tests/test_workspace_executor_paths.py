from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from apps.api.routers import agent_workspace
from packages.agent.workspace.workspace_executor import (
    edit_path_file,
    glob_path_entries,
    grep_path_contents,
    inspect_workspace,
    read_path_file,
    run_workspace_command,
    write_workspace_file,
)


def test_inspect_workspace_does_not_create_missing_directory(tmp_path: Path) -> None:
    missing_workspace = tmp_path / "missing-workspace"

    snapshot = inspect_workspace(str(missing_workspace))

    assert snapshot["workspace_path"] == str(missing_workspace.resolve())
    assert snapshot["files"] == []
    assert snapshot["total_entries"] == 0
    assert not missing_workspace.exists()


def test_inspect_workspace_can_disable_entry_cap(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(140):
        (workspace / f"file-{index:03d}.txt").write_text("ok\n", encoding="utf-8")

    capped = inspect_workspace(str(workspace), max_entries=120)
    uncapped = inspect_workspace(str(workspace), max_entries=0)

    assert capped["truncated"] is True
    assert capped["total_entries"] == 120
    assert len(capped["files"]) == 120
    assert uncapped["truncated"] is False
    assert uncapped["total_entries"] == 140
    assert len(uncapped["files"]) == 140


def test_workspace_overview_route_accepts_zero_as_unlimited(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(135):
        (workspace / f"item-{index:03d}.md").write_text("# ok\n", encoding="utf-8")

    snapshot = agent_workspace.get_workspace_overview(
        path=str(workspace),
        depth=2,
        max_entries=0,
        server_id=agent_workspace.LOCAL_SERVER_ID,
    )

    assert snapshot["exists"] is True
    assert snapshot["truncated"] is False
    assert snapshot["total_entries"] == 135
    assert len(snapshot["files"]) == 135


def test_write_workspace_file_repairs_empty_directory_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    conflict = workspace / "run.log"
    conflict.mkdir(parents=True, exist_ok=True)

    result = write_workspace_file(str(workspace), "run.log", "hello\n")

    assert result["changed"] is True
    assert conflict.is_file()
    assert conflict.read_text(encoding="utf-8") == "hello\n"


def test_edit_path_file_with_empty_old_string_creates_missing_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / ".opencode" / "plans" / "plan.md"

    result = edit_path_file(
        str(target),
        "",
        "# Plan\n\n- Check runtime parity\n",
        workspace_path=str(workspace),
    )

    assert result["created"] is True
    assert result["changed"] is True
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "# Plan\n\n- Check runtime parity\n"


def test_reveal_workspace_does_not_create_missing_file_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_path = tmp_path / "reports" / "literature-review.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_workspace.shutil, "which", lambda _: None)

    result = agent_workspace.reveal_workspace(
        agent_workspace.WorkspacePathPayload(
            path=str(report_path),
            server_id=agent_workspace.LOCAL_SERVER_ID,
        )
    )

    assert result["opened"] is False
    assert "父目录" in str(result["message"])
    assert not report_path.exists()


def test_run_workspace_command_returns_updated_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    docs_dir = workspace / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    command = "Set-Location docs" if os.name == "nt" else "cd docs"
    result = run_workspace_command(str(workspace), command)

    assert result["success"] is True
    assert result["cwd"] == str(docs_dir.resolve())
    assert result["stdout"] == ""


def test_create_workspace_git_branch_detects_existing_branch(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "pytest@example.com"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Pytest"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=workspace, check=True, capture_output=True, text=True)

    created = agent_workspace.create_workspace_git_branch(
        agent_workspace.WorkspaceGitBranchPayload(
            path=str(workspace),
            server_id=agent_workspace.LOCAL_SERVER_ID,
            branch_name="feature/demo",
            checkout=True,
        )
    )
    switched = agent_workspace.create_workspace_git_branch(
        agent_workspace.WorkspaceGitBranchPayload(
            path=str(workspace),
            server_id=agent_workspace.LOCAL_SERVER_ID,
            branch_name="feature/demo",
            checkout=True,
        )
    )

    assert created["ok"] is True
    assert created["created"] is True
    assert created["git"]["branch"] == "feature/demo"
    assert switched["ok"] is True
    assert switched["created"] is False
    assert switched["git"]["branch"] == "feature/demo"


def test_grep_path_contents_prioritizes_implementation_paths_over_tests_and_docs(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    impl_dir = workspace / "packages" / "ai"
    test_dir = workspace / "tests"
    docs_dir = workspace / "docs"
    impl_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    symbol = "build_plan_mode_reminder"
    (impl_dir / "session_plan.py").write_text(f"def {symbol}():\n    return 'impl'\n", encoding="utf-8")
    (test_dir / "test_session_plan.py").write_text(f"def test_symbol():\n    assert '{symbol}'\n", encoding="utf-8")
    (docs_dir / "notes.md").write_text(f"{symbol} appears in docs\n", encoding="utf-8")

    result = grep_path_contents(symbol, path_input=str(workspace), workspace_path=str(workspace), limit=10)

    assert result["count"] >= 1
    assert result["matches"][0]["relative_path"] == "packages/ai/session_plan.py"
    assert all(not str(item["relative_path"]).startswith("tests/") for item in result["matches"])
    assert all(not str(item["relative_path"]).startswith("docs/") for item in result["matches"])


def test_glob_path_entries_prioritizes_implementation_paths_over_tests_and_docs(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    impl_dir = workspace / "packages" / "ai"
    test_dir = workspace / "tests"
    docs_dir = workspace / "docs"
    impl_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    (impl_dir / "session_plan.py").write_text("impl\n", encoding="utf-8")
    (test_dir / "test_session_plan.py").write_text("test\n", encoding="utf-8")
    (docs_dir / "notes.py").write_text("docs\n", encoding="utf-8")

    result = glob_path_entries("**/*.py", path_input=str(workspace), workspace_path=str(workspace), limit=10)

    assert result["count"] >= 3
    assert result["matches"][0]["relative_path"] == "packages/ai/session_plan.py"


def test_grep_path_contents_prioritizes_definition_lines_for_identifier_queries(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    impl_dir = workspace / "packages" / "ai"
    impl_dir.mkdir(parents=True, exist_ok=True)

    symbol = "build_plan_mode_reminder"
    (impl_dir / "agent_service.py").write_text(
        "\n".join(
            [
                "from packages.agent.session.session_plan import build_plan_mode_reminder",
                "value = build_plan_mode_reminder(session_payload)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (impl_dir / "session_plan.py").write_text(
        f"def {symbol}(session_payload):\n    return '<system-reminder>'\n",
        encoding="utf-8",
    )

    result = grep_path_contents(symbol, path_input=str(workspace), workspace_path=str(workspace), limit=10)

    assert result["count"] >= 3
    assert result["matches"][0]["relative_path"] == "packages/ai/session_plan.py"
    assert result["matches"][0]["line"] == 1


def test_grep_path_contents_skips_generated_noise_before_falling_back_to_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    impl_dir = workspace / "packages" / "ai"
    tmp_dir = workspace / "tmp"
    impl_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    symbol = "build_plan_mode_reminder"
    (impl_dir / "session_plan.py").write_text(
        f"def {symbol}(session_payload):\n    return '<system-reminder>'\n",
        encoding="utf-8",
    )
    (tmp_dir / "assistant_runtime_eval.json").write_text(
        "\n".join(f'{{"text":"{symbol}"}}' for _ in range(120)),
        encoding="utf-8",
    )

    result = grep_path_contents(symbol, path_input=str(workspace), workspace_path=str(workspace), limit=20)

    assert result["matches"]
    assert result["matches"][0]["relative_path"] == "packages/ai/session_plan.py"
    assert all(not str(item["relative_path"]).startswith("tmp/") for item in result["matches"])


def test_grep_path_contents_treats_wildcard_include_as_unscoped_identifier_lookup(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    impl_dir = workspace / "packages" / "ai"
    scripts_dir = workspace / "scripts"
    tmp_dir = workspace / "tmp"
    impl_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    symbol = "build_plan_mode_reminder"
    (impl_dir / "session_plan.py").write_text(
        f"def {symbol}(session_payload):\n    return '<system-reminder>'\n",
        encoding="utf-8",
    )
    (scripts_dir / "eval_runtime.py").write_text(
        f"PROMPT = '{symbol}'\n",
        encoding="utf-8",
    )
    (tmp_dir / "assistant_runtime_eval.json").write_text(
        f'{{"text":"{symbol}"}}',
        encoding="utf-8",
    )

    result = grep_path_contents(
        symbol,
        path_input=str(workspace),
        workspace_path=str(workspace),
        include_glob="*",
        limit=20,
    )

    assert result["matches"]
    assert result["matches"][0]["relative_path"] == "packages/ai/session_plan.py"
    assert all(not str(item["relative_path"]).startswith("scripts/") for item in result["matches"])
    assert all(not str(item["relative_path"]).startswith("tmp/") for item in result["matches"])


def test_grep_path_contents_treats_star_dot_star_as_unscoped_identifier_lookup(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    impl_dir = workspace / "packages" / "ai"
    tmp_dir = workspace / "tmp"
    impl_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    symbol = "build_plan_mode_reminder"
    (impl_dir / "session_plan.py").write_text(
        f"def {symbol}(session_payload):\n    return '<system-reminder>'\n",
        encoding="utf-8",
    )
    (tmp_dir / "assistant_runtime_eval.json").write_text(
        f'{{"text":"{symbol}"}}',
        encoding="utf-8",
    )

    result = grep_path_contents(
        symbol,
        path_input=str(workspace),
        workspace_path=str(workspace),
        include_glob="*.*",
        limit=20,
    )

    assert result["matches"]
    assert result["matches"][0]["relative_path"] == "packages/ai/session_plan.py"
    assert all(not str(item["relative_path"]).startswith("tmp/") for item in result["matches"])


def test_read_path_file_supports_line_offset_and_limit_windows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "sample.py"
    target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    result = read_path_file(
        str(target),
        workspace_path=str(workspace),
        offset=2,
        limit=2,
    )

    assert result["content"] == "2: line2\n3: line3"
    assert result["raw_content"] == "line2\nline3"
    assert result["line_start"] == 2
    assert result["line_end"] == 3
    assert result["total_lines"] == 4
    assert result["next_offset"] == 4
    assert result["truncated"] is True


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 7 wrapper behavior is Windows-specific")
def test_run_workspace_command_handles_multiline_python_without_outer_command_quoting_breakage(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    command = (
        "@'\n"
        "from pathlib import Path\n"
        "print(\"hello from python\")\n"
        "print(Path.cwd().name)\n"
        "'@ | python -"
    )
    result = run_workspace_command(str(workspace), command)

    assert result["success"] is True
    assert "hello from python" in result["stdout"]
    assert "workspace" in result["stdout"]
    assert result.get("error_code") is None

