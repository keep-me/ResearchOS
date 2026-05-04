"""Artifact helpers shared by project workflow executors.

The implementation currently delegates to the legacy workflow runner to keep
behavior stable while callers move to this public facade.
"""

from __future__ import annotations

from typing import Any


def collect_run_artifacts(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _collect_run_artifacts

    return _collect_run_artifacts(*args, **kwargs)


def write_run_artifact(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _write_run_artifact

    return _write_run_artifact(*args, **kwargs)


def write_run_json_artifact(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _write_run_json_artifact

    return _write_run_json_artifact(*args, **kwargs)


def write_run_log(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _write_run_log

    return _write_run_log(*args, **kwargs)


__all__ = [
    "collect_run_artifacts",
    "write_run_artifact",
    "write_run_json_artifact",
    "write_run_log",
]
