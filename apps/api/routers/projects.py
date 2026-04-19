"""ResearchClaw Projects for ResearchOS."""

from __future__ import annotations

import posixpath
import re
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm.exc import StaleDataError

from apps.api.deps import iso_dt, paper_list_response, pipelines
from packages.domain.enums import (
    ActionType,
    ProjectRunActionType,
    ProjectRunStatus,
    ProjectWorkflowType,
)
from packages.agent.workspace.workspace_remote import build_remote_overview, remote_terminal_result
from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry
from packages.ai.project.amadeus_compat import (
    amadeus_action_label,
    build_run_directory,
    build_run_log_path,
    build_run_workspace_path,
    build_remote_session_name,
    describe_sync_strategy,
    infer_sync_strategy,
)
from packages.ai.project.checkpoint_service import (
    auto_proceed_enabled,
    build_checkpoint_settings,
    checkpoint_state,
    normalize_notification_recipients,
    pending_checkpoint,
    process_checkpoint_response,
)
from packages.ai.project.execution_service import (
    submit_project_run,
    supports_project_run,
)
from packages.agent.runtime.acp_service import get_acp_registry_service
from packages.agent.session.session_runtime import (
    list_session_messages,
    list_sessions,
)
from packages.domain.task_tracker import global_tracker
from packages.ai.project.run_action_service import submit_project_run_action
from packages.ai.project.followup_actions import list_followup_actions
from packages.ai.project.workflow_catalog import (
    build_run_orchestration,
    build_stage_trace,
    is_active_project_workflow,
    list_project_agent_templates,
    list_public_project_workflow_presets,
    list_project_workflow_presets,
)
from packages.ai.project.output_sanitizer import sanitize_project_run_metadata
from packages.ai.project.report_formatter import build_workflow_report_markdown, markdown_excerpt
from packages.ai.project.paper_context import (
    clean_text as _clean_paper_context_text,
    load_analysis_reports,
    merge_refs as merge_paper_refs,
    normalize_paper_ids,
    paper_ref_from_model,
)
from packages.ai.project.project_serializer import (
    serialize_idea as _serialize_idea,
    serialize_repo as _serialize_repo,
    serialize_report as _serialize_report,
    serialize_run_action as _serialize_run_action_payload,
)
from packages.agent.workspace.workspace_executor import default_projects_root, inspect_workspace, run_workspace_command
from packages.integrations.llm_engine_profiles import (
    list_llm_engine_profiles,
    public_engine_profile_payload,
    recommend_llm_engine_profiles,
    resolve_llm_engine_profile,
)
from packages.integrations.llm_client import LLMClient
from packages.ai.paper.pipelines import PaperPipelines
from packages.storage.db import session_scope
from packages.storage.models import Paper
from packages.storage.repository_facades import ProjectDataFacade
from packages.storage.repositories import PaperRepository, ProjectRepository

router = APIRouter()

WORKFLOW_PRESETS = list_project_workflow_presets()
PROJECT_AGENT_TEMPLATES = list_project_agent_templates()

RUN_ACTION_PRESETS = list_followup_actions(None)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_server_id(value: str | None) -> str | None:
    server_id = _clean_text(value)
    if not server_id or server_id == "local":
        return None
    return server_id


def _project_data(session):
    return ProjectDataFacade.from_session(session)


def _slugify_project_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "project"


def _ensure_local_workdir(name: str, requested: str | None = None) -> str:
    if requested:
        workdir = Path(requested).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
        return str(workdir.resolve())

    root = default_projects_root()
    base = root / _slugify_project_name(name)
    candidate = base
    suffix = 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = root / f"{base.name}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=True)
    return str(candidate.resolve())


def _workflow_label(value: str) -> str:
    for item in WORKFLOW_PRESETS:
        if item["workflow_type"] == value:
            return str(item["label"])
    return value.replace("_", " ").strip() or "Workflow"


def _action_label(value: str) -> str:
    for item in RUN_ACTION_PRESETS:
        if item["action_type"] == value:
            return str(item["label"])
    return amadeus_action_label(value)


def _trim_preview_text(value: str | None, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _build_run_paper_index(
    project_repo: ProjectRepository,
    paper_repo: PaperRepository,
    project_id: str,
    selected_paper_ids: list[str] | None = None,
) -> list[dict]:
    selected_ids = normalize_paper_ids(selected_paper_ids)
    project_rows = project_repo.list_project_papers(project_id)
    project_ids = [str(paper.id) for _link, paper in project_rows]
    ordered_ids = [*selected_ids, *[paper_id for paper_id in project_ids if paper_id not in selected_ids]]
    if not ordered_ids:
        return []

    selected_set = set(selected_ids)
    project_id_set = set(project_ids)
    project_note_by_id = {str(paper.id): link.note for link, paper in project_rows}
    paper_by_id = {str(paper.id): paper for _link, paper in project_rows}

    missing_ids = [paper_id for paper_id in selected_ids if paper_id not in paper_by_id]
    if missing_ids:
        for paper in paper_repo.list_by_ids(missing_ids):
            paper_by_id[str(paper.id)] = paper

    analysis_by_id = load_analysis_reports(project_repo.session, ordered_ids)
    refs: list[dict] = []
    for index, paper_id in enumerate(ordered_ids, start=1):
        paper = paper_by_id.get(paper_id)
        if paper is None:
            continue
        selected = paper_id in selected_set
        project_linked = paper_id in project_id_set
        source = "selected" if selected else "project_linked"
        refs.append(
            paper_ref_from_model(
                paper,
                ref_id=f"P{index}",
                source=source,
                match_reason="启动时显式选择" if selected else "项目已关联论文",
                selected=selected,
                project_linked=project_linked,
                analysis_report=analysis_by_id.get(paper_id),
                note=project_note_by_id.get(paper_id),
            )
        )
    return refs


def _candidate_to_external_entry(candidate: dict) -> dict:
    return {
        "title": _clean_paper_context_text(candidate.get("title")),
        "abstract": str(candidate.get("abstract") or "").strip(),
        "publication_year": candidate.get("publication_year") or candidate.get("year"),
        "publication_date": _clean_paper_context_text(candidate.get("publication_date")) or None,
        "citation_count": candidate.get("citation_count"),
        "venue": _clean_paper_context_text(candidate.get("venue")) or None,
        "venue_type": _clean_paper_context_text(candidate.get("venue_type")) or None,
        "venue_tier": _clean_paper_context_text(candidate.get("venue_tier")) or None,
        "authors": [
            _clean_paper_context_text(item)
            for item in (candidate.get("authors") if isinstance(candidate.get("authors"), list) else [])
            if _clean_paper_context_text(item)
        ],
        "categories": [
            _clean_paper_context_text(item)
            for item in (candidate.get("categories") if isinstance(candidate.get("categories"), list) else [])
            if _clean_paper_context_text(item)
        ],
        "arxiv_id": _clean_paper_context_text(candidate.get("arxiv_id")) or None,
        "openalex_id": _clean_paper_context_text(candidate.get("openalex_id")) or None,
        "source_url": _clean_paper_context_text(candidate.get("source_url")) or None,
        "pdf_url": _clean_paper_context_text(candidate.get("pdf_url")) or None,
        "source": _clean_paper_context_text(candidate.get("source")) or "external_candidate",
    }


def _project_workspace_path(project) -> str | None:
    if project is None:
        return None
    return project.remote_workdir if project.workspace_server_id else project.workdir


def _target_workspace_path(target) -> str | None:
    if target is None:
        return None
    return target.remote_workdir if target.workspace_server_id else target.workdir


def _run_workspace_path(run) -> str | None:
    if run is None:
        return None
    return run.remote_workdir if run.workspace_server_id else run.workdir


def _build_remote_execution_metadata(
    workflow_type: ProjectWorkflowType,
    *,
    run_id: str,
    workspace_server_id: str | None,
    run_directory: str | None,
) -> dict[str, str]:
    if not workspace_server_id or workflow_type != ProjectWorkflowType.run_experiment:
        return {}
    execution_workspace = build_run_workspace_path(run_directory, remote=True)
    payload = {
        "remote_session_name": build_remote_session_name(run_id),
        "remote_isolation_mode": "pending",
        "gpu_mode": "auto",
        "gpu_strategy": "least_used_free",
        "gpu_memory_threshold_mb": 500,
    }
    if execution_workspace:
        payload["remote_execution_workspace"] = execution_workspace
    return payload


def _list_engine_profiles(session=None) -> list[dict]:
    return [
        item
        for item in (
            public_engine_profile_payload(profile)
            for profile in list_llm_engine_profiles(session=session)
        )
        if item is not None
    ]


def _engine_binding_payload(metadata: dict[str, object], role: str) -> dict[str, object]:
    bindings = metadata.get("engine_bindings")
    if isinstance(bindings, dict):
        payload = bindings.get(role)
        if isinstance(payload, dict):
            return dict(payload)
    engine_id = str(metadata.get(f"{role}_engine_id") or "").strip()
    if engine_id:
        return {"id": engine_id}
    return {}


def _resolve_engine_profile_or_400(session, value: str | None, *, field_label: str) -> dict | None:
    engine_id = _clean_text(value)
    if not engine_id:
        return None
    payload = resolve_llm_engine_profile(engine_id, session=session)
    if payload is None:
        raise HTTPException(status_code=400, detail=f"{field_label} 无效或对应的 LLM 配置不存在")
    return public_engine_profile_payload(payload)


def _apply_engine_binding_metadata(
    metadata: dict[str, object],
    *,
    executor_profile: dict | None,
    reviewer_profile: dict | None,
) -> None:
    bindings = dict(metadata.get("engine_bindings") or {})
    if executor_profile is not None:
        metadata["executor_engine_id"] = executor_profile["id"]
        bindings["executor"] = executor_profile
    else:
        metadata.pop("executor_engine_id", None)
        bindings.pop("executor", None)
    if reviewer_profile is not None:
        metadata["reviewer_engine_id"] = reviewer_profile["id"]
        bindings["reviewer"] = reviewer_profile
    else:
        metadata.pop("reviewer_engine_id", None)
        bindings.pop("reviewer", None)
    if bindings:
        metadata["engine_bindings"] = bindings
    else:
        metadata.pop("engine_bindings", None)


def _active_workflow_presets() -> list[dict]:
    return list_public_project_workflow_presets()


def _public_legacy_workflow_presets() -> list[dict]:
    # Legacy presets are kept in backend for historical run compatibility only.
    # They should not be surfaced to the current product UI as "planned" abilities.
    return []


def _tail_local_file(path: str | None, *, limit: int = 60) -> list[str]:
    raw = _clean_text(path)
    if not raw:
        return []
    target = Path(raw)
    if not target.exists() or not target.is_file():
        return []
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, limit):]


