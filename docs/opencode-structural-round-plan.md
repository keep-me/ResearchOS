# ResearchOS OpenCode 结构收敛轮次计划

更新时间：2026-03-25

## 0. 说明

这份文档只追踪当前仍需继续收紧的三类结构差异：

- `callback-loop`
- `frontend ownership`
- `tool registry / tool exposure`

它和 [docs/opencode-hard-checklist.md](/D:/Desktop/ResearchOS/docs/opencode-hard-checklist.md) 的关系是：

- `hard-checklist` 记录当前验证基线下已经通过的 parity 验收
- 本文件继续记录更严格的源码结构收敛工作

当前预计还需 `0` 轮收完。

## 1. 当前轮次总表

| 轮次 | 主题 | 目标 | 关键文件 | 验证 | 当前状态 |
|---|---|---|---|---|---|
| `R0` | 桌面数据目录统一 | 桌面 fallback backend / Tauri 默认配置统一落到仓库 `data`，不再和桌面默认数据目录分叉 | [apps/desktop/server.py](/D:/Desktop/ResearchOS/apps/desktop/server.py)、[src-tauri/src/main.rs](/D:/Desktop/ResearchOS/src-tauri/src/main.rs)、[frontend/src/pages/SetupWizard.tsx](/D:/Desktop/ResearchOS/frontend/src/pages/SetupWizard.tsx) | Python data-dir probe、`tsc`、`cargo check` | `已完成` |
| `R1` | callback settle 内收 | 把 callback settle / resolve / reject 从 `session_lifecycle` 内收回 `agent_service` active loop，减少 lifecycle callback-loop 原语 | [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)、[packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py)、[tests/test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py) | `tests/test_agent_prompt_lifecycle.py`、`tests/test_agent_permission_next.py` | `已完成` |
| `R2` | callback owner 压平 | 去掉 lifecycle 中仅供 callback-loop 使用的 loop 风格 claim/advance/release 原语，把 queued callback 的 ownership 继续压回单 active loop | [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py)、[packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py)、[tests/test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py) | `tests/test_agent_prompt_lifecycle.py`、`tests/test_agent_permission_next.py` | `已完成` |
| `R3` | frontend ownership 收敛 | assistant runtime 的 active conversation / session / workspace 真源完全收回 instance/session store，`conversationStore` 只保留 sidebar 元信息持久化 | [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts)、[frontend/src/hooks/useConversations.ts](/D:/Desktop/ResearchOS/frontend/src/hooks/useConversations.ts)、[frontend/src/contexts/ConversationContext.tsx](/D:/Desktop/ResearchOS/frontend/src/contexts/ConversationContext.tsx) | `tsc`、`build`、assistant smoke | `已完成` |
| `R4` | tool registry 单面化 | 把 builtin / extension / compat tool 的暴露面继续收成单一 registry 入口，减少静态 catalog 分叉 | [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py)、[packages/ai/tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py)、[packages/ai/research_tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/research_tool_catalog.py) | `tests/test_tool_registry.py`、`tests/test_agent_prompt_lifecycle.py` | `已完成` |
| `R5` | tool runtime 薄化 + 最终复核 | 继续压薄 `agent_tools.py`，核对 prompt/tool exposure 主链，并做最终结构复核和全量验证 | [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)、[packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py)、[reference/opencode-dev/packages/opencode/src/tool/registry.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/tool/registry.ts) | `pytest -q`、`tsc`、`build`、Playwright smoke | `已完成` |

## 2. 轮次明细

### R0. 桌面数据目录统一

目标：

- 桌面 fallback backend 在仓库开发环境下默认使用仓库 `data`
- Tauri 无显式 launcher 配置时也默认落到仓库 `data`
- setup 向导不再硬编码 macOS 风格路径说明

本轮完成：

