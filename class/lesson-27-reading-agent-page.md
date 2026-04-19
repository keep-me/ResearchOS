# 第 27 课：如何阅读 Agent 页面

## 1. 本课定位

这一课是前端部分最重要的一课。`frontend/src/pages/Agent.tsx` 是典型的大状态页面，新手看到几百行状态和引用很容易直接放弃。目标不是一次看懂所有细节，而是学会正确拆解它。最近这轮修订里，这个页面与 `AssistantInstance` store 的会话创建、工作区绑定和 smoke 覆盖都发生了真实变化。

## 2. 学完你应该能回答的问题

- 为什么 `Agent` 页面会很大。
- 页面内部哪些状态是本地 UI 状态，哪些来自全局上下文，哪些来自后端会话。
- `AssistantInstanceContext` 在整个页面中扮演什么角色。
- 为什么复杂页面必须依赖上下文、服务层和共享工具函数。
- 为什么当前前端把“工作区绑定”和“持久化 conversation”看得更重。

## 3. 学习前准备

- 阅读 `frontend/src/pages/Agent.tsx` 文件开头。
- 阅读 `frontend/src/contexts/AssistantInstanceContext.tsx`。
- 浏览 `frontend/src/features/assistantInstance/`，重点看 `store.ts`。
- 浏览 `frontend/tests/smoke.spec.ts`。

## 4. 详细讲解

### 4.1 不要按顺序硬读大页面

读 `Agent.tsx` 的正确姿势不是从第一行看到最后一行，而是先分层：

- 页面从上下文拿到了什么。
- 页面自己维护了哪些局部状态。
- 页面调用了哪些 API。
- 页面依赖了哪些共享组件。

只有这样，你才能在复杂页面里保持方向感。

### 4.2 `useAssistantInstance()` 仍然是阅读入口

这个 hook/上下文暴露了页面最核心的会话状态：

- 当前会话与会话标题。
- 当前 session id。
- 权限预设、模式、推理等级。
- 已挂载论文。
- 消息 items。
- 待确认动作。
- 发送消息、确认、拒绝、停止生成等行为。

所以阅读 `Agent.tsx` 时，先把这些从上下文来的能力归成一组，你就已经拿到了主框架。

### 4.3 这轮前端改动说明了两个设计选择

最近的几个改动很值得当阅读锚点：

- `createConversationWithRuntime()` 不再以 `{ persist: false }` 的方式创建临时 conversation。
- `bootstrapConversation()` 发现没有 `workspacePath` 时直接返回。

这两个变化加在一起，说明当前前端更倾向于：

- 让会话记录尽快持久化。
- 只有在绑定了工作区时才去自动拉起真正的后端 session runtime。

这不是 UI 小调整，而是运行时边界在前端的体现。

### 4.4 局部状态很多，但不是同一种复杂度

页面里大量 `useState` 并不一定是坏设计，因为它承载的是真实复杂交互：

- 论文导入弹窗。
- 模型配置切换。
- MCP 配置弹窗。
- 工作流抽屉。
- 工作区侧栏。
- 终端抽屉。
- 搜索、筛选、上传、进度展示。
- ACP 权限确认流。

这些状态很多属于“界面控制状态”，它们不适合全都塞进后端，也不适合全都塞进一个全局 store。

### 4.5 smoke 测试是阅读页面的第二入口

`frontend/tests/smoke.spec.ts` 现在已经覆盖了很多 Agent 页真实行为：

- 本地工作区会话切换并跨刷新保持。
- 远程 ACP confirm/reject/abort。
- 问题卡片提交流程。
- 默认 projects root 下的项目创建。
- 设置页里的 ACP registry 和 mock ACP 测试。

这意味着你读 `Agent.tsx` 时，不能只看组件本身，还要看测试如何证明这些交互成立。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 的工作台页面组织提醒你：研究场景页面天然会承载多个面板、上下文和资产入口，大页面不一定是坏事，关键是结构是否清楚。

## 6. 代码精读顺序

1. `frontend/src/pages/Agent.tsx`
2. `frontend/src/contexts/AssistantInstanceContext.tsx`
3. `frontend/src/contexts/AgentWorkbenchContext.tsx`
4. `frontend/src/features/assistantInstance/store.ts`
5. `frontend/tests/smoke.spec.ts`
6. `frontend/src/components/agent/agentPageShared.ts`

## 7. 动手任务

1. 把 `Agent.tsx` 中的状态分成三类：全局上下文、页面局部 UI、后端驱动状态。
2. 画出 `AssistantInstanceContext` 与页面的关系图。
3. 解释为什么当前 store 会在没有工作区时停止自动 bootstrap session。
4. 任选一个 smoke case，反推它在保护页面的哪条交互链路。

## 8. 验收标准

- 你能用分层方式读懂 `Agent` 页面。
- 你能说清楚页面为什么复杂而又不完全混乱。
- 你能理解 `AssistantInstanceContext` 是前端 Agent 运行时核心。
- 你能说明最近这轮改动在前端会话边界上收紧了什么。

## 9. 常见误区

- 误区一：从上到下机械读大页面。
- 误区二：把所有 `useState` 都当成设计失败。
- 误区三：只把 Agent 页面看成聊天 UI，不看它的工作台属性。
