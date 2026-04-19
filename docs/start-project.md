# ResearchOS 启动说明

本文档按当前仓库 `D:\Desktop\ResearchOS` 在 `2026-04-17` 的实际状态整理。启动命令统一按 Windows `cmd.exe` 写法给出，建议本地开发时分别打开后端和前端两个 `cmd` 窗口。

## 1. 启动结论

| 链路 | 状态 | 说明 |
| --- | --- | --- |
| 本地数据库初始化 | 已验证 | `scripts\local_bootstrap.py` 可初始化本地 SQLite 数据库 |
| 本地后端 | 已验证 | `http://127.0.0.1:8000/health` 可访问 |
| 本地前端 | 已验证 | Vite 前端可通过 `http://127.0.0.1:5173` 访问 |
| Docker 直接启动 | 已验证 | `docker compose up -d` 可使用本机已有镜像启动 |
| Docker 重建启动 | 受网络影响 | `docker compose up -d --build` 可能受 Docker Hub 基础镜像拉取网络影响 |

## 2. 端口与访问地址

### 本地开发

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

### Docker

- 前端：`http://127.0.0.1:3002`
- 后端：`http://127.0.0.1:8002`
- API 文档：`http://127.0.0.1:8002/docs`
- 健康检查：`http://127.0.0.1:8002/health`

## 3. 前置要求

- Windows
- Python 3.11+
- Node.js 20+
- Docker Desktop，仅 Docker 启动需要
- 根目录存在 `.env`
- 根目录存在 `.venv`
- `frontend\node_modules` 已安装

在 `cmd.exe` 中检查：

```cmd
cd /d D:\Desktop\ResearchOS
if exist .env (echo OK .env) else (echo MISSING .env)
if exist .venv\Scripts\python.exe (echo OK .venv) else (echo MISSING .venv)
if exist frontend\node_modules (echo OK frontend\node_modules) else (echo MISSING frontend\node_modules)
```

如果还没有 `.env`：

```cmd
cd /d D:\Desktop\ResearchOS
if not exist .env copy .env.example .env
```

至少确认 `.env` 中按需配置：

- `DATABASE_URL`
- `PDF_STORAGE_ROOT`
- `BRIEF_OUTPUT_ROOT`
- 一个可用的 LLM Key，例如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 或 `ZHIPU_API_KEY`
- 如果启用站点登录：`AUTH_PASSWORD`、`AUTH_SECRET_KEY`

## 4. 本地启动

### 4.1 初始化数据库

在一个 `cmd.exe` 窗口中执行：

```cmd
cd /d D:\Desktop\ResearchOS
.venv\Scripts\python.exe scripts\local_bootstrap.py
```

成功后会看到类似输出：

- `当前数据库: sqlite:///./data/researchos.db`
- `创建了 ... 个表`
- `[OK] 数据库初始化成功！`

### 4.2 启动后端

打开第一个 `cmd.exe` 窗口，执行：

```cmd
cd /d D:\Desktop\ResearchOS
.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

这个窗口保持打开，后端会在前台运行。

### 4.3 启动前端

打开第二个 `cmd.exe` 窗口，执行：

```cmd
cd /d D:\Desktop\ResearchOS\frontend
set VITE_PROXY_TARGET=http://127.0.0.1:8000
node node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173 --strictPort
```

这个窗口保持打开，前端会在前台运行。

如果当前窗口还在仓库根目录 `D:\Desktop\ResearchOS`，也可以不切目录，直接用下面这条根目录命令：

```cmd
cd /d D:\Desktop\ResearchOS
set VITE_PROXY_TARGET=http://127.0.0.1:8000
node frontend\node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173 --strictPort
```

如果你更习惯使用 npm，也可以在 `cmd.exe` 中这样启动：

```cmd
cd /d D:\Desktop\ResearchOS\frontend
set VITE_PROXY_TARGET=http://127.0.0.1:8000
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

### 4.4 一键打开两个本地启动窗口

如果希望从一个 `cmd.exe` 一次性拉起后端和前端两个窗口：

```cmd
cd /d D:\Desktop\ResearchOS
start "ResearchOS Backend" cmd /k "cd /d D:\Desktop\ResearchOS && .venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000"
start "ResearchOS Frontend" cmd /k "cd /d D:\Desktop\ResearchOS\frontend && set VITE_PROXY_TARGET=http://127.0.0.1:8000 && node node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173 --strictPort"
```

