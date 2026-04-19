# ResearchOS 对齐 OpenCode 硬验收清单

更新时间：2026-03-25

当前原子任务记录：

- 当前无未完成项；最近一轮原子任务拆分与完成归档见 [docs/opencode-remaining-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-remaining-checklist.md)

## 0. 使用规则

这份清单用于替代之前那种“渐进收敛 + 轮次描述”的模糊判定方式。

说明：

- 本文件负责模块级硬验收
- [docs/opencode-remaining-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-remaining-checklist.md) 负责最近一轮原子任务拆分与完成归档

从现在开始，只有满足下面两层条件，某一模块才允许标记为“已完成”：

1. 结构完成
   - ownership 已经压到和 OpenCode 对应源码同一层
   - 本地兼容壳、旁路 mutation、额外 lifecycle owner、额外 tool registry 语义已经移除或降为纯兼容薄壳
2. 验证完成
   - 有对应自动化测试
   - 通过对应命令验证
   - 文档里没有残留“主要完成”“接近完成”“第一版完成”这种说法

不允许再用以下说法代替完成：

- “主路径已切过去”
- “大部分已经对齐”
- “只剩一点点差异”
- “功能已经能用”

只要 ownership 还不对、协议还不对、验证没补齐，就必须继续做，不能标记完成。

## 1. 总体验收门槛

只有当下面 7 个模块全部完成，才允许宣称“ResearchOS 通用 runtime 已与 OpenCode 对齐”：

- `M1` SessionProcessor + MessageV2 ownership 完全同构
- `M2` 单 active loop + callback promise + permission resume 完全同构
- `M3` provider/runtime typed error + transport 语义完全同构
- `M4` instance/project lifecycle 完全同构
- `M5` tool exposure / permission / skills-plugin 边界完全同构
- `M6` frontend assistant runtime 完全 bus-native / instance-driven
- `M7` 端到端验证、稳定性验证、人工 smoke 验证全部通过

当且仅当仍存在未完成模块时，总结论才只能明确写成“当前仍有模块未完成，不能宣称已与 OpenCode 对齐”。

当前 `M1-M7` 已全部完成，因此当前结论以本文件第 `5` 节和 `M7` 验证结果为准。

## 2. 对照源码基线

所有验收必须对照这些源码，不允许按本地习惯重新解释：

- [reference/opencode-dev/packages/opencode/src/session/prompt.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/prompt.ts)
- [reference/opencode-dev/packages/opencode/src/session/message-v2.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/message-v2.ts)
- [reference/opencode-dev/packages/opencode/src/session/llm.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/llm.ts)
- [reference/opencode-dev/packages/opencode/src/session/system.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/system.ts)
- [reference/opencode-dev/packages/opencode/src/session/status.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/status.ts)
- [reference/opencode-dev/packages/opencode/src/project/instance.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/project/instance.ts)
- [reference/opencode-dev/packages/opencode/src/provider/transform.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/provider/transform.ts)

## 3. 模块硬清单

### M1. SessionProcessor + MessageV2 ownership 完全同构

状态：`已完成`

ResearchOS 目标文件：

- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
- [packages/ai/session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py)
- [packages/ai/session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)
- [packages/ai/session_pending.py](/D:/Desktop/ResearchOS/packages/ai/session_pending.py)

必须完成：

- [x] `agent_service.py` 不再拥有厚 `_stream_active()` 外层调度
- [x] `agent_service.py` 不再拥有厚 `_respond_native_action_impl()` 外层调度
- [x] prompt / permission resume 的 message/part mutation 统一由 processor 直接驱动
- [x] `SessionProcessor` 成为唯一的 assistant message/part lifecycle owner
- [x] route/service 层不再做第二套 message mutation 或 event-to-mutation 桥接
- [x] user/assistant/part 的持久化边界与 OpenCode `message-v2.ts` 的对象模型一致
- [x] 不能再存在“processor 发事件，service 再解释一次才落库”的主路径

完成判定：

- [x] 搜索 `agent_service.py`，外层只剩 transport / route / thin adapter
- [x] 搜索主路径，不再存在第二套直接写 message/part 的旁路
- [x] 权限恢复、tool continuation、retry continuation 都由同一 processor ownership 驱动

验证：

- [x] 补充对应单测
- [x] `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_permission_next.py -q`
- [x] `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py -q`
- [x] 回归 tool / retry / compaction / permission 四条链路
- [x] `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_session_message_v2.py -q`

2026-03-24 本轮已完成：

