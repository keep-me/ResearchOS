# Amadeus 技术路线

## 1. 项目定位

Amadeus 的定位非常明确：一个面向个人研究者的 Web 化 AI 研究助手，核心不在“本地桌面”，而在“持续获取论文 + 自动阅读 + 远程研究执行”。

它的能力主线分成四块：

- Latest Feed：持续追踪外部论文源。
- Library：个人论文库与笔记库。
- ARIS：远程自治研究工作流。
- Sessions：会话镜像与协作执行面板。

这个项目的价值在于，它不是把论文管理和 AI 阅读割裂开，而是把“输入、阅读、远程执行”做成了闭环。

## 2. 技术栈与目录结构

## 前端

- Next.js 14
- React 18
- Radix Themes
- 也保留了 Vite 启动脚本，说明前端曾考虑双运行模式

## 后端

- Node.js + Express
- S3/MinIO 兼容对象存储
- `@libsql/client`，适合 Turso / SQLite 路线
- Playwright / Puppeteer / IMAP / OAuth 等外部集成
- WebSocket 终端代理

## 核心目录

- `frontend/app/`：Next App Router 壳层。
- `frontend/src/components/`：页面核心组件，主 UI 实际高度集中在这里。
- `backend/src/routes/`：路由层。
- `backend/src/services/`：系统主要能力都在 service 层。
- `backend/src/ws/`：终端代理。
- `backend/src/mcp/`：MCP Server。

## 3. 系统分层判断

## 3.1 Web 前端是单控制台式架构

`frontend/src/App.jsx` 是典型的“单大壳 + 顶部模式切换 + 中间内容区 + 大量 modal”架构：

- `latest`
- `library`
- `aris`
- `sessions`

这种结构的优点是：

- 用户学习成本低。
- 整个产品心智非常统一。
- Feed、Library、Remote Run 在同一上下文里切换非常顺滑。

它的缺点也明显：

- `App.jsx` 体量大，状态密度很高。
- 当功能再继续增长时，状态与副作用会难以维护。

## 3.2 后端是服务导向单体

Amadeus 后端很典型：

- `routes/` 只暴露 API。
- `services/` 承担核心业务逻辑。
- `middleware/` 处理认证与响应封装。
- `db/` 提供存储访问。

服务目录非常能说明它的产品主线：

- `document.service.js`
- `auto-reader.service.js`
- `reader.service.js`
- `paper-tracker.service.js`
- `rss-tracker.service.js`
- `scholar-tracker.service.js`
- `aris.service.js`
- `ssh-transport.service.js`
- `research-pack.service.js`

这说明它不是单纯“读论文”，而是把论文输入、阅读、打包导出、远程执行都做成服务组合。

## 4. 关键业务链路

## 4.1 论文输入链路

- Semantic Scholar、Google Scholar 邮件、RSS、Twitter/X、AlphaXiv 等来源。
- 统一进入 tracker service。
- 汇总到 Latest Feed。
- 用户可以一键保存进入 Library。

这是 Amadeus 最值得借鉴的地方：输入端是“持续情报系统”，不是一次性搜索。

## 4.2 阅读链路

- 论文进入 Library。
- 触发多轮 AI 阅读。
- 输出 Markdown、Mermaid、KaTeX、图示化结果。
- 支持导出到 Obsidian。

它把“阅读结果”当成长期知识资产，而不是一次性聊天答案。

## 4.3 ARIS 远程工作流链路

- 本地定义项目、工作区、远程 SSH 目标。
- 远程执行研究任务。
- 跟踪运行状态、日志和结果。
- 支持浏览器端查看会话与文件状态。

这条路线非常适合高强度研究用户，也解释了为什么它需要对象存储、SSH、终端代理、MCP。

## 5. 前端 UI 布局路线

从截图和 `App.jsx` 可以看出，Amadeus 的前端布局遵循以下策略：

### 1. 顶部主导航切换模式

- Latest
- Library
- ARIS
- Sessions

这相当于把“产品模式”放在第一层，而不是把所有功能都塞进侧栏。

### 2. 内容区强调卡片流

- Latest Feed 使用多卡片网格。
- Library 走大列表 + 搜索 + 筛选。
- ARIS 和 Sessions 更偏操作控制台。

### 3. 运维动作放在页头右侧

- Tracker
- Servers
- Settings
- Auth/User

这个布局很适合“Web 控制台”，因为它更强调远程系统管理感。

## 对本项目可借鉴的 UI 点

- `collect` 页可以借鉴 Latest Feed 的卡片化输入视图。
- `papers` 页可以借鉴 Library 的搜索、标签、批量操作面板。
- `operations / pipelines` 可以借鉴其管理型头部动作布局。

## 6. 工程化与部署路线

Amadeus 的部署并不是简单的前后端启动，而是从一开始就考虑了两种运行模式：

- All-in-one
- Proxy + Local Device

这带来几个工程特点：

- 对象存储必须解耦本地文件。
- 前端和后端必须适合跨域代理与 HTTPS 反向代理。
- AI CLI 和重任务更偏向在个人设备运行，而不是全放云端。

这条路线的核心思想是：便宜的云机负责入口，自有设备负责算力和模型调用。

## 7. 如果把 Amadeus 当参考项目，应该吸收什么

## 最优先吸收

- Feed/Library/Tracker 的业务闭环。
- 对象存储 + 文档下载链接的思路。
- 远程研究执行和 SSH 节点建模。

## 次优先吸收

- Obsidian 导出链路。
- MCP 暴露论文库。
- 多来源 tracker 抽象。

## 不建议直接照搬

- 单文件超大前端壳状态管理。
- 以顶部多 Tab 单体页为核心的组织方式。

原因是 ResearchOS 已经走上多页面 + 桌面壳路线，更适合逐页分模块治理，而不是重新合并成单大页。

## 8. 最终路线判断

Amadeus 的技术路线适合做“远程研究控制台”和“持续论文情报系统”。它对 ResearchOS 的最大价值，不在 UI 视觉，而在以下三点：

- 如何把论文输入做成持续系统。
- 如何把阅读结果沉淀成可导出的知识资产。
- 如何把远程工作流接到研究助手能力上。
