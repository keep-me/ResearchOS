"""Workspace execution and inspection facade for project workflows."""

from __future__ import annotations

from typing import Any


def command_result_preview(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _command_result_preview

    return _command_result_preview(*args, **kwargs)


def format_command_log(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _format_command_log

    return _format_command_log(*args, **kwargs)


def inspect_workspace_payload(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _inspect_workspace_payload

    return _inspect_workspace_payload(*args, **kwargs)


def run_workspace_command_for_context(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _run_workspace_command_for_context

    return _run_workspace_command_for_context(*args, **kwargs)


__all__ = [
    "command_result_preview",
    "format_command_log",
    "inspect_workspace_payload",
    "run_workspace_command_for_context",
]

