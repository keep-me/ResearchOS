# ResearchOS 课程索引

这套课程是对 [ResearchOS-30课课程总纲.md](D:/Desktop/ResearchOS/class/ResearchOS-30课课程总纲.md) 的细化版本。目标不是把仓库“介绍一遍”，而是带着学习者按真实工程链路逐步建立系统认知。课程和图谱都只以当前仓库里的真实代码、配置、迁移、测试和运行脚本为准。

建议使用顺序：

1. 先读总纲，了解 30 课的整体地图。
2. 再按本目录中的课时文件顺序学习。
3. 每完成 5 课，回到总纲做一次阶段复盘。
4. 遇到调用链或模块边界不清时，直接对照 [diagrams/README.md](D:/Desktop/ResearchOS/class/diagrams/README.md) 里的图谱导航。

每个课时文件统一包含这些部分：

- 本课定位
- 学完你应该能回答的问题
- 学习前准备
- 详细讲解
- 参考项目对照
- 代码精读顺序
- 动手任务
- 验收标准
- 常见误区

---

## 模块一：认识项目与基础环境

- [第 01 课：ResearchOS 是什么](D:/Desktop/ResearchOS/class/lesson-01-overview.md)
- [第 02 课：仓库结构入门](D:/Desktop/ResearchOS/class/lesson-02-repo-layout.md)
- [第 03 课：技术栈总览](D:/Desktop/ResearchOS/class/lesson-03-tech-stack.md)
- [第 04 课：本地启动全流程](D:/Desktop/ResearchOS/class/lesson-04-local-startup.md)
- [第 05 课：第一条前后端调用链](D:/Desktop/ResearchOS/class/lesson-05-first-end-to-end-trace.md)

## 模块二：后端主干与 Web 服务基础

- [第 06 课：FastAPI 应用入口](D:/Desktop/ResearchOS/class/lesson-06-fastapi-entrypoint.md)
- [第 07 课：中间件与横切能力](D:/Desktop/ResearchOS/class/lesson-07-middleware-and-cross-cutting.md)
- [第 08 课：Router 分层方式](D:/Desktop/ResearchOS/class/lesson-08-router-layering.md)
- [第 09 课：配置系统与环境变量](D:/Desktop/ResearchOS/class/lesson-09-configuration-system.md)
- [第 10 课：认证与权限](D:/Desktop/ResearchOS/class/lesson-10-auth-and-permissions.md)

## 模块三：数据层与论文主业务

- [第 11 课：存储启动链路](D:/Desktop/ResearchOS/class/lesson-11-storage-bootstrap.md)
- [第 12 课：SQLAlchemy 与 Repository](D:/Desktop/ResearchOS/class/lesson-12-sqlalchemy-and-repositories.md)
- [第 13 课：Topic 与 Paper 核心对象](D:/Desktop/ResearchOS/class/lesson-13-topic-and-paper-models.md)
- [第 14 课：论文输入链路](D:/Desktop/ResearchOS/class/lesson-14-paper-ingest-pipeline.md)
- [第 15 课：论文处理流水线](D:/Desktop/ResearchOS/class/lesson-15-paper-processing-pipeline.md)

## 模块四：研究能力与自动化管线

- [第 16 课：RAG 与证据链](D:/Desktop/ResearchOS/class/lesson-16-rag-and-evidence.md)
- [第 17 课：图谱与领域洞察](D:/Desktop/ResearchOS/class/lesson-17-graph-and-insights.md)
- [第 18 课：Wiki、日报与写作助手](D:/Desktop/ResearchOS/class/lesson-18-wiki-brief-writing.md)
- [第 19 课：成本、性能与稳定性](D:/Desktop/ResearchOS/class/lesson-19-cost-performance-reliability.md)
- [第 20 课：Worker 与自动调度](D:/Desktop/ResearchOS/class/lesson-20-worker-and-scheduling.md)

## 模块五：Agent、工作区与项目执行

- [第 21 课：统一 LLM Client](D:/Desktop/ResearchOS/class/lesson-21-llm-client-unification.md)
- [第 22 课：Session Runtime 与对话状态](D:/Desktop/ResearchOS/class/lesson-22-session-runtime.md)
- [第 23 课：Tool、Skill 与 MCP](D:/Desktop/ResearchOS/class/lesson-23-tools-skills-mcp.md)
- [第 24 课：本地与远程工作区执行](D:/Desktop/ResearchOS/class/lesson-24-local-and-remote-workspace.md)
- [第 25 课：Project Workflow 与 ARIS](D:/Desktop/ResearchOS/class/lesson-25-project-workflows-and-aris.md)

## 模块六：前端、桌面端、测试与演进

- [第 26 课：前端入口与路由系统](D:/Desktop/ResearchOS/class/lesson-26-frontend-entry-and-routing.md)
- [第 27 课：如何阅读 Agent 页面](D:/Desktop/ResearchOS/class/lesson-27-reading-agent-page.md)
- [第 28 课：其他业务页面与 API 服务层](D:/Desktop/ResearchOS/class/lesson-28-other-pages-and-api-layer.md)
- [第 29 课：桌面化链路与打包方式](D:/Desktop/ResearchOS/class/lesson-29-desktop-and-packaging.md)
- [第 30 课：测试体系与后续深入路线](D:/Desktop/ResearchOS/class/lesson-30-testing-and-roadmap.md)

---

## 参考代码如何使用

这套课优先基于当前仓库真实代码来写。当前仓库里能直接对照阅读的参考代码主要是：

- `reference/claw-code-main`

学习时不要把参考代码当成“标准答案”，而是把它当成一个对照视角：

- 当前 `ResearchOS` 的目录边界和入口是怎么组织的。
- `reference/claw-code-main` 在桌面工作台、本地资产和界面壳层上是怎么组织的。
- 哪些差异来自产品定位不同，哪些差异来自架构抽象层次不同。

---

## 图谱导航

- [图谱总索引](D:/Desktop/ResearchOS/class/diagrams/README.md)
- 图谱共 28 张，覆盖学习路线、架构、启动链路、认证、存储 bootstrap、论文流水线、Agent runtime、Project Workflow、前端路由、测试地图和读码导航。
- 图谱内容已经吸收 `锐评 -> 验收通过` 之间的真实代码修订，包括认证收紧、Alembic bootstrap、工作区默认权限收紧、论文上传服务化和前端 smoke 回归保护。
