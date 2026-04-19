# ResearchOS 技术路线

## 1. 项目定位

ResearchOS 当前定位是“AI 驱动的学术研究工作流平台”。它不只是论文管理器，也不只是聊天助手，而是试图把以下几条链路放进一个系统里：

- 论文输入：检索、订阅、抓取、导入。
- 论文处理：粗读、精读、图表理解、嵌入、引用同步。
- 知识生产：图谱、Wiki、研究简报、写作辅助。
- Agent 执行：研究助手、工作区文件读写、终端/远程执行、MCP 接入。
- 桌面交互：`frontend + src-tauri` 说明项目已经明显朝桌面研究工作台演进。

从仓库结构看，当前项目已经具备成为“研究操作系统”的雏形，但还处于“功能很多、架构已分层、产品心智仍需统一”的阶段。

## 2. 当前技术栈与目录结构

## 后端

- Python 3.11+
- FastAPI
- SQLAlchemy + Alembic
- APScheduler
- 本地 SQLite
- 多模型接入：OpenAI、Anthropic 等

## 前端

- React 18
- TypeScript
- Vite
- Tailwind CSS v4
- React Router
- Tauri API

## 桌面层

- `src-tauri/` 说明桌面打包与本地系统能力走 Tauri 路线。
- `apps/desktop/` 还有桌面服务入口，说明桌面能力还在持续整合。

## 核心目录

- `apps/api/`：FastAPI 入口与路由。
- `packages/ai/`：研究 AI 能力主层，包含 agent、RAG、graph、brief、writing、pipelines。
- `packages/storage/`：数据库、ORM 模型、仓储。
- `packages/integrations/`：arXiv、OpenAlex、Semantic Scholar、邮件、LLM 等外部集成。
- `packages/domain/`：枚举、异常、schema、任务跟踪等领域层。
- `frontend/src/`：页面、组件、上下文、hooks、API 服务层。

## 3. 当前系统分层判断

## 3.1 API 层

`apps/api/main.py` 已经承担了标准网关职责：

- 中间件：请求日志、认证、GZip、CORS。
- 路由注册：`system / papers / topics / graph / agent / content / pipelines / writing / jobs / auth / opencode / mcp_bridge`。
- 生命周期：启动时运行数据库迁移，同时挂载 `researchos_mcp`。

这说明项目已经不是单一 REST 服务，而是在向“HTTP API + MCP 服务 + 桌面桥接服务”并行演进。

## 3.2 领域与能力层

`packages/ai/` 已经是系统核心：

- `pipelines.py`：论文抓取、处理流水线。
- `agent_service.py` / `agent_tools.py`：研究助手和工具调度。
- `rag_service.py`：知识问答与跨论文检索。
- `graph_service.py`：图谱、演化、研究空白。
- `brief_service.py`：日报/周报。
- `writing_service.py`：写作辅助。
- `workspace_executor.py` / `workspace_ssh_executor.py`：本地/远程工作区执行。
- `skill_registry.py` / `native_mcp_manager.py` / `opencode_manager.py`：运行时扩展能力。

这层已经具备平台化趋势，但目前的主要问题是模块很多，产品心智还不够“主线化”。

## 3.3 数据层

`packages/storage/models.py` 展示了当前数据模型的主骨架：

- `Paper`：论文主实体。
- `AnalysisReport`：粗读/深读结果。
- `ImageAnalysis`：图表或公式理解结果。
- `Citation`：引用边。
- `PipelineRun`：任务执行历史。
- `PromptTrace`：模型调用与成本痕迹。
- `TopicSubscription`：主题/订阅。
- `PaperTopic`：论文与主题关系。

这套模型已经覆盖“输入-处理-知识-成本-任务”主链路，说明后端数据基础是够用的。

## 4. 当前业务主链路

ResearchOS 的主链路可以抽成五段：

### 1. 输入层

- 主题订阅或手动检索。
- 通过 arXiv / 外部接口抓取论文元数据。
- 将论文写入 `papers`，并与 topic 建立关系。

### 2. 处理层

- 触发 `skim / deep_dive / embed / figure / graph` 等 pipeline。
- 写入 `analysis_reports`、`prompt_traces`、`pipeline_runs`。
- 需要时下载 PDF 并提取全文。

### 3. 资产层

