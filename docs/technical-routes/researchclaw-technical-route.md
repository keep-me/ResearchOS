# ResearchClaw 技术路线

## 1. 项目定位

ResearchClaw 是一个非常典型、也非常值得参考的“原生科研桌面应用”。

它不是 Web 控制台套壳，而是把以下能力直接放进 Electron 本地应用：

- 论文导入与文献库
- AI 阅读与结构化阅读卡
- Projects 与创意生成
- Agent Todo / Chat / CLI Tools
- 本地数据库与本地文件系统

这条路线与 ResearchOS 当前 `frontend + src-tauri` 的目标最接近，所以它最适合作为前端壳层和桌面交互的参考基线。

## 2. 技术栈与工程结构

## 核心栈

- Electron
- React 19
- Vite
- TypeScript
- Prisma + SQLite
- Vercel AI SDK
- Tailwind CSS

## 目录结构

- `src/main/`：Electron 主进程，IPC、服务、Store、Agent 运行。
- `src/renderer/`：React 渲染层。
- `src/shared/`：共享类型、模板、提示词、工具。
- `src/db/`：数据库访问与 repository。
- `prisma/schema.prisma`：完整数据模型。

这种结构非常清晰，是典型的桌面应用三层拆法：

- Main Process 负责系统能力。
- Renderer 负责 UI。
- Shared 负责共用协议。

## 3. 架构分层判断

## 3.1 Main Process 是真正的后端

ResearchClaw 没有额外起一个 HTTP 服务器作为主中台，而是把主进程当本地后端：

- IPC handler 暴露能力
- service 承担业务逻辑
- store 承担本地配置与状态持久化
- db/repository 承担数据访问

这种方式的优点是：

- 本地桌面应用体验更直接。
- 文件、窗口、CLI、代理、SSH 等系统能力更好接。
- 无需把所有交互都再包成网络协议。

## 3.2 Renderer 非常专注于桌面 UI 壳

`src/renderer/components/app-shell.tsx` 是它最重要的文件之一。这个文件体现了它的 UI 路线：

- 顶部标题栏与 tab strip
- 左侧可折叠侧边栏
- 主内容区
- Recent 区
- 全局 toast
- 设置向导入口

这不是一个普通网页布局，而是一个“桌面工作台壳”。ResearchOS 本轮前端改造也正是参考了这条路线。

## 3.3 数据模型是“研究工作单元”导向

从 `prisma/schema.prisma` 可以看出，它的数据建模非常完整：

- `Paper`
- `Tag`
- `ReadingNote`
- `Project`
- `ProjectRepo`
- `ProjectIdea`
- `PaperEmbedding`
- `PaperCitation`
- `AgentTodo`
- `TaskResult`
- `ChatSession`

这说明它不把论文看成孤立对象，而是把论文、项目、想法、任务、会话都视作同一研究系统内的工作单元。

## 4. 核心业务链路

## 4.1 本地导入链路

- 从 Chrome 历史、URL、arXiv 等导入。
- 下载 PDF。
- 写入 SQLite。
- 自动做 tag、索引、embedding 等后处理。

## 4.2 AI 阅读链路

- 打开论文。
- PDF 与聊天/阅读卡并排。
- 生成结构化阅读卡。
- 用户继续编辑、补充、进入项目沉淀。

ResearchClaw 强调的不是“AI 给一段摘要”，而是“AI 协助你形成阅读资产”。

## 4.3 项目链路

- 将论文与代码仓库组织进 project。
- 生成 research ideas。
- 记录 todo、报告和结果。

这一步很适合作为 ResearchOS 后续“工作区 + 论文 + 助手”统一建模的参考。

## 5. 前端 UI 布局路线

ResearchClaw 的 UI 路线很稳定，也很适合桌面场景。

## 5.1 壳层结构

- 左侧栏：Dashboard、Search、Library、Projects、Tasks、Recent、Settings。
- 顶部栏：窗口控制、tab、返回入口。
- 主内容：宽留白内容区。

这种布局有几个明显好处：

- 导航层级很清楚。
- 内容区可以专注于单一任务。
- 最近访问对象自然进入侧栏，不需要单独做“历史页”。

## 5.2 视觉语言

- 用浅色中性底色承接长时间阅读。
- 卡片边框轻、圆角大、层级柔和。
- 强调“桌面文档应用”的稳定感，而不是 Web SaaS 的营销感。

## 5.3 适合本项目的借鉴点

ResearchOS 本轮已经吸收了以下几点：

- 窗口式整体壳层。
- 左侧导航的轻桌面化层级。
- 顶部轻量切换条。
- 首页从入口页改为“总控台”。

后续还可以继续借鉴：

- Recent 区如何更好承接最近访问论文/工作区。
- 顶部 tab 如何承接多会话、多论文、多工作区并行。
- 页面内容区的宽度与留白规范。

## 6. 工程化与桌面能力路线

ResearchClaw 的工程组织对桌面项目非常有启发：

### 1. IPC 边界明确

- 主进程做系统能力。
- 渲染层只通过 IPC 访问。
- 类型从 `shared` 统一下发。

### 2. 数据模型先行

- 先把论文、项目、想法、任务、会话建模清楚。
- UI 只是这些实体的不同视图。

### 3. 本地优先

- 数据落本地 SQLite。
- 文件、PDF、repo、CLI 都优先本地。
- 更贴合个人研究者工作方式。

## 7. 如果把 ResearchClaw 当作长期参考，ResearchOS 应该怎么吸收

## 第一层：壳层与信息架构

- 已开始落地。
- 继续统一页面头部、侧栏区块、内容留白、近期对象入口。

## 第二层：工作单元建模

- 让“论文、工作区、任务、会话、产出”形成统一对象体系。
- 减少页面之间只靠 URL 和临时请求拼接上下文。

## 第三层：桌面原生能力

- 更明确地把文件系统、工作区授权、终端、系统通知、窗口行为交给 Tauri 层。
- 前端仅负责展示与交互调度。

## 8. 最终路线判断

ResearchClaw 的价值不只是“界面好看”，而在于它证明了：

- 科研产品完全可以按原生桌面应用的方式组织。
- 论文管理、项目管理、Agent、任务、阅读可以共享同一桌面壳。
- 只要主进程与渲染层边界清晰，应用会非常适合长期扩展。

对 ResearchOS 来说，ResearchClaw 是最适合拿来定义“桌面端长什么样”的参考项目。
