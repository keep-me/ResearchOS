# opencode-dev 技术文档

## 1. 项目定位

`opencode-dev` 的定位不是科研应用，而是开源 AI 编码 Agent 平台。它最重要的价值不在论文业务，而在于它已经把 Agent 产品中最难的一层抽出来了：runtime、权限、会话、工具、工作区、多客户端。

对 ResearchOS 来说，它的意义是：

- 当 ResearchOS 从“研究工具集合”走向“研究 Agent 平台”时，需要一套更成熟的 runtime 设计。
- 这套设计在 `opencode-dev` 里已经很完整。

## 2. 技术栈

## 核心 Runtime

- Bun workspace
- TypeScript
- Effect
- Hono
- Hono OpenAPI
- Drizzle ORM / Drizzle Kit
- zod
- AI SDK 多 provider

## 客户端与 UI

- SolidJS
- Tailwind CSS v4
- OpenTUI
- Astro + Starlight（官网与文档）
- Tauri 与 Electron 双桌面形态

## 工程特征

- 多包 monorepo
- workspace catalog 统一依赖版本
- patch 管理第三方依赖
- SDK 独立发布

## 3. 工程结构

从仓库目录可以看出，它不是单一应用，而是平台化工程：

- `packages/opencode/`: 核心 runtime。
- `packages/app/`: 主应用 UI。
- `packages/desktop/`: Tauri 桌面端。
- `packages/desktop-electron/`: Electron 桌面端。
- `packages/web/`: Astro 官方站点/文档站。
- `packages/sdk/js/`: JS SDK。
- `packages/plugin/`, `packages/util/`, `packages/script/`: 平台配套包。
- `packages/ui/`, `packages/console/` 等目录显示其生态范围已超出单一客户端。

## 4. 核心架构判断

## Runtime 先于 UI

`packages/opencode/src/` 的目录结构直接暴露了它的真实核心：

- `agent`
- `session`
- `permission`
- `tool`
- `skill`
- `mcp`
- `provider`
- `shell`
- `pty`
- `worktree`
- `storage`
- `project`
- `plugin`
- `server`
- `auth`

这说明它不是“先做页面再补业务”，而是先做一个可复用 runtime，再让多个客户端共用。

## UI 只是不同入口

`packages/app/src` 包含：

- `components`
- `context`
- `hooks`
- `pages`
- `i18n`
- `utils`

而 `packages/web` 又单独承担官网和文档站。再加上 `desktop`、`desktop-electron`、`sdk`，可以看出 UI 在整个系统里只是 runtime 的不同投影，而不是唯一主体。

## 5. 运行时能力地图

从依赖和目录判断，opencode-dev 已经把 Agent 系统最关键的几个问题都做了显式模块化：

## 会话与状态

- session 生命周期
- 消息流
- storage
- snapshot
- share

## 权限与执行

- permission
- shell
- pty
- file
- worktree
- scheduler

## 扩展能力

- tool
- skill
- plugin
- mcp
- provider

## 环境与配置

- config
- env
- installation
- global
- auth

这套模块划分非常值得 ResearchOS 借鉴，因为当前 ResearchOS 也已经开始出现 `skill_registry`、`workspace_executor`、`researchos_mcp`、`opencode_manager` 这类模块。

## 6. SDK 与平台化

`packages/sdk/js/package.json` 表明 SDK 是一等公民：

- 公开 `client` 与 `server` 入口。
- 还维护了 `v2` 版本命名空间。
- 具备独立 build/typecheck 能力。

这意味着 opencode-dev 不是只想做自己的客户端，而是希望别人也能在其 runtime 能力之上构建新客户端和集成。

## 7. 文档与多端交付

`packages/web/package.json` 说明其文档站使用：

- Astro
- Starlight
- Solid 集成
- 代码高亮与文档主题组件

这和普通应用的 README 级文档完全不同，它已经具备平台产品的完整文档交付思路。

## 8. 对 ResearchOS 的直接参考点

opencode-dev 最值得吸收的部分不是视觉，而是抽象层次：

- Agent runtime 要独立于具体页面。
- 工作区、终端、文件、权限应该成为一级模型。
- MCP、Skill、Plugin 应该归入统一扩展层。
- 多客户端共享同一个核心，比在每个前端里重复写逻辑更可持续。

## 9. 结合当前 ResearchOS 的落地建议

如果把它映射到当前 ResearchOS，可以得出很清晰的方向：

- `packages/ai/` 继续承担研究业务能力。
- 另起更稳定的 runtime 抽象，统一管理会话、权限、工作区、技能和 MCP。
- 前端页面不要继续直接承载过多 Agent 逻辑，而应逐步改成 runtime 的表现层。
- 桌面端、Web 端、将来的远程控制端，都应该复用相同的核心能力。

因此，opencode-dev 是 ResearchOS 从“应用”走向“平台”的关键参考对象。
