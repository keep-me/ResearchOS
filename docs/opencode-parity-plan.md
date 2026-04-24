# ResearchOS 对齐 OpenCode 实施记录

更新时间：2026-03-23

硬验收清单：

- [docs/opencode-hard-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-hard-checklist.md)

当前未完成原子清单：

- [docs/opencode-remaining-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-remaining-checklist.md)

说明：

- 本文保留历史实施记录
- 从 2026-03-24 起，是否“完成对齐”一律以硬验收清单为准，不再以轮次描述或“主路径已切换”作为完成判定
- 当前真正要执行的剩余工作，以未完成原子清单为准；本文件的 Round 记录仅作为历史记录

## 0. 五轮收敛进度

| 轮次 | 状态 | 本轮目标 | 当前结果 | 预计剩余轮次 |
|---|---|---|---|---|
| Round 1 | 已完成 | `SessionProcessor + MessageV2` 主链继续内收，减少 `agent_service` 外层 lifecycle / mutation ownership | 已把 prompt lifecycle 启动入口下沉到 `session_processor.stream_prompt_lifecycle(...)`，并删除 `agent_service` 中 `_run_model_turn_events / _execute_single_tool / _process_tool_calls / _stream_step_limit_summary / _stream_persisted_text_events / _emit_step_finish` 对 `SessionProcessor` 的直接写入旁路；原生主链现在统一走 `prompt event -> SessionProcessor.apply_event(...)` 进行 message/part mutation | 4 |
| Round 2 | 已完成 | 单 active loop + callback promise + lifecycle 收平 | `session_lifecycle` 已移除独立 `callback_loop_active` 布尔 owner，改为把 callback loop ownership 收进 `PromptInstance(loop_kind/running)`；`acquire -> running -> paused -> callback handoff -> finish` 现在走同一份 active loop token。`claim_prompt_callback_loop()` 也已感知 active prompt owner，不再只看 callback 布尔态 | 3 |
| Round 3 | 已完成 | provider/runtime typed error + transport 语义统一 | provider/runtime 现在统一经 `llm_provider_error.py` 输出同一份 typed error contract；`session_errors` 收成薄壳，`stream/probe` 直接消费同一份 `name/message/isRetryable/statusCode/responseHeaders/responseBody/providerID/transport/gateway/bucket/url` 语义，并补齐 typed SDK class-name 映射 | 2 |
| Round 4 | 已完成 | permission / tool exposure / registry 最后收平 | function tool 的 permission/default exposure/read-only/local-only 语义已内收到 `ToolDef.spec`，`tool_registry` 不再维护厚 `_TOOL_SPECS` 外挂表；builtin/custom tool 统一经 definition spec 驱动，provider-defined 只剩最薄兼容层 | 1 |
| Round 5 | 已完成 | 前端 bus-native / instance-driven 收齐 + 最终端到端验证 | assistant 前端已移除 `conversation.messages` / `flushPersistedMessages` 这条本地消息持久化旁路；`useConversations` 只保留会话元信息和 session 绑定，聊天历史统一以 `/session state + global bus` 为真源，首条用户消息与 session title 会回填会话标题 | 0 |

## 0.1 四轮收尾进度

| 轮次 | 状态 | 本轮目标 | 当前结果 | 预计剩余轮次 |
|---|---|---|---|---|
| Round 6 | 已完成 | `SessionPromptProcessor + MessageV2` 主链继续内收，减少 `agent_service` 外层 lifecycle / permission resume 调度厚度 | `SessionPromptProcessor` 现在统一持有 prompt 与 native permission resume 的 lifecycle 入口；`_stream_active()` 已收成共享 `_stream_lifecycle(...)` helper，`_respond_native_action_impl()` 与 callback loop permission 分支都已降为委托 `SessionPromptProcessor.stream_permission_response(...)`，confirm/reject 后续继续通过同一类 processor 的 `_resume_processor(...)` 续跑 | 3 |
| Round 7 | 已完成 | callback promise / instance lifecycle / permission object 完全同构 | instance/session lifecycle 的目录级清理由 `Instance.state(..., dispose=...)` 正式接管；queued callback payload 现在显式携带 `request_message_id`，但只会在它仍对应当前未完成 turn 时优先恢复，否则自动回落到最新 `turn_state`；native permission resume 也已改为绑定原始 assistant 的 `parentID`，不再错误吃“最新 user message” | 2 |
| Round 8 | 已完成 | provider runtime / tool exposure / system prompt 完全同构 | provider chat runtime 已补 LiteLLM transcript 兼容 `_noop` tool 和大小写 tool-call repair；native system prompt 主链已去掉本地 mode/language/selected-skill adapter，收成 OpenCode 风格的 `provider + environment + skills` 三段结构 | 1 |
| Round 9 | 已完成 | frontend instance/bus native 收尾 + 最终 OpenCode 验收 | assistant store 已不再把 `ConversationContext.activeConv/activeWorkspace` 当运行时真源；assistant 页面当前通过 `AssistantInstanceStore` 快照直接消费 `activeSession/activeWorkspace/conversationTitle`，会话列表仍只保留薄元信息持久化。最终验收结论：frontend 这一层已进一步贴近 OpenCode instance-driven 消费，但整体仍存在第 5 节列出的 backend/runtime 级差异 | 0 |

### Round 1 验证结论

- `SessionPromptProcessor._stream_active()` 与 `_respond_native_action_impl()` 不再各自直接创建厚本地 lifecycle 流程，而是统一经过 `session_processor.stream_prompt_lifecycle(...)`。
- native prompt 主链里原先那些“发事件的同时再直接写 `SessionProcessor`”的旧旁路已移除，assistant text/reasoning/tool/step/patch 的 mutation ownership 进一步收敛到 processor。
- 本轮完成后，`agent_service` 仍负责 prompt loop 编排与 native permission resume 语义，但不再在模型/工具/step 辅助函数里同时维护第二套 message mutation 路径。

### Round 2 验证结论

- `session_lifecycle.PromptSessionState` 不再维护独立 `callback_loop_active`，active owner 统一收进 `PromptInstance`，并显式区分 `loop_kind` 与 `running`。
- `acquire_prompt_instance()` 现在只拿 owner token；真正开始 prompt loop 时由 `mark_prompt_instance_running(...)` 切到 running，避免“只是占位就被当成 active loop”。
- callback resume / handoff 现在会在同一个 active loop token 上从 `prompt -> callback -> prompt(paused)` 切换，不再靠第二个布尔态表达 ownership。
- 新增回归用例覆盖：
  - running prompt 会阻止 callback loop 并行启动
  - paused prompt owner 可以被 callback loop 接管

### Round 3 验证结论

- provider/runtime 错误解释现在统一收口到 `packages/integrations/llm_provider_error.py`；`session_errors.normalize_error()` 不再重复做 transport/provider 解释，只保留 session 级 aborted 包装和最终 Unknown fallback。
- `stream/probe` 不再各自手拼错误 metadata，统一改为消费同一份 provider typed error contract：
  - `name`
  - `message`
  - `isRetryable`
  - `statusCode`
  - `responseHeaders`
  - `responseBody`
  - `providerID`
  - `transport`
  - `transportKind`
  - `gateway`
  - `bucket`
  - `url`
- 新增并验证了 Python SDK 常见 typed exception class-name 映射：
  - `AuthenticationError`
  - `RateLimitError`
  - `APIConnectionError`
  - `APITimeoutError`
- probe 失败结果现在会直接透出同一份归一化后的 transport/error 语义，而不是只返回裸 `str(exc)`。
- 本轮验证：
  - `python -m py_compile packages/integrations/llm_provider_error.py packages/ai/session_errors.py packages/integrations/llm_provider_stream.py packages/integrations/llm_provider_probe.py tests/test_agent_session_retry.py tests/test_llm_client_probe.py`
  - `python -m pytest tests/test_agent_session_retry.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py -q`：`23 passed`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py -q`：`145 passed`

### Round 4 验证结论

- `tool permission / default exposure / read-only / local-only` 语义现在挂在 `ToolDef.spec` 上，不再由 `tool_registry.py` 维护一张厚本地 `_TOOL_SPECS` 对照表。
- builtin tool、custom tool 现在统一通过 definition spec 暴露这些能力：
  - `permission`
  - `managed_permission`
  - `default_local_enabled`
  - `default_remote_enabled`
  - `allow_in_read_only`
  - `allow_user_enable`
  - `local_only`
- `tool_registry.tool_permission()`、`manages_tool()`、`default_tool_names_for_workspace()`、`enabled_tool_names_for_workspace()` 已全部改为直接消费 definition spec。
- provider-defined tool 仅保留最薄兼容层：
  - `local_shell -> bash permission`
- 新增回归覆盖：
  - builtin definition 自带 spec
  - local/remote 默认 tool exposure 来自 definition spec
  - custom tool 自带 spec 后可以直接驱动 permission 与 read-only 可见性
- 本轮验证：
  - `python -m py_compile packages/ai/tool_schema.py packages/ai/tool_catalog.py packages/ai/tool_registry.py tests/test_tool_registry.py`
  - `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py -q`：`33 passed`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_tool_registry.py -q`：`120 passed`

### Round 5 验证结论

- assistant 前端消息真源已收回到 `session state + global bus`：
  - `frontend/src/features/assistantInstance/store.ts` 不再把 `messageV2` 物化结果写回本地 conversation storage
  - `frontend/src/contexts/AssistantInstanceContext.tsx` 已删除基于 timer / `beforeunload` 的 `flushPersistedMessages(...)`
  - `frontend/src/hooks/useConversations.ts` 现在只持久化会话元信息、workspace 绑定、session 绑定和 assistant runtime 选项
- 旧 localStorage 兼容已做清洗迁移：
  - `loadConversation(...)` 会把历史存量里的 `messages` 字段剥离后再写回当前 key，避免旧消息缓存继续干扰 bus-native 渲染
- 会话标题不再依赖本地消息快照：
  - 首条用户消息会在发送前写入 metadata title
  - 如果 bus / state 返回了 session title，会优先回填 conversation title
- assistant 相关前端类型已同步收口：
  - `ConversationContext`、`AssistantInstanceStoreContext`、`AssistantInstanceStore`、`backend.ts` 的 prompt payload 类型都已改为不再依赖本地 message 持久化接口
- 本轮验证：
  - `rg -n --fixed-strings 'saveMessagesForConversation' frontend/src`：无结果
  - `rg -n --fixed-strings 'flushPersistedMessages' frontend/src`：无结果
  - `npm --prefix frontend run build`：通过
  - `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`：assistant 本轮涉及文件已无新增类型错误，但仓库仍存在历史遗留的全局前端类型错误，当前集中在 `InsightPanel.tsx`、`MermaidBlock.tsx`、`Sidebar.tsx`、`Agent.tsx`、`Collect.tsx`、`PaperDetail.tsx`、`Projects.tsx`、`Tasks.tsx`、`services/api.ts`

### Round 6 验证结论

- `SessionPromptProcessor` 的 prompt 与 permission resume 两条入口已经继续收平：
  - `_stream_active()` 不再自己展开 lifecycle 包装，而是统一委托共享 `_stream_lifecycle(...)`
  - native permission confirm/reject 不再由模块级 `_respond_native_action_impl()` 自己拼 lifecycle closure；现在改为 `SessionPromptProcessor.stream_permission_response(...)`
  - queued callback loop 内的 permission 分支也直接走 `SessionPromptProcessor.stream_permission_response(...)`
- confirm/reject 后的续跑 processor 也已进一步内收：
  - reject 后的补写 `tool_result / step_finish / persisted text`
  - confirm 后的 `_process_tool_calls(...)` 执行
  - 后续 resume prompt
  现在统一由 `SessionPromptProcessor._stream_permission_response(...)` 和 `_resume_processor(...)` 继续驱动，而不是外层函数重新拼第二套 resume orchestration
- 这轮之后，`agent_service.py` 在这块剩下的模块级 `_respond_native_action_impl()` 已经只是兼容薄壳，主要 ownership 回到 `SessionPromptProcessor`
- 本轮验证：
  - `python -m py_compile packages/ai/agent_service.py packages/ai/session_processor.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py -q`：`47 passed`
  - `python -m pytest tests/test_agent_permission_next.py -q`：`30 passed`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py -q`：`118 passed`

### Round 7 验证结论

- instance/project lifecycle 的 dispose ownership 继续向 OpenCode `Instance.reload()/dispose()/disposeAll()` 收平：
  - `session_lifecycle` 的目录级 prompt/session 状态现在通过 `Instance.state(..., dispose=_dispose_directory_sessions)` 跟随 instance state 一起回收
  - `session_instance.reload()/dispose()` 不再自己显式 drain `dispose_sessions(...)`，只负责发出 cooperative abort 并失效 directory cache / state cache
  - abort 标志也已从单纯挂在 `PromptSessionState` 上，改成附带独立 `_ABORTED_SESSION_IDS`，避免 instance dispose 后 cooperative abort 提前丢失
- callback promise / queued request cursor 进一步贴近 OpenCode：
  - busy 时排队的 prompt callback payload 不再只有 `session_id`，现在会携带显式 `request_message_id`
  - `_session_prompt_runtime(...)` 会优先恢复这个 cursor，但只有在它仍对应当前未完成的 assistant turn 时才采用；如果期间队列里已经出现更新的 pending user，则会自动回落到最新 `turn_state`
  - 这样既保留了恢复精度，又不破坏当前单 loop “catch up to latest pending turn”的运行语义
- permission object / native resume 也继续变薄：
  - `session_pending.native_pending_context(...)` 新增 `request_message_id`
  - native permission reply 的 `parent_id` 现在优先绑定 pending assistant 的 `parentID`，而不是再依赖 `get_latest_user_message_id(...)` 这条本地旁路
  - 这让 permission resume 和 OpenCode `PermissionNext.Request.tool.messageID -> current assistant turn` 的对应关系更直接
- 本轮验证：
  - `python -m py_compile packages/ai/session_lifecycle.py packages/ai/session_instance.py packages/ai/session_pending.py packages/ai/agent_service.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py -q`：`48 passed`
  - `python -m pytest tests/test_agent_permission_next.py -q`：`31 passed`
  - `python -m pytest tests/test_global_routes.py -q`：`7 passed`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py -q`：`127 passed`

### Round 8 验证结论

- provider/runtime 继续向 OpenCode `session/llm.ts + provider/transform.ts` 收平：
  - `llm_provider_stream.stream_openai_compatible(...)` 现在会在 LiteLLM transcript 已包含 tool history、但当前 turn 没有 active tools 时，自动补最小 `_noop` function tool，避免代理层因“历史里有 tool call 但本轮 tools 为空”直接拒绝请求
  - chat-completions 流式 tool call 现在会按当前 active tool 集合做一次大小写 repair；如果模型吐出 `Bash` 而 registry/tool exposure 里实际暴露的是 `bash`，会在流式层直接修正到可执行名字
- system prompt 主链已经去掉本地 adapter 噪音：
  - `_build_system_prompt_messages(...)` 不再注入 `Default to concise Simplified Chinese`、`Current mode is ...`、`User-selected skills ...` 这些本地产品层提示
  - native prompt 现在默认只保留 OpenCode 风格的三段结构：`provider prompt + environment prompt + skills prompt`
  - 这让 core prompt 更接近 `reference/opencode-dev/packages/opencode/src/session/system.ts`
- tool exposure 这一轮没有再引入新的本地规则层，仍沿用 Round 4 已完成的 registry-owned spec 结构；本轮主要把 provider stream 侧与这个 registry/exposure 结果的兼容行为补齐
- 本轮验证：
  - `python -m py_compile packages/integrations/llm_provider_stream.py packages/ai/agent_service.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py`
  - `python -m pytest tests/test_llm_client_stream.py tests/test_agent_prompt_lifecycle.py -q`：`55 passed`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py -q`：`160 passed`

### Round 9 验证结论

- assistant frontend 继续向 OpenCode `instance/session` 真源收平：
  - `frontend/src/features/assistantInstance/store.ts` 现在自己维护 conversation cache，不再把 `ConversationContext.activeConv/activeWorkspace` 当运行时事实源
  - active conversation 的 `workspace/title/session binding` 改为由 `AssistantInstanceSnapshot` 统一导出：`activeWorkspace`、`conversationTitle`、`activeSession`
  - 会话 metadata 仍然只做薄持久化和 sidebar 列表用途，assistant 页面本身不再直接依赖 `ConversationContext` 暴露的活动 conversation 对象来驱动工作区/标题/会话切换
- `frontend/src/contexts/AssistantInstanceContext.tsx` 现在只把 `metas + activeId + create/patchConversation` 这些最薄上下文同步给 store：
  - 创建新会话时会优先沿用 `snapshot.activeWorkspace`
  - 页面展示的工作区、标题、session id 都从 snapshot 读取，而不是再从 conversation context 拼装
- `frontend/src/pages/Agent.tsx` 的 assistant runtime 消费又薄了一层：
  - workspace server、workspace panel reset、terminal bootstrap 这些副作用现在以 `activeConversationId + activeWorkspace` 为依赖
  - 页面已经不再把 `activeConv.id/workspaceServerId/effectiveWorkspacePath` 作为主驱动键；`activeConv` 只剩少量论文标题文案场景
- 本轮验证：
  - `npm --prefix frontend run build`：通过
  - `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`：仍失败，但错误仍集中在仓库既有全局类型问题，当前包括 `InsightPanel.tsx`、`MermaidBlock.tsx`、`Sidebar.tsx`、`Agent.tsx`、`Collect.tsx`、`PaperDetail.tsx`、`Projects.tsx`、`Tasks.tsx`、`services/api.ts`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py -q`：`160 passed`

## 1. 目标

目标不是把 ResearchOS 变成另一个通用 coding agent，而是先把通用 runtime 底座严格拉到 `reference/opencode-dev/packages/opencode/src/*` 的能力层级，再把 ResearchOS 现有论文能力以扩展工具的方式挂回去。

本轮执行基线：

- 通用 runtime 以 OpenCode 的 `project -> instance -> session -> message/part -> permission -> provider -> prompt loop` 为顺序实现
- 未完成前不宣称“质量已完全达到 OpenCode”
- 通用层不主动发明 OpenCode 源码里没有的产品行为

## 2. 功能对齐表

| 模块 | OpenCode | ResearchOS 当前状态 | 本轮状态 | 说明 |
|---|---|---|---|---|
| Project 模型 | `project/worktree/sandbox` 一等公民 | 仅有 workspace_path/项目业务模型 | 已完成第一版 | 新增持久化 `agent_projects`，支持目录识别、Git 初始化、worktree/sandbox 记录 |
| Instance / Session 基座 | `Session.Info` + 按目录隔离实例 | 原生 agent 仅内存 session dict | 已完成第一版 | 新增持久化 `agent_sessions`，`agent_runtime_state` 改为数据库事实源 |
| Message / Part 存储 | `message + part` | 主聊天无后端结构化存储 | 已完成第一版 | 新增 `agent_session_messages` / `agent_session_parts`，支持按 session 取历史 |
| 旧聊天兼容层 | OpenCode 以 session runtime 为中心 | `/agent/chat` 直接喂 `stream_chat()` | 已完成第一版 | 旧 `/agent/chat` 已开始同步到新 session store |
| Session 状态 | `idle / busy / retry` | 无统一状态出口 | 已完成第一版 | 新增 `/session/status`，当前支持 `idle / busy / retry` |
| Abort | `session.abort` | 仅前端本地 stop | 已完成第一版 | 新增 `/session/{id}/abort` 最小链路，当前为 cooperative abort |
| Todo 持久化 | session 级 todo | 内存 dict | 已完成第一版 | `agent_runtime_state` 已改为数据库持久化 todo |
| PermissionNext | allow/deny/ask + pattern | allowlist + confirm | 已完成第一版 | 已接入规则评估、工具禁用、pending permission、`once/always/reject`、项目级持久化 allow |
| Revert / Fork / Diff / Summarize | 完整 | 无 | Fork / Revert / Diff / Summarize 已完成第一版 | 已新增 `session.fork`、`session.revert`、`session.unrevert`、`session.diff`、`session.summarize`；compaction history filter 与 replay 已接入第一版 |
| Prompt Processor / Bus / Instance 生命周期 | `prompt.ts + processor.ts + bus + status` | SSE wrapper + 函数式 loop | 已完成第一版 | 已新增 session bus、prompt instance ownership、`SessionPromptProcessor`、prompt/step lifecycle event 发布 |
| Assistant part / retry 生命周期 | `message-v2` / reasoning / retry | 只有纯文本流 | 已完成第一版 | 已接入 reasoning part、step token.reasoning、瞬态错误 retry、step 边界切分与 tool transcript 重建 |
| Provider 同构 | 多 provider/default small model/variant | CLI 适配 + native LLMClient | 进行中 | 已补 message/option transform 与 small options，当前继续把 schema/transform 从 `LLMClient` 单体中拆到 OpenCode 风格分层 |
| ResearchOS 论文能力回挂 | OpenCode 无 | 已有 | 未开始 | 需在通用 runtime 稳定后回挂 |

## 3. 本轮已完成

### 3.0 2026-03-21 增量收敛

本次继续收了两条最影响稳定性的链路：

