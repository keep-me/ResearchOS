# 第 12 课：SQLAlchemy 与 Repository

## 1. 本课定位

这一课的目标是搞清楚数据层分层方式。你不需要立刻精通 SQLAlchemy，但必须知道模型、session、repository、facade 在这个仓库里分别扮演什么角色。最近这轮修订里，`db.py` 被进一步收窄职责，很多行为明确落回 repository 和 bootstrap。

## 2. 学完你应该能回答的问题

- 为什么项目没有直接在 router 里写所有数据库操作。
- `Base`、`engine`、`SessionLocal`、`session_scope()` 各自的职责是什么。
- repository 和 facade 分别适合解决什么问题。
- 如何从一个模型一路追到实际读写逻辑。
- 为什么一些“看似业务细节”的正确性，其实是 repository 的职责。

## 3. 学习前准备

- 阅读 `packages/storage/db.py`。
- 阅读 `packages/storage/models.py` 中模型列表。
- 浏览 `packages/storage/repositories.py`、`packages/storage/paper_repository.py`、`packages/storage/project_repository.py` 与 `packages/storage/repository_facades.py`。

## 4. 详细讲解

### 4.1 `db.py` 现在只解决存储基础设施问题

`db.py` 里的关键词要形成牢固印象：

- `Base`：声明式模型基类。
- `engine`：数据库连接引擎。
- `SessionLocal`：session 工厂。
- `session_scope()`：统一事务边界。

这里的重点不是记 API，而是理解：业务代码不应该自己到处 new 连接和 commit/rollback。基础设施层已经提供了统一入口，而且 schema 迁移逻辑已经被移出这里。

### 4.2 `session_scope()` 为什么是关键接口

它负责：

- 打开 session。
- 正常时 commit。
- 异常时 rollback。
- 最后关闭 session。

这让上层业务可以更专注在“我要做什么”，而不是“事务该怎么收尾”。这是典型的基础设施抽象。

### 4.3 模型文件告诉你系统有哪些一级对象

`packages/storage/models.py` 里的类名很值得逐个扫一遍。当前尤其值得重点分组：

- 论文主线：`Paper`、`AnalysisReport`、`Citation`、`PipelineRun`
- 收集与入库：`CollectionAction`、`ActionPaper`、`TopicSubscription`、`PaperTopic`
- 项目主线：`Project`、`ProjectRepo`、`ProjectIdea`、`ProjectResearchWikiNode`、`ProjectResearchWikiEdge`、`ProjectRun`、`ProjectRunAction`
- Agent 主线：`AgentProject`、`AgentSession`、`AgentSessionMessage`、`AgentSessionPart`、`AgentSessionTodo`
- 权限与待办：`AgentPermissionRuleSet`、`AgentPendingAction`

只看类名，你就已经能感受到这个系统的业务跨度。

### 4.4 repository 不只是“把 SQL 藏起来”

repository 的目的不是“显得高级”，而是：

- 把数据库查询和业务层隔开。
- 复用常见查询。
- 保持事务边界清晰。
- 把和数据正确性强相关的规则集中起来。

例如 `PaperRepository` 里已经承载了不少真实业务规则：

- `folder_stats()` 会按用户时区聚合日期。
- `_rank_embedding_candidates()` 不再只扫最近 500 条，而是按 embedding 相似度在候选集中排序。
- `link_to_topic()`、`unlink_from_topic()` 负责维持关联关系。

这说明 repository 是“数据行为层”，不是简单 DAO。

### 4.5 facade 和聚合仓储的使用场景

facade 一般出现在“一个业务动作需要组合多个 repository”的场景。例如论文详情页同时需要 paper、analysis、topic 映射时，直接在 router 里手拼会很乱。Facade 的作用是：

- 给接口层一个更高层的读写入口。
- 把多仓储协同打包起来。
- 降低上层拿一堆 repository 手工拼的复杂度。

所以 repository 更像单一领域访问层，facade 更像跨对象组合层。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 同样把研究对象、助手状态和本地能力组织成正式结构。对照它看 `packages/storage/models.py`，你会更容易意识到：优秀科研系统的核心不是页面，而是资产模型和状态模型。

## 6. 代码精读顺序

1. `packages/storage/db.py`
2. `packages/storage/models.py`
3. `packages/storage/paper_repository.py`
4. `packages/storage/project_repository.py`
5. `packages/storage/repositories.py`
6. `packages/storage/repository_facades.py`
7. 找一个 router 看它如何打开 `session_scope()`

## 7. 动手任务

1. 从 `models.py` 中挑 12 个类，按业务域分组。
2. 追踪一个实体从模型到 repository 的路径。
3. 解释为什么 `folder_stats()` 和 semantic candidate ranking 应该放在 repository，而不是 router。

## 8. 验收标准

- 你能解释 `db.py` 中主要对象的职责。
- 你能说明 repository 分层带来的实际好处。
- 你能根据模型类名大致说出系统对象版图。
- 你能举出至少两个 repository 承担业务正确性规则的例子。

## 9. 常见误区

- 误区一：以为 ORM 学习等于背 SQLAlchemy 语法。
- 误区二：把 repository 视为多余包装。
- 误区三：只看表结构，不看对象之间如何支持产品流程。
