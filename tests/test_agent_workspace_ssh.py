from __future__ import annotations

from apps.api.routers.agent_workspace import _translate_workspace_error
from apps.api.routers.agent_workspace_ssh import format_ssh_exception


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