- `SessionPromptProcessor`、permission resume、inline persistence 现在统一经过 `PromptEventStreamDriver -> SessionStreamPersistence.apply_event(...)`
- `text / reasoning / tool / usage / retry / step-finish / patch` 这些事件不再在 `_run_model_turn_events()`、`_process_tool_calls()`、`_emit_step_finish()` 等子流程里直接落库，而是先发事件，再由上层 driver 统一持久化
- 这让 native prompt 主链更接近 OpenCode `SessionProcessor.process()` 那种“单 loop 驱动 message/part mutation”的结构，processor/persistence 的边界又收薄了一层
- `SessionStreamPersistence.stream()` 现在主要只保留给 legacy/raw stream 兼容路径和直接测试辅助；主执行链已经不再依赖它做逐事件解释

本次还修了一个真实前端稳定性问题：

- `MermaidBlock` 现在优先加载 `mermaid/dist/mermaid.esm.mjs`，再回退到裸模块 `mermaid`
- 已做 dev server 真实浏览器 smoke，组件级挂载渲染成功，不再出现此前本地环境里 `dayjs default export` / Mermaid 动态模块加载失败导致的渲染报错
- 本地 smoke 产物：
  - `D:/Desktop/ResearchOS/tmp-mermaid-smoke.png`
  - `D:/Desktop/ResearchOS/tmp-mermaid-block-smoke.png`

### 3.1 新增的持久化 runtime 结构

- 新增 `agent_projects`
- 新增 `agent_sessions`
- 新增 `agent_session_messages`
- 新增 `agent_session_parts`
- 新增 `agent_session_todos`

落点：

- [models.py](/D:/Desktop/ResearchOS/packages/storage/models.py)
- [repositories.py](/D:/Desktop/ResearchOS/packages/storage/repositories.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)

### 3.2 新增的 OpenCode 风格路由

- `GET /project`
- `GET /project/current`
- `POST /project/init`
- `GET /session`
- `GET /session/status`
- `POST /session`
- `GET /session/{session_id}`
- `POST /session/{session_id}/abort`
- `GET /session/{session_id}/message`
- `POST /session/{session_id}/message`
- `DELETE /session/{session_id}/message/{message_id}`
- `DELETE /session/{session_id}/message/{message_id}/part/{part_id}`

落点：

- [session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py)
- [main.py](/D:/Desktop/ResearchOS/apps/api/main.py)

### 3.3 旧接口兼容层已切到新事实源

- `/agent/chat` 在流式回复时会把会话和消息写入新的 session store
- `/agent/conversations*` 已改读新 session/message store
- `agent_runtime_state.ensure_session/get_todos/update_todos` 已不再使用进程内 dict

落点：

- [agent.py](/D:/Desktop/ResearchOS/apps/api/routers/agent.py)
- [agent_runtime_state.py](/D:/Desktop/ResearchOS/packages/ai/agent_runtime_state.py)

### 3.4 PermissionNext 已完成第一版

已完成的核心点：

- 新增 `agent_permission_rules`
- 新增 `agent_pending_actions`
- 新增 OpenCode 风格 `allow / deny / ask` 规则集与 pattern 评估
- 按 ruleset 自动禁用不可用工具，避免模型看到确定不能用的工具
- 工具调用时按 permission + pattern + project boundary 判定
- 支持 `once / always / reject`
- `always` 会把 allow 规则落到 project 级持久化 store
- pending permission request 与 pending action continuation 现在会一起持久化
- 清空进程内 cache 后，`GET /session/{id}/permissions` 和确认续跑仍可恢复
- 权限暂停时会先持久化带 `tool.state.status=pending` 的 assistant message
- 确认/拒绝后会回到同一条 assistant message 上续写 tool result 与 `step-finish`
- 新增 `GET /session/{id}/permissions`
- 新增 `POST /session/{id}/permissions/{permission_id}`
- 旧 `/agent/confirm`、`/agent/reject` 已兼容新的 PermissionNext，并修复确认后续跑结果落库

落点：

- [permission_next.py](/D:/Desktop/ResearchOS/packages/ai/permission_next.py)
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- [agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py)
- [agent.py](/D:/Desktop/ResearchOS/apps/api/routers/agent.py)
- [models.py](/D:/Desktop/ResearchOS/packages/storage/models.py)
- [repositories.py](/D:/Desktop/ResearchOS/packages/storage/repositories.py)

### 3.5 Fork 已完成第一版

已完成的核心点：

- 新增 `POST /session/{id}/fork`
- 复制 source session 的 message / part 历史
- assistant `parentID` 会映射到 fork 后的新 user message id
- 支持按 `messageID` 截断 fork 范围
- fork 标题按 OpenCode 规则追加 `(fork #n)`

落点：

- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py)

### 3.6 Revert / Diff 已完成第一版

已完成的核心点：

- agent 工具执行链现在会把可回滚的文本文件修改记录为结构化 `patch` part
- 当前覆盖 `write_workspace_file`、`replace_workspace_text`、`write`、`edit`、`multiedit`
- 新增 `packages/ai/session_snapshot.py`，为本地工作区提供独立 git-backed snapshot 仓库
- `step-start / step-finish` 现在会记录 workspace snapshot
- 新增 `GET /session/{id}/diff`
- 新增 `POST /session/{id}/revert`
- 新增 `POST /session/{id}/unrevert`
- `session diff` / session summary / user summary 现在优先按 snapshot 边界生成，失败时才回退 legacy patch 聚合
- `revert` 现在优先按 snapshot patch 回滚，本地 shell/外部文件改动也可恢复
- `unrevert` 现在优先直接恢复 `revert.snapshot`
- 异常退出且没有 `step-finish` 时，也会补算当前 step 的 snapshot patch
- 在存在 `revert` 状态时，下一次 prompt 前会自动 cleanup 被回滚的 message/part
- snapshot patch part 会以 `type=patch + hash/files` 持久化
- session summary 与 user message summary 会基于 snapshot diff 或 patch diff 重算摘要
- SSH 工作区现在也支持 `revert/unrevert` 第一版：远程工具 patch 会保留远程绝对路径，回滚时通过 `remote_restore_file()` 写回远端文件
- `session diff` / `revert diff` 现在会按真实文件 identity 聚合：同一文件的 direct patch 与 snapshot diff 不会重复计数，跨多个 step 连续修改也会合并成“最早 before / 最新 after”

当前限制：

- 当前 snapshot 生命周期仍是本地第一版，不是 OpenCode `SessionProcessor + Snapshot` 的完整时序与远程实现

落点：

- [agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [session_snapshot.py](/D:/Desktop/ResearchOS/packages/ai/session_snapshot.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py)
- [repositories.py](/D:/Desktop/ResearchOS/packages/storage/repositories.py)
- [agent.py](/D:/Desktop/ResearchOS/apps/api/routers/agent.py)

### 3.7 Summarize / Compaction 已完成第一版

已完成的核心点：

- 新增 `POST /session/{id}/summarize`
- 新增 `packages/ai/session_compaction.py`，按 `reference/opencode-dev/packages/opencode/src/session/compaction.ts` 的语义写入：
  - compaction user message
  - assistant summary message
  - `mode: compaction` / `agent: compaction` / `summary: true`
- `load_agent_messages()` 已按 OpenCode 的 compaction 边界加载历史：
  - 不再把压缩前的全量历史和压缩后的摘要同时喂给模型
  - 当前会只保留最近一次已完成 compaction 之后的上下文
- `auto + overflow` 第一版已支持 replay user message 生成：
  - compaction 摘要只覆盖 replay 之前的历史
  - replay user message 会把最后一条真实 user prompt 重新挂回 session
- 现有 `agent_service` loop 已接入自动 compaction 第一版：
  - 新请求开始前会根据上一条 assistant 的 token 使用量做 preflight auto-compaction
  - 模型直接报 context overflow 时，会自动 compaction 后继续续跑
  - compaction 后最终 assistant 消息会重新绑定到 replay user message，避免 parentID 错位
- 现已补上更接近 OpenCode `prompt.ts` 的同轮 post-step auto-compaction：
  - step-finish 后会按本步 usage 判断是否需要自动 compaction
  - compaction 前会先把当前 assistant message checkpoint 落库，确保摘要能看到刚完成的 tool result
  - compaction 后会切到新的 assistant message 继续后续 model turn，避免把压缩前后的 part 混写进同一条 assistant message
- assistant 工具结果 part 已改为更接近 OpenCode 的结构化形态，后续历史装载会把工具结果摘要重新带回模型上下文
- assistant message 已新增 `step-start / step-finish` 第一版 part，用于记录当前 prompt loop 的 step 生命周期
- `wrap_stream_with_persistence()` 已新增中途 assistant checkpoint / rollover 第一版，用于给自动 compaction 提供必要的持久化切分点

当前限制：

- 当前自动 compaction 已覆盖 preflight、显式 context overflow、同轮 post-step usage overflow 三条主链路
- 当前只在发生 post-step auto-compaction 时补了 assistant checkpoint / rollover，不是 OpenCode `SessionProcessor` 那种所有 step / part 都逐步落库
- 已接入本地 `step-start / step-finish + snapshot` 第一版，但还没有 OpenCode `SessionProcessor` 的总线式逐步持久化生命周期
- 当前 compaction 仍基于本地 `LLMClient` 直接总结，不是 OpenCode 的 provider/model registry + prompt bus 架构

落点：

- [session_compaction.py](/D:/Desktop/ResearchOS/packages/ai/session_compaction.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py)
- [config.py](/D:/Desktop/ResearchOS/packages/config.py)
- [llm_client.py](/D:/Desktop/ResearchOS/packages/integrations/llm_client.py)

### 3.8 Reasoning / Retry 生命周期已完成第一版

已完成的核心点：

- `LLMClient.chat_stream()` 已新增 `reasoning_delta` 事件与 `reasoning_tokens`
- OpenAI Responses / OpenAI-compatible 流程现在会提取 reasoning 文本并透传到 agent loop
- `agent_service` 现在会在单轮内保留 `reasoning_content`，并在多 step prompt loop 中继续带回后续模型调用
- `wrap_stream_with_persistence()` 现在按 part 顺序落库，不再把所有正文粗暴压成单一 text part：
  - `step-start`
  - `reasoning`
  - `text`
  - `tool`
  - `retry`
  - `step-finish`
- assistant message 的 `tokens.reasoning` 与 `step-finish.tokens.reasoning` 已接通
- `load_agent_messages()` 现在会更接近 OpenCode `message-v2`：
  - assistant 历史按 `step-start` 边界切分
  - 保留 `reasoning_content`
  - 过滤只有 `step-start` 的空 assistant message
  - 已把持久化 `tool` part 重建成 `assistant.tool_calls + tool result` 历史，而不是退化为自然语言摘要
- `_run_model_turn()` 已接入第一版 transient error retry：
  - session status 会切到 `retry`
  - 延迟后自动续跑
  - 中止请求时会提前打断 retry sleep
- retry 现在也会持久化为结构化 `retry` part，而不是只体现在 session status 上
  - retry part 中保存的是原始上游错误归一化结果，不再写成“准备重试”的提示语
- assistant 最终错误对象已开始按 OpenCode 风格做第一版归类：
  - `AbortedError`
  - `AuthError`
  - `ContextOverflowError`
  - `APIError`
  - `UnknownError`
- aborted assistant 现在会显式落库 `error + finish=aborted`
- 中止时未完成的 tool call 不会丢失，最终会落成 `tool.state.status=error`
- OpenAI Responses 历史重放已开始写回：
  - `assistant` 文本
  - `function_call`
  - `function_call_output`
  这比之前只回放纯文本更接近 OpenCode 的工具历史语义

当前限制：

- 当前对 reasoning 的回放仍主要兼容 `openai-compatible` 风格的 `reasoning_content`
- 还没有 OpenCode provider transform 那套 provider-specific reasoning metadata / encrypted content / item reference 体系
- 还没有做到 OpenCode `SessionProcessor` 每一步落库再继续执行的总线式生命周期

落点：

- [llm_client.py](/D:/Desktop/ResearchOS/packages/integrations/llm_client.py)
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [session_retry.py](/D:/Desktop/ResearchOS/packages/ai/session_retry.py)
- [test_agent_session_runtime.py](/D:/Desktop/ResearchOS/tests/test_agent_session_runtime.py)
- [test_agent_session_retry.py](/D:/Desktop/ResearchOS/tests/test_agent_session_retry.py)

### 3.9 Prompt Processor / Bus / Instance 生命周期已完成第一版

已完成的核心点：

- 新增 `packages/ai/session_bus.py`，补上类似 OpenCode `Bus` 的进程内事件总线
- 新增 `packages/ai/session_lifecycle.py`，补上类似 OpenCode `SessionPrompt.state()` / `SessionStatus` 的 prompt ownership、abort、status、callback/waiter 基元
- `SessionStatus` 已从 `session_runtime.py` 的裸全局 dict 收口到独立 lifecycle 模块
- 新增 `SessionPromptProcessor`，让 native agent prompt loop 有了明确的 processor 边界，而不再只是外层函数式拼接
- `/session/{id}/message` 现在已补上 OpenCode `PromptInput.noReply` 的第一版语义：只写入 user message，不启动 prompt loop
- processor 现在会发布：
  - `session.prompt.started`
  - `session.prompt.finished`
  - `session.prompt.paused`
  - `session.prompt.cancelled`
  - `session.step.started`
  - `session.step.finished`
  - `session.error`
- message / part 持久化现在会同步发布：
  - `session.message.updated`
  - `session.part.updated`
- overflow compaction 现在会在 compaction 前显式 reset 瞬态 assistant message，并在 compaction 后 rollover 到新的 assistant message，避免压缩前 step-start 污染压缩后历史顺序
- bus 事件现已补到：
  - `session.message.removed`
  - `session.part.removed`
- `/session/{id}/abort` 现在优先走 managed prompt instance 的 cancel，再回退到 legacy abort 标志，兼容旧测试和 fake stream 场景
- prompt instance 已支持 owner acquire/release、waiter 注册与完成通知，开始接近 OpenCode `prompt.ts` 的 instance lifecycle
- 同一 session 的 queued prompt 现在会等待前一轮 prompt 彻底落库后，再从最新持久化历史重新装载并继续执行，比之前单纯阻塞锁更接近 OpenCode `resume_existing` 语义
- prompt instance 的 release / queued callback handoff 已从 `wrap_stream_with_persistence()` 收回 `SessionPromptProcessor`
- native prompt 的 `busy / handoff / idle / abort-clear` 现在也由 `SessionPromptProcessor` 驱动，persistence wrapper 对这条链路只保留持久化职责
- queued prompt 直接 handoff 时，session status 不会在中间误落回 `idle`，更接近 OpenCode 单 active loop 的生命周期
- 新增 `SessionStreamPersistence`，把原先 wrapper 内的大部分 SSE 持久化状态机收口成独立事件 sink
- native `SessionPromptProcessor` 现在会在内部直接驱动 `SessionStreamPersistence`，普通 session prompt 已不再依赖 `_persist_stream_if_needed()` 才能落库
- native main loop 的 `model turn / tool turn / step-finish / max-steps summary` 现在会直接调用 `SessionStreamPersistence` 的 message/part mutation 接口：
  - `text/reasoning` part
  - `tool input / tool result`
  - `retry`
  - `step-finish / patch`
  这条主链已不再依赖 `PromptEventStreamDriver` 解释 SSE 事件后再落库，更接近 OpenCode `processor.ts -> message-v2.ts` 的结构
- native permission confirm/reject resume 现在也会在 `agent_service.respond_action()` 内直接驱动 `SessionStreamPersistence`，不再依赖外层 wrapper 为确认后续跑落库
- native 未配置模型时的错误流也已切到 inline persistence，不再走 `_persist_stream_if_needed()` 托底
- CLI backend prompt stream 与 ACP confirm stream 也已切到 inline persistence；`agent_service` 运行时已不再实际依赖 `_persist_stream_if_needed()`
- `wrap_stream_with_persistence()` 与 inline persistence 现在都统一走 `SessionStreamPersistence.stream()`，agent/service 两侧不再各自维护一套 `start/consume/finalize` 包装
- 旧的 `_persist_stream_if_needed()` compatibility wrapper 已删除；相关回归现在直接断言 native/confirm/CLI 路径不会回退到 `wrap_stream_with_persistence()`
- `SessionPromptProcessor.stream()` 现在只负责 acquire/queue gating；真正的 active prompt 执行已收口到 `_stream_active()`
- queued callback worker 不再重走完整 `stream()` 入口，而是直接复用 `_stream_active()`；active path 和 queued-resume path 的执行边界开始统一
- `PersistedSSEStream` 现在会透传 `_researchos_prompt_control`，queued callback worker 能正确感知 `done / paused / error / cancelled`
- queued prompt 现在只会把 callback payload 挂进 queue；payload 内显式保存 `options / request_message_id / persistence / assistant_message_id / step_index`
- queued callback resume 已不再共享 live `SessionPromptProcessor` 对象；首条 callback 会从 payload 重建 runner，后续 callback 继续 rebinding 到同一个 runner
- `/agent/chat` 兼容路由现在也会显式把最新 user message id 作为 `request_message_id` 传给 processor；`stream_chat()` 仍保留 `persistence.parent_id` fallback，兼容旧调用方
- permission pause 现在不再释放 prompt owner；confirm/reject 会通过 `resume_existing=True` 接回同一个 active prompt，再继续处理后续 queued callback
- queued callback 的 stream / handoff helper 也已收口到 `SessionPromptProcessor`
- 旧的模块级 `_run_agent_loop()` 已删除，native prompt loop 现在只保留 processor-owned 实现
- queued callback handoff 进一步收口成“单 callback loop + 单 runner”模式：
  - 第一条 queued callback 先从自己的 payload 恢复 runner
  - 后续 callback 不再各自新建 processor，而是把 runtime state rebinding 到同一个 runner 后继续执行
- 当前 active owner prompt 在正常结束后，会直接在同一条执行链里进入 `_run_callback_loop()`：
  - owner thread 会直接内联 drain 下一条 queued callback
  - 显式 `_resume_queued_callbacks()` 也会直接 claim 同一个 callback loop，而不是再拉后台线程
  - `_start_queued_callback_worker()` / `_drain_queued_callback_worker()` 已删除
  - 这让本地 handoff 语义进一步逼近 OpenCode `loop(resume_existing)` 的“同一个 active loop 继续消费 callbacks”
- `PromptStreamControl` 现在已经收口 SSE 终态观察逻辑：
  - `_stream_active()` 与 callback loop 统一复用同一套 `observe()/absorb()` 规则解析 `assistant_message_id / pause / error / cancelled / done`
  - bus step/pause/error 事件与 callback terminal error 判定不再各自维护一份分支
  - `action_confirm` payload 也已并入 control，paused callback 在没有原始 SSE 缓冲时，仍能从保存的 `result/control` 重建 pause 输出
- prompt owner 的 handoff / finish 现在也改成原子语义：
  - `handoff_or_finish_prompt_instance()` 会在同一把 lifecycle 锁内决定“继续 handoff 下一条 callback”还是“真正释放 owner/resolve waiter”
  - `reject_callbacks_and_finish_prompt_instance()` 会原子 drain 仍在队列中的 callback，再一起 finish owner
  - 这补掉了 queued callback 可能卡在“队列检查已空，但 owner 还没 release”窗口里的竞态
- native `respond_action()` 的 confirm/reject resume 现在也不再单独维护一套 lifecycle/persistence 分支：
  - `ActionResumeControl` 已删除
  - native permission resume 改为直接返回带 `PromptStreamControl` 的流，再交给统一 inline persistence 处理
  - confirm 后如果再次触发 `action_confirm`，session status 会继续保持 `busy`，不再被本地 wrapper 误落回 `idle`

当前限制：

- 目前的 bus 还是进程内事件总线，没有 OpenCode `GlobalBus` / 跨实例广播
- 当前 prompt instance 已不再保留后台 worker handoff；native queued resume 已收口成单 callback loop + callback outcome promise 第一版
- 与 OpenCode 仍有差异：
  - queued callback 仍会按各自 `request_message_id` rebinding processor 状态，而不是完全在同一个 `loop()` 的 history scan 中直接 resolve `callbacks[]`
  - HTTP 路由层仍要把 resolved/rejected callback 结果回放成 SSE，不是 OpenCode 那种纯 promise 返回消息对象
- fake stream、测试注入流与直接 `wrap_stream_with_persistence()` 调用仍保留 wrapper 适配层
- `message/part removed` 已补上，但 callback/resume 与外部兼容流仍保留 event wrapper 适配层，还没完全压平到 OpenCode 单一 processor/message-v2 写路径

### 3.10 Streaming Part Delta 已完成第一版

已完成的核心点：

- `session_bus` 已新增 `session.part.delta`
- `wrap_stream_with_persistence()` 现在会为流式 `reasoning/text` part 提前分配稳定 `partID`
- 流中首次收到 `reasoning_delta/text_delta` 时会先发布对应的 `session.part.updated` 占位事件
- 后续每个 chunk 会继续发布 `session.part.delta`
- 最终 assistant message 落库时会沿用同一个 `partID`，因此 bus 中的 delta 事件已可和最终持久化 part 一一对齐

当前限制：

- 当前 `part delta` 已覆盖流式 `text/reasoning` 与 `tool input raw`，但还没有扩展到更多 tool/result 细粒度字段
- 当前仍然是 wrapper 聚合后统一落库，不是 OpenCode `SessionProcessor` 的每个 part 逐步写库

落点：

- [session_bus.py](/D:/Desktop/ResearchOS/packages/ai/session_bus.py)
- [session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py)
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py)

