# ResearchOS vs ARIS 功能对照矩阵

最后更新：2026-03-19

参考源码：
- `D:\Desktop\Auto-claude-code-research-in-sleep-main\README.md`
- `D:\Desktop\Auto-claude-code-research-in-sleep-main\README_CN.md`
- `skills/paper-plan`
- `skills/paper-figure`
- `skills/paper-write`
- `skills/paper-compile`
- `skills/auto-paper-improvement-loop`
- `skills/monitor-experiment`

## 对齐结果

| 能力 | ARIS 语义 | ResearchOS 当前状态 | 结论 |
|---|---|---|---|
| `paper_plan` | claims-evidence matrix、section plan、figure plan、citation scaffolding | 已生成 `reports/PAPER_PLAN.md` 和 metadata | 已对齐 |
| `paper_figure` | 图表计划、LaTeX include、对比表/图表清单 | 已生成 `figures/FIGURE_PLAN.md`、`latex_includes.tex`、tables、manifest | 已对齐 |
| `paper_write` | 逐 section 写稿，输出 LaTeX 工作区 | 已生成 `paper/main.tex`、`sections/*.tex`、`references.bib` | 已对齐 |
| `paper_compile` | 编译并汇总 PDF / 日志 / 校验结果 | 已支持显式 compile command，且会自动探测 `latexmk / pdflatex / bibtex` 生成默认编译命令，写回 compile report、日志和 PDF 路径 | 已对齐 |
| `paper_improvement` | 两轮评审、修订记录、格式检查 | 已生成两轮 review/revise/compile 产物，落 round1 / round2 / revision / score progression / format check / round PDF 快照 | 已对齐 |
| `paper_writing` | 一键跑完整论文工作区产物链 | 原生 workflow 会直接物化论文目录与报告 | 已对齐 |
| `research-lit` 项目级上下文 | 项目 workflow 可继承多源文献扫描结果 | 已聚合项目关联论文、ResearchOS 论文库检索、工作区 `papers/` / `literature/` PDF 扫描，以及按需启用的 arXiv 候选 | 已对齐 |
| `idea_discovery` 阶段编排 | literature survey → idea generation/pilots → novelty → review → `IDEA_REPORT.md` | 已补齐为 5 段原生 workflow，并支持前两段 checkpoint 恢复 | 已对齐 |
| `auto_review_loop` 状态持久化 | `AUTO_REVIEW.md` + `REVIEW_STATE.json` compact recovery | 已在原生 workflow 中持久化两个文件、稳定 `threadId`，并回写阶段产物 | 已对齐 |
| `auto_review_loop` 的 `almost` checkpoint | reviewer 给出 `almost` 时可暂停等待人决定继续或停止 | 已支持在 verdict=`almost` 且启用人工确认时进入 `paused`，批准后从下一轮恢复 | 已对齐 |
| `research-pipeline` Gate 1 产物 | Stage 1 输出 `IDEA_REPORT.md`，进入实现前有明确 Gate | `full_pipeline` 的 Gate 1 现已输出 `IDEA_REPORT.md`，并保留阶段 checkpoint | 已对齐 |
| prompt / toolchain 模板 | 各 workflow 使用 reference skill 中的 prompt 模板与 allowed-tools 约束 | 兼容层、原生 runner、多智能体 runner、后续动作服务都已统一从 ARIS `SKILL.md` 读取 | 已对齐 |
| `sync_workspace` | 工作区差异分析、真实同步、结果校验 | 已支持本地到本地、本地到 SSH、SSH 到本地、SSH 到 SSH，并写回 manifest / validation | 已对齐 |
| `monitor_experiment` | 工作区监控、日志/指标采集、进度简报 | 已采集 tree、runtime、候选信号文件、日志摘录、`screen` 会话、hardcopy 输出、GPU 状态、GPU lease 状态，支持多 session 聚合，并结构化识别结果文件 / TensorBoard / W&B / checkpoint / delta 对比 | 已对齐 |
| `run_experiment` 远程执行语义 | `screen` 后台会话 + 隔离工作区 + GPU 绑定 + lease 协调 | 已通过 `screen` 启动远程实验，持久化 session/worktree 元数据，自动注入 `CUDA_VISIBLE_DEVICES`，避让其他活动 lease，并支持批量实验 fan-out/fan-in | 已对齐 |
| `CLAUDE.md` 环境激活语义 | ARIS 会从 `CLAUDE.md` 读取 SSH / conda / activate / code dir 细节驱动实验环境 | `run_experiment / auto_review_loop / full_pipeline` 已读取 `Activate:` / `Conda env` / `Code dir`，并在本地与远程执行链路自动拼接激活命令、解析实际执行目录、持久化 `effective_execution_command / runtime_environment / execution_workspace` | 已对齐 |
| 任务中心持久化 | 页面关闭或后端重启后仍可见 | 已持久化到 `tracker_tasks`，重启后可查看历史 | 已对齐 |
| 运行与后续动作重试 | 任务中心可重新触发 run / action | 已通过持久化 retry metadata 重新提交项目运行/动作 | 已对齐 |

