# 数据链路详图与查询手册

本文基于 `packages/storage/models.py` 当前模型定义整理，覆盖四条链路：
- Paper 数据链
- Project 工作流链
- Agent 会话链
- 任务追踪链

## 1. Paper 数据链

### 1.1 Mermaid 图

```mermaid
erDiagram
    TOPIC_SUBSCRIPTIONS {
        string id PK
        string name UK
        string kind
        string query
        string source
        bool enabled
        string default_folder_id FK "self -> topic_subscriptions.id"
        datetime created_at
    }

    PAPERS {
        string id PK
        string arxiv_id UK
        string title
        date publication_date
        string read_status
        bool favorited
        datetime created_at
    }

    PAPER_TOPICS {
        string id PK
        string paper_id FK
        string topic_id FK
        datetime created_at
        string uq_paper_topic UK
    }

    ANALYSIS_REPORTS {
        string id PK
        string paper_id FK
        text summary_md
        text deep_dive_md
        datetime created_at
    }

    IMAGE_ANALYSES {
        string id PK
        string paper_id FK
        int page_number
        int image_index
        string image_type
        datetime created_at
    }

    PIPELINE_RUNS {
        string id PK
        string paper_id FK "nullable, SET NULL"
        string pipeline_name
        string status
        int retry_count
        datetime created_at
    }

    PROMPT_TRACES {
        string id PK
        string paper_id FK "nullable, SET NULL"
        string stage
        string provider
        string model
        float total_cost_usd
        datetime created_at
    }

    GENERATED_CONTENTS {
        string id PK
        string content_type
        string title
        string paper_id FK "nullable, SET NULL"
        datetime created_at
    }

    CITATIONS {
        string id PK
        string source_paper_id FK
        string target_paper_id FK
        text context
        string uq_citation_edge UK
    }

    COLLECTION_ACTIONS {
        string id PK
        string action_type
        string topic_id FK "nullable, SET NULL"
        int paper_count
        datetime created_at
    }

    ACTION_PAPERS {
        string id PK
        string action_id FK
        string paper_id FK
        string uq_action_paper UK
    }

    SOURCE_CHECKPOINTS {
        string id PK
        string source UK
        datetime last_fetch_at
        date last_published_date
    }

    TOPIC_SUBSCRIPTIONS ||--o{ PAPER_TOPICS : maps
    PAPERS ||--o{ PAPER_TOPICS : maps

    PAPERS ||--o{ ANALYSIS_REPORTS : has
    PAPERS ||--o{ IMAGE_ANALYSES : has
    PAPERS ||--o{ PIPELINE_RUNS : processed_by
    PAPERS ||--o{ PROMPT_TRACES : traced_by
    PAPERS ||--o{ GENERATED_CONTENTS : referenced_by

    PAPERS ||--o{ CITATIONS : source
    PAPERS ||--o{ CITATIONS : target

    TOPIC_SUBSCRIPTIONS ||--o{ COLLECTION_ACTIONS : triggered_by
    COLLECTION_ACTIONS ||--o{ ACTION_PAPERS : contains
    PAPERS ||--o{ ACTION_PAPERS : selected
```

### 1.2 字段解释（核心）

- `papers`: 论文主表，`arxiv_id` 全局唯一，`read_status/favorited` 支撑阅读状态。
- `paper_topics`: 论文与主题多对多中间表，唯一约束 `uq_paper_topic` 防重复绑定。
- `pipeline_runs`: 处理流水，记录某篇论文跑过哪些 pipeline、状态如何。
- `analysis_reports`: 论文分析正文沉淀（摘要、深读）。
- `image_analyses`: 图表/公式级别分析结果。
- `citations`: 引用边（source -> target），`uq_citation_edge` 防重复边。
- `generated_contents`: 派生内容（wiki/brief/report），可回链到论文。
- `collection_actions` + `action_papers`: 一次收集动作与被纳入论文的关系。

### 1.3 查询示例 SQL

