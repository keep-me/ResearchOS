# 04 仓库结构图

## 覆盖模块

- 仓库根目录
- `apps/`
- `packages/`
- `frontend/`
- `infra/`
- `scripts/`
- `tests/`
- `reference/`
- `class/`

## 图

```mermaid
flowchart TD
  Root[ResearchOS]
  Root --> Apps[apps/\napi worker desktop]
  Root --> Packages[packages/\nagent ai storage integrations domain]
  Root --> Frontend[frontend/\nsrc tests package.json]
  Root --> Infra[infra/\nmigrations]
  Root --> Scripts[scripts/\nbootstrap start smoke helpers]
  Root --> Tests[tests/\nstartup auth runtime workflow]
  Root --> Reference[reference/\nclaw-code-main]
  Root --> Class[class/\n30 节课 + diagrams]
  Root --> Data[data/\ndb papers briefs runtime json]
  Root --> Projects[projects/\nworkspace roots artifacts]

  Apps --> ApiApp[apps/api/main.py + routers]
  Apps --> WorkerApp[apps/worker/main.py]
  Apps --> DesktopApp[apps/desktop/server.py]

  Packages --> AgentPkg[packages/agent\nsession runtime tools workspace]
  Packages --> AiPkg[packages/ai\npaper research project ops]
  Packages --> StoragePkg[packages/storage\nbootstrap db models repositories]
  Packages --> IntPkg[packages/integrations\nLLM 学术数据源]
```

## 阅读提示

- 学习时优先顺序通常是 `apps -> packages -> frontend -> tests -> scripts`。
- `reference/` 用来做对照，不参与当前仓库运行事实。
