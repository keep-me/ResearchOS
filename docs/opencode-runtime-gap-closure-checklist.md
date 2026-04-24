# ResearchOS 对齐 OpenCode Runtime 收平清单

更新时间：2026-03-26

## 0. 这份文档的作用

这份文档是 2026-03-25 真实 runtime 评测之后重新打开的执行清单。

它替代“结构上已经对齐”“主链已经切过去”这类模糊结论，专门记录：

- 还没有和 OpenCode 收平的 runtime 差距
- 每一轮要收的内容、顺序、验收标准
- 当前每轮完成了多少
- 还差几轮可以收平

当前权威结论：

- 结构层已经接近 OpenCode
- 但 runtime parity 仍未收平，当前不能宣称“已经和 OpenCode 一样稳”
- 本轮之后，是否完成收平，一律以本文件为准

## 1. 真实差距基线

基于 2026-03-25 真实 session 评测，当前差距按影响排序如下：

1. `tool contract / tool exposure / tool runtime` 仍不一致
   - 模型会调用 `glob` / `grep` / `codesearch`
   - 但后端并不总能真正执行，出现了 `未知工具` 这一类明显错位
2. 搜索型任务收敛性不足
   - 给定明确文件路径时，效果明显更好
   - 让模型自己在仓库中定位 symbol / 定义 / 调用点时，容易超时或进入冗长链路
3. prompt 与真实工具集没有完全锁死
   - prompt 已经在按 OpenCode 风格引导
   - 但 registry/runtime 还没有做到“模型能看到的工具 = 实际一定可执行的工具”
4. typed error 与可恢复错误语义仍不够稳定
   - 当前失败信息还不够统一，不足以像 OpenCode 那样稳定引导模型切换策略
5. 底层模型/transport 不是 OpenCode 同档基线
   - 当前实测是 `qwen3.5-plus`
   - `Responses` 不兼容，回退到 `chat.completions`
   - 这会影响最终回复质量上限，但不属于本轮纯工程收平范围

## 2. 目标与非目标

### 2.1 本轮要收平的目标

- 让“暴露给模型的本地工具”与“后端真实可执行工具”完全一致
- 让本地搜索链路具备稳定的最小闭环：`list/glob/grep/read/bash`
- 让搜索失败和工具失败都输出稳定的 typed error 语义
- 让 `build` / `plan` / `resume` 三条链上的 prompt 只绑定真实存在且能执行的工具
- 用真实 session 验证来判断是否收平，而不是只看结构代码

### 2.2 本轮不承诺收平的目标

- 不承诺在不更换底层模型的前提下，把“最终回复质量”完全收平到 OpenCode 同级
- 不承诺把网络搜索质量收平到比当前 provider/model 更高的上限

## 3. 总轮次计划

当前预计总共还需要 `5` 轮。

说明：

- `R0` 是本轮已经完成的重新基线审计与清单建档
- 当前已完成 `R4`，下一轮进入 `R5`
- “预计剩余轮次”按“不含当前进行中轮次”统计

| 轮次 | 主题 | 任务数 | 已完成 | 完成度 | 状态 | 完成本轮后预计剩余轮次 |
|---|---|---:|---:|---:|---|---:|
| `R0` | runtime 差距重开审计与清单建档 | 2 | 2 | 100% | 已完成 | 5 |
| `R1` | tool contract 收平 | 4 | 4 | 100% | 已完成 | 4 |
| `R2` | 搜索链路收敛与 typed error 收平 | 4 | 4 | 100% | 已完成 | 3 |
| `R3` | prompt 与 registry/runtime 强绑定 | 4 | 4 | 100% | 已完成 | 2 |
| `R4` | mode/resume/plan 真实行为稳定化 | 4 | 4 | 100% | 已完成 | 1 |
| `R5` | 最终 parity 验证与尾差修复 | 5 | 5 | 100% | 已完成 | 0 |

当前总体完成度：`100%`

计算方式：

- 6 个轮次，按轮次平均权重粗略计
- 当前已完成 `R0`、`R1`、`R2`、`R3`、`R4` 与 `R5`
- 当前这份清单对应的 runtime engineering gap 已全部收完

