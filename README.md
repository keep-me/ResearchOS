# ResearchOS

ResearchOS 是一款面向高校学生和科研人员的一体化智能研究辅助平台。随着学术论文数量快速增长，研究者在开展课题时常需反复检索论文、筛选文献、阅读原文、提炼方法与实验要点，并手动整理笔记和研究脉络，过程耗时长、重复性高。ResearchOS 以研究助手 Agent 为核心，围绕文献发现、论文导读、知识沉淀和科研工作流推进构建连续研究链路，帮助用户快速发现感兴趣方向的论文，理解单篇论文的核心内容与技术细节，梳理研究趋势，并将论文库沉淀为个人科研知识库。

更完整的 Windows 本地启动、Docker 部署和安装包构建说明见 [`docs/start-project.md`](docs/start-project.md)。

## 功能概览

- 论文发现：支持主题订阅、论文检索、arXiv 导入和本地论文入库。
- 论文阅读：支持 PDF 阅读、Markdown 对齐、阅读笔记、粗读、精读、三轮分析和推理链分析。
- 证据分析：围绕论文原文、图表、章节和分析结果组织可追溯证据，辅助快速定位论文要点。
- 图表解析：支持论文图表、表格、算法块和题注等结构化信息提取与分析。
- 知识沉淀：通过主题归档、研究趋势概览、引用图谱和知识图谱构建个人科研知识库。
- 科研工作流：在用户形成研究想法、实验内容和初步结果后，辅助完成方案整理、实验推进、评审反馈和写作准备等重复性工作。
- 自动化任务：Worker 负责主题订阅抓取、首页 arXiv 趋势预计算、每日简报、每周图谱维护和闲时自动处理。

## 技术栈

- Backend: Python 3.11, FastAPI, SQLAlchemy, Alembic
- Frontend: React, TypeScript, Vite
- Database: 默认 SQLite，可通过 `DATABASE_URL` 切换
- Worker: Python 后台调度进程
- Deployment: 本地开发、Docker Compose、Windows exe / 安装包

## 目录结构

- `apps/api`: FastAPI 入口、路由和中间件
- `apps/worker`: 后台调度与自动化任务
- `apps/desktop`: 桌面版服务端入口
- `packages`: 核心业务、论文处理、LLM 集成、存储层和领域服务
- `frontend`: React 前端工程
- `infra`: Alembic migration、备份脚本等基础设施文件
- `scripts`: 本地初始化、测试、Windows 构建等脚本
- `docs`: 项目说明、设计文档、启动部署说明和交付材料
- `data`: 本地数据库、PDF、简报和运行数据，默认不提交到仓库

## 环境要求

- Windows 本地环境
- Python 3.11
- Node.js 20+ 和 npm
- Docker Desktop，可选，用于 Docker Compose 部署
- PyInstaller，可选，用于构建 Windows exe
- .NET Framework C# 编译器，可选，用于构建 Windows 安装包

构建安装包前可检查 C# 编译器：

```cmd
if exist "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" (echo OK csc x64) else (echo MISSING csc x64)
if exist "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe" (echo OK csc x86) else (echo MISSING csc x86)
```

## 配置

第一次启动前复制环境变量示例：

```cmd
cd /d D:\Desktop\ResearchOS
copy /Y .env.example .env
notepad .env
```

本地开发至少确认下面配置：

```env
APP_ENV=dev
SITE_URL=http://127.0.0.1:3002

DATABASE_URL=sqlite:///./data/researchos.db
PDF_STORAGE_ROOT=./data/papers
BRIEF_OUTPUT_ROOT=./data/briefs

CORS_ALLOW_ORIGINS=http://127.0.0.1:3002,http://localhost:3002

LLM_PROVIDER=zhipu
ZHIPU_API_KEY=你的Key
RESEARCHOS_DASHBOARD_TREND_ON_DEMAND=true
```

AI 分析、写作助手和 Agent 等功能需要配置可用的大模型服务 Key。若只验证页面、论文导入和基础流程，可以先不配置 Key。

## 本地启动

### 1. 安装后端依赖

```cmd
cd /d D:\Desktop\ResearchOS
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
pip install -e ".[dev,llm,pdf]"
```

