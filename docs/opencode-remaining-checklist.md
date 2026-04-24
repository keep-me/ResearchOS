# ResearchOS 对齐 OpenCode 当前状态与完成归档

> 2026-03-25 真实 runtime 评测后，OpenCode parity 已重新打开。
> 当前权威执行清单已切换到 [docs/opencode-runtime-gap-closure-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-runtime-gap-closure-checklist.md)。
> 本文件保留历史归档，不再作为“已经完全收平”的判断依据。

更新时间：2026-03-25

## 0. 使用方式

这份文档用于记录最近一轮原子任务拆分、当前状态和完成归档。

规则：

- 当前无待办时，保留最近一轮已完成原子任务归档，便于追踪验收证据
- 每一项都必须是可执行的原子任务
- 每完成一项，就在本文件中勾选，并同步更新 [docs/opencode-hard-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-hard-checklist.md)
- 如果某项完成后发现仍和 OpenCode 源码不同，必须立刻重新拆分并追加子项，不能直接标“完成”

当前总原则：

- 以 2026-03-25 的最新源码审计为准，这一轮重新打开的 `M2` / `M3` 已完成收敛
- 历史“已完成”归档保留，用于保留验收证据

## 1. 当前模块状态

| 模块 | 当前状态 | 说明 |
|---|---|---|
| `M1` SessionProcessor + MessageV2 | 已完成 | prompt / permission resume / tool-call / compaction message ownership 已统一回到 processor + MessageV2 边界，route/service 不再承担第二套 message mutation |
| `M2` 单 active loop + callback promise | 已完成 | callback-loop 的 handoff / finish / reject ownership 已从厚 `session_lifecycle` helper 收回 `SessionPromptProcessor` 的 finalize 主链；`session_lifecycle` 公开面现在只保留更薄的 `pause/finish/claim/drain` 原语 |
| `M3` provider/runtime typed error | 已完成 | `stream/probe/runtime` 现在统一透出同一份 typed error / attempt contract；Responses -> chat/raw-http 的多 transport fallback 不再被压成单条 message 字符串 |
| `M4` instance/project lifecycle | 已完成 | `reload/dispose/dispose_all` 已统一到 `Instance` 单 teardown 入口，旧 loop 不会在 dispose 后复活 lifecycle state |
| `M5` tool exposure / permission / skills-plugin | 已完成 | spec / provider-defined / extension runtime 已统一回到 registry，research handler 已移出 core builtin runtime |
| `M6` frontend assistant runtime | 已完成 | assistant runtime 已完全切到 instance/session store + `/session/* + bus` 主链，并已通过 host + Docker 双路线验收 |
| `M7` 全量验收 | 已完成 | Docker 路线 external abort blocker 已收敛；当前代码与当前验证基线下已无已知 parity blocker |

## 1.1 2026-03-25 最新三轮计划

对照源码：

- [reference/opencode-dev/packages/opencode/src/session/prompt.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/prompt.ts)
- [reference/opencode-dev/packages/opencode/src/provider/error.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/provider/error.ts)
- [reference/opencode-dev/packages/opencode/src/session/llm.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/llm.ts)

轮次表：

| 轮次 | 状态 | 本轮目标 | 预计剩余轮次 |
|---|---|---|---|
| Round A1 | 已完成 | 收平 `provider/runtime` 的 typed error 合同：把 `stream/probe/runtime` 的多 transport fallback 统一成同一份 normalized error/attempt 语义，并补回归测试 | 2 |
| Round A2 | 已完成 | 收薄 `session_lifecycle`：把 callback-loop 的 handoff/finalize/reject ownership 从厚辅助 API 压回单 active loop 主链，`session_lifecycle` 只保留更薄的 state/queue/abort 原语 | 1 |
| Round A3 | 已完成 | 清理 callback-loop 剩余兼容层，重跑 targeted tests，更新本文件与硬验收清单的真实完成状态 | 0 |

本轮真实剩余项：

