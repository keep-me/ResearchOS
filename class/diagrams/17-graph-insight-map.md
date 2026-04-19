# 17 图谱洞察图

## 覆盖模块

- `packages/ai/research/graph_service.py`
- `packages/integrations/citation_provider.py`
- `packages/integrations/semantic_scholar_client.py`
- `packages/storage/repositories.py`
- `apps/api/routers/graph.py`

## 图

```mermaid
flowchart LR
  Papers[Paper / Citation / Topic 数据] --> GraphService[GraphService]
  GraphService --> CitationProvider[CitationProvider\nOpenAlex + Semantic Scholar]
  GraphService --> LLM[LLMClient]
  GraphService --> Cache[citation-detail cache]

  CitationProvider --> Rich[rich citations\nreferences + cites]
  Papers --> LocalGraph[本地 citation edges]
  Rich --> Merge[合并本地与外部引文信息]
  LocalGraph --> Merge

  Merge --> InsightA[引用树 / 时间线]
  Merge --> InsightB[bridging papers / frontier]
  Merge --> InsightC[research gaps / evolution]
  Merge --> InsightD[survey / wiki context]

  InsightA --> Router[graph router]
  InsightB --> Router
  InsightC --> Router
  InsightD --> Router
```

## 阅读提示

- 图谱能力不是“把论文连起来就完了”，它还要拼本地数据、外部引文源和 LLM 分析。
- `GraphService` 本质上是“检索 + 关系整理 + 解释生成”的混合层。
