from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.domain.enums import (
    ProjectRunActionType,
    ProjectRunStatus,
    ProjectWorkflowType,
)
from packages.storage.db import Base
from packages.storage.repositories import ProjectRepository


def _make_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_project_workflow_records_roundtrip():
    session = _make_session()
    try:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name="Workflow Repo Test",
            description="test",
            workdir="D:/tmp/researchos-workflow-test",
        )

        target = repo.ensure_default_target(project.id)
        assert target is not None
        assert target.project_id == project.id
        assert target.is_primary is True
        assert target.workdir == "D:/tmp/researchos-workflow-test"

        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=ProjectWorkflowType.literature_review,
            title="Literature Review",
            prompt="Summarize recent progress.",
            status=ProjectRunStatus.queued,
            active_phase="queued",
            workspace_server_id=target.workspace_server_id,
            workdir=target.workdir,
            remote_workdir=target.remote_workdir,
        )
        action = repo.create_run_action(
            run_id=run.id,
            action_type=ProjectRunActionType.review,
            prompt="Review the outline and improve it.",
            status=ProjectRunStatus.queued,
        )
        session.commit()

        runs = repo.list_runs(project.id)
        assert len(runs) == 1
        assert runs[0].id == run.id
        assert runs[0].workflow_type == ProjectWorkflowType.literature_review

        actions = repo.list_run_actions(run.id)
        assert len(actions) == 1
        assert actions[0].id == action.id
        assert actions[0].action_type == ProjectRunActionType.review
        assert actions[0].status == ProjectRunStatus.queued
    finally:
        session.close()
