# ResearchOS 基线技术文档

## 1. 项目定位

`ResearchOS` 基线版本是当前主工程最早确立的研究工作流骨架，产品定位非常清晰：面向科研工作者的 AI 学术研究工作流平台。它希望解决的不是“搜到论文”这一点，而是从主题订阅、论文筛选、精读理解、图谱分析、综述生成到学术写作的全流程问题。

它的核心产品心智是：

- 让 AI 承担研究助理角色。
- 把论文处理从被动阅读改成主动工作流。
- 让输出单位从“单篇论文摘要”升级成“领域理解和研究资产”。

## 2. 技术栈

## 后端

- Python 3.11+
- FastAPI
- SQLAlchemy + Alembic
- APScheduler
- SQLite
- HTTPX
- Pydantic Settings
- OpenAI / Anthropic 可选集成
- PDF 处理能力可选依赖 `pymupdf`

## 前端

- React 18
- TypeScript
- Vite
- Tailwind CSS v4
- React Router 7
- react-markdown + remark/rehype 体系
- react-pdf
- lucide-react

## 3. 工程结构

这套基线版本的顶层结构很干净：

- `apps/`: API 与 Worker 入口。
- `packages/`: AI 能力、领域层、集成层、存储层。
- `frontend/`: React 前端。
- `scripts/`: 初始化与开发脚本。
- `infra/`: 迁移与基础设施相关文件。
- `docs/`: 项目文档。

## 后端主结构

- `apps/api/main.py`: FastAPI 网关。
- `apps/api/routers/`: 路由层。
- `apps/worker/main.py`: 调度与后台任务。
- `packages/ai/`: 研究能力主层。
- `packages/storage/`: 数据层。
- `packages/domain/`: 领域定义与异常。
- `packages/integrations/`: 外部平台接入。

## 前端主结构

- `frontend/src/App.tsx`: 路由入口。
- `frontend/src/components/Layout.tsx`: 应用壳层。
- `frontend/src/components/Sidebar.tsx`: 左侧导航。
- `frontend/src/pages/`: 页面集合。
- `frontend/src/services/api.ts`: 前后端通信封装。

## 4. 运行架构

ResearchOS 基线版本的运行形态是“Web 优先，但前端已经预留桌面启动引导能力”。

## API 入口

`apps/api/main.py` 负责：

- 请求日志中间件。
- 站点密码认证中间件。
- GZip 和 CORS。
- 启动时自动迁移数据库。
- 注册业务路由。

它注册的主路由为：

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

## 鉴权特点

ResearchOS 基线版本的认证是“可选站点级认证”模式：

- 如果未配置 `AUTH_PASSWORD`，整站可以免认证运行。
- 如果启用认证，要求同时配置 `AUTH_SECRET_KEY`。
- 认证中间件支持 `Authorization: Bearer` 与 query token 两种方式，后者适合浏览器直接访问 PDF 或图片资源。

这是一种对个人研究工具非常友好的安全模型：默认轻量，需要时再上锁。

## Worker 与自动化

`apps/worker/main.py` 负责：

- 每小时按 UTC 检查主题调度。
- 执行主题抓取。
- 每日生成研究简报。
- 每周执行图谱维护。
- 启动闲时处理器补做后处理。

这说明 ResearchOS 基线版本的核心能力不是“点一下跑一次”，而是已经具备持续自动化研究处理能力。

## 5. 核心能力模块

`packages/ai/` 的文件集合基本对应了产品能力地图：

- `agent_service.py` / `agent_tools.py`: 研究助手和工具编排。
- `auto_read_service.py`: 自动阅读相关逻辑。
- `brief_service.py`: 日报能力。
- `figure_service.py`: 图表理解。
- `graph_service.py`: 图谱、演化、空白分析。
- `keyword_service.py`: 关键词建议。
- `pipelines.py`: 处理流水线。
- `rag_service.py`: 知识问答。
- `reasoning_service.py`: 结构化推理。
- `recommendation_service.py`: 推荐逻辑。
- `vision_reader.py`: 面向 PDF/页面的视觉阅读补充。
- `wiki_context.py`: 综述生成上下文。
- `writing_service.py`: 学术写作辅助。

这个模块集合说明 ResearchOS 基线版本已经形成完整的研究闭环：输入、处理、问答、图谱、写作都被纳入一套后端能力层。

## 6. 数据与领域建模

从 API 和目录结构可推断，ResearchOS 基线版本的核心领域对象包括：

- Topic: 主题与订阅规则。
- Paper: 论文主实体。
- PipelineRun: 各种处理任务的执行记录。
- Citation/Graph 对象: 领域图谱和引用网络。
- Prompt/Trace: 模型调用与成本痕迹。
- Auth: 站点级认证状态。

ResearchOS 基线版本最大的优点在于：它并不是仅围绕论文 CRUD 建模，而是围绕“研究工作流”来建模。

## 7. 前端与原版 UI 形态

ResearchOS 基线版本前端的产品形态非常明确：

- 左侧固定导航承担主信息架构。
- 首页 `/` 是研究助手页面，采用全屏工作区布局。
- 其余页面走内容容器布局。
- 页面集合围绕“采集、管理、分析、产出”展开。

主要页面包括：

- `Agent`
- `Collect`
- `Dashboard`
- `Papers`
- `PaperDetail`
- `Topics`
- `GraphExplorer`
- `Wiki`
- `DailyBrief`
- `Pipelines`
- `Operations`
- `EmailSettings`
- `Writing`

这一套 UI 不是普通的仪表盘堆叠，而是服务于完整研究流程的信息架构。

## 8. 启动与构建

## 后端

- `pyproject.toml` 管理 Python 包与可选依赖。
- README 提供 Docker 与本地开发两条路径。
- 数据库通过启动自动迁移。

## 前端

- `frontend/package.json` 提供 `dev`、`build`、`preview`。
- 同时具备 lint/format 脚本，说明原版前端工程化程度比当前主工程更整洁。

## 桌面准备度

虽然本参考目录顶层未包含完整的 `src-tauri/` 壳层，但前端里已经存在 `DesktopBootstrap.tsx` 和 Tauri 相关依赖，说明原版前端对桌面包装是有预留设计的。

## 9. 对 ResearchOS 的直接意义

ResearchOS 基线版本对当前主工程最重要的价值有三点：

- 它是产品主心智的来源，定义了“研究工作流”而不是“功能堆叠”的方向。
- 它是当前前端 UI 的原始基线，最适合做视觉和交互恢复参考。
- 它的后端能力边界非常清晰，是 ResearchOS 继续扩展 Agent/runtime 前最稳定的核心层。

因此，ResearchOS 基线版本既是参考基线，也是当前工程应该持续守住的产品骨架。
