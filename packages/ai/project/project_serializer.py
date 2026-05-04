"""Serialization helpers for project API responses."""

from __future__ import annotations

from typing import Any

from packages.ai.project.output_sanitizer import sanitize_project_run_metadata


def iso_dt(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def serialize_repo(repo: Any) -> dict[str, Any]:
    return {
        "id": repo.id,
        "project_id": repo.project_id,
        "repo_url": repo.repo_url,
        "local_path": repo.local_path,
        "cloned_at": iso_dt(repo.cloned_at),
        "is_workdir_repo": bool(repo.is_workdir_repo),
        "created_at": iso_dt(repo.created_at),
        "updated_at": iso_dt(repo.updated_at),
    }


def serialize_idea(idea: Any) -> dict[str, Any]:
    return {
        "id": idea.id,
        "project_id": idea.project_id,
        "title": idea.title,
        "content": idea.content,
        "paper_ids": list(idea.paper_ids_json or []),
        "created_at": iso_dt(idea.created_at),
        "updated_at": iso_dt(idea.updated_at),
    }


def serialize_report(content: Any, paper: Any | None) -> dict[str, Any]:
    metadata = content.metadata_json or {}
    return {
        "id": content.id,
        "content_type": content.content_type,
        "title": content.title,
        "paper_id": content.paper_id,
        "paper_title": paper.title if paper else None,
        "keyword": content.keyword,
        "excerpt": str(content.markdown or "").strip()[:360],
        "metadata": metadata,
        "created_at": iso_dt(content.created_at),
    }


def serialize_run_action(action: Any, *, action_label: str | None = None) -> dict[str, Any]:
    metadata = sanitize_project_run_metadata(action.metadata_json or {})
    return {
        "id": action.id,
        "run_id": action.run_id,
        "action_type": str(action.action_type),
        "action_label": action_label or str(action.action_type),
        "prompt": action.prompt,
        "status": str(action.status),
        "active_phase": action.active_phase,
        "summary": action.summary,
        "task_id": action.task_id,
        "log_path": action.log_path,
        "result_path": action.result_path,
        "metadata": metadata,
        "created_at": iso_dt(action.created_at),
        "updated_at": iso_dt(action.updated_at),
    }


__all__ = [
    "serialize_idea",
    "serialize_repo",
    "serialize_report",
    "serialize_run_action",
]
