# 15 Worker 调度时间线

## 覆盖模块

- `apps/worker/main.py`
- `packages/ai/ops/daily_runner.py`
- `packages/ai/ops/idle_processor.py`
- `packages/storage/bootstrap.py`

## 图

```mermaid
flowchart TD
  Start["run_worker"] --> Lock[单实例锁 worker.lock]
  Lock --> Bootstrap["bootstrap_worker_runtime"]
  Bootstrap --> Scheduler[BlockingScheduler UTC]

  Scheduler --> H1[每整点\ntopic_dispatch_job]
  Scheduler --> H2[每日\nbrief_job]
  Scheduler --> H3[每周\nweekly_graph_job]
  Scheduler --> H4[全天\nidle_processor]

  H1 --> Topic["TopicRepository.list_topics"]
  Topic --> Ingest["run_topic_ingest"]
  Ingest --> PaperPipes[PaperPipelines]
  PaperPipes --> Heartbeat[写 worker_heartbeat]

  H2 --> Brief["run_daily_brief"]
  Brief --> Heartbeat

  H3 --> Graph["run_weekly_graph_maintenance"]
  Graph --> Heartbeat

  H4 --> Idle[IdleDetector\nCPU / Mem / request rate]
  Idle --> Batch[批量处理未读论文]
  Batch --> Heartbeat
```

## 阅读提示

- Worker 不是只有“定时任务”，还有单实例锁、心跳、重试和闲时处理。
- 如果你在追“为什么某些论文会被自动处理”，从这张图入手最快。