def _local_workspace_health(path: str | None) -> dict:
    workspace_path = _clean_text(path)
    if not workspace_path:
        return {
            "status": "error",
            "workspace_path": None,
            "exists": False,
            "message": "未配置工作区路径",
        }
    target = Path(workspace_path)
    if not target.exists() or not target.is_dir():
        return {
            "status": "error",
            "workspace_path": workspace_path,
            "exists": False,
            "message": f"工作区不存在: {workspace_path}",
        }
    runtime = {}
    for command_key, command in (
        ("python", "python --version"),
        ("git", "git --version"),
        ("uv", "uv --version"),
    ):
        result = run_workspace_command(workspace_path, command, timeout_sec=20)
        runtime[command_key] = {
            "available": bool(result.get("success")),
            "detail": str(result.get("stdout") or result.get("stderr") or "").strip()[:240],
        }
    overview = inspect_workspace(workspace_path, max_depth=2, max_entries=80)
    disk = shutil.disk_usage(target)
    return {
        "status": "ready",
        "workspace_path": workspace_path,
        "exists": True,
        "git": None,
        "tree": overview.get("tree"),
        "runtime": runtime,
        "disk_free_gb": round(disk.free / (1024 ** 3), 2),
        "message": None,
    }


def _remote_workspace_health(server_id: str, path: str | None) -> dict:
    workspace_path = _clean_text(path)
    if not workspace_path:
        return {
            "status": "error",
            "workspace_path": None,
            "exists": False,
            "message": "未配置远程工作区路径",
        }
    server_entry = get_workspace_server_entry(server_id)
    overview = build_remote_overview(server_entry, workspace_path, depth=2, max_entries=80)
    runtime = {}
    for command_key, command in (
        ("python", "python --version"),
        ("git", "git --version"),
        ("uv", "uv --version"),
    ):
        try:
            result = remote_terminal_result(server_entry, path=workspace_path, command=command, timeout_sec=20)
            runtime[command_key] = {
                "available": bool(result.get("success")),
                "detail": str(result.get("stdout") or result.get("stderr") or "").strip()[:240],
            }
        except Exception as exc:
            runtime[command_key] = {
                "available": False,
                "detail": str(exc)[:240],
            }
    return {
        "status": "ready" if overview.get("exists") else "error",
        "workspace_path": overview.get("workspace_path") or workspace_path,
        "exists": bool(overview.get("exists")),
        "git": overview.get("git"),
        "tree": overview.get("tree"),
        "runtime": runtime,
        "message": None if overview.get("exists") else "远程工作区不存在",
    }


def _workspace_health(server_id: str | None, path: str | None) -> dict:
    if server_id:
        try:
            return _remote_workspace_health(server_id, path)
        except Exception as exc:
            return {
                "status": "error",
                "workspace_path": _clean_text(path),
                "exists": False,
                "message": str(exc),
            }
    try:
        return _local_workspace_health(path)
    except Exception as exc:
        return {
            "status": "error",
            "workspace_path": _clean_text(path),
            "exists": False,
            "message": str(exc),
        }


def _collect_local_artifacts(path: str | None, *, limit: int = 40) -> list[dict]:
    raw = _clean_text(path)
    if not raw:
        return []
    root = Path(raw)
    if not root.exists() or not root.is_dir():
        return []
    items: list[dict] = []
    for target in sorted(root.rglob("*")):
        if len(items) >= limit:
            break
        if not target.is_file():
            continue
        items.append(
            {
                "path": str(target),
                "relative_path": target.relative_to(root).as_posix(),
                "size_bytes": target.stat().st_size,
                "kind": "artifact",
                "updated_at": iso_dt(datetime.fromtimestamp(target.stat().st_mtime, tz=UTC)),
            }
        )
    return items


def _merge_artifact_refs(scanned: list[dict], persisted: list[dict], *, limit: int = 40) -> list[dict]:
    merged: list[dict] = []
    index_by_path: dict[str, int] = {}
    for item in [*(scanned or []), *(persisted or [])]:
        if not isinstance(item, dict):
            continue
        path = _clean_text(item.get("path"))
        if not path:
            continue
        existing_index = index_by_path.get(path)
        if existing_index is not None:
            existing = merged[existing_index]
            existing_kind = str(existing.get("kind") or "").strip().lower()
            incoming_kind = str(item.get("kind") or "").strip().lower()
            if existing_kind in {"", "artifact"} and incoming_kind not in {"", "artifact"}:
                existing["kind"] = item.get("kind")
            if not existing.get("size_bytes") and item.get("size_bytes") is not None:
                existing["size_bytes"] = item.get("size_bytes")
            if not existing.get("updated_at") and item.get("updated_at") is not None:
                existing["updated_at"] = item.get("updated_at")
            continue
        index_by_path[path] = len(merged)
        merged.append(dict(item))
        if len(merged) >= limit:
            break
    return merged


def _collect_run_artifacts(run, *, limit: int = 40) -> list[dict]:
    metadata = dict(run.metadata_json or {})
    persisted_refs = [item for item in (metadata.get("artifact_refs") or []) if isinstance(item, dict)]
    run_directory = _clean_text(run.run_directory)
    if not run_directory:
        return persisted_refs[:limit]
    if run.workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(str(run.workspace_server_id))
            overview = build_remote_overview(server_entry, run_directory, depth=3, max_entries=limit)
        except Exception:
            return persisted_refs[:limit]
        scanned_refs = [
            {
                "path": f"{run_directory.rstrip('/')}/{str(item).lstrip('/')}",
                "relative_path": item,
                "kind": "artifact",
            }
            for item in (overview.get("files") or [])[:limit]
        ]
        return _merge_artifact_refs(scanned_refs, persisted_refs, limit=limit)
    scanned_refs = _collect_local_artifacts(run_directory, limit=limit)
    return _merge_artifact_refs(scanned_refs, persisted_refs, limit=limit)


def _recent_run_logs(run, *, limit: int = 40) -> list[str]:
    if run is None:
        return []
    if run.workspace_server_id and _clean_text(run.log_path):
        try:
            server_entry = get_workspace_server_entry(str(run.workspace_server_id))
            result = remote_terminal_result(
                server_entry,
                path=_run_workspace_path(run) or "",
                command=f"tail -n {max(1, limit)} {shlex.quote(str(run.log_path))}",
                timeout_sec=20,
            )
            lines = str(result.get("stdout") or "").splitlines()
            return lines[-max(1, limit):]
        except Exception:
            return []
    return _tail_local_file(run.log_path, limit=limit)


def _serialize_target(target, *, workspace_health: dict | None = None) -> dict:
    return {
        "id": target.id,
        "project_id": target.project_id,
        "label": target.label,
        "workspace_server_id": target.workspace_server_id,
        "workdir": target.workdir,
        "remote_workdir": target.remote_workdir,
        "workspace_path": _target_workspace_path(target),
        "dataset_root": target.dataset_root,
        "checkpoint_root": target.checkpoint_root,
        "output_root": target.output_root,
        "enabled": bool(target.enabled),
        "is_primary": bool(target.is_primary),
        "workspace_health": workspace_health,
        "created_at": iso_dt(target.created_at),
        "updated_at": iso_dt(target.updated_at),
    }


def _serialize_run_action(action) -> dict:
    metadata = sanitize_project_run_metadata(action.metadata_json or {})
    action_label = str(metadata.get("resolved_label") or "").strip() or _action_label(str(action.action_type))
    return _serialize_run_action_payload(action, action_label=action_label)


