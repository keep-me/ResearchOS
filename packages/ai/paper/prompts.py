"""
LLM Prompt 模板
@author Bamzc
"""

from packages.ai.paper.analysis_options import normalize_analysis_detail_level


def _deep_detail_guidance(detail_level: str) -> str:
    if detail_level == "low":
        return (
            "本次为低详略精读：优先抓住论文主线，只总结最关键的方法、实验结论和 2-3 个主要风险。"
            " method_summary / experiments_summary / ablation_summary 保持紧凑，避免展开次要细节。"
        )
    if detail_level == "high":
        return (
            "本次为高详略精读：尽量展开方法动机、关键设计、实验设置、消融洞察和潜在风险。"
            " reviewer_risks 建议给出 5-6 条，并尽量指出风险对应的依据。"
        )
    return (
        "本次为中等详略精读：在方法、实验、消融和风险上给出较完整总结，兼顾准确性与篇幅。"
        " reviewer_risks 建议给出 3-5 条。"
    )


def _reasoning_detail_guidance(detail_level: str) -> str:
    if detail_level == "low":
        return (
            "本次为低详略推理：以核心判断为主，避免扩展到次要猜测。"
            " reasoning_steps 保留 5 个核心步骤，每步尽量简洁聚焦。"
        )
    if detail_level == "high":
        return (
            "本次为高详略推理：请充分展开每一步推理依据，尽量结合全文摘录与已有分析，"
            "补充关键假设、理论依据、实验证据和局限讨论。"
        )
    return (
        "本次为中等详略推理：保持完整的推理链，同时控制篇幅，优先覆盖最重要的逻辑和证据。"
    )


def build_skim_prompt(title: str, abstract: str) -> str:
    return (
        "你是科研助手。请根据标题和摘要输出严格 JSON：\n"
        '{"one_liner":"一句话中文总结", '
        '"innovations":["创新点1","创新点2","创新点3"], '
        '"keywords":["keyword1","keyword2","keyword3","keyword4","keyword5"], '
        '"title_zh":"中文标题翻译", '
        '"abstract_zh":"中文摘要翻译（完整翻译，不要缩写）", '
        '"relevance_score":0.0}\n'
        "要求：\n"
        "- one_liner、innovations、title_zh、abstract_zh 必须使用中文\n"
        "- relevance_score 在 0 到 1 之间\n"
        "- keywords 提取 3~8 个最具代表性的英文学术关键词\n"
        f"标题: {title}\n摘要: {abstract}\n"
    )


def build_deep_prompt(
    title: str,
    extracted_pages: str,
    detail_level: str = "medium",
) -> str:
    normalized_detail = normalize_analysis_detail_level(detail_level)
    return (
        "你是审稿专家。请用中文输出严格 JSON：\n"
        '{"method_summary":"方法总结", '
        '"experiments_summary":"实验总结", '
        '"ablation_summary":"消融实验总结", '
        '"reviewer_risks":["风险点1","风险点2"]}\n'
        "要求：所有字段必须使用中文回答。\n"
        "证据说明：输入可能是跨全文选择的结构化证据包，或按正文顺序保留的线性论文摘录，并附带阶段分析；"
        "只有当某个具体判断确实缺少直接证据时，才写“证据不足”，不要把摘录跨度误判为证据不足，不要臆测或虚构。"
        "当前证据也可能是为某个分析目标筛选出来的，某类细节未出现不代表原论文不存在。"
        "如果阶段分析与论文证据冲突，以更接近原文的论文证据为准。\n"
        f"详略级别：{normalized_detail}。\n"
        f"{_deep_detail_guidance(normalized_detail)}\n"
        f"论文标题: {title}\n"
        f"论文证据与阶段分析: {extracted_pages}\n"
    )


