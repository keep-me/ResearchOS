# ResearchOS 参考项目技术路线总览

本文档用于汇总本项目与 `reference/` 中三个参考项目的技术路线，方便后续按模块、按优先级逐步吸收成熟设计。

## 文档清单

- `researchos-technical-route.md`：本项目现状、架构拆分、前端改造方向与分阶段落地路线。
- `amadeus-technical-route.md`：Amadeus 的 Web 平台路线，重点看论文流、ARIS 远程工作流和对象存储方案。
- `researchclaw-technical-route.md`：ResearchClaw 的桌面应用路线，重点看 Electron + IPC + 桌面壳层布局。
- `opencode-dev-technical-route.md`：opencode-dev 的多包 Agent 平台路线，重点看 runtime、权限、会话和多客户端架构。

## 本轮结论

- 本项目前端布局改造基线选用 `ResearchClaw`。
- 原因不是“它最好看”，而是它最贴近当前 `frontend + src-tauri` 的桌面场景。
- `Amadeus` 更适合作为论文流、Tracker、远程工作流的业务参考。
- `opencode-dev` 更适合作为 Agent runtime、权限控制、工作区执行与多端共享核心的架构参考。

## 四个项目的参考价值矩阵

| 维度 | ResearchOS | Amadeus | ResearchClaw | opencode-dev |
| --- | --- | --- | --- | --- |
| 主定位 | AI 学术研究工作流平台 | Web 化个人 AI 研究助手 | 原生科研桌面应用 | 通用 AI 编码 Agent 平台 |
| 前端壳层 | React + Vite + Tauri WebView | Next.js Web 单页式控制台 | React + Vite + Electron 桌面壳 | Solid + 多客户端壳层 |
| 后端形态 | Python FastAPI + 本地任务 | Node/Express + 对象存储 + Tracker | Electron Main Process 本地服务 | Bun core + app/web/desktop 多包 |
| 论文管理 | 已有，但仍偏功能页 | 最成熟，含 Feed/Library/Tracker | 本地库、标签、项目联动强 | 非主目标 |
| Agent 能力 | 已接入研究助手与工作区执行 | 有 AI 阅读与远程执行 | 有桌面内 Agent Todo / Chat | 最成熟，权限与会话体系强 |
| UI 可借鉴点 | 本轮已改造成桌面总控壳 | 顶部模式切换、Feed 卡片 | 侧边栏层级、窗口式布局 | 会话布局、侧栏与工作区切片 |

## 本项目的组合借鉴策略

### 1. UI 壳层先学 ResearchClaw

- 采用桌面应用式外框、左侧导航、顶部轻量切换条。
- 首页从“功能入口页”改成“研究工作台总控页”。
- 工作区/聊天区保留本项目已有能力，只调整信息层级和视觉组织。

### 2. 论文流和运维心智再学 Amadeus

- 主题订阅、Latest Feed、Library、Tracker Admin 的模块切分很清楚。
- 适合作为本项目 `collect / papers / topics / pipelines` 的后续重构参考。
- 尤其适合补强“输入端持续性”和“远程研究执行”的产品闭环。

### 3. Agent runtime 和权限模型再学 opencode-dev

- 研究助手、工作区命令执行、MCP、技能体系，后面都可以向其靠拢。
- 重点不是照搬 UI，而是吸收其“会话-工作区-权限-工具-终端”分层。
- 适合支撑本项目从“研究工具集合”继续进化为“研究 Agent 平台”。

## 建议的落地顺序

1. 先稳定本项目现有 FastAPI + React + Tauri 主链路，统一页面壳层和设计语言。
2. 再把论文输入、资产管理、知识产出三条线重构成更清晰的产品闭环。
3. 然后抽出研究助手 runtime，把工作区执行、MCP、Skill、远程执行做成独立层。
4. 最后再考虑多客户端形态，例如纯 Web、桌面端、远程控制端共享同一核心。