def _serialize_run_summary(run, target=None) -> dict:
    workspace_path = _run_workspace_path(run)
    metadata = sanitize_project_run_metadata(dict(run.metadata_json or {}))
    normalized_report = build_workflow_report_markdown(
        workflow_type=str(run.workflow_type),
        project_label=str(run.title or _workflow_label(str(run.workflow_type))),
        prompt=run.prompt,
        metadata=metadata,
    )
    if normalized_report:
        metadata["workflow_output_markdown"] = normalized_report
        metadata["workflow_output_excerpt"] = markdown_excerpt(normalized_report)
    executor_engine = _engine_binding_payload(metadata, "executor")
    reviewer_engine = _engine_binding_payload(metadata, "reviewer")
    run_checkpoint_state = checkpoint_state(metadata)
    run_pending_checkpoint = pending_checkpoint(metadata)
    notification_recipients = normalize_notification_recipients(metadata.get("notification_recipients"))
    if run.workspace_server_id and run.workflow_type == ProjectWorkflowType.run_experiment:
        metadata.setdefault("remote_session_name", build_remote_session_name(run.id))
        if run.run_directory:
            metadata.setdefault(
                "remote_execution_workspace",
                build_run_workspace_path(run.run_directory, remote=True),
            )
        metadata.setdefault("remote_isolation_mode", "pending")
        metadata.setdefault("gpu_mode", "auto")
        metadata.setdefault("gpu_strategy", "least_used_free")
        metadata.setdefault("gpu_memory_threshold_mb", 500)
    orchestration = build_run_orchestration(
        run.workflow_type,
        metadata.get("orchestration"),
        target_id=run.target_id,
        workspace_server_id=run.workspace_server_id,
    )
    stage_trace = build_stage_trace(orchestration, existing=metadata.get("stage_trace"))
    metadata["orchestration"] = orchestration
    metadata["stage_trace"] = stage_trace
    artifact_refs = _collect_run_artifacts(run)
    if artifact_refs:
        metadata["artifact_refs"] = artifact_refs
    result_path = str(run.result_path or "").strip() or _infer_primary_result_path(artifact_refs or metadata.get("artifact_refs"))
    paper_index = metadata.get("paper_index") if isinstance(metadata.get("paper_index"), list) else []
    literature_candidates = metadata.get("literature_candidates") if isinstance(metadata.get("literature_candidates"), list) else []
    return {
        "id": run.id,
        "project_id": run.project_id,
        "target_id": run.target_id,
        "target_label": target.label if target is not None else ("远程工作区" if run.workspace_server_id else "本地工作区"),
        "workflow_type": str(run.workflow_type),
        "workflow_label": _workflow_label(str(run.workflow_type)),
        "title": run.title,
        "prompt": run.prompt,
        "status": str(run.status),
        "active_phase": run.active_phase,
        "summary": run.summary,
        "task_id": run.task_id,
        "workspace_server_id": run.workspace_server_id,
        "workdir": run.workdir,
        "remote_workdir": run.remote_workdir,
        "workspace_path": workspace_path,
        "dataset_root": run.dataset_root,
        "checkpoint_root": run.checkpoint_root,
        "output_root": run.output_root,
        "log_path": run.log_path,
        "result_path": result_path,
        "run_directory": run.run_directory,
        "retry_of_run_id": run.retry_of_run_id,
        "max_iterations": run.max_iterations,
        "executor_engine_id": executor_engine.get("id"),
        "executor_engine_label": executor_engine.get("label"),
        "reviewer_engine_id": reviewer_engine.get("id"),
        "reviewer_engine_label": reviewer_engine.get("label"),
        "executor_model": getattr(run, "executor_model", None),
        "reviewer_model": run.reviewer_model,
        "auto_proceed": auto_proceed_enabled(metadata),
        "human_checkpoint_enabled": bool(metadata.get("human_checkpoint_enabled")),
        "checkpoint_state": run_checkpoint_state,
        "pending_checkpoint": run_pending_checkpoint,
        "notification_recipients": notification_recipients,
        "paper_ids": normalize_paper_ids(metadata.get("paper_ids") if isinstance(metadata.get("paper_ids"), list) else []),
        "paper_index": paper_index,
        "literature_candidates": literature_candidates,
        "metadata": metadata,
        "orchestration": orchestration,
        "stage_trace": stage_trace,
        "artifact_refs": artifact_refs,
        "next_actions": list_followup_actions(run.workflow_type),
        "started_at": iso_dt(run.started_at),
        "finished_at": iso_dt(run.finished_at),
        "created_at": iso_dt(run.created_at),
        "updated_at": iso_dt(run.updated_at),
    }


def _infer_primary_result_path(value) -> str | None:
    artifact_refs = value if isinstance(value, list) else []
    preferred_kinds = ("report", "paper", "pdf", "artifact", "log")
    for preferred_kind in preferred_kinds:
        for item in artifact_refs:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            path = str(item.get("path") or "").strip()
            if kind == preferred_kind and path:
                return path
    for item in artifact_refs:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path:
            return path
    return None


def _serialize_run_detail(run, target, actions: list) -> dict:
    payload = _serialize_run_summary(run, target=target)
    payload["actions"] = [_serialize_run_action(action) for action in actions]
    payload["recent_logs"] = _recent_run_logs(run)
    return payload


def _run_is_active(status: str | ProjectRunStatus | None) -> bool:
    normalized = str(status or "").strip().lower()
    return normalized in {
        ProjectRunStatus.queued.value,
        ProjectRunStatus.running.value,
        ProjectRunStatus.paused.value,
    }


def _safe_metadata_values(value) -> list[dict]:
    return value if isinstance(value, list) else []


def _collect_generated_content_ids(value, results: set[str] | None = None) -> set[str]:
    collected = results if results is not None else set()
    if isinstance(value, dict):
        generated_content_id = _clean_text(value.get("generated_content_id"))
        if generated_content_id:
            collected.add(generated_content_id)
        for item in value.values():
            _collect_generated_content_ids(item, collected)
    elif isinstance(value, list):
        for item in value:
            _collect_generated_content_ids(item, collected)
    return collected


def _extract_artifact_paths(*values) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _safe_metadata_values(value):
            if not isinstance(item, dict):
                continue
            path = _clean_text(item.get("path"))
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def _is_relative_to_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_remote_subpath(candidate: str, workspace_root: str) -> bool:
    normalized_candidate = posixpath.normpath(str(candidate or "").replace("\\", "/"))
    normalized_root = posixpath.normpath(str(workspace_root or "").replace("\\", "/"))
    if not normalized_candidate or not normalized_root:
        return False
    return normalized_candidate == normalized_root or normalized_candidate.startswith(f"{normalized_root.rstrip('/')}/")


def _collect_run_deletion_candidates(run, actions: list) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in [
        run.run_directory,
        run.log_path,
        run.result_path,
        *_extract_artifact_paths((run.metadata_json or {}).get("artifact_refs")),
    ]:
        text = _clean_text(raw)
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)
    for action in actions:
        for raw in [
            action.log_path,
            action.result_path,
            *_extract_artifact_paths((action.metadata_json or {}).get("artifact_refs")),
        ]:
            text = _clean_text(raw)
            if text and text not in seen:
                seen.add(text)
                candidates.append(text)
    candidates.sort(key=len, reverse=True)
    return candidates


def _delete_local_run_paths(run, paths: list[str]) -> tuple[list[str], list[str]]:
    workspace_root = _clean_text(_run_workspace_path(run))
    if not workspace_root:
        raise HTTPException(status_code=400, detail="当前运行缺少工作区路径，无法删除运行文件")
    resolved_workspace = Path(workspace_root).expanduser().resolve()
    deleted: list[str] = []
    skipped: list[str] = []
    for raw_path in paths:
        target = Path(raw_path).expanduser().resolve()
        if not _is_relative_to_path(target, resolved_workspace):
            skipped.append(raw_path)
            continue
        if not target.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=False)
        else:
            target.unlink(missing_ok=True)
        deleted.append(str(target))
    return deleted, skipped


def _delete_remote_run_paths(run, paths: list[str]) -> tuple[list[str], list[str]]:
    workspace_root = _clean_text(_run_workspace_path(run))
    if not workspace_root:
        raise HTTPException(status_code=400, detail="当前运行缺少远程工作区路径，无法删除运行文件")
    safe_paths = [path for path in paths if _is_remote_subpath(path, workspace_root)]
    skipped = [path for path in paths if path not in safe_paths]
    if not safe_paths:
        return [], skipped
    server_entry = get_workspace_server_entry(str(run.workspace_server_id))
    command = "rm -rf -- " + " ".join(shlex.quote(path) for path in safe_paths)
    result = remote_terminal_result(
        server_entry,
        path=workspace_root,
        command=command,
        timeout_sec=40,
    )
    if not result.get("success"):
        detail = str(result.get("stderr") or result.get("stdout") or "远程删除运行目录失败").strip()
        raise HTTPException(status_code=400, detail=detail[:300])
    return safe_paths, skipped


def _delete_run_artifacts(run, actions: list) -> tuple[list[str], list[str]]:
    paths = _collect_run_deletion_candidates(run, actions)
    if not paths:
        return [], []
    if run.workspace_server_id:
        return _delete_remote_run_paths(run, paths)
    return _delete_local_run_paths(run, paths)


def _serialize_project_summary(project, project_repo: ProjectRepository) -> dict:
    repos = project_repo.list_repos(project.id)
    ideas = project_repo.list_ideas(project.id)
    papers = project_repo.list_project_papers(project.id)
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "workdir": project.workdir,
        "workspace_server_id": project.workspace_server_id,
        "remote_workdir": project.remote_workdir,
        "workspace_path": _project_workspace_path(project),
        "created_at": iso_dt(project.created_at),
        "updated_at": iso_dt(project.updated_at),
        "last_accessed_at": iso_dt(project.last_accessed_at),
        "paper_count": len(papers),
        "repo_count": len(repos),
        "idea_count": len(ideas),
        "run_count": project_repo.count_runs(project.id),
        "has_remote_workspace": bool(project.workspace_server_id and project.remote_workdir),
    }


def _serialize_project_detail(project, project_repo: ProjectRepository, paper_repo: PaperRepository) -> dict:
    detail = _serialize_project_summary(project, project_repo)
    project_paper_rows = project_repo.list_project_papers(project.id)
    papers = [paper for _link, paper in project_paper_rows]
    paper_items_by_id = {
        item["id"]: item for item in paper_list_response(papers, paper_repo)["items"]
    }
    detail["papers"] = [
        {
            **paper_items_by_id.get(paper.id, {"id": paper.id, "title": paper.title, "arxiv_id": paper.arxiv_id}),
            "project_paper_id": link.id,
            "note": link.note,
            "added_at": iso_dt(link.added_at),
        }
        for link, paper in project_paper_rows
    ]
    detail["repos"] = [_serialize_repo(repo) for repo in project_repo.list_repos(project.id)]
    detail["ideas"] = [_serialize_idea(idea) for idea in project_repo.list_ideas(project.id)]
    detail["reports"] = [
        _serialize_report(content, paper)
        for content, paper in project_repo.list_project_reports(project.id)
    ]
    return detail


