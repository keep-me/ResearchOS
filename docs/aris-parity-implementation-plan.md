# ARIS 对齐实施文档

最后更新：2026-03-20

## 目标与完成定义

- 目标：让 `ResearchOS` 的项目工作区在保持 Web 产品形态的前提下，尽量完整复现 ARIS 的研究流程能力与执行语义。
- 完成标准：
  - 关键 workflow 不再停留在命名或占位状态，前后端都能真实执行。
  - 运行参数中的执行模型、评审模型、阶段角色对后端真实生效。
  - 项目运行、后续动作、任务中心、远程工作区、产物输出可以连成完整闭环。
  - 与 ARIS 的功能差异要持续核销，关键未达项不能保留为“planned”假能力。

## 当前实施表

| 编号 | 任务 | 状态 | 当前目标 | 验收标准 |
|---|---|---|---|---|
| T1 | 项目运行语义真接通 | 已完成 | 打通 `executor_model / reviewer_model / stage role` 的真实后端语义 | 修改前端参数后，后端实际执行模型变化，运行详情能看到每阶段实际模型来源 |
| T2 | `auto_review_loop` 真执行 | 已完成 | 把自动评审循环从预设升级为真实 workflow | 可创建、启动、重试、取消，产生循环评审输出 |
| T3 | `novelty_check / research_review` | 已完成 | 新增查新与研究评审两个一等 workflow | 项目工作区可独立发起，产出结构化报告并写回运行产物 |
| T4 | 论文细粒度流水线 | 已完成 | 补齐 `paper_plan / paper_figure / paper_write / paper_compile / paper_improvement` | 各子流程可单独运行，一键写作可复用子流程产物 |
| T5 | SSH / sync / monitor 统一 | 已完成 | 统一本地与远程路径、同步、监控和产物目录语义 | SSH 目标上运行与显示路径一致，同步和监控可真实执行 |
| T6 | 任务中心持久可见性 | 已完成 | 项目运行与后续动作在关闭页面后仍能在任务中心查看 | 任务、日志、结果、重试、取消对项目运行可持续可见 |
| T7 | ARIS 最终核销与补漏 | 已完成 | 对照 ARIS 逐项核销剩余差异 | 关键能力无未达项，剩余差异仅限非关键体验细节 |
| T8 | 远程 `screen/worktree` 对齐 | 已完成 | 把 SSH 实验执行升级为 ARIS 风格后台会话 + 隔离工作区 | 远程实验通过 `screen` 后台启动，run metadata 持久化会话/隔离目录，`monitor_experiment` 可读取会话状态 |
| T9 | 多 GPU 探测与分配 | 已完成 | 在远程实验启动前自动探测 GPU、选择空闲卡并绑定 | `run_experiment` 会先跑 `nvidia-smi`，按策略选卡并注入 `CUDA_VISIBLE_DEVICES`，监控链路可看到 GPU 状态 |
| T10 | 跨项目 GPU 锁与并发协调 | 已完成 | 让同一台 SSH 服务器上的多个 run 自动避让已占用 GPU | 远程实验启动前会清理陈旧 lease、读取活动 lease、抢占 GPU 槽位；第二个并发 run 不会重复拿到同一块卡 |
| T11 | 批量实验 fan-out/fan-in 编排 | 已完成 | 让单个 `run_experiment` 支持一次提交多实验并分别分配 session / workspace / GPU | 一个 run 可持久化多实验执行计划、分别启动多个 `screen` 会话、避免同 run 内重复占卡，监控链路能读取多 session 状态 |
| T12 | 人机 checkpoint / 通知集成 | 已完成 | 为项目运行补齐可选审批暂停和状态通知 | 创建 run 时可启用关键步骤确认与通知邮箱；run 会进入 `paused` 等待审批，批准后继续、拒绝则取消，并发送状态通知 |
| T13 | 阶段内 checkpoint 恢复执行 | 已完成 | 让关键工作流在阶段之间暂停，并在批准后从下一阶段继续 | `full_pipeline / paper_writing` 的关键阶段可暂停等待审批；批准后不会重跑已完成阶段，任务中心与运行详情都能看到阶段确认信息 |
| T14 | 飞书通知配置与交互审批闭环 | 已完成 | 补齐飞书设置页、Webhook 推送和 Bridge 双向审批语义 | 设置页可保存/测试飞书配置；checkpoint 可通过 bridge 自动 approve/reject 并继续驱动运行 |
| T15 | 项目引擎矩阵真接通 | 已完成 | 把项目工作区从伪智能体/硬编码角色切到真实 LLM 引擎档案绑定 | 项目运行可选择已配置 LLM 档案作为执行/评审引擎；阶段 trace 和后续动作会真实显示并使用对应 provider/model |
| T16 | ARIS 模板/编排/工具链细节真对齐 | 已完成 | 把参考 skill prompt 模板、阶段编排、关键产物和工具链要求接到真实后端 | 原生 runner、后续动作和多智能体入口统一使用 ARIS skill 文本；`idea_discovery / paper_writing / auto_review_loop / full_pipeline` 输出 ARIS 标准产物并支持更细粒度 checkpoint |
| T17 | `auto_review_loop` 的 almost-checkpoint 对齐 | 已完成 | 对齐 ARIS 在 verdict=`almost` 时暂停并等待人工决定是否继续 | 第 N 轮评审给出 `almost` 后，运行进入 `paused`；批准后从第 N+1 轮恢复，不重跑前面轮次 |
| T18 | 文献/编译/改稿收尾对齐 | 已完成 | 补项目级多源文献上下文、自动编译探测、两轮论文改进和稳定 thread id | `literature_review / idea_discovery / full_pipeline` 可共享项目论文+论文库+workspace papers/literature+可选 arXiv 候选；`paper_writing` 默认自动探测 LaTeX 工具链并产出 round0/1/2 PDF 与状态文件；`auto_review_loop / paper_improvement` 的 `threadId` 不再为空 |
| T19 | `paper_improvement` 评分语义对齐 | 已完成 | 改成 ARIS 风格显式解析 `score / verdict / action_items`，移除关键词猜分 | 单智能体与多智能体 `paper_improvement` 均只接受显式 score；无分数时保持 `N/A`，并把 verdict / action items 写入 score progression 与 improvement metadata |
| T20 | 项目运行报告统一呈现 | 已完成 | 把剩余 workflow 的运行结果统一归一成正式报告，而不是散乱阶段原文 | `paper_plan / paper_figure / paper_write / paper_compile / paper_improvement / monitor_experiment / sync_workspace / custom_run` 在运行详情和主报告预览中都输出统一结构化报告 |
| T21 | ARIS 回归检查产品化 | 已完成 | 把脚本级 smoke 回归接入产品任务中心，支持前端触发和结果查看 | 任务中心可直接启动快速/完整 ARIS 回归；结果、日志、报告路径和重试入口可持久查看 |

