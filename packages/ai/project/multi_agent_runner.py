from __future__ import annotations

import csv
import io
import json
import logging
import math
import mimetypes
import posixpath
import re
import shlex
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from packages.agent.runtime.agent_backends import (
    DEFAULT_AGENT_BACKEND_ID,
    normalize_agent_backend_id,
)
from packages.agent.workspace.workspace_executor import (
    DEFAULT_IGNORES,
    run_workspace_command,
    write_workspace_file,
)
from packages.agent.workspace.workspace_remote import (
    build_remote_overview,
    clean_text,
    open_ssh_session,
    remote_capture_screen_session,
    remote_is_dir,
    remote_list_screen_sessions,
    remote_make_dirs,
    remote_probe_gpus,
    remote_read_file,
    remote_stat,
    remote_terminal_result,
    remote_upload_file,
    resolve_remote_workspace_path,
)
from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry
from packages.ai.project.amadeus_compat import (
    workflow_assistant_skill_id,
    workflow_is_workspace_skill,
    workflow_runner_preamble,
)
from packages.ai.project.checkpoint_service import checkpoint_resume_stage
from packages.ai.project.output_sanitizer import sanitize_project_markdown
from packages.ai.project.paper_artifacts import (
    build_figure_bundle,
    build_paper_compile_bundle,
    build_paper_improvement_bundle,
    build_paper_plan_bundle,
    build_paper_write_bundle,
    parse_review_text,
    resolve_paper_venue,
)
from packages.ai.project.paper_context import format_ref_index_for_prompt
from packages.ai.project.runtime.artifacts import (
    collect_run_artifacts as _collect_run_artifacts,
)
from packages.ai.project.runtime.artifacts import (
    write_run_artifact as _write_run_artifact,
)
from packages.ai.project.runtime.artifacts import (
    write_run_json_artifact as _write_run_json_artifact,
)
from packages.ai.project.runtime.artifacts import (
    write_run_log as _write_run_log,
)
from packages.ai.project.runtime.context import ProgressCallback, WorkflowContext
from packages.ai.project.runtime.gpu import list_active_gpu_leases, reconcile_gpu_leases
from packages.ai.project.runtime.llm_roles import (
    resolve_role_profile as _resolve_role_profile,
)
from packages.ai.project.runtime.llm_roles import (
    resolve_stage_model_target as _resolve_stage_model_target,
)
from packages.ai.project.runtime.stage_state import (
    cancel_active_stage as _cancel_active_stage,
)
from packages.ai.project.runtime.stage_state import (
    emit_progress as _emit_progress,
)
from packages.ai.project.runtime.stage_state import (
    ensure_run_orchestration as _ensure_run_orchestration,
)
from packages.ai.project.runtime.stage_state import (
    fail_active_stage as _fail_active_stage,
)
from packages.ai.project.runtime.stage_state import (
    iso_now as _iso_now,
)
from packages.ai.project.runtime.stage_state import (
    maybe_pause_after_stage as _maybe_pause_after_stage,
)
from packages.ai.project.runtime.stage_state import (
    patch_run as _patch_run,
)
from packages.ai.project.runtime.stage_state import (
    record_stage_output as _record_stage_output,
)
from packages.ai.project.runtime.stage_state import (
    set_stage_state as _set_stage_state,
)
from packages.ai.project.runtime.workspace import (
    command_result_preview as _command_result_preview,
)
from packages.ai.project.runtime.workspace import (
    format_command_log as _format_command_log,
)
from packages.ai.project.runtime.workspace import (
    inspect_workspace_payload as _inspect_workspace_payload,
)
from packages.ai.project.runtime.workspace import (
    run_workspace_command_for_context as _run_workspace_command_for_context,
)
from packages.ai.project.workflow_catalog import (
    build_run_orchestration,
    build_stage_trace,
    get_project_workflow_preset,
)
from packages.ai.project.workflows.shared import (
    build_experiment_audit_prompt as _build_experiment_audit_prompt,
)
from packages.ai.project.workflows.shared import (
    collect_experiment_audit_bundle as _collect_experiment_audit_bundle,
)
from packages.ai.project.workflows.shared import (
    load_context as _load_context,
)
from packages.ai.project.workflows.shared import (
    markdown_excerpt as _markdown_excerpt,
)
from packages.ai.project.workflows.shared import (
    render_experiment_audit_report as _render_experiment_audit_report,
)
from packages.ai.project.workflows.shared import (
    resolve_execution_command as _resolve_execution_command,
)
from packages.ai.project.workflows.shared import (
    resolve_execution_timeout as _resolve_execution_timeout,
)
from packages.ai.project.workflows.shared import (
    resolve_experiment_audit_payload as _resolve_experiment_audit_payload,
)
from packages.ai.project.workflows.shared import (
    resolve_idea_payloads as _resolve_idea_payloads,
)
from packages.ai.project.workflows.shared import (
    resolve_literature_markdown as _resolve_literature_markdown,
)
from packages.domain.enums import ProjectRunStatus
from packages.domain.task_tracker import TaskCancelledError, TaskPausedError, global_tracker
from packages.integrations.llm_client import LLMClient, LLMResult
from packages.storage.db import session_scope
from packages.storage.repositories import GeneratedContentRepository, ProjectRepository

logger = logging.getLogger(__name__)

_TOTAL_PROGRESS = 100
_DEFAULT_AGENT_ID = "codex"
_REPORT_ARTIFACT_WORKFLOWS = {
    "paper_plan",
    "paper_figure",
    "paper_write",
    "paper_compile",
    "paper_improvement",
    "experiment_audit",
    "monitor_experiment",
    "sync_workspace",
}

_SYNC_SKIP_DIRS = {
    ".auto-researcher",
    "data",
    "outputs",
    "checkpoints",
    "artifacts",
}
_SYNC_SKIP_SUFFIXES = {
    ".pt",
    ".pth",
    ".ckpt",
    ".bin",
    ".npz",
    ".npy",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
}
_SYNC_MAX_FILE_SIZE_BYTES = 8 * 1024 * 1024
_MONITOR_TEXT_SUFFIXES = {".log", ".out", ".err", ".json", ".csv", ".txt", ".md"}
_MONITOR_CHECKPOINT_SUFFIXES = {".ckpt", ".pt", ".pth", ".safetensors"}
_MONITOR_RESULT_NAME_MARKERS = ("metric", "result", "eval", "summary", "score", "report")
_MONITOR_PRIORITY_METRICS = (
    "accuracy",
    "acc",
    "f1",
    "bleu",
    "rouge",
    "score",
    "success_rate",
    "win_rate",
    "loss",
    "val_loss",
    "train_loss",
    "error",
    "wer",
    "cer",
)
_MONITOR_ALERT_PATTERNS = (
    ("traceback", re.compile(r"traceback", re.IGNORECASE)),
    ("oom", re.compile(r"out of memory|cuda oom|oom-killed", re.IGNORECASE)),
    ("nan", re.compile(r"(^|[^A-Za-z])nan([^A-Za-z]|$)", re.IGNORECASE)),
    ("divergence", re.compile(r"diverg|overflow|explod", re.IGNORECASE)),
    ("error", re.compile(r"\berror\b|\bexception\b|\bfailed\b", re.IGNORECASE)),
)

_AGENT_ROLE_PROFILES: dict[str, dict[str, str]] = {
    "codex": {
        "label": "Codex（原生模型角色）",
        "strategy": "偏工程实现与实验可执行性，强调步骤清晰和可落地。",
    },
    "claude_code": {
        "label": "Claude Code（原生模型角色）",
        "strategy": "偏规划与审阅，强调结构化推理、风险识别与决策依据。",
    },
    "gemini": {
        "label": "Gemini（原生模型角色）",
        "strategy": "偏大上下文压缩与归纳，强调信息覆盖和多源对齐。",
    },
    "qwen": {
        "label": "Qwen（原生模型角色）",
        "strategy": "偏中文科研表达与结构化写作，强调术语准确与可读性。",
    },
    "goose": {
        "label": "Goose（原生模型角色）",
        "strategy": "偏轻量执行与快速闭环，强调最小可用结果与迭代建议。",
    },
    "custom_acp": {
        "label": "Custom Agent（原生模型角色）",
        "strategy": "按自定义目标组织执行，强调上下文复用与结果回填。",
    },
}


def _normalize_agent_role_id(agent_id: str | None) -> str:
    raw = str(agent_id or "").strip()
    if normalize_agent_backend_id(raw) == DEFAULT_AGENT_BACKEND_ID:
        return "codex"
    return raw or _DEFAULT_AGENT_ID


def _resolve_agent_role(agent_id: str) -> dict[str, str]:
    resolved = _normalize_agent_role_id(agent_id)
    return _AGENT_ROLE_PROFILES.get(resolved, _AGENT_ROLE_PROFILES[_DEFAULT_AGENT_ID])


def supports_multi_agent_project_workflow(workflow_type) -> bool:
    return get_project_workflow_preset(workflow_type) is not None


def submit_multi_agent_project_run(
    run_id: str, *, resume_stage_id: str | None = None
) -> str | None:
    tracker_metadata: dict[str, Any] | None = None
    retry_metadata: dict[str, Any] | None = None
    retry_run_id = run_id
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found")
        if not supports_multi_agent_project_workflow(run.workflow_type):
            return None

        task_id = run.task_id or f"project_multi_run_{run.id.replace('-', '')[:12]}"
        metadata = dict(run.metadata_json or {})
        resolved_resume_stage_id = (
            str(resume_stage_id or checkpoint_resume_stage(metadata) or "").strip() or None
        )
        orchestration = build_run_orchestration(
            run.workflow_type,
            metadata.get("orchestration"),
            target_id=run.target_id,
            workspace_server_id=run.workspace_server_id,
            reset_stage_status=not bool(resolved_resume_stage_id),
        )
        metadata.update(
            {
                "executor": "project_multi_agent_runner",
                "submitted_at": _iso_now(),
                "orchestration": orchestration,
                "stage_trace": build_stage_trace(
                    orchestration,
                    existing=metadata.get("stage_trace"),
                    reset=not bool(resolved_resume_stage_id),
                ),
            }
        )
        metadata.pop("pending_checkpoint", None)
        if resolved_resume_stage_id:
            metadata["checkpoint_resume_stage_id"] = resolved_resume_stage_id
        else:
            metadata["stage_outputs"] = {}
            metadata.pop("checkpoint_resume_stage_id", None)
            metadata.pop("checkpoint_resume_stage_label", None)
        project_repo.update_run(
            run.id,
            task_id=task_id,
            status=ProjectRunStatus.running,
            active_phase=resolved_resume_stage_id or "initializing",
            summary="多智能体工作流恢复中，正在准备继续执行。"
            if resolved_resume_stage_id
            else "多智能体工作流已启动（原生后端模式），正在准备阶段上下文。",
            started_at=run.started_at or datetime.now(UTC),
            finished_at=None,
            metadata=metadata,
        )
        title = run.title or run.workflow_type.value
        workflow_type_value = run.workflow_type.value
        tracker_metadata = {
            "source": "project",
            "source_id": str(run.id),
            "project_id": str(run.project_id),
            "run_id": str(run.id),
            "log_path": run.log_path,
            "workspace_server_id": run.workspace_server_id or "local",
            "workspace_path": run.run_directory or run.remote_workdir or run.workdir,
            "run_directory": run.run_directory,
            "executor_model": getattr(run, "executor_model", None),
            "reviewer_model": run.reviewer_model,
            "remote_session_name": metadata.get("remote_session_name"),
            "remote_execution_workspace": metadata.get("remote_execution_workspace"),
            "remote_isolation_mode": metadata.get("remote_isolation_mode"),
            "checkpoint_resume_stage_id": resolved_resume_stage_id,
            "retry_label": "重新运行",
            "retry_metadata": {
                "project_id": str(run.project_id),
                "run_id": str(run.id),
                "workflow_type": workflow_type_value,
            },
        }
        retry_metadata = {
            "project_id": str(run.project_id),
            "run_id": str(run.id),
            "workflow_type": workflow_type_value,
        }
        retry_run_id = str(run.id)

    global_tracker.submit(
        "project_multi_agent_workflow",
        title,
        run_multi_agent_project_workflow,
        run_id,
        task_id=task_id,
        total=_TOTAL_PROGRESS,
        metadata=tracker_metadata or {},
    )
    global_tracker.register_retry(
        task_id,
        lambda: submit_multi_agent_project_run(retry_run_id),
        label="重新运行",
        metadata=retry_metadata or {},
    )
    return task_id


