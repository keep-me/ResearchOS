# 16 RAG 证据链图

## 覆盖模块

- `packages/ai/research/rag_service.py`
- `packages/storage/paper_repository.py`
- `packages/storage/repositories.py`
- `packages/ai/research/cost_guard.py`
- `packages/integrations/llm_client.py`

## 图

```mermaid
flowchart TD
  Q[用户问题] --> Lex[full_text_candidates]
  Q --> Emb[LLM.embed_text]
  Emb --> Sem[semantic_candidates]
  Lex --> Merge[合并候选并去重]
  Sem --> Merge

  Merge --> TopK[截取 top_k papers]
  TopK --> Ctx[AnalysisRepository.contexts_for_papers]
  TopK --> Evidence[构造 evidence snippet]
  Ctx --> Prompt[build_rag_prompt]
  Evidence --> Prompt

  Prompt --> Guard[CostGuardService.choose_model]
  Guard --> LLM[LLM.complete_json]
  LLM --> Trace[PromptTraceRepository.create]
  LLM --> Resp[AskResponse\nanswer + cited_paper_ids + evidence]
```

## 阅读提示

- 这张图里的重点不是“检索”本身，而是“证据怎么被拼到回答里”。
- `semantic_candidates()` 最近修过一次候选排序回归，读这张图时可以顺手看相关测试。