## 当前开发阶段

### T4-T7 收尾状态

- 当前已完成：
  - `paper_plan / paper_figure / paper_write / paper_compile / paper_improvement` 均会落标准化工作区产物。
  - `paper_writing` 原生 workflow 会直接生成 `PAPER_PLAN.md`、`latex_includes.tex`、`paper/main.tex`、`references.bib` 等标准文件。
  - `paper_improvement` 已改为 ARIS 风格显式 review 解析：只解析 reviewer 明确给出的 `Score`，不再根据 `ready / almost / weak accept / reject` 等词做保守估分，同时会持久化 verdict 和 action items。
  - `sync_workspace` 会执行真实本地/远程统一同步，已覆盖本地到本地、本地到 SSH、SSH 到本地、SSH 到 SSH，并生成同步清单、同步报告、校验报告。
  - `monitor_experiment` 会读取工作区状态、日志摘录、`screen` hardcopy、多会话状态，并结构化识别 `results/metrics/eval`、TensorBoard、W&B summary、checkpoint 等实验信号，回写监控报告与信号清单。
  - `TaskTracker` 已改为“内存运行态 + DB 持久态”，支持服务重启后查看历史任务、结果和日志。
  - ARIS 对照矩阵已拆到独立文档：`docs/aris-feature-gap-matrix.md`。

### T8 远程基础设施补齐状态

- 当前已完成：
  - `run_experiment` 在 SSH 工作区上不再前台阻塞执行，而是改为 `screen` 后台启动。
  - 远程 run 会预先规划并持久化 `remote_session_name / remote_execution_workspace / remote_isolation_mode`。
  - 远程实验执行目录改为隔离工作区语义：优先 `git worktree`，随后覆盖当前工作树状态；若不可用则回退为复制型隔离目录。
  - `monitor_experiment` 会额外采集远程 `screen` 会话列表和 hardcopy 输出，而不只是扫目录与日志文件。

### T9 GPU 调度补齐状态

- 当前已完成：
  - 远程 `run_experiment` 会在启动前自动执行 `nvidia-smi` 预检。
  - 默认策略为 `least_used_free`，默认空闲阈值为 `500 MiB`，并支持通过 run metadata 覆盖 `gpu_mode / gpu_strategy / preferred_gpu_ids / allow_busy_gpu`。
  - GPU 分配结果会写入 run metadata、任务中心元数据和远程启动报告，并注入 `CUDA_VISIBLE_DEVICES`。
  - `monitor_experiment` 会额外回传 GPU 清单与显存占用状态。

### T10 GPU lease 协调状态

- 当前已完成：
  - 新增持久化 GPU lease 表，用于记录“某台 SSH 服务器的某块 GPU 当前被哪个 run/session 占用”。
  - `run_experiment` 启动前会先对远程 `screen` 会话做一次 reconcile，清理陈旧 lease。
  - 新运行会在选卡时避开其他活动 lease，并在启动成功后保留锁，直到后续监控或下一次 reconcile 释放。
  - `monitor_experiment` 会返回当前活动 lease 和本次释放的陈旧 lease。

### T11 批量实验编排状态

- 当前已完成：
  - `run_experiment` 新增 `parallel_experiments / experiment_matrix` 解析，可将单个 run 展开为多项远程实验执行计划。
  - 每个实验项都会独立规划 `screen` 会话名、隔离工作区目录和日志路径，并分别执行 `remote_prepare_run_environment`。
  - GPU 分配已纳入同 run 内部避让，不会因为 lease 属于同一个 run 而重复占用同一块卡。
  - 启动结果会聚合写回 `remote_experiments / remote_session_names / remote_launch_failures / execution_result.batch_experiments`。
  - `monitor_experiment` 已支持从 run metadata 中读取多 session 并统一展示。

### T12 人机 checkpoint / 通知集成状态

- 当前已完成：
  - 项目运行创建接口已支持 `human_checkpoint_enabled` 和 `notification_recipients`。
  - 当启用人工确认且尚未批准时，run 会在真正执行前进入 `paused`，任务中心同步为暂停态并保留待审批信息。
  - 前端项目页已提供“启用关键步骤确认”和“通知邮箱”配置，并在运行卡片上展示审批卡片，可直接批准或拒绝。
  - 批准后会重新提交同一 run 继续执行；拒绝则取消 run，并同步写入任务日志。
  - 邮件通知已接入现有 SMTP 配置，支持 checkpoint 待审批、执行成功、失败、取消、拒绝等事件。

### T13 阶段内 checkpoint 恢复执行状态