```sql
-- Q1: 某个 topic 最近 30 天纳入的论文（含基础状态）
SELECT p.id, p.title, p.arxiv_id, p.read_status, p.created_at
FROM paper_topics pt
JOIN papers p ON p.id = pt.paper_id
WHERE pt.topic_id = :topic_id
  AND p.created_at >= CURRENT_TIMESTAMP - INTERVAL '30 day'
ORDER BY p.created_at DESC;

-- Q2: 每篇论文最新一次 pipeline 状态
SELECT pr.paper_id, pr.pipeline_name, pr.status, pr.updated_at
FROM pipeline_runs pr
JOIN (
  SELECT paper_id, pipeline_name, MAX(updated_at) AS max_updated
  FROM pipeline_runs
  WHERE paper_id IS NOT NULL
  GROUP BY paper_id, pipeline_name
) t
  ON pr.paper_id = t.paper_id
 AND pr.pipeline_name = t.pipeline_name
 AND pr.updated_at = t.max_updated;

-- Q3: 某篇论文的引用出边和入边数量
SELECT p.id,
       SUM(CASE WHEN c.source_paper_id = p.id THEN 1 ELSE 0 END) AS out_citations,
       SUM(CASE WHEN c.target_paper_id = p.id THEN 1 ELSE 0 END) AS in_citations
FROM papers p
LEFT JOIN citations c
  ON c.source_paper_id = p.id OR c.target_paper_id = p.id
WHERE p.id = :paper_id
GROUP BY p.id;
```

## 2. Project 工作流链

### 2.1 Mermaid 图

```mermaid
erDiagram
    PROJECTS {
        string id PK
        string name
        string workdir
        string workspace_server_id
        datetime created_at
    }

    PROJECT_REPOS {
        string id PK
        string project_id FK
        string repo_url
        string branch
    }

    PROJECT_IDEAS {
        string id PK
        string project_id FK
        string title
        text content
    }

    PROJECT_PAPERS {
        string id PK
        string project_id FK
        string paper_id FK
        string uq_project_paper UK
    }

    PROJECT_DEPLOYMENT_TARGETS {
        string id PK
        string project_id FK
        string target_type
        string name
    }

    PROJECT_RUNS {
        string id PK
        string project_id FK
        string target_id FK "nullable, SET NULL"
        string retry_of_run_id FK "self nullable, SET NULL"
        string workflow_type
        string status
        string task_id "soft link to tracker_tasks.task_id"
        datetime started_at
        datetime finished_at
    }

    PROJECT_RUN_ACTIONS {
        string id PK
        string run_id FK
        string action_type
        string status
        string task_id "soft link to tracker_tasks.task_id"
    }

    PROJECT_RESEARCH_WIKI_NODES {
        string id PK
        string project_id FK
        string node_key
        string source_paper_id FK "nullable, SET NULL"
        string source_run_id FK "nullable, SET NULL"
        string uq_project_research_wiki_node_key UK
    }

    PROJECT_RESEARCH_WIKI_EDGES {
        string id PK
        string project_id FK
        string source_node_id FK
        string target_node_id FK
        string relation_type
        string uq_project_research_wiki_edge UK
    }

    PROJECT_GPU_LEASES {
        string id PK
        string workspace_server_id
        int gpu_index
        bool active
        string project_id FK "nullable, SET NULL"
        string run_id FK "nullable, SET NULL"
        string task_id "soft link"
        string uq_project_gpu_leases_server_gpu UK
    }

    PAPERS {
        string id PK
        string arxiv_id UK
        string title
    }

    PROJECTS ||--o{ PROJECT_REPOS : owns
    PROJECTS ||--o{ PROJECT_IDEAS : owns
    PROJECTS ||--o{ PROJECT_PAPERS : includes
    PAPERS ||--o{ PROJECT_PAPERS : linked

    PROJECTS ||--o{ PROJECT_DEPLOYMENT_TARGETS : has
    PROJECTS ||--o{ PROJECT_RUNS : executes
    PROJECT_DEPLOYMENT_TARGETS ||--o{ PROJECT_RUNS : target_of
    PROJECT_RUNS ||--o{ PROJECT_RUNS : retry_chain
    PROJECT_RUNS ||--o{ PROJECT_RUN_ACTIONS : followups

    PROJECTS ||--o{ PROJECT_RESEARCH_WIKI_NODES : has
    PAPERS ||--o{ PROJECT_RESEARCH_WIKI_NODES : evidence_source
    PROJECT_RUNS ||--o{ PROJECT_RESEARCH_WIKI_NODES : run_source

    PROJECTS ||--o{ PROJECT_RESEARCH_WIKI_EDGES : has
    PROJECT_RESEARCH_WIKI_NODES ||--o{ PROJECT_RESEARCH_WIKI_EDGES : source_node
    PROJECT_RESEARCH_WIKI_NODES ||--o{ PROJECT_RESEARCH_WIKI_EDGES : target_node

    PROJECTS ||--o{ PROJECT_GPU_LEASES : uses
    PROJECT_RUNS ||--o{ PROJECT_GPU_LEASES : occupies
```

