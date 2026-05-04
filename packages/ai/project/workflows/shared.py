"""Shared workflow helpers exposed behind a stable module boundary."""

from __future__ import annotations

from typing import Any


def build_experiment_audit_prompt(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _build_experiment_audit_prompt

    return _build_experiment_audit_prompt(*args, **kwargs)


def collect_experiment_audit_bundle(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _collect_experiment_audit_bundle

    return _collect_experiment_audit_bundle(*args, **kwargs)


def load_context(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _load_context

    return _load_context(*args, **kwargs)


def markdown_excerpt(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _markdown_excerpt

    return _markdown_excerpt(*args, **kwargs)


def render_experiment_audit_report(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _render_experiment_audit_report

    return _render_experiment_audit_report(*args, **kwargs)


def resolve_execution_command(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _resolve_execution_command

    return _resolve_execution_command(*args, **kwargs)


def resolve_execution_timeout(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _resolve_execution_timeout

    return _resolve_execution_timeout(*args, **kwargs)


def resolve_experiment_audit_payload(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _resolve_experiment_audit_payload

    return _resolve_experiment_audit_payload(*args, **kwargs)


def resolve_idea_payloads(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _resolve_idea_payloads

    return _resolve_idea_payloads(*args, **kwargs)


def resolve_literature_markdown(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _resolve_literature_markdown

    return _resolve_literature_markdown(*args, **kwargs)


__all__ = [
    "build_experiment_audit_prompt",
    "collect_experiment_audit_bundle",
    "load_context",
    "markdown_excerpt",
    "render_experiment_audit_report",
    "resolve_execution_command",
    "resolve_execution_timeout",
    "resolve_experiment_audit_payload",
    "resolve_idea_payloads",
    "resolve_literature_markdown",
]
