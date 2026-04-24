# ResearchOS

ResearchOS 是一个面向个人研究者和小团队的 AI 研究工作台，核心目标是把“论文采集、阅读分析、主题跟踪、图表提取、知识沉淀、写作辅助”放到同一套系统里。

当前仓库已经支持两种主要运行方式：
- 本地开发：`uvicorn + vite`
- 服务器部署：`systemd + nginx`

当前论文 OCR / 图表补充链路已切换为 `MinerU API`，不再依赖本地 MinerU 模型。

## 功能概览

- 论文收集：支持主题订阅、手动导入、arXiv 链接导入
- 论文管理：论文库、状态流转、元数据维护、PDF 存储
- AI 阅读：粗读、精读、多轮分析、结构化证据抽取
- 图表处理：`arxiv_source` 优先，必要时回退到 MinerU API OCR 结构化结果
- 主题与图谱：主题详情、引用图谱、每日/每周自动维护
- 写作辅助：Brief、Wiki、问答、图像生成接口
- 自动化：worker 定时任务、闲时处理、日报/周维护

## 技术栈

- Backend: Python 3.11, FastAPI, SQLAlchemy, Alembic
- Frontend: React 18, TypeScript, Vite
- Database: SQLite 默认，可通过 `DATABASE_URL` 切换
- Reverse Proxy: nginx
- Process Manager: systemd

## 目录结构

- `apps/api`: FastAPI 入口与路由
- `apps/worker`: 后台调度与自动化任务
- `packages`: 核心业务、LLM 集成、论文处理、存储层
- `frontend`: React 前端
- `infra`: Alembic migration 等基础设施文件
- `docs`: 项目文档
- `.env.example`: 后端主配置示例
- `frontend/.env.example`: 前端开发环境变量示例

## 环境要求

- Python `3.11`
- Node.js `20+`
- npm
- Linux 服务器部署时建议具备：
  - `systemd`
  - `nginx`
  - 已放行公网 `80` 端口

## 配置文件

后端配置来自根目录 [`.env.example`](/home/ResearchOS/.env.example)。

初始化方式：

```bash
cp .env.example .env
```

前端开发环境如需单独配置，可参考 [frontend/.env.example](/home/ResearchOS/frontend/.env.example)。

## 必填配置

至少要配置一组可用的大模型 Key：

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `ZHIPU_API_KEY`
- `GEMINI_API_KEY`

通常你还需要确认这些字段：

- `SITE_URL`
  - 本地开发可写 `http://127.0.0.1:5173`
  - 服务器公网部署可写 `http://<你的公网IP>` 或正式域名
- `CORS_ALLOW_ORIGINS`
  - 本地开发示例：`http://127.0.0.1:5173,http://localhost:5173`
  - nginx 公网部署可直接填公网地址，或在同域反代场景下保持较窄白名单
- `AUTH_SECRET_KEY`
  - 生产环境必须改成随机字符串

## MinerU API 配置

当前仓库使用 MinerU 官方 API，而不是本地模型。

相关变量：

- `FIGURE_EXTRACT_MODE=arxiv_source`
- `MINERU_BACKEND=api`
- `MINERU_API_BASE_URL=https://mineru.net`
- `MINERU_API_TOKEN=<你的 token>`
- `MINERU_API_MODEL_VERSION=vlm`
- `MINERU_API_POLL_INTERVAL_SECONDS=3`
- `MINERU_API_TIMEOUT_SECONDS=300`
- `MINERU_API_UPLOAD_TIMEOUT_SECONDS=600`

说明：

- `arxiv_source` 是默认图表提取模式
- 对 arXiv 论文，系统优先使用 arXiv 源文件中的图表资源
- 当需要 OCR / 结构化补充时，系统再调用 MinerU API
- 不再需要本地下载 MinerU 模型，也不再需要 `MINERU_DEVICE_MODE`

## 常用环境变量说明

常用项如下，完整列表看 [`.env.example`](/home/ResearchOS/.env.example)：

- `APP_ENV`: `dev` / `production`
- `API_HOST`: 后端监听地址
- `API_PORT`: 后端监听端口
- `SITE_URL`: 前端站点地址
- `DATABASE_URL`: 数据库连接串
- `PDF_STORAGE_ROOT`: PDF 存储目录
- `BRIEF_OUTPUT_ROOT`: Brief 输出目录
- `CORS_ALLOW_ORIGINS`: CORS 白名单
- `LLM_PROVIDER`: 默认模型提供方
- `LLM_MODEL_SKIM`
- `LLM_MODEL_DEEP`
- `LLM_MODEL_VISION`
- `AUTH_PASSWORD`: 站点密码，留空则不启用登录保护
- `AUTH_PASSWORD_HASH`: 推荐生产环境使用 bcrypt 哈希
- `AUTH_SECRET_KEY`: JWT 签名密钥
- `DAILY_CRON`
- `DASHBOARD_TREND_CRON`
- `WEEKLY_CRON`
- `IDLE_PROCESSOR_ENABLED`

## 本地开发启动

### 1. 安装后端依赖

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

### 2. 安装前端依赖

```bash
cd frontend
npm install
cd ..
```

### 3. 写 `.env`

```bash
cp .env.example .env
```

本地开发推荐至少修改：

```env
APP_ENV=dev
SITE_URL=http://127.0.0.1:5173
CORS_ALLOW_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
LLM_PROVIDER=zhipu
ZHIPU_API_KEY=你的key
MINERU_API_TOKEN=你的token
```

### 4. 启动后端

