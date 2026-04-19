# 02 知识依赖图

## 覆盖模块

- `apps/api/main.py`
- `packages/config.py`
- `packages/storage/bootstrap.py`
- `packages/storage/models.py`
- `packages/storage/paper_repository.py`
- `packages/ai/paper/pipelines.py`
- `packages/ai/research/rag_service.py`
- `packages/agent/session/session_runtime.py`
- `packages/agent/workspace/workspace_executor.py`
- `packages/ai/project/workflow_runner.py`
- `frontend/src/App.tsx`
- `frontend/src/pages/Agent.tsx`

## 图

```mermaid
flowchart TD
  A[仓库结构] --> B[本地启动]
  B --> C[FastAPI 入口]
  B --> D[前端入口]
  C --> E[配置系统]
  C --> F[认证与中间件]
  C --> G[存储 bootstrap]
  G --> H[ORM 模型]
  H --> I[Repository]
  I --> J[论文输入链路]
  J --> K[论文处理流水线]
  K --> L[RAG 证据链]
  K --> M[图谱与洞察]
  K --> N[Wiki Brief Writing]
  G --> O[Worker 调度]
  E --> O
  H --> P[Session Runtime]
  E --> P
  P --> Q[Workspace 权限执行]
  Q --> R[Project Workflow / ARIS]
  D --> S[App 路由与 Layout]
  S --> T[Agent 页面]
  T --> P
  T --> Q
  T --> R
  R --> U[测试与回归保护]
  P --> U
  F --> U
  G --> U
```

## 阅读提示

- 这张图回答的是“哪些知识必须先学，哪些知识是后续叠加出来的”。
- `配置 -> 存储 -> session/workspace` 是整个仓库最强的一条基础依赖链。
