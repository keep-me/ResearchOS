from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.agent import cli_agent_service


@pytest.fixture
def claw_service(monkeypatch: pytest.MonkeyPatch) -> cli_agent_service.CliAgentService:
    monkeypatch.setattr(
        cli_agent_service,
        "list_project_agent_templates",
        lambda: [
            {
                "id": "claw",
                "label": "Claw",
                "kind": "cli",
                "description": "Claw backend",
            }
        ],
    )
    monkeypatch.setattr(cli_agent_service, "_load_active_llm_defaults", lambda: {})
    return cli_agent_service.CliAgentService()


def test_claw_normalizes_to_auto_and_keeps_local_execution_when_workspace_server_id_present(
    claw_service: cli_agent_service.CliAgentService,
):
    normalized = claw_service._normalize_config(
        {
            "agent_type": "claw",
            "execution_mode": "local",
        }
    )

    assert normalized["execution_mode"] == "auto"
    assert claw_service._resolve_execution_mode(normalized, workspace_server_id=None) == "local"
    assert claw_service._resolve_execution_mode(normalized, workspace_server_id="xdu") == "local"

def test_run_local_claw_remote_bridge_sets_allowed_tools_and_context_env(
    monkeypatch: pytest.MonkeyPatch,
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

    captured: dict[str, object] = {}

    def _fake_subprocess_run(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"message":"remote bridge ok","session_path":"/tmp/bridge-session"}',
            stderr="",
        )

    monkeypatch.setattr(cli_agent_service, "_ensure_claw_workspace_settings", lambda _workspace_dir: settings_path)
    monkeypatch.setattr(cli_agent_service, "bridge_qualified_tool_names", lambda: ["mcp__ResearchOS__read", "mcp__ResearchOS__bash"])
    monkeypatch.setattr(cli_agent_service, "_session_mode_for_claw", lambda _session_id: "plan")
    monkeypatch.setattr(cli_agent_service.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(cli_agent_service.subprocess, "run", _fake_subprocess_run)

    service = cli_agent_service.CliAgentService()
    result = service._run_local_claw(
        {
            "id": "claw",
            "agent_type": "claw",
            "label": "Claw",
            "command": str(claw_binary),
            "command_path": str(claw_binary),
            "provider": "openai",
            "base_url": "https://openai.example.com/v1/chat/completions",
            "api_key": "sk-test",
            "default_model": "qwen-plus",
        },
        prompt="hello remote bridge",
        workspace_dir=bridge_workspace,
        timeout_sec=90,
        session_id="sess-1",
        requested_workspace_path="/remote/workspace",
        requested_workspace_server_id="xdu",
    )

    command = list(captured["command"] or [])
    kwargs = dict(captured["kwargs"] or {})
    env = dict(kwargs["env"])

    assert result["success"] is True
    assert result["execution_mode"] == "ssh"
    assert result["workspace_server_id"] == "xdu"
    assert result["workspace_path"] == "/remote/workspace"
    assert result["content"] == "remote bridge ok"
    assert result["session_path"] == "/tmp/bridge-session"
    assert result["claw_settings_path"] == str(settings_path)
    assert "--allowedTools" in command
    assert "bridge-turn" in command
    assert "qwen-plus" in command
    assert kwargs["cwd"] == str(bridge_workspace)
    assert kwargs["input"] == "hello remote bridge"
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert env["OPENAI_BASE_URL"] == "https://openai.example.com/v1"
    assert env["CLAW_CONFIG_HOME"] == str(runtime_data_dir / "claw-config")
    assert Path(env["CLAW_CONFIG_HOME"]).exists()
    assert env["HOME"] == r"C:\Users\ResearchOS"
    assert env["USERPROFILE"] == r"C:\Users\ResearchOS"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_MODEL" not in env
    assert env["RESEARCHOS_AGENT_SESSION_ID"] == "sess-1"
    assert env["RESEARCHOS_AGENT_MODE"] == "plan"
    assert env["RESEARCHOS_AGENT_WORKSPACE_PATH"] == "/remote/workspace"
    assert env["RESEARCHOS_AGENT_WORKSPACE_SERVER_ID"] == "xdu"


def test_execute_prompt_routes_remote_claw_through_local_bridge(
    monkeypatch: pytest.MonkeyPatch,
    claw_service: cli_agent_service.CliAgentService,
):
    config = claw_service._normalize_config({"agent_type": "claw"})
    local_calls: list[dict[str, object]] = []

    monkeypatch.setattr(claw_service, "_find_config", lambda _agent_type: config)
    monkeypatch.setattr(
        claw_service,
        "_resolve_config",
        lambda resolved: {
            **resolved,
            "command": "claw",
            "command_path": "D:/Desktop/ResearchOS/claw-code-main/rust/target/debug/claw.exe",
            "installed": True,
        },
    )
    monkeypatch.setattr(
        claw_service,
        "_build_chat_capability",
        lambda _resolved: {
            "chat_supported": True,
            "chat_ready": True,
            "chat_status": "ready",
            "chat_status_label": "Ready",
            "chat_blocked_reason": None,
        },
    )
    monkeypatch.setattr(
        claw_service,
        "_execute_remote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("_execute_remote should not be used")),
    )

    def _fake_execute_local(config: dict[str, object], **kwargs):
        local_calls.append({"config": config, **kwargs})
        return {
            "config_id": "claw",
            "agent_type": "claw",
            "label": "Claw",
            "command": "claw",
            "workspace_path": kwargs["requested_workspace_path"],
            "execution_mode": "ssh",
            "duration_ms": 10,
            "exit_code": 0,
            "success": True,
            "content": "bridge claw ok",
            "stdout": "",
            "stderr": "",
            "parsed": {"message": "bridge claw ok"},
            "workspace_server_id": kwargs["requested_workspace_server_id"],
        }

    monkeypatch.setattr(claw_service, "_execute_local", _fake_execute_local)

    result = claw_service.execute_prompt(
        "claw",
        prompt="bridge please",
        workspace_path="D:/remote/project",
        workspace_server_id="xdu",
        timeout_sec=45,
        session_id="sess-1",
    )

    assert result["success"] is True
    assert result["execution_mode"] == "ssh"
    assert result["content"] == "bridge claw ok"
    assert result.get("fallback_reason") in {None, ""}
    assert len(local_calls) == 1
    assert local_calls[0]["workspace_path"] == "D:/remote/project"
    assert local_calls[0]["requested_workspace_path"] == "D:/remote/project"
    assert local_calls[0]["requested_workspace_server_id"] == "xdu"
    assert local_calls[0]["session_id"] == "sess-1"


def test_local_claw_workspace_settings_use_packaged_mcp_flag_when_frozen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(cli_agent_service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli_agent_service.sys, "executable", r"C:\Program Files\ResearchOS\researchos-server.exe")
    monkeypatch.setenv("RESEARCHOS_DATA_DIR", r"D:\ResearchOS\data")

    settings_path = cli_agent_service._ensure_claw_workspace_settings(tmp_path)
    payload = cli_agent_service._load_json_file(settings_path)

    assert settings_path == tmp_path / ".claw" / "settings.local.json"
    server_payload = payload["mcpServers"]["ResearchOS"]
    assert server_payload["command"] == r"C:\Program Files\ResearchOS\researchos-server.exe"
    assert server_payload["args"] == ["--researchos-mcp-stdio"]
    assert server_payload["env"]["RESEARCHOS_DATA_DIR"] == r"D:\ResearchOS\data"
    assert "PYTHONPATH" not in server_payload["env"]


def test_local_claw_workspace_settings_propagate_runtime_storage_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv("RESEARCHOS_DATA_DIR", raising=False)
    monkeypatch.delenv("RESEARCHOS_ENV_FILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PDF_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("BRIEF_OUTPUT_ROOT", raising=False)
    monkeypatch.setattr(
        cli_agent_service,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="sqlite:///D:/ResearchOS/data/researchos.db",
            pdf_storage_root=Path(r"D:\ResearchOS\data\papers"),
            brief_output_root=Path(r"D:\ResearchOS\data\briefs"),
        ),
    )

    settings_path = cli_agent_service._ensure_claw_workspace_settings(tmp_path)
    payload = cli_agent_service._load_json_file(settings_path)
    server_payload = payload["mcpServers"]["ResearchOS"]
    env_payload = server_payload["env"]

    assert env_payload["RESEARCHOS_DATA_DIR"] == r"D:\ResearchOS\data"
    assert env_payload["DATABASE_URL"] == "sqlite:///D:/ResearchOS/data/researchos.db"
    assert env_payload["PDF_STORAGE_ROOT"] == r"D:\ResearchOS\data\papers"
    assert env_payload["BRIEF_OUTPUT_ROOT"] == r"D:\ResearchOS\data\briefs"


def test_packaged_missing_claw_message_does_not_point_to_meipass_build_dir(
    monkeypatch: pytest.MonkeyPatch,
    claw_service: cli_agent_service.CliAgentService,
):
    monkeypatch.setattr(cli_agent_service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli_agent_service.sys, "executable", r"C:\Program Files\ResearchOS\researchos-server.exe")
    monkeypatch.delenv("RESEARCHOS_CLAW_BINARY", raising=False)

    capability = claw_service._build_chat_capability(
        {
            "id": "claw",
            "agent_type": "claw",
            "label": "Claw",
            "installed": False,
            "command": "claw",
            "command_path": None,
        }
    )

    assert capability["chat_ready"] is False
    assert "RESEARCHOS_CLAW_BINARY" in str(capability["chat_blocked_reason"] or "")
    assert "_MEI" not in str(capability["chat_blocked_reason"] or "")


def test_frozen_claw_resolves_bundled_binary_from_env_override(
    monkeypatch: pytest.MonkeyPatch,
    claw_service: cli_agent_service.CliAgentService,
    tmp_path: Path,
):
    bundled = tmp_path / "claw.exe"
    bundled.write_bytes(b"MZfake")

    monkeypatch.setattr(cli_agent_service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli_agent_service.sys, "executable", str(tmp_path / "researchos-server.exe"))
    monkeypatch.setenv("RESEARCHOS_CLAW_BINARY", str(bundled))

    resolved = claw_service._resolve_config(claw_service._normalize_config({"agent_type": "claw"}))

    assert resolved["command_path"] == str(bundled)
    assert resolved["installed"] is True


def test_frozen_claw_resolves_repo_bundled_binary_without_env_override(
    monkeypatch: pytest.MonkeyPatch,
    claw_service: cli_agent_service.CliAgentService,
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    (repo_root / "apps" / "desktop").mkdir(parents=True)
    (repo_root / "apps" / "desktop" / "server.py").write_text("", encoding="utf-8")
    (repo_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    bundled = repo_root / "src-tauri" / "binaries" / "claw-x86_64-pc-windows-msvc.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZfake")
    executable = repo_root / "src-tauri" / "target" / "release" / "researchos-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZserver")

    monkeypatch.setattr(cli_agent_service, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli_agent_service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli_agent_service.sys, "executable", str(executable))
    monkeypatch.delenv("RESEARCHOS_CLAW_BINARY", raising=False)

    resolved = claw_service._resolve_config(claw_service._normalize_config({"agent_type": "claw"}))

    assert resolved["command_path"] == str(bundled)
    assert resolved["installed"] is True


def test_packaged_temp_runtime_still_prefers_install_dir_claw_binary(
    monkeypatch: pytest.MonkeyPatch,
    claw_service: cli_agent_service.CliAgentService,
    tmp_path: Path,
):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    bundled = install_dir / "claw.exe"
    bundled.write_bytes(b"MZfake")
    temp_runtime_root = tmp_path / "_MEI12345" / "claw-code-main"
    temp_runtime_root.mkdir(parents=True)

    monkeypatch.chdir(install_dir)
    monkeypatch.setattr(cli_agent_service.sys, "executable", str(tmp_path / "_MEI12345" / "researchos-server.exe"))
    monkeypatch.setattr(cli_agent_service.sys, "argv", [str(tmp_path / "_MEI12345" / "researchos-server.exe")], raising=False)
    monkeypatch.delenv("RESEARCHOS_CLAW_BINARY", raising=False)
    monkeypatch.setattr(cli_agent_service, "_claw_runtime_root", lambda: temp_runtime_root)

    resolved = claw_service._resolve_config(claw_service._normalize_config({"agent_type": "claw"}))

    assert resolved["command_path"] == str(bundled)
    assert resolved["installed"] is True