### 3.11 Queued Prompt FIFO / Resume Handoff 已完成第一版

已完成的核心点：

- `session_lifecycle` 不再在上一轮 prompt 结束时把所有 waiter 一次性放开
- 现在改成更接近 OpenCode callback queue 的 FIFO handoff：
  - 只释放队首 waiter
  - 为队首 waiter 建立 reservation
  - 非队首请求不能抢占下一轮 prompt owner
- queued prompt 现在会按自己的 `user message id` 重新装载历史，而不是盲目读取“当前最新全量历史”
- 这修复了多条排队 prompt 时前面的请求读到后面 user prompt、导致续跑串位的问题
- `cancel_prompt_instance()` 现在也会清理 queue / reservation，并拒绝仍在等待的 queued prompt

当前限制：

- 这仍然不是 OpenCode `prompt.ts` 那种“同一个 loop 持续消费 callbacks 并统一 resolve 最终 assistant”的完整架构
- 由于当前仍是按 HTTP SSE request 驱动，每个 queued prompt 还是会在拿到 owner 后单独进入一次 native processor，而不是完全复用前一轮 loop

### 3.12 Generic Assistant Step Rollover 已完成第一版

已完成的核心点：

- 不再只在 post-step compaction 时才做 assistant checkpoint / rollover
- 现在普通 tool step 结束后也会：
  - 先持久化当前 assistant message，`finish=tool-calls`
  - 再切到新的 assistant message 继续下一轮 model turn
- 这让 native runtime 更接近 OpenCode `prompt.ts` 的“每个 model turn 一个 assistant message”语义
- continuation turn 现在会从持久化历史中读取上一条 assistant 的 tool transcript，而不是把多个 step 混在同一条 assistant message 内

当前限制：

- 当前 checkpoint / rollover 仍由 SSE persistence wrapper 驱动，不是 OpenCode `SessionProcessor` 在流内逐 part 持久化
- reasoning metadata、message-v2 provider transform 仍未完全内聚到 processor 内

### 3.13 Incremental Part Persistence 已完成第一版

已完成的核心点：

- `session_runtime` 现在不再只依赖最终 `persist_assistant_message()` 聚合写库
- 流式过程中这些 part 已开始增量 upsert 到数据库：
  - `text`
  - `reasoning`
  - `tool`
  - `retry`
  - `step-start`
  - `step-finish`
  - `patch`
- `repositories.py` 已补上 `AgentSessionPartRepository.upsert()`，并把 `replace_for_message()` 调整为真正的 replace/upsert 语义，避免和流式增量写库冲突
- 现在在 stream 尚未结束时，`list_session_messages()` 已能读到中途产生的 assistant part

当前限制：

- 当前增量写库仍由 `wrap_stream_with_persistence()` 驱动，不是 OpenCode `SessionProcessor.process()` 在 provider stream 内直接写库
- 当前 `part delta` 事件已发布，但数据库仍是写入当前完整 part 内容，不是单独存 delta log
- message meta（例如 final error / completed / finish）仍以 step 边界和最终收口为主，不是完全事件驱动

### 3.14 Processor Content Lifecycle / Stable Part Identity 已完成第二版

已完成的核心点：

- `_run_model_turn()` 现在会显式发出更接近 OpenCode `processor.ts` 的内容生命周期事件：
  - `reasoning-start`
  - `reasoning_delta`
  - `reasoning-end`
  - `text-start`
  - `text_delta`
  - `text-end`
- `tool-input-start`
- `tool-input-delta`
- `tool-input-end`
- 上述 start / delta / end 事件现在都会携带稳定的 `partID`
- `wrap_stream_with_persistence()` 现在优先消费这些显式边界事件，而不是主要依赖“delta 类型切换时猜测 flush”
- `text/reasoning` part 现在会保留 `time.start/end`
- tool input lifecycle 现在会先把 pending tool part 增量落库，再进入：
  - `action_confirm`
  - `tool_start`
  - `tool_result`
- permission pause 前的 pending tool part 现在会保留：
  - `state.input`
  - `state.raw`
- assistant message / part 合并现在会按 `part.id` 与 `tool.callID` 做真正的 upsert/dedup，而不是简单 append
- permission pause/resume 场景下，续跑会优先复用已持久化的 pending tool part，避免确认后生成重复 tool part

当前限制：

- 显式内容生命周期虽然已经补齐，但 part 持久化主导权仍在 wrapper，不是 OpenCode `SessionProcessor` 直接写库
- 当前 `tool-input-delta` 仍只是把完整 raw 参数串增量落到 `tool.state.raw`，还不是 OpenCode provider stream 那种 token 级输入增量处理
- `callbacks + resume_existing loop` 仍未做成 OpenCode 那种单 loop 统一消费 callback 的结构

### 3.15 Queued Prompt Callback / Auto Resume 已完成第一版

已完成的核心点：

- native runtime 的 queued prompt 现在不再使用“等待前一轮完成后，由当前 HTTP 请求线程自己重新 acquire owner 再跑一遍 processor”的旧模式
- `session_lifecycle` 已新增 prompt callback queue，busy 时的 prompt 请求现在只注册 callback 并等待自己的回包流
- 当前 prompt 完成后，runtime 会自动拉起下一条 queued callback，对应请求只消费它自己的 SSE 流
- 这让 native runtime 更接近 OpenCode `prompt.ts` 的核心语义：
  - queued 请求本身不驱动 active processor
  - 由 runtime 在前一轮完成后自动 `resume` 下一条请求
  - callback 仍按 FIFO 顺序执行
- callback queue 与现有 persistence wrapper 已打通：
  - queued 请求仍会通过自己的 `wrap_stream_with_persistence()` 完成持久化
  - tool / reasoning / text / step lifecycle 的现有持久化与 delta 语义不回退
- 现已进一步收口成单个 handoff worker 连续排空 callback queue，不再为每条 queued callback 递归创建下一层 handoff 线程
- 当前 prompt 失败时，待处理 callback 会被统一拒绝，并透传前一轮真实错误；permission pause 时则不会提前消费 queued callback，而是继续等待当前会话解除暂停
- 现已补上 prompt control 透传与 queued pause 边界：如果某条 queued callback 自己进入 `action_confirm` pause，后续 queued prompt 不会被提前消费
- 现已补上 deterministic 回归，直接锁住：
  - queued callback resume 从 callback payload 重建 processor，不再共享 live processor 实例
  - queued callback drain 只在第一条上取 runner，后续 callback 复用同一个 runner rebinding
  - 缺失 runtime options 的 malformed callback 会被直接拒绝，不再错误继承上一条 callback 的运行态
  - `/agent/chat` 兼容路由会把最新 user message id 显式透传给 processor request cursor
  - paused callback 会把后续 callback 留在队列里，并继续占有 active prompt owner
- `PromptCallback` 现在在 close 时会保存 `result/control`：
  - callback 不再只有原始 SSE item 缓冲，还会持有本次 drain 的明确终态
  - 这让 queued callback 更接近 OpenCode `resolve/reject` 的对象语义，后续继续压 lifecycle 时不必再反向猜测终态
- `PromptCallback` 现已进一步补上 `resolve()/reject()`：
  - queued worker 与 reject path 不再直接写 `close(error=...)`，而是按 callback 终态显式 resolve/reject
  - `_iter_callback_stream()` 现在优先根据 callback 保存的 `control/result` 补齐尾部 `error/done`，不再只依赖裸 `error` 字符串
- `PromptCallback` 现在还补上了显式终态与等待能力：
  - callback 会明确区分 `pending / resolved / rejected`
  - 新增 `wait_closed()`，后续继续向 OpenCode `callbacks.push({ resolve, reject })` 靠时，不必再把“已结束”语义绑死在 SSE 消费上
  - 顺手修掉了 `resolve(error=None)` 会把 `'None'` 串进 callback.error 的状态污染问题
- callback worker 的推进也已补上原子 `advance`/finish 回归：
  - worker 排空队列后会先走 `handoff_or_finish_prompt_instance()`，而不是先 release 再由外层补 finish
  - active prompt 正常完成、queued worker 正常排空、error drain 三条路径现在都共享同一种 owner 收尾语义
- `session_lifecycle` 现在还增加了 callback worker 所有权：
  - 同一 session 同时只允许一个 queued-drain worker 活跃
  - 后续重复触发 `_resume_queued_callbacks()` 时，不会并行拉起第二个 worker 抢消费 callback queue
  - 这让本地 queued callback drain 更接近 OpenCode 的“单 active loop / 单消费方”语义
- callback stream 的尾部现在也能从保存下来的终态重建：
  - 即使原始 SSE item 缓冲里没有完整的 `assistant_message_id / done` 尾巴，`_iter_callback_stream()` 也会依据 callback 保存的 `result/control` 补齐
  - 这进一步降低了本地实现对“原始 SSE 尾事件必须完整存活”的依赖，更接近 OpenCode callback resolve 后再交付最终消息的思路
- prompt / callback / owner completion result 现在不再只有 `messageID`：
  - 如果 session store 里已经有对应 assistant message，completion result 会附带完整 `message` payload
  - 这样 callback / waiter 的完成值开始逼近 OpenCode `resolve(final assistant message)` 的语义，而不是只返回一个本地 ID
- resolved callback 现在在没有 raw SSE items 时，也能直接从 `result.message` 回放最终 assistant 内容：
  - 会重建 `assistant_message_id`
  - 会按持久化 text part 逐条回放 `text_delta`
  - 然后补 `done`
  - 这意味着 callback 交付已经开始真正依赖 resolved message，而不只是依赖原始流缓存
- 如果 callback 只保留了部分 raw SSE（例如只有 `assistant_message_id` 而没有正文），现在也会从 `result.message` 继续补回缺失的 `text_delta`
  - 这样 callback 响应端不再要求“正文必须来自原始流缓冲”
  - resolved message 已经开始成为 callback 回放的主事实源
- callback reject / exception 的尾部现在也主要依赖 callback 终态重放：
  - `_reject_queued_callbacks()` 与 queued callback exception path 不再必须手工往 `items` 缓冲里塞 `error/done`
  - `_iter_callback_stream()` 会根据 `outcome/control/result` 统一补齐 reject/error/done
  - 这让 callback 的权威状态进一步从“buffered SSE items”转向“resolved/rejected outcome”，更接近 OpenCode callback promise 语义

当前限制：

- 这仍然不是 OpenCode 那种“同一个 active loop 连续消费后续 callback 并最终统一 resolve”的完整单 loop 结构
- 当前自动 resume 虽然已由单个 handoff worker + 单 runner 连续驱动，但仍是“worker 逐条 rebinding queued 请求状态”，而不是 OpenCode 那种直接在同一个 loop 内部消费 callbacks[]
- queued callback 仍依赖 HTTP SSE request 存活来消费结果，不是 OpenCode 那种纯 promise/Bus 解析模型

### 3.16 Provider Message Transform 已完成第一版

已完成的核心点：

- `LLMClient` 已开始对齐 OpenCode `ProviderTransform.message()` 的 provider 入参清洗逻辑
- 现在在 chat/completions 调用前会按 provider/model 做第一版 message normalization：
  - Anthropic/Claude 路径会过滤空消息
  - Claude 路径会规范化 `tool_call_id`
  - Mistral/Devstral 路径会把 `tool_call_id` 规范到 provider 可接受格式
  - Mistral 的 `tool -> user` 非法序列现在会自动插入一个中间 assistant `"Done."`，避免 provider 拒绝请求
- 这让 native runtime 在多 provider 场景下更接近 OpenCode 的稳定性策略，而不是把内部 transcript 直接原样透传给 provider

当前限制：

- 当前只补了最关键的 message normalization；provider option transform 已在下一节补上第一版，但还没有做到 OpenCode `providerOptions()` / namespace remap / small model 的完整同构
- 还没有接入 OpenCode 那套更完整的 caching/providerOptions namespace remap/interleaved reasoning providerOptions 体系
- 目前仍主要覆盖 OpenAI-compatible / Anthropic 路径，没有完整扩展到更多 provider SDK 语义

### 3.17 Provider Option Transform 已完成第一版

已完成的核心点：

- `LLMClient` 已开始对齐 OpenCode `ProviderTransform.options()` 的 provider/model 默认选项
- OpenAI / GPT-5 路径现在会更接近 OpenCode 默认值：
  - official OpenAI target 默认 `store=false`
  - native session stream 现在会向 official OpenAI 传递 session 级 `prompt_cache_key`
  - GPT-5 默认 `reasoning effort=medium`
  - GPT-5/Codex chat fallback 现在会带 `reasoning_summary=auto`
  - GPT-5 Responses 路径现在会带 `reasoning.summary=auto`
  - GPT-5 路径会继续请求 `reasoning.encrypted_content`
  - 非 chat 的 `gpt-5.x` Responses 路径会设置 `text.verbosity=low`
- OpenAI-compatible / Gemini 路径现在不再简单复用 `reasoning_effort`：
  - 改为按 OpenCode 语义写入 `extra_body.google.thinking_config`
  - 默认会开启 `include_thoughts`
  - `gemini-3` 默认 `thinking_level=high`
  - `gemini-2.5` 的 `high/max` 已映射到 budget 档位
- Zhipu OpenAI-compatible 路径现在会自动带：
  - `thinking.type=enabled`
  - `thinking.clear_thinking=false`
- DashScope / 百炼 OpenAI-compatible 路径现在会更接近 OpenCode `enable_thinking` 语义：
  - 对 reasoning 模型族默认补 `enable_thinking=true`
- OpenCode `temperature/topP/topK` 里明确写死的模型族参数，已在 native `LLMClient` 第一版补齐：
  - `qwen`
  - `gemini`
  - `glm-4.6 / glm-4.7`
  - `minimax-m2`
  - `kimi-k2`
- raw HTTP fallback 不再绕过 provider transform：
  - `responses` raw HTTP
  - `chat/completions` raw HTTP
  现在都会复用同一套 option builder
- provider transform 当前已开始从 `LLMClient` 单体里外提，新增：
  - `packages/integrations/llm_provider_schema.py`
  - `packages/integrations/llm_provider_transform.py`
  这让 provider schema / transform / transport 的边界开始逼近 OpenCode `provider/schema.ts + provider/transform.ts + provider/provider.ts`

当前限制：

- Gemini 等 provider 目前仍通过 OpenAI-compatible transport 对齐语义，不是 OpenCode 那种原生 provider SDK

补齐进展：

- OpenRouter OpenAI-compatible 目标现在也会像 OpenCode 一样附带 `prompt_cache_key=sessionID`
- Venice OpenAI-compatible 目标现在也会附带 `promptCacheKey=sessionID`
- 这两条链路已同时覆盖：
  - `responses`
  - `chat/completions`

### 3.18 Callback Resume Existing / Owner Reuse 已完成第一版

已完成的核心点：

- queued callback 续跑现在不再走“上一轮 prompt finish 后释放 owner，再由下一轮 callback processor 重新 acquire”的旧路径
- `SessionPromptProcessor` 已支持 `resume_existing`：
  - callback auto-resume 时会直接复用当前活跃 prompt instance
  - 如果活跃 owner 意外丢失，会显式报错，而不是静默退化成新的 acquire
- `wrap_stream_with_persistence()` 现在只有在这些场景下才真正 `finish_prompt_instance()`：
  - 当前 prompt pause
  - 当前 prompt error
  - callback queue 已空
- 如果当前 prompt 正常完成且仍有 queued callback：
  - runtime 会先启动下一轮 `resume_existing` callback processor
  - session status 会保持 `busy`
  - owner 会一直保留到最后一条 queued callback 结束
- 这让 native runtime 更接近 OpenCode `prompt.ts` 的 `resume_existing` 语义：
  - queued callback handoff 不再经过 owner release/reacquire
  - callback 之间的 active prompt 生命周期现在更连续

当前限制：

- 这仍然不是 OpenCode `loop()` 内部直接消费 `callbacks[]` 的完整单 loop 架构
- 当前仍然不是 OpenCode 的原生 callback promise/loop 结构；ResearchOS 这里只做到“首条 callback 建 runner，后续 callback 复用同一 runner 重绑状态”
- callback 现在虽然已经保存 `result/control`，completion result 也开始携带完整 assistant message，并能在必要时从 resolved message 或 rejected outcome 回放结果，但外层交付仍依赖 HTTP SSE request 存活，不是 OpenCode 那种纯 promise resolve

### 3.19 OpenAI Responses Reasoning Metadata Persistence / Replay 已完成第一版

已完成的核心点：

- `LLMClient` 现在会按 OpenCode `openai-responses-language-model.ts` 的语义，从 official OpenAI Responses 输出中提取：
  - `openai.itemId`
  - `openai.reasoningEncryptedContent`
- 如果 Responses 返回了 `reasoning` item 但没有 summary 文本，native runtime 现在也会像 OpenCode 一样保留一个空 reasoning part，用于下一轮 continuity replay
- `_run_model_turn()` 现在会把 reasoning part 的 stable `part_id + metadata` 一路透传到 SSE：
  - `reasoning-start`
  - `reasoning_delta`
- `wrap_stream_with_persistence()` 现在会把 reasoning metadata 真正落到 session `part.metadata`
  - 空 text 的 reasoning metadata part 不再被丢弃
  - `load_agent_messages()` 会把它们重建成 `reasoning_parts`
- `_build_responses_input_from_messages()` 现在会按 OpenCode `convert-to-openai-responses-input.ts` 的 `store=false` 语义回放：
  - `type: "reasoning"`
  - `id`
  - `encrypted_content`
  - `summary[]`
- 同一个 OpenAI reasoning item 的多段 summary text 现在会按 `itemId` 合并回同一个 replay item，而不是被拆散成多个无关联消息

当前限制：

- 如果 official OpenAI Responses 失败并退回到 chat-completions / raw HTTP fallback，这条 reasoning metadata continuity 链路仍然不可用

### 3.20 OpenAI Responses Assistant Text / Tool-call Item Metadata Replay 已完成第一版

已完成的核心点：

- `_extract_responses_output_parts()` 现在会像 OpenCode `openai-responses-language-model.ts` 一样，把 assistant `message.id` 回写为 text part 的 `openai.itemId`
- `_extract_responses_tool_calls()` 现在会从 official OpenAI Responses `function_call.id` 提取 provider metadata，并随 `tool_call` 事件透传
- `_run_model_turn()` 现在会保留 assistant tool call 的 metadata，不再只保留 `id / name / arguments`
- `wrap_stream_with_persistence()` 现在会把 tool call metadata 落到 session `tool part.metadata`
  - `tool_start`
  - `tool_result`
  - `action_confirm`
  这三条链都会保留 `openai.itemId`
- `load_agent_messages()` 现在会把持久化的 `tool part.metadata` 重建到 assistant `tool_calls[].metadata`
- `_build_responses_input_from_messages()` 现在会按 OpenCode `convert-to-openai-responses-input.ts` 的语义回放：
  - assistant `text` item `id`
  - assistant `function_call` item `id`

当前限制：

- 当前已补齐的是 official OpenAI Responses 的 assistant text / reasoning / tool-call `itemId` continuity；provider-executed builtin tool 的 lifecycle / replay 已在下一节补上第一版，但 native runtime 目前实际请求仍默认 `store=false`
- 如果 official OpenAI Responses 失败并退回到 chat-completions / raw HTTP fallback，这条 assistant item metadata continuity 链路仍然不可用

### 3.21 Revert / Unrevert Busy Guard 已完成第一版

已完成的核心点：

- `session_lifecycle` 现已补上更接近 OpenCode `SessionPrompt.assertNotBusy()` 的 busy 判断原语：
  - `is_prompt_busy()`
  - `assert_prompt_not_busy()`
- `revert_session()` / `unrevert_session()` 现在会在真正修改工作区前先断言当前 session 不处于 busy
- busy 判定当前覆盖：
  - active prompt instance
  - queued waiter / reservation
  - pending callback queue
- `/session/{id}/revert` 与 `/session/{id}/unrevert` 已沿用既有 `RuntimeError -> HTTP 400` 映射，因此活跃 prompt 期间会直接返回 `session is busy`
- 这与 OpenCode `session/revert.ts` 在 core 层先做 `SessionPrompt.assertNotBusy()` 的语义保持一致，而不是只靠路由层做特判

当前限制：

- 当前仍然是 ResearchOS 本地 runtime 的 busy lifecycle，不是 OpenCode 的完整跨实例 `GlobalBus` / server 进程协同实现

### 3.22 Session Message / Part Delete 路由已完成第一版

已完成的核心点：

- 新增 `DELETE /session/{id}/message/{message_id}`
- 新增 `DELETE /session/{id}/message/{message_id}/part/{part_id}`
- message delete 现在会像 OpenCode 路由一样在 HTTP 入口先断 busy session，并返回 HTTP 400
- part delete 现在会：
  - 删除目标 part
  - 重算 message 的文本 content
  - 发布 `session.part.removed`
  - 发布更新后的 `session.message.updated`
- 这让 ResearchOS session runtime 开始具备 OpenCode `session.deleteMessage / part.delete` 的基础编辑能力

补充加固：

- queued callback 在 assistant message 首次初始化时，session row 查找现在会做轻量重试，减少并发 request / callback handoff 下的瞬态 `session not found`

