# 11 FastAPI 入口装配图

## 覆盖模块

- `apps/api/main.py`
- `apps/api/routers/__init__.py`
- `packages/auth.py`
- `packages/storage/bootstrap.py`

## 图

```mermaid
flowchart TD
  Settings["get_settings"] --> App[FastAPI app]
  App --> MW1[RequestLogMiddleware]
  App --> MW2[AuthMiddleware]
  App --> MW3[GZipMiddleware]
  App --> MW4[CORS middleware]
  App --> Err[AppError exception handler]

  App --> Startup[startup event]
  Startup --> AuthCfg["validate_auth_configuration"]
  Startup --> Bootstrap["bootstrap_api_runtime"]
  Bootstrap --> Tracker["global_tracker.bootstrap_from_store"]

  App --> Routers["include_router(...)"]
  Routers --> R1[system / global_routes / auth]
  Routers --> R2[papers / topics / graph / writing]
  Routers --> R3[agent / session_runtime / agent_workspace / acp / mcp / opencode]
  Routers --> R4[projects / content / pipelines / jobs / settings]

  App --> Shutdown[shutdown event]
  Shutdown --> Cleanup["dispose_runtime_state"]
```

## 阅读提示

- 这张图回答的是“`main.py` 虽然短，但到底装配了哪些东西”。
- 近期变更最关键的是 startup 先校验认证配置，再 bootstrap runtime。