def build_deep_focus_prompt(
    title: str,
    *,
    focus: str,
    evidence_text: str,
    detail_level: str = "medium",
) -> str:
    normalized_detail = normalize_analysis_detail_level(detail_level)
    focus_key = str(focus or "").strip().lower()
    focus_map = {
        "method": (
            "方法机制",
            "请聚焦论文的问题定义、核心假设、模型/算法结构、关键公式或训练目标。"
            " 输出中文 Markdown，优先解释“为什么这样设计”和“各模块如何协同”。",
        ),
        "experiment": (
            "实验与结果",
            "请聚焦实验设置、数据集、对比基线、主结果、表格结论、消融与误差分析。"
            " 输出中文 Markdown，明确哪些结论有证据支持。",
        ),
        "risk": (
            "局限与复现",
            "请聚焦局限性、失败模式、适用边界、实现依赖、复现实验风险与开放问题。"
            " 输出中文 Markdown，优先给出审稿视角的风险判断。",
        ),
    }
    focus_label, instruction = focus_map.get(
        focus_key,
        (
            "论文分析",
            "请基于证据包做中文 Markdown 分析，优先给出有明确证据支持的结论。",
        ),
    )
    return (
        "你是顶级论文审稿专家。请先做一轮焦点分析，输出中文 Markdown，不要输出 JSON。\n"
        f"详略级别：{normalized_detail}。\n"
        f"{_deep_detail_guidance(normalized_detail)}\n"
        "证据说明：下面给出的可能是跨全文选择的结构化证据包，也可能是按正文顺序保留的线性论文摘录；"
        "只有当某个具体判断确实缺少直接证据时，才写“证据不足”，不要把摘录边界误写成全文边界。"
        "当前证据只服务于本轮焦点，未出现的其他细节不代表原文不存在。\n"
        f"论文标题: {title}\n"
        f"分析焦点: {focus_label}\n"
        f"任务要求: {instruction}\n"
        f"论文证据:\n{evidence_text}\n"
    )


def build_rag_prompt(
    question: str, contexts: list[str]
) -> str:
    joined = "\n\n".join(
        f"[ctx{i + 1}] {ctx}"
        for i, ctx in enumerate(contexts)
    )
    return (
        "请基于上下文回答问题，输出严格 JSON："
        '{"answer":"...", "confidence":0.0}\n'
        f"问题: {question}\n上下文:\n{joined}"
    )


def build_survey_prompt(
    keyword: str,
    milestones: list[dict],
    seminal: list[dict],
) -> str:
    milestone_text = "\n".join(
        f"- {m['year']}: {m['title']} "
        f"(score={m['seminal_score']:.3f})"
        for m in milestones[:20]
    )
    seminal_text = "\n".join(
        f"- {m['title']} "
        f"(year={m['year']}, "
        f"score={m['seminal_score']:.3f})"
        for m in seminal[:10]
    )
    return (
        "你是科研综述作者。请输出严格 JSON：\n"
        '{"overview":"...", '
        '"stages":[{"name":"...","description":"..."}], '
        '"reading_list":["...","..."], '
        '"open_questions":["...","..."]}\n'
        f"主题关键词: {keyword}\n"
        f"里程碑:\n{milestone_text}\n\n"
        f"Seminal候选:\n{seminal_text}\n"
    )