### 3.23 OpenAI Responses Provider-executed Builtin Tool Lifecycle 已完成第一版

已完成的核心点：

- `LLMClient._extract_responses_tool_calls()` 现在会按 OpenCode `openai-responses-language-model.ts` 的语义，识别 official OpenAI Responses 的 provider-executed builtin tool output：
  - `web_search_call`
  - `computer_call`
  - `file_search_call`
  - `code_interpreter_call`
  - `image_generation_call`
- 这些 builtin tool output 现在会透传为：
  - `tool_call(provider_executed=true)`
  - `tool_result(provider_executed=true)`
- native agent prompt loop 现在不会再把这类 provider-executed builtin tool 误当成需要本地执行的函数工具
- `_run_model_turn()` 现在会：
  - 把 provider-executed builtin tool 保留在 assistant `tool_calls`
  - 立即发出对应 `tool_result`
  - 生成后续内存态 `tool` message，供同轮后续上下文继续使用
- `wrap_stream_with_persistence()` 现在会把 `providerExecuted` 真正落到 session `tool part`
- `load_agent_messages()` 现在会把持久化的 builtin tool transcript 重建为：
  - assistant `tool_calls[].provider_executed=true`
  - tool message `provider_executed=true`
- `_build_responses_input_from_messages()` 现在开始按 OpenCode `convert-to-openai-responses-input.ts` 的语义处理 provider-executed builtin tool replay：
  - `store=false` 时跳过 builtin tool transcript
  - `store=true` 时把 builtin tool result 回放为 `item_reference`
- 同时补上了 `store=true` 下 OpenAI reasoning item 的 `item_reference` replay 第一版

当前限制：

- native runtime 当前发起 official OpenAI Responses 请求时仍默认 `store=false`，因此 builtin tool 的 `item_reference` replay 目前主要体现在 transform 层与测试，而不是生产调用默认路径
- `local_shell` 的 actual execution / replay continuity 已在下一节补齐，但目前 runtime 注入的 official builtin tool 仍只覆盖 `openai.local_shell` 与 `openai.web_search`
- 如果 official OpenAI Responses 失败并退回到 chat-completions / raw HTTP fallback，这条 provider-executed builtin tool continuity 链路仍然不可用

### 3.24 OpenAI Responses Local Shell / Response Metadata Continuity 已完成第一版

已完成的核心点：

- `_build_responses_input_from_messages()` 现在开始按 OpenCode `convert-to-openai-responses-input.ts` 的语义处理 `local_shell`：
  - assistant `tool_calls[].function.name == local_shell` 时回放为 `local_shell_call`
  - tool message `name == local_shell` 时会优先解析为 `local_shell_call_output`
  - 解析失败时才回退为普通 `function_call_output`
- native runtime 现在会把 official OpenAI Responses 的 message-level provider metadata 持久化到 assistant message meta：
  - `openai.responseId`
  - `openai.serviceTier`（如果响应提供）
- `LLMClient._chat_stream_openai_responses()` 现在会把 response-level metadata 通过 `usage.metadata` 透传到 agent/runtime
- `wrap_stream_with_persistence()` 现在会把 usage metadata 写回 assistant message `providerMetadata`
- `load_agent_messages()` 现在会把持久化的 message-level provider metadata 重建到 assistant message：
  - `provider_metadata`
- `_normalize_messages()` 与 `_build_assistant_message()` 已打通这条 message-level metadata continuity，确保同一轮 prompt 内存态历史也不会丢失
- `LLMClient` 现在已补上更接近 OpenCode 的 `previous_response_id` 第一版接线：
  - 当 Responses 请求显式 `store=true` 且上一条 assistant message 带有 `openai.responseId` 时
  - 会自动把该值写入 `previous_response_id`

当前限制：

- 由于 native runtime 当前默认仍是 `store=false`，`previous_response_id` 这条链路目前主要体现在 transform 层和测试，不是默认线上路径
- 当前只补了 message-level `responseId/serviceTier` continuity，还没有把更多 response-level provider metadata 体系完全补齐
- provider-defined builtin tool 的 runtime 注入与 `local_shell` 执行链已在下一节补齐，但当前仍未把 `file_search` / `code_interpreter` / `image_generation` 等 builtin tool 真正接入 native runtime

### 3.25 Official OpenAI Responses Builtin Tool Exposure / Local Shell Execution 已完成第一版

已完成的核心点：

- `agent_service._build_turn_tools()` 现在会在 official OpenAI target 上，按 OpenCode `provider-defined` 语义补下发：
  - `openai.local_shell`
  - `openai.web_search`
- builtin tool 注入不是无条件开启，而是复用 native runtime 当前已暴露的函数工具集合：
  - 只有当前 turn 原本允许 `bash` 时，才额外附加 `openai.local_shell`
  - 只有当前 turn 原本允许 `search_web/websearch` 时，才额外附加 `openai.web_search`
  - SSH / remote workspace 当前不会附加 `openai.local_shell`，避免把本地 shell 误暴露给远程工作区场景
- tool exposure 现在已进一步收敛为稳定核心集：
  - `get_openai_tools()` / `build_turn_tools()` 不再接受 `user_request` 驱动的动态扩展入口
  - 当前工具集合只由 registry、workspace 边界、permission 与消息级 `tools` override 决定，更接近 OpenCode `resolveTools()` 的静态暴露语义
  - 默认 core set 现在已补齐更接近 OpenCode 的 `list / multiedit / apply_patch / skill`
  - `list_local_skills` / `read_local_skill` / `ls` 仍保留为兼容 handler，但不再占用默认模型工具暴露面
  - 新的 `skill` 工具会输出 OpenCode 风格的 `<skill_content name="...">` 包装内容，并附带 skill 目录与采样文件列表
  - `apply_patch` 现在也已补上 OpenCode 风格的 patch 解析、move/update/add/delete 执行链，并能把 patch 里的路径提前暴露给 permission 层做 `edit` 判定
- `permission_next` 现在已把 `local_shell` 当作 `bash` 权限处理：
  - `tool_permission(local_shell) -> bash`
  - `authorize_tool_call()` 会从 `action.command[]` 生成 command pattern
  - `ask/allow/deny`、project rule 与 command allowlist 现在都能覆盖 `local_shell`
- `workspace_executor` 现在已补上 OpenCode 风格的 local shell 执行基元：
  - `normalize_local_shell_command_parts()`
  - `local_shell_command_to_string()`
  - `build_local_shell_command()`
  - `run_local_shell_command()`
- `agent_tools` 现在已新增真实 `local_shell` handler：
  - 支持 `action.type=exec`
  - 支持 `command[]`
  - 支持 `workingDirectory`
  - 支持 `env`
  - 支持 `timeoutMs`
  - 返回结果会把 stdout/stderr 聚合到 `data.output`，从而和前面已完成的 `local_shell_call_output` replay continuity 对齐
- native prompt loop 现在已经能完整走通：
  - model 发出 `local_shell` tool call
  - runtime 本地执行
  - 持久化 tool transcript
  - assistant message rollover
  - 后续 model turn 读取 tool result 继续回答

当前限制：

- 当前 official OpenAI Responses builtin tool runtime 注入只补了 `openai.local_shell` 与 `openai.web_search`
- `file_search` / `code_interpreter` / `image_generation` 仍停留在 transform continuity 层，尚未像 OpenCode 一样从 native agent runtime 真正下发与消费
- 如果 official OpenAI Responses fallback 到 chat-completions / raw HTTP 路径，builtin tool transport 仍不会生效

### 3.26 User parts / tools continuity 已完成第一版

已完成的核心点：

- `POST /session/{id}/message` 现在接受更接近 OpenCode `PromptInput.parts` 的 user part 字段：
  - `text/content`
  - `mime/url/filename/source`
  - `synthetic/ignored`
- prompt 路由现在接受 `tools: Record<string, boolean>`，并把它持久化到 user message meta
- prompt 路由不再强制“至少一段 text”，file-only prompt 现在可以直接入库
- `load_agent_messages()` / `build_agent_messages()` 现在会保留 user `file` part，而不是统一退化为占位字符串
- user message 历史现在会继续带回：
  - `tools`
  - `system`
  - `variant`
  - `format`
- native `agent_service` 现在会保留结构化 user content，不再在 normalize 阶段强制 `str(...)`
- turn tool 选择现在会读取最后一条 user message 的 `tools`，按消息级别禁用工具，行为更接近 OpenCode `input.user.tools`
- OpenAI Responses 输入转换现在支持 user `text/file` parts：
  - `text -> input_text`
  - `image/* -> input_image`
  - `application/pdf -> input_file`
  - `text/*` / `application/json|xml|yaml` 的本地或 data URL 文件会转成 `input_text`
- OpenAI chat/completions 输入转换现在支持 user `text/image` 多段内容；不支持的 file 类型会安全退化为文本
- Anthropic fallback / pseudo fallback / CLI transcript 现在会正确渲染结构化 user content，而不是输出 Python/JSON 字面量

落点：

- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py)
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- [llm_client.py](/D:/Desktop/ResearchOS/packages/integrations/llm_client.py)
- [test_agent_session_runtime.py](/D:/Desktop/ResearchOS/tests/test_agent_session_runtime.py)
- [test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py)
- [test_agent_permission_next.py](/D:/Desktop/ResearchOS/tests/test_agent_permission_next.py)
- [test_llm_client_message_transform.py](/D:/Desktop/ResearchOS/tests/test_llm_client_message_transform.py)

### 3.27 Responses include / chat fallback / annotation continuity 已完成第一版

已完成的核心点：

- OpenAI Responses 路径现在会按 OpenCode `openai-responses-language-model.ts` 的语义自动补齐 builtin tool `include`：
  - 有 `openai.web_search` / `openai.web_search_preview` 时，自动附加 `web_search_call.action.sources`
  - 有 `openai.code_interpreter` 时，自动附加 `code_interpreter_call.outputs`
- OpenAI-compatible chat/completions 路径现在会像 OpenCode `openai-compatible-prepare-tools.ts` 一样，过滤掉 `provider-defined` tools：
  - 普通流式 chat fallback 不再把 `openai.web_search` / `openai.local_shell` 这类 provider-defined tool 原样发给 chat API
  - raw HTTP chat fallback 也会复用相同过滤，避免 transport fallback 时出现无效 tool payload
- OpenAI Responses assistant 文本输出里的 `annotations` 现在会随 `text_delta.metadata` 透传：
  - `url_citation`
  - `file_citation`
- 这些 annotation metadata 会继续走现有 text part 持久化链路，不会在 runtime 中途丢失

落点：

- [llm_client.py](/D:/Desktop/ResearchOS/packages/integrations/llm_client.py)
- [test_llm_client_message_transform.py](/D:/Desktop/ResearchOS/tests/test_llm_client_message_transform.py)

### 3.28 Legacy `/agent/chat` structured transcript continuity 已完成第一版

已完成的核心点：

- 仍在被前端主聊天使用的 `POST /agent/chat` 链路现在不再把 user message 强制压成纯字符串：
  - `AgentMessage.content` 现在接受 `string | dict | list`
  - `AgentMessage` 现在接受消息级：
    - `tools`
    - `system`
    - `variant`
    - `format`
- legacy 路由现在会把结构化消息原样传给 native `stream_chat()`：
  - user `text/file` parts 不会在 Pydantic schema 层被提前丢失
  - 最后一条 user message 的 `tools/system/variant` 也会继续透传到 runtime
- `sync_external_transcript()` 现在会像 `/session/{id}/message` 一样，把 legacy transcript 的 user message 以 `parts + meta` 持久化到新 session store：
  - `text` part 会保留为 `type=text`
  - `file` part 会保留为 `type=file`
  - `tools/system/variant/format` 会保留到 user message meta
- 因此前端虽然还没切到 `/session/*`，但已经可以复用前面完成的：
  - structured user input continuity
  - message-level tool gating
  - provider/runtime 对 `text/file` parts 的输入转换
- 前端 TS `AgentMessage` 类型也已同步放宽，避免现有 UI 接口层继续把结构化消息卡死在类型定义里

落点：

- [schemas.py](/D:/Desktop/ResearchOS/packages/domain/schemas.py)
- [agent.py](/D:/Desktop/ResearchOS/apps/api/routers/agent.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [index.ts](/D:/Desktop/ResearchOS/frontend/src/types/index.ts)
- [test_agent_session_runtime.py](/D:/Desktop/ResearchOS/tests/test_agent_session_runtime.py)
- [test_agent_remote_workspace.py](/D:/Desktop/ResearchOS/tests/test_agent_remote_workspace.py)

### 3.29 Session bus -> global bus mirror 已完成第一版

已完成的核心点：

- 当前 ResearchOS session bus 在保留原有进程内订阅能力的同时，新增了一个 OpenCode 风格的 global event 通道：
  - 事件格式为 `{ directory, payload }`
  - `payload` 继续复用原有 `type + properties`
- `session.status / prompt / step / message / part` 事件现在会自动镜像到 global bus，而不是只停留在 session-local 订阅器里
- 为避免在事件路径上额外触发数据库读取，session 目录现在在 `ensure_session_record()` / `get_session_record()` 时提前缓存：
  - global bus 只读缓存，不在 `publish()` 时临时查库
  - 这样不会破坏现有 queued callback / resume_existing 多线程测试
- prompt lifecycle 测试里已经补了 global bus 镜像回归，验证 busy/idle/message/part 事件都会带上 session directory

落点：

- [global_bus.py](/D:/Desktop/ResearchOS/packages/ai/global_bus.py)
- [session_bus.py](/D:/Desktop/ResearchOS/packages/ai/session_bus.py)
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py)

### 3.30 Global event / dispose routes 已完成第一版

已完成的核心点：

- 新增了 OpenCode 风格的 global 路由面：
  - `GET /global/health`
  - `GET /global/event`
  - `POST /global/dispose`
- `/global/event` 现在会把前一块补好的 global bus 事件流以 SSE 暴露出来：
  - 首包发送 `server.connected`
  - 空闲时发送 `server.heartbeat`
  - 事件体格式保持 `{directory, payload}`
- `/global/dispose` 现在会做真实清理，而不是空返回：
  - abort 所有 active / queued prompt session
  - 停止 idle processor
  - 关闭 MCP registry 连接
  - 关闭 ACP registry 连接
  - 停止本地 opencode sidecar runtime
  - 广播 `global.disposed`
- prompt lifecycle 基础设施补了 `list_prompt_session_ids()`，因此 dispose 可以覆盖：
  - active prompt
  - reserved prompt owner
  - waiters
  - queued callbacks
- FastAPI 主入口已注册新 global 路由，当前主入口 route 数变为 `246`

落点：

- [session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py)
- [global_routes.py](/D:/Desktop/ResearchOS/apps/api/routers/global_routes.py)
- [main.py](/D:/Desktop/ResearchOS/apps/api/main.py)
- [test_global_routes.py](/D:/Desktop/ResearchOS/tests/test_global_routes.py)

### 3.31 Provider `smallOptions()` / namespace remap 已完成第二版

已完成的核心点：

- `LLMClient` 现在新增了更接近 OpenCode `transform.ts` 的 provider option 抽象层：
  - `_build_small_provider_options()`
  - `_remap_provider_options_namespace()`
  - chat / responses 两条链路共用同一套小模型 option 注入逻辑
- 当前 native runtime 里，OpenCode `small=true` 的本地等价分支先映射到 `stage="skim"`：
  - `skim` 请求会走 small option 覆盖
  - `rag` 仍保持现有主对话语义，不额外强压成 OpenCode small mode
- GPT-5 small 路径现在会按 OpenCode 语义把默认 effort 从常规档位下调到 small 档位：
  - `gpt-5.x -> low`
  - `gpt-5 -> minimal`
  - 同时保留现有 official OpenAI continuity 所需的 `summary/include` 链路
- Gemini / Google OpenAI-compatible small 路径现在会按 OpenCode 语义下调 thinking：
  - `gemini-3 -> thinking_level=minimal`
  - `gemini-2.5 -> thinking_budget=0`
- OpenRouter small 路径现在会按 OpenCode 语义：
  - 普通模型走 `reasoningEffort=minimal`
  - Google/Gemini 模型走 `reasoning.enabled=false`
- Venice small 路径现在会补 `disableThinking=true`
- OpenCode `providerOptions()` 的 namespace remap 第一版已补齐到 native helper：
  - 常规目标会映射到对应 provider namespace
  - gateway 目标会拆成 `gateway + upstream slug`
  - `amazon/* -> bedrock` 的 slug override 已对齐
- provider option focused tests 已覆盖：
  - GPT-5 `skim` small effort
  - Gemini `skim` thinking 降档
  - OpenRouter `skim` small reasoning
  - Venice `skim` disable thinking
  - gateway namespace remap
- 本轮继续把 provider 结构往 OpenCode 分层推进：
  - `ResolvedModelTarget / ParsedModelTarget / provider/model variant normalize` 已迁到独立 schema 模块
  - `smallOptions / namespace remap / reasoning effort / provider-specific kwargs` 已迁到独立 transform 模块
  - `LLMClient` 现阶段保留兼容方法名，但内部已改为委托新模块，减少后续 provider registry 拆分时的耦合面

当前限制：

- 这轮补的是 OpenCode `smallOptions()/providerOptions()` 的 native 等价层；ResearchOS 仍没有 OpenCode 那种独立的 provider SDK registry / gateway runtime
- 当前 gateway namespace remap 已有 helper 与测试，但 runtime 里还没有真实的 gateway provider transport 路径去消费这套分桶结果

### 3.32 Instance-scoped dispose broadcast 已完成第一版

已完成的核心点：

- `/global/dispose` 现在不再只广播 `global.disposed`
- 在真正清理 active prompt / idle processor / MCP / ACP / opencode runtime 之前，global 路由现在会先收集当前已知 instance directory：
  - active prompt session 对应的 `session.directory/workspace_path`
  - opencode runtime manager 的 `default_directory`
- 对每个唯一目录，global bus 现在都会发布：
  - `type: "server.instance.disposed"`
  - `properties.directory`
- 这让当前 global dispose 链路更接近 OpenCode `Instance.disposeAll()` 的目录级释放语义，而不是只有一个全局级别的 `global.disposed`
- 回归测试已覆盖：
  - 创建 session 后占用 prompt instance
  - 调用 `/global/dispose`
  - 断言同时收到目录级 `server.instance.disposed` 与全局 `global.disposed`

当前限制：

- 当前只在 `/global/dispose` 这条总清理路径上补了 directory-scoped dispose 事件
- 还没有 OpenCode `Instance.reload()/dispose()/disposeAll()` 那种统一 instance cache 管理与 reload/dispose 全链路绑定

### 3.33 Provider resolver / transport policy 拆分已完成第一版

已完成的核心点：

- `LLMClient` 中原本混在一起的 provider target / embedding / engine-profile 解析逻辑，现已进一步拆到独立模块：
  - [llm_provider_resolver.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_resolver.py)
  - [llm_provider_schema.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_schema.py)
- 现在以下逻辑已不再由 `LLMClient` 自己维护细节，而是委托 resolver：
  - transport base URL 规范化
  - embedding provider / api key / base URL 推断
  - `stage -> model` 选择
  - `provider/model/variant` target 解析
  - engine profile runtime_config 解析与 target 落地
- 这让 provider 分层开始更接近 OpenCode 的：
  - `schema`
  - `provider/model resolution`
  - `transform`
  - `transport execution`
- 同时，chat/raw-http fallback 相关的 provider 判定也已开始独立化：
  - [llm_provider_policy.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_policy.py)
- 当前已从 `LLMClient` 抽出的 policy 包括：
  - `supports_chat_reasoning_content`
  - `is_anthropic_chat_target`
  - `is_mistral_chat_target`
  - `should_try_raw_openai_http_fallback`

当前限制：

- 这一步仍然停留在“解析与判定逻辑外提”，还没有做到 OpenCode `provider/provider.ts` 那种完整 provider registry / loader / SDK dispatch
- `LLMClient` 当前仍负责实际 transport 执行与 provider 分发；下一步要继续把 dispatch matrix 抽出，逼近 OpenCode `Provider.get()/model()/sdk` 结构

### 3.34 Provider dispatch matrix 拆分已完成第一版

已完成的核心点：

- `LLMClient` 入口处原本散落的 provider route 判定，已继续外提到：
  - [llm_provider_dispatch.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_dispatch.py)
- 当前已从 `LLMClient` 收拢的 dispatch 决策包括：
  - `summarize_text()` 的 `openai-responses / openai-compatible / anthropic / pseudo`
  - `chat_stream()` 的 official OpenAI Responses 与 OpenAI-compatible 分流
  - `test_config()` 的 chat transport route
  - embedding provider / pseudo fallback route
- `LLMClient` 现阶段仍保留兼容方法名和实际 transport 实现，但入口不再直接依赖 `provider == "openai"/"zhipu"/"anthropic"` 的分支矩阵，而是统一读取 dispatch route
- 新增单元测试覆盖 dispatch 分层：
  - [test_llm_client_dispatch.py](/D:/Desktop/ResearchOS/tests/test_llm_client_dispatch.py)
- 这一步让 provider 结构进一步逼近 OpenCode 的：
  - `schema`
  - `policy`
  - `transform`
  - `dispatch`
  - `transport execution`

当前限制：

