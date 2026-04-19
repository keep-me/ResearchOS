# 第 06 课：FastAPI 应用入口

## 1. 本课定位

这一课开始正式进入后端主干。`apps/api/main.py` 很短，但它是整个后端的总装配点。你要学会从这个文件看出：系统如何启动、哪些公共能力在入口层处理、业务路由如何被接入。

## 2. 学完你应该能回答的问题

- 为什么 `main.py` 文件不大，却非常关键。
- `RequestLogMiddleware`、`AuthMiddleware`、异常处理、CORS、GZip 分别在什么阶段接入。
- 启动事件为什么要调用 `bootstrap_api_runtime()`。
- 业务 router 是如何被统一挂到应用上的。

## 3. 学习前准备

- 通读 `apps/api/main.py`。
- 回看 `packages/storage/bootstrap.py`。
- 对 FastAPI 有最基础的概念即可，不要求深入框架原理。

## 4. 详细讲解

### 4.1 应用入口是“拼装台”，不是业务仓库

`main.py` 的关键价值不在“实现业务”，而在“把系统拼起来”。它负责：

- 初始化日志。
- 读取配置。
- 创建 FastAPI 应用实例。
- 挂请求日志和认证中间件。
- 挂 GZip 和 CORS。
- 注册统一业务异常处理。
- 在启动时做 runtime bootstrap。
- 注册全部业务路由。
- 在关闭时清理运行时状态。

如果你看到这里有很多 `include_router`，不要以为它只是“列文件清单”。这正说明后端入口的职责是装配，而不是承载业务细节。

### 4.2 请求日志中间件为什么值得单独看

`RequestLogMiddleware` 做了几件很实用的事：

- 为每个请求生成 `request_id`。
- 记录方法、路径、状态码、耗时。
- 把 `X-Request-Id` 写回响应头。

这类中间件解决的不是业务问题，而是可观测性问题。大型系统一旦没有请求级日志，后期排查会非常痛苦。你以后读别的项目，也要对这种“横切能力”敏感。

### 4.3 认证中间件为什么放在入口层

`AuthMiddleware` 的逻辑说明了一个典型设计：

- 先根据配置判断认证是否开启。
- 再对白名单路径放行。
- 再允许文档或静态访问路径跳过。
- 再从 `Authorization` header 或 query token 读取 token。
- 最后做 JWT 解码与用户信息注入。

这类逻辑适合放在入口层，因为它针对的是“所有请求”的通用策略。若散落到每个 router，会变得很难维护。

### 4.4 启动事件为什么重要

`@app.on_event("startup")` 中调用 `bootstrap_api_runtime()`，说明后端不是“请求来了再说”。系统在真正接收流量前，要先准备好：

- 数据库 schema
- 任务追踪器状态
- 其他运行时依赖

这里其实已经体现出“服务启动”和“服务可用”是两个层次。后面读 Worker、Project Workflow 时，你会越来越意识到这个差别。

### 4.5 路由注册顺序透露了业务版图

从 `include_router` 列表可以直接看到当前产品版图：

- 基础系统与全局路由
- 论文、主题、图谱
- Agent 与工作区
- Projects 与 Session Runtime
- 内容、Pipelines、MCP、Settings、Writing、Jobs、Auth、OpenCode

这一串列表本身就是后端能力地图。你读它时，不只是看“有多少个 router”，而是在看“产品已经长到了哪些边界”。

## 5. 参考代码对照

### 5.1 `reference/claw-code-main`

`reference/claw-code-main` 的入口组织更偏桌面工作台与本地能力调度，而当前 `ResearchOS` 的 `apps/api/main.py` 则是标准的 Web 服务装配点。对照这两种入口，你会更容易理解“入口层负责装配，不负责承载全部业务”这个原则。

### 5.2 回到当前仓库

当前仓库里真正决定后端边界的，是 `apps/api/main.py`、`apps/api/routers/` 和 `packages/` 的分层关系。只要把这三层读顺，后端主骨架就清楚了。

## 6. 代码精读顺序

1. `apps/api/main.py`
2. `packages/storage/bootstrap.py`
3. `packages/domain/exceptions.py`
4. `packages/auth.py`
5. 观察 `apps/api/routers/` 下有哪些路由文件

## 7. 动手任务

1. 手工写出一次请求在 `main.py` 里经过的处理顺序。
2. 解释 `startup` 和 `shutdown` 各自承担什么职责。
3. 对照 `include_router` 列表，给后端能力分组。

## 8. 验收标准

- 你能解释 `main.py` 的所有主要职责。
- 你能说清楚哪些逻辑适合放入口层，哪些不适合。
- 你能根据 router 列表描述当前后端的能力版图。

## 9. 常见误区

- 误区一：因为文件短，就低估其重要性。
- 误区二：把入口层和业务层混为一谈。
- 误区三：只看 `include_router`，不看启动和中间件逻辑。
