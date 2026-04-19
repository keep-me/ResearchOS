from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any

from packages.ai.project.aris_skill_templates import render_aris_skill_reference
from packages.domain.enums import ProjectRunActionType, ProjectWorkflowType

_WORKFLOW_COMPAT: dict[str, dict[str, Any]] = {
    ProjectWorkflowType.init_repo.value: {
        "label": "初始化仓库",
        "prefill_prompt": "为以下研究任务初始化新的 ResearchOS 项目仓库：",
        "runner_preamble": "",
        "assistant_skill_id": None,
        "workspace_skill": False,
    },
    ProjectWorkflowType.literature_review.value: {
        "label": "文献综述",
        "prefill_prompt": "围绕以下主题梳理文献与相关工作：",
        "runner_preamble": (
            "You are the ResearchOS research assistant performing a literature review. "
            "Survey the relevant field, identify key papers, trends, and gaps. "
            "Provide a structured summary."
        ),
        "assistant_skill_id": "research-lit",
        "workspace_skill": False,
    },
    ProjectWorkflowType.idea_discovery.value: {
        "label": "想法发现",
        "prefill_prompt": "围绕以下方向发掘有潜力的研究想法：",
        "runner_preamble": (
            "You are the ResearchOS research assistant performing idea discovery. "
            "Analyze the current state of the field and propose novel, actionable "
            "research ideas with clear motivation and feasibility assessment."
        ),
        "assistant_skill_id": "idea-discovery",
        "workspace_skill": True,
    },
    ProjectWorkflowType.novelty_check.value: {
        "label": "查新评估",
        "prefill_prompt": "围绕当前研究方向执行 novelty check：",
        "runner_preamble": (
            "You are the ResearchOS research assistant performing a novelty check. "
            "Compare the proposed contribution against closely related work, "
            "identify overlap risks, and highlight the strongest differentiators."
        ),
        "assistant_skill_id": "novelty-check",
        "workspace_skill": False,
    },
    ProjectWorkflowType.research_review.value: {
        "label": "研究评审",
        "prefill_prompt": "以审稿人视角评审以下研究任务：",
        "runner_preamble": (
            "You are the ResearchOS research assistant acting as a rigorous reviewer. "
            "Assess novelty, technical soundness, empirical evidence, and writing clarity."
        ),
        "assistant_skill_id": "research-review",
        "workspace_skill": False,
    },
    ProjectWorkflowType.run_experiment.value: {
        "label": "实验桥接",
        "prefill_prompt": "围绕当前实验计划执行 experiment-bridge：",
        "runner_preamble": (
            "You are the ResearchOS research assistant performing the experiment-bridge workflow. "
            "Implement the planned experiment, run a sanity check, deploy the execution, "
            "collect initial results, and prepare the handoff into auto-review-loop."
        ),
        "assistant_skill_id": "experiment-bridge",
        "workspace_skill": False,
    },
    ProjectWorkflowType.experiment_audit.value: {
        "label": "实验审计",
        "prefill_prompt": "围绕当前实验结果执行 experiment-audit：",
        "runner_preamble": (
            "You are the ResearchOS research assistant performing an experiment integrity audit. "
            "Collect experiment artifacts, inspect evaluation logic and results, and produce "
            "an integrity report that flags provenance, score normalization, result existence, "
            "dead code, scope, and evaluation type issues."
        ),
        "assistant_skill_id": "experiment-audit",
        "workspace_skill": False,
    },
    ProjectWorkflowType.auto_review_loop.value: {
        "label": "自动评审循环",
        "prefill_prompt": "围绕该研究方向启动一次自主评审循环：",
        "runner_preamble": (
            "You are the ResearchOS research assistant performing an iterative self-review loop. "
            "Execute the task, review your own output critically, and iterate to improve quality."
        ),
        "assistant_skill_id": "auto-review-loop",
        "workspace_skill": True,
    },
    ProjectWorkflowType.paper_plan.value: {
        "label": "论文规划",
        "prefill_prompt": "围绕当前项目先规划论文结构：",
        "runner_preamble": (
            "You are the ResearchOS research assistant planning a paper. "
            "Design the paper outline, section logic, and evidence checklist."
        ),
        "assistant_skill_id": "paper-plan",
        "workspace_skill": True,
    },
    ProjectWorkflowType.paper_figure.value: {
        "label": "图表规划",
        "prefill_prompt": "围绕当前研究内容规划论文图表：",
        "runner_preamble": (
            "You are the ResearchOS research assistant planning figures and tables for a paper. "
            "Map findings to visual artifacts and draft figure captions."
        ),
        "assistant_skill_id": "paper-figure",
        "workspace_skill": True,
    },
    ProjectWorkflowType.paper_write.value: {
        "label": "论文成稿",
        "prefill_prompt": "基于当前材料生成论文正文初稿：",
        "runner_preamble": (
            "You are the ResearchOS research assistant writing the main manuscript. "
            "Turn the current plan, evidence, and figures into a paper draft."
        ),
        "assistant_skill_id": "paper-write",
        "workspace_skill": True,
    },
    ProjectWorkflowType.paper_compile.value: {
        "label": "编译稿件",
        "prefill_prompt": "编译当前论文稿件并汇总结果：",
        "runner_preamble": (
            "You are the ResearchOS research assistant compiling the manuscript. "
            "Run the compile step, collect logs, and summarize the result."
        ),
        "assistant_skill_id": "paper-compile",
        "workspace_skill": True,
    },
    ProjectWorkflowType.paper_writing.value: {
        "label": "论文写作",
        "prefill_prompt": "将当前研究内容整理为论文草稿：",
        "runner_preamble": (
            "You are the ResearchOS research assistant writing a paper draft. "
            "Organize findings into sections (intro, related work, method, experiments, conclusion) "
            "with proper academic tone."
        ),
        "assistant_skill_id": "paper-writing",
        "workspace_skill": True,
    },
    ProjectWorkflowType.rebuttal.value: {
        "label": "Rebuttal",
        "prefill_prompt": "围绕以下论文与审稿意见准备 rebuttal：",
        "runner_preamble": (
            "You are the ResearchOS research assistant preparing a submission rebuttal. "
            "Parse the reviews, build an issue board and strategy plan, then draft a grounded, "
            "venue-compliant rebuttal under the provided character limit."
        ),
        "assistant_skill_id": "rebuttal",
        "workspace_skill": True,
    },
    ProjectWorkflowType.paper_improvement.value: {
        "label": "论文改进",
        "prefill_prompt": "在远程项目工作区中改进当前论文草稿：",
        "runner_preamble": (
            "You are the ResearchOS research assistant improving an existing paper. "
            "Read the current draft, identify weaknesses, and make targeted improvements."
        ),
        "assistant_skill_id": "auto-paper-improvement-loop",
        "workspace_skill": False,
    },
    ProjectWorkflowType.full_pipeline.value: {
        "label": "科研流程",
        "prefill_prompt": "围绕以下任务运行完整的科研流程：",
        "runner_preamble": (
            "You are the ResearchOS research assistant running a full research pipeline. "
            "Plan the research, implement experiments, analyze results, and document findings."
        ),
        "assistant_skill_id": "research-pipeline",
        "workspace_skill": True,
    },
    ProjectWorkflowType.monitor_experiment.value: {
        "label": "监控实验",
        "prefill_prompt": "监控当前实验并汇总以下任务的进展：",
        "runner_preamble": (
            "You are the ResearchOS research assistant monitoring a running experiment. "
            "Check status, report progress, and flag any issues."
        ),
        "assistant_skill_id": "monitor-experiment",
        "workspace_skill": False,
    },
    ProjectWorkflowType.sync_workspace.value: {
        "label": "同步工作区",
        "prefill_prompt": "围绕以下任务同步本地与远程项目文件（代码、资源、论文）：",
        "runner_preamble": (
            "You are the ResearchOS research assistant synchronizing local and remote project files. "
            "Compare the two workspaces, describe the diff clearly, then produce a safe sync plan."
        ),
        "assistant_skill_id": "sync-workspace",
        "workspace_skill": False,
    },
    ProjectWorkflowType.custom_run.value: {
        "label": "自定义运行",
        "prefill_prompt": "在所选项目目标上运行以下自定义工作流：",
        "runner_preamble": "",
        "assistant_skill_id": None,
        "workspace_skill": False,
    },
    ProjectWorkflowType.autoresearch_claude_code.value: {
        "label": "自动研究（Claude Code）",
        "prefill_prompt": "在当前项目工作区中运行一轮 autoresearch-claude-code 迭代：",
        "runner_preamble": (
            "You are an AutoResearch orchestration assistant. "
            "Bootstrap the session, run a minimal baseline, and propose the next measurable iterations."
        ),
        "assistant_skill_id": "autoresearch-claude-code",
        "workspace_skill": False,
    },
}

