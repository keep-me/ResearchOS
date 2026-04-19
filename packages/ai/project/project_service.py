"""Application service helpers for project router flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.storage.repositories import PaperRepository, ProjectRepository


class ProjectServiceError(RuntimeError):
    """Base project service error."""


class ProjectNotFoundError(ProjectServiceError):
    """Raised when a project does not exist."""


@dataclass(slots=True)
class ProjectService:
    projects: ProjectRepository
    papers: PaperRepository

    def get_project_or_raise(self, project_id: str) -> Any:
        project = self.projects.get_project(project_id)
        if project is None:
            raise ProjectNotFoundError(f"project {project_id} not found")
        return project

    def list_project_papers(self, project_id: str) -> list[tuple[Any, Any]]:
        self.get_project_or_raise(project_id)
        return list(self.projects.list_project_papers(project_id))


__all__ = ["ProjectNotFoundError", "ProjectService", "ProjectServiceError"]

