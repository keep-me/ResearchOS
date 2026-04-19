# 14 存储 Bootstrap 图

## 覆盖模块

- `packages/storage/bootstrap.py`
- `packages/storage/db.py`
- `tests/test_storage_bootstrap.py`
- `scripts/local_bootstrap.py`
- `apps/api/main.py`
- `apps/worker/main.py`

## 图

```mermaid
flowchart TD
  EntryA[scripts/local_bootstrap.py] --> Local[bootstrap_local_runtime]
  EntryB[API startup] --> ApiBoot[bootstrap_api_runtime]
  EntryC[Worker start] --> WorkerBoot[bootstrap_worker_runtime]

  Local --> Storage["bootstrap_storage"]
  ApiBoot --> Storage
  WorkerBoot --> Storage

  Storage --> Inspect["_user_tables"]
  Storage --> Rev["_current_revision"]
  Rev --> Decision{有用户表但无 alembic_version?}
  Decision -->|yes| Stamp[stamp 20260412_0011_add_project_research_wiki]
  Decision -->|no| Upgrade
  Stamp --> Upgrade[upgrade head]
  Upgrade --> Head[20260414_0012_schema_reconciliation]
  Head --> Backfill["_ensure_initial_import_action"]
  ApiBoot --> Tracker["global_tracker.bootstrap_from_store"]

  ImportOnly[仅 import packages.storage.db] -.-> NoSchema[不会自动建表]
```

## 阅读提示

- 这张图回答的是“为什么现在必须显式 bootstrap，而不是导入 ORM 就算完成”。
- `tests/test_storage_bootstrap.py` 基本把整条链路的关键点都锁住了。
