# 10 Project Workflow 与 ARIS 编排图

## 覆盖模块

- `apps/api/routers/projects.py`
- `packages/ai/project/execution_service.py`
- `packages/ai/project/workflow_runner.py`
- `packages/ai/project/workflow_catalog.py`
- `packages/storage/models.py`
- `packages/ai/research/research_wiki_service.py`

## 图

```mermaid
flowchart TD
  UI[Projects.tsx / Agent workflow launch] --> Router[projects router]
  Router --> Exec[execution_service.py\nsubmit / retry / checkpoint response]
  Exec --> Runner[workflow_runner.py]

  Runner --> Ctx[WorkflowContext\nRunSnapshot / ProjectSnapshot / PaperSnapshot / RepoSnapshot]
  Runner --> Stage[orchestration + stage_trace]
  Runner --> WS[workspace\nlocal or remote]
  Runner --> GPU[GPU lease / screen session]
  Runner --> Roles[executor / reviewer / tool agents]
  Runner --> Artifacts[artifact files\nreports / logs / outputs]

  WS --> Actions[run experiment\npaper writing\nauto review loop]
  Roles --> Actions
  Stage --> Actions

  Actions --> RunModel[ProjectRun]
  Actions --> RunAction[ProjectRunAction]
  Actions --> Wiki[ProjectResearchWikiNode / Edge]
  Actions --> Tracker[tracker_tasks]
  GPU --> Lease[ProjectGpuLease]
  Artifacts --> ProjectsDir[projects/\nworkspace artifacts]

  Wiki --> ResearchWikiService[research_wiki_service.py]
```

## 阅读提示

- `workflow_runner.py` 复杂不是偶然，而是因为它真的在做执行系统编排。
- `ProjectResearchWikiNode/Edge` 说明结果不只是日志，而是在资产化。