def build_topic_wiki_prompt(
    keyword: str,
    paper_contexts: list[dict],
    milestones: list[dict],
    seminal: list[dict],
    survey_summary: dict | None = None,
) -> str:
    """构建主题 Wiki 生成 prompt，喂入真实论文数据"""
    paper_section = ""
    for i, p in enumerate(paper_contexts[:25], 1):
        paper_section += (
            f"\n[P{i}] {p['title']}"
            f" ({p.get('year', '?')})"
            f"\nAbstract: {p.get('abstract', 'N/A')[:400]}"
            f"\nAnalysis: {p.get('analysis', 'N/A')[:400]}\n"
        )

    milestone_text = "\n".join(
        f"- {m['year']}: {m['title']} "
        f"(seminal_score={m['seminal_score']:.3f})"
        for m in milestones[:15]
    )
    seminal_text = "\n".join(
        f"- {s['title']} "
        f"(year={s['year']}, score={s['seminal_score']:.3f})"
        for s in seminal[:10]
    )

    survey_hint = ""
    if survey_summary:
        survey_hint = (
            f"\n参考综述: {survey_summary.get('overview', '')[:600]}\n"
            f"发展阶段: {survey_summary.get('stages', [])}\n"
        )

    return (
        "你是一位世界顶级的学术综述作者和知识百科编辑。"
        "请基于以下真实论文数据和分析结果，撰写一篇全面、深入、"
        "结构清晰的主题百科文章。\n\n"
        "## 输出要求\n"
        "请输出严格的 JSON 对象，结构如下：\n"
        "```json\n"
        "{\n"
        '  "overview": "主题概述（1000-2000字，涵盖定义、重要性、'
        '核心思想、发展脉络，需深入展开）",\n'
        '  "sections": [\n'
        '    {\n'
        '      "title": "章节标题",\n'
        '      "content": "章节内容（800-1500字，引用具体论文，'
        '用[P1][P2]标记引用来源，深度分析）"\n'
        "    }\n"
        "  ],\n"
        '  "key_findings": [\n'
        '    "重要发现1（引用来源论文）",\n'
        '    "重要发现2"\n'
        "  ],\n"
        '  "methodology_evolution": "方法论演化描述（500-1000字）",\n'
        '  "future_directions": [\n'
        '    "未来方向1",\n'
        '    "未来方向2"\n'
        "  ],\n"
        '  "reading_list": [\n'
        '    {"title": "论文标题", "year": 2020, '
        '"reason": "推荐理由"}\n'
        "  ]\n"
        "}\n```\n\n"
        "## 写作要求\n"
        "1. 必须基于提供的真实论文数据，引用具体论文（用[P1][P2]标记）\n"
        "2. sections 至少包含 4-6 个章节，覆盖：起源与背景、核心方法、"
        "关键变体与改进、应用场景、挑战与局限\n"
        "3. 用学术但易懂的语言，中文撰写\n"
        "4. 每个章节需要有深度分析，不是简单罗列\n"
        "5. reading_list 至少推荐 5 篇关键论文\n\n"
        f"## 主题关键词: {keyword}\n\n"
        f"## 里程碑论文:\n{milestone_text}\n\n"
        f"## 最具影响力论文:\n{seminal_text}\n\n"
        f"{survey_hint}"
        f"## 论文数据库:\n{paper_section}\n"
    )


def build_paper_wiki_prompt(
    title: str,
    abstract: str,
    analysis: str,
    related_papers: list[dict],
    ancestors: list[str],
    descendants: list[str],
) -> str:
    """构建论文 Wiki 生成 prompt"""
    related_section = ""
    for i, p in enumerate(related_papers[:10], 1):
        related_section += (
            f"\n[R{i}] {p['title']}"
            f" ({p.get('year', '?')})"
            f"\nAbstract: {p.get('abstract', 'N/A')[:300]}\n"
        )

    ancestor_text = "\n".join(
        f"- {a}" for a in ancestors[:15]
    ) or "暂无引用数据"
    descendant_text = "\n".join(
        f"- {d}" for d in descendants[:15]
    ) or "暂无被引数据"

    return (
        "你是一位学术百科编辑。请基于以下论文信息，撰写一篇"
        "全面的论文百科页面。\n\n"
        "## 输出要求\n"
        "请输出严格的 JSON 对象：\n"
        "```json\n"
        "{\n"
        '  "summary": "论文核心摘要（600-1000字，'
        '用通俗语言深度解释研究动机、方法、贡献）",\n'
        '  "contributions": ["贡献1", "贡献2", "贡献3"],\n'
        '  "methodology": "方法论详述（800-1500字）",\n'
        '  "significance": "学术意义与影响力分析（400-800字，'
        '结合引用关系）",\n'
        '  "limitations": ["局限性1", "局限性2"],\n'
        '  "related_work_analysis": "相关工作分析'
        '（500-1000字，引用[R1][R2]等标记）",\n'
        '  "reading_suggestions": [\n'
        '    {"title": "推荐论文", "reason": "理由"}\n'
        "  ]\n"
        "}\n```\n\n"
        f"## 论文标题: {title}\n\n"
        f"## 摘要:\n{abstract}\n\n"
        f"## 已有分析:\n{analysis or '暂无'}\n\n"
        f"## 引用的论文（祖先）:\n{ancestor_text}\n\n"
        f"## 被引用（后代）:\n{descendant_text}\n\n"
        f"## 相关论文:\n{related_section}\n"
    )


