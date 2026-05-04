"""LLM role and model-target facade for project workflows."""

from __future__ import annotations

from typing import Any


def resolve_role_profile(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _resolve_role_profile

    return _resolve_role_profile(*args, **kwargs)


def resolve_stage_model_target(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _resolve_stage_model_target

    return _resolve_stage_model_target(*args, **kwargs)


__all__ = [
    "resolve_role_profile",
    "resolve_stage_model_target",
]
