# 第 13 课：Topic 与 Paper 核心对象

## 1. 本课定位

这一课进入最核心的业务对象。无论是抓论文、读论文、做图谱、写日报还是跑项目，最后都离不开 Topic 和 Paper 两类对象。你必须先把这两个对象看清楚，后续流程才不会漂浮。

## 2. 学完你应该能回答的问题

- Topic 和 Paper 为什么是系统最基础的两个对象。
- 哪些对象是围绕它们生长出来的。
- `PipelineRun`、`Citation`、`GeneratedContent` 与它们是什么关系。
- 为什么后来又会长出 `Project` 系列对象。

## 3. 学习前准备

- 在 `packages/storage/models.py` 中重点看 `Paper`、`TopicSubscription`、`PaperTopic`、`PipelineRun`、`Citation`。
- 浏览 `packages/storage/topic_repository.py` 和 `packages/storage/paper_repository.py`。

## 4. 详细讲解

### 4.1 Topic 是“输入策略对象”，不只是关键词

很多人第一次看到 Topic，会把它理解成搜索词。但在这个项目里，Topic 更像“订阅和抓取策略对象”：

- 它定义你关心的研究方向。
- 它可以带有抓取频率和触发时间。
- 它决定后续自动化输入链路的来源。

所以 Topic 不只是内容标签，它还是调度和持续输入的起点。

### 4.2 Paper 是“研究资产对象”，不只是搜索结果

Paper 在系统中不是一次性返回给你的结果条目，而是长期资产：

- 有元数据
- 有状态
- 有 PDF 路径
- 有分析报告
- 有嵌入
- 有引用关系
- 有所属主题
- 可能进入项目工作流

一旦你这样理解 Paper，后面很多设计都会自然得多。

### 4.3 `PaperTopic` 说明对象关系不是一对一

论文和主题之间显然不是简单单向关系：

- 一个主题可以包含多篇论文。
- 一篇论文也可以被多个主题关注。

`PaperTopic` 这种中间关系对象体现的是现实研究活动，而不是简化后的教科书模型。

### 4.4 `PipelineRun`、`AnalysisReport`、`Citation` 为什么重要

这些对象说明系统不只是“保存论文”，还记录论文被处理后的结果：

- `PipelineRun`：记录处理链路执行历史。
- `AnalysisReport`：保存 AI 分析产物。
- `Citation`：让论文之间形成网络。

这也是为什么项目后来能做图谱、日报、RAG，而不是只停留在论文列表页。

### 4.5 为什么后来要引入 `Project`

当你对 `Paper` 的理解足够深，就会自然明白为什么 `Project` 系列对象会出现：

- 论文不会永远停留在阅读阶段。
- 研究者会把论文拉进项目。
- 项目会关联代码仓库、想法、运行记录、报告。

从这个角度看，`Project` 不是和 `Paper` 平级竞争，而是论文资产继续沉淀后的上层组织方式。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 会把论文、助手、项目相关对象都放在同一工作台语境里。对照它可以帮助你理解：为什么 `ResearchOS` 里 Topic 和 Paper 只是起点，后面还会长出 Project、Run、Session 等对象。

## 6. 代码精读顺序

1. `packages/storage/models.py` 中 `Paper`
2. `packages/storage/models.py` 中 `TopicSubscription`、`PaperTopic`
3. `packages/storage/models.py` 中 `PipelineRun`、`Citation`、`GeneratedContent`
4. `packages/storage/paper_repository.py`
5. `packages/storage/topic_repository.py`

## 7. 动手任务

1. 画出 Topic、Paper、PipelineRun、Citation、GeneratedContent 的关系图。
2. 用一句话定义每个对象的职责。
3. 思考：如果没有 `PipelineRun`，系统会缺少什么能力。

## 8. 验收标准

- 你能解释 Topic 和 Paper 分别是什么层次的对象。
- 你能说清楚哪些对象是围绕 Paper 生长出来的。
- 你能理解为什么项目后期会扩展到 Project 模型。

## 9. 常见误区

- 误区一：把 Topic 当成纯标签。
- 误区二：把 Paper 当成一次性搜索结果。
- 误区三：忽视“处理历史”和“知识产物”也是核心数据。
