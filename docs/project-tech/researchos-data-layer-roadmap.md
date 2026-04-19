# ResearchOS 数据层路线

日期：2026-04-15

## JSON Schema Version

主要 metadata JSON 统一使用 `schema_version` 字段。本轮新增 `packages/storage/json_schema.py`，并在 paper、project run、run action、task metadata/log/artifact refs 写入路径补默认版本。

## Embedding 迁移路线

当前 `papers.embedding` 使用 JSON，适合 SQLite 和本地开发，但不适合大规模相似度搜索。

推荐路线：

1. 保留 `papers.embedding` 作为兼容读路径。
2. 新建 `paper_embeddings` 表或 pgvector column，字段包含 `paper_id`、`provider`、`model`、`dimensions`、`embedding_version`、`vector`。
3. 写入新 embedding 时双写 JSON 与新表。
4. 相似度查询优先走 pgvector，失败时回退 JSON cosine。
5. 迁移完成后将 JSON embedding 降级为缓存或删除。

## Task Logs

当前 `TaskRecord.logs_json` 仍保留兼容。本轮新增 `tracker_task_logs` sidecar 表，`TaskRepository.upsert_task()` 会把 logs 同步写入追加式行模型，便于后续分页查询和按 level/time 过滤。

## Agent Session Part

本轮为 `agent_session_parts` 增加 `session_id + part_type + created_at` 组合索引，覆盖按 session 读取 tool/text/reasoning/patch 的常见路径。下一步可增加 `agent_session_events` 追加表，把 SSE 原始事件与 MessageV2 part 派生结果分离。

