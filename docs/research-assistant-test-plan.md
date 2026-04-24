# Research Assistant Test Plan

## Goal

验证 `ResearchOS` 研究助手是否具备类 opencode / codex 的核心能力，并确认它与 `ResearchOS` 论文能力、项目级 skills、MCP 工具链已经打通。

## Scope

- 通用 Agent 能力
  - 工具路由是否正确
  - 网页搜索
  - 本地 skills 发现与读取
  - 工作区读写
  - 工作区命令执行
  - 后台任务状态查询
- ResearchOS 集成能力
  - 本地论文库概览
  - arXiv 外部论文检索
  - 论文详情上下文读取
  - 重型论文任务仅在显式请求时暴露
  - 项目级 skills 可被研究助手发现
  - MCP 服务可同时提供网页搜索与论文工具

## Automated Smoke Checks

执行脚本: [scripts/agent_smoke_check.py](/D:/Desktop/ResearchOS/scripts/agent_smoke_check.py)

覆盖项:

1. 工具路由
   - 通用网页检索请求应暴露 `search_web`，且不暴露论文重型工具
   - 论文检索请求应暴露 `search_papers` / `search_arxiv`
   - 显式精读/推理链请求才暴露 `deep_read_paper` / `reasoning_analysis`
2. 网页搜索
   - `search_web("openclaw github")` 应返回至少 1 条结果
3. 本地 skills
   - 应发现项目级 skills:
     - `research-os-paper-workflows`
     - `research-os-web-research`
   - 应能读取 `SKILL.md`
4. 工作区工具
   - 写入文件
   - 读取文件
   - 定点编辑文件
   - 列目录
   - 前台执行命令
   - 后台执行命令并轮询任务状态
5. MCP / ResearchOS 工具
   - `researchos_mcp.web_search` 可调用
   - `researchos_mcp.search_arxiv` 可调用
   - `paper_library_overview` 可返回结果
   - 若库内已有论文，则 `paper_detail` 可读取单篇上下文

## Manual UI Checks

建议在助手页面手动验以下交互:

1. 通用搜索
   - 输入“去网上搜一下 openclaw”
   - 预期: 优先走网页搜索，不自动触发论文粗读/精读/图表/推理链
2. skills 使用
   - 输入“当前有哪些 skills”
   - 预期: 能列出全局与项目级 skills
   - 再输入“读取 research-os-paper-workflows”
   - 预期: 能展示 skill 内容摘要
3. 工作区操作
   - 指定一个本地目录后，让助手读取、写入、修改文件并执行简单命令
   - 预期: 步骤卡片中显示读取/修改/执行记录
4. 论文联动
   - 输入“帮我找 Attention Is All You Need 这篇论文”
   - 预期: 先给出本地库或 arXiv 候选，不自动启动精读/推理链
   - 再输入“对这篇论文做精读”
   - 预期: 才启动后台任务，并可在任务列表看到
5. 导入论文上下文
   - 手动导入 1 到多篇论文到当前聊天
   - 预期: 助手回答时优先使用导入论文及其已有分析结果

## Pass Criteria

- 自动 smoke check 全部通过
- 手动 UI 检查无阻塞性错误
- 研究助手能够区分“通用搜索”和“论文分析”两类请求
- ResearchOS 的论文能力可被作为工作流能力稳定调用
