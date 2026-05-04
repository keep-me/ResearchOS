from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from packages.agent.workspace.workspace_executor import write_workspace_file
from packages.agent.workspace.workspace_remote import remote_write_file
from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry
from packages.ai.project.amadeus_compat import (
    amadeus_action_label,
    build_action_log_path,
    build_action_result_path,
    build_remote_session_name,
    build_run_directory,
    build_run_log_path,
    build_run_workspace_path,
    get_amadeus_workflow_config,
)
from packages.ai.project.checkpoint_service import build_checkpoint_settings
from packages.ai.project.execution_service import submit_project_run, supports_project_run
from packages.ai.project.followup_actions import resolve_followup_action
from packages.ai.project.workflow_catalog import (
    build_run_orchestration,
    build_stage_trace,
    is_active_project_workflow,
)
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.domain.task_tracker import TaskCancelledError, global_tracker
from packages.storage.db import session_scope
from packages.storage.repository_facades import ProjectDataFacade

_TOTAL_PROGRESS = 100


def _project_repo(session):
    return ProjectDataFacade.from_session(session).projects


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _excerpt(value: str, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _run_workspace_path(run) -> str:
    return str(run.remote_workdir or run.workdir or "").strip()


def _display_root_path(run) -> str:
    return str(run.run_directory or _run_workspace_path(run)).strip()


def _storage_root_path(run) -> str:
    if run.workspace_server_id:
        return _run_workspace_path(run)
    return _display_root_path(run)


def _artifact_root_path(run) -> str:
    return _display_root_path(run) or _run_workspace_path(run)


def _relative_path(root_path: str | None, target_path: str | None, *, remote: bool) -> str | None:
    root = str(root_path or "").strip()
    target = str(target_path or "").strip()
    if not root or not target:
        return None
    if remote:
        normalized_root = root.replace("\\", "/").rstrip("/")
        normalized_target = target.replace("\\", "/")
        if normalized_target == normalized_root:
            return ""
        prefix = f"{normalized_root}/"
        if normalized_target.startswith(prefix):
            return normalized_target[len(prefix) :]
        return None
    try:
        resolved_root = Path(root).expanduser().resolve()
        resolved_target = Path(target).expanduser().resolve()
        return resolved_target.relative_to(resolved_root).as_posix()
    except Exception:
        return None


def _artifact_ref(run, path: str | None, *, kind: str = "artifact") -> dict[str, Any] | None:
    absolute_path = str(path or "").strip()
    if not absolute_path:
        return None
    relative_path = _relative_path(
        _artifact_root_path(run), absolute_path, remote=bool(run.workspace_server_id)
    )
    payload: dict[str, Any] = {
        "kind": kind,
        "path": absolute_path,
    }
    if relative_path is not None:
        payload["relative_path"] = relative_path
    return payload


def _write_action_file(
    run, absolute_path: str | None, content: str, *, kind: str = "artifact"
) -> dict[str, Any] | None:
    target_path = str(absolute_path or "").strip()
    if not target_path:
        return None
    root_path = _storage_root_path(run)
    if not root_path:
        return None
    relative_path = _relative_path(root_path, target_path, remote=bool(run.workspace_server_id))
    if relative_path is None:
        return None

    if run.workspace_server_id:
        server_entry = get_workspace_server_entry(run.workspace_server_id)
        remote_write_file(
            server_entry,
            path=root_path,
            relative_path=relative_path,
            content=content,
            create_dirs=True,
            overwrite=True,
        )
    else:
        write_workspace_file(
            root_path,
            relative_path,
            content,
            create_dirs=True,
            overwrite=True,
        )
    return _artifact_ref(run, target_path, kind=kind)


def _workflow_label(workflow_type: ProjectWorkflowType | str) -> str:
    compat = get_amadeus_workflow_config(workflow_type)
    label = str(compat.get("label") or "").strip()
    raw = str(
        workflow_type.value
        if isinstance(workflow_type, ProjectWorkflowType)
        else workflow_type or ""
    ).strip()
    return label or raw.replace("_", " ").strip() or "Workflow"


def _normalize_transition(run, action) -> dict[str, Any]:
    action_metadata = dict(getattr(action, "metadata_json", {}) or {})
    requested_workflow = str(action_metadata.get("workflow_type") or "").strip() or None
    transition = resolve_followup_action(
        run.workflow_type,
        action.action_type,
        workflow_type=requested_workflow,
    )
    workflow_type = ProjectWorkflowType(str(transition["workflow_type"]))
    if not is_active_project_workflow(workflow_type):
        raise ValueError(f"后续流程尚未开放执行: {workflow_type.value}")
    if not supports_project_run(workflow_type):
        raise ValueError(f"后续流程当前不可执行: {workflow_type.value}")
    return transition


def _build_followup_run_prompt(project, run, action, target, transition: dict[str, Any]) -> str:
    metadata = dict(run.metadata_json or {})
    workflow_output = str(metadata.get("workflow_output_markdown") or "").strip()
    stage_outputs = metadata.get("stage_outputs") or {}

    lines = [
        "Continue this research project using the next ResearchOS workflow.",
        "",
        f"Project: {project.name}",
        f"Project Description: {project.description or 'N/A'}",
        f"Parent Workflow: {run.workflow_type.value}",
        f"Parent Run Title: {run.title or 'N/A'}",
        f"Parent Prompt: {run.prompt or 'N/A'}",
        f"Next Workflow: {transition.get('label') or transition.get('workflow_label')}",
        f"Next Workflow Type: {transition['workflow_type']}",
        f"Upstream Command: {transition.get('command') or 'N/A'}",
        f"Source Skill: {transition.get('source_skill') or 'N/A'}",
        f"Target: {target.label if target is not None else 'project_default'}",
        f"Workspace: {_run_workspace_path(run) or 'N/A'}",
        f"Workspace Server: {run.workspace_server_id or 'local'}",
        "",
        "User Follow-up Instruction:",
        str(action.prompt or "").strip() or "N/A",
    ]

    if workflow_output:
        lines.extend(["", "Parent Workflow Output:", workflow_output[:12000]])

    if isinstance(stage_outputs, dict) and stage_outputs:
        lines.extend(["", "Parent Stage Output Summaries:"])
        for key, item in stage_outputs.items():
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("stage_id") or key or "stage").strip()
            summary = str(item.get("summary") or item.get("content") or "").strip()
            if summary:
                lines.append(f"- {label}: {summary[:1400]}")

    lines.extend(
        [
            "",
            "Requirements:",
            "1. Treat the parent run outputs as input material for this next workflow.",
            "2. Stay aligned with the current ResearchOS workflow semantics.",
            "3. Preserve concrete artifacts, decisions, blockers, and next steps in the new run outputs.",
        ]
    )
    return "\n".join(lines).strip()