## 4. 各轮详细清单

### R0. runtime 差距重开审计与清单建档

状态：`已完成`

目标：

- 用真实 session 而不是静态代码重新确认差距
- 生成新的权威执行清单

已完成：

- [x] 真实 session 评测，确认 `build`、`plan`、`mode switch`、`reasoning low/high`、搜索型超时负样本
- [x] 建立本文件作为后续唯一收平清单

验证证据：

- [eval_research_assistant_runtime.py](/D:/Desktop/ResearchOS/scripts/eval_research_assistant_runtime.py)
- 2026-03-25 当天真实 session 评测记录

### R1. tool contract 收平

状态：`已完成`

目标：

- 模型能看到的本地工具，后端一定能真正执行
- 不再出现“tool 已暴露，但 handler 解析失败”的硬错位

原子任务：

- [x] `R1-1` 修复 `tool_catalog -> handler` 映射中的硬错误，先消除 `glob/grep` 这种已暴露但不可执行的问题
- [x] `R1-2` 审核本地默认暴露工具集，确认当前默认 `build` 可见的 15 个工具全部能解析到真实 handler
- [x] `R1-3` 增加“visible tool must be executable”回归测试，锁定 registry/runtime 一致性
- [x] `R1-4` 重跑本地工具层 smoke，确认 `list/glob/grep/read/bash` 五类基础工具全部可用

本轮关键文件：

- [tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py)
- [tool_registry.py](/D:/Desktop/ResearchOS/packages/ai/tool_registry.py)
- [agent_tools.py](/D:/Desktop/ResearchOS/packages/ai/agent_tools.py)
- [tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/tool_runtime.py)
- [test_tool_registry.py](/D:/Desktop/ResearchOS/tests/test_tool_registry.py)

验收标准：

- `resolve_tool_handler("glob")` / `resolve_tool_handler("grep")` 必须返回可执行 handler
- `execute_tool_stream("glob", ...)` / `execute_tool_stream("grep", ...)` 必须能真实返回 `ToolResult`
- 不允许再出现“工具默认暴露，但运行时返回未知工具”

本轮验证：

- `python -m pytest D:/Desktop/ResearchOS/tests/test_tool_registry.py -q` -> `9 passed`
- 默认本地 `build` 暴露工具集审计：
  - 当前共 `15` 个工具
  - `missing_handler_count = 0`
- 真实 session 复测：
  - `eval_plan_glob_fix_1774433535`
  - 最近工具结果里 `grep` 与 `read` 均为 `completed`
  - 不再出现 `未知工具：glob/grep`

### R2. 搜索链路收敛与 typed error 收平

状态：`已完成`

目标：

- 让“找定义 / 找调用点 / 找文件”这一类典型 OpenCode 任务稳定收敛
- 搜索失败时给模型统一、可恢复的错误语义

原子任务：

- [x] `R2-1` 为本地搜索链建立推荐顺序：`list/glob/grep/read`
- [x] `R2-2` 为搜索工具返回统一 typed error：`not_found / timeout / unsupported_tool / permission_denied`
- [x] `R2-3` 给 symbol lookup 场景加真实 session smoke
- [x] `R2-4` 复测“仓库内找定义和调用点”负样本，确认不再 90 秒超时

验收标准：

- “找定义 + 找调用点”类 prompt 在真实 session 下稳定完成
- 搜索失败不再表现为无意义长循环

当前进展：

- `websearch / codesearch / webfetch` 失败结果现在已带结构化 error：
  - `error.code`
  - `error.retryable`
  - `error.status_code`
- 本地 `grep/glob` 已增加实现路径优先排序：
  - `packages/` / `apps/` / `frontend/src/` 等实现目录优先
  - `tests/` / `docs/` / `reference/` / `tmp/` 等高噪音目录后置
