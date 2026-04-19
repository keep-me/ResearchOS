# 21 App.tsx 路由图

## 覆盖模块

- `frontend/src/App.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/components/shell/navigation.ts`

## 图

```mermaid
flowchart TD
  App[App.tsx] --> AuthBoot[auth bootstrap]
  AuthBoot -->|后端未就绪| Waiting[BackendWaitingScreen]
  AuthBoot -->|未认证| Login[LoginPage]
  AuthBoot -->|已认证| Router[BrowserRouter + Layout]

  Router --> Assistant["/assistant\n/assistant/:conversationId"]
  Router --> Collect["/collect"]
  Router --> Papers["/papers\n/papers/:id"]
  Router --> Projects["/projects\n/projects/:projectId"]
  Router --> Graph["/graph"]
  Router --> Wiki["/wiki"]
  Router --> Brief["/brief"]
  Router --> Tasks["/tasks"]
  Router --> Settings["/settings"]
  Router --> Writing["/writing"]

  Router --> Redirects[常见重定向\n/ -> /assistant\n/dashboard -> /assistant\n/workbench -> /projects\n/topics -> /collect\n/my-day -> /brief]
  Router --> NotFound[* -> 404]
```

## 阅读提示

- 这张图回答的是“前端主路由到底长什么样”。
- `App.tsx` 在进入路由树之前还要先完成认证状态协商。