def _serialize_project_workspace_context(project, project_repo: ProjectRepository) -> dict:
    project_repo.ensure_default_target(project.id)
    targets = project_repo.list_targets(project.id)
    target_map = {target.id: target for target in targets}
    paper_repo = PaperRepository(project_repo.session)
    project_detail = _serialize_project_detail(project, project_repo, paper_repo)
    engine_profiles = _list_engine_profiles(project_repo.session)
    default_engine_bindings = recommend_llm_engine_profiles(engine_profiles)
    target_items = []
    for target in targets:
        target_items.append(
            _serialize_target(
                target,
                workspace_health=_workspace_health(target.workspace_server_id, _target_workspace_path(target)),
            )
        )
    runs = project_repo.list_runs(project.id, limit=80)
    primary_target = next((target for target in targets if target.is_primary), targets[0] if targets else None)
    active_presets = _active_workflow_presets()
    default_workflow = active_presets[0]["workflow_type"] if active_presets else ProjectWorkflowType.literature_review.value
    latest_run = runs[0] if runs else None
    workspace_path = _target_workspace_path(primary_target) if primary_target is not None else _project_workspace_path(project)

    return {
        "project": {
            **project_detail,
            "target_count": len(targets),
            "run_count": project_repo.count_runs(project.id),
            "primary_target_id": primary_target.id if primary_target is not None else None,
            "latest_run_id": latest_run.id if latest_run else None,
        },
        "targets": target_items,
        "runs": [_serialize_run_summary(run, target_map.get(run.target_id)) for run in runs],
        "workflow_presets": active_presets,
        "planned_workflow_presets": _public_legacy_workflow_presets(),
        "action_items": RUN_ACTION_PRESETS,
        "agent_templates": PROJECT_AGENT_TEMPLATES,
        "role_templates": PROJECT_AGENT_TEMPLATES,
        "engine_profiles": engine_profiles,
        "workspace_health": _workspace_health(primary_target.workspace_server_id if primary_target else project.workspace_server_id, workspace_path),
        "recent_logs": _recent_run_logs(latest_run),
        "artifacts": _collect_run_artifacts(latest_run) if latest_run is not None else [],
        "default_selections": {
            "target_id": primary_target.id if primary_target is not None else None,
            "run_id": latest_run.id if latest_run else None,
            "workflow_type": default_workflow,
            **default_engine_bindings,
        },
    }


def _filter_project_tasks(project_id: str, *, run_ids: set[str], limit: int = 20) -> list[dict]:
    tasks = global_tracker.list_tasks(limit=max(limit * 8, 200))
    matched = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        if str(item.get("project_id") or "").strip() == project_id:
            matched.append(item)
            continue
        run_id = str(item.get("run_id") or "").strip()
        if run_id and run_id in run_ids:
            matched.append(item)
    matched.sort(key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0), reverse=True)
    return matched[: max(1, limit)]


def _message_preview(message: dict) -> dict | None:
    if not isinstance(message, dict):
        return None
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    parts = message.get("parts") if isinstance(message.get("parts"), list) else []
    text = "".join(
        str(part.get("text") or part.get("content") or "")
        for part in parts
        if isinstance(part, dict) and str(part.get("type") or "") == "text"
    ).strip()
    if not text:
        return None
    return {
        "id": info.get("id"),
        "role": info.get("role"),
        "created_at": ((info.get("time") or {}) if isinstance(info.get("time"), dict) else {}).get("created"),
        "text": _trim_preview_text(text),
    }


def _list_project_sessions(
    *,
    workspace_path: str | None,
    workspace_server_id: str | None,
    limit: int = 12,
) -> list[dict]:
    normalized_path = _clean_text(workspace_path)
    normalized_server_id = _normalize_server_id(workspace_server_id)
    if not normalized_path:
        return []
    sessions = list_sessions(directory=normalized_path, limit=max(limit * 4, 40), archived=False)
    matched: list[dict] = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        item_path = _clean_text(str(item.get("workspace_path") or item.get("directory") or ""))
        item_server_id = _normalize_server_id(item.get("workspace_server_id"))
        if item_path != normalized_path:
            continue
        if item_server_id != normalized_server_id:
            continue
        preview = None
        recent_messages = list_session_messages(str(item.get("id") or ""), limit=6)
        for candidate in reversed(recent_messages):
            preview = _message_preview(candidate)
            if preview is not None:
                break
        matched.append(
            {
                **item,
                "latest_message": preview,
            }
        )
        if len(matched) >= limit:
            break
    return matched


def _load_project_or_404(project_repo: ProjectRepository, project_id: str):
    project = project_repo.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _load_target_or_404(project_repo: ProjectRepository, project_id: str, target_id: str):
    target = project_repo.get_target(target_id)
    if target is None or target.project_id != project_id:
        raise HTTPException(status_code=404, detail="项目部署目标不存在")
    return target


def _load_run_or_404(project_repo: ProjectRepository, run_id: str):
    run = project_repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="项目运行不存在")
    return run


def _ensure_repo_in_project(project_id: str, repo_id: str, project_repo: ProjectRepository):
    repo = project_repo.get_repo(repo_id)
    if repo is None or repo.project_id != project_id:
        raise HTTPException(status_code=404, detail="项目仓库不存在")
    return repo


def _require_local_git_path(path: str | None) -> Path:
    raw = _clean_text(path)
    if not raw:
        raise HTTPException(status_code=400, detail="仓库本地路径为空")
    target = Path(raw).expanduser().resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"仓库路径不存在: {target}")
    return target


def _git_commits(path: Path, limit: int) -> list[dict]:
    if not shutil.which("git"):
        raise HTTPException(status_code=400, detail="系统中未安装 Git")
    result = subprocess.run(
        [
            "git",
            "log",
            f"--pretty=format:%H|%h|%s|%an|%ai",
            "-n",
            str(limit),
        ],
        cwd=str(path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip() or "无法读取 Git 提交记录"
        raise HTTPException(status_code=400, detail=message)
    items: list[dict] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        commit_hash, short_hash, message, author, created_at = parts
        items.append(
            {
                "hash": commit_hash,
                "short_hash": short_hash,
                "message": message,
                "author": author,
                "date": created_at,
            }
        )
    return items


def _generate_idea_title(project_name: str, focus: str | None = None) -> str:
    prefix = "AI 灵感"
    if focus:
        focus_text = focus.strip()
        return f"{prefix}: {focus_text[:48]}"
    return f"{prefix}: {project_name[:48]}"


def _resolve_project_idea_inputs(
    project_id: str,
    body: ProjectIdeaGenerateRequest,
    project_repo: ProjectRepository,
    paper_repo: PaperRepository,
):
    project = _load_project_or_404(project_repo, project_id)

    selected_ids = [paper_id for paper_id in body.paper_ids if _clean_text(paper_id)]
    if not selected_ids:
        selected_ids = [paper.id for _link, paper in project_repo.list_project_papers(project_id)[:6]]

    selected_papers = paper_repo.list_by_ids(selected_ids) if selected_ids else []
    selected_repos = [
        repo
        for repo in project_repo.list_repos(project_id)
        if not body.repo_ids or repo.id in body.repo_ids
    ]
    if not selected_papers and not selected_repos:
        raise ValueError("请至少选择论文或仓库作为灵感输入")
    return project, selected_papers, selected_repos


def _build_project_idea_prompt(project, body: ProjectIdeaGenerateRequest, selected_papers: list, selected_repos: list) -> str:
    paper_context = "\n".join(
        [
            (
                f"- paper_id: {paper.id} | 标题: {paper.title} | "
                f"arXiv: {paper.arxiv_id or 'N/A'} | 摘要可用: {'是' if (paper.abstract or '').strip() else '否'}"
            )
            for paper in selected_papers
        ]
    )
    repo_context = "\n".join(
        [
            f"- 仓库: {repo.repo_url} | 本地路径: {repo.local_path or '未克隆'}"
            for repo in selected_repos
        ]
    )
    return (
        "你是研究项目策划助手。请基于下面的项目信息、论文与代码上下文，"
        "为该项目产出一个有执行价值的新想法。\n"
        "论文上下文只包含元信息和 ID，不包含摘要、正文或分析全文；不要编造论文结论。"
        "如果想法依赖具体证据，请在 content 中写清需要按 paper_id 继续读取的分析或 PDF。\n\n"
        f"项目名称: {project.name}\n"
        f"项目描述: {project.description or '暂无描述'}\n"
        f"关注点: {(_clean_text(body.focus) or '请优先给出可快速验证的方向')}\n\n"
        f"论文上下文:\n{paper_context or '无'}\n\n"
        f"代码上下文:\n{repo_context or '无'}\n\n"
        "请只输出一个 JSON 对象，格式为："
        '{"title":"一句话标题","content":"使用 Markdown，包含问题、机会、最小实验、风险与下一步"}'
    )


def _generate_project_idea_payload(
    project_id: str,
    body: ProjectIdeaGenerateRequest,
    progress_callback=None,
) -> dict:
    def report(message: str, current: int, total: int = 100):
        if progress_callback:
            progress_callback(message, current, total)

    report("正在加载项目上下文...", 8)
    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        paper_repo = repos.papers
        project, selected_papers, selected_repos = _resolve_project_idea_inputs(
            project_id,
            body,
            project_repo,
            paper_repo,
        )

        report("正在整理论文与仓库信息...", 24)
        prompt = _build_project_idea_prompt(project, body, selected_papers, selected_repos)

        report("正在调用模型生成研究想法...", 42)
        result = LLMClient().complete_json(
            prompt,
            stage="project_idea_generate",
            max_tokens=1800,
            max_retries=1,
        )
        parsed = result.parsed_json or {}
        title = str(parsed.get("title") or "").strip() or _generate_idea_title(project.name, body.focus)
        content = str(parsed.get("content") or result.content or "").strip()
        if not content:
            content = "暂未成功生成结构化内容，请稍后重试。"

        report("正在保存研究想法...", 92)
        idea = project_repo.create_idea(
            project_id=project_id,
            title=title[:512],
            content=content,
            paper_ids=[paper.id for paper in selected_papers],
        )
        payload = {"item": _serialize_idea(idea)}

    report("研究想法已生成", 100)
    return payload


def _run_generate_project_idea_task(
    project_id: str,
    body_payload: dict[str, object],
    progress_callback=None,
) -> dict:
    body = ProjectIdeaGenerateRequest.model_validate(body_payload)
    return _generate_project_idea_payload(
        project_id,
        body,
        progress_callback=progress_callback,
    )


def _resolve_workflow_type(value: str) -> ProjectWorkflowType:
    raw = _clean_text(value)
    if not raw:
        raise HTTPException(status_code=400, detail="workflow_type 不能为空")
    try:
        return ProjectWorkflowType(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ProjectWorkflowType)
        raise HTTPException(status_code=400, detail=f"不支持的 workflow_type，允许值: {allowed}") from exc


def _resolve_action_type(value: str) -> ProjectRunActionType:
    raw = _clean_text(value)
    if not raw:
        raise HTTPException(status_code=400, detail="action_type 不能为空")
    try:
        return ProjectRunActionType(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ProjectRunActionType)
        raise HTTPException(status_code=400, detail=f"不支持的 action_type，允许值: {allowed}") from exc


def _build_run_title(project_name: str, workflow_type: ProjectWorkflowType, title: str | None = None) -> str:
    explicit_title = _clean_text(title)
    if explicit_title:
        return explicit_title[:512]
    return f"{_workflow_label(workflow_type.value)} · {project_name[:72]}"[:512]


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    workdir: str | None = None
    workspace_server_id: str | None = None
    remote_workdir: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    workdir: str | None = None
    workspace_server_id: str | None = None
    remote_workdir: str | None = None


class ProjectPaperRequest(BaseModel):
    paper_id: str
    note: str | None = None


class ProjectRepoRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=1024)
    local_path: str | None = None
    cloned_at: str | None = None
    is_workdir_repo: bool = False


class ProjectRepoUpdateRequest(BaseModel):
    repo_url: str | None = None
    local_path: str | None = None
    cloned_at: str | None = None
    is_workdir_repo: bool | None = None


class ProjectIdeaRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    paper_ids: list[str] = Field(default_factory=list)


class ProjectIdeaGenerateRequest(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    repo_ids: list[str] = Field(default_factory=list)
    focus: str | None = None


class ProjectIdeaUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    content: str | None = None
    paper_ids: list[str] | None = None


class ProjectTargetCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=256)
    workspace_server_id: str | None = None
    workdir: str | None = None
    remote_workdir: str | None = None
    dataset_root: str | None = None
    checkpoint_root: str | None = None
    output_root: str | None = None
    enabled: bool = True
    is_primary: bool = False


class ProjectTargetUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=256)
    workspace_server_id: str | None = None
    workdir: str | None = None
    remote_workdir: str | None = None
    dataset_root: str | None = None
    checkpoint_root: str | None = None
    output_root: str | None = None
    enabled: bool | None = None
    is_primary: bool | None = None


