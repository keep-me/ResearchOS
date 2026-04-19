# 12 配置系统解析图

## 覆盖模块

- `packages/config.py`
- `.env.example`
- `apps/desktop/server.py`
- `packages/auth.py`
- `packages/storage/db.py`

## 图

```mermaid
flowchart LR
  Env[环境变量\nRESEARCHOS_ENV_FILE\nRESEARCHOS_DATA_DIR\nDATABASE_URL\nPDF_STORAGE_ROOT\nBRIEF_OUTPUT_ROOT\nAUTH_*] --> Config[packages/config.py]
  DotEnv[.env / .env.example] --> Config
  Desktop[apps/desktop/server.py\n桌面入口注入变量] --> Config

  Config --> Settings["Settings + get_settings"]
  Settings --> Paths[data dir / db url / pdf root / brief root]
  Settings --> AuthCfg[auth_password\nauth_password_hash\nauth_secret_key]
  Settings --> LLMCfg[provider / model / embedding / image]
  Settings --> WorkerCfg[daily_cron / weekly_cron / retry / cost_guard]
  Settings --> RuntimeCfg[user_timezone / agent_max_tool_steps / CORS]

  Paths --> DB[packages/storage/db.py]
  AuthCfg --> Auth[packages/auth.py]
  LLMCfg --> AI[packages/ai + integrations]
  WorkerCfg --> Worker[apps/worker/main.py]
  RuntimeCfg --> API[apps/api/main.py]
```

## 阅读提示

- 当前仓库里，`Settings` 类而不是 `.env.example` 才是配置真相的最终解释器。
- `get_settings()` 还会顺手准备目录，不只是纯读取。