- [apps/desktop/server.py](/D:/Desktop/ResearchOS/apps/desktop/server.py) 新增 repo-root/data 默认解析
- [src-tauri/src/main.rs](/D:/Desktop/ResearchOS/src-tauri/src/main.rs) 新增 default launcher config，开发态桌面默认落到仓库 `data`
- [frontend/src/pages/SetupWizard.tsx](/D:/Desktop/ResearchOS/frontend/src/pages/SetupWizard.tsx) 改为平台相关默认目录文案，并去掉固定 macOS launcher 路径提示

验证：

- Python probe:
  - `_default_data_dir()` -> `D:\Desktop\ResearchOS\data`
  - `_server_store_path()` -> `D:\Desktop\ResearchOS\data\assistant_workspace_servers.json`
  - `_load_server_entries()` -> `['xdu', 'bita1']`
- `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- `cargo clean && cargo check` in [src-tauri](/D:/Desktop/ResearchOS/src-tauri)

### R1. callback settle 内收

目标：

- 不再让 `session_lifecycle` 持有 callback settle / resolve 主语义
- callback 的 resolve/reject 收回到 [agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 当前 active loop

本轮完成：

- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 新增 `_settle_callback_result(...)`
- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 的 `_run_callback_loop()` 直接在本地完成 callback settle，不再调用 lifecycle settle helper
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 删除 `resolve_prompt_callbacks(...)`
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 删除 `settle_prompt_callback_loop(...)`
- [tests/test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py) 更新生命周期暴露面断言

验证：

- `python -m pytest tests/test_agent_prompt_lifecycle.py -q` -> `54 passed`
- `python -m pytest tests/test_agent_permission_next.py -q` -> `39 passed`

### R2. callback owner 压平

目标：

- 去掉 `claim_prompt_callback_loop / advance_prompt_callback_loop / release_prompt_callback_loop` 这类 loop 风格 API
- queued callback 只表达“下一个待恢复请求”，不再表达第二条 loop owner 语义

本轮完成：

- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 把 `claim_prompt_callback_loop(...)` 改成更薄的 `claim_prompt_callback(...)`
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 删除 `advance_prompt_callback_loop(...)`
- [packages/ai/session_lifecycle.py](/D:/Desktop/ResearchOS/packages/ai/session_lifecycle.py) 删除 `release_prompt_callback_loop(...)`
- [packages/ai/agent_service.py](/D:/Desktop/ResearchOS/packages/ai/agent_service.py) 改为直接消费 `claim_prompt_callback(...)`
- [tests/test_agent_prompt_lifecycle.py](/D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py) 改成断言 lifecycle 不再暴露 loop 风格 callback API，并保留 callback claim / paused owner / handoff 语义覆盖

验证：

- `python -m pytest tests/test_agent_prompt_lifecycle.py -q` -> `53 passed`
- `python -m pytest tests/test_agent_permission_next.py -q` -> `39 passed`

预计剩余轮次：`3`

### R3. frontend ownership 收敛

目标：

- assistant 页面 active truth 完全来自 instance/session store
- `conversationStore` 不再承担 active runtime ownership，只保留元信息持久化

本轮完成：

- [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 新增 assistant 自有 active conversation 持久化键，并把 active conversation / draft conversation 的 ownership 完全收回 instance store
- [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 改为本地创建 draft conversation，`persist: false` 不再依赖 `conversationStore.activeConv` 暂存
- [frontend/src/features/assistantInstance/store.ts](/D:/Desktop/ResearchOS/frontend/src/features/assistantInstance/store.ts) 的 `switchConversation(...) / deleteConversation(...) / patchConversation(...)` 改为由 assistant instance 自己驱动 active selection 和 draft/persisted handoff
- [frontend/src/hooks/useConversations.ts](/D:/Desktop/ResearchOS/frontend/src/hooks/useConversations.ts) 移除 `activeId / activeConv` 持有和 `switchConversation` 语义，收成 metadata persistence store
- [frontend/src/hooks/useConversations.ts](/D:/Desktop/ResearchOS/frontend/src/hooks/useConversations.ts) 新增 `upsertConversation(...)`，让 persisted conversation 由上层 instance store 驱动落盘

验证：

- `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- `npm --prefix frontend run build`
- Playwright ownership smoke：
  - `assistant route keeps active conversation in sync with the instance store`
  - `assistant new conversation button creates a routed conversation shell`
  - 上述功能断言已通过到最终 `assertClean()` 前，当前失败点来自本地 web backend 未起导致的 `/api/*` 500，而不是 route/ownership 回归
  - `assistant switching workspace-bound conversations updates the active workspace and survives refresh` 当前受 `127.0.0.1:8000` 未启动阻塞，属于本地 smoke 环境问题