def build_reasoning_prompt(
    title: str,
    abstract: str,
    extracted_text: str,
    analysis_context: str = "",
    detail_level: str = "medium",
) -> str:
    """构建推理链深度分析 prompt，引导 LLM 分步推理"""
    normalized_detail = normalize_analysis_detail_level(detail_level)
    return (
        "你是一位顶级论文审稿专家和方法论分析师。请对以下论文进行深度推理链分析。\n\n"
        "## 分析方法\n"
        "请按照以下推理步骤，逐步深入分析。每一步都需要展示你的思考过程。\n\n"
        f"## 详略级别\n{normalized_detail}\n{_reasoning_detail_guidance(normalized_detail)}\n\n"
        "## 证据使用要求\n"
        "以下提供的是跨全文选择的结构化证据包或线性论文摘录，以及已有分析，不代表论文只到某一节。\n"
        "除非证据明确显示缺页/截断，否则不要写“正文只覆盖到 Sec. x.x”。\n"
        "只有当某个具体判断确实找不到直接证据时，才写“证据不足”；"
        "不要把上下文过长、章节较多或引用分散误判为证据不足。\n"
        "如果同时给了“已有分析”，它们只是弱参考，用于术语对齐和快速回忆；一旦与论文证据冲突，必须以论文证据为准。\n"
        "论文证据也可能按当前任务做过筛选，某类细节未出现不代表原文没有。\n\n"
        "## 输出要求\n"
        "请输出严格的 JSON 对象：\n"
        "```json\n"
        "{\n"
        '  "reasoning_steps": [\n'
        "    {\n"
        '      "step": "步骤名称",\n'
        '      "thinking": "推理思考过程（详细展开）",\n'
        '      "conclusion": "该步骤的结论"\n'
        "    }\n"
        "  ],\n"
        '  "method_chain": {\n'
        '    "problem_definition": "问题定义与动机分析",\n'
        '    "core_hypothesis": "核心假设",\n'
        '    "method_derivation": "方法推导过程（为什么选择这种方法）",\n'
        '    "theoretical_basis": "理论基础",\n'
        '    "innovation_analysis": "创新性多维评估"\n'
        "  },\n"
        '  "experiment_chain": {\n'
        '    "experimental_design": "实验设计合理性评估",\n'
        '    "baseline_fairness": "基线对比公平性分析",\n'
        '    "result_validation": "结果可靠性验证",\n'
        '    "ablation_insights": "消融实验洞察"\n'
        "  },\n"
        '  "impact_assessment": {\n'
        '    "novelty_score": 0.0,\n'
        '    "rigor_score": 0.0,\n'
        '    "impact_score": 0.0,\n'
        '    "overall_assessment": "综合评估（200-400字）",\n'
        '    "strengths": ["优势1", "优势2"],\n'
        '    "weaknesses": ["不足1", "不足2"],\n'
        '    "future_suggestions": ["建议1", "建议2"]\n'
        "  }\n"
        "}\n```\n\n"
        "## 推理步骤要求\n"
        "reasoning_steps 至少包含以下 5 个步骤：\n"
        "1. **问题理解** — 这篇论文要解决什么问题？为什么重要？\n"
        "2. **方法推导** — 作者的方法是如何一步步推导出来的？核心创新在哪？\n"
        "3. **理论验证** — 方法的理论基础是否扎实？有无逻辑漏洞？\n"
        "4. **实验评估** — 实验设计是否合理？结果是否令人信服？\n"
        "5. **影响预测** — 这篇论文对领域的潜在影响和后续可能的研究方向\n\n"
        "## 评分标准\n"
        "novelty_score / rigor_score / impact_score 均为 0-1 之间的浮点数：\n"
        "- 0.0-0.3: 低（常规/已有工作的小改进）\n"
        "- 0.3-0.6: 中等（有一定新意/较好的实验）\n"
        "- 0.6-0.8: 高（显著创新/严格的验证）\n"
        "- 0.8-1.0: 极高（突破性工作/领域里程碑）\n\n"
        "请用中文回答，展示完整推理过程。\n\n"
        f"## 论文标题: {title}\n\n"
        f"## 摘要:\n{abstract}\n\n"
        f"## 论文证据:\n{extracted_text}\n\n"
        + (f"## 已有分析:\n{analysis_context}\n" if analysis_context else "")
    )


