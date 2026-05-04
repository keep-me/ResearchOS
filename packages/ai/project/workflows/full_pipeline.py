"""Full-pipeline workflow entrypoint."""

from __future__ import annotations

from typing import Any


def execute(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _execute_full_pipeline

    return _execute_full_pipeline(*args, **kwargs)
