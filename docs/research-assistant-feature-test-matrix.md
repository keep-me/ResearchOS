# Research Assistant Feature Test Matrix

更新时间：2026-04-07

本矩阵覆盖当前 ResearchOS 研究助手前端显式功能、`claw` 后端主链路、ResearchOS MCP 工具、工作区/SSH、技能模板与图表引用逻辑。

## 1. 会话与模型配置

| ID | 场景 | 预期 |
| --- | --- | --- |
| A-01 | 新建研究助手会话 | 默认后端为 `claw`，可正常发送消息并流式返回 |
| A-02 | 切换模型 provider | Anthropic、OpenAI、Gemini、智谱、Qwen、Kimi、MiniMax、自定义均可完成一次基础问答 |
| A-03 | 切换主模型 | 下一轮请求实际使用新模型 |
| A-04 | 切换推理档位 | 请求体与后端运行态中的 `reasoning_level` 一致 |
| A-05 | 切换权限预设 | 只影响代码/工作区类行为，不阻断 ResearchOS 论文主工具 |
| A-06 | 切换模式 | `general`、`build`、`plan` 的系统提示与行为一致 |
| A-07 | 模型角色配置 | 粗读、精读、视觉、fallback 角色保存成功，后端可读取最新值 |

## 2. MCP 与集成面板

| ID | 场景 | 预期 |
| --- | --- | --- |
| M-01 | 打开 Agent 页 MCP 面板 | 显示 “Claw MCP”，不再出现“连接 researchos”按钮 |
| M-02 | 打开设置页 MCP 区块 | 文案明确 “ResearchOS 内置 MCP 会在对话时自动注入到 Claw” |
| M-03 | 内置 ResearchOS 可用 | 面板显示 “内置可用”，并展示工具数量 |
| M-04 | 内置 ResearchOS 异常 | 面板显示异常状态，不把异常表述成“未连接” |
| M-05 | 新增自定义 stdio MCP | 保存成功，配置可在列表中看到 |
| M-06 | 新增自定义 http MCP | 保存成功，配置可在列表中看到 |
| M-07 | 编辑自定义 MCP | 列表和配置读取结果同步更新 |
| M-08 | 删除自定义 MCP | 自定义配置消失，不影响内置 ResearchOS |
| M-09 | 自定义 MCP 生效 | 下一次研究助手会话启动时，自定义 MCP 被写入当前工作区 `.claw/settings.local.json` |
| M-10 | 旧工作区含重复 `researchos` 配置 | 重新启动会话后自动清理，只保留一份内置 `ResearchOS` MCP 项 |

## 3. 技能模板 / Skills

| ID | 场景 | 预期 |
| --- | --- | --- |
| S-01 | 打开 Skills 面板 | 文案强调“辅助流程模板”，不是核心功能来源 |
| S-02 | 关闭全部 Skills | 论文导入、粗读、精读、三轮分析、图表分析仍然可用 |
| S-03 | 显式启用论文相关 Skill | 只增强流程提示和输出组织，不改变底层工具结果结构 |
| S-04 | 未启用任何 Skill | 系统提示中不再默认注入全部本地 skills 列表 |
| S-05 | mounted paper 会话 | 不再因为挂载论文自动注入 paper skills |

## 4. 论文导入与挂载

| ID | 场景 | 预期 |
| --- | --- | --- |
| P-01 | `paper_import_arxiv` 导入 | 论文记录创建成功，可选下载 PDF |
| P-02 | `paper_import_pdf` 导入 | 本地 PDF 被复制入库，标题/摘要尽可能抽取 |
| P-03 | 导入后挂载到当前会话 | 研究助手不再要求重复上传 PDF |
| P-04 | 同 arXiv 再次导入 | 更新已有记录，不重复创建 |
| P-05 | 仅挂载不分析 | 系统先读取已有 detail / analysis，不自动重跑 |

## 5. 论文阅读与分析

| ID | 场景 | 预期 |
| --- | --- | --- |
| R-01 | 请求“粗读这篇论文” | `skim_paper` 直接返回可渲染结果，不再只回 `task_id` |
| R-02 | 请求“精读这篇论文” | `deep_read_paper` 直接返回可渲染结果 |
| R-03 | 请求“三轮分析这篇论文” | 优先复用 `get_paper_analysis`；不足时 `analyze_paper_rounds` 直接返回完整结构化结果 |
| R-04 | 请求“推理链分析” | `reasoning_analysis` 直接返回 `reasoning_steps`，前端可渲染 |
| R-05 | 请求“生成综述” | `generate_wiki` 返回最终结果，而不是仅返回后台任务编号 |
| R-06 | 请求“生成研究简报” | `generate_daily_brief` 返回结果并保存历史 |
| R-07 | 请求“向量嵌入” | `embed_paper` 返回完成结果 |
| R-08 | 请求“研究空白/未来方向” | `identify_research_gaps` 返回结构化分析结果 |

