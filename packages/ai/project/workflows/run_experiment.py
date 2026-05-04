"""Run-experiment workflow entrypoint."""

from __future__ import annotations

from typing import Any


def execute(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _execute_run_experiment

    return _execute_run_experiment(*args, **kwargs)
