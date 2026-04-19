# 05 本地启动时序图

## 覆盖模块

- `scripts/local_bootstrap.py`
- `packages/storage/bootstrap.py`
- `scripts/start_api_dev.ps1`
- `apps/api/main.py`
- `scripts/start_frontend_dev.ps1`
- `frontend/package.json`

## 图

```mermaid
sequenceDiagram
  actor Dev as 开发者
  participant Bootstrap as scripts/local_bootstrap.py
  participant Storage as bootstrap_local_runtime
  participant Alembic as Alembic command.upgrade
  participant DB as SQLite
  participant APIBoot as apps/api/main.py startup
  participant Tracker as global_tracker
  participant Vite as frontend dev server
  participant Browser as 浏览器

  Dev->>Bootstrap: 运行 bootstrap
  Bootstrap->>Storage: bootstrap_local_runtime()
  Storage->>DB: inspect tables + read revision
  alt 旧库无 alembic_version
    Storage->>Alembic: stamp 20260412_0011
  end
  Storage->>Alembic: upgrade head
  Alembic->>DB: 迁移到 20260414_0012
  Storage->>DB: ensure initial_import action

  Dev->>APIBoot: 运行 uvicorn 127.0.0.1:8010
  APIBoot->>APIBoot: validate_auth_configuration()
  APIBoot->>Storage: bootstrap_api_runtime()
  Storage->>Tracker: bootstrap_from_store()

  Dev->>Vite: 运行 vite 127.0.0.1:4317
  Vite->>APIBoot: 通过 VITE_PROXY_TARGET 代理 API
  Browser->>Vite: 打开前端页面
  Browser->>APIBoot: 请求 /auth/status /health 等
```

## 阅读提示

- 这里最重要的认知是“显式 bootstrap”和“API startup bootstrap”是两层防线。
- 端口来自脚本，不要靠记忆猜。