- 当前已完成：
  - `full_pipeline` 已支持在 `review_prior_work` 结束后暂停，批准后从 `implement_and_run` 继续，不会重复生成相关工作阶段产物。
  - `paper_writing` 已支持在 `gather_materials` 和 `draft_sections` 结束后暂停，批准后分别从 `draft_sections` 和 `polish_manuscript` 继续。
  - 运行 metadata 已持久化 `checkpoint_resume_stage_id / checkpoint_resume_stage_label`，审批后恢复执行由后端真实读取这些字段。
  - 任务追踪器新增 `TaskPausedError` 语义，后台线程可在暂停时保持任务为 `paused`，不再被误记为完成或失败。
  - 前端运行卡片会区分“运行前确认”和“阶段确认”，并展示阶段摘要与下一阶段名称；阶段追踪里会标明“需确认”阶段。

### T14 飞书通知层状态

- 当前已完成：
  - 后端新增 `FeishuConfig` 持久化模型与 `GET/PUT/TEST /settings/feishu-config` 接口。
  - 项目运行状态通知已同时支持 SMTP 与飞书；`paused / approved / rejected / succeeded / failed / cancelled` 都会构造飞书消息。
  - 前端“系统配置 -> 通知与报告”已新增飞书设置卡片，支持 `off / push / interactive`、Webhook、Secret、Bridge URL、超时秒数、超时策略保存与测试发送。
  - `interactive` 已支持后台 bridge 轮询；当收到 `approve / reject` 回执时，会自动走现有 checkpoint 审批链路，继续执行或取消运行。
  - 手动审批和飞书交互审批已复用同一条后端服务路径，避免两套语义分叉。
  - 同一 checkpoint 会通过内存 waiter key 去重，避免重复启动多个 bridge 轮询线程。
  - `interactive` 新增 `timeout_action` 配置，支持 `approve / reject / wait`。其中 `approve` 对齐 ARIS 的 `AUTO_PROCEED` 自动继续语义。

### T15 多引擎矩阵状态

- 当前已完成：
  - 新增项目级 `engine profiles`，直接从系统中已保存的 LLM 配置派生 `deep / skim / fallback / vision` 引擎档案。
  - 项目工作区启动表单已改为选择真实执行引擎 / 评审引擎，不再依赖前端伪智能体名称。
  - 运行 metadata、阶段 trace、后续动作都会保留 `engine_id / engine_label / model_source`，后端真实按所选 provider/model 执行。
  - `LLMClient` 已支持通过引擎档案 ID 解析对应配置，因此即使不是当前 active config，也能被项目运行直接使用。

### T16 ARIS 模板 / 编排 / 工具链对齐状态

- 当前已完成：
  - 新增 `packages/ai/aris_skill_templates.py`，直接读取 ARIS `SKILL.md`，供原生 runner、multi-agent runner、后续动作服务统一复用。
  - `workflow_runner_preamble` 不再依赖手写简化文案，而是动态注入 reference skill 的 description / allowed-tools / body。
  - `idea_discovery` 已对齐为 `文献调研 -> 想法生成与试验 -> 深度查新 -> 外部评审 -> IDEA_REPORT`，并在前两段支持 checkpoint 恢复执行。
  - `paper_writing` 已扩展为 `paper-plan -> paper-figure -> paper-write -> paper-compile -> improvement` 五段流程，并支持多段 checkpoint 恢复。
  - `auto_review_loop` 已持久化 `AUTO_REVIEW.md / REVIEW_STATE.json`；`full_pipeline` 的 Gate 1 输出 `IDEA_REPORT.md`，自动评审阶段输出 `AUTO_REVIEW.md`。

### T17 `auto_review_loop` almost-checkpoint 状态

- 当前已完成：
  - 当 `auto_review_loop` 某轮评审返回 `verdict=almost` 且运行启用了人工确认时，后端会真实进入 `paused`。
  - 当前轮的 `iterations / AUTO_REVIEW.md / REVIEW_STATE.json` 会先写回，再发起 checkpoint。
  - 批准后会从下一轮 `execute_cycle` 恢复，不会重跑 `plan_cycle` 或已完成的 earlier rounds。
  - 新增回归测试覆盖 `almost -> pause -> approve -> round N+1 resume`。

### T18 文献/编译/改稿收尾状态

- 当前已完成：
  - `literature_review / idea_discovery / full_pipeline` 已共享统一的项目级文献上下文聚合，覆盖项目已关联论文、ResearchOS 论文库模糊检索、工作区 `papers/` / `literature/` 本地 PDF 扫描，以及按需启用的 arXiv 候选补充。
  - `paper_writing` 的 `compile_manuscript` 不再只依赖显式命令；当前会优先自动探测 `latexmk / pdflatex / bibtex` 并生成默认编译命令，只有工具链缺失时才回退到人工编译说明。
  - `paper_writing` 的 `polish_manuscript` 已升级为两轮 `review -> revise -> compile`，会落 `paper/main_round0_original.pdf`、`paper/main_round1.pdf`、`paper/main_round2.pdf`、两轮 compile report、revision notes、score progression 与 format check。
  - `auto_review_loop` 与 `paper_improvement` 的状态文件已持久化稳定 `threadId`，不再写死为 `None`。
  - `run_experiment / auto_review_loop / full_pipeline` 已接通 `CLAUDE.md` 的 `Activate:` / `Conda env` / `Code dir` 语义；本地与远程执行都会自动拼接环境激活命令、解析真实命令工作目录，并在 run metadata 中持久化 `effective_execution_command / runtime_environment / execution_workspace`。
  - 已新增回归测试覆盖：多源文献上下文聚合、自动编译探测、round PDF 产物、稳定 `threadId` 持久化，以及本地/远程 `run_experiment`、`auto_review_loop`、`full_pipeline` 的 runtime environment 接线。