class ProjectRunCreateRequest(BaseModel):
    target_id: str | None = None
    workflow_type: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=512)
    prompt: str = Field(min_length=1)
    paper_ids: list[str] = Field(default_factory=list)
    execution_command: str | None = Field(default=None, max_length=2048)
    max_iterations: int | None = Field(default=None, ge=1, le=100)
    executor_engine_id: str | None = Field(default=None, max_length=128)
    reviewer_engine_id: str | None = Field(default=None, max_length=128)
    executor_model: str | None = Field(default=None, max_length=128)
    reviewer_model: str | None = Field(default=None, max_length=128)
    auto_proceed: bool | None = None
    human_checkpoint_enabled: bool = False
    notification_recipients: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class ProjectRunActionRequest(BaseModel):
    action_type: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1)
    workflow_type: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=512)
    metadata: dict[str, object] = Field(default_factory=dict)


class ProjectRunCheckpointResponseRequest(BaseModel):
    action: str = Field(min_length=1, max_length=16)
    comment: str | None = Field(default=None, max_length=2000)


class ProjectRunLiteratureCandidateImportRequest(BaseModel):
    candidate_ref_ids: list[str] = Field(default_factory=list)
    link_to_project: bool = True


@router.get("/projects/workflow-presets")
def list_workflow_presets() -> dict:
    engine_profiles = _list_engine_profiles()
    return {
        "items": _active_workflow_presets(),
        "planned_items": _public_legacy_workflow_presets(),
        "action_items": RUN_ACTION_PRESETS,
        "agent_templates": PROJECT_AGENT_TEMPLATES,
        "role_templates": PROJECT_AGENT_TEMPLATES,
        "engine_profiles": engine_profiles,
        "default_engine_bindings": recommend_llm_engine_profiles(engine_profiles),
    }


@router.get("/projects/companion/overview")
def get_projects_companion_overview(
    project_limit: int = Query(default=20, ge=1, le=100),
    task_limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        projects = project_repo.list_projects()[: max(1, project_limit)]
        items: list[dict] = []
        tasks = global_tracker.list_tasks(limit=max(task_limit, 1))
        for project in projects:
            runs = project_repo.list_runs(project.id, limit=1)
            latest_run = runs[0] if runs else None
            active_task_count = sum(
                1
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("project_id") or "").strip() == project.id
                and not bool(task.get("finished"))
            )
            items.append(
                {
                    **_serialize_project_summary(project, project_repo),
                    "latest_run": _serialize_run_summary(latest_run) if latest_run is not None else None,
                    "active_task_count": active_task_count,
                }
            )
    try:
        acp_summary = get_acp_registry_service().get_backend_summary()
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        acp_summary = {
            "chat_ready": False,
            "chat_status_label": f"ACP 摘要获取失败: {exc}",
        }
    return {
        "items": items,
        "tasks": tasks[: max(1, task_limit)],
        "acp": acp_summary,
    }


@router.get("/projects")
def list_projects() -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        items = [_serialize_project_summary(project, project_repo) for project in project_repo.list_projects()]
    return {"items": items}


@router.post("/projects")
def create_project(body: ProjectCreateRequest) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")

    workspace_server_id = _normalize_server_id(body.workspace_server_id)
    workdir = _clean_text(body.workdir)
    remote_workdir = _clean_text(body.remote_workdir)

    if workspace_server_id is None:
        workdir = _ensure_local_workdir(name, workdir)

    with session_scope() as session:
        project_repo = _project_data(session).projects
        project = project_repo.create_project(
            name=name,
            description=_clean_text(body.description),
            workdir=workdir,
            workspace_server_id=workspace_server_id,
            remote_workdir=remote_workdir,
        )
        project_repo.touch_last_accessed(project.id)
        paper_repo = _project_data(session).papers
        return {"item": _serialize_project_detail(project, project_repo, paper_repo)}


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        paper_repo = repos.papers
        project = _load_project_or_404(project_repo, project_id)
        return {"item": _serialize_project_detail(project, project_repo, paper_repo)}


