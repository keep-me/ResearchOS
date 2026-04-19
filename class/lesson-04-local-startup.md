# 第 04 课：本地启动全流程

## 1. 本课定位

如果你不能稳定启动项目，你后面所有理解都只能停留在“看静态代码”。这节课的目标不是机械抄命令，而是弄清楚启动时到底发生了什么，哪些目录会被创建，哪些进程会起来，哪些端口会暴露，以及这轮代码修订后哪些安全检查已经提前到启动阶段。

## 2. 学完你应该能回答的问题

- 本地启动的最小步骤是什么。
- 数据库初始化为什么不能省略。
- 前端和后端分别在哪个端口提供服务。
- `scripts/local_bootstrap.py`、`uvicorn`、Vite 三者分别扮演什么角色。
- 为什么现在 API 启动时还会先校验认证配置。

## 3. 学习前准备

- 阅读 `scripts/local_bootstrap.py`。
- 阅读 `scripts/start_api_dev.ps1` 和 `scripts/start_frontend_dev.ps1`。
- 阅读 `apps/api/main.py` 的 `startup` 钩子。
- 确保你知道 `.env`、`.venv`、`frontend/node_modules` 的作用。

## 4. 详细讲解

### 4.1 启动本质上是四件事

你可以把本地开发启动拆成四件独立事件：

1. 准备配置和依赖。
2. 显式 bootstrap 存储层。
3. 启动 API，并在启动事件里再次做运行时准备。
4. 启动前端开发服务器，通过代理访问后端。

这四件事必须区分开。因为以后你排查问题时，问题也基本落在这四个层次里。

### 4.2 `local_bootstrap.py` 现在做的是“显式准备存储”

很多新手会忽略这一步，以为“跑 `uvicorn` 就行”。但当前代码已经把这件事写得非常明确：

- `scripts/local_bootstrap.py` 调用的是 `bootstrap_local_runtime()`。
- `bootstrap_local_runtime()` 会进入 `packages/storage/bootstrap.py`。
- `bootstrap_storage()` 会检查当前数据库 revision，必要时给旧库补 stamp，再用 Alembic 升级到 head。

这意味着数据库初始化不再是“导入 ORM 就自动建表”，而是显式、可验证、可回归测试的启动动作。

### 4.3 本地默认端口不是猜的，是脚本里写死的

当前开发脚本给了你非常明确的端口约定：

- `scripts/start_api_dev.ps1` 用 `uvicorn apps.api.main:app --host 127.0.0.1 --port 8010`
- `scripts/start_frontend_dev.ps1` 用 `npx vite --host 127.0.0.1 --port 4317 --strictPort`
- 前端通过 `VITE_PROXY_TARGET=http://127.0.0.1:8010` 把 API 请求代理到后端

所以你排查启动问题时，不要先猜端口，先回到脚本。

### 4.4 API 启动时已经不是“只开 HTTP 端口”

`apps/api/main.py` 在 `startup` 钩子里依次做了两件重要事情：

- `validate_auth_configuration()`
- `bootstrap_api_runtime()`

这说明当前 API 启动会先确认认证配置有没有明显危险状态，再确保数据库 schema 和任务追踪器已经准备好。也就是说，服务“进程已启动”和服务“具备可用运行态”已经被明确拆开。

### 4.5 健康检查要验证两层

就算命令都执行成功了，复杂系统仍然可能处在“进程在，但服务没真正可用”的状态。当前仓库里至少要检查两层：

- API 健康，例如 `/health`、`/global/health`
- 前端页面是否能正常加载，并且代理请求真的能打到 `8010`

前端页面能打开，只说明 Vite 在；只有请求能成功，你才能说整条链路是通的。

## 5. 参考代码对照

### 5.1 `reference/claw-code-main`

`reference/claw-code-main` 的启动中心更偏本地工作台与原生层，而当前 `ResearchOS` 的本地开发链路仍然是“后端进程 + 前端开发服务器”双进程主导。对照这两种形态，能帮助你理解为什么当前仓库更接近 Web/API 驱动的研发体验。

## 6. 代码精读顺序

1. `scripts/local_bootstrap.py`
2. `packages/storage/bootstrap.py`
3. `apps/api/main.py`
4. `scripts/start_api_dev.ps1`
5. `scripts/start_frontend_dev.ps1`
6. `frontend/package.json`

## 7. 动手任务

1. 按脚本和代码入口完整启动一次本地环境。
2. 记录数据库 bootstrap 成功时的关键输出，确认有没有 `alembic_version`。
3. 记录后端和前端分别监听的端口。
4. 用浏览器或请求工具访问健康检查接口。
5. 故意在启用认证但未配置 `AUTH_SECRET_KEY` 的情况下启动一次 API，观察启动阶段报错。

## 8. 验收标准

- 你能独立完成本地启动。
- 你能解释每一步启动命令的作用。
- 你能说清楚“显式 bootstrap”和“API startup bootstrap”的区别。
- 你能区分“环境问题”“后端问题”“前端问题”的基本排查方向。

## 9. 常见误区

- 误区一：把前端能打开当成系统已正常。
- 误区二：跳过数据库初始化。
- 误区三：不记录端口、数据目录、日志位置，导致每次都从零排查。
