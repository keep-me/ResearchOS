# 第 01 课：ResearchOS 是什么

## 1. 本课定位

这一课不是读代码细节，而是先建立正确的大图。很多人一上来就钻进某个函数，最后只记住了 API 名字，却不知道整个项目为什么存在、为什么会长成现在这样。对 `ResearchOS` 来说，这种风险尤其大，因为它同时包含论文管理、研究服务、Agent runtime、前端工作台和桌面化链路。

## 2. 学完你应该能回答的问题

- `ResearchOS` 到底是“论文工具”还是“研究平台”。
- 它为什么不能只用一个简单的聊天页面来实现。
- 它的五条主线能力分别是什么。
- 它和当前仓库中的参考代码 `reference/claw-code-main` 分别像在哪里、不像在哪里。

## 3. 学习前准备

- 先浏览 `apps/api/main.py`、`apps/worker/main.py`、`frontend/src/App.tsx`。
- 再浏览 `packages/storage/models.py`、`packages/agent/`、`packages/ai/project/`。
- 不要求记住所有模块，但要先对 `papers`、`graph`、`wiki`、`agent`、`workspace`、`project workflow` 这些关键词在代码里的落点有直觉。

## 4. 详细讲解

### 4.1 不要把它理解成单点功能应用

`ResearchOS` 的真实定位不是“帮你搜论文的工具”，也不是“套了论文数据的聊天机器人”。从当前仓库的入口文件、模块分层和数据模型可以归纳出，它至少有五条稳定主线：

- 论文输入：主题订阅、检索、抓取、引用导入。
- 论文处理：粗读、深读、图表分析、嵌入、推理分析。
- 知识生产：图谱、Wiki、日报、写作辅助。
- Agent 执行：研究助手、技能、MCP、工作区命令、远程 SSH。
- 交互与交付：React 前端、桌面化入口、本地数据目录、打包链路。

如果你只盯着某一个页面，就会误判这个项目的重心。这个仓库更像一套“研究工作流操作系统”，而不是一个页面产品。

### 4.2 为什么会出现这么多不同类型的模块

很多新手看到 `apps/`、`packages/ai/`、`packages/agent/`、`frontend/`、`tests/` 会觉得很乱。其实这些目录正对应着产品能力的增长过程：

- 先有论文业务，产生 `paper`、`research` 相关模块。
- 后来加入更多自动化处理，产生 `worker`、`ops`、`daily_runner`。
- 再后来加入研究助手和工作区执行，出现 `agent`、`workspace`、`mcp`。
- 随着功能越来越多，前端不再只是几个按钮，而是变成完整工作台。

所以目录复杂，不一定说明设计失败，很多时候只是说明产品边界扩大了。

### 4.3 本项目的核心产品心智

把 `ResearchOS` 想成三层会更容易理解：

1. 输入层：把论文和研究材料持续带进系统。
2. 处理层：让 AI 对材料做结构化分析和加工。
3. 产出层：把分析结果变成图谱、总结、写作、项目执行结果。

如果再往上加一层，就是“Agent 协调层”：它让系统不只是被动提供工具，而是开始主动组织工具、工作区和执行流。

### 4.4 为什么课程一开始就要看参考项目

因为只看当前仓库，你很容易把很多设计看成“作者随手写的”。参考项目能帮你分辨哪些是共性问题：

- `reference/claw-code-main` 说明科研工作台为什么常常需要统一壳层、本地资产和多面板布局。
- 当前仓库里的 `packages/agent` 和 `packages/ai/project` 说明 Agent runtime、工作区和项目执行为什么会逐步从页面逻辑中抽出来。

这不是为了抄设计，而是为了给你建立“行业坐标系”。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 更像“本地优先研究工作台”。它特别强调统一壳层、本地能力、会话和助手界面共存。对于 `ResearchOS`，这能帮助你理解为什么仓库里会同时存在 `frontend/`、`apps/desktop/`、`packages/agent/`、`packages/ai/project/` 这些不同层次。

### 5.2 回到当前仓库

当前仓库真正有价值的不是某个单点页面，而是这些代码层次已经同时出现：

- `packages/storage/models.py` 代表研究资产模型。
- `packages/ai/research/` 代表知识生产能力。
- `packages/agent/` 代表会话、工具、工作区运行时。
- `packages/ai/project/` 代表项目执行与实验编排。

## 6. 代码精读顺序

1. `apps/api/main.py`
2. `apps/worker/main.py`
3. `packages/storage/models.py`
4. `packages/agent/session/`
5. `packages/ai/project/`
6. `frontend/src/App.tsx`
7. `reference/claw-code-main/src/`
8. `reference/claw-code-main/rust/`

## 7. 动手任务

1. 用不超过 300 字描述你理解的 `ResearchOS`。
2. 画一张简单结构图，把“论文输入、论文处理、知识生产、Agent 执行、交互与交付”五层画出来。
3. 对每一层写一个“你最不理解的问题”，为后续课程建立问题清单。

## 8. 验收标准

- 你能不用看文档，复述出 `ResearchOS` 的五条主线能力。
- 你能解释为什么它不是“论文版 ChatGPT”。
- 你能说明三个参考项目分别提供哪一种观察角度。

## 9. 常见误区

- 误区一：把项目理解成纯后端 API 服务。
- 误区二：把 Agent 看成唯一主角，忽视论文和研究资产。
- 误区三：看到参考项目就想“照搬”，而不是理解它们在解决什么问题。
