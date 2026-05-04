"""Stage state and run patching facade for project workflows."""

from __future__ import annotations

from typing import Any


def cancel_active_stage(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _cancel_active_stage

    return _cancel_active_stage(*args, **kwargs)


def emit_progress(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _emit_progress

    return _emit_progress(*args, **kwargs)


def ensure_run_orchestration(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _ensure_run_orchestration

    return _ensure_run_orchestration(*args, **kwargs)


def fail_active_stage(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _fail_active_stage

    return _fail_active_stage(*args, **kwargs)


def iso_now(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _iso_now

    return _iso_now(*args, **kwargs)


def maybe_pause_after_stage(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _maybe_pause_after_stage

    return _maybe_pause_after_stage(*args, **kwargs)


def patch_run(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _patch_run

    return _patch_run(*args, **kwargs)


def record_stage_output(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _record_stage_output

    return _record_stage_output(*args, **kwargs)


def set_stage_state(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _set_stage_state

    return _set_stage_state(*args, **kwargs)


__all__ = [
    "cancel_active_stage",
    "emit_progress",
    "ensure_run_orchestration",
    "fail_active_stage",
    "iso_now",
    "maybe_pause_after_stage",
    "patch_run",
    "record_stage_output",
    "set_stage_state",
]
