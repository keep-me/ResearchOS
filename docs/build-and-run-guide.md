# ResearchOS 本地构建与 Docker 构建指南

本文给出两套完整流程：
- 本地构建运行（适合开发调试）
- Docker 构建运行（适合部署和稳定运行）

默认仓库路径示例：`D:\Desktop\ResearchOS`

---

## 1. 环境准备

### 1.1 必要软件

本地构建需要：
- Python 3.11+
- Node.js 20+
- PowerShell 7（`pwsh`）

Docker 构建需要：
- Docker Desktop（或 Linux Docker Engine）
- Docker Compose（`docker compose`）

### 1.2 获取代码

```powershell
pwsh -NoLogo -Command "git clone https://github.com/keep-me/ResearchOS.git D:\Desktop\ResearchOS"
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; git status"
```

---

## 2. 环境变量配置（两种方式共用）

先复制模板：

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; Copy-Item .env.example .env -Force"
```

编辑 `.env`（至少配置以下关键项）：

- `LLM_PROVIDER`：例如 `openai` / `anthropic` / `zhipu`
- 对应 Key（至少一个）：
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `ZHIPU_API_KEY`
- `DATABASE_URL`：
  - 默认即可：`sqlite:///./data/researchos.db`
- `SITE_URL`：
  - 本地开发：`http://127.0.0.1:5173`
  - Docker 部署：`http://127.0.0.1:3002`（或你的公网域名）
- `CORS_ALLOW_ORIGINS`：
  - 本地建议：`*` 或具体地址（如 `http://127.0.0.1:5173`）
- 如启用登录认证：
  - `AUTH_PASSWORD`（或 `AUTH_PASSWORD_HASH`）
  - `AUTH_SECRET_KEY`（生产务必替换为随机长字符串）

---

## 3. 本地构建运行（开发模式）

## 3.1 安装后端依赖

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; python -m venv .venv"
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; .\.venv\Scripts\python.exe -m pip install --upgrade pip"
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; .\.venv\Scripts\python.exe -m pip install -e '.[llm,pdf]'"
```

## 3.2 初始化数据库

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; .\.venv\Scripts\python.exe scripts\local_bootstrap.py"
```

## 3.3 启动后端（窗口 1）

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; .\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002"
```

## 3.4 启动前端（窗口 2）

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS\frontend'; npm install"
pwsh -NoLogo -Command "$env:VITE_PROXY_TARGET='http://127.0.0.1:8002'; Set-Location -LiteralPath 'D:\Desktop\ResearchOS\frontend'; npm run dev -- --host 127.0.0.1 --port 3002 --strictPort"
```

## 3.5 访问与验证

- 前端：`http://127.0.0.1:3002`
- 后端：`http://127.0.0.1:8002`
- 健康检查：`http://127.0.0.1:8002/health`
- API 文档：`http://127.0.0.1:8002/docs`

验证命令：

```powershell
pwsh -NoLogo -Command "(Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8002/health').StatusCode"
```

## 3.6 停止本地服务

- 在对应窗口 `Ctrl + C` 停止

---

## 4. Docker 构建运行（部署模式）

## 4.1 确认 Docker 可用

```powershell
pwsh -NoLogo -Command "docker --version; docker compose version; docker info | Out-Null; 'docker ready'"
```

## 4.2 构建并启动

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; docker compose up -d --build"
```

默认会启动三个服务：
- `researchos-backend`
- `researchos-worker`
- `researchos-frontend`

## 4.3 访问与验证

- 前端：`http://127.0.0.1:3002`
- 后端：`http://127.0.0.1:8002`
- 健康检查：`http://127.0.0.1:8002/health`
- API 文档：`http://127.0.0.1:8002/docs`

验证命令：

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; docker compose ps"
pwsh -NoLogo -Command "(Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8002/health').StatusCode"
pwsh -NoLogo -Command "(Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:3002/').StatusCode"
```

## 4.4 查看日志

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; docker compose logs -f backend"
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; docker compose logs -f worker"
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; docker compose logs -f frontend"
```

## 4.5 停止与清理

只停止：

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; docker compose down"
```

重建前清理旧缓存（可选）：

```powershell
pwsh -NoLogo -Command "docker builder prune -af"
pwsh -NoLogo -Command "docker image prune -af"
```

---

## 5. 常见问题

### 5.1 `docker compose up` 拉镜像慢/失败

可在 `.env` 设置镜像源后重试：

- `BASE_REGISTRY=docker.m.daocloud.io`
- `NPM_REGISTRY=https://registry.npmmirror.com`

然后：

```powershell
pwsh -NoLogo -Command "Set-Location -LiteralPath 'D:\Desktop\ResearchOS'; docker compose up -d --build"
```

### 5.2 前端打不开或请求失败

- 确认后端健康：`/health` 返回 200
- 本地开发确认 `VITE_PROXY_TARGET=http://127.0.0.1:8000`
- Docker 模式确认访问端口是 `3002/8002`

### 5.3 数据库初始化报错

- 本地模式先执行 `scripts/local_bootstrap.py`
- Docker 模式会在镜像构建时执行 bootstrap

### 5.4 开启认证后无法登录

检查 `.env`：
- `AUTH_PASSWORD`（或 `AUTH_PASSWORD_HASH`）
- `AUTH_SECRET_KEY` 不为空

---

## 6. 推荐用法

- 日常开发：用“本地构建运行”
- 交付/部署：用“Docker 构建运行”
- 服务器对外访问：再加 Nginx + HTTPS（80/443）
