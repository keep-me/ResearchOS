# ResearchOS 技术文档

## 1. 项目定位

ResearchOS 是当前主工程，目标不是单点的论文管理或单独的聊天助手，而是把研究输入、AI 处理、知识沉淀、工作区执行和桌面交互整合到一个系统里。

从仓库结构看，它已经具备五条主线：

- 论文输入：主题订阅、检索、抓取、引用导入。
- 论文处理：粗读、深读、图表分析、嵌入、推理分析。
- 知识生产：图谱、Wiki、日报、写作辅助。
- Agent 执行：研究助手、技能注册、MCP、工作区命令执行、远程 SSH 执行。
- 桌面交互：`src-tauri/` 负责本地桌面壳层，FastAPI 作为 sidecar 后端运行。

它本质上是在 `ResearchOS` 早期基线版本上持续演进、正在向“研究操作系统”方向扩展的桌面研究工作台。

## 2. 技术栈

## 后端

- Python 3.11+
- FastAPI
- SQLAlchemy + Alembic
- APScheduler
- SQLite
- HTTPX
- Pydantic Settings
- OpenAI / Anthropic 等 LLM SDK（按可选依赖启用）

## 前端

- React 18
- TypeScript
- Vite
- Tailwind CSS v4
- React Router
- react-markdown + KaTeX
- react-pdf
- lucide-react

## 桌面层

- Tauri 2
- Rust sidecar launcher
- 本地 sidecar 可执行文件 `researchos-server-*`

## 3. 工程结构

## 顶层目录

- `apps/`: Python 应用入口，包含 API 与 Worker。
- `packages/`: 领域层、AI 能力层、存储层、外部集成层。
- `frontend/`: React 前端。
- `src-tauri/`: 桌面壳层、sidecar 启动与打包配置。
- `scripts/`: 启动、构建、MCP Server、桌面打包等脚本。
- `reference/`: 四个参考项目，供后续演进借鉴。
- `data/`: 运行时数据目录。
- `docs/`: 项目文档与技术路线草稿。

## 后端分层

- `apps/api/main.py`: FastAPI 入口，负责中间件、迁移、路由注册。
- `apps/api/routers/`: 按业务域拆分的 API 层。
- `apps/worker/main.py`: 定时任务 Worker，负责抓取、日报、图谱维护、闲时处理。
- `packages/ai/`: AI 与研究流程核心能力。
- `packages/storage/`: 数据库模型、仓储、事务与迁移。
- `packages/integrations/`: 外部服务接入。
- `packages/domain/`: 枚举、异常、通用 schema、任务/领域类型。

## 前端分层

- `frontend/src/App.tsx`: 路由总入口。
- `frontend/src/components/`: 通用组件、布局、侧栏、PDF 阅读器、图谱面板。
- `frontend/src/pages/`: 业务页面。
- `frontend/src/contexts/`: 会话、全局任务、Toast、Agent Session 等状态容器。
- `frontend/src/services/api.ts`: REST API 调用封装。
- `frontend/src/lib/tauri.ts`: Web/Tauri 双态桥接。

## 桌面层分层

- `src-tauri/src/main.rs`: 启动 sidecar 后端、监听 stdout/stderr、向前端派发 `backend-ready` 与 `backend-error` 事件。
- `src-tauri/tauri.conf.json`: 桌面窗口参数、打包、前端 dev/build 连接方式。
- `src-tauri/binaries/`: sidecar 可执行文件所在位置。

## 4. 运行架构

ResearchOS 当前是一个“三段式”架构：

1. Tauri 壳层启动前端 WebView。
2. Rust 主进程拉起 FastAPI sidecar，并从 stdout 解析 API 端口。
3. React 前端通过 `lib/tauri.ts` 解析 API 基地址，再调用 FastAPI 接口。

如果不走桌面模式，前端也可以直接以普通 Vite Web 应用运行。

## 后端入口职责

`apps/api/main.py` 的职责比较集中：

- 请求日志中间件。
- 鉴权中间件。
- GZip 和 CORS。
- 启动时运行数据库迁移。
- 注册业务路由。

当前实际注册的路由包括：

- `system`
- `papers`
- `topics`
- `graph`
- `agent`
- `content`
- `pipelines`
- `settings`
- `writing`
- `jobs`
- `auth`

仓库中还存在 `mcp_bridge.py` 和 `opencode.py` 两个路由文件，但当前主入口尚未显式注册，说明这部分属于扩展或实验态能力。

## Worker 职责

`apps/worker/main.py` 负责后台自动化：

- 每小时检查主题调度并触发抓取。
- 每日生成研究简报。
- 每周执行图谱维护。
- 启动闲时处理器，补做低优先级任务。

