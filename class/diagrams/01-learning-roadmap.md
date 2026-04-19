# 01 学习路线总图

## 覆盖模块

- `class/lesson-01-overview.md` 到 `class/lesson-30-testing-and-roadmap.md`
- `class/ResearchOS-30课课程总纲.md`

## 图

```mermaid
flowchart LR
  A[模块一\n认识项目与基础环境\n01-05] --> B[模块二\n后端主干与 Web 服务基础\n06-10]
  B --> C[模块三\n数据层与论文主业务\n11-15]
  C --> D[模块四\n研究能力与自动化管线\n16-20]
  D --> E[模块五\nAgent 工作区与项目执行\n21-25]
  E --> F[模块六\n前端 桌面端 测试与演进\n26-30]

  A --> A1[认识仓库\n启动本地环境\n打通首条调用链]
  B --> B1[读懂 main.py\n配置系统\n认证与中间件]
  C --> C1[读懂存储层\n模型关系\n论文输入与处理]
  D --> D1[RAG 图谱\nWiki Brief Writing\nWorker 调度]
  E --> E1[LLM Client\nSession Runtime\nWorkspace\nARIS]
  F --> F1[前端路由\nAgent 页面\n桌面链路\n测试体系]

  F1 --> G[最终能力\n能按链路读代码\n能做小改动\n能补测试]
```

## 阅读提示

- 这张图回答的是“先学什么，后学什么”。
- 真正的依赖关系看下一张 `02`，这张更强调学习顺序和阶段目标。
