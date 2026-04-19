# 09 Agent Runtime 关系图

## 覆盖模块

- `frontend/src/contexts/AssistantInstanceContext.tsx`
- `frontend/src/features/assistantInstance/store.ts`
- `frontend/src/pages/Agent.tsx`
- `apps/api/routers/session_runtime.py`
- `apps/api/routers/agent.py`
- `apps/api/routers/opencode.py`
- `packages/agent/runtime/agent_service.py`
- `packages/agent/runtime/permission_next.py`
- `packages/agent/session/session_runtime.py`
- `packages/agent/workspace/workspace_executor.py`

## 图

```mermaid
flowchart LR
  subgraph Front[前端 Agent 工作台]
    AgentPage[Agent.tsx]
    AICtx[AssistantInstanceContext]
    Store[assistantInstance store]
  end

  subgraph Api[后端会话接口]
    SessionRouter[session_runtime router]
    AgentRouter[agent / opencode router]
    GlobalRouter[global_routes router]
  end

  subgraph Runtime[Agent runtime]
    AgentService[agent_service.py]
    SessionRT[session_runtime.py]
    Permission[permission_next.py]
    Tools[tool registry / skill / MCP]
    Workspace[workspace_executor.py\nworkspace_remote.py]
  end

  subgraph Persist[持久化]
    AgentModels[AgentSession / Message / Part / Todo]
    Pending[AgentPendingAction]
    Rules[AgentPermissionRuleSet]
  end

  AgentPage --> AICtx --> Store
  Store --> SessionRouter
  Store --> AgentRouter
  Store --> GlobalRouter
  SessionRouter --> SessionRT
  AgentRouter --> AgentService
  AgentService --> SessionRT
  AgentService --> Permission
  AgentService --> Tools
  AgentService --> Workspace
  SessionRT --> AgentModels
  Permission --> Pending
  Permission --> Rules
  Workspace --> Permission
  Store -. SSE / ws .-> GlobalRouter
```

## 阅读提示

- 这张图回答的是“Agent 页面背后到底连着哪些后端运行时模块”。
- 如果你在读会话、权限、工作区三者边界，这张图最有用。
