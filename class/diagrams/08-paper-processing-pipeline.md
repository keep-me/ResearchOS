# 08 论文处理流水线图

## 覆盖模块

- `packages/ai/paper/paper_ops_service.py`
- `packages/ai/paper/pipelines.py`
- `packages/ai/paper/pdf_parser.py`
- `packages/ai/paper/paper_analysis_service.py`
- `packages/ai/paper/paper_evidence.py`
- `apps/api/routers/papers.py`
- `packages/storage/paper_repository.py`

## 图

```mermaid
flowchart TD
  InA[Topic 自动抓取\narXiv / OpenAlex / Semantic Scholar]
  InB[手动搜索 / 导入]
  InC[上传 PDF / 替换 PDF\npaper_ops_service]

  InA --> P0[Paper 规范化资产\nPaper + metadata + pdf_path]
  InB --> P0
  InC --> P0

  P0 --> P1[embed_paper\n生成 embedding]
  P0 --> P2[skim\n粗读摘要与相关性]
  P0 --> P3[ensure / parse PDF\nPdfTextExtractor]
  P0 --> P4[extract figures\n图像/图表材料]

  P2 --> P5[deep_dive\n结构化深读]
  P3 --> P6[paper_evidence\n证据片段组织]
  P4 --> P7[figure/image analysis]
  P0 --> P8[_bg_auto_link\n后台引用自动关联]

  P1 --> OutA[semantic retrieval / similar papers]
  P5 --> OutB[AnalysisReport / metadata_json]
  P6 --> OutC[RAG evidence]
  P7 --> OutD[PaperDetail figures]
  P8 --> OutE[Citation graph]

  OutA --> Apps[Graph / RAG / Wiki / Brief / Projects]
  OutB --> Apps
  OutC --> Apps
  OutD --> Apps
  OutE --> Apps
```

## 阅读提示

- 当前代码已经把“文件接收与落盘”和“后续分析流水线”分开了。
- `paper_ops_service.py` 负责把 paper 资产规范化，`pipelines.py` 负责继续加工。
