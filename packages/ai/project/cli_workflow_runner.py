from __future__ import annotations

from packages.ai.project.multi_agent_runner import (
    run_multi_agent_project_workflow,
    submit_multi_agent_project_run,
    supports_multi_agent_project_workflow,
)

# Backward-compatible aliases (legacy name only).
supports_cli_project_workflow = supports_multi_agent_project_workflow
submit_cli_project_run = submit_multi_agent_project_run
run_cli_project_workflow = run_multi_agent_project_workflow

__all__ = [
    "supports_multi_agent_project_workflow",
    "submit_multi_agent_project_run",
    "run_multi_agent_project_workflow",
    "supports_cli_project_workflow",
    "submit_cli_project_run",
    "run_cli_project_workflow",
]
