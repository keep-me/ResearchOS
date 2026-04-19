# 24 页面到 API 服务依赖图

## 覆盖模块

- `frontend/src/pages/*.tsx`
- `frontend/src/services/api.ts`
- `apps/api/routers/*.py`

## 图

```mermaid
flowchart TD
  Agent[Agent.tsx] --> AgentApis[sessionApi\nassistantWorkspaceApi\nprojectApi\nmcpApi\nacpApi\nllmConfigApi\npaperApi\ntopicApi]
  Collect[Collect.tsx] --> CollectApis[topicApi\ningestApi\npaperApi]
  Papers[Papers.tsx] --> PapersApis[paperApi\nactionApi\ntasksApi\npipelineApi\ntopicApi]
  Detail[PaperDetail.tsx] --> DetailApis[paperApi\npipelineApi\ntasksApi\ntopicApi]
  Projects[Projects.tsx] --> ProjectApis[projectApi\nassistantWorkspaceApi\nworkspaceRootApi]
  Wiki[Wiki.tsx] --> WikiApis[wikiApi\ngeneratedApi\ntasksApi\ntopicApi]
  Brief[DailyBrief.tsx] --> BriefApis[briefApi\ngeneratedApi\ntasksApi]
  Writing[Writing.tsx] --> WritingApis[writingApi]
  Tasks[Tasks.tsx] --> TaskApis[tasksApi\nprojectApi\nassistantWorkspaceApi]
  Settings[SettingsPage.tsx] --> SettingsApis[llmConfigApi\nassistantExecPolicyApi\nassistantSkillApi\nmcpApi\nacpApi]

  AgentApis --> Routers1[session_runtime / agent_workspace / projects / mcp / acp / papers / topics]
  CollectApis --> Routers2[topics / papers / pipelines]
  PapersApis --> Routers3[papers / pipelines / jobs]
  ProjectApis --> Routers4[projects / agent_workspace]
  WikiApis --> Routers5[writing / content / jobs / topics]
  SettingsApis --> Routers6[settings / mcp / acp]
```

## 阅读提示

- 这张图适合在“我想知道某个页面到底调用了哪些后端面”时使用。
- `Agent.tsx` 的服务依赖明显最重，说明它确实是主工作台。
