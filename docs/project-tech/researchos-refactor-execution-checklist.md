# ResearchOS 重构执行清单

本文档是 `ResearchOS` 后续结构重构的唯一执行基线，创建日期为 `2026-04-13`。后续执行时，不再额外维护一份平行计划；每完成一项，直接在本文档内勾选并补充日期或备注。

## 0. 使用规则

- 所有命令默认在仓库根目录 `D:\Desktop\ResearchOS` 下、使用 PowerShell 7 执行。
- 每个 PR 只做一件事，不允许把“顺手加功能”混进重构 PR。
- 每个 PR 合并前，至少执行本节对应的验收命令；执行失败时不得勾选。
- 每周所有 PR 合并后，补勾本周“完成定义”与顶部总进度。
- 如果实际拆分与文档略有偏差，优先保持“依赖方向变干净、文件显著变小、测试仍然通过”这 3 个结果。

## 1. 通用准备命令

先执行一次下面的命令，后续验收直接复用 `$python`：

```powershell
Set-Location D:\Desktop\ResearchOS
$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
```

## 2. 总进度

- [x] W01 启动链收口
- [x] W02 拉直依赖方向
- [x] W03 拆分 Session 子系统
- [x] W04 拆分 Workflow Runner
- [x] W05 收缩 Repository 层
- [x] W06 拆分前端 Agent 页面
- [x] W07 收口前后端状态协议
- [x] W08 回归补测与兼容清理

## 3. 当前基线指标

- `packages/storage/db.py` 已移除 import-time `run_migrations()`。
- `packages/ai/project_workflow_runner.py` 当前约 `8982` 行。
- `packages/agent/runtime/agent_service.py` 当前约 `4093` 行。
- `packages/agent/session/session_runtime.py` 当前约 `2098` 行。
- `packages/storage/repositories.py` 当前约 `1015` 行。
- `frontend/src/pages/Agent.tsx` 当前约 `4259` 行。
- `packages` 内 `from apps.api.routers...` 反向依赖已清零。
- agent 内核代码现收口到 `packages/agent/runtime`、`packages/agent/session`、`packages/agent/tools`、`packages/agent/workspace`、`packages/agent/mcp` 5 个子包。
- `packages/ai` 已移除 agent runtime / tool / workspace / backend / MCP 顶层 shim 文件。
- `packages/ai` 业务代码现收口到 `packages/ai/project`、`packages/ai/paper`、`packages/ai/research`、`packages/ai/ops` 4 个子包，根目录不再平铺 project/paper/research 文件。

## 4. 执行记录

