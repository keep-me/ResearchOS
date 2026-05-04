from __future__ import annotations

import json
import re
from typing import Any

PAPER_TEMPLATE_BY_VENUE = {
    "ICLR": "iclr2026.tex",
    "NEURIPS": "neurips2025.tex",
    "ICML": "icml2025.tex",
}

_REVIEW_SCORE_PATTERNS = (
    r"[Ss]core\s*[:=]\s*(\d+(?:\.\d+)?)\s*/\s*10",
    r"(\d+(?:\.\d+)?)\s*/\s*10",
    r"[Ss]core\s*[:=]\s*(\d+(?:\.\d+)?)\b",
    r"[Ss]core\s+(?:of\s+)?(\d+(?:\.\d+)?)\b",
)
_REVIEW_READY_PATTERN = re.compile(
    r"\b(ready for submission|accept|sufficient|ready)\b", re.IGNORECASE
)
_REVIEW_ALMOST_PATTERN = re.compile(r"\b(almost|close|minor revisions)\b", re.IGNORECASE)
_REVIEW_ACTION_HEADER_PATTERN = re.compile(
    r"(?i)^(weakness|weaknesses|fix|fixes|action|actions|recommendation|recommendations|suggestion|suggestions|critical|major|minor)"
)
_REVIEW_ACTION_ITEM_PATTERN = re.compile(r"^(?:\d+[\.\)]|[-*])\s+")
_REVIEW_NUMBERED_ITEM_PATTERN = re.compile(r"^\d+[\.\)]\s+(.+)")


def resolve_paper_venue(metadata: dict[str, Any] | None) -> tuple[str, str]:
    payload = dict(metadata or {})
    for key in ("venue", "paper_venue", "target_venue"):
        value = str(payload.get(key) or "").strip().upper()
        if value in PAPER_TEMPLATE_BY_VENUE:
            return value, PAPER_TEMPLATE_BY_VENUE[value]
    return "ICLR", PAPER_TEMPLATE_BY_VENUE["ICLR"]


def build_paper_plan_bundle(
    *,
    project_name: str,
    project_description: str,
    prompt: str,
    stage_markdown: str,
    paper_summaries: list[str],
    venue: str,
    template_name: str,
) -> dict[str, str]:
    claims_rows = [
        (
            "问题价值",
            _truncate(prompt or project_description or "待明确研究问题与目标。", 180),
            "项目任务描述、现有论文与研究背景",
            "已有初始证据",
        ),
        (
            "方法贡献",
            _truncate(stage_markdown or "待明确方法主张与实现路径。", 180),
            "论文规划阶段输出、项目说明、代码仓库",
            "需要进一步细化",
        ),
        (
            "实验验证",
            "通过主结果表、消融实验、误差分析验证关键贡献。",
            "实验脚本、结果文件、图表计划",
            "待补实验数据",
        ),
    ]
    lines = [
        "# PAPER_PLAN",
        "",
        f"- Project: {project_name}",
        f"- Venue: {venue}",
        f"- Template: {template_name}",
        "",
        "## Narrative Goal",
        prompt.strip() or project_description.strip() or "待补充写作目标。",
        "",
        "## Claims-Evidence Matrix",
        "",
        "| Claim | Current Framing | Evidence Source | Status |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {claim} | {framing} | {evidence} | {status} |"
        for claim, framing, evidence, status in claims_rows
    )
    lines.extend(
        [
            "",
            "## Section Plan",
            "",
            "1. Abstract: 提炼问题、方法与最强结果。",
            "2. Introduction: 说明问题背景、痛点与核心贡献。",
            "3. Related Work: 对比最接近方法并突出差异。",
            "4. Method: 给出方法框架、关键模块与训练/推理流程。",
            "5. Experiments: 主结果、消融、误差分析与案例。",
            "6. Limitations: 资源约束、外推边界与潜在风险。",
            "7. Conclusion: 总结贡献并给出下一步工作。",
            "",
            "## Figure/Table Plan",
            "",
            "| ID | Type | Purpose | Expected Source |",
            "| --- | --- | --- | --- |",
            "| Fig.1 | Pipeline figure | 总览方法流程与组件关系 | 手工架构图或现有设计稿 |",
            "| Table.1 | Main results | 对比主结果与代表性基线 | 实验输出 / CSV / JSON |",
            "| Fig.2 | Training curve | 展示收敛趋势或奖励变化 | 训练日志 / metrics.json |",
            "| Table.2 | Ablation | 拆分关键模块效果 | 消融结果文件 |",
            "",
            "## Citation Scaffolding",
        ]
    )
    if paper_summaries:
        lines.extend([f"- {summary}" for summary in paper_summaries[:8]])
    else:
        lines.append("- 当前项目尚未关联足够论文，后续需要补 BibTeX 与相关工作证据。")
    lines.extend(
        [
            "",
            "## Planner Notes",
            stage_markdown.strip() or "待补充更细的章节与证据拆解。",
            "",
        ]
    )
    metadata = {
        "project_name": project_name,
        "venue": venue,
        "template": template_name,
        "section_order": [
            "abstract",
            "introduction",
            "related_work",
            "method",
            "experiments",
            "limitations",
            "conclusion",
        ],
    }
    return {
        "reports/PAPER_PLAN.md": "\n".join(lines).strip() + "\n",
        "reports/paper-plan-metadata.json": json.dumps(metadata, ensure_ascii=False, indent=2)
        + "\n",
    }


