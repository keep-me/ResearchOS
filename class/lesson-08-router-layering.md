# 第 08 课：Router 分层方式

## 1. 本课定位

Router 是后端里最容易“看得见”的层，但也是最容易被误用的层。这一课的目标是让你明白：router 应该负责什么，不应该负责什么，以及如何通过 router 快速理解业务版块。

## 2. 学完你应该能回答的问题

- 为什么 `ResearchOS` 要拆出这么多 router。
- `auth.py`、`papers.py`、`projects.py` 体现了哪些不同类型的接口。
- router 层和 repository/service 层的边界在哪里。
- 如何用 router 列表反向理解产品信息架构。

## 3. 学习前准备

- 阅读 `apps/api/routers/auth.py`。
- 阅读 `apps/api/routers/papers.py` 前半部分。
- 阅读 `apps/api/routers/projects.py` 前半部分。

## 4. 详细讲解

### 4.1 router 的核心职责

router 层通常负责：

- 定义 URL 路径和 HTTP 方法
- 解析请求参数
- 组装或校验响应模型
- 调用下层服务或仓储
- 处理少量接口级参数归一化

router 最不适合做的是：

- 长流程业务编排
- 复杂数据库事务
- 重复的通用逻辑

如果一个接口函数已经开始充满业务步骤，那通常说明逻辑应该继续下沉。

### 4.2 从 `auth.py` 看轻量 router

`auth.py` 是很好的入门例子，因为它很聚焦：

- 请求模型 `LoginRequest`
- 响应模型 `LoginResponse`
- 状态查询 `AuthStatusResponse`
- 登录接口
- 认证状态接口

它能让你看到“一个干净 router”的最小形态：路由定义清楚，业务逻辑少但不空洞。

### 4.3 从 `papers.py` 看复杂业务 router

`papers.py` 和 `auth.py` 完全不是一个量级。你会看到：

- 上传 PDF
- OCR 状态
- PDF 阅读提示词构造
- 图表、笔记、元数据处理
- 论文详情和文件输出相关逻辑

这说明论文模块已经是产品核心区，router 自然也会更复杂。但你要学会区分：

- 哪些复杂性来自“接口种类多”
- 哪些复杂性来自“有些逻辑还留在 router 里”

这两者不是一回事。

### 4.4 从 `projects.py` 看“业务编排型接口”

`projects.py` 的重要性在于它不只是 CRUD。它已经开始涉及：

- 项目工作区
- 本地和远程执行
- workflow 预设
- engine profile
- run action
- checkpoint

这意味着项目模块是 `ResearchOS` 从“研究应用”走向“执行平台”的关键接口面。你后面读 `workflow_runner.py` 时，会发现很多复杂性从这里进入系统。

### 4.5 如何用 router 列表理解产品结构

一个简单方法：

- `papers/topics/graph` 代表论文与知识主线。
- `agent/session_runtime/agent_workspace` 代表研究助手与工作区主线。
- `projects/jobs/opencode/mcp` 代表执行平台和扩展主线。
- `auth/settings/system` 代表基础设施主线。

所以 router 不只是接口入口，它也是产品边界图。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 更偏本地工作台和桥接调用，而不是标准 HTTP router。对照它能帮助你理解：当前 `ResearchOS` 仍然是明显的 Web API 中心架构，因此 router 本身就是产品边界图。

## 6. 代码精读顺序

1. `apps/api/routers/auth.py`
2. `apps/api/routers/papers.py`
3. `apps/api/routers/projects.py`
4. 对照 `apps/api/main.py` 里的 `include_router`

## 7. 动手任务

1. 给现有 router 按产品域分组。
2. 分析 `auth.py` 与 `papers.py` 的复杂度差异。
3. 选一个 `projects.py` 中的接口，描述它更像 CRUD 还是工作流编排。

## 8. 验收标准

- 你能解释 router 层的职责边界。
- 你能从三个 router 里看出三种不同接口形态。
- 你能根据 router 结构描述当前产品版图。

## 9. 常见误区

- 误区一：把 router 当成业务逻辑主战场。
- 误区二：看到文件大就直接否定设计，而不区分复杂性的来源。
- 误区三：不把接口分组与产品信息架构联系起来。