- [x] `2026-04-13` 创建本文档，初始状态全部未开始。
- [x] `2026-04-13` 完成 `PR-01A`：移除 `packages/storage/db.py` 的 import-time 自动迁移副作用。
- [x] `2026-04-13` 完成 `PR-01B`：统一 API、Worker 与本地脚本的显式 bootstrap 入口。
- [x] `2026-04-13` 完成 `PR-01C`：补 import 无副作用与 API startup 显式 bootstrap 守门测试。
- [x] `2026-04-13` 完成 `W01`：启动链收口完成，后续进入依赖方向清理。
- [x] `2026-04-13` 完成 `PR-02A`：将远程 SSH 工作区 helper 与 server registry 从 router 下沉到 `packages/ai`。
- [x] `2026-04-13` 完成 `PR-02B`：清理 AI runtime 对 router 的反向依赖，并补 `paper_ops_service` 承接 MCP/论文路由共用逻辑。
- [x] `2026-04-13` 完成 `PR-02C`：router 层互相 import 清零，保留兼容 wrapper 作为 HTTP/测试边界。
- [x] `2026-04-13` 完成 `W02`：依赖方向已拉直，后续进入 Session 子系统拆分。
- [x] `2026-04-13` 完成 `PR-03A`：抽出 `session_store`，将 session/message/part 的 DB 持久化与 materialization 从 `session_runtime.py` 下沉。
- [x] `2026-04-13` 完成 `PR-03B`：移除历史 `_pending_actions/_pending_lock` shim，并将 pending permission 收口为 DB 单一状态源。
- [x] `2026-04-13` 完成 `PR-03C`：将 session 事件发布与 revert/patch 流程拆到 `packages/agent/session_events.py`、`packages/agent/session_revert.py`，`session_runtime.py` 保留兼容导出。
- [x] `2026-04-13` 完成 `W03`：Session 子系统已按 persistence / pending / revert / event 边界拆开，并开始向 `packages/agent` 迁移。
- [x] `2026-04-13` 完成 `PR-04A`：以 handler registry 替换 `project_workflow_runner.py` 中的 workflow type 长 `if` 链。
- [x] `2026-04-13` 完成 `PR-04B`：验证 workflow 外置模块模式，先将 `literature_review` handler 迁到 `packages/ai/project_workflows/literature_review.py`。
- [x] `2026-04-13` 完成 `PR-04C`：以 registry + 外置 handler + 回归验证收尾，`project_workflow_runner.py` 降到 `8982` 行并通过 ARIS 定向回归。
- [x] `2026-04-13` 完成 `W04`：Workflow Runner 已切成调度器主路径，后续继续按需要外迁剩余 handler。
- [x] `2026-04-13` 完成 `PR-05A`：将 `TaskRepository` 与 agent/session 相关仓储拆到 `packages/storage/task_repository.py`、`packages/storage/agent_session_repository.py`，`repositories.py` 保留 re-export。
- [x] `2026-04-13` 完成 `PR-05B`：将 paper/topic/project 仓储拆到 `packages/storage/paper_repository.py`、`packages/storage/topic_repository.py`、`packages/storage/project_repository.py`，`repositories.py` 收缩到 `1015` 行。
- [x] `2026-04-13` 完成 `PR-05C`：补 `packages/storage/repository_facades.py`，收口 router / service 层对 repository 的直接拼装。
- [x] `2026-04-13` 完成 `W05`：Repository 层按领域拆开，`repositories.py` 不再是主要开发入口。
- [x] `2026-04-13` 完成 `PR-06A`：将 Agent 页面的工作区 / 终端 / toolbar 共享逻辑抽到 `frontend/src/components/agent/agentPageShared.tsx`。
- [x] `2026-04-13` 完成 `PR-06B`：继续把 MCP、模型、workflow launcher 与 runtime 配置辅助逻辑从 `Agent.tsx` 下沉到共享模块。
- [x] `2026-04-13` 完成 `PR-06C`：抽出 `frontend/src/components/agent/TraceViews.tsx` 承接消息 trace、tool 结果视图与空态/工件视图。
- [x] `2026-04-13` 完成 `PR-06D`：`frontend/src/pages/Agent.tsx` 压缩到 `4259` 行，构建与 smoke 保持通过。
- [x] `2026-04-13` 完成 `W06`：前端 Agent 页面收缩为装配页，主要视图与共享逻辑已独立。
- [x] `2026-04-13` 完成 `PR-07A`：新增 `packages/domain/assistant_schemas.py`，统一 assistant/session DTO 并让 router 返回 schema。
- [x] `2026-04-13` 完成 `PR-07B`：新增 `frontend/src/features/assistantInstance/sessionProtocol.ts`，统一 workspace diff / session diff / revert info 协议。
- [x] `2026-04-13` 完成 `PR-07C`：删除前端大量 fallback parsing 分支，消息与 session review 解析收口到 shared normalizer。
- [x] `2026-04-13` 完成 `W07`：assistant/session/workspace 协议已统一，前后端对应回归通过。
- [x] `2026-04-13` 完成 `PR-08A`：补 `experiment_audit` ARIS smoke 场景并加固前端 smoke，assistant 主链路已全绿。
- [x] `2026-04-13` 完成 `PR-08B`：将 session-domain 内部实现入口切到 `packages/agent`，`packages/ai/session_*` 仅保留兼容 shim，并同步回填本文档。
- [x] `2026-04-13` 完成 `W08`：ARIS、pytest 与前端 smoke 验收全部通过，执行清单收口完成。
- [x] `2026-04-13` 追加迁移：将 agent runtime / tool / workspace / backend / MCP 相关实现从 `packages/ai` 物理迁到 `packages/agent`，`packages/ai` 同名文件统一改为模块别名 shim。
- [x] `2026-04-13` 追加清理：将 `packages/agent` 进一步拆成 `runtime/session/tools/workspace/mcp` 子包，仓库内部引用全部切到新包路径，并删除 `packages/ai` 顶层 agent shim 文件。
- [x] `2026-04-13` 追加清理：将 `packages/ai` 进一步拆成 `project/paper/research/ops` 子包，仓库内部旧顶层导入全部切到新包路径，并补跑 `tests/` 全量回归与前端 smoke。
- [x] `2026-04-13` 追加清理：将 `packages/ai/paper/pipelines.py` 中的参考文献导入链路拆到 `packages/ai/paper/reference_importer.py`，路由改为直接依赖新模块，并补跑全量 `pytest` 与 `packages.ai/packages.agent` import smoke。

