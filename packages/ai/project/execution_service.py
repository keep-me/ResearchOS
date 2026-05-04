from __future__ import annotations

from uuid import uuid4

from packages.ai.project.checkpoint_service import (
    checkpoint_resume_stage,
    mark_run_waiting_for_checkpoint,
    should_pause_for_preflight,
)
from packages.ai.project.multi_agent_runner import (
    submit_multi_agent_project_run,
    supports_multi_agent_project_workflow,
)
from packages.ai.project.workflow_catalog import build_run_orchestration
from packages.ai.project.workflow_runner import (
    submit_project_run as submit_native_project_run,
)
from packages.ai.project.workflow_runner import (
    supports_project_workflow as supports_native_project_workflow,
)
from packages.storage.db import session_scope
from packages.storage.repository_facades import ProjectDataFacade


def supports_project_run(workflow_type) -> bool:
    return supports_native_project_workflow(workflow_type) or supports_multi_agent_project_workflow(
        workflow_type
    )


def submit_project_run(run_id: str) -> str | None:
    with session_scope() as session:
        repos = ProjectDataFacade.from_session(session)
        run = repos.projects.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found")
        workflow_type = run.workflow_type
        metadata = dict(run.metadata_json or {})
        orchestration = build_run_orchestration(
            workflow_type,
            metadata.get("orchestration"),
            target_id=run.target_id,
            workspace_server_id=run.workspace_server_id,
        )
        task_id = str(run.task_id or f"project_run_{run.id.replace('-', '')[:8]}_{uuid4().hex[:4]}")
        resume_stage_id = checkpoint_resume_stage(metadata)

    stages = orchestration.get("stages") or []

    if should_pause_for_preflight(metadata):
        return mark_run_waiting_for_checkpoint(run_id, task_id=task_id)

    if supports_native_project_workflow(workflow_type):
        return submit_native_project_run(run_id, resume_stage_id=resume_stage_id)
    if supports_multi_agent_project_workflow(workflow_type) and stages:
        return submit_multi_agent_project_run(run_id, resume_stage_id=resume_stage_id)
    return None
