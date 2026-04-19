# 19 本地与远程工作区对照图

## 覆盖模块

- `packages/agent/workspace/workspace_executor.py`
- `packages/agent/workspace/workspace_remote.py`
- `packages/agent/workspace/workspace_server_registry.py`
- `apps/api/routers/agent_workspace.py`
- `frontend/tests/smoke.spec.ts`

## 图

```mermaid
flowchart LR
  subgraph Local[本地工作区]
    L1[workspace_executor.py]
    L2[allowed roots / default projects root]
    L3[read glob grep write run command]
    L4[assistant_exec_policy.json]
  end

  subgraph Remote[远程工作区]
    R1[workspace_remote.py]
    R2[SSH server registry]
    R3[remote read/write/git/terminal]
    R4[screen session / GPU probe / remote env]
  end

  API[agent_workspace router] --> Local
  API --> Remote
  Local --> Policy[permission_next + exec policy]
  Remote --> Policy
  Policy --> UI[Agent.tsx / Projects.tsx]
  UI --> Smoke[smoke.spec.ts\nlocal switch + ACP remote]
```

## 阅读提示

- 本地和远程不是两套完全独立产品面，而是共享同一组前端入口与权限框架。
- 真正的差异主要在传输层、环境准备和远程设备管理。