## W01 启动链收口

目标：消除 import-time 副作用，统一数据库初始化和运行时 bootstrap 入口。

### PR-01A 移除 `packages/storage/db.py` 的 import-time 迁移

- [x] 完成 PR-01A
- 范围文件：`packages/storage/db.py`
- 可新增文件：`packages/storage/bootstrap.py` 或同等职责文件
- 完成定义：`packages/storage/db.py` 不再在模块 import 阶段自动执行 `run_migrations()`
- 完成定义：迁移入口变成显式函数调用，而不是文件底部副作用
- 完成定义：保留当前测试数据库配置能力，不破坏内存 SQLite 测试

验收命令：

```powershell
rg -n "run_migrations\(" packages/storage/db.py apps/api/main.py scripts/local_bootstrap.py apps/worker/main.py
& $python -m pytest tests/test_session_message_v2.py tests/test_analysis_repository.py -q
```

### PR-01B 统一 API、Worker、本地脚本的 bootstrap 路径

- [x] 完成 PR-01B
- 范围文件：`apps/api/main.py`、`apps/worker/main.py`、`scripts/local_bootstrap.py`、`packages/domain/task_tracker.py`
- 完成定义：API 启动时只做明确的 runtime bootstrap，不重复做隐藏迁移
- 完成定义：Worker 启动链与 API 启动链职责清楚，不出现“两个入口各做一遍数据库初始化”
- 完成定义：`global_tracker.bootstrap_from_store()` 的调用位置可解释、可测试

验收命令：

```powershell
rg -n "bootstrap_from_store|run_migrations\(" apps packages scripts
$env:DATABASE_URL = "sqlite:///./tmp/w01-bootstrap.db"
& $python scripts/local_bootstrap.py
Remove-Item ".\tmp\w01-bootstrap.db" -ErrorAction SilentlyContinue
Remove-Item ".\tmp\w01-bootstrap.db-shm" -ErrorAction SilentlyContinue
Remove-Item ".\tmp\w01-bootstrap.db-wal" -ErrorAction SilentlyContinue
```

### PR-01C 补启动链测试，防止回归

- [x] 完成 PR-01C
- 范围文件：`tests/conftest.py`、`tests/test_task_tracker.py`
- 可新增文件：`tests/test_storage_bootstrap.py`、`tests/test_app_startup.py`
- 完成定义：至少覆盖“导入 storage 模块不会直接写库”和“显式 bootstrap 才会初始化”
- 完成定义：测试里明确校验 API/脚本初始化流程

