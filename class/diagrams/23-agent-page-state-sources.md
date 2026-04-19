# 23 Agent 页面状态来源图

## 覆盖模块

- `frontend/src/pages/Agent.tsx`
- `frontend/src/contexts/AssistantInstanceContext.tsx`
- `frontend/src/contexts/AgentWorkbenchContext.tsx`
- `frontend/src/features/assistantInstance/store.ts`
- `frontend/src/services/api.ts`

## 图

```mermaid
flowchart LR
  subgraph Context[上下文与共享状态]
    Workbench[AgentWorkbenchContext\npermissionPreset / backend / reasoning / skills]
    AssistantCtx[AssistantInstanceContext\nconversation / session / items / pendingActions]
    Store[assistantInstance store\nbootstrap / hydrate / route sync]
  end

  subgraph LocalUI[Agent.tsx 局部 UI 状态]
    Drawer[workspace panel / terminal drawer]
    Modal[workflow / MCP / server edit modal]
    Lists[paper picker / model list / server list]
    Input[input / slash command / scroll follow]
  end

  subgraph Backend[后端数据源]
    SessionApi[sessionApi]
    WorkspaceApi[assistantWorkspaceApi]
    ProjectApi[projectApi]
    MCPApi[mcpApi / acpApi]
    PaperApi[paperApi / topicApi / llmConfigApi]
  end

  Workbench --> AssistantCtx
  Store --> AssistantCtx
  AssistantCtx --> AgentPage[Agent.tsx]
  LocalUI --> AgentPage
  Backend --> AgentPage
  AgentPage --> WorkspaceApi
  AgentPage --> ProjectApi
  AgentPage --> MCPApi
  AgentPage --> SessionApi
  AgentPage --> PaperApi
```

## 阅读提示

- 读 `Agent.tsx` 时，先把状态分成上下文状态、页面局部状态、后端驱动状态三类。
- 最近变更最关键的是 store 会在没有 `workspacePath` 时停止自动 bootstrap。