def run_multi_agent_project_workflow(
    run_id: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    context = _load_context(run_id)
    resume_stage_id = str(checkpoint_resume_stage(context.metadata) or "").strip() or None
    _ensure_run_orchestration(run_id, context, reset_stage_status=not bool(resume_stage_id))
    if resume_stage_id:
        _patch_run(
            run_id,
            status=ProjectRunStatus.running,
            active_phase=resume_stage_id,
            summary=f"已批准继续，正在恢复阶段：{resume_stage_id}。",
            started_at=context.run.started_at or datetime.now(UTC),
            finished_at=None,
            metadata_updates={
                "error": None,
                "checkpoint_resume_stage_id": resume_stage_id,
            },
        )
        _emit_progress(progress_callback, f"正在恢复阶段：{resume_stage_id}。", 6)
    else:
        _patch_run(
            run_id,
            status=ProjectRunStatus.running,
            active_phase="prepare_context",
            summary="正在准备多智能体工作流上下文。",
            started_at=datetime.now(UTC),
            finished_at=None,
            metadata_updates={
                "error": None,
                "stage_outputs": {},
            },
        )
        _emit_progress(progress_callback, "正在准备多智能体工作流上下文。", 6)

    try:
        return _execute_multi_agent_workflow(context, progress_callback)
    except TaskPausedError:
        raise
    except TaskCancelledError:
        _cancel_active_stage(run_id, "任务已终止")
        _patch_run(
            run_id,
            status=ProjectRunStatus.cancelled,
            active_phase="cancelled",
            summary="工作流已取消。",
            finished_at=datetime.now(UTC),
            metadata_updates={"error": "任务已终止"},
        )
        raise
    except Exception as exc:
        logger.exception("Project multi-agent workflow failed: %s", run_id)
        _fail_active_stage(run_id, str(exc))
        _patch_run(
            run_id,
            status=ProjectRunStatus.failed,
            active_phase="failed",
            summary=f"工作流执行失败：{str(exc)[:180]}",
            finished_at=datetime.now(UTC),
            metadata_updates={
                "error": str(exc),
                "failed_at": _iso_now(),
            },
        )
        raise


def _execute_multi_agent_workflow(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    run_state = _load_run_state(context.run.id)
    resume_stage_id = (
        str(checkpoint_resume_stage(run_state.get("metadata") or {}) or "").strip() or None
    )
    orchestration = build_run_orchestration(
        context.run.workflow_type,
        run_state.get("metadata", {}).get("orchestration"),
        target_id=run_state.get("target_id"),
        workspace_server_id=run_state.get("workspace_server_id"),
    )
    stages = [stage for stage in (orchestration.get("stages") or []) if isinstance(stage, dict)]
    if not stages:
        raise ValueError("当前 workflow 没有可执行阶段")

    stage_outputs = dict(run_state.get("metadata", {}).get("stage_outputs") or {})

    resume_started = not bool(resume_stage_id)
    for index, stage in enumerate(stages, start=1):
        _raise_if_cancel_requested(context)
        stage_id = str(stage.get("id") or f"stage_{index}")
        if not resume_started:
            if stage_id != resume_stage_id:
                continue
            resume_started = True
        stage_label = str(stage.get("label") or stage_id)
        agent_id = (
            str(stage.get("selected_agent_id") or stage.get("default_agent_id") or "").strip()
            or _DEFAULT_AGENT_ID
        )
        agent_role = _resolve_agent_role(agent_id)

        workspace_server_id, workspace_path = _resolve_stage_workspace(run_state, context, stage)
        progress_start, progress_done = _stage_progress_window(index, len(stages))
        running_message = f"{stage_label} 正在通过 {agent_role['label']} 执行。"
        _patch_run(
            context.run.id,
            active_phase=stage_id,
            summary=running_message,
        )
        _set_stage_state(
            context.run.id,
            stage_id,
            status="running",
            message=running_message,
            progress_pct=progress_start,
        )
        _emit_progress(progress_callback, running_message, progress_start)

        prompt = _build_stage_prompt(
            context,
            stage,
            stage_outputs,
            workspace_path=workspace_path,
            agent_id=agent_id,
        )
        execution = _maybe_execute_local_init_repo_scaffold(
            context,
            stage,
            stage_outputs,
            workspace_path=workspace_path,
            workspace_server_id=workspace_server_id,
        )
        if execution is None:
            execution = _maybe_execute_autoresearch_stage(
                context,
                stage,
                workspace_path=workspace_path,
                workspace_server_id=workspace_server_id,
            )
        if execution is None:
            execution = _maybe_execute_sync_workspace_stage(
                context,
                stage,
                workspace_path=workspace_path,
                workspace_server_id=workspace_server_id,
            )
        if execution is None:
            execution = _maybe_execute_experiment_audit_stage(
                context,
                stage,
                stage_outputs,
                workspace_path=workspace_path,
            )
        if execution is None:
            execution = _maybe_execute_monitor_stage(
                context,
                stage,
                workspace_path=workspace_path,
                workspace_server_id=workspace_server_id,
            )
        if execution is None:
            execution = _maybe_execute_paper_compile_stage(
                context,
                stage,
                workspace_path=workspace_path,
            )
        if execution is None:
            execution = _execute_native_stage(context, stage, prompt, agent_id=agent_id)
        content = str(execution.get("content") or "").strip()
        if not content:
            raise RuntimeError(f"阶段 {stage_label} 没有返回有效结果")

        stage_outputs[stage_id] = {
            "stage_id": stage_id,
            "label": stage_label,
            "agent_id": agent_id,
            "engine_id": execution.get("engine_id"),
            "engine_label": execution.get("engine_label"),
            "execution_target": stage.get("execution_target"),
            "model_role": execution.get("model_role") or stage.get("model_role") or "executor",
            "model_source": execution.get("model_source") or execution.get("provider"),
            "workspace_path": workspace_path,
            "workspace_server_id": workspace_server_id,
            "content": content,
            "summary": _markdown_excerpt(content),
            "provider": execution.get("provider"),
            "model": execution.get("model") or execution.get("default_model"),
            "variant": execution.get("variant"),
            "base_url": execution.get("base_url"),
            "default_model": execution.get("default_model"),
            "command": execution.get("command"),
            "command_path": execution.get("command_path"),
            "duration_ms": execution.get("duration_ms"),
            "stdout": execution.get("stdout"),
            "stderr": execution.get("stderr"),
            "artifact_refs": list(execution.get("artifact_refs") or []),
            "completed_at": _iso_now(),
        }
        _record_stage_output(context.run.id, stage_id, stage_outputs[stage_id])
        _patch_run(
            context.run.id,
            metadata_updates={
                "stage_outputs": stage_outputs,
                "last_agent_run": stage_outputs[stage_id],
            },
        )
        _set_stage_state(
            context.run.id,
            stage_id,
            status="completed",
            message=f"{stage_label} 已完成。",
            progress_pct=progress_done,
        )
        _emit_progress(progress_callback, f"{stage_label} 已完成。", progress_done)
        next_stage_id = None
        if index < len(stages):
            next_candidate = stages[index]
            next_stage_id = str(next_candidate.get("id") or "").strip() or None
        _maybe_pause_after_stage(
            context,
            stage_id,
            next_stage_id,
            stage_summary=_markdown_excerpt(content),
        )

    if resume_stage_id and not resume_started:
        raise RuntimeError(f"恢复多智能体 workflow 失败：未找到阶段 {resume_stage_id}")

    return _finalize_multi_agent_workflow(context, stage_outputs, progress_callback)


def _finalize_multi_agent_workflow(
    context: WorkflowContext,
    stage_outputs: dict[str, Any],
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    run_state = _load_run_state(context.run.id)
    workflow_markdown = _build_workflow_output_markdown(context, stage_outputs)
    artifact_refs = _collect_stage_artifact_refs(stage_outputs)
    materialized = _materialize_workflow_artifacts(
        context,
        stage_outputs,
        fallback_markdown=workflow_markdown,
    )
    workflow_markdown = str(
        materialized.get("workflow_output_markdown") or workflow_markdown
    ).strip()
    workflow_markdown = sanitize_project_markdown(workflow_markdown)
    artifact_refs.extend(materialized.get("artifact_refs") or [])
    summary = _markdown_excerpt(workflow_markdown)
    report_artifact = _write_workflow_report_artifact(context, workflow_markdown)
    if report_artifact:
        artifact_refs.append(report_artifact)
    if not artifact_refs:
        artifact_refs = _collect_run_artifacts(context)
    metadata_updates: dict[str, Any] = {
        "stage_outputs": stage_outputs,
        "workflow_output_markdown": workflow_markdown,
        "workflow_output_excerpt": summary,
        "completed_at": _iso_now(),
    }
    metadata_updates.update(materialized.get("metadata_updates") or {})
    result: dict[str, Any] = {
        "run_id": context.run.id,
        "workflow_type": context.run.workflow_type.value,
        "summary": summary,
        "markdown": workflow_markdown,
    }
    result.update(materialized.get("result_updates") or {})
    if artifact_refs:
        metadata_updates["artifact_refs"] = artifact_refs
        result["artifact_refs"] = artifact_refs

    if context.run.workflow_type.value == "literature_review":
        final_text = str(
            stage_outputs.get("deliver_review", {}).get("content") or workflow_markdown
        )
        markdown = _resolve_literature_markdown(context, LLMResult(content=final_text))
        metadata_updates["workflow_output_markdown"] = markdown
        metadata_updates["workflow_output_excerpt"] = _markdown_excerpt(markdown)
        if context.selected_papers:
            with session_scope() as session:
                generated = GeneratedContentRepository(session).create(
                    content_type="project_literature_review",
                    title=f"{context.project.name} 文献综述",
                    markdown=markdown,
                    keyword=context.project.name,
                    paper_id=context.selected_papers[0].id,
                    metadata_json={
                        "project_id": context.project.id,
                        "run_id": context.run.id,
                        "workflow_type": context.run.workflow_type.value,
                    },
                )
                metadata_updates["generated_content_id"] = generated.id
                result["generated_content_id"] = generated.id
        result["markdown"] = markdown
        result["summary"] = metadata_updates["workflow_output_excerpt"]

    if context.run.workflow_type.value == "idea_discovery":
        final_text = str(
            stage_outputs.get("expand_directions", {}).get("content")
            or stage_outputs.get("rank_and_persist", {}).get("content")
            or ""
        )
        parsed = _extract_stage_json(final_text)
        llm_result = LLMResult(content=final_text, parsed_json=parsed)
        ideas_payload = _resolve_idea_payloads(context, llm_result)
        created_ideas: list[dict[str, Any]] = []
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            for item in ideas_payload:
                idea = project_repo.create_idea(
                    project_id=context.project.id,
                    title=str(item["title"])[:512],
                    content=str(item["content"]),
                    paper_ids=list(item.get("paper_ids") or []),
                )
                created_ideas.append(
                    {
                        "id": idea.id,
                        "title": idea.title,
                        "paper_ids": list(idea.paper_ids_json or []),
                    }
                )
        metadata_updates["created_ideas"] = created_ideas
        metadata_updates["created_idea_ids"] = [item["id"] for item in created_ideas]
        metadata_updates["workflow_output_excerpt"] = f"已生成 {len(created_ideas)} 条研究想法。"
        result["created_ideas"] = created_ideas
        result["summary"] = metadata_updates["workflow_output_excerpt"]

    _patch_run(
        context.run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=str(metadata_updates.get("workflow_output_excerpt") or summary),
        finished_at=datetime.now(UTC),
        metadata_updates=metadata_updates,
    )
    _emit_progress(progress_callback, "项目 workflow 已完成。", 100)

    task_id = str(run_state.get("task_id") or context.run.task_id or "").strip()
    if task_id:
        global_tracker.set_result(task_id, result)
    return result


def _load_run_state(run_id: str) -> dict[str, Any]:
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found")
        return {
            "id": run.id,
            "task_id": run.task_id,
            "target_id": run.target_id,
            "workflow_type": run.workflow_type,
            "workspace_server_id": run.workspace_server_id,
            "workdir": run.workdir,
            "remote_workdir": run.remote_workdir,
            "metadata": dict(run.metadata_json or {}),
        }


def _stage_progress_window(index: int, total: int) -> tuple[int, int]:
    start = 10 + int(((index - 1) / max(total, 1)) * 72)
    done = 18 + int((index / max(total, 1)) * 72)
    return max(8, min(start, 92)), max(12, min(done, 96))


def _raise_if_cancel_requested(context: WorkflowContext) -> None:
    task_id = str(context.run.task_id or "").strip()
    if task_id and global_tracker.is_cancel_requested(task_id):
        raise TaskCancelledError("任务已终止")


def _resolve_stage_workspace(
    run_state: dict[str, Any],
    context: WorkflowContext,
    stage: dict[str, Any],
) -> tuple[str | None, str]:
    execution_target = str(stage.get("execution_target") or "workspace_target").strip().lower()
    remote_server_id = clean_text(run_state.get("workspace_server_id")) or None
    local_workdir = clean_text(run_state.get("workdir")) or None
    remote_workdir = clean_text(run_state.get("remote_workdir")) or None

    if execution_target == "ssh":
        if not remote_server_id or not remote_workdir:
            raise ValueError("当前阶段要求 SSH 执行，但项目未配置远程工作区")
        return remote_server_id, remote_workdir

    if execution_target == "workspace_target" and remote_server_id and remote_workdir:
        return remote_server_id, remote_workdir

    if local_workdir:
        return None, local_workdir

    for repo in context.selected_repos:
        if repo.local_path and Path(repo.local_path).exists():
            return None, repo.local_path

    return None, str(Path.cwd())


def _build_local_init_repo_files(
    context: WorkflowContext,
    stage_outputs: dict[str, Any],
) -> dict[str, str]:
    project_name = context.project.name.strip() or "Research Project"
    prompt = context.run.prompt.strip() or "为当前研究方向建立最小可用仓库脚手架。"
    plan_excerpt = str(stage_outputs.get("plan_repo", {}).get("summary") or "").strip()
    return {
        "README.md": "\n".join(
            [
                f"# {project_name}",
                "",
                "## 目标",
                prompt,
                "",
                "## 当前仓库结构",
                "- `src/`: 最小运行入口",
                "- `scripts/`: 本地 smoke 与辅助脚本",
                "- `experiments/`: 实验记录占位",
                "- `docs/`: 设计说明与后续扩展",
                "- `configs/`: 配置样例与说明",
                "- `data/`: 数据目录占位",
                "- `outputs/`: 输出目录占位",
                "",
                "## 最小运行方式",
                "1. 使用 PowerShell 7 执行 `./scripts/run_smoke.ps1`",
                "2. 或直接执行 `python src/main.py`",
                "",
                "## 后续建议",
                "- 在 `configs/` 中补充实验配置",
                "- 在 `experiments/` 中记录每轮实验设计与结果",
                "- 按需要把真实训练/评测脚本拆分到 `src/` 与 `scripts/`",
                "",
                "## 规划摘录",
                plan_excerpt or "当前阶段使用最小可运行模板完成初始化。",
                "",
            ]
        ),
        ".gitignore": "\n".join(
            [
                "__pycache__/",
                ".pytest_cache/",
                ".ruff_cache/",
                ".venv/",
                "venv/",
                "data/*",
                "!data/.gitkeep",
                "outputs/*",
                "!outputs/.gitkeep",
                "",
            ]
        ),
        "src/main.py": "\n".join(
            [
                '"""Minimal entrypoint for the initialized ResearchOS project."""',
                "",
                "from pathlib import Path",
                "",
                "",
                "def main() -> None:",
                f"    project_name = {project_name!r}",
                "    root = Path(__file__).resolve().parents[1]",
                '    print(f"{project_name} scaffold is ready.")',
                '    print(f"Workspace: {root}")',
                '    print("Next step: replace this placeholder with your experiment pipeline.")',
                "",
                "",
                'if __name__ == "__main__":',
                "    main()",
                "",
            ]
        ),
        "scripts/run_smoke.ps1": "\n".join(
            [
                "$root = Split-Path -Parent $PSScriptRoot",
                "Set-Location $root",
                "python ./src/main.py",
                "",
            ]
        ),
        "experiments/README.md": "# Experiments\n\n记录实验设计、运行参数、结果摘要与结论。\n",
        "docs/README.md": "# Docs\n\n存放项目说明、方法设计、数据流程和里程碑文档。\n",
        "configs/README.md": "# Configs\n\n在这里放置模型、数据与评测配置样例。\n",
        "data/.gitkeep": "",
        "outputs/.gitkeep": "",
    }


def _local_init_repo_tree_lines() -> list[str]:
    return [
        ".gitignore",
        "README.md",
        "configs/",
        "configs/README.md",
        "data/",
        "data/.gitkeep",
        "docs/",
        "docs/README.md",
        "experiments/",
        "experiments/README.md",
        "outputs/",
        "outputs/.gitkeep",
        "scripts/",
        "scripts/run_smoke.ps1",
        "src/",
        "src/main.py",
    ]


def _maybe_execute_local_init_repo_scaffold(
    context: WorkflowContext,
    stage: dict[str, Any],
    stage_outputs: dict[str, Any],
    *,
    workspace_path: str,
    workspace_server_id: str | None,
) -> dict[str, Any] | None:
    if context.run.workflow_type.value != "init_repo":
        return None
    if workspace_server_id:
        return None

    stage_id = str(stage.get("id") or "").strip()
    if stage_id not in {"plan_repo", "create_scaffold", "validate_bootstrap"}:
        return None

    workspace_dir = Path(workspace_path).expanduser().resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    files = _build_local_init_repo_files(context, stage_outputs)
    tree_lines = _local_init_repo_tree_lines()

    if stage_id == "plan_repo":
        content = "\n".join(
            [
                "## repository_layout",
                "- `src/`: 最小入口与后续核心逻辑",
                "- `scripts/`: PowerShell 7 辅助脚本",
                "- `experiments/`: 实验记录占位",
                "- `docs/`: 设计与路线说明",
                "- `configs/`: 配置样例",
                "- `data/`: 数据目录占位",
                "- `outputs/`: 结果输出占位",
                "",
                "## seed_files",
                *[f"- `{item}`" for item in files],
                "",
                "## bootstrap_sequence",
                "1. 先写 README 与 .gitignore 明确约束。",
                "2. 再写 `src/main.py` 和 `scripts/run_smoke.ps1` 建立最小运行链路。",
                "3. 最后补齐 `experiments/`、`docs/`、`configs/`、`data/`、`outputs/` 占位内容。",
                "",
                "## note",
                "本地 init_repo 阶段采用稳定的最小模板规划，避免开放式 CLI 规划导致长时间悬挂。",
            ]
        )
    elif stage_id == "create_scaffold":
        created_paths: list[str] = []
        for relative_path, content_value in files.items():
            write_workspace_file(
                str(workspace_dir),
                relative_path,
                content_value,
                create_dirs=True,
                overwrite=True,
            )
            created_paths.append(relative_path)
        content = "\n".join(
            [
                "## created_paths",
                *[f"- `{item}`" for item in created_paths],
                "",
                "## entrypoint",
                "- `python src/main.py`",
                "- `pwsh ./scripts/run_smoke.ps1`",
                "",
                "## how_to_run",
                "1. 进入当前工作区。",
                "2. 使用 PowerShell 7 运行 `./scripts/run_smoke.ps1` 验证脚手架。",
                "3. 按项目需要补充真实实验逻辑、配置与文档。",
                "",
                "## workspace_tree",
                *[f"- {line}" for line in tree_lines],
                "",
                "## note",
                "当前阶段采用 ResearchOS 的本地确定性脚手架回填，以保证 init_repo 在本地工作区稳定落盘。",
            ]
        )
    else:
        checked_lines: list[str] = []
        repaired_paths: list[str] = []
        for relative_path, content_value in files.items():
            target = workspace_dir / Path(relative_path)
            if target.exists():
                checked_lines.append(f"- [x] `{relative_path}`")
                continue
            write_workspace_file(
                str(workspace_dir),
                relative_path,
                content_value,
                create_dirs=True,
                overwrite=True,
            )
            repaired_paths.append(relative_path)
            checked_lines.append(f"- [x] `{relative_path}` (已补齐)")
        content = "\n".join(
            [
                "## checklist",
                *checked_lines,
                "",
                "## repaired_paths",
                *([f"- `{item}`" for item in repaired_paths] or ["- 无"]),
                "",
                "## run_commands",
                "- `python src/main.py`",
                "- `pwsh ./scripts/run_smoke.ps1`",
                "",
                "## note",
                "本地 init_repo 验证阶段采用确定性检查，确保关键文件与目录真实存在。",
            ]
        )

    return {
        "agent_type": "researchos_init_repo_local",
        "label": "ResearchOS Local InitRepo",
        "provider": "researchos_init_repo_local",
        "base_url": None,
        "default_model": None,
        "command": "write_workspace_file",
        "command_path": None,
        "duration_ms": None,
        "stdout": "",
        "stderr": "",
        "content": content,
        "parsed": None,
    }


def _build_autoresearch_bootstrap_files(context: WorkflowContext) -> dict[str, str]:
    project_name = context.project.name.strip() or "Research Project"
    goal = (
        context.run.prompt.strip()
        or "Define first baseline and iterate with measurable improvements."
    )
    session_payload = {
        "framework": "autoresearch-claude-code",
        "project_name": project_name,
        "goal": goal,
        "workflow_type": context.run.workflow_type.value,
        "status": "initialized",
        "iteration": 0,
        "created_at": _iso_now(),
    }
    return {
        "autoresearch/README.md": "\n".join(
            [
                "# AutoResearch Session",
                "",
                "This workspace uses a lightweight `autoresearch-claude-code` style loop:",
                "1. Bootstrap session files",
                "2. Run baseline script",
                "3. Propose next iterations",
                "",
                "## Baseline Command",
                "- `python ./scripts/autoresearch_baseline.py`",
                "",
                "## Outputs",
                "- `autoresearch/reports/baseline_report.md`",
                "- `autoresearch/reports/baseline_metrics.json`",
                "",
            ]
        ),
        "autoresearch/session.json": json.dumps(session_payload, ensure_ascii=False, indent=2)
        + "\n",
        "scripts/autoresearch_baseline.py": "\n".join(
            [
                '"""Deterministic baseline script for ResearchOS AutoResearch workflow."""',
                "",
                "from __future__ import annotations",
                "",
                "import json",
                "from datetime import datetime, timezone",
                "from pathlib import Path",
                "",
                "ROOT = Path(__file__).resolve().parents[1]",
                "SESSION_PATH = ROOT / 'autoresearch' / 'session.json'",
                "REPORT_PATH = ROOT / 'autoresearch' / 'reports' / 'baseline_report.md'",
                "METRICS_PATH = ROOT / 'autoresearch' / 'reports' / 'baseline_metrics.json'",
                "",
                "",
                "def main() -> None:",
                "    session = {}",
                "    if SESSION_PATH.exists():",
                "        session = json.loads(SESSION_PATH.read_text(encoding='utf-8'))",
                "    now = datetime.now(timezone.utc).isoformat()",
                "    project_name = str(session.get('project_name') or 'Research Project')",
                "    goal = str(session.get('goal') or 'Define first baseline and iterate with measurable improvements.')",
                "",
                "    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)",
                "    report_lines = [",
                "        f'# {project_name} · AutoResearch Baseline',",
                "        '',",
                "        '## Goal',",
                "        goal,",
                "        '',",
                "        '## Baseline Status',",
                "        '- status: completed',",
                "        f'- generated_at_utc: {now}',",
                "        '',",
                "        '## Next',",
                "        '- Compare baseline against one stronger variant.',",
                "        '- Attach error analysis samples before next iteration.',",
                "    ]",
                "    REPORT_PATH.write_text('\\n'.join(report_lines) + '\\n', encoding='utf-8')",
                "",
                "    metrics = {",
                "        'generated_at_utc': now,",
                "        'status': 'completed',",
                "        'baseline_score': 0.0,",
                "        'notes': 'Replace placeholder metrics with real experiment outputs.',",
                "    }",
                "    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')",
                "",
                "    session['last_baseline_run_at'] = now",
                "    session['last_baseline_report'] = str(REPORT_PATH.relative_to(ROOT).as_posix())",
                "    SESSION_PATH.write_text(json.dumps(session, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')",
                "",
                "    print(str(REPORT_PATH.relative_to(ROOT).as_posix()))",
                "    print(str(METRICS_PATH.relative_to(ROOT).as_posix()))",
                "",
                "",
                "if __name__ == '__main__':",
                "    main()",
                "",
            ]
        ),
        "autoresearch/iterations/.gitkeep": "",
        "autoresearch/reports/.gitkeep": "",
    }


def _autoresearch_local_commands() -> list[str]:
    return [
        "python ./scripts/autoresearch_baseline.py",
        "python3 ./scripts/autoresearch_baseline.py",
        "py ./scripts/autoresearch_baseline.py",
    ]


def _maybe_execute_autoresearch_stage(
    context: WorkflowContext,
    stage: dict[str, Any],
    *,
    workspace_path: str,
    workspace_server_id: str | None,
) -> dict[str, Any] | None:
    if context.run.workflow_type.value != "autoresearch_claude_code":
        return None

    stage_id = str(stage.get("id") or "").strip()
    if stage_id not in {"bootstrap_session", "run_baseline"}:
        return None

    if stage_id == "bootstrap_session":
        files = _build_autoresearch_bootstrap_files(context)
        if workspace_server_id:
            content = "\n".join(
                [
                    "## remote_workspace_notice",
                    "当前项目绑定的是 SSH 工作区，ResearchOS 暂不直接写入远程模板文件。",
                    "",
                    "## files_to_prepare",
                    *[f"- `{item}`" for item in files],
                    "",
                    "## run_command",
                    "- `python ./scripts/autoresearch_baseline.py`",
                    "",
                    "## note",
                    "切回本地工作区可自动落盘以上文件；远程场景建议先同步模板再执行基线。",
                ]
            )
            return {
                "agent_type": "researchos_autoresearch_remote_hint",
                "label": "ResearchOS AutoResearch Remote Hint",
                "provider": "researchos_autoresearch_remote_hint",
                "base_url": None,
                "default_model": None,
                "command": None,
                "command_path": workspace_path,
                "duration_ms": None,
                "stdout": "",
                "stderr": "",
                "content": content,
                "parsed": None,
            }

        workspace_dir = Path(workspace_path).expanduser().resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        created_paths: list[str] = []
        for relative_path, content_value in files.items():
            write_workspace_file(
                str(workspace_dir),
                relative_path,
                content_value,
                create_dirs=True,
                overwrite=True,
            )
            created_paths.append(relative_path)

        content = "\n".join(
            [
                "## created_paths",
                *[f"- `{item}`" for item in created_paths],
                "",
                "## session_file",
                "- `autoresearch/session.json`",
                "",
                "## baseline_command",
                "- `python ./scripts/autoresearch_baseline.py`",
                "",
                "## note",
                "已完成 AutoResearch 会话初始化，可进入下一阶段运行基线命令。",
            ]
        )
        return {
            "agent_type": "researchos_autoresearch_local",
            "label": "ResearchOS AutoResearch Local Bootstrap",
            "provider": "researchos_autoresearch_local",
            "base_url": None,
            "default_model": None,
            "command": "write_workspace_file",
            "command_path": str(workspace_dir),
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": content,
            "parsed": None,
        }

    if workspace_server_id:
        content = "\n".join(
            [
                "## remote_execution_notice",
                "当前阶段需要运行本地命令，SSH 工作区请先在远程手动执行以下命令：",
                "",
                "## command",
                "- `python ./scripts/autoresearch_baseline.py`",
                "",
                "## expected_outputs",
                "- `autoresearch/reports/baseline_report.md`",
                "- `autoresearch/reports/baseline_metrics.json`",
            ]
        )
        return {
            "agent_type": "researchos_autoresearch_remote_hint",
            "label": "ResearchOS AutoResearch Remote Hint",
            "provider": "researchos_autoresearch_remote_hint",
            "base_url": None,
            "default_model": None,
            "command": "python ./scripts/autoresearch_baseline.py",
            "command_path": workspace_path,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": content,
            "parsed": None,
        }

    workspace_dir = Path(workspace_path).expanduser().resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    command_result: dict[str, Any] | None = None

    for command in _autoresearch_local_commands():
        result = run_workspace_command(str(workspace_dir), command, timeout_sec=180)
        attempts.append(result)
        if bool(result.get("success")):
            command_result = result
            break
    if command_result is None and attempts:
        command_result = attempts[-1]

    if command_result is None:
        command_result = {
            "command": "python ./scripts/autoresearch_baseline.py",
            "exit_code": None,
            "stdout": "",
            "stderr": "AutoResearch baseline command did not execute.",
            "success": False,
        }

    attempted_commands = [
        f"- `{item.get('command')}` => {'success' if item.get('success') else 'failed'} (exit_code={item.get('exit_code')})"
        for item in attempts
    ]
    success = bool(command_result.get("success"))
    content_lines = [
        "## baseline_command_result",
        f"- success: {'true' if success else 'false'}",
        f"- command: `{command_result.get('command')}`",
        f"- exit_code: {command_result.get('exit_code')}",
        "",
        "## attempts",
        *(attempted_commands or ["- 无"]),
        "",
    ]
    if success:
        content_lines.extend(
            [
                "## outputs",
                "- `autoresearch/reports/baseline_report.md`",
                "- `autoresearch/reports/baseline_metrics.json`",
                "",
                "## note",
                "基线命令已执行完成，可进入下一阶段生成迭代计划。",
            ]
        )
    else:
        content_lines.extend(
            [
                "## fallback",
                "自动执行失败，请在工作区手动运行以下命令并检查 Python 环境：",
                "- `python ./scripts/autoresearch_baseline.py`",
            ]
        )

    return {
        "agent_type": "researchos_autoresearch_local",
        "label": "ResearchOS AutoResearch Baseline",
        "provider": "researchos_autoresearch_local",
        "base_url": None,
        "default_model": None,
        "command": command_result.get("command"),
        "command_path": str(workspace_dir),
        "duration_ms": None,
        "stdout": str(command_result.get("stdout") or ""),
        "stderr": str(command_result.get("stderr") or ""),
        "content": "\n".join(content_lines),
        "parsed": None,
    }


def _collect_stage_artifact_refs(stage_outputs: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    collected: list[dict[str, Any]] = []
    for item in stage_outputs.values():
        if not isinstance(item, dict):
            continue
        for artifact in item.get("artifact_refs") or []:
            if not isinstance(artifact, dict):
                continue
            key = (str(artifact.get("kind") or ""), str(artifact.get("path") or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            collected.append(dict(artifact))
    return collected


def _write_workflow_report_artifact(
    context: WorkflowContext,
    workflow_markdown: str,
) -> dict[str, Any] | None:
    workflow_type = context.run.workflow_type.value
    if workflow_type not in _REPORT_ARTIFACT_WORKFLOWS:
        return None
    relative_path = f"reports/{workflow_type.replace('_', '-')}.md"
    return _write_run_artifact(
        context, relative_path, workflow_markdown.rstrip() + "\n", kind="report"
    )


def _materialize_workflow_artifacts(
    context: WorkflowContext,
    stage_outputs: dict[str, Any],
    *,
    fallback_markdown: str,
) -> dict[str, Any]:
    workflow_type = context.run.workflow_type.value
    venue, template_name = resolve_paper_venue(context.metadata)
    paper_summaries = [paper.title for paper in context.selected_papers]

    if workflow_type == "paper_plan":
        bundle = build_paper_plan_bundle(
            project_name=context.project.name,
            project_description=context.project.description or "",
            prompt=context.run.prompt,
            stage_markdown=_paper_stage_markdown(
                stage_outputs, "outline_manuscript", "collect_materials"
            ),
            paper_summaries=paper_summaries,
            venue=venue,
            template_name=template_name,
        )
        refs = _write_bundle_artifacts(context, bundle)
        return {
            "workflow_output_markdown": bundle.get("reports/PAPER_PLAN.md") or fallback_markdown,
            "artifact_refs": refs,
            "metadata_updates": {
                "paper_venue": venue,
                "paper_template": template_name,
            },
        }

    if workflow_type == "paper_figure":
        bundle = build_figure_bundle(
            project_name=context.project.name,
            prompt=context.run.prompt,
            stage_markdown=_paper_stage_markdown(
                stage_outputs, "design_figures", "collect_results"
            ),
            venue=venue,
        )
        refs = _write_bundle_artifacts(context, bundle)
        return {
            "workflow_output_markdown": bundle.get("figures/FIGURE_PLAN.md") or fallback_markdown,
            "artifact_refs": refs,
            "metadata_updates": {
                "paper_venue": venue,
                "paper_template": template_name,
            },
        }

    if workflow_type == "paper_write":
        bundle = build_paper_write_bundle(
            project_name=context.project.name,
            project_description=context.project.description or "",
            prompt=context.run.prompt,
            stage_markdown=_paper_stage_markdown(
                stage_outputs, "draft_sections", "gather_materials"
            ),
            venue=venue,
            template_name=template_name,
            paper_titles=paper_summaries,
        )
        write_summary = "\n".join(
            [
                "# PAPER_WRITE",
                "",
                f"- Venue: {venue}",
                f"- Template: {template_name}",
                "- Generated files:",
                "- `paper/main.tex`",
                "- `paper/sections/*.tex`",
                "- `paper/references.bib`",
                "",
                "## Draft Source",
                _paper_stage_markdown(stage_outputs, "draft_sections", "gather_materials")
                or fallback_markdown,
                "",
            ]
        )
        bundle["reports/PAPER_WRITE.md"] = write_summary
        refs = _write_bundle_artifacts(context, bundle)
        return {
            "workflow_output_markdown": write_summary,
            "artifact_refs": refs,
            "metadata_updates": {
                "paper_venue": venue,
                "paper_template": template_name,
            },
        }

    if workflow_type == "paper_compile":
        compile_stage = dict(stage_outputs.get("run_compile") or {})
        command = str(compile_stage.get("command") or "").strip()
        stdout_text = str(compile_stage.get("stdout") or "")
        stderr_text = str(compile_stage.get("stderr") or "")
        pdf_paths = _discover_pdf_paths(context)
        bundle = build_paper_compile_bundle(
            project_name=context.project.name,
            compile_command=command,
            exit_code=int(compile_stage.get("exit_code"))
            if str(compile_stage.get("exit_code") or "").strip()
            else None,
            pdf_paths=pdf_paths,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
        )
        refs = _write_bundle_artifacts(context, bundle)
        refs.extend(
            item
            for item in (
                _build_existing_file_artifact_ref(context, pdf_path, kind="pdf")
                for pdf_path in pdf_paths
            )
            if item
        )
        return {
            "workflow_output_markdown": bundle.get("reports/PAPER_COMPILE.md") or fallback_markdown,
            "artifact_refs": refs,
            "metadata_updates": {
                "compiled_pdf_paths": pdf_paths,
            },
            "result_updates": {
                "pdf_paths": pdf_paths,
            },
        }

    if workflow_type == "paper_improvement":
        review_round_one = _paper_stage_markdown(stage_outputs, "diagnose_draft")
        revision_notes = _paper_stage_markdown(stage_outputs, "revise_sections")
        review_round_two = _paper_stage_markdown(stage_outputs, "final_check")
        review_round_one_state = parse_review_text(review_round_one)
        review_round_two_state = parse_review_text(review_round_two)
        bundle = build_paper_improvement_bundle(
            project_name=context.project.name,
            review_round_one=review_round_one,
            revision_notes=revision_notes,
            review_round_two=review_round_two,
            score_round_one=review_round_one_state.get("score"),
            score_round_two=review_round_two_state.get("score"),
            verdict_round_one=str(review_round_one_state.get("verdict") or "not ready"),
            verdict_round_two=str(review_round_two_state.get("verdict") or "not ready"),
            action_items_round_one=list(review_round_one_state.get("action_items") or []),
            action_items_round_two=list(review_round_two_state.get("action_items") or []),
        )
        refs = _write_bundle_artifacts(context, bundle)
        return {
            "workflow_output_markdown": bundle.get("reports/paper-score-progression.md")
            or fallback_markdown,
            "artifact_refs": refs,
            "metadata_updates": {
                "paper_improvement_scores": {
                    "round_1": review_round_one_state.get("score"),
                    "round_2": review_round_two_state.get("score"),
                },
                "paper_improvement_verdicts": {
                    "round_1": review_round_one_state.get("verdict"),
                    "round_2": review_round_two_state.get("verdict"),
                },
                "paper_improvement_action_items": {
                    "round_1": list(review_round_one_state.get("action_items") or []),
                    "round_2": list(review_round_two_state.get("action_items") or []),
                },
            },
        }

    if workflow_type == "experiment_audit":
        collect_stage = dict(stage_outputs.get("collect_artifacts") or {})
        review_stage = dict(stage_outputs.get("review_integrity") or {})
        report_stage = dict(stage_outputs.get("issue_audit_report") or {})
        workspace_path = (
            clean_text(collect_stage.get("workspace_path"))
            or clean_text(context.run.workdir)
            or clean_text(context.run.remote_workdir)
        )
        audit_payload = _extract_stage_json(str(review_stage.get("content") or ""))
        if not audit_payload:
            audit_payload = {}
        report_markdown = str(report_stage.get("content") or "").strip()
        if not report_markdown and workspace_path:
            report_markdown = _render_experiment_audit_report(
                context,
                audit_payload=audit_payload,
                workspace_path=workspace_path,
            )
        refs: list[dict[str, Any]] = []
        report_artifact = _write_run_artifact(
            context,
            "EXPERIMENT_AUDIT.md",
            (report_markdown or fallback_markdown).rstrip() + "\n",
            kind="report",
        )
        if report_artifact:
            refs.append(report_artifact)
        if audit_payload:
            json_artifact = _write_run_json_artifact(
                context,
                "EXPERIMENT_AUDIT.json",
                audit_payload,
                kind="artifact",
            )
            if json_artifact:
                refs.append(json_artifact)
        return {
            "workflow_output_markdown": report_markdown or fallback_markdown,
            "artifact_refs": refs,
            "metadata_updates": {
                "audit_payload": audit_payload,
                "integrity_status": audit_payload.get("integrity_status"),
                "overall_verdict": audit_payload.get("overall_verdict"),
                "evaluation_type": audit_payload.get("evaluation_type"),
                "execution_workspace": workspace_path,
            },
            "result_updates": {
                "integrity_status": audit_payload.get("integrity_status"),
                "overall_verdict": audit_payload.get("overall_verdict"),
                "evaluation_type": audit_payload.get("evaluation_type"),
            },
        }

    return {}


def _write_bundle_artifacts(
    context: WorkflowContext,
    bundle: dict[str, str],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for relative_path, content in bundle.items():
        artifact = _write_run_artifact(
            context,
            relative_path,
            content,
            kind="report" if relative_path.lower().endswith(".md") else "artifact",
        )
        if artifact:
            refs.append(artifact)
    return refs


def _build_existing_file_artifact_ref(
    context: WorkflowContext,
    absolute_path: str,
    *,
    kind: str,
) -> dict[str, Any] | None:
    workspace_path = str(context.run.workdir or context.run.remote_workdir or "").strip()
    path_text = str(absolute_path or "").strip()
    if not path_text:
        return None
    if context.run.workspace_server_id:
        normalized_workspace = workspace_path.replace("\\", "/").rstrip("/")
        normalized_path = path_text.replace("\\", "/")
        relative_path = normalized_path
        if normalized_workspace and normalized_path.startswith(f"{normalized_workspace}/"):
            relative_path = normalized_path[len(normalized_workspace) + 1 :]
        return {
            "kind": kind,
            "path": normalized_path,
            "relative_path": relative_path,
        }

    target = Path(path_text)
    if not target.exists() or not target.is_file():
        return None
    relative_path = target.name
    if workspace_path:
        try:
            relative_path = target.relative_to(Path(workspace_path)).as_posix()
        except ValueError:
            relative_path = target.name
    return {
        "kind": kind,
        "path": str(target),
        "relative_path": relative_path,
        "size_bytes": target.stat().st_size,
    }


def _paper_stage_markdown(stage_outputs: dict[str, Any], *stage_ids: str) -> str:
    parts: list[str] = []
    for stage_id in stage_ids:
        item = stage_outputs.get(stage_id) or {}
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(content)
    return "\n\n".join(parts).strip()


def _discover_pdf_paths(context: WorkflowContext) -> list[str]:
    roots: list[str] = []
    for candidate in (context.run.workdir, context.run.run_directory, context.run.remote_workdir):
        value = str(candidate or "").strip()
        if value and value not in roots:
            roots.append(value)
    if not roots:
        return []
    if context.run.workspace_server_id:
        pdf_paths: list[str] = []
        try:
            server_entry = get_workspace_server_entry(context.run.workspace_server_id)
            for root in roots:
                overview = build_remote_overview(server_entry, root, depth=4, max_entries=200)
                workspace_root = str(overview.get("workspace_path") or root).rstrip("/")
                for item in overview.get("files") or []:
                    if not str(item).lower().endswith(".pdf"):
                        continue
                    absolute_path = f"{workspace_root}/{str(item).lstrip('/')}"
                    if absolute_path not in pdf_paths:
                        pdf_paths.append(absolute_path)
                    if len(pdf_paths) >= 12:
                        return pdf_paths[:12]
        except Exception:
            return pdf_paths[:12]
        return pdf_paths[:12]
    pdf_paths: list[str] = []
    for root_value in roots:
        root = Path(root_value)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.pdf")):
            normalized = str(path)
            if normalized in pdf_paths:
                continue
            pdf_paths.append(normalized)
            if len(pdf_paths) >= 12:
                return pdf_paths[:12]
    return pdf_paths[:12]


def _maybe_execute_sync_workspace_stage(
    context: WorkflowContext,
    stage: dict[str, Any],
    *,
    workspace_path: str,
    workspace_server_id: str | None,
) -> dict[str, Any] | None:
    if context.run.workflow_type.value != "sync_workspace":
        return None

    stage_id = str(stage.get("id") or "").strip()
    source_workspace = (
        str(context.metadata.get("project_workspace_path") or "").strip() or workspace_path
    )
    target_workspace = (
        str(context.metadata.get("target_workspace_path") or "").strip() or workspace_path
    )
    source_workspace_server_id = _normalize_server_id(
        context.metadata.get("project_workspace_server_id")
    )
    target_workspace_server_id = _normalize_server_id(
        context.metadata.get("target_workspace_server_id") or workspace_server_id
    )
    sync_strategy = str(context.metadata.get("sync_strategy") or "").strip() or "project_workspace"

    if stage_id == "scan_diff":
        preview_markdown = _build_sync_preview_markdown(
            source_workspace=source_workspace,
            source_workspace_server_id=source_workspace_server_id,
            target_workspace=target_workspace,
            target_workspace_server_id=target_workspace_server_id,
            sync_strategy=sync_strategy,
        )
        return {
            "agent_type": "researchos_sync_preview",
            "label": "ResearchOS Sync Preview",
            "provider": "workspace_sync_preview",
            "base_url": None,
            "default_model": None,
            "model": None,
            "variant": None,
            "command": sync_strategy,
            "command_path": target_workspace,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": preview_markdown,
            "parsed": None,
            "model_role": "executor",
            "model_source": "workspace_sync_preview",
        }

    if stage_id == "sync_paths":
        sync_result = _perform_workspace_sync(
            source_workspace=source_workspace,
            source_workspace_server_id=source_workspace_server_id,
            target_workspace=target_workspace,
            target_workspace_server_id=target_workspace_server_id,
        )
        report_markdown = _build_sync_result_markdown(sync_result)
        artifact_refs: list[dict[str, Any]] = []
        report_artifact = _write_run_artifact(
            context, "reports/sync-report.md", report_markdown, kind="report"
        )
        if report_artifact:
            artifact_refs.append(report_artifact)
        manifest_artifact = _write_run_artifact(
            context,
            "reports/sync-manifest.json",
            json.dumps(sync_result, ensure_ascii=False, indent=2) + "\n",
            kind="artifact",
        )
        if manifest_artifact:
            artifact_refs.append(manifest_artifact)
        return {
            "agent_type": "researchos_sync_execute",
            "label": "ResearchOS Sync Execute",
            "provider": "workspace_sync_executor",
            "base_url": None,
            "default_model": None,
            "model": None,
            "variant": None,
            "command": str(sync_result.get("mode") or sync_strategy),
            "command_path": target_workspace,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": report_markdown,
            "parsed": sync_result,
            "artifact_refs": artifact_refs,
            "model_role": "executor",
            "model_source": "workspace_sync_executor",
        }

    if stage_id == "validate_state":
        validation_markdown = _build_sync_validation_markdown(
            target_workspace=target_workspace,
            workspace_server_id=target_workspace_server_id,
        )
        artifact_refs: list[dict[str, Any]] = []
        validation_artifact = _write_run_artifact(
            context,
            "reports/sync-validation.md",
            validation_markdown,
            kind="report",
        )
        if validation_artifact:
            artifact_refs.append(validation_artifact)
        return {
            "agent_type": "researchos_sync_validate",
            "label": "ResearchOS Sync Validate",
            "provider": "workspace_sync_validator",
            "base_url": None,
            "default_model": None,
            "model": None,
            "variant": None,
            "command": "validate_workspace_state",
            "command_path": target_workspace,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": validation_markdown,
            "parsed": None,
            "artifact_refs": artifact_refs,
            "model_role": "reviewer",
            "model_source": "workspace_sync_validator",
        }
    return None


def _build_sync_preview_markdown(
    *,
    source_workspace: str,
    source_workspace_server_id: str | None,
    target_workspace: str,
    target_workspace_server_id: str | None,
    sync_strategy: str,
) -> str:
    source_files = _collect_sync_files(source_workspace, source_workspace_server_id)
    lines = [
        "# SYNC_PREVIEW",
        "",
        f"- Source Workspace: `{source_workspace or 'N/A'}`",
        f"- Source Server: `{source_workspace_server_id or 'local'}`",
        f"- Target Workspace: `{target_workspace or 'N/A'}`",
        f"- Target Server: `{target_workspace_server_id or 'local'}`",
        f"- Strategy: `{sync_strategy}`",
        f"- Candidate Files: {len(source_files)}",
        "",
        "## Sample Files",
    ]
    if source_files:
        lines.extend(f"- `{item['relative_path']}`" for item in source_files[:12])
    else:
        lines.append("- 未检测到可同步的代码/文档文件。")
    if source_workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(source_workspace_server_id)
            overview = build_remote_overview(
                server_entry, source_workspace, depth=2, max_entries=60
            )
            lines.extend(
                [
                    "",
                    "## Remote Source Snapshot",
                    f"- Exists: {bool(overview.get('exists'))}",
                    f"- Total Entries: {int(overview.get('total_entries') or 0)}",
                    "",
                    str(overview.get("tree") or "").strip() or "(empty)",
                ]
            )
        except Exception as exc:
            lines.extend(["", "## Remote Source Snapshot", f"- 读取失败: {str(exc)}"])
    else:
        source_root = Path(source_workspace).expanduser()
        lines.extend(
            [
                "",
                "## Local Source Snapshot",
                f"- Exists: {source_root.exists()}",
                f"- Is Dir: {source_root.is_dir() if source_root.exists() else False}",
            ]
        )
    if target_workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(target_workspace_server_id)
            overview = build_remote_overview(
                server_entry, target_workspace, depth=2, max_entries=60
            )
            lines.extend(
                [
                    "",
                    "## Remote Target Snapshot",
                    f"- Exists: {bool(overview.get('exists'))}",
                    f"- Total Entries: {int(overview.get('total_entries') or 0)}",
                    "",
                    str(overview.get("tree") or "").strip() or "(empty)",
                ]
            )
        except Exception as exc:
            lines.extend(["", "## Remote Target Snapshot", f"- 读取失败: {str(exc)}"])
    else:
        target = Path(target_workspace)
        lines.extend(
            [
                "",
                "## Local Target Snapshot",
                f"- Exists: {target.exists()}",
                f"- Is Dir: {target.is_dir() if target.exists() else False}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _perform_workspace_sync(
    *,
    source_workspace: str,
    source_workspace_server_id: str | None,
    target_workspace: str,
    target_workspace_server_id: str | None,
) -> dict[str, Any]:
    source_server_id = _normalize_server_id(source_workspace_server_id)
    target_server_id = _normalize_server_id(target_workspace_server_id)
    if not source_server_id and not target_server_id:
        source_root = Path(source_workspace).expanduser()
        try:
            if source_root.resolve() == Path(target_workspace).expanduser().resolve():
                return {
                    "mode": "no_op",
                    "status": "completed",
                    "reason": "源工作区与目标工作区相同，跳过文件复制。",
                    "synced_files": 0,
                    "skipped_files": 0,
                    "source_workspace": str(source_root),
                    "target_workspace": str(Path(target_workspace).expanduser()),
                }
        except Exception:
            pass
    files = _collect_sync_files(source_workspace, source_server_id)
    if not source_server_id:
        source_root = Path(source_workspace).expanduser()
        if not source_root.exists() or not source_root.is_dir():
            return {
                "mode": "skipped",
                "status": "skipped",
                "reason": f"本地源工作区不存在: {source_workspace}",
                "synced_files": 0,
                "skipped_files": 0,
                "target_workspace": target_workspace,
                "workspace_server_id": target_server_id,
            }
        if target_server_id:
            return _sync_local_to_remote(
                source_root=source_root,
                target_workspace=target_workspace,
                workspace_server_id=target_server_id,
                files=files,
            )
        return _sync_local_to_local(
            source_root=source_root,
            target_root=Path(target_workspace).expanduser(),
            files=files,
        )
    if not target_server_id:
        return _sync_remote_to_local(
            source_workspace=source_workspace,
            workspace_server_id=source_server_id,
            target_root=Path(target_workspace).expanduser(),
            files=files,
        )
    return _sync_remote_to_remote(
        source_workspace=source_workspace,
        source_workspace_server_id=source_server_id,
        target_workspace=target_workspace,
        target_workspace_server_id=target_server_id,
        files=files,
    )


def _normalize_server_id(value: Any) -> str | None:
    normalized = clean_text(value)
    return normalized or None


def _collect_sync_files(
    source_workspace: str, workspace_server_id: str | None
) -> list[dict[str, Any]]:
    if workspace_server_id:
        return _collect_remote_sync_files(source_workspace, workspace_server_id)
    return _collect_local_sync_files(source_workspace)


def _sync_local_to_local(
    *,
    source_root: Path,
    target_root: Path,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    target_root.mkdir(parents=True, exist_ok=True)
    synced: list[str] = []
    skipped = 0
    for item in files:
        relative_path = str(item["relative_path"])
        source_path = source_root / relative_path
        destination = target_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        synced.append(relative_path)
    return {
        "mode": "local_copy",
        "status": "completed",
        "source_workspace": str(source_root),
        "source_workspace_server_id": None,
        "target_workspace": str(target_root),
        "target_workspace_server_id": None,
        "synced_files": len(synced),
        "skipped_files": skipped,
        "synced_paths": synced[:80],
    }


def _sync_local_to_remote(
    *,
    source_root: Path,
    target_workspace: str,
    workspace_server_id: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    server_entry = get_workspace_server_entry(workspace_server_id)
    with open_ssh_session(server_entry) as session:
        remote_root = resolve_remote_workspace_path(server_entry, target_workspace, session)
        _ensure_remote_directory(session.sftp, remote_root)

    synced: list[str] = []
    skipped = 0
    for item in files:
        relative_path = str(item["relative_path"])
        source_path = source_root / relative_path
        try:
            payload = remote_upload_file(
                server_entry,
                path=target_workspace,
                relative_path=relative_path,
                filename=source_path.name,
                mime_type=mimetypes.guess_type(source_path.name)[0] or "application/octet-stream",
                content=source_path.read_bytes(),
            )
            if payload.get("relative_path"):
                synced.append(str(payload["relative_path"]))
        except Exception:
            skipped += 1
    return {
        "mode": "local_to_remote",
        "status": "completed",
        "source_workspace": str(source_root),
        "source_workspace_server_id": None,
        "target_workspace": target_workspace,
        "target_workspace_server_id": workspace_server_id,
        "workspace_server_id": workspace_server_id,
        "synced_files": len(synced),
        "skipped_files": skipped,
        "synced_paths": synced[:80],
    }


def _sync_remote_to_local(
    *,
    source_workspace: str,
    workspace_server_id: str,
    target_root: Path,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    server_entry = get_workspace_server_entry(workspace_server_id)
    target_root.mkdir(parents=True, exist_ok=True)
    synced: list[str] = []
    skipped = 0
    with open_ssh_session(server_entry) as session:
        remote_root = resolve_remote_workspace_path(server_entry, source_workspace, session)
        root_attr = remote_stat(session.sftp, remote_root)
        if root_attr is None or not remote_is_dir(root_attr):
            return {
                "mode": "skipped",
                "status": "skipped",
                "reason": f"远程源工作区不存在: {remote_root}",
                "source_workspace": source_workspace,
                "source_workspace_server_id": workspace_server_id,
                "target_workspace": str(target_root),
                "target_workspace_server_id": None,
                "synced_files": 0,
                "skipped_files": 0,
            }
        for item in files:
            relative_path = str(item["relative_path"])
            source_path = posixpath.join(remote_root, relative_path)
            destination = target_root / Path(relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with session.sftp.file(source_path, "rb") as handle:
                    destination.write_bytes(handle.read())
                synced.append(relative_path)
            except Exception:
                skipped += 1
    return {
        "mode": "remote_to_local",
        "status": "completed",
        "source_workspace": source_workspace,
        "source_workspace_server_id": workspace_server_id,
        "target_workspace": str(target_root),
        "target_workspace_server_id": None,
        "synced_files": len(synced),
        "skipped_files": skipped,
        "synced_paths": synced[:80],
    }


def _sync_remote_to_remote(
    *,
    source_workspace: str,
    source_workspace_server_id: str,
    target_workspace: str,
    target_workspace_server_id: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    source_server_entry = get_workspace_server_entry(source_workspace_server_id)
    target_server_entry = get_workspace_server_entry(target_workspace_server_id)
    synced: list[str] = []
    skipped = 0

    if source_workspace_server_id == target_workspace_server_id:
        with open_ssh_session(source_server_entry) as session:
            source_root = resolve_remote_workspace_path(
                source_server_entry, source_workspace, session
            )
            target_root = resolve_remote_workspace_path(
                target_server_entry, target_workspace, session
            )
            source_attr = remote_stat(session.sftp, source_root)
            if source_attr is None or not remote_is_dir(source_attr):
                return {
                    "mode": "skipped",
                    "status": "skipped",
                    "reason": f"远程源工作区不存在: {source_root}",
                    "source_workspace": source_workspace,
                    "source_workspace_server_id": source_workspace_server_id,
                    "target_workspace": target_workspace,
                    "target_workspace_server_id": target_workspace_server_id,
                    "synced_files": 0,
                    "skipped_files": 0,
                }
            if posixpath.normpath(source_root) == posixpath.normpath(target_root):
                return {
                    "mode": "no_op",
                    "status": "completed",
                    "reason": "源工作区与目标工作区相同，跳过远程复制。",
                    "source_workspace": source_root,
                    "source_workspace_server_id": source_workspace_server_id,
                    "target_workspace": target_root,
                    "target_workspace_server_id": target_workspace_server_id,
                    "synced_files": 0,
                    "skipped_files": 0,
                }
            _ensure_remote_directory(session.sftp, target_root)
            for item in files:
                relative_path = str(item["relative_path"])
                try:
                    _copy_remote_file(
                        source_sftp=session.sftp,
                        source_path=posixpath.join(source_root, relative_path),
                        target_sftp=session.sftp,
                        target_path=posixpath.join(target_root, relative_path),
                    )
                    synced.append(relative_path)
                except Exception:
                    skipped += 1
            target_workspace = target_root
    else:
        with open_ssh_session(source_server_entry) as source_session:
            source_root = resolve_remote_workspace_path(
                source_server_entry, source_workspace, source_session
            )
            source_attr = remote_stat(source_session.sftp, source_root)
            if source_attr is None or not remote_is_dir(source_attr):
                return {
                    "mode": "skipped",
                    "status": "skipped",
                    "reason": f"远程源工作区不存在: {source_root}",
                    "source_workspace": source_workspace,
                    "source_workspace_server_id": source_workspace_server_id,
                    "target_workspace": target_workspace,
                    "target_workspace_server_id": target_workspace_server_id,
                    "synced_files": 0,
                    "skipped_files": 0,
                }
            with open_ssh_session(target_server_entry) as target_session:
                target_root = resolve_remote_workspace_path(
                    target_server_entry, target_workspace, target_session
                )
                _ensure_remote_directory(target_session.sftp, target_root)
                for item in files:
                    relative_path = str(item["relative_path"])
                    try:
                        _copy_remote_file(
                            source_sftp=source_session.sftp,
                            source_path=posixpath.join(source_root, relative_path),
                            target_sftp=target_session.sftp,
                            target_path=posixpath.join(target_root, relative_path),
                        )
                        synced.append(relative_path)
                    except Exception:
                        skipped += 1
                target_workspace = target_root
    return {
        "mode": "remote_to_remote",
        "status": "completed",
        "source_workspace": source_workspace,
        "source_workspace_server_id": source_workspace_server_id,
        "target_workspace": target_workspace,
        "target_workspace_server_id": target_workspace_server_id,
        "workspace_server_id": target_workspace_server_id,
        "synced_files": len(synced),
        "skipped_files": skipped,
        "synced_paths": synced[:80],
    }


def _ensure_remote_directory(sftp, target_dir: str) -> None:
    normalized = posixpath.normpath(target_dir)
    target_attr = remote_stat(sftp, normalized)
    if target_attr is None:
        remote_make_dirs(sftp, normalized)
        return
    if not remote_is_dir(target_attr):
        raise ValueError(f"远程目标不是目录: {normalized}")


def _copy_remote_file(*, source_sftp, source_path: str, target_sftp, target_path: str) -> None:
    parent_dir = posixpath.dirname(target_path)
    if remote_stat(target_sftp, parent_dir) is None:
        remote_make_dirs(target_sftp, parent_dir)
    with source_sftp.file(source_path, "rb") as source_handle:
        payload = source_handle.read()
    with target_sftp.file(target_path, "wb") as target_handle:
        target_handle.write(payload)


def _collect_local_sync_files(source_workspace: str) -> list[dict[str, Any]]:
    root = Path(source_workspace).expanduser()
    if not root.exists() or not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in DEFAULT_IGNORES or part in _SYNC_SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in _SYNC_SKIP_SUFFIXES:
            continue
        size_bytes = path.stat().st_size
        if size_bytes > _SYNC_MAX_FILE_SIZE_BYTES:
            continue
        items.append(
            {
                "relative_path": relative.as_posix(),
                "size_bytes": size_bytes,
            }
        )
    return items[:400]


def _collect_remote_sync_files(
    source_workspace: str, workspace_server_id: str
) -> list[dict[str, Any]]:
    server_entry = get_workspace_server_entry(workspace_server_id)
    items: list[dict[str, Any]] = []
    with open_ssh_session(server_entry) as session:
        source_root = resolve_remote_workspace_path(server_entry, source_workspace, session)
        root_attr = remote_stat(session.sftp, source_root)
        if root_attr is None or not remote_is_dir(root_attr):
            return []

        def _walk(current_dir: str, relative_prefix: str = "") -> None:
            if len(items) >= 400:
                return
            entries = sorted(
                session.sftp.listdir_attr(current_dir),
                key=lambda current: str(getattr(current, "filename", "")),
            )
            for entry in entries:
                name = str(getattr(entry, "filename", "") or "").strip()
                if not name or name in {".", ".."}:
                    continue
                relative_path = f"{relative_prefix}/{name}" if relative_prefix else name
                parts = tuple(
                    part for part in PurePosixPath(relative_path).parts if part not in {"", "."}
                )
                if any(part in DEFAULT_IGNORES or part in _SYNC_SKIP_DIRS for part in parts):
                    continue
                full_path = posixpath.join(current_dir, name)
                if remote_is_dir(entry):
                    _walk(full_path, relative_path)
                    if len(items) >= 400:
                        return
                    continue
                if Path(name).suffix.lower() in _SYNC_SKIP_SUFFIXES:
                    continue
                size_bytes = int(getattr(entry, "st_size", 0) or 0)
                if size_bytes > _SYNC_MAX_FILE_SIZE_BYTES:
                    continue
                items.append(
                    {
                        "relative_path": relative_path,
                        "size_bytes": size_bytes,
                    }
                )
                if len(items) >= 400:
                    return

        _walk(source_root)
    return items[:400]


def _build_sync_result_markdown(sync_result: dict[str, Any]) -> str:
    lines = [
        "# SYNC_RESULT",
        "",
        f"- Mode: `{sync_result.get('mode')}`",
        f"- Status: `{sync_result.get('status')}`",
        f"- Source: `{sync_result.get('source_workspace') or 'N/A'}`",
        f"- Source Server: `{sync_result.get('source_workspace_server_id') or 'local'}`",
        f"- Target: `{sync_result.get('target_workspace') or 'N/A'}`",
        f"- Target Server: `{sync_result.get('target_workspace_server_id') or sync_result.get('workspace_server_id') or 'local'}`",
        f"- Synced Files: {int(sync_result.get('synced_files') or 0)}",
        f"- Skipped Files: {int(sync_result.get('skipped_files') or 0)}",
        "",
        "## Synced Paths",
    ]
    synced_paths = list(sync_result.get("synced_paths") or [])
    if synced_paths:
        lines.extend(f"- `{path}`" for path in synced_paths[:40])
    else:
        lines.append(f"- {sync_result.get('reason') or '无'}")
    return "\n".join(lines).strip() + "\n"


def _build_sync_validation_markdown(
    *,
    target_workspace: str,
    workspace_server_id: str | None,
) -> str:
    lines = [
        "# SYNC_VALIDATION",
        "",
        f"- Target Workspace: `{target_workspace or 'N/A'}`",
        f"- Target Server: `{workspace_server_id or 'local'}`",
        "",
    ]
    if workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(workspace_server_id)
            overview = build_remote_overview(
                server_entry, target_workspace, depth=2, max_entries=80
            )
            lines.extend(
                [
                    f"- Exists: {bool(overview.get('exists'))}",
                    "",
                    "## Tree",
                    str(overview.get("tree") or "").strip() or "(empty)",
                ]
            )
        except Exception as exc:
            lines.extend(["- Exists: False", "", f"错误: {str(exc)}"])
    else:
        target = Path(target_workspace).expanduser()
        lines.append(f"- Exists: {target.exists()}")
        lines.extend(["", "## Tree", _local_tree_preview(target)])
    return "\n".join(lines).strip() + "\n"


def _resolve_tracked_remote_session_names(metadata: dict[str, Any]) -> list[str]:
    names: list[str] = []
    single = clean_text(metadata.get("remote_session_name"))
    if single:
        names.append(single)
    for item in metadata.get("remote_session_names") or []:
        cleaned = clean_text(item)
        if cleaned:
            names.append(cleaned)
    for item in metadata.get("remote_experiments") or []:
        if not isinstance(item, dict):
            continue
        cleaned = clean_text(item.get("remote_session_name"))
        if cleaned:
            names.append(cleaned)
    execution_result = metadata.get("execution_result") or {}
    for item in execution_result.get("batch_experiments") or []:
        if not isinstance(item, dict):
            continue
        cleaned = clean_text(item.get("remote_session_name"))
        if cleaned:
            names.append(cleaned)
    return list(dict.fromkeys(names))


def _collect_remote_screen_state(
    workspace_server_id: str,
    *,
    tracked_session_name: str | None = None,
    tracked_session_names: list[str] | None = None,
) -> dict[str, Any]:
    raw_names = list(tracked_session_names or [])
    if not raw_names and tracked_session_name:
        raw_names.append(tracked_session_name)
    normalized_names = list(dict.fromkeys(filter(None, (clean_text(item) for item in raw_names))))
    session_name = normalized_names[0] if normalized_names else None
    try:
        server_entry = get_workspace_server_entry(workspace_server_id)
        sessions: list[dict[str, Any]] = []
        raw_outputs: list[str] = []
        if normalized_names:
            for name in normalized_names:
                snapshot = remote_list_screen_sessions(server_entry, session_name=name)
                raw_outputs.append(str(snapshot.get("stdout") or ""))
                sessions.extend(list(snapshot.get("sessions") or []))
        else:
            snapshot = remote_list_screen_sessions(server_entry, session_name=session_name)
            raw_outputs.append(str(snapshot.get("stdout") or ""))
            sessions = list(snapshot.get("sessions") or [])
        deduped_sessions: list[dict[str, Any]] = []
        seen_session_names: set[str] = set()
        for item in sessions:
            name = str(item.get("name") or "").strip()
            if not name or name in seen_session_names:
                continue
            seen_session_names.add(name)
            deduped_sessions.append(dict(item))
        captures: list[dict[str, Any]] = []
        for item in deduped_sessions[: max(3, len(normalized_names) or 0)]:
            capture = remote_capture_screen_session(
                server_entry,
                session_name=str(item.get("name") or ""),
                lines=50,
            )
            captures.append(
                {
                    "session_name": item.get("name"),
                    "success": capture.get("success"),
                    "stdout": capture.get("stdout"),
                    "stderr": capture.get("stderr"),
                }
            )
        return {
            "tracked_session_name": session_name,
            "tracked_session_names": normalized_names,
            "sessions": deduped_sessions,
            "captures": captures,
            "raw_output": "\n".join(part for part in raw_outputs if part).strip(),
            "error": None,
        }
    except Exception as exc:
        return {
            "tracked_session_name": session_name,
            "tracked_session_names": normalized_names,
            "sessions": [],
            "captures": [],
            "raw_output": "",
            "error": str(exc),
        }


def _collect_remote_gpu_state(
    workspace_server_id: str,
    *,
    workspace_path: str,
    active_session_names: list[str] | None = None,
) -> dict[str, Any]:
    try:
        server_entry = get_workspace_server_entry(workspace_server_id)
        if active_session_names is None:
            lease_state = {
                "active": list_active_gpu_leases(workspace_server_id),
                "released": [],
            }
        else:
            lease_state = reconcile_gpu_leases(
                workspace_server_id=workspace_server_id,
                active_session_names=list(active_session_names or []),
            )
        snapshot = remote_probe_gpus(server_entry, path=workspace_path)
        return {
            "available": bool(snapshot.get("available")),
            "success": bool(snapshot.get("success")),
            "gpus": list(snapshot.get("gpus") or []),
            "reason": snapshot.get("reason"),
            "active_leases": list(lease_state.get("active") or []),
            "released_leases": list(lease_state.get("released") or []),
        }
    except Exception as exc:
        return {
            "available": False,
            "success": False,
            "gpus": [],
            "reason": str(exc),
            "active_leases": list_active_gpu_leases(workspace_server_id),
            "released_leases": [],
        }


def _maybe_execute_monitor_stage(
    context: WorkflowContext,
    stage: dict[str, Any],
    *,
    workspace_path: str,
    workspace_server_id: str | None,
) -> dict[str, Any] | None:
    if context.run.workflow_type.value != "monitor_experiment":
        return None

    stage_id = str(stage.get("id") or "").strip()
    if stage_id == "inspect_runs":
        tracked_session_names = _resolve_tracked_remote_session_names(context.metadata)
        tracked_session_name = tracked_session_names[0] if tracked_session_names else None
        screen_state = (
            _collect_remote_screen_state(
                workspace_server_id,
                tracked_session_name=tracked_session_name,
                tracked_session_names=tracked_session_names,
            )
            if workspace_server_id
            else {
                "tracked_session_name": tracked_session_name,
                "tracked_session_names": tracked_session_names,
                "sessions": [],
                "captures": [],
                "raw_output": "",
                "error": None,
            }
        )
        active_session_names = [
            str(item.get("name") or "").strip()
            for item in (screen_state.get("sessions") or [])
            if str(item.get("name") or "").strip()
        ]
        gpu_state = (
            _collect_remote_gpu_state(
                workspace_server_id,
                workspace_path=workspace_path,
                active_session_names=active_session_names
                if not screen_state.get("error")
                else None,
            )
            if workspace_server_id
            else {
                "available": False,
                "success": False,
                "gpus": [],
                "reason": None,
            }
        )
        try:
            inspection = _inspect_workspace_payload(context)
        except Exception as exc:
            inspection = {
                "status": "error",
                "message": str(exc),
                "tree": "",
                "runtime": {},
            }
        content = "\n".join(
            [
                "# MONITOR_INSPECTION",
                "",
                f"- Workspace: `{workspace_path}`",
                f"- Server: `{workspace_server_id or 'local'}`",
                f"- Status: `{inspection.get('status')}`",
                f"- Tracked Session: `{tracked_session_name or 'N/A'}`",
                f"- Tracked Session Count: `{len(tracked_session_names)}`",
                "",
                "## Tree",
                str(inspection.get("tree") or inspection.get("message") or "(empty)").strip(),
                "",
                "## Runtime",
                json.dumps(inspection.get("runtime") or {}, ensure_ascii=False, indent=2),
                "",
                "## GPU State",
                json.dumps(gpu_state, ensure_ascii=False, indent=2),
                "",
                "## Screen Sessions",
                json.dumps(
                    {
                        "sessions": screen_state.get("sessions") or [],
                        "captures": screen_state.get("captures") or [],
                        "error": screen_state.get("error"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
            ]
        )
        return {
            "agent_type": "researchos_monitor_inspect",
            "label": "ResearchOS Monitor Inspect",
            "provider": "workspace_monitor",
            "base_url": None,
            "default_model": None,
            "model": None,
            "variant": None,
            "command": "inspect_workspace",
            "command_path": workspace_path,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": content,
            "parsed": {
                **inspection,
                "gpu_state": gpu_state,
                "screen_state": screen_state,
            },
            "model_role": "executor",
            "model_source": "workspace_monitor",
        }

    if stage_id == "collect_signals":
        signals = _collect_monitor_signals(
            context=context,
            workspace_path=workspace_path,
            workspace_server_id=workspace_server_id,
        )
        content = _build_monitor_signals_markdown(signals)
        artifact_refs: list[dict[str, Any]] = []
        signal_artifact = _write_run_artifact(
            context,
            "reports/monitor-signals.json",
            json.dumps(signals, ensure_ascii=False, indent=2) + "\n",
            kind="artifact",
        )
        if signal_artifact:
            artifact_refs.append(signal_artifact)
        artifact_refs.extend(list(signals.get("artifact_refs") or []))
        return {
            "agent_type": "researchos_monitor_collect",
            "label": "ResearchOS Monitor Collect",
            "provider": "workspace_monitor",
            "base_url": None,
            "default_model": None,
            "model": None,
            "variant": None,
            "command": "collect_monitor_signals",
            "command_path": workspace_path,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": content,
            "parsed": signals,
            "artifact_refs": artifact_refs,
            "model_role": "executor",
            "model_source": "workspace_monitor",
        }
    return None


def _collect_monitor_signals(
    *,
    context: WorkflowContext,
    workspace_path: str,
    workspace_server_id: str | None,
) -> dict[str, Any]:
    if workspace_server_id:
        return _collect_remote_monitor_signals(context, workspace_path, workspace_server_id)
    return _collect_local_monitor_signals(context, workspace_path)


def _collect_local_monitor_signals(context: WorkflowContext, workspace_path: str) -> dict[str, Any]:
    root = Path(workspace_path).expanduser()
    relative_paths: list[str] = []
    artifact_refs: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if not _should_collect_monitor_path(relative):
                continue
            relative_paths.append(relative)
            artifact_refs.append(
                {
                    "kind": "artifact",
                    "path": str(path),
                    "relative_path": relative,
                }
            )
    log_excerpt = _tail_local_log(context.run.log_path)
    summary = _summarize_monitor_inventory(
        relative_paths=relative_paths,
        metadata=context.metadata,
        log_excerpt=log_excerpt,
        screen_captures=[],
        text_reader=lambda relative_path, max_chars: _read_local_monitor_file(
            root / Path(relative_path), max_chars=max_chars
        ),
    )
    return {
        **summary,
        "workspace_path": workspace_path,
        "workspace_server_id": None,
        "log_excerpt": log_excerpt,
        "artifact_refs": artifact_refs[:16],
    }


def _collect_remote_monitor_signals(
    context: WorkflowContext,
    workspace_path: str,
    workspace_server_id: str,
) -> dict[str, Any]:
    artifact_refs: list[dict[str, Any]] = []
    relative_paths: list[str] = []
    log_excerpt = ""
    tracked_session_names = _resolve_tracked_remote_session_names(context.metadata)
    tracked_session_name = tracked_session_names[0] if tracked_session_names else None
    screen_state = _collect_remote_screen_state(
        workspace_server_id,
        tracked_session_name=tracked_session_name,
        tracked_session_names=tracked_session_names,
    )
    gpu_state = _collect_remote_gpu_state(
        workspace_server_id,
        workspace_path=workspace_path,
        active_session_names=(
            [
                str(item.get("name") or "").strip()
                for item in (screen_state.get("sessions") or [])
                if str(item.get("name") or "").strip()
            ]
            if not screen_state.get("error")
            else None
        ),
    )
    server_entry = None
    try:
        server_entry = get_workspace_server_entry(workspace_server_id)
        overview = build_remote_overview(server_entry, workspace_path, depth=5, max_entries=240)
        for relative_path in overview.get("files") or []:
            normalized = str(relative_path)
            if not _should_collect_monitor_path(normalized):
                continue
            relative_paths.append(normalized)
            artifact_refs.append(
                {
                    "kind": "artifact",
                    "path": f"{str(overview.get('workspace_path') or workspace_path).rstrip('/')}/{normalized.lstrip('/')}",
                    "relative_path": normalized,
                }
            )
    except Exception as exc:
        log_excerpt = f"远程监控读取失败: {str(exc)}"
    if server_entry is not None and context.run.log_path:
        try:
            result = remote_terminal_result(
                server_entry,
                path=workspace_path,
                command=f"tail -n 80 {shlex.quote(str(context.run.log_path))}",
                timeout_sec=30,
            )
            log_excerpt = str(result.get("stdout") or result.get("stderr") or "").strip()
        except Exception as exc:
            if not log_excerpt:
                log_excerpt = f"远程监控日志读取失败: {str(exc)}"
    summary = _summarize_monitor_inventory(
        relative_paths=relative_paths,
        metadata=context.metadata,
        log_excerpt=log_excerpt,
        screen_captures=screen_state.get("captures") or [],
        text_reader=(
            (
                lambda relative_path, max_chars: _read_remote_monitor_file(
                    server_entry, workspace_path, relative_path, max_chars=max_chars
                )
            )
            if server_entry is not None
            else None
        ),
    )
    return {
        **summary,
        "workspace_path": workspace_path,
        "workspace_server_id": workspace_server_id,
        "tracked_session_name": tracked_session_name,
        "tracked_session_names": tracked_session_names,
        "gpu_state": gpu_state,
        "screen_sessions": screen_state.get("sessions") or [],
        "screen_captures": screen_state.get("captures") or [],
        "screen_error": screen_state.get("error"),
        "log_excerpt": log_excerpt,
        "artifact_refs": artifact_refs[:16],
    }


def _build_monitor_signals_markdown(signals: dict[str, Any]) -> str:
    tracked_session_names = [
        str(item).strip()
        for item in (signals.get("tracked_session_names") or [])
        if str(item).strip()
    ]
    lines = [
        "# MONITOR_SIGNALS",
        "",
        f"- Workspace: `{signals.get('workspace_path') or 'N/A'}`",
        f"- Server: `{signals.get('workspace_server_id') or 'local'}`",
        f"- Tracked Session: `{signals.get('tracked_session_name') or 'N/A'}`",
    ]
    if tracked_session_names:
        lines.extend(
            ["", "## Tracked Sessions", *[f"- `{item}`" for item in tracked_session_names[:12]]]
        )
    alerts = list(signals.get("alerts") or [])
    if alerts:
        lines.extend(
            ["", "## Alerts", *[f"- {item.get('message') or item}" for item in alerts[:12]]]
        )
    gpu_state = signals.get("gpu_state") or {}
    gpu_items = list(gpu_state.get("gpus") or [])
    if gpu_items:
        lines.extend(
            [
                "",
                "## GPU State",
                *[
                    f"- GPU {item.get('index')}: {item.get('memory_used_mb')}/{item.get('memory_total_mb')} MiB, util={item.get('utilization_gpu_pct')}"
                    for item in gpu_items[:12]
                ],
            ]
        )
    elif gpu_state.get("reason"):
        lines.extend(["", "## GPU State", f"- {gpu_state.get('reason')}"])
    active_leases = list(gpu_state.get("active_leases") or [])
    if active_leases:
        lines.extend(
            [
                "",
                "## GPU Leases",
                *[
                    f"- GPU {item.get('gpu_index')} -> session `{item.get('remote_session_name') or 'N/A'}` / run `{item.get('run_id') or 'N/A'}`"
                    for item in active_leases[:12]
                ],
            ]
        )
    released_leases = list(gpu_state.get("released_leases") or [])
    if released_leases:
        lines.extend(
            [
                "",
                "## Released GPU Leases",
                *[
                    f"- GPU {item.get('gpu_index')} released ({item.get('release_reason') or 'remote_session_missing'})"
                    for item in released_leases[:12]
                ],
            ]
        )
    screen_sessions = list(signals.get("screen_sessions") or [])
    if screen_sessions:
        lines.extend(
            [
                "",
                "## Screen Sessions",
                *[f"- `{item.get('name')}` ({item.get('state')})" for item in screen_sessions[:10]],
            ]
        )
    screen_error = str(signals.get("screen_error") or "").strip()
    if screen_error:
        lines.extend(["", "## Screen Error", screen_error])
    comparison = signals.get("comparison") or {}
    comparison_rows = list(comparison.get("rows") or [])
    if comparison_rows:
        lines.extend(
            [
                "",
                "## Result Comparison",
                f"- Metric: `{comparison.get('metric')}`",
                f"- Baseline: `{comparison.get('baseline_label') or 'N/A'}`",
                "",
                "| Experiment | Value | Delta vs Baseline | Status |",
                "| --- | --- | --- | --- |",
                *[
                    f"| {row.get('label') or row.get('relative_path') or 'N/A'} | {_format_monitor_metric_value(row.get('value'))} | "
                    f"{_format_monitor_delta(row.get('delta'))} | {row.get('status') or 'N/A'} |"
                    for row in comparison_rows[:10]
                ],
            ]
        )
    result_summaries = list(signals.get("result_summaries") or [])
    if result_summaries:
        lines.extend(["", "## Result Summaries"])
        for item in result_summaries[:8]:
            metrics = list((item.get("metrics") or {}).items())[:5]
            metric_summary = (
                ", ".join(f"{key}={_format_monitor_metric_value(value)}" for key, value in metrics)
                or "无结构化指标"
            )
            lines.append(
                f"- `{item.get('label') or item.get('relative_path')}` | status={item.get('status') or 'N/A'} | {metric_summary}"
            )
    wandb_summaries = list(signals.get("wandb_summaries") or [])
    if wandb_summaries:
        lines.extend(["", "## Weights & Biases"])
        for item in wandb_summaries[:6]:
            metrics = list((item.get("metrics") or {}).items())[:4]
            metric_summary = (
                ", ".join(f"{key}={_format_monitor_metric_value(value)}" for key, value in metrics)
                or "无 summary 指标"
            )
            lines.append(f"- `{item.get('relative_path')}` | {metric_summary}")
    tensorboard_files = list(signals.get("tensorboard_files") or [])
    if tensorboard_files:
        lines.extend(["", "## TensorBoard", *[f"- `{item}`" for item in tensorboard_files[:10]]])
    checkpoint_files = list(signals.get("checkpoint_files") or [])
    if checkpoint_files:
        lines.extend(["", "## Checkpoints", *[f"- `{item}`" for item in checkpoint_files[:12]]])
    parse_errors = list(signals.get("parse_errors") or [])
    if parse_errors:
        lines.extend(
            [
                "",
                "## Parse Notes",
                *[
                    f"- `{item.get('relative_path')}`: {item.get('error')}"
                    for item in parse_errors[:8]
                ],
            ]
        )
    lines.extend(["", "## Candidate Files"])
    files = list(signals.get("candidate_files") or [])
    if files:
        lines.extend(f"- `{item}`" for item in files[:40])
    else:
        lines.append("- 未发现可用日志/指标文件。")
    lines.extend(
        [
            "",
            "## Log Excerpt",
            "```text",
            str(signals.get("log_excerpt") or "N/A")[:4000],
            "```",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _tail_local_log(log_path: str | None) -> str:
    path = Path(str(log_path or "").strip())
    if not path.exists() or not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])


def _read_local_monitor_file(path: Path, *, max_chars: int) -> str:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8", errors="replace")[:max_chars]


def _read_remote_monitor_file(
    server_entry: dict,
    workspace_path: str,
    relative_path: str,
    *,
    max_chars: int,
) -> str:
    payload = remote_read_file(server_entry, workspace_path, relative_path, max_chars=max_chars)
    return str(payload.get("content") or "")


def _should_collect_monitor_path(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/").strip()
    if not normalized:
        return False
    parts = tuple(part for part in PurePosixPath(normalized).parts if part not in {"", "."})
    if any(part in DEFAULT_IGNORES for part in parts):
        return False
    lower = normalized.lower()
    if _is_tensorboard_artifact(lower) or _is_checkpoint_artifact(lower):
        return True
    suffix = Path(normalized).suffix.lower()
    if suffix in _MONITOR_TEXT_SUFFIXES:
        return True
    return "wandb-summary.json" in lower


def _is_tensorboard_artifact(relative_path: str) -> bool:
    return "events.out.tfevents" in str(relative_path or "").lower()


def _is_checkpoint_artifact(relative_path: str) -> bool:
    lower = str(relative_path or "").lower()
    parts = tuple(part for part in PurePosixPath(lower).parts if part not in {"", "."})
    return (
        any(part.startswith("checkpoint") for part in parts)
        or Path(lower).suffix.lower() in _MONITOR_CHECKPOINT_SUFFIXES
    )


def _should_parse_structured_monitor_file(relative_path: str) -> bool:
    lower = str(relative_path or "").lower()
    if "wandb-summary.json" in lower:
        return True
    suffix = Path(lower).suffix.lower()
    if suffix not in {".json", ".csv"}:
        return False
    return any(marker in lower for marker in _MONITOR_RESULT_NAME_MARKERS)


def _resolve_monitor_experiment_names(metadata: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in metadata.get("remote_experiments") or []:
        if isinstance(item, dict):
            cleaned = clean_text(item.get("name"))
            if cleaned:
                names.append(cleaned)
    execution_result = metadata.get("execution_result") or {}
    for item in execution_result.get("batch_experiments") or []:
        if isinstance(item, dict):
            cleaned = clean_text(item.get("name"))
            if cleaned:
                names.append(cleaned)
    return list(dict.fromkeys(names))


def _summarize_monitor_inventory(
    *,
    relative_paths: list[str],
    metadata: dict[str, Any],
    log_excerpt: str,
    screen_captures: list[dict[str, Any]],
    text_reader,
) -> dict[str, Any]:
    candidate_files: list[str] = []
    tensorboard_files: list[str] = []
    checkpoint_files: list[str] = []
    result_summaries: list[dict[str, Any]] = []
    wandb_summaries: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    experiment_names = _resolve_monitor_experiment_names(metadata)

    for relative_path in relative_paths:
        normalized = str(relative_path or "").replace("\\", "/").strip()
        if not normalized:
            continue
        lower = normalized.lower()
        if normalized not in candidate_files:
            candidate_files.append(normalized)
        if _is_tensorboard_artifact(lower):
            tensorboard_files.append(normalized)
        if _is_checkpoint_artifact(lower):
            checkpoint_files.append(normalized)
        if not text_reader or not _should_parse_structured_monitor_file(lower):
            continue
        if len(result_summaries) >= 8:
            continue
        try:
            content = text_reader(normalized, 12000)
            summary = _parse_monitor_structured_file(
                normalized,
                content,
                experiment_names=experiment_names,
            )
            if summary:
                result_summaries.append(summary)
                if summary.get("kind") == "wandb_summary":
                    wandb_summaries.append(summary)
        except Exception as exc:
            parse_errors.append({"relative_path": normalized, "error": str(exc)})

    comparison = _build_monitor_comparison(result_summaries)
    alerts = _collect_monitor_alerts(
        log_excerpt=log_excerpt,
        screen_captures=screen_captures,
        result_summaries=result_summaries,
        parse_errors=parse_errors,
    )
    return {
        "candidate_files": candidate_files[:60],
        "tensorboard_files": tensorboard_files[:12],
        "checkpoint_files": checkpoint_files[:16],
        "result_summaries": result_summaries[:8],
        "wandb_summaries": wandb_summaries[:8],
        "parse_errors": parse_errors[:8],
        "comparison": comparison,
        "alerts": alerts,
    }


def _parse_monitor_structured_file(
    relative_path: str,
    content: str,
    *,
    experiment_names: list[str],
) -> dict[str, Any] | None:
    normalized = str(relative_path or "").replace("\\", "/").strip()
    lower = normalized.lower()
    suffix = Path(normalized).suffix.lower()
    if suffix == ".json":
        payload = json.loads(content)
        metrics = _extract_monitor_metrics(payload)
        status = _extract_monitor_status(payload)
        if not metrics and not status:
            return None
        kind = "wandb_summary" if "wandb-summary.json" in lower else "json_result"
        return {
            "kind": kind,
            "relative_path": normalized,
            "label": _derive_monitor_label(normalized, experiment_names),
            "status": status,
            "metrics": metrics,
        }
    if suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(content)))
        if not rows:
            return None
        last_row = rows[-1]
        metrics = _extract_monitor_metrics(last_row)
        status = clean_text(last_row.get("status") or last_row.get("state")) or None
        if not metrics and not status:
            return None
        return {
            "kind": "csv_result",
            "relative_path": normalized,
            "label": _derive_monitor_label(normalized, experiment_names),
            "status": status,
            "metrics": metrics,
        }
    return None


def _extract_monitor_metrics(payload: Any) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {}

    def _walk(value: Any, prefix: str = "") -> None:
        if len(metrics) >= 20:
            return
        if isinstance(value, dict):
            for key, nested in value.items():
                next_prefix = f"{prefix}.{key}" if prefix else str(key)
                _walk(nested, next_prefix)
            return
        if isinstance(value, list):
            if value and isinstance(value[-1], dict):
                _walk(value[-1], prefix)
            return
        number = _coerce_monitor_number(value)
        if number is None or not prefix:
            return
        metrics[prefix] = number

    _walk(payload)
    return metrics


def _coerce_monitor_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _extract_monitor_status(payload: Any) -> str | None:
    status_keys = {"status", "state", "phase", "outcome", "result"}

    def _walk(value: Any) -> str | None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in status_keys:
                    cleaned = clean_text(nested)
                    if cleaned:
                        return cleaned
            for nested in value.values():
                resolved = _walk(nested)
                if resolved:
                    return resolved
        if isinstance(value, list):
            for nested in value:
                resolved = _walk(nested)
                if resolved:
                    return resolved
        return None

    return _walk(payload)


def _derive_monitor_label(relative_path: str, experiment_names: list[str]) -> str:
    lower = relative_path.lower()
    for name in experiment_names:
        if name.lower() in lower:
            return name
    path = PurePosixPath(relative_path)
    if "wandb-summary.json" in lower and len(path.parts) >= 3:
        return path.parts[-3]
    if len(path.parts) >= 2:
        return path.parts[-2]
    return path.stem or relative_path


def _build_monitor_comparison(result_summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = [item for item in result_summaries if item.get("metrics")]
    if len(rows) < 2:
        return None
    metric = _select_monitor_metric(rows)
    if not metric:
        return None
    baseline_index = next(
        (
            index
            for index, item in enumerate(rows)
            if "baseline" in str(item.get("label") or item.get("relative_path") or "").lower()
        ),
        0,
    )
    baseline_item = rows[baseline_index]
    baseline_value = (baseline_item.get("metrics") or {}).get(metric)
    comparison_rows: list[dict[str, Any]] = []
    for item in rows:
        value = (item.get("metrics") or {}).get(metric)
        delta = None
        if value is not None and baseline_value is not None:
            delta = float(value) - float(baseline_value)
        comparison_rows.append(
            {
                "label": item.get("label") or item.get("relative_path"),
                "relative_path": item.get("relative_path"),
                "value": value,
                "delta": delta,
                "status": item.get("status"),
            }
        )
    return {
        "metric": metric,
        "baseline_label": baseline_item.get("label") or baseline_item.get("relative_path"),
        "higher_is_better": _monitor_metric_higher_is_better(metric),
        "rows": comparison_rows,
    }


def _select_monitor_metric(result_summaries: list[dict[str, Any]]) -> str | None:
    metric_counts: dict[str, int] = {}
    for item in result_summaries:
        for key in (item.get("metrics") or {}).keys():
            metric_counts[key] = metric_counts.get(key, 0) + 1
    if not metric_counts:
        return None
    for preferred in _MONITOR_PRIORITY_METRICS:
        for key, count in metric_counts.items():
            if preferred in key.lower() and count >= 2:
                return key
    ranked = sorted(metric_counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[0][0] if ranked else None


def _monitor_metric_higher_is_better(metric: str) -> bool:
    lower = str(metric or "").lower()
    return not any(
        token in lower for token in ("loss", "error", "wer", "cer", "rmse", "mae", "mse")
    )


def _collect_monitor_alerts(
    *,
    log_excerpt: str,
    screen_captures: list[dict[str, Any]],
    result_summaries: list[dict[str, Any]],
    parse_errors: list[dict[str, Any]],
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(kind: str, message: str) -> None:
        normalized = f"{kind}:{message}"
        if normalized in seen:
            return
        seen.add(normalized)
        alerts.append({"kind": kind, "message": message})

    text_blocks: list[tuple[str, str]] = [("日志摘录", str(log_excerpt or ""))]
    for capture in screen_captures[:6]:
        session_name = clean_text(capture.get("session_name")) or "screen"
        text_blocks.append(
            (f"Screen `{session_name}`", str(capture.get("stdout") or capture.get("stderr") or ""))
        )
    for source_name, text in text_blocks:
        for kind, pattern in _MONITOR_ALERT_PATTERNS:
            if text and pattern.search(text):
                _add(kind, f"{source_name} 检测到 `{kind}` 迹象")
    for item in result_summaries:
        status = clean_text(item.get("status"))
        if status and any(marker in status.lower() for marker in ("fail", "error", "cancel")):
            _add("status", f"{item.get('label') or item.get('relative_path')} 状态异常：{status}")
        for key, value in (item.get("metrics") or {}).items():
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                _add(
                    "metric",
                    f"{item.get('label') or item.get('relative_path')} 的 `{key}` 为无效数值",
                )
    for item in parse_errors[:6]:
        _add("parse", f"{item.get('relative_path')} 解析失败：{item.get('error')}")
    return alerts[:12]


def _format_monitor_metric_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Inf"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _format_monitor_delta(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return _format_monitor_metric_value(value)
        return f"{value:+.4f}".rstrip("0").rstrip(".")
    return str(value)


def _local_tree_preview(root: Path) -> str:
    if not root.exists() or not root.is_dir():
        return "(missing)"
    lines = [str(root)]
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= 60:
            lines.append("... (truncated)")
            break
        if any(part in DEFAULT_IGNORES for part in path.relative_to(root).parts):
            continue
        indent = "  " * max(0, len(path.relative_to(root).parts) - 1)
        suffix = "/" if path.is_dir() else ""
        lines.append(f"{indent}- {path.name}{suffix}")
        count += 1
    return "\n".join(lines)


def _resolve_paper_compile_command(context: WorkflowContext) -> str:
    metadata = context.metadata
    for key in (
        "paper_compile_command",
        "compile_command",
        "execution_command",
        "command",
        "run_command",
    ):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return _resolve_execution_command(context, allow_default=False)


def _maybe_execute_paper_compile_stage(
    context: WorkflowContext,
    stage: dict[str, Any],
    *,
    workspace_path: str,
) -> dict[str, Any] | None:
    if context.run.workflow_type.value != "paper_compile":
        return None

    stage_id = str(stage.get("id") or "").strip()
    if stage_id == "prepare_compile":
        inspection = _inspect_workspace_payload(context)
        compile_command = _resolve_paper_compile_command(context)
        tree = str(
            inspection.get("tree") or inspection.get("message") or workspace_path or ""
        ).strip()
        content = "\n".join(
            [
                "## workspace",
                tree or "未记录",
                "",
                "## compile_command",
                f"- `{compile_command}`" if compile_command else "- 未检测到显式编译命令",
                "",
                "## note",
                "优先使用 run metadata 中的 compile_command / execution_command；若为空，则需要用户补充。",
            ]
        )
        return {
            "agent_type": "researchos_paper_compile_prepare",
            "label": "ResearchOS Paper Compile Prepare",
            "provider": "workspace_inspector",
            "base_url": None,
            "default_model": None,
            "model": None,
            "variant": None,
            "command": compile_command or None,
            "command_path": workspace_path,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": content,
            "parsed": None,
            "model_role": "executor",
            "model_source": "workspace_inspector",
        }

    if stage_id != "run_compile":
        return None

    compile_command = _resolve_paper_compile_command(context)
    if not compile_command:
        content = (
            "## compile_status\n"
            "- skipped: true\n"
            "- reason: 未检测到 compile_command / execution_command，未执行实际编译。\n"
            "- next: 请在运行参数中填写 LaTeX 编译命令，例如 `latexmk -pdf main.tex`。\n"
        )
        return {
            "agent_type": "researchos_paper_compile_skip",
            "label": "ResearchOS Paper Compile Skip",
            "provider": "workspace_executor_skip",
            "base_url": None,
            "default_model": None,
            "model": None,
            "variant": None,
            "command": None,
            "command_path": workspace_path,
            "duration_ms": None,
            "stdout": "",
            "stderr": "",
            "content": content,
            "parsed": None,
            "model_role": "executor",
            "model_source": "workspace_executor",
        }

    execution = _run_workspace_command_for_context(
        context,
        compile_command,
        timeout_sec=_resolve_execution_timeout(context),
    )
    log_artifact = _write_run_log(context, _format_command_log(execution))
    artifact_refs = [item for item in [log_artifact] if item]
    return {
        "agent_type": "researchos_paper_compile_run",
        "label": "ResearchOS Paper Compile Run",
        "provider": "workspace_executor_remote"
        if context.run.workspace_server_id
        else "workspace_executor_local",
        "base_url": None,
        "default_model": None,
        "model": None,
        "variant": None,
        "command": compile_command,
        "command_path": workspace_path,
        "duration_ms": None,
        "stdout": str(execution.get("stdout") or ""),
        "stderr": str(execution.get("stderr") or ""),
        "content": _command_result_preview(execution),
        "parsed": None,
        "model_role": "executor",
        "model_source": "workspace_executor",
        "artifact_refs": artifact_refs,
    }


def _paper_index_from_context(context: WorkflowContext) -> list[dict[str, Any]]:
    existing = context.metadata.get("paper_index")
    if isinstance(existing, list):
        return [dict(item) for item in existing if isinstance(item, dict)]
    refs: list[dict[str, Any]] = []
    for index, paper in enumerate(context.selected_papers, start=1):
        refs.append(
            {
                "ref_id": f"P{index}",
                "source": "project",
                "status": "library",
                "paper_id": paper.id,
                "title": paper.title,
                "arxiv_id": paper.arxiv_id,
                "abstract_available": bool(str(paper.abstract or "").strip()),
                "selected": True,
                "project_linked": True,
                "importable": False,
                "linkable": True,
                "asset_status": {},
            }
        )
    return refs


def _build_stage_prompt(
    context: WorkflowContext,
    stage: dict[str, Any],
    stage_outputs: dict[str, Any],
    *,
    workspace_path: str,
    agent_id: str,
) -> str:
    stage_id = str(stage.get("id") or "")
    stage_label = str(stage.get("label") or stage_id)
    execution_target = str(stage.get("execution_target") or "workspace_target")
    agent_role = _resolve_agent_role(agent_id)
    workflow_type = context.run.workflow_type.value
    workflow_preamble = workflow_runner_preamble(workflow_type)
    skill_id = workflow_assistant_skill_id(workflow_type)
    stage_intro = [
        "你是 ResearchOS 项目 workflow 的阶段执行智能体。",
        f"项目名称: {context.project.name}",
        f"项目描述: {context.project.description or '暂无描述'}",
        f"工作流类型: {workflow_type}",
        f"阶段: {stage_label} ({stage_id})",
        f"当前角色: {agent_role['label']} ({agent_id})",
        f"角色策略: {agent_role['strategy']}",
        f"阶段目标: {stage.get('description') or '请根据当前阶段完成对应产出'}",
        f"用户要求: {context.run.prompt or '无'}",
        f"执行位置: {execution_target}",
        f"工作区路径: {workspace_path}",
        "",
        "请直接完成当前阶段，并返回最终结果。除非阶段明确要求 JSON，否则使用中文 Markdown 输出。",
        "优先在一个短周期内完成任务，不要启动长期后台进程，不要执行大规模下载、训练或安装。",
    ]

    if workflow_preamble:
        stage_intro.extend(["", "项目流程语义:", workflow_preamble])

    if skill_id:
        stage_intro.append(
            f"该 workflow 在项目工作区中对齐研究助手的 /{skill_id} skill，请保持同一任务目标、产出结构和推进方式。"
        )

    if workflow_is_workspace_skill(workflow_type):
        stage_intro.append("这是项目工作区中的正式 skill workflow，不要退回成普通闲聊式回答。")

    if context.run.max_iterations:
        stage_intro.append(f"最大迭代轮次: {context.run.max_iterations}")
    if context.run.executor_model:
        stage_intro.append(f"执行模型: {context.run.executor_model}")
    if context.run.reviewer_model:
        stage_intro.append(f"评审 / 覆核模型: {context.run.reviewer_model}")

    if stage_outputs:
        stage_intro.append("")
        stage_intro.append("前序阶段输出摘要:")
        for item in stage_outputs.values():
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("stage_id") or "阶段")
            summary = str(item.get("summary") or item.get("content") or "").strip()
            stage_intro.append(f"- {label}: {summary[:1200] or '无'}")

    paper_index = _paper_index_from_context(context)
    if paper_index:
        stage_intro.append("")
        stage_intro.append("项目相关论文索引:")
        stage_intro.append(
            format_ref_index_for_prompt(
                paper_index,
                empty_text="暂无项目论文索引。",
            )
        )

    if context.selected_repos:
        stage_intro.append("")
        stage_intro.append("项目相关仓库:")
        for repo in context.selected_repos[:6]:
            stage_intro.append(f"- {repo.repo_url} | 本地路径: {repo.local_path or '未克隆'}")

    contract = _stage_output_contract(context, stage)
    if contract:
        stage_intro.extend(["", "输出要求:", contract])

    return "\n".join(stage_intro).strip()


def _stage_output_contract(context: WorkflowContext, stage: dict[str, Any]) -> str:
    stage_id = str(stage.get("id") or "")
    workflow_type = context.run.workflow_type.value
    if workflow_type == "literature_review":
        if stage_id == "collect_context":
            return (
                "输出一份 Markdown 证据包，至少包含：研究问题、关键论文要点、方法脉络、"
                "数据与评测、主要风险与空白。"
            )
        if stage_id == "synthesize_evidence":
            return "输出 Markdown 综述草稿，结构清晰，不要使用代码块。"
        if stage_id == "deliver_review":
            return (
                "输出最终 Markdown 文献综述，不要使用代码块，至少包含：项目背景与研究目标、"
                "当前研究脉络、代表性论文与启发、关键空白与风险、下一步建议。"
            )
    if workflow_type == "idea_discovery":
        if stage_id == "collect_context":
            return "输出 Markdown landscape summary，包含研究版图、结构性空白、scope 建议。"
        if stage_id == "expand_directions":
            return (
                "只输出一个 JSON 对象，不要输出 Markdown 代码块。格式为："
                '{"ideas":[{"title":"一句话标题","content":"Markdown 内容","paper_refs":["P1","P2"]}]}. '
                "请给出 3 条以内、务实可落地的研究想法。"
            )
        if stage_id == "verify_novelty":
            return (
                "输出 Markdown 深度查新报告，按 idea 汇总 closest prior work、overlap risk、delta。"
            )
        if stage_id == "external_review":
            return "输出 Markdown reviewer feedback，包含 score、主要 objections、最小修复建议。"
        if stage_id == "rank_and_persist":
            return "输出最终 IDEA_REPORT.md 风格 Markdown，汇总 landscape、ideas、novelty、review、next steps。"
    if workflow_type == "init_repo":
        if stage_id == "plan_repo":
            return (
                "只输出一份精简 Markdown 仓库规划，不要修改文件，不要安装依赖。"
                "规划范围收敛到首批一级目录和最小文件集合，不要设计复杂架构。"
                "至少包含：建议目录结构、每个目录用途、首批需要创建的最小文件、后续扩展建议。"
            )
        if stage_id == "create_scaffold":
            return (
                "直接在工作区创建最小可用脚手架，只创建轻量文件与目录，不要下载依赖、不要执行长耗时命令。"
                "不要遍历无关目录，不要生成超过 9 个文件，不要运行测试，不要安装任何包。"
                "只需确保以下路径存在并写入最小内容：README.md、.gitignore、src/main.py、scripts/run_smoke.ps1、"
                "experiments/README.md、docs/README.md、configs/README.md、data/.gitkeep、outputs/.gitkeep。"
                "如果目录或文件已存在，只补齐缺失项。完成后立即停止，并只输出 Markdown：created_paths、entrypoint、how_to_run。"
            )
        if stage_id == "validate_bootstrap":
            return (
                "输出 Markdown 检查单，只核对 README.md、.gitignore、src/main.py、scripts/run_smoke.ps1、"
                "experiments/README.md、docs/README.md、configs/README.md、data/.gitkeep、outputs/.gitkeep 是否齐全。"
                "如有缺失，只补最小缺失项；不要做额外重构或大规模改动。"
            )
    if workflow_type == "autoresearch_claude_code":
        if stage_id == "bootstrap_session":
            return (
                "输出 Markdown 初始化记录，至少包含：created_paths、session_file、baseline_command、"
                "远程场景的手动执行提示（如适用）。"
            )
        if stage_id == "run_baseline":
            return (
                "输出 Markdown 基线执行记录，至少包含：command、exit_code、关键输出路径、"
                "失败时的回退动作。"
            )
        if stage_id == "propose_iterations":
            return (
                "输出 Markdown 迭代计划，至少包含 3 条可执行 iteration 卡片。"
                "每条卡片包含 objective、hypothesis、command_or_action、metric、risk。"
            )
    if workflow_type == "paper_plan":
        return (
            "输出 Markdown 论文规划，至少包含：目标 venue、claims-evidence matrix、section plan、"
            "figure/table plan、citation scaffolding。"
        )
    if workflow_type == "paper_figure":
        return (
            "输出 Markdown 图表规划，至少包含：figure/table inventory、数据来源、预期文件名、"
            "哪些图需要手工制作。"
        )
    if workflow_type == "paper_write":
        return (
            "输出 Markdown 写作结果，至少包含：abstract、introduction、related work、method、"
            "experiments、limitations、conclusion 的要点。"
        )
    if workflow_type == "paper_improvement":
        if stage_id == "diagnose_draft":
            return "输出 Markdown 审稿意见，必须包含 `Score: <0-10>`、主要问题、优先修订项。"
        if stage_id == "revise_sections":
            return "输出 Markdown 修订记录，说明每个主要问题如何被修改或暂未解决。"
        if stage_id == "final_check":
            return (
                "输出 Markdown 终检结果，必须包含 `Score: <0-10>`、format checklist、"
                "是否达到下一轮投稿前要求。"
            )
    if workflow_type == "monitor_experiment":
        return "输出 Markdown 监控简报，至少包含：运行状态、关键日志/指标、异常提示、下一步动作。"
    if workflow_type == "sync_workspace":
        return (
            "输出 Markdown 同步结果，至少包含：源路径、目标路径、同步模式、文件数量、"
            "跳过项、校验结论。"
        )
    return (
        "输出一份 Markdown 阶段结果，至少包含：阶段目标、关键操作、关键发现、阶段产出、下一步建议。"
    )


def _build_workflow_output_markdown(
    context: WorkflowContext,
    stage_outputs: dict[str, Any],
) -> str:
    parts = [
        f"# {context.project.name} · {context.run.title or context.run.workflow_type.value}",
        "",
    ]
    for key, item in stage_outputs.items():
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or key)
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        parts.extend([f"## {label}", content, ""])
    return "\n".join(parts).strip()


def _extract_stage_json(text: str) -> dict[str, Any] | None:
    value = (text or "").strip()
    if not value:
        return None
    if value.startswith("```"):
        segments = value.split("```")
        if len(segments) >= 3:
            body = segments[1]
            if "\n" in body:
                body = body.split("\n", 1)[1]
            value = body.strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _execute_native_stage(
    context: WorkflowContext,
    stage: dict[str, Any],
    prompt: str,
    *,
    agent_id: str,
) -> dict[str, Any]:
    stage_id = str(stage.get("id") or "stage")
    agent_role = _resolve_agent_role(agent_id)
    role_profile = _resolve_role_profile(agent_id)
    llm = LLMClient()
    target = _resolve_stage_model_target(context, stage_id, role_profile, llm)
    model_override = target["model_override"]
    variant_override = str(
        target.get("variant_override") or role_profile.get("variant") or "medium"
    )
    if context.run.workflow_type.value == "idea_discovery" and stage_id == "expand_directions":
        result = llm.complete_json(
            prompt,
            stage=f"project_{context.run.workflow_type.value}_{stage_id}",
            model_override=model_override,
            variant_override=variant_override,
            max_tokens=2400,
            max_retries=1,
            request_timeout=180,
        )
        content = json.dumps(result.parsed_json or {}, ensure_ascii=False)
        parsed = result.parsed_json or {}
    elif context.run.workflow_type.value == "experiment_audit" and stage_id == "review_integrity":
        result = llm.complete_json(
            prompt,
            stage="project_experiment_audit_review",
            model_override=model_override,
            variant_override=variant_override,
            max_tokens=2600,
            max_retries=1,
            request_timeout=240,
        )
        content = json.dumps(result.parsed_json or {}, ensure_ascii=False)
        parsed = result.parsed_json or {}
    else:
        result = llm.summarize_text(
            prompt,
            stage=f"project_{context.run.workflow_type.value}_{stage_id}",
            model_override=model_override,
            variant_override=variant_override,
            max_tokens=2400,
            request_timeout=180,
        )
        content = sanitize_project_markdown(str(result.content or "").strip())
        parsed = None
    return {
        "agent_type": agent_id,
        "label": agent_role["label"],
        "provider": target.get("provider")
        or getattr(llm, "provider", None)
        or "native_multi_agent",
        "base_url": None,
        "default_model": target.get("display_model") or model_override,
        "model": target.get("display_model") or model_override,
        "variant": variant_override,
        "command": None,
        "command_path": None,
        "duration_ms": None,
        "stdout": "",
        "stderr": "",
        "content": content,
        "parsed": parsed,
        "model_role": target["model_role"],
        "model_source": target["model_source"],
        "engine_id": target.get("engine_id"),
        "engine_label": target.get("engine_label"),
    }


def _maybe_execute_experiment_audit_stage(
    context: WorkflowContext,
    stage: dict[str, Any],
    stage_outputs: dict[str, Any],
    *,
    workspace_path: str | None,
) -> dict[str, Any] | None:
    if context.run.workflow_type.value != "experiment_audit":
        return None
    stage_id = str(stage.get("id") or "").strip()
    resolved_workspace = str(
        workspace_path or context.run.remote_workdir or context.run.workdir or ""
    ).strip()
    if not resolved_workspace:
        raise RuntimeError("当前运行缺少工作区路径，无法执行 experiment_audit")

    if stage_id == "collect_artifacts":
        bundle = _collect_experiment_audit_bundle(context, workspace_path=resolved_workspace)
        return {
            "provider": "workspace_audit_inventory",
            "content": str(bundle.get("inventory_markdown") or "").strip(),
            "stdout": "",
            "stderr": "",
            "model_role": "executor",
            "model_source": "workspace_executor",
        }

    if stage_id == "review_integrity":
        agent_id = (
            str(stage.get("selected_agent_id") or stage.get("default_agent_id") or "").strip()
            or _DEFAULT_AGENT_ID
        )
        role_profile = _resolve_role_profile(agent_id)
        llm = LLMClient()
        target = _resolve_stage_model_target(context, stage_id, role_profile, llm)
        bundle = _collect_experiment_audit_bundle(context, workspace_path=resolved_workspace)
        result = llm.complete_json(
            _build_experiment_audit_prompt(context, bundle),
            stage="project_experiment_audit_review",
            model_override=target["model_override"],
            variant_override=str(
                target.get("variant_override") or role_profile.get("variant") or "medium"
            ),
            max_tokens=2600,
            max_retries=1,
            request_timeout=240,
        )
        audit_payload = _resolve_experiment_audit_payload(bundle, result)
        return {
            "provider": target.get("provider")
            or getattr(llm, "provider", None)
            or "native_multi_agent",
            "model": target.get("display_model") or target["model_override"],
            "default_model": target.get("display_model") or target["model_override"],
            "variant": str(
                target.get("variant_override") or role_profile.get("variant") or "medium"
            ),
            "content": json.dumps(audit_payload, ensure_ascii=False, indent=2),
            "parsed": audit_payload,
            "stdout": "",
            "stderr": "",
            "model_role": target["model_role"],
            "model_source": target["model_source"],
            "engine_id": target.get("engine_id"),
            "engine_label": target.get("engine_label"),
        }

    if stage_id == "issue_audit_report":
        bundle = _collect_experiment_audit_bundle(context, workspace_path=resolved_workspace)
        review_content = str(
            (stage_outputs.get("review_integrity") or {}).get("content") or ""
        ).strip()
        audit_payload = _resolve_experiment_audit_payload(
            bundle,
            LLMResult(content=review_content, parsed_json=_extract_stage_json(review_content)),
        )
        report_markdown = _render_experiment_audit_report(
            context,
            audit_payload=audit_payload,
            workspace_path=resolved_workspace,
        )
        return {
            "provider": "experiment_audit_reporter",
            "content": report_markdown,
            "stdout": "",
            "stderr": "",
            "model_role": "executor",
            "model_source": "workflow_reporter",
        }

    return None