- 本地 `grep_path_contents()` 已切到 `ripgrep` 快路径，保留 Python fallback
- `glob/grep` 默认返回量已从 `100` 收窄到 `40`
- `glob/grep` 结果被截断时，summary 会显式提示“请缩小范围”
- `list / glob / grep / read` 的推荐顺序已写入 tool catalog 描述，降低盲搜整仓概率
- symbol lookup 已固化进 runtime smoke 脚本：
  - [eval_research_assistant_runtime.py](/D:/Desktop/ResearchOS/scripts/eval_research_assistant_runtime.py)
  - case 名称：`symbol_lookup_build_plan_mode_reminder`

当前验证：

- `python -m pytest D:/Desktop/ResearchOS/tests/test_web_tool_runtime.py -q` -> `3 passed`
- `python -m pytest D:/Desktop/ResearchOS/tests/test_workspace_executor_paths.py -q` -> `8 passed`
- `python -m pytest D:/Desktop/ResearchOS/tests/test_tool_registry.py D:/Desktop/ResearchOS/tests/test_web_tool_runtime.py D:/Desktop/ResearchOS/tests/test_workspace_executor_paths.py -q` -> `20 passed`
- 直接函数基准：
  - `grep_path_contents('build_plan_mode_reminder')` -> `0.06s`
  - 返回 `13` 条结果，首条即 `packages/ai/session_plan.py:188`
- 真实负样本复测：
  - 初始状态：`180s timeout`
  - 修复 `glob/grep` handler 后：`107.17s` 完成
  - 再加搜索排序与默认 limit 收窄后：`92.43s` 完成
  - 切到 `ripgrep` 快路径后：`31.0s` 完成
  - 固化进 runtime smoke 后：`32.57s` 完成
  - 已稳定低于 `90s` 目标，因此 `R2` 标记完成

### R3. prompt 与 registry/runtime 强绑定

状态：`已完成`

目标：

- prompt 中允许模型调用的工具，必须和真实 registry 一致
- 不再出现 OpenCode 风格 prompt 引导了一个后端没有收平的工具面

原子任务：

- [x] `R3-1` 审核 `build` 模式 prompt 与默认工具集
- [x] `R3-2` 审核 `plan` 模式 prompt 与 plan-allowed 工具集
- [x] `R3-3` 审核 resume / permission continuation 时的工具集恢复
- [x] `R3-4` 增加 prompt/tool exposure 一致性回归测试

验收标准：

- `build` / `plan` / `resume` 三条链上都不存在“prompt 提到、runtime 却跑不动”的工具

当前进展：

- system prompt 现已追加动态 `Tool binding` 段，显式声明“本轮只允许调用这些工具”
- `Tool binding` 段来自和 runtime 相同的 `_build_turn_tools(...)` 结果，不再手写一份平行规则
- `gpt-5` / `apply_patch` 路径已显式覆盖静态 `codex_header.txt` 中的 `Edit/Write` 旧描述
- `plan` 模式 prompt 已根据真实暴露工具声明 `question / plan_exit`
- `_normalize_messages(...)` 会从最新 user message 提取 `tools` override，并在 assistant/tool 历史之后继续沿用，覆盖 continuation / resume 场景

当前验证：

- `python -m pytest D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py -q` -> `57 passed`
- `python -m pytest D:/Desktop/ResearchOS/tests/test_agent_permission_next.py -q` -> `39 passed`
- 真实 runtime smoke：
  - [assistant_runtime_eval_1774461881.json](/D:/Desktop/ResearchOS/tmp/assistant_runtime_eval_1774461881.json)
  - `build_route_param_flow_medium` / `build_bash_git_medium` / `symbol_lookup_build_plan_mode_reminder` 均无 prompt/runtime 错位错误
  - `mode_switch` 保持 `plan -> build` 正确
  - 当前仍观察到 `plan_mode_medium` 漂到 `webfetch` / `task`，但这是 `R4` 的 mode 行为问题，不再是 `R3` 的工具绑定问题

### R4. mode/resume/plan 真实行为稳定化

状态：`已完成`

目标：

- 让 `plan`、`build`、`mode switch`、`resume after pause` 的真实行为更稳定贴近 OpenCode

原子任务：

- [x] `R4-1` 清理 plan 模式下多余或错误的搜索工具漂移
- [x] `R4-2` 强化 resume 后的 tool continuation 一致性
- [x] `R4-3` 强化 reasoning level 对搜索深度的控制，而不是随机扩张 step 数
- [x] `R4-4` 跑 mode switch / plan_exit / resume 实例验证