这意味着项目的自动化处理不完全依赖用户手工点击，而是已经具备持续运行的研究管线特征。

## 5. 核心能力模块

## 基线能力继承

当前项目仍保留 `ResearchOS` 基线版本的主干能力：

- 论文主题订阅与抓取。
- 论文库、详情页、PDF 阅读。
- 粗读、深读、RAG 问答。
- 图谱、Wiki、日报、写作工具。

## 研究助手与扩展能力

相比早期 `ResearchOS` 基线版本，当前 `packages/ai/` 增加了更明显的 Agent/runtime 方向模块：

- `academic_query.py`: 学术查询能力扩展。
- `agent_runtime_state.py`: Agent 运行时状态持久化或编排辅助。
- `native_mcp_manager.py`: 本地 MCP 资源管理。
- `opencode_manager.py`: 与 OpenCode 风格 runtime 的桥接尝试。
- `researchos_mcp.py`: ResearchOS 自身 MCP 暴露层。
- `skill_registry.py`: 技能注册表。
- `web_search_service.py`: Web 搜索服务封装。
- `workspace_executor.py`: 本地工作区执行。
- `workspace_ssh_executor.py`: 远程 SSH 工作区执行。
- `workspace_server_registry.py`: 工作区服务器注册表。

这些文件说明当前项目已不再只是“论文处理平台”，而是在往“研究 Agent 平台”演进。

## 6. 数据与状态模型

虽然本次没有逐个模型全文展开，但从目录和 API 能力可以看出，当前系统围绕以下核心对象组织：

- Topic: 主题订阅、抓取策略、调度频率。
- Paper: 论文元数据、状态、PDF、嵌入、AI 报告。
- PipelineRun: 处理流水线执行历史。
- Citation/Graph 相关对象: 引用网络与研究图谱。
- Prompt/Cost 相关对象: 模型调用痕迹与成本控制。
- Auth 状态: 登录、Token、站点级保护。

前端状态主要由以下 Context 组织：

- `ConversationContext`: 多会话状态。
- `AgentSessionContext`: 研究助手上下文。
- `GlobalTaskContext`: 全局运行任务与状态提示。
- `ToastContext`: 全局消息提示。

## 7. 前端与 UI 形态

当前前端已经恢复为接近 `ResearchOS` 基线版本的交互基线，核心特点是：

- 左侧固定导航侧栏。
- 首页为全屏研究助手页。
- 其余页面采用内容区容器布局。
- 主要页面包括 `Collect`、`Dashboard`、`Papers`、`PaperDetail`、`Topics`、`GraphExplorer`、`Wiki`、`DailyBrief`、`Pipelines`、`Operations`、`EmailSettings`、`Writing`。
- `DesktopBootstrap.tsx` 负责在桌面模式下先做配置检查和后端就绪等待。

仓库里还保留了当前项目新增的实验页面：

- `OpenClaw.tsx`
- `ResearchWorkbench.tsx`
- `Tasks.tsx`

这些文件仍然存在于代码中，但在恢复为统一基线 UI 后，不再是当前主导航的主入口。这是一个很健康的过渡状态：先统一主壳层，再逐步决定哪些实验能力正式进入产品心智。

## 8. 启动、开发与打包

## 后端

- Python 依赖通过 `pyproject.toml` 管理。
- 本地开发通常使用 `uvicorn apps.api.main:app --reload`。
- Worker 单独运行 `apps/worker/main.py`。

## 前端

- `frontend/package.json` 提供 `dev`、`build`、`preview`。
- 当前前端构建已验证通过 `vite build`。

## 桌面端

- `src-tauri/tauri.conf.json` 指向 `../frontend` 作为前端资源。
- 打包时会捆绑 `researchos-server` sidecar。
- Rust 侧通过 launcher 配置和 sidecar 管理本地后端生命周期。

## 9. 当前状态判断

ResearchOS 当前的真实状态可以概括为：

- 主干产品仍然是 `ResearchOS` 研究工作流应用。
- 工程层已经加入桌面壳层，具备独立桌面应用交付能力。
- 能力层正在明显向 Agent runtime、Workspace、MCP、Skill 方向延展。
- 仓库里存在不少实验性文件和未提交改动，说明项目正处于高速演进阶段。

## 10. 对后续迭代的意义

ResearchOS 后续演进最适合采用“分层吸收参考项目”的策略：

- UI 基线继续以 `ResearchOS` 基线版本为一致性来源。
- 桌面壳层和本地资产建模优先参考 `ResearchClaw`。
- Agent runtime、权限和工作区抽象优先参考 `opencode-dev`。
- 论文输入流、远程研究工作流和扩展入口优先参考 `Amadeus`。