```bash
cd /path/to/ResearchOS
./.venv/bin/python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

### 5. 启动前端

```bash
cd /path/to/ResearchOS/frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

### 6. 本地访问地址

- 前端: `http://127.0.0.1:5173`
- 后端健康检查: `http://127.0.0.1:8000/health`
- API 文档: `http://127.0.0.1:8000/docs`

## 服务器部署：systemd + nginx + 公网 IP

这是当前仓库在服务器上的推荐运行方式。

部署思路：

- 后端只监听 `127.0.0.1:8000`
- 前端只监听 `127.0.0.1:3002`
- nginx 监听公网 `80`
- nginx `/` 转发到前端
- nginx `/api/` 转发到后端

### 1. 准备 `.env`

生产环境建议至少这样配置：

```env
APP_ENV=production
API_HOST=127.0.0.1
API_PORT=8000
SITE_URL=http://47.93.160.229
CORS_ALLOW_ORIGINS=http://47.93.160.229

LLM_PROVIDER=zhipu
ZHIPU_API_KEY=你的key

FIGURE_EXTRACT_MODE=arxiv_source
MINERU_BACKEND=api
MINERU_API_BASE_URL=https://mineru.net
MINERU_API_TOKEN=你的token

AUTH_SECRET_KEY=请改成随机字符串
DASHBOARD_TREND_CRON=0 16 * * *
```

如果要开放匿名访问，保持：

```env
AUTH_PASSWORD=
AUTH_PASSWORD_HASH=
```

### 2. 安装 systemd 服务

后端服务示例：

```ini
[Unit]
Description=ResearchOS Backend API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/ResearchOS
EnvironmentFile=/home/ResearchOS/.env
ExecStart=/home/ResearchOS/.venv/bin/python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

前端服务示例：

```ini
[Unit]
Description=ResearchOS Frontend Dev Server
After=network.target researchos-backend.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/ResearchOS/frontend
Environment=VITE_PROXY_TARGET=http://127.0.0.1:8000
ExecStart=/root/.nvm/versions/node/v24.15.0/bin/node /home/ResearchOS/frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3002 --strictPort
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

生效命令：

```bash
systemctl daemon-reload
systemctl enable --now researchos-backend.service
systemctl enable --now researchos-frontend.service
```

### 3. 配置 nginx

示例：

```nginx
server {
    listen 80;
    server_name _;

    client_max_body_size 100m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }

    location /docs {
        proxy_pass http://127.0.0.1:8000/docs;
    }

    location /openapi.json {
        proxy_pass http://127.0.0.1:8000/openapi.json;
    }

    location / {
        proxy_pass http://127.0.0.1:3002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }
}
```

检查并重载：

```bash
nginx -t
systemctl reload nginx
```

### 4. 放行公网端口

至少需要放行：

- `80/tcp`

如果服务器安全组或防火墙没放行，公网 IP 还是访问不到。

### 5. 验证

```bash
curl http://127.0.0.1:8000/health
curl -I http://127.0.0.1:3002/
curl -I http://127.0.0.1/
curl -I http://<你的公网IP>/
```

## Docker Compose 部署

仓库仍然保留 Docker Compose 方案：

```bash
cp .env.example .env
docker compose up -d --build
```

默认端口：

- 前端: `3002`
- 后端: `8002`

访问地址：

- 前端: `http://localhost:3002`
- 后端: `http://localhost:8002`
- API 文档: `http://localhost:8002/docs`

说明：

- Docker 路线适合隔离运行
- 如果你已经采用 `systemd + nginx`，通常不需要再同时跑 Docker 版本

## Worker 说明

自动主题抓取、每日简报、每周图谱维护、闲时自动处理等能力依赖 worker。

Docker 部署时：

- `docker compose up` 会自动拉起 `worker`

本地 / systemd 部署时，如需自动化调度，可以单独运行：

```bash
cd /home/ResearchOS
./.venv/bin/python -m apps.worker.main
```

如果你只需要手动使用 Web 界面，不一定要先启动 worker。

## 常用运维命令

### systemd

```bash
systemctl status researchos-backend.service
systemctl status researchos-frontend.service
systemctl restart researchos-backend.service
systemctl restart researchos-frontend.service
journalctl -u researchos-backend.service -f
journalctl -u researchos-frontend.service -f
```

### nginx

```bash
nginx -t
systemctl status nginx
systemctl reload nginx
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

### Docker

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f worker
docker compose logs -f frontend
docker compose up -d --build
docker compose down
```

## 常见问题

### 1. 公网 IP 打不开

优先检查：

- 安全组是否放行 `80/tcp`
- nginx 是否在监听 `0.0.0.0:80`
- `curl -I http://127.0.0.1/` 是否正常

### 2. 页面能打开，但 API 不通

优先检查：

- nginx `/api/` 是否正确反代到 `127.0.0.1:8000`
- 后端是否正常：`curl http://127.0.0.1:8000/health`
- `CORS_ALLOW_ORIGINS` 是否包含你的前端访问地址

### 3. 提取图表报 MinerU 错误

优先检查：

- `MINERU_API_TOKEN` 是否填写
- MinerU API quota / token 是否可用
- 默认模式是否还是 `FIGURE_EXTRACT_MODE=arxiv_source`

### 4. 为什么手动启动的后台进程会消失

因为临时 shell 起的 `uvicorn` / `vite` 不等于真正的系统服务。
生产环境请用 `systemd`、Docker 或其他进程管理器托管。

## 许可证

MIT
