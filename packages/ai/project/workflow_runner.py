from __future__ import annotations

import json
import logging
import posixpath
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from packages.agent.workspace.workspace_remote import (
    build_remote_overview,
    remote_capture_screen_session,
    remote_launch_screen_job,
    remote_list_screen_sessions,
    remote_probe_gpus,
    remote_prepare_run_environment,
    remote_terminal_result,
    remote_write_file,
)
from packages.agent.workspace.workspace_server_registry import get_workspace_server_entry
from packages.ai.project.aris_skill_templates import render_aris_skill_bundle
from packages.agent.runtime.agent_service import PromptStreamControl, StreamPersistenceConfig, stream_chat
from packages.agent.runtime.agent_backends import DEFAULT_AGENT_BACKEND_ID, normalize_agent_backend_id
from packages.ai.project.amadeus_compat import (
    build_remote_session_name,
    build_run_workspace_path,
    workflow_assistant_skill_id,
)
from packages.ai.project.checkpoint_service import (
    checkpoint_resume_stage,
    mark_run_waiting_for_stage_checkpoint,
)
from packages.ai.project.gpu_lease_service import (
    acquire_gpu_lease,
    list_active_gpu_leases,
    reconcile_gpu_leases,
    release_gpu_lease,
    touch_gpu_lease,
)
from packages.ai.project.workflow_catalog import (
    build_run_orchestration,
    build_stage_trace,
)
from packages.ai.project.experiment_audit_bundle import (
    _collect_experiment_audit_bundle,
)
from packages.ai.project.workflows import literature_review as literature_review_workflow
from packages.ai.project.paper_artifacts import (
    build_paper_improvement_bundle,
    build_figure_bundle,
    build_paper_compile_bundle,
    build_paper_write_bundle,
    build_paper_plan_bundle,
    parse_review_text,
    resolve_paper_venue,
)
from packages.ai.project.output_sanitizer import (
    sanitize_project_artifact_content,
    sanitize_project_markdown,
)
from packages.ai.project.report_formatter import (
    format_auto_review_loop_report,
    format_experiment_report,
    format_full_pipeline_report,
    format_idea_discovery_report,
    format_novelty_check_report,
    format_paper_writing_report,
    format_rebuttal_report,
    format_research_review_report,
)
from packages.ai.project.paper_context import (
    external_candidate_ref,
    format_ref_index_for_prompt,
    load_analysis_reports,
    merge_refs as merge_paper_refs,
    normalize_paper_ids,
    paper_ref_from_model,
    workspace_pdf_ref,
)
from packages.ai.research.research_wiki_service import ResearchWikiService
from packages.agent.session.session_runtime import (
    append_session_message,
    build_user_message_meta,
    delete_session,
    ensure_session_record,
    resolve_default_model_identity,
)
from packages.agent.workspace.workspace_executor import (
    inspect_workspace,
    run_workspace_command,
    write_workspace_file,
)
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.domain.task_tracker import TaskCancelledError, TaskPausedError, global_tracker
from packages.integrations.arxiv_client import ArxivClient
from packages.integrations.llm_engine_profiles import resolve_llm_engine_profile
from packages.integrations.llm_client import LLMClient, LLMResult
from packages.storage.db import session_scope
from packages.storage.repositories import (
    GeneratedContentRepository,
    PaperRepository,
    ProjectRepository,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]
_MISSING = object()
_TOTAL_PROGRESS = 100
_SUPPORTED_WORKFLOWS = {
    ProjectWorkflowType.literature_review,
    ProjectWorkflowType.idea_discovery,
    ProjectWorkflowType.novelty_check,
    ProjectWorkflowType.research_review,
    ProjectWorkflowType.run_experiment,
    ProjectWorkflowType.experiment_audit,
    ProjectWorkflowType.auto_review_loop,
    ProjectWorkflowType.paper_writing,
    ProjectWorkflowType.rebuttal,
    ProjectWorkflowType.full_pipeline,
}
_LLM_ERROR_MARKERS = (
    "未配置模型",
    "模型服务暂不可用",
    "api key 无效",
    "invalid_api_key",
    "connection error",
    "模型连接异常",
)
_REVIEWER_AGENT_DISABLED_TOOLS = {
    "apply_patch",
    "bash",
    "edit",
    "local_shell",
    "multiedit",
    "plan_exit",
    "question",
    "replace_workspace_text",
    "run_workspace_command",
    "task",
    "todowrite",
    "write",
    "write_workspace_file",
}
_ROLE_TEMPLATE_MAP: dict[str, dict[str, str | None]] = {
    "codex": {
        "label": "工程执行角色",
        "model_channel": "deep",
        "variant": "medium",
    },
    "claude_code": {
        "label": "审阅规划角色",
        "model_channel": "deep",
        "variant": "high",
    },
    "gemini": {
        "label": "大上下文归纳角色",
        "model_channel": "skim",
        "variant": "medium",
    },
    "qwen": {
        "label": "中文写作角色",
        "model_channel": "skim",
        "variant": "low",
    },
    "goose": {
        "label": "快速闭环角色",
        "model_channel": "skim",
        "variant": "low",
    },
}


def _normalize_role_id(value: str | None) -> str:
    raw = str(value or "").strip()
    if normalize_agent_backend_id(raw) == DEFAULT_AGENT_BACKEND_ID:
        return "codex"
    return raw or "codex"
_WORKFLOW_STAGE_ALIASES: dict[str, dict[str, list[str]]] = {
    ProjectWorkflowType.idea_discovery.value: {
        "collect_context": ["literature_survey"],
        "expand_directions": ["idea_generation"],
        "verify_novelty": ["deep_novelty_verification"],
        "external_review": ["external_critical_review"],
        "rank_and_persist": ["method_refinement", "final_report"],
    },
    ProjectWorkflowType.run_experiment.value: {
        "inspect_workspace": ["parse_experiment_plan", "implement_experiment_code"],
        "execute_experiment": ["sanity_check", "deploy_full_experiments"],
        "summarize_results": ["collect_initial_results", "handoff_to_auto_review"],
    },
    ProjectWorkflowType.auto_review_loop.value: {
        "plan_cycle": ["initialization", "external_review", "parse_assessment"],
        "execute_cycle": ["implement_fixes", "wait_for_results"],
        "review_cycle": ["document_and_persist", "termination"],
    },
    ProjectWorkflowType.paper_writing.value: {
        "gather_materials": ["paper_plan"],
        "design_figures": ["figure_generation"],
        "draft_sections": ["latex_writing"],
        "compile_manuscript": ["compilation"],
        "polish_manuscript": ["auto_improvement_loop", "final_report"],
    },
}
_WORKFLOW_STAGE_INVOCATION_BINDINGS: dict[str, dict[str, str]] = {
    ProjectWorkflowType.auto_review_loop.value: {
        "review_cycle": "termination",
    },
}


@dataclass
class WorkflowContext:
    run: "RunSnapshot"
    project: "ProjectSnapshot"
    metadata: dict[str, Any]
    selected_papers: list["PaperSnapshot"]
    selected_repos: list["RepoSnapshot"]
    analysis_contexts: dict[str, str]


@dataclass
class RunSnapshot:
    id: str
    workflow_type: ProjectWorkflowType
    prompt: str
    title: str
    max_iterations: int | None
    executor_model: str | None
    reviewer_model: str | None
    task_id: str | None
    started_at: datetime | None
    target_id: str | None
    workspace_server_id: str | None
    workdir: str | None
    remote_workdir: str | None
    run_directory: str | None
    log_path: str | None


@dataclass
class ProjectSnapshot:
    id: str
    name: str
    description: str


@dataclass
class PaperSnapshot:
    id: str
    title: str
    arxiv_id: str
    abstract: str


@dataclass
class RepoSnapshot:
    id: str
    repo_url: str
    local_path: str | None


@dataclass
class ExecutionPlanItem:
    item_id: str
    name: str
    command: str
    metadata_overrides: dict[str, Any]
    source_index: int


WorkflowHandler = Callable[..., dict[str, Any]]


def supports_project_workflow(workflow_type: ProjectWorkflowType | str) -> bool:
    try:
        return ProjectWorkflowType(str(workflow_type)) in _SUPPORTED_WORKFLOWS
    except ValueError:
        return False