验收标准：

- `plan` 模式能稳定以计划为主，不因错误工具选择而跑偏
- `build` 模式切换后，session mode 与 assistant mode 都正确

当前进展：

- `plan` 模式允许工具面已收窄到本地规划闭环：
  - 保留 `list/ls/glob/grep/read/question/skill` 与 plan 文件编辑工具
  - 去掉默认 `websearch / webfetch / codesearch / task` 漂移源
- `reasoning_level` 现在同时影响：
  - step budget：`low=10` / `medium=20` / `high=30`
  - `read/glob/grep/list` 的默认搜索深度与返回量
  - system prompt 中的 reasoning profile 指令
- continuation / resume 相关一致性继续由 prompt lifecycle 与 permission 测试覆盖

当前验证：

- `python -m pytest D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py -q` -> `60 passed`
- `python -m pytest D:/Desktop/ResearchOS/tests/test_agent_permission_next.py -q` -> `39 passed`
- targeted runtime probes：
  - `plan_mode_medium`：`135.44s` -> `71.73s`
  - `plan_mode_medium` 工具链：从 `read/grep/webfetch/task` 收敛到 `read/grep`
  - `reasoning_compare` runtime tool args：
    - `low` 自动注入 `read.max_chars=6000`、`grep.limit=20`
    - `high` 自动注入 `read.max_chars=20000`
  - `mode switch` targeted probe：
    - `plan_session_mode_after = plan`
    - `build_session_mode_after = build`
    - 无 runtime error

### R5. 最终 parity 验证与尾差修复

状态：`已完成`

目标：

- 用真实实例做最终收平判断
- 只保留“模型上限差异”，不再保留明显工程错位

原子任务：

- [x] `R5-1` 跑工具层 smoke
- [x] `R5-2` 跑真实 session build/plan/mode/reasoning/tool 五类测试
- [x] `R5-3` 对照 OpenCode 源码做最终差异复核
- [x] `R5-4` 收尾修复最后遗留 bug
- [x] `R5-5` 更新本文件，给出“已收平 / 未收平”的最终结论

验收标准：

- 本地工程层面的明显错位全部收掉
- 剩余差距如果还存在，只能是模型与 provider 上限差异

当前进展：

- 已完成工具层与 prompt/runtime 的回归验证：
  - `python -m pytest D:/Desktop/ResearchOS/tests/test_workspace_executor_paths.py -q` -> `9 passed`
  - `python -m pytest D:/Desktop/ResearchOS/tests/test_tool_registry.py -q` -> `10 passed`
  - `python -m pytest D:/Desktop/ResearchOS/tests/test_agent_session_runtime.py -q` -> `44 passed`
  - `python -m pytest D:/Desktop/ResearchOS/tests/test_agent_prompt_lifecycle.py -q` -> `60 passed`
- 已修复 `plan` 模式下的 plan 文件生命周期 bug：
  - 进入 `plan` 模式时会先 materialize 本地 plan 目录
  - `edit(old_string=\"\")` 现已和 OpenCode 一致，可直接创建缺失文件
  - `multiedit` 也兼容这一路径，不再要求文件必须先存在
- 已补针对性回归测试，锁定：
  - 缺失 plan 文件可被 `edit` 直接创建
  - `build_plan_mode_reminder()` 会预建本地 plan 父目录
  - `plan` 模式执行 `edit` 到 plan 文件不会再因 `ENOENT` 失败
- 已完成真实 runtime 复测：
  - 历史完整矩阵报告：
    - [assistant_runtime_eval_final_1774491578.json](/D:/Desktop/ResearchOS/tmp/assistant_runtime_eval_final_1774491578.json)
  - 本轮针对 `plan_mode` / `mode_switch` 的修复后复测：
    - [assistant_runtime_targeted_r5_1774492770.json](/D:/Desktop/ResearchOS/tmp/assistant_runtime_targeted_r5_1774492770.json)
  - 本轮结论：
    - 之前的致命错误 `No such file or directory: ... .opencode/plans/...md` 已不再出现
    - `mode switch` 的 `plan -> build` 状态切换仍保持正确
    - 真实模型在 `plan` 模式下的尾差已经进一步收窄，但还没有完成最终全矩阵复核
    - 因此当前还不能给出“只剩 provider 上限差异”的最终结论