- [x] `A1-1` 给 `llm_provider_error.py` 增加统一的 transport attempt contract，避免 `responses -> chat/raw-http` 这类多次尝试被压成单条裸字符串
- [x] `A1-2` 让 [packages/integrations/llm_provider_stream.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_stream.py) 在 fallback 失败时透出完整 typed error 链，而不是只保留最终异常
- [x] `A1-3` 让 [packages/integrations/llm_provider_probe.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_probe.py) / [packages/integrations/llm_provider_runtime.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_runtime.py) 使用同一份 error/attempt 语义
- [x] `A2-1` 删薄 [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 里的 callback handoff/finalize 辅助层，减少 `PromptSettlement` 风格的本地 loop orchestration
- [x] `A2-2` 把 [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 中 queued callback 的 finish/reject/handoff 收到同一条 active loop finalize 路径
- [x] `A3-1` 跑 `tests/test_llm_client_stream.py`、`tests/test_llm_client_probe.py`、`tests/test_llm_client_runtime.py`、`tests/test_agent_prompt_lifecycle.py`、`tests/test_agent_session_runtime.py` 并回填本文件结果

本轮新增完成：

- [packages/integrations/llm_provider_error.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_error.py) 新增统一的 session error payload / attempt contract；`stream/probe/runtime` 现在都能保留 `responses -> chat/raw-http` 这类多 transport 尝试链
- [packages/integrations/llm_provider_stream.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_stream.py) 现在会把前序失败 transport 合并进最终 `error` event 的 `attempts`
- [packages/integrations/llm_provider_probe.py](/D:/Desktop/ResearchOS/packages/integrations/llm_provider_probe.py) 现在不再把 Responses 失败只拼到 message 字符串里，而是统一落到 typed `attempts`
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 已删除 `PromptSettlement`、`settle_prompt_instance()`、`handoff_or_finish_prompt_instance()`、`reject_callbacks_and_finish_prompt_instance()`、`drain_callbacks_and_finish_prompt_instance()`，公开面只保留更薄的 `pause/finish/claim/drain`
- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 queued callback handoff / finish / reject 现在直接在 `SessionPromptProcessor` finalize 路径决定，不再回绕 `session_lifecycle` 的厚 settlement API
- [packages/ai/session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 的 paused-abort finalize 已改成 `drain -> finish -> reject` 薄原语组合
- [tests/test_llm_client_stream.py](/D:/Desktop/ResearchOS/tests/test_llm_client_stream.py) 与 [tests/test_llm_client_probe.py](/D:/Desktop/ResearchOS/tests/test_llm_client_probe.py) 新增 attempt-chain 回归
- [tests/test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py) 已改为验证新的 `pause/claim/finish/drain` 语义，并锁定旧厚 API 不再暴露

验证：

- [x] `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_llm_client_runtime.py tests/test_agent_session_retry.py -q`
- [x] `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_global_routes.py -q`
- [x] `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py -q`
- [x] `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_llm_client_runtime.py tests/test_agent_session_retry.py -q`

## 2. M1 完成归档

对照源码：

- [reference/opencode-dev/packages/opencode/src/session/prompt.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/prompt.ts)
- [reference/opencode-dev/packages/opencode/src/session/processor.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/processor.ts)
- [reference/opencode-dev/packages/opencode/src/session/message-v2.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/message-v2.ts)

当前热点：

- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)
  - `SessionPromptProcessor`
- [packages/ai/session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py)
- [packages/ai/session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py)

剩余任务：

- [x] `M1-1` 把 [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 中 `_run_model_turn_events()` 的剩余 prompt/tool orchestration 继续下沉到 [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 或同构 prompt runtime，避免 `agent_service.py` 继续当主 owner
- [x] `M1-2` 把 [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 中 `_process_tool_calls()` 的剩余 tool lifecycle 逻辑继续下沉，做到 tool pending/running/completed/error 的主 mutation 语义由 processor 侧统一决定
- [x] `M1-3` 清理 [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 里的 `SessionStreamPersistence` 厚兼容层，让 native 主链不再依赖第二套 event-to-mutation 解释器
- [x] `M1-4` 对照 OpenCode 审核 owner：prompt / permission resume / retry 三条 streaming 主路径继续统一经过 `SessionProcessor.apply_event(...)` / processor-owned mutation；compaction 则单独对齐 [session_compaction.py](/D:/Desktop/ResearchOS/packages/ai/session_compaction.py) 与 OpenCode `session/compaction.ts` 的 message creation 语义，不能再回落到 service/SSE wrapper 兼容桥
- [x] `M1-5` 对照 OpenCode `message-v2.ts`，继续核对 user/assistant/part 的对象边界，补齐仍然混在 `meta` 或 runtime helper 里的本地字段

完成判定：

- [x] [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 只剩 transport、route、thin adapter
- [x] native 主链不再存在第二套 `message/part` mutation owner
- [x] `SessionStreamPersistence` 不再是 native 主路径依赖

验证：

- [x] `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py -q`
- [x] `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py -q`
- [x] `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py tests/test_agent_session_compaction.py tests/test_agent_session_retry.py tests/test_session_message_v2.py -q`

本轮新增完成：

- [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 新增 `stream_model_turn_events(...)`、`stream_tool_execution_events(...)`、`stream_tool_call_processing(...)`，把 model-turn / tool execution / tool processing owner 压回 processor 层
- [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 `_run_model_turn_events()`、`_execute_single_tool()`、`_process_tool_calls()` 已降为 thin adapter，只负责组装依赖并回传 processor runtime 结果
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 的公开 `SessionStreamPersistence` 符号已压成明确的 `SessionProcessor` 代理，`wrap_stream_with_persistence(...)` 不再暴露第二套 live event-to-mutation owner
- [session_processor.py](/D:/Desktop/ResearchOS/packages/ai/session_processor.py) 调整了 `assistant_message_id` 的 announce 时机，preflight / post-step auto compaction 不再先看到占位 assistant message，自动压缩续跑重新对齐到正确的 parent rollover 语义
- [tests/test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py) 新增 delegation 回归，锁定 `agent_service -> session_processor` 的 model-turn / tool-processing 下沉边界
- [session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 新增 OpenCode 风格的 user/assistant `MessageV2.Info` 归一化：user 持久化 `agent/model/format`，assistant 持久化 `mode/agent/path/tokens/cost/time.completed/structured`
- [apps/api/routers/session_runtime.py](/D:/Desktop/ResearchOS/apps/api/routers/session_runtime.py) 与 [apps/api/routers/agent.py](/D:/Desktop/ResearchOS/apps/api/routers/agent.py) 现在会在 prompt/user message 落库前补齐默认 `agent/model`，并在 assistant persistence meta 中预填 `providerID/modelID/tokens/cost`
- [session_compaction.py](/D:/Desktop/ResearchOS/packages/ai/session_compaction.py) 已按 OpenCode `session/compaction.ts` 收平 compaction / summary / replay 三类消息的元数据边界，auto replay 不再丢失原 user 的 `agent/model/format/tools/system/variant`
- 新增回归覆盖 `MessageV2` info shape、session route user meta 持久化、assistant persistence meta 初值、以及 compaction replay meta 保留

## 4. M4 完成归档

对照源码：

- [reference/opencode-dev/packages/opencode/src/project/instance.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/project/instance.ts)

当前热点：

- [packages/ai/session_instance.py](/D:/Desktop/ResearchOS/packages/ai/session_instance.py)
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py)

剩余任务：

- [x] `M4-1` 让 [session_instance.py](/D:/Desktop/ResearchOS/packages/ai/session_instance.py) 的 `Instance.reload()/dispose()/dispose_all()` 成为目录级 session/runtime/state 清理的唯一入口
- [x] `M4-2` 清理仍然散落在 route/service 层的手动 session/runtime dispose 协调逻辑
- [x] `M4-3` 继续对齐 OpenCode 的 `instance disposed` 事件语义，确保 reload/dispose/dispose_all 的触发边界一致
- [x] `M4-4` 审核 `request_session_abort()` 与 instance dispose 的关系，保证 busy dispose、abort-after-dispose、reload-after-busy 的行为稳定

完成判定：

- [x] 目录级 owner 只剩 `Instance.reload()/dispose()/dispose_all()`
- [x] session/runtime 清理不再由多处手动拼装

验证：

- [x] `python -m pytest tests/test_global_routes.py tests/test_agent_prompt_lifecycle.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py -q`
- [x] `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py -q`

## 5. M5 完成归档

对照源码：

- [reference/opencode-dev/packages/opencode/src/session/prompt.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/prompt.ts)
- [reference/opencode-dev/packages/opencode/src/session/system.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/system.ts)

当前热点：

- [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)
- [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py)
- [packages/ai/tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py)
- [packages/ai/tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/tool_runtime.py)

剩余任务：

- [x] `M5-1` 删除 [tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py) 里的 `_TOOL_SPECS` + `_apply_tool_specs()`，把 spec 真源收回 `ToolDef`/registry
- [x] `M5-2` 继续收薄 [agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)，避免 builtin handler、tool exposure、permission 语义继续耦在一起
- [x] `M5-3` 继续收薄 [tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 里的 provider-defined spec 兼容层，确保不是靠本地特判修正暴露行为
- [x] `M5-4` 把 ResearchOS 论文能力挂回 skill/plugin 或外层扩展层，而不是继续混在通用 runtime builtin tool 层
- [x] `M5-5` 审核 system/tool prompt，不再让 prompt adapter 暗中修补工具行为

完成判定：

- [x] 通用 runtime 内无本地 `_TOOL_SPECS`
- [x] `agent_tools.py` 不再混有 research extension handler / tool exposure / permission 特化
- [x] builtin/custom/provider-defined tool 都由统一 registry/spec + permission layer 决定

验证：

- [x] `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py -q`
- [x] `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py -q`

## 7. M7 完成归档

当前状态：

- [x] `M7-1` 跑完整后端测试：`python -m pytest -q` -> `490 passed in 293.99s`
- [x] `M7-2` 跑前端构建：`npm --prefix frontend run build`
- [x] `M7-3` 跑前端类型检查：`frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- [x] `M7-4` 验证桌面端启动、聊天、permission、终端、恢复：host `18 passed, 1 skipped`
- [x] `M7-5` 验证 Docker 路线启动、聊天、permission、恢复：container `19 passed`
- [x] `M7-6` 做最终人工 smoke matrix 并写出最终 parity 结论
- [x] `M7-7` 修复 Docker 路线 `assistant ui reflects an external abort for a paused custom ACP prompt`，并重跑容器全量 smoke 至 `19/19`

最终 smoke matrix：

- [x] 新建会话
- [x] 普通对话
- [x] tool call
- [x] permission `once`
- [x] permission `always`
- [x] permission `reject`
- [x] abort
- [x] retry
- [x] compaction
- [x] diff / revert / unrevert
- [x] 切换工作区
- [x] 刷新恢复
- [x] 队列 busy prompt
- [x] queued permission resume
- [x] 桌面端终端
- [x] Docker 路线

矩阵说明：

- 详细矩阵与证据已写入 [docs/opencode-hard-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-hard-checklist.md) 的 `M7` 小节
- `桌面端终端` 在 host 当前机器上仍是环境条件项：host 缺少预配置 `ssh workspace server`，但同等路径已在 Docker 路线通过，因此不再构成 parity blocker

完成判定：

- [x] `M1-M6` 已全部打勾
- [x] 自动化验证全部通过
- [x] 不再存在已知“同样输入下因架构差异导致链路不同”的结构性问题

当前自动化状态：

- Docker 路线已验证到“可启动 + 健康 + 全量 Playwright smoke 通过”
- Docker 前端验证必须使用 `http://localhost:3002`；当前机器上的 `http://127.0.0.1:3002` 会命中本地 Vite dev server，不代表容器内 nginx
- 桌面 host 路线已验证到“desktop fallback backend + frontend dev + 全量 Playwright smoke”
- 本轮补齐后，[frontend/vite.config.ts](/D:/Desktop/ResearchOS/frontend/vite.config.ts) 会同时读取 `.env` 与 shell 环境变量，`VITE_PROXY_TARGET` 不再在桌面 host 验证时失效
- 本轮新增：paused custom ACP permission 的外部 abort 已在 [packages/ai/session_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_runtime.py) 直接完成 pending 清理、aborted message 持久化和 bus idle/message 更新
- 本轮新增：[frontend/src/services/api.ts](/D:/Desktop/ResearchOS/frontend/src/services/api.ts) 已修正 `replyPermission(...)` 丢失 `answers`，`question` 卡片提交会把结构化答案完整发往 session permission api
- 本轮新增：[frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 已修正 `normalizeAgentMode(...)`，默认 `plan` 时不会再把显式 `build` 选择折回去
- 本轮新增：[frontend/src/contexts/AssistantInstanceContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/AssistantInstanceContext.tsx) 已修正 route/store 双向同步，工作区切会话会同步更新 `/assistant/:conversationId`
- 本轮新增：[packages/ai/session_bus.py](/D:/Desktop/ResearchOS/packages/ai/session_bus.py) 已把 session bus 镜像到 global bus 的 payload 收成纯 dict，修复 `/global/event` 在 external abort 场景下的序列化崩溃
- 本轮新增：[frontend/tests/smoke.spec.ts](/D:/Desktop/ResearchOS/frontend/tests/smoke.spec.ts) 已补四条 host/Docker 路线 smoke，锁定 routed conversation shell、workspace switch refresh、mode forwarding、question-answer forwarding
- 对应自动化现已通过：
  - `python -m pytest tests/test_agent_permission_next.py -k "custom_acp_abort_clears_paused_permission_and_publishes_aborted_message or custom_acp_permission_confirm_flow or custom_acp_confirm_does_not_fall_back_to_wrapper_persistence" -q`
  - `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant ui reflects an external abort for a paused custom ACP prompt"`
  - `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant mode selection is forwarded to session create and prompt requests"`
  - `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant question cards submit structured answers through the session permission api"`
  - `pwsh -NoLogo -Command '& { $env:PLAYWRIGHT_API_BASE="http://127.0.0.1:52532"; $env:PLAYWRIGHT_BASE_URL="http://127.0.0.1:4317"; $env:PLAYWRIGHT_BACKEND_FS="host"; Set-Location "D:\Desktop\ResearchOS\frontend"; .\node_modules\.bin\playwright.cmd test -c playwright.config.ts }'` -> `18 passed, 1 skipped`
  - `pwsh -NoLogo -Command '& { $env:PLAYWRIGHT_API_BASE="http://localhost:8002"; $env:PLAYWRIGHT_BASE_URL="http://localhost:3002"; $env:PLAYWRIGHT_BACKEND_FS="container"; Set-Location "D:\Desktop\ResearchOS\frontend"; .\node_modules\.bin\playwright.cmd test -c playwright.config.ts }'` -> `19 passed`
  - `python -m pytest -q` -> `490 passed in 293.99s`
  - `npm --prefix frontend run build`
  - `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- 最终人工 smoke matrix 与 parity 结论已完成，当前结论是：`ResearchOS 通用 runtime 已完成当前验证基线下的 OpenCode parity 验收`
- 当前已无待办项

## 8. 当前推荐执行顺序

- 当前无待执行模块

## 9. 最近一次已通过的验证

- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_agent_permission_next.py tests/test_agent_session_runtime.py tests/test_tool_registry.py -q`
- `python -m pytest tests/test_agent_prompt_lifecycle.py tests/test_agent_session_runtime.py tests/test_agent_permission_next.py tests/test_global_routes.py tests/test_llm_client_stream.py tests/test_llm_client_provider_options.py tests/test_tool_registry.py -q`
- `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py -q`
- `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py tests/test_agent_session_runtime.py -q`
- `python -m pytest tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_agent_remote_workspace.py -q`
- `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py -q`
- `python -m pytest tests/test_tool_registry.py tests/test_agent_permission_next.py tests/test_agent_tools_status.py tests/test_agent_prompt_lifecycle.py tests/test_agent_remote_workspace.py tests/test_agent_session_runtime.py tests/test_agent_session_compaction.py tests/test_llm_client_stream.py tests/test_llm_client_probe.py tests/test_agent_session_retry.py tests/test_llm_client_runtime.py tests/test_global_routes.py -q`
- `python -m pytest tests/test_global_routes.py tests/test_agent_prompt_lifecycle.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py -q`
- `python -m pytest -q`
- `npm --prefix frontend run build`
- `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- `docker compose up -d --build`
- `frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts`
- `PLAYWRIGHT_API_BASE=http://localhost:8002 PLAYWRIGHT_BASE_URL=http://localhost:3002 PLAYWRIGHT_BACKEND_FS=container frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts`
- `python -m pytest tests/test_agent_permission_next.py -k "custom_acp_abort_clears_paused_permission_and_publishes_aborted_message or custom_acp_permission_confirm_flow or custom_acp_confirm_does_not_fall_back_to_wrapper_persistence" -q`
- `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant ui reflects an external abort for a paused custom ACP prompt"`
- `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant mode selection is forwarded to session create and prompt requests"`
- `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant question cards submit structured answers through the session permission api"`
- `PLAYWRIGHT_API_BASE=http://127.0.0.1:52532 PLAYWRIGHT_BASE_URL=http://127.0.0.1:4317 PLAYWRIGHT_BACKEND_FS=host frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts`
- `frontend/node_modules/.bin/playwright.cmd test -c playwright.config.ts --grep "assistant route keeps active conversation in sync with the instance store"`
- `pwsh -NoLogo -Command '& { $env:PLAYWRIGHT_API_BASE="http://localhost:8002"; $env:PLAYWRIGHT_BASE_URL="http://localhost:3002"; $env:PLAYWRIGHT_BACKEND_FS="container"; Set-Location "D:\Desktop\ResearchOS\frontend"; .\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "assistant ui reflects an external abort for a paused custom ACP prompt" }'`

说明：

- 上述命令构成最近一次完成归档的验证基线
- 本文档记录的结果已经支撑 `M1-M7` 当前状态为已完成
- 当前可以准确表述为：`ResearchOS 通用 runtime 已完成当前验证基线下的 OpenCode parity 验收`