## 专项核销项

| 能力 | ARIS | ResearchOS 当前状态 | 说明 |
|---|---|---|---|
| 论文改进评分 | ARIS adapter 只解析 reviewer 明确给出的 `score / verdict / action_items` | 已改为共享 ARIS 风格解析器；单智能体与多智能体 `paper_improvement` 都不再按 verdict 关键词猜分，无分数时保持 `N/A`，并把 verdict / action items 写入 `paper-score-progression.md` 与 `improvement-metadata.json` | 已对齐 |
| 人机 checkpoint / 通知 | `AUTO_PROCEED=false` 时在关键阶段等待审批，并可发推送或交互式通知 | 已支持项目运行前 checkpoint、`paused` 任务态、批准继续 / 拒绝取消、SMTP 邮件通知；active workflows 已覆盖阶段内 checkpoint 与恢复执行：`literature_review / idea_discovery / novelty_check / research_review / run_experiment / auto_review_loop / paper_plan / paper_figure / paper_write / paper_compile / paper_writing / paper_improvement / full_pipeline / monitor_experiment / sync_workspace`，其中 `auto_review_loop` 还支持 `almost` 分支暂停；飞书 `push` 与 `interactive bridge` 已接通，可在设置页保存/测试，并自动处理 approve / reject 回执；timeout 支持 `approve / reject / wait` | 已对齐 |

## 仍缺失的 ARIS 非核心项

这些没有纳入本轮 T4-T7 的强制完成范围，但已明确为源码差异：

| 差异项 | ARIS | ResearchOS 当前状态 |
|---|---|---|
| 多 AI 引擎矩阵 | ARIS 通过多 CLI / 多模型入口切换不同执行与审稿引擎 | 已改为基于已保存 LLM 配置派生真实 `engine profiles`，项目运行/阶段追踪/后续动作都能直接绑定不同 provider/model，无 CLI 依赖 |
| VS Code companion | 编辑器内运行管理 | 当前已补 `projects companion overview / companion snapshot` 后端聚合 API，并在项目工作区加入 Web companion 聚合面，可直接查看项目、运行、任务、会话、最近消息、工作区健康与 ACP 摘要；但尚无真正的 VS Code 扩展壳层 | 
| ACP 权限交互 | ARIS 原生主链路不依赖 ACP；若引入外部 agent bridge，权限确认通常由 bridge 或宿主接管 | ACP 自定义聊天链路现已同时支持 `stdio` 与 `http` transport 的权限暂停/确认/恢复：研究助手选择 `custom_acp` 时，`session/request_permission` 会转成前端确认卡，批准/拒绝后继续同一 ACP prompt | 已对齐 |
| 跨项目 GPU 锁 | 多个实验可共享同一台服务器并避免撞卡 | 已支持持久化 GPU lease、陈旧锁回收和跨项目自动避让 | 已对齐 |

## 本轮结论

- T4/T5/T6/T7、远程 `screen/worktree`、GPU 调度/lease、批量实验 fan-out/fan-in、remote-to-remote sync、结构化实验监控已补齐。
- 项目运行前 checkpoint、阶段内 checkpoint、恢复执行与飞书双向审批闭环已经接通；ResearchOS 通过结构化 `auto_proceed` 字段实现了 active workflows 的统一语义，形态不是 ARIS CLI 环境变量，但执行效果已对齐。
- 与 ARIS 的剩余差异目前主要集中在一块：VS Code companion 的独立客户端壳层。
- 当前如继续做 ARIS 生态层复刻，优先级应为：
  1. VS Code companion 前端壳层 / 扩展实现