_ACTION_LABELS: dict[str, str] = {
    ProjectRunActionType.continue_run.value: "继续执行",
    ProjectRunActionType.run_experiment.value: "实验桥接",
    ProjectRunActionType.monitor.value: "监控进度",
    ProjectRunActionType.review.value: "评审修订",
    ProjectRunActionType.retry.value: "重新运行",
    ProjectRunActionType.sync_workspace.value: "同步工作区",
    ProjectRunActionType.custom.value: "自定义动作",
}


def get_amadeus_workflow_config(workflow_type: ProjectWorkflowType | str) -> dict[str, Any]:
    key = str(workflow_type.value if isinstance(workflow_type, ProjectWorkflowType) else workflow_type)
    return dict(_WORKFLOW_COMPAT.get(key) or {})


def apply_amadeus_workflow_defaults(preset: dict[str, Any]) -> dict[str, Any]:
    payload = dict(preset or {})
    compat = get_amadeus_workflow_config(str(payload.get("workflow_type") or ""))
    if not compat:
        return payload
    payload["label"] = compat.get("label") or payload.get("label")
    payload["prefill_prompt"] = compat.get("prefill_prompt") or payload.get("prefill_prompt")
    payload["assistant_skill_id"] = compat.get("assistant_skill_id")
    payload["workspace_skill"] = bool(compat.get("workspace_skill"))
    payload["runner_preamble"] = workflow_runner_preamble(str(payload.get("workflow_type") or ""))
    return payload