- 这一步仍然只是把“选择哪条 transport route”外提，并没有完成 OpenCode `provider/provider.ts` 里的 SDK registry / lazy loader / cache key 生命周期
- `LLMClient` 目前仍持有 `_get_openai_client()`、`_call_*()`、`_embed_*()` 等执行面；下一步要继续把 provider SDK dispatch / client registry 抽离出来

### 3.35 Provider client registry 拆分已完成第一版

已完成的核心点：

- 新增 provider client registry：
  - [llm_provider_registry.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_registry.py)
- 当前已从 `LLMClient` 外提的 SDK/client cache 包括：
  - OpenAI client cache key 与复用
  - Anthropic client cache key 与复用
- `LLMClient` 仍保留 `_get_openai_client()` / `_get_anthropic_client()` 兼容入口，但实际已改成委托 registry，减少后续继续拆 provider runtime 时对现有调用点和测试的破坏面
- Anthropic 调用现在也开始走统一 client factory，且会带上 resolved `base_url`，这更接近 OpenCode `getSDK()` 以 provider options 生成 SDK 的方式
- 新增 registry 单元测试：
  - [test_llm_provider_registry.py](/D:/Desktop/ResearchOS/tests/test_llm_provider_registry.py)

当前限制：

- 当前 registry 只覆盖 Python SDK client cache，还没有 OpenCode `provider/provider.ts` 那种按 provider model/api/options 组合出的完整 SDK registry
- transport 执行仍由 `LLMClient` 拥有；下一步还要继续把 `responses/chat/embeddings/test_config` 的 handler surface 往 registry/adapter 收

### 3.36 Provider probe / test transport 拆分已完成第一版

已完成的核心点：

- 新增 provider probe 模块：
  - [llm_provider_probe.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_probe.py)
- `test_config()` 里原本堆在 `LLMClient` 里的 transport probe 逻辑，现已外提到独立 probe：
  - OpenAI Responses probe
  - OpenAI-compatible chat probe
  - Anthropic chat probe
  - embedding probe
- `LLMClient` 现在在 `test_config()` 路径只负责：
  - resolve target / embedding config
  - dispatch route 选择
  - disabled / missing key / unsupported 出口
  - probe 调用
- 新增 probe 单元测试：
  - [test_llm_client_probe.py](/D:/Desktop/ResearchOS/tests/test_llm_client_probe.py)

当前限制：

- 当前 probe 只是把“测试 transport”从 `LLMClient` 中拿出，真正的 `summarize/chat_stream/embed` handler 仍然在 `LLMClient`
- 下一步要继续把实际 handler surface 外提，逼近 OpenCode `provider.ts` 那种“registry + runtime adapter + execution”结构

### 3.37 Native permission resume 持久化已继续内收

已完成的核心点：

