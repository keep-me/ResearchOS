from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import projects as projects_router
from packages.ai.project.amadeus_compat import build_remote_session_name
from packages.ai.project.workflow_catalog import (
    get_project_workflow_preset,
    is_active_project_workflow,
)
from packages.domain.enums import ProjectRunActionType, ProjectRunStatus, ProjectWorkflowType
from packages.storage import db
from packages.storage.db import Base

ACTIVE_WORKFLOWS = [
    workflow_type
    for workflow_type in ProjectWorkflowType
    if is_active_project_workflow(workflow_type)
]

PLANNED_WORKFLOWS = [
    workflow_type for workflow_type in ProjectWorkflowType if workflow_type not in ACTIVE_WORKFLOWS
]


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    import packages.storage.models  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _bind_projects_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "router-matrix-projects"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(projects_router, "default_projects_root", lambda: root)
    return root


def _create_project(name: str = "ARIS Router Matrix Project") -> dict:
    return projects_router.create_project(
        projects_router.ProjectCreateRequest(
            name=name,
            description="router matrix test",
        )
    )["item"]


@pytest.mark.parametrize("workflow_type", ACTIVE_WORKFLOWS, ids=lambda item: item.value)
def test_aris_router_active_workflow_create_and_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workflow_type: ProjectWorkflowType,
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project(f"Router {workflow_type.value}")
    project_id = project["id"]

    submitted_run_ids: list[str] = []

    monkeypatch.setattr(
        projects_router, "supports_project_run", lambda value: value == workflow_type
    )

    def _fake_submit_project_run(run_id: str):
        submitted_run_ids.append(run_id)
        return f"task-{workflow_type.value}"

    monkeypatch.setattr(projects_router, "submit_project_run", _fake_submit_project_run)

    created = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=workflow_type.value,
            prompt=f"Execute {workflow_type.value} via router matrix.",
            execution_command="python ./scripts/run_smoke.py"
            if workflow_type
            in {ProjectWorkflowType.run_experiment, ProjectWorkflowType.full_pipeline}
            else None,
            max_iterations=4,
            executor_model="mock-executor",
            reviewer_model="mock-reviewer",
        ),
    )["item"]

    preset = get_project_workflow_preset(workflow_type)
    assert preset is not None
    assert created["id"] in submitted_run_ids
    assert created["workflow_type"] == workflow_type.value
    assert created["orchestration"]["workflow_type"] == workflow_type.value
    assert len(created["orchestration"]["stages"]) == len(preset["stages"])
    assert len(created["stage_trace"]) == len(preset["stages"])
    assert created["run_directory"]
    assert created["log_path"]
    assert "同步方案：" in created["summary"]
    assert created["executor_model"] == "mock-executor"
    assert created["reviewer_model"] == "mock-reviewer"

    retried = projects_router.retry_project_run(created["id"])["item"]
    assert retried["retry_of_run_id"] == created["id"]
    assert retried["workflow_type"] == workflow_type.value
    assert retried["id"] in submitted_run_ids
    assert retried["run_directory"]
    assert retried["log_path"]
    assert retried["executor_model"] == "mock-executor"
    assert retried["reviewer_model"] == "mock-reviewer"


@pytest.mark.parametrize("workflow_type", PLANNED_WORKFLOWS, ids=lambda item: item.value)
def test_aris_router_planned_workflows_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workflow_type: ProjectWorkflowType,
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project(f"Router reject {workflow_type.value}")

    with pytest.raises(HTTPException) as exc:
        projects_router.create_project_run(
            project["id"],
            projects_router.ProjectRunCreateRequest(
                workflow_type=workflow_type.value,
                prompt=f"Try to execute {workflow_type.value}.",
            ),
        )

    assert exc.value.status_code == 400
    assert "尚未开放真实执行" in str(exc.value.detail)


@pytest.mark.parametrize("action_type", list(ProjectRunActionType), ids=lambda item: item.value)
def test_aris_router_action_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action_type: ProjectRunActionType,
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project(f"Router action {action_type.value}")
    project_id = project["id"]

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)
    created_run = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="Seed parent run for action matrix.",
        ),
    )["item"]

    submitted_action_ids: list[str] = []

    def _fake_submit_project_run_action(action_id: str):
        submitted_action_ids.append(action_id)
        with db.session_scope() as session:
            repo = projects_router.ProjectRepository(session)
            action = repo.get_run_action(action_id)
            run = repo.get_run(action.run_id) if action is not None else None
            if action is not None:
                repo.update_run_action(
                    action_id,
                    task_id=f"task-action-{action_type.value}",
                    log_path=(run.log_path + f".{action_type.value}.log")
                    if run and run.log_path
                    else f"/tmp/{action_id}.log",
                    result_path=(run.run_directory + f"/actions/{action_id}.md")
                    if run and run.run_directory
                    else f"/tmp/{action_id}.md",
                    status=ProjectRunStatus.running,
                )
        return f"task-action-{action_type.value}"

    monkeypatch.setattr(
        projects_router, "submit_project_run_action", _fake_submit_project_run_action
    )

    created_action = projects_router.create_project_run_action(
        created_run["id"],
        projects_router.ProjectRunActionRequest(
            action_type=action_type.value,
            prompt=f"Execute follow-up action {action_type.value}.",
        ),
    )["item"]

    assert created_action["id"] in submitted_action_ids
    assert created_action["run_id"] == created_run["id"]
    assert created_action["action_type"] == action_type.value
    assert created_action["task_id"] == f"task-action-{action_type.value}"
    assert created_action["log_path"]
    assert created_action["result_path"]


