"""Auto-review-loop workflow entrypoint."""

from __future__ import annotations

from typing import Any


def execute(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _execute_auto_review_loop

    return _execute_auto_review_loop(*args, **kwargs)