### 2.2 字段解释（核心）

- `projects`: 项目主实体。
- `project_runs`: 项目 workflow 执行主线，`workflow_type/status` 定义运行态。
- `project_run_actions`: 针对某次 run 的后续动作（continue/retry 等）。
- `project_deployment_targets`: run 的部署目标。
- `project_papers`: 项目与论文关联表。
- `project_research_wiki_nodes/edges`: 项目知识图谱节点与边。
- `project_gpu_leases`: GPU 占用记录，与 project/run 关联。

### 2.3 查询示例 SQL

```sql
-- Q1: 某项目最近 20 次 run（附目标名称）
SELECT r.id, r.workflow_type, r.status, r.active_phase,
       r.started_at, r.finished_at,
       t.name AS target_name
FROM project_runs r
LEFT JOIN project_deployment_targets t ON t.id = r.target_id
WHERE r.project_id = :project_id
ORDER BY r.created_at DESC
LIMIT 20;

-- Q2: 某次 run 的动作链
SELECT a.id, a.action_type, a.status, a.active_phase, a.created_at
FROM project_run_actions a
WHERE a.run_id = :run_id
ORDER BY a.created_at ASC;

-- Q3: 某项目 wiki 图谱边（带两端节点 key）
SELECT e.id,
       sn.node_key AS source_key,
       tn.node_key AS target_key,
       e.relation_type,
       e.created_at
FROM project_research_wiki_edges e
JOIN project_research_wiki_nodes sn ON sn.id = e.source_node_id
JOIN project_research_wiki_nodes tn ON tn.id = e.target_node_id
WHERE e.project_id = :project_id
ORDER BY e.created_at DESC;

-- Q4: 当前活跃 GPU 占用
SELECT l.workspace_server_id, l.gpu_index, l.project_id, l.run_id, l.task_id, l.locked_at
FROM project_gpu_leases l
WHERE l.active = TRUE
ORDER BY l.workspace_server_id, l.gpu_index;
```

## 3. Agent 会话链

### 3.1 Mermaid 图

```mermaid
erDiagram
    AGENT_PROJECTS {
        string id PK
        string worktree UK
        string name
        datetime created_at
    }

    AGENT_PERMISSION_RULES {
        string project_id PK
        json data_json
        datetime updated_at
    }

    AGENT_SESSIONS {
        string id PK
        string slug
        string project_id FK
        string parent_id FK "self nullable, SET NULL"
        string user_id
        string directory
        string mode
        string backend_id
        datetime created_at
        datetime archived_at
    }

    AGENT_SESSION_MESSAGES {
        string id PK
        string session_id FK
        string parent_id "message thread pointer"
        string role
        string message_type
        text content
        datetime created_at
    }

    AGENT_SESSION_PARTS {
        string id PK
        string session_id FK
        string message_id FK
        string part_type
        text content
        datetime created_at
    }

    AGENT_SESSION_TODOS {
        string id PK
        string session_id FK
        text content
        string status
        string priority
        int position
    }

    AGENT_PENDING_ACTIONS {
        string id PK
        string session_id FK
        string project_id FK
        string action_type
        json permission_json
        json continuation_json
        datetime created_at
    }

    AGENT_PROJECTS ||--|| AGENT_PERMISSION_RULES : has_rule_set
    AGENT_PROJECTS ||--o{ AGENT_SESSIONS : owns
    AGENT_SESSIONS ||--o{ AGENT_SESSIONS : parent_child

    AGENT_SESSIONS ||--o{ AGENT_SESSION_MESSAGES : has
    AGENT_SESSION_MESSAGES ||--o{ AGENT_SESSION_PARTS : splits_into
    AGENT_SESSIONS ||--o{ AGENT_SESSION_PARTS : denormalized_session_index

    AGENT_SESSIONS ||--o{ AGENT_SESSION_TODOS : tracks
    AGENT_SESSIONS ||--o{ AGENT_PENDING_ACTIONS : waits_for_confirm
    AGENT_PROJECTS ||--o{ AGENT_PENDING_ACTIONS : scoped_to_project
```