- 在 `papers / topics / folderStats` 等视图中组织资产。
- 对论文做收藏、状态追踪、分类、工作区绑定。

### 4. 产出层

- 生成图谱、研究空白、综述、简报、写作内容。
- 以“研究洞察”而不是“单篇论文处理结果”为输出单位。

### 5. 执行层

- 研究助手读取当前工作区上下文。
- 通过 Skills、MCP、workspace executor 进行文件与命令层操作。
- 向 opencode 风格的运行时继续靠拢。

## 5. 当前前端与 UI 布局路线

## 当前现状

前端已经有完整页面群：

- `/` 研究工作台
- `/collect` 论文收集
- `/papers` 文献资产
- `/topics` 文件夹管理
- `/assistant` 研究助手
- `/graph` 研究洞察
- `/wiki` 专题综述
- `/brief` 研究简报
- `/writing` 写作助手
- `/tasks` 与 `/operations`

但原先的问题在于：

- 页面更多是“功能列表”，而不是“研究流程”。
- 侧栏信息密度高，但层级不够清晰。
- 首页更像入口页，不像桌面研究总控台。

## 本轮前端改造方向

本轮已按 `ResearchClaw` 路线做了第一轮壳层改造：

- 外层改成桌面窗口式外框，而不是纯网页式铺满布局。
- 侧边栏改成更轻的研究工作区导航风格。
- 顶部加入轻量 tab 切换，形成“桌面内模块切换”的体验。
- 首页改成“研究工作台总控页”，突出今日脉冲、主通道、任务、推荐阅读和工作区入口。

## 这条 UI 路线后续应继续怎么走

### Phase A：统一壳层

- 所有页面遵守同一套 `shell + page header + panel` 规则。
- 页面标题、副标题、状态胶囊、操作按钮位置统一。
- 卡片圆角、间距、边框、悬浮反馈统一。

### Phase B：按研究流重组页面

- `collect` 负责输入。
- `papers` 负责资产管理。
- `assistant / graph / wiki / brief / writing` 负责知识输出。
- `tasks / operations` 负责系统面。

### Phase C：强化工作区

- 把“聊天属于哪个工作区、工作区里有哪些论文/文件/任务”做成可视主线。
- 让侧栏从“导航栏”进一步变成“研究上下文栏”。

## 6. 推荐的技术演进路线

## 第一阶段：稳定主链路

- 统一 API 返回结构与错误模型。
- 给 pipeline、agent、workspace 相关能力补齐边界测试。
- 统一前端 loading、empty、error 状态组件。

## 第二阶段：把论文资产体系做扎实

- 统一 `papers / topics / folderStats / recommendations` 的数据口径。
- 对“阅读状态、收藏、标签、处理阶段、工作区归属”建立清晰资产模型。
- 在前端形成真正的资产视图，而不只是列表视图。

## 第三阶段：把研究助手 runtime 平台化

- 将 `agent_service / skill_registry / workspace_executor / native_mcp_manager` 抽象成统一 runtime。
- 增加权限边界、执行审计、上下文记忆与任务编排。
- 向 `opencode-dev` 的“会话-权限-工作区-工具”模型靠拢。

## 第四阶段：桌面端强化

- 统一 Web 与 Tauri 桌面能力的边界。
- 将本地文件、终端、工作区授权、系统通知等桌面能力全部明确到单独层。
- 使桌面端成为研究工作主端，Web 端作为轻量访问端。

## 7. 本项目最应该借鉴参考项目的地方

## 借鉴 Amadeus

- Tracker 和 Latest Feed 的输入端闭环。
- 远程研究执行的 ARIS 思路。
- 论文收集与导出链路。

## 借鉴 ResearchClaw

- 桌面壳层布局。
- Electron/Tauri 场景下的本地研究工作台体验。
- Library/Projects/Tasks 的结构化导航。

## 借鉴 opencode-dev

- Agent runtime 分层。
- 权限控制与工作区执行模型。
- 多客户端共享核心的工程组织方式。

## 8. 最终路线判断

ResearchOS 最合理的终局，不是继续堆功能，而是把自己收束成三层：

- 底层：研究资产与执行 runtime。
- 中层：论文输入、处理、知识生产流水线。
- 上层：桌面研究工作台与助手交互壳。

只要这三层收紧，ResearchOS 就能从“功能很多的研究工具箱”进化为“真正的研究操作系统”。