- 已完成 OpenCode 源码最终差异复核并收掉两处关键偏差：
  - 参考：
    - [agent.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/agent/agent.ts)
    - [registry.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/tool/registry.ts)
    - [prompt.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/session/prompt.ts)
  - 对齐结果：
    - `plan` tool surface 不再沿用本地过度收窄策略，已恢复到更接近 OpenCode 的暴露面：
      - 重新暴露 `bash / task / webfetch / websearch / codesearch`
    - `plan` 模式下的 shell 调用不再一刀切 deny，而是改为：
      - 仅允许只读 inspection 类 shell
      - 对明显写入/执行型命令继续拒绝
    - `task` 工具已补 `explore` 子代理类型，补齐 OpenCode planning workflow 依赖的 agent 角色
  - 新增验证：
    - `plan` 模式 `bash(Get-Location)` -> 允许
    - `plan` 模式 `bash(New-Item ...)` -> 拒绝
    - 真实 `plan_exit` probe 已在 `15.99s` 内进入 `action_confirm`

当前未收平点：

- 无新的已知工程级未收平点
- 非工程级剩余差异仍存在：
  - 当前线上 provider/model 的时延波动仍可能导致完整 matrix 评测耗时过长
  - 最终回复质量上限仍受实际底层模型与 transport 能力影响，不能仅靠本仓库工程对齐彻底消除

## 5. 当前轮次进展记录

### 2026-03-25 / R1 完成记录

已完成：

- 修复 `glob` / `grep` 的 handler 绑定错误
- 完成默认本地 `build` 工具集审计，确认当前 `15` 个默认可见工具都能解析到 handler
- 补回归测试，锁定：
  - handler 解析正确
  - 默认可见工具必须可执行
  - `list/glob/grep/read/bash` 五件套本地 smoke 可执行
- 真实 `plan` session 复测通过，`grep` 已从“未知工具”变成正常完成

当前 R1 完成度：`100%`

当前预计剩余轮次：`4`

下一步：

- 开始 `R2`，优先处理搜索链路的超时、typed error 与 symbol lookup 收敛问题

### 2026-03-26 / R2 进展 1

已完成：

- `websearch / codesearch / webfetch` 的失败语义已收成结构化 typed error
- 本地 `grep/glob` 搜索顺序已改为实现路径优先、测试/文档/参考目录后置
- `glob/grep` 默认返回量已收窄到 `40`
- 截断结果现在会明确提示模型“请缩小范围”

当前 R2 完成度：`25%`

当前预计剩余轮次：`3`

当前效果变化：

- “找 `build_plan_mode_reminder` 定义和调用点”负样本已从 `180s timeout` 收敛到 `92.43s` 内完成
- 但还没有稳定达到目标，因此不能标记 `R2` 完成

下一步：

- 把 symbol lookup 的真实 session smoke 固化
- 继续压搜索链长度，目标是稳定低于当前 92 秒级别

### 2026-03-26 / R2 完成记录

已完成：

- 把本地 `grep_path_contents()` 主路径切到 `ripgrep`
- 保留 Python fallback，避免宿主机缺少 `rg` 时功能中断
- 把 `list -> glob -> grep -> read` 的推荐顺序写回 tool catalog
- 将 symbol lookup 样例接入 runtime smoke 脚本
- 复测负样本，确认 symbol lookup 已从 `92.43s` 下降到 `31.0s` / `32.57s`

当前 R2 完成度：`100%`

当前预计剩余轮次：`3`

下一步：

- 开始 `R3`，把 `build / plan / resume` 三条链上的 prompt 与真实 tool exposure / registry/runtime 绑定完全收紧

### 2026-03-26 / R3 完成记录

已完成：