验收命令：

```powershell
& $python -m pytest tests/test_task_tracker.py tests/test_storage_bootstrap.py tests/test_app_startup.py -q
```

### W01 完成定义

- [x] `packages/storage/db.py` 不再带 import-time 数据库写入副作用
- [x] API、Worker、本地脚本的初始化职责收口完成
- [x] 启动链回归测试已补齐

## W02 拉直依赖方向

目标：消灭 `packages/* -> apps/api/routers/*` 的反向 import，把远程工作区能力抽成可复用 service。

### PR-02A 抽出远程工作区/SSH 服务层

- [x] 完成 PR-02A
- 范围文件：`apps/api/routers/agent_workspace.py`、`apps/api/routers/agent_workspace_ssh.py`
- 可新增文件：`packages/ai/workspace_remote.py`、`packages/ai/workspace_server_registry.py` 或同等职责文件
- 完成定义：远程文件读写、终端执行、工作区概览、SSH 探测这些能力不再定义在 router 内
- 完成定义：router 只负责请求/响应适配和 HTTP 错误码

验收命令：

```powershell
& $python -m pytest tests/test_agent_workspace_ssh.py tests/test_agent_remote_workspace.py -q
```

### PR-02B 把 AI runtime 全部切到新 service

- [x] 完成 PR-02B
- 范围文件：`packages/ai/agent_tools.py`、`packages/ai/project_workflow_runner.py`、`packages/ai/project_multi_agent_runner.py`、`packages/ai/project_run_action_service.py`
- 范围文件：`packages/ai/claw_mcp_runtime.py`、`packages/ai/cli_agent_service.py`、`packages/ai/terminal_service.py`、`packages/ai/tool_context.py`、`packages/ai/session_runtime.py`、`packages/ai/acp_service.py`
- 完成定义：这些模块不再 import `apps.api.routers.agent_workspace*`
- 完成定义：远程/本地工作区能力通过统一 service 接口访问

验收命令：

```powershell
rg -n "from apps\.api\.routers|import apps\.api\.routers" packages
& $python -m pytest tests/test_agent_workspace_ssh.py tests/test_agent_remote_workspace.py tests/test_project_execution_service.py tests/test_claw_runtime_manager.py -q
```

### PR-02C 清理 router 间互相 import

- [x] 完成 PR-02C
- 范围文件：`apps/api/routers/projects.py`、`apps/api/routers/agent_workspace.py`、`apps/api/routers/agent_workspace_ssh.py`
- 完成定义：router 之间不再互相 import helper
- 完成定义：共用 helper 下沉到 `packages/` 或 router 私有模块

验收命令：

```powershell
rg -n "from apps\.api\.routers|import apps\.api\.routers" apps/api/routers
& $python -m pytest tests/test_projects_router_flows.py tests/test_agent_workspace_ssh.py -q
```

### W02 完成定义

- [x] `packages` 中反向依赖 `apps.api.routers` 的 import 基本清零
- [x] router 间互相引用基本清零
- [x] 远程工作区能力已经从 HTTP 层抽离

## W03 拆分 Session 子系统

目标：把 session 的持久化、pending action、revert/patch、事件发布拆开，降低 `session_runtime.py` 和 `agent_service.py` 的中心化程度。

### PR-03A 抽出 session persistence/store

- [x] 完成 PR-03A
- 范围文件：`packages/ai/session_runtime.py`
- 可新增文件：`packages/ai/session_store.py`、`packages/ai/session_part_store.py` 或同等职责文件
- 完成定义：消息写入、part materialization、session touch 这类 DB 细节从 `session_runtime.py` 拆出
- 完成定义：`session_runtime.py` 只保留 runtime 编排，不再同时承担完整仓储职责

验收命令：

```powershell
(Get-Content packages/ai/session_runtime.py | Measure-Object -Line).Lines
& $python -m pytest tests/test_session_message_v2.py tests/test_agent_session_runtime.py -q
```

