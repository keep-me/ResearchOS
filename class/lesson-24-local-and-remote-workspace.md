# 第 24 课：本地与远程工作区执行

## 1. 本课定位

这一课是 `ResearchOS` 从“研究应用”走向“研究执行平台”的关键转折点。工作区执行意味着 Agent 不只读论文和回答问题，还能真正进入目录、读写文件、运行命令、操作远程机器。这轮代码修订里，这一层最重要的变化是默认策略不再全自动放行。

## 2. 学完你应该能回答的问题

- 为什么工作区能力是 Agent 系统的分水岭。
- `workspace_executor.py` 和 `workspace_remote.py` 分别处理什么。
- 本地工作区和远程工作区有哪些共同点与差异点。
- 为什么权限策略、根目录限制、命令允许列表非常重要。
- 为什么最近默认策略从 `full + off` 改成了 `allowlist + on_request`。

## 3. 学习前准备

- 阅读 `packages/agent/workspace/workspace_executor.py` 前半部分和关键函数名列表。
- 阅读 `packages/agent/workspace/workspace_remote.py` 的函数列表。
- 浏览 `packages/agent/workspace/workspace_server_registry.py`。
- 阅读 `tests/test_runtime_safety_regressions.py` 中权限相关测试。
- 阅读 `frontend/tests/smoke.spec.ts` 中工作区和 ACP 相关 smoke。

## 4. 详细讲解

### 4.1 工作区能力意味着什么

一旦 Agent 能操作工作区，系统能力会质变：

- 不只是“建议你做什么”。
- 而是“真的去目录里做事”。

这会直接引入新的工程问题：

- 路径安全。
- 权限控制。
- 命令执行策略。
- 文件读写审计。
- 远程连接稳定性。

所以工作区能力是强大能力，也是高风险能力。

### 4.2 本地执行层已经是“受控工作区操作系统”

`workspace_executor.py` 的函数和持久化配置文件已经非常说明问题：

- 默认 projects 根目录。
- 允许访问的 roots。
- 隐藏 roots。
- 助手执行策略。
- 目录列举、glob、grep、read。
- 写文件、替换文本、运行命令。

这说明本地执行层不只是一个 `subprocess.run()` 包装，而是正式的“受控工作区操作系统”。

### 4.3 这轮最关键的变化：默认策略收紧了

当前 `DEFAULT_ASSISTANT_EXEC_POLICY` 变成：

- `workspace_access = read_write`
- `command_execution = allowlist`
- `approval_mode = on_request`
- `allowed_command_prefixes = DEFAULT_COMMAND_ALLOWLIST`

允许列表里主要保留：

- `python`、`pytest`、`uv`
- `node`、`npm`、`pnpm`
- 只读或低风险的 git 前缀，例如 `git status`、`git diff`、`git log`

这和之前的全自动放行相比，默认面已经明显缩小。`tests/test_runtime_safety_regressions.py` 还专门验证默认策略不再是 full-auto。

### 4.4 权限系统有“兼容层”，但默认方向已经改了

`permission_next.py` 和 `agent_service.py` 里还能看到一层兼容逻辑：

- 当使用默认 policy provider 时，保留历史 permissive fallback。
- 当测试或调用方显式注入 override 时，按 override 来。
- 若 override 只写了 `approval_mode = off`，runtime 会补齐历史默认字段，避免旧测试全部失效。

这说明项目在做安全收紧时，同时注意了历史兼容和测试稳定性。

### 4.5 远程工作区和前端 smoke 让这层真正落地

`workspace_remote.py` 比本地执行层更复杂，因为它要处理：

- SSH 连接。
- 私钥与口令。
- 远程路径规范化。
- screen session 与 GPU 探测。
- 远程 git 操作。
- 远程执行环境准备。

而 `frontend/tests/smoke.spec.ts` 已经把这些能力真实串起来了：

- 本地工作区会话切换。
- 默认 projects root 创建流程。
- 远程 SSH 工作区上的 ACP confirm/reject/abort smoke。
- 带工作区绑定的问题卡片提交流程。

这说明工作区层已经不只是后端能力，而是前后端共同维护的正式产品面。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 更强调工作台、本地资产和助手界面共存，这提醒你本地工作区能力天然适合研究场景。对照它再回看当前仓库的 `workspace_executor.py`，会更容易意识到为什么路径限制、权限策略和执行治理必须正式存在。

## 6. 代码精读顺序

1. `packages/agent/workspace/workspace_executor.py`
2. `packages/agent/runtime/permission_next.py`
3. `packages/agent/runtime/agent_service.py`
4. `packages/agent/workspace/workspace_remote.py`
5. `packages/agent/workspace/workspace_server_registry.py`
6. `frontend/tests/smoke.spec.ts`

## 7. 动手任务

1. 总结本地执行层提供的 8 类能力。
2. 总结远程执行层比本地多出来的复杂点。
3. 解释为什么默认策略从 `full + off` 改成 `allowlist + on_request` 是合理的。
4. 画出一次远程 ACP confirm 流程图。

## 8. 验收标准

- 你能说明工作区能力带来的系统质变。
- 你能区分本地与远程执行层的共同点和差异。
- 你能理解权限、根目录限制、审批模式为什么是强制配置。
- 你能说清楚这轮默认策略收紧到底改了什么。

## 9. 常见误区

- 误区一：把工作区执行理解成“加了个终端”。
- 误区二：低估路径与权限安全问题。
- 误区三：忽视远程执行比本地执行高得多的复杂度。
