from __future__ import annotations

from packages.agent.tools.tool_schema import ToolDef, ToolSpec


_DEFAULT_RESEARCH_READ_TOOL_SPEC = ToolSpec(
    default_local_enabled=True,
    default_remote_enabled=True,
)

_DEFAULT_RESEARCH_ACTION_TOOL_SPEC = ToolSpec(
    permission="task",
    managed_permission=True,
    default_local_enabled=True,
    default_remote_enabled=True,
)


def _research_tool(**kwargs) -> ToolDef:
    name = str(kwargs["name"])
    return ToolDef(
        **kwargs,
        handler=f"packages.agent.tools.research_tool_runtime:_{name}",
    )


def _research_action_tool(**kwargs) -> ToolDef:
    kwargs.setdefault("spec", _DEFAULT_RESEARCH_ACTION_TOOL_SPEC)
    return _research_tool(**kwargs)


RESEARCH_TOOL_REGISTRY: list[ToolDef] = [
    _research_tool(
        name="search_papers",
        description="在本地论文库中搜索论文标题和摘要，返回紧凑候选列表；需要完整摘要或已有分析时再调用 get_paper_detail。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "返回数量上限", "default": 20},
            },
            "required": ["keyword"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="get_paper_detail",
        description="获取单篇论文的详细信息。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
            },
            "required": ["paper_id"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="get_paper_analysis",
        description="读取论文已有的三轮分析、最终结构化笔记和相关分析元数据，适合实验解读、优缺点判断和综合结论问题；不返回图表图片。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
            },
            "required": ["paper_id"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="paper_figures",
        description="只读取论文已提取的图片、图表与表格候选，不启动三轮分析，也不重新提取或分析图表。用户明确要查看图片、查看已提取图表或打开原图时使用。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
            },
            "required": ["paper_id"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="get_similar_papers",
        description="基于向量相似度获取相似论文。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
                "top_k": {"type": "integer", "description": "返回数量", "default": 5},
            },
            "required": ["paper_id"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="get_citation_tree",
        description="获取论文的引用树结构。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
                "depth": {"type": "integer", "description": "树深度", "default": 2},
            },
            "required": ["paper_id"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="get_timeline",
        description="按关键词获取论文时间线与里程碑。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "关键词"},
                "limit": {"type": "integer", "description": "分析论文数量", "default": 100},
            },
            "required": ["keyword"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="research_kg_status",
        description="查看论文库级 Research KG / GraphRAG 构建状态、实体数、关系数和已构建论文数。",
        parameters={"type": "object", "properties": {}},
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_action_tool(
        name="build_research_kg",
        description="为本地论文库构建或刷新 GraphRAG 实体关系图。用户明确要求构建、刷新，或 graph_rag_query 显示图谱为空时使用。",
        parameters={
            "type": "object",
            "properties": {
                "paper_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的论文 UUID 列表；缺省时按最近论文批量构建",
                },
                "limit": {"type": "integer", "description": "缺省构建数量", "default": 12},
                "force": {"type": "boolean", "description": "是否强制重建", "default": False},
            },
        },
    ),
    _research_tool(
        name="graph_rag_query",
        description="基于本地论文库 Research KG 执行 GraphRAG 查询，返回实体、关系、论文、引用和已有分析组成的证据包。适合研究趋势、方法关系、数据集指标、研究空白和论文脉络问题。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用户研究问题或聚焦关键词"},
                "top_k": {"type": "integer", "description": "返回证据规模", "default": 6},
                "paper_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的论文 UUID 约束范围",
                },
            },
            "required": ["query"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="list_topics",
        description="列出研究工作区与订阅。",
        parameters={"type": "object", "properties": {}},
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="get_system_status",
        description="查看数据库、论文数量、任务与流水线运行概况。",
        parameters={"type": "object", "properties": {}},
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="search_literature",
        description="统一检索外部学术文献，可覆盖 arXiv、会议和期刊，并支持 CCF-A / venue 过滤。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词"},
                "max_results": {"type": "integer", "description": "最大结果数", "default": 20},
                "source_scope": {
                    "type": "string",
                    "enum": ["hybrid", "arxiv", "openalex"],
                    "description": "数据源范围",
                    "default": "hybrid",
                },
                "venue_tier": {
                    "type": "string",
                    "enum": ["all", "ccf_a"],
                    "description": "venue 分级过滤",
                    "default": "all",
                },
                "venue_type": {
                    "type": "string",
                    "enum": ["all", "conference", "journal"],
                    "description": "venue 类型过滤",
                    "default": "all",
                },
                "venue_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的 venue 名称或简称过滤列表",
                },
                "from_year": {
                    "type": "integer",
                    "description": "可选的最早年份（含）",
                },
            },
            "required": ["query"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="preview_external_paper_head",
        description="对未入库的 arXiv 论文做外部目录预览，返回摘要元数据和可用章节标题。",
        parameters={
            "type": "object",
            "properties": {
                "arxiv_id": {"type": "string", "description": "arXiv ID 或 arXiv URL"},
            },
            "required": ["arxiv_id"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="preview_external_paper_section",
        description="对未入库的 arXiv 论文做指定章节预读，适合先看 Introduction、Method、Experiments 等核心部分。",
        parameters={
            "type": "object",
            "properties": {
                "arxiv_id": {"type": "string", "description": "arXiv ID 或 arXiv URL"},
                "section_name": {"type": "string", "description": "章节名，例如 Introduction、Method、Experiments"},
            },
            "required": ["arxiv_id", "section_name"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_action_tool(
        name="ingest_external_literature",
        description="将外部文献检索结果中的论文导入本地论文库，可选挂到指定文件夹/主题。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "原始检索词或来源说明"},
                "topic_id": {"type": "string", "description": "可选的目标文件夹/主题 ID"},
                "entries": {
                    "type": "array",
                    "description": "需要导入的外部论文条目",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "abstract": {"type": "string"},
                            "publication_year": {"type": "integer"},
                            "publication_date": {"type": "string"},
                            "citation_count": {"type": "integer"},
                            "venue": {"type": "string"},
                            "venue_type": {"type": "string"},
                            "venue_tier": {"type": "string"},
                            "authors": {"type": "array", "items": {"type": "string"}},
                            "categories": {"type": "array", "items": {"type": "string"}},
                            "arxiv_id": {"type": "string"},
                            "openalex_id": {"type": "string"},
                            "source_url": {"type": "string"},
                            "pdf_url": {"type": "string"},
                            "source": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["entries"],
        },
    ),
    _research_tool(
        name="search_arxiv",
        description="搜索 arXiv 论文候选，不直接入库。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "arXiv 搜索语句"},
                "max_results": {"type": "integer", "description": "最大结果数", "default": 20},
            },
            "required": ["query"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_action_tool(
        name="ingest_arxiv",
        description="将用户选中的 arXiv 论文导入本地库。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "原始搜索语句"},
                "arxiv_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要导入的 arXiv id 列表",
                },
            },
            "required": ["query", "arxiv_ids"],
        },
    ),
    _research_action_tool(
        name="skim_paper",
        description="对论文执行粗读分析，适合速览、贡献、创新点和是否值得继续深入阅读的判断。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
            },
            "required": ["paper_id"],
        },
    ),
    _research_action_tool(
        name="deep_read_paper",
        description="对论文执行精读分析，适合方法细节、模块设计、训练流程和实现层面的理解。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
            },
            "required": ["paper_id"],
        },
    ),
    _research_action_tool(
        name="analyze_paper_rounds",
        description="对论文执行粗到深的三轮分析，并生成最终结构化笔记，适合实验结论解读、证据充分性、局限性和综合判断；不负责查看或返回已提取图表图片。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
                "detail_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "分析详略等级",
                    "default": "medium",
                },
                "reasoning_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "default"],
                    "description": "推理强度",
                    "default": "default",
                },
            },
            "required": ["paper_id"],
        },
    ),
    _research_action_tool(
        name="embed_paper",
        description="为论文生成向量嵌入。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
            },
            "required": ["paper_id"],
        },
    ),
    _research_action_tool(
        name="generate_wiki",
        description="生成专题综述或单篇论文综述。",
        parameters={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["topic", "paper"],
                    "description": "topic 表示专题，paper 表示单篇论文",
                },
                "keyword_or_id": {"type": "string", "description": "关键词或论文 UUID"},
            },
            "required": ["type", "keyword_or_id"],
        },
    ),
    _research_action_tool(
        name="generate_daily_brief",
        description="生成研究简报并保存历史记录。",
        parameters={
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "可选的邮件接收人", "default": ""},
            },
        },
    ),
    _research_action_tool(
        name="research_wiki_init",
        description="初始化当前项目的 research wiki，并同步项目论文与想法为结构化节点。",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "可选的项目 UUID；缺省时尝试按当前工作区推断"},
            },
        },
    ),
    _research_tool(
        name="research_wiki_stats",
        description="查看当前项目 research wiki 的节点、边和类型统计。",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "可选的项目 UUID；缺省时尝试按当前工作区推断"},
            },
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_tool(
        name="research_wiki_query",
        description="查询当前项目 research wiki，返回紧凑的 query pack 供想法生成和项目决策使用。",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "可选的项目 UUID；缺省时尝试按当前工作区推断"},
                "query": {"type": "string", "description": "可选的聚焦查询，例如某个子方向、方法或风险点"},
                "limit": {"type": "integer", "description": "返回节点数量上限", "default": 5},
            },
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_action_tool(
        name="research_wiki_update_node",
        description="创建或更新当前项目 research wiki 节点，可用于手动沉淀 gap、claim、experiment、note 等结构化记录。",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "可选的项目 UUID；缺省时尝试按当前工作区推断"},
                "node_id": {"type": "string", "description": "已有 wiki 节点 ID；提供后优先按该节点更新"},
                "node_key": {"type": "string", "description": "节点稳定键，例如 note:eval-plan、gap:data-coverage"},
                "node_type": {"type": "string", "description": "节点类型，例如 note、gap、claim、experiment、idea"},
                "title": {"type": "string", "description": "节点标题"},
                "summary": {"type": "string", "description": "节点摘要"},
                "body_md": {"type": "string", "description": "节点 Markdown 正文"},
                "status": {
                    "type": "string",
                    "enum": ["active", "proposed", "failed", "archived"],
                    "description": "节点状态",
                },
                "source_paper_id": {"type": "string", "description": "可选的来源论文 ID"},
                "source_run_id": {"type": "string", "description": "可选的来源项目运行 ID"},
                "metadata": {"type": "object", "description": "可选的附加元数据"},
            },
        },
    ),
    _research_action_tool(
        name="manage_subscription",
        description="启用或关闭订阅，并调整抓取频率与时间。",
        parameters={
            "type": "object",
            "properties": {
                "topic_name": {"type": "string", "description": "主题名称"},
                "enabled": {"type": "boolean", "description": "是否启用"},
                "schedule_frequency": {
                    "type": "string",
                    "enum": ["daily", "twice_daily", "weekdays", "weekly"],
                    "description": "抓取频率",
                },
                "schedule_time_beijing": {
                    "type": "integer",
                    "description": "北京时间小时，0-23",
                },
            },
            "required": ["topic_name", "enabled"],
        },
    ),
    _research_tool(
        name="suggest_keywords",
        description="根据研究描述和当前检索源生成更适合实际检索的关键词建议。",
        parameters={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "研究兴趣描述"},
                "source_scope": {
                    "type": "string",
                    "enum": ["hybrid", "arxiv", "openalex"],
                    "description": "当前检索源",
                    "default": "hybrid",
                },
                "search_field": {
                    "type": "string",
                    "enum": ["all", "title", "keywords", "authors", "arxiv_id"],
                    "description": "当前搜索字段",
                    "default": "all",
                },
            },
            "required": ["description"],
        },
        spec=_DEFAULT_RESEARCH_READ_TOOL_SPEC,
    ),
    _research_action_tool(
        name="reasoning_analysis",
        description="生成论文的推理链分析。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
            },
            "required": ["paper_id"],
        },
    ),
    _research_action_tool(
        name="identify_research_gaps",
        description="分析某个方向的研究空白与趋势。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "研究方向关键词"},
                "limit": {"type": "integer", "description": "分析论文数量", "default": 120},
            },
            "required": ["keyword"],
        },
    ),
    _research_action_tool(
        name="writing_assist",
        description="执行学术写作辅助，例如翻译、润色、压缩、扩写和图表说明。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "写作动作类型"},
                "text": {"type": "string", "description": "待处理文本"},
            },
            "required": ["action", "text"],
        },
    ),
    _research_action_tool(
        name="analyze_figures",
        description="提取并分析论文中的图片、图表与表格。涉及架构图、模块图、流程图、编码器/解码器结构或精确表格数值时优先使用，并在回答里引用对应原图页码与题注。",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "论文 UUID"},
                "max_figures": {"type": "integer", "description": "最大分析数量", "default": 10},
            },
            "required": ["paper_id"],
        },
    ),
]