def build_research_gaps_prompt(
    keyword: str,
    papers_data: list[dict],
    network_stats: dict,
) -> str:
    """构建研究空白识别 prompt"""
    paper_lines = []
    for i, p in enumerate(papers_data[:30], 1):
        paper_lines.append(
            f"[P{i}] {p.get('title', 'N/A')} ({p.get('year', '?')})\n"
            f"  Keywords: {', '.join(p.get('keywords', []))}\n"
            f"  Abstract: {p.get('abstract', '')[:300]}\n"
            f"  indegree={p.get('indegree', 0)}, outdegree={p.get('outdegree', 0)}"
        )
    papers_text = "\n".join(paper_lines)

    return (
        "你是一位资深的学术研究战略分析师。请基于以下领域论文数据和引用网络统计，"
        "识别该领域中尚未被充分探索的研究空白和潜在机会。\n\n"
        "## 输出要求\n"
        "请输出严格的 JSON 对象：\n"
        "```json\n"
        "{\n"
        '  "research_gaps": [\n'
        "    {\n"
        '      "gap_title": "研究空白标题",\n'
        '      "description": "详细描述（200-400字）",\n'
        '      "evidence": "为什么认为这是空白（引用论文数据）",\n'
        '      "potential_impact": "填补该空白的潜在影响",\n'
        '      "suggested_approach": "建议的研究方向",\n'
        '      "difficulty": "easy/medium/hard",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ],\n"
        '  "method_comparison": {\n'
        '    "dimensions": ["维度1", "维度2"],\n'
        '    "methods": [\n'
        '      {"name": "方法名", "scores": {"维度1": "强/中/弱"}, "papers": ["P1"]}\n'
        "    ],\n"
        '    "underexplored_combinations": ["未被探索的方法组合"]\n'
        "  },\n"
        '  "trend_analysis": {\n'
        '    "hot_directions": ["热门方向"],\n'
        '    "declining_areas": ["式微方向"],\n'
        '    "emerging_opportunities": ["新兴机会"]\n'
        "  },\n"
        '  "overall_summary": "领域研究空白总结（300-500字）"\n'
        "}\n```\n\n"
        "## 分析要求\n"
        "1. research_gaps 至少识别 3-5 个研究空白\n"
        "2. confidence 为 0-1，表示你对该空白判断的置信度\n"
        "3. method_comparison 构建跨论文的方法对比矩阵\n"
        "4. 基于引用网络的稀疏区域来发现空白\n"
        "5. 所有文本字段必须使用简体中文；仅 difficulty 字段保留 easy/medium/hard\n"
        "6. 允许保留模型名、数据集名、缩写等专有名词英文形式\n\n"
        f"## 领域关键词: {keyword}\n\n"
        f"## 引用网络统计:\n"
        f"- 总论文数: {network_stats.get('total_papers', 0)}\n"
        f"- 引用边数: {network_stats.get('edge_count', 0)}\n"
        f"- 网络密度: {network_stats.get('density', 0):.4f}\n"
        f"- 连通比例: {network_stats.get('connected_ratio', 0):.1%}\n"
        f"- 孤立论文数: {network_stats.get('isolated_count', 0)}\n\n"
        f"## 论文数据:\n{papers_text}\n"
    )


def build_evolution_prompt(
    keyword: str, year_buckets: list[dict]
) -> str:
    lines = []
    for x in year_buckets:
        lines.append(
            f"- {x['year']}: "
            f"count={x['paper_count']}, "
            f"avg_score={x['avg_seminal_score']:.3f}, "
            f"top={x['top_titles']}"
        )
    joined = "\n".join(lines)
    return (
        "你是领域分析师。请基于时间桶数据输出严格 JSON：\n"
        '{"trend_summary":"...", '
        '"phase_shift_signals":"...", '
        '"next_week_focus":"..."}\n'
        "要求：\n"
        "1. 三个字段都必须是简体中文字符串。\n"
        "2. 每个字段 1-2 句，避免过长列表。\n"
        "3. 允许保留模型名、数据集名、缩写等专有名词英文形式。\n"
        f"关键词: {keyword}\n数据:\n{joined}\n"
    )


