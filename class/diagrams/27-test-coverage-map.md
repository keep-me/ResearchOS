# 27 测试覆盖地图

## 覆盖模块

- `tests/test_app_startup.py`
- `tests/test_auth_security.py`
- `tests/test_storage_bootstrap.py`
- `tests/test_runtime_safety_regressions.py`
- `tests/test_agent_session_runtime.py`
- `tests/test_project_workflow_runner.py`
- `frontend/tests/smoke.spec.ts`

## 图

```mermaid
flowchart TD
  Startup[test_app_startup.py] --> Main[apps/api/main.py startup]
  Auth[test_auth_security.py] --> AuthCode[packages/auth.py + auth router]
  Storage[test_storage_bootstrap.py] --> StorageCode[storage bootstrap + db + migrations]
  RuntimeReg[test_runtime_safety_regressions.py] --> Safety[TTL cache + exec policy + timezone + semantic retrieval]
  Session[test_agent_session_runtime.py] --> SessionCode[session runtime + agent service + persistence]
  Workflow[test_project_workflow_runner.py] --> WorkflowCode[workflow_runner + project models + remote execution]
  Smoke[frontend/tests/smoke.spec.ts] --> UIFlows[Agent / Projects / Settings / ACP / workspace switching]

  Main --> Architecture[后端入口稳定性]
  AuthCode --> Security[认证安全]
  StorageCode --> Migration[迁移与 bootstrap]
  Safety --> Guardrails[运行时护栏]
  SessionCode --> AgentCore[Agent 核心运行时]
  WorkflowCode --> Execution[ARIS 执行系统]
  UIFlows --> ProductSurface[端到端产品链路]
```

## 阅读提示

- 这张图适合在“我想知道作者到底在保护什么”时使用。
- 最近这轮提交最值得优先读的是 `Auth`、`Storage`、`RuntimeReg`、`Smoke` 四组测试。
