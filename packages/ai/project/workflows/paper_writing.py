"""Paper-writing workflow entrypoint."""

from __future__ import annotations

from typing import Any


def execute(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _execute_paper_writing

    return _execute_paper_writing(*args, **kwargs)