### T20 项目运行报告统一呈现状态

- 当前已完成：
  - 新增统一报告格式化层，补齐 `paper_plan / paper_figure / paper_write / paper_compile / paper_improvement / monitor_experiment / sync_workspace / custom_run` 的正式报告输出。
  - `GET /project-runs/{id}` 与主报告文件预览会在读取旧 run 时自动重建结构化报告，不依赖历史数据是否已经按新格式落库。
  - `paper_*` 子流程不再只展示 bundle 原文或阶段摘要，而是统一给出“当前结论 + 正文/产物/评分进展”的报告结构。
  - `monitor_experiment / sync_workspace` 不再直接暴露底层阶段原文，统一收敛成面向用户阅读的监控报告与同步报告。
  - 新增 `scripts/aris_workflow_smoke.py`，可在临时数据库与临时工作区中一键回归 `sync_workspace / monitor_experiment / paper_compile / paper_improvement`。
  - `scripts/aris_workflow_smoke.py` 已继续扩展，新增覆盖 `run_experiment` 远程 batch `screen` 启动、跨 run GPU lease 避让、远程 `monitor_experiment`、`paper_writing`、`full_pipeline`，并把 GPU 分配顺序、远程会话名、round PDF、runtime environment 一并输出。
  - 新增固定入口 `scripts/run-aris-smoke.ps1`，并接入根 `package.json` 的 `npm run smoke:aris / smoke:aris:quick / test:aris`。
  - 新增 GitHub Actions 工作流 `.github/workflows/aris-smoke.yml`，在 Windows runner 上执行同一套 ARIS 回归。
  - 修复 `paper_compile` 的 PDF 产物暴露问题：真实生成的 `paper/main.pdf` 现在会进入 artifact refs，前端文件区可见。
  - 已补充格式化回归测试，覆盖剩余 workflow 的报告输出。

### T21 ARIS 回归检查产品化状态

- 当前已完成：
  - 新增后端 ARIS smoke 执行服务，使用独立子进程运行 `scripts/aris_workflow_smoke.py` 或 `scripts/run-aris-smoke.ps1`，避免污染主服务数据库与运行态。
  - 新增 `POST /jobs/aris-smoke/run-once`，支持 `quick / full` 两种模式，并复用 `TaskTracker` 持久化任务、日志、结果、报告路径和重试入口。
  - 任务中心前端已新增 `ARIS 快速检查 / ARIS 完整回归` 入口，并可直接查看结构化结果摘要与日志输出。
  - 新增产品化回归测试，覆盖结果解析、命令选择和任务落库语义。

### T22 项目工作台减法收口状态

- 当前已完成：
  - `Projects` 页顶部 hero 已继续压缩成“标题 + 一句话说明 + 行内统计 + 当前项目 + 刷新”的摘要栏，不再使用营销页式大卡片和多块统计面板。
  - `Projects` 页项目头部已进一步改成项目名、描述和行内元信息布局，保留主按钮 `在研究助手中打开` + 折叠式 `更多`，不再使用四张指标卡。
  - `启动运行` 已继续压成“工作流切换 + 行内说明 + 基础输入 + 高级选项折叠”结构，移除顶部独立说明卡，默认只保留工作流、目标、标题、任务提示和启动按钮。
  - 高级选项折叠后会显示当前目标、工作流、环境、AUTO_PROCEED、引擎覆盖等摘要标签，避免隐藏后失去上下文。
  - `当前运行` 顶部操作已收敛为 `刷新详情` + 折叠式 `运行操作`，删除类危险动作不再长期暴露。
  - `工作区 companion` 已继续压成“标题 + 一句摘要 + 摘要标签”，展开后保留最新会话、最新任务和 ACP 快照三块；ACP 区不再重复展示最近消息。
  - `部署目标` 已改为“左侧列表 + 右侧当前目标摘要”，右侧把环境摘要、更新时间和附加目录合并到同一块，且不再重复左侧已经展示的目标名、路径、服务器类型和状态。
  - 项目列表卡片、最近运行卡片与项目头部主卡的选中态、悬停态、阴影和顶部光带已统一，整体观感更接近桌面工作台而不是表单页。
  - `当前运行 / 概览` 已进一步改成“左侧主结果，右侧补充信息与关键路径”的阅读路径：正文输出或当前状态优先显示，执行环境和通知改成行内摘要，不再重复头部里的模型与策略信息。
  - `当前运行 / 阶段追踪` 已继续压成更扁的时间线条目：阶段元信息从六宫格收成行内摘要标签，顶部统计也从卡片收成 chip，滚动阅读负担更小。
  - `当前运行 / 后续动作` 已继续压成“左侧创建，右侧记录”的轻列表结构：动作统计从卡片收成 chip，动作记录项缩短头部、摘要和文件区高度，更像日志列表而不是大卡片堆叠。
  - `当前运行` 内明显重复的信息层已再删一轮：顶部 `快速概览` 改成行内统计，`trace/actions` 下方重复的 `详细摘要 + 关键路径` 面板已删除，阶段编号和进度重复展示也已收掉一层。
  - `项目头部 / companion / 部署目标 / 启动运行` 的重复上下文已再合并一轮：项目头部移除工作区路径和运行记录摘要，companion 摘要移除工作区路径标签与模式徽标，部署目标详情改成单行 meta，启动运行里重复的 `启动上下文` 面板已删除。
  - `最近运行 / 当前运行` 的外层容器、圆角、标题字号和间距也已统一压缩，整页信息密度基本收敛到同一层级。
  - 本轮调整不改后端语义，只压缩前端信息密度和操作层级。

## 任务执行日志

### Companion / 外部编辑器聚合面

