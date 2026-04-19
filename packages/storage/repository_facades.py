"""Lightweight repository bundles for router and service boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from packages.storage.repositories import (
    GeneratedContentRepository,
    PaperRepository,
    ProjectRepository,
    TaskRepository,
    TopicRepository,
)


@dataclass(slots=True)
class PaperDataFacade:
    papers: PaperRepository

    @classmethod
    def from_session(cls, session: Session) -> "PaperDataFacade":
        return cls(papers=PaperRepository(session))


@dataclass(slots=True)
class TopicDataFacade:
    topics: TopicRepository
    papers: PaperRepository

    @classmethod
    def from_session(cls, session: Session) -> "TopicDataFacade":
        return cls(
            topics=TopicRepository(session),
            papers=PaperRepository(session),
        )


@dataclass(slots=True)
class ProjectDataFacade:
    projects: ProjectRepository
    papers: PaperRepository
    tasks: TaskRepository
    generated: GeneratedContentRepository

    @classmethod
    def from_session(cls, session: Session) -> "ProjectDataFacade":
        return cls(
            projects=ProjectRepository(session),
            papers=PaperRepository(session),
            tasks=TaskRepository(session),
            generated=GeneratedContentRepository(session),
        )