### PR-03B 收口 pending action 的单一状态源

- [x] 完成 PR-03B
- 范围文件：`packages/ai/agent_service.py`、`packages/ai/session_pending.py`、`packages/ai/permission_next.py`
- 完成定义：移除或冻结历史 `_pending_actions` 内存态，避免和数据库态双写双读
- 完成定义：确认、拒绝、恢复动作都依赖单一来源

验收命令：

```powershell
rg -n "_pending_actions|_pending_lock" packages/ai
& $python -m pytest tests/test_agent_permission_next.py tests/test_agent_session_retry.py tests/test_agent_runtime_policy.py -q
```

### PR-03C 抽出 patch/revert 与 session 事件发布

- [x] 完成 PR-03C
- 范围文件：`packages/ai/session_runtime.py`、`packages/ai/session_snapshot.py`、`packages/ai/session_bus.py`
- 可新增文件：`packages/ai/session_revert.py`、`packages/ai/session_events.py` 或同等职责文件
- 完成定义：revert diff 收集、文件恢复、消息广播不再混在一个文件里
- 完成定义：远程恢复逻辑通过 W02 抽出的 service 访问，不再直连 router

验收命令：

```powershell
& $python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_session_compaction.py tests/test_agent_prompt_lifecycle.py -q
```

### W03 完成定义

- [x] `packages/ai/session_runtime.py` 明显瘦身
- [x] pending action 只剩一个可信状态源
- [x] revert/patch/事件发布边界已拆开

## W04 拆分 Workflow Runner

目标：把 `project_workflow_runner.py` 从“大总管”拆成“调度器 + handler 注册表 + workflow 模块”。

### PR-04A 建 handler 注册表和公共上下文

- [x] 完成 PR-04A
- 范围文件：`packages/ai/project_workflow_runner.py`、`packages/ai/project_workflow_catalog.py`
- 可新增目录：`packages/ai/project_workflows/`
- 完成定义：引入 workflow handler 注册表，runner 负责分发，不再保留长 if 链为主路径
- 完成定义：公共上下文加载、错误处理、阶段状态更新抽成共用层

验收命令：

```powershell
rg -n "if context\.run\.workflow_type ==" packages/ai/project_workflow_runner.py
& $python -m pytest tests/test_project_workflow_runner.py tests/test_project_execution_service.py -q
```

### PR-04B 验证 workflow 外置模块模式

- [x] 完成 PR-04B
- 范围文件：`packages/ai/project_workflow_runner.py`
- 可新增文件：`packages/ai/project_workflows/literature_review.py`、后续同类 handler 模块
- 完成定义：先把至少一个正式 workflow handler 从 runner 主文件迁到外置模块，确认 runtime 句柄模式可复用
- 完成定义：文献综述与 ARIS 相关测试矩阵保持通过

验收命令：

```powershell
& $python -m pytest tests/test_project_workflow_runner.py tests/test_aris_prompt_templates.py tests/test_aris_feature_matrix.py -q
```

### PR-04C 收尾 runner 瘦身与回归

- [x] 完成 PR-04C
- 范围文件：`packages/ai/project_workflow_runner.py`
- 可新增文件：`packages/ai/project_workflows/` 下的后续 handler 模块
- 完成定义：`project_workflow_runner.py` 行数降到 `9000` 以下，主路径只保留调度、公共 helper 与少量兼容入口
- 完成定义：workflow 相关 runner / action / GPU lease / ARIS 回归保持通过

验收命令：

```powershell
(Get-Content packages/ai/project_workflow_runner.py | Measure-Object -Line).Lines
& $python -m pytest tests/test_project_workflow_runner.py tests/test_project_multi_agent_runner.py tests/test_project_run_action_service.py tests/test_project_gpu_lease_service.py -q
npm run test:aris
```

### W04 完成定义