def test_aris_router_remote_run_plans_session_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = projects_router.create_project(
        projects_router.ProjectCreateRequest(
            name="Router remote run",
            description="remote run metadata planning",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/router-remote",
        )
    )["item"]

    submitted_run_ids: list[str] = []

    monkeypatch.setattr(
        projects_router,
        "supports_project_run",
        lambda value: value == ProjectWorkflowType.run_experiment,
    )
    monkeypatch.setattr(
        projects_router,
        "submit_project_run",
        lambda run_id: submitted_run_ids.append(run_id) or "task-remote-run",
    )

    created = projects_router.create_project_run(
        project["id"],
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.run_experiment.value,
            prompt="Launch the remote experiment.",
            execution_command="python train.py --epochs 1",
        ),
    )["item"]

    assert created["id"] in submitted_run_ids
    assert created["workspace_server_id"] == "ssh-main"
    assert created["metadata"]["remote_session_name"] == build_remote_session_name(created["id"])
    assert created["metadata"]["remote_execution_workspace"].endswith("/workspace")
    assert created["metadata"]["remote_isolation_mode"] == "pending"
    assert created["metadata"]["gpu_mode"] == "auto"
    assert created["metadata"]["gpu_strategy"] == "least_used_free"
    assert created["metadata"]["gpu_memory_threshold_mb"] == 500

    retried = projects_router.retry_project_run(created["id"])["item"]

    assert retried["retry_of_run_id"] == created["id"]
    assert retried["metadata"]["remote_session_name"] == build_remote_session_name(retried["id"])
    assert retried["metadata"]["remote_execution_workspace"].endswith("/workspace")
    assert retried["metadata"]["remote_isolation_mode"] == "pending"
    assert retried["metadata"]["gpu_mode"] == "auto"


def test_aris_router_remote_run_preserves_custom_gpu_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = projects_router.create_project(
        projects_router.ProjectCreateRequest(
            name="Router remote gpu override",
            description="preserve custom gpu metadata",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/router-remote-gpu",
        )
    )["item"]

    monkeypatch.setattr(
        projects_router,
        "supports_project_run",
        lambda value: value == ProjectWorkflowType.run_experiment,
    )
    monkeypatch.setattr(
        projects_router,
        "submit_project_run",
        lambda run_id: "task-remote-run-custom",
    )

    created = projects_router.create_project_run(
        project["id"],
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.run_experiment.value,
            prompt="Launch with custom gpu config.",
            execution_command="python train.py --epochs 1",
            metadata={
                "gpu_mode": "require",
                "gpu_strategy": "first_fit",
                "preferred_gpu_ids": [3, 1],
                "gpu_memory_threshold_mb": 256,
            },
        ),
    )["item"]

    assert created["metadata"]["gpu_mode"] == "require"
    assert created["metadata"]["gpu_strategy"] == "first_fit"
    assert created["metadata"]["preferred_gpu_ids"] == [3, 1]
    assert created["metadata"]["gpu_memory_threshold_mb"] == 256


def test_aris_router_remote_run_preserves_parallel_experiment_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = projects_router.create_project(
        projects_router.ProjectCreateRequest(
            name="Router remote batch run",
            description="preserve remote batch experiment metadata",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/router-remote-batch",
        )
    )["item"]

    monkeypatch.setattr(
        projects_router,
        "supports_project_run",
        lambda value: value == ProjectWorkflowType.run_experiment,
    )
    monkeypatch.setattr(
        projects_router,
        "submit_project_run",
        lambda run_id: "task-remote-batch",
    )

    created = projects_router.create_project_run(
        project["id"],
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.run_experiment.value,
            prompt="Launch the remote batch experiment.",
            metadata={
                "parallel_experiments": [
                    {"name": "baseline", "command": "python train.py --config baseline.yaml"},
                    {"name": "improved", "command": "python train.py --config improved.yaml"},
                ],
            },
        ),
    )["item"]

    assert created["metadata"]["remote_session_name"] == build_remote_session_name(created["id"])
    assert len(created["metadata"]["parallel_experiments"]) == 2
    assert created["metadata"]["parallel_experiments"][0]["name"] == "baseline"