### 4.5 本地健康检查

后端和前端启动后，在新的 `cmd.exe` 中检查：

```cmd
curl http://127.0.0.1:8000/health
curl -I http://127.0.0.1:5173
```

预期结果：

- 后端返回 `200`，内容包含健康状态。
- 前端返回 `HTTP/1.1 200 OK` 或等价的 200 响应。

### 4.6 停止本地前后端

如果是前台窗口启动，直接在对应窗口按 `Ctrl+C`。

如果需要按端口强制停止：

```cmd
for /f "tokens=5" %p in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %p /F
for /f "tokens=5" %p in ('netstat -ano ^| findstr /R /C:":5173 .*LISTENING"') do taskkill /PID %p /F
```

注意：上面是直接在 `cmd.exe` 里执行的写法。如果放进 `.bat` 文件，需要把 `%p` 改成 `%%p`。

## 5. Docker 启动

### 5.1 确认 Docker daemon 已就绪

```cmd
docker info --format "{{.ServerVersion}}"
```

如果这里报错，先手动打开 Docker Desktop，等状态变成 Running 后再继续。

### 5.2 重建并启动

```cmd
cd /d D:\Desktop\ResearchOS
docker compose up -d --build
```

### 5.3 如果 Docker Hub 拉基础镜像失败

如果出现 `failed to resolve source metadata`、`registry-1.docker.io`、`connectex` 等网络错误，可以临时切换基础镜像源后重试：

```cmd
cd /d D:\Desktop\ResearchOS
set BASE_REGISTRY=docker.m.daocloud.io
docker compose up -d --build
set BASE_REGISTRY=
```

### 5.4 使用本机已有镜像直接启动

如果本机已经存在相关镜像，可以不重建：

```cmd
cd /d D:\Desktop\ResearchOS
docker compose up -d
```

### 5.5 查看 Docker 状态

```cmd
cd /d D:\Desktop\ResearchOS
docker compose ps
```

预期服务：

- `researchos-backend`
- `researchos-frontend`
- `researchos-worker`

### 5.6 Docker 健康检查

```cmd
curl http://127.0.0.1:8002/health
curl -I http://127.0.0.1:3002
```

### 5.7 查看 Docker 日志

```cmd
cd /d D:\Desktop\ResearchOS
docker compose logs backend --tail 50
docker compose logs worker --tail 50
docker compose logs frontend --tail 50
```

### 5.8 停止 Docker

```cmd
cd /d D:\Desktop\ResearchOS
docker compose down
```

## 6. 推荐启动顺序

1. 检查 `.env`、`.venv`、`frontend\node_modules`。
2. 执行 `scripts\local_bootstrap.py`。
3. 打开后端 `cmd` 窗口并启动 `uvicorn`。
4. 打开前端 `cmd` 窗口并启动 `Vite`。
5. 用 `curl` 做本地健康检查。
6. 如果需要容器部署，再执行 Docker Compose。

本地开发通常只需要完成第 1 到第 5 步。

## 7. 常见问题

### 7.1 前端能打开但 API 请求失败

确认前端启动窗口里设置了：

```cmd
set VITE_PROXY_TARGET=http://127.0.0.1:8000
```

然后重新启动前端。

### 7.2 端口被占用

检查端口：

```cmd
netstat -ano | findstr ":8000"
netstat -ano | findstr ":5173"
```

按 PID 停止：

```cmd
taskkill /PID <PID> /F
```

### 7.3 Docker daemon 没起来

先执行：

```cmd
docker info --format "{{.ServerVersion}}"
```

如果失败：

1. 打开 Docker Desktop。
2. 等待 Desktop 完全启动。
3. 再执行 `docker compose up -d` 或 `docker compose up -d --build`。

### 7.4 Compose 提示已有 volume 不是当前项目创建

如果你就是要复用旧卷，这个 warning 可以接受；它不会阻止当前容器启动。

## 8. 当前验证记录

### 本地

- `scripts\local_bootstrap.py`：已通过
- `http://127.0.0.1:8000/health`：已通过
- `http://127.0.0.1:5173`：已通过

### Docker

- `docker compose up -d --build`：可能受基础镜像拉取网络影响
- `docker compose up -d`：已成功启动
- `http://127.0.0.1:8002/health`：已通过
- `http://127.0.0.1:3002`：已通过