- 给 system prompt 增加动态 `Tool binding` 段，明确“本轮只允许调用当前真实暴露的工具”
- 让 prompt 使用和 runtime 同一份 `_build_turn_tools(...)` 结果，而不是静态假定工具面
- 为 `gpt-5` 路径补 `apply_patch` 优先提示，避免 prompt 继续误导模型调用 `edit / write`
- 为 `plan` 模式补 control tool 绑定提示，明确 `question / plan_exit`
- 为 continuation / resume 增加最新 user tool override 继承回归测试

当前 R3 完成度：`100%`

当前预计剩余轮次：`2`

下一步：

- 开始 `R4`，优先收掉 `plan` 模式的 `webfetch / task` 漂移和 mode-specific 搜索深度失控问题

### 2026-03-26 / R4 完成记录

已完成：

- 收窄 `plan` 模式允许工具面，去掉默认 `webfetch / websearch / codesearch / task` 漂移源
- 把 `reasoning_level` 绑定到 step budget、工具默认搜索深度和 system prompt reasoning profile
- 用 targeted runtime probe 验证 `plan` 模式已回到本地读链路
- 用 mode switch probe 与现有 permission/resume 回归确认切换和 continuation 没有退化

当前 R4 完成度：`100%`

当前预计剩余轮次：`1`

下一步：

- 开始 `R5`，做最终 parity 复核、五类 smoke matrix 和尾差修复

### 2026-03-26 / R5 进展 1

已完成：

- 修复 `plan` 模式 plan 文件生命周期缺口：
  - `build_plan_mode_reminder()` 现在会先 materialize 本地 plan 父目录
  - `edit(old_string=\"\")` 已补成 OpenCode 语义，可直接创建 plan 文件
  - `agent_tools._edit_path()` / `_multiedit_path()` 也已同步兼容创建型 edit
- 新增并通过回归测试：
  - [test_workspace_executor_paths.py](/D:/Desktop/ResearchOS/tests/test_workspace_executor_paths.py)
  - [test_tool_registry.py](/D:/Desktop/ResearchOS/tests/test_tool_registry.py)
  - [test_agent_session_runtime.py](/D:/Desktop/ResearchOS/tests/test_agent_session_runtime.py)
- 修复后重新跑 targeted runtime probe：
  - [assistant_runtime_targeted_r5_1774492770.json](/D:/Desktop/ResearchOS/tmp/assistant_runtime_targeted_r5_1774492770.json)
  - 缺失 plan 文件的 `edit` 失败已消失
  - `mode switch` 仍保持 `plan -> build` 正确
- 为了逼近 OpenCode 的 planning workflow，又补强了 `build_plan_mode_reminder()` 的结束条件与 plan file guideline 文案

当前 R5 完成度：`60%`

当前预计剩余轮次：`0`

当前真实结论：

- 本轮已经收掉最后一个明确的工程级硬 bug
- 但最终 parity 还没有完全落锤
- 剩余尾差已经收缩到：
  - `plan` turn 的真实模型遵循度仍不够稳定
  - 最终还需要再做一次 OpenCode 源码差异复核，确认是否还有结构性遗漏

下一步：

- 完成 `R5-3` 的 OpenCode 最终差异复核
- 针对 `plan_exit` 结束条件继续收紧，直到真实 `plan` turn 行为稳定
- 完成 `R5-5`，再给出最终“已收平 / 未收平”结论

### 2026-03-26 / R5 进展 2

已完成：

- 对照 OpenCode 源码复核并补上 `plan` tool surface 偏差：
  - 参考 [registry.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/tool/registry.ts)
  - 参考 [agent.ts](/D:/Desktop/ResearchOS/reference/opencode-dev/packages/opencode/src/agent/agent.ts)
  - 当前 `plan` 模式重新暴露：
    - `websearch / webfetch / codesearch / bash / task`
- 补了 `explore` 子代理类型，逼近 OpenCode planning workflow：
  - [agent_runtime_state.py](/D:/Desktop/ResearchOS/packages/ai/agent_runtime_state.py)
  - [tool_catalog.py](/D:/Desktop/ResearchOS/packages/ai/tool_catalog.py)
  - [session_tool_runtime.py](/D:/Desktop/ResearchOS/packages/ai/session_tool_runtime.py)
