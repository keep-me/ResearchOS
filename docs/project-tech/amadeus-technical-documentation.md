# Amadeus 技术文档

## 1. 项目定位

`Amadeus-master` 是一个非常成熟的 Web 化个人 AI 研究助手。它不是简单的论文库，而是把外部论文追踪、自动阅读、远程研究工作流、多端扩展入口整合成一个完整平台。

它最突出的产品心智包括：

- 持续输入，而不是一次性搜索。
- 研究资产沉淀，而不是一次性摘要。
- 远程执行与工作流管理，而不是单纯网页问答。

## 2. 技术栈

## 后端

- Node.js
- Express
- `@libsql/client`
- S3/MinIO 兼容对象存储
- Playwright / Puppeteer
- IMAP / Mail Parser
- WebSocket
- MCP Server
- 可选 OpenAI / Anthropic / Gemini 等模型接入

## 前端

- Next.js 14
- React 18
- Radix Themes
- Markdown + KaTeX + Mermaid
- xterm.js
- 同时保留 Vite 脚本，说明前端存在双模式或迁移过渡痕迹

## 扩展端

- Chrome Extension
- VS Code Extension

## 3. 工程结构

顶层结构已经说明它不是单一 Web 页应用，而是一个多入口研究平台：

- `backend/`: Express 后端。
- `frontend/`: Next.js 前端。
- `chrome-extension/`: 浏览器扩展。
- `vscode-extension/`: VS Code 扩展。
- `docs/`: 文档。
- `skills/`: 研究或自动化技能。
- `scripts/`: 安装和运行辅助脚本。

## 后端内部结构

`backend/src/` 下的目录有很明确的职责分层：

- `config`
- `db`
- `middleware`
- `routes`
- `services`
- `mcp`
- `ws`
- `utils`

这种 Express 单体 + services 的方式非常适合业务快速扩展。

## 4. 后端能力地图

`backend/src/services/` 的服务列表非常长，而且几乎直接对应产品能力：

- 论文与文档: `document.service.js`, `pdf.service.js`, `citation.service.js`, `tag.service.js`
- 自动阅读: `auto-reader.service.js`, `reader.service.js`, `ai-edit.service.js`
- 追踪器: `paper-tracker.service.js`, `rss-tracker.service.js`, `scholar-tracker.service.js`, `alphaxiv-tracker.service.js`, `twitter-tracker.service.js`, `twitter-playwright-tracker.service.js`
- 外部模型与代理: `codex-cli.service.js`, `claude-code.service.js`, `gemini-cli.service.js`, `llm.service.js`
- 远程工作流: `aris.service.js`, `ssh-auth.service.js`, `ssh-transport.service.js`, `arisProjectFiles.service.js`
- 平台扩展: `mcp-paper-library.service.js`, `session-mirror.service.js`, `research-pack.service.js`
- 存储与队列: `s3.service.js`, `queue.service.js`, `scheduler.service.js`

这说明 Amadeus 的产品能力不是松散拼接，而是围绕研究工作流拆成了稳定的 service 层。

## 5. 前端形态

前端使用 `Next.js 14`，并具备 `app/`、`src/`、`public/`、`scripts/` 等结构，说明它采用的是现代 React Web 应用模式。

从依赖和 README 可以判断，前端承担的核心体验包括：

- Latest Feed
- Paper Library
- ARIS Workspace
- Tracker Admin
- Markdown / Mermaid / KaTeX 阅读结果展示
- 终端/远程工作流面板

这类 UI 更像一个研究控制台，而不是单一页面应用。

## 6. 多端入口

## Chrome Extension

`chrome-extension/manifest.json` 显示它的浏览器扩展负责：

- 在网页上提取论文或 PDF 资源。
- 将页面发送到本体服务中保存。
- 处理研究资料“从网页进入知识库”的最短路径。

这是 Amadeus 最有价值的入口之一，因为它真正把论文输入前移到了用户浏览场景。

## VS Code Extension

`vscode-extension/package.json` 显示它提供：

- Tracked Papers
- Library
- ARIS Projects
- ARIS Runs
- 一组围绕论文与运行任务的命令

这说明 Amadeus 不把“研究工作流”限制在浏览器中，而是在 IDE 中也开放入口。

## 7. 数据与基础设施判断

从依赖和 README 可以推断，Amadeus 采用的是复合式基础设施：

- 元数据层倾向 LibSQL/Turso/SQLite。
- 文件层采用 S3/MinIO/兼容对象存储。
- 自动追踪依赖邮件、RSS、Twitter/X、Scholar 等多源输入。
- 远程执行依赖 SSH、终端代理和浏览器端会话镜像。

这让它成为一个典型的“研究平台”，而不是轻量本地工具。

## 8. 关键业务链路

## 输入链路

- Scholar、RSS、Twitter/X、AlphaXiv 等来源持续输入。
- 聚合到 Tracker Feed。
- 用户将候选论文保存进 Library。

## 阅读链路

- 论文进入 Library。
- 触发多轮自动阅读。
- 生成 Markdown、Mermaid、KaTeX 等可沉淀内容。
- 可继续导出到 Obsidian 等知识库。

## 远程执行链路

- 配置 ARIS project 与远程 SSH 目标。
- 发起远程研究运行。
- 观察日志、状态和结果。
- 通过 Web 或 VS Code 继续跟进。

## 9. 对 ResearchOS 的参考价值

Amadeus 对 ResearchOS 的参考点非常明确：

- 输入端: 它证明“持续追踪”比“临时搜索”更适合科研产品。
- 资产端: 它把 AI 阅读结果当作长期资产，而不是一次性回答。
- 工作流端: 它把远程研究执行、SSH、终端代理、扩展入口都纳入产品主线。
- 平台端: Chrome Extension 和 VS Code Extension 说明研究平台可以有多个高价值入口。

如果 ResearchOS 后续要增强论文源追踪、远程工作流和多端协作，Amadeus 是最直接的业务参考样本之一。
