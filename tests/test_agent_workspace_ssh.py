from __future__ import annotations

from types import SimpleNamespace

import paramiko
import pytest

from apps.api.routers.agent_workspace import _translate_workspace_error
from apps.api.routers.agent_workspace_ssh import format_ssh_exception
from packages.agent.workspace.workspace_remote import (
    FingerprintHostKeyPolicy,
    SSHWorkspaceSession,
    host_key_fingerprint,
    resolve_remote_workspace_path,
)


def test_format_ssh_exception_reports_real_ssh_banner(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.api.routers.agent_workspace_ssh.probe_ssh_banner",
        lambda host, port: {
            "state": "ssh",
            "banner": "SSH-2.0-OpenSSH_8.2p1 Ubuntu-4ubuntu0.13",
            "error": None,
        },
    )

    message = format_ssh_exception(
        Exception("Error reading SSH protocol banner"),
        host="cois.cloud",
        port=6000,
    )

    assert "cois.cloud:6000" in message
    assert "确实返回了 SSH 协议标识" in message
    assert "端口本身就是 SSH" in message


def test_format_ssh_exception_reports_non_ssh_banner(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.api.routers.agent_workspace_ssh.probe_ssh_banner",
        lambda host, port: {
            "state": "non_ssh",
            "banner": "HTTP/1.1 400 Bad Request",
            "error": None,
        },
    )

    message = format_ssh_exception(
        Exception("Error reading SSH protocol banner"),
        host="example.com",
        port=8080,
    )

    assert "example.com:8080" in message
    assert "返回的不是 SSH 协议标识" in message
    assert "HTTP/1.1 400 Bad Request" in message


def test_translate_workspace_error_uses_neutral_banner_message() -> None:
    response = _translate_workspace_error(Exception("Error reading SSH protocol banner"))

    assert response.status_code == 400
    assert "未能完成 SSH 协议标识读取" in str(response.detail)
    assert "先在终端手动执行一次 ssh" in str(response.detail)


def test_missing_ssh_host_key_requires_fingerprint() -> None:
    key = paramiko.RSAKey.generate(1024)
    policy = FingerprintHostKeyPolicy("")

    with pytest.raises(paramiko.SSHException, match="Unknown SSH host key"):
        policy.missing_host_key(paramiko.SSHClient(), "example.com", key)


def test_missing_ssh_host_key_accepts_matching_fingerprint() -> None:
    key = paramiko.RSAKey.generate(1024)
    client = paramiko.SSHClient()
    policy = FingerprintHostKeyPolicy(host_key_fingerprint(key))

    policy.missing_host_key(client, "example.com", key)

    assert client.get_host_keys().lookup("example.com") is not None


def test_resolve_remote_workspace_path_rejects_absolute_requested_path() -> None:
    session = SSHWorkspaceSession(
        client=SimpleNamespace(), sftp=SimpleNamespace(), home_dir="/home/research"
    )

    with pytest.raises(Exception, match="相对路径"):
        resolve_remote_workspace_path({"workspace_root": "/srv/research"}, "/etc", session)


def test_resolve_remote_workspace_path_rejects_parent_traversal() -> None:
    session = SSHWorkspaceSession(
        client=SimpleNamespace(), sftp=SimpleNamespace(), home_dir="/home/research"
    )

    with pytest.raises(Exception, match="越界"):
        resolve_remote_workspace_path({"workspace_root": "/srv/research"}, "../etc", session)


def test_resolve_remote_workspace_path_allows_relative_path_under_configured_root() -> None:
    session = SSHWorkspaceSession(
        client=SimpleNamespace(), sftp=SimpleNamespace(), home_dir="/home/research"
    )

    resolved = resolve_remote_workspace_path(
        {"workspace_root": "/srv/research"},
        "runs/demo",
        session,
    )

    assert resolved == "/srv/research/runs/demo"