预计剩余轮次：`2`

### R4. tool registry 单面化

目标：

- registry 不再同时维护 builtin / extension / compat 三套显式目录语义
- 继续减少 `tool_catalog.py + research_tool_catalog.py` 的静态分叉

本轮完成：

- [packages/ai/research_tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/research_tool_catalog.py) 改为 research tool 显式声明 handler module，不再依赖 registry 根据 `tool_source == extension` 猜 runtime 模块
- [packages/ai/tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py) 改为单一 `TOOL_REGISTRY` 暴露面，把基础工具和 research 工具合并为同一静态 catalog
- [packages/ai/tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py) 删除 builtin/extension 双表和对应 source 特判，收成 `catalog + custom + compat` 三层结构
- [tests/test_tool_registry.py](/D:/Desktop/ResearchOS/tests/test_tool_registry.py) 更新断言，research tools 现在走统一 catalog surface，而不再是单独 extension layer

验证：

- `python -m pytest tests/test_tool_registry.py -q` -> `6 passed`
- `python -m pytest tests/test_agent_prompt_lifecycle.py -q` -> `53 passed`
- `python -m pytest tests/test_agent_permission_next.py -q` -> `39 passed`

预计剩余轮次：`1`

### R5. tool runtime 薄化 + 最终复核

目标：

- 压薄 [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)
- 对照 OpenCode tool registry / prompt resolve 再做一轮结构核查
- 跑最终全量验证

本轮完成：

- [packages/ai/tool_context.py](/D:/Desktop/ResearchOS/packages/ai/tool_context.py) 新增统一 tool context helper，收走 session/workspace/server 解析
- [packages/ai/web_tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/web_tool_runtime.py) 抽出 websearch/webfetch/codesearch runtime
- [packages/ai/skill_tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/skill_tool_runtime.py) 抽出 skill discovery/load/read runtime
- [packages/ai/session_tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_tool_runtime.py) 抽出 todo/task/question/plan_exit runtime
- [packages/ai/tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py) 为上述工具改成显式 module-qualified handler，减少 `tool_registry -> agent_tools.py` 的隐式耦合
- [packages/ai/agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py) 删除 web/skill/session 三组具体实现，只保留文件/工作区类 runtime 和兼容导出，厚度继续下降
- [tests/test_tool_registry.py](/D:/Desktop/ResearchOS/tests/test_tool_registry.py) 新增 handler module 断言，确保 registry 真正解析到新 runtime 模块
- [tests/test_agent_permission_next.py](/D:/Desktop/ResearchOS/tests/test_agent_permission_next.py) 更新 skill runtime monkeypatch 目标，跟随新的 handler ownership

验证：

- `python -m pytest tests/test_tool_registry.py -q` -> `6 passed`
- `python -m pytest tests/test_agent_permission_next.py -q` -> `39 passed`
- `python -m pytest tests/test_agent_session_runtime.py -q` -> `43 passed`
- `python -m pytest tests/test_agent_session_revert_diff.py -q` -> `9 passed`
- `python -m pytest -q` -> `489 passed`
- `frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`
- `npm --prefix frontend run build`
- Playwright smoke 现状：
  - 结构相关场景此前已验证到功能断言层
  - 当前完整 smoke 仍受本地 web backend `127.0.0.1:8000` 未运行影响，不属于本轮结构改造引入的问题

预计剩余轮次：`0`

预计剩余轮次：`0`