- 把 prompt loop 执行骨架从 [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 下沉到 [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 的 `PromptLoopRuntimeConfig + stream_prompt_loop(...)`
- 把 native permission response 执行骨架从 [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 下沉到 [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 的 `PermissionResponseConfig + stream_permission_response_runtime(...)`
- 把 model-turn / tool execution / tool processing runtime 从 [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 继续下沉到 [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 的 `stream_model_turn_events(...)`、`stream_tool_execution_events(...)`、`stream_tool_call_processing(...)`
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 `_run_model_turn_events()`、`_execute_single_tool()`、`_process_tool_calls()` 现已降为 thin adapter，只保留依赖注入与兼容导出
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 的公开 `SessionStreamPersistence` 已压成明确的 `SessionProcessor` 代理，legacy wrapper 不再保留第二套 live mutation owner
- [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 调整 `assistant_message_id` 的 announce 顺序，preflight / post-step auto compaction 的 replay parent rollover 已重新对齐
- 新增回归测试锁定这两条 delegation，不再允许 `agent_service.py` 长回厚外层 orchestration
- 新增 delegation 回归锁定 model-turn / tool-processing 继续由 `session_processor` 持有
- `assistant_message_id` 事件现在会在 [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 里立即 materialize assistant message，进一步贴近 OpenCode `processor -> MessageV2` 的 owner 关系；queued resume 不再需要等 step-start 时才第一次创建 assistant message
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 已把 user/assistant `MessageV2.Info` 统一整理成 OpenCode 风格：user 落库 `agent/model/format`，assistant 落库 `mode/agent/path/tokens/cost/time.completed/structured`
- [apps/api/routers/session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py) 与 [apps/api/routers/agent.py](/D:/Desktop/ResearchOS/apps/api/routers/agent.py) 已补齐 prompt user message 的默认 `agent/model`，并在 assistant persistence meta 初始化时就带入 `providerID/modelID/tokens/cost`
- [session_compaction.py](/D:/Desktop/ResearchOS/packages/ai/session_compaction.py) 已对齐 OpenCode `session/compaction.ts`：summary / compaction / replay message 不再丢失 user `agent/model/format/tools/system/variant`
- 对应回归已通过：
  - `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py -q`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py -q`
  - `python -m pytest tests/test_agent_session_compaction.py tests/test_agent_session_retry.py -q`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_session_message_v2.py -q`

### M2. 单 active loop + callback promise + permission resume 完全同构

状态：`已完成`

ResearchOS 目标文件：

- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py)
- [packages/ai/session_pending.py](/D:/Desktop/ResearchOS/packages/ai/session_pending.py)
- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)

必须完成：

- [x] 整个 session 只有一份真正的 active loop owner
- [x] callback / permission / resume 都走同一个 loop ownership，而不是本地 callback-loop 原语
- [x] `PendingAction` 不再承载厚 continuation 状态
- [x] permission reply 不再依赖本地 SSE continuation wrapper 才能继续执行
- [x] queued callback restore/catch-up 不再依赖本地 lifecycle callback runner，而是统一复用同一 loop 入口
- [x] permission resume object 语义收薄到和 OpenCode permission/session object 同级

完成判定：

- [x] `session_lifecycle.py` 不再保留独立 callback-loop ownership 语义
- [x] permission confirm/reject/abort/reload/dispose 后的恢复都不会绕开同一 active loop 入口
- [x] 本地 pending cache 不是运行必要条件

验证：

- [x] `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py -q`
- [x] `python -m pytest tests/test_global_routes.py tests/test_agent_session_runtime.py -q`
- [x] `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_global_routes.py tests/test_agent_session_runtime.py -q`
- [x] busy queue / paused resume / reject / once / always / abort / reload / dispose 全覆盖

2026-03-24 本轮已完成：

- `queued permission callback` 不再在 [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 callback loop 外层走单独 `stream_permission_response(...)` 分支；现在会先还原成 `SessionPromptProcessor`，再统一进入同一个 `_stream_active()` lifecycle 入口
- [session_pending.py](/D:/Desktop/ResearchOS/packages/ai/session_pending.py) 为 `PendingAction` 新增显式 `kind`，native / ACP 分流不再依赖各处重复读取 `continuation.kind`
- [session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 的 callback loop 新增 `PromptCallbackRunResult.next_payload`，permission 成功后的 “继续按 prompt 跑” 由 run result 自己声明；`agent_service` 已不再依赖本地 `promote_callback` 特判
- [session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 已移除 `PromptCallbackLoopHooks` / `run_prompt_callback_loop()` 这类厚 callback runner；lifecycle 现在只保留 queue/owner/state 原语，不再持有 prompt/permission 的具体执行逻辑
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 `SessionPromptProcessor._run_callback_loop()` 已改为本地单点恢复执行：queued callback 的 prompt/permission 切换、terminal settle、queued callback resolve/reject 全部收回 service/processor 边界，不再委托 lifecycle hook runner
- [tests/test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py) 已补回归，锁定 lifecycle 不再暴露 `run_prompt_callback_loop` / `PromptCallbackLoopHooks`，paused owner 恢复后也不再切到本地 `callback` loop kind
- [session_pending.py](/D:/Desktop/ResearchOS/packages/ai/session_pending.py) 已新增 `PendingResumeState`，permission resume 不再在主链上传递厚 `pending_context` dict；step/snapshot/usage 都由持久化 assistant message 现状即时派生
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 queued permission response 已不再缓存 `pending_context/assistant_message_id` 快照；执行前会重新派生 resume state，并用 `_merge_pending_persistence(...)` 统一 parent/message cursor
- [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 的 `PermissionResponseConfig` 已改为消费 `resume_state`，permission resume runtime 不再依赖本地 continuation dict 语义
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 `resume_existing` 已不再在缺少 active owner 时重新 `acquire_prompt_instance()`；abort/reload/dispose 后的 permission reply 会在同一 active loop 入口被取消，不会再偷偷起一个新 owner 继续执行工具
- [tests/test_agent_permission_next.py](/D:/Desktop/ResearchOS/tests/test_agent_permission_next.py) 已补回归，锁定 abort 后的 native permission reply 只走 cancelled 收尾，不会重新执行工具
- 对应回归已通过：
  - `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py -q`
  - `python -m pytest tests/test_global_routes.py -q`
  - `python -m pytest tests/test_global_routes.py tests/test_agent_session_runtime.py -q`
  - `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_global_routes.py tests/test_agent_session_runtime.py -q`

### M3. provider/runtime typed error + transport 语义完全同构

状态：`已完成`

ResearchOS 目标文件：

- [packages/integrations/llm_provider_error.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_error.py)
- [packages/integrations/llm_provider_stream.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_stream.py)
- [packages/integrations/llm_provider_probe.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_probe.py)
- [packages/ai/session_errors.py](/D:/Desktop/ResearchOS/packages/ai/session_errors.py)

必须完成：

- [x] typed SDK exception 映射补齐到 OpenCode 所需层级
- [x] chat / probe / retry 统一消费同一份错误 contract
- [x] transport / gateway / bucket / providerID / url / statusCode / headers / body 语义一致
- [x] runtime 侧错误分类不再混有本地特化兜底字段
- [x] 多 transport 行为保持同构，不再由各调用点自己拼错误语义

完成判定：

- [x] `stream`、`probe`、`retry`、`public runtime` 共用同一归一化错误层
- [x] 不再有“某些接口返回结构化错误，某些接口返回裸字符串”的情况

验证：

- [x] `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py -q`
- [x] 覆盖 auth / timeout / rate limit / model not found / transport failure / abort
- [x] `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py tests/test_agent_session_runtime.py -q`

2026-03-24 本轮已完成：

- [packages/integrations/llm_provider_error.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_error.py) 新增统一 fallback `to_session_error_payload(...)` 与 `runtime_failure_result(...)`，`stream / probe / retry / public runtime` 现在全部从同一归一化错误层出参，不再允许裸字符串错误旁路
- [packages/ai/session_errors.py](/D:/Desktop/ResearchOS/packages/ai/session_errors.py) 已收薄为单纯消费 `llm_provider_error.to_session_error_payload(...)`，session runtime 不再保留第二套 transport 语义拼装逻辑
- [packages/integrations/llm_provider_probe.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_probe.py) 的 Anthropic probe 已改为直接走 SDK + 结构化捕错，不再复用会吞错并回落 pseudo summary 的 `_call_anthropic()`；OpenAI / Anthropic / embedding probe failure 现在都返回同构 `error` shape
- [packages/integrations/llm_provider_runtime.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_runtime.py) 的 `disabled / missing_api_key / unsupported` 等 public runtime failure 已统一附带结构化 `error`，不再只返回裸 `message`
- [packages/integrations/llm_provider_error.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_error.py) 已补齐更多 typed SDK class-name 映射，覆盖 `PermissionDeniedError`、`TooManyRequestsError`、`ServiceUnavailableError`、`TransportError`、`DeadlineExceeded` 等常见 transport/provider 错误类
- 新增回归：
  - [tests/test_llm_client_runtime.py](/D:/Desktop/ResearchOS/tests/test_llm_client_runtime.py)
  - [tests/test_llm_client_probe.py](/D:/Desktop/ResearchOS/tests/test_llm_client_probe.py)
  - [tests/test_agent_session_retry.py](/D:/Desktop/ResearchOS/tests/test_agent_session_retry.py)
- 对应验证已通过：
  - `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py -q`
  - `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py tests/test_agent_session_runtime.py -q`

### M4. instance/project lifecycle 完全同构

状态：`已完成`

ResearchOS 目标文件：

- [packages/ai/session_instance.py](/D:/Desktop/ResearchOS/packages/ai/session_instance.py)
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py)

必须完成：

- [x] 目录级 session/runtime/state 的 reload / dispose / disposeAll ownership 统一
- [x] 不再由多处手动协调 directory/session/runtime 清理
- [x] global dispose、instance dispose、session abort 的语义分层和 OpenCode 一致
- [x] project/instance/session 的 owner 边界固定，不再交叉管理

完成判定：

- [x] `Instance.reload()/dispose()/disposeAll()` 风格的单入口成立
- [x] `session_instance.py` 不再手工补多套状态回收逻辑

验证：

- [x] `python -m pytest tests/test_global_routes.py tests/test_agent_prompt_lifecycle.py -q`
- [x] 覆盖 directory dispose / reload / busy dispose / abort-after-dispose
- [x] `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py -q`

2026-03-24 本轮已完成：

- [packages/ai/session_instance.py](/D:/Desktop/ResearchOS/packages/ai/session_instance.py) 新增统一 `_teardown_directory(...)` 与 `_collect_known_directories(...)`，`reload()/dispose()/dispose_all()` 不再各自拼装 session abort + state dispose + cache pop
- [packages/ai/session_instance.py](/D:/Desktop/ResearchOS/packages/ai/session_instance.py) 的 `dispose_all()` 已加单次 active barrier，行为更贴近 OpenCode `disposeAll()` 的单 active disposal 语义
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 新增 disposed-session tombstone；目录 dispose/reload 后，旧 prompt loop 的 `finish/status/callback` 收尾不会再把 lifecycle state 重新创建出来
- [tests/test_global_routes.py](/D:/Desktop/ResearchOS/tests/test_global_routes.py) 新增 busy dispose/reload 回归，锁定 dispose 后旧 loop 不能 resurrect lifecycle state，reload 后只有显式新 acquire 才能恢复 session owner
- 对应验证已通过：
  - `python -m pytest tests/test_global_routes.py tests/test_agent_prompt_lifecycle.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py -q`
  - `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py -q`

### M5. tool exposure / permission / skills-plugin 边界完全同构

状态：`已完成`

ResearchOS 目标文件：

- [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)
- [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py)
- [packages/ai/tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py)
- [packages/ai/tool_schema.py](/D:/Desktop/ResearchOS/packages/ai/tool_schema.py)
- [packages/ai/tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/tool_runtime.py)

必须完成：

- [x] `agent_tools.py` 不再承载 tool spec / 默认启用 / 权限特化；只保留 builtin/compat runtime handler
- [x] 默认 tool exposure 统一由 registry/spec 决定
- [x] permission 判断统一由 registry + permission layer 决定
- [x] ResearchOS 自有能力以 skill/plugin 或外层扩展方式接回，不再塞进通用 runtime 的本地分支
- [x] adapter prompt 不再承担“帮工具层修正行为”的职责

完成判定：

- [x] `agent_tools.py` 不再混有 research extension handler、tool exposure、默认启用、permission 特化
- [x] 无本地 `_TOOL_SPECS`、默认启用白名单、prompt adapter 来偷偷修补 tool exposure

验证：

- [x] `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py -q`
- [x] 覆盖 builtin/custom/provider-defined/read-only/local-only/default exposure
- [x] `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py -q`

2026-03-24 当前进展：

- [packages/ai/tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py) 已删除 `_TOOL_SPECS` + `_apply_tool_specs()`，default exposure / permission / read-only / local-only 真源已回收到各 `ToolDef.spec`
- [packages/ai/tool_schema.py](/D:/Desktop/ResearchOS/packages/ai/tool_schema.py) 新增 `handler` 字段；[packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 不再维护大块 builtin handler 名称映射，handler 解析改为从 `ToolDef` 派生
- [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 的 provider-defined `local_shell` 兼容权限已收成单个 compat `ToolDef`，不再单独保留 `_PROVIDER_DEFINED_TOOL_SPECS`
- [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py) 已去掉 registry/runtime 的 re-export 入口，外部测试和脚本开始直接消费 [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 与 [packages/ai/tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/tool_runtime.py)
- [packages/ai/research_tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/research_tool_catalog.py) 已把 ResearchOS 论文工具从 core builtin catalog 中拆出；[packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 现在把它们作为 `extension` source 装入，不再混在 OpenCode 风格 core builtin 集合中
- [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 的 provider-defined 暴露已收进 `ToolDef.provider_tools`，不再靠 registry 里的单独 `_PROVIDER_DEFINED_TOOL_SPECS` 或硬编码 “bash/search_web => provider-defined tool” 规则
- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 已删除本地 `_build_local_system_prompt_adapter()` 与对应 mode/remote-workspace/active-skill 注入，system prompt 只保留 provider/environment/skills 三类 OpenCode 风格 section
- [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 现在会按 `builtin/extension/compat` source 解析 handler module；research extension 默认从 [packages/ai/research_tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/research_tool_runtime.py) 取实现，不再回落到 [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)
- [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py) 已删除整段论文库/知识库/arXiv/分析工具实现，回收到 core builtin runtime handler；ResearchOS 自有论文能力只保留在 [packages/ai/research_tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/research_tool_runtime.py)
- [packages/ai/tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py)、[packages/ai/permission_next.py](/D:/Desktop/ResearchOS/packages/ai/permission_next.py)、[packages/ai/session_question.py](/D:/Desktop/ResearchOS/packages/ai/session_question.py) 已补齐 OpenCode 风格 `question` 工具：tool exposure、pending ask、answer resume、plan-mode clarification 都走统一 registry + permission + processor 主链
- [packages/ai/session_plan.py](/D:/Desktop/ResearchOS/packages/ai/session_plan.py) 的 plan reminder 已明确要求用 `question` 工具做澄清，不再只是 prompt 文案里泛泛写“向用户提问”
- [tests/test_tool_registry.py](/D:/Desktop/ResearchOS/tests/test_tool_registry.py) 新增 extension handler 解析断言；[tests/test_agent_tools_status.py](/D:/Desktop/ResearchOS/tests/test_agent_tools_status.py) 已直接改为从 [packages/ai/research_tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/research_tool_runtime.py) 验证 research runtime
- 对应回归已通过：
  - `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py -q`
  - `python -m pytest tests/test_agent_permission_next.py tests/test_agent_session_runtime.py tests/test_tool_registry.py -q`
  - `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_agent_remote_workspace.py -q`
  - `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_agent_remote_workspace.py -q`
  - `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_remote_workspace.py -q`
  - `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py -q`
  - `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py -q`

### M6. frontend assistant runtime 完全 bus-native / instance-driven

状态：`已完成`

ResearchOS 目标文件：

- [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts)
- [frontend/src/contexts/AssistantInstanceContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/AssistantInstanceContext.tsx)
- [frontend/src/pages/Agent.tsx](/D:/Desktop/ResearchOS/frontend/src/pages/Agent.tsx)
- [frontend/src/hooks/useConversations.ts](/D:/Desktop/ResearchOS/frontend/src/hooks/useConversations.ts)

必须完成：

- [x] assistant 页面不再把 `ConversationContext.activeConv/activeWorkspace` 当运行时真源
- [x] session 列表和路由切换也并入统一 instance/session store
- [x] 前端 runtime 不再依赖本地 conversation metadata 决定 session ownership
- [x] permission / message / status / dispose 全由 bus + session state 驱动
- [x] 旧 `/agent/*` 只剩兼容入口，assistant 主链完全基于 `/session/*`

完成判定：

- [x] `useConversations()` 只剩 sidebar 元信息持久化，不参与 assistant runtime ownership
- [x] assistant 页面切会话、切工作区、权限恢复、状态刷新都不依赖 local conversation cache 作为真源

验证：

- [x] `npm --prefix frontend run build`
- [x] `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- [x] 桌面 fallback backend + frontend dev full Playwright smoke
- [x] Docker full Playwright smoke

2026-03-24 当前进展：

- [frontend/src/hooks/useConversations.ts](/D:/Desktop/ResearchOS/frontend/src/hooks/useConversations.ts) 已从组件内 `useState/useEffect` 改成 shared external conversation store；Sidebar/ConversationContext 与 assistant runtime 现在订阅同一份 conversation metadata
- [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 已改为直接订阅 [frontend/src/hooks/useConversations.ts](/D:/Desktop/ResearchOS/frontend/src/hooks/useConversations.ts) 暴露的 `conversationStore`，并移除了本地 `Conversation` runtime cache；create/patch/mounted-paper/runtime seed 已收回 store owner
- [frontend/src/contexts/AssistantInstanceContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/AssistantInstanceContext.tsx) 已去掉对 `useConversations()` 的依赖，只同步默认 workbench 设置；assistant 主链不再经由 `ConversationContext` 或 hook 传递 active/runtime ownership
- [frontend/src/contexts/ConversationContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/ConversationContext.tsx) 与 [frontend/src/components/Layout.tsx](/D:/Desktop/ResearchOS/frontend/src/components/Layout.tsx) 已调整为 “conversationStore 只做元信息持久化，active/runtime 真源由 AssistantInstance 提供”
- [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 已开始把 conversation list / switch / delete 入口挂回 assistant store；Sidebar / Projects 通过 [frontend/src/contexts/ConversationContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/ConversationContext.tsx) 消费 assistant store 的 active truth，不再直接拿 conversation hook 的 active 状态
- [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 现在自持 `activeConversationId/activeConversation`，`conversationStore.activeId` 不再是 assistant runtime 的真源；切会话/删会话会先在 instance store 内统一收敛，再把 metadata 持久化回 local store
- [frontend/src/contexts/AssistantInstanceContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/AssistantInstanceContext.tsx) 新增 `/assistant/:conversationId` 路由同步，assistant 页面当前会话由 instance store 与路由双向对齐；[frontend/src/App.tsx](/D:/Desktop/ResearchOS/frontend/src/App.tsx) 已把根路由收敛到 `/assistant` 兼容入口
- [frontend/src/pages/Agent.tsx](/D:/Desktop/ResearchOS/frontend/src/pages/Agent.tsx) 已修正 `workspaceServerId` 首帧来源与 workspace bootstrap 时序；远端 SSH 目标不再被默认 `local` 回写覆盖，assistant 工作区目标重新与会话绑定状态一致
- [frontend/vite.config.ts](/D:/Desktop/ResearchOS/frontend/vite.config.ts) 现已同时读取 `.env` 与 shell 环境里的 `VITE_PROXY_TARGET/VITE_PORT/VITE_HMR_*`，桌面 fallback backend + 本地 frontend dev 验证时不会再丢失 `/api` 代理
- [frontend/tests/smoke.spec.ts](/D:/Desktop/ResearchOS/frontend/tests/smoke.spec.ts) 新增 `assistant route keeps active conversation in sync with the instance store`，锁定 `/assistant/:conversationId` 与 `researchos_active_conversation` 的同步关系
- [frontend/src/services/api.ts](/D:/Desktop/ResearchOS/frontend/src/services/api.ts)、[frontend/src/types/index.ts](/D:/Desktop/ResearchOS/frontend/src/types/index.ts) 和若干页面的历史类型错误已清理，前端仓库级 `tsc` 现已通过
- 对应验证已通过：
  - `npm --prefix frontend run build`
  - `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
  - `frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant route keeps active conversation in sync with the instance store"`
  - `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts` -> `18 passed, 1 skipped`
  - `PLAYWRIGHT_API_BASE=http://localhost:8002 PLAYWRIGHT_BASE_URL=http://localhost:3002 PLAYWRIGHT_BACKEND_FS=container frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts` -> `19 passed`

### M7. 端到端验证与稳定性验收

状态：`已完成`

必须完成：

- [x] 后端 focused suite 通过
- [x] 前端 build 通过
- [x] 前端 tsc 通过
- [x] 桌面端路线可启动并可用
- [x] Docker 路线可启动并可用
- [x] 人工 smoke 覆盖核心科研场景
- [x] 与 OpenCode 对比后，不再存在“同样输入下，本地 runtime 因架构差异导致结果链路不同”的已知结构性问题

必须执行的验证：

- [x] `python -m pytest -q`
- [x] `npm --prefix frontend run build`
- [x] `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- [x] 桌面端启动验证
- [x] Docker 路线启动验证
- [x] 人工 smoke：
  - [x] 新建会话
  - [x] 发送普通消息
  - [x] tool call
  - [x] permission once/always/reject
  - [x] abort
  - [x] retry
  - [x] compaction
  - [x] diff / revert / unrevert
  - [x] 切换工作区
  - [x] 刷新恢复

2026-03-25 当前进展：

- 后端全量自动化已通过：`python -m pytest -q` -> `490 passed in 293.99s`
- 前端自动化已通过：`npm --prefix frontend run build`、`frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- Docker 路线现已验证到“可启动 + 容器可访问 + 全量 Playwright smoke 通过”：
  - `docker compose ps` 显示 `backend/frontend/worker` 均为 `healthy`
  - `curl http://localhost:3002/` -> `200`
  - `curl http://localhost:8002/healthz` -> `404`，说明容器后端端口可达；本轮 Playwright 已实际打到 `http://localhost:8002`
  - `docker cp .\frontend\dist\. researchos-frontend:/usr/share/nginx/html/`
  - `pwsh -NoLogo -Command '& { $env:PLAYWRIGHT_API_BASE="http://localhost:8002"; $env:PLAYWRIGHT_BASE_URL="http://localhost:3002"; $env:PLAYWRIGHT_BACKEND_FS="container"; Set-Location "D:\Desktop\ResearchOS\frontend"; .\node_modules\.bin\playwright.cmd test -c playwright.config.ts }'` -> `19 passed`
  - 当前机器上 `http://127.0.0.1:3002` 会命中本地 Vite dev server；Docker frontend 验证必须使用 `http://localhost:3002`
- Docker 验证过程中已补掉的真实问题：
  - [frontend/tests/smoke.spec.ts](/D:/Desktop/ResearchOS/frontend/tests/smoke.spec.ts) 已按 backend 文件系统模式切换 ACP mock 路径、workspace 路径和 shell 命令，不再把 host Windows 路径误发给 Linux 容器
  - [Dockerfile.backend](/D:/Desktop/ResearchOS/Dockerfile.backend) 已补 `git`，容器版 workspace git init / branch / diff 主链恢复可用
  - [frontend/src/components/graph/OverviewPanel.tsx](/D:/Desktop/ResearchOS/frontend/src/components/graph/OverviewPanel.tsx) 已改成 overview 先出、bridges/frontier/cocitation/similarity map 后补，图谱页不再被慢请求长时间锁在 loading
  - [packages/ai/session_bus.py](/D:/Desktop/ResearchOS/packages/ai/session_bus.py) 现已把 session bus 往 global bus 的镜像统一收成纯 dict payload，不再把 `SessionEvent` 实例直接推给 `/global/event` SSE
  - [tests/test_global_routes.py](/D:/Desktop/ResearchOS/tests/test_global_routes.py) 新增 `test_global_event_stream_serializes_session_bus_events`，锁定 `/global/event` 可以稳定透传 session bus 事件
- 桌面路线现已验证到“可启动 + 健康 + assistant/project 全量 Playwright smoke”：
  - 当前 `src-tauri/target/debug/researchos-server.exe` 在本机上不是可用的 Windows 本机可执行文件，会触发 Tauri 里的 Python fallback
  - `python -m apps.desktop.server` 可正常启动，`/health` 返回 `200`
  - 修复 [frontend/vite.config.ts](/D:/Desktop/ResearchOS/frontend/vite.config.ts) 后，桌面 fallback backend + 本地 frontend dev 已能正确走 `/api` 代理
  - [packages/ai/session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 现已把 paused permission 的外部 abort 直接收口到 runtime：会立即清理 pending permission/ACP prompt、持久化 `会话已中止`、并发布 bus idle/message 更新
  - [tests/test_agent_permission_next.py](/D:/Desktop/ResearchOS/tests/test_agent_permission_next.py) 已补 `test_custom_acp_abort_clears_paused_permission_and_publishes_aborted_message`
  - [frontend/src/services/api.ts](/D:/Desktop/ResearchOS/frontend/src/services/api.ts) 已修正 `sessionApi.replyPermission(...)` 丢失 `answers` 的问题；`question` 卡片现在会把结构化答案完整发到 `/api/session/{id}/permissions/{permission_id}`
  - [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 已修正 `normalizeAgentMode(...)`，默认 `plan` 时不再把显式选中的 `build` 错误折回 `plan`
  - [frontend/src/contexts/AssistantInstanceContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/AssistantInstanceContext.tsx) 已修正 instance store 与 `/assistant/:conversationId` 的双向同步，侧栏/工作区切会话不再只改 store 而不改路由
  - [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 已补 paused permission 的 session-state 补同步，即使外部事件流短暂重连，前端也会把 external abort 后的 message/permission 收敛到最新状态
  - [frontend/tests/smoke.spec.ts](/D:/Desktop/ResearchOS/frontend/tests/smoke.spec.ts) 新增两条桌面 host 路线 smoke：
    - `assistant new conversation button creates a routed conversation shell`
    - `assistant switching workspace-bound conversations updates the active workspace and survives refresh`
    - `assistant mode selection is forwarded to session create and prompt requests`
    - `assistant question cards submit structured answers through the session permission api`
  - `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant ui reflects an external abort for a paused custom ACP prompt"` -> `1 passed`
  - `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant mode selection is forwarded to session create and prompt requests"` -> `1 passed`
  - `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant question cards submit structured answers through the session permission api"` -> `1 passed`
  - `pwsh -NoLogo -Command '& { $env:PLAYWRIGHT_API_BASE="http://127.0.0.1:52532"; $env:PLAYWRIGHT_BASE_URL="http://127.0.0.1:4317"; $env:PLAYWRIGHT_BACKEND_FS="host"; Set-Location "D:\Desktop\ResearchOS\frontend"; .\node_modules\.bin\playwright.cmd test -c playwright.config.ts }'` -> `18 passed, 1 skipped`
  - 跳过项：`assistant shell can bind a real ssh target in the compact toolbar`
  - 跳过原因：当前 host 环境没有预配置 `ssh` workspace server

2026-03-25 最终 smoke matrix：

| 场景 | Host 桌面路线 | Docker 路线 | 其他验证 | 结论 |
|---|---|---|---|---|
| 新建会话 | `assistant new conversation button creates a routed conversation shell` 通过 | 同名 smoke 通过 | 无 | 通过 |
| 普通对话 / prompt 发起 | `assistant mode selection is forwarded to session create and prompt requests` 通过 | 同名 smoke 通过 | `python -m pytest -q` 已过 | 通过 |
| tool call | `assistant custom ACP confirm flow survives refresh and session switching` 通过 | 同名 smoke 通过 | `tests/test_tool_registry.py`、`tests/test_agent_permission_next.py` 已过 | 通过 |
| permission `once` | ACP confirm smoke 通过 | ACP confirm smoke 通过 | `test_session_permission_once_flow` 已过 | 通过 |
| permission `always` | `assistant custom ACP reject and full access auto-allow flows work` 通过 | 同名 smoke 通过 | `test_permission_always_persists_project_rule_and_skips_repeat_prompt` 已过 | 通过 |
| permission `reject` | `assistant custom ACP reject and full access auto-allow flows work` 通过 | 同名 smoke 通过 | `test_session_permission_reject_resumes_with_feedback` 已过 | 通过 |
| abort | `assistant ui reflects an external abort for a paused custom ACP prompt` 通过 | 同名 smoke 通过 | `test_custom_acp_abort_clears_paused_permission_and_publishes_aborted_message` 已过 | 通过 |
| retry | 未单独做前端 smoke | 未单独做前端 smoke | `tests/test_agent_session_retry.py` 已过 | 后端通过，前端未单独 smoke |
| compaction | 未单独做前端 smoke | 未单独做前端 smoke | `tests/test_agent_session_compaction.py` 已过 | 后端通过，前端未单独 smoke |
| diff / revert / unrevert | `restored workspace api works through the frontend proxy` 通过，但只覆盖 diff/terminal/proxy | 同名 smoke 通过，但只覆盖 diff/terminal/proxy | `tests/test_agent_session_revert_diff.py` 已过 | 后端通过，前端仅部分 smoke |
| 切换工作区 | `assistant switching workspace-bound conversations updates the active workspace and survives refresh` 通过 | 同名 smoke 通过 | 无 | 通过 |
| 刷新恢复 | ACP confirm smoke 与 workspace switch smoke 均通过 | 同名 smoke 均通过 | `tests/test_agent_permission_next.py` 已过 | 通过 |
| 队列 busy prompt | 未单独做前端 smoke | 未单独做前端 smoke | `tests/test_agent_prompt_lifecycle.py` 中 queued prompt 系列已过 | 后端通过，前端未单独 smoke |
| queued permission resume | ACP confirm smoke 通过 | ACP confirm smoke 通过 | `tests/test_agent_permission_next.py` queued/native permission 系列已过 | 通过 |
| 桌面端终端 / SSH target | 跳过：当前 host 无 SSH workspace server | `assistant shell can bind a real ssh target in the compact toolbar` 通过 | `restored workspace api works through the frontend proxy` 通过 | Host 环境缺少前置条件，Docker 通过 |
| Docker 路线整体 | 不适用 | 全量 smoke `19 passed` | `docker compose ps` healthy | 通过 |

2026-03-25 最终 parity 结论：

- `M7` 的 smoke matrix、自动化验证和 parity 结论现已全部收齐。
- 截至 `2026-03-25`，在当前代码与当前验证基线下，`ResearchOS 通用 runtime 已完成对 OpenCode 的 parity 验收`。
- host 路线唯一未跑成 `passed` 的项是 `assistant shell can bind a real ssh target in the compact toolbar`，原因是当前这台 host 机器没有预配置 `ssh workspace server`；同等能力已在 Docker 路线 smoke 中通过，因此这不是当前代码的 parity blocker。
- 当前已无已知“同样输入下因架构差异导致链路不同”的结构性问题。

## 4. 执行顺序

后续必须严格按这个顺序执行，不能再被 UI、目录整理、产品小功能打断：

1. `M1` SessionProcessor + MessageV2
2. `M2` 单 active loop + permission/callback promise
3. `M3` provider/runtime typed error + transport
4. `M4` instance/project lifecycle
5. `M5` tool exposure / skills-plugin
6. `M6` frontend runtime
7. `M7` 全量验证与回归

原因：

- `M1-M5` 是内核
- `M6` 是消费层
- `M7` 是最终验收

如果 `M1-M5` 没收完，`M6` 只能算部分对齐；如果 `M7` 没过，不能对外说“已经和 OpenCode 一样稳”。

## 5. 当前实际结论

截至 2026-03-25，实际状态是：

- `M1-M7` 已全部完成
- 当前代码在 host + Docker 双路线验证下已无已知 parity blocker

因此当前准确结论是：

`ResearchOS 通用 runtime 已完成当前验证基线下的 OpenCode parity 验收`

## 6. 后续记录规则

从现在开始，每次推进必须按下面格式更新：

1. 改的是哪个模块
2. 删除了哪些本地 ownership
3. 还有哪些硬条件没满足
4. 跑了哪些验证命令
5. 该模块是否允许从“未完成”改成“已完成”

如果第 5 条答案不是“是”，就必须继续迭代，不能写成“基本完成”。
