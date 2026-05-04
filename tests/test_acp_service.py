from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.agent import acp_service
from tests.fixtures.mock_acp_http_permission_server import serve_mock_acp_http_permission_server

FIXTURE_DIR = Path(__file__).parent / "fixtures"
MOCK_SERVER = FIXTURE_DIR / "mock_acp_server.py"
MOCK_PERMISSION_SERVER = FIXTURE_DIR / "mock_acp_permission_server.py"


def _fresh_service(tmp_path, monkeypatch):
    registry_path = tmp_path / "assistant_acp_registry.json"
    data_dir = tmp_path
    monkeypatch.setattr(acp_service, "_DATA_DIR", data_dir)
    monkeypatch.setattr(acp_service, "_REGISTRY_PATH", registry_path)
    acp_service.get_acp_registry_service.cache_clear()
    return acp_service.get_acp_registry_service()


def test_acp_config_roundtrip_and_runtime_snapshot(tmp_path, monkeypatch):
    service = _fresh_service(tmp_path, monkeypatch)

    empty_summary = service.get_backend_summary()
    assert empty_summary["chat_ready"] is False
    assert empty_summary["default_server"] is None

    saved = service.update_config(
        {
            "default_server": "mock-stdio",
            "servers": {
                "mock-stdio": {
                    "label": "Mock ACP",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(MOCK_SERVER)],
                    "cwd": str(tmp_path),
                    "enabled": True,
                    "timeout_sec": 30,
                }
            },
        }
    )

    assert saved["default_server"] == "mock-stdio"
    assert "mock-stdio" in saved["servers"]
    assert saved["servers"]["mock-stdio"]["command"] == sys.executable

    runtime = service.runtime_snapshot()
    assert runtime["available"] is True
    assert runtime["server_count"] == 1
    assert runtime["enabled_count"] == 1
    assert runtime["connected_count"] == 0

    summary = service.get_backend_summary()
    assert summary["chat_ready"] is True
    assert summary["default_server"] == "mock-stdio"
    assert summary["default_transport"] == "stdio"


def test_acp_stdio_connect_execute_and_disconnect(tmp_path, monkeypatch):
    service = _fresh_service(tmp_path, monkeypatch)
    service.update_config(
        {
            "default_server": "mock-stdio",
            "servers": {
                "mock-stdio": {
                    "label": "Mock ACP",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(MOCK_SERVER)],
                    "cwd": str(tmp_path),
                    "enabled": True,
                    "timeout_sec": 30,
                }
            },
        }
    )

    connected = service.connect_server("mock-stdio")
    assert connected["connected"] is True
    assert connected["status"] == "connected"

    result = service.execute_prompt(
        prompt="Reply with exactly OK",
        workspace_path=str(tmp_path),
        timeout_sec=30,
    )

    assert result["success"] is True
    assert result["agent_type"] == "custom_acp"
    assert result["transport"] == "stdio"
    assert result["execution_mode"] == "local"
    assert result["workspace_path"] == str(tmp_path.resolve())
    assert "MOCK_ACP_OK" in result["content"]
    assert result["updates"]
    assert result["updates"][0]["sessionUpdate"] == "agent_message_chunk"

    disconnected = service.disconnect_server("mock-stdio")
    assert disconnected["connected"] is False
    assert disconnected["status"] == "disconnected"


def test_updating_connected_server_restarts_connection(tmp_path, monkeypatch):
    service = _fresh_service(tmp_path, monkeypatch)
    service.update_config(
        {
            "default_server": "mock-stdio",
            "servers": {
                "mock-stdio": {
                    "label": "Mock ACP",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(MOCK_SERVER)],
                    "cwd": str(tmp_path),
                    "enabled": True,
                    "timeout_sec": 30,
                }
            },
        }
    )

    service.connect_server("mock-stdio")
    listed = service.list_servers()
    assert listed[0]["connected"] is True

    service.update_config(
        {
            "default_server": "mock-stdio",
            "servers": {
                "mock-stdio": {
                    "label": "Mock ACP Updated",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(MOCK_SERVER)],
                    "cwd": str(tmp_path),
                    "enabled": True,
                    "timeout_sec": 30,
                }
            },
        }
    )

    refreshed = service.list_servers()
    assert refreshed[0]["label"] == "Mock ACP Updated"
    assert refreshed[0]["connected"] is False


def test_acp_stdio_permission_pause_and_resume(tmp_path, monkeypatch):
    service = _fresh_service(tmp_path, monkeypatch)
    service.update_config(
        {
            "default_server": "mock-stdio",
            "servers": {
                "mock-stdio": {
                    "label": "Mock ACP Permission",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(MOCK_PERMISSION_SERVER)],
                    "cwd": str(tmp_path),
                    "enabled": True,
                    "timeout_sec": 30,
                }
            },
        }
    )

    service.connect_server("mock-stdio")

    paused = service.execute_prompt(
        prompt="Please continue",
        workspace_path=str(tmp_path),
        timeout_sec=30,
        session_id="acp-interactive-session",
    )

    assert paused["paused"] is True
    assert paused["pending_action_id"].startswith("acp_permission_mock-stdio_")
    assert "Permission required" in paused["content"]
    assert paused["permission_request"]["tool_name"] == "bash"

    resumed = service.respond_to_pending_permission(
        paused["pending_action_id"],
        response="once",
    )

    assert resumed["paused"] is False
    assert "Permission outcome: allow_once" in resumed["content"]


def test_acp_http_permission_pause_and_resume(tmp_path, monkeypatch):
    service = _fresh_service(tmp_path, monkeypatch)
    with serve_mock_acp_http_permission_server() as server_url:
        service.update_config(
            {
                "default_server": "mock-http",
                "servers": {
                    "mock-http": {
                        "label": "Mock ACP HTTP Permission",
                        "transport": "http",
                        "url": server_url,
                        "enabled": True,
                        "timeout_sec": 30,
                    }
                },
            }
        )

        service.connect_server("mock-http")

        paused = service.execute_prompt(
            prompt="Please continue",
            workspace_path=str(tmp_path),
            timeout_sec=30,
            session_id="acp-http-interactive-session",
        )

        assert paused["paused"] is True
        assert paused["transport"] == "http"
        assert paused["pending_action_id"].startswith("acp_permission_mock-http_")
        assert "Permission required" in paused["content"]
        assert paused["permission_request"]["tool_name"] == "bash"

        resumed = service.respond_to_pending_permission(
            paused["pending_action_id"],
            response="once",
        )

        assert resumed["paused"] is False
        assert resumed["transport"] == "http"
        assert "Permission outcome: allow_once" in resumed["content"]