### 3.2 字段解释（核心）

- `agent_projects`: Agent 维度项目（`worktree` 唯一）。
- `agent_sessions`: 会话主表，支持 parent session 分叉。
- `agent_session_messages`: 顶层消息流。
- `agent_session_parts`: 消息分片（tool call、thinking、text 等结构化片段）。
- `agent_session_todos`: 会话内待办序列。
- `agent_permission_rules`: 项目级持久化权限规则。
- `agent_pending_actions`: 等待人工确认的动作。

### 3.3 查询示例 SQL

```sql
-- Q1: 某项目下最近活跃会话
SELECT s.id, s.slug, s.title, s.mode, s.updated_at
FROM agent_sessions s
WHERE s.project_id = :agent_project_id
  AND s.archived_at IS NULL
ORDER BY s.updated_at DESC
LIMIT 30;

-- Q2: 某会话最近 100 条消息（含 part 数量）
SELECT m.id, m.role, m.message_type, m.created_at,
       COUNT(p.id) AS part_count
FROM agent_session_messages m
LEFT JOIN agent_session_parts p ON p.message_id = m.id
WHERE m.session_id = :session_id
GROUP BY m.id, m.role, m.message_type, m.created_at
ORDER BY m.created_at DESC
LIMIT 100;

-- Q3: 待确认动作队列
SELECT pa.id, pa.action_type, pa.session_id, pa.project_id, pa.created_at
FROM agent_pending_actions pa
ORDER BY pa.created_at ASC;
```

## 4. 任务追踪链

### 4.1 Mermaid 图

```mermaid
erDiagram
    TRACKER_TASKS {
        string task_id PK
        string task_type
        string status
        bool finished
        bool success
        float progress_pct
        string source
        string source_id
        string project_id "soft ref"
        string paper_id "soft ref"
        string run_id "soft ref"
        string action_id "soft ref"
        datetime started_at
        datetime finished_at
    }

    TRACKER_TASK_LOGS {
        string id PK
        string task_id FK
        string level
        text message
        json data_json
        datetime created_at
    }

    PROJECT_RUNS {
        string id PK
        string task_id "soft ref to tracker"
    }

    PROJECT_RUN_ACTIONS {
        string id PK
        string task_id "soft ref to tracker"
    }

    PROJECT_GPU_LEASES {
        string id PK
        string task_id "soft ref to tracker"
    }

    TRACKER_TASKS ||--o{ TRACKER_TASK_LOGS : append_logs

    TRACKER_TASKS }o--o{ PROJECT_RUNS : logical_by_task_id
    TRACKER_TASKS }o--o{ PROJECT_RUN_ACTIONS : logical_by_task_id
    TRACKER_TASKS }o--o{ PROJECT_GPU_LEASES : logical_by_task_id
```

### 4.2 字段解释（核心）

- `tracker_tasks`: 任务状态快照主表（进度、结果、错误、来源对象）。
- `tracker_task_logs`: append-friendly 日志明细表。
- 与 `project_runs/project_run_actions/project_gpu_leases` 通过 `task_id` 做逻辑关联（非 FK）。

### 4.3 查询示例 SQL

```sql
-- Q1: 最近失败任务
SELECT t.task_id, t.task_type, t.status, t.error, t.updated_at
FROM tracker_tasks t
WHERE t.success = FALSE OR t.status IN ('failed', 'error')
ORDER BY t.updated_at DESC
LIMIT 100;

-- Q2: 某任务日志时间线
SELECT l.created_at, l.level, l.message
FROM tracker_task_logs l
WHERE l.task_id = :task_id
ORDER BY l.created_at ASC;

-- Q3: 任务与项目 run 的关联检查（逻辑 join）
SELECT t.task_id, t.status, r.id AS run_id, r.workflow_type, r.status AS run_status
FROM tracker_tasks t
LEFT JOIN project_runs r ON r.task_id = t.task_id
WHERE t.task_id = :task_id;
```

## 5. 使用建议

- 若只做架构评审，优先看每节 `1.1/2.1/3.1/4.1` 图。
- 若做排障，先查 `tracker_tasks -> tracker_task_logs`，再按 `task_id` 回溯到 run/action。
- 若做数据治理，优先核查软关联字段一致性（`task_id/project_id/paper_id/run_id/action_id`）。