def _build_action_result_markdown(
    project, run, action, transition: dict[str, Any], child_run
) -> str:
    lines = [
        "# Follow-up Workflow Started",
        "",
        f"- Project: {project.name}",
        f"- Parent Run: {run.id}",
        f"- Parent Workflow: {run.workflow_type.value}",
        f"- Action Type: {action.action_type.value}",
        f"- Action Label: {transition.get('label') or amadeus_action_label(action.action_type)}",
        f"- Next Workflow: {transition['workflow_type']}",
        f"- Next Workflow Label: {transition.get('workflow_label') or _workflow_label(transition['workflow_type'])}",
        f"- Upstream Command: {transition.get('command') or 'N/A'}",
        f"- Source Skill: {transition.get('source_skill') or 'N/A'}",
        f"- Spawned Run: {child_run.id}",
        f"- Spawned Run Title: {child_run.title}",
        f"- Spawned Run Status: {child_run.status.value if hasattr(child_run.status, 'value') else child_run.status}",
        f"- Spawned Run Directory: {child_run.run_directory or 'N/A'}",
        f"- Spawned Run Log: {child_run.log_path or 'N/A'}",
        "",
        "## User Instruction",
        "",
        str(action.prompt or "").strip() or "N/A",
    ]
    if child_run.prompt:
        lines.extend(
            [
                "",
                "## Follow-up Prompt",
                "",
                str(child_run.prompt).strip(),
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _build_action_log(
    action, run, transition: dict[str, Any], child_run, *, status: str, summary: str
) -> str:
    lines = [
        f"# {transition.get('label') or amadeus_action_label(action.action_type)}",
        "",
        f"- Status: {status}",
        f"- Action Type: {action.action_type.value if hasattr(action.action_type, 'value') else action.action_type}",
        f"- Run ID: {run.id}",
        f"- Project ID: {run.project_id}",
        f"- Parent Workflow: {run.workflow_type.value}",
        f"- Spawned Workflow: {transition['workflow_type']}",
        f"- Spawned Workflow Label: {transition.get('workflow_label') or _workflow_label(transition['workflow_type'])}",
        f"- Upstream Command: {transition.get('command') or 'N/A'}",
        f"- Source Skill: {transition.get('source_skill') or 'N/A'}",
        f"- Spawned Run ID: {child_run.id}",
        f"- Spawned Run Status: {child_run.status.value if hasattr(child_run.status, 'value') else child_run.status}",
        f"- Workspace: {_display_root_path(run) or 'N/A'}",
        f"- Workspace Server: {run.workspace_server_id or 'local'}",
        f"- Generated At: {_iso_now()}",
        "",
        "## Action Prompt",
        "",
        str(action.prompt or "").strip() or "N/A",
        "",
        "## Summary",
        "",
        summary or "N/A",
    ]
    return "\n".join(lines).strip() + "\n"


def _build_followup_metadata(run, action, transition: dict[str, Any]) -> dict[str, Any]:
    inherited = dict(run.metadata_json or {})
    for key in (
        "workflow_output_markdown",
        "workflow_output_excerpt",
        "stage_outputs",
        "artifact_refs",
        "stage_trace",
        "orchestration",
        "run_directory",
        "log_path",
        "result_path",
        "pending_checkpoint",
        "checkpoint_requested_at",
        "checkpoint_approved_at",
        "checkpoint_rejected_at",
        "checkpoint_response_comment",
        "checkpoint_resume_stage_id",
        "checkpoint_resume_stage_label",
        "last_checkpoint",
        "last_action_id",
        "last_action_type",
        "last_action_summary",
        "last_action_spawned_run_id",
        "completed_at",
        "failed_at",
        "error",
    ):
        inherited.pop(key, None)
    inherited = build_checkpoint_settings(
        inherited,
        auto_proceed=inherited.get("auto_proceed"),
        notification_recipients=inherited.get("notification_recipients"),
        reset_state=True,
    )
    inherited["launched_from"] = "project_run_action"
    inherited["followup_parent_run_id"] = run.id
    inherited["followup_parent_workflow_type"] = run.workflow_type.value
    inherited["followup_parent_title"] = run.title
    inherited["followup_parent_prompt"] = run.prompt
    inherited["followup_action_id"] = action.id
    inherited["followup_action_type"] = action.action_type.value
    inherited["followup_command"] = transition.get("command")
    inherited["followup_source_skill"] = transition.get("source_skill")
    inherited["followup_transition_label"] = transition.get("label")
    inherited["followup_requested_instruction"] = str(action.prompt or "").strip()
    return inherited


def _build_followup_title(project_name: str, action, transition: dict[str, Any]) -> str:
    explicit = str(getattr(action, "title", "") or "").strip()
    if explicit:
        return explicit[:512]
    label = str(transition.get("label") or transition.get("workflow_label") or "Follow-up").strip()
    return f"{label} · {project_name[:72]}"[:512]


def _load_child_run(run_id: str):
    with session_scope() as session:
        project_repo = _project_repo(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"spawned project run {run_id} not found")
        return SimpleNamespace(
            id=str(run.id),
            title=str(run.title or ""),
            prompt=str(run.prompt or ""),
            status=run.status,
            run_directory=str(run.run_directory or "") or None,
            log_path=str(run.log_path or "") or None,
            result_path=str(run.result_path or "") or None,
            task_id=str(run.task_id or "") or None,
            workflow_type=run.workflow_type,
        )


def _create_followup_run(project, run, target, action, transition: dict[str, Any]) -> str:
    workflow_type = ProjectWorkflowType(str(transition["workflow_type"]))
    metadata = _build_followup_metadata(run, action, transition)
    prompt = _build_followup_run_prompt(project, run, action, target, transition)
    child_run_id = ""

    with session_scope() as session:
        project_repo = _project_repo(session)
        run_row = project_repo.get_run(run.id)
        if run_row is None:
            raise ValueError(f"project run {run.id} not found")
        orchestration = build_run_orchestration(
            workflow_type,
            target_id=run_row.target_id,
            workspace_server_id=run_row.workspace_server_id,
        )
        metadata["orchestration"] = orchestration
        metadata["stage_trace"] = build_stage_trace(orchestration, reset=True)

        child_run = project_repo.create_run(
            project_id=run_row.project_id,
            target_id=run_row.target_id,
            workflow_type=workflow_type,
            title=_build_followup_title(project.name, action, transition),
            prompt=prompt,
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary=f"由父运行 {run.id} 发起，准备进入 {_workflow_label(workflow_type)}。",
            workspace_server_id=run_row.workspace_server_id,
            workdir=run_row.workdir,
            remote_workdir=run_row.remote_workdir,
            dataset_root=run_row.dataset_root,
            checkpoint_root=run_row.checkpoint_root,
            output_root=run_row.output_root,
            max_iterations=run_row.max_iterations,
            executor_model=run_row.executor_model,
            reviewer_model=run_row.reviewer_model,
            metadata=metadata,
        )
        workspace_path = _run_workspace_path(child_run)
        run_directory = build_run_directory(
            workspace_path,
            child_run.id,
            remote=bool(child_run.workspace_server_id),
        )
        log_path = build_run_log_path(run_directory, remote=bool(child_run.workspace_server_id))
        metadata["run_directory"] = run_directory
        metadata["log_path"] = log_path
        if child_run.workspace_server_id and workflow_type == ProjectWorkflowType.run_experiment:
            execution_workspace = build_run_workspace_path(run_directory, remote=True)
            metadata["remote_session_name"] = build_remote_session_name(child_run.id)
            metadata["remote_isolation_mode"] = "pending"
            metadata["gpu_mode"] = "auto"
            metadata["gpu_strategy"] = "least_used_free"
            metadata["gpu_memory_threshold_mb"] = 500
            if execution_workspace:
                metadata["remote_execution_workspace"] = execution_workspace
        project_repo.update_run(
            child_run.id,
            run_directory=run_directory,
            log_path=log_path,
            metadata=metadata,
        )
        child_run_id = child_run.id

    try:
        submit_project_run(child_run_id)
    except Exception as exc:
        with session_scope() as session:
            project_repo = _project_repo(session)
            child_run = project_repo.get_run(child_run_id)
            if child_run is not None:
                project_repo.update_run(
                    child_run.id,
                    status=ProjectRunStatus.failed,
                    active_phase="failed",
                    summary=f"后续流程启动失败：{str(exc)[:180]}",
                    finished_at=datetime.now(UTC),
                )
        raise

    return child_run_id


def submit_project_run_action(action_id: str) -> str:
    project_id = ""
    run_id = ""
    action_ref = ""
    workspace_server_id = "local"
    workspace_path = ""
    run_directory = None
    with session_scope() as session:
        project_repo = _project_repo(session)
        action = project_repo.get_run_action(action_id)
        if action is None:
            raise ValueError(f"project run action {action_id} not found")
        run = project_repo.get_run(action.run_id)
        if run is None:
            raise ValueError(f"project run {action.run_id} not found")
        project = project_repo.get_project(run.project_id)
        if project is None:
            raise ValueError(f"project {run.project_id} not found")

        task_id = action.task_id or f"project_run_action_{action.id.replace('-', '')[:12]}"
        result_path = action.result_path or build_action_result_path(
            run.run_directory,
            action.id,
            remote=bool(run.workspace_server_id),
        )
        log_path = action.log_path or build_action_log_path(
            run.run_directory,
            action.id,
            remote=bool(run.workspace_server_id),
        )
        metadata = dict(action.metadata_json or {})
        project_id = str(project.id)
        run_id = str(run.id)
        action_ref = str(action.id)
        workspace_server_id = str(run.workspace_server_id or "").strip() or "local"
        workspace_path = _display_root_path(run)
        run_directory = run.run_directory
        metadata.update(
            {
                "submitted_at": _iso_now(),
                "workspace_path": _run_workspace_path(run),
                "workspace_server_id": run.workspace_server_id,
                "run_id": run.id,
                "project_id": project.id,
            }
        )
        project_repo.update_run_action(
            action.id,
            task_id=task_id,
            status=ProjectRunStatus.running,
            active_phase="resolve_followup",
            summary="后续动作已启动，正在解析下一条项目工作流。",
            log_path=log_path,
            result_path=result_path,
            metadata=metadata,
        )
        title = f"{project.name} · {amadeus_action_label(action.action_type)}"

    global_tracker.submit(
        "project_run_action",
        title,
        run_project_run_action,
        action_id,
        task_id=task_id,
        total=_TOTAL_PROGRESS,
        metadata={
            "source": "project",
            "source_id": action_ref,
            "project_id": project_id,
            "run_id": run_id,
            "action_id": action_ref,
            "log_path": log_path,
            "workspace_server_id": workspace_server_id,
            "workspace_path": workspace_path,
            "run_directory": run_directory,
            "result_path": result_path,
        },
    )
    return task_id


def run_project_run_action(
    action_id: str,
    *,
    progress_callback=None,
) -> dict[str, Any]:
    with session_scope() as session:
        project_repo = _project_repo(session)
        action_row = project_repo.get_run_action(action_id)
        if action_row is None:
            raise ValueError(f"project run action {action_id} not found")
        run_row = project_repo.get_run(action_row.run_id)
        if run_row is None:
            raise ValueError(f"project run {action_row.run_id} not found")
        project_row = project_repo.get_project(run_row.project_id)
        if project_row is None:
            raise ValueError(f"project {run_row.project_id} not found")
        target_row = project_repo.get_target(run_row.target_id) if run_row.target_id else None

        action = SimpleNamespace(
            id=str(action_row.id),
            run_id=str(action_row.run_id),
            action_type=action_row.action_type,
            prompt=str(action_row.prompt or ""),
            task_id=str(action_row.task_id or "") or None,
            log_path=str(action_row.log_path or "") or None,
            result_path=str(action_row.result_path or "") or None,
            metadata_json=dict(action_row.metadata_json or {}),
            title=str((action_row.metadata_json or {}).get("title") or "").strip() or None,
        )
        run = SimpleNamespace(
            id=str(run_row.id),
            project_id=str(run_row.project_id),
            workflow_type=run_row.workflow_type,
            title=str(run_row.title or ""),
            prompt=str(run_row.prompt or ""),
            workspace_server_id=str(run_row.workspace_server_id or "") or None,
            workdir=str(run_row.workdir or "") or None,
            remote_workdir=str(run_row.remote_workdir or "") or None,
            run_directory=str(run_row.run_directory or "") or None,
            log_path=str(run_row.log_path or "") or None,
            metadata_json=dict(run_row.metadata_json or {}),
            max_iterations=run_row.max_iterations,
            target_id=str(run_row.target_id or "") or None,
            dataset_root=str(run_row.dataset_root or "") or None,
            checkpoint_root=str(run_row.checkpoint_root or "") or None,
            output_root=str(run_row.output_root or "") or None,
            executor_model=str(run_row.executor_model or "") or None,
            reviewer_model=str(run_row.reviewer_model or "") or None,
        )
        project = SimpleNamespace(
            id=str(project_row.id),
            name=str(project_row.name or ""),
            description=str(project_row.description or "") or None,
        )
        target = (
            SimpleNamespace(
                id=str(target_row.id),
                label=str(target_row.label or ""),
            )
            if target_row is not None
            else None
        )

    task_id = str(action.task_id or "").strip()
    child_run_id = ""
    try:
        _raise_if_cancel_requested(task_id)
        _update_action(
            action_id,
            status=ProjectRunStatus.running,
            active_phase="resolve_followup",
            summary="正在匹配下一条项目工作流。",
        )
        _emit_progress(progress_callback, "正在匹配下一条项目工作流。", 18)

        transition = _normalize_transition(run, action)
        _raise_if_cancel_requested(task_id)
        _update_action(
            action_id,
            status=ProjectRunStatus.running,
            active_phase="spawn_followup_run",
            summary=f"正在启动 {transition.get('label') or transition['workflow_type']}。",
            metadata_updates={
                "resolved_label": transition.get("label"),
                "resolved_workflow_type": transition.get("workflow_type"),
                "resolved_workflow_label": transition.get("workflow_label"),
                "resolved_command": transition.get("command"),
                "source_skill": transition.get("source_skill"),
            },
        )
        _emit_progress(
            progress_callback,
            f"正在启动 {transition.get('label') or transition['workflow_type']}。",
            52,
        )

        child_run_id = _create_followup_run(project, run, target, action, transition)
        child_run = _load_child_run(child_run_id)

        markdown = _build_action_result_markdown(project, run, action, transition, child_run)
        result_path = str(action.result_path or "").strip() or build_action_result_path(
            run.run_directory,
            action.id,
            remote=bool(run.workspace_server_id),
        )
        result_artifact = _write_action_file(run, result_path, markdown, kind="report")

        summary = f"已启动 {transition.get('label') or transition['workflow_type']}，新的运行 ID 为 {child_run.id}。"
        log_content = _build_action_log(
            action,
            run,
            transition,
            child_run,
            status="completed",
            summary=summary,
        )
        log_artifact = _write_action_file(run, action.log_path, log_content, kind="log")
        artifact_refs = [item for item in [result_artifact, log_artifact] if item]

        metadata_updates = {
            "completed_at": _iso_now(),
            "artifact_refs": artifact_refs,
            "resolved_label": transition.get("label"),
            "resolved_workflow_type": transition.get("workflow_type"),
            "resolved_workflow_label": transition.get("workflow_label"),
            "resolved_command": transition.get("command"),
            "source_skill": transition.get("source_skill"),
            "spawned_run_id": child_run.id,
            "spawned_run_title": child_run.title,
            "spawned_run_task_id": child_run.task_id,
            "spawned_run_workflow_type": child_run.workflow_type.value,
            "spawned_run_workflow_label": _workflow_label(child_run.workflow_type),
            "spawned_run_log_path": child_run.log_path,
            "spawned_run_result_path": child_run.result_path,
        }
        _update_action(
            action_id,
            status=ProjectRunStatus.succeeded,
            active_phase="completed",
            summary=summary,
            result_path=result_path,
            metadata_updates=metadata_updates,
        )
        _update_run_metadata(
            run.id,
            {
                "last_action_id": action.id,
                "last_action_type": str(action.action_type.value),
                "last_action_summary": summary,
                "last_action_spawned_run_id": child_run.id,
            },
        )
        _emit_progress(progress_callback, "后续工作流已启动。", 100)

        result = {
            "action_id": action.id,
            "run_id": run.id,
            "spawned_run_id": child_run.id,
            "spawned_run_title": child_run.title,
            "spawned_workflow_type": child_run.workflow_type.value,
            "summary": summary,
            "markdown": markdown,
            "result_path": result_path,
            "artifact_refs": artifact_refs,
        }
        if task_id:
            global_tracker.set_metadata(
                task_id,
                {
                    "source": "project",
                    "source_id": action.id,
                    "project_id": project.id,
                    "run_id": run.id,
                    "action_id": action.id,
                    "log_path": action.log_path,
                    "workspace_server_id": run.workspace_server_id or "local",
                    "workspace_path": _display_root_path(run),
                    "run_directory": run.run_directory,
                    "result_path": result_path,
                    "artifact_refs": artifact_refs,
                    "spawned_run_id": child_run.id,
                    "spawned_run_task_id": child_run.task_id,
                    "spawned_workflow_type": child_run.workflow_type.value,
                },
            )
            global_tracker.set_result(task_id, result)
        return result
    except TaskCancelledError:
        cancelled_summary = "后续动作已取消。"
        _update_action(
            action_id,
            status=ProjectRunStatus.cancelled,
            active_phase="cancelled",
            summary=cancelled_summary,
            metadata_updates={"cancelled_at": _iso_now()},
        )
        raise
    except Exception as exc:
        failed_summary = f"后续动作执行失败：{str(exc)[:180]}"
        _update_action(
            action_id,
            status=ProjectRunStatus.failed,
            active_phase="failed",
            summary=failed_summary,
            metadata_updates={"error": str(exc), "failed_at": _iso_now()},
        )
        if task_id:
            global_tracker.set_metadata(
                task_id,
                {
                    "source": "project",
                    "source_id": action.id,
                    "project_id": project.id,
                    "run_id": run.id,
                    "action_id": action.id,
                    "log_path": action.log_path,
                    "workspace_server_id": run.workspace_server_id or "local",
                    "workspace_path": _display_root_path(run),
                    "run_directory": run.run_directory,
                    "result_path": action.result_path,
                    "artifact_refs": [
                        item for item in [_artifact_ref(run, action.log_path, kind="log")] if item
                    ],
                },
            )
        raise


def _update_action(
    action_id: str,
    *,
    status: ProjectRunStatus,
    active_phase: str,
    summary: str,
    result_path: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
) -> None:
    with session_scope() as session:
        project_repo = _project_repo(session)
        action = project_repo.get_run_action(action_id)
        if action is None:
            return
        metadata = dict(action.metadata_json or {})
        metadata.update(metadata_updates or {})
        project_repo.update_run_action(
            action_id,
            status=status,
            active_phase=active_phase,
            summary=summary,
            result_path=result_path if result_path is not None else action.result_path,
            metadata=metadata,
        )


def _update_run_metadata(run_id: str, updates: dict[str, Any]) -> None:
    with session_scope() as session:
        project_repo = _project_repo(session)
        run = project_repo.get_run(run_id)
        if run is None:
            return
        metadata = dict(run.metadata_json or {})
        metadata.update(updates)
        project_repo.update_run(run_id, metadata=metadata)


def _emit_progress(progress_callback, message: str, current: int) -> None:
    if progress_callback:
        progress_callback(message, current, _TOTAL_PROGRESS)


def _raise_if_cancel_requested(task_id: str) -> None:
    if task_id and global_tracker.is_cancel_requested(task_id):
        raise TaskCancelledError("任务已终止")
