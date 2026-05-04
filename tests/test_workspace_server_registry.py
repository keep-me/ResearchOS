from __future__ import annotations

import json
from types import SimpleNamespace

from packages.agent.workspace import workspace_server_registry as registry


def test_workspace_server_registry_does_not_persist_plaintext_secrets(
    monkeypatch,
    tmp_path,
) -> None:
    store = tmp_path / "servers.json"
    monkeypatch.setattr(registry, "_server_store_path", lambda: store)

    item = registry.create_workspace_server(
        {
            "id": "gpu-main",
            "label": "GPU Main",
            "host": "gpu.example.com",
            "username": "tester",
            "password": "super-secret-password",
            "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----",
            "passphrase": "key-passphrase",
            "workspace_root": "/srv/research",
            "host_key_fingerprint": "aa:bb",
        }
    )

    payload_text = store.read_text(encoding="utf-8")
    assert "super-secret-password" not in payload_text
    assert "BEGIN OPENSSH PRIVATE KEY" not in payload_text
    assert "key-passphrase" not in payload_text
    payload = json.loads(payload_text)
    saved = payload[0]
    assert saved["password_ref"].startswith("session:gpu-main:password")
    assert saved["private_key_ref"].startswith("session:gpu-main:private_key")
    assert saved["passphrase_ref"].startswith("session:gpu-main:passphrase")
    assert "password" not in saved
    assert "private_key" not in saved
    assert "passphrase" not in saved
    assert item["has_password"] is True
    assert item["has_private_key"] is True
    assert item["has_passphrase"] is True

    loaded = registry.get_workspace_server_entry("gpu-main")
    assert loaded["password"] == "super-secret-password"
    assert "BEGIN OPENSSH PRIVATE KEY" in loaded["private_key"]
    assert loaded["passphrase"] == "key-passphrase"


def test_workspace_server_registry_resolves_env_secret_refs(monkeypatch, tmp_path) -> None:
    store = tmp_path / "servers.json"
    store.write_text(
        json.dumps(
            [
                {
                    "id": "gpu-env",
                    "label": "GPU Env",
                    "host": "gpu.example.com",
                    "username": "tester",
                    "password_ref": "env:RESEARCHOS_TEST_SSH_PASSWORD",
                    "workspace_root": "/srv/research",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "_server_store_path", lambda: store)
    monkeypatch.setenv("RESEARCHOS_TEST_SSH_PASSWORD", "from-env")

    loaded = registry.get_workspace_server_entry("gpu-env")

    assert loaded["password"] == "from-env"


def test_workspace_server_payload_preserves_host_key_fingerprint(monkeypatch, tmp_path) -> None:
    store = tmp_path / "servers.json"
    monkeypatch.setattr(registry, "_server_store_path", lambda: store)

    registry.create_workspace_server(
        SimpleNamespace(
            id="gpu-hostkey",
            label="GPU HostKey",
            host="gpu.example.com",
            port=22,
            username="tester",
            password="secret",
            private_key=None,
            passphrase=None,
            workspace_root="/srv/research",
            host_key_fingerprint="aa:bb:cc",
            enabled=True,
            base_url=None,
        )
    )

    loaded = registry.get_workspace_server_entry("gpu-hostkey")
    listed = registry.list_workspace_servers()[1]

    assert loaded["host_key_fingerprint"] == "aa:bb:cc"
    assert listed["host_key_fingerprint"] == "aa:bb:cc"
