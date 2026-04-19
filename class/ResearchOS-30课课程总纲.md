# ResearchOS 三十课课程总纲

## 1. 文档定位

这是一份面向“偏新手向、先理解架构”的课程总纲，目标是在大约 30 节课内，循序渐进地覆盖 `ResearchOS` 当前仓库的核心知识点。课程重点不是立刻改代码，而是先建立完整的系统认知，再逐步过渡到可验证、可跟踪、可扩展的工程理解。课程内容严格以当前仓库的真实代码、目录结构、配置、迁移和测试为准。

本总纲适合以下学习目标：

- 看懂 `ResearchOS` 这个科研助手项目到底由哪些部分组成。
- 跑通本地开发环境，并知道各个进程、端口、数据目录的关系。
- 理解后端、论文业务、Agent runtime、前端、桌面端之间的连接方式。
- 为后续“深入读代码”“做功能修改”“写测试”“参与重构”打下基础。

## 2. 学习对象

- 对 Python、前端、数据库、LLM 工程有初步听说，但项目经验还不系统的学习者。
- 想深入理解这个仓库，而不是只会启动和使用的开发者。
- 希望以后能够独立读模块、跟调用链、做小型改动的人。

## 3. 建议学习方式

- 每节课先看“学习目标”，明确这一课要回答什么问题。
- 再按“必读代码入口”进入仓库，不要求一遍全部看懂，但要能画出本节的调用链。
- 最后完成“课后练习”，通过复述、运行、跟踪、比对来巩固理解。
- 建议每学完 5 节课，自己写一次阶段总结，记录已经搞清楚和仍然模糊的点。

## 4. 预备知识

- Python 基础语法、包与模块、虚拟环境
- HTTP 基础、REST API 基本概念
- React 组件、状态、路由的基础概念
- SQLite/SQLAlchemy 的基本认知
- 了解 LLM、RAG、流式输出、向量检索这些术语即可，不要求一开始就精通

## 5. 课程结构总览

本课程分为 6 个模块，共 30 节课：

1. 认识项目与基础环境
2. 后端主干与 Web 服务基础
3. 数据层与论文主业务
4. 研究能力与自动化管线
5. Agent、工作区与项目执行
6. 前端、桌面端、测试与演进

---

## 模块一：认识项目与基础环境

### 第 1 课：ResearchOS 是什么

- 学习目标：理解项目的产品定位、目标用户、核心能力，建立“这不是单点工具，而是一套研究工作流系统”的整体认识。
- 必读代码入口：`apps/api/main.py`、`apps/worker/main.py`、`packages/storage/models.py`、`packages/agent/`、`frontend/src/App.tsx`
- 课后练习：用自己的话写出 ResearchOS 的 5 条主线能力，并解释“科研助手”和“研究操作系统”有什么区别。

### 第 2 课：仓库结构入门

- 学习目标：认识根目录各层职责，分清源码、运行数据、工程资产、参考资料和可再生产物。
- 必读代码入口：`apps/`、`packages/`、`frontend/src/`、`scripts/`、`tests/`、`reference/claw-code-main/`
- 课后练习：画出根目录脑图，标出 `apps/`、`packages/`、`frontend/`、`data/`、`scripts/`、`tests/` 的职责。

### 第 3 课：技术栈总览

- 学习目标：搞清楚 Python/FastAPI/SQLite/React/Tauri/LLM 在这个项目里分别负责什么，避免只记技术名词。
- 必读代码入口：`pyproject.toml`、`frontend/package.json`、`packages/storage/db.py`、`packages/config.py`
- 课后练习：整理一张“技术 -> 在本项目中的职责 -> 你目前理解程度”的三列表。

### 第 4 课：本地启动全流程

- 学习目标：掌握项目的本地启动路径、依赖准备、端口使用、健康检查和最小可运行链路。
- 必读代码入口：`scripts/local_bootstrap.py`、`apps/api/main.py`、`frontend/package.json`、`scripts/start_api_dev.ps1`、`scripts/start_frontend_dev.ps1`
- 课后练习：亲自完成一次本地启动，并记录数据库初始化、后端启动、前端启动各自的命令与输出含义。

### 第 5 课：从页面到代码的第一条调用链

