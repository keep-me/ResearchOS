# Project Tech Docs

这组文档面向当前仓库和 `reference/` 下的四个参考项目，目标是把每个项目的定位、技术栈、工程结构、运行链路和可借鉴点整理成后续迭代可直接使用的资料。

## 文档清单

- `researchos-technical-documentation.md`: 当前项目 `ResearchOS` 的正式技术文档。
- `researchos-baseline-technical-documentation.md`: `ResearchOS` 基线版本技术文档。
- `researchclaw-technical-documentation.md`: `reference/ResearchClaw-main` 技术文档。
- `opencode-dev-technical-documentation.md`: `reference/opencode-dev` 技术文档。
- `amadeus-technical-documentation.md`: `reference/Amadeus-master` 技术文档。
- `researchos-refactor-execution-checklist.md`: 当前项目结构重构的按周执行清单与勾选基线。

## 快速对比

| 项目 | 主定位 | 主要运行形态 | 后端/核心 Runtime | 前端/客户端 | 对 ResearchOS 的直接参考价值 |
| --- | --- | --- | --- | --- | --- |
| ResearchOS | AI 研究工作流与桌面研究台 | FastAPI + Worker + Tauri | Python, FastAPI, APScheduler, SQLite | React, Vite, Tailwind, Tauri | 当前主工程 |
| ResearchOS Baseline | 基线版 AI 学术研究工作流 | Web 优先, 前端预留桌面启动引导 | Python, FastAPI, APScheduler, SQLite | React, Vite, Tailwind | UI 基线与原始产品心智 |
| ResearchClaw | 本地优先科研桌面应用 | Electron 桌面应用 | Electron Main + Prisma/SQLite | React, Vite, Tailwind | 桌面壳层与本地数据建模 |
| opencode-dev | 通用 AI 编码 Agent 平台 | Bun 多包 + 多客户端 | Bun, Effect, Hono, Drizzle | Solid, Astro, Tauri/Electron | Agent runtime、权限和工作区模型 |
| Amadeus | Web 化个人 AI 研究助手 | Express + Next + 扩展/插件 | Node, Express, 对象存储, Tracker | Next.js, Radix, VS Code/Chrome 扩展 | 论文流、远程工作流与多端协作 |

## 当前结论

- `ResearchOS Baseline` 是当前项目前端 UI 的原始基线，已适合作为视觉和交互恢复参考。
- `ResearchClaw` 更适合持续参考桌面应用壳层、主进程边界和本地研究资产建模。
- `opencode-dev` 更适合参考 Agent runtime、权限控制、MCP/Skill/Workspace 抽象。
- `Amadeus` 最适合作为论文输入链路、远程研究工作流、对象存储和多端入口的业务参考。