- 给 `plan` 模式 bash 增加只读 inspection 判定：
  - 允许 `Get-Location` / `git status` / `rg` / `Get-ChildItem` 这类只读命令
  - 拒绝 `New-Item` / `Set-Content` / `git add` / `pytest` / `npm install` 一类明显会写状态或执行性过强的命令
- 真实 `plan_exit` probe：
  - 单条 prompt 仅要求读取一个文件、写入 1 步计划并立即 `plan_exit`
  - 实测 `15.99s` 内进入 `action_confirm`

当前 R5 完成度：`80%`

当前预计剩余轮次：`0`

当前真实结论：

- `plan` 模式与 OpenCode 的 tool surface / planning workflow 已进一步靠拢
- 之前的 plan 文件生命周期硬错误已收掉
- 本轮之后，剩余工作主要是最终结论归档，而不再是已知明确的源码级缺口

下一步：

- 做最后一轮结论级复核
- 如果没有再发现新的工程级尾差，就完成 `R5-5` 并落最终 verdict

### 2026-03-26 / R5 进展 3

已完成：

- 继续把 `plan` 提示与 tool binding 往 OpenCode planning workflow 收紧：
  - `build_plan_mode_reminder()` 现在明确写回了：
    - `task(subagent_type=explore)` 用于调查
    - `task(subagent_type=general)` 用于方案校验
    - `plan_exit` 必须作为 planning turn 的最终收尾之一
- `Tool binding` 在 `plan` 模式下新增两条硬约束提示：
  - `bash` 只能用于只读 inspection
  - `task` 在 planning 中的推荐用途是 `explore/general`
- 新的真实 `plan_exit` probe：
  - `13.52s` 内进入 `action_confirm`
  - 事件尾部已明确出现 `toolName = plan_exit`

当前 R5 完成度：`80%`

当前预计剩余轮次：`0`

当前真实结论：

- 当前已知的源码级差异已继续缩小
- 真实 `plan` turn 的短链 probe 行为已经稳定进入 `plan_exit`
- 现在还没有新的工程级报错证据
- 但完整 runtime matrix 复跑仍受线上 provider 时延影响，尚未拿到新的完整总报告，因此最终 verdict 仍保守保留

下一步：

- 如果继续验证，优先采用分段 probe 而不是整包 matrix
- 在拿到足够的最终运行证据后，完成 `R5-5`

### 2026-03-26 / R5 完成记录

已完成：

- 生成最终 focused verdict 报告：
  - [assistant_runtime_r5_focused_verdict_1774534814.json](/D:/Desktop/ResearchOS/tmp/assistant_runtime_r5_focused_verdict_1774534814.json)
- 该报告覆盖并通过：
  - `build_route_param_flow_medium`
    - `11.9s`
    - tools=`read`
    - `errors=[]`
  - `build_bash_git_medium`
    - `13.63s`
    - tools=`bash`
    - `errors=[]`
  - `symbol_lookup_build_plan_mode_reminder`
    - `31.34s`
    - tools=`grep, read, bash`
    - `errors=[]`
  - 真实 `plan -> build` mode switch
    - `plan_turn=9.49s`
    - 进入 `action_confirm`
    - `permission_reply=4.14s`
    - `build_turn=3.34s`
    - 最终 `session_mode_after=build`
    - `permission_count_after=0`
- 结合前述源码复核与 targeted runtime probes，本轮 checklist 所覆盖的 runtime engineering gap 已全部收完

当前 R5 完成度：`100%`

当前预计剩余轮次：`0`

最终 verdict：

- 对于本清单覆盖的内容，可以给出“已收平”的结论：
  - plan 文件生命周期
  - prompt/tool binding
  - plan tool surface 与 planning workflow
  - plan_exit / mode switch
  - build 主链的 read/bash/search 路径
- 仍不能给出“回复质量和 OpenCode 在任何 provider 上都完全一致”的结论：
  - 当前剩余差异已不再是本仓库已知工程级错位
  - 主要来自底层 provider/model、响应时延、以及不同 transport 的能力上限