- 状态：已完成后端聚合 + Web companion 面
- 已完成：
  - 新增 `projects companion overview / companion snapshot` 聚合 API。
  - 外部编辑器现在可通过单个项目快照接口直接拿到项目详情、工作区上下文、运行列表、关联任务、会话预览、最近消息和 ACP 摘要。
  - 已新增路由测试覆盖 companion overview 与 snapshot 聚合输出。
  - 项目工作区前端已接入 companion 概览与单项目快照：左侧项目卡会展示活跃任务与最新运行，右侧主区新增 companion 聚合面，直接展示 ACP 状态、关联任务、会话预览和最近消息。
- 仍未完成：
  - 真正的 VS Code companion 前端壳层 / 扩展入口。
  - 编辑器内项目 / 运行 / 论文库的交互式 UI。

### 本轮追加核对结论

- `auto_proceed / checkpoint` 已在 active workflows 层实现统一语义；ResearchOS 通过结构化 run 字段驱动，不再复刻 ARIS CLI 环境变量形态，但暂停、审批、恢复执行效果已对齐。
- 已覆盖阶段内 checkpoint / resume 的 active workflows：
  - `literature_review`
  - `idea_discovery`
  - `novelty_check`
  - `research_review`
  - `run_experiment`
  - `auto_review_loop`
  - `paper_plan`
  - `paper_figure`
  - `paper_write`
  - `paper_compile`
  - `paper_writing`
  - `paper_improvement`
  - `full_pipeline`
  - `monitor_experiment`
  - `sync_workspace`
- `auto_review_loop` 额外支持 ARIS 风格的 `verdict=almost` 中途暂停，并从下一轮恢复。
- ACP 目前已接通 registry、连接、prompt 执行和 Web companion 摘要；`stdio` 与 `http` 自定义 ACP 聊天都已支持真正的权限暂停/确认/恢复：
  - 研究助手选择 `custom_acp` 时，`session/request_permission` 会统一转成会话确认卡，批准/拒绝后继续同一 ACP prompt
  - 已补齐 service 级与 chat 级回归测试，覆盖 `http` transport 的 pause / confirm / resume 链路
- 因此当前与 ARIS 的剩余关键差异收敛为：
  - VS Code companion 的独立客户端壳层

### T1

- 状态：已完成
- 已完成：
  - 建立 ARIS 对齐唯一文档并落地实施表。
  - 增加 `executor_model` 持久化字段，并打通创建 run / retry run / 前端表单。
  - stage 级别补齐 `model_role`，native runner 与 multi-agent runner 都按 `executor / reviewer` 选模型。
  - `stage_trace` 和 `stage_outputs` 写回 `provider / model / variant / model_role / model_source`。
  - 项目工作区阶段追踪页展示实际模型角色、来源、提供方和模型名。
  - 后续动作服务按动作语义选择执行模型，不再固定走 `reviewer_model`。

### T2

- 状态：已完成
- 已完成：
  - `auto_review_loop` 已具备真实循环执行逻辑。
  - 执行阶段与评审阶段均写回阶段产物和模型元数据。
  - smoke test 已覆盖循环完成状态与产物写回。

### T3

- 状态：已完成
- 已完成：
  - `novelty_check` 与 `research_review` 已作为一等 workflow 接入原生 runner。
  - 运行结果会写回结构化 Markdown 报告与运行产物。
  - router / feature matrix 已覆盖这两个 workflow 的真实执行。

### T4

- 状态：已完成
- 已完成：
  - `paper_plan` 会产出 `reports/PAPER_PLAN.md` 与 plan metadata，包含 claims-evidence matrix、section plan、figure/table plan。
  - `paper_figure` 会产出 `figures/FIGURE_PLAN.md`、`figures/latex_includes.tex`、LaTeX 表格与 figure manifest。
  - `paper_write` 会产出 `paper/main.tex`、`paper/sections/*.tex`、`paper/references.bib` 和 `reports/PAPER_WRITE.md`。
  - `paper_compile` 会产出 `reports/PAPER_COMPILE.md`，并记录 PDF 路径与编译日志。
  - `paper_improvement` 会产出两轮评审、修订记录、score progression 和 format check。
  - `paper_writing` 原生 workflow 会直接物化整套论文目录，而不是只留一份 Markdown 草稿。

### T5

- 状态：已完成
- 已完成：
  - `sync_workspace` 已从“策略说明”升级为真实文件同步，支持本地到本地、本地到 SSH、SSH 到本地、SSH 到 SSH 四类路径。
  - 同步阶段会写回 `sync-report.md`、`sync-manifest.json`、`sync-validation.md`。
  - `monitor_experiment` 会真实读取工作区树、运行时探针、候选日志/指标文件、日志摘录以及 `screen` 输出，并额外结构化汇总 `results.json / metrics.json / eval*.json / wandb-summary.json / events.out.tfevents* / checkpoints/*` 等实验信号。
  - 运行元数据会持续记录工作区路径、同步目标和产物目录，避免显示路径与执行路径割裂。

### T6

- 状态：已完成
- 已完成：
  - 新增 `tracker_tasks` 持久化表。
  - `TaskTracker` 的 start/update/finish/cancel/result/log/retry 元数据都会同步写库。
  - 页面关闭后重新进入仍能查看任务、日志、产物和结果。
  - 服务重启后未完成任务会被明确标记为“因服务重启中断”，不再假装继续运行。

### T7

- 状态：已完成
- 已完成：
  - 新增 `docs/aris-feature-gap-matrix.md`，逐项对照 ARIS README 与关键 skill。
  - 新增任务持久化、论文产物、工作区同步等针对性测试。
  - 回归测试已覆盖 native runner、multi-agent runner、feature matrix、task tracker。

### T8

