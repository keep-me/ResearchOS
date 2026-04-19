# 第 23 课：Tool、Skill 与 MCP

## 1. 本课定位

Agent 之所以不是普通对话框，关键就在于它能调用外部能力。这一课要把三个最容易混淆的概念分开：Tool、Skill、MCP。

## 2. 学完你应该能回答的问题

- Tool、Skill、MCP 分别是什么。
- 为什么它们不能被简单看成同一类“扩展”。
- `tool_registry.py`、`skill_registry.py`、`mcp_service.py` 各自解决什么问题。
- 为什么扩展层必须显式建模，而不能靠约定俗成。

## 3. 学习前准备

- 阅读 `packages/agent/tools/tool_registry.py`。
- 阅读 `packages/agent/tools/skill_registry.py`。
- 阅读 `packages/agent/mcp/mcp_service.py`。

## 4. 详细讲解

### 4.1 Tool 是“可调用能力”

Tool 的重点在于：

- 有明确名字
- 有参数 schema
- 有 handler
- 有权限或执行策略

`tool_registry.py` 里能看到 builtin、custom、compat 等来源，这说明工具不是随手绑个函数，而是被正式纳入注册和治理体系。

### 4.2 Skill 是“带方法论的本地知识包”

Skill 和 Tool 的区别在于：

- Tool 更像动作能力
- Skill 更像流程知识和操作指南

`skill_registry.py` 里在扫描本地 `SKILL.md`、提取 frontmatter、构建技能项。说明 skill 系统主要解决：

- 本地知识可发现
- 特定任务工作流复用
- 给 Agent 提供结构化方法提示

所以 Skill 更像“专家手册”，而不是直接执行接口。

### 4.3 MCP 是“外部服务接入协议层”

`mcp_service.py` 展示的是另一种层次：

- 维护服务注册表
- 支持 stdio 和 http transport
- 管理连接生命周期
- 发现外部工具列表

这意味着 MCP 不等于某个具体工具，而是一个接入外部能力的协议层和连接管理层。

### 4.4 为什么三者必须区分

如果你把三者混成一个概念，就会出很多设计问题：

- Tool 粒度不清
- Skill 变成不可治理的 prompt 碎片
- MCP 连接生命周期没人负责

清晰的理解应该是：

- Tool：Agent 当前可调用的动作接口
- Skill：Agent 当前可参考的方法论与领域知识
- MCP：把外部服务暴露成可接入能力的桥

### 4.5 扩展层为什么对平台化至关重要

一旦 Agent 能力想持续扩展，就必须有正式扩展层。否则每加一个能力，都会散落在不同页面或不同函数里。`ResearchOS` 现在已经在往平台化走，这三层正是关键标志。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 也包含 `skills`、`assistant`、`bridge` 等目录，这提醒你：扩展层一旦不正式建模，助手工作台几乎无法长期演化。当前 `ResearchOS` 的 Tool、Skill、MCP 分层正是在解决这个问题。

## 6. 代码精读顺序

1. `packages/agent/tools/tool_registry.py`
2. `packages/agent/tools/skill_registry.py`
3. `packages/agent/mcp/mcp_service.py`
4. 再回看 `scripts/researchos_mcp_server.py`

## 7. 动手任务

1. 用一句话分别定义 Tool、Skill、MCP。
2. 说明如果新增一个外部服务，为什么不能只在 prompt 里告诉模型“你会用它”。
3. 从 `tool_registry.py` 里总结工具注册系统至少做了哪 4 件事。

## 8. 验收标准

- 你能清晰区分三者。
- 你能说明扩展层正式建模的必要性。
- 你能理解 MCP 不是具体工具，而是协议与连接管理层。

## 9. 常见误区

- 误区一：把 Tool、Skill、MCP 混成一类“插件”。
- 误区二：把 Skill 理解成另一个函数注册表。
- 误区三：低估连接管理和配置持久化的重要性。