def build_figure_bundle(
    *,
    project_name: str,
    prompt: str,
    stage_markdown: str,
    venue: str,
) -> dict[str, str]:
    plan_lines = [
        "# FIGURE_PLAN",
        "",
        f"- Project: {project_name}",
        f"- Venue: {venue}",
        "",
        "## Figure Inventory",
        "",
        "| ID | Kind | Input Data | Output Form | Notes |",
        "| --- | --- | --- | --- | --- |",
        "| fig_training_curve | curve | metrics.json / CSV | PDF + PNG | 训练或奖励变化曲线 |",
        "| table_main_results | table | main_results.json / CSV | LaTeX table | 主结果对比表 |",
        "| table_ablation | table | ablation.json / CSV | LaTeX table | 关键模块消融 |",
        "| fig_error_breakdown | bar | eval_breakdown.json | PDF + PNG | 错误类型或类别分解 |",
        "",
        "## Manual Figure Placeholders",
        "- `figures/architecture_manual.pdf`: 方法总览图，需要手工绘制后放入该目录。",
        "- `figures/qualitative_manual.png`: 定性案例图，需要手工补充。",
        "",
        "## Figure Notes",
        stage_markdown.strip() or prompt.strip() or "待补充图表说明。",
        "",
    ]
    latex_includes = (
        "\n".join(
            [
                "% Auto-generated by ResearchOS paper figure workflow",
                "\\usepackage{graphicx}",
                "\\usepackage{booktabs}",
                "\\usepackage{subcaption}",
                "",
                "% Example figure/table include points",
                "% \\input{../figures/table_main_results.tex}",
                "% \\input{../figures/table_ablation.tex}",
                "",
            ]
        ).strip()
        + "\n"
    )
    table_main = "\n".join(
        [
            "% Auto-generated placeholder main results table",
            "\\begin{table}[t]",
            "\\centering",
            "\\caption{Main results placeholder. Replace with real metrics before submission.}",
            "\\begin{tabular}{lcc}",
            "\\toprule",
            "Method & Metric A & Metric B \\\\",
            "\\midrule",
            "Baseline & TBD & TBD \\\\",
            "Ours & TBD & TBD \\\\",
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    table_ablation = "\n".join(
        [
            "% Auto-generated placeholder ablation table",
            "\\begin{table}[t]",
            "\\centering",
            "\\caption{Ablation placeholder.}",
            "\\begin{tabular}{lc}",
            "\\toprule",
            "Variant & Score \\\\",
            "\\midrule",
            "Full model & TBD \\\\",
            "w/o key module & TBD \\\\",
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    manifest = {
        "project_name": project_name,
        "venue": venue,
        "artifacts": [
            {
                "id": "fig_training_curve",
                "relative_path": "figures/fig_training_curve.png",
                "kind": "figure",
            },
            {
                "id": "table_main_results",
                "relative_path": "figures/table_main_results.tex",
                "kind": "table",
            },
            {
                "id": "table_ablation",
                "relative_path": "figures/table_ablation.tex",
                "kind": "table",
            },
        ],
    }
    return {
        "figures/FIGURE_PLAN.md": "\n".join(plan_lines).strip() + "\n",
        "figures/latex_includes.tex": latex_includes,
        "figures/table_main_results.tex": table_main,
        "figures/table_ablation.tex": table_ablation,
        "figures/figure_manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    }


def build_paper_write_bundle(
    *,
    project_name: str,
    project_description: str,
    prompt: str,
    stage_markdown: str,
    venue: str,
    template_name: str,
    paper_titles: list[str],
) -> dict[str, str]:
    title = _truncate(project_name.strip() or "ResearchOS Paper Draft", 96)
    abstract = (
        f"We study {project_name}. "
        "This manuscript draft is generated from the current ResearchOS project context and should be refined with real experiment evidence before submission."
    )
    intro = (
        project_description.strip()
        or prompt.strip()
        or "This project investigates a research problem that still requires a sharper problem statement."
    )
    related = (
        "Related work currently includes: "
        + (
            ", ".join(paper_titles[:5])
            if paper_titles
            else "TBD references from the linked paper library"
        )
        + "."
    )
    method = _truncate(
        stage_markdown
        or "Method details should be refined from the current workflow output and implementation notes.",
        1200,
    )
    experiments = (
        "Experiments should report the main comparison table, at least one ablation study, and one error analysis or robustness slice. "
        "Replace all TBD placeholders with actual metrics before compiling for submission."
    )
    limitations = "Current limitations include incomplete evidence, missing figure assets, and pending bibliography verification."
    conclusion = "The next step is to replace placeholders with verified results, figures, and references, then run compile and improvement loops."

    references_bib = _build_reference_bib(paper_titles)
    sections = {
        "paper/sections/abstract.tex": "\n".join(
            ["\\begin{abstract}", _latex_escape(abstract), "\\end{abstract}", ""]
        )
        + "\n",
        "paper/sections/introduction.tex": _section_tex("Introduction", intro),
        "paper/sections/related_work.tex": _section_tex("Related Work", related),
        "paper/sections/method.tex": _section_tex("Method", method),
        "paper/sections/experiments.tex": _section_tex("Experiments", experiments),
        "paper/sections/limitations.tex": _section_tex("Limitations", limitations),
        "paper/sections/conclusion.tex": _section_tex("Conclusion", conclusion),
    }
    main_tex = "\n".join(
        [
            "% Auto-generated by ResearchOS paper write workflow",
            "\\documentclass{article}",
            "\\input{../figures/latex_includes.tex}",
            "\\title{" + _latex_escape(title) + "}",
            "\\author{ResearchOS}",
            "\\date{}",
            "\\begin{document}",
            "\\maketitle",
            "\\input{sections/abstract}",
            "\\input{sections/introduction}",
            "\\input{sections/related_work}",
            "\\input{sections/method}",
            "\\input{sections/experiments}",
            "\\input{sections/limitations}",
            "\\input{sections/conclusion}",
            "\\bibliographystyle{plain}",
            "\\bibliography{references}",
            "\\end{document}",
            "",
        ]
    )
    readme = "\n".join(
        [
            "# Paper Workspace",
            "",
            f"- Venue: {venue}",
            f"- Template: {template_name}",
            "- Main entry: `paper/main.tex`",
            "- Sections: `paper/sections/*.tex`",
            "- Figures: `figures/latex_includes.tex` and `figures/*.tex`",
            "",
        ]
    )
    bundle = {
        "paper/README.md": readme,
        "paper/main.tex": main_tex,
        "paper/references.bib": references_bib,
        "paper/write-metadata.json": json.dumps(
            {
                "project_name": project_name,
                "venue": venue,
                "template": template_name,
                "paper_title": title,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    bundle.update(sections)
    return bundle


def build_paper_compile_bundle(
    *,
    project_name: str,
    compile_command: str,
    exit_code: int | None,
    pdf_paths: list[str],
    stdout_text: str,
    stderr_text: str,
) -> dict[str, str]:
    lines = [
        "# PAPER_COMPILE",
        "",
        f"- Project: {project_name}",
        f"- Command: `{compile_command or 'N/A'}`",
        f"- Exit Code: {exit_code if exit_code is not None else 'N/A'}",
        "",
        "## PDF Outputs",
    ]
    if pdf_paths:
        lines.extend([f"- `{path}`" for path in pdf_paths])
    else:
        lines.append("- 未发现 PDF 输出，请检查编译入口、依赖或日志。")
    lines.extend(
        [
            "",
            "## Stdout",
            "```text",
            _truncate(stdout_text or "N/A", 4000),
            "```",
            "",
            "## Stderr",
            "```text",
            _truncate(stderr_text or "N/A", 3000),
            "```",
            "",
            "## Page Check",
            "- 若已生成 PDF，请进一步核查页数、参考文献、浮动体位置与超页情况。",
            "",
        ]
    )
    return {
        "reports/PAPER_COMPILE.md": "\n".join(lines).strip() + "\n",
    }


def build_paper_improvement_bundle(
    *,
    project_name: str,
    review_round_one: str,
    revision_notes: str,
    review_round_two: str,
    score_round_one: float | None,
    score_round_two: float | None,
    verdict_round_one: str | None = None,
    verdict_round_two: str | None = None,
    action_items_round_one: list[str] | None = None,
    action_items_round_two: list[str] | None = None,
) -> dict[str, str]:
    progression_lines = [
        "# SCORE_PROGRESSION",
        "",
        "| Round | Focus | Score | Verdict |",
        "| --- | --- | --- | --- |",
        f"| 1 | 内容评审 | {_score_text(score_round_one)} | {_verdict_text(verdict_round_one)} |",
        f"| 2 | 修订后复审 | {_score_text(score_round_two)} | {_verdict_text(verdict_round_two)} |",
        "",
    ]
    format_lines = [
        "# FORMAT_CHECK",
        "",
        "- [ ] 标题与摘要是否对齐贡献主张",
        "- [ ] 图表与正文交叉引用是否完整",
        "- [ ] 参考文献键值与正文引用是否一致",
        "- [ ] 页数、附录与致谢是否符合投稿要求",
        "",
        "## Final Review Notes",
        review_round_two.strip() or "待补充最终格式与投稿检查结果。",
        "",
    ]
    final_action_items = list(action_items_round_two or action_items_round_one or [])
    if final_action_items:
        format_lines.extend(
            [
                "## Structured Action Items",
                *[f"- {item}" for item in final_action_items],
                "",
            ]
        )
    return {
        "reports/paper-improvement-round1.md": (
            review_round_one.strip() or "待补充第一轮评审。"
        ).rstrip()
        + "\n",
        "reports/paper-improvement-round2.md": (
            review_round_two.strip() or "待补充第二轮评审。"
        ).rstrip()
        + "\n",
        "reports/paper-revision-notes.md": (revision_notes.strip() or "待补充修订记录。").rstrip()
        + "\n",
        "reports/paper-score-progression.md": "\n".join(progression_lines).strip() + "\n",
        "reports/paper-format-check.md": "\n".join(format_lines).strip() + "\n",
        "paper/improvement-metadata.json": json.dumps(
            {
                "project_name": project_name,
                "score_round_one": score_round_one,
                "score_round_two": score_round_two,
                "verdict_round_one": verdict_round_one,
                "verdict_round_two": verdict_round_two,
                "action_items_round_one": list(action_items_round_one or []),
                "action_items_round_two": list(action_items_round_two or []),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }


def parse_review_text(text: str) -> dict[str, Any]:
    value = str(text or "")
    return {
        "score": _extract_review_score(value),
        "verdict": extract_review_verdict(value),
        "action_items": extract_review_action_items(value),
    }


def extract_score(text: str) -> float | None:
    return _extract_review_score(str(text or ""))


def extract_review_verdict(text: str) -> str:
    value = str(text or "")
    if _REVIEW_READY_PATTERN.search(value):
        return "ready"
    if _REVIEW_ALMOST_PATTERN.search(value):
        return "almost"
    return "not ready"


def extract_review_action_items(text: str) -> list[str]:
    value = str(text or "")
    lines = value.splitlines()
    action_items: list[str] = []
    in_action_section = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _REVIEW_ACTION_HEADER_PATTERN.match(stripped):
            in_action_section = True
            continue
        if in_action_section and _REVIEW_ACTION_ITEM_PATTERN.match(stripped):
            item = _REVIEW_ACTION_ITEM_PATTERN.sub("", stripped).strip()
            if item:
                action_items.append(item)
            continue
        if in_action_section and len(stripped) > 50:
            in_action_section = False

    if action_items:
        return action_items

    for line in lines:
        stripped = line.strip()
        match = _REVIEW_NUMBERED_ITEM_PATTERN.match(stripped)
        if match and len(match.group(1).strip()) > 10:
            action_items.append(match.group(1).strip())
    return action_items


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": "\\textbackslash{}",
        "&": "\\&",
        "%": "\\%",
        "$": "\\$",
        "#": "\\#",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
    }
    escaped = "".join(replacements.get(char, char) for char in str(text or ""))
    return escaped.replace("^", "\\textasciicircum{}").replace("~", "\\textasciitilde{}")


def _section_tex(title: str, body: str) -> str:
    paragraph = _latex_escape(_truncate(body or "TBD", 3000))
    return "\n".join([f"\\section{{{_latex_escape(title)}}}", paragraph, ""]) + "\n"


def _build_reference_bib(paper_titles: list[str]) -> str:
    entries: list[str] = []
    for index, title in enumerate(paper_titles[:8], start=1):
        key = f"paper{index}"
        entries.extend(
            [
                f"@misc{{{key},",
                f"  title = {{{_latex_escape(title)}}},",
                "  author = {TBD},",
                "  year = {2026},",
                "  note = {Imported from ResearchOS paper library}",
                "}",
                "",
            ]
        )
    if not entries:
        entries = [
            "@misc{placeholder_reference,",
            "  title = {Placeholder Reference},",
            "  author = {TBD},",
            "  year = {2026},",
            "  note = {Replace with verified bibliography entries before submission}",
            "}",
            "",
        ]
    return "\n".join(entries)


def _score_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}"


def _verdict_text(value: str | None) -> str:
    resolved = str(value or "").strip()
    return resolved or "N/A"


def _extract_review_score(text: str) -> float | None:
    value = str(text or "")
    for pattern in _REVIEW_SCORE_PATTERNS:
        match = re.search(pattern, value)
        if not match:
            continue
        try:
            return float(match.group(1))
        except ValueError:
            continue
    return None


def _truncate(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