- 学习目标：建立“前端页面 -> API -> 后端服务 -> 数据层”的第一条整体心智模型。
- 必读代码入口：`frontend/src/App.tsx`、`frontend/src/services/api.ts`、`apps/api/main.py`
- 课后练习：任选一个页面入口，追踪一次它调用的 API 路径，并写出请求从浏览器进入后端的大致过程。

---

## 模块二：后端主干与 Web 服务基础

### 第 6 课：FastAPI 应用入口

- 学习目标：看懂 `apps/api/main.py` 在整个系统中的位置，理解应用初始化、中间件挂载、路由注册和生命周期事件。
- 必读代码入口：`apps/api/main.py`
- 课后练习：画出 `main.py` 的职责清单，并解释为什么数据库 bootstrap 放在启动阶段。

### 第 7 课：中间件与公共能力

- 学习目标：理解请求日志、认证中间件、GZip、CORS、统一异常处理为什么要放在入口层。
- 必读代码入口：`apps/api/main.py`、`packages/domain/exceptions.py`、`packages/auth.py`
- 课后练习：写出一次受保护 API 请求经过的公共处理步骤，并解释每一步解决什么问题。

### 第 8 课：Router 分层方式

- 学习目标：认识 API 路由如何按业务域拆分，理解 route 层和 service/repository 层之间的职责边界。
- 必读代码入口：`apps/api/routers/`、`apps/api/routers/papers.py`、`apps/api/routers/projects.py`
- 课后练习：选两个 router，对比它们的接口风格、依赖注入方式和调用下层模块的模式。

### 第 9 课：配置系统与环境变量

- 学习目标：搞清楚 `.env`、Pydantic Settings、运行时配置、前后端地址和模型配置是如何进入系统的。
- 必读代码入口：`.env.example`、`packages/config.py`、`apps/desktop/server.py`
- 课后练习：列出至少 10 个关键环境变量，并说明它们分别影响哪些功能。

### 第 10 课：认证与权限

- 学习目标：理解站点密码、JWT、登录状态检查、受保护接口和文件访问鉴权的基本链路。
- 必读代码入口：`apps/api/routers/auth.py`、`packages/auth.py`、`frontend/src/pages/Login.tsx`
- 课后练习：梳理用户从访问站点到获得 token，再到后续请求附带认证信息的全过程。

---

## 模块三：数据层与论文主业务

### 第 11 课：存储启动链路

- 学习目标：理解数据库迁移、启动引导、运行时 bootstrap 之间的关系，明白系统为什么能在启动时自动准备存储层。
- 必读代码入口：`packages/storage/bootstrap.py`、`packages/storage/db.py`、`scripts/local_bootstrap.py`
- 课后练习：写出“应用启动时存储层准备过程”的顺序图，说明 migration 和 runtime bootstrap 的区别。

### 第 12 课：SQLAlchemy 与 Repository 模式

- 学习目标：理解数据模型、session、repository、facade 的分层方式，以及项目为什么不把数据库逻辑全写进 router。
- 必读代码入口：`packages/storage/models.py`、`packages/storage/repositories.py`、`packages/storage/repository_facades.py`
- 课后练习：选一个实体，追踪它从模型定义到仓储访问的完整路径。

### 第 13 课：Topic 与 Paper 核心对象

- 学习目标：认识系统最核心的业务对象，理解主题订阅、论文元数据、处理状态、知识产物之间的关系。
- 必读代码入口：`packages/storage/models.py`、`packages/storage/topic_repository.py`、`packages/storage/paper_repository.py`
- 课后练习：尝试画出 Topic、Paper 及相关运行记录之间的概念关系图。

### 第 14 课：论文输入链路

- 学习目标：理解论文如何从外部世界进入系统，包括主题检索、抓取、引用补全和外部元数据接入。
- 必读代码入口：`packages/integrations/arxiv_client.py`、`packages/integrations/openalex_client.py`、`packages/integrations/semantic_scholar_client.py`
- 课后练习：比较 ArXiv、OpenAlex、Semantic Scholar 在本项目中的角色差异。

### 第 15 课：论文处理流水线

- 学习目标：理解粗读、深读、PDF 解析、证据抽取、图表分析、分类等模块如何组成论文处理链。
- 必读代码入口：`packages/ai/paper/pipelines.py`、`packages/ai/paper/paper_analysis_service.py`、`packages/ai/paper/pdf_parser.py`
- 课后练习：选择“粗读”或“深读”其中一条链路，画出输入、处理中间步骤、输出结果。

