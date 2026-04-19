# ResearchOS 图谱索引

这套图谱严格基于当前仓库真实代码、配置、迁移、测试与脚本，不把仓库中的历史说明文档当作事实来源。

## 总览与启动

- [01 学习路线总图](./01-learning-roadmap.md)
- [02 知识依赖图](./02-knowledge-dependency-map.md)
- [03 项目总架构图](./03-system-architecture.md)
- [04 仓库结构图](./04-repo-structure.md)
- [05 本地启动时序图](./05-local-startup-sequence.md)
- [06 第一条前后端调用链时序图](./06-first-fullstack-call-chain.md)
- [07 核心数据模型关系图](./07-core-data-models.md)

## 后端主干与研究主线

- [08 论文处理流水线图](./08-paper-processing-pipeline.md)
- [09 Agent Runtime 关系图](./09-agent-runtime-map.md)
- [10 Project Workflow 与 ARIS 编排图](./10-project-workflow-aris.md)
- [11 FastAPI 入口装配图](./11-fastapi-composition.md)
- [12 配置系统解析图](./12-configuration-resolution.md)
- [13 认证流程图](./13-auth-flow.md)
- [14 存储 Bootstrap 图](./14-storage-bootstrap.md)
- [15 Worker 调度时间线](./15-worker-scheduling-timeline.md)
- [16 RAG 证据链图](./16-rag-evidence-chain.md)
- [17 图谱洞察图](./17-graph-insight-map.md)
- [18 Wiki / Brief / Writing 产出链图](./18-wiki-brief-writing-chain.md)

## Agent、工作区与前端

- [19 本地与远程工作区对照图](./19-local-vs-remote-workspace.md)
- [20 Session Runtime 状态机](./20-session-runtime-state-machine.md)
- [21 App.tsx 路由图](./21-app-routing-map.md)
- [22 Layout Provider 树](./22-layout-provider-tree.md)
- [23 Agent 页面状态来源图](./23-agent-page-state-sources.md)
- [24 页面到 API 服务依赖图](./24-page-api-dependency-map.md)

## 学习与测试

- [25 30 课技能树](./25-skill-tree.md)
- [26 14 天学习计划甘特图](./26-14-day-study-gantt.md)
- [27 测试覆盖地图](./27-test-coverage-map.md)
- [28 如何读代码导航图](./28-code-reading-navigation.md)

## 使用建议

- 先看 `01`、`03`、`05` 建立整体心智。
- 碰到具体问题时，按领域跳到对应图，例如认证看 `13`，工作区看 `19`，Project Workflow 看 `10`。
- 配合 `class/` 课时文件一起读，图负责建立结构，课时负责解释代码阅读顺序与练习。
