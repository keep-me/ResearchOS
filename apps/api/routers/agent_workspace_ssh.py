"""Compatibility wrapper for remote workspace helpers."""

from packages.agent import workspace_remote as _remote
from packages.agent.workspace.workspace_remote import *  # noqa: F401,F403

probe_ssh_banner = _remote.probe_ssh_banner


def format_ssh_exception(exc: Exception, *, host: str, port: int) -> str:
    original_probe = _remote.probe_ssh_banner
    _remote.probe_ssh_banner = probe_ssh_banner
    try:
        return _remote.format_ssh_exception(exc, host=host, port=port)
    finally:
        _remote.probe_ssh_banner = original_probe

