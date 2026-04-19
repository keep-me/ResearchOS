# 第 22 课：Session Runtime 与对话状态

## 1. 本课定位

这是整个课程中最关键的一课之一。`Session Runtime` 是 `ResearchOS` 从普通聊天接口走向真正 Agent 系统的核心。你要理解的不是“消息列表怎么保存”，而是“会话为什么会变成一个正式运行时系统”。最近这轮修订里，前端 `AssistantInstance` 对 session 的绑定规则也被进一步收紧了。

## 2. 学完你应该能回答的问题

- 为什么 Agent 会话不能只是一张消息表。
- `session_runtime.py`、`session_store.py`、`session_lifecycle.py` 分别负责什么。
- 为什么系统要处理快照、回滚、重试、问题确认、待办、边界修复等复杂问题。
- 为什么工作区绑定已经成为会话能否真正启动的前提之一。
- 为什么这一层必须当成正式运行时系统来看待。

## 3. 学习前准备

- 阅读 `packages/agent/session/session_runtime.py` 前半部分。
- 浏览 `packages/agent/session/session_store.py`、`session_lifecycle.py`。
- 浏览 `frontend/src/features/assistantInstance/store.ts`。
- 浏览 `tests/test_agent_session_runtime.py` 的测试名列表。

## 4. 详细讲解

### 4.1 普通聊天与 Agent 会话的区别

普通聊天系统通常只要处理：

- 用户消息。
- 模型回复。

但 Agent 会话远不止如此，它还要处理：

- 工具调用。
- 流式增量。
- 推理片段。
- 挂起确认。
- 重试与恢复。
- 中断与终止。
- 会话级配置。
- 工作区绑定。

这就是为什么 `Session Runtime` 会看起来非常复杂。

### 4.2 `AgentSession` 模型已经不只是“聊天记录头”

从 `packages/storage/models.py` 往回看，你会发现当前 `AgentSession` 还持有：

- `mode`
- `backend_id`
- `workspace_path`
- `workspace_server_id`
- `permission_json`
- `revert_json`

这说明 session 已经是运行时快照容器，而不是单纯消息容器。

### 4.3 为什么消息要拆成 part

模型回复不一定是“一段纯文本”。它可能包含：

- reasoning
- text
- tool call
- tool result
- usage metadata

所以数据库里不仅有 `AgentSessionMessage`，还有 `AgentSessionPart`。这说明系统在努力保留更细粒度的对话结构，而不是把所有内容扁平拼成一条字符串。

### 4.4 前端这轮改动说明“没有工作区，不自动拉起 session”

`frontend/src/features/assistantInstance/store.ts` 这轮有一个很重要的行为变化：

- `bootstrapConversation()` 发现没有 `workspacePath` 时会直接返回。
- `ensureSessionForConversation()` 在没有工作区时会抛出 `ASSISTANT_WORKSPACE_REQUIRED_MESSAGE`。

这和测试里的几个断言是呼应的：

- `test_session_create_requires_workspace_binding`
- `test_session_prompt_requires_workspace_for_new_session`
- `test_agent_chat_requires_workspace_binding`

它传递的设计信号很明确：当前 Agent 运行态已经把“绑定工作区”当成默认前提，而不是可有可无的附加信息。

### 4.5 为什么测试文件如此巨大

`tests/test_agent_session_runtime.py` 很长，这不是坏事，反而说明：

- 会话层问题很多且细碎。
- 边界条件非常多。
- 一旦回归，用户体验会直接受损。

测试名里你能看到很多高价值关键词：

- `tool_metadata`
- `reasoning_spacing`
- `persistence`
- `reload_latest_transcript`
- `plan_mode_transition`
- `workspace_binding`

这些测试名本身就是学习索引。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 也把助手、工作台和本地状态放在同一个系统语境里。对照它可以帮助你理解：会话不是临时 UI 状态，而是研究过程中的正式资产和运行时状态。

## 6. 代码精读顺序

1. `packages/agent/session/session_runtime.py`
2. `packages/agent/session/session_store.py`
3. `packages/agent/session/session_lifecycle.py`
4. `packages/storage/models.py` 中 `AgentSession`、`AgentSessionMessage`、`AgentSessionPart`
5. `frontend/src/features/assistantInstance/store.ts`
6. `tests/test_agent_session_runtime.py`

## 7. 动手任务

1. 画出一轮 Agent 会话的生命周期图。
2. 说明为什么一条 assistant 回复需要拆成多个 part。
3. 从测试名中挑 10 个，反推系统正在保护哪些行为边界。
4. 解释为什么当前前端会在没有工作区时停止自动 bootstrap 会话。

## 8. 验收标准

- 你能说明会话层为什么会复杂。
- 你能解释 message 和 part 的差别。
- 你能理解测试为什么在这一层尤其重要。
- 你能说清楚工作区绑定在当前实现里已经是 session 启动前提。

## 9. 常见误区

- 误区一：把 Agent 会话当成普通聊天消息列表。
- 误区二：看到复杂状态就以为都是过度设计。
- 误区三：忽视对流式、工具、回滚、确认等边界的持久化需求。
