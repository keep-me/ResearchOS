from __future__ import annotations

from packages.agent.tools.research_tool_catalog import RESEARCH_TOOL_REGISTRY
from packages.agent.tools.tool_schema import ToolDef, ToolSpec

_BASE_TOOL_REGISTRY: list[ToolDef] = [
    ToolDef(
        name="search_web",
        description="从网页搜索引擎检索通用网络资料，适合官网、项目、新闻和概念说明。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回结果数", "default": 8},
            },
            "required": ["query"],
        },
        spec=ToolSpec(permission="websearch", managed_permission=True),
        handler="packages.agent.tools.web_tool_runtime:_search_web",
        provider_tools=[{"type": "provider-defined", "id": "openai.web_search", "name": "web_search", "args": {}}],
    ),
    ToolDef(
        name="websearch",
        description="opencode 风格的网页搜索工具，适合搜索官网、教程、新闻和最新资料。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回结果数", "default": 8},
            },
            "required": ["query"],
        },
        spec=ToolSpec(
            permission="websearch",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
        ),
        handler="packages.agent.tools.web_tool_runtime:_websearch",
        provider_tools=[{"type": "provider-defined", "id": "openai.web_search", "name": "web_search", "args": {}}],
    ),
    ToolDef(
        name="webfetch",
        description="抓取指定网页内容，返回 text / markdown / html，适合读取官方文档和网页正文。",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整网页 URL"},
                "format": {
                    "type": "string",
                    "enum": ["text", "markdown", "html"],
                    "description": "返回格式",
                    "default": "markdown",
                },
                "timeout_sec": {"type": "integer", "description": "请求超时秒数", "default": 30},
            },
            "required": ["url"],
        },
        spec=ToolSpec(
            permission="webfetch",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
        ),
        handler="packages.agent.tools.web_tool_runtime:_webfetch",
    ),
    ToolDef(
        name="codesearch",
        description="面向代码、SDK、官方文档和 GitHub 项目的联网搜索，适合查 API 用法与示例。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "代码或文档搜索语句"},
                "max_results": {"type": "integer", "description": "返回结果数", "default": 8},
            },
            "required": ["query"],
        },
        spec=ToolSpec(
            permission="codesearch",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
        ),
        handler="packages.agent.tools.web_tool_runtime:_codesearch",
    ),
    ToolDef(
        name="skill",
        description="加载一个本地 skill，把该 skill 的完整说明、目录和附带文件样本注入当前上下文，行为接近 opencode skill 工具。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要加载的 skill 名称、id 或路径标识"},
            },
            "required": ["name"],
        },
        spec=ToolSpec(
            permission="skill",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
        ),
        handler="packages.agent.tools.skill_tool_runtime:_load_skill",
    ),
    ToolDef(
        name="list_local_skills",
        description="列出当前机器上可用的本地 skills，帮助判断有哪些现成工作流可以调用。",
        parameters={"type": "object", "properties": {}},
        spec=ToolSpec(permission="skill", managed_permission=True),
        handler="packages.agent.tools.skill_tool_runtime:_list_local_skills",
    ),
    ToolDef(
        name="read_local_skill",
        description="读取某个本地 skill 的 SKILL.md 内容，用于遵循已有流程与约束。",
        parameters={
            "type": "object",
            "properties": {
                "skill_ref": {"type": "string", "description": "skill 的 id、名称、相对路径或目录路径"},
                "max_chars": {"type": "integer", "description": "最大返回字符数", "default": 12000},
            },
            "required": ["skill_ref"],
        },
        spec=ToolSpec(permission="skill", managed_permission=True),
        handler="packages.agent.tools.skill_tool_runtime:_read_local_skill",
    ),
    ToolDef(
        name="inspect_workspace",
        description="查看本地工作区目录结构，帮助自动化研究和实验执行。",
        parameters={
            "type": "object",
            "properties": {
                "workspace_path": {"type": "string", "description": "工作区目录路径"},
                "max_depth": {"type": "integer", "description": "最大树深度", "default": 2},
                "max_entries": {"type": "integer", "description": "最大条目数", "default": 120},
            },
            "required": ["workspace_path"],
        },
        spec=ToolSpec(
            permission="list",
            managed_permission=True,
            default_remote_enabled=True,
        ),
    ),
    ToolDef(
        name="read_workspace_file",
        description="读取工作区中的代码、配置或日志文件。",
        parameters={
            "type": "object",
            "properties": {
                "workspace_path": {"type": "string", "description": "工作区目录路径"},
                "relative_path": {"type": "string", "description": "相对工作区根目录的文件路径"},
                "max_chars": {"type": "integer", "description": "最大返回字符数", "default": 12000},
            },
            "required": ["workspace_path", "relative_path"],
        },
        spec=ToolSpec(
            permission="read",
            managed_permission=True,
            default_remote_enabled=True,
        ),
    ),
    ToolDef(
        name="write_workspace_file",
        description="创建或覆盖工作区中的文本文件，适合生成代码、配置和实验脚本。",
        parameters={
            "type": "object",
            "properties": {
                "workspace_path": {"type": "string", "description": "工作区目录路径"},
                "relative_path": {"type": "string", "description": "相对工作区根目录的文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
                "create_dirs": {"type": "boolean", "description": "父目录不存在时是否自动创建", "default": True},
                "overwrite": {"type": "boolean", "description": "文件已存在时是否允许覆盖", "default": True},
            },
            "required": ["workspace_path", "relative_path", "content"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="edit",
            managed_permission=True,
            default_remote_enabled=True,
            allow_in_read_only=False,
        ),
    ),
    ToolDef(
        name="replace_workspace_text",
        description="在工作区文件中精确替换已有文本，适合做小范围代码修改。",
        parameters={
            "type": "object",
            "properties": {
                "workspace_path": {"type": "string", "description": "工作区目录路径"},
                "relative_path": {"type": "string", "description": "相对工作区根目录的文件路径"},
                "search_text": {"type": "string", "description": "需要被替换的原始文本，默认要求唯一匹配"},
                "replace_text": {"type": "string", "description": "替换后的新文本"},
                "replace_all": {"type": "boolean", "description": "是否替换全部匹配", "default": False},
            },
            "required": ["workspace_path", "relative_path", "search_text", "replace_text"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="edit",
            managed_permission=True,
            default_remote_enabled=True,
            allow_in_read_only=False,
        ),
    ),
    ToolDef(
        name="run_workspace_command",
        description="在工作区中运行命令，可用于安装依赖、测试、改代码后的验证或启动实验。",
        parameters={
            "type": "object",
            "properties": {
                "workspace_path": {"type": "string", "description": "工作区目录路径"},
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout_sec": {"type": "integer", "description": "超时秒数", "default": 120},
                "background": {"type": "boolean", "description": "是否作为后台任务提交", "default": False},
            },
            "required": ["workspace_path", "command"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="bash",
            managed_permission=True,
            default_remote_enabled=True,
            allow_in_read_only=False,
        ),
    ),
    ToolDef(
        name="get_workspace_task_status",
        description="查询后台工作区命令的执行状态。",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 id"},
            },
            "required": ["task_id"],
        },
        spec=ToolSpec(
            permission="read",
            managed_permission=True,
            default_remote_enabled=True,
        ),
    ),
    ToolDef(
        name="list",
        description="列出目录结构，行为接近 opencode 的 list 工具。适合作为仓库定位第一步；通常先 list，再 glob，再 grep，最后 read。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "绝对路径；也可结合 workspace_path 传相对路径"},
                "recursive": {"type": "boolean", "description": "是否递归列出", "default": False},
                "max_depth": {"type": "integer", "description": "递归时的最大深度", "default": 2},
                "max_entries": {"type": "integer", "description": "最大返回条目数", "default": 120},
            },
        },
        spec=ToolSpec(permission="list", managed_permission=True, local_only=True),
        handler="_list_path_entries",
    ),
    ToolDef(
        name="ls",
        description="列出目录结构，行为接近 opencode 的 list/ls 工具。适合作为仓库定位第一步；通常先 list，再 glob，再 grep，最后 read。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "绝对路径；也可结合 workspace_path 传相对路径"},
                "recursive": {"type": "boolean", "description": "是否递归列出", "default": False},
                "max_depth": {"type": "integer", "description": "递归时的最大深度", "default": 2},
                "max_entries": {"type": "integer", "description": "最大返回条目数", "default": 120},
            },
        },
        spec=ToolSpec(permission="list", managed_permission=True, local_only=True),
        handler="_list_path_entries",
    ),
    ToolDef(
        name="glob",
        description="按 glob 模式查找目录中的文件或子目录，适合在 list 之后收窄候选文件，再配合 grep/read 继续定位。如果 grep 已经给出精确文件路径，就不要再用 glob 重新扫描整个仓库。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
                "path": {"type": "string", "description": "搜索目录；可省略并使用当前 workspace_path"},
                "limit": {"type": "integer", "description": "最大返回数量", "default": 40},
            },
            "required": ["pattern"],
        },
        spec=ToolSpec(
            permission="glob",
            managed_permission=True,
            default_local_enabled=True,
            local_only=True,
        ),
        handler="_glob_path_entries",
    ),
    ToolDef(
        name="grep",
        description="在目录文件内容中搜索正则模式，适合优先在源码目录定位定义、调用点和关键文本，再用 read 打开目标文件；做代码事实查询时通常先用 grep，不要先用 bash。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则搜索模式"},
                "path": {"type": "string", "description": "搜索目录；可省略并使用当前 workspace_path"},
                "include": {"type": "string", "description": "可选文件过滤模式，例如 *.ts"},
                "limit": {"type": "integer", "description": "最大返回命中数", "default": 40},
            },
            "required": ["pattern"],
        },
        spec=ToolSpec(
            permission="grep",
            managed_permission=True,
            default_local_enabled=True,
            local_only=True,
        ),
        handler="_grep_path_contents",
    ),
    ToolDef(
        name="read",
        description="读取绝对路径或工作区相对路径的文件；若给的是目录，则返回目录列表。如果用户已经给了明确文件路径，优先直接 read，不要先用 bash 或宽泛搜索。支持用 offset/limit 按行读取，并返回带行号的内容窗口，适合紧接 grep 命中后读取局部上下文。",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件或目录路径"},
                "max_chars": {"type": "integer", "description": "最大返回字符数", "default": 12000},
                "offset": {"type": "integer", "description": "从第几行开始读取，1 开始计数"},
                "limit": {"type": "integer", "description": "最多读取多少行；用于局部窗口读取"},
            },
            "required": ["file_path"],
        },
        spec=ToolSpec(
            permission="read",
            managed_permission=True,
            default_local_enabled=True,
            local_only=True,
        ),
        handler="_read_path",
    ),
    ToolDef(
        name="write",
        description="创建或覆盖任意文件路径，行为接近 opencode write 工具。",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
                "create_dirs": {"type": "boolean", "description": "父目录不存在时自动创建", "default": True},
                "overwrite": {"type": "boolean", "description": "是否允许覆盖已有文件", "default": True},
            },
            "required": ["file_path", "content"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="edit",
            managed_permission=True,
            default_local_enabled=True,
            allow_in_read_only=False,
            local_only=True,
        ),
        handler="_write_path",
    ),
    ToolDef(
        name="edit",
        description="基于 old_string/new_string 定点编辑文件，行为接近 opencode edit 工具。",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "原始文本"},
                "new_string": {"type": "string", "description": "替换后的文本"},
                "replace_all": {"type": "boolean", "description": "是否替换全部匹配", "default": False},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="edit",
            managed_permission=True,
            default_local_enabled=True,
            allow_in_read_only=False,
            local_only=True,
        ),
        handler="_edit_path",
    ),
    ToolDef(
        name="multiedit",
        description="按顺序执行多段文本替换，适合对同一文件进行多处结构化修改。",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "默认文件路径；各 edit 可单独覆盖"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "当前 edit 的文件路径，可省略并复用外层 file_path"},
                            "old_string": {"type": "string", "description": "原始文本"},
                            "new_string": {"type": "string", "description": "替换后的文本"},
                            "replace_all": {"type": "boolean", "description": "是否替换全部匹配", "default": False},
                        },
                        "required": ["old_string", "new_string"],
                    },
                    "description": "按顺序执行的编辑列表",
                },
            },
            "required": ["edits"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="edit",
            managed_permission=True,
            allow_in_read_only=False,
            local_only=True,
        ),
        handler="_multiedit_path",
    ),
    ToolDef(
        name="apply_patch",
        description="应用一个统一的补丁文本来批量新增、修改、移动或删除文件，行为接近 opencode apply_patch 工具。",
        parameters={
            "type": "object",
            "properties": {
                "patchText": {"type": "string", "description": "完整 patch 文本，必须包含 *** Begin Patch / *** End Patch"},
            },
            "required": ["patchText"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="edit",
            managed_permission=True,
            default_local_enabled=True,
            allow_in_read_only=False,
            local_only=True,
        ),
        handler="_apply_patch_text",
    ),
    ToolDef(
        name="bash",
        description="在工作区或指定目录执行命令，行为接近 opencode bash 工具。只在确实需要 shell 状态、命令输出或精确验证时使用；普通代码阅读、符号定位和文件事实查询优先用 read/glob/grep。不要只为了打印文件片段、行号或静态代码内容而调用 bash。",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "workdir": {"type": "string", "description": "工作目录；可省略并使用当前 workspace_path"},
                "timeout_sec": {"type": "integer", "description": "超时秒数", "default": 120},
                "background": {"type": "boolean", "description": "是否作为后台任务提交", "default": False},
            },
            "required": ["command"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="bash",
            managed_permission=True,
            default_local_enabled=True,
            allow_in_read_only=False,
            local_only=True,
        ),
        handler="_bash_command",
        provider_tools=[{"type": "provider-defined", "id": "openai.local_shell", "name": "local_shell", "args": {}}],
    ),
    ToolDef(
        name="todoread",
        description="读取当前 assistant session 的待办列表。",
        parameters={"type": "object", "properties": {}},
        spec=ToolSpec(permission="todoread", managed_permission=True),
        handler="packages.agent.session.session_tool_runtime:_todo_read",
    ),
    ToolDef(
        name="todowrite",
        description="写入当前 assistant session 的待办列表。",
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string"},
                            "priority": {"type": "string"},
                        },
                        "required": ["content"],
                    },
                },
            },
            "required": ["todos"],
        },
        requires_confirm=True,
        spec=ToolSpec(
            permission="todowrite",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
            allow_in_read_only=False,
        ),
        handler="packages.agent.session.session_tool_runtime:_todo_write",
    ),
    ToolDef(
        name="task",
        description="创建一个轻量子任务，使用指定 agent 模式生成独立执行建议。",
        parameters={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "子任务标题"},
                "prompt": {"type": "string", "description": "子任务内容"},
                "subagent_type": {
                    "type": "string",
                    "enum": ["build", "plan", "general", "explore"],
                    "description": "子任务 agent 模式",
                    "default": "general",
                },
                "task_id": {"type": "string", "description": "可选的已存在子任务 id"},
            },
            "required": ["description", "prompt", "subagent_type"],
        },
        spec=ToolSpec(
            permission="task",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
        ),
        handler="packages.agent.session.session_tool_runtime:_task_subagent",
    ),
    ToolDef(
        name="question",
        description=(
            "在执行过程中向用户提问，用于澄清需求、收集偏好、确认实现方向，"
            "行为对齐 opencode question 工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "要向用户提出的问题列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "完整问题"},
                            "header": {"type": "string", "description": "短标题"},
                            "options": {
                                "type": "array",
                                "description": "候选选项",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string", "description": "选项标签"},
                                        "description": {"type": "string", "description": "选项说明"},
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                            "multiple": {"type": "boolean", "description": "是否允许多选"},
                            "custom": {"type": "boolean", "description": "是否允许自定义输入"},
                        },
                        "required": ["question", "header", "options"],
                    },
                },
            },
            "required": ["questions"],
        },
        spec=ToolSpec(
            permission="question",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
        ),
        handler="packages.agent.session.session_tool_runtime:_question",
    ),
    ToolDef(
        name="plan_exit",
        description="在 plan 模式完成计划后调用，请求用户批准切换到 build 模式并开始按计划执行。",
        parameters={"type": "object", "properties": {}},
        spec=ToolSpec(
            permission="plan",
            managed_permission=True,
            default_local_enabled=True,
            default_remote_enabled=True,
        ),
        handler="packages.agent.session.session_tool_runtime:_plan_exit",
    ),
]

TOOL_REGISTRY: list[ToolDef] = [
    *_BASE_TOOL_REGISTRY,
    *RESEARCH_TOOL_REGISTRY,
]

