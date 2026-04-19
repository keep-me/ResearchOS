"""Idea-discovery workflow entrypoint."""

from __future__ import annotations

from typing import Any


def execute(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _execute_idea_discovery

    return _execute_idea_discovery(*args, **kwargs)