- [x] `packages/ai/project_workflow_runner.py` 不再是 9000+ 行神文件
- [x] workflow 分发改为注册表或 handler 映射
- [x] ARIS 定向 pytest 仍然通过

## W05 收缩 Repository 层

目标：按领域拆开 `repositories.py`，避免继续把所有数据访问往一个文件里堆。

### PR-05A 先拆 task 与 agent session 仓储

- [x] 完成 PR-05A
- 范围文件：`packages/storage/repositories.py`
- 可新增文件：`packages/storage/task_repository.py`、`packages/storage/agent_session_repository.py`
- 完成定义：任务追踪与 agent session 相关 repository 从单文件移出
- 完成定义：保留临时兼容 re-export，避免一次性改太多 import

验收命令：

```powershell
& $python -m pytest tests/test_task_tracker.py tests/test_agent_session_runtime.py tests/test_agent_session_revert_diff.py -q
```

### PR-05B 再拆 paper/topic/project 仓储

- [x] 完成 PR-05B
- 范围文件：`packages/storage/repositories.py`
- 可新增文件：`packages/storage/paper_repository.py`、`packages/storage/topic_repository.py`、`packages/storage/project_repository.py`
- 完成定义：paper/topic/project 主仓储脱离单文件
- 完成定义：`repositories.py` 只保留过渡导出或极少数共用 helper

验收命令：

```powershell
(Get-Content packages/storage/repositories.py | Measure-Object -Line).Lines
& $python -m pytest tests/test_analysis_repository.py tests/test_project_workflow_repository.py tests/test_projects_router_flows.py tests/test_topic_subscription_filters.py -q
```

### PR-05C 收口业务层对 repository 的直接拼装

- [x] 完成 PR-05C
- 范围文件：`apps/api/routers/projects.py`、`apps/api/routers/papers.py`、`apps/api/routers/topics.py`
- 范围文件：`packages/ai/project_execution_service.py`、`packages/ai/project_run_action_service.py`
- 完成定义：router 不再直接拼一长串 repository 细节
- 完成定义：高频业务路径补一层薄 service 或 facade

验收命令：

```powershell
& $python -m pytest tests/test_projects_router_flows.py tests/test_paper_analysis_service.py tests/test_topic_subscription_filters.py tests/test_project_execution_service.py -q
```

### W05 完成定义

- [x] `packages/storage/repositories.py` 不再是主要开发入口
- [x] 核心领域 repository 已拆分
- [x] 业务层对数据访问的编排开始收口

## W06 拆分前端 Agent 页面

目标：把 `frontend/src/pages/Agent.tsx` 收缩为装配页，抽出工作区、MCP/模型、trace 渲染三大块。

### PR-06A 抽工作区与终端面板

- [x] 完成 PR-06A
- 范围文件：`frontend/src/pages/Agent.tsx`
- 可新增文件：`frontend/src/components/agent/WorkspacePanel.tsx`、`frontend/src/components/agent/TerminalPanel.tsx`、`frontend/src/hooks/useAgentWorkspace.ts`
- 完成定义：工作区树、文件编辑、git diff、terminal session 逻辑从页面主文件迁出
- 完成定义：页面主文件不再直接持有大部分 workspace 局部状态
- 执行备注：本轮实际以 `frontend/src/components/agent/agentPageShared.tsx` 收口工作区、终端、toolbar 与 slash-command 共享逻辑，未继续维持额外 hook/面板文件拆分。

验收命令：

```powershell
(Get-Content frontend/src/pages/Agent.tsx | Measure-Object -Line).Lines
Set-Location frontend
npm run build
Set-Location ..
```

### PR-06B 抽 MCP、模型和 workflow launcher 配置区