def workflow_runner_preamble(workflow_type: ProjectWorkflowType | str) -> str:
    compat = get_amadeus_workflow_config(workflow_type)
    skill_id = str(compat.get("assistant_skill_id") or "").strip()
    if skill_id:
        rendered = render_aris_skill_reference(skill_id)
        if rendered:
            return rendered
    return str(compat.get("runner_preamble") or "").strip()


def workflow_assistant_skill_id(workflow_type: ProjectWorkflowType | str) -> str | None:
    value = get_amadeus_workflow_config(workflow_type).get("assistant_skill_id")
    text = str(value or "").strip()
    return text or None


def workflow_is_workspace_skill(workflow_type: ProjectWorkflowType | str) -> bool:
    return bool(get_amadeus_workflow_config(workflow_type).get("workspace_skill"))


def amadeus_action_label(action_type: ProjectRunActionType | str) -> str:
    key = str(action_type.value if isinstance(action_type, ProjectRunActionType) else action_type)
    return _ACTION_LABELS.get(key, key.replace("_", " ").strip() or "动作")


def infer_sync_strategy(
    *,
    project_workspace: str | None,
    project_workspace_server_id: str | None = None,
    target_workspace: str | None,
    workspace_server_id: str | None,
    target_workspace_server_id: str | None = None,
) -> str:
    project_path = str(project_workspace or "").strip()
    target_path = str(target_workspace or "").strip()
    source_server_id = str(project_workspace_server_id or "").strip()
    resolved_target_server_id = str(target_workspace_server_id or workspace_server_id or "").strip()
    source_remote = bool(source_server_id)
    target_remote = bool(resolved_target_server_id)
    if source_remote and target_remote and project_path and target_path and project_path != target_path:
        if source_server_id == resolved_target_server_id:
            return "remote_overlay_copy"
        return "remote_bridge_copy"
    if source_remote and not target_remote and project_path and target_path and project_path != target_path:
        return "remote_download"
    if not source_remote and target_remote and project_path and target_path and project_path != target_path:
        return "incremental_rsync"
    if target_remote and target_path:
        return "remote_workspace_only"
    if project_path and target_path and project_path != target_path:
        return "incremental_copy"
    return "project_workspace"


def describe_sync_strategy(sync_strategy: str) -> str:
    if sync_strategy == "remote_overlay_copy":
        return "同步方案：在同一台 SSH 服务器上直接复制项目工作区到目标目录。"
    if sync_strategy == "remote_bridge_copy":
        return "同步方案：通过 ResearchOS 桥接，将远程项目工作区复制到另一台 SSH 服务器。"
    if sync_strategy == "remote_download":
        return "同步方案：从远程项目工作区拉取文件到当前本地目标目录。"
    if sync_strategy == "incremental_rsync":
        return "同步方案：将项目工作区的增量变更 rsync 到当前选定目标。"
    if sync_strategy == "remote_workspace_only":
        return "同步方案：直接使用现有远程工作区。"
    if sync_strategy == "incremental_copy":
        return "同步方案：复用当前项目工作区，并在需要时只复制发生变化的文件。"
    return "同步方案：直接使用项目工作区。"


def build_run_directory(workspace_path: str | None, run_id: str, *, remote: bool) -> str | None:
    base = str(workspace_path or "").strip()
    if not base:
        return None
    if remote:
        normalized = base.rstrip("/\\")
        return posixpath.join(normalized, ".auto-researcher", "aris-runs", str(run_id))
    root = Path(base).expanduser()
    if not root.is_absolute():
        root = root.resolve()
    return str(root / ".auto-researcher" / "aris-runs" / str(run_id))


def build_run_log_path(run_directory: str | None, *, remote: bool) -> str | None:
    base = str(run_directory or "").strip()
    if not base:
        return None
    if remote:
        return posixpath.join(base.rstrip("/\\"), "run.log")
    return str(Path(base) / "run.log")


def build_run_workspace_path(run_directory: str | None, *, remote: bool) -> str | None:
    base = str(run_directory or "").strip()
    if not base:
        return None
    if remote:
        return posixpath.join(base.rstrip("/\\"), "workspace")
    return str(Path(base) / "workspace")


def build_remote_session_name(run_id: str, *, prefix: str = "aris-run") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "", str(run_id or "").lower())
    suffix = normalized[:12] or "session"
    return f"{prefix}-{suffix}"


def build_action_result_path(run_directory: str | None, action_id: str, *, remote: bool) -> str | None:
    base = str(run_directory or "").strip()
    if not base:
        return None
    if remote:
        return posixpath.join(base.rstrip("/\\"), "actions", f"{action_id}.md")
    return str(Path(base) / "actions" / f"{action_id}.md")


def build_action_log_path(run_directory: str | None, action_id: str, *, remote: bool) -> str | None:
    base = str(run_directory or "").strip()
    if not base:
        return None
    if remote:
        return posixpath.join(base.rstrip("/\\"), "actions", f"{action_id}.log")
    return str(Path(base) / "actions" / f"{action_id}.log")
