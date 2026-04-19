# 第 26 课：前端入口与路由系统

## 1. 本课定位

进入最后一个模块后，重点变成“前端如何承载整个系统”。这一课要先从最顶层的 `App.tsx` 和 `Layout.tsx` 入手，理解前端为什么已经是完整应用壳层，而不是几个松散页面。

## 2. 学完你应该能回答的问题

- `App.tsx` 负责哪些全局职责。
- `Layout.tsx` 为什么会包裹这么多 Provider。
- 为什么前端需要认证前置、路由懒加载、错误边界和全局布局。
- `Agent` 页面为什么被放在路由中心位置。

## 3. 学习前准备

- 阅读 `frontend/src/App.tsx`。
- 阅读 `frontend/src/components/Layout.tsx`。
- 浏览 `frontend/src/contexts/` 目录。

## 4. 详细讲解

### 4.1 `App.tsx` 是前端总入口，不只是路由表

从这个文件你可以看出前端在做几类全局工作：

- 检查认证状态
- 处理“后端尚未就绪”的等待页
- 组织错误边界和 Toast
- 构建路由树
- 对重型页面做懒加载
- 处理常见旧路由重定向

这说明前端已经在承担运行时协调职责。

### 4.2 为什么要有等待后端的启动页

`BackendWaitingScreen` 很值得注意。它说明产品运行时假设不是“后端永远在那儿”，而是：

- 后端可能还没起好
- 前端需要轮询状态
- 用户不能直接看到崩坏页面

这类设计非常符合本项目“本地启动 + 开发态 + 历史桌面兼容”的真实环境。

### 4.3 `Layout.tsx` 体现的是“壳层优先”

`Layout.tsx` 的重点不是 HTML 结构，而是 Provider 顺序和布局组织：

- `AgentWorkbenchProvider`
- `AssistantInstanceProvider`
- `ConversationProvider`
- `GlobalTaskProvider`

这说明页面不是各自孤立管理状态，而是共享一个工作台上下文。也就是说，前端已经朝“研究工作台”而不是“多页面网站”发展。

### 4.4 路由结构本身就是信息架构

从路由表你能直接看出系统主页面：

- `/assistant`
- `/collect`
- `/papers`
- `/projects`
- `/graph`
- `/wiki`
- `/brief`
- `/tasks`
- `/settings`
- `/writing`

这其实就是前端产品心智地图。你以后分析功能优先级时，路由就是第一层信号。

### 4.5 为什么 `Agent` 页面在最中心

`/assistant` 被设置成多个入口的默认落点，说明当前产品的“中心交互”已经是研究助手工作台，而不是旧式 Dashboard。这是一个很重要的产品判断。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 最值得参考的是工作台壳层思路。对照它看 `Layout.tsx`，你会更容易理解为什么 `ResearchOS` 强调统一壳层、侧边栏和全局上下文。

## 6. 代码精读顺序

1. `frontend/src/App.tsx`
2. `frontend/src/components/Layout.tsx`
3. `frontend/src/components/Sidebar.tsx`
4. `frontend/src/contexts/`

## 7. 动手任务

1. 画出前端路由树。
2. 画出 `Layout` 内的 Provider 结构图。
3. 解释为什么等待后端和登录态检查都应该在入口层处理。

## 8. 验收标准

- 你能解释 `App.tsx` 与 `Layout.tsx` 的不同职责。
- 你能说清楚主要路由与产品模块的映射关系。
- 你能理解“壳层优先”的前端组织方式。

## 9. 常见误区

- 误区一：把前端入口看成纯路由表。
- 误区二：忽视 Provider 结构就是状态架构的一部分。
- 误区三：只看页面，不看壳层与全局状态如何连接。