- [x] 完成 PR-06B
- 范围文件：`frontend/src/pages/Agent.tsx`
- 可新增文件：`frontend/src/components/agent/AgentRuntimePanel.tsx`、`frontend/src/hooks/useAgentRuntimeConfig.ts`
- 完成定义：MCP 配置、模型切换、workflow drawer 逻辑从主页面迁出
- 完成定义：对应 API 调用集中在 hook 或 feature 层
- 执行备注：MCP / 模型 / workflow launcher 相关辅助函数与共享状态已并入 `frontend/src/components/agent/agentPageShared.tsx` 和 `frontend/src/features/assistantInstance/*`。

验收命令：

```powershell
Set-Location frontend
npm run build
Set-Location ..
```

### PR-06C 抽消息 trace、tool 结果视图与辅助组件

- [x] 完成 PR-06C
- 范围文件：`frontend/src/pages/Agent.tsx`
- 可新增文件：`frontend/src/components/agent/TraceViews.tsx`、`frontend/src/components/agent/ChatBlock.tsx`
- 完成定义：`CanvasPanel`、`WorkspaceInspectView`、`WorkspaceFileView`、`WorkspaceCommandView` 等从主页面迁出
- 完成定义：主页面只负责组合，而不是持有几千行视图实现
- 执行备注：实际新增 `frontend/src/components/agent/TraceViews.tsx` 承接消息 trace、空态、工件面板与多类 tool 结果视图。

验收命令：

```powershell
Set-Location frontend
npm run build
Set-Location ..
```

### PR-06D 收尾，压缩主页面状态数量

- [x] 完成 PR-06D
- 范围文件：`frontend/src/pages/Agent.tsx`
- 完成定义：`Agent.tsx` 行数降到 `4500` 以下
- 完成定义：主页面里的 `useState`、`useEffect` 数量明显下降
- 当前结果：`Agent.tsx` `4259` 行，`useState` `43` 处，`useEffect` `37` 处。

验收命令：

```powershell
(Get-Content frontend/src/pages/Agent.tsx | Measure-Object -Line).Lines
(Select-String -Path frontend/src/pages/Agent.tsx -Pattern "useState\(" | Measure-Object).Count
(Select-String -Path frontend/src/pages/Agent.tsx -Pattern "useEffect\(" | Measure-Object).Count
Set-Location frontend
npm run build
Set-Location ..
```

### W06 完成定义

- [x] `frontend/src/pages/Agent.tsx` 已明显缩小
- [x] 工作区、runtime 配置、trace view 已独立成 feature 模块
- [x] 前端构建仍通过

## W07 收口前后端状态协议

目标：减少前端猜字段、后端随手拼 dict 的情况，统一 assistant/session/workspace 相关 DTO。

### PR-07A 统一 assistant/session DTO

- [x] 完成 PR-07A
- 范围文件：`apps/api/routers/agent.py`、`apps/api/routers/session_runtime.py`、`packages/domain/schemas.py`
- 可新增文件：`packages/domain/assistant_schemas.py`
- 完成定义：conversation、message、pending action、assistant meta 的响应模型统一
- 完成定义：router 尽量返回 schema，而不是临时拼装 dict

验收命令：

```powershell
& $python -m pytest tests/test_agent_session_runtime.py tests/test_agent_tools_status.py tests/test_global_routes.py -q
```

### PR-07B 统一 workspace diff 与会话 patch review 协议

- [x] 完成 PR-07B
- 范围文件：`apps/api/routers/agent_workspace.py`、`apps/api/routers/agent.py`
- 范围文件：`frontend/src/services/api.ts`、`frontend/src/types/index.ts`
- 完成定义：workspace diff、session diff、revert info 字段命名统一
- 完成定义：前端不再同时兼容多套近似结构
- 执行备注：新增 `frontend/src/features/assistantInstance/sessionProtocol.ts` 作为 session/workspace 协议统一 normalizer。

验收命令：

```powershell
& $python -m pytest tests/test_agent_session_revert_diff.py tests/test_agent_workspace_ssh.py tests/test_projects_router_flows.py -q
Set-Location frontend
npm run build
Set-Location ..
```

