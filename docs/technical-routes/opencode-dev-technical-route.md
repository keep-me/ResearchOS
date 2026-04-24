# opencode-dev 技术路线

## 1. 项目定位

opencode-dev 不是科研应用，它的目标是“开源 AI 编码 Agent 平台”。但它对于 ResearchOS 的意义非常大，因为 ResearchOS 正在长出越来越多 Agent runtime 特征：

- 工作区执行
- Skills
- MCP
- 会话上下文
- 权限控制
- 本地/桌面/远程多端入口

这些地方，opencode-dev 都比一般科研项目成熟得多。

## 2. 技术栈与工程结构

## 基础栈

- Bun workspace
- 多包 monorepo
- SolidJS
- Tailwind v4
- Effect
- Hono / OpenAPI
- Drizzle
- Astro Web 站点
- Tauri 与 Electron 双桌面端

## 关键 packages

- `packages/opencode/`：核心 runtime。
- `packages/app/`：主应用 UI。
- `packages/ui/`：共享 UI 组件。
- `packages/web/`：官网和文档站。
- `packages/desktop/`：Tauri 桌面端。
- `packages/desktop-electron/`：Electron 桌面端。
- `packages/console/*`：云控制台相关能力。
- `packages/sdk/js/`：SDK。

这不是单体应用，而是“核心 runtime + 多客户端 + SDK + 控制台”的平台结构。

## 3. 架构路线判断

## 3.1 先做 core runtime，再做客户端

这是 opencode-dev 最关键的地方。

它没有把逻辑都写死在前端页面里，而是把真正的能力下沉到 `packages/opencode/`：

- agent
- session
- permission
- tool
- file
- shell
- lsp
- mcp
- provider
- skill
- storage

这意味着它的 Web、Desktop、TUI、Console 都可以围绕同一核心展开。

## 3.2 UI 层是“会话操作界面”，不是传统页面集合

`packages/app/src/app.tsx` 和 `packages/app/src/pages/` 体现出明显的 Agent 产品形态：

- Home
- Directory Layout
- Session
- Session Side Panel
- Terminal Panel
- File Tabs
- Composer Docks

这类 UI 不是论文管理式的“列表-详情”，而是“工作区-会话-终端-评论-权限请求”的高频操作界面。

## 3.3 平台化能力非常完整

从包结构就能看出它具备以下平台属性：

- Web 客户端
- Tauri 桌面客户端
- Electron 桌面客户端
- SDK
- 插件系统
- 控制台
- 文档站

这说明它的终局不是一个 App，而是一个 Agent 平台生态。

## 4. 关键业务链路

虽然 opencode-dev 不是科研项目，但其 runtime 链路很值得抽象：

### 1. 会话建立

- 选择目录或工作区。
- 创建 session。
- 绑定 provider、权限、layout、terminal 等上下文。

### 2. 提示与工具执行

- 用户输入 prompt。
- Runtime 选择模型与工具。
- 触发文件、终端、网络、权限请求。
- 产出消息流。

### 3. 工作区状态同步

- 文件树
- diff
- review
- terminal
- comments
- highlight

这些能力都不是页面拼出来的，而是共享 context 与 runtime 驱动的。

## 5. 前端 UI 路线

opencode-dev 的前端不适合直接拿来做 ResearchOS 的页面风格，但非常适合借鉴布局思路：

## 5.1 目录布局是“工作区中心”

- 先进入目录。
- 再进入 session。
- 再展开侧面板、终端、文件 tabs、评论与权限 dock。

这种方式非常适合 Agent 产品，因为核心对象不是“页面”，而是“工作现场”。

## 5.2 侧栏不是传统导航，而是上下文承载区

- Workspace
- Project
- Session list
- Recent state
- Inline editor

这对 ResearchOS 很有启发：未来研究助手的侧栏也不该只是模块导航，还应该承载工作区状态。

## 5.3 权限和提问 Dock 是一等公民

- permission dock
- question dock
- revert dock
- todo dock

这意味着“运行时交互”本身是 UI 主体，而不是附属弹窗。

## 6. 对 ResearchOS 的参考价值

## 最重要的不是视觉，而是 runtime 设计

ResearchOS 已经有：

- `agent_service`
- `skill_registry`
- `workspace_executor`
- `native_mcp_manager`
- `opencode_manager`

这些模块说明项目正在进入 Agent 平台阶段。opencode-dev 可以作为以下方面的强参考：

### 1. 会话层

- 一个研究助手 session 如何建模。
- 一个 session 如何绑定工作区、权限、任务和工具。

### 2. 权限层

- 文件读写、命令执行、网络访问、MCP 工具调用如何显式授权。

### 3. 多客户端层

- Web、Tauri、桌面端是否共享 runtime。
- UI 是否只做壳和展示。

### 4. 插件层

- Skill、Plugin、SDK、MCP 如何形成统一扩展生态。

## 7. 如果 ResearchOS 要吸收 opencode-dev，应怎么分阶段做

## 第一阶段：抽出研究助手 runtime

- 将研究助手页面和底层执行逻辑进一步解耦。
- 让 `frontend` 只负责交互与状态展示。
- 让 `packages/ai` 中的 Agent 模块拥有稳定接口。

## 第二阶段：建立权限与审计体系

- 文件操作要有权限边界。
- 命令执行要有确认和日志。
- 外部工具调用要有可追踪痕迹。

## 第三阶段：统一桌面与 Web 执行模型

- Tauri、FastAPI、工作区执行层统一上下文协议。
- 为后续移动端或远程控制端预留空间。

## 8. 最终路线判断

opencode-dev 对 ResearchOS 的最大价值是：

- 它能告诉我们，ResearchOS 的研究助手不该只是一个聊天页。
- 它应该是一个真正的 runtime。
- 一旦 runtime 成立，Skills、MCP、工作区执行、远程节点、多端同步都会自然变成同一层问题。

因此，opencode-dev 应被视为 ResearchOS 的“Agent 平台参考项目”，而不是“UI 视觉参考项目”。
