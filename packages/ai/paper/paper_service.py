"""Application service helpers for paper router flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from packages.storage.repositories import PaperRepository


class PaperServiceError(RuntimeError):
    """Base paper service error."""


class PaperNotFoundError(PaperServiceError):
    """Raised when a paper does not exist."""


@dataclass(slots=True)
class PaperService:
    papers: PaperRepository

    def get_paper_or_raise(self, paper_id: UUID | str) -> Any:
        paper = self.papers.get_paper(str(paper_id))
        if paper is None:
            raise PaperNotFoundError(f"paper {paper_id} not found")
        return paper


__all__ = ["PaperNotFoundError", "PaperService", "PaperServiceError"]
