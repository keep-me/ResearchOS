# 第 03 课：技术栈总览

## 1. 本课定位

新手最常见的问题不是“不知道技术名词”，而是不知道每个技术在当前项目里真正负责什么。这一课的目的，就是把技术名词从“背诵表”变成“职责图”。

## 2. 学完你应该能回答的问题

- 为什么这个项目需要 `FastAPI + React + SQLite + APScheduler + LLM SDK` 这样的组合。
- 哪些技术解决的是业务问题，哪些解决的是工程问题。
- 为什么前端、后端、任务调度、数据库、模型调用会同时出现。

## 3. 学习前准备

- 阅读 `pyproject.toml`。
- 阅读 `frontend/package.json`。
- 对照 `packages/config.py`、`packages/storage/db.py`、`apps/api/main.py` 看这些依赖在运行时是怎么落地的。

## 4. 详细讲解

### 4.1 Python 侧技术栈不是随意堆出来的

从 `pyproject.toml` 看，后端核心栈包括：

- `fastapi`：Web API 与流式接口入口。
- `sqlalchemy`、`alembic`：数据模型和迁移。
- `apscheduler`：定时任务。
- `httpx`：访问外部论文源和模型服务。
- `pydantic-settings`：配置系统。
- `python-jose`、`passlib`：认证。
- `paramiko`、`pywinpty`：远程执行和本地终端支持。

这些依赖不是同一层面的问题：

- Web 层负责暴露能力。
- 存储层负责持久化。
- 调度层负责持续处理。
- 集成层负责连外部系统。
- Agent/Workspace 相关依赖负责执行能力。

### 4.2 前端技术栈的重点不是“好不好看”，而是“能不能承载复杂状态”

`frontend/package.json` 可以看出前端核心是：

- `react`、`react-dom`
- `react-router-dom`
- `vite`
- `typescript`
- `tailwindcss`
- `react-markdown`、`katex`、`mermaid`
- `react-pdf`

这说明前端承担的不是普通表单页，而是复杂内容展示：

- Markdown 渲染
- 数学公式
- Mermaid 图
- PDF 阅读
- 大量异步状态和路由切换

### 4.3 SQLite 在这里不是“玩具数据库”

很多人一看到 SQLite 就会低估项目复杂度。但看 `packages/storage/db.py` 就会发现，这里明确启用了：

- WAL
- busy timeout
- foreign keys
- cache size
- temp store in memory

这说明作者是在把 SQLite 当成单机科研工作台的正式存储，而不是测试临时库。

### 4.4 LLM 相关依赖说明了产品真实形态

可选依赖里有 `openai`、`anthropic`，主配置里还支持 `zhipu`、`gemini`。这说明项目一开始就没有把模型调用硬绑死到一个厂商，而是把“模型提供商切换”当成系统能力。

这对学习很重要，因为你之后看到 `LLMClient` 时就不会把它误以为是“多写了一层壳”，而会明白它是在隔离厂商差异。

## 5. 参考代码对照

### 5.1 `reference/claw-code-main`

`reference/claw-code-main` 更偏本地研究工作台栈，根目录里能直接看到 `src/` 和 `rust/` 两条实现线。对照它有助于你理解：同样是科研助手，架构中心可以是桌面原生壳层，也可以像当前 `ResearchOS` 这样以 FastAPI + React 为中心。

## 6. 代码精读顺序

1. `pyproject.toml`
2. `frontend/package.json`
3. `packages/storage/db.py`
4. `packages/config.py`
5. `apps/api/main.py`
6. `reference/claw-code-main/src/`
7. `reference/claw-code-main/rust/`

## 7. 动手任务

1. 做一张“技术 -> 项目职责 -> 所属层次”的表。
2. 把所有依赖分成五类：Web、存储、调度、模型、前端展示。
3. 思考：如果拿掉 `APScheduler`、`React-PDF`、`LLM SDK` 中任意一个，产品会损失什么核心能力。

## 8. 验收标准

- 你能解释关键依赖为什么存在。
- 你能区分“业务能力依赖”和“工程基础依赖”。
- 你能说明当前技术栈为什么适合单机科研工作台逐步演进成研究平台。

## 9. 常见误区

- 误区一：把技术栈当成招聘 JD 来背。
- 误区二：只看框架名字，不看它在项目中的职责。
- 误区三：看到 SQLite 就低估工程复杂度。
