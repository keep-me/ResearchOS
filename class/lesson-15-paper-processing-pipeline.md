# 第 15 课：论文处理流水线

## 1. 本课定位

如果说上一课解决的是“论文怎么进来”，这一课解决的是“论文进来以后发生什么”。这正是 `ResearchOS` 区别于普通论文抓取器的核心：它会继续对论文做结构化处理。最近这轮修订也让输入链路和处理链路的边界更清楚了。

## 2. 学完你应该能回答的问题

- `PaperPipelines` 在项目中的地位是什么。
- 粗读、深读、PDF 解析、证据抽取、引用导入之间是什么关系。
- 为什么上传 PDF 的逻辑要从处理流水线里拆出去。
- 为什么处理流水线需要 `PipelineRun` 记录。
- 为什么论文处理既有同步步骤，也有后台异步补全步骤。

## 3. 学习前准备

- 阅读 `packages/ai/paper/pipelines.py`。
- 浏览 `packages/ai/paper/pdf_parser.py`、`paper_analysis_service.py`、`paper_evidence.py`。
- 阅读 `packages/ai/paper/paper_ops_service.py` 中上传相关函数。
- 回看 `packages/storage/models.py` 中 `PipelineRun`。

## 4. 详细讲解

### 4.1 先建立一个边界：输入归输入，处理归处理

这轮代码修订之后，一个很好的学习切口是看清边界：

- `paper_ops_service.py` 负责上传、替换 PDF、落盘、初始元数据提取。
- `PaperPipelines` 负责论文进入系统后的进一步处理和编排。

这意味着流水线现在更像“对规范化 paper 资产做加工”，而不是一边接收文件一边做所有事情。

### 4.2 `PaperPipelines` 是论文业务的编排器

从 `pipelines.py` 里可以看出，`PaperPipelines` 不是一个单功能类，而是把多个处理动作组织到一起：

- 摄入和保存论文。
- 粗读。
- 深读。
- PDF 文本抽取。
- 嵌入。
- 引用导入。
- 与图谱能力联动。

这说明论文处理不是散函数，而是正式业务主线。

### 4.3 为什么需要 `PipelineRun`

处理流水线一定会涉及：

- 开始时间。
- 结束时间。
- 失败原因。
- 决策说明。
- 执行耗时。

`PipelineRun` 的存在让系统不只是“做过就做过”，而是能追踪历史、排查失败、理解成本和产出。这是从玩具脚本迈向平台的关键一步。

### 4.4 粗读、深读、证据、图像分析是分层能力

不要把它们简单理解成“同一个 prompt 长一点短一点”。

- 粗读更偏快速筛选、价值判断、基本摘要。
- 深读更偏结构化理解、细节推理、重点剖析。
- PDF 解析更偏为后续分析提供正文材料。
- `paper_evidence` 更偏证据片段组织。
- figure/image 相关模块更偏视觉层补充。

这意味着系统在处理论文时，实际上在做层级化分析，而不是一股脑把最贵的分析全跑一遍。

### 4.5 为什么会有后台自动补链动作

`_bg_auto_link()` 说明论文入库后还可能触发后台引用自动关联。这揭示出一个很重要的工程模式：

- 主流程先完成核心任务。
- 辅助增强任务在后台补上。

这样可以兼顾响应速度和能力完整度。未来你读更多模块时，也要留意这种“主流程 + 后台补全”的模式。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 更强调阅读卡、助手界面和本地资产组织。对照它可以帮助你理解：论文处理不是为了生成一次回答，而是为了支持后续长期使用、进一步沉淀和进入项目上下文。

## 6. 代码精读顺序

1. `packages/ai/paper/pipelines.py`
2. `packages/ai/paper/pdf_parser.py`
3. `packages/ai/paper/paper_evidence.py`
4. `packages/ai/paper/paper_analysis_service.py`
5. `packages/ai/paper/paper_ops_service.py`
6. `packages/storage/models.py` 中 `PipelineRun`

## 7. 动手任务

1. 画出一篇论文从入库到粗读、深读、证据准备、引用自动关联的大致流程图。
2. 说明粗读和深读各自更适合回答什么问题。
3. 总结为什么上传 service 和处理 pipeline 应该分层。
4. 总结为什么流水线执行历史必须被持久化。

## 8. 验收标准

- 你能解释 `PaperPipelines` 的编排角色。
- 你能区分不同处理步骤的层次。
- 你能说明 `PipelineRun` 对排错和可观测性的价值。
- 你能解释为什么最近把上传逻辑拆到 service 层是合理的。

## 9. 常见误区

- 误区一：把论文处理理解成一次模型调用。
- 误区二：认为粗读和深读只是提示词强弱区别。
- 误区三：忽视后台补全与主流程解耦的工程价值。
