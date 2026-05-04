"""Project repository extracted from the monolithic repository module."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.domain.enums import ProjectRunActionType, ProjectRunStatus, ProjectWorkflowType
from packages.storage.json_schema import with_schema_version
from packages.storage.models import (
    GeneratedContent,
    Paper,
    Project,
    ProjectDeploymentTarget,
    ProjectIdea,
    ProjectPaper,
    ProjectRepo,
    ProjectRun,
    ProjectRunAction,
)


def _normalize_workspace_match_key(value: str | None) -> str:
    raw = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not raw:
        return ""
    return raw.casefold()


def _path_matches_workspace(candidate: str | None, workspace_path: str | None) -> bool:
    base = _normalize_workspace_match_key(candidate)
    target = _normalize_workspace_match_key(workspace_path)
    if not base or not target:
        return False
    return target == base or target.startswith(f"{base}/")


class ProjectRepository:
    """研究项目仓储。"""

    def __init__(self, session: Session):
        self.session = session

    def create_project(
        self,
        *,
        name: str,
        description: str | None = None,
        workdir: str | None = None,
        workspace_server_id: str | None = None,
        remote_workdir: str | None = None,
    ) -> Project:
        project = Project(
            name=name,
            description=description,
            workdir=workdir,
            workspace_server_id=workspace_server_id,
            remote_workdir=remote_workdir,
        )
        self.session.add(project)
        self.session.flush()
        return project

    def list_projects(self) -> list[Project]:
        rows = (
            self.session.execute(
                select(Project).order_by(
                    func.coalesce(Project.last_accessed_at, Project.created_at).desc(),
                    Project.created_at.desc(),
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def get_project(self, project_id: str) -> Project | None:
        return self.session.get(Project, project_id)

    def find_project_by_workspace_path(
        self,
        workspace_path: str | None,
        *,
        workspace_server_id: str | None = None,
    ) -> Project | None:
        normalized_target = _normalize_workspace_match_key(workspace_path)
        if not normalized_target:
            return None

        server_id = str(workspace_server_id or "").strip() or None
        best_match: tuple[int, Project] | None = None
        projects = self.list_projects()
        for project in projects:
            if server_id and str(project.workspace_server_id or "").strip() not in {"", server_id}:
                continue
            candidates = [
                str(project.workdir or "").strip(),
                str(project.remote_workdir or "").strip(),
            ]
            repos = self.list_repos(project.id)
            candidates.extend(str(repo.local_path or "").strip() for repo in repos)
            for candidate in candidates:
                if not _path_matches_workspace(candidate, normalized_target):
                    continue
                score = len(_normalize_workspace_match_key(candidate))
                if best_match is None or score > best_match[0]:
                    best_match = (score, project)
        return best_match[1] if best_match else None

    def update_project(self, project_id: str, **kwargs) -> Project | None:
        project = self.get_project(project_id)
        if project is None:
            return None
        for key, value in kwargs.items():
            if hasattr(project, key):
                setattr(project, key, value)
        self.session.flush()
        return project

    def delete_project(self, project_id: str) -> bool:
        project = self.get_project(project_id)
        if project is None:
            return False
        self.session.delete(project)
        self.session.flush()
        return True

    def touch_last_accessed(self, project_id: str) -> Project | None:
        project = self.get_project(project_id)
        if project is None:
            return None
        project.last_accessed_at = datetime.now(UTC)
        self.session.flush()
        return project

    def _sync_project_workspace_from_target(
        self,
        project: Project | None,
        target: ProjectDeploymentTarget | None,
    ) -> None:
        if project is None or target is None:
            return
        project.workspace_server_id = target.workspace_server_id
        if target.workspace_server_id:
            project.remote_workdir = target.remote_workdir
        else:
            project.workdir = target.workdir
            project.remote_workdir = None

    def list_targets(self, project_id: str) -> list[ProjectDeploymentTarget]:
        rows = (
            self.session.execute(
                select(ProjectDeploymentTarget)
                .where(ProjectDeploymentTarget.project_id == project_id)
                .order_by(
                    ProjectDeploymentTarget.is_primary.desc(),
                    ProjectDeploymentTarget.created_at.asc(),
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def get_target(self, target_id: str) -> ProjectDeploymentTarget | None:
        return self.session.get(ProjectDeploymentTarget, target_id)

    def get_primary_target(self, project_id: str) -> ProjectDeploymentTarget | None:
        return (
            self.session.execute(
                select(ProjectDeploymentTarget)
                .where(
                    ProjectDeploymentTarget.project_id == project_id,
                    ProjectDeploymentTarget.is_primary == True,
                )
                .order_by(ProjectDeploymentTarget.created_at.asc())
            )
            .scalars()
            .first()
        )

    def ensure_default_target(self, project_id: str) -> ProjectDeploymentTarget | None:
        existing = self.list_targets(project_id)
        if existing:
            return existing[0]

        project = self.get_project(project_id)
        if project is None:
            return None

        workspace_server_id = project.workspace_server_id
        workdir = project.workdir if workspace_server_id is None else None
        remote_workdir = project.remote_workdir if workspace_server_id else None
        if not (workdir or remote_workdir):
            return None

        target = ProjectDeploymentTarget(
            project_id=project_id,
            label="Primary Workspace",
            workspace_server_id=workspace_server_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            enabled=True,
            is_primary=True,
        )
        self.session.add(target)
        self.session.flush()
        return target

    def create_target(
        self,
        *,
        project_id: str,
        label: str,
        workspace_server_id: str | None = None,
        workdir: str | None = None,
        remote_workdir: str | None = None,
        dataset_root: str | None = None,
        checkpoint_root: str | None = None,
        output_root: str | None = None,
        enabled: bool = True,
        is_primary: bool = False,
    ) -> ProjectDeploymentTarget:
        project = self.get_project(project_id)
        existing = self.list_targets(project_id)
        should_be_primary = is_primary or not existing
        if should_be_primary:
            for item in existing:
                item.is_primary = False

        target = ProjectDeploymentTarget(
            project_id=project_id,
            label=label,
            workspace_server_id=workspace_server_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            dataset_root=dataset_root,
            checkpoint_root=checkpoint_root,
            output_root=output_root,
            enabled=enabled,
            is_primary=should_be_primary,
        )
        self.session.add(target)
        self.session.flush()
        if target.is_primary:
            self._sync_project_workspace_from_target(project, target)
        self.session.flush()
        return target

    def update_target(self, target_id: str, **kwargs) -> ProjectDeploymentTarget | None:
        target = self.get_target(target_id)
        if target is None:
            return None

        project = self.get_project(target.project_id)
        requested_primary = kwargs.get("is_primary")
        if requested_primary:
            for item in self.list_targets(target.project_id):
                if item.id != target.id:
                    item.is_primary = False

        for key, value in kwargs.items():
            if hasattr(target, key):
                setattr(target, key, value)

        if target.is_primary:
            self._sync_project_workspace_from_target(project, target)
        self.session.flush()
        return target

    def delete_target(self, target_id: str) -> bool:
        target = self.get_target(target_id)
        if target is None:
            return False

        project_id = target.project_id
        was_primary = bool(target.is_primary)
        self.session.delete(target)
        self.session.flush()

        if was_primary:
            next_target = self.get_primary_target(project_id)
            if next_target is None:
                remaining = self.list_targets(project_id)
                next_target = remaining[0] if remaining else None
                if next_target is not None:
                    next_target.is_primary = True
            self._sync_project_workspace_from_target(self.get_project(project_id), next_target)
            self.session.flush()
        return True

    def list_runs(self, project_id: str, limit: int = 50) -> list[ProjectRun]:
        rows = (
            self.session.execute(
                select(ProjectRun)
                .where(ProjectRun.project_id == project_id)
                .order_by(ProjectRun.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def count_runs(self, project_id: str) -> int:
        value = self.session.execute(
            select(func.count(ProjectRun.id)).where(ProjectRun.project_id == project_id)
        ).scalar_one()
        return int(value or 0)

    def get_run(self, run_id: str) -> ProjectRun | None:
        return self.session.get(ProjectRun, run_id)

    def delete_run(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        if run is None:
            return False
        for action in self.list_run_actions(run_id):
            self.session.delete(action)
        self.session.delete(run)
        self.session.flush()
        return True

    def create_run(
        self,
        *,
        project_id: str,
        workflow_type: ProjectWorkflowType,
        prompt: str,
        title: str = "",
        target_id: str | None = None,
        status: ProjectRunStatus = ProjectRunStatus.queued,
        active_phase: str = "queued",
        summary: str = "",
        task_id: str | None = None,
        workspace_server_id: str | None = None,
        workdir: str | None = None,
        remote_workdir: str | None = None,
        dataset_root: str | None = None,
        checkpoint_root: str | None = None,
        output_root: str | None = None,
        log_path: str | None = None,
        result_path: str | None = None,
        run_directory: str | None = None,
        retry_of_run_id: str | None = None,
        max_iterations: int | None = None,
        executor_model: str | None = None,
        reviewer_model: str | None = None,
        metadata: dict | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> ProjectRun:
        run = ProjectRun(
            project_id=project_id,
            target_id=target_id,
            workflow_type=workflow_type,
            prompt=prompt,
            title=title,
            status=status,
            active_phase=active_phase,
            summary=summary,
            task_id=task_id,
            workspace_server_id=workspace_server_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            dataset_root=dataset_root,
            checkpoint_root=checkpoint_root,
            output_root=output_root,
            log_path=log_path,
            result_path=result_path,
            run_directory=run_directory,
            retry_of_run_id=retry_of_run_id,
            max_iterations=max_iterations,
            executor_model=executor_model,
            reviewer_model=reviewer_model,
            metadata_json=with_schema_version(metadata),
            started_at=started_at,
            finished_at=finished_at,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def update_run(self, run_id: str, **kwargs) -> ProjectRun | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        for key, value in kwargs.items():
            if key == "metadata":
                run.metadata_json = with_schema_version(value)
            elif hasattr(run, key):
                setattr(run, key, value)
        self.session.flush()
        return run

    def list_run_actions(self, run_id: str) -> list[ProjectRunAction]:
        rows = (
            self.session.execute(
                select(ProjectRunAction)
                .where(ProjectRunAction.run_id == run_id)
                .order_by(ProjectRunAction.created_at.asc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def get_run_action(self, action_id: str) -> ProjectRunAction | None:
        return self.session.get(ProjectRunAction, action_id)

    def delete_run_action(self, action_id: str) -> bool:
        action = self.get_run_action(action_id)
        if action is None:
            return False
        self.session.delete(action)
        self.session.flush()
        return True

    def create_run_action(
        self,
        *,
        run_id: str,
        action_type: ProjectRunActionType,
        prompt: str,
        status: ProjectRunStatus = ProjectRunStatus.queued,
        active_phase: str = "queued",
        summary: str = "",
        task_id: str | None = None,
        log_path: str | None = None,
        result_path: str | None = None,
        metadata: dict | None = None,
    ) -> ProjectRunAction:
        action = ProjectRunAction(
            run_id=run_id,
            action_type=action_type,
            prompt=prompt,
            status=status,
            active_phase=active_phase,
            summary=summary,
            task_id=task_id,
            log_path=log_path,
            result_path=result_path,
            metadata_json=with_schema_version(metadata),
        )
        self.session.add(action)
        self.session.flush()
        return action

    def update_run_action(self, action_id: str, **kwargs) -> ProjectRunAction | None:
        action = self.get_run_action(action_id)
        if action is None:
            return None
        for key, value in kwargs.items():
            if key == "metadata":
                action.metadata_json = with_schema_version(value)
            elif hasattr(action, key):
                setattr(action, key, value)
        self.session.flush()
        return action

    def list_repos(self, project_id: str) -> list[ProjectRepo]:
        rows = (
            self.session.execute(
                select(ProjectRepo)
                .where(ProjectRepo.project_id == project_id)
                .order_by(ProjectRepo.created_at.asc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def create_repo(
        self,
        *,
        project_id: str,
        repo_url: str,
        local_path: str | None = None,
        cloned_at: datetime | None = None,
        is_workdir_repo: bool = False,
    ) -> ProjectRepo:
        repo = ProjectRepo(
            project_id=project_id,
            repo_url=repo_url,
            local_path=local_path,
            cloned_at=cloned_at,
            is_workdir_repo=is_workdir_repo,
        )
        self.session.add(repo)
        self.session.flush()
        return repo

    def get_repo(self, repo_id: str) -> ProjectRepo | None:
        return self.session.get(ProjectRepo, repo_id)

    def update_repo(self, repo_id: str, **kwargs) -> ProjectRepo | None:
        repo = self.get_repo(repo_id)
        if repo is None:
            return None
        for key, value in kwargs.items():
            if hasattr(repo, key):
                setattr(repo, key, value)
        self.session.flush()
        return repo

    def delete_repo(self, repo_id: str) -> bool:
        repo = self.get_repo(repo_id)
        if repo is None:
            return False
        self.session.delete(repo)
        self.session.flush()
        return True

    def list_ideas(self, project_id: str) -> list[ProjectIdea]:
        rows = (
            self.session.execute(
                select(ProjectIdea)
                .where(ProjectIdea.project_id == project_id)
                .order_by(ProjectIdea.updated_at.desc(), ProjectIdea.created_at.desc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def create_idea(
        self,
        *,
        project_id: str,
        title: str,
        content: str,
        paper_ids: list[str] | None = None,
    ) -> ProjectIdea:
        idea = ProjectIdea(
            project_id=project_id,
            title=title,
            content=content,
            paper_ids_json=paper_ids or [],
        )
        self.session.add(idea)
        self.session.flush()
        return idea

    def get_idea(self, idea_id: str) -> ProjectIdea | None:
        return self.session.get(ProjectIdea, idea_id)

    def update_idea(self, idea_id: str, **kwargs) -> ProjectIdea | None:
        idea = self.get_idea(idea_id)
        if idea is None:
            return None
        for key, value in kwargs.items():
            if key == "paper_ids":
                idea.paper_ids_json = value or []
            elif hasattr(idea, key):
                setattr(idea, key, value)
        self.session.flush()
        return idea

    def delete_idea(self, idea_id: str) -> bool:
        idea = self.get_idea(idea_id)
        if idea is None:
            return False
        self.session.delete(idea)
        self.session.flush()
        return True

    def add_paper_to_project(
        self,
        *,
        project_id: str,
        paper_id: str,
        note: str | None = None,
    ) -> ProjectPaper:
        existing = self.session.execute(
            select(ProjectPaper).where(
                ProjectPaper.project_id == project_id,
                ProjectPaper.paper_id == paper_id,
            )
        ).scalar_one_or_none()
        if existing:
            existing.note = note
            self.session.flush()
            return existing

        row = ProjectPaper(project_id=project_id, paper_id=paper_id, note=note)
        self.session.add(row)
        self.session.flush()
        return row

    def remove_paper_from_project(self, project_id: str, paper_id: str) -> bool:
        row = self.session.execute(
            select(ProjectPaper).where(
                ProjectPaper.project_id == project_id,
                ProjectPaper.paper_id == paper_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def list_project_papers(self, project_id: str) -> list[tuple[ProjectPaper, Paper]]:
        rows = self.session.execute(
            select(ProjectPaper, Paper)
            .join(Paper, Paper.id == ProjectPaper.paper_id)
            .where(ProjectPaper.project_id == project_id)
            .order_by(ProjectPaper.added_at.desc())
        ).all()
        return [(row[0], row[1]) for row in rows]

    def list_projects_for_paper(self, paper_id: str) -> list[Project]:
        rows = (
            self.session.execute(
                select(Project)
                .join(ProjectPaper, ProjectPaper.project_id == Project.id)
                .where(ProjectPaper.paper_id == paper_id)
                .order_by(Project.created_at.desc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_project_reports(
        self,
        project_id: str,
        limit: int = 50,
    ) -> list[tuple[GeneratedContent, Paper | None]]:
        rows = self.session.execute(
            select(GeneratedContent, Paper)
            .join(ProjectPaper, ProjectPaper.paper_id == GeneratedContent.paper_id)
            .join(Paper, Paper.id == GeneratedContent.paper_id, isouter=True)
            .where(ProjectPaper.project_id == project_id)
            .order_by(GeneratedContent.created_at.desc())
            .limit(limit)
        ).all()
        return [(row[0], row[1]) for row in rows]
