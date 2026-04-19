# 03 项目总架构图

## 覆盖模块

- `frontend/src/App.tsx`
- `frontend/src/components/Layout.tsx`
- `apps/api/main.py`
- `apps/api/routers/`
- `packages/agent/`
- `packages/ai/`
- `packages/storage/`
- `packages/integrations/`
- `apps/worker/main.py`
- `apps/desktop/server.py`

## 图

```mermaid
flowchart LR
  subgraph FE[前端 React 工作台]
    App[App.tsx]
    Layout[Layout.tsx]
    Pages[Agent / Papers / Projects / Graph / Wiki / Brief / Writing / Settings]
    Ctx[Contexts + assistantInstance store]
  end

  subgraph API[FastAPI API]
    Main[apps/api/main.py]
    Routers[routers/auth papers projects session_runtime agent_workspace graph topics writing settings jobs]
    MW[RequestLog + Auth + GZip + CORS + AppError]
  end

  subgraph Domain[核心能力层]
    Agent[packages/agent\nsession runtime / tools / permissions / workspace]
    AI[packages/ai\npaper / research / project / ops]
    Storage[packages/storage\nbootstrap / db / models / repositories]
    Config[packages/config.py + packages/auth.py]
    Integrations[packages/integrations\nLLM / arXiv / OpenAlex / Semantic Scholar / citation]
  end

  subgraph Runtime[独立运行时]
    Worker[apps/worker/main.py]
    Desktop[apps/desktop/server.py]
  end

  subgraph Data[状态与资产]
    DB[(SQLite + Alembic)]
    Files[data/papers\nbriefs\nworkspace_roots\nassistant_exec_policy]
    Projects[projects/\nworkspace artifacts]
  end

  External[外部服务\nLLM / 学术数据源 / SSH 远程机]

  FE --> API
  API --> Domain
  Agent --> Storage
  AI --> Storage
  Config --> API
  Config --> Domain
  Integrations --> External
  AI --> Integrations
  Agent --> External
  Storage --> DB
  Storage --> Files
  Agent --> Projects
  AI --> Projects
  Worker --> Domain
  Desktop --> API
  Desktop --> Worker
```

## 阅读提示

- 这是“全局静态架构图”，回答系统由哪些层组成。
- 真正的动态行为请继续看 `05`、`06`、`08`、`09`、`10`。
