# 第 11 课：存储启动链路

## 1. 本课定位

从这一课开始，课程进入数据层。你要理解的第一件事不是模型细节，而是“存储层如何被准备好”。最近这轮代码修订里，数据库启动链路已经从旧式的 ad-hoc migration 逻辑，收敛成 Alembic 驱动的显式 bootstrap。

## 2. 学完你应该能回答的问题

- `bootstrap_storage()`、`bootstrap_api_runtime()`、`bootstrap_worker_runtime()`、`bootstrap_local_runtime()` 分别做什么。
- 为什么现在不应该再把 `packages/storage/db.py` 理解成“导入即建表”。
- API 进程和 Worker 进程在存储准备上的异同是什么。
- 为什么任务追踪器要在启动阶段从存储恢复。
- 为什么旧数据库要先 stamp 再 reconcile upgrade。

## 3. 学习前准备

- 阅读 `packages/storage/bootstrap.py`。
- 阅读 `packages/storage/db.py`。
- 回看 `apps/api/main.py`、`apps/worker/main.py` 和 `scripts/local_bootstrap.py` 中对 bootstrap 的调用。
- 阅读 `tests/test_storage_bootstrap.py`。

## 4. 详细讲解

### 4.1 为什么要把启动引导单独抽成模块

如果没有统一 bootstrap，各个入口会各自做数据库初始化、目录准备、运行时恢复，最终变成：

- 重复逻辑
- 顺序不一致
- 某些入口忘记初始化
- 后续难以维护

`packages/storage/bootstrap.py` 的价值就在于：把“运行前必须准备好的事情”集中起来，供 API、Worker、本地脚本等多个入口复用。

### 4.2 这轮改动后的核心变化：迁移正式交给 Alembic

当前 `bootstrap_storage()` 的真实流程是：

1. 用 SQLAlchemy inspector 查看用户表。
2. 用 `MigrationContext` 读取当前 revision。
3. 如果已经有用户表但没有 `alembic_version`，判定为旧库。
4. 先 stamp 到 `_LEGACY_RECONCILE_BASELINE = 20260412_0011_add_project_research_wiki`。
5. 再 `command.upgrade(..., "head")`。
6. 执行 `_ensure_initial_import_action()` 做迁移后的数据补全。

这已经不是“简单建表”，而是正式 schema 演进链路。

### 4.3 `db.py` 现在只保留基础设施，不再偷偷管 schema

这一点很关键。`packages/storage/db.py` 现在只负责：

- `Base`
- `engine`
- `SessionLocal`
- SQLite PRAGMA
- `session_scope()`
- `check_db_connection()`

对应的测试 `tests/test_storage_bootstrap.py` 还专门验证了：

- 仅仅 `import packages.storage.db` 不会创建任何表。
- 只有显式调用 `bootstrap_local_runtime()` 才会初始化 schema。

这说明“导入基础设施”和“执行迁移”已经被明确分层。

### 4.4 API runtime bootstrap 为什么多了一步

`bootstrap_api_runtime()` 在 `bootstrap_storage()` 之后，还会恢复 `global_tracker`。这说明 API 进程除了存储层本身，还承担运行时可观察状态：

- 某些异步任务可能之前已经存在。
- 前端页面可能需要看到这些状态。
- 启动后需要把任务信息重新加载进内存结构。

这个细节很重要，因为它说明后端不是“纯数据库 + 纯请求”，而是存在运行时状态恢复需求。

### 4.5 `_ensure_initial_import_action()` 说明迁移不只是表结构

当前 bootstrap 里还有一个容易被忽略的动作：如果发现 `papers` 已经有数据，但 `action_papers` 里还没有对应关联，就会自动创建一条 `initial_import` 的 `collection_actions` 记录并回填关联。

这告诉你两个事实：

- 存储 bootstrap 既要管 schema，也可能要管历史数据补全。
- 迁移完成后的“业务一致性”同样是启动链路的一部分。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 的本地优先形态也必须解决“启动时把数据层准备好”的问题。对照它能帮助你理解：不管是 Web/API 中心还是桌面工作台中心，启动时的数据准备都是正式架构层，而不是临时脚本细节。

## 6. 代码精读顺序

1. `packages/storage/bootstrap.py`
2. `packages/storage/db.py`
3. `tests/test_storage_bootstrap.py`
4. `apps/api/main.py`
5. `apps/worker/main.py`
6. `scripts/local_bootstrap.py`

## 7. 动手任务

1. 画出 API 启动时的存储准备顺序图。
2. 画出 Worker 启动时的存储准备顺序图。
3. 解释为什么旧数据库需要先 stamp 再 upgrade。
4. 记录当前测试期望的 Alembic head revision：`20260414_0012_schema_reconciliation`。

## 8. 验收标准

- 你能解释四个 bootstrap 函数的边界。
- 你能说明为什么 API 需要恢复 tracker。
- 你能描述“服务启动”和“存储准备”之间的关系。
- 你能解释这轮 Alembic 化改造解决了什么问题。

## 9. 常见误区

- 误区一：觉得 bootstrap 只是样板代码。
- 误区二：认为数据库迁移只需要手动跑一次。
- 误区三：忽视运行时状态恢复与数据库准备的区别。
