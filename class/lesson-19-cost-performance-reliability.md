# 第 19 课：成本、性能与稳定性

## 1. 本课定位

一个 LLM 系统即使功能很多，如果成本不可控、响应不稳定、资源使用失控，也很难长期运行。这一课要让你从“功能视角”切换到“工程约束视角”。最好的切口就是看最近新增的回归测试，它们直接告诉你作者刚刚修复了哪些真实稳定性问题。

## 2. 学完你应该能回答的问题

- 为什么科研助手项目一定会遇到成本与稳定性问题。
- `cost_guard`、超时、缓存、限流各自在缓解什么风险。
- 为什么性能优化在这里不只是“更快”，而是“更可持续”。
- 为什么安全默认值本身也是稳定性的一部分。

## 3. 学习前准备

- 阅读 `packages/ai/research/cost_guard.py`。
- 阅读 `packages/ai/ops/rate_limiter.py`。
- 阅读 `packages/integrations/llm_provider_policy.py`。
- 阅读 `tests/test_runtime_safety_regressions.py`。
- 对照 `packages/storage/paper_repository.py`、`packages/agent/workspace/workspace_executor.py`、`apps/api/deps.py` 看这些风险怎样真正落地。

## 4. 详细讲解

### 4.1 LLM 系统的四类常见风险

你可以先建立一个风险分类框架：

- 成本风险：调用太频繁、模型太贵、重复处理太多。
- 时延风险：请求慢、串行步骤过多、上下文太大。
- 稳定性风险：外部 API 不稳定、限流、超时、网络波动。
- 资源和安全风险：大文件、多并发、后台任务堆积、默认权限过宽。

这一课所有模块都可以映射回这四类风险。

### 4.2 `cost_guard` 为什么不是“财务功能”

成本守卫的意义不是记账，而是让系统做出更理性的运行选择：

- 控制单次调用预算。
- 控制每日预算。
- 避免低价值、重复、过度昂贵的处理。

当系统进入自动化运行阶段时，没有成本守卫几乎一定会出问题。

### 4.3 最近的回归测试给了你三个很好的稳定性案例

`tests/test_runtime_safety_regressions.py` 直接暴露了几个容易被忽略的问题：

- `TTLCache.get()` 读取过期键时必须顺手清理，否则缓存会积脏数据。
- `PaperRepository.folder_stats()` 按日期分组不能粗暴按 UTC 天切，需要走用户时区换算。
- `semantic_candidates()` 不能只看最近 500 篇，否则老论文里真正最相似的候选会被漏掉。

这三个例子很好，因为它们分别对应了：

- 内存正确性。
- 时间语义正确性。
- 检索质量正确性。

### 4.4 安全默认值本身也是系统稳定性的组成部分

这轮回归里还有一个非常重要的变化：默认工作区权限不再是全自动。

当前 `DEFAULT_ASSISTANT_EXEC_POLICY` 变成：

- `workspace_access = read_write`
- `command_execution = allowlist`
- `approval_mode = on_request`

这不是单纯“更保守”，而是避免系统在默认配置下做出不可控操作。对 Agent 平台来说，这本身就是可靠性设计。

### 4.5 缓存、限流和重试要放回真实运行链路里理解

当前仓库里，这些策略不是抽象口号，而是散落在多处真实链路里：

- `brief_cache_ttl` 控制 brief 类缓存时长。
- `rate_limiter` 和 worker 重试控制外部 API 访问节奏。
- `cost_guard` 影响 RAG、论文分析等步骤的模型选择。
- repository 查询策略影响性能与结果质量。

所以别把“性能优化”理解成单点微调，它往往是配置、策略、数据访问和默认权限一起作用的结果。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 更偏本地工作台，但同样会遇到状态、执行和资源管理问题。对照它能帮助你意识到：可靠性不仅属于网络调用，也属于本地能力、工作区和助手状态管理。

## 6. 代码精读顺序

1. `packages/ai/research/cost_guard.py`
2. `packages/ai/ops/rate_limiter.py`
3. `packages/integrations/llm_provider_policy.py`
4. `packages/storage/paper_repository.py`
5. `packages/agent/workspace/workspace_executor.py`
6. `apps/api/deps.py`
7. `tests/test_runtime_safety_regressions.py`

## 7. 动手任务

1. 列出 5 个你认为本项目最容易出现的成本或稳定性风险。
2. 将这些风险对应到现有模块或配置项。
3. 解释为什么“默认权限不能全自动”也属于稳定性设计。
4. 思考：如果你要新增一个高成本工作流，应该先检查哪些防线。

## 8. 验收标准

- 你能说出至少四类工程约束风险。
- 你能解释成本守卫、缓存、限流、超时分别解决什么问题。
- 你能用最近的回归测试举出至少三个真实稳定性案例。
- 你能理解可靠性是平台能力的一部分，而不是附属优化。

## 9. 常见误区

- 误区一：觉得成本问题只属于产品运营，不属于工程设计。
- 误区二：把缓存理解成纯粹性能技巧。
- 误区三：低估自动化任务场景下的不稳定性放大效应。
