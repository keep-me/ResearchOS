# ResearchClaw 技术文档

## 1. 项目定位

`ResearchClaw-main` 是一个非常完整的本地优先科研桌面应用。它没有采用“浏览器前端 + 独立 HTTP 后端”的传统 Web 结构，而是以 Electron 主进程作为本地后端，再用 React 渲染桌面 UI。

它的产品目标很统一：

- 管理本地论文资产。
- 组织项目、仓库、想法、任务和聊天。
- 把 AI 阅读和本地工作流整合进桌面壳层。

这使它对 ResearchOS 的参考价值非常高，尤其适合桌面应用壳层、主进程边界和本地数据建模。

## 2. 技术栈

## 应用基础

- Electron
- React 19
- Vite
- TypeScript
- Tailwind CSS

## 数据与业务

- Prisma
- SQLite
- sql.js
- zod
- uuid

## AI 与可视化

- Vercel AI SDK 相关 provider
- Cytoscape + cytoscape-dagre
- @nivo/line
- react-markdown + rehype-highlight
- TipTap
- SSH2

## 工程与测试

- electron-builder
- Vitest
- Testing Library
- Husky
- Prettier

## 3. 工程结构

顶层目录说明了它是典型的 Electron 三层结构：

- `src/main/`: Electron 主进程。
- `src/renderer/`: React 渲染层。
- `src/shared/`: 主进程和渲染层共享类型/协议。
- `src/db/`: 数据访问层。
- `prisma/`: 数据模型与生成配置。
- `scripts/`: 开发与打包脚本。
- `tests/`: 集成测试与前端测试。

`src/renderer/` 进一步拆为：

- `components`
- `hooks`
- `locales`
- `pages`
- `public`
- `styles`
- `types`

这是一种非常适合桌面应用长期维护的目录组织方式。

## 4. 运行架构

ResearchClaw 的核心判断是：Electron Main Process 就是本地后端。

## Main Process 负责什么

从依赖和目录可推断，Main Process 负责：

- 窗口与应用生命周期管理。
- 本地文件系统、SSH、CLI、代理等系统能力。
- IPC handler 注册。
- 数据层访问与业务服务调度。
- AI 请求与任务协调。

## Renderer 负责什么

Renderer 主要承担：

- 桌面工作台 UI。
- 页面切换与局部状态。
- 通过 IPC 调用 Main Process 能力。
- 展示论文库、阅读卡、项目、对话、任务等对象。

## 为什么这条路线重要

这种架构和 ResearchOS 的 `frontend + src-tauri` 非常接近，因为两者都在解决同一个问题：

- 不只是做 Web 页面。
- 而是做带本地系统能力的研究工作台。

## 5. 数据模型

`prisma/schema.prisma` 是这个项目最有价值的部分之一，因为它展示了完整的研究对象建模。

已确认的核心模型包括：

- `Paper`
- `PaperEmbedding`
- `SourceEvent`
- `Tag`
- `PaperTag`
- `ReadingNote`
- `Project`
- `ProjectRepo`
- `ProjectIdea`
- `PaperCodeLink`
- `ProjectPaper`

以及 README 和脚本层可推断出的其他研究资产对象：

- 引用关系
- Agent Todo
- Chat Session
- Task Result
- Experiment Report

这种建模方式最值得注意的点在于：

- 论文不是孤立资源。
- 项目、想法、代码仓库、任务、会话都被视为同一研究系统里的一级对象。

## 6. 产品链路

## 论文链路

- 导入论文。
- 保存元数据与 PDF 路径。
- 执行后处理、索引、标签、嵌入。
- 进入阅读和项目沉淀流程。

## 阅读链路

- 打开论文。
- AI 生成结构化阅读卡或阅读笔记。
- 用户继续编辑和沉淀。

## 项目链路

- 将论文绑定到 `Project`。
- 记录 `ProjectRepo`、`ProjectIdea`。
- 继续向 Agent Todo 或结果报告流转。

这说明 ResearchClaw 已经把科研活动抽成长期资产管理，而不是单次 AI 对话。

## 7. 前端与桌面 UI 形态

ResearchClaw 的 UI 路线不是传统网页，而是明显的桌面工作台模式：

- 左侧或顶部壳层导航。
- 论文、项目、聊天、任务共用一个桌面上下文。
- 局部面板、最近项、状态提示等桌面元素突出。

这种布局最适合给 ResearchOS 参考的部分包括：

- 如何组织桌面信息层级。
- 如何把本地研究资产和 AI 工作流放进同一个壳层。
- 如何把“研究项目”提升为一级信息架构对象。

## 8. 启动、构建与测试

`package.json` 暴露了非常完整的工程化脚本：

- `dev`: 启动 Electron 开发环境。
- `build`: 构建 main + renderer。
- `pack` / `dist`: 生成安装包。
- `test`, `test:frontend`, `test:all`: 执行后端/前端测试。
- `db:generate`, `db:push`: Prisma 工作流。
- `release:mac`, `release:win`, `release:linux`: 平台化发布。

这说明它已经不是原型，而是面向分发交付的桌面产品工程。

## 9. 对 ResearchOS 的参考价值

ResearchClaw 最值得被吸收的不是某个页面，而是三件事：

- 桌面壳层应该如何组织。
- 主进程/渲染层/共享协议应该如何拆边界。
- 研究对象如何从“论文列表”升级为“论文 + 项目 + 想法 + 任务 + 会话”的统一模型。

如果 ResearchOS 后续想把桌面端真正做强，ResearchClaw 是最应该长期跟踪的参考之一。