---

## 模块四：研究能力与自动化管线

### 第 16 课：RAG 与跨论文问答

- 学习目标：理解系统如何基于论文库做检索、拼接上下文并生成回答，建立 RAG 在项目中的具体认识。
- 必读代码入口：`packages/ai/research/rag_service.py`、`packages/ai/paper/paper_evidence.py`
- 课后练习：总结“用户提问 -> 检索证据 -> 生成答案”的关键步骤，并说明证据为什么重要。

### 第 17 课：图谱与领域洞察

- 学习目标：理解引用树、主题图谱、桥接论文、研究前沿等能力背后的服务划分和目标。
- 必读代码入口：`packages/ai/research/graph_service.py`、`apps/api/routers/graph.py`
- 课后练习：解释图谱类功能和普通论文列表功能的本质区别，并举出 3 个应用场景。

### 第 18 课：Wiki、日报与写作助手

- 学习目标：理解知识生产类模块如何把论文分析结果进一步加工成综述、日报和写作支持能力。
- 必读代码入口：`packages/ai/research/research_wiki_service.py`、`packages/ai/research/brief_service.py`、`packages/ai/research/writing_service.py`
- 课后练习：比较 Wiki、Daily Brief、Writing 三类输出的目标用户和输入来源。

### 第 19 课：成本、性能与稳定性思路

- 学习目标：理解 LLM 项目里为什么需要成本守卫、缓存、限流、并发控制和超时策略。
- 必读代码入口：`packages/ai/research/cost_guard.py`、`packages/ai/ops/rate_limiter.py`、`packages/integrations/llm_provider_policy.py`
- 课后练习：列出 5 种在 LLM 系统里常见的成本或稳定性风险，并说明本项目怎样缓解。

### 第 20 课：Worker 与自动调度

- 学习目标：理解后台任务是如何自动运行的，以及为何科研系统不能只靠手动触发。
- 必读代码入口：`apps/worker/main.py`、`packages/ai/ops/daily_runner.py`、`packages/ai/ops/idle_processor.py`
- 课后练习：梳理 Worker 的定时任务列表，并说明这些任务分别对产品体验有什么价值。

---

## 模块五：Agent、工作区与项目执行

### 第 21 课：统一 LLM Client

- 学习目标：理解多模型提供商抽象、模型解析、流式事件和 embedding 调用是如何被统一封装的。
- 必读代码入口：`packages/integrations/llm_client.py`、`packages/integrations/llm_provider_registry.py`、`packages/integrations/llm_provider_stream.py`
- 课后练习：说明为什么项目不直接在每个业务模块里分别调用不同厂商 SDK。

### 第 22 课：Session Runtime 与对话状态

- 学习目标：理解 Agent 会话如何持久化，消息、状态、快照、重试、回滚、问题确认是如何组织的。
- 必读代码入口：`packages/agent/session/session_runtime.py`、`packages/agent/session/session_store.py`、`packages/agent/session/session_lifecycle.py`
- 课后练习：画出一轮会话消息从创建到保存再到前端展示的大致生命周期。

### 第 23 课：Tool、Skill 与 MCP

- 学习目标：理解工具系统、技能系统和 MCP 接入是如何协同构成 Agent 能力边界的。
- 必读代码入口：`packages/agent/tools/tool_registry.py`、`packages/agent/tools/skill_registry.py`、`packages/agent/mcp/mcp_service.py`
- 课后练习：比较 Tool、Skill、MCP 三者的定位差异，并说明它们为什么不能混成一个概念。

### 第 24 课：本地与远程工作区执行

- 学习目标：理解工作区、终端、远程 SSH、服务器注册表这些模块为什么是科研 Agent 的关键基础设施。
- 必读代码入口：`packages/agent/workspace/workspace_executor.py`、`packages/agent/workspace/workspace_remote.py`、`packages/agent/workspace/workspace_server_registry.py`
- 课后练习：梳理本地工作区执行和远程工作区执行的异同，并说明各自适合的场景。

### 第 25 课：Project Workflow 与 ARIS 链路