如果只需要启动基础 API，也可以先安装最小依赖：

```cmd
pip install -e .
```

### 2. 安装前端依赖

```cmd
cd /d D:\Desktop\ResearchOS\frontend
npm install
cd /d D:\Desktop\ResearchOS
```

### 3. 初始化数据库

```cmd
cd /d D:\Desktop\ResearchOS
call .venv\Scripts\activate.bat
python scripts\local_bootstrap.py
```

初始化后会创建本地 SQLite 数据库和必要目录：

```text
data\researchos.db
data\papers
data\briefs
```

### 4. 启动后端

```cmd
cd /d D:\Desktop\ResearchOS
call .venv\Scripts\activate.bat
set RESEARCHOS_DASHBOARD_TREND_ON_DEMAND=true
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002
```

后端地址：

```text
http://127.0.0.1:8002
```

### 5. 启动前端

```cmd
cd /d D:\Desktop\ResearchOS\frontend
set VITE_PROXY_TARGET=http://127.0.0.1:8002
npm run dev -- --host 127.0.0.1 --port 3002 --strictPort
```

前端地址：

```text
http://127.0.0.1:3002
```

### 6. 启动 Worker

```cmd
cd /d D:\Desktop\ResearchOS
call .venv\Scripts\activate.bat
python -m apps.worker.main
```

只演示手动论文导入、论文阅读和项目工作流时，可以先不启动 Worker；需要展示自动订阅和定时任务时再启动。

### 7. 检查服务

```cmd
curl http://127.0.0.1:8002/health
curl -I http://127.0.0.1:3002
curl "http://127.0.0.1:8002/dashboard/arxiv-trend?subdomain=all&refresh=true"
```

## Docker 部署

Docker Compose 会启动 backend、worker 和 frontend。启动前先确认 `.env` 已经配置完成。

```cmd
cd /d D:\Desktop\ResearchOS
docker compose up -d --build
```

默认访问地址：

```text
前端：http://127.0.0.1:3002
后端：http://127.0.0.1:8002
API 文档：http://127.0.0.1:8002/docs
```

常用命令：

```cmd
docker compose ps
docker compose logs backend --tail 80
docker compose logs worker --tail 80
docker compose logs frontend --tail 80
docker compose down
```

## Windows exe 和安装包构建

构建流程会生成前端静态文件、后端可执行文件和 Windows 安装包。

```cmd
cd /d D:\Desktop\ResearchOS
call .venv\Scripts\activate.bat
pip install pyinstaller
scripts\windows\build-windows-installer.cmd -Clean
```

构建产物：

```text
dist\researchos-server.exe
dist\ResearchOS-Windows-Setup.exe
```

`researchos-server.exe` 是桌面版服务端可执行文件，内置后端、静态前端和 Worker。启动后会自动选择本机空闲端口，并在标准输出首行打印端口：

```json
{"port": 51234}
```

拿到端口后，在浏览器打开：

```text
http://127.0.0.1:51234
```

桌面版默认数据目录：

```text
%APPDATA%\ResearchOS\data
```

也可以通过环境变量指定：

```cmd
set RESEARCHOS_DATA_DIR=D:\ResearchOSData
dist\researchos-server.exe
```

## 常见问题

### 前端能打开，但 API 请求失败

确认前端启动前设置了代理地址：

```cmd
set VITE_PROXY_TARGET=http://127.0.0.1:8002
```

然后重启前端开发服务器。

### 端口被占用

```cmd
netstat -ano | findstr ":8002"
netstat -ano | findstr ":3002"
taskkill /PID <PID> /F
```

### 首页趋势没有生成

确认 `.env` 或当前终端中启用了：

```env
RESEARCHOS_DASHBOARD_TREND_ON_DEMAND=true
```

然后请求：

```cmd
curl "http://127.0.0.1:8002/dashboard/arxiv-trend?subdomain=all&refresh=true"
```

### PyInstaller 找不到

```cmd
cd /d D:\Desktop\ResearchOS
call .venv\Scripts\activate.bat
pip install pyinstaller
python -m PyInstaller --version
```

## 许可证

MIT
