from __future__ import annotations

from copy import deepcopy
from typing import Any

from packages.ai.project.amadeus_compat import get_amadeus_workflow_config
from packages.domain.enums import ProjectRunActionType, ProjectWorkflowType


def _workflow_label(workflow_type: ProjectWorkflowType) -> str:
    compat = get_amadeus_workflow_config(workflow_type)
    label = str(compat.get("label") or "").strip()
    return label or workflow_type.value.replace("_", " ").strip() or "Workflow"


def _item(
    item_id: str,
    label: str,
    action_type: ProjectRunActionType,
    workflow_type: ProjectWorkflowType,
    *,
    command: str | None = None,
    source_skill: str | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "action_type": action_type.value,
        "workflow_type": workflow_type.value,
        "workflow_label": _workflow_label(workflow_type),
        "command": str(command or "").strip() or None,
        "source_skill": str(source_skill or "").strip() or None,
    }


_GENERIC_ACTIONS: list[dict[str, Any]] = [
    _item(
        "continue",
        "继续到下一步",
        ProjectRunActionType.continue_run,
        ProjectWorkflowType.custom_run,
    ),
    _item(
        "experiment_bridge",
        "实验桥接",
        ProjectRunActionType.run_experiment,
        ProjectWorkflowType.run_experiment,
        command="/experiment-bridge",
        source_skill="experiment-bridge",
    ),
    _item(
        "auto_review_loop",
        "自动评审循环",
        ProjectRunActionType.review,
        ProjectWorkflowType.auto_review_loop,
        command="/auto-review-loop",
        source_skill="auto-review-loop",
    ),
    _item(
        "monitor_experiment",
        "监控实验",
        ProjectRunActionType.monitor,
        ProjectWorkflowType.monitor_experiment,
        command="/monitor-experiment",
        source_skill="monitor-experiment",
    ),
    _item(
        "retry",
        "重新运行",
        ProjectRunActionType.retry,
        ProjectWorkflowType.custom_run,
    ),
    _item(
        "sync_workspace",
        "同步工作区",
        ProjectRunActionType.sync_workspace,
        ProjectWorkflowType.sync_workspace,
        command="/sync-workspace",
        source_skill="sync-workspace",
    ),
    _item(
        "custom_run",
        "自定义流程",
        ProjectRunActionType.custom,
        ProjectWorkflowType.custom_run,
    ),
]


