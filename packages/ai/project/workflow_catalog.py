"""Project workflow catalog for ResearchOS project workflows."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from packages.agent.runtime.agent_backends import (
    DEFAULT_AGENT_BACKEND_ID,
    normalize_agent_backend_id,
)
from packages.ai.project.amadeus_compat import apply_amadeus_workflow_defaults
from packages.domain.enums import ProjectWorkflowType

_ALLOWED_EXECUTION_TARGETS = {"local", "workspace_target", "ssh"}
_ALLOWED_STAGE_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}
_ALLOWED_MODEL_ROLES = {"executor", "reviewer"}

_AGENT_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "codex",
        "label": "Codex（模型角色）",
        "kind": "native",
        "description": "原生后端模型角色，偏工程实现、实验脚本编排与工程化执行。",
        "supports_local": True,
        "supports_remote": True,
        "supports_mcp": True,
        "accent": "info",
    },
    {
        "id": "claude_code",
        "label": "Claude Code（模型角色）",
        "kind": "native",
        "description": "原生后端模型角色，偏长链路规划、审阅与复杂多步任务编排。",
        "supports_local": True,
        "supports_remote": True,
        "supports_mcp": True,
        "accent": "success",
    },
    {
        "id": "gemini",
        "label": "Gemini（模型角色）",
        "kind": "native",
        "description": "原生后端模型角色，偏大上下文整理、信息抽取与项目材料压缩。",
        "supports_local": True,
        "supports_remote": True,
        "supports_mcp": True,
        "accent": "warning",
    },
    {
        "id": "qwen",
        "label": "Qwen（模型角色）",
        "kind": "native",
        "description": "原生后端模型角色，偏中文科研资料阅读、总结与结构化输出。",
        "supports_local": True,
        "supports_remote": True,
        "supports_mcp": True,
        "accent": "default",
    },
    {
        "id": "goose",
        "label": "Goose（模型角色）",
        "kind": "native",
        "description": "原生后端模型角色，偏轻量执行与快速迭代。",
        "supports_local": True,
        "supports_remote": True,
        "supports_mcp": True,
        "accent": "default",
    },
    {
        "id": "custom_acp",
        "label": "Custom ACP",
        "kind": "acp",
        "description": "接入自定义 ACP 智能体，统一复用 ResearchOS 的项目上下文。",
        "supports_local": True,
        "supports_remote": True,
        "supports_mcp": True,
        "accent": "default",
    },
]

_ALL_AGENT_IDS = [item["id"] for item in _AGENT_TEMPLATES]
_ACTIVE_WORKFLOW_TYPES = {
    ProjectWorkflowType.literature_review.value,
    ProjectWorkflowType.idea_discovery.value,
    ProjectWorkflowType.novelty_check.value,
    ProjectWorkflowType.research_review.value,
    ProjectWorkflowType.run_experiment.value,
    ProjectWorkflowType.experiment_audit.value,
    ProjectWorkflowType.auto_review_loop.value,
    ProjectWorkflowType.paper_plan.value,
    ProjectWorkflowType.paper_figure.value,
    ProjectWorkflowType.paper_write.value,
    ProjectWorkflowType.paper_compile.value,
    ProjectWorkflowType.paper_writing.value,
    ProjectWorkflowType.rebuttal.value,
    ProjectWorkflowType.paper_improvement.value,
    ProjectWorkflowType.full_pipeline.value,
    ProjectWorkflowType.monitor_experiment.value,
    ProjectWorkflowType.sync_workspace.value,
}
_PRIMARY_PUBLIC_WORKFLOW_TYPES = [
    ProjectWorkflowType.idea_discovery.value,
    ProjectWorkflowType.run_experiment.value,
    ProjectWorkflowType.auto_review_loop.value,
    ProjectWorkflowType.paper_writing.value,
    ProjectWorkflowType.rebuttal.value,
    ProjectWorkflowType.full_pipeline.value,
]
_PRIMARY_PUBLIC_WORKFLOW_LABELS: dict[str, str] = {
    ProjectWorkflowType.idea_discovery.value: "Workflow 1 · Idea Discovery",
    ProjectWorkflowType.run_experiment.value: "Workflow 1.5 · Experiment Bridge",
    ProjectWorkflowType.auto_review_loop.value: "Workflow 2 · Auto Review Loop",
    ProjectWorkflowType.paper_writing.value: "Workflow 3 · Paper Writing",
    ProjectWorkflowType.rebuttal.value: "Workflow 4 · Rebuttal",
    ProjectWorkflowType.full_pipeline.value: "One-Click · Research Pipeline",
}
_PRIMARY_PUBLIC_WORKFLOW_COMMANDS: dict[str, str] = {
    ProjectWorkflowType.idea_discovery.value: "/idea-discovery",
    ProjectWorkflowType.run_experiment.value: "/experiment-bridge",
    ProjectWorkflowType.auto_review_loop.value: "/auto-review-loop",
    ProjectWorkflowType.paper_writing.value: "/paper-writing",
    ProjectWorkflowType.rebuttal.value: "/rebuttal",
    ProjectWorkflowType.full_pipeline.value: "/research-pipeline",
}
_PRIMARY_PUBLIC_WORKFLOW_SKILLS: dict[str, list[str]] = {
    ProjectWorkflowType.idea_discovery.value: [
        "research-lit",
        "idea-creator",
        "novelty-check",
        "research-review",
        "research-refine-pipeline",
    ],
    ProjectWorkflowType.run_experiment.value: [
        "experiment-bridge",
        "run-experiment",
        "monitor-experiment",
    ],
    ProjectWorkflowType.auto_review_loop.value: [
        "auto-review-loop",
        "research-review",
        "novelty-check",
        "run-experiment",
        "analyze-results",
        "monitor-experiment",
    ],
    ProjectWorkflowType.paper_writing.value: [
        "paper-plan",
        "paper-figure",
        "paper-write",
        "paper-compile",
        "auto-paper-improvement-loop",
    ],
    ProjectWorkflowType.rebuttal.value: [
        "rebuttal",
    ],
    ProjectWorkflowType.full_pipeline.value: [
        "research-pipeline",
        "idea-discovery",
        "experiment-bridge",
        "auto-review-loop",
        "paper-writing",
    ],
}
_PRIMARY_PUBLIC_WORKFLOW_GUIDES: dict[str, dict[str, Any]] = {
    ProjectWorkflowType.idea_discovery.value: {
        "intro": "从研究方向出发，完成文献调研、候选想法生成、查新和 reviewer 视角筛选，最后沉淀可直接进入实验阶段的 proposal。",
        "when_to_use": [
            "只有研究方向，还没有稳定方案。",
            "需要从多篇论文或一个主题里筛出最值得做的 idea。",
            "想在进入实验前先做 novelty 与 reviewer 视角把关。",
        ],
        "required_inputs": [
            "明确的研究方向、问题或目标。",
            "如果有参考论文、现有 repo 或本地工作区，建议先绑定到项目。",
            "若想人工把关 idea 选择，关闭自动继续。",
        ],
        "usage_steps": [
            "在任务说明里写清研究方向、约束条件、目标数据/指标、已有 baseline。",
            "启动后重点查看 IDEA_REPORT.md、FINAL_PROPOSAL.md、EXPERIMENT_PLAN.md。",
            "选定方向后，再进入 Experiment Bridge 或 Research Pipeline。",
        ],
        "expected_outputs": [
            "IDEA_REPORT.md",
            "FINAL_PROPOSAL.md",
            "EXPERIMENT_PLAN.md",
        ],
        "sample_prompt": (
            "研究方向：多模态 RAG 在长文档问答中的检索-生成错配问题。\n"
            "已有资源：当前 workspace 里有一个可运行 baseline，项目已关联若干 LongDoc QA / Multimodal RAG 论文。\n"
            "目标：生成 3 个候选 idea，完成 novelty check，最后收敛成 1 个能在 3 天内拿到 pilot 结果的方案。\n"
            "约束：优先使用公开数据集和轻量改动，不做大规模预训练。"
        ),
    },
    ProjectWorkflowType.run_experiment.value: {
        "intro": "把已有实验计划落到代码、sanity check、实际运行和结果采集层，适合从 proposal 进入真正可执行实验。",
        "when_to_use": [
            "已经有明确方案或 EXPERIMENT_PLAN。",
            "工作区里已经有可复用仓库或基础代码。",
            "你需要一个明确的实验命令来启动训练/评测。",
        ],
        "required_inputs": [
            "绑定可运行的本地或远程工作区。",
            "提供实验命令，例如 python train.py --config ...。",
            "最好已有 FINAL_PROPOSAL.md 或 EXPERIMENT_PLAN.md。",
        ],
        "usage_steps": [
            "先确认当前项目绑定的是正确 repo 和工作区。",
            "填写任务说明，再填写实验命令。",
            "启动后查看日志、experiment summary 和 EXPERIMENT_RESULTS.md。",
        ],
        "expected_outputs": [
            "实验日志",
            "EXPERIMENT_RESULTS.md",
            "reports/experiment-summary.md",
        ],
        "sample_prompt": (
            "实验目标：根据 EXPERIMENT_PLAN.md 实现主实验，并验证新方法相比 baseline 的收益。\n"
            "执行要求：先跑 1 个小样本 sanity check，确认数据读取、指标计算和日志写入都正常，再启动完整实验。\n"
            "结果整理：把关键指标、失败日志、输出路径和下一步补实验建议写入 EXPERIMENT_RESULTS.md。"
        ),
        "sample_execution_command": "python scripts/run_experiment.py --config configs/pilot.yaml --seed 1",
    },
    ProjectWorkflowType.auto_review_loop.value: {
        "intro": "围绕当前实验结果或草稿做多轮 reviewer 式评审与修复闭环，适合把工作从“能跑”推进到“能投稿”。",
        "when_to_use": [
            "已经有实验结果或初稿，需要找关键短板。",
            "希望让系统自动提出修复动作、补实验和重写建议。",
            "想在有限轮次内持续逼近 ready/almost ready。",
        ],
        "required_inputs": [
            "当前项目里最好已有运行结果、报告或论文草稿。",
            "如果要真的执行命令修复，可同时提供工作区和实验命令。",
            "如需逐轮人工把关，可关闭自动继续。",
        ],
        "usage_steps": [
            "在任务说明里写清这轮评审对象，例如某个主题、某版论文或某批实验。",
            "如果有执行命令，系统会优先结合真实工作区运行；没有命令则只生成评审闭环内容。",
            "重点查看 AUTO_REVIEW.md 和 REVIEW_STATE.json。",
        ],
        "expected_outputs": [
            "AUTO_REVIEW.md",
            "REVIEW_STATE.json",
            "reports/auto-review-loop.md",
        ],
        "sample_prompt": (
            "评审对象：当前 AnchorCoT 论文草稿、AUTO_REVIEW 之前的评论和 workspace 中最新实验结果。\n"
            "评审目标：最多运行 3 轮 reviewer-style 自动评审与修复建议，优先解决创新性定位、实验充分性和 ablation 缺口。\n"
            "执行边界：如需要运行命令，只做低成本验证；高成本补实验只生成明确计划和命令草案。"
        ),
    },
    ProjectWorkflowType.paper_writing.value: {
        "intro": "将已有研究材料组织成论文写作流水线，包括提纲、图表规划、正文写作、编译检查与两轮自动改稿。",
        "when_to_use": [
            "已经有相对稳定的 idea、实验结果和叙事。",
            "希望一次性生成 paper plan、draft、compile report 和 improvement log。",
            "需要为后续投稿或 rebuttal 形成完整稿件资产。",
        ],
        "required_inputs": [
            "尽量先有实验结果、AUTO_REVIEW 结论或 narrative 材料。",
            "最好在项目里关联关键论文、repo 和已有报告。",
            "若本地没有 LaTeX 工具链，编译阶段会输出待补环境说明。",
        ],
        "usage_steps": [
            "在任务说明里写清论文目标、目标会议/期刊和当前成果范围。",
            "生成后先看 PAPER_PLAN.md、paper/main.tex、PAPER_COMPILE.md。",
            "如果稿件已有审稿意见，再进入 Rebuttal。",
        ],
        "expected_outputs": [
            "PAPER_PLAN.md",
            "paper/main.tex",
            "PAPER_COMPILE.md",
            "PAPER_IMPROVEMENT_LOG.md",
        ],
        "sample_prompt": (
            "写作目标：围绕当前 AnchorCoT 项目生成 ICLR 风格论文草稿。\n"
            "已有材料：项目中已有 FINAL_PROPOSAL、实验结果表、部分图表草案和自动评审意见。\n"
            "输出重点：明确 problem framing、方法贡献、主要实验、ablation、局限性讨论，并生成 paper/main.tex 与编译检查报告。"
        ),
    },
    ProjectWorkflowType.rebuttal.value: {
        "intro": "针对外部审稿意见生成 grounded、可控、限字符的 rebuttal 流程，包含 issue board、strategy plan、draft、stress test 和最终可粘贴版本。",
        "when_to_use": [
            "论文已经投稿，收到了外部 reviewers 的意见。",
            "需要在字符限制下组织多 reviewer 的回应。",
            "想把所有问题、证据和承诺都追踪清楚，避免漏答或过度承诺。",
        ],
        "required_inputs": [
            "论文材料或当前稿件上下文。",
            "原始审稿意见，建议直接贴进任务说明或放进工作区后在说明里指出。",
            "目标 venue 和字符上限。",
        ],
        "usage_steps": [
            "选择 Rebuttal 后填写 venue、字符上限，并把 reviews 贴进任务说明。",
            "若只想先梳理问题板与策略，可开启 quick mode。",
            "完成后优先查看 ISSUE_BOARD.md、STRATEGY_PLAN.md、PASTE_READY.txt 和 REBUTTAL_DRAFT_rich.md。",
        ],
        "expected_outputs": [
            "rebuttal/ISSUE_BOARD.md",
            "rebuttal/STRATEGY_PLAN.md",
            "rebuttal/PASTE_READY.txt",
            "rebuttal/REBUTTAL_DRAFT_rich.md",
        ],
        "sample_prompt": (
            "材料位置：paper/ 下是当前投稿稿件，reviews.md 中包含三位 reviewer 的原文意见。\n"
            "任务目标：生成 ICML rebuttal，字符上限 5000，先梳理 issue board，再给出 response strategy 和最终可粘贴版本。\n"
            "重点问题：R1 质疑 novelty，R2 质疑实验充分性，R3 主要关注表述和 limitation。回复必须 grounded，不承诺无法完成的新实验。"
        ),
        "sample_rebuttal_review_bundle": (
            "Reviewer 1: The contribution is incremental and the paper should clarify how the method differs from the closest retrieval-augmented reasoning baselines.\n\n"
            "Reviewer 2: The experiments are promising but missing an ablation on the routing module and significance analysis across seeds.\n\n"
            "Reviewer 3: The writing is generally clear, but the limitation section should better discuss failure cases on long-context examples."
        ),
    },
    ProjectWorkflowType.full_pipeline.value: {
        "intro": "一键串起 ResearchOS 主流程：想法发现、实现与实验、自动评审总结和最终 handoff，适合从研究方向一路推进到可写作状态。",
        "when_to_use": [
            "你希望从方向开始，直接推进到实验和总结。",
            "当前已经有 repo 和主实验命令，想让系统自动串联主线。",
            "希望少切换 workflow，直接跑完整主干。",
        ],
        "required_inputs": [
            "明确的研究方向或任务说明。",
            "绑定可运行工作区。",
            "提供主实验命令，用于实现与实验阶段。",
        ],
        "usage_steps": [
            "在任务说明里写研究方向、目标、约束和已有资源。",
            "填写主实验命令，系统会在 Workflow 1 后继续进入实现和总结。",
            "完成后重点查看 IDEA_REPORT.md、AUTO_REVIEW.md 和 final handoff 报告。",
        ],
        "expected_outputs": [
            "IDEA_REPORT.md",
            "AUTO_REVIEW.md",
            "reports/final-handoff.md",
        ],
        "sample_prompt": (
            "研究主线：多模态长文档问答中的检索-生成错配问题。\n"
            "已有基础：当前 workspace 有可运行 baseline，项目已导入相关论文和初步实验脚本。\n"
            "一键目标：从 idea discovery 开始，完成方案收敛、实验桥接、结果评审和最终 handoff，产出可进入论文写作阶段的结论。\n"
            "约束：优先做可复现实验和低成本 ablation，遇到高成本实验时先生成计划并停在人工确认点。"
        ),
        "sample_execution_command": "python scripts/run_experiment.py --config configs/main.yaml --seed 1",
    },
}


def _stage(
    stage_id: str,
    label: str,
    description: str,
    *,
    default_agent_id: str = "codex",
    execution_target: str = "workspace_target",
    model_role: str = "executor",
    mcp_required: bool = False,
    deliverable: str = "",
    checkpoint_required: bool = False,
    supported_agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "description": description,
        "default_agent_id": default_agent_id,
        "execution_target": execution_target,
        "model_role": model_role if model_role in _ALLOWED_MODEL_ROLES else "executor",
        "mcp_required": bool(mcp_required),
        "deliverable": deliverable,
        "checkpoint_required": bool(checkpoint_required),
        "supported_agent_ids": list(supported_agent_ids or _ALL_AGENT_IDS),
    }


def _preset(
    workflow_type: ProjectWorkflowType,
    label: str,
    prefill_prompt: str,
    description: str,
    stages: list[dict[str, Any]],
    *,
    readiness: str = "planned",
) -> dict[str, Any]:
    preset = {
        "id": workflow_type.value,
        "label": label,
        "workflow_type": workflow_type.value,
        "prefill_prompt": prefill_prompt,
        "description": description,
        "readiness": readiness,
        "availability": "active" if workflow_type.value in _ACTIVE_WORKFLOW_TYPES else "planned",
        "source_reference": "amadeus_aris",
        "stages": deepcopy(stages),
    }
    guide = _PRIMARY_PUBLIC_WORKFLOW_GUIDES.get(workflow_type.value)
    if isinstance(guide, dict):
        preset.update(deepcopy(guide))
    return preset


_WORKFLOW_PRESETS: list[dict[str, Any]] = [
    _preset(
        ProjectWorkflowType.literature_review,
        "文献综述",
        "基于当前项目已有论文与上下文，输出一份结构化文献综述。",
        "聚焦项目相关工作、主线脉络和研究空白。",
        [
            _stage(
                "collect_context",
                "检索与整理",
                "从项目论文、仓库和已有分析中整理本轮综述所需证据。",
                mcp_required=True,
                model_role="executor",
                deliverable="项目证据包",
            ),
            _stage(
                "synthesize_evidence",
                "阅读与分析",
                "围绕主线问题汇总代表性工作、方法脉络与风险。",
                mcp_required=True,
                model_role="executor",
                checkpoint_required=True,
                deliverable="综述草稿",
            ),
            _stage(
                "deliver_review",
                "综述产出",
                "把结论写回项目报告，并沉淀下一步建议。",
                model_role="executor",
                deliverable="Markdown 文献综述",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.idea_discovery,
        "想法发现",
        "围绕当前研究方向运行完整的想法发现流程，生成带查新和评审结果的 IDEA_REPORT。",
        "ResearchOS 的想法发现主链路：文献梳理 → 候选想法 → 查新验证 → 外部评审 → 方法收敛与实验规划。",
        [
            _stage(
                "literature_survey",
                "Literature Survey",
                "调用 /research-lit 建立研究版图，梳理子方向、结构性空白和高价值切入点。",
                mcp_required=True,
                model_role="executor",
                checkpoint_required=True,
                deliverable="文献版图与 working notes",
            ),
            _stage(
                "idea_generation",
                "Idea Generation + Filtering + Pilots",
                "调用 /idea-creator 生成候选 idea、做可行性筛选，并对前几名进行 pilot 验证。",
                mcp_required=True,
                model_role="executor",
                checkpoint_required=True,
                deliverable="IDEA_REPORT.md（初版）",
            ),
            _stage(
                "deep_novelty_verification",
                "Deep Novelty Verification",
                "调用 /novelty-check 对 top ideas 做深度查新，识别最近工作、撞题风险和差异点。",
                default_agent_id="claude_code",
                model_role="reviewer",
                mcp_required=True,
                deliverable="Novelty check 更新",
            ),
            _stage(
                "external_critical_review",
                "External Critical Review",
                "调用 /research-review 以 NeurIPS/ICML reviewer 视角评审候选 idea 和 pilot 证据。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="Reviewer feedback",
            ),
            _stage(
                "method_refinement",
                "Method Refinement + Experiment Planning",
                "调用 /research-refine-pipeline 固化问题锚点、方法提案与 claim-driven 实验计划。",
                default_agent_id="claude_code",
                model_role="executor",
                checkpoint_required=True,
                deliverable="FINAL_PROPOSAL.md + EXPERIMENT_PLAN.md",
            ),
            _stage(
                "final_report",
                "Final Report",
                "汇总最终排名、pilot 结果、查新结论与 reviewer 反馈，产出可衔接 Workflow 1.5/2 的结论。",
                model_role="executor",
                deliverable="IDEA_REPORT.md",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.novelty_check,
        "查新评估",
        "围绕当前研究问题进行 novelty check，识别最相近工作与潜在撞题风险。",
        "围绕项目想法和已有论文做查新分析，识别撞题风险与差异点。",
        [
            _stage(
                "collect_claims",
                "整理主张",
                "提取当前项目的核心主张、方法要点和实验边界。",
                default_agent_id="native",
                model_role="executor",
                deliverable="主张摘要",
            ),
            _stage(
                "compare_prior_work",
                "对比现有工作",
                "对照已关联论文和项目材料，梳理最接近的已有方法。",
                default_agent_id="claude_code",
                model_role="reviewer",
                mcp_required=True,
                checkpoint_required=True,
                deliverable="相近工作对比",
            ),
            _stage(
                "issue_novelty_report",
                "输出查新报告",
                "形成 novelty 风险、差异点与下一步验证建议。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="Markdown 查新报告",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.research_review,
        "研究评审",
        "以审稿人视角评审当前研究任务、方案和已有输出。",
        "以审稿人视角输出结构化评审意见。",
        [
            _stage(
                "collect_submission",
                "整理材料",
                "整理研究目标、相关论文、实验产物和当前草稿。",
                default_agent_id="native",
                model_role="executor",
                deliverable="评审资料包",
            ),
            _stage(
                "review_submission",
                "形成评审意见",
                "从创新性、技术可信度、实验充分性和写作表达四个维度给出评审。",
                default_agent_id="claude_code",
                model_role="reviewer",
                checkpoint_required=True,
                deliverable="结构化评审意见",
            ),
            _stage(
                "deliver_verdict",
                "输出评审结论",
                "给出综合结论、主要问题和明确修订建议。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="Markdown 研究评审报告",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.init_repo,
        "初始化仓库",
        "为当前研究方向初始化一个可复现实验仓库结构。",
        "适合建立项目目录、实验脚手架和文档结构。",
        [
            _stage(
                "plan_repo",
                "脚手架规划",
                "定义仓库目录、实验入口和文档结构。",
                default_agent_id="codex",
                deliverable="仓库规划说明",
            ),
            _stage(
                "create_scaffold",
                "生成结构",
                "在工作区生成代码目录、README 和基础配置。",
                default_agent_id="codex",
                execution_target="workspace_target",
                deliverable="项目脚手架",
            ),
            _stage(
                "validate_bootstrap",
                "验证初始化",
                "检查关键目录、依赖说明和启动入口是否完整。",
                default_agent_id="claude_code",
                deliverable="初始化检查单",
            ),
        ],
    ),
    _preset(
        ProjectWorkflowType.autoresearch_claude_code,
        "自动研究（Claude Code）",
        "参考 autoresearch-claude-code 在当前项目内启动一次自动研究迭代。",
        "以会话模板 + 基线运行 + 迭代规划的方式，把 AutoResearch 能力接入 ResearchOS 项目流。",
        [
            _stage(
                "bootstrap_session",
                "初始化会话",
                "写入 AutoResearch 会话模板、脚本与目录结构。",
                default_agent_id="claude_code",
                execution_target="workspace_target",
                deliverable="AutoResearch 会话模板",
            ),
            _stage(
                "run_baseline",
                "执行基线",
                "运行一轮最小基线流程，产出初始报告与指标。",
                default_agent_id="codex",
                execution_target="workspace_target",
                deliverable="基线运行结果",
            ),
            _stage(
                "propose_iterations",
                "规划下一轮",
                "根据基线输出给出可执行的下一轮迭代计划。",
                default_agent_id="claude_code",
                deliverable="下一轮迭代计划",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.run_experiment,
        "实验桥接",
        "围绕当前实验计划执行 experiment-bridge，完成实现、sanity check、部署与结果采集。",
        "ResearchOS 的实验桥接链路：读取 EXPERIMENT_PLAN → 实现代码 → sanity check → 部署 → 收集初始结果。",
        [
            _stage(
                "parse_experiment_plan",
                "Parse Experiment Plan",
                "读取 EXPERIMENT_PLAN / TRACKER / FINAL_PROPOSAL，提取 run order、预算和 success criteria。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="实验里程碑与预算摘要",
            ),
            _stage(
                "implement_experiment_code",
                "Implement Experiment Code",
                "按计划补齐训练、评测、数据处理和结果落盘脚本，优先复用现有仓库代码。",
                default_agent_id="codex",
                execution_target="workspace_target",
                checkpoint_required=True,
                deliverable="实验脚本与配置",
            ),
            _stage(
                "sanity_check",
                "Sanity Check",
                "优先执行最小 sanity run，确认训练、日志、指标和输出格式都正确。",
                default_agent_id="codex",
                execution_target="workspace_target",
                checkpoint_required=True,
                deliverable="Sanity 通过记录",
            ),
            _stage(
                "deploy_full_experiments",
                "Deploy Full Experiments",
                "按 milestone 顺序部署完整实验，必要时通过远程工作区并行执行。",
                default_agent_id="codex",
                execution_target="workspace_target",
                deliverable="运行中的实验批次",
            ),
            _stage(
                "collect_initial_results",
                "Collect Initial Results",
                "解析 JSON/CSV/logs，更新 EXPERIMENT_TRACKER，并判断主结果是正向、负向还是不确定。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="EXPERIMENT_RESULTS.md",
            ),
            _stage(
                "handoff_to_auto_review",
                "Handoff",
                "整理 bridge 结论并明确是否进入 /auto-review-loop 继续迭代。",
                default_agent_id="native",
                model_role="executor",
                deliverable="Workflow 2 交接摘要",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.experiment_audit,
        "实验审计",
        "围绕当前工作区与实验结果执行完整性审计，检查 ground truth、分数归一化、结果引用和评测范围。",
        "在进入自动评审或论文写作前，对实验完整性做 reviewer 风格核查。",
        [
            _stage(
                "collect_artifacts",
                "Collect Audit Artifacts",
                "收集评测脚本、结果文件、实验跟踪、论文主张和配置文件，形成审计证据包。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="审计证据包",
            ),
            _stage(
                "review_integrity",
                "Cross-Model Integrity Review",
                "以 reviewer 视角检查 ground truth provenance、score normalization、result existence、dead code、scope 和 evaluation type。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="结构化实验审计结论",
            ),
            _stage(
                "issue_audit_report",
                "Issue Audit Report",
                "输出 EXPERIMENT_AUDIT.md 和 EXPERIMENT_AUDIT.json，并给出 claim impact 和后续修正动作。",
                default_agent_id="native",
                model_role="executor",
                deliverable="实验审计报告",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.auto_review_loop,
        "自动评审循环",
        "围绕当前研究方向开启一次自主执行与自我评审循环。",
        "ResearchOS 的自动评审链路：外部评审 → 实施修复 → 再评审，直到通过或达到最大轮次。",
        [
            _stage(
                "initialization",
                "Initialization",
                "恢复 REVIEW_STATE、读取 AUTO_REVIEW 和近期结果，确定本轮起点与未完成事项。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="Loop state snapshot",
            ),
            _stage(
                "external_review",
                "External Review",
                "调用外部 reviewer 对当前研究状态重新评分，提取主要 weaknesses 和 minimum fixes。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="Round review response",
            ),
            _stage(
                "parse_assessment",
                "Parse Assessment",
                "解析 score、verdict 和 action items，判断是否已经 ready / almost ready。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="结构化评审结论",
            ),
            _stage(
                "implement_fixes",
                "Implement Fixes",
                "根据 reviewer 建议修改代码、补实验、补分析或重写叙事，优先解决高严重度问题。",
                default_agent_id="codex",
                execution_target="workspace_target",
                model_role="executor",
                deliverable="Fixes 与实验更新",
            ),
            _stage(
                "wait_for_results",
                "Wait for Results",
                "跟踪长时实验和远程运行，收集必要结果后回填当前轮次证据。",
                default_agent_id="codex",
                execution_target="workspace_target",
                model_role="executor",
                deliverable="补充结果与日志",
            ),
            _stage(
                "document_and_persist",
                "Document + Persist",
                "把完整 reviewer 原文、采取的动作、结果与状态写入 AUTO_REVIEW.md / REVIEW_STATE.json。",
                default_agent_id="native",
                model_role="executor",
                deliverable="AUTO_REVIEW.md + REVIEW_STATE.json",
            ),
            _stage(
                "termination",
                "Termination",
                "输出最终轮次总结，明确剩余 blockers、是否可投稿，以及后续 paper-writing 接口。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="Final loop verdict",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.paper_plan,
        "论文规划",
        "先形成论文提纲、章节结构和证据需求清单。",
        "论文写作前的结构设计阶段。",
        [
            _stage(
                "collect_materials",
                "整理材料",
                "汇总项目背景、相关工作、实验结果和关键卖点。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="写作材料包",
            ),
            _stage(
                "outline_manuscript",
                "生成提纲",
                "生成论文结构、章节要点与证据缺口。",
                default_agent_id="claude_code",
                model_role="executor",
                deliverable="论文提纲",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.paper_figure,
        "图表规划",
        "规划论文中需要的图表、表格和示意图。",
        "论文图表设计阶段。",
        [
            _stage(
                "collect_results",
                "整理结果",
                "提取实验结果、错误案例和可视化素材。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="图表素材清单",
            ),
            _stage(
                "design_figures",
                "设计图表",
                "输出图表规划、图注和建议渲染形式。",
                default_agent_id="gemini",
                model_role="executor",
                deliverable="图表规划文档",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.paper_write,
        "论文成稿",
        "基于当前材料生成论文正文初稿。",
        "论文正文起草阶段。",
        [
            _stage(
                "gather_materials",
                "整理材料",
                "汇总提纲、图表规划、实验结果和相关工作。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="正文材料包",
            ),
            _stage(
                "draft_sections",
                "生成正文",
                "写出论文主体章节和待补充内容。",
                default_agent_id="claude_code",
                model_role="executor",
                deliverable="正文草稿",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.paper_compile,
        "编译稿件",
        "检查并编译当前论文稿件，输出编译结果与日志。",
        "论文编译与检查阶段。",
        [
            _stage(
                "prepare_compile",
                "检查稿件",
                "检查当前工作区中的稿件文件、依赖和编译入口。",
                default_agent_id="codex",
                execution_target="workspace_target",
                model_role="executor",
                checkpoint_required=True,
                deliverable="编译前检查",
            ),
            _stage(
                "run_compile",
                "执行编译",
                "执行编译命令并收集 stdout/stderr。",
                default_agent_id="codex",
                execution_target="workspace_target",
                model_role="executor",
                checkpoint_required=True,
                deliverable="编译日志",
            ),
            _stage(
                "summarize_compile",
                "整理结果",
                "总结编译结果、失败原因和修复建议。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="编译报告",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.paper_writing,
        "论文写作",
        "按论文写作 pipeline 逐阶段生成论文工作区产物。",
        "ResearchOS 的论文写作主链路：paper-plan → paper-figure → paper-write → paper-compile → auto-paper-improvement-loop。",
        [
            _stage(
                "paper_plan",
                "Paper Plan",
                "从项目材料中提取 claims-evidence matrix、section plan、figure plan 和 citation scaffolding。",
                default_agent_id="native",
                model_role="executor",
                mcp_required=True,
                checkpoint_required=True,
                deliverable="PAPER_PLAN.md",
            ),
            _stage(
                "figure_generation",
                "Figure Generation",
                "根据 paper plan 生成 figure/table inventory、LaTeX includes 和图表素材规划。",
                default_agent_id="gemini",
                model_role="executor",
                checkpoint_required=True,
                deliverable="figures/ + latex_includes.tex",
            ),
            _stage(
                "latex_writing",
                "LaTeX Writing",
                "根据计划与图表素材写出 paper/ 目录、sections/*.tex 和 references.bib。",
                default_agent_id="claude_code",
                model_role="executor",
                checkpoint_required=True,
                deliverable="paper/main.tex",
            ),
            _stage(
                "compilation",
                "Compilation",
                "检查依赖、执行编译并生成 compile report / PDF 路径摘要。",
                default_agent_id="codex",
                model_role="executor",
                execution_target="workspace_target",
                checkpoint_required=True,
                deliverable="PAPER_COMPILE.md",
            ),
            _stage(
                "auto_improvement_loop",
                "Auto Improvement Loop",
                "对稿件执行两轮 review → fix → recompile，修正 overclaims、表达和结构问题。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="PAPER_IMPROVEMENT_LOG.md",
            ),
            _stage(
                "final_report",
                "Final Report",
                "汇总 round0/round1/round2 PDF、改进分数和剩余问题，输出可提交的最终摘要。",
                default_agent_id="native",
                model_role="executor",
                deliverable="Paper pipeline report",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.rebuttal,
        "Rebuttal",
        "围绕以下论文与审稿意见准备 rebuttal：",
        "ResearchOS 的 rebuttal 链路：解析 reviews → issue board → strategy plan → rebuttal draft → stress test → finalize。",
        [
            _stage(
                "normalize_reviews",
                "Normalize Reviews",
                "整理审稿意见原文、venue 规则、字符限制和当前 round，初始化 rebuttal 工作目录与状态文件。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="rebuttal/REVIEWS_RAW.md + REBUTTAL_STATE.md",
            ),
            _stage(
                "issue_board",
                "Issue Board",
                "将 reviewer concerns 原子化，按 reviewer / severity / response mode 组织成 ISSUE_BOARD。",
                default_agent_id="claude_code",
                model_role="reviewer",
                checkpoint_required=True,
                deliverable="rebuttal/ISSUE_BOARD.md",
            ),
            _stage(
                "strategy_plan",
                "Strategy Plan",
                "归纳 shared themes、character budget、grounding constraints 和 blocked claims，形成正式 rebuttal 策略。",
                default_agent_id="native",
                model_role="executor",
                checkpoint_required=True,
                deliverable="rebuttal/STRATEGY_PLAN.md",
            ),
            _stage(
                "draft_rebuttal",
                "Draft Rebuttal",
                "基于 issue board 与 strategy plan 起草 grounded rebuttal 初稿，覆盖 opener、per-reviewer replies 与 closing。",
                default_agent_id="qwen",
                model_role="executor",
                checkpoint_required=True,
                deliverable="rebuttal/REBUTTAL_DRAFT_v1.md",
            ),
            _stage(
                "stress_test",
                "Stress Test",
                "以 meta-review / adversarial reviewer 视角检查 coverage、unsupported claims、tone 与 over-commitment 风险。",
                default_agent_id="codex",
                model_role="reviewer",
                deliverable="rebuttal/MCP_STRESS_TEST.md",
            ),
            _stage(
                "finalize_package",
                "Finalize Package",
                "输出 rich rebuttal、paste-ready 纯文本版本、最终 state 与汇总报告。",
                default_agent_id="native",
                model_role="executor",
                deliverable="rebuttal/REBUTTAL_DRAFT_rich.md + PASTE_READY.txt + reports/rebuttal.md",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.paper_improvement,
        "论文改进",
        "对当前论文草稿做针对性的结构修订和内容增强。",
        "适合审稿意见响应或大改稿。",
        [
            _stage(
                "diagnose_draft",
                "诊断问题",
                "识别当前稿件在结构、论证和实验上的主要问题。",
                default_agent_id="claude_code",
                model_role="reviewer",
                checkpoint_required=True,
                deliverable="问题诊断单",
            ),
            _stage(
                "revise_sections",
                "针对性修订",
                "逐段修正关键薄弱点并补齐缺失信息。",
                default_agent_id="codex",
                execution_target="workspace_target",
                model_role="executor",
                checkpoint_required=True,
                deliverable="修订版本",
            ),
            _stage(
                "final_check",
                "最终检查",
                "检查术语一致性、图表引用和逻辑闭环。",
                default_agent_id="qwen",
                model_role="reviewer",
                deliverable="终检报告",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.full_pipeline,
        "科研流程",
        "围绕以下任务运行完整的科研流程：",
        "对应研究助手中的 /research-pipeline skill，是项目工作区里的正式科研自动化入口。",
        [
            _stage(
                "review_prior_work",
                "想法发现（Gate 1）",
                "执行完整的 Workflow 1，输出 IDEA_REPORT，并在进入实现前触发人工 Gate 1。",
                default_agent_id="native",
                model_role="executor",
                mcp_required=True,
                checkpoint_required=True,
                deliverable="IDEA_REPORT.md",
            ),
            _stage(
                "implement_and_run",
                "实现与部署",
                "围绕已选 idea 完成实现、部署实验命令并收集初始运行结果。",
                default_agent_id="codex",
                execution_target="workspace_target",
                model_role="executor",
                deliverable="实现记录与实验日志",
            ),
            _stage(
                "synthesize_findings",
                "自动评审循环",
                "对齐 auto-review-loop，对实验结果做多轮 reviewer 视角总结、问题归纳与下一轮修复建议。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="AUTO_REVIEW.md",
            ),
            _stage(
                "handoff_output",
                "最终总结",
                "输出完整的 pipeline handoff，总结从 idea 到实验与评审的最终状态。",
                default_agent_id="qwen",
                model_role="executor",
                deliverable="Research pipeline report",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.monitor_experiment,
        "监控实验",
        "跟踪远程实验进度，汇总关键日志、指标与异常。",
        "面向 SSH 工作区实验监控和定期汇报。",
        [
            _stage(
                "inspect_runs",
                "检查运行状态",
                "确认远程进程、日志、检查点和输出目录。",
                default_agent_id="codex",
                execution_target="ssh",
                model_role="executor",
                checkpoint_required=True,
                deliverable="运行状态摘要",
            ),
            _stage(
                "collect_signals",
                "收集信号",
                "提取关键指标、异常片段和趋势信息。",
                default_agent_id="gemini",
                execution_target="ssh",
                model_role="executor",
                checkpoint_required=True,
                deliverable="指标与异常列表",
            ),
            _stage(
                "issue_digest",
                "形成简报",
                "输出简洁的进度汇报与后续建议。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="实验监控简报",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.sync_workspace,
        "同步工作区",
        "同步当前项目的本地与远程工作区，并说明差异。",
        "面向本地目录与远程目录的工作区同步。",
        [
            _stage(
                "scan_diff",
                "扫描差异",
                "对比项目工作区与远程目标的目录、文件和配置差异。",
                default_agent_id="native",
                execution_target="workspace_target",
                model_role="executor",
                checkpoint_required=True,
                deliverable="差异摘要",
            ),
            _stage(
                "sync_paths",
                "执行同步",
                "按策略同步代码、文档和必要资源文件。",
                default_agent_id="codex",
                execution_target="workspace_target",
                model_role="executor",
                checkpoint_required=True,
                deliverable="同步记录",
            ),
            _stage(
                "validate_state",
                "验证结果",
                "确认关键文件可用、路径正确且工作区状态一致。",
                default_agent_id="claude_code",
                model_role="reviewer",
                deliverable="同步校验报告",
            ),
        ],
        readiness="native",
    ),
    _preset(
        ProjectWorkflowType.custom_run,
        "自定义运行",
        "在当前项目上下文中执行以下自定义研究任务：",
        "适合用户自定义编排提示词。",
        [
            _stage(
                "plan_custom",
                "规划任务",
                "根据用户目标拆解阶段、风险和依赖。",
                default_agent_id="claude_code",
                model_role="executor",
                deliverable="自定义计划",
            ),
            _stage(
                "execute_custom",
                "执行主体任务",
                "在项目上下文中执行主要任务，必要时使用工作区和 MCP。",
                default_agent_id="custom_acp",
                execution_target="workspace_target",
                model_role="executor",
                mcp_required=True,
                deliverable="主体产出",
            ),
            _stage(
                "summarize_custom",
                "整理结果",
                "回填结论、行动项和下一步建议。",
                default_agent_id="qwen",
                model_role="reviewer",
                deliverable="运行总结",
            ),
        ],
    ),
]

_WORKFLOW_PRESET_BY_TYPE = {item["workflow_type"]: item for item in _WORKFLOW_PRESETS}

_WORKFLOW_PRESETS = [apply_amadeus_workflow_defaults(item) for item in _WORKFLOW_PRESETS]
_WORKFLOW_PRESET_BY_TYPE = {item["workflow_type"]: item for item in _WORKFLOW_PRESETS}


def list_project_agent_templates() -> list[dict[str, Any]]:
    return deepcopy(_AGENT_TEMPLATES)


def list_project_workflow_presets() -> list[dict[str, Any]]:
    return [_normalize_preset_agent_fields(item) for item in deepcopy(_WORKFLOW_PRESETS)]


def list_public_project_workflow_presets() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for order, workflow_type in enumerate(_PRIMARY_PUBLIC_WORKFLOW_TYPES, start=1):
        preset = get_project_workflow_preset(workflow_type)
        if not preset:
            continue
        preset["label"] = _PRIMARY_PUBLIC_WORKFLOW_LABELS.get(
            workflow_type, preset.get("label") or workflow_type
        )
        preset["entry_command"] = _PRIMARY_PUBLIC_WORKFLOW_COMMANDS.get(workflow_type)
        preset["source_skills"] = list(_PRIMARY_PUBLIC_WORKFLOW_SKILLS.get(workflow_type, []))
        preset["workflow_group"] = "primary"
        preset["workflow_order"] = order
        items.append(preset)
    return items


def is_active_project_workflow(workflow_type: ProjectWorkflowType | str) -> bool:
    key = str(
        workflow_type.value if isinstance(workflow_type, ProjectWorkflowType) else workflow_type
    )
    return key in _ACTIVE_WORKFLOW_TYPES


def get_project_workflow_preset(workflow_type: ProjectWorkflowType | str) -> dict[str, Any] | None:
    key = str(
        workflow_type.value if isinstance(workflow_type, ProjectWorkflowType) else workflow_type
    )
    preset = _WORKFLOW_PRESET_BY_TYPE.get(key)
    return _normalize_preset_agent_fields(deepcopy(preset)) if preset else None


def build_run_orchestration(
    workflow_type: ProjectWorkflowType | str,
    existing: dict[str, Any] | None = None,
    *,
    target_id: str | None = None,
    workspace_server_id: str | None = None,
    reset_stage_status: bool = False,
) -> dict[str, Any]:
    preset = get_project_workflow_preset(workflow_type)
    key = str(
        workflow_type.value if isinstance(workflow_type, ProjectWorkflowType) else workflow_type
    )
    existing = existing if isinstance(existing, dict) else {}

    stages_source = list(preset.get("stages", [])) if preset else []
    existing_stages = {
        str(item.get("id")): item
        for item in (existing.get("stages") or [])
        if isinstance(item, dict) and item.get("id")
    }

    stages: list[dict[str, Any]] = []
    for order, stage in enumerate(stages_source, start=1):
        current = existing_stages.get(str(stage["id"]), {})
        status = "pending" if reset_stage_status else _normalize_stage_status(current.get("status"))
        execution_target = _normalize_execution_target(
            current.get("execution_target"),
            fallback=str(stage.get("execution_target") or "workspace_target"),
            workspace_server_id=workspace_server_id,
        )
        stages.append(
            {
                **deepcopy(stage),
                "order": order,
                "selected_agent_id": _normalize_agent_id(
                    current.get("selected_agent_id")
                    or current.get("agent_id")
                    or stage.get("default_agent_id")
                ),
                "selected_engine_id": _normalize_engine_id(current.get("selected_engine_id")),
                "execution_target": execution_target,
                "model_role": _normalize_model_role(
                    current.get("model_role"), fallback=str(stage.get("model_role") or "executor")
                ),
                "mcp_enabled": bool(
                    current.get("mcp_enabled")
                    if current.get("mcp_enabled") is not None
                    else stage.get("mcp_required", False)
                ),
                "checkpoint_required": bool(
                    current.get("checkpoint_required")
                    if current.get("checkpoint_required") is not None
                    else stage.get("checkpoint_required", False)
                ),
                "status": status,
                "notes": str(current.get("notes") or "").strip(),
            }
        )

    return {
        "workflow_type": key,
        "preset_id": preset["id"] if preset else key,
        "label": preset["label"] if preset else key,
        "readiness": str((preset or {}).get("readiness") or "planned"),
        "source_reference": (preset or {}).get("source_reference") or "custom",
        "target_binding": str(target_id or existing.get("target_binding") or "project_default"),
        "workspace_server_id": workspace_server_id,
        "stages": stages,
    }


def build_stage_trace(
    orchestration: dict[str, Any] | None,
    *,
    existing: list[dict[str, Any]] | None = None,
    reset: bool = False,
) -> list[dict[str, Any]]:
    orchestration = orchestration if isinstance(orchestration, dict) else {}
    existing_map = {
        str(item.get("stage_id")): item
        for item in (existing or [])
        if isinstance(item, dict) and item.get("stage_id")
    }
    trace: list[dict[str, Any]] = []
    for stage in orchestration.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        current = existing_map.get(str(stage.get("id")), {})
        trace.append(
            {
                "stage_id": stage.get("id"),
                "label": stage.get("label"),
                "description": stage.get("description"),
                "deliverable": stage.get("deliverable"),
                "status": "pending"
                if reset
                else _normalize_stage_status(current.get("status") or stage.get("status")),
                "model_role": _normalize_model_role(
                    current.get("model_role") or stage.get("model_role"), fallback="executor"
                ),
                "message": "" if reset else str(current.get("message") or ""),
                "progress_pct": 0 if reset else int(current.get("progress_pct") or 0),
                "agent_id": stage.get("selected_agent_id") or stage.get("default_agent_id"),
                "engine_id": None
                if reset
                else current.get("engine_id") or stage.get("selected_engine_id"),
                "engine_label": None if reset else current.get("engine_label"),
                "execution_target": stage.get("execution_target"),
                "mcp_enabled": bool(stage.get("mcp_enabled")),
                "checkpoint_required": bool(stage.get("checkpoint_required")),
                "provider": None if reset else current.get("provider"),
                "model": None if reset else current.get("model"),
                "variant": None if reset else current.get("variant"),
                "model_source": None if reset else current.get("model_source"),
                "started_at": None if reset else current.get("started_at"),
                "completed_at": None if reset else current.get("completed_at"),
                "error": None if reset else current.get("error"),
            }
        )
    return trace


def _normalize_agent_id(value: Any) -> str:
    raw = str(value or "").strip()
    if normalize_agent_backend_id(raw) == DEFAULT_AGENT_BACKEND_ID:
        return "codex"
    if raw and raw in _ALL_AGENT_IDS:
        return raw
    return "codex"


def _normalize_supported_agent_ids(values: Any) -> list[str]:
    normalized: list[str] = []
    for item in values or []:
        agent_id = _normalize_agent_id(item)
        if agent_id not in normalized:
            normalized.append(agent_id)
    return normalized or list(_ALL_AGENT_IDS)


def _normalize_preset_agent_fields(preset: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(preset)
    stages: list[dict[str, Any]] = []
    for raw_stage in normalized.get("stages") or []:
        if not isinstance(raw_stage, dict):
            continue
        stage = dict(raw_stage)
        stage["default_agent_id"] = _normalize_agent_id(stage.get("default_agent_id"))
        stage["supported_agent_ids"] = _normalize_supported_agent_ids(
            stage.get("supported_agent_ids")
        )
        if stage.get("selected_agent_id") is not None:
            stage["selected_agent_id"] = _normalize_agent_id(stage.get("selected_agent_id"))
        stages.append(stage)
    normalized["stages"] = stages
    return normalized


def _normalize_engine_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw or None


def _normalize_execution_target(
    value: Any,
    *,
    fallback: str,
    workspace_server_id: str | None,
) -> str:
    target = str(value or fallback or "workspace_target").strip().lower()
    if target not in _ALLOWED_EXECUTION_TARGETS:
        target = str(fallback or "workspace_target").strip().lower() or "workspace_target"
    if target == "ssh" and not workspace_server_id:
        return "workspace_target"
    return target


def _normalize_stage_status(value: Any) -> str:
    status = str(value or "pending").strip().lower()
    if status not in _ALLOWED_STAGE_STATUSES:
        return "pending"
    return status


def _normalize_model_role(value: Any, *, fallback: str) -> str:
    role = str(value or fallback or "executor").strip().lower()
    if role not in _ALLOWED_MODEL_ROLES:
        return "executor"
    return role