- 状态：已完成
- 已完成：
  - SSH 实验运行会生成稳定的 `screen` 会话名，并将其写入 run metadata 和任务中心元数据。
  - 远程隔离工作区会优先使用 `git worktree`，并覆盖当前工作树状态以保留未提交改动语义。
  - `monitor_experiment` 已纳入远程会话列表与 hardcopy 日志采集。
  - router / runner / tracker 回归测试已覆盖远程 session/workspace 规划与监控输出。

### T9

- 状态：已完成
- 已完成：
  - 远程实验启动前会探测 GPU 清单，并优先选择空闲且显存占用最低的 GPU。
  - 选中的 GPU 会通过 `CUDA_VISIBLE_DEVICES` 绑定到 `screen` 后台命令。
  - GPU 探测结果与选卡结果会写回 `gpu_probe / selected_gpu` 元数据。
  - `monitor_experiment` 已纳入远程 GPU 状态采集，回归测试覆盖 remote run / remote monitor / router / tracker。

### T10

- 状态：已完成
- 已完成：
  - 新增 `project_gpu_leases` 持久化表与 `project_gpu_lease_service` 服务层。
  - 同一 SSH 服务器上的多个 `run_experiment` 会自动避开已锁定 GPU，不再重复绑定到同一块卡。
  - 远程 `screen` 会话消失时，监控链路和下一次实验启动都会自动回收陈旧 lease。
  - 新增 lease 服务测试和双 run 并发选卡测试，覆盖跨项目 GPU 协调语义。

### T11

- 状态：已完成
- 已完成：
  - 新增批量实验执行计划解析，支持 `parallel_experiments / experiment_matrix` 两类 metadata 入口。
  - 远程 `run_experiment` 会为每个实验项单独准备 worktree/workspace、后台 `screen` 会话、日志文件和 GPU 绑定。
  - 批量实验在同一个 run 内也会避让已分配 GPU，避免重复拿到同一块卡。
  - 部分实验启动失败时，会立即释放对应 GPU lease，并保留已成功启动的实验继续运行。
  - `monitor_experiment` 已支持多 session 监控；router / runner / monitor 回归已覆盖批量元数据透传与输出聚合。

### T12

- 状态：已完成
- 已完成：
  - 新增项目运行前人工确认服务，run 在执行前可转为 `paused`，并持久化 `checkpoint_state / pending_checkpoint / notification_recipients` 元数据。
  - 新增审批响应接口，支持批准继续和拒绝取消，并把结果同步到 run 状态和任务中心状态。
  - `TaskTracker` 已支持 `paused` 持久化语义，服务重启后不会误把等待审批任务标成中断失败。
  - 新增邮件通知服务，复用系统 SMTP 配置向指定邮箱发送 checkpoint、成功、失败、取消等状态通知。
  - 前端项目页和任务中心已展示等待审批状态与审批操作入口。

### T13

- 状态：已完成
- 已完成：
  - `project_workflow_catalog` 已支持阶段级 `checkpoint_required` 声明，并已接到 active workflows 的关键阶段，包括 `literature_review / idea_discovery / novelty_check / research_review / run_experiment / auto_review_loop / paper_* / full_pipeline / monitor_experiment / sync_workspace`。
  - `project_checkpoint_service` 已从单一 preflight 扩展为通用 checkpoint 服务，可处理 `preflight` 与 `stage_transition` 两类审批。
  - `project_execution_service / project_workflow_runner` 已支持带 `resume_stage_id` 的恢复执行，不再在审批后重置全部 `stage_trace`。
  - 新增阶段暂停/恢复回归测试，验证恢复后不会重复执行已完成阶段。

### T14

- 状态：已完成
- 已完成：
  - 新增飞书配置持久化模型、仓储与设置路由，并支持配置测试发送。
  - `project_notification_service` 已接入飞书通知，项目 checkpoint 和运行状态可以真实发到飞书。
  - 设置页已补齐飞书 / Lark 配置卡片，支持保存与测试。
  - `FeishuNotificationService` 修正了返回码误判 bug，并按配置超时执行请求。
  - 新增 bridge 轮询与回执处理线程，`approve / reject` 会自动驱动 checkpoint 审批与后续运行恢复。
  - 项目页手动审批与飞书交互审批已统一到 `process_checkpoint_response` 服务层。
  - 新增 `timeout_action` 后，timeout 可配置为自动继续、自动拒绝或保持暂停。

### T15

- 状态：已完成
- 已完成：
  - 新增 `packages/integrations/llm_engine_profiles.py`，把已保存 LLM 配置展开成项目工作区可选引擎档案。
  - `create_project_run / retry_project_run / workspace_context` 已接通 `executor_engine_id / reviewer_engine_id` 以及默认推荐绑定。
  - 原生 runner、多阶段 runner、后续动作服务都已接入真实引擎档案解析，不再只依赖 `executor_model / reviewer_model` 文本字段。
  - 新增引擎矩阵回归测试，验证选择非 active config 时，后端解析出的 provider/base_url/model 确实切换。

### T16

- 状态：已完成
- 已完成：
  - 新增 ARIS skill 模板加载层，后端直接读取 reference `SKILL.md` 作为 prompt/preamble 来源。
  - `project_amadeus_compat`、`project_workflow_runner`、`project_multi_agent_runner`、`project_run_action_service` 已统一接入 ARIS skill 文本。
  - `idea_discovery` 新增 `verify_novelty / external_review` 阶段，支持更细粒度 checkpoint，并产出 `IDEA_REPORT.md`。
  - `paper_writing` 已对齐为五阶段流水线，支持 `PAPER_PLAN.md / FIGURE_PLAN / PAPER_COMPILE / PAPER_IMPROVEMENT_LOG` 产物链。
  - `auto_review_loop` 已追加 `AUTO_REVIEW.md / REVIEW_STATE.json` 持久化；`full_pipeline` 的 Gate 1 与 auto-review 阶段也会写出 ARIS 标准产物。
  - 回归测试已覆盖 ARIS prompt 模板加载、`literature_review / novelty_check / research_review / run_experiment / idea_discovery / paper_writing / auto_review_loop / full_pipeline` 的 checkpoint 恢复，以及 auto-review 状态文件持久化。

