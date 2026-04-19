# 20 Session Runtime 状态机

## 覆盖模块

- `packages/agent/session/session_runtime.py`
- `packages/agent/runtime/agent_service.py`
- `frontend/src/features/assistantInstance/store.ts`
- `packages/storage/models.py`

## 图

```mermaid
stateDiagram-v2
  [*] --> DraftConversation
  DraftConversation --> BoundConversation: 绑定 workspace / backend / mode
  BoundConversation --> SessionEnsured: ensureSessionForConversation
  BoundConversation --> DraftConversation: 无 workspacePath 时停止 bootstrap
  SessionEnsured --> Hydrated: hydrateSession
  Hydrated --> Idle
  Idle --> PromptQueued: sendMessage
  PromptQueued --> Streaming
  Streaming --> WaitingPermission: 出现 permission / question action
  WaitingPermission --> Streaming: confirm / answer / continue
  Streaming --> Idle: done
  Streaming --> Aborted: abort / external abort
  Streaming --> Error: session.error
  Idle --> Forked: revert / fork / new conversation
  Aborted --> Idle
  Error --> Idle
  Idle --> Disposed: global.disposed / server instance disposed
  Disposed --> [*]
```

## 阅读提示

- 前端 store 和后端 runtime 一起决定了这个状态机，不是单边逻辑。
- 最近的变更点是“没有 workspacePath 就不自动推进到 SessionEnsured”。
