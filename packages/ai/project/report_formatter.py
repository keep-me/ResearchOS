from __future__ import annotations

import json
import re
from typing import Any

from packages.ai.project.output_sanitizer import sanitize_project_markdown


def markdown_excerpt(markdown: str | None, limit: int = 220) -> str:
    text = re.sub(r"[#>*`_-]", " ", str(markdown or ""))
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_literature_review_report(project_label: str, prompt: str | None, body: str) -> str:
    cleaned_body = sanitize_project_markdown(body)
    summary = _summary_bullets(cleaned_body)
    lines = [
        "# 文献综述报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**研究问题**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 核心结论",
            *summary,
            "",
            "## 详细综述",
            cleaned_body or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_idea_discovery_report(
    project_label: str,
    prompt: str | None,
    literature_markdown: str,
    created_ideas: list[dict[str, Any]],
    novelty_markdown: str,
    review_markdown: str,
) -> str:
    executive_summary_lines = [
        "- 已完成文献调研、候选想法生成、深度查新与外部评审。",
        f"- 当前共形成 {len(created_ideas)} 条候选研究想法。",
    ]
    if created_ideas:
        executive_summary_lines.append(
            f"- 推荐优先推进：{str(created_ideas[0].get('title') or 'Top idea').strip()}。"
        )
    if str(novelty_markdown or "").strip():
        executive_summary_lines.append("- 已补充 closest prior work、重叠风险与定位差异分析。")
    if str(review_markdown or "").strip():
        executive_summary_lines.append("- 已汇总 reviewer objections 与最小可执行修复建议。")

    lines = [
        "# Idea Discovery Report",
        "",
        f"**Direction**: {str(prompt or project_label).strip()}",
        f"**Project**: {project_label}",
        "**Pipeline**: research-lit -> idea-creator -> novelty-check -> research-review",
        "",
        "## Executive Summary",
        "\n".join(executive_summary_lines),
        "",
        "## Literature Landscape",
        sanitize_project_markdown(literature_markdown) or "待补充。",
        "",
        "## Ranked Ideas",
        _ideas_to_markdown(created_ideas) if created_ideas else "暂无结构化想法产出。",
    ]
    if str(novelty_markdown or "").strip():
        lines.extend(
            ["", "## Deep Novelty Verification", sanitize_project_markdown(novelty_markdown)]
        )
    if str(review_markdown or "").strip():
        lines.extend(
            ["", "## External Critical Review", sanitize_project_markdown(review_markdown)]
        )
    lines.extend(
        [
            "",
            "## Next Steps",
            "- [ ] 选定 Top idea，进入实现与实验阶段",
            "- [ ] 用 /run-experiment 或项目工作区实验运行全量验证",
            "- [ ] 如需持续打磨，进入 /auto-review-loop 或完整 /research-pipeline",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_novelty_check_report(
    project_label: str,
    prompt: str | None,
    comparison_markdown: str,
    verdict_markdown: str,
) -> str:
    cleaned_verdict = sanitize_project_markdown(verdict_markdown)
    lines = [
        "# 查新评估报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**评估对象**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 综合判断",
            *(_summary_bullets(cleaned_verdict) or ["- 已生成查新评估结论。"]),
            "",
            "## 相近工作对比",
            sanitize_project_markdown(comparison_markdown) or "待补充。",
            "",
            "## 查新结论",
            cleaned_verdict or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_research_review_report(
    project_label: str,
    prompt: str | None,
    review_markdown: str,
    verdict_markdown: str,
) -> str:
    cleaned_review = sanitize_project_markdown(review_markdown)
    cleaned_verdict = sanitize_project_markdown(verdict_markdown)
    score_line = _extract_score_line(cleaned_verdict) or _extract_score_line(cleaned_review)

    lines = [
        "# 研究评审报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**评审任务**: {str(prompt).strip()}")
    if score_line:
        lines.append(f"**评分摘要**: {score_line}")
    lines.extend(
        [
            "",
            "## 总体结论",
            *(_summary_bullets(cleaned_verdict) or ["- 已完成研究评审结论整理。"]),
            "",
            "## 详细评审",
            cleaned_review or "待补充。",
            "",
            "## 最终结论",
            cleaned_verdict or cleaned_review or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_experiment_report(
    project_label: str,
    prompt: str | None,
    summary_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    execution = (
        payload.get("execution_result") if isinstance(payload.get("execution_result"), dict) else {}
    )
    cleaned_summary = sanitize_project_markdown(summary_markdown)
    command = _clean_text(execution.get("command")) or _clean_text(payload.get("execution_command"))
    effective_command = _clean_text(execution.get("effective_command")) or _clean_text(
        payload.get("effective_execution_command")
    )
    workspace = _clean_text(execution.get("workspace_path")) or _clean_text(
        payload.get("execution_workspace")
    )
    remote_session = _clean_text(execution.get("remote_session_name")) or _clean_text(
        payload.get("remote_session_name")
    )
    remote_workspace = _clean_text(execution.get("remote_execution_workspace")) or _clean_text(
        payload.get("remote_execution_workspace")
    )
    remote_isolation_mode = _clean_text(execution.get("remote_isolation_mode")) or _clean_text(
        payload.get("remote_isolation_mode")
    )
    launch_status = _clean_text(payload.get("remote_launch_status")) or _clean_text(
        execution.get("mode")
    )
    gpu_text = _format_gpu(payload.get("selected_gpu") or execution.get("selected_gpu"))
    exit_code = execution.get("exit_code")
    stdout_excerpt = _plain_excerpt(execution.get("stdout"), limit=900)
    stderr_excerpt = _plain_excerpt(execution.get("stderr"), limit=700)

    summary_lines: list[str] = []
    if launch_status in {"running", "partial_running"}:
        summary_lines.append("- 远程实验已成功启动，当前可在后台持续监控。")
    elif exit_code == 0 or execution.get("success") is True:
        summary_lines.append("- 实验命令执行成功，已整理结果摘要。")
    elif command:
        summary_lines.append("- 实验已执行，请结合结果摘要和日志继续复核。")
    if command:
        summary_lines.append(f"- 主命令: `{command}`")
    if workspace:
        summary_lines.append(f"- 工作区: `{workspace}`")
    if remote_session:
        summary_lines.append(f"- 远程会话: `{remote_session}`")
    if gpu_text:
        summary_lines.append(f"- GPU: `{gpu_text}`")
    summary_lines.extend(_summary_bullets(cleaned_summary, max_items=4))

    config_lines: list[str] = []
    if command:
        config_lines.append(f"- 执行命令: `{command}`")
    if effective_command and effective_command != command:
        config_lines.append(f"- 实际执行命令: `{effective_command}`")
    if workspace:
        config_lines.append(f"- 执行工作区: `{workspace}`")
    if remote_workspace and remote_workspace != workspace:
        config_lines.append(f"- 远程隔离工作区: `{remote_workspace}`")
    if remote_session:
        config_lines.append(f"- 远程会话: `{remote_session}`")
    if remote_isolation_mode:
        config_lines.append(f"- 隔离模式: `{remote_isolation_mode}`")
    if gpu_text:
        config_lines.append(f"- GPU 分配: `{gpu_text}`")
    if exit_code is not None:
        config_lines.append(f"- 退出码: `{exit_code}`")
    if launch_status:
        config_lines.append(f"- 运行状态: `{launch_status}`")

    lines = [
        "# 实验运行报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**实验目标**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 执行摘要",
            *(summary_lines or ["- 已生成实验结果摘要。"]),
            "",
            "## 执行配置",
            *(config_lines or ["- 当前运行未记录额外执行配置。"]),
            "",
            "## 关键结果",
            cleaned_summary or "待补充。",
        ]
    )
    if stdout_excerpt:
        lines.extend(["", "## 运行输出摘录", stdout_excerpt])
    if stderr_excerpt:
        lines.extend(["", "## 异常与告警", stderr_excerpt])
    return sanitize_project_markdown("\n".join(lines).strip())


def format_paper_writing_report(
    project_label: str,
    prompt: str | None,
    final_manuscript_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    plan_markdown = _stage_content(stage_outputs, "gather_materials")
    figure_markdown = _stage_content(stage_outputs, "design_figures")
    compile_markdown = _stage_content(stage_outputs, "compile_manuscript")
    polish_payload = (
        stage_outputs.get("polish_manuscript")
        if isinstance(stage_outputs.get("polish_manuscript"), dict)
        else {}
    )
    final_draft = sanitize_project_markdown(final_manuscript_markdown) or _stage_content(
        stage_outputs, "polish_manuscript", "draft_sections"
    )
    venue = _resolve_paper_venue(payload)
    scores = (
        payload.get("paper_improvement_scores")
        if isinstance(payload.get("paper_improvement_scores"), dict)
        else {}
    )
    verdicts = (
        payload.get("paper_improvement_verdicts")
        if isinstance(payload.get("paper_improvement_verdicts"), dict)
        else {}
    )
    score_round_one = scores.get("round_1") or polish_payload.get("score_round_one")
    score_round_two = scores.get("round_2") or polish_payload.get("score_round_two")
    verdict_round_one = _clean_text(verdicts.get("round_1")) or _clean_text(
        polish_payload.get("verdict_round_one")
    )
    verdict_round_two = _clean_text(verdicts.get("round_2")) or _clean_text(
        polish_payload.get("verdict_round_two")
    )
    action_items_round_one = _normalize_string_list(polish_payload.get("action_items_round_one"))
    action_items_round_two = _normalize_string_list(polish_payload.get("action_items_round_two"))

    summary_lines = [
        "- 已完成写作规划、图表计划、初稿生成、编译检查和两轮改稿整理。",
    ]
    if venue:
        summary_lines.append(f"- 目标投稿模板: `{venue}`。")
    if score_round_two is not None:
        summary_lines.append(f"- 第二轮评审分数: `{score_round_two}`。")
    if verdict_round_two:
        summary_lines.append(f"- 第二轮评审结论: `{verdict_round_two}`。")
    elif verdict_round_one:
        summary_lines.append(f"- 第一轮评审结论: `{verdict_round_one}`。")
    summary_lines.extend(_summary_bullets(final_draft, max_items=4))

    lines = [
        "# 论文写作报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**写作任务**: {str(prompt).strip()}")
    if venue:
        lines.append(f"**目标模板**: {venue}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 写作规划",
            plan_markdown or "待补充。",
            "",
            "## 图表计划",
            figure_markdown or "待补充。",
            "",
            "## 编译状态",
            compile_markdown or "待补充。",
            "",
            "## 改稿结论",
        ]
    )
    if score_round_one is not None or verdict_round_one:
        lines.append(
            f"- Round 1: score=`{score_round_one if score_round_one is not None else 'N/A'}`, verdict=`{verdict_round_one or 'N/A'}`"
        )
    if action_items_round_one:
        lines.extend([f"- Round 1 action: {item}" for item in action_items_round_one[:6]])
    if score_round_two is not None or verdict_round_two:
        lines.append(
            f"- Round 2: score=`{score_round_two if score_round_two is not None else 'N/A'}`, verdict=`{verdict_round_two or 'N/A'}`"
        )
    if action_items_round_two:
        lines.extend([f"- Round 2 action: {item}" for item in action_items_round_two[:6]])
    if (
        not action_items_round_one
        and not action_items_round_two
        and score_round_one is None
        and score_round_two is None
    ):
        lines.append("- 当前运行未记录额外改稿结论。")
    lines.extend(
        [
            "",
            "## 最终稿正文",
            final_draft or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_paper_plan_report(
    project_label: str,
    prompt: str | None,
    plan_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    cleaned_plan = sanitize_project_markdown(plan_markdown)
    venue = _resolve_paper_venue(payload)
    template_name = _clean_text(payload.get("paper_template")) or _clean_text(
        payload.get("template_name")
    )

    summary_lines = [
        "- 已完成论文目标拆解、claims-evidence 对齐和章节规划。",
    ]
    if venue:
        summary_lines.append(f"- 目标 venue: `{venue}`。")
    if template_name:
        summary_lines.append(f"- 模板: `{template_name}`。")
    summary_lines.extend(_summary_bullets(cleaned_plan, max_items=4))

    lines = [
        "# 论文规划报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**规划任务**: {str(prompt).strip()}")
    if venue:
        lines.append(f"**目标模板**: {venue}")
    if template_name:
        lines.append(f"**模板名称**: {template_name}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 规划正文",
            cleaned_plan or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_paper_figure_report(
    project_label: str,
    prompt: str | None,
    figure_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    cleaned_figure = sanitize_project_markdown(figure_markdown)
    venue = _resolve_paper_venue(payload)

    summary_lines = [
        "- 已整理图表清单、数据来源和预期图表产物。",
    ]
    if venue:
        summary_lines.append(f"- 目标 venue: `{venue}`。")
    summary_lines.extend(_summary_bullets(cleaned_figure, max_items=4))

    lines = [
        "# 图表规划报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**图表任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 图表规划正文",
            cleaned_figure or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_paper_write_report(
    project_label: str,
    prompt: str | None,
    draft_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    cleaned_draft = sanitize_project_markdown(draft_markdown)
    venue = _resolve_paper_venue(payload)
    template_name = _clean_text(payload.get("paper_template")) or _clean_text(
        payload.get("template_name")
    )

    summary_lines = [
        "- 已生成论文工作区初稿，并输出标准 LaTeX 文件结构。",
        "- 默认产物包含 `paper/main.tex`、`paper/sections/*.tex` 与 `paper/references.bib`。",
    ]
    if venue:
        summary_lines.append(f"- 目标 venue: `{venue}`。")
    if template_name:
        summary_lines.append(f"- 模板: `{template_name}`。")
    summary_lines.extend(_summary_bullets(cleaned_draft, max_items=4))

    lines = [
        "# 论文初稿报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**写作任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 初稿正文",
            cleaned_draft or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_paper_compile_report(
    project_label: str,
    prompt: str | None,
    compile_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    compile_stage = (
        stage_outputs.get("run_compile")
        if isinstance(stage_outputs.get("run_compile"), dict)
        else {}
    )
    cleaned_compile = sanitize_project_markdown(compile_markdown)
    command = (
        _clean_text(compile_stage.get("command"))
        or _clean_text(payload.get("paper_compile_command"))
        or _clean_text(payload.get("compile_command"))
        or _clean_text(payload.get("execution_command"))
    )
    exit_code = compile_stage.get("exit_code")
    pdf_paths = (
        payload.get("compiled_pdf_paths")
        if isinstance(payload.get("compiled_pdf_paths"), list)
        else []
    )
    stdout_excerpt = _plain_excerpt(compile_stage.get("stdout"), limit=900)
    stderr_excerpt = _plain_excerpt(compile_stage.get("stderr"), limit=700)

    summary_lines = []
    if exit_code == 0:
        summary_lines.append("- 编译命令执行成功。")
    elif exit_code is not None:
        summary_lines.append(f"- 编译命令已执行，退出码为 `{exit_code}`。")
    else:
        summary_lines.append("- 已完成编译检查与产物整理。")
    if command:
        summary_lines.append(f"- 编译命令: `{command}`。")
    if pdf_paths:
        summary_lines.append(f"- 已发现 {len(pdf_paths)} 个 PDF 产物。")
    summary_lines.extend(_summary_bullets(cleaned_compile, max_items=4))

    lines = [
        "# 论文编译报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**编译任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 编译配置",
        ]
    )
    if command:
        lines.append(f"- 编译命令: `{command}`")
    if exit_code is not None:
        lines.append(f"- 退出码: `{exit_code}`")
    if not command and exit_code is None:
        lines.append("- 当前运行未记录额外编译配置。")
    if pdf_paths:
        lines.extend(["", "## PDF 产物", *[f"- `{item}`" for item in pdf_paths[:12]]])
    lines.extend(["", "## 编译结果", cleaned_compile or "待补充。"])
    if stdout_excerpt:
        lines.extend(["", "## 编译输出摘录", stdout_excerpt])
    if stderr_excerpt:
        lines.extend(["", "## 编译告警", stderr_excerpt])
    return sanitize_project_markdown("\n".join(lines).strip())


def format_paper_improvement_report(
    project_label: str,
    prompt: str | None,
    score_progress_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    review_round_one = _stage_content(stage_outputs, "diagnose_draft")
    revision_notes = _stage_content(stage_outputs, "revise_sections")
    review_round_two = _stage_content(stage_outputs, "final_check")
    cleaned_progress = sanitize_project_markdown(score_progress_markdown)
    scores = (
        payload.get("paper_improvement_scores")
        if isinstance(payload.get("paper_improvement_scores"), dict)
        else {}
    )
    verdicts = (
        payload.get("paper_improvement_verdicts")
        if isinstance(payload.get("paper_improvement_verdicts"), dict)
        else {}
    )
    action_items = (
        payload.get("paper_improvement_action_items")
        if isinstance(payload.get("paper_improvement_action_items"), dict)
        else {}
    )

    summary_lines = [
        "- 已完成两轮评审、修订记录与最终终检整理。",
    ]
    if scores.get("round_1") is not None or verdicts.get("round_1"):
        summary_lines.append(
            f"- Round 1: score=`{scores.get('round_1') if scores.get('round_1') is not None else 'N/A'}`, "
            f"verdict=`{verdicts.get('round_1') or 'N/A'}`。"
        )
    if scores.get("round_2") is not None or verdicts.get("round_2"):
        summary_lines.append(
            f"- Round 2: score=`{scores.get('round_2') if scores.get('round_2') is not None else 'N/A'}`, "
            f"verdict=`{verdicts.get('round_2') or 'N/A'}`。"
        )
    summary_lines.extend(_summary_bullets(cleaned_progress, max_items=4))

    lines = [
        "# 论文改稿报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**改稿任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## Round 1 评审",
            review_round_one or "待补充。",
            "",
            "## 修订记录",
            revision_notes or "待补充。",
            "",
            "## Round 2 终检",
            review_round_two or "待补充。",
            "",
            "## 评分进展",
            cleaned_progress or "待补充。",
        ]
    )
    round_one_actions = _normalize_string_list(action_items.get("round_1"))
    round_two_actions = _normalize_string_list(action_items.get("round_2"))
    if round_one_actions or round_two_actions:
        lines.extend(["", "## Action Items"])
        lines.extend([f"- Round 1: {item}" for item in round_one_actions[:6]])
        lines.extend([f"- Round 2: {item}" for item in round_two_actions[:6]])
    return sanitize_project_markdown("\n".join(lines).strip())


def format_full_pipeline_report(
    project_label: str,
    prompt: str | None,
    handoff_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    review_markdown = _stage_content(stage_outputs, "review_prior_work")
    findings_markdown = _stage_content(stage_outputs, "synthesize_findings")
    cleaned_handoff = sanitize_project_markdown(handoff_markdown) or _stage_content(
        stage_outputs, "handoff_output"
    )
    execution = (
        payload.get("execution_result") if isinstance(payload.get("execution_result"), dict) else {}
    )
    command = _clean_text(execution.get("command")) or _clean_text(payload.get("execution_command"))
    effective_command = _clean_text(execution.get("effective_command")) or _clean_text(
        payload.get("effective_execution_command")
    )
    workspace = _clean_text(execution.get("workspace_path")) or _clean_text(
        payload.get("execution_workspace")
    )
    stdout_excerpt = _plain_excerpt(execution.get("stdout"), limit=900)
    stderr_excerpt = _plain_excerpt(execution.get("stderr"), limit=700)

    summary_lines = [
        "- 已完成想法筛选、实现与实验、自动评审总结以及最终交付。",
    ]
    if command:
        summary_lines.append(f"- 主执行命令: `{command}`。")
    if workspace:
        summary_lines.append(f"- 执行工作区: `{workspace}`。")
    summary_lines.extend(
        _summary_bullets(cleaned_handoff or findings_markdown or review_markdown, max_items=4)
    )

    execution_lines: list[str] = []
    if command:
        execution_lines.append(f"- 执行命令: `{command}`")
    if effective_command and effective_command != command:
        execution_lines.append(f"- 实际执行命令: `{effective_command}`")
    if workspace:
        execution_lines.append(f"- 工作区: `{workspace}`")
    if execution.get("exit_code") is not None:
        execution_lines.append(f"- 退出码: `{execution.get('exit_code')}`")

    lines = [
        "# 科研流程交付报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**研究任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 交付摘要",
            *summary_lines,
            "",
            "## 想法关口",
            review_markdown or "待补充。",
            "",
            "## 实现与实验",
            *(execution_lines or ["- 当前运行未记录额外执行信息。"]),
        ]
    )
    if stdout_excerpt:
        lines.extend(["", "### 执行输出摘录", stdout_excerpt])
    if stderr_excerpt:
        lines.extend(["", "### 执行异常", stderr_excerpt])
    lines.extend(
        [
            "",
            "## 自动评审总结",
            findings_markdown or "待补充。",
            "",
            "## 最终交付",
            cleaned_handoff or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_rebuttal_report(
    project_label: str,
    prompt: str | None,
    final_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    reviews_markdown = _stage_content(stage_outputs, "normalize_reviews")
    issue_board_markdown = _stage_content(stage_outputs, "issue_board")
    strategy_markdown = _stage_content(stage_outputs, "strategy_plan")
    stress_markdown = _stage_content(stage_outputs, "stress_test")
    cleaned_final = sanitize_project_markdown(final_markdown) or _stage_content(
        stage_outputs, "finalize_package"
    )
    venue = _clean_text(payload.get("rebuttal_venue")) or "ICML"
    round_label = _clean_text(payload.get("rebuttal_round")) or "initial"
    character_limit = payload.get("rebuttal_character_limit")
    character_count = payload.get("rebuttal_character_count")
    quick_mode = bool(
        payload.get("rebuttal_quick_mode") is True
        or str(payload.get("rebuttal_quick_mode") or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    paste_ready_text = _clean_text(payload.get("paste_ready_text"))

    summary_lines = [
        "- 已完成 rebuttal 的 reviews 归档、问题拆解、策略规划和最终交付整理。",
        f"- Venue: `{venue}`，round: `{round_label}`。",
    ]
    if character_limit is not None:
        summary_lines.append(
            f"- 字符预算: `{character_count if character_count is not None else 'N/A'}` / `{character_limit}`。"
        )
    if quick_mode:
        summary_lines.append(
            "- 当前运行使用 quick mode，只输出 issue board 与 strategy plan，不生成最终提交稿。"
        )
    summary_lines.extend(
        _summary_bullets(cleaned_final or strategy_markdown or issue_board_markdown, max_items=4)
    )

    lines = [
        "# Rebuttal 报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**回复目标**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## Reviews Raw",
            reviews_markdown or "待补充。",
            "",
            "## Issue Board",
            issue_board_markdown or "待补充。",
            "",
            "## Strategy Plan",
            strategy_markdown or "待补充。",
        ]
    )
    if stress_markdown:
        lines.extend(["", "## Stress Test", stress_markdown])
    lines.extend(["", "## Final Rebuttal", cleaned_final or "待补充。"])
    if paste_ready_text:
        lines.extend(["", "## Paste Ready", paste_ready_text])
    return sanitize_project_markdown("\n".join(lines).strip())


def format_auto_review_loop_report(
    project_label: str,
    prompt: str | None,
    metadata: dict[str, Any] | None = None,
    raw_markdown: str | None = None,
) -> str:
    payload = dict(metadata or {})
    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    plan_markdown = _stage_content(stage_outputs, "plan_cycle")
    iterations = payload.get("iterations") if isinstance(payload.get("iterations"), list) else []
    cleaned_raw = sanitize_project_markdown(raw_markdown) if raw_markdown else ""
    latest = iterations[-1] if iterations and isinstance(iterations[-1], dict) else {}
    latest_review = latest.get("review") if isinstance(latest.get("review"), dict) else {}
    latest_execution = latest.get("execution") if isinstance(latest.get("execution"), dict) else {}
    latest_score = latest_review.get("score")
    latest_verdict = _clean_text(latest_review.get("verdict"))
    latest_summary = _clean_text(latest_review.get("summary"))
    execution_command = _clean_text(latest_execution.get("command")) or _clean_text(
        payload.get("execution_command")
    )

    summary_lines = [
        f"- 已完成 {len(iterations)} 轮自动评审循环。",
    ]
    if latest_verdict:
        summary_lines.append(f"- 最新 verdict: `{latest_verdict}`。")
    if latest_score is not None:
        summary_lines.append(f"- 最新 score: `{latest_score}`。")
    if latest_summary:
        summary_lines.append(f"- 最新评审摘要: {latest_summary}")
    if execution_command:
        summary_lines.append(f"- 评审执行命令: `{execution_command}`。")
    summary_lines.extend(_summary_bullets(cleaned_raw, max_items=3))

    lines = [
        "# 自动评审循环报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**评审目标**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 循环计划",
            plan_markdown or "待补充。",
            "",
            "## 轮次记录",
        ]
    )
    if iterations:
        for item in iterations:
            if not isinstance(item, dict):
                continue
            review = item.get("review") if isinstance(item.get("review"), dict) else {}
            execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
            round_number = item.get("iteration")
            lines.append(f"### Round {round_number}")
            round_command = _clean_text(execution.get("command"))
            round_effective = _clean_text(execution.get("effective_command"))
            round_workspace = _clean_text(execution.get("command_workspace_path"))
            round_score = review.get("score")
            round_verdict = _clean_text(review.get("verdict"))
            round_summary = _clean_text(review.get("summary"))
            if round_command:
                lines.append(f"- 命令: `{round_command}`")
            if round_effective and round_effective != round_command:
                lines.append(f"- 实际执行命令: `{round_effective}`")
            if round_workspace:
                lines.append(f"- 工作区: `{round_workspace}`")
            if round_score is not None or round_verdict:
                lines.append(
                    f"- 评审结果: score=`{round_score if round_score is not None else 'N/A'}`, verdict=`{round_verdict or 'N/A'}`"
                )
            if round_summary:
                lines.append(f"- 摘要: {round_summary}")
            issues = _normalize_string_list(review.get("issues"))
            next_actions = _normalize_string_list(review.get("next_actions"))
            pending_experiments = _normalize_string_list(review.get("pending_experiments"))
            for issue in issues[:4]:
                lines.append(f"- 问题: {issue}")
            for action in next_actions[:4]:
                lines.append(f"- 下一步: {action}")
            for pending in pending_experiments[:4]:
                lines.append(f"- 待实验: {pending}")
            execution_summary = sanitize_project_markdown(
                str(item.get("execution_summary") or "").strip()
            )
            if execution_summary:
                lines.extend(["", execution_summary, ""])
    else:
        lines.append("- 当前运行尚未记录轮次数据。")

    if cleaned_raw:
        lines.extend(["", "## 原始评审汇总", cleaned_raw])
    return sanitize_project_markdown("\n".join(lines).strip())


def format_monitor_experiment_report(
    project_label: str,
    prompt: str | None,
    inspect_markdown: str,
    signals_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    cleaned_inspect = sanitize_project_markdown(inspect_markdown)
    cleaned_signals = sanitize_project_markdown(signals_markdown)
    tracked_session_names = [
        str(item).strip()
        for item in (payload.get("remote_session_names") or [])
        if str(item).strip()
    ]
    tracked_session_name = _clean_text(payload.get("remote_session_name"))
    if tracked_session_name and tracked_session_name not in tracked_session_names:
        tracked_session_names.insert(0, tracked_session_name)
    workspace = (
        _extract_markdown_field(cleaned_signals, "Workspace")
        or _extract_markdown_field(cleaned_inspect, "Workspace")
        or _clean_text(payload.get("execution_workspace"))
    )
    server = (
        _extract_markdown_field(cleaned_signals, "Server")
        or _extract_markdown_field(cleaned_inspect, "Server")
        or _clean_text(payload.get("workspace_server_id"))
        or "local"
    )
    metric = _extract_markdown_field(cleaned_signals, "Metric")
    baseline = _extract_markdown_field(cleaned_signals, "Baseline")

    summary_lines = [
        "- 已完成工作区巡检、日志/指标信号收集和结构化监控总结。",
    ]
    if workspace:
        summary_lines.append(f"- 工作区: `{workspace}`。")
    if server:
        summary_lines.append(f"- 执行目标: `{server}`。")
    if tracked_session_names:
        summary_lines.append(f"- 正在追踪 {len(tracked_session_names)} 个后台会话。")
    if metric:
        summary_lines.append(f"- 当前对比主指标: `{metric}`。")
    if baseline:
        summary_lines.append(f"- 比较基线: `{baseline}`。")
    summary_lines.extend(_summary_bullets(cleaned_signals or cleaned_inspect, max_items=5))

    lines = [
        "# 实验监控报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**监控任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
        ]
    )
    if tracked_session_names:
        lines.extend(["", "## 追踪会话", *[f"- `{item}`" for item in tracked_session_names[:12]]])
    lines.extend(
        [
            "",
            "## 工作区巡检",
            cleaned_inspect or "待补充。",
            "",
            "## 指标与告警",
            cleaned_signals or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_experiment_audit_report(
    project_label: str,
    prompt: str | None,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    raw_markdown = sanitize_project_markdown(
        str(payload.get("workflow_output_markdown") or "").strip()
    )
    if raw_markdown.startswith("# Experiment Audit Report"):
        return raw_markdown

    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    audit_payload = _resolve_experiment_audit_payload(payload, stage_outputs)
    checks = audit_payload.get("checks") if isinstance(audit_payload.get("checks"), dict) else {}
    workspace = (
        _clean_text(payload.get("execution_workspace"))
        or _clean_text(payload.get("project_workspace_path"))
        or _clean_text((stage_outputs.get("collect_artifacts") or {}).get("workspace_path"))
    )
    summary = _clean_text(audit_payload.get("summary"))
    lines = [
        "# Experiment Audit Report",
        "",
        f"**Project**: {project_label}",
    ]
    if workspace:
        lines.append(f"**Workspace**: `{workspace}`")
    lines.append(
        f"**Overall Verdict**: {str(audit_payload.get('overall_verdict') or 'WARN').upper()}"
    )
    lines.append(
        f"**Integrity Status**: {str(audit_payload.get('integrity_status') or 'warn').lower()}"
    )
    lines.append(f"**Evaluation Type**: {str(audit_payload.get('evaluation_type') or 'unknown')}")
    if str(prompt or "").strip():
        lines.append(f"**Audit Scope**: {str(prompt).strip()}")
    if summary:
        lines.extend(["", "## Summary", summary])

    lines.extend(["", "## Checks"])
    for key, label in [
        ("gt_provenance", "A. Ground Truth Provenance"),
        ("score_normalization", "B. Score Normalization"),
        ("result_existence", "C. Result File Existence"),
        ("dead_code", "D. Dead Code Detection"),
        ("scope", "E. Scope Assessment"),
        ("eval_type", "F. Evaluation Type"),
    ]:
        item = checks.get(key) if isinstance(checks.get(key), dict) else {}
        status = str(item.get("status") or "WARN").upper()
        details = _clean_text(item.get("details")) or "待补充。"
        evidence = _normalize_string_list(item.get("evidence"))
        lines.extend(["", f"### {label}: {status}", details])
        if evidence:
            lines.append("")
            lines.append("Evidence:")
            lines.extend(f"- {entry}" for entry in evidence[:6])

    action_items = _normalize_string_list(audit_payload.get("action_items"))
    lines.extend(["", "## Action Items"])
    if action_items:
        lines.extend(f"- {item}" for item in action_items[:10])
    else:
        lines.append("- 当前没有额外 action item。")

    claims = audit_payload.get("claims") if isinstance(audit_payload.get("claims"), list) else []
    lines.extend(["", "## Claim Impact"])
    if claims:
        for item in claims[:8]:
            if not isinstance(item, dict):
                continue
            claim_id = _clean_text(item.get("id")) or "C?"
            impact = _clean_text(item.get("impact")) or "needs_qualifier"
            details = _clean_text(item.get("details"))
            line = f"- {claim_id}: {impact}"
            if details:
                line += f" | {details}"
            lines.append(line)
    else:
        lines.append("- 当前没有解析出显式 claim 影响，建议人工核对 narrative 和 paper 草稿。")

    if raw_markdown:
        lines.extend(["", "## Detailed Notes", raw_markdown])
    return sanitize_project_markdown("\n".join(lines).strip())


def format_sync_workspace_report(
    project_label: str,
    prompt: str | None,
    preview_markdown: str,
    sync_markdown: str,
    validation_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    cleaned_preview = sanitize_project_markdown(preview_markdown)
    cleaned_sync = sanitize_project_markdown(sync_markdown)
    cleaned_validation = sanitize_project_markdown(validation_markdown)
    source_workspace = _clean_text(payload.get("project_workspace_path"))
    target_workspace = _clean_text(payload.get("target_workspace_path"))
    source_server = _clean_text(payload.get("project_workspace_server_id")) or "local"
    target_server = (
        _clean_text(payload.get("target_workspace_server_id"))
        or _clean_text(payload.get("workspace_server_id"))
        or "local"
    )
    sync_strategy = _clean_text(payload.get("sync_strategy"))
    sync_mode = _extract_markdown_field(cleaned_sync, "Mode")
    sync_status = _extract_markdown_field(cleaned_sync, "Status")

    summary_lines = [
        "- 已完成同步预检查、真实文件同步和目标工作区校验。",
    ]
    if source_workspace:
        summary_lines.append(f"- 源工作区: `{source_workspace}` ({source_server})。")
    if target_workspace:
        summary_lines.append(f"- 目标工作区: `{target_workspace}` ({target_server})。")
    if sync_strategy:
        summary_lines.append(f"- 同步策略: `{sync_strategy}`。")
    if sync_mode:
        summary_lines.append(f"- 同步模式: `{sync_mode}`。")
    if sync_status:
        summary_lines.append(f"- 执行状态: `{sync_status}`。")
    summary_lines.extend(_summary_bullets(cleaned_sync or cleaned_validation, max_items=4))

    lines = [
        "# 工作区同步报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**同步任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 同步预检查",
            cleaned_preview or "待补充。",
            "",
            "## 同步执行",
            cleaned_sync or "待补充。",
            "",
            "## 目标校验",
            cleaned_validation or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def format_custom_run_report(
    project_label: str,
    prompt: str | None,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = dict(metadata or {})
    execution = (
        payload.get("execution_result") if isinstance(payload.get("execution_result"), dict) else {}
    )
    cleaned_body = sanitize_project_markdown(body)
    command = (
        _clean_text(execution.get("command"))
        or _clean_text(payload.get("execution_command"))
        or _clean_text(payload.get("command"))
    )
    workspace = _clean_text(execution.get("workspace_path")) or _clean_text(
        payload.get("execution_workspace")
    )
    exit_code = execution.get("exit_code")

    summary_lines = [
        "- 已完成自定义工作流执行，下面保留本次运行的结构化正文。",
    ]
    if command:
        summary_lines.append(f"- 命令: `{command}`。")
    if workspace:
        summary_lines.append(f"- 工作区: `{workspace}`。")
    if exit_code is not None:
        summary_lines.append(f"- 退出码: `{exit_code}`。")
    summary_lines.extend(_summary_bullets(cleaned_body, max_items=4))

    lines = [
        "# 自定义工作流报告",
        "",
        f"**项目**: {project_label}",
    ]
    if str(prompt or "").strip():
        lines.append(f"**运行任务**: {str(prompt).strip()}")
    lines.extend(
        [
            "",
            "## 当前结论",
            *summary_lines,
            "",
            "## 完整输出",
            cleaned_body or "待补充。",
        ]
    )
    return sanitize_project_markdown("\n".join(lines).strip())


def build_workflow_report_markdown(
    *,
    workflow_type: str,
    project_label: str,
    prompt: str | None,
    metadata: dict[str, Any] | None,
) -> str | None:
    payload = dict(metadata or {})
    stage_outputs = (
        payload.get("stage_outputs") if isinstance(payload.get("stage_outputs"), dict) else {}
    )
    raw_markdown = sanitize_project_markdown(
        str(payload.get("workflow_output_markdown") or "").strip()
    )
    workflow_key = str(workflow_type or "").strip()

    if workflow_key == "literature_review":
        body = (
            _stage_content(stage_outputs, "deliver_review", "synthesize_evidence") or raw_markdown
        )
        return format_literature_review_report(project_label, prompt, body) if body else None

    if workflow_key == "idea_discovery":
        created_ideas = list(payload.get("created_ideas") or [])
        literature_markdown = _stage_content(stage_outputs, "collect_context")
        novelty_markdown = _stage_content(stage_outputs, "verify_novelty")
        review_markdown = _stage_content(stage_outputs, "external_review")
        if created_ideas or literature_markdown or novelty_markdown or review_markdown:
            return format_idea_discovery_report(
                project_label,
                prompt,
                literature_markdown or raw_markdown,
                created_ideas,
                novelty_markdown,
                review_markdown,
            )
        return raw_markdown or None

    if workflow_key == "novelty_check":
        comparison_markdown = _stage_content(stage_outputs, "compare_prior_work")
        verdict_markdown = _stage_content(stage_outputs, "issue_novelty_report") or raw_markdown
        return (
            format_novelty_check_report(
                project_label, prompt, comparison_markdown, verdict_markdown
            )
            if verdict_markdown
            else None
        )

    if workflow_key == "research_review":
        review_markdown = _stage_content(stage_outputs, "review_submission")
        verdict_markdown = _stage_content(stage_outputs, "deliver_verdict") or raw_markdown
        return (
            format_research_review_report(project_label, prompt, review_markdown, verdict_markdown)
            if verdict_markdown
            else None
        )

    if workflow_key == "run_experiment":
        summary_markdown = _stage_content(stage_outputs, "summarize_results") or raw_markdown
        return (
            format_experiment_report(project_label, prompt, summary_markdown, payload)
            if summary_markdown or payload.get("execution_result")
            else None
        )

    if workflow_key == "experiment_audit":
        return (
            format_experiment_audit_report(project_label, prompt, payload)
            if raw_markdown or stage_outputs or payload.get("audit_payload")
            else None
        )

    if workflow_key == "paper_writing":
        final_manuscript_markdown = (
            _stage_content(stage_outputs, "polish_manuscript", "draft_sections") or raw_markdown
        )
        return (
            format_paper_writing_report(project_label, prompt, final_manuscript_markdown, payload)
            if final_manuscript_markdown or stage_outputs
            else None
        )

    if workflow_key == "rebuttal":
        final_rebuttal_markdown = (
            _stage_content(stage_outputs, "finalize_package", "draft_rebuttal") or raw_markdown
        )
        return (
            format_rebuttal_report(project_label, prompt, final_rebuttal_markdown, payload)
            if final_rebuttal_markdown or stage_outputs
            else None
        )

    if workflow_key == "paper_plan":
        plan_markdown = (
            _stage_content(stage_outputs, "outline_manuscript", "collect_materials") or raw_markdown
        )
        return (
            format_paper_plan_report(project_label, prompt, plan_markdown, payload)
            if plan_markdown or stage_outputs
            else None
        )

    if workflow_key == "paper_figure":
        figure_markdown = (
            _stage_content(stage_outputs, "design_figures", "collect_results") or raw_markdown
        )
        return (
            format_paper_figure_report(project_label, prompt, figure_markdown, payload)
            if figure_markdown or stage_outputs
            else None
        )

    if workflow_key == "paper_write":
        draft_markdown = (
            _stage_content(stage_outputs, "draft_sections", "gather_materials") or raw_markdown
        )
        return (
            format_paper_write_report(project_label, prompt, draft_markdown, payload)
            if draft_markdown or stage_outputs
            else None
        )

    if workflow_key == "paper_compile":
        compile_markdown = (
            _stage_content(stage_outputs, "run_compile", "prepare_compile") or raw_markdown
        )
        return (
            format_paper_compile_report(project_label, prompt, compile_markdown, payload)
            if compile_markdown or stage_outputs
            else None
        )

    if workflow_key == "paper_improvement":
        score_progress_markdown = raw_markdown or _stage_content(
            stage_outputs, "final_check", "revise_sections", "diagnose_draft"
        )
        return (
            format_paper_improvement_report(project_label, prompt, score_progress_markdown, payload)
            if score_progress_markdown or stage_outputs
            else None
        )

    if workflow_key == "full_pipeline":
        handoff_markdown = _stage_content(stage_outputs, "handoff_output") or raw_markdown
        return (
            format_full_pipeline_report(project_label, prompt, handoff_markdown, payload)
            if handoff_markdown or stage_outputs
            else None
        )

    if workflow_key == "auto_review_loop":
        return (
            format_auto_review_loop_report(project_label, prompt, payload, raw_markdown)
            if raw_markdown or payload.get("iterations") or stage_outputs
            else None
        )

    if workflow_key == "monitor_experiment":
        inspect_markdown = _stage_content(stage_outputs, "inspect_runs")
        signals_markdown = _stage_content(stage_outputs, "collect_signals") or raw_markdown
        return (
            format_monitor_experiment_report(
                project_label, prompt, inspect_markdown, signals_markdown, payload
            )
            if inspect_markdown or signals_markdown or stage_outputs
            else None
        )

    if workflow_key == "sync_workspace":
        preview_markdown = _stage_content(stage_outputs, "scan_diff")
        sync_markdown = _stage_content(stage_outputs, "sync_paths") or raw_markdown
        validation_markdown = _stage_content(stage_outputs, "validate_state")
        return (
            format_sync_workspace_report(
                project_label, prompt, preview_markdown, sync_markdown, validation_markdown, payload
            )
            if preview_markdown or sync_markdown or validation_markdown or stage_outputs
            else None
        )

    if workflow_key == "custom_run":
        return (
            format_custom_run_report(project_label, prompt, raw_markdown, payload)
            if raw_markdown or payload.get("execution_result")
            else None
        )

    return raw_markdown or None


def _stage_content(stage_outputs: dict[str, Any], *stage_ids: str) -> str:
    for stage_id in stage_ids:
        item = stage_outputs.get(stage_id)
        if not isinstance(item, dict):
            continue
        content = sanitize_project_markdown(str(item.get("content") or "").strip())
        if content:
            return content
    return ""


def _ideas_to_markdown(ideas: list[dict[str, Any]]) -> str:
    parts = ["# 想法发现结果", ""]
    for index, item in enumerate(ideas, start=1):
        title = str(item.get("title") or f"想法 {index}").strip()
        parts.append(f"## {index}. {title}")
        parts.append(str(item.get("content") or "").strip() or "待补充。")
        parts.append("")
    return "\n".join(parts).strip()


def _summary_bullets(markdown: str, *, max_items: int = 3) -> list[str]:
    lines = [line.strip() for line in str(markdown or "").splitlines()]
    bullets: list[str] = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        normalized = re.sub(r"^[\-\*\d\.\)\s]+", "", line).strip()
        if not normalized:
            continue
        if len(normalized) > 120:
            normalized = normalized[:119].rstrip() + "…"
        bullets.append(f"- {normalized}")
        if len(bullets) >= max_items:
            break
    if bullets:
        return bullets

    excerpt = markdown_excerpt(markdown, limit=320)
    if excerpt:
        return [f"- {excerpt}"]
    return []


def _extract_score_line(markdown: str) -> str | None:
    match = re.search(
        r"\bscore\b\s*[:：]\s*([0-9]+(?:\.[0-9]+)?(?:\s*/\s*10)?)", markdown, re.IGNORECASE
    )
    if match:
        return match.group(1)
    return None


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _extract_markdown_field(markdown: str, label: str) -> str | None:
    pattern = re.compile(
        rf"^[\-\*]\s*{re.escape(label)}\s*:\s*`?(.+?)`?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(str(markdown or ""))
    if not match:
        return None
    return _clean_text(match.group(1))


def _plain_excerpt(value: Any, *, limit: int = 600) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_gpu(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    index = value.get("index")
    name = _clean_text(value.get("name"))
    memory = value.get("memory_used_mb")
    if index is None and not name:
        return ""
    parts = [f"GPU {index}" if index is not None else "GPU"]
    if name:
        parts.append(name)
    if memory is not None:
        parts.append(f"{memory} MB used")
    return " / ".join(parts)


def _resolve_paper_venue(metadata: dict[str, Any]) -> str:
    for key in ("venue", "paper_venue", "target_venue"):
        value = _clean_text(metadata.get(key))
        if value:
            return value.upper()
    return "ICLR"


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        return {}
    if value.startswith("```"):
        segments = value.split("```")
        if len(segments) >= 3:
            block = segments[1]
            if "\n" in block:
                block = block.split("\n", 1)[1]
            value = block.strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_experiment_audit_payload(
    metadata: dict[str, Any],
    stage_outputs: dict[str, Any],
) -> dict[str, Any]:
    direct_payload = metadata.get("audit_payload")
    if isinstance(direct_payload, dict) and direct_payload:
        return direct_payload
    review_stage = (
        stage_outputs.get("review_integrity")
        if isinstance(stage_outputs.get("review_integrity"), dict)
        else {}
    )
    staged_payload = review_stage.get("audit_payload")
    if isinstance(staged_payload, dict) and staged_payload:
        return staged_payload
    parsed = _extract_json_object(str(review_stage.get("content") or ""))
    return parsed if parsed else {}
