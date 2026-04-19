# 第 25 课：Project Workflow 与 ARIS

## 1. 本课定位

这节课是整个课程的第二个高峰。到这里，`ResearchOS` 已经不再只是论文研究助手，而是在尝试把项目执行、实验运行、自动评审、论文写作等流程组织成真正的工作流系统。最近这轮修订里，Project 相关的数据模型和迁移也更完整了，尤其是 project research wiki 这条线。

## 2. 学完你应该能回答的问题

- 为什么 `Project Workflow` 是项目最平台化的一部分。
- `workflow_runner.py` 为什么会如此庞大。
- `execution_service.py` 和 `projects.py` 各自在工作流链路里扮演什么角色。
- 为什么 ARIS 链路本质上是“研究执行系统”而不是“聊天增强”。
- 为什么 `ProjectResearchWikiNode` / `ProjectResearchWikiEdge` 说明工作流结果正在沉淀成长期资产。

## 3. 学习前准备

- 阅读 `packages/ai/project/workflow_runner.py` 开头与关键函数列表。
- 阅读 `packages/ai/project/execution_service.py`。
- 阅读 `apps/api/routers/projects.py`。
- 浏览 `packages/storage/models.py` 里的 `Project*` 相关模型。
- 浏览 `tests/test_project_workflow_runner.py` 的测试名列表。

## 4. 详细讲解

### 4.1 先从心理预期上升级

当你看到 `workflow_runner.py` 几千行时，第一反应不该是“代码怎么这么大”，而应先意识到它在试图做的事情：

- 文献综述。
- idea discovery。
- novelty check。
- research review。
- run experiment。
- experiment audit。
- auto review loop。
- paper writing。
- rebuttal。
- full pipeline。

这已经是一个研究执行编排系统，而不只是业务函数集合。

### 4.2 `WorkflowContext` 说明工作流需要自己的上下文模型

工作流和普通单次请求不同，它需要长期状态：

- 当前 run。
- 所属 project。
- metadata。
- 选中的论文。
- 选中的仓库。
- 分阶段 orchestration 与 stage trace。

这就是为什么会出现 `WorkflowContext`、`RunSnapshot`、`ProjectSnapshot`、`PaperSnapshot`、`RepoSnapshot` 这样的数据类。它们说明工作流运行需要自己的上下文快照层。

### 4.3 Project 数据模型已经形成“执行资产层”

当前 `packages/storage/models.py` 里，Project 域已经不是单表：

- `Project`
- `ProjectRepo`
- `ProjectIdea`
- `ProjectPaper`
- `ProjectRun`
- `ProjectRunAction`
- `ProjectGpuLease`
- `ProjectResearchWikiNode`
- `ProjectResearchWikiEdge`

尤其是 research wiki 这条线很重要，因为它意味着工作流结果不只是写日志，而是在沉淀成可搜索、可连边、可长期演进的项目知识资产。

### 4.4 `execution_service.py` 与 `workflow_runner.py` 的分工

你可以这样理解：

- `execution_service.py` 更像提交和能力判断入口。
- `workflow_runner.py` 更像真正的执行编排核心。

这种分工能避免所有入口直接拥挤进超大编排文件，也方便上层先判断“这个 workflow 是否支持、何时提交”。

### 4.5 为什么测试文件会非常重要

`tests/test_project_workflow_runner.py` 的测试名是极好的学习入口。你可以看到它覆盖了：

- stage checkpoint 恢复。
- workspace artifacts 生成。
- compile command 自动检测。
- reviewer workspace agent。
- remote run。
- GPU lease 避让。
- screen session 启动。
- full pipeline 续跑。

这些名字直接告诉你：工作流不是“跑一遍 prompt”，而是和工作区、远程机、状态恢复、产物落盘深度耦合。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 让你看到项目、助手界面和本地工作台如何统一组织。对照它可以帮助你理解：`ResearchOS` 的 Project Workflow 最终为什么会落到 Project、Repo、Idea、Run 这些正式对象上，而不是只存在于聊天记录里。

## 6. 代码精读顺序

1. `packages/ai/project/execution_service.py`
2. `packages/ai/project/workflow_runner.py` 前 300 行和关键函数列表
3. `packages/storage/models.py` 中全部 `Project*` 相关模型
4. `apps/api/routers/projects.py`
5. `tests/test_project_workflow_runner.py`
6. `packages/ai/research/research_wiki_service.py`

## 7. 动手任务

1. 列出当前支持的 workflow 类型。
2. 按“输入准备、执行、恢复、产物、审查”给 workflow 能力分组。
3. 从测试名里总结工作流系统最关注的 8 个稳定性问题。
4. 解释为什么 project research wiki 的存在说明工作流结果正在被资产化。

## 8. 验收标准

- 你能解释为什么 `workflow_runner.py` 会如此复杂。
- 你能把 Project Workflow 理解成正式执行系统。
- 你能说明 ARIS 链路为什么超出了普通聊天 Agent 的范畴。
- 你能说清楚 Project 相关数据模型各自在沉淀什么资产。

## 9. 常见误区

- 误区一：把 workflow 视为长 prompt。
- 误区二：低估状态恢复和产物落盘的重要性。
- 误区三：只看接口表面，不看底层工作区和执行依赖。