### PR-07C 删除前端 fallback 解析分支

- [x] 完成 PR-07C
- 范围文件：`frontend/src/pages/Agent.tsx`、`frontend/src/features/assistantInstance/types.ts`、`frontend/src/features/assistantInstance/messageV2.ts`
- 完成定义：因为协议统一，前端删除多余 `Record<string, unknown>` 解析和兼容分支
- 完成定义：消息和工具渲染逻辑更稳定，不再靠字段猜测兜底

验收命令：

```powershell
Set-Location frontend
npm run build
Set-Location ..
& $python -m pytest tests/test_agent_session_runtime.py tests/test_session_message_v2.py -q
```

### W07 完成定义

- [x] assistant/session/workspace 关键 DTO 已统一
- [x] 前端 fallback parsing 明显减少
- [x] 前后端都通过对应回归

## W08 回归补测与兼容清理

目标：给前 7 周的结构调整补守门测试，删掉已无必要的兼容层，并把文档收口为可维护状态。

### PR-08A 补回归套件和 smoke

- [x] 完成 PR-08A
- 范围文件：`tests/conftest.py`、`tests/test_project_workflow_runner.py`、`tests/test_agent_session_runtime.py`
- 范围文件：`frontend/tests/smoke.spec.ts`、`scripts/run-aris-smoke.ps1`
- 完成定义：新增或加固“启动链、session、workflow dispatch、workspace service、Agent 页面主链路”回归
- 完成定义：至少一条 smoke 路径覆盖本次重构主干
- 执行备注：`scripts/aris_workflow_smoke.py` 已补 `experiment_audit` 覆盖；`frontend/tests/smoke.spec.ts` 已补齐 assistant 主链路并稳定化 LLM 配置选择器。

验收命令：

```powershell
npm run test:aris
pwsh -NoLogo -File scripts/run-aris-smoke.ps1
Set-Location frontend
npm run test:smoke
Set-Location ..
```

### PR-08B 删除无用兼容层并更新文档

- [x] 完成 PR-08B
- 范围文件：`packages/ai/agent_service.py`、`packages/storage/repositories.py`
- 范围文件：`docs/project-tech/researchos-refactor-execution-checklist.md`
- 完成定义：已确认不再使用的兼容 import、旧 helper、双状态源 shim 被清掉
- 完成定义：本文档所有完成项已勾选，未完成项明确写明阻塞原因
- 执行备注：内部实现入口已统一切到 `packages/agent/*`；`packages/ai` 下 session / runtime / tool / workspace / backend / MCP 同名文件仅保留给历史导入与测试的模块别名 shim。

验收命令：

```powershell
git diff --stat
rg -n "compat|shim|legacy" packages frontend/src
```

### W08 完成定义

- [x] 关键重构路径有测试守门
- [x] 兼容层清理到可接受范围
- [x] 本文档状态已同步到最新

## 5. 收官标准

- [x] `packages/storage/db.py` 不再做 import-time 初始化
- [x] `packages` 内不再反向依赖 `apps.api.routers`
- [x] `packages/ai/project_workflow_runner.py` 不再承担全部 workflow 细节
- [x] `packages/ai/session_runtime.py`、`packages/ai/agent_service.py` 职责收口
- [x] `packages/storage/repositories.py` 不再是单点巨型仓储文件
- [x] `frontend/src/pages/Agent.tsx` 退化为装配页
- [x] 前后端 assistant/session/workspace 协议已统一
- [x] `npm run test:aris` 与关键 pytest/smoke 验收通过

## 6. 每次合并后必须回填

- [x] 勾选对应 PR 项
- [x] 勾选对应周完成项
- [x] 在“执行记录”补一条日期、PR 编号和结果
- [x] 如果范围变更，直接修改本文档，不再另外起草一份新计划