### T17

- 状态：已完成
- 已完成：
  - `run_project_workflow` 已支持把 `resume_stage_id` 传入 `auto_review_loop`。
  - `auto_review_loop` 现在会在每轮之后持续写回 `iterations`、`AUTO_REVIEW.md`、`REVIEW_STATE.json`。
  - 当 reviewer verdict 为 `almost` 时，会把 `checkpoint_resume_iteration` 持久化到 run metadata，并用阶段 checkpoint 进入暂停态。
  - 审批通过后，后端会从下一轮继续，而不是从第 1 轮重跑。

## 最终差异清单

- 见 `docs/aris-feature-gap-matrix.md`。

## 测试记录

- 2026-03-18:
  - `python -m pytest tests/test_aris_router_matrix.py tests/test_aris_feature_matrix.py tests/test_project_run_action_service.py tests/test_project_submit_tracker_regression.py tests/test_project_multi_agent_runner.py tests/test_project_execution_service.py -q`
  - 结果：`60 passed`
  - `cd frontend && npm run build`
  - 结果：通过
- 2026-03-18:
  - `python -m pytest tests/test_project_workflow_runner.py tests/test_aris_feature_matrix.py tests/test_project_multi_agent_runner.py tests/test_aris_router_matrix.py tests/test_project_run_action_service.py tests/test_project_submit_tracker_regression.py tests/test_project_execution_service.py -q`
  - 结果：`63 passed`
  - `cd frontend && npm run build`
  - 结果：通过
- 2026-03-18:
  - `python -m pytest tests/test_task_tracker.py tests/test_project_multi_agent_runner.py tests/test_project_workflow_runner.py tests/test_aris_feature_matrix.py -q`
  - 结果：`39 passed`
  - 覆盖项：任务持久化恢复、论文标准化产物、真实同步复制、ARIS smoke matrix
- 2026-03-18:
  - `python -m pytest tests/test_task_tracker.py tests/test_project_workflow_runner.py tests/test_project_multi_agent_runner.py tests/test_aris_feature_matrix.py tests/test_aris_router_matrix.py tests/test_project_run_action_service.py tests/test_project_submit_tracker_regression.py tests/test_project_execution_service.py -q`
  - 结果：`75 passed`
  - 覆盖项：远程 `screen/worktree` 运行规划、SSH 监控会话采集、任务中心元数据回归、ARIS 路由与执行器矩阵
- 2026-03-18:
  - `python -m pytest tests/test_task_tracker.py tests/test_project_workflow_runner.py tests/test_project_multi_agent_runner.py tests/test_aris_feature_matrix.py tests/test_aris_router_matrix.py tests/test_project_run_action_service.py tests/test_project_submit_tracker_regression.py tests/test_project_execution_service.py -q`
  - 结果：`75 passed`
  - 覆盖项：远程 GPU 探测与选卡、`CUDA_VISIBLE_DEVICES` 绑定、GPU 监控输出、ARIS 回归矩阵
- 2026-03-18:
  - `python -m pytest tests/test_task_tracker.py tests/test_project_gpu_lease_service.py tests/test_project_workflow_runner.py tests/test_project_multi_agent_runner.py tests/test_aris_feature_matrix.py tests/test_aris_router_matrix.py tests/test_project_run_action_service.py tests/test_project_submit_tracker_regression.py tests/test_project_execution_service.py -q`
  - 结果：`79 passed`
  - 覆盖项：跨项目 GPU lease 协调、陈旧 lease 回收、双 run 自动避让选卡、全量 ARIS 回归矩阵
- 2026-03-18:
  - `python -m pytest tests/test_task_tracker.py tests/test_project_gpu_lease_service.py tests/test_project_workflow_runner.py tests/test_project_multi_agent_runner.py tests/test_aris_feature_matrix.py tests/test_aris_router_matrix.py tests/test_project_run_action_service.py tests/test_project_submit_tracker_regression.py tests/test_project_execution_service.py -q`
  - 结果：`85 passed`
  - 覆盖项：批量实验 fan-out/fan-in、多 session 监控、同 run 内 GPU 避让、部分失败 lease 回收、remote->remote sync、结构化实验监控、ARIS 全量回归矩阵
- 2026-03-18:
  - `python -m pytest tests/test_task_tracker.py tests/test_project_execution_service.py tests/test_projects_router_flows.py -q`
  - 结果：`21 passed`
  - 覆盖项：任务暂停持久化、项目运行前人工 checkpoint、审批继续/拒绝、项目路由审批流
  - `cd frontend && npm run build`
  - 结果：通过
- 2026-03-18:
  - `python -m pytest tests/test_project_execution_service.py tests/test_project_workflow_runner.py tests/test_projects_router_flows.py tests/test_task_tracker.py tests/test_project_submit_tracker_regression.py -q`
  - 结果：`35 passed`
  - 覆盖项：阶段内 checkpoint 暂停与恢复执行、恢复后不重复执行已完成阶段、任务暂停异常语义、项目执行器 resume-stage 分派
- 2026-03-18:
  - `python -m pytest tests/test_feishu_notification.py tests/test_project_execution_service.py tests/test_project_workflow_runner.py tests/test_projects_router_flows.py -q`
  - 结果：`31 passed`
  - 覆盖项：飞书配置保存与测试、项目 checkpoint 飞书通知、飞书返回码误判修复、阶段 checkpoint 回归
  - `cd frontend && npm run build`
  - 结果：通过