- 学习目标：理解项目执行、实验工作流、多 Agent 协同、远程运行和成果整理这条更高层的自动化主线。
- 必读代码入口：`packages/ai/project/workflow_runner.py`、`packages/ai/project/execution_service.py`、`packages/ai/project/aris_smoke_service.py`
- 课后练习：概括项目工作流模块与普通聊天式 Agent 模块的区别，并写出它更像“执行系统”的原因。

---

## 模块六：前端、桌面端、测试与演进

### 第 26 课：前端入口与路由系统

- 学习目标：理解前端主入口、认证前置、路由懒加载、页面壳层和错误边界的组织方式。
- 必读代码入口：`frontend/src/App.tsx`、`frontend/src/main.tsx`、`frontend/src/components/Layout.tsx`
- 课后练习：画出前端页面路由图，并解释为什么 `Agent` 页面被放在主入口位置。

### 第 27 课：Agent 页面怎么读

- 学习目标：学会阅读复杂前端页面，理解 `Agent` 页面的状态、Context、API 调用和 UI 组件如何拆分。
- 必读代码入口：`frontend/src/pages/Agent.tsx`、`frontend/src/contexts/AssistantInstanceContext.tsx`、`frontend/src/features/assistantInstance/`
- 课后练习：整理 `Agent` 页面涉及的主要状态来源，并区分哪些是本地 UI 状态，哪些来自后端会话状态。

### 第 28 课：其余业务页面与前端服务层

- 学习目标：通过 Papers、Graph、Wiki、Projects、Settings 等页面，看懂页面层、通用组件和 API 服务层的复用方式。
- 必读代码入口：`frontend/src/services/api.ts`、`frontend/src/pages/Papers.tsx`、`frontend/src/pages/Projects.tsx`
- 课后练习：任选两个页面，对比它们的数据请求、加载状态和组件复用方式。

### 第 29 课：桌面化链路与打包方式

- 学习目标：理解当前仓库保留的桌面化与打包链路，以及它和当前 Web 优先实现之间的关系。
- 必读代码入口：`apps/desktop/server.py`、`researchos-server.spec`、`frontend/src/lib/tauri.ts`
- 课后练习：写出当前可见的桌面化链路，并说明为什么课程内容要优先以真实仓库状态为准。

### 第 30 课：测试体系与后续深入路线

- 学习目标：理解测试在这个仓库中的价值，学会利用测试文件反向理解模块，并建立后续深挖路线图。
- 必读代码入口：`tests/`、`tests/test_app_startup.py`、`tests/test_agent_session_runtime.py`、`tests/test_project_workflow_runner.py`
- 课后练习：为自己制定一个“下一阶段 14 天深入学习计划”，要求包含阅读模块、运行验证和至少 1 个小型改动目标。

---

## 6. 建议的阶段性成果

学完前 10 课后，你应该能够：

- 说清楚项目的产品目标与核心架构
- 独立启动本地环境
- 找到主要后端入口和前端入口

学完前 20 课后，你应该能够：

- 读懂论文处理与研究服务的主链路
- 看懂数据库、任务调度、LLM 调用的基本组织方式
- 对“系统为什么这么设计”形成初步判断

学完 30 课后，你应该能够：

- 按功能追踪从前端到后端再到数据层的完整调用链
- 读懂 Agent/session/tool/workspace 的主要模块职责
- 为后续改代码、写测试、做功能演进建立可靠基础

## 7. 后续扩展建议

这份总纲适合作为第一阶段的总地图。后续可以继续扩展为：

- 30 份单课讲义
- 每课配套的代码导读清单
- 每课配套的练习答案或参考提示
- 一份 14 天或 30 天的学习执行计划

如果后面继续展开，建议优先扩写以下课时：

1. 第 6 课：FastAPI 应用入口
2. 第 15 课：论文处理流水线
3. 第 21 课：统一 LLM Client
4. 第 22 课：Session Runtime 与对话状态
5. 第 27 课：Agent 页面怎么读

## 8. 配套图谱

这套课程已经配套补齐图谱索引，位置在 [diagrams/README.md](D:/Desktop/ResearchOS/class/diagrams/README.md)。图谱同样严格基于当前代码与测试，包括：

- 认证收紧后的登录与资源访问链路
- Alembic 驱动的存储 bootstrap
- 工作区默认权限从全自动改为白名单加按需确认
- 论文上传与替换 PDF 的服务化链路
- 前端 `AssistantInstance` 与 `Agent` 页最新会话绑定和 smoke 覆盖
