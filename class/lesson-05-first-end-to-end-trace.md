# 第 05 课：第一条前后端调用链

## 1. 本课定位

前四课建立的是“地图”。这一课开始做第一条完整追踪：从前端页面进入，到 API 服务层，再到后端入口层。目标不是一次看懂全项目，而是学会一种以后能反复复用的阅读方法。

## 2. 学完你应该能回答的问题

- 前端是如何决定请求哪个后端地址的。
- `frontend/src/services/api.ts` 在项目里扮演什么角色。
- 后端 `apps/api/main.py` 如何把请求转给具体路由。
- 为什么“功能追踪”比“按目录从头到尾看”更适合学习这个仓库。

## 3. 学习前准备

- 阅读 `frontend/src/App.tsx`。
- 阅读 `frontend/src/services/api.ts` 前半部分。
- 阅读 `apps/api/main.py`。

## 4. 详细讲解

### 4.1 从 `App.tsx` 看系统入口

`frontend/src/App.tsx` 告诉你几件关键事实：

- 前端有认证前置逻辑。
- 页面通过路由懒加载组织。
- `Agent` 页面是主入口之一。
- 未认证、后端未就绪、正常应用态是不同分支。

这说明前端不是“几个散落页面”，而是有完整应用壳层和状态切换。

### 4.2 `api.ts` 为什么是关键学习点

很多新手会跳过服务层，直接看页面里怎么 `fetch`。但在这个项目里，`frontend/src/services/api.ts` 是非常关键的中间层：

- 统一解析 API 基地址。
- 统一拼接认证 token。
- 统一处理错误。
- 把后端能力分成一个个可调用的前端 API。

只要你学会看这个文件，以后几乎所有页面的数据流都能更快理解。

### 4.3 API 基地址为什么值得单独理解

`api.ts` 通过 `resolveApiBase()` 获取后端地址，而这个逻辑又来自 `frontend/src/lib/tauri.ts`。这里反映的不是简单的地址拼接，而是系统兼容多运行方式的痕迹：

- Web 开发态走本地后端。
- 生产态可能走 `/api` 代理。
- 桌面兼容层还保留了历史接口表面。

这说明“运行环境”会直接影响前端如何连接后端。

### 4.4 后端入口如何接住请求

`apps/api/main.py` 的职责是：

- 构建 FastAPI 应用
- 注册公共中间件
- 注册各业务 router
- 在启动事件里准备运行时依赖

所以你在追一个具体请求时，通常会经历这条脑内路径：

前端页面 -> `services/api.ts` -> 后端 `main.py` -> 某个 router -> 某个 service/repository

这一课先只追到 router 层即可，后面的课程再继续往下钻。

### 4.5 为什么这种追踪方法很重要

如果你直接读 `packages/`，很容易在庞大模块树里失焦。更高效的方法是：

1. 先找用户看到的页面。
2. 找页面调用的 API。
3. 找 API 对应的 router。
4. 再顺着 router 进入业务层。

以后你读论文、图谱、项目工作流、Agent 页面，都应该用这个方法。

## 5. 参考代码对照

### 5.1 `reference/claw-code-main`

`reference/claw-code-main` 更强调统一工作台壳层和本地助手界面。对照它看 `ResearchOS`，你会更容易意识到：页面不只是显示数据，还在承载运行时状态、会话和工具入口。

### 5.2 回到当前仓库

当前仓库的 `frontend/src/services/api.ts`、`frontend/src/contexts/`、`apps/api/main.py` 已经构成了一条很典型的前后端调用链。先把这条链路读顺，比先啃单个大页面更有效。

## 6. 代码精读顺序

1. `frontend/src/App.tsx`
2. `frontend/src/lib/tauri.ts`
3. `frontend/src/services/api.ts`
4. `apps/api/main.py`
5. 任选一个 router，例如 `apps/api/routers/auth.py` 或 `apps/api/routers/papers.py`

## 7. 动手任务

1. 追踪登录状态检查这条链路：前端如何发起 `/auth/status` 请求，后端如何返回结果。
2. 再追踪一个论文相关请求或项目相关请求。
3. 用一句话总结每一层的职责：页面层、服务层、入口层、路由层。

## 8. 验收标准

- 你能画出至少一条完整的前后端调用链。
- 你能解释 `api.ts` 为什么是前端理解的关键入口。
- 你能说明为什么先按功能追踪比先按目录通读更有效。

## 9. 常见误区

- 误区一：页面一多就只盯住 JSX，不看数据来源。
- 误区二：把 `api.ts` 当成无聊样板而跳过。
- 误区三：以为所有后端逻辑都在 `main.py`，忽略 router 分发。