- native `permission confirm/reject -> resume` 路径的持久化，现已从 `respond_action()` 外层 inline wrapper 内收到：
  - [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- `_respond_native_action_impl()` 现在会在内部直接驱动：
  - `SessionStreamPersistence.consume()`
  - `SessionStreamPersistence.finalize()`
  - persisted stream 标记
- `respond_action()` 对 native pending path 不再额外套 `_persist_inline_stream_if_needed()`，减少了一层本地 wrapper 语义
- 这让 native permission pause/resume 更接近 OpenCode “processor 自己拥有续跑生命周期”的结构，而不是“外层拿到 SSE 再补持久化”
- 新增验证：
  - [test_agent_permission_next.py](/D:/Desktop/ResearchOS/tests/test_agent_permission_next.py)
  - 直接卡住 `_persist_inline_stream_if_needed()`，确认 native confirm 现在不再走外层 inline persist

当前限制：

- 这一步只收了 native permission resume 路径；整体 prompt 执行主导权仍未完全回收到单一 `SessionProcessor` active loop
- ACP confirm/resume 仍保留现有兼容路径

### 3.38 Part delta 已补到 OpenCode 风格字段并扩展 tool input raw

已完成的核心点：

- `session.message.part.delta` 事件现在已补齐更接近 OpenCode `message.part.delta` 的字段：
  - `sessionID`
  - `messageID`
  - `partID`
  - `field`
  - `delta`
- 文本与 reasoning delta 现在会显式发布 `field="text"`
- `tool-input-delta` 现在也会发布 `field="state.raw"` 的 part delta 事件
- 新增验证：
  - [test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py)
  - [test_agent_session_runtime.py](/D:/Desktop/ResearchOS/tests/test_agent_session_runtime.py)

当前限制：

- 当前只补到 `text` 和 `tool input raw` 两类增量字段，还没有把 tool output / structured result 等更多 delta 细分到 OpenCode 的完整粒度
- 数据库存储层目前仍是更新当前完整 part，而不是单独维护 delta log

### 3.39 Provider raw HTTP transport 已继续拆分

已完成的核心点：

- 新增 provider HTTP transport 模块：
  - [llm_provider_http.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_http.py)
- `LLMClient` 中原本内联的 raw OpenAI-compatible HTTP 执行逻辑，现已外提到独立 transport helper：
  - `raw_openai_compatible_post()`
  - `call_openai_responses_raw_http()`
  - `call_openai_chat_raw_http()`
- `LLMClient` 仍保留原有同名兼容入口，但内部只做委托：
  - `_raw_openai_compatible_post()`
  - `_call_openai_responses_raw_http()`
  - `_call_openai_chat_raw_http()`
- 这一步继续把 provider 结构往 OpenCode `registry + transport adapter + execution helper` 的方向收口，减少 raw HTTP fallback 与主 `LLMClient` 单体的耦合面
- 新增验证：
  - [test_llm_client_provider_options.py](/D:/Desktop/ResearchOS/tests/test_llm_client_provider_options.py)
  - [test_llm_client_dispatch.py](/D:/Desktop/ResearchOS/tests/test_llm_client_dispatch.py)
  - [test_llm_client_probe.py](/D:/Desktop/ResearchOS/tests/test_llm_client_probe.py)

当前限制：

- 当前只拆出了 raw HTTP transport helper；`chat_stream()` / `embed_text()` 的实际 provider handler surface 仍主要留在 `LLMClient`
- 这一步还不是 OpenCode `provider/provider.ts` 那种完整 registry + runtime adapter 结构，后续还要继续把 execution handler 往外收

### 3.40 Provider summary execution adapter 已继续拆分

已完成的核心点：

- 新增 provider summary adapter：
  - [llm_provider_summary.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_summary.py)
- `LLMClient.summarize_text()` 这条 execution 链里原本内联在 `LLMClient` 的 provider summary handler，现已继续外提：
  - `call_openai_responses()`
  - `call_openai_compatible()`
  - `call_anthropic()`
- `LLMClient` 仍保留兼容方法名：
  - `_call_openai_responses()`
  - `_call_openai_compatible()`
  - `_call_anthropic()`
  但内部已改为委托 provider summary adapter
- summary adapter 现在统一拥有：
  - OpenAI Responses SDK 调用
  - OpenAI-compatible SDK 调用
  - raw HTTP fallback / chat fallback / pseudo fallback
  - Anthropic summary 调用与 pseudo fallback
- 这一步让 provider execution 继续从 `LLMClient` 单体向 OpenCode 更接近的 `transport + execution adapter` 结构收口
- 新增验证：
  - [test_llm_client_summary.py](/D:/Desktop/ResearchOS/tests/test_llm_client_summary.py)

当前限制：

- 当前只拆出了 summary execution；`chat_stream()` 与更多 pseudo/runtime execution 仍主要留在 `LLMClient`
- 这一步还不是 OpenCode `provider/provider.ts` 那种统一 provider runtime registry；后续仍要继续把 streaming chat handler 往外收

### 3.41 Provider embedding execution adapter 已继续拆分

已完成的核心点：

- 新增 provider embedding adapter：
  - [llm_provider_embedding.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_embedding.py)
- `LLMClient` 中原本内联的 embedding execution/pseudo helper，现已继续外提：
  - `embedding_candidates()`
  - `embedding_error_priority()`
  - `pseudo_embedding()`
  - `embed_openai_compatible()`
  - `embed_openai_compatible_or_raise()`
- `LLMClient` 仍保留兼容方法名：
  - `_embedding_candidates()`
  - `_embedding_error_priority()`
  - `_pseudo_embedding()`
  - `_embed_openai_compatible()`
  - `_embed_openai_compatible_or_raise()`
  但内部已改为委托 provider embedding adapter
- embedding adapter 现在统一拥有：
  - OpenAI-compatible embedding transport
  - model/base URL fallback 候选生成
  - pseudo embedding
  - embedding error 优先级判定
- 这一步让 provider execution 继续从 `LLMClient` 单体向 OpenCode 更接近的 `transport + execution adapter` 结构推进
- 新增验证：
  - [test_llm_client_embedding.py](/D:/Desktop/ResearchOS/tests/test_llm_client_embedding.py)

当前限制：

- 当前只拆出了 summary / embedding execution；vision 与 streaming chat 仍主要留在 `LLMClient`
- 这一步还不是 OpenCode `provider/provider.ts` 那种统一 provider runtime registry；下一步仍要继续把 streaming chat / vision execution 往外收

### 3.42 Provider streaming chat adapter 已继续拆分

已完成的核心点：

- 新增 provider streaming adapter：
  - [llm_provider_stream.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_stream.py)
- `LLMClient` 中原本内联的 streaming chat execution，现已继续外提：
  - `stream_openai_responses()`
  - `stream_openai_compatible()`
  - `stream_anthropic_fallback()`
  - `stream_pseudo()`
- `LLMClient` 仍保留兼容方法名：
  - `_chat_stream_openai_responses()`
  - `_chat_stream_openai_compatible()`
  - `_chat_stream_anthropic_fallback()`
  - `_chat_stream_pseudo()`
  但内部已改为委托 provider streaming adapter
- streaming adapter 现在统一拥有：
  - OpenAI Responses streaming output -> `StreamEvent`
  - OpenAI-compatible streaming delta/tool-call 聚合
  - raw HTTP fallback
  - anthropic/pseudo fallback
  - OpenAI Responses provider-executed builtin tool event 发射
- 这一步让最重的 provider execution surface 继续从 `LLMClient` 单体向 OpenCode 更接近的 `transport + execution adapter` 结构推进
- 新增验证：
  - [test_llm_client_stream.py](/D:/Desktop/ResearchOS/tests/test_llm_client_stream.py)

当前限制：

- 当前 streaming execution 虽然已外提，但 message transform / responses input build / tool extraction helper 仍主要留在 `LLMClient`
- 这一步还不是 OpenCode `provider/provider.ts` 那种统一 provider runtime registry；后续还要继续把更底层 transform/extractor surface 往外收

### 3.43 Provider vision execution adapter 已继续拆分

已完成的核心点：

- 新增 provider vision adapter：
  - [llm_provider_vision.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_vision.py)
- `LLMClient` 中原本内联的 vision execution，现已继续外提：
  - `vision_analyze()`
  - `vision_openai_compatible()`
- `LLMClient` 仍保留兼容方法名：
  - `vision_analyze()`
  - `_vision_openai_compatible()`
  但内部的 provider-specific execution 已改为委托 vision adapter
- vision adapter 现在统一拥有：
  - official OpenAI Responses vision 调用
  - OpenAI-compatible vision fallback
  - Zhipu 兼容路径复用
- 新增验证：
  - [test_llm_client_vision.py](/D:/Desktop/ResearchOS/tests/test_llm_client_vision.py)

当前限制：

- 当前只外提了 vision execution；vision input transform 仍然直接内嵌在 adapter 里，还没有进一步抽象成 OpenCode 风格的 shared transform/helper
- provider runtime 目前仍由 `LLMClient` 顶层统一编排，不是 OpenCode 的统一 provider registry/loader

### 3.44 Provider responses extractor 已继续拆分

已完成的核心点：

- 新增 provider responses extractor：
  - [llm_provider_responses.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_responses.py)
- `LLMClient` 中原本内联的 OpenAI Responses 输出提取逻辑，现已继续外提：
  - `build_openai_provider_metadata()`
  - `build_openai_response_metadata()`
  - `extract_openai_item_id()`
  - `extract_openai_reasoning_metadata()`
  - `extract_openai_response_id_from_message()`
  - `extract_previous_responses_response_id()`
  - `extract_responses_output_parts()`
  - `extract_responses_tool_calls()`
- `LLMClient` 仍保留兼容方法名：
  - `_build_openai_provider_metadata()`
  - `_build_openai_response_metadata()`
  - `_extract_openai_item_id()`
  - `_extract_openai_reasoning_metadata()`
  - `_extract_openai_response_id_from_message()`
  - `_extract_previous_responses_response_id()`
  - `_extract_responses_output_parts()`
  - `_extract_responses_tool_calls()`
  但内部已改成薄包装委托 extractor module
- 这次拆分前先对照了 OpenCode：
  - `openai-responses-language-model.ts`
  - `convert-to-openai-responses-input.ts`
  然后把本地已对齐的 `itemId / reasoningEncryptedContent / responseId / builtin tool output` 提取语义整体搬进独立模块
- 同时补平了 extractor 行为细节，避免结构拆分时引入回退：
  - `annotations` 会继续做深拷贝
  - `reasoningEncryptedContent` 继续保留空字符串/非空字符串语义，不会因为 truthy 判断被吞掉
- 这一步让 OpenAI Responses 的 metadata / output-part / tool-call extractor surface 继续从 `LLMClient` 单体向 OpenCode 更接近的 provider module 结构推进

当前限制：

- 当前只是把 Responses extractor surface 从 `LLMClient` 收出去；responses input build、message-v2 error 映射、更多 provider-specific transcript transform 仍主要留在 `LLMClient`
- provider runtime 目前仍是 `LLMClient` 顶层编排，不是 OpenCode `provider/provider.ts` 那种统一 provider loader + runtime object

### 3.45 Explicit queued callback resume 已继续内收

已完成的核心点：

- `SessionPromptProcessor._resume_queued_callbacks()` 现在会直接 claim 单一 callback loop，而不再经由任何 worker/thread 包装
- 显式 queued callback 续跑入口现已与 owner finish handoff 完全同构：
  - 先通过 `claim_prompt_callback_loop()` 抢到单一 loop 所有权
  - 然后直接进入 `_run_callback_loop()`
  - owner 正常完成后的 handoff 也会走同一条 `_run_callback_loop()` 路径
- 这意味着当前 ResearchOS 里两条主要 callback 续跑入口：
  - owner prompt 正常完成后的 handoff
  - 显式 `_resume_queued_callbacks()` 续跑
  都已经改成 caller-thread inline callback loop，而不是再分出第二套后台 worker 语义
- `callback_loop_active` 现在显式表示“同一 session 只允许一个 callback loop 消费 queue”
- 新增回归直接锁住：
  - 显式 `_resume_queued_callbacks()` 只会启动一次 callback loop
  - 第二次并发触发不会并行再起第二个 callback loop
  - owner handoff 与显式 resume 都只会调用 `_run_callback_loop()` 一次
- queued callback payload 也继续瘦身，开始逼近 OpenCode 的 callback promise 形态：
  - 新 payload 现已进一步压成只保留 `session_id`
  - 不再把整份 `options / persistence / step_index / lifecycle_kind` 序列化进 callback queue
  - `reasoning_level / active_skill_ids` 现在优先从持久化 user transcript 恢复，不再默认留在 callback payload 里
  - callback runner 恢复时会从 session record + 最新 user message 重新推导 runtime options 与 persistence parent/meta
- callback loop 的推进语义也已从“逐 callback rebinding runner”改成更接近 OpenCode `loop({ sessionID })` 的 session-state 驱动：
  - callback loop 先看 `get_session_turn_state(session_id)`
  - 只要 session 还有 pending prompt，就继续用当前 session 历史重建 processor 并续跑
  - 不再按 callback payload 逐条恢复各自的 request-bound processor 状态
  - 如果 session 已经没有 pending turn，则会直接用最新 finished assistant 结果 resolve 所有 waiters，而不会再跑一轮空 processor
- queued callback 结果交付现在也更接近 OpenCode callback promise：
  - handoff 后的首条 callback 只是“激活 loop 的 waiter”
  - 同一轮 catch-up 完成后，当前 callback 与剩余 queued callbacks 会统一拿到同一个最终 assistant 结果
  - 多条 queued user prompt 在同一 active loop 内累积时，本地行为已改成更接近 OpenCode 的“session 追平后统一 resolve final result”，而不是每条 callback 各自产生一条 assistant
- malformed callback 的报错边界也更贴近“session facts first”：
  - 缺 `session_id` 时会报 `queued prompt callback is missing session context`
  - 缺 `request_message_id` 时会报 `queued prompt callback is missing request cursor`
- `/session/{id}/message` 主链现在也会把显式传入的 `reasoning_level/variant` 落到 user message 的 `variant` 字段上
  - 只记录显式传入值，不会把隐式 `default` 写回 transcript
  - 这让 callback resume 后续继续摆脱 payload 内的推理档位依赖
- legacy `/agent/chat` transcript 现在也会把 request 级 `active_skill_ids` 落到 user message 的 `activeSkillIDs`
  - `load_agent_messages()` 会把它恢复成 `active_skill_ids`
  - callback resume 因而可以从 transcript 恢复 skills，而不是依赖排队时的本地 payload

当前限制：

- 这已经不再是旧的 per-callback rebinding drain，但仍然不是 OpenCode `prompt.ts` 里的原生 callback promise 数组加同一个 `loop()` 直接续跑的最终结构
- 当前仍保留 `claim/release_prompt_callback_loop()` 这一层本地状态机，用来序列化 callback drain；而不是完全退化成 OpenCode 那种仅由 `state()[sessionID].callbacks` 数组驱动
- callback 结果交付依然依赖 HTTP SSE request 活着去消费 `_iter_callback_stream()`，不是 OpenCode 那种纯 promise resolve

### 3.46 Provider responses input builder 已继续拆分

已完成的核心点：

- `llm_provider_responses.py` 现在不只负责 output extractor，也开始承接更接近 OpenCode `convert-to-openai-responses-input.ts` 的 input build 逻辑
- 新外提的 responses helper 包括：
  - `parse_local_shell_output()`
  - `assistant_reasoning_parts()`
  - `assistant_text_parts()`
  - `normalize_responses_tools()`
  - `build_responses_input_from_messages()`
- `LLMClient` 仍保留兼容方法名：
  - `_parse_local_shell_output()`
  - `_assistant_reasoning_parts()`
  - `_assistant_text_parts()`
  - `_normalize_responses_tools()`
  - `_build_responses_input_from_messages()`
  但内部已改成 thin wrapper，实际实现落在 `llm_provider_responses.py`
- 这次拆分前继续对照了 OpenCode：
  - `provider/sdk/copilot/responses/convert-to-openai-responses-input.ts`
  然后把本地已经对齐的 continuity 逻辑整体外提：
  - reasoning `item_reference` / `reasoning` replay
  - assistant text `id` replay
  - function/local_shell call replay
  - provider-executed builtin tool 在 `store=true/false` 下的 replay 分支
  - provider-defined builtin tool schema normalize
- 这让 OpenAI Responses 的 input/output 两端都开始从 `LLMClient` 单体向同一 provider module 聚拢，结构上更接近 OpenCode 的 responses adapter 分层

当前限制：

- 当前 responses input build 虽已外提，但 user file/image content transform 仍通过 `LLMClient` helper 提供，不是完全独立的 provider input adapter
- `normalize_openai_chat_tools()`、chat message build、更多 message-v2/provider error transform 仍主要留在 `LLMClient`
- provider runtime 仍由 `LLMClient` 顶层统一编排，不是 OpenCode `provider/provider.ts` 那种 provider object/loader 结构

### 3.47 Provider chat message transform 已继续拆分

已完成的核心点：

- `llm_provider_transform.py` 现在开始承接更接近 OpenCode `provider/transform.ts` 的 chat message normalization 逻辑
- 新外提的 chat transform helper 包括：
  - `normalize_claude_tool_call_id()`
  - `normalize_mistral_tool_call_id()`
  - `normalize_openai_chat_tools()`
  - `normalize_openai_chat_messages()`
  - `build_openai_chat_messages()`
- `LLMClient` 仍保留兼容方法名：
  - `_normalize_claude_tool_call_id()`
  - `_normalize_mistral_tool_call_id()`
  - `_normalize_openai_chat_tools()`
  - `_normalize_openai_chat_messages()`
  - `_build_openai_chat_messages()`
  但内部已改成 thin wrapper，真正实现转到 `llm_provider_transform.py`
- 这次拆分前继续对照了 OpenCode：
  - `provider/sdk/copilot/chat/convert-to-openai-compatible-chat-messages.ts`
  然后把本地已对齐的关键语义收进 transform module：
  - Anthropic 空 assistant message 过滤
  - Claude tool-call id 规范化
  - Mistral tool-call id 规范化
  - Mistral `tool -> user` 非法序列自动插入 `assistant: "Done."`
  - provider-defined builtin tool 在 chat/completions 路径下的过滤
- 这一步让 provider message transform 的 message/tool 两个主入口都开始从 `LLMClient` 单体向 `llm_provider_transform.py` 聚拢，结构上继续逼近 OpenCode

当前限制：

- user/file content normalize 仍有一部分 helper 留在 `LLMClient`，还没有完全抽成 shared transform/input helper
- responses user content build 与 chat user content build 目前仍复用 `LLMClient` helper，不是完全独立的 provider prompt adapter
- provider runtime 仍由 `LLMClient` 顶层统一编排，不是 OpenCode `provider/provider.ts` 那种 loader/runtime object

### 3.48 Provider user/file content transform 已继续拆分

已完成的核心点：

- `llm_provider_transform.py` 现在继续承接 OpenAI prompt 构造里共用的 user/file content helper
- 新外提的 shared helper 包括：
  - `coerce_openai_message_text()`
  - `stringify_message_content()`
  - `normalize_user_content_parts()`
  - `decode_data_url_bytes()`
  - `read_local_file_bytes_from_url()`
  - `build_data_url()`
  - `resolve_model_accessible_file_url()`
  - `extract_text_from_user_file_part()`
  - `build_responses_user_content()`
  - `build_openai_chat_user_content()`
- `LLMClient` 仍保留兼容方法名：
  - `_coerce_openai_message_text()`
  - `_stringify_message_content()`
  - `_normalize_user_content_parts()`
  - `_decode_data_url_bytes()`
  - `_read_local_file_bytes_from_url()`
  - `_build_data_url()`
  - `_resolve_model_accessible_file_url()`
  - `_extract_text_from_user_file_part()`
  - `_build_responses_user_content()`
  - `_build_openai_chat_user_content()`
  但内部都已改成 thin wrapper，真正实现转到 `llm_provider_transform.py`
- 这次拆分前继续对照了 OpenCode：
  - `convert-to-openai-compatible-chat-messages.ts`
  - `convert-to-openai-responses-input.ts`
  并把本地现有扩展语义一起收进 shared transform：
  - 结构化 user text/file part normalize
  - 本地 `file://` 转模型可访问 data URL
  - 文本文件提取为 input text
  - PDF/image 对 chat/responses 两条路径的分流构造
- 这让 chat/responses prompt build 现在真正开始共用同一层 provider transform helper，而不是分别挂在 `LLMClient` 私有方法上

当前限制：

- 目前 user/file content transform 已收进 shared helper，但顶层 provider runtime 调度、message-v2 error 映射、更多 provider-specific transcript policy 仍在 `LLMClient`
- responses/chat prompt build 虽然已大幅外提，仍然通过 `LLMClient` wrapper 暴露，不是 OpenCode `provider object` 自身方法
- provider runtime 仍由 `LLMClient` 统一编排，不是 OpenCode `provider/provider.ts` 那种 provider loader/runtime object

### 3.49 Error normalization / retry delay 已继续对齐 OpenCode

已完成的核心点：

- `session_errors.py` 现在开始按更接近 OpenCode `MessageV2.fromError()` 的方式保留结构化错误信息：
  - `responseHeaders`
  - `responseBody`
  - `metadata`
  - `statusCode`
- 新增了更贴近 OpenCode 的连接重置映射：
  - `code == ECONNRESET` 会归一成 retryable `APIError`
  - `message` 固定为 `Connection reset by server`
  - 同时保留 `code/syscall/errno/...` metadata
- `session_retry.delay()` 现在开始按更接近 OpenCode `SessionRetry.delay()` 的方式优先读取：
  - `retry-after-ms`
  - `retry-after`
  - `retry-after` HTTP date
  如果没有这些头，才回退到原有指数退避
- 无 header 时的退避常量也已对齐到更接近 OpenCode 的基线：
  - initial delay: `2000ms`
  - backoff factor: `2`
  - max delay without headers: `30000ms`
- agent prompt loop 在触发 retry 时，现已把结构化 `error_payload` 传给 `session_retry.delay()`，不再只传裸 attempt 计数
- 新增回归锁住：
  - `ECONNRESET -> APIError(isRetryable=true, metadata preserved)`
  - `retry-after-ms`
  - `retry-after`

当前限制：

- 当前仍然只是本地 `normalize_error()` 规则集，不是 OpenCode `ProviderError.parseAPICallError/parseStreamError` 那种 provider SDK 级错误解析
- 普通 runtime 里还没有完整 providerID 维度的 auth/api/context error 解析上下文
- retry 现在已支持 header-driven delay，但仍不是 OpenCode 那种完整 `FreeUsageLimitError / overloaded / rate_limit` 文案级别对齐

### 3.50 Provider public runtime entry 已继续拆分

已完成的核心点：

- 新增 provider runtime adapter：
  - [llm_provider_runtime.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_runtime.py)
- `LLMClient` 顶层 public entry 里原本内联的 provider dispatch orchestration，现已继续外提：
  - `summarize_text()`
  - `vision_analyze()`
  - `embed_text_with_info()`
  - `chat_stream()`
- `LLMClient` 仍保留兼容 public API，但内部已改成 thin wrapper，真正 dispatch 编排转到 `llm_provider_runtime.py`
- 新 adapter 现在统一承接：
  - summary route dispatch
  - vision route dispatch
  - embedding route dispatch
  - chat stream route dispatch
- 这一步让 `LLMClient` 更接近 OpenCode 里的“兼容 facade + provider runtime object/loader”方向，而不再把 provider route 选择、target resolve 后的顶层编排全部堆在同一个类里

当前限制：

- `test_config()` / provider probe 入口仍然留在 `LLMClient`
- 当前 runtime adapter 仍通过 `LLMClient` wrapper 暴露，不是 OpenCode `provider/provider.ts` 那种 provider object 自身对外提供完整 runtime surface
- provider runtime 现在只是入口编排外提，还没有做到 OpenCode 那种统一 provider loader/runtime instance 结构

### 3.51 Provider test/probe public entry 已继续拆分

已完成的核心点：

- `llm_provider_runtime.py` 现在继续承接 provider public test/probe 入口编排：
  - `test_config()`
  - `test_chat_config()`
  - `test_embedding_config()`
- `LLMClient` 仍保留兼容方法名：
  - `test_config()`
  - `_test_chat_config()`
  - `_test_embedding_config()`
  但内部都已改成 thin wrapper，真正的 dispatch 编排转到 `llm_provider_runtime.py`
- probe 细节仍由既有 `llm_provider_probe.py` 负责：
  - `probe_openai_chat()`
  - `probe_openai_compatible_chat()`
  - `probe_anthropic_chat()`
  - `probe_embedding_openai_compatible()`
  只是“该走哪条 probe route”的 public entry 决策，已经不再内联堆在 `LLMClient`
- 这一步让 `LLMClient` 顶层 public runtime/test surface 更接近“兼容 facade”，进一步逼近 OpenCode provider runtime object 的边界感

当前限制：

- `LLMClient` 目前仍是这些 runtime/test adapter 的统一 facade，不是 OpenCode `provider/provider.ts` 那种 provider loader 直接返回 runtime object
- provider probe 仍主要围绕当前本地 SDK/HTTP fallback 组合，不是 OpenCode 那种完整 provider capability registry
- lifecycle 侧与 provider 侧之间仍通过现有 `LLMClient` facade 对接，不是更彻底的 provider object 注入模式

### 3.52 Native prompt persistence dispatch 已继续向 processor 内收

已完成的核心点：

- `SessionStreamPersistence` 新增了更接近 processor-owned lifecycle 的已解析事件入口：
  - `apply_event(event_name, data)`
- `consume(raw)` 现在只保留为兼容 wrapper/legacy stream 的薄封装：
  - 先 `parse_sse_event`
  - 再转调 `apply_event(...)`
  - 最后仅把 synthetic event 重新格式化成 SSE
- native `SessionPromptProcessor._stream_active()` 现在不再让 persistence 反向解析原始 SSE：
  - processor 已经在本地拿到 event string
  - 会先解析成 `(event_name, data)`
  - 然后直接调用 `SessionStreamPersistence.apply_event(...)`
  - synthetic error/event 再由 processor 自己回放给外层
- native permission confirm/reject 的 resume 路径也同样改成 direct parsed-event dispatch：
  - `respond_action()` 本地续跑流不再依赖 `consume(raw)`
- 新增回归直接锁住：
  - native `/session/{id}/message` 路径如果再次回退到 `consume(raw)`，测试会直接失败
- 这一步让本地 prompt persistence 更接近 OpenCode `SessionProcessor` 的方向：
  - persistence 的事实来源开始从“wrapper 重新读 SSE 文本”转向“processor 已知事件直接驱动”
  - callback loop / prompt loop / resume loop 三条 native 主链现在共享同一套 direct event dispatch 逻辑

当前限制：

- 当前 direct dispatch 仍然是“processor 先生成 SSE event，再在同线程内解析成 `(event_name, data)`”的过渡形态
- 还没有完全收成 OpenCode 那种 processor 直接产出 message/part mutation，而不是先经过 SSE event 名称层
- `wrap_stream_with_persistence()` 仍保留给 legacy/raw stream 路径使用，因此 `consume(raw)` 兼容层还不能删

### 3.53 Native permission resume 已继续压回 session-history 驱动

已完成的核心点：

- `SessionPromptProcessor._messages_for_run()` 现在已把 `lifecycle_kind="resume"` 一并纳入 session-history 优先装载
  - native confirm/reject 后的继续执行，不再默认依赖本地 `pending.messages`
- native permission confirm 路径现在会更接近 OpenCode 单 loop 语义：
  - 前半段仍在当前 assistant message 内补齐 `tool_result / step-finish`
  - 之后新的 resume processor 改为从持久化 session transcript 重新装载历史，而不是吃本地 continuation message 数组
- native permission reject 路径也补齐了更接近 transcript-first 的 tool rejection 落库：
  - 在恢复前会显式发出 `tool_result(success=false)`
  - 已存在的 pending tool part 现在会落成 `state.status=error`
  - `summary` 会保留用户拒绝原因
- confirm/reject 两条 native resume 链都已改成 `messages=[] + lifecycle_kind="resume"`：
  - 继续跑的事实源是 session transcript
  - `PendingAction.messages` 只保留兼容 fallback/ACP 场景，不再是 native resume 的主事实源
- 新增回归直接锁住：
  - native permission confirm 续跑时必须调用 `load_agent_messages(session_id)`
  - reject 后 assistant 历史中的 tool part 必须变成 `error`

当前限制：

- native permission resume 虽然已经改成 session-history 驱动，但前半段 confirm/reject 仍通过本地 SSE 事件把 continuation 写回 transcript
- `PendingAction` 结构本身还没有像 OpenCode 那样彻底退化成更薄的 promise/permission continuation 记录

### 3.54 Native pending action / request cursor 已继续瘦身

已完成的核心点：

- native permission pause 现在会继续减少本地 continuation 状态：
  - 新创建的 native pending action 不再把 `messages` 持久化到 `continuation_json`
  - 也不再把 `tool_calls` 持久化到 `continuation_json`
- native permission confirm/reject 现在优先从已持久化 assistant message 恢复 pending tool calls：
  - 通过当前 `assistant_message_id` 读取 transcript 中的 tool parts
  - 仅对 `status != completed/error` 的 tool parts 重建 pending `ToolCall`
  - 只有在 transcript 缺失时，才回退到旧的 `PendingAction.tool_calls`
- 这意味着 native permission continuation 的主事实源已经进一步从：
  - 本地 `PendingAction.messages/tool_calls`
  转到：
  - persisted assistant/tool parts + session transcript
- `/agent/chat` 与 `/session/{id}/message` 两条 native prompt 入口现在也不再显式把 `request_message_id` 传给 `stream_chat()`
  - 主链只保留 `persistence.parent_id`
  - prompt loop 的历史选择继续向 session-history 驱动靠拢，而不是 request-bound cursor 驱动
- 新增回归直接锁住：
  - native pending action 持久化里不应再出现 `messages`
  - native pending action 持久化里不应再出现 `tool_calls`
  - legacy/session prompt 路由透传 `stream_chat()` 时不再显式下发 `request_message_id`

当前限制：

- callback restore 仍会在 `_callback_runtime_from_payload()` 里根据 session turn state 和最新 user transcript 反推出 request cursor
- native permission continuation 虽然已经瘦到 transcript-first，但整体交付仍是本地 `PendingAction + SSE resume` 结构，不是 OpenCode 那种更薄的 session loop continuation

### 3.55 Native permission continuation metadata 已继续从持久化中移除

已完成的核心点：

- native permission pause 现在进一步不再把这些 continuation 元数据持久化到 `continuation_json`：
  - `step_index`
  - `assistant_message_id`
  - `step_snapshot`
  - `step_usage`
- native confirm/reject 恢复时会优先从 transcript/session state 反推出当前 continuation 上下文：
  - `assistant_message_id` 改为优先读取 `permission_json.tool.messageID`
  - `step_index / step_snapshot` 改为从已持久化 assistant message 的 `step-start` part 恢复
  - `step_usage` 改为优先从当前 assistant message 的 `info.tokens` 反推
- 新创建的 native pending action 现在连进程内 `_pending_actions` cache 也不再保留：
  - `tool_calls`
  - `assistant_message_id`
  - `step_index / step_snapshot / step_usage`
- 这让 native permission continuation 的持久化事实源继续从本地 continuation 字段收回到：
  - pending permission request 自身
  - persisted assistant message meta
  - persisted assistant parts
- 新增回归直接锁住：
  - native pending action 持久化里不应再出现 `step_index`
  - native pending action 持久化里不应再出现 `assistant_message_id`
  - native pending action 持久化里不应再出现 `step_snapshot`
  - native pending action 持久化里不应再出现 `step_usage`
  - cache 清空后 native permission confirm/reject 仍能继续恢复

当前限制：

- 这一步已经把 native permission continuation 的持久化字段和进程内 pending cache 都继续削薄；但 `PendingAction` 兼容结构和 SSE resume 交付模型本身仍然存在
- processor/persistence 的边界虽然继续变薄，但还没有完全收成 OpenCode `SessionProcessor` 直接拥有 message/part mutation 的结构

### 3.56 Native parsed-event dispatch 已进一步去掉二次字符串回灌

已完成的核心点：

- `PromptStreamControl` 现在新增 `observe_event(event_name, data)`：
  - `observe(raw_sse)` 只保留为兼容层
  - native prompt/resume 主链不再必须把同一条事件反复 parse 后再交给 control
- native `SessionPromptProcessor._stream_active()` 现在会对每条事件只做一次 `parse_sse_event`：
  - 同一份 `(event_name, data)` 会直接送入 persistence
  - synthetic event 也会直接送入 control
  - 最后才格式化成 SSE 返回给外层
- native permission confirm/reject 的 resume 路径也已经切到同样的 parsed-event 驱动：
  - 不再通过“先拼 synthetic SSE 字符串，再让 control/persistence 重新 parse”来推进状态
- 这让本地 native lifecycle 又往 OpenCode `SessionProcessor.process()` 那种“已解析事件驱动状态机”的方向靠近了一步

当前限制：

- native path 仍然以 SSE event 名称作为 processor 和外层交付之间的公共边界，不是 OpenCode 那种直接 message/part mutation
- callback/legacy wrapper 兼容层仍会继续使用 `observe(raw_sse)` 这条 parse-on-read 入口

### 3.57 Queued callback restore 已继续压回严格 session-derived 语义

已完成的核心点：

- `_callback_runtime_from_payload()` 现在对 native queued callback restore 只要求最小 `session_id`
- native queued callback restore 不再接受这些 legacy runtime 字段作为事实源：
  - `request_message_id`
  - `reasoning_level`
  - `active_skill_ids`
  - `assistant_message_id`
  - `options`
  - `persistence`
- callback restore 现在统一从这些 session 事实源恢复 runtime：
  - 本地 transcript 直接扫描出的 latest user / assistant / finished assistant
  - 当前 request user message 的 persisted `variant / activeSkillIDs`
  - session record 里的 `mode / workspace_path / workspace_server_id`
- queued callback 的 `assistant_message_id` 现在也不再盲目沿用 turn state 里的最新 assistant：
  - 只有当该 assistant 的 `parentID` 仍指向当前 pending user request 时才复用
  - queued 新 prompt 会重新生成新的 assistant message id
- 新增回归直接锁住：
  - minimal callback payload 仍可恢复 processor
  - 即使传入 legacy runtime fields，restore 也必须以 session transcript/session state 为准

当前限制：

- callback restore 现在虽然已经直接扫描 persisted transcript，但还没有完全复用 OpenCode `loop()` 本体那种统一 history scan 入口
- callback 交付本身仍是本地 promise/control -> SSE replay 兼容层

### 3.58 Callback catch-up loop 已直接切到 transcript scan

已完成的核心点：

- native queued callback restore/catch-up 现在都不再依赖 `get_session_turn_state()` 这个外层摘要 helper
- `agent_service` 内部已补上一层更接近 OpenCode `prompt.ts` 的本地 history scan：
  - 直接遍历 persisted session messages
  - 直接找 `latest user / latest assistant / latest finished assistant`
  - 直接判断当前 session 是否还有 pending prompt
- `_callback_runtime_from_payload()` 和 `_run_callback_loop()` 现在共用这套 transcript scan 结果
- 这让 callback restore / callback catch-up 又往 OpenCode `MessageV2.stream(sessionID)` 驱动 loop 的形态逼近一步

当前限制：

- 目前还是 `agent_service` 内部自建的 transcript scan helper，不是完全复用一套 processor-owned history scan
- queued callback 的最终交付仍是本地 callback promise/control -> SSE replay 兼容层

### 3.59 Native system prompt 已补上 OpenCode-style provider/environment/skills 结构

已完成的核心点：

- native system prompt 现在不再只有单段 ResearchOS 本地说明：
  - 已前置接入 OpenCode 风格 provider-specific header 选择
  - 已前置接入 OpenCode 风格 environment section
  - 已前置接入 OpenCode 风格 skills section
- 当前 provider prompt 选择规则已经对齐到 `reference/opencode-dev/packages/opencode/src/session/system.ts`：
  - `gpt-5 -> codex_header`
  - `gpt-/o1/o3 -> beast`
  - `gemini -> gemini`
  - `claude -> anthropic`
  - `trinity -> trinity`
  - fallback -> `qwen`
- native `_normalize_messages()` 现在会像 OpenCode 一样注入多条 `system` message，而不是只拼一大段单一 system prompt
- skills section 现在会在 `skill` 工具未被 permission 禁用时，列出本地 skills，逼近 OpenCode `SystemPrompt.skills()` 的行为

当前限制：

- 当前仍保留了一层很薄的本地 adapter prompt，用于补 mode / remote workspace / user-selected skills 等运行时差异，还不是完全零本地层
- `agent_tools.py` 里仍和 OpenCode 一样以单 registry module 暴露工具定义，但 ResearchOS 扩展工具 handler 还没有真正搬到 skill/plugin 层

### 3.60 Default tool exposure 已继续收口到 OpenCode core

已完成的核心点：

- `get_openai_tools()` 的默认暴露集合现在只保留更接近 OpenCode `tool/registry.ts` 的 core tools：
  - `bash`
  - `read`
  - `write`
  - `edit`
  - `apply_patch`
  - `glob`
  - `grep`
  - `skill`
  - `task`
  - `todowrite`
  - `webfetch`
  - `websearch`
  - `codesearch`
- 这些不再默认暴露：
  - `list / ls / multiedit / todoread`
  - `search_web`
  - 论文库 / arXiv / 写作 / figure / RAG 等 ResearchOS 扩展工具
  - `list_local_skills / read_local_skill`
- 扩展工具 handler 仍然保留在本地实现中，但现在需要显式 opt-in 才会暴露：
  - `get_openai_tools(..., enabled_tools={...})`
  - `build_turn_tools(..., user_tools={tool_name: true})`
- 远程工作区上的 tool exposure 仍保留产品所需的 runtime 差异：
  - remote session 默认继续暴露 `inspect_workspace / read_workspace_file / write_workspace_file / replace_workspace_text / run_workspace_command / get_workspace_task_status`
  - 即使显式 opt-in，也不会在 remote workspace 上重新暴露 `bash / read / write / edit / list` 这类本地路径工具
- 新回归已经锁住：
  - 默认核心集不再漏出本地扩展工具
  - 扩展工具可显式启用
  - remote workspace 仍会过滤 local-only tools

当前限制：

- 这一步只是把“默认暴露面”压回 OpenCode core；ResearchOS 扩展 handler 还在同一个 `agent_tools.py` 模块里，没有像 OpenCode plugin/custom tool 那样完全分层
- 远程 SSH 工作区工具仍是 ResearchOS 产品能力，不属于 vanilla OpenCode core set

### 3.61 Model-specific edit tool selection / local prompt adapter 已继续对齐

已完成的核心点：

- `build_turn_tools()` 现在已补上更接近 OpenCode `tool/registry.ts` 的 model-specific edit tool 选择：
  - `gpt-*` 且非 `oss`、非 `gpt-4*` 时，优先暴露 `apply_patch`
  - 其他模型隐藏 `apply_patch`，继续暴露 `edit + write`
- 这让 GPT-5 路径不再同时看到 `apply_patch + edit + write` 三套重叠编辑工具，减少了和 OpenCode 相比的 tool-choice 噪音
- 本地 adapter prompt 也继续瘦身到运行时必需信息：
  - 默认简体中文
  - 严格遵守用户格式/长度要求
  - mode 只保留 build / plan / general 的最小提示
  - 仅在 remote workspace 时补一条 remote-tool hint
  - 仅在存在 user-selected `active_skill_ids` 时补一条 skill title 提示
- 已移除的本地 prompt 噪音包括：
  - 当前 session id
  - 当前权限模式
  - 当前推理档位
  - 当前已保存工作区列表
  - 当前待办摘要
  - 大段 ResearchOS 特有论文工具调用策略说明
- 新回归已经锁住：
  - GPT-5 优先 `apply_patch`
  - 非 GPT-5 模型优先 `edit/write`
  - system prompt 不再带 `当前待办 / 当前已保存工作区 / 当前权限` 这些本地 adapter 重描述

当前限制：

- 本地 adapter prompt 仍未完全消失，主要因为当前 runtime 仍有 mode / SSH workspace / preselected skills 这三类 OpenCode 原生没有的产品状态
- prompt 虽然已明显变薄，但本地技能选择仍不是 OpenCode `Skill.available(agent)` 那种完全由 agent 对象驱动的原生注入路径

### 3.62 Queued callback / permission continuation 已进一步统一到单 callback loop

已完成的核心点：

- `queue_prompt_callback()` 现在支持 `front=True`
  - 普通 queued prompt 仍按 FIFO 追加
  - permission confirm / reject 会插到队首，优先恢复当前被 pause 的 active session
- native permission confirm / reject 不再直接走一条独立的 HTTP-native continuation runner
  - 现在会先转成 `kind=permission` 的 queued callback
  - 再由 `SessionPromptProcessor._resume_queued_callbacks()` 进入同一个 callback loop
- `_run_callback_loop()` 现在可同时消费两类 callback：
  - `prompt`
  - `permission`
- `permission` callback 内部会：
  - 从 persisted pending action / transcript 恢复 continuation 上下文
  - 执行批准或拒绝后的 tool continuation
  - 再切回同一个 session loop 做后续 prompt catch-up
- 这让当前 native session 的两类续跑入口：
  - busy session 上的新 queued prompt
  - paused permission 的 confirm / reject
  已经都落到同一个 callback queue + single loop 消费模型中
- callback 的内部交付现在也更接近 OpenCode promise 模型：
  - callback runtime 只保存 `result + control`
  - SSE 只是 route edge 的重放兼容层
  - continuation 本身不再依赖另一条独立的 native HTTP runner 事实源
- 新回归已经锁住：
  - native permission 回复会进入 callback queue
  - permission callback 会以 `front=True` 抢在普通 queued prompt 前恢复当前 paused turn

当前限制：

- HTTP 路由层面对外仍然返回 SSE，而不是 OpenCode app/cli 内部那种直接消费 promise/message 对象
- `PromptCallback` 结构里仍保留 `items` 字段做兼容回放，但当前主链已经基本只依赖 `result + control`

### 3.63 Frontend 已切到 `/session/*` 原生协议

已完成的核心点：

- 前端 `AgentSessionContext` 发送消息不再走旧 `/agent/chat`
  - 已切到 `POST /session/{id}/message`
- 前端权限确认/拒绝不再走旧 `/agent/confirm` / `/agent/reject`
  - 已切到 `POST /session/{id}/permissions/{permission_id}`
- `SessionPromptRequest` 已补上前端所需的 native 字段透传：
  - `agent_backend_id`
  - `active_skill_ids`
- `/session/{id}/message` 现在会把 `active_skill_ids` 落到 user transcript 的 `activeSkillIDs`
  - 也会把 `agent_backend_id` 继续传给 `stream_chat()`
- `AgentSessionContext` 现在还会在切换/重载会话时，从 native session runtime 拉回真实历史：
  - `GET /session/{id}/message`
  - `GET /session/{id}/permissions`
- 这意味着前端当前主聊天链路已经切到 session runtime 事实源，而不是继续依赖 legacy `/agent/*` wrapper
- 前端构建已验证通过：
  - `npm run build`

当前限制：

- 当前前端仍会把聊天 UI state 同步存到本地 localStorage，作为切页/刷新恢复层；还不是纯后端 session store 单事实源
- `/agent/*` legacy routes 仍保留，主要用于兼容旧入口与测试，不再是主前端通路

### 3.64 Remote workspace revert / unrevert 已确认对齐

已完成的核心点：

- 本地已有的 remote patch diff / revert / unrevert 链路已再次确认：
  - remote patch diff 会保留 `workspace_server_id`
  - revert / unrevert 会走 `agent_workspace_ssh.remote_restore_file`
- 对应回归已经覆盖：
  - `test_remote_workspace_diff_revert_and_unrevert`
- 因此 remote SSH workspace 的 revert / unrevert 已不再属于当前与 OpenCode 的剩余阻断差异

当前限制：

- 当前 remote revert 仍建立在 ResearchOS 自己的 SSH workspace API 之上，而不是 OpenCode project/instance abstraction

### 3.65 Callback replay 已进一步收口到 result-first 交付模型

已完成的核心点：

- `queued callback` 主链现在已经不再依赖 live SSE item buffer 才能完成交付
  - active loop 内部继续只保存 `result + control`
  - route edge 需要 SSE 时，再由 callback outcome 重放
- 本轮继续把 callback / permission resume 的事件观察统一到 `PromptEventStreamDriver`
  - prompt active path
  - native permission resume path
  现在都走同一套：
  - parsed event observe
  - synthetic persistence event 注入
  - bus publish
- 这让 route edge 剩下的 SSE replay 更接近纯适配层，而不是 runtime 内部另一套事实源

当前限制：

- HTTP 对外协议仍是 SSE，因此 callback outcome 到 SSE 的重放适配层还保留
- `PromptCallback.items` 仍保留兼容能力，用于测试和极少量 fallback replay 场景

### 3.66 Native pending continuation 已进一步压成 transcript-first

已完成的核心点：

- `PendingAction` 已继续减薄：
  - 不再保存 native continuation 的 `messages`
  - 不再保存 native continuation 的 `tool_calls`
  - 不再保存 native continuation 的 `step_index / assistant_message_id / step_snapshot / step_usage`
- native permission resume 现在优先从 persisted transcript 反推：
  - pending assistant message
  - step context
  - unfinished tool call
- 当 transcript 尚未命中 tool part 时，会退回到 `permission_request.metadata/tool` 做最小工具重建
  - 这让 native continuation 更接近 OpenCode 的 session/message 事实源驱动，而不是本地 continuation blob 驱动
- ACP pending action 也不再单独依赖本地 `assistant_message_id` 字段
  - 统一回到 permission request 的 `tool.messageID`
- native runtime 已不再实际使用进程内 `_pending_actions` cache 作为事实源
  - `store/get/pop` 都直接走持久化 pending action store
  - 这把 permission pause/resume 的恢复语义进一步压回数据库 transcript / permission store

当前限制：

- `PendingAction` 这层持久化对象仍然存在，尚未完全消失到 OpenCode 那种更薄的 permission/session object

### 3.67 Provider error / runtime normalization 已补上第一版 `message-v2.fromError` 对齐

已完成的核心点：

- `normalize_error()` 现在不再只依赖字符串猜测
  - 会解析 `responseBody` / provider error JSON
  - 会提取更接近 OpenCode `fromError()` 的字段：
    - `message`
    - `type`
    - `code`
    - `param`
    - `status`
    - `statusCode`
- 新增 provider-structured 归一化能力：
  - context overflow body -> `ContextOverflowError`
  - auth body -> `AuthError`
  - rate limit / overload body -> `APIError(isRetryable=true)`
- retry 层现在可以直接复用这些 provider-specific 归一化结果，而不是只吃外层通用 HTTP 文本

当前限制：

- 目前仍是 Python runtime 里的 provider body parser 第一版，还不是 OpenCode 那种完整 SDK typed error 分发
- gateway transport / provider bucket 的真实 runtime 仍未完全消费这层 richer error metadata

### 3.68 Raw HTTP transport error 已补上结构化异常层

已完成的核心点：

- `llm_provider_http.raw_openai_compatible_post()` 不再把 upstream HTTP 错误压扁成普通 `RuntimeError`
- 现在会抛出带结构化上下文的 `ProviderHTTPError`：
  - `status_code`
  - `response_body`
  - `response_headers`
  - `message`
- `normalize_error()` 已补上对这类 transport error 的原生读取
  - transport 层的 `status/body/headers` 现在会直接进入统一 error normalization
  - raw HTTP fallback / gateway fallback 路径拿到的错误信息更接近 OpenCode `fromError()` 的 provider transport 语义
- 新回归已锁住：
  - raw HTTP transport 会抛出 `ProviderHTTPError`
  - `normalize_error()` 会正确把它归一化成带 headers/body/metadata 的 `APIError`
- raw HTTP runtime 现在还会附带更接近 OpenCode provider transport 的执行上下文：
  - `transport`
  - `bucket`
  - `provider`
  - `url`
  - gateway target 下的 `gateway`
- `responses(raw-http)` / `chat.completions(raw-http)` 的 runtime metadata 已接通到 `ProviderHTTPError.metadata`
  这让后续 retry / error normalization 能拿到更稳定的 transport 语义，而不只靠 message 文本猜测

当前限制：

- 目前这层主要覆盖 openai-compatible raw HTTP transport
- 还没有把所有 provider SDK 自身的 typed exception 全部拉平成 OpenCode 那种统一分发表


## 4. 验证结果

已执行：

- `python -m pytest -q`
- 最新全量结果：`430 passed`
- `python -m pytest tests/test_llm_client_provider_options.py tests/test_agent_session_retry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_project_engine_profiles.py -q`
- 最新 provider/runtime focused 结果：`68 passed`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- 最新聚合结果：`202 passed`
- `npm run build`

- `python -m py_compile packages/ai/agent_service.py packages/ai/session_lifecycle.py apps/api/routers/session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py -q`
- `npm run build`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- 最新聚合结果：`199 passed`
- `python -m py_compile packages/ai/agent_tools.py packages/ai/agent_service.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- 最新聚合结果：`197 passed`
- `python -m py_compile packages/ai/session_runtime.py packages/ai/agent_runtime_state.py apps/api/routers/session_runtime.py apps/api/routers/agent.py packages/storage/models.py packages/storage/repositories.py apps/api/main.py`
- `python -m pytest tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_agent_remote_workspace.py -q`
- `python -m py_compile packages/ai/permission_next.py packages/ai/agent_service.py packages/ai/session_runtime.py apps/api/routers/session_runtime.py apps/api/routers/agent.py`
- `python -m pytest tests/test_agent_permission_next.py -q`
- `python -m py_compile packages/ai/agent_tools.py packages/ai/agent_service.py packages/ai/session_runtime.py packages/storage/repositories.py apps/api/routers/session_runtime.py apps/api/routers/agent.py tests/test_agent_session_revert_diff.py`
- `python -m pytest tests/test_agent_session_revert_diff.py -q`
- `python -m py_compile packages/integrations/llm_client.py packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_llm_client_message_transform.py tests/test_agent_session_runtime.py`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/ai/session_runtime.py packages/ai/session_compaction.py apps/api/routers/session_runtime.py packages/integrations/llm_client.py tests/test_agent_session_compaction.py`
- `python -m py_compile packages/integrations/llm_client.py packages/integrations/llm_provider_schema.py packages/integrations/llm_provider_transform.py`
- `python -m py_compile packages/ai/opencode_manager.py packages/integrations/llm_client.py packages/integrations/llm_provider_schema.py packages/integrations/llm_provider_transform.py`
- `python -m pytest tests/test_llm_client_provider_options.py tests/test_llm_client_message_transform.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_llm_client_provider_options.py tests/test_llm_client_message_transform.py tests/test_project_engine_profiles.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_session_compaction.py -q`
- `python -m py_compile packages/config.py packages/ai/session_compaction.py packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_agent_session_compaction.py`
- `python -m pytest tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py -q`
- `python -m py_compile packages/integrations/llm_client.py packages/integrations/llm_provider_registry.py tests/test_llm_provider_registry.py`
- `python -m pytest tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py -q`
- `python -m py_compile packages/integrations/llm_client.py packages/integrations/llm_provider_probe.py tests/test_llm_client_probe.py`
- `python -m pytest tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py -q`
- `python -m py_compile packages/ai/agent_service.py packages/ai/session_lifecycle.py tests/test_agent_prompt_lifecycle.py`
- `python -m pytest tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile apps/api/routers/agent.py packages/ai/agent_service.py tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/integrations/llm_provider_http.py packages/integrations/llm_client.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py`
- `python -m pytest tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_summary.py packages/integrations/llm_client.py tests/test_llm_client_summary.py`
- `python -m pytest tests/test_llm_client_summary.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_embedding.py packages/integrations/llm_client.py tests/test_llm_client_embedding.py`
- `python -m pytest tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_stream.py packages/integrations/llm_client.py tests/test_llm_client_stream.py`
- `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py -q`
- `python -m py_compile packages/integrations/llm_provider_vision.py packages/integrations/llm_client.py tests/test_llm_client_vision.py`
- `python -m pytest tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_responses.py packages/integrations/llm_client.py tests/test_llm_client_message_transform.py`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_vision.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/ai/agent_service.py tests/test_agent_prompt_lifecycle.py`
- `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_responses.py packages/integrations/llm_client.py tests/test_llm_client_message_transform.py tests/test_llm_client_stream.py`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_vision.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_transform.py packages/integrations/llm_client.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/ai/session_errors.py packages/ai/session_retry.py packages/ai/agent_service.py tests/test_agent_session_retry.py`
- `python -m pytest tests/test_agent_session_retry.py tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_revert_diff.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/ai/session_retry.py tests/test_agent_session_retry.py`
- `python -m pytest tests/test_agent_session_retry.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_runtime.py packages/integrations/llm_client.py tests/test_llm_client_summary.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_vision.py tests/test_llm_client_probe.py`
- `python -m pytest tests/test_llm_client_summary.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_vision.py tests/test_llm_client_probe.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_runtime.py packages/integrations/llm_client.py tests/test_llm_client_probe.py`
- `python -m pytest tests/test_llm_client_probe.py tests/test_llm_client_summary.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_vision.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_provider_transform.py packages/integrations/llm_client.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_llm_client_dispatch.py tests/test_llm_client_probe.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_vision.py tests/test_llm_client_stream.py tests/test_llm_client_embedding.py tests/test_llm_client_summary.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/ai/agent_service.py tests/test_agent_permission_next.py`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_session_runtime.py -q`
- `python -m py_compile packages/ai/session_runtime.py tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py tests/test_agent_permission_next.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_llm_client_probe.py tests/test_llm_provider_registry.py tests/test_llm_client_dispatch.py tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py -q`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_session_compaction.py -q`
- `python -m py_compile packages/integrations/llm_client.py packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_revert_diff.py tests/test_agent_session_retry.py`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py -q`
- `python -m py_compile packages/ai/session_runtime.py packages/integrations/llm_client.py tests/test_agent_session_runtime.py`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py -q`
- `python -m py_compile packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_session_retry.py -q`
- `python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_remote_workspace.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py -q`
- `python -m py_compile packages/ai/session_snapshot.py packages/ai/session_runtime.py tests/test_agent_session_revert_diff.py`
- `python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_session_runtime.py tests/test_agent_session_retry.py -q`
- `python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_remote_workspace.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py -q`
- `python -m py_compile packages/storage/models.py packages/storage/repositories.py packages/ai/permission_next.py packages/ai/agent_service.py tests/test_agent_permission_next.py`
- `python -m pytest tests/test_agent_permission_next.py -q`
- `python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_remote_workspace.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py -q`
- `python -m py_compile packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_agent_session_compaction.py`
- `python -m pytest tests/test_agent_session_compaction.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_retry.py -q`
- `python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_session_compaction.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_retry.py -q`
- `python -m py_compile packages/ai/session_bus.py packages/ai/session_lifecycle.py packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_agent_prompt_lifecycle.py`
- `python -m pytest tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py -q`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py -q`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_agent_session_compaction.py tests/test_agent_prompt_lifecycle.py`
- `python -m pytest tests/test_agent_session_compaction.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py -q`
- `@'from apps.api.main import app; print('routes', len(app.routes))'@ | python -`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_remote_workspace.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/ai/session_runtime.py tests/test_agent_session_revert_diff.py`
- `python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/integrations/llm_client.py tests/test_llm_client_provider_options.py`
- `python -m pytest tests/test_llm_client_provider_options.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/ai/agent_service.py packages/ai/session_runtime.py tests/test_agent_session_runtime.py`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/domain/schemas.py apps/api/routers/agent.py packages/ai/session_runtime.py tests/test_agent_session_runtime.py tests/test_agent_remote_workspace.py`
- `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_remote_workspace.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_remote_workspace.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- `python -m py_compile packages/ai/global_bus.py packages/ai/session_bus.py packages/ai/session_runtime.py tests/test_agent_prompt_lifecycle.py`
- `python -m pytest tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_remote_workspace.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/ai/session_lifecycle.py apps/api/routers/global_routes.py apps/api/main.py tests/test_global_routes.py`
- `python -m pytest tests/test_global_routes.py -q`
- `@'from apps.api.main import app; print('routes', len(app.routes))'@ | python -`
- `python -m pytest tests/test_global_routes.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_remote_workspace.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/integrations/llm_client.py tests/test_llm_client_provider_options.py`
- `python -m pytest tests/test_llm_client_provider_options.py -q`
- `python -m pytest tests/test_global_routes.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_remote_workspace.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`

最新结果：

- `tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
  - `87 passed`
- `python -m py_compile packages/ai/workspace_executor.py packages/ai/permission_next.py packages/ai/agent_tools.py packages/ai/agent_service.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py`
- `python -m pytest tests/test_agent_permission_next.py -q`
- `python -m pytest tests/test_agent_session_runtime.py::test_session_prompt_executes_local_shell_and_continues -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m py_compile packages/ai/session_runtime.py apps/api/routers/session_runtime.py packages/ai/agent_service.py packages/integrations/llm_client.py tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py tests/test_agent_permission_next.py tests/test_llm_client_message_transform.py`
- `python -m pytest tests/test_agent_session_runtime.py::test_load_agent_messages_preserves_user_file_parts_system_and_tools -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py::test_session_prompt_accepts_file_only_parts_and_persists_tool_overrides -q`
- `python -m pytest tests/test_agent_permission_next.py::test_build_turn_tools_respects_latest_user_tool_overrides -q`
- `python -m pytest tests/test_llm_client_message_transform.py::test_build_openai_chat_messages_preserves_structured_user_text_and_image_parts tests/test_llm_client_message_transform.py::test_build_responses_input_supports_structured_user_text_and_file_parts -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
  - `93 passed`
- `python -m py_compile packages/integrations/llm_client.py tests/test_llm_client_message_transform.py`
- `python -m pytest tests/test_llm_client_message_transform.py::test_normalize_openai_chat_tools_drops_provider_defined_builtin_tools tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_adds_builtin_include_fields tests/test_llm_client_message_transform.py::test_chat_stream_openai_compatible_strips_provider_defined_tools tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_preserves_output_annotations_metadata -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
- `tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`
  - `98 passed`
- `python -m py_compile apps/api/routers/session_runtime.py packages/ai/session_runtime.py tests/test_agent_session_runtime.py`
- `python -m pytest tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py::test_queued_prompts_resume_in_fifo_order_without_leaking_later_user_prompts -q`
- `python -m pytest tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_agent_prompt_lifecycle.py -q`

结果：

- `tests/test_agent_session_runtime.py`：9 通过
- `tests/test_agent_remote_workspace.py`：3 通过
- `tests/test_agent_permission_next.py`：6 通过
- `tests/test_agent_session_revert_diff.py`：5 通过
- `tests/test_agent_session_compaction.py`：6 通过
- `tests/test_agent_session_retry.py`：2 通过
- `tests/test_agent_prompt_lifecycle.py`：6 通过
- `tests/test_agent_session_runtime.py`：11 通过
- `tests/test_agent_session_runtime.py`：22 通过
- `tests/test_agent_session_runtime.py`：24 通过
- 聚合 focused suite：`44 passed`
- provider transform focused suite：`13 passed`
- 聚合 focused suite（含 provider option transform / callback owner reuse / official OpenAI prompt cache key）：`51 passed`
- reasoning metadata focused suite：`22 passed`
- official OpenAI builtin exposure / local_shell focused suite：`87 passed`
- 聚合 focused suite（含 OpenAI Responses reasoning metadata persistence / replay）：`57 passed`
- 聚合 focused suite（含 OpenAI Responses assistant text / tool-call item metadata replay）：`65 passed`
- 聚合 focused suite（含 PromptInput.noReply prompt path 对齐 / revert busy guard 对齐 / OpenRouter-Venice cache key 对齐 / tool input lifecycle 对齐）：`72 passed`
- 聚合 focused suite（含 session message/part delete 路由与 queued callback assistant-row 初始化加固）：`74 passed`
- FastAPI 主入口可加载，新路由已注册（`routes 243`）
- `tests/test_agent_session_runtime.py + tests/test_agent_remote_workspace.py`：`32 passed`
- `tests/test_agent_prompt_lifecycle.py`：`12 passed`
- 聚合 focused suite（含 legacy /agent/chat structured transcript continuity / global bus mirror）：`104 passed`
- `tests/test_global_routes.py`：`3 passed`
- FastAPI 主入口可加载，新路由已注册（`routes 246`）
- 聚合 focused suite（含 global event / dispose routes）：`107 passed`
- `tests/test_agent_prompt_lifecycle.py`：`22 passed`
- 聚合 focused suite（含 callback single-runner drain / malformed callback guard）：`108 passed`
- `tests/test_agent_permission_next.py`：`16 passed`
- 聚合 focused suite（含 native confirm re-pause busy guard）：`109 passed`
- `tests/test_agent_prompt_lifecycle.py`：`23 passed`
- 聚合 focused suite（含 callback resolve/reject tail completion）：`110 passed`
- `tests/test_agent_prompt_lifecycle.py`：`24 passed`
- 聚合 focused suite（含 callback pending/resolved/rejected outcome semantics）：`111 passed`
- `tests/test_agent_prompt_lifecycle.py`：`25 passed`
- 聚合 focused suite（含 single callback worker ownership guard）：`112 passed`
- `tests/test_agent_prompt_lifecycle.py`：`27 passed`
- 聚合 focused suite（含 callback terminal tail reconstruction / completion message payload）：`114 passed`
- `tests/test_agent_prompt_lifecycle.py`：`28 passed`
- 聚合 focused suite（含 callback resolved-message replay fallback）：`115 passed`
- `tests/test_agent_prompt_lifecycle.py`：`29 passed`
- 聚合 focused suite（含 reject/error tail replay from callback outcome）：`116 passed`
- `tests/test_agent_prompt_lifecycle.py`：`30 passed`
- 聚合 focused suite（含 resolved message text backfill fallback）：`117 passed`
- `tests/test_agent_prompt_lifecycle.py`：`32 passed`
- 聚合 focused suite（含 atomic worker advance/release guard）：`119 passed`
- `tests/test_agent_prompt_lifecycle.py`：`35 passed`
- 聚合 focused suite（含 atomic owner handoff-finish / reject-finish semantics）：`122 passed`
- `tests/test_agent_prompt_lifecycle.py`：`36 passed`
- 聚合 focused suite（含 paused callback action_confirm reconstruction）：`123 passed`
- `tests/test_agent_permission_next.py`：`17 passed`
- `tests/test_agent_session_runtime.py + tests/test_agent_prompt_lifecycle.py`：`68 passed`
- 聚合 focused suite（含 callback payload-only resume / legacy agent request cursor passthrough）：`162 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`：`89 passed`
- 聚合 focused suite（含 owner-inline queued callback handoff）：`177 passed`
- `tests/test_llm_client_summary.py + provider focused suite`：`62 passed`
- 聚合 focused suite（含 provider summary execution adapter）：`166 passed`
- `tests/test_llm_client_embedding.py + provider focused suite`：`66 passed`
- 聚合 focused suite（含 provider embedding execution adapter）：`170 passed`
- `tests/test_llm_client_stream.py + provider focused suite`：`69 passed`
- `tests/test_llm_client_vision.py + provider focused suite`：`72 passed`
- 聚合 focused suite（含 provider streaming + vision execution adapter）：`176 passed`
- `tests/test_llm_client_message_transform.py + provider focused suite`：`72 passed`
- 聚合 focused suite（含 provider responses extractor adapter）：`177 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`：`89 passed`
- 聚合 focused suite（含 explicit queued callback inline resume）：`177 passed`
- `tests/test_llm_client_message_transform.py + tests/test_llm_client_stream.py + provider focused suite`：`72 passed`
- 聚合 focused suite（含 provider responses input builder adapter）：`177 passed`
- `tests/test_llm_client_message_transform.py + tests/test_llm_client_provider_options.py + provider focused suite`：`60 passed`
- 聚合 focused suite（含 provider chat message transform adapter）：`177 passed`
- `tests/test_llm_client_message_transform.py + tests/test_llm_client_provider_options.py + provider focused suite`：`60 passed`
- 聚合 focused suite（含 provider user/file content transform adapter）：`177 passed`
- `tests/test_agent_session_retry.py + lifecycle focused suite`：`103 passed`
- 聚合 focused suite（含 error normalization + retry-after delay parity）：`179 passed`
- `tests/test_agent_session_retry.py`：`6 passed`
- 聚合 focused suite（含 OpenCode retry backoff constants）：`180 passed`
- `tests/test_llm_client_summary.py + tests/test_llm_client_stream.py + tests/test_llm_client_embedding.py + tests/test_llm_client_vision.py + tests/test_llm_client_probe.py + provider focused suite`：`74 passed`
- 聚合 focused suite（含 provider public runtime adapter）：`180 passed`
- `tests/test_llm_client_probe.py + provider public runtime focused suite`：`74 passed`
- 聚合 focused suite（含 provider public test/probe adapter）：`180 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`（含 true single callback loop / explicit resume single-loop guard / session reasoning persistence / transcript-derived active skills）：`91 passed`
- 聚合 focused suite（含 true single callback loop + callback promise model）：`182 passed`
- `tests/test_agent_prompt_lifecycle.py`（含 minimal callback payload / session-derived resume config）：`37 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`（含 session-driven callback catch-up / final-result waiter resolve）：`93 passed`
- 聚合 focused suite（含 session-driven single callback loop + shared final callback result）：`184 passed`
- `tests/test_agent_prompt_lifecycle.py`（含 minimal callback payload / no-pending latest-result resolve）：`38 passed`
- `tests/test_agent_prompt_lifecycle.py`（含 native processor direct parsed-event persistence dispatch）：`39 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`（含 native direct persistence dispatch / session-driven callback catch-up）：`94 passed`
- 聚合 focused suite（含 native direct parsed-event persistence dispatch）：`185 passed`
- `tests/test_agent_permission_next.py`（含 session-history-driven native permission resume / rejected tool_result persistence）：`21 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`（含 native permission resume transcript-first continuation）：`95 passed`
- 聚合 focused suite（含 native permission resume transcript-first continuation）：`186 passed`
- `tests/test_agent_permission_next.py`（含 thin native pending action persistence）：`22 passed`
- `tests/test_agent_session_runtime.py`（含 no explicit request_message_id route passthrough）：`35 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`（含 thin native pending action + no request cursor passthrough）：`96 passed`
- 聚合 focused suite（含 thin native pending action + no request cursor passthrough）：`187 passed`
- `tests/test_agent_permission_next.py`（含 transcript-derived native pending continuation metadata）：`22 passed`
- `tests/test_agent_prompt_lifecycle.py`（含 callback/single-loop regression rerun）：`39 passed`
- `tests/test_agent_session_runtime.py`（含 persistence/session-state regression rerun）：`35 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py + tests/test_agent_permission_next.py`（含 native pending continuation metadata thinning）：`96 passed`
- 聚合 focused suite（含 native pending continuation metadata thinning）：`187 passed`
- `tests/test_agent_permission_next.py`（含 in-memory native pending cache thinning）：`22 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_session_runtime.py`（含 lifecycle/runtime rerun after cache thinning）：`74 passed`
- 聚合 focused suite（含 in-memory native pending cache thinning）：`187 passed`
- `tests/test_agent_prompt_lifecycle.py`（含 parsed-event direct control/persistence feed）：`39 passed`
- `tests/test_agent_permission_next.py`（含 native resume parsed-event direct feed）：`22 passed`
- `tests/test_agent_session_runtime.py`（含 persistence apply_event regression rerun）：`35 passed`
- 聚合 focused suite（含 native parsed-event direct feed）：`187 passed`
- `tests/test_agent_prompt_lifecycle.py`（含 strict session-derived callback restore）：`40 passed`
- `tests/test_agent_permission_next.py + tests/test_agent_session_runtime.py`（含 callback restore regression side-check）：`57 passed`
- 聚合 focused suite（含 strict session-derived callback restore）：`188 passed`
- `tests/test_agent_prompt_lifecycle.py`（含 transcript-scanned callback catch-up）：`40 passed`
- `tests/test_agent_permission_next.py + tests/test_agent_session_runtime.py`（含 transcript-scanned callback regression side-check）：`57 passed`
- 聚合 focused suite（含 transcript-scanned callback catch-up）：`188 passed`
- `tests/test_agent_session_runtime.py`（含 OpenCode-style provider/environment/skills prompt injection）：`36 passed`
- `tests/test_agent_prompt_lifecycle.py + tests/test_agent_permission_next.py`（含 prompt alignment regression side-check）：`62 passed`
- 聚合 focused suite（含 OpenCode-style provider/environment/skills prompt injection）：`189 passed`
- 聚合 focused suite（含 OpenCode-style core tool exposure / skill tool output）：`124 passed`
- `tests/test_agent_permission_next.py`：`19 passed`
- 聚合 focused suite（含 native apply_patch tool + permission path extraction）：`126 passed`
- `tests/test_agent_session_retry.py`：`3 passed`
- 聚合 focused suite（含 retry original error payload / AuthError normalization）：`127 passed`
- `tests/test_llm_client_provider_options.py`：`19 passed`
- 聚合 focused suite（含 provider smallOptions / namespace remap）：`113 passed`
- `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
  - 当前 frontend 仍存在仓库内既有类型错误，未形成一轮干净通过
  - 这次改动未出现指向新增 `AgentMessage` 结构化字段的专属报错
- `tests/test_agent_session_runtime.py`（含 OpenCode `plan/build` reminder 与 `max-steps` prompt 注入）：`40 passed`
- 聚合 focused suite（含 OpenCode prompt reminder / max-steps alignment）：`177 passed`
- `python -m pytest -q`：`416 passed`
- `tests/test_agent_session_runtime.py`（含 persisted transcript reload between steps）：`41 passed`
- 聚合 focused suite（含 processor-owned emit chain / transcript reload）：`178 passed`
- `python -m pytest -q`：`419 passed`

新增验证覆盖：

- Project / Session 路由创建与读取
- Todo 持久化
- 旧 `/agent/chat` 写入新 session store
- 新 `/session/{id}/message` 流式调用与消息落库
- `PromptInput.noReply` 只持久化 user message、不启动 prompt loop
- PermissionNext 的 `deny` / `once` / `always` / `reject`
- PermissionNext 的 pending request / pending action 持久化与 cache 恢复
- permission pause/resume 的 assistant pending tool part 与同 message 续写
- provider resolver 的 target / engine profile / embedding 推断
- provider transport policy 的 chat reasoning / Anthropic-Mistral target / raw HTTP fallback 判定

本轮新增验证：

- `python -m py_compile packages/integrations/llm_client.py packages/integrations/llm_provider_schema.py packages/integrations/llm_provider_resolver.py packages/integrations/llm_provider_transform.py tests/test_llm_client_resolution.py`
- `python -m pytest tests/test_llm_client_resolution.py tests/test_project_engine_profiles.py -q`
- `python -m py_compile packages/integrations/llm_client.py packages/integrations/llm_provider_policy.py tests/test_llm_client_transport_policy.py`
- `python -m pytest tests/test_llm_client_transport_policy.py tests/test_llm_client_resolution.py tests/test_llm_client_message_transform.py tests/test_llm_client_provider_options.py -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_permission_next.py tests/test_agent_session_retry.py tests/test_agent_session_revert_diff.py tests/test_project_engine_profiles.py -q`
- `python -m pytest tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py -q`
- 旧 `/agent/confirm` 续跑结果落库
- `session.fork` 的消息复制、parentID 重映射与截断复制
- `session.diff / revert / unrevert`
- revert 后下一轮 prompt 的 cleanup
- patch part 的持久化与 summary diff 重算
- snapshot-aware diff / revert / unrevert
- tool 执行后产生的本地外部文件改动可被 diff / revert / unrevert 捕获
- abort 后未 finish step 的 snapshot patch 仍可被 diff / revert / unrevert 捕获
- `session.summarize` 的 compaction message / summary message 持久化
- compaction 后下一轮 prompt 只读取压缩后的上下文，不再重复读取压缩前长历史
- `auto + overflow` compaction 的 replay user message 生成
- preflight auto-compaction 在新请求开始前自动触发
- context overflow error 后自动 compaction 并继续续跑
- 同一轮多步工具调用中的 post-step auto-compaction
- post-step auto-compaction 前 assistant checkpoint 持久化，以及 compaction 后 assistant message rollover
- prompt lifecycle 的 bus 事件发布
- 流式 `text/reasoning` part 的 `part.updated + part.delta` 事件发布，以及 delta `partID` 与最终落库 part 对齐
- prompt instance ownership、waiter completion 与 managed abort
- queued prompt 在前一轮 prompt 完整落库后重载最新历史继续执行
- 多条 queued prompt 的 FIFO handoff 与按 request user message 边界重载历史
- queued prompt 的 callback queue + auto resume 第一版
- busy 时的 queued prompt 已不再走 waiter handoff，而是由 runtime 自动启动下一轮 callback processor
- provider message normalization 第一版（Anthropic/Claude/Mistral）
- provider option transform 第一版（OpenAI GPT-5 / Gemini / Zhipu / DashScope / raw HTTP fallback / official OpenAI prompt cache key）
- OpenRouter / Venice 的 provider-specific prompt cache key 对齐
- `skim` smallOptions 对齐（GPT-5 / Gemini / OpenRouter / Venice）
- gateway `providerOptions()` namespace remap（含 `amazon/* -> bedrock`）
- `/global/dispose` 的目录级 `server.instance.disposed` 广播
- callback resume_existing owner reuse 第一版（queued callback 不再 release/reacquire prompt owner）
- official OpenAI Responses 的 `reasoning itemId / reasoningEncryptedContent` 提取
- 空 summary 的 OpenAI reasoning item 仍会持久化为可 replay 的空 reasoning part
- `_run_model_turn` 对 reasoning metadata 的 stable `partID + metadata` 透传
- `load_agent_messages()` 对 assistant `reasoning_parts` metadata 的重建
- `store=false` 场景下 OpenAI reasoning replay item 的按 `itemId` 合并回放
- official OpenAI Responses 的 assistant `text itemId` 提取、持久化与 replay
- official OpenAI Responses 的 assistant `function_call itemId` 提取、tool metadata 落库与 replay
- user `file-only prompt` 入库与 `tools` 元数据持久化
- `load_agent_messages()` 对 user `file/tools/system` 的结构化重建
- turn tool exposure 对最后一条 user `tools:false` 的消息级禁用
- OpenAI Responses / chat-completions 对结构化 user `text/file` parts 的输入转换
- legacy `/agent/chat` 对结构化 user `text/file` parts 与消息级 `tools/system/variant` 的透传与持久化
- OpenAI-compatible chat/raw fallback 对 `provider-defined` tool 的过滤
- OpenAI Responses 对 web search / code interpreter builtin `include` 的自动补齐
- OpenAI Responses assistant 输出 `annotations` metadata 的保留与透传
- session bus 事件到 global `{directory, payload}` 通道的镜像
- global SSE event stream 与 `global.dispose` 清理入口
- busy session 上 `revert / unrevert` 会被拒绝并返回 HTTP 400
- busy session 上 `session.deleteMessage` 会被拒绝并返回 HTTP 400
- `session.deleteMessage / part.delete` 的路由删除能力
- 普通 tool continuation 的 assistant checkpoint / rollover
- stream 未结束时的 assistant part 增量持久化
- 显式 `reasoning-start/end`、`text-start/end` 生命周期事件
- 显式 `tool-input-start/delta/end` 生命周期事件
- `text/reasoning` 的 stable `partID` 从 processor 透传到最终持久化 part
- 多段 `text` part 在显式边界下分段落库，而不是被合并成单一 text part
- permission pause 前 pending tool part 的 `state.raw/input` 持久化
- permission continuation 复用已有 pending tool part，避免同一 `callID` 重复落库
- overflow compaction 时瞬态 assistant reset 与 assistant rollover
- `step-start / step-finish` part 的持久化
- `reasoning` part 的流式落库与 `tokens.reasoning` 透传
- assistant 历史按 `step-start` 边界切分，并保留 `reasoning_content`
- assistant 历史中的 `tool` part 重建为 `tool_calls + tool result` transcript
- transient model error retry、`SessionStatus.retry` 与 `retry` part 持久化
- abort 时 assistant `error/finish` 落库
- 中止时未完成 tool part 的 error 收口
- retry 中止后的 aborted assistant message 持久化
- OpenCode `plan.txt / build-switch.txt` reminder 的 latest-user 注入
- OpenCode `max-steps.txt` 的最后一步 prompt 注入，优先让模型自行收束而不是直接本地截断
- `SessionPromptProcessor` 自己拥有 normal prompt loop 的 event/control/persistence 发射链，外层 `_stream_active()` 不再逐项桥接 raw prompt event
- persisted session prompt 在多步 tool continuation 之间会重新加载最新 transcript，新增 user message 可在下一步被直接看到
- native `SessionPromptProcessor` 与 permission resume 主链现在都不再经由 `SessionStreamPersistence.apply_event()` 解释 prompt-level event 才落库：
  - `assistant_message_id / session_parent / session_step_start / session_step_finish / session_assistant_commit / error / done`
  - 已统一改为 processor/service 侧直接调用 `SessionStreamPersistence` mutation helper
  - `PromptEventStreamDriver` 退回到 observe + serialize + 可插拔 mutator，更接近 OpenCode `processor -> message/part mutation` 的职责边界
- 本地验证补充：
  - `pytest tests/test_agent_prompt_lifecycle.py -q`：`43 passed`
  - `pytest tests/test_agent_session_retry.py -q`：`11 passed`
  - 实测前端 `PdfReader` 打开“已下载 PDF”和“后端临时拉取远程 PDF”两类论文，当前环境未复现阅读报错

## 5. 当前与 OpenCode 的剩余差异

当前仍存在这些关键差异：

- `/session/{id}/message` 仍复用现有 SSE 流式协议，不是 OpenCode `MessageV2.stream + bus` 那种原生订阅架构
- assistant frontend 虽然已经改成由 `AssistantInstanceStore` 快照驱动活动 session/workspace/title，但 session 列表与 route selection 仍靠本地 conversation metadata 维护，不是 OpenCode app 那种更彻底的全局 instance/session store
- `summarize` 和自动 overflow 已覆盖主要运行链路，但当前只有在 compaction 场景下才补 assistant checkpoint / rollover，不是 OpenCode `SessionProcessor` 那种全量逐步持久化
- `summarize` 和自动 overflow 已覆盖主要运行链路，普通 tool continuation 也已补 assistant checkpoint / rollover；但当前仍不是 OpenCode `SessionProcessor` 那种全量逐 part 持久化
- 当前 bus 已补到 global `{directory, payload}` 镜像层，也有了 `/global/event`、`/global/dispose` 和目录级 `server.instance.disposed` 广播；但还没有 OpenCode `Instance.reload()/dispose()/disposeAll()` 那种统一 project/instance manager 绑定
- `message part` 当前已接上 tool / compaction / step-start / step-finish / reasoning / retry / aborted tool lifecycle，以及 `text/reasoning/tool-input-raw part delta` 第一版语义；但仍未达到 OpenCode `message-v2.ts` 完整同构
- `abort` 目前是 managed + legacy fallback 的 cooperative 中止，不是 OpenCode prompt processor 级中止
- prompt instance 现在已有 owner/cancel、FIFO queued callback、permission-front callback、session-driven catch-up callback loop 和 final-result waiter resolve；内部 callback 交付已基本切到 `result + control` promise 模型，但外层 HTTP 仍保留 SSE replay wrapper
- 当前 native prompt/resume 路径已经把 prompt-level mutation 从 `SessionStreamPersistence.apply_event()` 收回 processor/service 层；但整体外部协议仍是 SSE event 名称流，不是 OpenCode `SessionProcessor` 直接暴露 `MessageV2` 对象流
- native permission confirm/reject 的继续执行虽然已并回同一个 callback loop，且 runtime 已不再依赖进程内 pending cache，也不再保存 `step_index / assistant_message_id / step_snapshot / step_usage` 等 native continuation 元数据；但底层仍保留 `PendingAction` 这一层本地持久化形态，不是 OpenCode 那种更薄的 permission/session object
- queued callback restore/catch-up 已经直接扫描 persisted transcript；但这套 history scan 仍是本地 helper，不是完全复用 OpenCode `loop()` 的统一入口
- native system prompt 已补上 provider/environment/skills 三段结构，默认 tool exposure 也已收口到 OpenCode core；但本地 adapter prompt 仍保留 mode / remote workspace / selected skills 这层最小产品差异，扩展工具 handler 也还没有完全迁到独立 skill/plugin 层
- `PermissionNext` 的 pending request / pending action 与 pause/resume assistant message 已持久化第一版，但还没有完全并入新的 processor / bus 生命周期
- `POST /session/{id}/permissions/{permission_id}` 目前对前端主链已切到 native session route，但 HTTP 返回形式仍是 SSE 续跑流，不是 OpenCode app/cli 的布尔 JSON + bus 消费模式
- 前端主聊天已经切到 `/session/*` 协议；旧 `/agent/*` 现在只剩兼容层角色，不再是主通路

## 6. 下一块

接下来按这个顺序继续补齐：

1. 继续把 processor / bus / instance 生命周期推进到更接近 OpenCode 的对象模型
   - 减少 route-edge SSE replay wrapper 的存在感
   - 继续把 part mutation 从 SSE event 语义压向 processor-owned state mutation
2. 继续补 provider/runtime 剩余缺口
   - 更完整的 SDK typed exception class 映射
   - gateway bucket/runtime 真实消费层
3. 继续减少本地 `PendingAction` / adapter prompt / extension tool handler 这些产品层差异
4. 如果要继续做 frontend 完全同构，下一步不是再堆页面效果，而是把会话列表/路由切换也并入统一 instance/session store，彻底拿掉 conversation metadata 这层 runtime ownership

在中途 compaction、processor 生命周期和 provider/runtime 同构完成前，不做质量完全对齐结论。
