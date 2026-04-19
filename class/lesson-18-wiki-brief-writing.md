# 第 18 课：Wiki、日报与写作助手

## 1. 本课定位

这一课讨论的是“知识产出层”。论文被收集、分析之后，如果不能稳定转化成用户可复用的内容，那系统价值就会大幅下降。`ResearchOS` 在这方面已经形成了三条很清晰的产出线：Wiki、Daily Brief、Writing。

## 2. 学完你应该能回答的问题

- `ResearchWikiService`、`DailyBriefService`、`WritingService` 各自负责什么。
- 为什么它们都属于知识生产层，但目标用户和使用场景不同。
- 为什么写作辅助不是“顺手加一个 prompt”，而是正式模块。
- 这些能力如何依赖前面的论文处理和证据准备。

## 3. 学习前准备

- 阅读 `packages/ai/research/research_wiki_service.py`。
- 阅读 `packages/ai/research/brief_service.py`。
- 阅读 `packages/ai/research/writing_service.py`。

## 4. 详细讲解

### 4.1 三类产出对应三种用户需求

你可以把它们这样区分：

- Wiki：帮助用户系统理解某个主题或项目知识结构。
- Daily Brief：帮助用户快速消费最新动态。
- Writing：帮助用户把已有研究内容表达出来。

这三类需求都围绕论文，但对应的时间尺度和输出形态不同。

### 4.2 `ResearchWikiService` 更像知识组织器

从函数名和模型关系可以看出，这个服务不仅是在生成一篇长文，还在处理：

- 节点与边
- 论文分析摘要
- 想法节点
- 查询评分

这说明 Wiki 不只是“写一段综述”，而是在组织知识结构。这个特点非常接近知识图谱和项目沉淀之间的中间层。

### 4.3 `DailyBriefService` 解决的是节奏问题

日报的本质不是“再生成一个摘要”，而是帮助用户用固定节奏消费系统最新产出：

- 今天哪些论文值得注意
- 哪些分析结果已完成
- 是否需要邮件通知

这代表研究系统不再只是在用户主动访问时提供能力，而是开始按时间节奏主动输出。

### 4.4 `WritingService` 体现了“研究成果表达也是一条主线”

`WritingService` 里有很多构建函数，如：

- 中译英、英译中
- 中英文润色
- 压缩、扩展
- 逻辑检查
- 去 AI 味
- 图表标题、审稿视角

这说明项目把“研究完成后的表达阶段”也纳入了主线。对科研用户来说，这非常合理，因为阅读、理解、写作本来就是连续过程。

### 4.5 为什么这些模块都依赖前面的分析和证据层

如果前面的论文处理和证据准备做得不扎实，这里很多输出都会变虚：

- Wiki 会泛泛而谈
- Daily Brief 会只有标题摘要
- Writing 会缺乏真实研究上下文

所以知识生产层不是独立魔法，而是建立在前面所有层次的成果之上。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 的阅读卡和工作台沉淀视角提醒你：知识产出不应停留在一次性页面展示，而应服务于更长期的研究过程。对照它看当前仓库，会更容易理解 Wiki、Brief、Writing 为什么都是正式模块。

## 6. 代码精读顺序

1. `packages/ai/research/research_wiki_service.py`
2. `packages/ai/research/brief_service.py`
3. `packages/ai/research/writing_service.py`
4. 再回看 `packages/storage/models.py` 中 `GeneratedContent`

## 7. 动手任务

1. 比较 Wiki、Brief、Writing 的输入、输出和用户场景。
2. 解释为什么它们都属于知识生产层。
3. 从 `WritingService` 的函数列表中挑 5 个动作，思考它们对应的真实科研场景。

## 8. 验收标准

- 你能清楚区分三类知识产出服务。
- 你能说明它们和论文处理层的依赖关系。
- 你能解释为什么写作助手是正式能力而不是附属功能。

## 9. 常见误区

- 误区一：把 Wiki、日报、写作都当成“换个 prompt 的摘要”。
- 误区二：忽视它们服务的是不同时间尺度和不同任务。
- 误区三：不把知识产出视作研究工作流的正式阶段。