- 2026-03-19:
  - `python -m pytest tests/test_feishu_notification.py tests/test_projects_router_flows.py tests/test_project_execution_service.py tests/test_project_workflow_runner.py tests/test_task_tracker.py tests/test_project_submit_tracker_regression.py -q`
  - 结果：`43 passed`
  - 覆盖项：飞书 bridge 回执轮询、自动 approve/reject 闭环、手动审批与自动审批共用服务路径、项目路由回归
  - `cd frontend && npm run build`
  - 结果：通过
- 2026-03-19:
  - `python -m pytest tests/test_feishu_notification.py tests/test_projects_router_flows.py tests/test_project_execution_service.py tests/test_project_workflow_runner.py tests/test_task_tracker.py tests/test_project_submit_tracker_regression.py -q`
  - 结果：`45 passed`
  - 覆盖项：飞书 `timeout_action` 配置持久化、超时自动继续/保持暂停策略、交互审批与项目执行回归
  - `cd frontend && npm run build`
  - 结果：通过
- 2026-03-19:
  - `python -m pytest tests/test_project_engine_profiles.py tests/test_projects_router_flows.py tests/test_project_workflow_runner.py tests/test_project_multi_agent_runner.py tests/test_project_run_action_service.py tests/test_aris_feature_matrix.py tests/test_aris_router_matrix.py tests/test_project_execution_service.py tests/test_feishu_notification.py tests/test_aris_prompt_templates.py -q`
  - 结果：`106 passed`
  - 覆盖项：ARIS prompt 模板加载、idea / paper 多阶段 checkpoint、auto-review `almost` 判定暂停与恢复、项目执行/通知/路由全链路回归
- 2026-03-19:
  - `python -m pytest tests/test_project_workflow_runner.py tests/test_project_execution_service.py tests/test_projects_router_flows.py tests/test_aris_feature_matrix.py -q`
  - 结果：`72 passed`
  - 覆盖项：`literature_review / novelty_check / research_review / run_experiment / auto_review_loop` 的阶段 checkpoint 暂停与恢复、`auto_proceed` 路由映射、native workflow resume-stage 分派
- 2026-03-19:
  - `python -m pytest @(Get-ChildItem tests -Filter "test_project_*.py" | Select-Object -ExpandProperty FullName) tests/test_projects_router_flows.py tests/test_aris_feature_matrix.py -q`
  - 结果：`96 passed`
  - 覆盖项：active workflows 项目执行矩阵、multi-agent 与 native workflow 的 checkpoint/恢复链路、ARIS feature gap 回归
- 2026-03-19:
  - `python -m pytest tests/test_acp_service.py tests/test_agent_permission_next.py tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py -q`
  - 结果：`50 passed`
  - 覆盖项：ACP stdio 权限暂停/确认/恢复、自定义 ACP 聊天确认卡、agent confirm/reject 恢复链路、session runtime 持久化回归
- 2026-03-20:
  - `npm --prefix frontend run build`
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "projects workspace supports creating a project through the ui and shows the desktop workbench layout"`（`frontend` 目录）
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "papers tasks and pipelines support safe view interactions"`（`frontend` 目录）
  - 结果：通过
  - 覆盖项：`Projects` 当前运行区的 `overview / trace / actions` 新布局与交互未回归。
- 2026-03-20:
  - `npm --prefix frontend run build`
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "projects workspace supports creating a project through the ui and shows the desktop workbench layout"`（`frontend` 目录）
  - 结果：通过
  - 覆盖项：`Projects` 页整页继续减法收口后，项目列表、项目头部、companion、部署目标、启动运行、最近运行和当前运行的主交互未回归。
- 2026-03-20:
  - `npm --prefix frontend run build`
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "projects workspace supports creating a project through the ui and shows the desktop workbench layout"`（`frontend` 目录）
  - 结果：通过
  - 覆盖项：`Projects` 页 `当前运行 / 概览` 改成“主结果 + 侧边摘要 / 关键路径”后，工作台布局与核心交互未回归。
- 2026-03-20:
  - `npm --prefix frontend run build`
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "projects workspace supports creating a project through the ui and shows the desktop workbench layout"`（`frontend` 目录）
  - 结果：通过
  - 覆盖项：`Projects` 页 `当前运行 / 阶段追踪 / 后续动作` 压缩单条记录高度后，工作台布局与核心交互未回归。
- 2026-03-20:
  - `npm --prefix frontend run build`
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "projects workspace supports creating a project through the ui and shows the desktop workbench layout"`（`frontend` 目录）
  - 结果：通过
  - 覆盖项：`Projects` 页删除重复的 `详细摘要 / 关键路径` 面板、压缩头部快速概览并去重阶段/动作标签后，工作台布局与核心交互未回归。
- 2026-03-20:
  - `npm --prefix frontend run build`
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "projects workspace supports creating a project through the ui and shows the desktop workbench layout"`（`frontend` 目录）
  - 结果：通过
  - 覆盖项：`Projects` 页继续压缩 `companion / 部署目标 / 启动运行 / 当前运行` 的重复上下文后，项目工作台主路径与桌面化布局未回归。
- 2026-03-20:
  - `npm --prefix frontend run build`
  - 结果：通过
  - `.\node_modules\.bin\playwright.cmd test -c playwright.config.ts --grep "projects workspace supports creating a project through the ui and shows the desktop workbench layout"`（`frontend` 目录）
  - 结果：通过
  - 覆盖项：`Projects` 页 `部署目标` 右侧详情继续去重后，目标选择、详情展示和工作台主交互未回归。
