# 第 14 课：论文输入链路

## 1. 本课定位

这一课回答一个非常实际的问题：论文到底是怎么进系统的。研究平台的价值，很大程度上取决于输入端是否持续、是否稳定、是否能做增量处理。最近这轮代码修订里，手动 PDF 上传和替换 PDF 也被正式收口成输入链路的一部分。

## 2. 学完你应该能回答的问题

- `ArxivClient`、`OpenAlexClient`、`SemanticScholarClient` 分别负责什么。
- 为什么 Topic 订阅、外部元数据接入、手动 PDF 上传是三种不同入口。
- 为什么当前上传逻辑要从 router 下沉到 `paper_ops_service.py`。
- 为什么优秀科研产品更强调“持续输入”而不是“临时搜索”。

## 3. 学习前准备

- 阅读 `packages/integrations/arxiv_client.py`。
- 阅读 `packages/integrations/openalex_client.py`。
- 阅读 `packages/integrations/semantic_scholar_client.py`。
- 阅读 `packages/ai/paper/paper_ops_service.py` 中上传相关函数。
- 回看 `apps/worker/main.py` 中 topic dispatch 的逻辑。

## 4. 详细讲解

### 4.1 输入链路不是单一 API 调用

论文进入系统，当前至少有三种入口：

- 由 Topic 驱动的自动抓取。
- 人工触发搜索或导入。
- 手动上传或替换本地 PDF。

所以“输入链路”是流程，不是单函数。

### 4.2 `ArxivClient` 是主输入源，`OpenAlex` 与 `Semantic Scholar` 是增强源

`ArxivClient` 主要承担：

- 按 query 抓取论文。
- 支持排序和日期窗口。
- 下载 PDF。

而 `OpenAlex` 与 `Semantic Scholar` 更多承担：

- 引文数据增强。
- 外部元数据补充。
- 影响力和引用网络信息补全。

也就是说，`arXiv` 更偏原始论文输入，后两者更偏知识增强与补全。

### 4.3 手动 PDF 上传已经被提升为正式输入入口

这轮代码修订里，`packages/ai/paper/paper_ops_service.py` 新增了：

- `PaperUploadValidationError`
- `PaperUploadNotFoundError`
- `upload_paper_pdf()`
- `replace_paper_pdf()`

这条链路做的事情很具体：

- 校验上传内容必须是 PDF。
- 把文件落到 `pdf_storage_root/uploads` 或 `pdf_storage_root/manual`。
- 尝试从 PDF 中提取标题和摘要。
- 如有必要创建 paper，或更新已有 paper。
- 设置 `pdf_path`。
- 在提供 `topic_id` 时，把论文挂到文件夹型 topic 下。

这说明“手动导入”已经不是临时补丁，而是正式能力。

### 4.4 为什么上传逻辑要从 router 下沉到 service

当前 `apps/api/routers/papers.py` 的上传和替换接口已经改成：

- 用 `asyncio.to_thread(...)` 调用 service 层实现。
- 把 `PaperUploadNotFoundError` 映射到 404。
- 把 `PaperUploadValidationError` 映射到 400。
- 成功后统一失效 `folder_stats` 和图谱缓存。

这样做的价值是：

- router 负责协议层和错误映射。
- service 负责文件处理、元数据提取、数据库更新。
- 输入链路更容易测试和复用。

### 4.5 Topic 调度仍然是持续输入的主干

`apps/worker/main.py` 的 `topic_dispatch_job()` 告诉你，系统不是等用户想到才抓：

- 每整点检查主题调度规则。
- 根据频率和时间决定是否执行。
- 自动触发 `run_topic_ingest`。

这就是“持续输入”思维。手动上传只是补充入口，不会替代持续输入机制。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 更强调本地研究资产沉淀。对照它看当前仓库的输入链路，你会更容易理解：抓取论文不是终点，真正重要的是后续的分析、沉淀和进入项目流程。

## 6. 代码精读顺序

1. `packages/integrations/arxiv_client.py`
2. `packages/integrations/openalex_client.py`
3. `packages/integrations/semantic_scholar_client.py`
4. `packages/ai/paper/paper_ops_service.py`
5. `apps/api/routers/papers.py`
6. `apps/worker/main.py` 中 `topic_dispatch_job`

## 7. 动手任务

1. 画出 Topic 驱动自动抓取的流程图。
2. 比较三个外部来源在系统中的定位。
3. 画出“手动上传 PDF -> service -> repository -> cache invalidation”的流程图。
4. 说明如果你要新增一个论文来源，最可能涉及哪些层次。

## 8. 验收标准

- 你能区分主输入源、增强型来源和手动输入入口。
- 你能解释持续输入对科研产品的重要性。
- 你能把 Topic 调度、上传 service 和后续处理链路连接起来理解。

## 9. 常见误区

- 误区一：把输入链路理解成单个搜索接口。
- 误区二：只盯住 arXiv，忽视元数据增强来源的价值。
- 误区三：觉得抓到论文或上传 PDF 就已经完成了输入设计。