_FOLLOWUP_ACTIONS: dict[str, list[dict[str, Any]]] = {
    ProjectWorkflowType.literature_review.value: [
        _item(
            "idea_discovery",
            "想法发现",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.idea_discovery,
            command="/idea-discovery",
            source_skill="idea-discovery",
        ),
        _item(
            "research_review",
            "研究评审",
            ProjectRunActionType.review,
            ProjectWorkflowType.research_review,
            command="/research-review",
            source_skill="research-review",
        ),
        _item(
            "novelty_check",
            "查新评估",
            ProjectRunActionType.review,
            ProjectWorkflowType.novelty_check,
            command="/novelty-check",
            source_skill="novelty-check",
        ),
    ],
    ProjectWorkflowType.idea_discovery.value: [
        _item(
            "experiment_bridge",
            "实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
        _item(
            "paper_plan",
            "论文规划",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_plan,
            command="/paper-plan",
            source_skill="paper-plan",
        ),
    ],
    ProjectWorkflowType.novelty_check.value: [
        _item(
            "idea_discovery",
            "返回想法发现",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.idea_discovery,
            command="/idea-discovery",
            source_skill="idea-discovery",
        ),
        _item(
            "experiment_bridge",
            "实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
        _item(
            "research_review",
            "研究评审",
            ProjectRunActionType.review,
            ProjectWorkflowType.research_review,
            command="/research-review",
            source_skill="research-review",
        ),
    ],
    ProjectWorkflowType.research_review.value: [
        _item(
            "experiment_bridge",
            "实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
        _item(
            "paper_plan",
            "论文规划",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_plan,
            command="/paper-plan",
            source_skill="paper-plan",
        ),
    ],
    ProjectWorkflowType.run_experiment.value: [
        _item(
            "experiment_audit",
            "实验审计",
            ProjectRunActionType.review,
            ProjectWorkflowType.experiment_audit,
            command="/experiment-audit",
            source_skill="experiment-audit",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
        _item(
            "monitor_experiment",
            "监控实验",
            ProjectRunActionType.monitor,
            ProjectWorkflowType.monitor_experiment,
            command="/monitor-experiment",
            source_skill="monitor-experiment",
        ),
        _item(
            "sync_workspace",
            "同步工作区",
            ProjectRunActionType.sync_workspace,
            ProjectWorkflowType.sync_workspace,
            command="/sync-workspace",
            source_skill="sync-workspace",
        ),
    ],
    ProjectWorkflowType.experiment_audit.value: [
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
        _item(
            "paper_writing",
            "论文写作",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_writing,
            command="/paper-writing",
            source_skill="paper-writing",
        ),
        _item(
            "experiment_bridge",
            "返回实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
    ],
    ProjectWorkflowType.auto_review_loop.value: [
        _item(
            "experiment_bridge",
            "补充实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
        _item(
            "paper_writing",
            "论文写作",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_writing,
            command="/paper-writing",
            source_skill="paper-writing",
        ),
        _item(
            "paper_plan",
            "论文规划",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_plan,
            command="/paper-plan",
            source_skill="paper-plan",
        ),
    ],
    ProjectWorkflowType.paper_plan.value: [
        _item(
            "paper_figure",
            "图表规划",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_figure,
            command="/paper-figure",
            source_skill="paper-figure",
        ),
        _item(
            "paper_write",
            "论文成稿",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_write,
            command="/paper-write",
            source_skill="paper-write",
        ),
    ],
    ProjectWorkflowType.paper_figure.value: [
        _item(
            "paper_write",
            "论文成稿",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_write,
            command="/paper-write",
            source_skill="paper-write",
        ),
        _item(
            "paper_compile",
            "编译稿件",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_compile,
            command="/paper-compile",
            source_skill="paper-compile",
        ),
    ],
    ProjectWorkflowType.paper_write.value: [
        _item(
            "paper_compile",
            "编译稿件",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_compile,
            command="/paper-compile",
            source_skill="paper-compile",
        ),
        _item(
            "paper_improvement",
            "自动论文改进",
            ProjectRunActionType.review,
            ProjectWorkflowType.paper_improvement,
            command="/auto-paper-improvement-loop",
            source_skill="auto-paper-improvement-loop",
        ),
    ],
    ProjectWorkflowType.paper_compile.value: [
        _item(
            "paper_improvement",
            "自动论文改进",
            ProjectRunActionType.review,
            ProjectWorkflowType.paper_improvement,
            command="/auto-paper-improvement-loop",
            source_skill="auto-paper-improvement-loop",
        ),
        _item(
            "paper_write",
            "返回论文成稿",
            ProjectRunActionType.retry,
            ProjectWorkflowType.paper_write,
            command="/paper-write",
            source_skill="paper-write",
        ),
    ],
    ProjectWorkflowType.paper_writing.value: [
        _item(
            "paper_improvement",
            "自动论文改进",
            ProjectRunActionType.review,
            ProjectWorkflowType.paper_improvement,
            command="/auto-paper-improvement-loop",
            source_skill="auto-paper-improvement-loop",
        ),
        _item(
            "paper_compile",
            "编译稿件",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_compile,
            command="/paper-compile",
            source_skill="paper-compile",
        ),
    ],
    ProjectWorkflowType.paper_improvement.value: [
        _item(
            "paper_compile",
            "重新编译稿件",
            ProjectRunActionType.retry,
            ProjectWorkflowType.paper_compile,
            command="/paper-compile",
            source_skill="paper-compile",
        ),
        _item(
            "paper_write",
            "返回论文成稿",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_write,
            command="/paper-write",
            source_skill="paper-write",
        ),
    ],
    ProjectWorkflowType.full_pipeline.value: [
        _item(
            "paper_writing",
            "论文写作",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_writing,
            command="/paper-writing",
            source_skill="paper-writing",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
    ],
    ProjectWorkflowType.monitor_experiment.value: [
        _item(
            "experiment_audit",
            "实验审计",
            ProjectRunActionType.review,
            ProjectWorkflowType.experiment_audit,
            command="/experiment-audit",
            source_skill="experiment-audit",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
        _item(
            "sync_workspace",
            "同步工作区",
            ProjectRunActionType.sync_workspace,
            ProjectWorkflowType.sync_workspace,
            command="/sync-workspace",
            source_skill="sync-workspace",
        ),
    ],
    ProjectWorkflowType.sync_workspace.value: [
        _item(
            "experiment_bridge",
            "实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
    ],
    ProjectWorkflowType.custom_run.value: [
        _item(
            "experiment_bridge",
            "实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
        _item(
            "paper_writing",
            "论文写作",
            ProjectRunActionType.continue_run,
            ProjectWorkflowType.paper_writing,
            command="/paper-writing",
            source_skill="paper-writing",
        ),
    ],
    ProjectWorkflowType.init_repo.value: [
        _item(
            "experiment_bridge",
            "实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
    ],
    ProjectWorkflowType.autoresearch_claude_code.value: [
        _item(
            "auto_review_loop",
            "自动评审循环",
            ProjectRunActionType.review,
            ProjectWorkflowType.auto_review_loop,
            command="/auto-review-loop",
            source_skill="auto-review-loop",
        ),
        _item(
            "experiment_bridge",
            "实验桥接",
            ProjectRunActionType.run_experiment,
            ProjectWorkflowType.run_experiment,
            command="/experiment-bridge",
            source_skill="experiment-bridge",
        ),
    ],
}


def list_followup_actions(
    parent_workflow_type: ProjectWorkflowType | str | None,
) -> list[dict[str, Any]]:
    key = str(
        parent_workflow_type.value
        if isinstance(parent_workflow_type, ProjectWorkflowType)
        else parent_workflow_type or ""
    ).strip()
    items = _FOLLOWUP_ACTIONS.get(key)
    if not items:
        return deepcopy(_GENERIC_ACTIONS)
    suggestions = deepcopy(items)
    suggestions.append(deepcopy(_GENERIC_ACTIONS[-1]))
    return suggestions


def resolve_followup_action(
    parent_workflow_type: ProjectWorkflowType | str,
    action_type: ProjectRunActionType | str,
    *,
    workflow_type: ProjectWorkflowType | str | None = None,
) -> dict[str, Any]:
    parent = ProjectWorkflowType(
        str(
            parent_workflow_type.value
            if isinstance(parent_workflow_type, ProjectWorkflowType)
            else parent_workflow_type
        )
    )
    action = ProjectRunActionType(
        str(action_type.value if isinstance(action_type, ProjectRunActionType) else action_type)
    )
    desired_workflow = (
        ProjectWorkflowType(
            str(
                workflow_type.value
                if isinstance(workflow_type, ProjectWorkflowType)
                else workflow_type
            )
        )
        if workflow_type
        else None
    )

    if action == ProjectRunActionType.retry:
        workflow = desired_workflow or parent
        compat = get_amadeus_workflow_config(workflow)
        return {
            "id": f"retry_{workflow.value}",
            "label": f"重新运行 {str(compat.get('label') or _workflow_label(workflow))}",
            "action_type": action.value,
            "workflow_type": workflow.value,
            "workflow_label": str(compat.get("label") or _workflow_label(workflow)),
            "command": None,
            "source_skill": compat.get("assistant_skill_id"),
        }

    if action == ProjectRunActionType.custom:
        workflow = desired_workflow or parent
        compat = get_amadeus_workflow_config(workflow)
        return {
            "id": f"custom_{workflow.value}",
            "label": "自定义流程",
            "action_type": action.value,
            "workflow_type": workflow.value,
            "workflow_label": str(compat.get("label") or _workflow_label(workflow)),
            "command": None,
            "source_skill": compat.get("assistant_skill_id"),
        }

    suggestions = list_followup_actions(parent)
    if desired_workflow is not None:
        for item in suggestions:
            if str(item.get("workflow_type") or "").strip() == desired_workflow.value:
                return item

    for item in suggestions:
        if str(item.get("action_type") or "").strip() == action.value:
            return item

    for item in _GENERIC_ACTIONS:
        if str(item.get("action_type") or "").strip() == action.value:
            return deepcopy(item)

    raise ValueError(
        f"no project follow-up workflow mapping for parent workflow {parent.value} and action {action.value}"
    )