@router.get("/projects/{project_id}/workspace-context")
def get_project_workspace_context(project_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        project = _load_project_or_404(project_repo, project_id)
        return {"item": _serialize_project_workspace_context(project, project_repo)}


@router.get("/projects/{project_id}/companion-snapshot")
def get_project_companion_snapshot(
    project_id: str,
    task_limit: int = Query(default=20, ge=1, le=100),
    session_limit: int = Query(default=12, ge=1, le=50),
    include_latest_session_messages: bool = Query(default=False),
    latest_session_message_limit: int = Query(default=40, ge=1, le=200),
) -> dict:
    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        paper_repo = repos.papers
        project = _load_project_or_404(project_repo, project_id)
        project_detail = _serialize_project_detail(project, project_repo, paper_repo)
        workspace_context = _serialize_project_workspace_context(project, project_repo)
        run_ids = {
            str(item.get("id") or "").strip()
            for item in (workspace_context.get("runs") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        tasks = _filter_project_tasks(project_id, run_ids=run_ids, limit=task_limit)
        sessions = _list_project_sessions(
            workspace_path=project_detail.get("workspace_path"),
            workspace_server_id=project_detail.get("workspace_server_id"),
            limit=session_limit,
        )
    latest_session_messages: list[dict] = []
    if include_latest_session_messages and sessions:
        latest_session_messages = list_session_messages(
            str(sessions[0].get("id") or ""),
            limit=max(1, latest_session_message_limit),
        )
    try:
        acp_summary = get_acp_registry_service().get_backend_summary()
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        acp_summary = {
            "chat_ready": False,
            "chat_status_label": f"ACP 摘要获取失败: {exc}",
        }
    return {
        "item": {
            "project": project_detail,
            "workspace_context": workspace_context,
            "tasks": tasks,
            "sessions": sessions,
            "latest_session_messages": latest_session_messages,
            "acp": acp_summary,
        }
    }


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateRequest) -> dict:
    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        paper_repo = repos.papers
        project = _load_project_or_404(project_repo, project_id)

        name = body.name.strip() if body.name is not None else project.name
        if not name:
            raise HTTPException(status_code=400, detail="项目名称不能为空")

        workspace_server_id = (
            _normalize_server_id(body.workspace_server_id)
            if body.workspace_server_id is not None
            else project.workspace_server_id
        )

        workdir = _clean_text(body.workdir) if body.workdir is not None else project.workdir
        remote_workdir = (
            _clean_text(body.remote_workdir)
            if body.remote_workdir is not None
            else project.remote_workdir
        )

        if workspace_server_id is None and not workdir:
            workdir = _ensure_local_workdir(name, None)

        updated = project_repo.update_project(
            project_id,
            name=name,
            description=_clean_text(body.description)
            if body.description is not None
            else project.description,
            workdir=workdir,
            workspace_server_id=workspace_server_id,
            remote_workdir=remote_workdir,
        )
        assert updated is not None
        return {"item": _serialize_project_detail(updated, project_repo, paper_repo)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        deleted = project_repo.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="项目不存在")
    return {"deleted": project_id}


@router.post("/projects/{project_id}/touch")
def touch_project(project_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        try:
            project = project_repo.touch_last_accessed(project_id)
        except StaleDataError:
            session.rollback()
            raise HTTPException(status_code=404, detail="项目不存在")
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        last_accessed_at = iso_dt(project.last_accessed_at)
    return {"ok": True, "project_id": project_id, "last_accessed_at": last_accessed_at}


@router.get("/projects/{project_id}/papers")
def list_project_papers(project_id: str) -> dict:
    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        paper_repo = repos.papers
        project = _load_project_or_404(project_repo, project_id)
        detail = _serialize_project_detail(project, project_repo, paper_repo)
        return {"items": detail["papers"]}


@router.post("/projects/{project_id}/papers")
def add_project_paper(project_id: str, body: ProjectPaperRequest) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        paper = session.get(Paper, body.paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail="论文不存在")
        link = project_repo.add_paper_to_project(
            project_id=project_id,
            paper_id=body.paper_id,
            note=_clean_text(body.note),
        )
        paper_repo = _project_data(session).papers
        serialized_paper = paper_list_response([paper], paper_repo)["items"][0]
        return {
            "item": {
                **serialized_paper,
                "project_paper_id": link.id,
                "note": link.note,
                "added_at": iso_dt(link.added_at),
            }
        }


@router.delete("/projects/{project_id}/papers/{paper_id}")
def remove_project_paper(project_id: str, paper_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        deleted = project_repo.remove_paper_from_project(project_id, paper_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="项目论文关联不存在")
    return {"deleted": paper_id}


@router.get("/projects/{project_id}/reports")
def list_project_reports(project_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        items = [
            _serialize_report(content, paper)
            for content, paper in project_repo.list_project_reports(project_id, limit=limit)
        ]
    return {"items": items}


@router.get("/projects/{project_id}/targets")
def list_project_targets(project_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        project_repo.ensure_default_target(project_id)
        items = [_serialize_target(target) for target in project_repo.list_targets(project_id)]
    return {"items": items}


@router.post("/projects/{project_id}/targets")
def create_project_target(project_id: str, body: ProjectTargetCreateRequest) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        project = _load_project_or_404(project_repo, project_id)
        workspace_server_id = _normalize_server_id(body.workspace_server_id)
        workdir = _clean_text(body.workdir)
        remote_workdir = _clean_text(body.remote_workdir)

        if workspace_server_id is None:
            workdir = workdir or project.workdir or _ensure_local_workdir(project.name, None)
            remote_workdir = None
        elif not remote_workdir:
            raise HTTPException(status_code=400, detail="远程部署目标需要 remote_workdir")

        target = project_repo.create_target(
            project_id=project_id,
            label=body.label.strip(),
            workspace_server_id=workspace_server_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            dataset_root=_clean_text(body.dataset_root),
            checkpoint_root=_clean_text(body.checkpoint_root),
            output_root=_clean_text(body.output_root),
            enabled=body.enabled,
            is_primary=body.is_primary,
        )
        return {"item": _serialize_target(target)}


@router.patch("/projects/{project_id}/targets/{target_id}")
def update_project_target(project_id: str, target_id: str, body: ProjectTargetUpdateRequest) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        project = _load_project_or_404(project_repo, project_id)
        target = _load_target_or_404(project_repo, project_id, target_id)

        workspace_server_id = (
            _normalize_server_id(body.workspace_server_id)
            if body.workspace_server_id is not None
            else target.workspace_server_id
        )
        workdir = _clean_text(body.workdir) if body.workdir is not None else target.workdir
        remote_workdir = (
            _clean_text(body.remote_workdir)
            if body.remote_workdir is not None
            else target.remote_workdir
        )

        if workspace_server_id is None:
            workdir = workdir or project.workdir or _ensure_local_workdir(project.name, None)
            remote_workdir = None
        elif not remote_workdir:
            raise HTTPException(status_code=400, detail="远程部署目标需要 remote_workdir")

        updated = project_repo.update_target(
            target_id,
            label=body.label.strip() if body.label is not None else target.label,
            workspace_server_id=workspace_server_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            dataset_root=_clean_text(body.dataset_root)
            if body.dataset_root is not None
            else target.dataset_root,
            checkpoint_root=_clean_text(body.checkpoint_root)
            if body.checkpoint_root is not None
            else target.checkpoint_root,
            output_root=_clean_text(body.output_root)
            if body.output_root is not None
            else target.output_root,
            enabled=body.enabled if body.enabled is not None else target.enabled,
            is_primary=body.is_primary if body.is_primary is not None else target.is_primary,
        )
        assert updated is not None
        return {"item": _serialize_target(updated)}


@router.delete("/projects/{project_id}/targets/{target_id}")
def delete_project_target(project_id: str, target_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        _load_target_or_404(project_repo, project_id, target_id)
        project_repo.delete_target(target_id)
    return {"deleted": target_id}


@router.get("/projects/{project_id}/runs")
def list_project_runs(project_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        targets = {target.id: target for target in project_repo.list_targets(project_id)}
        items = [
            _serialize_run_summary(run, targets.get(run.target_id))
            for run in project_repo.list_runs(project_id, limit=limit)
        ]
    return {"items": items}


@router.post("/projects/{project_id}/runs")
def create_project_run(project_id: str, body: ProjectRunCreateRequest) -> dict:
    workflow_type = _resolve_workflow_type(body.workflow_type)
    if not is_active_project_workflow(workflow_type):
        raise HTTPException(status_code=400, detail=f"当前 workflow 尚未开放真实执行: {workflow_type.value}")
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    run_id = ""
    with session_scope() as session:
        project_repo = _project_data(session).projects
        project = _load_project_or_404(project_repo, project_id)

        target = None
        if _clean_text(body.target_id):
            target = _load_target_or_404(project_repo, project_id, str(body.target_id))
        else:
            target = project_repo.get_primary_target(project_id) or project_repo.ensure_default_target(project_id)

        workspace_server_id = target.workspace_server_id if target is not None else project.workspace_server_id
        workdir = target.workdir if target is not None else project.workdir
        remote_workdir = target.remote_workdir if target is not None else project.remote_workdir
        dataset_root = target.dataset_root if target is not None else None
        checkpoint_root = target.checkpoint_root if target is not None else None
        output_root = target.output_root if target is not None else None
        project_workspace_path = _project_workspace_path(project)
        target_workspace_path = _target_workspace_path(target) if target is not None else project_workspace_path
        sync_strategy = infer_sync_strategy(
            project_workspace=project_workspace_path,
            project_workspace_server_id=project.workspace_server_id,
            target_workspace=target_workspace_path,
            workspace_server_id=workspace_server_id,
            target_workspace_server_id=target.workspace_server_id if target is not None else workspace_server_id,
        )
        metadata = build_checkpoint_settings(
            dict(body.metadata or {}),
            enabled=body.human_checkpoint_enabled,
            auto_proceed=body.auto_proceed,
            notification_recipients=body.notification_recipients,
            reset_state=True,
        )
        selected_paper_ids = normalize_paper_ids(body.paper_ids)
        paper_repo = _project_data(session).papers
        if selected_paper_ids:
            found_papers = paper_repo.list_by_ids(selected_paper_ids)
            found_ids = {str(paper.id) for paper in found_papers}
            missing_ids = [paper_id for paper_id in selected_paper_ids if paper_id not in found_ids]
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"论文不存在: {', '.join(missing_ids[:5])}")
            existing_project_paper_ids = {
                str(paper.id)
                for _link, paper in project_repo.list_project_papers(project_id)
            }
            for paper_id in selected_paper_ids:
                if paper_id not in existing_project_paper_ids:
                    project_repo.add_paper_to_project(
                        project_id=project_id,
                        paper_id=paper_id,
                        note="由工作流启动时自动关联",
                    )
            metadata["paper_ids"] = selected_paper_ids
        else:
            metadata.pop("paper_ids", None)
        metadata["paper_index"] = _build_run_paper_index(
            project_repo,
            paper_repo,
            project_id,
            selected_paper_ids,
        )
        metadata.setdefault("literature_candidates", [])
        if _clean_text(body.execution_command):
            metadata["execution_command"] = _clean_text(body.execution_command)
        executor_engine = _resolve_engine_profile_or_400(
            session,
            body.executor_engine_id,
            field_label="executor_engine_id",
        )
        reviewer_engine = _resolve_engine_profile_or_400(
            session,
            body.reviewer_engine_id,
            field_label="reviewer_engine_id",
        )
        _apply_engine_binding_metadata(
            metadata,
            executor_profile=executor_engine,
            reviewer_profile=reviewer_engine,
        )
        executor_model = _clean_text(body.executor_model)
        reviewer_model = _clean_text(body.reviewer_model)
        if executor_model:
            metadata["executor_model"] = executor_model
        elif body.executor_model is not None:
            metadata.pop("executor_model", None)
        if reviewer_model:
            metadata["reviewer_model"] = reviewer_model
        elif body.reviewer_model is not None:
            metadata.pop("reviewer_model", None)
        orchestration = build_run_orchestration(
            workflow_type,
            metadata.get("orchestration"),
            target_id=target.id if target is not None else None,
            workspace_server_id=workspace_server_id,
        )
        metadata["sync_strategy"] = sync_strategy
        metadata["project_workspace_path"] = project_workspace_path
        metadata["project_workspace_server_id"] = project.workspace_server_id
        metadata["target_workspace_path"] = target_workspace_path
        metadata["target_workspace_server_id"] = target.workspace_server_id if target is not None else workspace_server_id
        metadata["orchestration"] = orchestration
        metadata["stage_trace"] = build_stage_trace(orchestration, reset=True)

        run = project_repo.create_run(
            project_id=project_id,
            target_id=target.id if target is not None else None,
            workflow_type=workflow_type,
            title=_build_run_title(project.name, workflow_type, body.title),
            prompt=prompt,
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary=describe_sync_strategy(sync_strategy),
            workspace_server_id=workspace_server_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            dataset_root=dataset_root,
            checkpoint_root=checkpoint_root,
            output_root=output_root,
            max_iterations=body.max_iterations,
            executor_model=executor_model,
            reviewer_model=reviewer_model,
            metadata=metadata,
        )
        resolved_workspace_path = _run_workspace_path(run)
        run_directory = build_run_directory(
            resolved_workspace_path,
            run.id,
            remote=bool(workspace_server_id),
        )
        log_path = build_run_log_path(run_directory, remote=bool(workspace_server_id))
        metadata["run_directory"] = run_directory
        metadata["log_path"] = log_path
        for key, value in _build_remote_execution_metadata(
            workflow_type,
            run_id=run.id,
            workspace_server_id=workspace_server_id,
            run_directory=run_directory,
        ).items():
            metadata.setdefault(key, value)
        project_repo.update_run(
            run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata=metadata,
        )
        run_id = run.id

    if supports_project_run(workflow_type):
        try:
            submit_project_run(run_id)
        except Exception as exc:
            with session_scope() as session:
                project_repo = _project_data(session).projects
                project_repo.update_run(
                    run_id,
                    status=ProjectRunStatus.failed,
                    active_phase="failed",
                    summary=f"执行器启动失败：{str(exc)[:180]}",
                    finished_at=datetime.now(UTC),
                )

    with session_scope() as session:
        project_repo = _project_data(session).projects
        run = _load_run_or_404(project_repo, run_id)
        target = project_repo.get_target(run.target_id) if run.target_id else None
        actions = project_repo.list_run_actions(run.id)
        return {"item": _serialize_run_detail(run, target, actions)}


@router.get("/project-runs/{run_id}")
def get_project_run(run_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        run = _load_run_or_404(project_repo, run_id)
        target = project_repo.get_target(run.target_id) if run.target_id else None
        actions = project_repo.list_run_actions(run.id)
        return {"item": _serialize_run_detail(run, target, actions)}


@router.get("/project-runs/{run_id}/literature-candidates")
def list_project_run_literature_candidates(run_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        run = _load_run_or_404(project_repo, run_id)
        metadata = sanitize_project_run_metadata(dict(run.metadata_json or {}))
        return {
            "paper_index": metadata.get("paper_index") if isinstance(metadata.get("paper_index"), list) else [],
            "items": metadata.get("literature_candidates") if isinstance(metadata.get("literature_candidates"), list) else [],
        }


@router.post("/project-runs/{run_id}/literature-candidates/import")
def import_project_run_literature_candidates(
    run_id: str,
    body: ProjectRunLiteratureCandidateImportRequest,
) -> dict:
    requested_ref_ids = {
        _clean_text(ref_id)
        for ref_id in body.candidate_ref_ids
        if _clean_text(ref_id)
    }
    if not requested_ref_ids:
        raise HTTPException(status_code=400, detail="candidate_ref_ids 不能为空")

    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        paper_repo = repos.papers
        run = _load_run_or_404(project_repo, run_id)
        project = _load_project_or_404(project_repo, run.project_id)
        metadata = dict(run.metadata_json or {})
        candidates = metadata.get("literature_candidates") if isinstance(metadata.get("literature_candidates"), list) else []
        paper_index = metadata.get("paper_index") if isinstance(metadata.get("paper_index"), list) else []

        updated_candidates: list[dict] = []
        imported_ids: list[str] = []
        linked_ids: list[str] = []
        missing_ref_ids = set(requested_ref_ids)
        existing_project_paper_ids = {
            str(paper.id)
            for _link, paper in project_repo.list_project_papers(run.project_id)
        }

        for raw_candidate in candidates:
            candidate = dict(raw_candidate) if isinstance(raw_candidate, dict) else {}
            ref_id = _clean_text(candidate.get("ref_id"))
            if ref_id not in requested_ref_ids:
                updated_candidates.append(candidate)
                continue
            missing_ref_ids.discard(ref_id)

            paper_id = _clean_text(candidate.get("paper_id"))
            if not paper_id and bool(candidate.get("importable")):
                entry = _candidate_to_external_entry(candidate)
                if not entry["title"]:
                    candidate["status"] = "failed"
                    candidate["error"] = "候选论文缺少标题，无法导入"
                    updated_candidates.append(candidate)
                    continue
                result = pipelines.ingest_external_entries(
                    entries=[entry],
                    action_type=ActionType.manual_collect,
                    query=str(run.prompt or project.name),
                )
                inserted = [
                    _clean_text(item.get("id"))
                    for item in (result.get("papers") or [])
                    if isinstance(item, dict) and _clean_text(item.get("id"))
                ]
                if inserted:
                    paper_id = inserted[0]
                else:
                    resolved_arxiv_id = PaperPipelines._resolve_external_entry_arxiv_id(entry)
                    existing = paper_repo.get_by_arxiv_id(resolved_arxiv_id)
                    paper_id = str(existing.id) if existing is not None else ""
                if paper_id:
                    imported_ids.append(paper_id)
                    candidate["paper_id"] = paper_id
                    candidate["imported_paper_id"] = paper_id
                    candidate["status"] = "imported"
                    candidate["importable"] = False
                    candidate["linkable"] = True
                else:
                    candidate["status"] = "failed"
                    candidate["error"] = "导入后未能定位论文记录"

            if paper_id and body.link_to_project:
                if paper_id not in existing_project_paper_ids:
                    project_repo.add_paper_to_project(
                        project_id=run.project_id,
                        paper_id=paper_id,
                        note=f"由运行 {run.id[:8]} 的候选池关联",
                    )
                    existing_project_paper_ids.add(paper_id)
                linked_ids.append(paper_id)
                candidate["project_linked"] = True
                if candidate.get("status") == "candidate":
                    candidate["status"] = "linked"

            updated_candidates.append(candidate)

        if missing_ref_ids:
            raise HTTPException(status_code=404, detail=f"候选不存在: {', '.join(sorted(missing_ref_ids)[:5])}")

        metadata["literature_candidates"] = updated_candidates
        metadata["paper_index"] = merge_paper_refs(
            paper_index,
            _build_run_paper_index(project_repo, paper_repo, run.project_id, normalize_paper_ids(metadata.get("paper_ids") if isinstance(metadata.get("paper_ids"), list) else [])),
        )
        project_repo.update_run(run.id, metadata=metadata)
        target = project_repo.get_target(run.target_id) if run.target_id else None
        actions = project_repo.list_run_actions(run.id)
        updated_run = _load_run_or_404(project_repo, run.id)
        return {
            "imported_paper_ids": list(dict.fromkeys(imported_ids)),
            "linked_paper_ids": list(dict.fromkeys(linked_ids)),
            "item": _serialize_run_detail(updated_run, target, actions),
        }


@router.delete("/project-runs/{run_id}")
def delete_project_run(
    run_id: str,
    delete_artifacts: bool = Query(default=False),
) -> dict:
    deleted_paths: list[str] = []
    skipped_paths: list[str] = []
    deleted_task_ids: list[str] = []
    deleted_generated_content_ids: list[str] = []

    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        task_repo = repos.tasks
        generated_repo = repos.generated
        run = _load_run_or_404(project_repo, run_id)
        actions = project_repo.list_run_actions(run.id)

        if _run_is_active(run.status):
            raise HTTPException(status_code=400, detail="运行仍在执行或等待确认，请先停止后再删除")
        active_actions = [action for action in actions if _run_is_active(action.status)]
        if active_actions:
            raise HTTPException(status_code=400, detail="当前运行仍有后续动作在执行，请先停止后再删除")

        if delete_artifacts:
            deleted_paths, skipped_paths = _delete_run_artifacts(run, actions)

        generated_content_ids = sorted(_collect_generated_content_ids(run.metadata_json or {}))
        for content_id in generated_content_ids:
            generated_repo.delete(content_id)
            deleted_generated_content_ids.append(content_id)

        task_id_candidates = {
            task_id
            for task_id in [run.task_id, *(action.task_id for action in actions)]
            if _clean_text(task_id)
        }
        for item in global_tracker.list_tasks(limit=500):
            if not isinstance(item, dict):
                continue
            item_task_id = _clean_text(item.get("task_id"))
            if not item_task_id:
                continue
            item_run_id = _clean_text(item.get("run_id"))
            item_action_id = _clean_text(item.get("action_id"))
            if item_run_id == run.id or item_action_id in {action.id for action in actions}:
                task_id_candidates.add(item_task_id)
        task_repo.delete_tasks(sorted(task_id_candidates))
        deleted_task_ids = sorted(task_id_candidates)

        deleted = project_repo.delete_run(run.id)
        if not deleted:
            raise HTTPException(status_code=404, detail="项目运行不存在")

    if deleted_task_ids:
        global_tracker.forget_tasks(deleted_task_ids, delete_persisted=False)

    return {
        "deleted": run_id,
        "artifacts_deleted": bool(delete_artifacts),
        "deleted_paths": deleted_paths,
        "skipped_paths": skipped_paths,
        "deleted_task_ids": deleted_task_ids,
        "deleted_generated_content_ids": deleted_generated_content_ids,
    }


@router.post("/project-runs/{run_id}/checkpoint/respond")
def respond_project_run_checkpoint(run_id: str, body: ProjectRunCheckpointResponseRequest) -> dict:
    action = str(body.action or "").strip().lower()
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="action 仅支持 approve / reject")

    try:
        process_checkpoint_response(
            run_id,
            action=action,
            comment=_clean_text(body.comment),
            response_source="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        failure_summary = f"执行器启动失败：{str(exc)[:180]}"
        raise HTTPException(status_code=500, detail=failure_summary) from exc

    with session_scope() as session:
        project_repo = _project_data(session).projects
        run = _load_run_or_404(project_repo, run_id)
        target = project_repo.get_target(run.target_id) if run.target_id else None
        actions = project_repo.list_run_actions(run.id)
        return {"item": _serialize_run_detail(run, target, actions)}


@router.post("/project-runs/{run_id}/retry")
def retry_project_run(run_id: str) -> dict:
    retry_run_id = ""
    workflow_type = None
    with session_scope() as session:
        project_repo = _project_data(session).projects
        existing = _load_run_or_404(project_repo, run_id)
        if not is_active_project_workflow(existing.workflow_type):
            raise HTTPException(
                status_code=400,
                detail=f"该 workflow 已从公开产品流中退役，不能重试: {existing.workflow_type.value}",
            )
        project = _load_project_or_404(project_repo, existing.project_id)
        workflow_type = existing.workflow_type
        metadata = build_checkpoint_settings(
            dict(existing.metadata_json or {}),
            reset_state=True,
        )
        _apply_engine_binding_metadata(
            metadata,
            executor_profile=_resolve_engine_profile_or_400(
                session,
                str(metadata.get("executor_engine_id") or ""),
                field_label="executor_engine_id",
            ) if _clean_text(str(metadata.get("executor_engine_id") or "")) else None,
            reviewer_profile=_resolve_engine_profile_or_400(
                session,
                str(metadata.get("reviewer_engine_id") or ""),
                field_label="reviewer_engine_id",
            ) if _clean_text(str(metadata.get("reviewer_engine_id") or "")) else None,
        )
        project_workspace_path = _project_workspace_path(project)
        target_workspace_path = _run_workspace_path(existing)
        sync_strategy = infer_sync_strategy(
            project_workspace=project_workspace_path,
            project_workspace_server_id=project.workspace_server_id,
            target_workspace=target_workspace_path,
            workspace_server_id=existing.workspace_server_id,
            target_workspace_server_id=existing.workspace_server_id,
        )
        orchestration = build_run_orchestration(
            existing.workflow_type,
            metadata.get("orchestration"),
            target_id=existing.target_id,
            workspace_server_id=existing.workspace_server_id,
            reset_stage_status=True,
        )
        metadata["sync_strategy"] = sync_strategy
        metadata["project_workspace_path"] = project_workspace_path
        metadata["project_workspace_server_id"] = project.workspace_server_id
        metadata["target_workspace_path"] = target_workspace_path
        metadata["target_workspace_server_id"] = existing.workspace_server_id
        metadata["orchestration"] = orchestration
        metadata["stage_trace"] = build_stage_trace(orchestration, reset=True)
        retry_run = project_repo.create_run(
            project_id=existing.project_id,
            target_id=existing.target_id,
            workflow_type=existing.workflow_type,
            title=_build_run_title(project.name, existing.workflow_type, f"{existing.title} · Retry"),
            prompt=existing.prompt,
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary=describe_sync_strategy(sync_strategy),
            workspace_server_id=existing.workspace_server_id,
            workdir=existing.workdir,
            remote_workdir=existing.remote_workdir,
            dataset_root=existing.dataset_root,
            checkpoint_root=existing.checkpoint_root,
            output_root=existing.output_root,
            retry_of_run_id=existing.id,
            max_iterations=existing.max_iterations,
            executor_model=getattr(existing, "executor_model", None),
            reviewer_model=existing.reviewer_model,
            metadata=metadata,
        )
        run_directory = build_run_directory(
            _run_workspace_path(retry_run),
            retry_run.id,
            remote=bool(existing.workspace_server_id),
        )
        log_path = build_run_log_path(run_directory, remote=bool(existing.workspace_server_id))
        metadata["run_directory"] = run_directory
        metadata["log_path"] = log_path
        for key, value in _build_remote_execution_metadata(
            existing.workflow_type,
            run_id=retry_run.id,
            workspace_server_id=existing.workspace_server_id,
            run_directory=run_directory,
        ).items():
            metadata[key] = value
        project_repo.update_run(
            retry_run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata=metadata,
        )
        retry_run_id = retry_run.id

    if workflow_type is not None and supports_project_run(workflow_type):
        try:
            submit_project_run(retry_run_id)
        except Exception as exc:
            with session_scope() as session:
                project_repo = _project_data(session).projects
                project_repo.update_run(
                    retry_run_id,
                    status=ProjectRunStatus.failed,
                    active_phase="failed",
                    summary=f"执行器启动失败：{str(exc)[:180]}",
                    finished_at=datetime.now(UTC),
                )

    with session_scope() as session:
        project_repo = _project_data(session).projects
        retry_run = _load_run_or_404(project_repo, retry_run_id)
        target = project_repo.get_target(retry_run.target_id) if retry_run.target_id else None
        return {"item": _serialize_run_detail(retry_run, target, project_repo.list_run_actions(retry_run.id))}


@router.post("/project-runs/{run_id}/actions")
def create_project_run_action(run_id: str, body: ProjectRunActionRequest) -> dict:
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    action_id = ""
    with session_scope() as session:
        project_repo = _project_data(session).projects
        run = _load_run_or_404(project_repo, run_id)
        metadata = dict(body.metadata or {})
        if _clean_text(body.workflow_type):
            metadata["workflow_type"] = _clean_text(body.workflow_type)
        if _clean_text(body.title):
            metadata["title"] = _clean_text(body.title)
        action = project_repo.create_run_action(
            run_id=run.id,
            action_type=_resolve_action_type(body.action_type),
            prompt=prompt,
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="跟进动作已创建，正在准备后台执行。",
            metadata=metadata,
        )
        action_id = action.id

    try:
        submit_project_run_action(action_id)
    except Exception as exc:
        with session_scope() as session:
            project_repo = _project_data(session).projects
            project_repo.update_run_action(
                action_id,
                status=ProjectRunStatus.failed,
                active_phase="failed",
                summary=f"后续动作启动失败：{str(exc)[:180]}",
            )

    with session_scope() as session:
        project_repo = _project_data(session).projects
        created = project_repo.get_run_action(action_id)
        if created is None:
            raise HTTPException(status_code=404, detail="项目运行动作不存在")
        return {"item": _serialize_run_action(created)}


@router.get("/projects/{project_id}/repos")
def list_project_repos(project_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        items = [_serialize_repo(repo) for repo in project_repo.list_repos(project_id)]
    return {"items": items}


@router.post("/projects/{project_id}/repos")
def create_project_repo(project_id: str, body: ProjectRepoRequest) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        cloned_at = None
        if _clean_text(body.cloned_at):
            try:
                cloned_at = datetime.fromisoformat(str(body.cloned_at))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="cloned_at 格式错误") from exc
        repo = project_repo.create_repo(
            project_id=project_id,
            repo_url=body.repo_url.strip(),
            local_path=_clean_text(body.local_path),
            cloned_at=cloned_at,
            is_workdir_repo=body.is_workdir_repo,
        )
        return {"item": _serialize_repo(repo)}


@router.patch("/projects/{project_id}/repos/{repo_id}")
def update_project_repo(project_id: str, repo_id: str, body: ProjectRepoUpdateRequest) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        _ensure_repo_in_project(project_id, repo_id, project_repo)
        payload: dict[str, object] = {}
        if body.repo_url is not None:
            payload["repo_url"] = body.repo_url.strip()
        if body.local_path is not None:
            payload["local_path"] = _clean_text(body.local_path)
        if body.cloned_at is not None:
            value = _clean_text(body.cloned_at)
            payload["cloned_at"] = datetime.fromisoformat(value) if value else None
        if body.is_workdir_repo is not None:
            payload["is_workdir_repo"] = body.is_workdir_repo
        updated = project_repo.update_repo(repo_id, **payload)
        if updated is None:
            raise HTTPException(status_code=404, detail="项目仓库不存在")
        return {"item": _serialize_repo(updated)}


@router.delete("/projects/{project_id}/repos/{repo_id}")
def delete_project_repo(project_id: str, repo_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        _ensure_repo_in_project(project_id, repo_id, project_repo)
        project_repo.delete_repo(repo_id)
    return {"deleted": repo_id}


@router.get("/projects/{project_id}/repos/{repo_id}/commits")
def list_repo_commits(project_id: str, repo_id: str, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        repo = _ensure_repo_in_project(project_id, repo_id, project_repo)
        local_path = repo.local_path
    path = _require_local_git_path(local_path)
    return {"items": _git_commits(path, limit)}


@router.get("/projects/{project_id}/ideas")
def list_project_ideas(project_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        items = [_serialize_idea(idea) for idea in project_repo.list_ideas(project_id)]
    return {"items": items}


@router.post("/projects/{project_id}/ideas")
def create_project_idea(project_id: str, body: ProjectIdeaRequest) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        idea = project_repo.create_idea(
            project_id=project_id,
            title=body.title.strip(),
            content=body.content.strip(),
            paper_ids=[paper_id for paper_id in body.paper_ids if _clean_text(paper_id)],
        )
        return {"item": _serialize_idea(idea)}


@router.post("/projects/{project_id}/ideas/generate")
def generate_project_idea(project_id: str, body: ProjectIdeaGenerateRequest) -> dict:
    try:
        return _generate_project_idea_payload(project_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/ideas/generate/async")
def generate_project_idea_async(project_id: str, body: ProjectIdeaGenerateRequest) -> dict:
    with session_scope() as session:
        repos = _project_data(session)
        project_repo = repos.projects
        paper_repo = repos.papers
        try:
            project, _selected_papers, _selected_repos = _resolve_project_idea_inputs(
                project_id,
                body,
                project_repo,
                paper_repo,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project_name = project.name

    task_id = global_tracker.submit(
        task_type="project_idea_generate",
        title=f"研究想法生成 · {project_name[:42]}",
        fn=_run_generate_project_idea_task,
        task_id=f"project_idea_{project_id[:8]}_{uuid4().hex[:6]}",
        total=100,
        project_id=project_id,
        body_payload=body.model_dump(),
    )
    return {
        "task_id": task_id,
        "status": "running",
        "message": "研究想法生成任务已启动，可在任务列表查看进度",
    }


@router.patch("/projects/{project_id}/ideas/{idea_id}")
def update_project_idea(project_id: str, idea_id: str, body: ProjectIdeaUpdateRequest) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        idea = project_repo.get_idea(idea_id)
        if idea is None or idea.project_id != project_id:
            raise HTTPException(status_code=404, detail="项目想法不存在")
        updated = project_repo.update_idea(
            idea_id,
            title=body.title.strip() if body.title is not None else idea.title,
            content=body.content.strip() if body.content is not None else idea.content,
            paper_ids=body.paper_ids if body.paper_ids is not None else list(idea.paper_ids_json or []),
        )
        assert updated is not None
        return {"item": _serialize_idea(updated)}


@router.delete("/projects/{project_id}/ideas/{idea_id}")
def delete_project_idea(project_id: str, idea_id: str) -> dict:
    with session_scope() as session:
        project_repo = _project_data(session).projects
        _load_project_or_404(project_repo, project_id)
        idea = project_repo.get_idea(idea_id)
        if idea is None or idea.project_id != project_id:
            raise HTTPException(status_code=404, detail="项目想法不存在")
        project_repo.delete_idea(idea_id)
    return {"deleted": idea_id}