def submit_project_run(run_id: str, *, resume_stage_id: str | None = None) -> str | None:
    tracker_metadata: dict[str, Any] | None = None
    retry_metadata: dict[str, Any] | None = None
    retry_run_id = run_id
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found")
        if run.workflow_type not in _SUPPORTED_WORKFLOWS:
            return None

        task_id = run.task_id or f"project_run_{run.id.replace('-', '')[:12]}"
        metadata = dict(run.metadata_json or {})
        resolved_resume_stage_id = str(resume_stage_id or checkpoint_resume_stage(metadata) or "").strip() or None
        orchestration = build_run_orchestration(
            run.workflow_type,
            metadata.get("orchestration"),
            target_id=run.target_id,
            workspace_server_id=run.workspace_server_id,
            reset_stage_status=not bool(resolved_resume_stage_id),
        )
        metadata.update(
            {
                "executor": "project_workflow_runner",
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
            metadata.pop("checkpoint_resume_stage_id", None)
            metadata.pop("checkpoint_resume_stage_label", None)
        project_repo.update_run(
            run.id,
            task_id=task_id,
            status=ProjectRunStatus.running,
            active_phase=resolved_resume_stage_id or "initializing",
            summary="工作流恢复中，正在准备继续执行。" if resolved_resume_stage_id else "工作流已启动，正在准备项目上下文。",
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
            "gpu_mode": metadata.get("gpu_mode"),
            "gpu_strategy": metadata.get("gpu_strategy"),
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
        "project_workflow",
        title,
        run_project_workflow,
        run_id,
        task_id=task_id,
        total=_TOTAL_PROGRESS,
        metadata=tracker_metadata or {},
    )
    global_tracker.register_retry(
        task_id,
        lambda: submit_project_run(retry_run_id),
        label="重新运行",
        metadata=retry_metadata or {},
    )
    return task_id


def run_project_workflow(
    run_id: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    context = _load_context(run_id)
    resume_stage_id = str(checkpoint_resume_stage(context.metadata) or "").strip() or None
    _ensure_run_orchestration(
        run_id,
        context,
        reset_stage_status=not bool(resume_stage_id),
    )
    initial_stage_id = _first_stage_id(context)
    if resume_stage_id:
        _patch_run(
            run_id,
            status=ProjectRunStatus.running,
            active_phase=resume_stage_id,
            summary=f"已批准继续，正在恢复阶段：{_stage_label(context, resume_stage_id)}。",
            started_at=context.run.started_at or datetime.now(UTC),
            finished_at=None,
            metadata_updates={
                "error": None,
                "checkpoint_resume_stage_id": resume_stage_id,
            },
        )
        _emit_progress(progress_callback, f"正在恢复阶段：{_stage_label(context, resume_stage_id)}。", 8)
    else:
        _patch_run(
            run_id,
            status=ProjectRunStatus.running,
            active_phase=initial_stage_id,
            summary="正在收集项目、论文与仓库上下文。",
            started_at=context.run.started_at or datetime.now(UTC),
            finished_at=None,
            metadata_updates={"error": None},
        )
        _set_stage_state(
            run_id,
            initial_stage_id,
            status="running",
            message="正在收集项目、论文与仓库上下文。",
            progress_pct=8,
        )
        _emit_progress(progress_callback, "正在收集项目、论文与仓库上下文。", 8)
    _write_run_log(
        context,
        "\n".join(
            [
                f"# {context.project.name} · {context.run.workflow_type.value}",
                "",
                f"- run_id: {context.run.id}",
                f"- status: running",
                f"- phase: {resume_stage_id or initial_stage_id}",
                f"- updated_at: {_iso_now()}",
                f"- prompt: {context.run.prompt.strip()[:800]}",
            ]
        ).strip()
        + "\n",
    )

    try:
        handler = _resolve_workflow_handler(context.run.workflow_type)
        if handler is None:
            raise ValueError(f"unsupported project workflow: {context.run.workflow_type}")
        return handler(
            context,
            progress_callback,
            resume_stage_id=resume_stage_id,
        )
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
        logger.exception("Project workflow failed: %s", run_id)
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


def _resolve_workflow_handler(workflow_type: ProjectWorkflowType | str) -> WorkflowHandler | None:
    try:
        normalized = ProjectWorkflowType(str(workflow_type))
    except ValueError:
        return None
    return {
        ProjectWorkflowType.literature_review: _execute_literature_review,
        ProjectWorkflowType.idea_discovery: _execute_idea_discovery_workflow,
        ProjectWorkflowType.novelty_check: _execute_novelty_check,
        ProjectWorkflowType.research_review: _execute_research_review,
        ProjectWorkflowType.run_experiment: _execute_run_experiment_workflow,
        ProjectWorkflowType.experiment_audit: _execute_experiment_audit,
        ProjectWorkflowType.auto_review_loop: _execute_auto_review_loop_workflow,
        ProjectWorkflowType.paper_writing: _execute_paper_writing_workflow,
        ProjectWorkflowType.rebuttal: _execute_rebuttal_workflow,
        ProjectWorkflowType.full_pipeline: _execute_full_pipeline_workflow,
    }.get(normalized)


def _execute_idea_discovery_workflow(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflows.idea_discovery import execute

    return execute(*args, **kwargs)


def _execute_run_experiment_workflow(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflows.run_experiment import execute

    return execute(*args, **kwargs)


def _execute_auto_review_loop_workflow(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflows.auto_review_loop import execute

    return execute(*args, **kwargs)


def _execute_paper_writing_workflow(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflows.paper_writing import execute

    return execute(*args, **kwargs)


def _execute_rebuttal_workflow(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflows.rebuttal import execute

    return execute(*args, **kwargs)


def _execute_full_pipeline_workflow(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflows.full_pipeline import execute

    return execute(*args, **kwargs)


def _execute_literature_review(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    return literature_review_workflow.execute_literature_review(
        context,
        progress_callback,
        resume_stage_id=resume_stage_id,
        runtime=sys.modules[__name__],
    )


def _execute_idea_discovery(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    later_from_landscape = {"expand_directions", "verify_novelty", "external_review", "rank_and_persist"}
    later_from_ideas = {"verify_novelty", "external_review", "rank_and_persist"}
    later_from_novelty = {"external_review", "rank_and_persist"}
    paper_id_to_ref = {paper.id: f"P{index}" for index, paper in enumerate(context.selected_papers, start=1)}
    idea_stage_output = _stage_output_payload(context, "expand_directions")
    review_stage_output = _stage_output_payload(context, "external_review")

    literature_markdown = ""
    if resume_stage_id in later_from_landscape:
        literature_markdown = _stage_output_content(context, "collect_context")
        if not literature_markdown:
            raise RuntimeError("恢复想法发现失败：缺少文献调研阶段产物。")
    else:
        _patch_run(run.id, active_phase="collect_context", summary="正在执行文献调研与范围确认。")
        _set_stage_state(
            run.id,
            "collect_context",
            status="running",
            message="正在执行文献调研与范围确认。",
            progress_pct=16,
        )
        _emit_progress(progress_callback, "正在执行文献调研与范围确认。", 16)
        landscape_execution = _invoke_role_markdown(
            context,
            "collect_context",
            _build_idea_landscape_prompt(context),
            stage="project_idea_discovery_literature",
            max_tokens=2600,
            request_timeout=220,
        )
        literature_markdown = _resolve_generic_markdown(
            landscape_execution["result"],
            fallback=_build_literature_review_fallback(context, landscape_execution["result"].content or ""),
        )
        _record_stage_output(
            run.id,
            "collect_context",
            {
                "summary": _markdown_excerpt(literature_markdown),
                "content": literature_markdown,
                "provider": landscape_execution.get("provider"),
                "model": landscape_execution.get("model"),
                "variant": landscape_execution.get("variant"),
                "model_role": landscape_execution.get("model_role"),
                "model_source": landscape_execution.get("model_source"),
                "role_template_id": landscape_execution.get("role_template_id"),
            },
        )
        _set_stage_state(
            run.id,
            "collect_context",
            status="completed",
            message="文献调研完成，准备生成候选想法。",
            progress_pct=28,
        )
        _maybe_pause_after_stage(
            context,
            "collect_context",
            "expand_directions",
            stage_summary=_markdown_excerpt(literature_markdown),
        )

    idea_json_text = ""
    idea_stage_payload: dict[str, Any] = {}
    if resume_stage_id in later_from_ideas:
        idea_json_text = _stage_output_content(context, "expand_directions")
        if not idea_json_text:
            raise RuntimeError("恢复想法发现失败：缺少想法生成阶段产物。")
        idea_stage_payload = _parse_json_payload_text(idea_json_text)
        idea_stage_output = _stage_output_payload(context, "expand_directions")
    else:
        _patch_run(run.id, active_phase="expand_directions", summary="正在生成候选想法并做首轮筛选。")
        _set_stage_state(
            run.id,
            "expand_directions",
            status="running",
            message="正在生成候选想法并做首轮筛选。",
            progress_pct=40,
        )
        _emit_progress(progress_callback, "正在生成候选想法并做首轮筛选。", 40)
        idea_execution = _invoke_role_json(
            context,
            "expand_directions",
            _build_idea_generation_prompt(context, literature_markdown),
            stage="project_idea_discovery_ideas",
            max_tokens=2600,
            max_retries=1,
            request_timeout=220,
        )
        llm_result = idea_execution["result"]
        normalized_ideas = _resolve_idea_payloads(context, llm_result)
        idea_stage_payload = {
            "ideas": [
                {
                    "title": item["title"],
                    "content": item["content"],
                    "paper_refs": [paper_id_to_ref[paper_id] for paper_id in item["paper_ids"] if paper_id in paper_id_to_ref],
                }
                for item in normalized_ideas
            ]
        }
        idea_json_text = json.dumps(idea_stage_payload, ensure_ascii=False, indent=2)
        _record_stage_output(
            run.id,
            "expand_directions",
            {
                "summary": f"已生成 {len(normalized_ideas)} 条候选想法并完成首轮筛选。",
                "content": idea_json_text,
                "provider": idea_execution.get("provider"),
                "model": idea_execution.get("model"),
                "variant": idea_execution.get("variant"),
                "model_role": idea_execution.get("model_role"),
                "model_source": idea_execution.get("model_source"),
                "role_template_id": idea_execution.get("role_template_id"),
                "llm_mode": _llm_mode(llm_result),
            },
        )
        idea_stage_output = {
            "provider": idea_execution.get("provider"),
            "model": idea_execution.get("model"),
            "variant": idea_execution.get("variant"),
            "model_role": idea_execution.get("model_role"),
            "model_source": idea_execution.get("model_source"),
            "role_template_id": idea_execution.get("role_template_id"),
            "llm_mode": _llm_mode(llm_result),
        }
        _set_stage_state(
            run.id,
            "expand_directions",
            status="completed",
            message="候选想法已生成，准备进入深度查新。",
            progress_pct=52,
        )
        _maybe_pause_after_stage(
            context,
            "expand_directions",
            "verify_novelty",
            stage_summary=f"已生成 {len(normalized_ideas)} 条候选想法。",
        )

    idea_llm_result = LLMResult(content=idea_json_text, parsed_json=idea_stage_payload or _parse_json_payload_text(idea_json_text))
    ideas_payload = _resolve_idea_payloads(context, idea_llm_result)

    novelty_markdown = ""
    if resume_stage_id in later_from_novelty:
        novelty_markdown = _stage_output_content(context, "verify_novelty")
        if not novelty_markdown:
            raise RuntimeError("恢复想法发现失败：缺少深度查新阶段产物。")
    else:
        _patch_run(run.id, active_phase="verify_novelty", summary="正在执行 top ideas 的深度查新。")
        _set_stage_state(
            run.id,
            "verify_novelty",
            status="running",
            message="正在执行 top ideas 的深度查新。",
            progress_pct=64,
        )
        _emit_progress(progress_callback, "正在执行 top ideas 的深度查新。", 64)
        novelty_execution = _invoke_role_markdown(
            context,
            "verify_novelty",
            _build_idea_novelty_verification_prompt(context, literature_markdown, idea_json_text),
            stage="project_idea_discovery_novelty",
            max_tokens=2600,
            request_timeout=220,
        )
        novelty_markdown = _resolve_generic_markdown(
            novelty_execution["result"],
            fallback=(
                "# Deep Novelty Verification\n\n"
                "- 当前未拿到完整 novelty 输出，建议补充与最近 6 个月 arXiv / 顶会论文的人工核对。\n"
                f"- 候选 idea 数量: {len(ideas_payload)}"
            ),
        )
        _record_stage_output(
            run.id,
            "verify_novelty",
            {
                "summary": _markdown_excerpt(novelty_markdown),
                "content": novelty_markdown,
                "provider": novelty_execution.get("provider"),
                "model": novelty_execution.get("model"),
                "variant": novelty_execution.get("variant"),
                "model_role": novelty_execution.get("model_role"),
                "model_source": novelty_execution.get("model_source"),
                "role_template_id": novelty_execution.get("role_template_id"),
            },
        )
        _set_stage_state(
            run.id,
            "verify_novelty",
            status="completed",
            message="深度查新完成，准备进入外部评审。",
            progress_pct=74,
        )

    review_markdown = ""
    if resume_stage_id == "rank_and_persist":
        review_markdown = _stage_output_content(context, "external_review")
        if not review_markdown:
            raise RuntimeError("恢复想法发现失败：缺少外部评审阶段产物。")
        review_stage_output = _stage_output_payload(context, "external_review")
    else:
        _patch_run(run.id, active_phase="external_review", summary="正在获取外部 reviewer 视角反馈。")
        _set_stage_state(
            run.id,
            "external_review",
            status="running",
            message="正在获取外部 reviewer 视角反馈。",
            progress_pct=84,
        )
        _emit_progress(progress_callback, "正在获取外部 reviewer 视角反馈。", 84)
        review_execution = _invoke_role_markdown(
            context,
            "external_review",
            _build_idea_external_review_prompt(context, idea_json_text, novelty_markdown),
            stage="project_idea_discovery_review",
            max_tokens=2400,
            request_timeout=220,
        )
        review_markdown = _resolve_generic_markdown(
            review_execution["result"],
            fallback=(
                "# External Critical Review\n\n"
                "- 建议优先选择 pilot signal 最强、且 novelty 风险最低的方案进入实现。\n"
                "- 当前 reviewer fallback 未形成完整打分，请补充人工审阅。"
            ),
        )
        _record_stage_output(
            run.id,
            "external_review",
            {
                "summary": _markdown_excerpt(review_markdown),
                "content": review_markdown,
                "provider": review_execution.get("provider"),
                "model": review_execution.get("model"),
                "variant": review_execution.get("variant"),
                "model_role": review_execution.get("model_role"),
                "model_source": review_execution.get("model_source"),
                "role_template_id": review_execution.get("role_template_id"),
            },
        )
        review_stage_output = {
            "provider": review_execution.get("provider"),
            "model": review_execution.get("model"),
            "variant": review_execution.get("variant"),
            "model_role": review_execution.get("model_role"),
            "model_source": review_execution.get("model_source"),
            "role_template_id": review_execution.get("role_template_id"),
        }
        _set_stage_state(
            run.id,
            "external_review",
            status="completed",
            message="外部评审完成，准备生成 IDEA_REPORT。",
            progress_pct=92,
        )

    created_ideas: list[dict[str, Any]] = []
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        for item in ideas_payload:
            idea = project_repo.create_idea(
                project_id=context.project.id,
                title=item["title"][:512],
                content=item["content"],
                paper_ids=item["paper_ids"],
            )
            created_ideas.append(
                {
                    "id": idea.id,
                    "title": idea.title,
                    "content": idea.content,
                    "paper_ids": list(idea.paper_ids_json or []),
                    "pilot_signal": str(item.get("pilot_signal") or "SKIPPED").strip().upper() or "SKIPPED",
                    "ranking_reason": str(item.get("ranking_reason") or "").strip(),
                    "origin_skill": "idea-discovery",
                }
            )
    ResearchWikiService().upsert_idea_nodes(
        project_id=context.project.id,
        ideas=created_ideas,
        source_run_id=run.id,
    )

    final_markdown = _render_idea_discovery_report(
        context,
        literature_markdown=literature_markdown,
        created_ideas=created_ideas,
        novelty_markdown=novelty_markdown,
        review_markdown=review_markdown,
    )
    artifact_refs: list[dict[str, Any]] = []
    idea_report_artifact = _write_run_artifact(context, "IDEA_REPORT.md", final_markdown, kind="report")
    if idea_report_artifact:
        artifact_refs.append(idea_report_artifact)
    summary = f"已完成想法发现流程，生成 {len(created_ideas)} 条研究想法并写入 IDEA_REPORT。"
    metadata_updates = {
        "workflow_output_markdown": final_markdown,
        "workflow_output_excerpt": summary,
        "created_idea_ids": [item["id"] for item in created_ideas],
        "created_ideas": created_ideas,
        "paper_ids": [paper.id for paper in context.selected_papers],
        "repo_ids": [repo.id for repo in context.selected_repos],
        "llm_mode": str(idea_stage_output.get("llm_mode") or "llm"),
        "artifact_refs": artifact_refs,
        "completed_at": _iso_now(),
    }
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=summary,
        finished_at=datetime.now(UTC),
        metadata_updates=metadata_updates,
    )
    _set_stage_state(
        run.id,
        "rank_and_persist",
        status="completed",
        message="IDEA_REPORT 已生成，研究想法已写回项目。",
        progress_pct=100,
    )
    _record_stage_output(
        run.id,
        "rank_and_persist",
        {
            "summary": summary,
            "content": final_markdown,
            "provider": review_stage_output.get("provider"),
            "model": review_stage_output.get("model"),
            "variant": review_stage_output.get("variant"),
            "model_role": review_stage_output.get("model_role") or "executor",
            "model_source": review_stage_output.get("model_source"),
            "role_template_id": review_stage_output.get("role_template_id"),
            "created_idea_ids": [item["id"] for item in created_ideas],
            "artifact_refs": artifact_refs,
        },
    )
    _emit_progress(progress_callback, "想法发现已完成。", 100)

    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": summary,
        "markdown": final_markdown,
        "created_ideas": created_ideas,
        "artifact_refs": artifact_refs,
    }
    if run.task_id:
        global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        global_tracker.set_result(run.task_id, result)
    return result


def _execute_novelty_check(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    materials = _build_writing_materials(context)
    _set_stage_state(
        run.id,
        "collect_claims",
        status="completed",
        message="项目主张与已有材料已整理完成。",
        progress_pct=20,
    )
    _record_stage_output(
        run.id,
        "collect_claims",
        {
            "summary": "项目主张与已有材料已整理完成",
            "content": materials[:5000],
            "provider": "project_materials",
            "model_role": _stage_model_role(context, "collect_claims"),
            "model_source": "project_materials",
            "role_template_id": _stage_role_id(context, "collect_claims"),
        },
    )

    compare_payload = _stage_output_payload(context, "compare_prior_work")
    if resume_stage_id == "issue_novelty_report":
        comparison_markdown = _stage_output_content(context, "compare_prior_work")
        if not comparison_markdown:
            raise RuntimeError("恢复查新评估失败：缺少相近工作对比阶段产物。")
        compare_execution = compare_payload
    else:
        _patch_run(run.id, active_phase="compare_prior_work", summary="正在对比最相近的已有工作。")
        _set_stage_state(
            run.id,
            "compare_prior_work",
            status="running",
            message="正在对比最相近的已有工作。",
            progress_pct=42,
        )
        _emit_progress(progress_callback, "正在对比最相近的已有工作。", 42)
        compare_execution = _invoke_role_markdown(
            context,
            "compare_prior_work",
            _build_novelty_check_prompt(context, materials),
            stage="project_novelty_check_compare",
            max_tokens=2200,
            request_timeout=200,
        )
        comparison_markdown = _resolve_generic_markdown(
            compare_execution["result"],
            fallback=(
                f"# {context.project.name} 查新对比\n\n"
                "## 初步判断\n"
                "- 当前材料已整理完成，但模型未返回完整对比结果。\n"
                "- 建议手动核对最相近论文与当前主张的差异点。\n"
            ),
        )
        _record_stage_output(
            run.id,
            "compare_prior_work",
            {
                "summary": _markdown_excerpt(comparison_markdown),
                "content": comparison_markdown,
                "provider": compare_execution.get("provider"),
                "model": compare_execution.get("model"),
                "variant": compare_execution.get("variant"),
                "model_role": compare_execution.get("model_role"),
                "model_source": compare_execution.get("model_source"),
                "role_template_id": compare_execution.get("role_template_id"),
            },
        )
        _set_stage_state(
            run.id,
            "compare_prior_work",
            status="completed",
            message="相近工作对比完成，正在输出查新报告。",
            progress_pct=68,
        )
        _maybe_pause_after_stage(
            context,
            "compare_prior_work",
            "issue_novelty_report",
            stage_summary=_markdown_excerpt(comparison_markdown),
        )

    _patch_run(run.id, active_phase="issue_novelty_report", summary="正在生成查新报告。")
    _set_stage_state(
        run.id,
        "issue_novelty_report",
        status="running",
        message="正在生成查新报告。",
        progress_pct=84,
    )
    _emit_progress(progress_callback, "正在生成查新报告。", 84)
    report_execution = _invoke_role_markdown(
        context,
        "issue_novelty_report",
        _build_novelty_report_prompt(context, comparison_markdown),
        stage="project_novelty_check_report",
        max_tokens=2400,
        request_timeout=220,
    )
    final_markdown = _resolve_generic_markdown(
        report_execution["result"],
        fallback=(
            f"# {context.project.name} 查新报告\n\n"
            f"{comparison_markdown}\n\n"
            "## 结论\n- 建议进一步补齐与最相近工作的定量和机制差异。"
        ),
    )
    final_markdown = format_novelty_check_report(context.project.name, run.prompt, comparison_markdown, final_markdown)
    artifact_refs: list[dict[str, Any]] = []
    report_artifact = _write_run_artifact(context, "reports/novelty-check.md", final_markdown, kind="report")
    if report_artifact:
        artifact_refs.append(report_artifact)
    excerpt = _markdown_excerpt(final_markdown)
    _record_stage_output(
        run.id,
        "issue_novelty_report",
        {
            "summary": excerpt,
            "content": final_markdown,
            "provider": report_execution.get("provider"),
            "model": report_execution.get("model"),
            "variant": report_execution.get("variant"),
            "model_role": report_execution.get("model_role"),
            "model_source": report_execution.get("model_source"),
            "role_template_id": report_execution.get("role_template_id"),
            "artifact_refs": artifact_refs,
        },
    )
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "查新评估已完成。",
        finished_at=datetime.now(UTC),
        metadata_updates={
            "workflow_output_markdown": final_markdown,
            "workflow_output_excerpt": excerpt,
            "artifact_refs": artifact_refs,
            "completed_at": _iso_now(),
        },
    )
    _set_stage_state(
        run.id,
        "issue_novelty_report",
        status="completed",
        message="查新报告已生成。",
        progress_pct=100,
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": final_markdown,
        "artifact_refs": artifact_refs,
    }
    if run.task_id:
        global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        global_tracker.set_result(run.task_id, result)
    return result


def _execute_research_review(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    materials = _build_writing_materials(context)
    _set_stage_state(
        run.id,
        "collect_submission",
        status="completed",
        message="评审资料已整理完成。",
        progress_pct=22,
    )
    _record_stage_output(
        run.id,
        "collect_submission",
        {
            "summary": "评审资料已整理完成",
            "content": materials[:5000],
            "provider": "project_materials",
            "model_role": _stage_model_role(context, "collect_submission"),
            "model_source": "project_materials",
            "role_template_id": _stage_role_id(context, "collect_submission"),
        },
    )

    review_payload = _stage_output_payload(context, "review_submission")
    if resume_stage_id == "deliver_verdict":
        review_markdown = _stage_output_content(context, "review_submission")
        if not review_markdown:
            raise RuntimeError("恢复研究评审失败：缺少评审意见阶段产物。")
        review_execution = review_payload
    else:
        _patch_run(run.id, active_phase="review_submission", summary="正在形成研究评审意见。")
        _set_stage_state(
            run.id,
            "review_submission",
            status="running",
            message="正在形成研究评审意见。",
            progress_pct=48,
        )
        _emit_progress(progress_callback, "正在形成研究评审意见。", 48)
        review_execution = _invoke_role_markdown(
            context,
            "review_submission",
            _build_research_review_prompt(context, materials),
            stage="project_research_review",
            max_tokens=2600,
            request_timeout=220,
        )
        review_markdown = _resolve_generic_markdown(
            review_execution["result"],
            fallback=(
                f"# {context.project.name} 研究评审\n\n"
                "## 总评\n- 当前材料已整理，但模型未返回完整评审结果。\n"
            ),
        )
        _record_stage_output(
            run.id,
            "review_submission",
            {
                "summary": _markdown_excerpt(review_markdown),
                "content": review_markdown,
                "provider": review_execution.get("provider"),
                "model": review_execution.get("model"),
                "variant": review_execution.get("variant"),
                "model_role": review_execution.get("model_role"),
                "model_source": review_execution.get("model_source"),
                "role_template_id": review_execution.get("role_template_id"),
            },
        )
        _set_stage_state(
            run.id,
            "review_submission",
            status="completed",
            message="评审意见已生成，正在整理最终结论。",
            progress_pct=72,
        )
        _maybe_pause_after_stage(
            context,
            "review_submission",
            "deliver_verdict",
            stage_summary=_markdown_excerpt(review_markdown),
        )

    _patch_run(run.id, active_phase="deliver_verdict", summary="正在输出研究评审结论。")
    _set_stage_state(
        run.id,
        "deliver_verdict",
        status="running",
        message="正在输出研究评审结论。",
        progress_pct=86,
    )
    _emit_progress(progress_callback, "正在输出研究评审结论。", 86)
    verdict_execution = _invoke_role_markdown(
        context,
        "deliver_verdict",
        _build_research_review_verdict_prompt(context, review_markdown),
        stage="project_research_review_verdict",
        max_tokens=2400,
        request_timeout=220,
    )
    final_markdown = _resolve_generic_markdown(verdict_execution["result"], fallback=review_markdown)
    final_markdown = format_research_review_report(context.project.name, run.prompt, review_markdown, final_markdown)
    artifact_refs: list[dict[str, Any]] = []
    report_artifact = _write_run_artifact(context, "reports/research-review.md", final_markdown, kind="report")
    if report_artifact:
        artifact_refs.append(report_artifact)
    excerpt = _markdown_excerpt(final_markdown)
    _record_stage_output(
        run.id,
        "deliver_verdict",
        {
            "summary": excerpt,
            "content": final_markdown,
            "provider": verdict_execution.get("provider"),
            "model": verdict_execution.get("model"),
            "variant": verdict_execution.get("variant"),
            "model_role": verdict_execution.get("model_role"),
            "model_source": verdict_execution.get("model_source"),
            "role_template_id": verdict_execution.get("role_template_id"),
            "artifact_refs": artifact_refs,
        },
    )
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "研究评审已完成。",
        finished_at=datetime.now(UTC),
        metadata_updates={
            "workflow_output_markdown": final_markdown,
            "workflow_output_excerpt": excerpt,
            "artifact_refs": artifact_refs,
            "completed_at": _iso_now(),
        },
    )
    _set_stage_state(
        run.id,
        "deliver_verdict",
        status="completed",
        message="研究评审报告已生成。",
        progress_pct=100,
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": final_markdown,
        "artifact_refs": artifact_refs,
    }
    if run.task_id:
        global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        global_tracker.set_result(run.task_id, result)
    return result


def _execute_auto_review_loop(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    max_iterations = max(1, min(run.max_iterations or 3, 8))
    workspace_path = _resolve_workspace_path(run)
    artifact_refs: list[dict[str, Any]] = []
    review_thread_id = _stable_workflow_thread_id(context, "auto_review_thread_id", "auto-review")
    existing_iteration_reports = [
        dict(item)
        for item in context.metadata.get("iterations", [])
        if isinstance(item, dict)
    ]
    start_iteration = 1
    if resume_stage_id:
        plan_markdown = _stage_output_content(context, "plan_cycle")
        if not plan_markdown:
            raise RuntimeError("恢复自动评审循环失败：缺少循环计划产物。")
        try:
            start_iteration = max(1, int(context.metadata.get("checkpoint_resume_iteration") or 1))
        except (TypeError, ValueError):
            start_iteration = 1
    else:
        _patch_run(run.id, active_phase="plan_cycle", summary="正在规划自动评审循环。")
        _set_stage_state(
            run.id,
            "plan_cycle",
            status="running",
            message="正在规划自动评审循环。",
            progress_pct=12,
        )
        _emit_progress(progress_callback, "正在规划自动评审循环。", 12)
        plan_execution = _invoke_role_markdown(
            context,
            "plan_cycle",
            _build_auto_review_plan_prompt(context, max_iterations=max_iterations),
            stage="project_auto_review_loop_plan",
            max_tokens=1800,
            request_timeout=180,
        )
        plan_markdown = _resolve_generic_markdown(
            plan_execution["result"],
            fallback=f"# 自动评审循环计划\n\n- 最大迭代轮次: {max_iterations}\n- 用户目标: {run.prompt}",
        )
        _record_stage_output(
            run.id,
            "plan_cycle",
            {
                "summary": _markdown_excerpt(plan_markdown),
                "content": plan_markdown,
                "provider": plan_execution.get("provider"),
                "model": plan_execution.get("model"),
                "variant": plan_execution.get("variant"),
                "model_role": plan_execution.get("model_role"),
                "model_source": plan_execution.get("model_source"),
                "role_template_id": plan_execution.get("role_template_id"),
            },
        )
        _set_stage_state(
            run.id,
            "plan_cycle",
            status="completed",
            message="循环计划已生成。",
            progress_pct=24,
        )
        initial_state = {
            "round": 0,
            "threadId": review_thread_id,
            "status": "in_progress",
            "last_score": None,
            "last_verdict": None,
            "pending_experiments": [],
            "timestamp": _iso_now(),
        }
        state_artifact = _write_run_json_artifact(context, "REVIEW_STATE.json", initial_state, kind="artifact")
        if state_artifact:
            artifact_refs.append(state_artifact)
        _patch_run(
            run.id,
            metadata_updates={
                "artifact_refs": _dedupe_artifact_refs(artifact_refs),
                "auto_review_thread_id": review_thread_id,
            },
        )
        _maybe_pause_after_stage(
            context,
            "plan_cycle",
            "execute_cycle",
            stage_summary=_markdown_excerpt(plan_markdown),
        )

    command = _resolve_execution_command(context, allow_default=False)
    effective_command, runtime_environment = _wrap_command_with_runtime_environment(context, command)
    command_workspace_path = _resolve_execution_workspace_path(context)
    runtime_environment = {
        **runtime_environment,
        "command_workspace_path": command_workspace_path or workspace_path,
    }
    iteration_reports: list[dict[str, Any]] = list(existing_iteration_reports)
    review_markdown_parts = [plan_markdown]
    if existing_iteration_reports:
        review_markdown_parts.extend(_auto_review_iteration_markdown(item) for item in existing_iteration_reports)
    for iteration in range(start_iteration, max_iterations + 1):
        progress_base = 24 + int((iteration - 1) * (48 / max_iterations))
        _patch_run(run.id, active_phase="execute_cycle", summary=f"正在执行第 {iteration} 轮任务。")
        _set_stage_state(
            run.id,
            "execute_cycle",
            status="running",
            message=f"正在执行第 {iteration} 轮任务。",
            progress_pct=min(progress_base + 8, 78),
        )
        _emit_progress(progress_callback, f"正在执行第 {iteration} 轮任务。", min(progress_base + 8, 78))

        execution_markdown = ""
        execution_payload: dict[str, Any] = {}
        execute_execution: dict[str, Any] | None = None
        if command and command_workspace_path:
            if command_workspace_path != workspace_path:
                execution = _run_workspace_command_for_context(
                    context,
                    effective_command,
                    timeout_sec=_resolve_execution_timeout(context),
                    workspace_path_override=command_workspace_path,
                )
            else:
                execution = _run_workspace_command_for_context(
                    context,
                    effective_command,
                    timeout_sec=_resolve_execution_timeout(context),
                )
            execution["original_command"] = command
            execution["effective_command"] = effective_command
            execution["runtime_environment"] = runtime_environment
            execution["command_workspace_path"] = command_workspace_path
            execution_markdown = _command_result_preview(execution)
            execution_payload = {
                "command": command,
                "effective_command": effective_command,
                "exit_code": execution.get("exit_code"),
                "stdout": execution.get("stdout"),
                "stderr": execution.get("stderr"),
                "success": execution.get("success"),
                "runtime_environment": runtime_environment,
                "command_workspace_path": command_workspace_path,
            }
        else:
            execute_execution = _invoke_role_markdown(
                context,
                "execute_cycle",
                _build_auto_review_execute_prompt(context, plan_markdown, iteration=iteration, previous_reviews=review_markdown_parts[1:]),
                stage=f"project_auto_review_loop_execute_{iteration}",
                max_tokens=1800,
                request_timeout=180,
            )
            execution_markdown = _resolve_generic_markdown(
                execute_execution["result"],
                fallback=f"## 第 {iteration} 轮执行\n- 未提供工作区命令，已改为基于上下文生成本轮执行摘要。",
            )
            execution_payload = {
                "provider": execute_execution.get("provider"),
                "model": execute_execution.get("model"),
                "variant": execute_execution.get("variant"),
                "model_role": execute_execution.get("model_role"),
                "model_source": execute_execution.get("model_source"),
            }

        _record_stage_output(
            run.id,
            "execute_cycle",
            {
                "summary": _markdown_excerpt(execution_markdown),
                "content": execution_markdown,
                "provider": (
                    "workspace_executor_remote" if run.workspace_server_id else "workspace_executor_local"
                )
                if command and command_workspace_path
                else execute_execution.get("provider") if execute_execution else None,
                "model": execute_execution.get("model") if execute_execution else None,
                "variant": execute_execution.get("variant") if execute_execution else None,
                "model_role": (
                    _stage_model_role(context, "execute_cycle")
                    if command and command_workspace_path
                    else execute_execution.get("model_role") if execute_execution else _stage_model_role(context, "execute_cycle")
                ),
                "model_source": (
                    "workspace_executor"
                    if command and command_workspace_path
                    else execute_execution.get("model_source") if execute_execution else None
                ),
                "role_template_id": _stage_role_id(context, "execute_cycle"),
                "iteration": iteration,
                "execution": execution_payload,
            },
        )
        _set_stage_state(
            run.id,
            "execute_cycle",
            status="completed",
            message=f"第 {iteration} 轮执行已完成，准备进入评审。",
            progress_pct=min(progress_base + 14, 84),
        )

        _patch_run(run.id, active_phase="review_cycle", summary=f"正在评审第 {iteration} 轮结果。")
        _set_stage_state(
            run.id,
            "review_cycle",
            status="running",
            message=f"正在评审第 {iteration} 轮结果。",
            progress_pct=min(progress_base + 18, 92),
        )
        review_execution = _invoke_role_json(
            context,
            "review_cycle",
            _build_auto_review_json_prompt(context, plan_markdown, execution_markdown, iteration=iteration),
            stage=f"project_auto_review_loop_review_{iteration}",
            max_tokens=1400,
            max_retries=1,
            request_timeout=180,
        )
        review_payload = _resolve_auto_review_payload(review_execution["result"], iteration=iteration)
        iteration_report = {
            "iteration": iteration,
            "execution": execution_payload,
            "execution_summary": execution_markdown,
            "review": review_payload,
            "review_model": review_execution.get("model"),
            "review_provider": review_execution.get("provider"),
            "review_variant": review_execution.get("variant"),
            "model_role": review_execution.get("model_role"),
            "model_source": review_execution.get("model_source"),
        }
        iteration_reports.append(iteration_report)
        review_markdown_parts.append(_auto_review_iteration_markdown(iteration_report))
        round_markdown = "# AUTO_REVIEW\n\n" + "\n\n".join(review_markdown_parts)
        state_payload = {
            "round": iteration,
            "threadId": review_thread_id,
            "status": "in_progress",
            "last_score": review_payload.get("score"),
            "last_verdict": review_payload.get("verdict"),
            "pending_experiments": review_payload.get("pending_experiments") or [],
            "timestamp": _iso_now(),
        }
        auto_review_artifact = _write_run_artifact(context, "AUTO_REVIEW.md", round_markdown, kind="report")
        if auto_review_artifact:
            artifact_refs.append(auto_review_artifact)
        state_artifact = _write_run_json_artifact(context, "REVIEW_STATE.json", state_payload, kind="artifact")
        if state_artifact:
            artifact_refs.append(state_artifact)
        _patch_run(
            run.id,
            summary=f"自动评审已完成第 {iteration} 轮，当前 verdict: {review_payload.get('verdict') or 'not ready'}。",
            metadata_updates={
                "iterations": iteration_reports,
                "artifact_refs": _dedupe_artifact_refs(artifact_refs),
                "checkpoint_resume_iteration": None,
                "last_auto_review_round": iteration,
                "auto_review_thread_id": review_thread_id,
            },
        )
        if (
            bool(context.metadata.get("human_checkpoint_enabled"))
            and str(review_payload.get("verdict") or "").strip().lower() == "almost"
            and iteration < max_iterations
        ):
            _patch_run(
                run.id,
                metadata_updates={
                    "checkpoint_resume_iteration": iteration + 1,
                    "iterations": iteration_reports,
                    "artifact_refs": _dedupe_artifact_refs(artifact_refs),
                },
            )
            task_id = str(context.run.task_id or "").strip()
            if not task_id:
                raise RuntimeError("阶段确认失败：当前运行缺少任务追踪标识。")
            mark_run_waiting_for_stage_checkpoint(
                run.id,
                task_id=task_id,
                completed_stage_id="review_cycle",
                completed_stage_label=f"第 {iteration} 轮评审（almost）",
                resume_stage_id="execute_cycle",
                resume_stage_label=f"第 {iteration + 1} 轮执行",
                stage_summary=(
                    f"第 {iteration} 轮 verdict=almost，score={review_payload.get('score')}，"
                    f"如需继续将进入第 {iteration + 1} 轮。"
                ),
            )
            raise TaskPausedError(f"第 {iteration} 轮评审已达到 almost，等待人工确认是否继续。")
        if not review_payload["continue"]:
            break

    final_markdown = "# 自动评审循环报告\n\n" + "\n\n".join(review_markdown_parts)
    report_markdown = format_auto_review_loop_report(
        context.project.name,
        run.prompt,
        {
            "iterations": iteration_reports,
            "execution_command": command,
            "effective_execution_command": effective_command,
            "execution_workspace": command_workspace_path or workspace_path,
            "stage_outputs": {
                "plan_cycle": {"content": plan_markdown},
                "review_cycle": {"content": final_markdown},
            },
        },
        final_markdown,
    )
    report_artifact = _write_run_artifact(context, "reports/auto-review-loop.md", report_markdown, kind="report")
    if report_artifact:
        artifact_refs.append(report_artifact)
    auto_review_artifact = _write_run_artifact(context, "AUTO_REVIEW.md", final_markdown, kind="report")
    if auto_review_artifact:
        artifact_refs.append(auto_review_artifact)
    final_state_artifact = _write_run_json_artifact(
        context,
        "REVIEW_STATE.json",
        {
            "round": len(iteration_reports),
            "threadId": review_thread_id,
            "status": "completed",
            "last_score": (iteration_reports[-1].get("review") or {}).get("score") if iteration_reports else None,
            "last_verdict": (iteration_reports[-1].get("review") or {}).get("verdict") if iteration_reports else None,
            "pending_experiments": (iteration_reports[-1].get("review") or {}).get("pending_experiments") if iteration_reports else [],
            "timestamp": _iso_now(),
        },
        kind="artifact",
    )
    if final_state_artifact:
        artifact_refs.append(final_state_artifact)
    artifact_refs = _dedupe_artifact_refs(artifact_refs)
    excerpt = _markdown_excerpt(report_markdown)
    _record_stage_output(
        run.id,
        "review_cycle",
        {
            "summary": excerpt,
            "content": report_markdown,
            "provider": iteration_reports[-1].get("review_provider") if iteration_reports else None,
            "model": iteration_reports[-1].get("review_model") if iteration_reports else None,
            "variant": iteration_reports[-1].get("review_variant") if iteration_reports else None,
            "model_role": "reviewer",
            "model_source": iteration_reports[-1].get("model_source") if iteration_reports else None,
            "role_template_id": _stage_role_id(context, "review_cycle"),
            "artifact_refs": artifact_refs,
            "iterations": iteration_reports,
        },
    )
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "自动评审循环已完成。",
        finished_at=datetime.now(UTC),
        metadata_updates={
            "workflow_output_markdown": report_markdown,
            "workflow_output_excerpt": excerpt,
            "iterations": iteration_reports,
            "artifact_refs": artifact_refs,
            "checkpoint_resume_iteration": None,
            "auto_review_thread_id": review_thread_id,
            "execution_command": command,
            "effective_execution_command": effective_command,
            "runtime_environment": runtime_environment,
            "execution_workspace": command_workspace_path or workspace_path,
            "completed_at": _iso_now(),
        },
    )
    _set_stage_state(
        run.id,
        "review_cycle",
        status="completed",
        message=f"自动评审循环已完成，共执行 {len(iteration_reports)} 轮。",
        progress_pct=100,
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": report_markdown,
        "iterations": iteration_reports,
        "artifact_refs": artifact_refs,
    }
    if run.task_id:
        global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        global_tracker.set_result(run.task_id, result)
    return result


def _execute_run_experiment(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    workspace_path = _resolve_workspace_path(run) or _resolve_execution_workspace_path(context)
    if not workspace_path:
        raise RuntimeError("当前运行缺少有效工作区路径，无法执行实验")

    command_workspace_path = _resolve_execution_workspace_path(context) or workspace_path
    inspect_payload = _stage_output_payload(context, "inspect_workspace")
    inspection = inspect_payload.get("inspection") if isinstance(inspect_payload.get("inspection"), dict) else None
    if resume_stage_id not in {"execute_experiment", "summarize_results"} or not isinstance(inspection, dict):
        if command_workspace_path != _resolve_workspace_path(run):
            inspection = _inspect_workspace_payload(context, workspace_path_override=command_workspace_path)
        else:
            inspection = _inspect_workspace_payload(context)
        _set_stage_state(
            run.id,
            "inspect_workspace",
            status="completed",
            message="工作区检查完成，准备执行命令。",
            progress_pct=24,
        )
        _record_stage_output(
            run.id,
            "inspect_workspace",
            {
                "summary": "工作区检查完成",
                "content": inspection.get("tree") or inspection.get("message") or workspace_path,
                "provider": "workspace_inspector",
                "model_role": _stage_model_role(context, "inspect_workspace"),
                "model_source": "workspace_inspector",
                "role_template_id": _stage_role_id(context, "inspect_workspace"),
                "workspace_path": workspace_path,
                "inspection": inspection,
            },
        )
        _maybe_pause_after_stage(
            context,
            "inspect_workspace",
            "execute_experiment",
            stage_summary=str(inspection.get("message") or inspection.get("tree") or workspace_path)[:600],
        )
    else:
        _set_stage_state(
            run.id,
            "inspect_workspace",
            status="completed",
            message="工作区检查完成，准备执行命令。",
            progress_pct=24,
        )

    execution_plan = _resolve_execution_plan(context)
    if run.workspace_server_id:
        return _execute_remote_run_experiment(
            context,
            inspection=inspection,
            execution_plan=execution_plan,
            progress_callback=progress_callback,
        )
    if len(execution_plan) > 1:
        raise RuntimeError("当前批量实验编排仅支持 SSH 工作区，请切换到远程工作区后重试。")
    command = execution_plan[0].command
    effective_command, runtime_environment = _wrap_command_with_runtime_environment(context, command)
    runtime_environment = {
        **runtime_environment,
        "command_workspace_path": command_workspace_path,
    }

    _patch_run(
        run.id,
        active_phase="execute_experiment",
        summary=f"正在执行实验命令：{command}",
    )
    _set_stage_state(
        run.id,
        "execute_experiment",
        status="running",
        message=f"正在执行实验命令：{command}",
        progress_pct=42,
    )
    _emit_progress(progress_callback, f"正在执行实验命令：{command}", 42)
    if run.task_id:
        global_tracker.append_log(run.task_id, f"执行命令: {command}")
        if effective_command != command:
            global_tracker.append_log(run.task_id, f"实际执行命令: {effective_command}")

    if command_workspace_path != _resolve_workspace_path(run):
        execution = _run_workspace_command_for_context(
            context,
            effective_command,
            timeout_sec=_resolve_execution_timeout(context),
            workspace_path_override=command_workspace_path,
        )
    else:
        execution = _run_workspace_command_for_context(
            context,
            effective_command,
            timeout_sec=_resolve_execution_timeout(context),
        )
    execution["original_command"] = command
    execution["effective_command"] = effective_command
    execution["runtime_environment"] = runtime_environment
    execution["command_workspace_path"] = command_workspace_path
    log_text = _format_command_log(execution)
    log_artifact = _write_run_log(context, log_text)
    artifact_refs = [artifact for artifact in [log_artifact] if artifact]
    artifact_refs.extend(_collect_run_artifacts(context))
    _record_stage_output(
        run.id,
        "execute_experiment",
        {
            "summary": "实验命令执行完成" if execution.get("success") else "实验命令执行失败",
            "content": _command_result_preview(execution),
            "provider": "workspace_executor_remote" if run.workspace_server_id else "workspace_executor_local",
            "model_role": _stage_model_role(context, "execute_experiment"),
            "model_source": "workspace_executor",
            "role_template_id": _stage_role_id(context, "execute_experiment"),
            "workspace_path": command_workspace_path,
            "command": command,
            "effective_command": effective_command,
            "runtime_environment": runtime_environment,
            "exit_code": execution.get("exit_code"),
            "artifact_refs": artifact_refs,
        },
    )
    if run.task_id:
        global_tracker.set_metadata(
            run.task_id,
            {
                "artifact_refs": artifact_refs,
                "log_path": log_artifact.get("path") if log_artifact else run.log_path,
                "runtime_environment": runtime_environment,
                "effective_execution_command": effective_command,
                "execution_workspace": command_workspace_path,
            },
        )
        global_tracker.append_log(run.task_id, _command_result_preview(execution))

    if not execution.get("success"):
        raise RuntimeError(
            str(execution.get("stderr") or execution.get("stdout") or f"命令退出码 {execution.get('exit_code')}")
        )

    _set_stage_state(
        run.id,
        "execute_experiment",
        status="completed",
        message="实验命令执行完成，正在整理结果。",
        progress_pct=72,
    )
    _patch_run(
        run.id,
        active_phase="summarize_results",
        summary="正在整理实验结果与下一步建议。",
    )
    _set_stage_state(
        run.id,
        "summarize_results",
        status="running",
        message="正在整理实验结果与下一步建议。",
        progress_pct=84,
    )
    _emit_progress(progress_callback, "正在整理实验结果与下一步建议。", 84)

    summary_prompt = _build_experiment_summary_prompt(context, inspection, execution)
    summary_execution = _invoke_role_markdown(
        context,
        "summarize_results",
        summary_prompt,
        stage="project_run_experiment_summary",
        max_tokens=1800,
        request_timeout=180,
    )
    summary_markdown = _resolve_experiment_summary_markdown(context, execution, summary_execution["result"])
    summary_markdown = format_experiment_report(
        context.project.name,
        run.prompt,
        summary_markdown,
        {
            "execution_command": command,
            "effective_execution_command": effective_command,
            "execution_workspace": command_workspace_path,
            "runtime_environment": runtime_environment,
            "execution_result": {
                "command": command,
                "effective_command": effective_command,
                "exit_code": execution.get("exit_code"),
                "stdout": execution.get("stdout"),
                "stderr": execution.get("stderr"),
                "success": execution.get("success"),
                "workspace_path": command_workspace_path,
                "runtime_environment": runtime_environment,
            },
        },
    )
    summary_artifact = _write_run_artifact(context, "reports/experiment-summary.md", summary_markdown, kind="report")
    if summary_artifact:
        artifact_refs.append(summary_artifact)

    excerpt = _markdown_excerpt(summary_markdown)
    metadata_updates = {
        "workflow_output_markdown": summary_markdown,
        "workflow_output_excerpt": excerpt,
        "execution_command": command,
        "effective_execution_command": effective_command,
        "runtime_environment": runtime_environment,
        "execution_workspace": command_workspace_path,
        "execution_result": {
            "command": command,
            "effective_command": effective_command,
            "exit_code": execution.get("exit_code"),
            "stdout": execution.get("stdout"),
            "stderr": execution.get("stderr"),
            "success": execution.get("success"),
            "workspace_path": command_workspace_path,
            "runtime_environment": runtime_environment,
        },
        "artifact_refs": artifact_refs,
        "completed_at": _iso_now(),
    }
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "实验执行完成。",
        finished_at=datetime.now(UTC),
        metadata_updates=metadata_updates,
    )
    _set_stage_state(
        run.id,
        "summarize_results",
        status="completed",
        message="实验结果已整理完成。",
        progress_pct=100,
    )
    _record_stage_output(
        run.id,
        "summarize_results",
        {
            "summary": excerpt or "实验结果已整理完成",
            "content": summary_markdown,
            "provider": summary_execution.get("provider"),
            "model": summary_execution.get("model"),
            "variant": summary_execution.get("variant"),
            "model_role": summary_execution.get("model_role"),
            "model_source": summary_execution.get("model_source"),
            "role_template_id": summary_execution.get("role_template_id"),
            "artifact_refs": artifact_refs,
        },
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": summary_markdown,
        "command": command,
        "effective_command": effective_command,
        "artifact_refs": artifact_refs,
    }
    if run.task_id:
        global_tracker.set_result(run.task_id, result)
    return result


def _execute_experiment_audit(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    workspace_path = _resolve_workspace_path(run) or _resolve_execution_workspace_path(context)
    if not workspace_path:
        raise RuntimeError("当前运行缺少工作区路径，无法执行实验审计")

    if resume_stage_id == "issue_audit_report":
        review_json_text = _stage_output_content(context, "review_integrity")
        if not review_json_text:
            raise RuntimeError("恢复实验审计失败：缺少审计评审阶段产物。")
        bundle = _collect_experiment_audit_bundle(context, workspace_path=workspace_path)
        audit_payload = _resolve_experiment_audit_payload(
            bundle,
            LLMResult(content=review_json_text, parsed_json=_parse_json_payload_text(review_json_text)),
        )
    else:
        _patch_run(run.id, active_phase="collect_artifacts", summary="正在收集实验审计证据包。")
        _set_stage_state(
            run.id,
            "collect_artifacts",
            status="running",
            message="正在收集实验审计证据包。",
            progress_pct=18,
        )
        _emit_progress(progress_callback, "正在收集实验审计证据包。", 18)
        bundle = _collect_experiment_audit_bundle(context, workspace_path=workspace_path)
        bundle_summary = str(bundle.get("summary") or "已完成实验审计证据收集。").strip()
        _record_stage_output(
            run.id,
            "collect_artifacts",
            {
                "summary": bundle_summary,
                "content": str(bundle.get("inventory_markdown") or "").strip(),
                "provider": "workspace_audit_inventory",
                "model_role": _stage_model_role(context, "collect_artifacts"),
                "model_source": "workspace_executor",
                "role_template_id": _stage_role_id(context, "collect_artifacts"),
                "workspace_path": workspace_path,
                "audit_inventory": dict(bundle.get("inventory") or {}),
            },
        )
        _set_stage_state(
            run.id,
            "collect_artifacts",
            status="completed",
            message="实验审计证据包已整理完成。",
            progress_pct=34,
        )
        _maybe_pause_after_stage(
            context,
            "collect_artifacts",
            "review_integrity",
            stage_summary=bundle_summary,
        )

        _patch_run(run.id, active_phase="review_integrity", summary="正在执行跨模型实验完整性审计。")
        _set_stage_state(
            run.id,
            "review_integrity",
            status="running",
            message="正在执行跨模型实验完整性审计。",
            progress_pct=62,
        )
        _emit_progress(progress_callback, "正在执行跨模型实验完整性审计。", 62)
        review_execution = _invoke_role_json(
            context,
            "review_integrity",
            _build_experiment_audit_prompt(context, bundle),
            stage="project_experiment_audit_review",
            max_tokens=2600,
            request_timeout=240,
            max_retries=1,
        )
        audit_payload = _resolve_experiment_audit_payload(bundle, review_execution["result"])
        review_json_text = json.dumps(audit_payload, ensure_ascii=False, indent=2)
        _record_stage_output(
            run.id,
            "review_integrity",
            {
                "summary": _experiment_audit_summary_line(audit_payload),
                "content": review_json_text,
                "provider": review_execution.get("provider"),
                "model": review_execution.get("model"),
                "variant": review_execution.get("variant"),
                "model_role": review_execution.get("model_role"),
                "model_source": review_execution.get("model_source"),
                "role_template_id": review_execution.get("role_template_id"),
                "audit_payload": audit_payload,
            },
        )
        _set_stage_state(
            run.id,
            "review_integrity",
            status="completed",
            message="实验完整性审计已完成，正在输出审计报告。",
            progress_pct=84,
        )

    _patch_run(run.id, active_phase="issue_audit_report", summary="正在输出实验审计报告。")
    _set_stage_state(
        run.id,
        "issue_audit_report",
        status="running",
        message="正在输出实验审计报告。",
        progress_pct=92,
    )
    _emit_progress(progress_callback, "正在输出实验审计报告。", 92)

    final_markdown = _render_experiment_audit_report(
        context,
        audit_payload=audit_payload,
        workspace_path=workspace_path,
    )
    artifact_refs: list[dict[str, Any]] = []
    report_artifact = _write_run_artifact(context, "EXPERIMENT_AUDIT.md", final_markdown, kind="report")
    json_artifact = _write_run_json_artifact(context, "EXPERIMENT_AUDIT.json", audit_payload, kind="artifact")
    report_preview_artifact = _write_run_artifact(
        context,
        "reports/experiment-audit.md",
        final_markdown,
        kind="report",
    )
    for artifact in [report_artifact, json_artifact, report_preview_artifact]:
        if artifact:
            artifact_refs.append(artifact)
    artifact_refs = _dedupe_artifact_refs(artifact_refs)
    excerpt = _markdown_excerpt(final_markdown)
    metadata_updates = {
        "workflow_output_markdown": final_markdown,
        "workflow_output_excerpt": excerpt,
        "audit_payload": audit_payload,
        "audit_inventory": dict(bundle.get("inventory") or {}),
        "integrity_status": audit_payload.get("integrity_status"),
        "overall_verdict": audit_payload.get("overall_verdict"),
        "evaluation_type": audit_payload.get("evaluation_type"),
        "execution_workspace": workspace_path,
        "artifact_refs": artifact_refs,
        "completed_at": _iso_now(),
    }
    _record_stage_output(
        run.id,
        "issue_audit_report",
        {
            "summary": excerpt or _experiment_audit_summary_line(audit_payload),
            "content": final_markdown,
            "provider": "experiment_audit_reporter",
            "model_role": _stage_model_role(context, "issue_audit_report"),
            "model_source": "workflow_reporter",
            "role_template_id": _stage_role_id(context, "issue_audit_report"),
            "artifact_refs": artifact_refs,
            "audit_payload": audit_payload,
        },
    )
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or _experiment_audit_summary_line(audit_payload),
        finished_at=datetime.now(UTC),
        metadata_updates=metadata_updates,
    )
    _set_stage_state(
        run.id,
        "issue_audit_report",
        status="completed",
        message="实验审计报告已生成。",
        progress_pct=100,
    )

    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": final_markdown,
        "integrity_status": audit_payload.get("integrity_status"),
        "overall_verdict": audit_payload.get("overall_verdict"),
        "evaluation_type": audit_payload.get("evaluation_type"),
        "artifact_refs": artifact_refs,
    }
    if run.task_id:
        global_tracker.set_metadata(
            run.task_id,
            {
                "artifact_refs": artifact_refs,
                "integrity_status": audit_payload.get("integrity_status"),
                "overall_verdict": audit_payload.get("overall_verdict"),
            },
        )
        global_tracker.set_result(run.task_id, result)
    return result


def _launch_remote_experiment_item(
    context: WorkflowContext,
    *,
    item: ExecutionPlanItem,
    total_experiments: int,
    workspace_path: str,
    server_entry: dict[str, Any],
    gpu_probe: dict[str, Any],
    active_leases: list[dict[str, Any]],
) -> dict[str, Any]:
    run = context.run
    item_context = _clone_context_with_metadata(context, item.metadata_overrides)
    runtime = _build_remote_execution_runtime(context, item, total_items=total_experiments)
    item_session_name = str(runtime["remote_session_name"])
    item_run_directory = str(runtime["run_directory"])
    item_log_path = str(runtime["log_path"])
    item_planned_execution_workspace = str(runtime.get("planned_execution_workspace") or "").strip()
    effective_command, runtime_environment = _wrap_command_with_runtime_environment(item_context, item.command)
    selected_gpu: dict[str, Any] | None = None
    gpu_env_vars: dict[str, str] = {}
    gpu_lease: dict[str, Any] | None = None
    prepare_result: dict[str, Any] | None = None
    launch_result: dict[str, Any] | None = None
    execution_workspace = item_planned_execution_workspace or workspace_path
    command_workspace_path = execution_workspace
    isolation_mode = "copied_workspace"

    try:
        selected_gpu = _select_remote_gpu(
            item_context,
            gpu_probe,
            active_leases=active_leases,
            exclude_current_run=total_experiments <= 1,
        )
        if selected_gpu is not None:
            gpu_env_vars = {
                "CUDA_VISIBLE_DEVICES": str(selected_gpu["index"]),
                "RESEARCHOS_ASSIGNED_GPU": str(selected_gpu["index"]),
            }
            gpu_lease = acquire_gpu_lease(
                workspace_server_id=run.workspace_server_id or "",
                gpu_index=int(selected_gpu["index"]),
                gpu_name=str(selected_gpu.get("name") or "").strip() or None,
                project_id=context.project.id,
                run_id=run.id,
                task_id=run.task_id,
                remote_session_name=item_session_name,
                holder_title=f"{run.title or context.project.name} · {item.name}",
                metadata={
                    "execution_item_id": item.item_id,
                    "execution_item_name": item.name,
                    "selected_gpu": selected_gpu,
                    "execution_command": item.command,
                    "effective_execution_command": effective_command,
                },
            )
        prepare_result = remote_prepare_run_environment(
            server_entry,
            path=workspace_path,
            run_directory=item_run_directory,
            session_name=item_session_name,
        )
        execution_workspace = str(
            prepare_result.get("execution_workspace") or item_planned_execution_workspace or workspace_path
        ).strip()
        command_workspace_path = _resolve_execution_workspace_path(
            item_context,
            workspace_root=execution_workspace or workspace_path,
        ) or execution_workspace or workspace_path
        runtime_environment = {
            **runtime_environment,
            "command_workspace_path": command_workspace_path,
        }
        isolation_mode = str(prepare_result.get("isolation_mode") or "copied_workspace")
        launch_result = remote_launch_screen_job(
            server_entry,
            path=command_workspace_path or execution_workspace or workspace_path,
            session_name=item_session_name,
            command=effective_command,
            log_path=item_log_path,
            env_vars=gpu_env_vars,
            timeout_sec=30,
        )
        if not launch_result.get("success"):
            raise RuntimeError(
                str(
                    launch_result.get("stderr")
                    or launch_result.get("stdout")
                    or "远程后台实验启动失败"
                )
            )
        if selected_gpu is not None:
            gpu_lease = touch_gpu_lease(
                workspace_server_id=run.workspace_server_id or "",
                gpu_index=int(selected_gpu["index"]),
                metadata={
                    "execution_item_id": item.item_id,
                    "execution_item_name": item.name,
                    "selected_gpu": selected_gpu,
                    "execution_command": item.command,
                    "effective_execution_command": effective_command,
                    "execution_workspace": command_workspace_path or execution_workspace or workspace_path,
                    "remote_session_name": item_session_name,
                },
            )
            if gpu_lease is not None:
                active_leases = _merge_active_gpu_leases(active_leases, gpu_lease)
        screen_snapshot = remote_list_screen_sessions(server_entry, session_name=item_session_name)
        screen_capture = remote_capture_screen_session(server_entry, session_name=item_session_name, lines=60)
        return {
            "ok": True,
            "active_leases": active_leases,
            "record": {
                "id": item.item_id,
                "name": item.name,
                "command": item.command,
                "effective_command": effective_command,
                "status": "running",
                "remote_session_name": item_session_name,
                "remote_execution_workspace": execution_workspace or workspace_path,
                "command_workspace_path": command_workspace_path or execution_workspace or workspace_path,
                "remote_isolation_mode": isolation_mode,
                "run_directory": item_run_directory,
                "log_path": item_log_path,
                "selected_gpu": selected_gpu,
                "gpu_lease": gpu_lease,
                "gpu_settings": dict(item.metadata_overrides),
                "env_vars": gpu_env_vars,
                "runtime_environment": runtime_environment,
                "prepare": prepare_result,
                "launch": launch_result,
                "screen_sessions": list(screen_snapshot.get("sessions") or []),
                "screen_capture": {
                    "stdout": screen_capture.get("stdout"),
                    "stderr": screen_capture.get("stderr"),
                    "success": screen_capture.get("success"),
                },
                "timeout_sec": _resolve_execution_timeout(item_context),
            },
        }
    except Exception as exc:
        released_lease = None
        if selected_gpu is not None:
            released_lease = release_gpu_lease(
                workspace_server_id=run.workspace_server_id or "",
                gpu_index=int(selected_gpu["index"]),
                run_id=run.id,
                remote_session_name=item_session_name,
                reason="launch_failed",
            )
        return {
            "ok": False,
            "active_leases": active_leases,
            "record": {
                "id": item.item_id,
                "name": item.name,
                "command": item.command,
                "effective_command": effective_command,
                "status": "failed_to_launch",
                "error": str(exc),
                "remote_session_name": item_session_name,
                "remote_execution_workspace": execution_workspace or item_planned_execution_workspace or workspace_path,
                "command_workspace_path": command_workspace_path or execution_workspace or item_planned_execution_workspace or workspace_path,
                "remote_isolation_mode": isolation_mode,
                "run_directory": item_run_directory,
                "log_path": item_log_path,
                "selected_gpu": selected_gpu,
                "released_gpu_lease": released_lease,
                "gpu_settings": dict(item.metadata_overrides),
                "env_vars": gpu_env_vars,
                "runtime_environment": runtime_environment,
                "prepare": prepare_result,
                "launch": launch_result,
                "timeout_sec": _resolve_execution_timeout(item_context),
            },
        }


def _execute_remote_run_experiment_batch(
    context: WorkflowContext,
    *,
    inspection: dict[str, Any],
    execution_plan: list[ExecutionPlanItem],
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    run = context.run
    workspace_path = _resolve_workspace_path(run) or _resolve_execution_workspace_path(context)
    if not workspace_path or not run.workspace_server_id:
        raise RuntimeError("当前运行缺少远程工作区路径，无法执行远程实验")
    if not run.run_directory:
        raise RuntimeError("当前运行缺少 run_directory，无法准备远程隔离工作区")
    if not run.log_path:
        raise RuntimeError("当前运行缺少 log_path，无法写回远程实验日志")
    if not execution_plan:
        raise RuntimeError("当前运行缺少有效实验执行计划")

    server_entry = get_workspace_server_entry(run.workspace_server_id)
    total_experiments = len(execution_plan)
    batch_mode = total_experiments > 1
    remote_session_name = _resolve_remote_session_name(context)
    planned_execution_workspace = _resolve_remote_execution_workspace(context)
    reconcile_state = _reconcile_remote_gpu_leases(
        workspace_server_id=run.workspace_server_id,
        server_entry=server_entry,
    )
    gpu_probe = remote_probe_gpus(server_entry, path=workspace_path)
    normalized_plan = _serialize_execution_plan(execution_plan)
    launch_prep_message = (
        f"正在检查远程 GPU 资源、准备 {total_experiments} 个隔离工作区并启动后台会话。"
        if batch_mode
        else "正在检查远程 GPU 资源、准备隔离工作区并启动后台会话。"
    )

    _patch_run(
        run.id,
        active_phase="prepare_remote_workspace",
        summary=(
            f"正在检查远程 GPU 资源并准备 {total_experiments} 个后台实验会话。"
            if batch_mode
            else "正在检查远程 GPU 资源并准备隔离工作区。"
        ),
        metadata_updates={
            "remote_session_name": remote_session_name,
            "remote_execution_workspace": planned_execution_workspace,
            "remote_launch_status": "preparing",
            "gpu_lease_reconcile": reconcile_state,
            "gpu_probe": gpu_probe,
            "execution_plan": normalized_plan,
        },
    )
    _set_stage_state(
        run.id,
        "execute_experiment",
        status="running",
        message=launch_prep_message,
        progress_pct=42,
    )
    _emit_progress(progress_callback, launch_prep_message, 42)
    if run.task_id:
        global_tracker.append_log(
            run.task_id,
            f"远程实验计划: {total_experiments} 项 | 基础会话前缀: {remote_session_name}",
        )
        released_leases = list(reconcile_state.get("released_leases") or [])
        if released_leases:
            released_gpu_labels = ", ".join(
                f"gpu{item.get('gpu_index')}" for item in released_leases
            )
            global_tracker.append_log(
                run.task_id,
                f"已清理陈旧 GPU 锁: {released_gpu_labels}",
            )

    active_leases = list(reconcile_state.get("active_leases") or [])
    experiment_records: list[dict[str, Any]] = []
    launched_records: list[dict[str, Any]] = []
    failed_records: list[dict[str, Any]] = []
    for item in execution_plan:
        launch_result = _launch_remote_experiment_item(
            context,
            item=item,
            total_experiments=total_experiments,
            workspace_path=workspace_path,
            server_entry=server_entry,
            gpu_probe=gpu_probe,
            active_leases=active_leases,
        )
        active_leases = list(launch_result.get("active_leases") or [])
        record = dict(launch_result.get("record") or {})
        experiment_records.append(record)
        if launch_result.get("ok"):
            launched_records.append(record)
            if run.task_id:
                global_tracker.append_log(
                    run.task_id,
                    (
                        f"实验 `{record.get('name')}` 已启动: session={record.get('remote_session_name')}, "
                        f"workspace={record.get('remote_execution_workspace')}, "
                        f"gpu={record.get('selected_gpu', {}).get('index') if isinstance(record.get('selected_gpu'), dict) else 'none'}"
                    ),
                )
        else:
            failed_records.append(record)
            if run.task_id:
                global_tracker.append_log(
                    run.task_id,
                    f"实验 `{record.get('name')}` 启动失败: {str(record.get('error') or '')[:240]}",
                )

    if not launched_records:
        failure_text = "; ".join(f"{item['name']}: {item.get('error')}" for item in failed_records[:6]) or "未知错误"
        _patch_run(
            run.id,
            metadata_updates={
                "remote_launch_status": "failed",
                "execution_plan": normalized_plan,
                "remote_experiments": experiment_records,
                "remote_session_names": [
                    str(item.get("remote_session_name") or "").strip()
                    for item in experiment_records
                    if str(item.get("remote_session_name") or "").strip()
                ],
                "remote_launch_failures": failed_records,
                "gpu_lease_reconcile": reconcile_state,
                "gpu_probe": gpu_probe,
            },
        )
        raise RuntimeError(f"批量实验启动失败：{failure_text}")

    return _finalize_remote_run_experiment_batch(
        context,
        inspection=inspection,
        execution_plan=execution_plan,
        reconcile_state=reconcile_state,
        gpu_probe=gpu_probe,
        experiment_records=experiment_records,
        launched_records=launched_records,
        failed_records=failed_records,
        progress_callback=progress_callback,
    )


def _finalize_remote_run_experiment_batch(
    context: WorkflowContext,
    *,
    inspection: dict[str, Any],
    execution_plan: list[ExecutionPlanItem],
    reconcile_state: dict[str, Any],
    gpu_probe: dict[str, Any],
    experiment_records: list[dict[str, Any]],
    launched_records: list[dict[str, Any]],
    failed_records: list[dict[str, Any]],
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    run = context.run
    workspace_path = _resolve_workspace_path(run) or _resolve_execution_workspace_path(context) or ""
    first_success = launched_records[0]
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    aggregated_screen_sessions: list[dict[str, Any]] = []
    seen_screen_sessions: set[str] = set()

    for record in experiment_records:
        stdout_lines.extend(
            [
                f"[{record.get('name')}] status={record.get('status')}",
                f"session: {record.get('remote_session_name') or 'N/A'}",
                f"workspace: {record.get('remote_execution_workspace') or 'N/A'}",
                f"command workspace: {record.get('command_workspace_path') or 'N/A'}",
                f"isolation mode: {record.get('remote_isolation_mode') or 'N/A'}",
                (
                    f"gpu assignment: {record['selected_gpu']['index']}"
                    if isinstance(record.get("selected_gpu"), dict)
                    else "gpu assignment: none"
                ),
                f"log path: {record.get('log_path') or 'N/A'}",
                f"command: {record.get('command') or ''}",
                f"effective command: {record.get('effective_command') or record.get('command') or ''}",
            ]
        )
        capture_stdout = str(((record.get("screen_capture") or {}).get("stdout")) or "").strip()
        if capture_stdout:
            stdout_lines.extend(["", capture_stdout])
        stdout_lines.append("")
        error_text = str(record.get("error") or ((record.get("screen_capture") or {}).get("stderr")) or "").strip()
        if error_text:
            stderr_lines.append(f"[{record.get('name')}] {error_text}")
        for session_item in record.get("screen_sessions") or []:
            session_key = str(session_item.get("name") or "").strip()
            if not session_key or session_key in seen_screen_sessions:
                continue
            seen_screen_sessions.add(session_key)
            aggregated_screen_sessions.append(dict(session_item))

    launch_execution = {
        "command": f"batch[{len(execution_plan)}]" if len(execution_plan) > 1 else first_success.get("command"),
        "effective_command": (
            f"batch[{len(execution_plan)}]"
            if len(execution_plan) > 1
            else first_success.get("effective_command") or first_success.get("command")
        ),
        "exit_code": 0 if not failed_records else 1,
        "stdout": "\n".join(stdout_lines).strip(),
        "stderr": "\n".join(stderr_lines).strip(),
        "success": True,
        "launch_command": first_success.get("launch", {}).get("launch_command") if len(launched_records) == 1 else None,
        "remote_session_name": first_success.get("remote_session_name"),
        "remote_session_names": [
            str(item.get("remote_session_name") or "").strip()
            for item in launched_records
            if str(item.get("remote_session_name") or "").strip()
        ],
        "remote_execution_workspace": first_success.get("remote_execution_workspace"),
        "remote_execution_workspaces": [
            str(item.get("remote_execution_workspace") or "").strip()
            for item in launched_records
            if str(item.get("remote_execution_workspace") or "").strip()
        ],
        "command_workspace_path": first_success.get("command_workspace_path"),
        "command_workspace_paths": [
            str(item.get("command_workspace_path") or "").strip()
            for item in launched_records
            if str(item.get("command_workspace_path") or "").strip()
        ],
        "remote_isolation_mode": first_success.get("remote_isolation_mode"),
        "gpu_lease_reconcile": reconcile_state,
        "gpu_probe": gpu_probe,
        "selected_gpu": first_success.get("selected_gpu"),
        "gpu_lease": first_success.get("gpu_lease"),
        "env_vars": first_success.get("env_vars") or {},
        "runtime_environment": first_success.get("runtime_environment"),
        "screen_sessions": aggregated_screen_sessions,
        "screen_capture": first_success.get("screen_capture") or {},
        "batch_experiments": experiment_records,
        "launch_failures": failed_records,
        "mode": "remote_screen_batch_launch" if len(execution_plan) > 1 else "remote_screen_launch",
    }

    artifact_refs: list[dict[str, Any]] = []
    log_artifact = _write_run_log(context, _format_command_log(launch_execution))
    if log_artifact:
        artifact_refs.append(log_artifact)
    for record in experiment_records:
        log_path = str(record.get("log_path") or "").strip()
        if not log_path:
            continue
        relative_path = log_path
        if run.run_directory:
            root_prefix = f"{str(run.run_directory).rstrip('/')}/"
            if log_path.startswith(root_prefix):
                relative_path = log_path[len(root_prefix) :]
        artifact_refs.append(
            {
                "kind": "log",
                "path": log_path,
                "relative_path": relative_path,
            }
        )
    launch_report = _write_run_artifact(
        context,
        "reports/remote-launch.json",
        json.dumps(
            {
                "experiments": experiment_records,
                "gpu_lease_reconcile": reconcile_state,
                "gpu_probe": gpu_probe,
                "selected_gpu": first_success.get("selected_gpu"),
                "gpu_lease": first_success.get("gpu_lease"),
                "screen_sessions": aggregated_screen_sessions,
                "captured_at": _iso_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        kind="artifact",
    )
    if launch_report:
        artifact_refs.append(launch_report)
    artifact_refs.extend(_collect_run_artifacts(context))
    artifact_refs = _dedupe_artifact_refs(artifact_refs)

    launch_summary = (
        f"{len(launched_records)}/{len(execution_plan)} 个远程实验已在后台启动"
        + (f"，{len(failed_records)} 个启动失败" if failed_records else "")
    )
    _record_stage_output(
        run.id,
        "execute_experiment",
        {
            "summary": launch_summary,
            "content": _command_result_preview(launch_execution),
            "provider": "workspace_executor_remote_screen",
            "model_role": _stage_model_role(context, "execute_experiment"),
            "model_source": "workspace_executor",
            "role_template_id": _stage_role_id(context, "execute_experiment"),
            "workspace_path": workspace_path,
            "command": launch_execution.get("command"),
            "effective_command": launch_execution.get("effective_command"),
            "runtime_environment": first_success.get("runtime_environment"),
            "exit_code": launch_execution.get("exit_code"),
            "artifact_refs": artifact_refs,
            "remote_session_name": first_success.get("remote_session_name"),
            "remote_session_names": launch_execution.get("remote_session_names"),
            "remote_execution_workspace": first_success.get("remote_execution_workspace"),
            "command_workspace_path": first_success.get("command_workspace_path"),
            "remote_isolation_mode": first_success.get("remote_isolation_mode"),
            "selected_gpu": first_success.get("selected_gpu"),
            "gpu_lease": first_success.get("gpu_lease"),
            "experiments": experiment_records,
        },
    )
    if run.task_id:
        global_tracker.set_metadata(
            run.task_id,
            {
                "artifact_refs": artifact_refs,
                "log_path": run.log_path,
                "remote_session_name": first_success.get("remote_session_name"),
                "remote_session_names": launch_execution.get("remote_session_names"),
                "remote_execution_workspace": first_success.get("remote_execution_workspace"),
                "remote_execution_workspaces": launch_execution.get("remote_execution_workspaces"),
                "command_workspace_path": first_success.get("command_workspace_path"),
                "command_workspace_paths": launch_execution.get("command_workspace_paths"),
                "remote_isolation_mode": first_success.get("remote_isolation_mode"),
                "selected_gpu": first_success.get("selected_gpu"),
                "gpu_lease": first_success.get("gpu_lease"),
                "gpu_probe": gpu_probe,
                "runtime_environment": first_success.get("runtime_environment"),
                "remote_experiments": experiment_records,
            },
        )
        global_tracker.append_log(run.task_id, launch_summary)

    _set_stage_state(
        run.id,
        "execute_experiment",
        status="completed",
        message="远程实验已在后台启动，正在整理启动摘要。",
        progress_pct=72,
    )
    _patch_run(
        run.id,
        active_phase="summarize_results",
        summary="正在整理远程实验启动摘要与监控建议。",
    )
    _set_stage_state(
        run.id,
        "summarize_results",
        status="running",
        message="正在整理远程实验启动摘要与监控建议。",
        progress_pct=84,
    )
    _emit_progress(progress_callback, "正在整理远程实验启动摘要与监控建议。", 84)

    summary_prompt = _build_experiment_summary_prompt(context, inspection, launch_execution)
    summary_execution = _invoke_role_markdown(
        context,
        "summarize_results",
        summary_prompt,
        stage="project_run_experiment_summary",
        max_tokens=1800,
        request_timeout=180,
    )
    summary_markdown = _resolve_experiment_summary_markdown(
        context,
        launch_execution,
        summary_execution["result"],
    )
    summary_markdown = format_experiment_report(
        context.project.name,
        run.prompt,
        summary_markdown,
        {
            "execution_command": first_success.get("command"),
            "effective_execution_command": first_success.get("effective_command"),
            "execution_workspace": first_success.get("command_workspace_path"),
            "runtime_environment": first_success.get("runtime_environment"),
            "remote_launch_status": "partial_running" if failed_records else "running",
            "remote_session_name": first_success.get("remote_session_name"),
            "remote_execution_workspace": first_success.get("remote_execution_workspace"),
            "remote_isolation_mode": first_success.get("remote_isolation_mode"),
            "selected_gpu": first_success.get("selected_gpu"),
            "execution_result": {
                "mode": launch_execution.get("mode"),
                "command": launch_execution.get("command"),
                "effective_command": launch_execution.get("effective_command"),
                "exit_code": launch_execution.get("exit_code"),
                "stdout": launch_execution.get("stdout"),
                "stderr": launch_execution.get("stderr"),
                "success": True,
                "remote_session_name": first_success.get("remote_session_name"),
                "remote_execution_workspace": first_success.get("remote_execution_workspace"),
                "remote_isolation_mode": first_success.get("remote_isolation_mode"),
                "workspace_path": first_success.get("command_workspace_path"),
                "selected_gpu": first_success.get("selected_gpu"),
            },
        },
    )
    summary_artifact = _write_run_artifact(context, "reports/experiment-summary.md", summary_markdown, kind="report")
    if summary_artifact:
        artifact_refs.append(summary_artifact)
        artifact_refs = _dedupe_artifact_refs(artifact_refs)

    excerpt = _markdown_excerpt(summary_markdown) or "远程实验已在后台启动。"
    metadata_updates = {
        "workflow_output_markdown": summary_markdown,
        "workflow_output_excerpt": excerpt,
        "execution_command": first_success.get("command"),
        "effective_execution_command": first_success.get("effective_command"),
        "execution_commands": [str(item.command) for item in execution_plan],
        "effective_execution_commands": [
            str(item.get("effective_command") or item.get("command") or "").strip()
            for item in experiment_records
            if str(item.get("effective_command") or item.get("command") or "").strip()
        ],
        "runtime_environment": first_success.get("runtime_environment"),
        "execution_workspace": first_success.get("command_workspace_path"),
        "execution_result": {
            "mode": launch_execution.get("mode"),
            "command": launch_execution.get("command"),
            "effective_command": launch_execution.get("effective_command"),
            "exit_code": launch_execution.get("exit_code"),
            "stdout": launch_execution.get("stdout"),
            "stderr": launch_execution.get("stderr"),
            "success": True,
            "launch_command": launch_execution.get("launch_command"),
            "remote_session_name": first_success.get("remote_session_name"),
            "remote_session_names": launch_execution.get("remote_session_names"),
            "remote_execution_workspace": first_success.get("remote_execution_workspace"),
            "remote_execution_workspaces": launch_execution.get("remote_execution_workspaces"),
            "command_workspace_path": first_success.get("command_workspace_path"),
            "command_workspace_paths": launch_execution.get("command_workspace_paths"),
            "remote_isolation_mode": first_success.get("remote_isolation_mode"),
            "gpu_lease_reconcile": reconcile_state,
            "gpu_probe": gpu_probe,
            "selected_gpu": first_success.get("selected_gpu"),
            "gpu_lease": first_success.get("gpu_lease"),
            "screen_sessions": aggregated_screen_sessions,
            "runtime_environment": first_success.get("runtime_environment"),
            "batch_experiments": experiment_records,
            "launch_failures": failed_records,
        },
        "remote_session_name": first_success.get("remote_session_name"),
        "remote_session_names": launch_execution.get("remote_session_names"),
        "remote_execution_workspace": first_success.get("remote_execution_workspace"),
        "remote_execution_workspaces": launch_execution.get("remote_execution_workspaces"),
        "command_workspace_path": first_success.get("command_workspace_path"),
        "command_workspace_paths": launch_execution.get("command_workspace_paths"),
        "remote_isolation_mode": first_success.get("remote_isolation_mode"),
        "remote_launch_status": "partial_running" if failed_records else "running",
        "gpu_lease_reconcile": reconcile_state,
        "gpu_probe": gpu_probe,
        "selected_gpu": first_success.get("selected_gpu"),
        "gpu_lease": first_success.get("gpu_lease"),
        "remote_experiments": experiment_records,
        "remote_launch_failures": failed_records,
        "execution_plan": _serialize_execution_plan(execution_plan),
        "artifact_refs": artifact_refs,
        "completed_at": _iso_now(),
    }
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt,
        finished_at=datetime.now(UTC),
        metadata_updates=metadata_updates,
    )
    _set_stage_state(
        run.id,
        "summarize_results",
        status="completed",
        message="远程实验启动摘要已整理完成。",
        progress_pct=100,
    )
    _record_stage_output(
        run.id,
        "summarize_results",
        {
            "summary": excerpt,
            "content": summary_markdown,
            "provider": summary_execution["provider"],
            "model_role": summary_execution["model_role"],
            "model_source": summary_execution["model_source"],
            "model": summary_execution["model"],
            "variant": summary_execution["variant"],
            "role_template_id": summary_execution["role_template_id"],
            "workspace_path": workspace_path,
            "artifact_refs": artifact_refs,
        },
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": summary_markdown,
        "command": first_success.get("command"),
        "effective_command": first_success.get("effective_command"),
        "artifact_refs": artifact_refs,
        "remote_session_name": first_success.get("remote_session_name"),
        "remote_session_names": launch_execution.get("remote_session_names"),
        "remote_execution_workspace": first_success.get("remote_execution_workspace"),
        "remote_execution_workspaces": launch_execution.get("remote_execution_workspaces"),
        "command_workspace_path": first_success.get("command_workspace_path"),
        "command_workspace_paths": launch_execution.get("command_workspace_paths"),
        "remote_isolation_mode": first_success.get("remote_isolation_mode"),
        "selected_gpu": first_success.get("selected_gpu"),
        "gpu_lease": first_success.get("gpu_lease"),
        "experiments": experiment_records,
        "launch_failures": failed_records,
    }
    if run.task_id:
        global_tracker.set_result(run.task_id, result)
    return result


def _execute_remote_run_experiment(
    context: WorkflowContext,
    *,
    inspection: dict[str, Any],
    execution_plan: list[ExecutionPlanItem],
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    return _execute_remote_run_experiment_batch(
        context,
        inspection=inspection,
        execution_plan=execution_plan,
        progress_callback=progress_callback,
    )

def _execute_paper_writing(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    materials = _build_writing_materials(context)
    venue, template_name = resolve_paper_venue(context.metadata)
    paper_titles = [paper.title for paper in context.selected_papers]
    artifact_refs = _stage_output_artifact_refs(
        context,
        "gather_materials",
        "design_figures",
        "draft_sections",
        "compile_manuscript",
        "polish_manuscript",
    )

    if resume_stage_id in {"design_figures", "draft_sections", "compile_manuscript", "polish_manuscript"}:
        plan_markdown = _stage_output_content(context, "gather_materials")
        if not plan_markdown:
            raise RuntimeError("恢复论文流程失败：缺少 PAPER_PLAN 阶段产物。")
    else:
        _patch_run(run.id, active_phase="gather_materials", summary="正在生成 PAPER_PLAN。")
        _set_stage_state(
            run.id,
            "gather_materials",
            status="running",
            message="正在生成 PAPER_PLAN。",
            progress_pct=20,
        )
        _emit_progress(progress_callback, "正在生成 PAPER_PLAN。", 20)
        plan_execution = _invoke_role_markdown(
            context,
            "gather_materials",
            _build_paper_plan_phase_prompt(context, materials),
            stage="project_paper_writing_plan",
            max_tokens=2600,
            request_timeout=240,
        )
        plan_markdown = _resolve_generic_markdown(
            plan_execution["result"],
            fallback=(
                f"# PAPER_PLAN\n\n"
                f"- Venue: {venue}\n"
                f"- Project: {context.project.name}\n"
                "- Claims-Evidence Matrix: 待结合实验与 narrative 补充。\n"
                "- Section Plan: Introduction / Related Work / Method / Experiments / Conclusion\n"
            ),
        )
        plan_bundle = build_paper_plan_bundle(
            project_name=context.project.name,
            project_description=context.project.description or "",
            prompt=context.run.prompt,
            stage_markdown=plan_markdown,
            paper_summaries=paper_titles,
            venue=venue,
            template_name=template_name,
        )
        stage_artifacts = [
            artifact
            for relative_path, content in plan_bundle.items()
            for artifact in [
                _write_run_artifact(
                    context,
                    relative_path,
                    content,
                    kind="report" if relative_path.lower().endswith(".md") else "artifact",
                )
            ]
            if artifact
        ]
        artifact_refs.extend(stage_artifacts)
        _record_stage_output(
            run.id,
            "gather_materials",
            {
                "summary": _markdown_excerpt(plan_markdown),
                "content": plan_markdown,
                "provider": plan_execution.get("provider"),
                "model": plan_execution.get("model"),
                "variant": plan_execution.get("variant"),
                "model_role": plan_execution.get("model_role"),
                "model_source": plan_execution.get("model_source"),
                "role_template_id": plan_execution.get("role_template_id"),
                "artifact_refs": stage_artifacts,
            },
        )
        _set_stage_state(
            run.id,
            "gather_materials",
            status="completed",
            message="PAPER_PLAN 已生成。",
            progress_pct=32,
        )
        _maybe_pause_after_stage(
            context,
            "gather_materials",
            "design_figures",
            stage_summary=_markdown_excerpt(plan_markdown),
        )

    if resume_stage_id in {"draft_sections", "compile_manuscript", "polish_manuscript"}:
        figure_markdown = _stage_output_content(context, "design_figures")
        if not figure_markdown:
            raise RuntimeError("恢复论文流程失败：缺少 FIGURE_PLAN 阶段产物。")
    else:
        _patch_run(run.id, active_phase="design_figures", summary="正在生成 FIGURE_PLAN。")
        _set_stage_state(
            run.id,
            "design_figures",
            status="running",
            message="正在生成 FIGURE_PLAN。",
            progress_pct=44,
        )
        _emit_progress(progress_callback, "正在生成 FIGURE_PLAN。", 44)
        figure_execution = _invoke_role_markdown(
            context,
            "design_figures",
            _build_paper_figure_phase_prompt(context, plan_markdown, materials),
            stage="project_paper_writing_figure",
            max_tokens=2400,
            request_timeout=220,
        )
        figure_markdown = _resolve_generic_markdown(
            figure_execution["result"],
            fallback=(
                "# FIGURE_PLAN\n\n"
                "- Auto-generated: main result table, ablation plot\n"
                "- Manual: hero figure / architecture diagram\n"
                "- Include snippet: figures/latex_includes.tex\n"
            ),
        )
        figure_bundle = build_figure_bundle(
            project_name=context.project.name,
            prompt=context.run.prompt,
            stage_markdown=figure_markdown,
            venue=venue,
        )
        stage_artifacts = [
            artifact
            for relative_path, content in figure_bundle.items()
            for artifact in [
                _write_run_artifact(
                    context,
                    relative_path,
                    content,
                    kind="report" if relative_path.lower().endswith(".md") else "artifact",
                )
            ]
            if artifact
        ]
        artifact_refs.extend(stage_artifacts)
        _record_stage_output(
            run.id,
            "design_figures",
            {
                "summary": _markdown_excerpt(figure_markdown),
                "content": figure_markdown,
                "provider": figure_execution.get("provider"),
                "model": figure_execution.get("model"),
                "variant": figure_execution.get("variant"),
                "model_role": figure_execution.get("model_role"),
                "model_source": figure_execution.get("model_source"),
                "role_template_id": figure_execution.get("role_template_id"),
                "artifact_refs": stage_artifacts,
            },
        )
        _set_stage_state(
            run.id,
            "design_figures",
            status="completed",
            message="FIGURE_PLAN 已生成。",
            progress_pct=56,
        )
        _maybe_pause_after_stage(
            context,
            "design_figures",
            "draft_sections",
            stage_summary=_markdown_excerpt(figure_markdown),
        )

    if resume_stage_id in {"compile_manuscript", "polish_manuscript"}:
        draft_markdown = _stage_output_content(context, "draft_sections")
        if not draft_markdown:
            raise RuntimeError("恢复论文流程失败：缺少 paper-write 阶段产物。")
    else:
        _patch_run(run.id, active_phase="draft_sections", summary="正在生成论文正文与 LaTeX 工作区。")
        _set_stage_state(
            run.id,
            "draft_sections",
            status="running",
            message="正在生成论文正文与 LaTeX 工作区。",
            progress_pct=64,
        )
        _emit_progress(progress_callback, "正在生成论文正文与 LaTeX 工作区。", 64)
        draft_execution = _invoke_role_markdown(
            context,
            "draft_sections",
            _build_paper_write_phase_prompt(
                context,
                materials=materials,
                plan_markdown=plan_markdown,
                figure_markdown=figure_markdown,
            ),
            stage="project_paper_writing_write",
            max_tokens=2800,
            request_timeout=240,
        )
        draft_markdown = _resolve_paper_draft_markdown(context, draft_execution["result"])
        write_bundle = build_paper_write_bundle(
            project_name=context.project.name,
            project_description=context.project.description or "",
            prompt=context.run.prompt,
            stage_markdown=draft_markdown,
            venue=venue,
            template_name=template_name,
            paper_titles=paper_titles,
        )
        stage_artifacts = [
            artifact
            for relative_path, content in write_bundle.items()
            for artifact in [
                _write_run_artifact(
                    context,
                    relative_path,
                    content,
                    kind="report" if relative_path.lower().endswith(".md") else "artifact",
                )
            ]
            if artifact
        ]
        artifact_refs.extend(stage_artifacts)
        _record_stage_output(
            run.id,
            "draft_sections",
            {
                "summary": _markdown_excerpt(draft_markdown),
                "content": draft_markdown,
                "provider": draft_execution.get("provider"),
                "model": draft_execution.get("model"),
                "variant": draft_execution.get("variant"),
                "model_role": draft_execution.get("model_role"),
                "model_source": draft_execution.get("model_source"),
                "role_template_id": draft_execution.get("role_template_id"),
                "artifact_refs": stage_artifacts,
            },
        )
        _set_stage_state(
            run.id,
            "draft_sections",
            status="completed",
            message="paper-write 已完成，准备编译。",
            progress_pct=76,
        )
        _maybe_pause_after_stage(
            context,
            "draft_sections",
            "compile_manuscript",
            stage_summary=_markdown_excerpt(draft_markdown),
        )

    if resume_stage_id == "polish_manuscript":
        compile_markdown = _stage_output_content(context, "compile_manuscript")
        if not compile_markdown:
            raise RuntimeError("恢复论文流程失败：缺少 paper-compile 阶段产物。")
    else:
        _patch_run(run.id, active_phase="compile_manuscript", summary="正在执行论文编译检查。")
        _set_stage_state(
            run.id,
            "compile_manuscript",
            status="running",
            message="正在执行论文编译检查。",
            progress_pct=84,
        )
        _emit_progress(progress_callback, "正在执行论文编译检查。", 84)
        compile_command = _resolve_paper_compile_command(context)
        stage_artifacts: list[dict[str, Any]] = []
        if compile_command:
            compile_markdown, stage_artifacts, _execution = _execute_compile_pass(
                context,
                compile_command=compile_command,
                report_relative_path="reports/PAPER_COMPILE.md",
            )
        else:
            compile_execution = _invoke_role_markdown(
                context,
                "compile_manuscript",
                _build_paper_compile_phase_prompt(
                    context,
                    draft_markdown=draft_markdown,
                    compile_command=None,
                ),
                stage="project_paper_writing_compile",
                max_tokens=2200,
                request_timeout=220,
            )
            compile_markdown = _resolve_generic_markdown(
                compile_execution["result"],
                fallback=_missing_compile_markdown(),
            )
            artifact = _write_run_artifact(context, "reports/PAPER_COMPILE.md", compile_markdown, kind="report")
            if artifact:
                stage_artifacts.append(artifact)
        artifact_refs.extend(stage_artifacts)
        _record_stage_output(
            run.id,
            "compile_manuscript",
            {
                "summary": _markdown_excerpt(compile_markdown),
                "content": compile_markdown,
                "provider": "workspace_executor" if compile_command else "project_paper_compile",
                "model": None if compile_command else None,
                "variant": None,
                "model_role": _stage_model_role(context, "compile_manuscript"),
                "model_source": "workspace_executor" if compile_command else "project_paper_compile",
                "role_template_id": _stage_role_id(context, "compile_manuscript"),
                "artifact_refs": stage_artifacts,
            },
        )
        _set_stage_state(
            run.id,
            "compile_manuscript",
            status="completed",
            message="paper-compile 已完成。",
            progress_pct=92,
        )
        _maybe_pause_after_stage(
            context,
            "compile_manuscript",
            "polish_manuscript",
            stage_summary=_markdown_excerpt(compile_markdown),
        )

    _patch_run(run.id, active_phase="polish_manuscript", summary="正在执行论文改进循环。")
    _set_stage_state(
        run.id,
        "polish_manuscript",
        status="running",
        message="正在执行论文改进循环。",
        progress_pct=96,
    )
    _emit_progress(progress_callback, "正在执行论文改进循环。", 96)
    improvement_thread_id = _stable_workflow_thread_id(context, "paper_improvement_thread_id", "paper-improvement")
    current_draft = draft_markdown
    current_compile = compile_markdown
    score_round_one: float | None = None
    score_round_two: float | None = None
    verdict_round_one: str | None = None
    verdict_round_two: str | None = None
    review_round_one = ""
    review_round_two = ""
    revision_notes = ""
    action_items_round_one: list[str] = []
    action_items_round_two: list[str] = []
    final_revision_execution: dict[str, Any] | None = None
    final_stage_artifacts: list[dict[str, Any]] = []
    original_pdf_artifact = _snapshot_primary_pdf_output(context, "paper/main_round0_original.pdf")
    if original_pdf_artifact:
        final_stage_artifacts.append(original_pdf_artifact)

    for round_number in (1, 2):
        review_execution = _invoke_role_markdown(
            context,
            "polish_manuscript",
            _build_paper_review_round_prompt(
                context,
                draft_markdown=current_draft,
                compile_markdown=current_compile,
                round_number=round_number,
            ),
            stage=f"project_paper_writing_improve_review_{round_number}",
            max_tokens=2200,
            request_timeout=240,
        )
        review_markdown = _resolve_generic_markdown(
            review_execution["result"],
            fallback=(
                f"# Review Round {round_number}\n\n"
                "Score: 6.5\n\n"
                "- 需要进一步压实贡献主张、实验描述与局限性。"
            ),
        )
        review_state = parse_review_text(review_markdown)
        revision_execution = _invoke_role_markdown(
            context,
            "polish_manuscript",
            _build_paper_revision_round_prompt(
                context,
                draft_markdown=current_draft,
                review_markdown=review_markdown,
                compile_markdown=current_compile,
                round_number=round_number,
            ),
            stage=f"project_paper_writing_improve_revise_{round_number}",
            max_tokens=3000,
            request_timeout=260,
        )
        revised_markdown = _resolve_paper_polish_markdown(current_draft, revision_execution["result"])
        round_artifacts = _materialize_manuscript_workspace(
            context,
            revised_markdown,
            report_relative_path=f"reports/manuscript-round{round_number}.md",
        )
        final_stage_artifacts.extend(round_artifacts)

        round_compile_command = _resolve_paper_compile_command(context)
        if round_compile_command:
            round_compile_markdown, compile_artifacts, _round_execution = _execute_compile_pass(
                context,
                compile_command=round_compile_command,
                report_relative_path=f"reports/PAPER_COMPILE_round{round_number}.md",
                log_relative_path=f"logs/paper_compile_round{round_number}.log",
            )
            final_stage_artifacts.extend(compile_artifacts)
        else:
            round_compile_markdown = _missing_compile_markdown()
            round_compile_artifact = _write_run_artifact(
                context,
                f"reports/PAPER_COMPILE_round{round_number}.md",
                round_compile_markdown,
                kind="report",
            )
            if round_compile_artifact:
                final_stage_artifacts.append(round_compile_artifact)

        pdf_snapshot = _snapshot_primary_pdf_output(context, f"paper/main_round{round_number}.pdf")
        if pdf_snapshot:
            final_stage_artifacts.append(pdf_snapshot)

        applied_score = review_state.get("score")
        applied_verdict = str(review_state.get("verdict") or "not ready")
        applied_action_items = list(review_state.get("action_items") or [])
        if round_number == 1:
            score_round_one = applied_score
            verdict_round_one = applied_verdict
            review_round_one = review_markdown
            action_items_round_one = applied_action_items
            revision_notes = (
                "# Revision Notes\n\n"
                "## Round 1 Review Summary\n"
                f"{_markdown_excerpt(review_markdown, limit=700)}\n\n"
                "## Applied Changes\n"
                f"{_markdown_excerpt(revised_markdown, limit=700)}\n"
            )
        else:
            score_round_two = applied_score
            verdict_round_two = applied_verdict
            review_round_two = review_markdown
            action_items_round_two = applied_action_items
        current_draft = revised_markdown
        current_compile = round_compile_markdown
        final_revision_execution = revision_execution

    final_markdown = current_draft
    improvement_bundle = build_paper_improvement_bundle(
        project_name=context.project.name,
        review_round_one=review_round_one,
        revision_notes=revision_notes,
        review_round_two=review_round_two,
        score_round_one=score_round_one,
        score_round_two=score_round_two,
        verdict_round_one=verdict_round_one,
        verdict_round_two=verdict_round_two,
        action_items_round_one=action_items_round_one,
        action_items_round_two=action_items_round_two,
    )
    for relative_path, content in improvement_bundle.items():
        artifact = _write_run_artifact(
            context,
            relative_path,
            content,
            kind="report" if relative_path.lower().endswith(".md") else "artifact",
        )
        if artifact:
            final_stage_artifacts.append(artifact)
    final_draft_artifact = _write_run_artifact(
        context,
        "reports/manuscript-draft.md",
        final_markdown,
        kind="report",
    )
    if final_draft_artifact:
        final_stage_artifacts.append(final_draft_artifact)
    improvement_log = (
        "# PAPER_IMPROVEMENT_LOG\n\n"
        "## Round 1 Review\n"
        f"{_markdown_excerpt(review_round_one, limit=600)}\n\n"
        "## Round 2 Review\n"
        f"{_markdown_excerpt(review_round_two, limit=600)}\n\n"
        "## Final Draft Summary\n"
        f"{_markdown_excerpt(final_markdown, limit=700)}\n"
    )
    improvement_log_artifact = _write_run_artifact(
        context,
        "PAPER_IMPROVEMENT_LOG.md",
        improvement_log,
        kind="report",
    )
    if improvement_log_artifact:
        final_stage_artifacts.append(improvement_log_artifact)
    improvement_state_artifact = _write_run_json_artifact(
        context,
        "PAPER_IMPROVEMENT_STATE.json",
        {
            "current_round": 2,
            "threadId": improvement_thread_id,
            "last_score": score_round_two if score_round_two is not None else score_round_one,
            "status": "completed",
            "timestamp": _iso_now(),
        },
        kind="artifact",
    )
    if improvement_state_artifact:
        final_stage_artifacts.append(improvement_state_artifact)
    artifact_refs.extend(final_stage_artifacts)
    artifact_refs = _dedupe_artifact_refs(artifact_refs)

    generated_content_id = None
    with session_scope() as session:
        generated = GeneratedContentRepository(session).create(
            content_type="project_paper_draft",
            title=f"{context.project.name} 论文草稿",
            markdown=final_markdown,
            keyword=context.project.name,
            paper_id=context.selected_papers[0].id if context.selected_papers else None,
            metadata_json={
                "project_id": context.project.id,
                "run_id": run.id,
                "workflow_type": run.workflow_type.value,
            },
        )
        generated_content_id = generated.id

    report_markdown = format_paper_writing_report(
        context.project.name,
        run.prompt,
        final_markdown,
        {
            "venue": venue,
            "paper_improvement_scores": {
                "round_1": score_round_one,
                "round_2": score_round_two,
            },
            "paper_improvement_verdicts": {
                "round_1": verdict_round_one,
                "round_2": verdict_round_two,
            },
            "stage_outputs": {
                "gather_materials": {"content": plan_markdown},
                "design_figures": {"content": figure_markdown},
                "draft_sections": {"content": draft_markdown},
                "compile_manuscript": {"content": compile_markdown},
                "polish_manuscript": {
                    "content": final_markdown,
                    "score_round_one": score_round_one,
                    "score_round_two": score_round_two,
                    "verdict_round_one": verdict_round_one,
                    "verdict_round_two": verdict_round_two,
                    "action_items_round_one": action_items_round_one,
                    "action_items_round_two": action_items_round_two,
                },
            },
        },
    )
    report_artifact = _write_run_artifact(
        context,
        "reports/paper-writing-report.md",
        report_markdown,
        kind="report",
    )
    if report_artifact:
        artifact_refs.insert(0, report_artifact)

    excerpt = _markdown_excerpt(report_markdown)
    _record_stage_output(
        run.id,
        "polish_manuscript",
        {
            "summary": excerpt,
            "content": final_markdown,
            "provider": final_revision_execution.get("provider") if final_revision_execution else None,
            "model": final_revision_execution.get("model") if final_revision_execution else None,
            "variant": final_revision_execution.get("variant") if final_revision_execution else None,
            "model_role": final_revision_execution.get("model_role") if final_revision_execution else None,
            "model_source": final_revision_execution.get("model_source") if final_revision_execution else None,
            "role_template_id": final_revision_execution.get("role_template_id") if final_revision_execution else None,
            "artifact_refs": final_stage_artifacts,
            "generated_content_id": generated_content_id,
            "thread_id": improvement_thread_id,
            "score_round_one": score_round_one,
            "score_round_two": score_round_two,
            "verdict_round_one": verdict_round_one,
            "verdict_round_two": verdict_round_two,
            "action_items_round_one": action_items_round_one,
            "action_items_round_two": action_items_round_two,
        },
    )
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "论文草稿已生成。",
        finished_at=datetime.now(UTC),
        metadata_updates={
            "workflow_output_markdown": report_markdown,
            "workflow_output_excerpt": excerpt,
            "artifact_refs": artifact_refs,
            "generated_content_id": generated_content_id,
            "paper_improvement_thread_id": improvement_thread_id,
            "paper_improvement_scores": {
                "round_1": score_round_one,
                "round_2": score_round_two,
            },
            "paper_improvement_verdicts": {
                "round_1": verdict_round_one,
                "round_2": verdict_round_two,
            },
            "completed_at": _iso_now(),
        },
    )
    _set_stage_state(
        run.id,
        "polish_manuscript",
        status="completed",
        message="论文草稿已整理完成。",
        progress_pct=100,
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": report_markdown,
        "artifact_refs": artifact_refs,
        "generated_content_id": generated_content_id,
    }
    if run.task_id:
        global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        global_tracker.set_result(run.task_id, result)
    return result


def _materialize_paper_writing_artifacts(
    context: WorkflowContext,
    *,
    materials: str,
    final_markdown: str,
) -> list[dict[str, Any]]:
    venue, template_name = resolve_paper_venue(context.metadata)
    paper_titles = [paper.title for paper in context.selected_papers]
    artifact_refs: list[dict[str, Any]] = []
    bundle = {}
    bundle.update(
        build_paper_plan_bundle(
            project_name=context.project.name,
            project_description=context.project.description or "",
            prompt=context.run.prompt,
            stage_markdown=materials,
            paper_summaries=paper_titles,
            venue=venue,
            template_name=template_name,
        )
    )
    bundle.update(
        build_figure_bundle(
            project_name=context.project.name,
            prompt=context.run.prompt,
            stage_markdown=final_markdown,
            venue=venue,
        )
    )
    bundle.update(
        build_paper_write_bundle(
            project_name=context.project.name,
            project_description=context.project.description or "",
            prompt=context.run.prompt,
            stage_markdown=final_markdown,
            venue=venue,
            template_name=template_name,
            paper_titles=paper_titles,
        )
    )
    bundle["reports/manuscript-draft.md"] = final_markdown.rstrip() + "\n"

    compile_command = _resolve_paper_compile_command(context)
    if compile_command:
        try:
            _compile_markdown, compile_artifacts, _execution = _execute_compile_pass(
                context,
                compile_command=compile_command,
                report_relative_path="reports/PAPER_COMPILE.md",
            )
            artifact_refs.extend(compile_artifacts)
        except Exception as exc:
            bundle["reports/PAPER_COMPILE.md"] = (
                "# PAPER_COMPILE\n\n"
                f"- Command: `{compile_command}`\n"
                f"- Status: failed to execute compile command\n"
                f"- Error: {str(exc)}\n"
            )

    for relative_path, content in bundle.items():
        artifact = _write_run_artifact(
            context,
            relative_path,
            content,
            kind="report" if relative_path.lower().endswith(".md") else "artifact",
        )
        if artifact:
            artifact_refs.append(artifact)
    return _dedupe_artifact_refs(artifact_refs)


def _execute_rebuttal(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    venue = _resolve_rebuttal_venue(context)
    character_limit = _resolve_rebuttal_character_limit(context)
    round_label = _resolve_rebuttal_round(context)
    quick_mode = _resolve_rebuttal_quick_mode(context)
    review_bundle = _resolve_rebuttal_review_bundle(context)
    materials = _build_writing_materials(context)
    artifact_refs: list[dict[str, Any]] = _stage_output_artifact_refs(
        context,
        "normalize_reviews",
        "issue_board",
        "strategy_plan",
        "draft_rebuttal",
        "stress_test",
        "finalize_package",
    )

    normalize_payload = _stage_output_payload(context, "normalize_reviews")
    if resume_stage_id in {"issue_board", "strategy_plan", "draft_rebuttal", "stress_test", "finalize_package"}:
        normalize_markdown = str(normalize_payload.get("content") or "").strip()
        if not normalize_markdown:
            raise RuntimeError("恢复 rebuttal 失败：缺少原始 reviews 归档。")
    else:
        normalize_markdown = _render_rebuttal_reviews_markdown(
            review_bundle=review_bundle,
            venue=venue,
            round_label=round_label,
            character_limit=character_limit,
        )
        _patch_run(run.id, active_phase="normalize_reviews", summary="正在整理审稿意见与 rebuttal 约束。")
        _set_stage_state(
            run.id,
            "normalize_reviews",
            status="running",
            message="正在整理审稿意见与 rebuttal 约束。",
            progress_pct=12,
        )
        _emit_progress(progress_callback, "正在整理审稿意见与 rebuttal 约束。", 12)
        reviews_artifact = _write_run_artifact(context, "rebuttal/REVIEWS_RAW.md", normalize_markdown, kind="report")
        state_markdown = _format_rebuttal_state_markdown(
            venue=venue,
            round_label=round_label,
            character_limit=character_limit,
            quick_mode=quick_mode,
            current_phase="normalize_reviews",
            status="running",
        )
        state_artifact = _write_run_artifact(context, "rebuttal/REBUTTAL_STATE.md", state_markdown, kind="report")
        stage_artifacts = [artifact for artifact in [reviews_artifact, state_artifact] if artifact]
        artifact_refs.extend(stage_artifacts)
        _record_stage_output(
            run.id,
            "normalize_reviews",
            {
                "summary": _markdown_excerpt(normalize_markdown),
                "content": normalize_markdown,
                "provider": "system",
                "model_role": _stage_model_role(context, "normalize_reviews"),
                "model_source": "system",
                "role_template_id": _stage_role_id(context, "normalize_reviews"),
                "artifact_refs": stage_artifacts,
            },
        )
        _set_stage_state(
            run.id,
            "normalize_reviews",
            status="completed",
            message="审稿意见已归档。",
            progress_pct=18,
        )

    issue_payload = _stage_output_payload(context, "issue_board")
    if resume_stage_id in {"strategy_plan", "draft_rebuttal", "stress_test", "finalize_package"}:
        issue_board_markdown = str(issue_payload.get("content") or "").strip()
        if not issue_board_markdown:
            raise RuntimeError("恢复 rebuttal 失败：缺少 ISSUE_BOARD。")
    else:
        _patch_run(run.id, active_phase="issue_board", summary="正在拆解 reviewer concerns。")
        _set_stage_state(
            run.id,
            "issue_board",
            status="running",
            message="正在拆解 reviewer concerns。",
            progress_pct=28,
        )
        _emit_progress(progress_callback, "正在拆解 reviewer concerns。", 28)
        issue_execution = _invoke_role_markdown(
            context,
            "issue_board",
            _build_rebuttal_issue_board_prompt(
                context,
                materials=materials,
                normalize_markdown=normalize_markdown,
                venue=venue,
                round_label=round_label,
                character_limit=character_limit,
            ),
            stage="project_rebuttal_issue_board",
            max_tokens=2600,
            request_timeout=220,
        )
        issue_board_markdown = _resolve_generic_markdown(
            issue_execution["result"],
            fallback=_fallback_rebuttal_issue_board(review_bundle),
        )
        issue_artifact = _write_run_artifact(context, "rebuttal/ISSUE_BOARD.md", issue_board_markdown, kind="report")
        stage_artifacts = [artifact for artifact in [issue_artifact] if artifact]
        artifact_refs.extend(stage_artifacts)
        _record_stage_output(
            run.id,
            "issue_board",
            {
                "summary": _markdown_excerpt(issue_board_markdown),
                "content": issue_board_markdown,
                "provider": issue_execution.get("provider"),
                "model": issue_execution.get("model"),
                "variant": issue_execution.get("variant"),
                "model_role": issue_execution.get("model_role"),
                "model_source": issue_execution.get("model_source"),
                "role_template_id": issue_execution.get("role_template_id"),
                "artifact_refs": stage_artifacts,
            },
        )
        _set_stage_state(
            run.id,
            "issue_board",
            status="completed",
            message="ISSUE_BOARD 已生成。",
            progress_pct=38,
        )

    strategy_payload = _stage_output_payload(context, "strategy_plan")
    if resume_stage_id in {"draft_rebuttal", "stress_test", "finalize_package"}:
        strategy_markdown = str(strategy_payload.get("content") or "").strip()
        if not strategy_markdown:
            raise RuntimeError("恢复 rebuttal 失败：缺少 STRATEGY_PLAN。")
    else:
        _patch_run(run.id, active_phase="strategy_plan", summary="正在制定 rebuttal 回复策略。")
        _set_stage_state(
            run.id,
            "strategy_plan",
            status="running",
            message="正在制定 rebuttal 回复策略。",
            progress_pct=48,
        )
        _emit_progress(progress_callback, "正在制定 rebuttal 回复策略。", 48)
        strategy_execution = _invoke_role_markdown(
            context,
            "strategy_plan",
            _build_rebuttal_strategy_prompt(
                context,
                materials=materials,
                normalize_markdown=normalize_markdown,
                issue_board_markdown=issue_board_markdown,
                venue=venue,
                round_label=round_label,
                character_limit=character_limit,
                quick_mode=quick_mode,
            ),
            stage="project_rebuttal_strategy",
            max_tokens=2800,
            request_timeout=240,
        )
        strategy_markdown = _resolve_generic_markdown(
            strategy_execution["result"],
            fallback=_fallback_rebuttal_strategy(issue_board_markdown, venue, character_limit),
        )
        strategy_artifact = _write_run_artifact(context, "rebuttal/STRATEGY_PLAN.md", strategy_markdown, kind="report")
        stage_artifacts = [artifact for artifact in [strategy_artifact] if artifact]
        artifact_refs.extend(stage_artifacts)
        _record_stage_output(
            run.id,
            "strategy_plan",
            {
                "summary": _markdown_excerpt(strategy_markdown),
                "content": strategy_markdown,
                "provider": strategy_execution.get("provider"),
                "model": strategy_execution.get("model"),
                "variant": strategy_execution.get("variant"),
                "model_role": strategy_execution.get("model_role"),
                "model_source": strategy_execution.get("model_source"),
                "role_template_id": strategy_execution.get("role_template_id"),
                "artifact_refs": stage_artifacts,
            },
        )
        _set_stage_state(
            run.id,
            "strategy_plan",
            status="completed",
            message="STRATEGY_PLAN 已生成。",
            progress_pct=56,
        )
        if not quick_mode:
            _maybe_pause_after_stage(
                context,
                "strategy_plan",
                "draft_rebuttal",
                stage_summary=_markdown_excerpt(strategy_markdown),
            )

    draft_markdown = ""
    stress_markdown = ""
    final_markdown = ""
    paste_ready_text = ""
    character_count = 0
    generated_content_id = None

    if quick_mode:
        skip_note = (
            "# Quick Mode\n\n"
            "- 已根据审稿意见完成 issue board 与 strategy plan。\n"
            "- 当前运行未生成正式 rebuttal draft，请在确认策略后关闭 quick mode 再生成提交稿。\n"
        )
        for stage_id, progress_pct in (("draft_rebuttal", 70), ("stress_test", 82)):
            _set_stage_state(
                run.id,
                stage_id,
                status="completed",
                message="Quick mode 已跳过该阶段。",
                progress_pct=progress_pct,
            )
            _record_stage_output(
                run.id,
                stage_id,
                {
                    "summary": "Quick mode skipped",
                    "content": skip_note,
                    "provider": "system",
                    "model_role": _stage_model_role(context, stage_id),
                    "model_source": "system",
                    "role_template_id": _stage_role_id(context, stage_id),
                    "artifact_refs": [],
                },
            )
        final_markdown = skip_note
    else:
        draft_payload = _stage_output_payload(context, "draft_rebuttal")
        if resume_stage_id in {"stress_test", "finalize_package"}:
            draft_markdown = str(draft_payload.get("content") or "").strip()
            if not draft_markdown:
                raise RuntimeError("恢复 rebuttal 失败：缺少初稿内容。")
        else:
            _patch_run(run.id, active_phase="draft_rebuttal", summary="正在起草 rebuttal 初稿。")
            _set_stage_state(
                run.id,
                "draft_rebuttal",
                status="running",
                message="正在起草 rebuttal 初稿。",
                progress_pct=68,
            )
            _emit_progress(progress_callback, "正在起草 rebuttal 初稿。", 68)
            draft_execution = _invoke_role_markdown(
                context,
                "draft_rebuttal",
                _build_rebuttal_draft_prompt(
                    context,
                    materials=materials,
                    issue_board_markdown=issue_board_markdown,
                    strategy_markdown=strategy_markdown,
                    venue=venue,
                    round_label=round_label,
                    character_limit=character_limit,
                ),
                stage="project_rebuttal_draft",
                max_tokens=3400,
                request_timeout=260,
            )
            draft_markdown = _resolve_generic_markdown(
                draft_execution["result"],
                fallback=_fallback_rebuttal_draft(strategy_markdown, venue, character_limit),
            )
            draft_artifact = _write_run_artifact(context, "rebuttal/REBUTTAL_DRAFT_v1.md", draft_markdown, kind="report")
            stage_artifacts = [artifact for artifact in [draft_artifact] if artifact]
            artifact_refs.extend(stage_artifacts)
            _record_stage_output(
                run.id,
                "draft_rebuttal",
                {
                    "summary": _markdown_excerpt(draft_markdown),
                    "content": draft_markdown,
                    "provider": draft_execution.get("provider"),
                    "model": draft_execution.get("model"),
                    "variant": draft_execution.get("variant"),
                    "model_role": draft_execution.get("model_role"),
                    "model_source": draft_execution.get("model_source"),
                    "role_template_id": draft_execution.get("role_template_id"),
                    "artifact_refs": stage_artifacts,
                },
            )
            _set_stage_state(
                run.id,
                "draft_rebuttal",
                status="completed",
                message="rebuttal 初稿已生成。",
                progress_pct=74,
            )

        stress_payload = _stage_output_payload(context, "stress_test")
        if resume_stage_id == "finalize_package":
            stress_markdown = str(stress_payload.get("content") or "").strip()
            if not stress_markdown:
                raise RuntimeError("恢复 rebuttal 失败：缺少 stress test 输出。")
        else:
            _patch_run(run.id, active_phase="stress_test", summary="正在执行 rebuttal stress test。")
            _set_stage_state(
                run.id,
                "stress_test",
                status="running",
                message="正在执行 rebuttal stress test。",
                progress_pct=82,
            )
            _emit_progress(progress_callback, "正在执行 rebuttal stress test。", 82)
            stress_execution = _invoke_role_markdown(
                context,
                "stress_test",
                _build_rebuttal_stress_prompt(
                    context,
                    issue_board_markdown=issue_board_markdown,
                    strategy_markdown=strategy_markdown,
                    draft_markdown=draft_markdown,
                    venue=venue,
                    character_limit=character_limit,
                ),
                stage="project_rebuttal_stress",
                max_tokens=2200,
                request_timeout=220,
            )
            stress_markdown = _resolve_generic_markdown(
                stress_execution["result"],
                fallback=_fallback_rebuttal_stress(draft_markdown),
            )
            stress_artifact = _write_run_artifact(context, "rebuttal/MCP_STRESS_TEST.md", stress_markdown, kind="report")
            stage_artifacts = [artifact for artifact in [stress_artifact] if artifact]
            artifact_refs.extend(stage_artifacts)
            _record_stage_output(
                run.id,
                "stress_test",
                {
                    "summary": _markdown_excerpt(stress_markdown),
                    "content": stress_markdown,
                    "provider": stress_execution.get("provider"),
                    "model": stress_execution.get("model"),
                    "variant": stress_execution.get("variant"),
                    "model_role": stress_execution.get("model_role"),
                    "model_source": stress_execution.get("model_source"),
                    "role_template_id": stress_execution.get("role_template_id"),
                    "artifact_refs": stage_artifacts,
                },
            )
            _set_stage_state(
                run.id,
                "stress_test",
                status="completed",
                message="stress test 已完成。",
                progress_pct=88,
            )

        final_payload = _stage_output_payload(context, "finalize_package")
        if resume_stage_id == "finalize_package":
            final_markdown = str(final_payload.get("content") or "").strip()
            paste_ready_text = str(context.metadata.get("paste_ready_text") or "").strip()
            character_count = len(paste_ready_text)
            if not final_markdown:
                raise RuntimeError("恢复 rebuttal 失败：缺少最终提交稿。")

    _patch_run(run.id, active_phase="finalize_package", summary="正在整理最终 rebuttal 交付物。")
    _set_stage_state(
        run.id,
        "finalize_package",
        status="running",
        message="正在整理最终 rebuttal 交付物。",
        progress_pct=94,
    )
    _emit_progress(progress_callback, "正在整理最终 rebuttal 交付物。", 94)

    final_provider_payload: dict[str, Any] = {
        "provider": "system",
        "model_role": _stage_model_role(context, "finalize_package"),
        "model_source": "system",
        "role_template_id": _stage_role_id(context, "finalize_package"),
    }
    if not quick_mode and not final_markdown:
        finalize_execution = _invoke_role_markdown(
            context,
            "finalize_package",
            _build_rebuttal_finalize_prompt(
                context,
                issue_board_markdown=issue_board_markdown,
                strategy_markdown=strategy_markdown,
                draft_markdown=draft_markdown,
                stress_markdown=stress_markdown,
                venue=venue,
                round_label=round_label,
                character_limit=character_limit,
            ),
            stage="project_rebuttal_finalize",
            max_tokens=3600,
            request_timeout=260,
        )
        final_markdown = _resolve_generic_markdown(finalize_execution["result"], fallback=draft_markdown)
        final_provider_payload = {
            "provider": finalize_execution.get("provider"),
            "model": finalize_execution.get("model"),
            "variant": finalize_execution.get("variant"),
            "model_role": finalize_execution.get("model_role"),
            "model_source": finalize_execution.get("model_source"),
            "role_template_id": finalize_execution.get("role_template_id"),
        }

    if quick_mode:
        paste_ready_text = ""
        character_count = 0
    elif not paste_ready_text:
        paste_ready_text = _fit_character_limit(_markdown_to_plain_text(final_markdown), character_limit)
        character_count = len(paste_ready_text)

    report_markdown = format_rebuttal_report(
        context.project.name,
        run.prompt,
        final_markdown,
        {
            "rebuttal_venue": venue,
            "rebuttal_round": round_label,
            "rebuttal_character_limit": character_limit,
            "rebuttal_character_count": character_count,
            "rebuttal_quick_mode": quick_mode,
            "paste_ready_text": paste_ready_text,
            "stage_outputs": {
                "normalize_reviews": {"content": normalize_markdown},
                "issue_board": {"content": issue_board_markdown},
                "strategy_plan": {"content": strategy_markdown},
                "draft_rebuttal": {"content": draft_markdown or final_markdown},
                "stress_test": {"content": stress_markdown},
                "finalize_package": {"content": final_markdown},
            },
        },
    )

    final_stage_artifacts: list[dict[str, Any]] = []
    if not quick_mode:
        rich_artifact = _write_run_artifact(context, "rebuttal/REBUTTAL_DRAFT_rich.md", final_markdown, kind="report")
        paste_artifact = _write_run_artifact(
            context,
            "rebuttal/PASTE_READY.txt",
            paste_ready_text.rstrip() + "\n",
            kind="artifact",
        )
        final_stage_artifacts.extend(artifact for artifact in [rich_artifact, paste_artifact] if artifact)
    state_markdown = _format_rebuttal_state_markdown(
        venue=venue,
        round_label=round_label,
        character_limit=character_limit,
        quick_mode=quick_mode,
        current_phase="finalize_package",
        status="completed",
        character_count=character_count if not quick_mode else None,
    )
    state_artifact = _write_run_artifact(context, "rebuttal/REBUTTAL_STATE.md", state_markdown, kind="report")
    report_artifact = _write_run_artifact(context, "reports/rebuttal.md", report_markdown, kind="report")
    final_stage_artifacts.extend(artifact for artifact in [state_artifact, report_artifact] if artifact)
    artifact_refs.extend(final_stage_artifacts)
    artifact_refs = _dedupe_artifact_refs(artifact_refs)

    with session_scope() as session:
        generated = GeneratedContentRepository(session).create(
            content_type="project_rebuttal_report",
            title=f"{context.project.name} Rebuttal Report",
            markdown=report_markdown,
            keyword=context.project.name,
            paper_id=context.selected_papers[0].id if context.selected_papers else None,
            metadata_json={
                "project_id": context.project.id,
                "run_id": run.id,
                "workflow_type": run.workflow_type.value,
                "venue": venue,
                "round": round_label,
                "character_limit": character_limit,
                "character_count": character_count,
                "quick_mode": quick_mode,
            },
        )
        generated_content_id = generated.id

    excerpt = _markdown_excerpt(report_markdown)
    _record_stage_output(
        run.id,
        "finalize_package",
        {
            "summary": excerpt,
            "content": final_markdown,
            **final_provider_payload,
            "artifact_refs": final_stage_artifacts,
            "generated_content_id": generated_content_id,
        },
    )
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "Rebuttal 已完成。",
        finished_at=datetime.now(UTC),
        metadata_updates={
            "workflow_output_markdown": report_markdown,
            "workflow_output_excerpt": excerpt,
            "artifact_refs": artifact_refs,
            "generated_content_id": generated_content_id,
            "rebuttal_venue": venue,
            "rebuttal_round": round_label,
            "rebuttal_character_limit": character_limit,
            "rebuttal_character_count": character_count,
            "rebuttal_quick_mode": quick_mode,
            "paste_ready_text": paste_ready_text,
            "completed_at": _iso_now(),
        },
    )
    _set_stage_state(
        run.id,
        "finalize_package",
        status="completed",
        message="最终 rebuttal 交付物已生成。",
        progress_pct=100,
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": report_markdown,
        "artifact_refs": artifact_refs,
        "generated_content_id": generated_content_id,
        "character_limit": character_limit,
        "character_count": character_count,
        "quick_mode": quick_mode,
    }
    if run.task_id:
        global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        global_tracker.set_result(run.task_id, result)
    return result


def _execute_full_pipeline(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
) -> dict[str, Any]:
    run = context.run
    workspace_path = _resolve_workspace_path(run)
    pipeline_artifact_refs: list[dict[str, Any]] = _stage_output_artifact_refs(
        context,
        "review_prior_work",
        "implement_and_run",
        "synthesize_findings",
        "handoff_output",
    )
    if resume_stage_id == "implement_and_run":
        review_markdown = _stage_output_content(context, "review_prior_work")
        if not review_markdown:
            raise RuntimeError("恢复科研流程失败：缺少想法发现阶段结果。")
    else:
        review_prompt = _build_full_pipeline_gate_prompt(context)
        _patch_run(run.id, active_phase="review_prior_work", summary="正在执行想法发现（Gate 1）。")
        _set_stage_state(
            run.id,
            "review_prior_work",
            status="running",
            message="正在执行想法发现（Gate 1）。",
            progress_pct=18,
        )
        _emit_progress(progress_callback, "正在执行想法发现（Gate 1）。", 18)
        review_execution = _invoke_role_markdown(
            context,
            "review_prior_work",
            review_prompt,
            stage="project_full_pipeline_gate",
            max_tokens=3200,
            request_timeout=240,
        )
        review_markdown = _resolve_generic_markdown(
            review_execution["result"],
            fallback=(
                f"# IDEA_REPORT\n\n"
                f"## Direction\n{context.run.prompt or context.project.name}\n\n"
                "## Recommended Idea\n- 建议先选一个最接近现有资源与数据的方案进入实现。\n"
            ),
        )
        idea_artifact = _write_run_artifact(context, "IDEA_REPORT.md", review_markdown, kind="report")
        if idea_artifact:
            pipeline_artifact_refs.append(idea_artifact)
        _record_stage_output(
            run.id,
            "review_prior_work",
            {
                "summary": _markdown_excerpt(review_markdown),
                "content": review_markdown,
                "provider": review_execution.get("provider"),
                "model": review_execution.get("model"),
                "variant": review_execution.get("variant"),
                "model_role": review_execution.get("model_role"),
                "model_source": review_execution.get("model_source"),
                "role_template_id": review_execution.get("role_template_id"),
                "artifact_refs": [artifact for artifact in [idea_artifact] if artifact],
            },
        )
        _set_stage_state(
            run.id,
            "review_prior_work",
            status="completed",
            message="想法发现完成，等待 Gate 1 决策。",
            progress_pct=32,
        )
        _maybe_pause_after_stage(
            context,
            "review_prior_work",
            "implement_and_run",
            stage_summary=_markdown_excerpt(review_markdown),
        )

    command = _resolve_execution_command(context)
    effective_command, runtime_environment = _wrap_command_with_runtime_environment(context, command)
    command_workspace_path = _resolve_execution_workspace_path(context) or workspace_path
    runtime_environment = {
        **runtime_environment,
        "command_workspace_path": command_workspace_path,
    }
    _patch_run(run.id, active_phase="implement_and_run", summary=f"正在执行实现与实验：{command}")
    _set_stage_state(
        run.id,
        "implement_and_run",
        status="running",
        message=f"正在执行实现与实验：{command}",
        progress_pct=46,
    )
    _emit_progress(progress_callback, f"正在执行实现与实验：{command}", 46)
    if command_workspace_path and command_workspace_path != workspace_path:
        inspection = _inspect_workspace_payload(context, workspace_path_override=command_workspace_path)
    else:
        inspection = _inspect_workspace_payload(context) if workspace_path else {"workspace_path": None}
    if command_workspace_path and command_workspace_path != workspace_path:
        execution = _run_workspace_command_for_context(
            context,
            effective_command,
            timeout_sec=_resolve_execution_timeout(context),
            workspace_path_override=command_workspace_path,
        )
    else:
        execution = _run_workspace_command_for_context(
            context,
            effective_command,
            timeout_sec=_resolve_execution_timeout(context),
        )
    execution["original_command"] = command
    execution["effective_command"] = effective_command
    execution["runtime_environment"] = runtime_environment
    execution["command_workspace_path"] = command_workspace_path
    log_text = _format_command_log(execution)
    log_artifact = _write_run_log(context, log_text)
    artifact_refs = [artifact for artifact in [log_artifact] if artifact]
    _record_stage_output(
        run.id,
        "implement_and_run",
        {
            "summary": "实验命令执行成功" if execution.get("success") else "实验命令执行失败",
            "content": _command_result_preview(execution),
            "provider": "workspace_executor_remote" if run.workspace_server_id else "workspace_executor_local",
            "model_role": _stage_model_role(context, "implement_and_run"),
            "model_source": "workspace_executor",
            "role_template_id": _stage_role_id(context, "implement_and_run"),
            "command": command,
            "effective_command": effective_command,
            "runtime_environment": runtime_environment,
            "workspace_path": command_workspace_path,
            "exit_code": execution.get("exit_code"),
            "artifact_refs": artifact_refs,
        },
    )
    if not execution.get("success"):
        raise RuntimeError(
            str(execution.get("stderr") or execution.get("stdout") or f"命令退出码 {execution.get('exit_code')}")
        )
    _set_stage_state(
        run.id,
        "implement_and_run",
        status="completed",
        message="实现与实验阶段完成。",
        progress_pct=66,
    )

    _patch_run(run.id, active_phase="synthesize_findings", summary="正在综合研究结论。")
    _set_stage_state(
        run.id,
        "synthesize_findings",
        status="running",
        message="正在执行自动评审循环总结。",
        progress_pct=78,
    )
    _emit_progress(progress_callback, "正在执行自动评审循环总结。", 78)
    synthesis_prompt = _build_pipeline_synthesis_prompt(context, review_markdown, inspection, execution)
    synthesis_execution = _invoke_role_markdown(
        context,
        "synthesize_findings",
        synthesis_prompt,
        stage="project_full_pipeline_auto_review",
        max_tokens=2200,
        request_timeout=200,
    )
    findings_markdown = _resolve_pipeline_findings_markdown(context, synthesis_execution["result"], review_markdown, execution)
    auto_review_artifact = _write_run_artifact(context, "AUTO_REVIEW.md", findings_markdown, kind="report")
    auto_review_state_artifact = _write_run_json_artifact(
        context,
        "REVIEW_STATE.json",
        {
            "round": 1,
            "threadId": None,
            "status": "completed",
            "last_score": None,
            "last_verdict": "not ready",
            "pending_experiments": [],
            "timestamp": _iso_now(),
        },
        kind="artifact",
    )
    for artifact in [auto_review_artifact, auto_review_state_artifact]:
        if artifact:
            pipeline_artifact_refs.append(artifact)
    _record_stage_output(
        run.id,
        "synthesize_findings",
        {
            "summary": _markdown_excerpt(findings_markdown),
            "content": findings_markdown,
            "provider": synthesis_execution.get("provider"),
            "model": synthesis_execution.get("model"),
            "variant": synthesis_execution.get("variant"),
            "model_role": synthesis_execution.get("model_role"),
            "model_source": synthesis_execution.get("model_source"),
            "role_template_id": synthesis_execution.get("role_template_id"),
            "artifact_refs": [artifact for artifact in [auto_review_artifact, auto_review_state_artifact] if artifact],
        },
    )
    _set_stage_state(
        run.id,
        "synthesize_findings",
        status="completed",
        message="自动评审总结已完成。",
        progress_pct=90,
    )

    _patch_run(run.id, active_phase="handoff_output", summary="正在生成最终交付物。")
    _set_stage_state(
        run.id,
        "handoff_output",
        status="running",
        message="正在生成最终交付物。",
        progress_pct=94,
    )
    _emit_progress(progress_callback, "正在生成最终交付物。", 94)
    handoff_prompt = _build_pipeline_handoff_prompt(context, review_markdown, findings_markdown, execution)
    handoff_execution = _invoke_role_markdown(
        context,
        "handoff_output",
        handoff_prompt,
        stage="project_full_pipeline_handoff",
        max_tokens=2600,
        request_timeout=220,
    )
    final_markdown = _resolve_pipeline_handoff_markdown(review_markdown, findings_markdown, handoff_execution["result"])
    final_markdown = format_full_pipeline_report(
        context.project.name,
        run.prompt,
        final_markdown,
        {
            "execution_command": command,
            "effective_execution_command": effective_command,
            "execution_workspace": command_workspace_path,
            "execution_result": {
                "command": command,
                "effective_command": effective_command,
                "exit_code": execution.get("exit_code"),
                "stdout": execution.get("stdout"),
                "stderr": execution.get("stderr"),
                "success": execution.get("success"),
                "workspace_path": command_workspace_path,
                "runtime_environment": runtime_environment,
            },
            "stage_outputs": {
                "review_prior_work": {"content": review_markdown},
                "synthesize_findings": {"content": findings_markdown},
                "handoff_output": {"content": final_markdown},
            },
        },
    )
    final_artifact = _write_run_artifact(context, "reports/final-handoff.md", final_markdown, kind="report")
    if final_artifact:
        artifact_refs.append(final_artifact)
    artifact_refs.extend(pipeline_artifact_refs)
    artifact_refs = _dedupe_artifact_refs(artifact_refs)

    generated_content_id = None
    with session_scope() as session:
        generated = GeneratedContentRepository(session).create(
            content_type="project_pipeline_report",
            title=f"{context.project.name} 科研流程报告",
            markdown=final_markdown,
            keyword=context.project.name,
            paper_id=context.selected_papers[0].id if context.selected_papers else None,
            metadata_json={
                "project_id": context.project.id,
                "run_id": run.id,
                "workflow_type": run.workflow_type.value,
                "execution_command": command,
                "effective_execution_command": effective_command,
            },
        )
        generated_content_id = generated.id

    excerpt = _markdown_excerpt(final_markdown)
    _record_stage_output(
        run.id,
        "handoff_output",
        {
            "summary": excerpt,
            "content": final_markdown,
            "provider": handoff_execution.get("provider"),
            "model": handoff_execution.get("model"),
            "variant": handoff_execution.get("variant"),
            "model_role": handoff_execution.get("model_role"),
            "model_source": handoff_execution.get("model_source"),
            "role_template_id": handoff_execution.get("role_template_id"),
            "artifact_refs": artifact_refs,
            "generated_content_id": generated_content_id,
        },
    )
    _patch_run(
        run.id,
        status=ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "科研流程执行完成。",
        finished_at=datetime.now(UTC),
        metadata_updates={
            "workflow_output_markdown": final_markdown,
            "workflow_output_excerpt": excerpt,
            "execution_command": command,
            "effective_execution_command": effective_command,
            "runtime_environment": runtime_environment,
            "execution_workspace": command_workspace_path,
            "artifact_refs": artifact_refs,
            "generated_content_id": generated_content_id,
            "completed_at": _iso_now(),
        },
    )
    _set_stage_state(
        run.id,
        "handoff_output",
        status="completed",
        message="最终交付物已生成。",
        progress_pct=100,
    )
    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": final_markdown,
        "command": command,
        "effective_command": effective_command,
        "artifact_refs": artifact_refs,
        "generated_content_id": generated_content_id,
    }
    if run.task_id:
        global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        global_tracker.set_result(run.task_id, result)
    return result


def _stage_role_id(context: WorkflowContext, stage_id: str, fallback: str = "codex") -> str:
    stage = _stage_binding_for_invocation(context, stage_id)
    if stage is not None:
        candidate = str(stage.get("selected_agent_id") or stage.get("default_agent_id") or "").strip()
        if candidate:
            return _normalize_role_id(candidate)
    return _normalize_role_id(fallback)


def _resolve_stage_alias_ids(workflow_type: ProjectWorkflowType | str, stage_id: str) -> list[str]:
    workflow_key = str(workflow_type.value if isinstance(workflow_type, ProjectWorkflowType) else workflow_type)
    normalized_stage_id = str(stage_id or "").strip()
    if not normalized_stage_id:
        return []
    alias_map = _WORKFLOW_STAGE_ALIASES.get(workflow_key, {})
    resolved: list[str] = [normalized_stage_id]

    for alias_id in alias_map.get(normalized_stage_id, []) or []:
        if alias_id and alias_id not in resolved:
            resolved.append(alias_id)

    for alias_id, target_ids in alias_map.items():
        if normalized_stage_id in (target_ids or []) and alias_id not in resolved:
            resolved.append(alias_id)

    return resolved


def _stage_binding(context: WorkflowContext, stage_id: str) -> dict[str, Any] | None:
    orchestration = context.metadata.get("orchestration")
    stages = orchestration.get("stages") if isinstance(orchestration, dict) else []
    stage_ids = _resolve_stage_alias_ids(context.run.workflow_type, stage_id)
    for stage in stages or []:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("id")) in stage_ids:
            return stage
    return None


def _stage_binding_for_invocation(context: WorkflowContext, stage_id: str) -> dict[str, Any] | None:
    orchestration = context.metadata.get("orchestration")
    stages = orchestration.get("stages") if isinstance(orchestration, dict) else []
    normalized_stage_id = str(stage_id or "").strip()
    if not normalized_stage_id:
        return None

    for stage in stages or []:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("id") or "").strip() == normalized_stage_id:
            return stage

    workflow_key = context.run.workflow_type.value
    preferred_stage_id = str(
        (_WORKFLOW_STAGE_INVOCATION_BINDINGS.get(workflow_key, {}) or {}).get(normalized_stage_id) or ""
    ).strip()
    if preferred_stage_id:
        for stage in stages or []:
            if not isinstance(stage, dict):
                continue
            if str(stage.get("id") or "").strip() == preferred_stage_id:
                return stage

    return _stage_binding(context, normalized_stage_id)


def _stage_label(context: WorkflowContext, stage_id: str) -> str:
    stage = _stage_binding(context, stage_id)
    label = str((stage or {}).get("label") or "").strip()
    return label or stage_id


def _stage_requires_checkpoint(context: WorkflowContext, stage_id: str) -> bool:
    if not bool(context.metadata.get("human_checkpoint_enabled")):
        return False
    stage = _stage_binding(context, stage_id)
    return bool((stage or {}).get("checkpoint_required"))


def _stage_output_payload(context: WorkflowContext, stage_id: str) -> dict[str, Any]:
    stage_outputs = context.metadata.get("stage_outputs")
    if not isinstance(stage_outputs, dict):
        return {}
    for candidate in [str(stage_id or "").strip(), *_resolve_stage_alias_ids(context.run.workflow_type, stage_id)]:
        payload = stage_outputs.get(candidate)
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _stage_output_content(context: WorkflowContext, stage_id: str) -> str:
    return str(_stage_output_payload(context, stage_id).get("content") or "").strip()


def _stage_output_artifact_refs(context: WorkflowContext, *stage_ids: str) -> list[dict[str, Any]]:
    stage_outputs = context.metadata.get("stage_outputs")
    if not isinstance(stage_outputs, dict):
        return []
    selected_ids = {str(stage_id).strip() for stage_id in stage_ids if str(stage_id).strip()}
    refs: list[dict[str, Any]] = []
    for key, value in stage_outputs.items():
        if selected_ids and str(key) not in selected_ids:
            continue
        if not isinstance(value, dict):
            continue
        artifact_refs = value.get("artifact_refs")
        if isinstance(artifact_refs, list):
            refs.extend(item for item in artifact_refs if isinstance(item, dict))
    return _dedupe_artifact_refs(refs)


def _first_stage_id(context: WorkflowContext) -> str:
    orchestration = context.metadata.get("orchestration")
    if isinstance(orchestration, dict):
        stages = orchestration.get("stages")
        if isinstance(stages, list):
            for item in stages:
                if isinstance(item, dict):
                    stage_id = str(item.get("id") or "").strip()
                    if stage_id:
                        return stage_id
    return "collect_context"


def _maybe_pause_after_stage(
    context: WorkflowContext,
    completed_stage_id: str,
    next_stage_id: str | None,
    *,
    stage_summary: str | None = None,
) -> None:
    if not next_stage_id or not _stage_requires_checkpoint(context, completed_stage_id):
        return
    task_id = str(context.run.task_id or "").strip()
    if not task_id:
        raise RuntimeError("阶段确认失败：当前运行缺少任务追踪标识。")
    message = (
        f"阶段“{_stage_label(context, completed_stage_id)}”已完成，等待人工确认后继续执行“{_stage_label(context, next_stage_id)}”。"
    )
    mark_run_waiting_for_stage_checkpoint(
        context.run.id,
        task_id=task_id,
        completed_stage_id=completed_stage_id,
        completed_stage_label=_stage_label(context, completed_stage_id),
        resume_stage_id=next_stage_id,
        resume_stage_label=_stage_label(context, next_stage_id),
        stage_summary=stage_summary,
    )
    raise TaskPausedError(message)


def _stage_model_role(context: WorkflowContext, stage_id: str, fallback: str = "executor") -> str:
    stage = _stage_binding_for_invocation(context, stage_id)
    value = str((stage or {}).get("model_role") or fallback or "executor").strip().lower()
    return value if value in {"executor", "reviewer"} else "executor"


def _engine_binding_snapshot(metadata: dict[str, Any], role: str) -> dict[str, Any]:
    bindings = metadata.get("engine_bindings")
    if isinstance(bindings, dict):
        payload = bindings.get(role)
        if isinstance(payload, dict):
            return dict(payload)
    engine_id = str(metadata.get(f"{role}_engine_id") or "").strip()
    if engine_id:
        return {"id": engine_id}
    return {}


def _resolve_engine_binding(
    engine_id: str | None,
    *,
    model_source: str,
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_id = str(engine_id or "").strip()
    if not normalized_id:
        return None
    resolved = resolve_llm_engine_profile(normalized_id)
    payload = dict(fallback_payload or {})
    if isinstance(resolved, dict):
        payload.update(
            {
                "id": resolved.get("id") or normalized_id,
                "label": resolved.get("label"),
                "provider": resolved.get("provider"),
                "model": resolved.get("model"),
                "default_variant": resolved.get("default_variant"),
            }
        )
    payload.setdefault("id", normalized_id)
    payload.setdefault("label", normalized_id)
    return {
        "engine_id": str(payload.get("id") or normalized_id),
        "engine_label": str(payload.get("label") or normalized_id),
        "provider": str(payload.get("provider") or "").strip() or None,
        "display_model": str(payload.get("model") or "").strip() or None,
        "default_variant": str(payload.get("default_variant") or "").strip() or None,
        "model_source": model_source,
    }


def _resolve_role_profile(role_id: str) -> dict[str, str | None]:
    normalized_role_id = _normalize_role_id(role_id)
    return dict(_ROLE_TEMPLATE_MAP.get(normalized_role_id) or _ROLE_TEMPLATE_MAP["codex"])


def _resolve_stage_model_target(
    context: WorkflowContext,
    stage_id: str,
    role: dict[str, str | None],
    llm: LLMClient,
) -> dict[str, str | None]:
    model_role = _stage_model_role(context, stage_id)
    stage = _stage_binding_for_invocation(context, stage_id)
    stage_engine = _resolve_engine_binding(
        str((stage or {}).get("selected_engine_id") or "").strip() or None,
        model_source="stage_engine_profile",
    )
    if stage_engine is not None:
        return {
            "model_role": model_role,
            "model_source": str(stage_engine["model_source"]),
            "model_override": str(stage_engine["engine_id"]),
            "engine_id": str(stage_engine["engine_id"]),
            "engine_label": str(stage_engine["engine_label"]),
            "display_model": stage_engine.get("display_model"),
            "provider": stage_engine.get("provider"),
            "variant_override": stage_engine.get("default_variant") or str(role.get("variant") or "medium"),
        }

    run_engine = _resolve_engine_binding(
        str(_engine_binding_snapshot(context.metadata, "reviewer" if model_role == "reviewer" else "executor").get("id") or "").strip() or None,
        model_source="reviewer_engine_profile" if model_role == "reviewer" else "executor_engine_profile",
        fallback_payload=_engine_binding_snapshot(context.metadata, "reviewer" if model_role == "reviewer" else "executor"),
    )
    if run_engine is not None:
        return {
            "model_role": model_role,
            "model_source": str(run_engine["model_source"]),
            "model_override": str(run_engine["engine_id"]),
            "engine_id": str(run_engine["engine_id"]),
            "engine_label": str(run_engine["engine_label"]),
            "display_model": run_engine.get("display_model"),
            "provider": run_engine.get("provider"),
            "variant_override": run_engine.get("default_variant") or str(role.get("variant") or "medium"),
        }

    explicit = context.run.reviewer_model if model_role == "reviewer" else context.run.executor_model
    if explicit:
        return {
            "model_role": model_role,
            "model_source": "reviewer_model" if model_role == "reviewer" else "executor_model",
            "model_override": explicit,
            "engine_id": None,
            "engine_label": None,
            "display_model": explicit,
            "provider": None,
            "variant_override": str(role.get("variant") or "medium"),
        }
    return {
        "model_role": model_role,
        "model_source": "role_template",
        "model_override": _resolve_model_override_for_role(llm, role),
        "engine_id": None,
        "engine_label": None,
        "display_model": _resolve_model_override_for_role(llm, role),
        "provider": None,
        "variant_override": str(role.get("variant") or "medium"),
    }


def _invoke_role_markdown(
    context: WorkflowContext,
    stage_id: str,
    prompt: str,
    *,
    stage: str,
    max_tokens: int,
    request_timeout: int,
) -> dict[str, Any]:
    role_template_id = _stage_role_id(context, stage_id)
    role = _resolve_role_profile(role_template_id)
    llm = LLMClient()
    target = _resolve_stage_model_target(context, stage_id, role, llm)
    model_override = target["model_override"]
    variant = str(target.get("variant_override") or role.get("variant") or "medium")
    resolved_prompt = _apply_reviewer_independence_contract(prompt, model_role=target["model_role"])
    result: LLMResult | None = None
    if str(target.get("model_role") or "").strip().lower() == "reviewer":
        agent_text = _invoke_reviewer_workspace_agent(
            context,
            stage_id=stage_id,
            stage=stage,
            prompt=resolved_prompt,
            target=target,
            role=role,
            role_template_id=role_template_id,
            variant=variant,
            output_mode="markdown",
        )
        if agent_text and not _looks_like_llm_error(agent_text):
            result = LLMResult(content=sanitize_project_markdown(agent_text))
    if result is None:
        result = llm.summarize_text(
            resolved_prompt,
            stage=stage,
            model_override=model_override,
            variant_override=variant,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )
    return {
        "result": result,
        "provider": target.get("provider") or llm.provider,
        "model": target.get("display_model") or model_override,
        "variant": variant,
        "model_role": target["model_role"],
        "model_source": target["model_source"],
        "role_template_id": role_template_id,
        "role_label": role.get("label"),
        "engine_id": target.get("engine_id"),
        "engine_label": target.get("engine_label"),
    }


def _invoke_role_json(
    context: WorkflowContext,
    stage_id: str,
    prompt: str,
    *,
    stage: str,
    max_tokens: int,
    request_timeout: int,
    max_retries: int = 1,
) -> dict[str, Any]:
    role_template_id = _stage_role_id(context, stage_id)
    role = _resolve_role_profile(role_template_id)
    llm = LLMClient()
    target = _resolve_stage_model_target(context, stage_id, role, llm)
    model_override = target["model_override"]
    variant = str(target.get("variant_override") or role.get("variant") or "medium")
    resolved_prompt = _apply_reviewer_independence_contract(prompt, model_role=target["model_role"])
    result: LLMResult | None = None
    if str(target.get("model_role") or "").strip().lower() == "reviewer":
        agent_text = _invoke_reviewer_workspace_agent(
            context,
            stage_id=stage_id,
            stage=stage,
            prompt=resolved_prompt,
            target=target,
            role=role,
            role_template_id=role_template_id,
            variant=variant,
            output_mode="json",
        )
        parsed = _parse_json_payload_text(agent_text or "")
        if parsed:
            result = LLMResult(content=str(agent_text or "").strip(), parsed_json=parsed)
    if result is None:
        result = llm.complete_json(
            resolved_prompt,
            stage=stage,
            model_override=model_override,
            variant_override=variant,
            max_tokens=max_tokens,
            max_retries=max_retries,
            request_timeout=request_timeout,
        )
    return {
        "result": result,
        "provider": target.get("provider") or llm.provider,
        "model": target.get("display_model") or model_override,
        "variant": variant,
        "model_role": target["model_role"],
        "model_source": target["model_source"],
        "role_template_id": role_template_id,
        "role_label": role.get("label"),
        "engine_id": target.get("engine_id"),
        "engine_label": target.get("engine_label"),
    }


def _apply_reviewer_independence_contract(prompt: str, *, model_role: str) -> str:
    if str(model_role or "").strip().lower() != "reviewer":
        return prompt
    contract = "\n".join(
        [
            "[ResearchOS Reviewer Independence Contract]",
            "你当前扮演独立评审者，而不是执行者的延伸。",
            "如果提示中同时出现执行侧总结与原始材料，请优先依据原始材料、日志、代码片段、结果表和明确列出的产物判断。",
            "执行侧总结只能当导航线索，不能直接当证据；未经原始材料支持，不得复述为结论。",
            "如果证据不足，请明确写“证据不足”或“无法确认”，不要替执行侧补全推断。",
        ]
    )
    return f"{contract}\n\n{prompt}".strip()


def _reviewer_agent_tool_overrides() -> dict[str, bool]:
    return {tool_name: False for tool_name in sorted(_REVIEWER_AGENT_DISABLED_TOOLS)}


def _resolve_reviewer_agent_workspace_path(context: WorkflowContext) -> str | None:
    candidates = [
        str(context.metadata.get("command_workspace_path") or "").strip(),
        str(context.metadata.get("execution_workspace") or "").strip(),
        str(context.metadata.get("remote_execution_workspace") or "").strip(),
        str(_resolve_execution_workspace_path(context) or "").strip(),
        str(_resolve_workspace_path(context.run) or "").strip(),
        str(context.run.run_directory or "").strip(),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _reviewer_agent_artifact_refs(context: WorkflowContext) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if isinstance(context.metadata.get("artifact_refs"), list):
        refs.extend(item for item in context.metadata.get("artifact_refs") if isinstance(item, dict))
    stage_outputs = context.metadata.get("stage_outputs")
    if isinstance(stage_outputs, dict):
        for payload in stage_outputs.values():
            if not isinstance(payload, dict):
                continue
            artifact_refs = payload.get("artifact_refs")
            if isinstance(artifact_refs, list):
                refs.extend(item for item in artifact_refs if isinstance(item, dict))
    refs.extend(
        _collect_workspace_artifacts_for_path(
            context,
            _resolve_reviewer_agent_workspace_path(context),
            limit=24,
        )
    )
    refs.extend(_collect_run_artifacts(context, limit=24))
    return _dedupe_artifact_refs(refs)[:24]


def _build_reviewer_agent_artifact_hint_block(
    context: WorkflowContext,
    *,
    workspace_path: str,
) -> str:
    refs = _reviewer_agent_artifact_refs(context)
    remote = bool(context.run.workspace_server_id)
    lines: list[str] = []
    for item in refs:
        path = str(item.get("path") or "").strip()
        relative_path = str(item.get("relative_path") or "").strip()
        if not relative_path and path:
            relative_path = (
                _artifact_relative_to_workspace(workspace_path, path, remote=remote)
                or relative_path
            )
        label = relative_path or path
        if not label:
            continue
        kind = str(item.get("kind") or "artifact").strip() or "artifact"
        lines.append(f"- [{kind}] {label}")
        if len(lines) >= 18:
            break

    pdf_paths = _collect_pdf_paths_for_run(context, limit=6)
    for pdf_path in pdf_paths:
        label = (
            _artifact_relative_to_workspace(workspace_path, pdf_path, remote=remote)
            or str(pdf_path).strip()
        )
        if not label:
            continue
        candidate = f"- [pdf] {label}"
        if candidate in lines:
            continue
        lines.append(candidate)
        if len(lines) >= 24:
            break

    if not lines:
        return ""
    return "\n".join(lines)


def _build_reviewer_agent_system_prompt(
    context: WorkflowContext,
    *,
    stage_id: str,
    role_label: str | None,
    role_template_id: str,
    workspace_path: str,
    output_mode: str,
) -> str:
    stage_label = _stage_label(context, stage_id)
    output_clause = (
        "最终只输出单个 JSON 对象，不要代码块、不要额外说明。"
        if output_mode == "json"
        else "最终只输出最终 Markdown 结果，不要额外解释你的过程。"
    )
    return "\n".join(
        [
            "You are ResearchOS reviewer agent for a project workflow stage.",
            "You must behave as an independent, read-only reviewer.",
            "Before concluding, inspect raw workspace files, generated artifacts, logs, PDFs, or other concrete evidence whenever they are available.",
            "Executor summaries are only navigation hints and never count as evidence on their own.",
            "Do not modify files, do not run commands, do not ask the user questions, and do not request permission escalation.",
            "If evidence is insufficient after inspection, say so explicitly.",
            output_clause,
            "",
            f"Project: {context.project.name}",
            f"Workflow: {context.run.workflow_type.value}",
            f"Stage: {stage_label} ({stage_id})",
            f"Role template: {role_template_id}",
            f"Role label: {str(role_label or '').strip() or 'reviewer'}",
            f"Workspace: {workspace_path}",
            f"Workspace server: {context.run.workspace_server_id or 'local'}",
        ]
    ).strip()


def _build_reviewer_agent_user_prompt(
    context: WorkflowContext,
    *,
    stage_id: str,
    prompt: str,
    workspace_path: str,
    output_mode: str,
) -> str:
    artifact_hints = _build_reviewer_agent_artifact_hint_block(
        context,
        workspace_path=workspace_path,
    )
    blocks = [
        f"当前审查阶段: {_stage_label(context, stage_id)} ({stage_id})",
        f"项目名称: {context.project.name}",
        f"用户目标: {context.run.prompt or '无'}",
        f"工作区路径: {workspace_path}",
        f"工作区服务器: {context.run.workspace_server_id or 'local'}",
    ]
    if artifact_hints:
        blocks.extend(
            [
                "可优先直接检查的工作区产物:",
                artifact_hints,
            ]
        )
    blocks.extend(
        [
            "审查要求:",
            "- 优先亲自读取 raw artifact/file/log/pdf，再得出结论。",
            "- 如果提示中同时有执行侧总结与原始材料，原始材料优先。",
            "- 明确点出你实际检查过的证据类型或文件路径。",
            "- 只读工作区，不要修改文件，不要运行命令。",
            "- 如果证据不足，直接写证据不足，不要替执行侧补全。",
            "",
            "任务说明:",
            prompt.strip(),
        ]
    )
    if output_mode == "json":
        blocks.extend(
            [
                "",
                "输出格式要求:",
                "- 最终只输出单个 JSON 对象。",
                "- 不要使用 Markdown 代码块。",
                "- 不要输出 JSON 之外的说明文字。",
            ]
        )
    return "\n".join(block for block in blocks if str(block or "").strip()).strip()


def _invoke_reviewer_workspace_agent(
    context: WorkflowContext,
    *,
    stage_id: str,
    stage: str,
    prompt: str,
    target: dict[str, Any],
    role: dict[str, Any],
    role_template_id: str,
    variant: str,
    output_mode: str,
) -> str | None:
    workspace_path = _resolve_reviewer_agent_workspace_path(context)
    if not workspace_path:
        return None

    skill_ids = [
        skill_id
        for skill_id in {
            workflow_assistant_skill_id(context.run.workflow_type),
            "research-review",
        }
        if str(skill_id or "").strip()
    ]
    mounted_paper_ids = [paper.id for paper in context.selected_papers if str(paper.id or "").strip()]
    mounted_primary_paper_id = mounted_paper_ids[0] if mounted_paper_ids else None
    session_id = (
        f"project_reviewer_{context.run.id.replace('-', '')[:12]}_"
        f"{re.sub(r'[^a-zA-Z0-9_]+', '_', str(stage_id or 'stage'))}_{uuid4().hex[:8]}"
    )
    user_tools = _reviewer_agent_tool_overrides()
    system_prompt = _build_reviewer_agent_system_prompt(
        context,
        stage_id=stage_id,
        role_label=str(role.get("label") or "").strip() or None,
        role_template_id=role_template_id,
        workspace_path=workspace_path,
        output_mode=output_mode,
    )
    user_prompt = _build_reviewer_agent_user_prompt(
        context,
        stage_id=stage_id,
        prompt=prompt,
        workspace_path=workspace_path,
        output_mode=output_mode,
    )
    model_identity = {
        "providerID": str(target.get("provider") or "").strip(),
        "modelID": str(target.get("display_model") or target.get("model_override") or "").strip(),
    }
    assistant_meta = {
        **resolve_default_model_identity(variant),
        "mode": "build",
        "agent": "researchos-reviewer",
        "cwd": workspace_path,
        "root": workspace_path,
        "variant": variant,
        "tokens": {"total": None, "input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        "cost": 0.0,
    }
    model_override = str(target.get("model_override") or "").strip() or None

    stream = None
    try:
        ensure_session_record(
            session_id,
            directory=workspace_path,
            workspace_path=workspace_path,
            workspace_server_id=context.run.workspace_server_id,
            title=f"{context.project.name} reviewer {stage_id}",
            mode="build",
            agent_backend_id=DEFAULT_AGENT_BACKEND_ID,
        )
        user_message = append_session_message(
            session_id=session_id,
            role="user",
            content=user_prompt,
            meta=build_user_message_meta(
                agent="researchos-reviewer",
                model=model_identity,
                tools=user_tools,
                system=system_prompt,
                variant=variant,
                active_skill_ids=skill_ids or None,
                mounted_paper_ids=mounted_paper_ids or None,
                mounted_primary_paper_id=mounted_primary_paper_id,
                reasoning_level=variant,
                fallback_agent="build",
            ),
        )
        persistence = StreamPersistenceConfig(
            session_id=session_id,
            parent_id=str((user_message.get("info") or {}).get("id") or "").strip() or None,
            assistant_meta=assistant_meta,
        )
        stream = stream_chat(
            [],
            session_id=session_id,
            agent_backend_id=DEFAULT_AGENT_BACKEND_ID,
            mode="build",
            workspace_path=workspace_path,
            workspace_server_id=context.run.workspace_server_id,
            reasoning_level=variant,
            model_override=model_override,
            active_skill_ids=skill_ids or None,
            mounted_paper_ids=mounted_paper_ids or None,
            mounted_primary_paper_id=mounted_primary_paper_id,
            persistence=persistence,
        )
        control = PromptStreamControl()
        for item in stream:
            control.observe(item, session_id=session_id, lifecycle_kind=stage, step_index=0, publish_bus=False)
        content = "".join(str(part.get("text") or "") for part in control.text_parts).strip()
        if control.paused:
            raise RuntimeError("reviewer agent unexpectedly paused")
        if control.error_message and not content:
            raise RuntimeError(control.error_message)
        return content or None
    except Exception as exc:
        logger.warning(
            "Reviewer workspace agent failed for run %s stage %s: %s",
            context.run.id,
            stage_id,
            exc,
        )
        return None
    finally:
        close_method = getattr(stream, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:
                logger.debug("Failed to close reviewer workspace agent stream", exc_info=True)
        try:
            delete_session(session_id)
        except Exception:
            logger.debug("Failed to clean reviewer session %s", session_id, exc_info=True)


def _resolve_model_override_for_role(llm: LLMClient, role: dict[str, str | None]) -> str | None:
    try:
        cfg = llm._config()
    except Exception:
        return None
    channel = str(role.get("model_channel") or "deep")
    if channel == "skim":
        return cfg.model_skim or cfg.model_fallback or cfg.model_deep
    return cfg.model_deep or cfg.model_fallback or cfg.model_skim


def _resolve_workspace_path(run: RunSnapshot) -> str | None:
    return run.remote_workdir if run.workspace_server_id else run.workdir


def _resolve_remote_session_name(context: WorkflowContext) -> str:
    existing = str(context.metadata.get("remote_session_name") or "").strip()
    if existing:
        return existing
    return build_remote_session_name(context.run.id)


def _resolve_remote_execution_workspace(context: WorkflowContext) -> str | None:
    existing = str(context.metadata.get("remote_execution_workspace") or "").strip()
    if existing:
        return existing
    if not context.run.workspace_server_id:
        return None
    return build_run_workspace_path(context.run.run_directory, remote=True)


def _describe_selected_gpu(selected_gpu: dict[str, Any] | None) -> str:
    if not selected_gpu:
        return "none"
    return (
        f"gpu{selected_gpu.get('index')}"
        f" ({selected_gpu.get('memory_used_mb')}/{selected_gpu.get('memory_total_mb')} MiB, "
        f"strategy={selected_gpu.get('strategy')})"
    )


def _clone_context_with_metadata(
    context: WorkflowContext,
    metadata_updates: dict[str, Any] | None,
) -> WorkflowContext:
    if not metadata_updates:
        return context
    merged = dict(context.metadata)
    merged.update(metadata_updates)
    return WorkflowContext(
        run=context.run,
        project=context.project,
        metadata=merged,
        selected_papers=context.selected_papers,
        selected_repos=context.selected_repos,
        analysis_contexts=context.analysis_contexts,
    )


def _build_remote_execution_runtime(
    context: WorkflowContext,
    item: ExecutionPlanItem,
    *,
    total_items: int,
) -> dict[str, str]:
    base_session_name = _resolve_remote_session_name(context)
    if total_items <= 1:
        return {
            "remote_session_name": base_session_name,
            "run_directory": str(context.run.run_directory or ""),
            "log_path": str(context.run.log_path or ""),
            "planned_execution_workspace": str(_resolve_remote_execution_workspace(context) or ""),
        }
    if not context.run.run_directory:
        raise RuntimeError("当前运行缺少 run_directory，无法规划批量实验目录。")
    suffix = re.sub(r"[^a-zA-Z0-9]+", "", item.item_id)[:18] or f"exp{item.source_index}"
    run_directory = posixpath.join(str(context.run.run_directory).rstrip("/"), "experiments", item.item_id)
    return {
        "remote_session_name": f"{base_session_name}-{suffix}",
        "run_directory": run_directory,
        "log_path": posixpath.join(run_directory, "run.log"),
        "planned_execution_workspace": str(build_run_workspace_path(run_directory, remote=True) or ""),
    }


def _merge_active_gpu_leases(
    active_leases: list[dict[str, Any]],
    lease: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        target_index = int(lease.get("gpu_index"))
    except (TypeError, ValueError):
        return list(active_leases)
    merged = [
        item
        for item in active_leases
        if int(item.get("gpu_index", -1)) != target_index
    ]
    merged.append(dict(lease))
    return merged


def _reconcile_remote_gpu_leases(
    *,
    workspace_server_id: str,
    server_entry: dict,
) -> dict[str, Any]:
    try:
        session_snapshot = remote_list_screen_sessions(server_entry, session_prefix="aris-run-")
        active_session_names = [
            str(item.get("name") or "").strip()
            for item in (session_snapshot.get("sessions") or [])
            if str(item.get("name") or "").strip()
        ]
        lease_state = reconcile_gpu_leases(
            workspace_server_id=workspace_server_id,
            active_session_names=active_session_names,
        )
        return {
            "screen_sessions": session_snapshot.get("sessions") or [],
            "active_session_names": active_session_names,
            "released_leases": lease_state.get("released") or [],
            "active_leases": lease_state.get("active") or [],
            "error": None,
        }
    except Exception as exc:
        return {
            "screen_sessions": [],
            "active_session_names": [],
            "released_leases": [],
            "active_leases": list_active_gpu_leases(workspace_server_id),
            "error": str(exc),
        }


def _resolve_execution_timeout(context: WorkflowContext) -> int:
    raw = context.metadata.get("execution_timeout_sec")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = 1800
    return max(30, min(parsed, 7200))


def _normalize_execution_plan_item_id(raw: Any, index: int, seen: dict[str, int]) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", str(raw or "").strip().lower()).strip("-")
    if not base:
        base = f"experiment-{index}"
    base = base[:32]
    count = seen.get(base, 0) + 1
    seen[base] = count
    if count == 1:
        return base
    suffix = f"-{count}"
    return f"{base[: max(1, 32 - len(suffix))]}{suffix}"


def _normalize_parallel_experiment_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        items: list[dict[str, Any]] = []
        for index, item in enumerate(raw, start=1):
            if isinstance(item, dict):
                items.append(dict(item))
                continue
            command = str(item or "").strip()
            if command:
                items.append({"name": f"实验 {index}", "command": command})
        return items
    if isinstance(raw, dict):
        for key in ("parallel_experiments", "experiments", "items", "runs"):
            nested = _normalize_parallel_experiment_items(raw.get(key))
            if nested:
                return nested
        variants = raw.get("variants")
        base_command = str(raw.get("base_command") or raw.get("execution_command") or raw.get("command") or "").strip()
        if isinstance(variants, list):
            normalized: list[dict[str, Any]] = []
            for index, item in enumerate(variants, start=1):
                payload = dict(item) if isinstance(item, dict) else {"name": f"实验 {index}", "command": str(item or "").strip()}
                has_explicit_command = any(
                    str(payload.get(key) or "").strip()
                    for key in ("command", "execution_command", "run_command")
                )
                if not has_explicit_command and base_command:
                    args = str(payload.get("args") or payload.get("suffix") or payload.get("command_suffix") or "").strip()
                    payload["command"] = f"{base_command} {args}".strip() if args else base_command
                normalized.append(payload)
            return normalized
    return []


def _resolve_execution_plan(context: WorkflowContext) -> list[ExecutionPlanItem]:
    raw_plan: list[dict[str, Any]] = []
    for key in ("parallel_experiments", "experiment_matrix"):
        raw_plan = _normalize_parallel_experiment_items(context.metadata.get(key))
        if raw_plan:
            break
    if not raw_plan:
        return [
            ExecutionPlanItem(
                item_id="main",
                name="主实验",
                command=_resolve_execution_command(context),
                metadata_overrides={},
                source_index=1,
            )
        ]

    seen_ids: dict[str, int] = {}
    resolved: list[ExecutionPlanItem] = []
    for index, item in enumerate(raw_plan, start=1):
        if not isinstance(item, dict):
            continue
        command = ""
        for key in ("command", "execution_command", "run_command"):
            command = str(item.get(key) or "").strip()
            if command:
                break
        if not command:
            continue
        item_id = _normalize_execution_plan_item_id(
            item.get("id") or item.get("name") or item.get("label") or item.get("title"),
            index,
            seen_ids,
        )
        name = str(item.get("name") or item.get("label") or item.get("title") or f"实验 {index}").strip() or f"实验 {index}"
        metadata_overrides = {
            key: item[key]
            for key in (
                "execution_timeout_sec",
                "gpu_mode",
                "gpu_strategy",
                "preferred_gpu_ids",
                "gpu_ids",
                "gpu_id",
                "gpu_memory_threshold_mb",
                "allow_busy_gpu",
            )
            if key in item and item.get(key) is not None
        }
        resolved.append(
            ExecutionPlanItem(
                item_id=item_id,
                name=name,
                command=command,
                metadata_overrides=metadata_overrides,
                source_index=index,
            )
        )
    if not resolved:
        raise RuntimeError("parallel_experiments / experiment_matrix 中没有可执行的实验命令。")
    return resolved


def _serialize_execution_plan(plan: list[ExecutionPlanItem]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.item_id,
            "name": item.name,
            "command": item.command,
            "metadata_overrides": dict(item.metadata_overrides),
            "source_index": item.source_index,
        }
        for item in plan
    ]


def _resolve_gpu_mode(context: WorkflowContext) -> str:
    raw = str(context.metadata.get("gpu_mode") or "auto").strip().lower()
    if raw in {"off", "disabled", "none"}:
        return "off"
    if raw in {"require", "required", "strict"}:
        return "require"
    return "auto"


def _resolve_gpu_strategy(context: WorkflowContext) -> str:
    raw = str(context.metadata.get("gpu_strategy") or "least_used_free").strip().lower()
    if raw in {"first_fit", "first"}:
        return "first_fit"
    if raw in {"least_used", "least_used_free"}:
        return raw
    return "least_used_free"


def _resolve_gpu_memory_threshold_mb(context: WorkflowContext) -> int:
    raw = context.metadata.get("gpu_memory_threshold_mb")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = 500
    return max(0, min(parsed, 80_000))


def _resolve_allow_busy_gpu(context: WorkflowContext) -> bool:
    value = context.metadata.get("allow_busy_gpu")
    return bool(value is True or str(value).strip().lower() in {"1", "true", "yes", "on"})


def _resolve_preferred_gpu_ids(context: WorkflowContext) -> list[int]:
    raw = context.metadata.get("preferred_gpu_ids", _MISSING)
    if raw is _MISSING:
        raw = context.metadata.get("gpu_ids", _MISSING)
    if raw is _MISSING:
        raw = context.metadata.get("gpu_id", _MISSING)
    values: list[object]
    if raw is _MISSING:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [part.strip() for part in str(raw).split(",")]
    resolved: list[int] = []
    for item in values:
        try:
            resolved.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(resolved))


def _select_remote_gpu(
    context: WorkflowContext,
    gpu_probe: dict[str, Any],
    *,
    active_leases: list[dict[str, Any]] | None = None,
    exclude_current_run: bool = True,
) -> dict[str, Any] | None:
    gpu_mode = _resolve_gpu_mode(context)
    if gpu_mode == "off":
        return None
    if not bool(gpu_probe.get("available")):
        if gpu_mode == "require":
            raise RuntimeError("当前远程服务器无法提供 GPU 清单，无法按要求绑定 GPU。")
        return None

    inventory = [item for item in (gpu_probe.get("gpus") or []) if isinstance(item, dict)]
    if not inventory:
        if gpu_mode == "require":
            raise RuntimeError("当前远程服务器未检测到可用 GPU。")
        return None

    preferred_gpu_ids = _resolve_preferred_gpu_ids(context)
    strategy = _resolve_gpu_strategy(context)
    memory_threshold_mb = _resolve_gpu_memory_threshold_mb(context)
    allow_busy_gpu = _resolve_allow_busy_gpu(context)
    lease_rows = [item for item in (active_leases or []) if isinstance(item, dict)]
    leased_gpu_indices = {
        int(item.get("gpu_index"))
        for item in lease_rows
        if (
            not exclude_current_run
            or str(item.get("run_id") or "").strip() != str(context.run.id).strip()
        )
    }

    candidates = list(inventory)
    if preferred_gpu_ids:
        candidates = [item for item in inventory if int(item.get("index", -1)) in preferred_gpu_ids]
        if not candidates:
            raise RuntimeError(f"未找到指定的 GPU: {preferred_gpu_ids}")
    candidates = [item for item in candidates if int(item.get("index", -1)) not in leased_gpu_indices]
    if not candidates:
        leased_text = ", ".join(str(item) for item in sorted(leased_gpu_indices))
        raise RuntimeError(f"当前没有可分配的 GPU，可用卡已被其他运行锁定: {leased_text or 'N/A'}")

    free_candidates = [
        item
        for item in candidates
        if int(item.get("memory_used_mb") or 0) < memory_threshold_mb
    ]
    selected_pool = free_candidates
    selection_reason = "free_gpu"
    if not selected_pool:
        if not allow_busy_gpu:
            raise RuntimeError(
                f"未找到空闲 GPU（阈值 {memory_threshold_mb} MiB）。"
                "如需强制占用最空闲 GPU，请设置 allow_busy_gpu=true。"
            )
        selected_pool = candidates
        selection_reason = "busy_gpu_fallback"

    if strategy == "first_fit":
        if preferred_gpu_ids:
            preferred_order = {gpu_id: index for index, gpu_id in enumerate(preferred_gpu_ids)}
            selected = min(
                selected_pool,
                key=lambda item: (
                    preferred_order.get(int(item.get("index", -1)), 999),
                    int(item.get("index", 999)),
                ),
            )
        else:
            selected = min(selected_pool, key=lambda item: int(item.get("index", 999)))
    else:
        selected = min(
            selected_pool,
            key=lambda item: (
                int(item.get("memory_used_mb") or 0),
                int(item.get("utilization_gpu_pct") or 0),
                int(item.get("index") or 999),
            ),
        )

    return {
        **selected,
        "strategy": strategy,
        "memory_threshold_mb": memory_threshold_mb,
        "allow_busy_gpu": allow_busy_gpu,
        "selection_reason": selection_reason,
        "preferred_gpu_ids": preferred_gpu_ids,
        "leased_gpu_indices": sorted(leased_gpu_indices),
    }


def _resolve_execution_command(context: WorkflowContext, *, allow_default: bool = True) -> str:
    metadata = context.metadata
    for key in ("execution_command", "command", "run_command"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    prompt = context.run.prompt.strip()
    if prompt.lower().startswith("command:"):
        return prompt.split(":", 1)[1].strip()
    first_line = prompt.splitlines()[0].strip() if prompt else ""
    if first_line.startswith("!"):
        return first_line[1:].strip()
    if not allow_default:
        return ""
    raise RuntimeError("缺少实验命令。请在运行时填写 execution_command。")


def _extract_inline_code_value(line: str) -> str:
    value = str(line or "").strip()
    if not value:
        return ""
    match = re.search(r"`([^`]+)`", value)
    if match:
        return match.group(1).strip()
    if ":" in value:
        value = value.split(":", 1)[1].strip()
    return value.strip(" -*")


def _extract_named_markdown_section(markdown_text: str, headings: list[str]) -> str:
    for heading in headings:
        section = _extract_markdown_section(markdown_text, heading)
        if section:
            return section
    return ""


def _parse_claude_runtime_environment(context: WorkflowContext) -> dict[str, Any]:
    metadata = context.metadata
    override_activate = (
        str(metadata.get("execution_activate_command") or "").strip()
        or str(metadata.get("activate_command") or "").strip()
    )
    override_code_dir = (
        str(metadata.get("execution_code_dir") or "").strip()
        or str(metadata.get("code_dir") or "").strip()
    )
    claude_text = _read_workspace_text_file(context, "CLAUDE.md", max_chars=16000)
    section_kind = "remote" if context.run.workspace_server_id else "local"
    section_text = _extract_named_markdown_section(
        claude_text,
        ["远程服务器", "Remote Server"] if section_kind == "remote" else ["本地环境", "Local Environment"],
    )
    search_text = section_text or claude_text
    parsed: dict[str, Any] = {
        "section": section_kind,
        "source": "metadata" if (override_activate or override_code_dir) else ("claude_md" if search_text.strip() else "none"),
        "activate_command": override_activate or "",
        "conda_env": "",
        "code_dir": override_code_dir or "",
        "ssh_command": "",
        "raw_section": search_text.strip()[:4000],
    }
    for raw_line in search_text.splitlines():
        line = str(raw_line or "").strip()
        if not line.startswith("-"):
            continue
        normalized = line.lstrip("-* ").strip()
        lowered = normalized.lower()
        inline_value = _extract_inline_code_value(normalized)
        if (lowered.startswith("ssh") or lowered.startswith("ssh：") or lowered.startswith("ssh:")) and inline_value:
            parsed["ssh_command"] = parsed["ssh_command"] or inline_value
            continue
        if any(
            token in lowered
            for token in ("激活", "activate", "conda:", "conda：")
        ):
            if "conda activate" in inline_value.lower() or "eval " in inline_value.lower():
                parsed["activate_command"] = parsed["activate_command"] or inline_value
        if any(
            token in lowered
            for token in ("conda env", "conda 环境", "condaenv", "conda environment")
        ):
            if inline_value and "&&" not in inline_value and "conda activate" not in inline_value.lower():
                parsed["conda_env"] = parsed["conda_env"] or inline_value
        if any(
            token in lowered
            for token in ("代码目录", "code dir", "code directory")
        ):
            if inline_value:
                parsed["code_dir"] = parsed["code_dir"] or inline_value
    if not parsed["activate_command"] and parsed["conda_env"]:
        parsed["activate_command"] = f"conda activate {parsed['conda_env']}"
    return parsed


def _resolve_execution_workspace_path(
    context: WorkflowContext,
    *,
    workspace_root: str | None = None,
) -> str | None:
    original_workspace_path = str(_resolve_workspace_path(context.run) or "").strip()
    current_workspace_path = str(workspace_root or original_workspace_path or "").strip()
    runtime = _parse_claude_runtime_environment(context)
    code_dir = str(runtime.get("code_dir") or "").strip()
    if not code_dir:
        return current_workspace_path or None

    if context.run.workspace_server_id:
        original_root = original_workspace_path.replace("\\", "/").rstrip("/")
        current_root = current_workspace_path.replace("\\", "/").rstrip("/")
        normalized_code_dir = code_dir.replace("\\", "/").strip()
        if normalized_code_dir.startswith("~"):
            return normalized_code_dir
        if normalized_code_dir.startswith("/"):
            normalized_code_dir = posixpath.normpath(normalized_code_dir)
            if original_root and current_root:
                if normalized_code_dir == original_root:
                    return current_root or normalized_code_dir
                if normalized_code_dir.startswith(f"{original_root}/"):
                    relative_path = normalized_code_dir[len(original_root) + 1 :]
                    return posixpath.normpath(posixpath.join(current_root or original_root, relative_path))
            return normalized_code_dir
        if current_root:
            return posixpath.normpath(posixpath.join(current_root, normalized_code_dir))
        return posixpath.normpath(normalized_code_dir)

    original_root = Path(original_workspace_path) if original_workspace_path else None
    current_root = Path(current_workspace_path) if current_workspace_path else None
    is_windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", code_dir))
    code_path = Path(code_dir)
    if code_path.is_absolute() or is_windows_absolute:
        absolute_code_path = code_path
        if current_root is not None and original_root is not None:
            try:
                relative_path = absolute_code_path.relative_to(original_root)
            except ValueError:
                return str(absolute_code_path)
            return str(current_root / relative_path)
        return str(absolute_code_path)
    if current_root is not None:
        return str(current_root / code_path)
    return str(code_path)


def _wrap_command_with_runtime_environment(
    context: WorkflowContext,
    command: str,
) -> tuple[str, dict[str, Any]]:
    runtime = _parse_claude_runtime_environment(context)
    activate_command = str(runtime.get("activate_command") or "").strip()
    normalized_command = str(command or "").strip()
    if not activate_command or not normalized_command:
        runtime["wrapped"] = False
        runtime["effective_command"] = normalized_command
        return normalized_command, runtime
    runtime["wrapped"] = True
    runtime["effective_command"] = f"{activate_command} && {normalized_command}"
    return str(runtime["effective_command"]), runtime


def _powershell_quote(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _command_in_subdir(context: WorkflowContext, subdir: str, command: str) -> str:
    clean_subdir = str(subdir or "").strip().replace("\\", "/").strip("/")
    if not clean_subdir:
        return command
    if context.run.workspace_server_id:
        return f"cd {shlex.quote(clean_subdir)} && {command}"
    quoted = _powershell_quote(clean_subdir.replace("/", "\\"))
    return f"Push-Location {quoted}; try {{ {command} }} finally {{ Pop-Location }}"


def _command_available(context: WorkflowContext, command: str) -> bool:
    try:
        result = _run_workspace_command_for_context(context, command, timeout_sec=20)
    except Exception:
        return False
    return bool(result.get("success"))


def _resolve_paper_compile_command(context: WorkflowContext) -> str:
    for key in ("paper_compile_command", "compile_command"):
        value = str(context.metadata.get(key) or "").strip()
        if value:
            return value
    directive = (
        str(_extract_prompt_directive(context.run.prompt, "compile command") or "").strip()
        or str(_extract_prompt_directive(context.run.prompt, "compile") or "").strip()
    )
    if directive:
        return directive
    if _command_available(context, "latexmk --version"):
        return _command_in_subdir(
            context,
            "paper",
            "latexmk -pdf -interaction=nonstopmode -file-line-error -outdir=build main.tex",
        )
    if not _command_available(context, "pdflatex --version"):
        return ""
    inner = "pdflatex -interaction=nonstopmode -file-line-error main.tex"
    if _command_available(context, "bibtex --version"):
        inner = f"{inner} && bibtex main && {inner} && {inner}"
    else:
        inner = f"{inner} && {inner}"
    return _command_in_subdir(context, "paper", inner)


def _missing_compile_markdown() -> str:
    return (
        "# PAPER_COMPILE\n\n"
        "- Status: pending manual compile\n"
        "- Missing toolchain: latexmk / pdflatex / bibtex\n"
    )


def _stable_workflow_thread_id(context: WorkflowContext, metadata_key: str, prefix: str) -> str:
    existing = str(context.metadata.get(metadata_key) or "").strip()
    if existing:
        return existing
    return f"{prefix}-{context.run.id.replace('-', '')[:16]}"


def _materialize_manuscript_workspace(
    context: WorkflowContext,
    draft_markdown: str,
    *,
    report_relative_path: str | None = None,
) -> list[dict[str, Any]]:
    venue, template_name = resolve_paper_venue(context.metadata)
    paper_titles = [paper.title for paper in context.selected_papers]
    bundle = build_paper_write_bundle(
        project_name=context.project.name,
        project_description=context.project.description or "",
        prompt=context.run.prompt,
        stage_markdown=draft_markdown,
        venue=venue,
        template_name=template_name,
        paper_titles=paper_titles,
    )
    if report_relative_path:
        bundle[report_relative_path] = draft_markdown.rstrip() + "\n"
    artifact_refs: list[dict[str, Any]] = []
    for relative_path, content in bundle.items():
        artifact = _write_run_artifact(
            context,
            relative_path,
            content,
            kind="report" if relative_path.lower().endswith(".md") else "artifact",
        )
        if artifact:
            artifact_refs.append(artifact)
    return artifact_refs


def _execute_compile_pass(
    context: WorkflowContext,
    *,
    compile_command: str,
    report_relative_path: str,
    log_relative_path: str | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    execution = _run_workspace_command_for_context(
        context,
        compile_command,
        timeout_sec=_resolve_execution_timeout(context),
    )
    artifact_refs: list[dict[str, Any]] = []
    log_content = _format_command_log(execution)
    if log_relative_path:
        log_artifact = _write_run_artifact(context, log_relative_path, log_content, kind="log")
    else:
        log_artifact = _write_run_log(context, log_content)
    if log_artifact:
        artifact_refs.append(log_artifact)
    compile_bundle = build_paper_compile_bundle(
        project_name=context.project.name,
        compile_command=compile_command,
        exit_code=int(execution.get("exit_code")) if str(execution.get("exit_code") or "").strip() else None,
        pdf_paths=_collect_pdf_paths_for_run(context),
        stdout_text=str(execution.get("stdout") or ""),
        stderr_text=str(execution.get("stderr") or ""),
    )
    compile_markdown = str(compile_bundle.get("reports/PAPER_COMPILE.md") or "").strip()
    for relative_path, content in compile_bundle.items():
        target_relative_path = report_relative_path if relative_path == "reports/PAPER_COMPILE.md" else relative_path
        artifact = _write_run_artifact(
            context,
            target_relative_path,
            content,
            kind="report" if target_relative_path.lower().endswith(".md") else "artifact",
        )
        if artifact:
            artifact_refs.append(artifact)
    return compile_markdown, artifact_refs, execution


def _select_primary_pdf_path(pdf_paths: list[str]) -> str | None:
    if not pdf_paths:
        return None
    normalized = [str(path or "") for path in pdf_paths if str(path or "").strip()]
    if not normalized:
        return None
    preferred = [
        path
        for path in normalized
        if "main_round" not in Path(path).name.lower()
        and (
            "/paper/build/" in path.replace("\\", "/").lower()
            or path.replace("\\", "/").lower().endswith("/paper/build/main.pdf")
        )
    ]
    if preferred:
        return preferred[0]
    non_snapshot = [path for path in normalized if "main_round" not in Path(path).name.lower()]
    return non_snapshot[0] if non_snapshot else normalized[0]


def _copy_workspace_file(
    context: WorkflowContext,
    source_path: str,
    target_relative_path: str,
) -> dict[str, Any] | None:
    workspace_path = _resolve_workspace_path(context.run)
    base_dir = context.run.run_directory or workspace_path
    if not workspace_path or not base_dir:
        return None
    if context.run.workspace_server_id:
        target_path = f"{str(base_dir).rstrip('/')}/{target_relative_path.lstrip('/')}"
        server_entry = get_workspace_server_entry(context.run.workspace_server_id)
        command = (
            f"mkdir -p {shlex.quote(str(posixpath.dirname(target_path) or '.'))} && "
            f"cp {shlex.quote(source_path)} {shlex.quote(target_path)}"
        )
        try:
            result = remote_terminal_result(
                server_entry,
                path=workspace_path,
                command=command,
                timeout_sec=40,
            )
        except Exception as exc:
            logger.warning("remote pdf snapshot failed: %s", exc)
            return None
        if not result.get("success"):
            return None
        return {
            "kind": "artifact",
            "path": target_path,
            "relative_path": target_relative_path,
        }

    target_path = Path(base_dir) / target_relative_path
    source = Path(source_path)
    if not source.exists() or not source.is_file():
        return None
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target_path)
    except OSError as exc:
        logger.warning("local pdf snapshot failed: %s", exc)
        return None
    return {
        "kind": "artifact",
        "path": str(target_path),
        "relative_path": target_relative_path,
        "size_bytes": target_path.stat().st_size,
    }


def _snapshot_primary_pdf_output(
    context: WorkflowContext,
    target_relative_path: str,
) -> dict[str, Any] | None:
    source_path = _select_primary_pdf_path(_collect_pdf_paths_for_run(context))
    if not source_path:
        return None
    return _copy_workspace_file(context, source_path, target_relative_path)


def _normalize_audit_status(value: Any, *, default: str = "WARN") -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"PASS", "WARN", "FAIL"} else default


def _normalize_claim_impact(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"supported", "needs_qualifier", "unsupported"}:
        return normalized
    return "needs_qualifier"


def _normalize_string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:limit]
    text = str(value or "").strip()
    return [text] if text else []


def _experiment_audit_fallback_payload(bundle: dict[str, Any], llm_message: str) -> dict[str, Any]:
    total_files = sum(len(items) for items in (bundle.get("inventory") or {}).values())
    warning = "模型未返回有效结构化实验审计结果，当前输出为保守 fallback。"
    if llm_message and _looks_like_llm_error(llm_message):
        warning = f"{warning} 模型返回：{llm_message}"
    checks = {
        "gt_provenance": {"status": "WARN", "evidence": [], "details": warning},
        "score_normalization": {"status": "WARN", "evidence": [], "details": warning},
        "result_existence": {"status": "WARN", "evidence": [], "details": warning},
        "dead_code": {"status": "WARN", "evidence": [], "details": warning},
        "scope": {"status": "WARN", "evidence": [], "details": warning},
        "eval_type": {"status": "WARN", "evidence": [], "details": warning},
    }
    return {
        "overall_verdict": "WARN",
        "integrity_status": "warn",
        "evaluation_type": "unknown",
        "summary": f"未拿到有效模型审计结果，已基于 {total_files} 个候选文件生成保守告警。",
        "checks": checks,
        "action_items": [
            "人工核对评测脚本的 ground truth 来源和结果文件引用。",
            "确认论文或 narrative 中的关键数字都能在实际结果文件中定位到。",
            "在进入 auto-review-loop 或 paper-writing 前重新运行 experiment-audit。",
        ],
        "claims": [],
    }


def _resolve_experiment_audit_payload(
    bundle: dict[str, Any],
    llm_result: LLMResult,
) -> dict[str, Any]:
    parsed = llm_result.parsed_json or {}
    if not isinstance(parsed, dict) or not parsed:
        parsed = _parse_json_payload_text(llm_result.content or "")
    if not isinstance(parsed, dict) or not parsed:
        return _experiment_audit_fallback_payload(bundle, llm_result.content or "")

    raw_checks = parsed.get("checks") if isinstance(parsed.get("checks"), dict) else {}
    evaluation_type = str(parsed.get("evaluation_type") or parsed.get("eval_type") or "unknown").strip() or "unknown"
    checks = {
        "gt_provenance": {
            "status": _normalize_audit_status((raw_checks.get("gt_provenance") or {}).get("status")),
            "evidence": _normalize_string_list((raw_checks.get("gt_provenance") or {}).get("evidence")),
            "details": str((raw_checks.get("gt_provenance") or {}).get("details") or "").strip(),
        },
        "score_normalization": {
            "status": _normalize_audit_status((raw_checks.get("score_normalization") or {}).get("status")),
            "evidence": _normalize_string_list((raw_checks.get("score_normalization") or {}).get("evidence")),
            "details": str((raw_checks.get("score_normalization") or {}).get("details") or "").strip(),
        },
        "result_existence": {
            "status": _normalize_audit_status((raw_checks.get("result_existence") or {}).get("status")),
            "evidence": _normalize_string_list((raw_checks.get("result_existence") or {}).get("evidence")),
            "details": str((raw_checks.get("result_existence") or {}).get("details") or "").strip(),
        },
        "dead_code": {
            "status": _normalize_audit_status((raw_checks.get("dead_code") or {}).get("status")),
            "evidence": _normalize_string_list((raw_checks.get("dead_code") or {}).get("evidence")),
            "details": str((raw_checks.get("dead_code") or {}).get("details") or "").strip(),
        },
        "scope": {
            "status": _normalize_audit_status((raw_checks.get("scope") or {}).get("status")),
            "evidence": _normalize_string_list((raw_checks.get("scope") or {}).get("evidence")),
            "details": str((raw_checks.get("scope") or {}).get("details") or "").strip(),
        },
        "eval_type": {
            "status": _normalize_audit_status((raw_checks.get("eval_type") or {}).get("status"), default="PASS"),
            "evidence": _normalize_string_list((raw_checks.get("eval_type") or {}).get("evidence")),
            "details": str((raw_checks.get("eval_type") or {}).get("details") or "").strip(),
        },
    }
    statuses = [item["status"] for item in checks.values()]
    derived_verdict = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    overall_verdict = _normalize_audit_status(parsed.get("overall_verdict"), default=derived_verdict)
    integrity_status = str(parsed.get("integrity_status") or "").strip().lower() or overall_verdict.lower()
    if integrity_status not in {"pass", "warn", "fail"}:
        integrity_status = overall_verdict.lower()

    claims: list[dict[str, Any]] = []
    raw_claims = parsed.get("claims")
    if isinstance(raw_claims, list):
        for index, item in enumerate(raw_claims[:8], start=1):
            if not isinstance(item, dict):
                continue
            claim_id = str(item.get("id") or f"C{index}").strip() or f"C{index}"
            claims.append(
                {
                    "id": claim_id,
                    "impact": _normalize_claim_impact(item.get("impact")),
                    "details": str(item.get("details") or item.get("reason") or "").strip(),
                }
            )

    payload = {
        "overall_verdict": overall_verdict,
        "integrity_status": integrity_status,
        "evaluation_type": evaluation_type,
        "summary": str(parsed.get("summary") or "").strip(),
        "checks": checks,
        "action_items": _normalize_string_list(parsed.get("action_items"), limit=10),
        "claims": claims,
    }
    if not payload["summary"]:
        payload["summary"] = _experiment_audit_summary_line(payload)
    return payload


def _experiment_audit_summary_line(payload: dict[str, Any]) -> str:
    return (
        f"实验审计完成：overall={str(payload.get('overall_verdict') or 'WARN')}, "
        f"integrity={str(payload.get('integrity_status') or 'warn')}, "
        f"eval_type={str(payload.get('evaluation_type') or 'unknown')}"
    )


def _build_experiment_audit_prompt(context: WorkflowContext, bundle: dict[str, Any]) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["experiment-audit"],
        phase_label="Experiment Audit - Cross-Model Integrity Verification",
        output_contract=(
            "只输出一个 JSON 对象，不要输出 Markdown 代码块。格式为："
            '{"overall_verdict":"PASS|WARN|FAIL","integrity_status":"pass|warn|fail",'
            '"evaluation_type":"real_gt|synthetic_proxy|self_supervised_proxy|simulation_only|human_eval|mixed|unknown",'
            '"summary":"一句话总结","checks":{"gt_provenance":{"status":"PASS|WARN|FAIL","evidence":["file:line"],"details":"..."},'
            '"score_normalization":{"status":"PASS|WARN|FAIL","evidence":["file:line"],"details":"..."},'
            '"result_existence":{"status":"PASS|WARN|FAIL","evidence":["file:line"],"details":"..."},'
            '"dead_code":{"status":"PASS|WARN|FAIL","evidence":["file:line"],"details":"..."},'
            '"scope":{"status":"PASS|WARN|FAIL","evidence":["file:line"],"details":"..."},'
            '"eval_type":{"status":"PASS|WARN|FAIL","evidence":["file:line"],"details":"..."}},"action_items":["..."],'
            '"claims":[{"id":"C1","impact":"supported|needs_qualifier|unsupported","details":"..."}]}'
        ),
        context_blocks=[
            (
                "Audit Checklist",
                (
                    "A. Ground Truth Provenance: 检查 ground truth / reference / target 是否来自真实数据集，而不是模型输出派生。\n"
                    "B. Score Normalization: 检查是否用模型自身输出统计量做归一化分母，或只报告过于接近 1.0 的归一化分数。\n"
                    "C. Result File Existence: 检查 narrative / paper claim 提到的结果文件、指标键和数字是否真实存在且匹配。\n"
                    "D. Dead Code Detection: 检查 metric/eval 函数是否真的被调用，还是只定义未用。\n"
                    "E. Scope Assessment: 检查实验覆盖的场景、配置、seed 数量是否支撑文案中的 strong claim。\n"
                    "F. Evaluation Type Classification: 归类为 real_gt / synthetic_proxy / self_supervised_proxy / simulation_only / human_eval / mixed / unknown。"
                ),
            ),
            ("Audit Evidence Bundle", str(bundle.get("prompt_bundle") or "")),
        ],
    )


def _render_experiment_audit_report(
    context: WorkflowContext,
    *,
    audit_payload: dict[str, Any],
    workspace_path: str,
) -> str:
    checks = dict(audit_payload.get("checks") or {})
    section_labels = [
        ("gt_provenance", "A. Ground Truth Provenance"),
        ("score_normalization", "B. Score Normalization"),
        ("result_existence", "C. Result File Existence"),
        ("dead_code", "D. Dead Code Detection"),
        ("scope", "E. Scope Assessment"),
        ("eval_type", "F. Evaluation Type"),
    ]
    lines = [
        "# Experiment Audit Report",
        "",
        f"**Date**: {datetime.now(UTC).date().isoformat()}",
        f"**Project**: {context.project.name}",
        f"**Workspace**: `{workspace_path}`",
        f"**Overall Verdict**: {audit_payload.get('overall_verdict') or 'WARN'}",
        f"**Integrity Status**: {audit_payload.get('integrity_status') or 'warn'}",
        f"**Evaluation Type**: {audit_payload.get('evaluation_type') or 'unknown'}",
    ]
    if str(context.run.prompt or "").strip():
        lines.append(f"**Audit Scope**: {str(context.run.prompt).strip()}")
    summary = str(audit_payload.get("summary") or "").strip()
    if summary:
        lines.extend(["", "## Summary", summary])

    lines.append("")
    lines.append("## Checks")
    for key, label in section_labels:
        item = checks.get(key) if isinstance(checks.get(key), dict) else {}
        evidence = _normalize_string_list(item.get("evidence"), limit=6)
        details = str(item.get("details") or "").strip() or "待补充。"
        lines.extend(
            [
                "",
                f"### {label}: {_normalize_audit_status(item.get('status'))}",
                details,
            ]
        )
        if evidence:
            lines.append("")
            lines.append("Evidence:")
            for ref in evidence:
                lines.append(f"- {ref}")

    action_items = _normalize_string_list(audit_payload.get("action_items"), limit=10)
    lines.extend(["", "## Action Items"])
    if action_items:
        for item in action_items:
            lines.append(f"- {item}")
    else:
        lines.append("- 当前没有额外 action item。")

    claims = audit_payload.get("claims") if isinstance(audit_payload.get("claims"), list) else []
    lines.extend(["", "## Claim Impact"])
    if claims:
        for item in claims:
            if not isinstance(item, dict):
                continue
            claim_id = str(item.get("id") or "C?").strip() or "C?"
            impact = str(item.get("impact") or "needs_qualifier").strip()
            details = str(item.get("details") or "").strip()
            line = f"- {claim_id}: {impact}"
            if details:
                line += f" | {details}"
            lines.append(line)
    else:
        lines.append("- 当前没有解析出显式 claim 影响，建议人工核对 narrative 和 paper 草稿。")
    return sanitize_project_markdown("\n".join(lines).strip())


def _inspect_workspace_payload(
    context: WorkflowContext,
    *,
    workspace_path_override: str | None = None,
) -> dict[str, Any]:
    workspace_path = str(workspace_path_override or _resolve_workspace_path(context.run) or "").strip()
    if not workspace_path:
        return {
            "workspace_path": None,
            "exists": False,
            "message": "未配置工作区路径",
        }
    if context.run.workspace_server_id:
        server_entry = get_workspace_server_entry(context.run.workspace_server_id)
        overview = build_remote_overview(server_entry, workspace_path, depth=2, max_entries=80)
        runtime = {
            "python": _remote_runtime_probe(server_entry, workspace_path, "python --version"),
            "git": _remote_runtime_probe(server_entry, workspace_path, "git --version"),
            "uv": _remote_runtime_probe(server_entry, workspace_path, "uv --version"),
        }
        status = "ready" if overview.get("exists") else "error"
        return {
            **overview,
            "runtime": runtime,
            "status": status,
        }
    try:
        overview = inspect_workspace(workspace_path, max_depth=2, max_entries=80)
    except Exception as exc:
        return {
            "workspace_path": workspace_path,
            "exists": False,
            "message": str(exc),
            "status": "error",
        }
    runtime = {
        "python": _local_runtime_probe(workspace_path, "python --version"),
        "git": _local_runtime_probe(workspace_path, "git --version"),
        "uv": _local_runtime_probe(workspace_path, "uv --version"),
    }
    return {
        **overview,
        "exists": True,
        "runtime": runtime,
        "status": "ready",
    }


def _local_runtime_probe(workspace_path: str, command: str) -> dict[str, Any]:
    try:
        result = run_workspace_command(workspace_path, command, timeout_sec=20)
    except Exception as exc:
        return {"available": False, "detail": str(exc)}
    detail = str(result.get("stdout") or result.get("stderr") or "").strip()
    return {"available": bool(result.get("success")), "detail": detail[:240]}


def _remote_runtime_probe(server_entry: dict, workspace_path: str, command: str) -> dict[str, Any]:
    try:
        result = remote_terminal_result(server_entry, path=workspace_path, command=command, timeout_sec=20)
    except Exception as exc:
        return {"available": False, "detail": str(exc)}
    detail = str(result.get("stdout") or result.get("stderr") or "").strip()
    return {"available": bool(result.get("success")), "detail": detail[:240]}


def _run_workspace_command_for_context(
    context: WorkflowContext,
    command: str,
    *,
    timeout_sec: int,
    workspace_path_override: str | None = None,
) -> dict[str, Any]:
    workspace_path = str(workspace_path_override or _resolve_workspace_path(context.run) or "").strip()
    if not workspace_path:
        raise RuntimeError("工作区路径为空")
    if context.run.workspace_server_id:
        server_entry = get_workspace_server_entry(context.run.workspace_server_id)
        return remote_terminal_result(
            server_entry,
            path=workspace_path,
            command=command,
            timeout_sec=timeout_sec,
        )
    return run_workspace_command(workspace_path, command, timeout_sec=timeout_sec)


def _format_command_log(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Command: {result.get('command') or ''}",
            f"Exit Code: {result.get('exit_code')}",
            "",
            "[stdout]",
            str(result.get("stdout") or "").strip(),
            "",
            "[stderr]",
            str(result.get("stderr") or "").strip(),
            "",
        ]
    ).strip()


def _command_result_preview(result: dict[str, Any], limit: int = 700) -> str:
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    body = stdout or stderr or "命令未返回输出"
    body = re.sub(r"\s+", " ", body).strip()
    body = body[:limit]
    return f"命令 `{result.get('command')}` 退出码 {result.get('exit_code')}。{body}"


def _write_run_log(context: WorkflowContext, content: str) -> dict[str, Any] | None:
    if not context.run.log_path:
        return None
    return _write_absolute_run_file(context, context.run.log_path, content, kind="log")


def _write_run_artifact(
    context: WorkflowContext,
    relative_path: str,
    content: str,
    *,
    kind: str,
) -> dict[str, Any] | None:
    workspace_path = _resolve_workspace_path(context.run)
    base_dir = context.run.run_directory or workspace_path
    if not base_dir or not workspace_path:
        return None
    if context.run.workspace_server_id:
        absolute_path = f"{str(base_dir).rstrip('/')}/{relative_path.lstrip('/')}"
    else:
        absolute_path = str(Path(base_dir) / relative_path)
    return _write_absolute_run_file(context, absolute_path, content, kind=kind)


def _write_run_json_artifact(
    context: WorkflowContext,
    relative_path: str,
    payload: dict[str, Any],
    *,
    kind: str,
) -> dict[str, Any] | None:
    return _write_run_artifact(
        context,
        relative_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        kind=kind,
    )


def _write_absolute_run_file(
    context: WorkflowContext,
    absolute_path: str,
    content: str,
    *,
    kind: str,
) -> dict[str, Any] | None:
    workspace_path = _resolve_workspace_path(context.run)
    if not workspace_path:
        return None
    content = sanitize_project_artifact_content(absolute_path, content, kind=kind)
    try:
        if context.run.workspace_server_id:
            workspace_root = workspace_path.rstrip("/")
            normalized_path = absolute_path.replace("\\", "/")
            relative_path = normalized_path
            if normalized_path.startswith(f"{workspace_root}/"):
                relative_path = normalized_path[len(workspace_root) + 1 :]
            server_entry = get_workspace_server_entry(context.run.workspace_server_id)
            payload = remote_write_file(
                server_entry,
                path=workspace_path,
                relative_path=relative_path,
                content=content,
                create_dirs=True,
                overwrite=True,
            )
            final_path = normalized_path
            size_bytes = payload.get("size_bytes")
            rel = payload.get("relative_path")
        else:
            root = Path(workspace_path)
            normalized_target = Path(absolute_path)
            relative_path = normalized_target.relative_to(root).as_posix()
            payload = write_workspace_file(
                workspace_path,
                relative_path,
                content,
                create_dirs=True,
                overwrite=True,
            )
            final_path = str(normalized_target)
            size_bytes = payload.get("size_bytes")
            rel = payload.get("relative_path")
    except Exception as exc:
        logger.warning("write run artifact failed: %s", exc)
        return None
    return {
        "kind": kind,
        "path": final_path,
        "relative_path": rel,
        "size_bytes": size_bytes,
    }


def _dedupe_artifact_refs(artifact_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in artifact_refs:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("kind") or ""), str(item.get("path") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _artifact_relative_to_workspace(
    workspace_root: str | None,
    absolute_path: str | Path | None,
    *,
    remote: bool,
) -> str | None:
    root = str(workspace_root or "").strip()
    target = str(absolute_path or "").strip()
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
        return Path(target).relative_to(Path(root)).as_posix()
    except Exception:
        return None


def _collect_run_artifacts(context: WorkflowContext, limit: int = 40) -> list[dict[str, Any]]:
    run_directory = context.run.run_directory
    if not run_directory:
        return []
    workspace_root = _resolve_workspace_path(context.run) or run_directory
    if context.run.workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(context.run.workspace_server_id)
            overview = build_remote_overview(server_entry, run_directory, depth=3, max_entries=limit)
        except Exception:
            return []
        items = []
        normalized_run_directory = str(overview.get("workspace_path") or run_directory).rstrip("/")
        for file_path in (overview.get("files") or [])[:limit]:
            absolute_path = f"{normalized_run_directory}/{str(file_path).lstrip('/')}"
            items.append(
                {
                    "kind": "artifact",
                    "path": absolute_path,
                    "relative_path": _artifact_relative_to_workspace(
                        workspace_root,
                        absolute_path,
                        remote=True,
                    ) or str(file_path),
                }
            )
        return items
    target = Path(run_directory)
    if not target.exists() or not target.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for file_path in sorted(target.rglob("*")):
        if len(items) >= limit:
            break
        if not file_path.is_file():
            continue
        items.append(
            {
                "kind": "artifact",
                "path": str(file_path),
                "relative_path": _artifact_relative_to_workspace(
                    workspace_root,
                    file_path,
                    remote=False,
                ) or file_path.relative_to(target).as_posix(),
                "size_bytes": file_path.stat().st_size,
            }
        )
    return items


def _collect_workspace_artifacts_for_path(
    context: WorkflowContext,
    workspace_path: str | None,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    target_path = str(workspace_path or "").strip()
    if not target_path:
        return []
    if context.run.workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(context.run.workspace_server_id)
            overview = build_remote_overview(server_entry, target_path, depth=3, max_entries=limit)
        except Exception:
            return []
        normalized_workspace = str(overview.get("workspace_path") or target_path).rstrip("/")
        items: list[dict[str, Any]] = []
        for file_path in (overview.get("files") or [])[:limit]:
            absolute_path = f"{normalized_workspace}/{str(file_path).lstrip('/')}"
            items.append(
                {
                    "kind": "workspace_file",
                    "path": absolute_path,
                    "relative_path": _artifact_relative_to_workspace(
                        normalized_workspace,
                        absolute_path,
                        remote=True,
                    ) or str(file_path),
                }
            )
        return items

    root = Path(target_path)
    if not root.exists() or not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for file_path in sorted(root.rglob("*")):
        if len(items) >= limit:
            break
        if not file_path.is_file():
            continue
        items.append(
            {
                "kind": "workspace_file",
                "path": str(file_path),
                "relative_path": _artifact_relative_to_workspace(
                    target_path,
                    file_path,
                    remote=False,
                ) or file_path.relative_to(root).as_posix(),
                "size_bytes": file_path.stat().st_size,
            }
        )
    return items


def _collect_pdf_paths_for_run(context: WorkflowContext, limit: int = 12) -> list[str]:
    run_directory = context.run.run_directory or _resolve_workspace_path(context.run)
    if not run_directory:
        return []
    if context.run.workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(context.run.workspace_server_id)
            overview = build_remote_overview(server_entry, run_directory, depth=4, max_entries=160)
        except Exception:
            return []
        workspace_root = str(overview.get("workspace_path") or run_directory).rstrip("/")
        return [
            f"{workspace_root}/{str(item).lstrip('/')}"
            for item in (overview.get("files") or [])
            if str(item).lower().endswith(".pdf")
        ][:limit]
    root = Path(run_directory)
    if not root.exists() or not root.is_dir():
        return []
    return [str(path) for path in sorted(root.rglob("*.pdf"))[:limit]]


def _build_experiment_summary_prompt(
    context: WorkflowContext,
    inspection: dict[str, Any],
    execution: dict[str, Any],
) -> str:
    effective_command_line = (
        f"实际执行命令: {execution.get('effective_command')}\n"
        if execution.get("effective_command") and execution.get("effective_command") != execution.get("command")
        else ""
    )
    remote_lines: list[str] = []
    batch_items = [item for item in (execution.get("batch_experiments") or []) if isinstance(item, dict)]
    if len(batch_items) > 1:
        remote_lines.extend(
            [
                f"批量实验数: {len(batch_items)}",
                *[
                    (
                        f"- {item.get('name')}: status={item.get('status')}, "
                        f"session={item.get('remote_session_name') or 'N/A'}, "
                        f"workspace={item.get('remote_execution_workspace') or 'N/A'}, "
                        f"gpu={_describe_selected_gpu(item.get('selected_gpu'))}, "
                        f"command={item.get('effective_command') or item.get('command')}"
                    )
                    for item in batch_items[:8]
                ],
            ]
        )
    elif execution.get("remote_session_name"):
        remote_lines.extend(
            [
                f"远程会话: {execution.get('remote_session_name')}",
                f"隔离工作区: {execution.get('remote_execution_workspace')}",
                f"隔离模式: {execution.get('remote_isolation_mode')}",
                f"GPU 分配: {_describe_selected_gpu(execution.get('selected_gpu'))}",
                f"启动命令: {execution.get('launch_command')}",
            ]
        )
    remote_block = "\n".join(remote_lines)
    return (
        "你是科研实验总结助手。请根据下面的工作区与实验执行结果，输出一份中文 Markdown 总结。\n"
        "结构至少包含：实验目的、执行情况、关键结果、异常风险、下一步建议。\n\n"
        f"项目名称: {context.project.name}\n"
        f"用户目标: {context.run.prompt}\n"
        f"工作区状态:\n{str(inspection.get('tree') or inspection.get('message') or '')[:3000]}\n\n"
        f"{remote_block}\n\n"
        f"命令: {execution.get('command')}\n"
        f"{effective_command_line}"
        f"退出码: {execution.get('exit_code')}\n"
        f"stdout:\n{str(execution.get('stdout') or '')[:5000]}\n\n"
        f"stderr:\n{str(execution.get('stderr') or '')[:3000]}\n"
    )


def _resolve_experiment_summary_markdown(
    context: WorkflowContext,
    execution: dict[str, Any],
    llm_result: LLMResult,
) -> str:
    content = str(llm_result.content or "").strip()
    if content and not _looks_like_llm_error(content):
        return content
    lines = [
        f"# {context.project.name} 实验总结",
        "",
        f"- 执行命令: `{execution.get('command')}`",
        *(
            [f"- 实际执行命令: `{execution.get('effective_command')}`"]
            if execution.get("effective_command") and execution.get("effective_command") != execution.get("command")
            else []
        ),
        f"- 退出码: `{execution.get('exit_code')}`",
    ]
    batch_items = [item for item in (execution.get("batch_experiments") or []) if isinstance(item, dict)]
    if len(batch_items) > 1:
        lines.extend(["", "## 批量实验状态"])
        lines.extend(
            [
                (
                    f"- {item.get('name')}: status=`{item.get('status')}`, "
                    f"session=`{item.get('remote_session_name') or 'N/A'}`, "
                    f"workspace=`{item.get('remote_execution_workspace') or 'N/A'}`, "
                    f"gpu=`{_describe_selected_gpu(item.get('selected_gpu'))}`"
                )
                for item in batch_items[:12]
            ]
        )
    elif execution.get("remote_session_name"):
        lines.extend(
            [
                f"- 远程会话: `{execution.get('remote_session_name')}`",
                f"- 隔离工作区: `{execution.get('remote_execution_workspace')}`",
                f"- 隔离模式: `{execution.get('remote_isolation_mode')}`",
                f"- GPU 分配: `{_describe_selected_gpu(execution.get('selected_gpu'))}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 结果概览",
            str(execution.get("stdout") or "无 stdout 输出")[:2400],
        ]
    )
    stderr = str(execution.get("stderr") or "").strip()
    if stderr:
        lines.extend(["", "## 异常与告警", stderr[:1600]])
    lines.extend(
        [
            "",
            "## 下一步建议",
            "- 根据 stdout / stderr 补齐关键指标记录和失败原因定位。",
            "- 若需要继续实验，请明确下一轮命令、配置差异和预期验证点。",
        ]
    )
    return "\n".join(lines).strip()


def _resolve_generic_markdown(llm_result: LLMResult, *, fallback: str) -> str:
    content = str(llm_result.content or "").strip()
    if content and not _looks_like_llm_error(content):
        return sanitize_project_markdown(content)
    return sanitize_project_markdown(fallback.strip())


def _parse_json_payload_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_aris_guided_prompt(
    context: WorkflowContext,
    *,
    skill_ids: list[str],
    phase_label: str,
    output_contract: str,
    context_blocks: list[tuple[str, str | None]],
) -> str:
    skill_bundle = _sanitize_upstream_reference_text(render_aris_skill_bundle(skill_ids))
    lines = [
        "你正在通过 ResearchOS 后端执行项目科研工作流。",
        "请保持参考 skill 中的阶段目标、产物结构和运行约束，但输出口径统一使用 ResearchOS 的项目工作流语义。",
        f"项目名称: {context.project.name}",
        f"项目描述: {context.project.description or '暂无描述'}",
        f"用户任务: {context.run.prompt or '无'}",
        f"当前阶段: {phase_label}",
        "checkpoint 语义: 如果当前运行启用了 human checkpoint，则在关键 gate 使用阶段暂停；否则默认自动继续。",
        "论文上下文语义: prompt 中的论文只以索引元信息出现；需要证据时按 paper_id/ref_id 按需读取已有分析或 PDF/OCR 摘要，不要把索引当作论文结论。",
    ]
    if skill_bundle:
        lines.extend(["", "[Reference Workflow Skills]", skill_bundle])
    for label, body in context_blocks:
        content = str(body or "").strip()
        if not content:
            continue
        lines.extend(["", f"[{label}]", content])
    lines.extend(["", "[Output Contract]", output_contract])
    return "\n".join(lines).strip()


def _sanitize_upstream_reference_text(text: str) -> str:
    sanitized = str(text or "").strip()
    if not sanitized:
        return ""
    replacements = {
        "ARIS": "ResearchOS",
        "Amadeus / ARIS": "ResearchOS",
        "amadeus_aris": "researchos_project_workflow",
    }
    for old, new in replacements.items():
        sanitized = sanitized.replace(old, new)
    sanitized = sanitized.replace("Reference skill:", "Reference workflow skill:")
    return sanitized


def _render_idea_discovery_report(
    context: WorkflowContext,
    *,
    literature_markdown: str,
    created_ideas: list[dict[str, Any]],
    novelty_markdown: str,
    review_markdown: str,
) -> str:
    return format_idea_discovery_report(
        context.project.name,
        context.run.prompt or context.project.name,
        literature_markdown,
        created_ideas,
        novelty_markdown,
        review_markdown,
    )


def _build_novelty_check_prompt(context: WorkflowContext, materials: str) -> str:
    context_blocks: list[tuple[str, str]] = [("Project Materials", materials)]
    reviewer_evidence = _build_project_reviewer_evidence_block(context)
    if reviewer_evidence:
        context_blocks.append(("Raw Project Evidence", reviewer_evidence))
    return _build_aris_guided_prompt(
        context,
        skill_ids=["novelty-check"],
        phase_label="Novelty Check",
        output_contract=(
            "输出中文 Markdown 查新对比。至少包含：当前主张、3-5 个核心 claims、"
            "最相近工作、重叠风险、真正差异点、需要补证据的地方。"
        ),
        context_blocks=context_blocks,
    )


def _build_novelty_report_prompt(context: WorkflowContext, comparison_markdown: str) -> str:
    context_blocks: list[tuple[str, str]] = [("Novelty Comparison", comparison_markdown)]
    reviewer_evidence = _build_project_reviewer_evidence_block(context)
    if reviewer_evidence:
        context_blocks.append(("Raw Project Evidence", reviewer_evidence))
    return _build_aris_guided_prompt(
        context,
        skill_ids=["novelty-check"],
        phase_label="Novelty Report",
        output_contract=(
            "输出最终中文 Markdown 查新报告。至少包含：Overall Novelty Assessment、"
            "Closest Prior Work 表格、Recommendation、Suggested Positioning。"
        ),
        context_blocks=context_blocks,
    )


def _build_research_review_prompt(context: WorkflowContext, materials: str) -> str:
    context_blocks: list[tuple[str, str]] = [("Research Materials", materials)]
    reviewer_evidence = _build_project_reviewer_evidence_block(context)
    if reviewer_evidence:
        context_blocks.append(("Raw Project Evidence", reviewer_evidence))
    return _build_aris_guided_prompt(
        context,
        skill_ids=["research-review"],
        phase_label="Research Review",
        output_contract=(
            "输出中文 Markdown 评审意见。至少包含：概述、创新性、技术可信度、实验充分性、"
            "表达质量、主要问题、建议评分，以及最小可执行修复项。"
        ),
        context_blocks=context_blocks,
    )


def _build_research_review_verdict_prompt(context: WorkflowContext, review_markdown: str) -> str:
    context_blocks: list[tuple[str, str]] = [("Review Draft", review_markdown)]
    reviewer_evidence = _build_project_reviewer_evidence_block(context)
    if reviewer_evidence:
        context_blocks.append(("Raw Project Evidence", reviewer_evidence))
    return _build_aris_guided_prompt(
        context,
        skill_ids=["research-review"],
        phase_label="Research Review Verdict",
        output_contract="整理成最终评审结论，至少包含：总体评价、关键问题、优先修复建议、是否建议继续推进。",
        context_blocks=context_blocks,
    )


def _build_auto_review_plan_prompt(context: WorkflowContext, *, max_iterations: int) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["auto-review-loop"],
        phase_label="Auto Review Initialization",
        output_contract=(
            "输出自动评审循环计划，至少包含：目标、成功标准、停止条件、"
            "每轮 Review/Implement/Wait/Document 的重点，以及 REVIEW_STATE.json 需保存的字段。"
        ),
        context_blocks=[("Loop Settings", f"MAX_ROUNDS={max_iterations}")],
    )


def _build_auto_review_execute_prompt(
    context: WorkflowContext,
    plan_markdown: str,
    *,
    iteration: int,
    previous_reviews: list[str],
) -> str:
    previous_block = "\n\n".join(previous_reviews[-2:]) if previous_reviews else "无"
    return _build_aris_guided_prompt(
        context,
        skill_ids=["auto-review-loop"],
        phase_label=f"Auto Review Execute Round {iteration}",
        output_contract="输出本轮执行摘要，至少包含：本轮行动、关键发现、阻塞点、对下一轮的影响。",
        context_blocks=[
            ("Loop Plan", plan_markdown),
            ("Previous Reviews", previous_block),
        ],
    )


def _build_auto_review_json_prompt(
    context: WorkflowContext,
    plan_markdown: str,
    execution_markdown: str,
    *,
    iteration: int,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["auto-review-loop"],
        phase_label=f"Auto Review Assessment Round {iteration}",
        output_contract=(
            "只输出 JSON，不要输出 Markdown 代码块。格式为："
            '{"score":0-10,"continue":true,"summary":"一句话总结","verdict":"ready|almost|not ready",'
            '"issues":["..."],"next_actions":["..."],"raw_review":"完整评审原文","pending_experiments":["..."]}'
        ),
        context_blocks=[
            ("Loop Plan", plan_markdown),
            ("Current Execution", execution_markdown),
        ],
    )


def _resolve_auto_review_payload(llm_result: LLMResult, *, iteration: int) -> dict[str, Any]:
    payload = llm_result.parsed_json or {}
    try:
        score = float(payload.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    summary = str(payload.get("summary") or f"第 {iteration} 轮评审已完成。").strip()
    verdict = str(payload.get("verdict") or "").strip().lower()
    issues = [str(item).strip() for item in payload.get("issues", []) if str(item).strip()] if isinstance(payload.get("issues"), list) else []
    next_actions = [str(item).strip() for item in payload.get("next_actions", []) if str(item).strip()] if isinstance(payload.get("next_actions"), list) else []
    raw_review = str(payload.get("raw_review") or "").strip()
    pending_experiments = [
        str(item).strip()
        for item in payload.get("pending_experiments", [])
        if str(item).strip()
    ] if isinstance(payload.get("pending_experiments"), list) else []
    if "continue" in payload:
        continue_flag = bool(payload.get("continue"))
    else:
        positive_verdict = verdict in {"ready", "almost"}
        continue_flag = iteration < 4 and not (score >= 6.0 and positive_verdict)
    return {
        "score": max(0.0, min(score, 10.0)),
        "continue": continue_flag,
        "summary": summary,
        "verdict": verdict or ("ready" if score >= 6.0 and not next_actions else "not ready"),
        "issues": issues,
        "next_actions": next_actions,
        "raw_review": raw_review or summary,
        "pending_experiments": pending_experiments,
    }


def _auto_review_iteration_markdown(iteration_report: dict[str, Any]) -> str:
    review = dict(iteration_report.get("review") or {})
    execution_summary = str(iteration_report.get("execution_summary") or "").strip()
    issues = review.get("issues") or []
    next_actions = review.get("next_actions") or []
    lines = [
        f"## 第 {iteration_report.get('iteration')} 轮",
        "",
        f"- 评审分数: {review.get('score')}",
        f"- Verdict: {review.get('verdict') or 'not ready'}",
        f"- 是否继续: {'是' if review.get('continue') else '否'}",
        f"- 评审摘要: {review.get('summary') or '无'}",
        "",
        "### 本轮执行",
        execution_summary or "无",
    ]
    if issues:
        lines.extend(["", "### 主要问题", *[f"- {item}" for item in issues]])
    if next_actions:
        lines.extend(["", "### 下一步", *[f"- {item}" for item in next_actions]])
    raw_review = str(review.get("raw_review") or "").strip()
    if raw_review:
        lines.extend(
            [
                "",
                "### Reviewer Raw Response",
                "<details>",
                "<summary>展开完整评审原文</summary>",
                "",
                raw_review,
                "",
                "</details>",
            ]
        )
    return "\n".join(lines).strip()


def _build_writing_materials(context: WorkflowContext) -> str:
    materials: list[str] = [
        f"项目名称: {context.project.name}",
        f"项目描述: {context.project.description or '暂无描述'}",
        f"写作目标: {context.run.prompt}",
    ]
    if context.selected_papers:
        materials.append("\n[关联论文]")
        materials.append(
            format_ref_index_for_prompt(
                _paper_index_from_context(context),
                empty_text="当前项目还没有关联论文。",
            )
        )
    prior_workflow_outputs = _project_workflow_output_summaries(
        context.project.id,
        [
            ProjectWorkflowType.paper_plan,
            ProjectWorkflowType.paper_figure,
            ProjectWorkflowType.paper_write,
            ProjectWorkflowType.paper_compile,
            ProjectWorkflowType.paper_improvement,
        ],
        exclude_run_id=context.run.id,
    )
    if prior_workflow_outputs:
        materials.append("\n[既有论文流程产物]")
        materials.extend(prior_workflow_outputs)
    reports = _project_report_summaries(context.project.id)
    if reports:
        materials.append("\n[现有项目报告]")
        materials.extend(reports)
    return "\n\n".join(materials)


def _project_report_summaries(project_id: str) -> list[str]:
    with session_scope() as session:
        repo = ProjectRepository(session)
        items = repo.list_project_reports(project_id, limit=8)
        return [
            f"- {content.title}: {str(content.markdown or '').strip()[:1000]}"
            for content, _paper in items
        ]


def _project_report_excerpts(
    project_id: str,
    *,
    limit: int = 4,
    max_chars: int = 2400,
) -> list[str]:
    with session_scope() as session:
        repo = ProjectRepository(session)
        items = repo.list_project_reports(project_id, limit=limit)
        excerpts: list[str] = []
        for content, _paper in items:
            markdown = sanitize_project_markdown(str(content.markdown or "").strip())
            if not markdown:
                continue
            excerpts.append(f"### {content.title}\n{markdown[:max_chars]}")
        return excerpts


def _project_workflow_output_summaries(
    project_id: str,
    workflow_types: list[ProjectWorkflowType],
    *,
    exclude_run_id: str | None = None,
) -> list[str]:
    workflow_values = [workflow_type.value for workflow_type in workflow_types]
    with session_scope() as session:
        repo = ProjectRepository(session)
        runs = repo.list_runs(project_id, limit=80)
        latest_by_type: dict[str, dict[str, Any]] = {}
        for run in runs:
            workflow_value = str(run.workflow_type)
            if workflow_value not in workflow_values:
                continue
            if exclude_run_id and str(run.id) == exclude_run_id:
                continue
            if str(run.status) != ProjectRunStatus.succeeded.value:
                continue
            if workflow_value in latest_by_type:
                continue
            latest_by_type[workflow_value] = {
                "workflow_type": workflow_value,
                "metadata": dict(run.metadata_json or {}),
            }

    summaries: list[str] = []
    for workflow_type in workflow_types:
        run_payload = latest_by_type.get(workflow_type.value)
        if run_payload is None:
            continue
        metadata = dict(run_payload.get("metadata") or {})
        markdown = str(metadata.get("workflow_output_markdown") or "").strip()
        if not markdown:
            stage_outputs = metadata.get("stage_outputs") or {}
            markdown = "\n\n".join(
                str(item.get("content") or "").strip()
                for item in stage_outputs.values()
                if isinstance(item, dict) and str(item.get("content") or "").strip()
            ).strip()
        if not markdown:
            continue
        summaries.append(
            f"- {workflow_type.value}: {markdown[:1600]}"
        )
    return summaries


def _project_workflow_output_excerpts(
    project_id: str,
    workflow_types: list[ProjectWorkflowType],
    *,
    exclude_run_id: str | None = None,
    max_chars: int = 2400,
) -> list[str]:
    workflow_values = [workflow_type.value for workflow_type in workflow_types]
    with session_scope() as session:
        repo = ProjectRepository(session)
        runs = repo.list_runs(project_id, limit=80)
        latest_by_type: dict[str, dict[str, Any]] = {}
        for run in runs:
            workflow_value = str(run.workflow_type)
            if workflow_value not in workflow_values:
                continue
            if exclude_run_id and str(run.id) == exclude_run_id:
                continue
            if str(run.status) != ProjectRunStatus.succeeded.value:
                continue
            if workflow_value in latest_by_type:
                continue
            latest_by_type[workflow_value] = {
                "workflow_type": workflow_value,
                "metadata": dict(run.metadata_json or {}),
            }

    excerpts: list[str] = []
    for workflow_type in workflow_types:
        run_payload = latest_by_type.get(workflow_type.value)
        if run_payload is None:
            continue
        metadata = dict(run_payload.get("metadata") or {})
        markdown = str(metadata.get("workflow_output_markdown") or "").strip()
        if not markdown:
            stage_outputs = metadata.get("stage_outputs") or {}
            markdown = "\n\n".join(
                str(item.get("content") or "").strip()
                for item in stage_outputs.values()
                if isinstance(item, dict) and str(item.get("content") or "").strip()
            ).strip()
        markdown = sanitize_project_markdown(markdown)
        if not markdown:
            continue
        excerpts.append(f"### {workflow_type.value}\n{markdown[:max_chars]}")
    return excerpts


def _build_project_reviewer_evidence_block(
    context: WorkflowContext,
    *,
    workflow_types: list[ProjectWorkflowType] | None = None,
) -> str:
    target_workflows = workflow_types or [
        ProjectWorkflowType.literature_review,
        ProjectWorkflowType.idea_discovery,
        ProjectWorkflowType.run_experiment,
        ProjectWorkflowType.experiment_audit,
        ProjectWorkflowType.auto_review_loop,
        ProjectWorkflowType.paper_writing,
        ProjectWorkflowType.paper_improvement,
        ProjectWorkflowType.full_pipeline,
    ]
    blocks: list[str] = []
    blocks.extend(
        _project_workflow_output_excerpts(
            context.project.id,
            target_workflows,
            exclude_run_id=context.run.id,
            max_chars=2200,
        )[:4]
    )
    report_limit = max(0, 6 - len(blocks))
    if report_limit:
        blocks.extend(_project_report_excerpts(context.project.id, limit=report_limit, max_chars=2200))
    return "\n\n".join(blocks).strip()


def _build_paper_plan_phase_prompt(context: WorkflowContext, materials: str) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["paper-writing", "paper-plan"],
        phase_label="Paper Writing Phase 1 - Paper Plan",
        output_contract=(
            "输出中文 Markdown 的 PAPER_PLAN 草稿。至少包含：claims-evidence matrix、"
            "section plan、figure plan、citation plan、target venue、page budget。"
        ),
        context_blocks=[("Writing Materials", materials)],
    )


def _build_paper_figure_phase_prompt(context: WorkflowContext, plan_markdown: str, materials: str) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["paper-writing", "paper-figure"],
        phase_label="Paper Writing Phase 2 - Figure Generation",
        output_contract=(
            "输出中文 Markdown 图表计划。至少包含：figure/table inventory、manual vs auto figure、"
            "latex include 提示、数据来源和缺失项。"
        ),
        context_blocks=[
            ("PAPER_PLAN", plan_markdown),
            ("Writing Materials", materials),
        ],
    )


def _build_paper_write_phase_prompt(
    context: WorkflowContext,
    *,
    materials: str,
    plan_markdown: str,
    figure_markdown: str,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["paper-writing", "paper-write"],
        phase_label="Paper Writing Phase 3 - LaTeX Writing",
        output_contract=(
            "输出 Markdown 论文内容草稿，用于后续物化到 LaTeX 工作区。至少包含：题目、摘要、引言、"
            "related work、method、experiments、limitations、conclusion。不要虚构未提供的结果。"
        ),
        context_blocks=[
            ("PAPER_PLAN", plan_markdown),
            ("FIGURE_PLAN", figure_markdown),
            ("Writing Materials", materials),
        ],
    )


def _build_paper_compile_phase_prompt(
    context: WorkflowContext,
    *,
    draft_markdown: str,
    compile_command: str | None,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["paper-writing", "paper-compile"],
        phase_label="Paper Writing Phase 4 - Compilation",
        output_contract=(
            "输出中文 Markdown 编译报告。至少包含：compiler/toolchain prerequisites、compile command、"
            "status、page budget、undefined refs/citations、next fixes。"
        ),
        context_blocks=[
            ("Draft Manuscript", draft_markdown),
            ("Compile Command", compile_command or "未提供 compile command，需要输出待执行的编译检查清单。"),
        ],
    )


def _build_paper_improvement_phase_prompt(
    context: WorkflowContext,
    *,
    draft_markdown: str,
    compile_markdown: str,
) -> str:
    return _build_paper_revision_round_prompt(
        context,
        draft_markdown=draft_markdown,
        review_markdown="待根据当前审稿意见对齐。",
        compile_markdown=compile_markdown,
        round_number=2,
    )


def _build_paper_review_round_prompt(
    context: WorkflowContext,
    *,
    draft_markdown: str,
    compile_markdown: str,
    round_number: int,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["paper-writing", "auto-paper-improvement-loop"],
        phase_label=f"Paper Writing Phase 5 - Review Round {round_number}",
        output_contract=(
            "输出中文 Markdown 审稿意见。必须包含 `Score: <0-10>`、主要问题、最高优先级修订项、"
            "是否建议继续投稿或继续修改。"
        ),
        context_blocks=[
            ("Draft Manuscript", draft_markdown),
            ("Compile Report", compile_markdown),
        ],
    )


def _build_paper_revision_round_prompt(
    context: WorkflowContext,
    *,
    draft_markdown: str,
    review_markdown: str,
    compile_markdown: str,
    round_number: int,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["paper-writing", "auto-paper-improvement-loop"],
        phase_label=f"Paper Writing Phase 5 - Revision Round {round_number}",
        output_contract=(
            "输出修订后的完整论文 Markdown，保持学术写作风格，并根据审稿意见落实关键修改。"
            "必须保留摘要、引言、相关工作、方法、实验、局限性、结论等核心章节。"
        ),
        context_blocks=[
            ("Current Draft", draft_markdown),
            ("Review Feedback", review_markdown),
            ("Compile Report", compile_markdown),
        ],
    )


def _build_paper_writing_prompt(context: WorkflowContext, materials: str) -> str:
    return _build_paper_write_phase_prompt(
        context,
        materials=materials,
        plan_markdown="待根据 PAPER_PLAN.md 对齐。",
        figure_markdown="待根据 FIGURE_PLAN.md 对齐。",
    )


def _build_manuscript_polish_prompt(context: WorkflowContext, draft_markdown: str) -> str:
    return _build_paper_improvement_phase_prompt(
        context,
        draft_markdown=draft_markdown,
        compile_markdown="待根据 PAPER_COMPILE.md 对齐。",
    )


def _resolve_paper_draft_markdown(context: WorkflowContext, llm_result: LLMResult) -> str:
    content = str(llm_result.content or "").strip()
    if content and not _looks_like_llm_error(content):
        return content
    return (
        f"# {context.project.name} 论文草稿\n\n"
        "## 摘要\n待补充。\n\n"
        "## 引言\n"
        f"{context.project.description or '请补充项目背景与研究目标。'}\n\n"
        "## 方法\n待根据当前项目材料补充。\n\n"
        "## 实验\n待补充实验设置与结果。\n\n"
        "## 结论\n请在补齐实验后完善结论。"
    )


def _resolve_paper_polish_markdown(draft_markdown: str, llm_result: LLMResult) -> str:
    content = str(llm_result.content or "").strip()
    if content and not _looks_like_llm_error(content):
        return content
    return draft_markdown


def _build_pipeline_synthesis_prompt(
    context: WorkflowContext,
    review_markdown: str,
    inspection: dict[str, Any],
    execution: dict[str, Any],
) -> str:
    context_blocks: list[tuple[str, str]] = [
        ("IDEA_REPORT", review_markdown[:5000]),
        ("Workspace Snapshot", str(inspection.get("tree") or inspection.get("message") or "")[:2500]),
        (
            "Execution Result",
            (
                f"命令: {execution.get('command')}\n退出码: {execution.get('exit_code')}\n"
                f"stdout:\n{str(execution.get('stdout') or '')[:3500]}\n\nstderr:\n{str(execution.get('stderr') or '')[:2000]}"
            ),
        ),
    ]
    reviewer_evidence = _build_project_reviewer_evidence_block(
        context,
        workflow_types=[
            ProjectWorkflowType.run_experiment,
            ProjectWorkflowType.experiment_audit,
            ProjectWorkflowType.auto_review_loop,
            ProjectWorkflowType.paper_writing,
            ProjectWorkflowType.full_pipeline,
        ],
    )
    if reviewer_evidence:
        context_blocks.append(("Raw Project Evidence", reviewer_evidence))
    return _build_aris_guided_prompt(
        context,
        skill_ids=["research-pipeline", "auto-review-loop"],
        phase_label="Research Pipeline Stage 3 - Auto Review Loop Summary",
        output_contract=(
            "输出中文 Markdown AUTO_REVIEW 摘要。至少包含：score/verdict、主要 weaknesses、"
            "最小修复建议、pending experiments、是否建议继续。"
        ),
        context_blocks=context_blocks,
    )


def _resolve_pipeline_findings_markdown(
    context: WorkflowContext,
    llm_result: LLMResult,
    review_markdown: str,
    execution: dict[str, Any],
) -> str:
    content = str(llm_result.content or "").strip()
    if content and not _looks_like_llm_error(content):
        return content
    return (
        f"# {context.project.name} 研究结论包\n\n"
        "## 相关工作结论\n"
        f"{_markdown_excerpt(review_markdown, limit=900)}\n\n"
        "## 实验执行状态\n"
        f"- 命令: `{execution.get('command')}`\n"
        f"- 退出码: `{execution.get('exit_code')}`\n\n"
        "## 下一步建议\n"
        "- 基于本轮相关工作与实验输出，补齐关键指标和失败样例分析。"
    )


def _build_pipeline_handoff_prompt(
    context: WorkflowContext,
    review_markdown: str,
    findings_markdown: str,
    execution: dict[str, Any],
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["research-pipeline"],
        phase_label="Research Pipeline Stage 4 - Final Summary",
        output_contract=(
            "输出最终中文 Markdown handoff。至少包含：Journey Summary、Final Status、"
            "Remaining TODOs、Files Changed、Next Steps。"
        ),
        context_blocks=[
            ("IDEA_REPORT", review_markdown[:4000]),
            ("AUTO_REVIEW", findings_markdown[:4500]),
            ("Execution Command", f"{execution.get('command')}\nexit_code={execution.get('exit_code')}"),
        ],
    )


def _resolve_pipeline_handoff_markdown(
    review_markdown: str,
    findings_markdown: str,
    llm_result: LLMResult,
) -> str:
    content = str(llm_result.content or "").strip()
    if content and not _looks_like_llm_error(content):
        return content
    return (
        "# 最终交付物\n\n"
        "## 相关工作\n"
        f"{_markdown_excerpt(review_markdown, limit=1200)}\n\n"
        "## 研究结论\n"
        f"{_markdown_excerpt(findings_markdown, limit=1600)}"
    )


def _record_stage_output(run_id: str, stage_id: str, payload: dict[str, Any]) -> None:
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            return
        metadata = dict(run.metadata_json or {})
        target_stage_ids = _resolve_stage_alias_ids(run.workflow_type, stage_id)
        resolved_payload = dict(payload)
        if not resolved_payload.get("engine_id"):
            model_source = str(resolved_payload.get("model_source") or "").strip()
            engine_binding: dict[str, Any] | None = None
            if model_source == "stage_engine_profile":
                orchestration = metadata.get("orchestration")
                for stage in (orchestration.get("stages") if isinstance(orchestration, dict) else []) or []:
                    if isinstance(stage, dict) and str(stage.get("id")) in target_stage_ids:
                        engine_binding = _resolve_engine_binding(
                            str(stage.get("selected_engine_id") or "").strip() or None,
                            model_source="stage_engine_profile",
                        )
                        break
            elif model_source == "executor_engine_profile":
                engine_binding = _resolve_engine_binding(
                    str(_engine_binding_snapshot(metadata, "executor").get("id") or "").strip() or None,
                    model_source=model_source,
                    fallback_payload=_engine_binding_snapshot(metadata, "executor"),
                )
            elif model_source == "reviewer_engine_profile":
                engine_binding = _resolve_engine_binding(
                    str(_engine_binding_snapshot(metadata, "reviewer").get("id") or "").strip() or None,
                    model_source=model_source,
                    fallback_payload=_engine_binding_snapshot(metadata, "reviewer"),
                )
            if engine_binding is not None:
                resolved_payload.setdefault("engine_id", engine_binding.get("engine_id"))
                resolved_payload.setdefault("engine_label", engine_binding.get("engine_label"))
        stage_outputs = dict(metadata.get("stage_outputs") or {})
        for target_stage_id in target_stage_ids:
            stage_outputs[target_stage_id] = resolved_payload
        metadata["stage_outputs"] = stage_outputs
        trace = list(metadata.get("stage_trace") or [])
        updated_trace: list[dict[str, Any]] = []
        for item in trace:
            current = dict(item) if isinstance(item, dict) else {}
            if str(current.get("stage_id")) in target_stage_ids:
                current.update(
                    {
                        "provider": resolved_payload.get("provider"),
                        "model": resolved_payload.get("model"),
                        "variant": resolved_payload.get("variant"),
                        "model_role": resolved_payload.get("model_role") or current.get("model_role"),
                        "model_source": resolved_payload.get("model_source"),
                        "engine_id": resolved_payload.get("engine_id") or current.get("engine_id"),
                        "engine_label": resolved_payload.get("engine_label") or current.get("engine_label"),
                    }
                )
            updated_trace.append(current)
        if updated_trace:
            metadata["stage_trace"] = updated_trace
        project_repo.update_run(run_id, metadata=metadata)


def _load_context(run_id: str) -> WorkflowContext:
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            raise ValueError(f"project run {run_id} not found")

        project = project_repo.get_project(run.project_id)
        if project is None:
            raise ValueError(f"project {run.project_id} not found")

        metadata = dict(run.metadata_json or {})
        paper_rows = project_repo.list_project_papers(project.id)
        paper_by_id = {paper.id: paper for _link, paper in paper_rows}
        selected_paper_ids = normalize_paper_ids(metadata.get("paper_ids") if isinstance(metadata.get("paper_ids"), list) else [])
        missing_selected_ids = [paper_id for paper_id in selected_paper_ids if paper_id not in paper_by_id]
        if missing_selected_ids:
            for paper in PaperRepository(session).list_by_ids(missing_selected_ids):
                paper_by_id[str(paper.id)] = paper
        selected_papers = _pick_selected_papers(paper_rows, paper_by_id, selected_paper_ids)

        selected_repo_ids = _normalize_id_list(metadata.get("repo_ids"))
        all_repos = project_repo.list_repos(project.id)
        selected_repos = _pick_selected_repos(all_repos, selected_repo_ids)

        return WorkflowContext(
            run=RunSnapshot(
                id=run.id,
                workflow_type=run.workflow_type,
                prompt=run.prompt,
                title=run.title,
                max_iterations=run.max_iterations,
                executor_model=getattr(run, "executor_model", None) or str(metadata.get("executor_model") or "").strip() or None,
                reviewer_model=(
                    str(run.reviewer_model or "").strip()
                    or str(metadata.get("reviewer_model") or "").strip()
                    or None
                ),
                task_id=run.task_id,
                started_at=run.started_at,
                target_id=run.target_id,
                workspace_server_id=run.workspace_server_id,
                workdir=run.workdir,
                remote_workdir=run.remote_workdir,
                run_directory=run.run_directory,
                log_path=run.log_path,
            ),
            project=ProjectSnapshot(
                id=project.id,
                name=project.name,
                description=project.description or "",
            ),
            metadata=metadata,
            selected_papers=selected_papers,
            selected_repos=selected_repos,
            analysis_contexts={},
        )


def _pick_selected_papers(
    paper_rows: list[tuple[Any, Any]],
    paper_by_id: dict[str, Any],
    selected_ids: list[str],
) -> list[PaperSnapshot]:
    if selected_ids:
        ordered = [_paper_snapshot(paper_by_id[paper_id]) for paper_id in selected_ids if paper_id in paper_by_id]
        if ordered:
            return ordered
    return [_paper_snapshot(paper) for _link, paper in paper_rows]


def _pick_selected_repos(all_repos: list[Any], selected_ids: list[str]) -> list[RepoSnapshot]:
    if selected_ids:
        selected = [_repo_snapshot(repo) for repo in all_repos if repo.id in selected_ids]
        if selected:
            return selected[:6]
    return [_repo_snapshot(repo) for repo in all_repos[:6]]


def _extract_prompt_directive(prompt: str, key: str) -> str | None:
    value = str(prompt or "").strip()
    if not value:
        return None
    pattern = re.compile(
        rf"(?:^|\s)(?:—|--|-)\s*{re.escape(key)}\s*:\s*(.+?)(?=(?:\s(?:—|--|-)\s*[A-Za-z][A-Za-z _-]*\s*:)|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(value)
    if not match:
        return None
    directive = re.sub(r"\s+", " ", match.group(1)).strip()
    return directive or None


def _strip_prompt_directives(prompt: str) -> str:
    value = str(prompt or "").strip()
    if not value:
        return ""
    cleaned = re.split(r"\s(?:—|--|-)\s*[A-Za-z][A-Za-z _-]*\s*:", value, maxsplit=1)[0].strip()
    return cleaned or value


def _resolve_literature_query(context: WorkflowContext) -> str:
    metadata_query = str(context.metadata.get("literature_query") or "").strip()
    if metadata_query:
        return metadata_query
    prompt_query = _strip_prompt_directives(context.run.prompt)
    if prompt_query:
        return prompt_query
    description = str(context.project.description or "").strip()
    if description:
        return description[:240]
    return context.project.name


def _resolve_literature_sources(context: WorkflowContext) -> set[str]:
    raw_value = (
        str(context.metadata.get("literature_sources") or "").strip()
        or str(_extract_prompt_directive(context.run.prompt, "sources") or "").strip()
    )
    if not raw_value:
        return {"project", "library", "local"}
    values = {
        item.strip().lower()
        for item in raw_value.split(",")
        if str(item or "").strip()
    }
    if "all" in values:
        return {"project", "library", "local", "web"}
    mapping = {
        "project": "project",
        "linked": "project",
        "library": "library",
        "paper_library": "library",
        "local": "local",
        "workspace": "local",
        "web": "web",
        "arxiv": "web",
    }
    resolved = {mapping[item] for item in values if item in mapping}
    return resolved or {"project", "library", "local"}


def _read_workspace_text_file(
    context: WorkflowContext,
    relative_path: str,
    *,
    max_chars: int = 20000,
) -> str:
    workspace_path = _resolve_workspace_path(context.run)
    if not workspace_path:
        return ""
    if context.run.workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(context.run.workspace_server_id)
            result = remote_terminal_result(
                server_entry,
                path=workspace_path,
                command=f"cat {shlex.quote(relative_path)}",
                timeout_sec=20,
            )
        except Exception:
            return ""
        if not result.get("success"):
            return ""
        return str(result.get("stdout") or "")[:max_chars]
    file_path = Path(workspace_path) / relative_path
    if not file_path.exists() or not file_path.is_file():
        return ""
    try:
        return file_path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except OSError:
        return ""


def _extract_markdown_section(markdown_text: str, heading: str) -> str:
    value = str(markdown_text or "")
    if not value.strip():
        return ""
    pattern = re.compile(
        rf"^\s*##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^\s*##\s+|\Z)",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(value)
    if not match:
        return ""
    return match.group(1).strip()


def _resolve_paper_library_override(context: WorkflowContext) -> str | None:
    explicit = (
        str(context.metadata.get("paper_library") or "").strip()
        or str(_extract_prompt_directive(context.run.prompt, "paper library") or "").strip()
    )
    if explicit:
        return explicit
    claude_text = _read_workspace_text_file(context, "CLAUDE.md", max_chars=12000)
    section = _extract_markdown_section(claude_text, "Paper Library")
    for raw_line in section.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        backtick_match = re.search(r"`([^`]+)`", line)
        candidate = backtick_match.group(1).strip() if backtick_match else line.lstrip("-* ").strip()
        if "/" in candidate or "\\" in candidate:
            return candidate
    return None


def _library_match_score(query: str, *parts: str) -> float:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
        if len(token) >= 2
    ]
    if not tokens:
        return 0.0
    corpus = " ".join(str(part or "").lower() for part in parts)
    score = 0.0
    for token in tokens:
        if token in corpus:
            score += 1.0
    return score / max(len(tokens), 1)


def _paper_index_from_context(context: WorkflowContext) -> list[dict[str, Any]]:
    existing = context.metadata.get("paper_index")
    if isinstance(existing, list) and existing:
        return [dict(item) for item in existing if isinstance(item, dict)]
    paper_ids = [paper.id for paper in context.selected_papers]
    if not paper_ids:
        return []
    with session_scope() as session:
        repo = PaperRepository(session)
        papers = repo.list_by_ids(paper_ids)
        paper_by_id = {str(paper.id): paper for paper in papers}
        analysis_by_id = load_analysis_reports(session, paper_ids)
        refs: list[dict[str, Any]] = []
        for index, paper_id in enumerate(paper_ids, start=1):
            paper = paper_by_id.get(paper_id)
            if paper is None:
                snapshot = next((item for item in context.selected_papers if item.id == paper_id), None)
                if snapshot is None:
                    continue
                refs.append(
                    {
                        "ref_id": f"P{index}",
                        "source": "project_linked",
                        "status": "library",
                        "paper_id": snapshot.id,
                        "title": snapshot.title,
                        "arxiv_id": snapshot.arxiv_id,
                        "abstract_available": bool(snapshot.abstract),
                        "selected": False,
                        "project_linked": True,
                        "asset_status": {},
                    }
                )
                continue
            refs.append(
                paper_ref_from_model(
                    paper,
                    ref_id=f"P{index}",
                    source="project_linked",
                    match_reason="项目已关联论文",
                    selected=False,
                    project_linked=True,
                    analysis_report=analysis_by_id.get(paper_id),
                )
            )
        return refs


def _persist_literature_candidates(context: WorkflowContext, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        existing = context.metadata.get("literature_candidates")
        return [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(context.run.id)
        if run is None:
            return candidates
        metadata = dict(run.metadata_json or {})
        merged = merge_paper_refs(
            metadata.get("literature_candidates") if isinstance(metadata.get("literature_candidates"), list) else [],
            candidates,
        )
        metadata["literature_candidates"] = merged
        project_repo.update_run(run.id, metadata=metadata)
        context.metadata["literature_candidates"] = merged
        return merged


def _collect_library_paper_candidates(
    context: WorkflowContext,
    query: str,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    selected_ids = {paper.id for paper in context.selected_papers}
    with session_scope() as session:
        repo = PaperRepository(session)
        papers, _total = repo.list_paginated(
            page=1,
            page_size=max(limit + len(selected_ids), 8),
            search=query,
            sort_by="impact",
        )
        fallback_papers = repo.list_latest(limit=200)
        candidate_rows: list[Any] = []
        seen_ids: set[str] = set()
        for paper in list(papers) + list(fallback_papers):
            paper_id = str(getattr(paper, "id", "") or "")
            if not paper_id or paper_id in selected_ids or paper_id in seen_ids:
                continue
            seen_ids.add(paper_id)
            candidate_rows.append(paper)
        analysis_by_id = load_analysis_reports(session, [str(getattr(paper, "id", "") or "") for paper in candidate_rows])
        ranked: list[tuple[float, Any]] = []
        seen_ranked_ids: set[str] = set()
        for paper in candidate_rows:
            paper_id = str(getattr(paper, "id", "") or "")
            if not paper_id or paper_id in selected_ids or paper_id in seen_ranked_ids:
                continue
            seen_ranked_ids.add(paper_id)
            score = _library_match_score(
                query,
                str(getattr(paper, "title", "") or ""),
                str(getattr(paper, "abstract", "") or ""),
                str(getattr(paper, "arxiv_id", "") or ""),
            )
            if score <= 0:
                continue
            ranked.append((score, paper))
        ranked.sort(
            key=lambda item: (
                item[0],
                int((getattr(item[1], "metadata_json", None) or {}).get("citation_count") or 0),
                str(getattr(item[1], "title", "") or "").lower(),
            ),
            reverse=True,
        )
        refs: list[dict[str, Any]] = []
        for index, (score, paper) in enumerate(ranked[:limit], start=1):
            refs.append(
                paper_ref_from_model(
                    paper,
                    ref_id=f"L{index}",
                    source="library_match",
                    match_reason=f"论文库按 `{query[:80]}` 自动匹配，score={score:.2f}",
                    selected=False,
                    project_linked=False,
                    analysis_report=analysis_by_id.get(str(getattr(paper, "id", "") or "")),
                )
            )
            refs[-1]["status"] = "candidate"
        return refs


def _scan_local_workspace_pdf_candidates(
    context: WorkflowContext,
    query: str,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    workspace_path = _resolve_workspace_path(context.run)
    if not workspace_path:
        return []
    if context.run.workspace_server_id:
        try:
            server_entry = get_workspace_server_entry(context.run.workspace_server_id)
            overview = build_remote_overview(server_entry, workspace_path, depth=4, max_entries=200)
        except Exception:
            return []
        refs: list[dict[str, Any]] = []
        for relative_path in overview.get("files") or []:
            normalized = str(relative_path).replace("\\", "/")
            if not normalized.lower().endswith(".pdf"):
                continue
            if not (
                normalized.startswith("papers/")
                or normalized.startswith("literature/")
            ):
                continue
            file_name = Path(normalized).name
            score = _library_match_score(query, file_name, normalized)
            if score <= 0:
                continue
            refs.append(
                workspace_pdf_ref(
                    ref_id=f"W{len(refs) + 1}",
                    path=normalized,
                    title=Path(normalized).stem.replace("_", " "),
                    match_reason=f"远程工作区 PDF 文件名匹配，score={score:.2f}",
                )
            )
            if len(refs) >= limit:
                break
        return refs

    roots: list[Path] = []
    for candidate in [Path(workspace_path) / "papers", Path(workspace_path) / "literature"]:
        if candidate.exists() and candidate.is_dir():
            roots.append(candidate)
    override_path = _resolve_paper_library_override(context)
    if override_path:
        override_root = Path(override_path).expanduser()
        if not override_root.is_absolute():
            override_root = Path(workspace_path) / override_root
        try:
            resolved_override = override_root.resolve()
        except OSError:
            resolved_override = override_root
        if resolved_override.exists() and resolved_override.is_dir():
            roots.append(resolved_override)
    seen_roots: set[str] = set()
    candidate_paths: list[Path] = []
    for root in roots:
        key = str(root.resolve()).lower()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        candidate_paths.extend(sorted(root.rglob("*.pdf"))[: max(limit * 2, 8)])
    ranked: list[tuple[float, Path]] = []
    for pdf_path in candidate_paths:
        title = pdf_path.stem.replace("_", " ")
        score = _library_match_score(query, pdf_path.name, title, str(pdf_path))
        if score <= 0:
            continue
        ranked.append((score, pdf_path))
    ranked.sort(key=lambda item: (item[0], item[1].name.lower()), reverse=True)
    refs: list[dict[str, Any]] = []
    for index, (score, pdf_path) in enumerate(ranked[:limit], start=1):
        refs.append(
            workspace_pdf_ref(
                ref_id=f"W{index}",
                path=str(pdf_path),
                title=pdf_path.stem.replace("_", " "),
                match_reason=f"本地工作区 PDF 文件名匹配，score={score:.2f}",
            )
        )
    return refs


def _collect_arxiv_candidate_refs(
    context: WorkflowContext,
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    sources = _resolve_literature_sources(context)
    if "web" not in sources:
        return []
    try:
        candidates = ArxivClient().search_candidates(query, max_results=limit)
    except Exception as exc:
        logger.info("Skip external arXiv candidate search for %s: %s", context.run.id, exc)
        return []
    refs: list[dict[str, Any]] = []
    for index, paper in enumerate(candidates[:limit], start=1):
        metadata = dict(paper.metadata or {})
        authors = metadata.get("authors") or []
        refs.append(
            external_candidate_ref(
                ref_id=f"E{index}",
                title=paper.title,
                abstract=paper.abstract,
                source="external_arxiv",
                arxiv_id=paper.arxiv_id,
                source_url=f"https://arxiv.org/abs/{paper.arxiv_id}" if paper.arxiv_id else None,
                pdf_url=f"https://arxiv.org/pdf/{paper.arxiv_id}" if paper.arxiv_id else None,
                authors=authors,
                categories=metadata.get("categories") or [],
                publication_date=paper.publication_date.isoformat() if paper.publication_date else None,
                publication_year=paper.publication_date.year if paper.publication_date else None,
                citation_count=int(metadata.get("citation_count") or 0),
                match_reason=f"外部 arXiv 按 `{query[:80]}` 自动检索",
            )
        )
    return refs


def _build_literature_context_blocks(context: WorkflowContext) -> list[tuple[str, str]]:
    query = _resolve_literature_query(context)
    sources = _resolve_literature_sources(context)
    blocks: list[tuple[str, str]] = []

    if "project" in sources:
        blocks.append(
            (
                "Project Paper Index",
                format_ref_index_for_prompt(
                    _paper_index_from_context(context),
                    empty_text="当前项目还没有关联论文。",
                ),
            )
        )

    candidate_refs: list[dict[str, Any]] = []

    if "library" in sources:
        candidate_refs.extend(_collect_library_paper_candidates(context, query))

    if "local" in sources:
        candidate_refs.extend(_scan_local_workspace_pdf_candidates(context, query))

    candidate_refs.extend(_collect_arxiv_candidate_refs(context, query))
    merged_candidates = _persist_literature_candidates(context, candidate_refs)
    if merged_candidates:
        blocks.append(
            (
                "Supplemental Literature Candidate Index",
                format_ref_index_for_prompt(
                    merged_candidates,
                    empty_text="当前没有自动补充候选论文。",
                    include_candidates=True,
                ),
            )
        )

    return blocks


def _build_literature_review_prompt(context: WorkflowContext) -> str:
    repo_blocks = [
        f"- {repo.repo_url} | 本地路径: {repo.local_path or '未克隆'}"
        for repo in context.selected_repos
    ]
    context_blocks = _build_literature_context_blocks(context)
    context_blocks.append(("Code Repositories", "\n".join(repo_blocks) or "当前项目还没有关联仓库。"))

    return _build_aris_guided_prompt(
        context,
        skill_ids=["research-lit"],
        phase_label="Literature Review",
        output_contract=(
            "输出中文 Markdown 文献综述。至少包含：项目背景与研究目标、当前研究脉络、"
            "代表性论文与启发、关键空白与风险、对本项目的下一步建议。"
        ),
        context_blocks=context_blocks,
    )


def _build_idea_landscape_prompt(context: WorkflowContext) -> str:
    repo_blocks = [
        f"- {repo.repo_url} | 本地路径: {repo.local_path or '未克隆'}"
        for repo in context.selected_repos
    ]
    context_blocks = _build_literature_context_blocks(context)
    context_blocks.append(("Repository Context", "\n".join(repo_blocks) or "无关联仓库。"))
    return _build_aris_guided_prompt(
        context,
        skill_ids=["idea-discovery", "research-lit"],
        phase_label="Idea Discovery Phase 1 - Literature Survey",
        output_contract=(
            "输出中文 Markdown landscape summary。至少包含：sub-directions、关键方法脉络、"
            "结构性空白、未来问题、建议的下一阶段聚焦方向。"
        ),
        context_blocks=context_blocks,
    )


def _project_research_wiki_context_block(
    context: WorkflowContext,
    *,
    query: str | None = None,
    limit: int = 6,
) -> tuple[str, str] | None:
    try:
        payload = ResearchWikiService().build_query_pack(
            project_id=context.project.id,
            query=query or context.run.prompt,
            limit=limit,
        )
    except Exception:
        logger.exception("Failed to build research wiki query pack for project %s", context.project.id)
        return None
    query_pack = str(payload.get("query_pack") or "").strip()
    if not query_pack:
        return None
    return ("Project Research Wiki", query_pack)


def _build_idea_generation_prompt(context: WorkflowContext, literature_markdown: str) -> str:
    refs_hint = ", ".join(f"P{index}" for index, _paper in enumerate(context.selected_papers, start=1)) or "无"
    context_blocks: list[tuple[str, str]] = []
    wiki_block = _project_research_wiki_context_block(context, query=context.run.prompt, limit=6)
    if wiki_block is not None:
        context_blocks.append(wiki_block)
    context_blocks.extend(
        [
            ("Landscape Summary", literature_markdown),
            ("Available Paper Refs", refs_hint),
        ]
    )
    return _build_aris_guided_prompt(
        context,
        skill_ids=["idea-discovery", "idea-creator"],
        phase_label="Idea Discovery Phase 2 - Idea Generation, Filtering and Pilots",
        output_contract=(
            "只输出一个 JSON 对象，不要输出 Markdown 代码块。格式为："
            '{"ideas":[{"title":"一句话标题","content":"Markdown 内容，需包含 hypothesis / minimum_experiment / risk / next_step",'
            '"paper_refs":["P1"],"pilot_signal":"POSITIVE|WEAK_POSITIVE|NEGATIVE|SKIPPED","ranking_reason":"排序理由"}]} '
            f"paper_refs 只能从以下引用中选择：{refs_hint}。"
        ),
        context_blocks=context_blocks,
    )


def _build_idea_novelty_verification_prompt(
    context: WorkflowContext,
    literature_markdown: str,
    idea_json_text: str,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["idea-discovery", "novelty-check"],
        phase_label="Idea Discovery Phase 3 - Deep Novelty Verification",
        output_contract=(
            "输出中文 Markdown 深度查新报告。至少包含：每个核心 idea 的 closest prior work、"
            "novelty level、overlap risk、delta、recommendation。"
        ),
        context_blocks=[
            ("Landscape Summary", literature_markdown),
            ("Ranked Ideas JSON", idea_json_text),
        ],
    )


def _build_idea_external_review_prompt(
    context: WorkflowContext,
    idea_json_text: str,
    novelty_markdown: str,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["idea-discovery", "research-review"],
        phase_label="Idea Discovery Phase 4 - External Critical Review",
        output_contract=(
            "输出中文 Markdown reviewer feedback。至少包含：score、主要 objections、"
            "最小可执行改进建议、推荐优先级和是否建议继续。"
        ),
        context_blocks=[
            ("Ranked Ideas JSON", idea_json_text),
            ("Novelty Verification", novelty_markdown),
        ],
    )


def _build_full_pipeline_gate_prompt(context: WorkflowContext) -> str:
    materials = _build_writing_materials(context)
    literature_context = _build_literature_context_blocks(context)
    context_blocks = list(literature_context)
    wiki_block = _project_research_wiki_context_block(context, query=context.run.prompt, limit=8)
    if wiki_block is not None:
        context_blocks.append(wiki_block)
    context_blocks.append(("Project Materials", materials))
    return _build_aris_guided_prompt(
        context,
        skill_ids=["research-pipeline", "idea-discovery", "research-lit", "novelty-check", "research-review"],
        phase_label="Research Pipeline Stage 1 - Idea Discovery (Gate 1)",
        output_contract=(
            "输出完整的中文 Markdown IDEA_REPORT。至少包含：Executive Summary、Literature Landscape、"
            "Ranked Ideas、Pilot/Feasibility Signals、Novelty、Reviewer Feedback、Recommended Idea、Next Steps。"
        ),
        context_blocks=context_blocks,
    )


def _resolve_literature_markdown(context: WorkflowContext, llm_result: LLMResult) -> str:
    content = (llm_result.content or "").strip()
    if content and not _looks_like_llm_error(content):
        return sanitize_project_markdown(content)
    return sanitize_project_markdown(_build_literature_review_fallback(context, content))


def _build_literature_review_fallback(context: WorkflowContext, llm_message: str) -> str:
    project = context.project
    prompt = context.run.prompt.strip()
    lines = [
        f"# {project.name} 文献综述",
        "",
        "## 项目背景与研究目标",
        project.description or "当前项目尚未填写详细描述，建议先补充研究目标、任务定义和预期产出。",
    ]
    if prompt:
        lines.extend(
            [
                "",
                "## 本次工作流关注点",
                prompt,
            ]
        )

    lines.extend(["", "## 当前研究脉络"])
    if context.selected_papers:
        lines.append(
            format_ref_index_for_prompt(
                _paper_index_from_context(context),
                empty_text="当前项目还没有关联论文。",
            )
        )
    else:
        lines.append("- 当前项目还没有关联论文，暂时无法沉淀高质量的证据链。")

    lines.extend(
        [
            "",
            "## 关键空白与风险",
            "- 需要先明确基线论文、数据来源和核心评价指标，否则后续实验设计会偏空泛。",
            "- 建议按 paper_id/ref_id 按需读取核心论文的 skim/deep/三轮分析，避免只依赖题录索引做判断。",
            "- 如果代码仓库尚未整理，建议先确认可运行入口与复现实验脚本。",
            "",
            "## 对本项目的下一步建议",
            "- 从现有关联论文中选 1-2 篇最接近目标的工作作为基线，整理复现差异。",
            "- 围绕本次 prompt 拆出最小实验问题，并把数据、模型、指标写成 checklist。",
            "- 在项目想法区沉淀候选方向，形成“问题 - 方法 - 实验 - 风险”的结构化记录。",
        ]
    )
    if context.selected_repos:
        lines.extend(
            [
                "",
                "## 代码仓库补充",
                *[
                    f"- {repo.repo_url} | 本地路径: {repo.local_path or '未克隆'}"
                    for repo in context.selected_repos
                ],
            ]
        )
    if llm_message and _looks_like_llm_error(llm_message):
        lines.extend(
            [
                "",
                "## 备注",
                f"- 当前未能使用在线模型生成更深入综述，已回退为基于项目上下文的结构化摘要。模型返回: {llm_message}",
            ]
        )
    return "\n".join(lines).strip()


def _build_idea_discovery_prompt(context: WorkflowContext) -> str:
    return _build_idea_generation_prompt(context, _build_literature_review_fallback(context, ""))


def _resolve_idea_payloads(
    context: WorkflowContext,
    llm_result: LLMResult,
) -> list[dict[str, Any]]:
    paper_ref_to_id = {f"P{index}": paper.id for index, paper in enumerate(context.selected_papers, start=1)}
    parsed = llm_result.parsed_json or {}
    raw_items = parsed.get("ideas")
    if not isinstance(raw_items, list):
        if parsed.get("title") or parsed.get("content"):
            raw_items = [parsed]
        else:
            raw_items = []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:3], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or f"{context.project.name} · 想法 {index}"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        pilot_signal = str(item.get("pilot_signal") or "SKIPPED").strip().upper() or "SKIPPED"
        ranking_reason = str(item.get("ranking_reason") or "").strip()
        refs = item.get("paper_refs")
        paper_ids = [
            paper_ref_to_id[ref]
            for ref in refs
            if isinstance(ref, str) and ref in paper_ref_to_id
        ] if isinstance(refs, list) else []
        if not paper_ids and context.selected_papers:
            paper_ids = [context.selected_papers[min(index - 1, len(context.selected_papers) - 1)].id]
        normalized.append(
            {
                "title": title,
                "content": content,
                "paper_ids": paper_ids,
                "pilot_signal": pilot_signal,
                "ranking_reason": ranking_reason,
            }
        )

    if normalized:
        return normalized
    return _build_idea_fallback(context, llm_result.content or "")


def _build_idea_fallback(context: WorkflowContext, llm_message: str) -> list[dict[str, Any]]:
    fallback_ideas: list[dict[str, Any]] = []
    project_name = context.project.name
    prompt = context.run.prompt.strip() or "围绕当前项目做一个可快速验证的研究闭环。"
    paper_ids = [paper.id for paper in context.selected_papers]
    primary_paper = context.selected_papers[0].title if context.selected_papers else "现有项目材料"
    secondary_paper = context.selected_papers[1].title if len(context.selected_papers) > 1 else primary_paper
    repo_hint = context.selected_repos[0].repo_url if context.selected_repos else "当前工作区"
    note_suffix = ""
    if llm_message and _looks_like_llm_error(llm_message):
        note_suffix = f"\n\n> 说明：当前使用了本地回退模板，因为模型返回 `{llm_message}`。"

    fallback_ideas.append(
        {
            "title": f"{project_name} · 基线复现与误差画像",
            "content": (
                "## 问题\n"
                f"围绕 `{primary_paper}` 的最接近方向，先确认当前项目在什么数据与指标上最容易形成可靠基线。\n\n"
                "## 机会\n"
                "通过复现实验和误差案例梳理，可以快速发现模型、数据或评测环节里的真实瓶颈。\n\n"
                "## 最小实验\n"
                f"1. 在 `{repo_hint}` 中整理可运行入口；\n"
                "2. 复现一个最小 baseline；\n"
                "3. 收集 20-50 个失败样本做误差分类。\n\n"
                "## 风险\n"
                "如果没有固定数据集或评测脚本，复现成本会先被工程问题放大。\n\n"
                "## 下一步\n"
                f"把本次关注点“{prompt}”拆成可度量指标，并补齐 baseline checklist。"
                f"{note_suffix}"
            ),
            "paper_ids": paper_ids[:1],
            "pilot_signal": "POSITIVE",
            "ranking_reason": "优先验证最接近现有项目的 baseline 闭环，实施成本最低。",
        }
    )
    fallback_ideas.append(
        {
            "title": f"{project_name} · 跨论文机制拼接验证",
            "content": (
                "## 问题\n"
                f"尝试把 `{primary_paper}` 与 `{secondary_paper}` 中最互补的机制组合起来，验证是否能提升关键指标。\n\n"
                "## 机会\n"
                "这种“轻组合”通常比完全新方法成本低，更适合当前阶段快速筛选方向。\n\n"
                "## 最小实验\n"
                "1. 明确两个候选机制；\n"
                "2. 仅改一个核心模块；\n"
                "3. 在小规模数据上做 ablation。\n\n"
                "## 风险\n"
                "机制之间可能耦合较强，导致收益不明显甚至训练不稳定。\n\n"
                "## 下一步\n"
                "优先定义一套最小对比表，记录组合前后在性能、速度和复杂度上的变化。"
                f"{note_suffix}"
            ),
            "paper_ids": paper_ids[:2] or paper_ids[:1],
            "pilot_signal": "WEAK_POSITIVE",
            "ranking_reason": "组合方向有潜在收益，但需要先做小规模 ablation 验证耦合风险。",
        }
    )
    fallback_ideas.append(
        {
            "title": f"{project_name} · 数据与评测闭环增强",
            "content": (
                "## 问题\n"
                "当前很多研究停在方法层，缺少对数据难例、评测断点和人工分析的闭环设计。\n\n"
                "## 机会\n"
                "如果先把数据分层和评测看板做起来，后面无论换模型还是换方法都能快速比较收益。\n\n"
                "## 最小实验\n"
                "1. 抽取 3 类典型难例；\n"
                "2. 为每类设计单独指标；\n"
                "3. 将实验结果沉淀到项目报告与想法列表。\n\n"
                "## 风险\n"
                "前期需要一些人工整理，但回报是后续每轮实验都更容易定位问题。\n\n"
                "## 下一步\n"
                "先确定一个最值得追踪的失败模式，把它做成固定评测切片。"
                f"{note_suffix}"
            ),
            "paper_ids": paper_ids[:3],
            "pilot_signal": "POSITIVE",
            "ranking_reason": "先补齐评测闭环能稳定提升后续所有方法实验的决策质量。",
        }
    )
    return fallback_ideas[:3]


def _ideas_to_markdown(ideas: list[dict[str, Any]]) -> str:
    parts = ["# 想法发现结果", ""]
    for index, item in enumerate(ideas, start=1):
        parts.append(f"## {index}. {item['title']}")
        parts.append(item["content"].strip())
        parts.append("")
    return "\n".join(parts).strip()


def _markdown_excerpt(markdown: str, limit: int = 220) -> str:
    text = re.sub(r"[#>*`_-]", " ", markdown or "")
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _llm_mode(result: LLMResult) -> str:
    return "fallback" if _looks_like_llm_error(result.content or "") else "llm"


def _looks_like_llm_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in _LLM_ERROR_MARKERS)


def _normalize_id_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _paper_snapshot(paper: Any) -> PaperSnapshot:
    return PaperSnapshot(
        id=str(paper.id),
        title=str(paper.title or ""),
        arxiv_id=str(paper.arxiv_id or ""),
        abstract=str(paper.abstract or ""),
    )


def _repo_snapshot(repo: Any) -> RepoSnapshot:
    return RepoSnapshot(
        id=str(repo.id),
        repo_url=str(repo.repo_url or ""),
        local_path=str(repo.local_path) if repo.local_path else None,
    )


def _ensure_run_orchestration(
    run_id: str,
    context: WorkflowContext,
    *,
    reset_stage_status: bool = False,
) -> None:
    orchestration = build_run_orchestration(
        context.run.workflow_type,
        context.metadata.get("orchestration"),
        target_id=context.run.target_id,
        workspace_server_id=context.run.workspace_server_id,
        reset_stage_status=reset_stage_status,
    )
    _patch_run(
        run_id,
        metadata_updates={
            "orchestration": orchestration,
            "stage_trace": build_stage_trace(
                orchestration,
                existing=context.metadata.get("stage_trace"),
                reset=reset_stage_status,
            ),
        },
    )
    context.metadata["orchestration"] = orchestration
    context.metadata["stage_trace"] = build_stage_trace(
        orchestration,
        existing=context.metadata.get("stage_trace"),
        reset=reset_stage_status,
    )


def _set_stage_state(
    run_id: str,
    stage_id: str,
    *,
    status: str,
    message: str,
    progress_pct: int,
    error: str | None = None,
) -> None:
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            return

        metadata = dict(run.metadata_json or {})
        orchestration = build_run_orchestration(
            run.workflow_type,
            metadata.get("orchestration"),
            target_id=run.target_id,
            workspace_server_id=run.workspace_server_id,
        )
        target_stage_ids = _resolve_stage_alias_ids(run.workflow_type, stage_id)
        now = _iso_now()
        updated_stages: list[dict[str, Any]] = []
        for stage in orchestration.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            stage_copy = dict(stage)
            if str(stage_copy.get("id")) in target_stage_ids:
                stage_copy["status"] = status
            updated_stages.append(stage_copy)
        orchestration["stages"] = updated_stages

        trace = build_stage_trace(orchestration, existing=metadata.get("stage_trace"))
        updated_trace: list[dict[str, Any]] = []
        for item in trace:
            current = dict(item)
            if str(current.get("stage_id")) in target_stage_ids:
                current["status"] = status
                current["message"] = message
                current["progress_pct"] = int(max(0, min(progress_pct, _TOTAL_PROGRESS)))
                current["error"] = error
                if status == "running" and not current.get("started_at"):
                    current["started_at"] = now
                if status in {"completed", "failed", "cancelled"}:
                    current["completed_at"] = now
                    if not current.get("started_at"):
                        current["started_at"] = now
            updated_trace.append(current)

        metadata["orchestration"] = orchestration
        metadata["stage_trace"] = updated_trace
        project_repo.update_run(run_id, metadata=metadata)


def _fail_active_stage(run_id: str, error: str) -> None:
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            return
        stage_id = str(run.active_phase or "").strip()
    if stage_id:
        _set_stage_state(
            run_id,
            stage_id,
            status="failed",
            message=f"阶段失败：{error[:160]}",
            progress_pct=100,
            error=error[:500],
        )


def _cancel_active_stage(run_id: str, reason: str) -> None:
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            return
        stage_id = str(run.active_phase or "").strip()
    if stage_id:
        _set_stage_state(
            run_id,
            stage_id,
            status="cancelled",
            message=reason,
            progress_pct=100,
        )


def _patch_run(
    run_id: str,
    *,
    status: ProjectRunStatus | object = _MISSING,
    active_phase: str | object = _MISSING,
    summary: str | object = _MISSING,
    task_id: str | object = _MISSING,
    result_path: str | None | object = _MISSING,
    started_at: datetime | None | object = _MISSING,
    finished_at: datetime | None | object = _MISSING,
    metadata_updates: dict[str, Any] | None = None,
) -> None:
    notify_event: str | None = None
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        if run is None:
            return
        previous_status = str(run.status)

        payload: dict[str, Any] = {}
        if status is not _MISSING:
            payload["status"] = status
        if active_phase is not _MISSING:
            payload["active_phase"] = active_phase
        if summary is not _MISSING:
            payload["summary"] = summary
        if task_id is not _MISSING:
            payload["task_id"] = task_id
        if result_path is not _MISSING:
            payload["result_path"] = result_path
        if started_at is not _MISSING:
            payload["started_at"] = started_at
        if finished_at is not _MISSING:
            payload["finished_at"] = finished_at
        if metadata_updates is not None:
            metadata = dict(run.metadata_json or {})
            metadata.update(metadata_updates)
            if metadata.get("error") is None:
                metadata.pop("error", None)
            payload["metadata"] = metadata
            if result_path is _MISSING:
                inferred_result_path = _infer_primary_result_path(metadata.get("artifact_refs"))
                if inferred_result_path:
                    payload["result_path"] = inferred_result_path

        if payload:
            project_repo.update_run(run_id, **payload)
            next_status = str(payload.get("status") or previous_status)
            if next_status != previous_status and next_status in {
                ProjectRunStatus.succeeded.value,
                ProjectRunStatus.failed.value,
                ProjectRunStatus.cancelled.value,
            }:
                notify_event = next_status
    if notify_event:
        try:
            from packages.ai.project.notification_service import notify_project_run_status

            notify_project_run_status(run_id, notify_event)
        except Exception:
            logger.exception("failed to send project run notification for %s", run_id)


def _emit_progress(
    progress_callback: ProgressCallback | None,
    message: str,
    current: int,
) -> None:
    if progress_callback is not None:
        progress_callback(message, current, _TOTAL_PROGRESS)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _infer_primary_result_path(value: Any) -> str | None:
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


def _resolve_rebuttal_venue(context: WorkflowContext) -> str:
    venue = (
        str(context.metadata.get("rebuttal_venue") or "").strip()
        or str(context.metadata.get("venue") or "").strip()
        or str(_extract_prompt_directive(context.run.prompt, "venue") or "").strip()
    )
    return venue or "ICML"


def _resolve_rebuttal_character_limit(context: WorkflowContext) -> int:
    raw_value = (
        str(context.metadata.get("rebuttal_character_limit") or "").strip()
        or str(context.metadata.get("character_limit") or "").strip()
        or str(_extract_prompt_directive(context.run.prompt, "character limit") or "").strip()
        or str(_extract_prompt_directive(context.run.prompt, "limit") or "").strip()
    )
    digits = re.sub(r"[^0-9]", "", raw_value)
    if not digits:
        raise RuntimeError("缺少 rebuttal 字符限制。请在运行时填写 character_limit。")
    try:
        value = int(digits)
    except ValueError as exc:
        raise RuntimeError("rebuttal 字符限制格式无效。") from exc
    if value <= 0:
        raise RuntimeError("rebuttal 字符限制必须大于 0。")
    return value


def _resolve_rebuttal_round(context: WorkflowContext) -> str:
    raw_value = (
        str(context.metadata.get("rebuttal_round") or "").strip()
        or str(context.metadata.get("round") or "").strip()
        or str(_extract_prompt_directive(context.run.prompt, "round") or "").strip()
    )
    normalized = raw_value.lower()
    if normalized in {"", "1", "initial", "first"}:
        return "initial"
    if normalized in {"2", "followup", "follow-up", "follow_up", "response"}:
        return "followup"
    return raw_value[:64] or "initial"


def _resolve_rebuttal_quick_mode(context: WorkflowContext) -> bool:
    value = context.metadata.get("rebuttal_quick_mode")
    if value is None:
        value = _extract_prompt_directive(context.run.prompt, "quick mode")
    return bool(value is True or str(value or "").strip().lower() in {"1", "true", "yes", "on"})


def _resolve_rebuttal_review_bundle(context: WorkflowContext) -> str:
    for key in ("rebuttal_review_bundle", "review_bundle", "reviews", "review_text"):
        candidate = str(context.metadata.get(key) or "").strip()
        if candidate:
            return candidate
    for directive_key in ("reviews", "review bundle", "review text"):
        candidate = str(_extract_prompt_directive(context.run.prompt, directive_key) or "").strip()
        if candidate:
            return candidate
    prompt_body = _strip_prompt_directives(context.run.prompt)
    if re.search(r"\breviewer\b", prompt_body, flags=re.IGNORECASE):
        return prompt_body
    raise RuntimeError("缺少审稿意见原文。请在运行时填写 review bundle。")


def _render_rebuttal_reviews_markdown(
    *,
    review_bundle: str,
    venue: str,
    round_label: str,
    character_limit: int,
) -> str:
    return (
        "# REVIEWS_RAW\n\n"
        f"- Venue: `{venue}`\n"
        f"- Round: `{round_label}`\n"
        f"- Character Limit: `{character_limit}`\n\n"
        "## Raw Reviews\n"
        f"{str(review_bundle or '').strip()}\n"
    )


def _format_rebuttal_state_markdown(
    *,
    venue: str,
    round_label: str,
    character_limit: int,
    quick_mode: bool,
    current_phase: str,
    status: str,
    character_count: int | None = None,
) -> str:
    lines = [
        "# REBUTTAL_STATE",
        "",
        f"- Status: `{status}`",
        f"- Current Phase: `{current_phase}`",
        f"- Venue: `{venue}`",
        f"- Round: `{round_label}`",
        f"- Character Limit: `{character_limit}`",
        f"- Quick Mode: `{str(bool(quick_mode)).lower()}`",
    ]
    if character_count is not None:
        lines.append(f"- Character Count: `{character_count}`")
    return "\n".join(lines).strip() + "\n"


def _fallback_rebuttal_issue_board(review_bundle: str) -> str:
    anchor = str(review_bundle or "").strip().splitlines()
    anchor_text = next((line.strip() for line in anchor if line.strip()), "Reviewer concerns pending manual normalization.")
    return (
        "# ISSUE_BOARD\n\n"
        "## R1-C1\n"
        f"- raw_anchor: {anchor_text[:180]}\n"
        "- issue_type: empirical_support\n"
        "- severity: major\n"
        "- reviewer_stance: swing\n"
        "- response_mode: grounded_evidence\n"
        "- status: open\n"
    )


def _fallback_rebuttal_strategy(issue_board_markdown: str, venue: str, character_limit: int) -> str:
    return (
        "# STRATEGY_PLAN\n\n"
        "## Global Themes\n"
        "- 先明确回答 reviewer 的核心质疑，再补 grounding evidence 与边界说明。\n\n"
        "## Character Budget\n"
        f"- Venue: {venue}\n"
        f"- Limit: {character_limit}\n"
        "- Opener: 10%\n"
        "- Reviewer Responses: 80%\n"
        "- Closing: 10%\n\n"
        "## Priority Issues\n"
        f"{issue_board_markdown[:1200].strip()}\n"
    )


def _fallback_rebuttal_draft(strategy_markdown: str, venue: str, character_limit: int) -> str:
    return (
        "# REBUTTAL_DRAFT\n\n"
        f"Venue: {venue}\n\n"
        "## Opening\n"
        "We thank the reviewers for the detailed feedback and address the main concerns below.\n\n"
        "## Reviewer Responses\n"
        "- We clarify the core claim, supporting evidence, and current scope.\n"
        "- We avoid unsupported commitments and explicitly mark future work when needed.\n\n"
        "## Closing\n"
        f"The response is organized to stay within the {character_limit}-character budget.\n\n"
        "## Strategy Alignment\n"
        f"{strategy_markdown[:1400].strip()}\n"
    )


def _fallback_rebuttal_stress(draft_markdown: str) -> str:
    return (
        "# MCP_STRESS_TEST\n\n"
        "## Verdict\n"
        "- needs revision\n\n"
        "## Risks\n"
        "- 需要进一步确认每条 reviewer concern 都有显式回答。\n"
        "- 避免任何未验证实验或未批准承诺。\n\n"
        "## Draft Snapshot\n"
        f"{draft_markdown[:1000].strip()}\n"
    )


def _build_rebuttal_issue_board_prompt(
    context: WorkflowContext,
    *,
    materials: str,
    normalize_markdown: str,
    venue: str,
    round_label: str,
    character_limit: int,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["rebuttal"],
        phase_label="Rebuttal Phase 2 - Issue Board",
        output_contract=(
            "输出中文 Markdown ISSUE_BOARD。按 atomic issue 组织，至少包含：issue_id、reviewer、raw_anchor、"
            "issue_type、severity、reviewer_stance、response_mode、status。"
        ),
        context_blocks=[
            ("Project Materials", materials),
            ("Venue Rules", f"Venue: {venue}\nRound: {round_label}\nCharacter Limit: {character_limit}"),
            ("Raw Reviews", normalize_markdown),
        ],
    )


def _build_rebuttal_strategy_prompt(
    context: WorkflowContext,
    *,
    materials: str,
    normalize_markdown: str,
    issue_board_markdown: str,
    venue: str,
    round_label: str,
    character_limit: int,
    quick_mode: bool,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["rebuttal"],
        phase_label="Rebuttal Phase 3 - Strategy Plan",
        output_contract=(
            "输出中文 Markdown STRATEGY_PLAN。至少包含：Global Themes、Per-Issue Response Mode、"
            "Character Budget、Blocked Claims、Open Risks。明确 quick mode 是否只停留在策略层。"
        ),
        context_blocks=[
            ("Project Materials", materials),
            ("Venue Rules", f"Venue: {venue}\nRound: {round_label}\nCharacter Limit: {character_limit}\nQuick Mode: {quick_mode}"),
            ("Raw Reviews", normalize_markdown),
            ("Issue Board", issue_board_markdown),
        ],
    )


def _build_rebuttal_draft_prompt(
    context: WorkflowContext,
    *,
    materials: str,
    issue_board_markdown: str,
    strategy_markdown: str,
    venue: str,
    round_label: str,
    character_limit: int,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["rebuttal"],
        phase_label="Rebuttal Phase 4 - Draft",
        output_contract=(
            "输出 grounded rebuttal 初稿。必须包含：简短 opener、per-reviewer numbered responses、closing。"
            "不要捏造实验、数字或承诺；超出证据范围的内容用 narrow concession 或 future work boundary 表达。"
        ),
        context_blocks=[
            ("Project Materials", materials),
            ("Venue Rules", f"Venue: {venue}\nRound: {round_label}\nCharacter Limit: {character_limit}"),
            ("Issue Board", issue_board_markdown),
            ("Strategy Plan", strategy_markdown),
        ],
    )


def _build_rebuttal_stress_prompt(
    context: WorkflowContext,
    *,
    issue_board_markdown: str,
    strategy_markdown: str,
    draft_markdown: str,
    venue: str,
    character_limit: int,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["rebuttal"],
        phase_label="Rebuttal Phase 6 - Stress Test",
        output_contract=(
            "输出中文 Markdown stress test。至少包含：Verdict、Unanswered Concerns、Unsupported Claims、"
            "Tone Risks、Minimal Fixes。不得发明新证据。"
        ),
        context_blocks=[
            ("Venue Rules", f"Venue: {venue}\nCharacter Limit: {character_limit}"),
            ("Issue Board", issue_board_markdown),
            ("Strategy Plan", strategy_markdown),
            ("Draft", draft_markdown),
        ],
    )


def _build_rebuttal_finalize_prompt(
    context: WorkflowContext,
    *,
    issue_board_markdown: str,
    strategy_markdown: str,
    draft_markdown: str,
    stress_markdown: str,
    venue: str,
    round_label: str,
    character_limit: int,
) -> str:
    return _build_aris_guided_prompt(
        context,
        skill_ids=["rebuttal"],
        phase_label="Rebuttal Phase 7 - Finalize",
        output_contract=(
            "输出最终 rich rebuttal Markdown。要求覆盖所有 issue，并根据 stress test 修正 unsupported claims、"
            "over-commitment 和 tone 问题。"
        ),
        context_blocks=[
            ("Venue Rules", f"Venue: {venue}\nRound: {round_label}\nCharacter Limit: {character_limit}"),
            ("Issue Board", issue_board_markdown),
            ("Strategy Plan", strategy_markdown),
            ("Draft", draft_markdown),
            ("Stress Test", stress_markdown),
        ],
    )


def _markdown_to_plain_text(markdown: str) -> str:
    text = str(markdown or "")
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fit_character_limit(text: str, character_limit: int) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if character_limit <= 0 or len(normalized) <= character_limit:
        return normalized
    return normalized[:character_limit].rstrip()