def build_wiki_outline_prompt(
    keyword: str,
    paper_summaries: list[dict],
    citation_contexts: list[str],
    scholar_metadata: list[dict],
    pdf_excerpts: list[dict],
) -> str:
    """构建 Wiki 大纲生成 prompt，输出章节规划"""
    paper_section = ""
    for i, p in enumerate(paper_summaries, 1):
        paper_section += (
            f"\n[P{i}] {p.get('title', 'N/A')} ({p.get('year', '?')})\n"
            f"Abstract: {p.get('abstract', '')[:500]}\n"
            f"Analysis: {p.get('analysis', '')[:500]}\n"
        )

    citation_section = ""
    for i, ctx in enumerate(citation_contexts, 1):
        citation_section += f"\n[C{i}] {ctx}\n"

    scholar_section = ""
    for i, s in enumerate(scholar_metadata, 1):
        parts = [f"[S{i}] {s.get('title', 'N/A')} ({s.get('year', '?')})"]
        if s.get("citationCount") is not None:
            parts.append(f"引用数: {s['citationCount']}")
        if s.get("venue"):
            parts.append(f"Venue: {s['venue']}")
        if s.get("tldr"):
            parts.append(f"TLDR: {s['tldr'][:300]}")
        scholar_section += "\n".join(parts) + "\n\n"

    pdf_section = ""
    for i, ex in enumerate(pdf_excerpts, 1):
        pdf_section += (
            f"\n[PDF{i}] {ex.get('title', 'N/A')}\n"
            f"Excerpt: {ex.get('excerpt', '')[:600]}\n"
        )

    return (
        "你是一位世界顶级的学术综述作者和知识百科编辑。"
        f"请基于以下全部资料，为「{keyword}」主题撰写一篇全面的百科文章大纲。\n\n"
        "## 输出要求\n"
        "请输出严格的 JSON 对象，结构如下：\n"
        "```json\n"
        "{\n"
        '  "title": "文章标题",\n'
        '  "outline": [\n'
        '    {\n'
        '      "section_title": "章节标题",\n'
        '      "key_points": ["要点1", "要点2"],\n'
        '      "source_refs": ["[P1]", "[P3]"]\n'
        "    }\n"
        "  ],\n"
        '  "total_sections": 6\n'
        "}\n```\n\n"
        "## 写作要求\n"
        "1. outline 必须包含 5-8 个章节，覆盖：背景与起源、核心方法、"
        "关键变体、应用场景、技术挑战、最新进展、未来方向\n"
        "2. 每个章节的 key_points 列出 2-4 个核心要点\n"
        "3. source_refs 引用相关来源（[P1][P2]、[C1][C2]、[S1][S2]、[PDF1][PDF2]）\n"
        "4. 必须基于提供的全部数据规划，不得虚构\n"
        "5. 用中文撰写\n\n"
        f"## 主题关键词: {keyword}\n\n"
        f"## 论文摘要与分析:\n{paper_section}\n\n"
        f"## 引用关系上下文:\n{citation_section}\n\n"
        f"## 学术元数据:\n{scholar_section}\n\n"
        f"## PDF 摘录:\n{pdf_section}\n"
    )


def build_wiki_section_prompt(
    keyword: str,
    section_title: str,
    key_points: list[str],
    source_refs: list[str],
    all_sources_text: str,
) -> str:
    """构建 Wiki 单章节生成 prompt，直接输出 markdown 文本"""
    points_text = "\n".join(f"- {p}" for p in key_points)
    refs_text = ", ".join(source_refs) if source_refs else "无"

    return (
        "你是一位世界顶级的学术综述作者和知识百科编辑。"
        f"请基于以下资料，为「{keyword}」主题的百科文章撰写「{section_title}」章节。\n\n"
        "## 输出要求\n"
        "直接输出章节内容的 Markdown 文本，不要输出 JSON，不要输出代码块包裹。\n"
        "- 不要重复章节标题（标题会自动添加）\n"
        "- 直接从正文开始写\n\n"
        "## 写作要求\n"
        "1. 内容 800-1500 字，深度分析，不要简单罗列\n"
        "2. 引用来源（用[P1][P2]等标记）\n"
        "3. 用学术但易懂的中文撰写\n"
        "4. 最后用一句话总结本章核心洞见（加粗标注）\n\n"
        f"## 主题关键词: {keyword}\n\n"
        f"## 本章节标题: {section_title}\n\n"
        f"## 本章节要点:\n{points_text}\n\n"
        f"## 需引用的来源: {refs_text}\n\n"
        f"## 全部资料来源:\n{all_sources_text}\n"
    )