## 6. 图表、原图引用与去重

| ID | 场景 | 预期 |
| --- | --- | --- |
| F-01 | 请求“解释某论文架构图” | 优先读取 `paper_figures` |
| F-02 | 本地无图卡 | 调用 `analyze_figures`，并直接返回图表结果 |
| F-03 | 图表问题回答 | 文本里显式写出“原图依据：Figure X（p.Y）” |
| F-04 | 同轮已有图表卡片 | `get_paper_analysis` 不重复渲染“关联图表 N 项” |
| F-05 | 图表问题 | 不为了补图而额外调用 `get_paper_analysis` |
| F-06 | `analyze_figures` + `get_paper_analysis` 同轮 | 原图只展示一次，引用信息保留 |
| F-07 | SigLIP 2 架构图回归 | 只出现一组原图引用，不出现重复 6 项关联图表 |

## 7. 论文库与研究库

| ID | 场景 | 预期 |
| --- | --- | --- |
| L-01 | `search_papers` | 返回本地论文库候选 |
| L-01b | `paper_search` | legacy 检索入口也能稳定返回候选，不出现 ORM 会话错误 |
| L-02 | `get_paper_detail` | 展示标题、摘要、已粗读/已精读/已三轮分析/图表数量等状态 |
| L-03 | `get_paper_analysis` | 返回三轮分析结构和 `figure_refs` |
| L-04 | `get_similar_papers` | 返回相似论文列表 |
| L-05 | `get_citation_tree` | 返回引用树并正常渲染 |
| L-06 | `get_timeline` | 返回时间线数据 |
| L-07 | `list_topics` | 返回文件夹与自动订阅 |
| L-08 | `manage_subscription` | 可开关订阅并更新频率/时间 |
| L-09 | `search_literature` | 支持外部文献检索 |
| L-10 | `search_arxiv` / `ingest_arxiv` | 检索与入库链路完整 |
| L-11 | `ingest_external_literature` | 外部条目可批量入库 |
| L-12 | `get_system_status` | 系统状态可读 |

## 8. 工作区、SSH 与终端

| ID | 场景 | 预期 |
| --- | --- | --- |
| W-01 | 本地工作区 | 文件树、读取、保存、diff、终端均可用 |
| W-02 | SSH 服务器新增 | 保存成功并可测试连接 |
| W-03 | SSH 测试连接 | 显示成功/失败消息 |
| W-04 | SSH 工作区会话 | `workspace_server_id` 生效，执行模式为远端 |
| W-05 | SSH 工作区下研究助手 | 仍可同时调用 ResearchOS 论文工具和远端 workspace 工具 |
| W-06 | 终端抽屉 | 可创建会话、切换会话、收发输出 |
| W-07 | 远端 `.claw/settings.local.json` | 自动注入内置 ResearchOS MCP 与自定义 MCP 配置 |
| W-08 | 已存在旧版重复 MCP 配置的工作区 | `.claw/settings.local.json` 中旧的 `researchos` 重复项会被清理 |

## 9. 前端显式交互与状态

| ID | 场景 | 预期 |
| --- | --- | --- |
| U-01 | 顶部模型/推理/权限/模式/目标 | 切换后 UI 与请求参数同步 |
| U-02 | 切换目标论文 | `mounted_paper_ids` 与 `mounted_primary_paper_id` 正确更新 |
| U-03 | 工具卡片标签 | 新工具和旧任务型工具标签不再混淆，旧 `paper_*` 标签明确为任务型 |
| U-04 | `skim_paper` / `deep_read_paper` 卡片 | 显示摘要、创新点、方法等内容 |
| U-05 | `analyze_paper_rounds` 卡片 | 展示结构化分析内容，必要时展示原图引用 |
| U-06 | `analyze_figures` / `paper_figures` 卡片 | 展示图表画廊 |
| U-07 | `reasoning_analysis` 卡片 | 展示推理链步骤 |
| U-08 | 错误场景 | toast 和卡片信息准确，不再用 “MCP 未连接” 解释内置工具不可用 |

## 10. 回归重点

- `claw` 对话可用，但 ResearchOS 论文动作工具失效：必须回归 `skim_paper`、`deep_read_paper`、`analyze_paper_rounds`、`analyze_figures`
- MCP 面板不再出现“researchos disconnected / 手动连接 researchos”
- 自定义 MCP 配置保存后，能被 `claw` 实际读取
- 旧工作区若残留重复的 `researchos` MCP 项，重新进入会话后会自动清理
- Skills 全关后，论文主能力仍完整可用
- 图表引用逻辑保持“原图优先、去重渲染、显式原图依据”
