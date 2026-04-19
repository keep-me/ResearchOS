from __future__ import annotations

from pathlib import Path

from packages.agent import claw_runtime_manager


def test_build_runtime_spec_uses_bridge_daemon_and_context_env(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\ResearchOS")
    runtime_data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("RESEARCHOS_DATA_DIR", str(runtime_data_dir))

    bridge_workspace = tmp_path / "bridge"
    bridge_workspace.mkdir()
    claw_binary = tmp_path / "claw.exe"
    claw_binary.write_bytes(b"MZfake")
    settings_path = bridge_workspace / ".claw" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        claw_runtime_manager,
        "_ensure_claw_bridge_workspace",
        lambda _workspace_path, _server_id: bridge_workspace,
    )
    monkeypatch.setattr(
        claw_runtime_manager,
        "_ensure_claw_workspace_settings",
        lambda _workspace_dir: settings_path,
    )
    monkeypatch.setattr(
        claw_runtime_manager,
        "_ensure_claw_binary",
        lambda timeout_sec=0: claw_binary,
    )
    monkeypatch.setattr(
        claw_runtime_manager,
        "bridge_qualified_tool_names",
        lambda: ["mcp__ResearchOS__read", "mcp__ResearchOS__bash"],
    )
    monkeypatch.setattr(
        claw_runtime_manager,
        "_session_mode_for_claw",
        lambda _session_id: "plan",
    )
    monkeypatch.setattr(
        claw_runtime_manager,
        "_shared_apply_claw_runtime_policy_env",
        lambda env: {**env, "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS": "65432"},
    )

    spec = claw_runtime_manager._build_runtime_spec(
        {
            "command": str(claw_binary),
            "command_path": str(claw_binary),
            "provider": "openai",
            "base_url": "https://openai.example.com/v1/chat/completions",
            "api_key": "sk-test",
            "default_model": "qwen-plus",
        },
        workspace_path="/remote/workspace",
        workspace_server_id="xdu",
        session_id="sess-1",
        timeout_sec=90,
    )

    env = spec.env
    command = list(spec.command)

    assert spec.workspace_dir == bridge_workspace
    assert spec.session_ref == "researchos-sess-1"
    assert command[0] == str(claw_binary)
    assert "bridge-daemon" in command
    assert "researchos-sess-1" in command
    assert "--allowedTools" in command
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert env["OPENAI_BASE_URL"] == "https://openai.example.com/v1"
    assert env["CLAW_CONFIG_HOME"] == str(runtime_data_dir / "claw-config")
    assert Path(env["CLAW_CONFIG_HOME"]).exists()
    assert env["HOME"] == r"C:\Users\ResearchOS"
    assert env["USERPROFILE"] == r"C:\Users\ResearchOS"
    assert env["RESEARCHOS_AGENT_SESSION_ID"] == "sess-1"
    assert env["RESEARCHOS_AGENT_MODE"] == "plan"
    assert env["RESEARCHOS_AGENT_WORKSPACE_PATH"] == "/remote/workspace"
    assert env["RESEARCHOS_AGENT_WORKSPACE_SERVER_ID"] == "xdu"
    assert env["CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS"] == "65432"
