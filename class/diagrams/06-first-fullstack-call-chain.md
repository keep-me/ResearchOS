# 06 第一条前后端调用链时序图

## 覆盖模块

- `frontend/src/pages/Papers.tsx`
- `frontend/src/services/api.ts`
- `apps/api/main.py`
- `apps/api/routers/papers.py`
- `packages/storage/db.py`
- `packages/storage/paper_repository.py`

## 图

```mermaid
sequenceDiagram
  actor User as 用户
  participant Page as Papers.tsx
  participant Api as paperApi
  participant MW as AuthMiddleware
  participant Router as /papers/folder-stats + /papers/latest
  participant Repo as PaperRepository
  participant DB as SQLite

  User->>Page: 打开论文库
  Page->>Api: folderStats()
  Api->>MW: GET /papers/folder-stats
  MW->>Router: 认证通过
  Router->>Repo: repo.folder_stats()
  Repo->>DB: 聚合 topic / status / by_date
  DB-->>Repo: 结果集
  Repo-->>Router: 统计结果
  Router-->>Api: JSON
  Api-->>Page: 渲染侧栏统计

  Page->>Api: latest(filters)
  Api->>MW: GET /papers/latest
  MW->>Router: 认证通过
  Router->>Repo: list_paginated(...)
  Repo->>DB: 查询 papers + count
  DB-->>Repo: rows
  Repo-->>Router: papers, total
  Router-->>Api: JSON
  Api-->>Page: 渲染列表
```

## 阅读提示

- 这条链路适合作为第一次“页面 -> 服务 -> 路由 -> repository -> 数据库”的完整练习。
- 如果要看上传链路，请直接看 `08` 和 `14`。
