# 第 30 课：测试体系与后续深入路线

## 1. 本课定位

最后一课不是收尾，而是把你真正送进“可持续深入学习”的阶段。大型项目学习到最后，最有价值的能力不是背下模块名，而是知道如何用测试和小改动继续深入。最近这轮 `锐评 -> 验收通过` 的修订，本身就留下了一组非常好的测试学习入口。

## 2. 学完你应该能回答的问题

- 为什么测试文件是理解大型仓库的高价值入口。
- `test_app_startup.py`、`test_agent_session_runtime.py`、`test_project_workflow_runner.py` 分别代表什么层次的测试价值。
- `test_auth_security.py`、`test_storage_bootstrap.py`、`test_runtime_safety_regressions.py` 在保护什么回归。
- 为什么前端 `smoke.spec.ts` 也是理解 Agent 页和工作区能力的重要入口。
- 学完 30 课后，下一阶段最合理的深入路线是什么。

## 3. 学习前准备

- 阅读 `tests/test_app_startup.py`。
- 浏览 `tests/test_auth_security.py`。
- 浏览 `tests/test_storage_bootstrap.py`。
- 浏览 `tests/test_runtime_safety_regressions.py`。
- 浏览 `tests/test_agent_session_runtime.py` 和 `tests/test_project_workflow_runner.py` 的测试名列表。
- 浏览 `frontend/tests/smoke.spec.ts` 的测试名列表。

## 4. 详细讲解

### 4.1 为什么测试是阅读大型项目的捷径

测试的价值不只是防回归，它还在告诉你：

- 系统认为什么行为必须稳定。
- 哪些边界最容易出错。
- 作者最担心什么回归。

所以当你不知从哪读时，测试往往比业务代码更容易给你方向。

### 4.2 先看最近新增的三类回归保护

这轮修订新增或强化的几个测试非常适合作为学习入口：

- `tests/test_auth_security.py`
  - 保护认证 secret 必填。
  - 保护非 dev 环境必须使用哈希密码。
  - 保护 query token 只用于允许路径。
- `tests/test_storage_bootstrap.py`
  - 保护“导入 db 不应自动建表”。
  - 保护显式 bootstrap 会创建 `alembic_version`。
  - 保护旧库会被正确 stamp 到最新 revision。
- `tests/test_runtime_safety_regressions.py`
  - 保护默认工作区策略不再 full-auto。
  - 保护 TTL cache 过期键清理。
  - 保护时区分组和 semantic candidate 查询质量。

这三类测试很好，因为它们对应的是安全、迁移、运行时稳定性三个核心面。

### 4.3 `test_app_startup.py` 的价值

这个测试虽然短，但它说明了一个关键设计原则：

- API 启动必须显式调用 `bootstrap_api_runtime`。

这会帮助你理解：系统作者把“启动时准备依赖”当成正式保障点，而不是偶然行为。

### 4.4 `test_agent_session_runtime.py` 与前端 smoke 是一体两面

`tests/test_agent_session_runtime.py` 很长，这恰恰说明 Agent 层复杂度高。再结合 `frontend/tests/smoke.spec.ts`，你会看到一套很完整的保护面：

- 后端保护消息 part、流式持久化、workspace binding、plan/build 模式切换。
- 前端保护工作区切换、ACP confirm/reject/abort、问题卡片、设置页 ACP registry。

这很重要，因为很多学习者只看后端测试，忽略了真正的用户链路其实还要靠 smoke 保证。

### 4.5 `test_project_workflow_runner.py` 的价值

这份测试文件告诉你工作流系统在乎什么：

- stage checkpoint。
- workspace artifacts。
- remote experiment execution。
- reviewer loops。
- full pipeline resume。
- GPU lease 避让。

也就是说，工作流层已经不只是业务逻辑，而是带有编排系统特征。

## 5. 参考项目对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 作为一套完整工作台代码，也需要依赖测试和分层边界来维持稳定。对照它可以帮助你理解：一旦产品边界足够大，靠记忆维护理解是不可能的，必须借助测试和小步改动闭环。

## 6. 代码精读顺序

1. `tests/test_auth_security.py`
2. `tests/test_storage_bootstrap.py`
3. `tests/test_runtime_safety_regressions.py`
4. `tests/test_app_startup.py`
5. `tests/test_agent_session_runtime.py`
6. `frontend/tests/smoke.spec.ts`
7. `tests/test_project_workflow_runner.py`
8. 回看与这些测试对应的实现文件

## 7. 动手任务

1. 从六份测试中各挑 3 个最有代表性的测试点，写出它们在保护什么行为。
2. 选择一条你最想深入的主线，写一个 14 天学习计划。
3. 为自己定义一个可控的小改动目标，并标出你打算先读哪些文件。
4. 解释为什么最近这轮提交能从“锐评”走到“验收通过”，测试补强起了什么作用。

## 8. 验收标准

- 你能把测试当成系统设计的窗口来使用。
- 你能根据测试名反推出关键稳定性边界。
- 你能说明后端单测和前端 smoke 在这个仓库里如何互补。
- 你能为下一阶段深入制定清晰路线，而不是继续泛泛浏览。

## 9. 常见误区

- 误区一：把测试只当成“以后再看”的东西。
- 误区二：读完课程后又回到无目标浏览。
- 误区三：一上来就做过大的改动，而不是先选一条线打穿。
