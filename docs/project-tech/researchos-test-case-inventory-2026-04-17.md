# ResearchOS 测试覆盖评估与 711 条 Pytest 用例清单

生成日期：2026-04-17
测试集合：`tests/` 目录下 pytest 测试，不包含 `frontend/tests/smoke.spec.ts` 的 19 条 Playwright 前端端到端测试。
数据来源：`python -m pytest --collect-only -q`，收集结果为 83 个 pytest 测试模块、711 条测试用例。`tests/` 目录总文件数为 89，未计入用例的文件主要是 `conftest.py`、`__init__.py` 和 fixtures。

## 1. 覆盖是否全面

结论：`tests/` 下的后端与服务层测试覆盖面较广，已经覆盖研究助手、Agent 权限、会话生命周期、LLM provider、论文采集与分析、项目工作区、ARIS 工作流、存储、任务、设置、安全、workspace 文件/Git/SSH 等核心模块。它适合作为后端主回归门禁。

但它不是严格意义上的 100% 全覆盖，也不能替代浏览器端、真实第三方服务、生产鉴权、安全渗透和性能压测。完整验收仍需要结合 Playwright 前端烟测、真实 SSH/ACP 补测、真实外部论文源测试、生产鉴权测试和负载测试。

已覆盖较充分的部分：

- 研究助手与 Agent：工具暴露、权限确认、prompt 生命周期、队列、回调、消息持久化、压缩、重试、revert/diff、Claw/ACP 兼容。
- 项目工作区与 ARIS：项目 CRUD、目标、论文、运行、checkpoint、native/multi-agent runner、论文写作、实验、GPU lease、报告格式化。
- 论文与研究工具：arXiv/OpenAlex、外部文献导入、论文分析、PDF/Markdown 阅读、图表分析、CCF-A venue 过滤。
- LLM 接入：provider registry、OpenAI/Anthropic-兼容ible 转换、流式输出、embedding、vision、probe、transport policy。
- 平台基础：启动、存储迁移、任务 tracker、全局路由、安全默认值、workspace path/Git/terminal 边界。

仍需补充或依赖其他测试的部分：

- 前端浏览器 UI：`tests/` 本身不包含 React 页面级断言，需要结合 `frontend/tests/smoke.spec.ts`。
- 真实第三方服务：arXiv、OpenAlex、Semantic Scholar、LLM API 等多数用例采用 mock/fixture，不等同于 live 稳定性验证。
- 生产鉴权与密钥安全：已有安全回归，但还需要开启生产 auth 的端到端测试和安全审计。
- 性能与容量：当前是功能回归，不是并发压测；论文大批量导入、向量化、长工作流队列仍需压力测试。
- 覆盖率量化：本文件统计的是测试用例清单和功能覆盖，没有运行 coverage.py 代码行覆盖率。

## 2. 按功能域统计

| 功能域 | 测试用例数 |
| --- | ---: |
| 研究助手、Agent、权限与工具 | 263 |
| 项目工作区与 ARIS 工作流 | 166 |
| 论文收集、解析、分析与研究工具 | 103 |
| LLM 与模型运行时 | 98 |
| 平台基础、安全、存储、任务与工作区 | 66 |
| 报告、图谱、通知与写作 | 15 |
| **合计** | **711** |

## 3. 按测试文件统计

| 测试文件 | 功能域 | 用例数 | 主要覆盖点 |
| --- | --- | ---: | --- |
| `tests/test_acp_service.py` | 研究助手、Agent、权限与工具 | 5 | ACP 服务配置、stdio/http 连接、权限暂停与恢复。 |
| `tests/test_agent_permission_next.py` | 研究助手、Agent、权限与工具 | 41 | Agent 工具暴露、权限确认、问题卡片、ACP 权限回路、shell/patch/plan 模式限制。 |
| `tests/test_agent_prompt_lifecycle.py` | 研究助手、Agent、权限与工具 | 76 | 会话 prompt 生命周期、事件总线、队列、回调、持久化、工具调用与系统提示词。 |
| `tests/test_agent_remote_workspace.py` | 研究助手、Agent、权限与工具 | 4 | 本地/远程 workspace tool 暴露差异与结构化消息绑定。 |
| `tests/test_agent_runtime_policy.py` | 研究助手、Agent、权限与工具 | 7 | 推理档位、工具步数、自动压缩阈值、重复工具调用硬停策略。 |
| `tests/test_agent_service_claw_stream.py` | 研究助手、Agent、权限与工具 | 4 | Claw daemon stream 事件、空回复 fallback、工具结果 fallback。 |
| `tests/test_agent_session_compaction.py` | 研究助手、Agent、权限与工具 | 7 | 会话摘要压缩、上下文溢出恢复、步骤生命周期持久化。 |
| `tests/test_agent_session_facades.py` | 研究助手、Agent、权限与工具 | 2 | SSE 事件解析、格式化与兼容。 |
| `tests/test_agent_session_retry.py` | 研究助手、Agent、权限与工具 | 17 | 模型/网络/鉴权/上下文溢出错误归一化与重试策略。 |
| `tests/test_agent_session_revert_diff.py` | 研究助手、Agent、权限与工具 | 9 | 会话文件 diff、revert/unrevert、本地/远程工作区补丁恢复。 |
| `tests/test_agent_session_runtime.py` | 研究助手、Agent、权限与工具 | 57 | 会话创建、prompt、workspace 绑定、消息持久化、计划/构建模式、工具与结构化内容。 |
| `tests/test_agent_tools_status.py` | 研究助手、Agent、权限与工具 | 1 | Agent 工具状态与可见性。 |
| `tests/test_agent_workspace_ssh.py` | 研究助手、Agent、权限与工具 | 3 | SSH workspace 配置、探测、远程文件/Git/终端行为。 |
| `tests/test_analysis_levels.py` | 论文收集、解析、分析与研究工具 | 8 | 论文分析层级与阶段定义。 |
| `tests/test_analysis_repository.py` | 论文收集、解析、分析与研究工具 | 1 | 分析结果仓储读写与元数据。 |
| `tests/test_app_startup.py` | 平台基础、安全、存储、任务与工作区 | 1 | 应用启动、数据库 bootstrap 与路由初始化。 |
| `tests/test_aris_feature_matrix.py` | 项目工作区与 ARIS 工作流 | 28 | ARIS 工作流矩阵、阶段、动作、报告和 checkpoint 行为。 |
| `tests/test_aris_prompt_templates.py` | 项目工作区与 ARIS 工作流 | 1 | ARIS prompt 模板加载和参数渲染。 |
| `tests/test_aris_router_matrix.py` | 项目工作区与 ARIS 工作流 | 30 | ARIS 路由矩阵和项目运行入口。 |
| `tests/test_aris_smoke_productization.py` | 项目工作区与 ARIS 工作流 | 4 | ARIS 产品化烟测、任务落库与输出结构。 |
| `tests/test_arxiv_client.py` | 论文收集、解析、分析与研究工具 | 3 | arXiv 客户端查询、解析、异常处理。 |
| `tests/test_auth_security.py` | 平台基础、安全、存储、任务与工作区 | 5 | 认证、安全中间件、token 与敏感路由保护。 |
| `tests/test_brief_service.py` | 报告、图谱、通知与写作 | 3 | 研究日报/brief 服务。 |
| `tests/test_claw_bridge_runtime.py` | 研究助手、Agent、权限与工具 | 2 | Claw bridge runtime 与事件转换。 |
| `tests/test_claw_runtime_manager.py` | 研究助手、Agent、权限与工具 | 1 | Claw runtime manager 生命周期。 |
| `tests/test_cli_agent_service.py` | 研究助手、Agent、权限与工具 | 9 | CLI agent service 兼容链路。 |
| `tests/test_collect_external_ingest.py` | 论文收集、解析、分析与研究工具 | 2 | 外部论文采集、导入、去重与入库。 |
| `tests/test_feishu_notification.py` | 报告、图谱、通知与写作 | 9 | 飞书通知配置、签名、发送与错误处理。 |
| `tests/test_figure_service.py` | 论文收集、解析、分析与研究工具 | 21 | 论文图表解析、引用、图像项归一化。 |
| `tests/test_global_routes.py` | 平台基础、安全、存储、任务与工作区 | 9 | 全局 API 路由与基础响应。 |
| `tests/test_graph_service_citation_cache.py` | 报告、图谱、通知与写作 | 1 | 引用图谱服务和缓存。 |
| `tests/test_llm_client_dispatch.py` | LLM 与模型运行时 | 5 | llm client dispatch |
| `tests/test_llm_client_embedding.py` | LLM 与模型运行时 | 4 | llm client embedding |
| `tests/test_llm_client_message_transform.py` | LLM 与模型运行时 | 24 | llm client message transform |
| `tests/test_llm_client_probe.py` | LLM 与模型运行时 | 6 | llm client probe |
| `tests/test_llm_client_provider_options.py` | LLM 与模型运行时 | 22 | llm client provider options |
| `tests/test_llm_client_resolution.py` | LLM 与模型运行时 | 6 | llm client resolution |
| `tests/test_llm_client_runtime.py` | LLM 与模型运行时 | 3 | llm client runtime |
| `tests/test_llm_client_stream.py` | LLM 与模型运行时 | 8 | llm client stream |
| `tests/test_llm_client_summary.py` | LLM 与模型运行时 | 7 | llm client summary |
| `tests/test_llm_client_transport_policy.py` | LLM 与模型运行时 | 3 | llm client transport policy |
| `tests/test_llm_client_vision.py` | LLM 与模型运行时 | 6 | llm client vision |
| `tests/test_llm_provider_registry.py` | LLM 与模型运行时 | 2 | llm provider registry |
| `tests/test_llm_provider_transform.py` | LLM 与模型运行时 | 2 | llm provider transform |
| `tests/test_mineru_runtime.py` | 论文收集、解析、分析与研究工具 | 8 | MinerU 运行时探测和解析适配。 |
| `tests/test_mounted_paper_context.py` | 论文收集、解析、分析与研究工具 | 2 | 挂载论文上下文、按需读取、prompt 膨胀控制。 |
| `tests/test_openalex_client_rerank.py` | 论文收集、解析、分析与研究工具 | 5 | OpenAlex 检索重排。 |
| `tests/test_openalex_client_source_selection.py` | 论文收集、解析、分析与研究工具 | 2 | OpenAlex 来源选择和候选过滤。 |
| `tests/test_paper_analysis_service.py` | 论文收集、解析、分析与研究工具 | 7 | 论文粗读/精读/分析服务。 |
| `tests/test_paper_evidence.py` | 论文收集、解析、分析与研究工具 | 3 | 论文证据、引用片段和证据链。 |
| `tests/test_paper_reader.py` | 论文收集、解析、分析与研究工具 | 11 | 论文阅读器、PDF/Markdown 内容读取。 |
| `tests/test_paper_reasoning_sync.py` | 论文收集、解析、分析与研究工具 | 5 | 论文分析推理结果同步。 |
| `tests/test_pdf_reader_ai_prompt.py` | 论文收集、解析、分析与研究工具 | 5 | PDF 阅读器 AI prompt 组装。 |
| `tests/test_pipelines_deep_dive.py` | 论文收集、解析、分析与研究工具 | 3 | 深度分析 pipeline、缓存和阶段跳过。 |
| `tests/test_project_engine_profiles.py` | 项目工作区与 ARIS 工作流 | 2 | 项目工作流模型引擎配置和绑定。 |
| `tests/test_project_execution_service.py` | 项目工作区与 ARIS 工作流 | 7 | 项目运行提交、native/multi-agent 选择、checkpoint 分发与恢复。 |
| `tests/test_project_gpu_lease_service.py` | 项目工作区与 ARIS 工作流 | 2 | 项目 GPU lease 获取、冲突、释放和 reconcile。 |
| `tests/test_project_multi_agent_runner.py` | 项目工作区与 ARIS 工作流 | 12 | 多 Agent 项目 runner、论文写作、实验、同步、监控。 |
| `tests/test_project_output_sanitizer.py` | 项目工作区与 ARIS 工作流 | 3 | 项目输出清洗、工具痕迹移除、预览安全。 |
| `tests/test_project_paper_artifacts.py` | 项目工作区与 ARIS 工作流 | 4 | 论文改进 bundle、review 分数、action item 解析。 |
| `tests/test_project_report_formatter.py` | 项目工作区与 ARIS 工作流 | 17 | 项目 workflow 报告格式化和 artifact 合并。 |
| `tests/test_project_run_action_service.py` | 项目工作区与 ARIS 工作流 | 2 | 项目运行动作提交、远程执行和任务元数据。 |
| `tests/test_project_submit_tracker_regression.py` | 项目工作区与 ARIS 工作流 | 3 | 项目提交 tracker 元数据和远程执行回归。 |
| `tests/test_project_workflow_repository.py` | 项目工作区与 ARIS 工作流 | 1 | 项目 workflow 记录仓储读写。 |
| `tests/test_project_workflow_runner.py` | 项目工作区与 ARIS 工作流 | 27 | 项目 workflow runner、阶段 checkpoint、文献/实验/写作/远程运行。 |
| `tests/test_projects_router_flows.py` | 项目工作区与 ARIS 工作流 | 23 | 项目 CRUD、目标、论文、运行、候选导入、checkpoint、删除和校验。 |
| `tests/test_removed_modules_surface.py` | 平台基础、安全、存储、任务与工作区 | 9 | 已移除通知/日报/维护端点不可访问性。 |
| `tests/test_research_assistant_tools.py` | 论文收集、解析、分析与研究工具 | 9 | 研究助手论文工具、wiki 工具、外部文献导入、图表分析。 |
| `tests/test_research_tool_runtime.py` | 论文收集、解析、分析与研究工具 | 3 | 研究工具运行时、CCF-A 过滤、arXiv/OpenAlex 合并。 |
| `tests/test_research_venue_catalog_coverage.py` | 论文收集、解析、分析与研究工具 | 5 | CCF-A venue catalog 覆盖和过滤。 |
| `tests/test_runtime_safety_regressions.py` | 平台基础、安全、存储、任务与工作区 | 4 | 运行时安全回归、默认权限、缓存和语义候选扫描。 |
| `tests/test_session_message_v2.py` | 研究助手、Agent、权限与工具 | 4 | Session message v2 分页、过滤、错误与运行时信息。 |
| `tests/test_settings_llm_provider_presets.py` | 平台基础、安全、存储、任务与工作区 | 5 | LLM provider presets、Zhipu/Gemini/OpenAI-兼容ible 输出。 |
| `tests/test_storage_bootstrap.py` | 平台基础、安全、存储、任务与工作区 | 3 | 存储 bootstrap、迁移 stamping 和 import 无副作用。 |
| `tests/test_storage_json_schema.py` | 平台基础、安全、存储、任务与工作区 | 2 | JSON schema version 和 task sidecar log。 |
| `tests/test_task_tracker.py` | 平台基础、安全、存储、任务与工作区 | 7 | 任务进度、过滤、日志、重试、持久化和 bootstrap 恢复。 |
| `tests/test_tool_registry.py` | 研究助手、Agent、权限与工具 | 11 | 工具注册、权限 spec、研究工具目录、workspace 核心工具执行。 |
| `tests/test_topic_subscription_filters.py` | 平台基础、安全、存储、任务与工作区 | 2 | 专题订阅过滤器持久化和清空。 |
| `tests/test_topics_cache_invalidation.py` | 平台基础、安全、存储、任务与工作区 | 1 | 专题变更后的缓存失效。 |
| `tests/test_web_tool_runtime.py` | 研究助手、Agent、权限与工具 | 3 | Web/search/fetch/code search 错误类型化。 |
| `tests/test_worker_schedule.py` | 平台基础、安全、存储、任务与工作区 | 2 | worker 自动任务注册与 cron 时区展示。 |
| `tests/test_workspace_executor_paths.py` | 平台基础、安全、存储、任务与工作区 | 16 | workspace/path 文件、grep/glob/read/run/git/reveal 边界。 |
| `tests/test_writing_image_service.py` | 报告、图谱、通知与写作 | 2 | 写作图片生成请求和参数校验。 |

## 4. 711 条测试用例清单

说明：`Node ID` 是 pytest 实际收集到的可执行测试标识；“中文描述”基于测试模块、测试函数名和参数化信息生成，并对项目常用术语进行了中文化处理。ACP、LLM、prompt、workspace、checkpoint 等专有名词和少量参数值会保留原始英文，便于与代码和测试输出对应。

### tests/test_acp_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 1 | `tests/test_acp_service.py::test_acp_config_roundtrip_and_runtime_snapshot` | 验证ACP 服务中“ACP 配置读写闭环与运行时快照”这一场景或边界行为。 |
| 2 | `tests/test_acp_service.py::test_acp_stdio_connect_execute_and_disconnect` | 验证ACP 服务中“ACP stdio 连接执行与断开连接”这一场景或边界行为。 |
| 3 | `tests/test_acp_service.py::test_updating_connected_server_restarts_connection` | 验证ACP 服务中“更新已连接服务器重启连接”这一场景或边界行为。 |
| 4 | `tests/test_acp_service.py::test_acp_stdio_permission_pause_and_resume` | 验证ACP 服务中“ACP stdio 权限暂停与恢复执行”这一场景或边界行为。 |
| 5 | `tests/test_acp_service.py::test_acp_http_permission_pause_and_resume` | 验证ACP 服务中“ACP HTTP 权限暂停与恢复执行”这一场景或边界行为。 |

### tests/test_agent_permission_next.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 6 | `tests/test_agent_permission_next.py::test_permission_deny_rule_disables_tool_exposure` | 验证Agent 权限系统中“权限拒绝规则禁用工具暴露”这一场景或边界行为。 |
| 7 | `tests/test_agent_permission_next.py::test_get_openai_tools_defaults_to_opencode_core_set` | 验证Agent 权限系统中“获取OpenAI 工具默认使用OpenCode 核心集合”这一场景或边界行为。 |
| 8 | `tests/test_agent_permission_next.py::test_build_turn_tools_exposes_plan_file_tools_in_plan_mode` | 验证Agent 权限系统中“构建模式轮次工具暴露计划模式下的文件工具”这一场景或边界行为。 |
| 9 | `tests/test_agent_permission_next.py::test_get_openai_tools_allows_explicit_extension_opt_in` | 验证Agent 权限系统中“获取OpenAI 工具允许显式扩展选择加入”这一场景或边界行为。 |
| 10 | `tests/test_agent_permission_next.py::test_get_openai_tools_remote_workspace_still_filters_local_only_hidden_tools` | 验证Agent 权限系统中“获取OpenAI 工具远程工作区仍然过滤本地仅隐藏工具”这一场景或边界行为。 |
| 11 | `tests/test_agent_permission_next.py::test_execute_tool_stream_skill_returns_opencode_style_skill_content` | 验证Agent 权限系统中“执行工具流式输出 Skill 返回 OpenCode 风格 Skill 内容”这一场景或边界行为。 |
| 12 | `tests/test_agent_permission_next.py::test_execute_tool_stream_apply_patch_updates_and_moves_files` | 验证Agent 权限系统中“执行工具流式输出应用补丁更新与移动文件”这一场景或边界行为。 |
| 13 | `tests/test_agent_permission_next.py::test_authorize_apply_patch_extracts_file_patterns` | 验证Agent 权限系统中“授权应用补丁提取文件模式”这一场景或边界行为。 |
| 14 | `tests/test_agent_permission_next.py::test_session_permission_once_flow` | 验证Agent 权限系统中“会话权限一次性流程”这一场景或边界行为。 |
| 15 | `tests/test_agent_permission_next.py::test_session_question_flow_pauses_and_resumes_with_answers` | 验证Agent 权限系统中“会话问题流程暂停与恢复执行带有答案”这一场景或边界行为。 |
| 16 | `tests/test_agent_permission_next.py::test_native_permission_confirm_does_not_fall_back_to_wrapper_persistence` | 验证Agent 权限系统中“原生权限确认不会回退到包装器持久化”这一场景或边界行为。 |
| 17 | `tests/test_agent_permission_next.py::test_native_permission_confirm_is_persisted_inside_native_resume_path` | 验证Agent 权限系统中“原生权限确认是已持久化在原生恢复执行路径”这一场景或边界行为。 |
| 18 | `tests/test_agent_permission_next.py::test_native_permission_confirm_does_not_use_apply_event_bridge` | 验证Agent 权限系统中“原生权限确认不会使用应用事件桥接”这一场景或边界行为。 |
| 19 | `tests/test_agent_permission_next.py::test_native_permission_confirm_is_queued_into_session_callback_loop` | 验证Agent 权限系统中“原生权限确认是排队进入会话回调循环”这一场景或边界行为。 |
| 20 | `tests/test_agent_permission_next.py::test_native_permission_response_delegates_to_session_prompt_processor` | 验证Agent 权限系统中“原生权限响应委托到会话提示词处理器”这一场景或边界行为。 |
| 21 | `tests/test_agent_permission_next.py::test_native_permission_response_runtime_delegates_to_session_processor_helper` | 验证Agent 权限系统中“原生权限响应运行时委托到会话处理器辅助器”这一场景或边界行为。 |
| 22 | `tests/test_agent_permission_next.py::test_permission_callback_processor_reuses_stream_active_entry` | 验证Agent 权限系统中“权限回调处理器复用流式输出活跃条目”这一场景或边界行为。 |
| 23 | `tests/test_agent_permission_next.py::test_aborted_native_permission_reply_cancels_on_same_resume_entry` | 验证Agent 权限系统中“已中止原生权限回复 在相同恢复执行条目取消”这一场景或边界行为。 |
| 24 | `tests/test_agent_permission_next.py::test_native_permission_confirm_resume_loads_persisted_session_history` | 验证Agent 权限系统中“原生权限确认恢复执行加载已持久化会话历史记录”这一场景或边界行为。 |
| 25 | `tests/test_agent_permission_next.py::test_native_pending_action_persistence_omits_continuation_messages` | 验证Agent 权限系统中“原生待处理动作持久化省略续接消息”这一场景或边界行为。 |
| 26 | `tests/test_agent_permission_next.py::test_native_pending_persistence_prefers_permission_request_parent_message` | 验证Agent 权限系统中“原生待处理持久化优先使用权限请求父消息”这一场景或边界行为。 |
| 27 | `tests/test_agent_permission_next.py::test_native_pending_tool_calls_fall_back_to_permission_request_metadata` | 验证Agent 权限系统中“原生待处理工具调用回退到权限请求元数据”这一场景或边界行为。 |
| 28 | `tests/test_agent_permission_next.py::test_native_permission_confirm_repause_keeps_session_busy` | 验证Agent 权限系统中“原生权限确认再次暂停保持会话忙碌”这一场景或边界行为。 |
| 29 | `tests/test_agent_permission_next.py::test_session_permission_persists_across_runtime_cache_reset` | 验证Agent 权限系统中“会话权限跨运行时缓存重置后仍保持持久化”这一场景或边界行为。 |
| 30 | `tests/test_agent_permission_next.py::test_permission_always_persists_project_rule_and_skips_repeat_prompt` | 验证Agent 权限系统中“权限始终持久化项目规则与跳过重复提示词”这一场景或边界行为。 |
| 31 | `tests/test_agent_permission_next.py::test_session_permission_reject_resumes_with_feedback` | 验证Agent 权限系统中“会话权限拒绝恢复执行带有反馈”这一场景或边界行为。 |
| 32 | `tests/test_agent_permission_next.py::test_legacy_confirm_route_persists_follow_up` | 验证Agent 权限系统中“兼容旧版确认路由持久化后续内容”这一场景或边界行为。 |
| 33 | `tests/test_agent_permission_next.py::test_custom_acp_permission_confirm_flow` | 验证Agent 权限系统中“自定义 ACP 权限确认流程”这一场景或边界行为。 |
| 34 | `tests/test_agent_permission_next.py::test_custom_acp_abort_clears_paused_permission_and_publishes_aborted_message` | 验证Agent 权限系统中“自定义 ACP 中止清空已暂停权限与发布已中止消息”这一场景或边界行为。 |
| 35 | `tests/test_agent_permission_next.py::test_custom_acp_confirm_does_not_fall_back_to_wrapper_persistence` | 验证Agent 权限系统中“自定义 ACP 确认不会回退到包装器持久化”这一场景或边界行为。 |
| 36 | `tests/test_agent_permission_next.py::test_custom_acp_http_permission_confirm_flow` | 验证Agent 权限系统中“自定义 ACP HTTP 权限确认流程”这一场景或边界行为。 |
| 37 | `tests/test_agent_permission_next.py::test_custom_acp_permission_auto_confirms_when_approval_is_off` | 验证Agent 权限系统中“自定义 ACP 权限自动确认当审批是关闭”这一场景或边界行为。 |
| 38 | `tests/test_agent_permission_next.py::test_build_turn_tools_appends_official_openai_builtin_tools` | 验证Agent 权限系统中“构建模式轮次工具追加官方 OpenAI 内置工具”这一场景或边界行为。 |
| 39 | `tests/test_agent_permission_next.py::test_build_turn_tools_prefers_apply_patch_for_gpt5_models` | 验证Agent 权限系统中“构建模式轮次工具优先使用应用补丁针对 GPT-5 模型”这一场景或边界行为。 |
| 40 | `tests/test_agent_permission_next.py::test_build_turn_tools_prefers_edit_and_write_for_non_gpt5_models` | 验证Agent 权限系统中“构建模式轮次工具优先使用编辑与写入针对非 GPT-5 模型”这一场景或边界行为。 |
| 41 | `tests/test_agent_permission_next.py::test_build_turn_tools_respects_latest_user_tool_overrides` | 验证Agent 权限系统中“构建模式轮次工具遵守最新用户工具覆盖项”这一场景或边界行为。 |
| 42 | `tests/test_agent_permission_next.py::test_authorize_local_shell_respects_bash_permission_rules` | 验证Agent 权限系统中“授权本地 Shell 遵守 bash 权限 rules”这一场景或边界行为。 |
| 43 | `tests/test_agent_permission_next.py::test_plan_mode_authorize_tool_call_allows_only_plan_file` | 验证Agent 权限系统中“计划模式模式授权工具调用允许仅计划模式文件”这一场景或边界行为。 |
| 44 | `tests/test_agent_permission_next.py::test_execute_tool_stream_local_shell_returns_output` | 验证Agent 权限系统中“执行工具流式输出本地 Shell 返回输出”这一场景或边界行为。 |
| 45 | `tests/test_agent_permission_next.py::test_plan_mode_execute_tool_stream_allows_read_only_bash` | 验证Agent 权限系统中“计划模式模式执行工具流式输出允许读取仅 bash”这一场景或边界行为。 |
| 46 | `tests/test_agent_permission_next.py::test_plan_mode_execute_tool_stream_denies_mutating_bash` | 验证Agent 权限系统中“计划模式模式执行工具流式输出拒绝会变更的 bash”这一场景或边界行为。 |

### tests/test_agent_prompt_lifecycle.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 47 | `tests/test_agent_prompt_lifecycle.py::test_session_bus_publishes_status_and_message_events` | 验证Agent 提示词生命周期中“会话事件总线发布状态与消息事件”这一场景或边界行为。 |
| 48 | `tests/test_agent_prompt_lifecycle.py::test_session_bus_mirrors_events_to_global_bus` | 验证Agent 提示词生命周期中“会话事件总线镜像事件到全局事件总线”这一场景或边界行为。 |
| 49 | `tests/test_agent_prompt_lifecycle.py::test_session_bus_subscribe_filters_by_event_type` | 验证Agent 提示词生命周期中“会话事件总线 subscribe 过滤通过事件类型”这一场景或边界行为。 |
| 50 | `tests/test_agent_prompt_lifecycle.py::test_stream_active_uses_unified_lifecycle_helper` | 验证Agent 提示词生命周期中“流式输出活跃使用统一生命周期辅助器”这一场景或边界行为。 |
| 51 | `tests/test_agent_prompt_lifecycle.py::test_run_loop_delegates_to_session_processor_prompt_loop` | 验证Agent 提示词生命周期中“运行循环委托到会话处理器提示词循环”这一场景或边界行为。 |
| 52 | `tests/test_agent_prompt_lifecycle.py::test_run_loop_scales_step_budget_with_reasoning_level` | 验证Agent 提示词生命周期中“运行循环按比例调整步骤预算带有推理级别”这一场景或边界行为。 |
| 53 | `tests/test_agent_prompt_lifecycle.py::test_fill_workspace_defaults_scales_search_profile_with_reasoning_level` | 验证Agent 提示词生命周期中“填充工作区默认按比例调整检索配置档带有推理级别”这一场景或边界行为。 |
| 54 | `tests/test_agent_prompt_lifecycle.py::test_run_model_turn_events_delegates_to_session_processor_runtime` | 验证Agent 提示词生命周期中“运行模型轮次事件委托到会话处理器运行时”这一场景或边界行为。 |
| 55 | `tests/test_agent_prompt_lifecycle.py::test_process_tool_calls_delegates_to_session_processor_runtime` | 验证Agent 提示词生命周期中“处理工具调用委托到会话处理器运行时”这一场景或边界行为。 |
| 56 | `tests/test_agent_prompt_lifecycle.py::test_session_bus_wait_for_returns_matching_event` | 验证Agent 提示词生命周期中“会话事件总线等待针对返回 matching 事件”这一场景或边界行为。 |
| 57 | `tests/test_agent_prompt_lifecycle.py::test_prompt_instance_manager_waits_for_release_and_resolves_waiter` | 验证Agent 提示词生命周期中“提示词实例管理器等待针对释放与解析等待方”这一场景或边界行为。 |
| 58 | `tests/test_agent_prompt_lifecycle.py::test_prompt_result_payload_includes_persisted_message` | 验证Agent 提示词生命周期中“提示词结果载荷包含已持久化消息”这一场景或边界行为。 |
| 59 | `tests/test_agent_prompt_lifecycle.py::test_session_prompt_processor_publishes_prompt_and_step_events` | 验证Agent 提示词生命周期中“会话提示词处理器发布提示词和步骤事件”这一场景或边界行为。 |
| 60 | `tests/test_agent_prompt_lifecycle.py::test_native_prompt_persistence_is_owned_by_processor` | 验证Agent 提示词生命周期中“原生提示词持久化是 owned 通过处理器”这一场景或边界行为。 |
| 61 | `tests/test_agent_prompt_lifecycle.py::test_native_prompt_persistence_does_not_reparse_raw_sse_in_processor_path` | 验证Agent 提示词生命周期中“原生提示词持久化不会重新解析原始 SSE在处理器路径”这一场景或边界行为。 |
| 62 | `tests/test_agent_prompt_lifecycle.py::test_native_prompt_processor_mutates_session_state_without_apply_event_bridge` | 验证Agent 提示词生命周期中“原生提示词处理器 mutates 会话状态不依赖应用事件桥接”这一场景或边界行为。 |
| 63 | `tests/test_agent_prompt_lifecycle.py::test_default_backend_routes_through_native_processor_without_cli_bridge` | 验证Agent 提示词生命周期中“默认后端路由通过原生处理器不依赖 CLI 桥接”这一场景或边界行为。 |
| 64 | `tests/test_agent_prompt_lifecycle.py::test_explicit_claw_backend_routes_through_cli_stream_without_wrapper_persistence` | 验证Agent 提示词生命周期中“显式 Claw 后端路由通过 CLI 流式输出不依赖包装器持久化”这一场景或边界行为。 |
| 65 | `tests/test_agent_prompt_lifecycle.py::test_claw_stream_persists_tool_parts_from_cli_tool_trace` | 验证Agent 提示词生命周期中“Claw 流式输出持久化工具片段从 CLI 工具轨迹”这一场景或边界行为。 |
| 66 | `tests/test_agent_prompt_lifecycle.py::test_session_prompt_route_loads_persisted_user_message_for_claw_prompt` | 验证Agent 提示词生命周期中“会话提示词路由加载已持久化用户消息针对 Claw 提示词”这一场景或边界行为。 |
| 67 | `tests/test_agent_prompt_lifecycle.py::test_legacy_native_backend_id_is_normalized_to_native` | 验证Agent 提示词生命周期中“兼容旧版原生后端 ID 是归一化的到原生”这一场景或边界行为。 |
| 68 | `tests/test_agent_prompt_lifecycle.py::test_session_prompt_route_uses_persisted_backend_when_request_omits_it` | 验证Agent 提示词生命周期中“会话提示词路由使用已持久化后端当请求省略 it”这一场景或边界行为。 |
| 69 | `tests/test_agent_prompt_lifecycle.py::test_claw_paper_analysis_without_final_message_emits_fallback_text_and_persists_history` | 验证Agent 提示词生命周期中“Claw 论文分析不依赖最终消息发出兜底文本与持久化历史记录”这一场景或边界行为。 |
| 70 | `tests/test_agent_prompt_lifecycle.py::test_cli_chat_prompt_reuses_shared_turn_context_sections` | 验证Agent 提示词生命周期中“CLI 对话提示词复用共享轮次上下文章节”这一场景或边界行为。 |
| 71 | `tests/test_agent_prompt_lifecycle.py::test_cli_chat_prompt_includes_latest_user_system_and_output_constraint` | 验证Agent 提示词生命周期中“CLI 对话提示词包含最新用户系统与输出约束”这一场景或边界行为。 |
| 72 | `tests/test_agent_prompt_lifecycle.py::test_cli_chat_prompt_renders_tool_chain_transcript_and_orphan_recovery` | 验证Agent 提示词生命周期中“CLI 对话提示词渲染工具链路转录记录与孤立恢复”这一场景或边界行为。 |
| 73 | `tests/test_agent_prompt_lifecycle.py::test_cli_backend_stream_does_not_fall_back_to_wrapper_persistence` | 验证Agent 提示词生命周期中“CLI 后端流式输出不会回退到包装器持久化”这一场景或边界行为。 |
| 74 | `tests/test_agent_prompt_lifecycle.py::test_provider_executed_builtin_tools_do_not_trigger_local_execution` | 验证Agent 提示词生命周期中“提供商已执行内置工具执行不 trigger 本地执行”这一场景或边界行为。 |
| 75 | `tests/test_agent_prompt_lifecycle.py::test_provider_executed_tool_only_turn_gets_final_fallback_reply` | 验证Agent 提示词生命周期中“提供商已执行工具仅轮次 gets 最终兜底回复”这一场景或边界行为。 |
| 76 | `tests/test_agent_prompt_lifecycle.py::test_provider_executed_tool_preamble_is_replaced_by_tool_summary` | 验证Agent 提示词生命周期中“提供商已执行工具前导说明是 replaced 通过工具摘要”这一场景或边界行为。 |
| 77 | `tests/test_agent_prompt_lifecycle.py::test_native_prompt_hard_stops_local_tool_execution_on_reserved_summary_turn` | 验证Agent 提示词生命周期中“原生提示词强制停止本地工具执行在预留摘要轮次”这一场景或边界行为。 |
| 78 | `tests/test_agent_prompt_lifecycle.py::test_native_prompt_hard_stops_repeated_identical_tool_calls` | 验证Agent 提示词生命周期中“原生提示词强制停止重复相同工具调用”这一场景或边界行为。 |
| 79 | `tests/test_agent_prompt_lifecycle.py::test_session_prompt_no_reply_only_persists_user_message` | 验证Agent 提示词生命周期中“会话提示词没有回复仅持久化用户消息”这一场景或边界行为。 |
| 80 | `tests/test_agent_prompt_lifecycle.py::test_session_prompt_accepts_file_only_parts_and_persists_tool_overrides` | 验证Agent 提示词生命周期中“会话提示词可接受文件仅片段与持久化工具覆盖项”这一场景或边界行为。 |
| 81 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_waits_for_previous_run_and_reloads_latest_history` | 验证Agent 提示词生命周期中“排队的提示词等待针对之前运行与重新加载最新历史记录”这一场景或边界行为。 |
| 82 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_uses_callback_queue_resume_path` | 验证Agent 提示词生命周期中“排队的提示词使用回调队列恢复执行路径”这一场景或边界行为。 |
| 83 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_resume_existing_reuses_active_prompt_instance` | 验证Agent 提示词生命周期中“排队的提示词恢复执行已有复用活跃提示词实例”这一场景或边界行为。 |
| 84 | `tests/test_agent_prompt_lifecycle.py::test_resume_existing_requires_existing_active_instance` | 验证Agent 提示词生命周期中“恢复执行已有要求已有活跃实例”这一场景或边界行为。 |
| 85 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_resume_restores_processor_from_minimal_callback_context` | 验证Agent 提示词生命周期中“排队的提示词恢复执行恢复处理器从 minimal 回调上下文”这一场景或边界行为。 |
| 86 | `tests/test_agent_prompt_lifecycle.py::test_callback_payload_can_restore_request_cursor_from_session_turn_state` | 验证Agent 提示词生命周期中“回调载荷可以恢复请求游标从会话轮次状态”这一场景或边界行为。 |
| 87 | `tests/test_agent_prompt_lifecycle.py::test_callback_payload_prefers_explicit_request_cursor_when_valid` | 验证Agent 提示词生命周期中“回调载荷优先使用显式请求游标当 valid”这一场景或边界行为。 |
| 88 | `tests/test_agent_prompt_lifecycle.py::test_callback_payload_restore_ignores_legacy_runtime_fields_and_uses_session_state` | 验证Agent 提示词生命周期中“回调载荷恢复忽略兼容旧版运行时字段与使用会话状态”这一场景或边界行为。 |
| 89 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_messages_drop_local_mode_and_skill_adapter_noise` | 验证Agent 提示词生命周期中“系统提示词消息移除本地模式与Skill 适配器噪声”这一场景或边界行为。 |
| 90 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_tool_binding_prefers_apply_patch_for_gpt5` | 验证Agent 提示词生命周期中“系统提示词工具绑定优先使用应用补丁针对 GPT-5”这一场景或边界行为。 |
| 91 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_tool_binding_respects_user_tool_overrides` | 验证Agent 提示词生命周期中“系统提示词工具绑定遵守用户工具覆盖项”这一场景或边界行为。 |
| 92 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_tool_binding_describes_plan_mode_controls` | 验证Agent 提示词生命周期中“系统提示词工具绑定 describes 计划模式模式 controls”这一场景或边界行为。 |
| 93 | `tests/test_agent_prompt_lifecycle.py::test_normalize_messages_keeps_latest_user_tool_binding_across_tool_history` | 验证Agent 提示词生命周期中“归一化消息保持最新用户工具绑定 跨工具历史记录”这一场景或边界行为。 |
| 94 | `tests/test_agent_prompt_lifecycle.py::test_normalize_messages_re覆盖_orphan_tool_result_into_user_context` | 验证Agent 提示词生命周期中“归一化消息恢复孤立工具结果到用户上下文”这一场景或边界行为。 |
| 95 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_reasoning_profile_varies_with_reasoning_level` | 验证Agent 提示词生命周期中“系统提示词推理配置档 varies 带有推理级别”这一场景或边界行为。 |
| 96 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_adds_repo_lookup_strategy_for_code_fact_queries` | 验证Agent 提示词生命周期中“系统提示词会添加仓库查找策略针对代码 fact 查询”这一场景或边界行为。 |
| 97 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_adds_academic_lookup_strategy_for_paper_queries` | 验证Agent 提示词生命周期中“系统提示词会添加学术查找策略针对论文查询”这一场景或边界行为。 |
| 98 | `tests/test_agent_prompt_lifecycle.py::test_system_prompt_adds_figure_基于图表证据的_mounted_paper_指导_without_academic_关键词` | 验证Agent 提示词生命周期中“系统提示词会添加图表 基于图表证据的 挂载的论文 指导 不依赖学术 关键词”这一场景或边界行为。 |
| 99 | `tests/test_agent_prompt_lifecycle.py::test_active_prompt_handoff_runs_callback_loop_inline` | 验证Agent 提示词生命周期中“活跃提示词交接运行回调循环内联”这一场景或边界行为。 |
| 100 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompts_catch_up_in_single_session_loop_and_share_final_result` | 验证Agent 提示词生命周期中“排队的提示词追赶补齐在单个会话循环与共享最终结果”这一场景或边界行为。 |
| 101 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_is_rejected_when_active_prompt_errors` | 验证Agent 提示词生命周期中“排队的提示词是被拒绝当活跃提示词错误”这一场景或边界行为。 |
| 102 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_handoff_resolves_all_waiters_from_single_session_loop` | 验证Agent 提示词生命周期中“排队的提示词交接解析全部等待方从单个会话循环”这一场景或边界行为。 |
| 103 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_resume_does_not_start_parallel_loops` | 验证Agent 提示词生命周期中“排队的提示词恢复执行不会 start 并行 循环s”这一场景或边界行为。 |
| 104 | `tests/test_agent_prompt_lifecycle.py::test_callback_stream_uses_saved_control_to_complete_tail` | 验证Agent 提示词生命周期中“回调流式输出使用已保存控制信息到complete 尾部”这一场景或边界行为。 |
| 105 | `tests/test_agent_prompt_lifecycle.py::test_prompt_event_stream_driver_accepts_structured_prompt_event_without_sse_parse` | 验证Agent 提示词生命周期中“提示词事件流式输出驱动器可接受结构化提示词事件不依赖 SSE 解析”这一场景或边界行为。 |
| 106 | `tests/test_agent_prompt_lifecycle.py::test_prompt_event_stream_driver_is_pure_observer_without_synthetic_mutation` | 验证Agent 提示词生命周期中“提示词事件流式输出驱动器是 pure observer 不依赖 synthetic 变更”这一场景或边界行为。 |
| 107 | `tests/test_agent_prompt_lifecycle.py::test_reject_queued_callbacks_replays_error_without_buffered_tail` | 验证Agent 提示词生命周期中“拒绝排队的回调重放错误不依赖 buffered 尾部”这一场景或边界行为。 |
| 108 | `tests/test_agent_prompt_lifecycle.py::test_callback_stream_reconstructs_terminal_tail_from_resolved_callback` | 验证Agent 提示词生命周期中“回调流式输出重建 terminal 尾部从已解析回调”这一场景或边界行为。 |
| 109 | `tests/test_agent_prompt_lifecycle.py::test_callback_stream_replays_resolved_message_when_raw_items_absent` | 验证Agent 提示词生命周期中“回调流式输出重放已解析消息当原始项目不存在”这一场景或边界行为。 |
| 110 | `tests/test_agent_prompt_lifecycle.py::test_callback_stream_backfills_text_from_resolved_message_when_raw_tail_lacks_text` | 验证Agent 提示词生命周期中“回调流式输出回填文本从已解析消息当原始尾部 lacks 文本”这一场景或边界行为。 |
| 111 | `tests/test_agent_prompt_lifecycle.py::test_callback_stream_reconstructs_action_confirm_from_saved_control` | 验证Agent 提示词生命周期中“回调流式输出重建动作确认从已保存控制信息”这一场景或边界行为。 |
| 112 | `tests/test_agent_prompt_lifecycle.py::test_prompt_callback_exposes_resolve_reject_outcomes` | 验证Agent 提示词生命周期中“提示词回调暴露解析拒绝 outcomes”这一场景或边界行为。 |
| 113 | `tests/test_agent_prompt_lifecycle.py::test_prompt_callback_loop_claim_is_single_owner` | 验证Agent 提示词生命周期中“提示词回调循环声明是单个所有者”这一场景或边界行为。 |
| 114 | `tests/test_agent_prompt_lifecycle.py::test_callback_loop_claim_respects_active_prompt_instance_as_single_owner` | 验证Agent 提示词生命周期中“回调循环声明遵守活跃提示词实例作为单个所有者”这一场景或边界行为。 |
| 115 | `tests/test_agent_prompt_lifecycle.py::test_callback_loop_claim_can_resume_paused_prompt_owner` | 验证Agent 提示词生命周期中“回调循环声明可以恢复执行已暂停提示词所有者”这一场景或边界行为。 |
| 116 | `tests/test_agent_prompt_lifecycle.py::test_session_lifecycle_no_longer_exposes_callback_runner_helpers` | 验证Agent 提示词生命周期中“会话生命周期没有 longer 暴露回调运行器 helpers”这一场景或边界行为。 |
| 117 | `tests/test_agent_prompt_lifecycle.py::test_prompt_instance_handoff_claims_callback_before_finishing_owner` | 验证Agent 提示词生命周期中“提示词实例交接 claims 回调之前 finishing 所有者”这一场景或边界行为。 |
| 118 | `tests/test_agent_prompt_lifecycle.py::test_finish_prompt_instance_finishes_when_queue_empty` | 验证Agent 提示词生命周期中“结束提示词实例结束当队列空”这一场景或边界行为。 |
| 119 | `tests/test_agent_prompt_lifecycle.py::test_drain_prompt_callbacks_then_finish_prompt_instance_drains_queue_atomically` | 验证Agent 提示词生命周期中“drain 提示词回调 then 结束提示词实例 drains 队列 atomically”这一场景或边界行为。 |
| 120 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_resolves_waiters_from_latest_finished_message_when_no_pending_turn` | 验证Agent 提示词生命周期中“排队的提示词解析等待方从最新已完成消息当没有待处理轮次”这一场景或边界行为。 |
| 121 | `tests/test_agent_prompt_lifecycle.py::test_queued_prompt_pause_does_not_consume_later_callbacks` | 验证Agent 提示词生命周期中“排队的提示词暂停不会 consume later 回调”这一场景或边界行为。 |
| 122 | `tests/test_agent_prompt_lifecycle.py::test_streaming_parts_publish_delta_events_with_stable_part_ids` | 验证Agent 提示词生命周期中“流式输出片段 publish 增量事件带有稳定片段 ID”这一场景或边界行为。 |

### tests/test_agent_remote_workspace.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 123 | `tests/test_agent_remote_workspace.py::test_agent_chat_request_accepts_workspace_server_id` | 验证远程工作区中“Agent 对话请求可接受工作区服务器 ID”这一场景或边界行为。 |
| 124 | `tests/test_agent_remote_workspace.py::test_agent_chat_request_accepts_structured_content_and_message_meta` | 验证远程工作区中“Agent 对话请求可接受结构化内容与消息元信息”这一场景或边界行为。 |
| 125 | `tests/test_agent_remote_workspace.py::test_remote_workspace_hides_local_only_path_tools` | 验证远程工作区中“远程工作区隐藏本地仅路径工具”这一场景或边界行为。 |
| 126 | `tests/test_agent_remote_workspace.py::test_local_workspace_keeps_path_tools_available` | 验证远程工作区中“本地工作区保持路径工具可用”这一场景或边界行为。 |

### tests/test_agent_runtime_policy.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 127 | `tests/test_agent_runtime_policy.py::test_get_max_tool_steps_respects_reasoning_profiles` | 验证Agent 运行策略中“获取最大工具步骤遵守推理配置档”这一场景或边界行为。 |
| 128 | `tests/test_agent_runtime_policy.py::test_auto_compaction_threshold_uses_shared_settings` | 验证Agent 运行策略中“自动压缩阈值使用共享设置”这一场景或边界行为。 |
| 129 | `tests/test_agent_runtime_policy.py::test_auto_compaction_threshold_effectively_disables_when_off` | 验证Agent 运行策略中“自动压缩阈值 effectively 禁用当关闭”这一场景或边界行为。 |
| 130 | `tests/test_agent_runtime_policy.py::test_is_tool_progress_placeholder_text_matches_preamble_but_not_result_summary` | 验证Agent 运行策略中“是工具进度占位文本文本匹配前导说明但不结果摘要”这一场景或边界行为。 |
| 131 | `tests/test_agent_runtime_policy.py::test_should_hard_stop_after_tool_request_reserves_summary_turn` | 验证Agent 运行策略中“应当强制停止之后工具请求 reserves 摘要轮次”这一场景或边界行为。 |
| 132 | `tests/test_agent_runtime_policy.py::test_tool_call_signature_is_stable_for_same_arguments` | 验证Agent 运行策略中“工具调用签名是稳定针对相同参数”这一场景或边界行为。 |
| 133 | `tests/test_agent_runtime_policy.py::test_should_hard_stop_after_repeated_tool_calls_only_for_identical_back_to_back_calls` | 验证Agent 运行策略中“应当强制停止之后重复工具调用仅针对相同回退到回退调用”这一场景或边界行为。 |

### tests/test_agent_service_claw_stream.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 134 | `tests/test_agent_service_claw_stream.py::test_stream_claw_daemon_chat_preserves_bridge_event_ids` | 验证Agent 模块中“流式输出 Claw 守护进程对话保留桥接事件 ID”这一场景或边界行为。 |
| 135 | `tests/test_agent_service_claw_stream.py::test_stream_claw_daemon_chat_emits_fallback_text_when_done_message_is_empty` | 验证Agent 模块中“流式输出 Claw 守护进程对话发出兜底文本当完成消息是空”这一场景或边界行为。 |
| 136 | `tests/test_agent_service_claw_stream.py::test_stream_claw_daemon_chat_uses_done_tool_results_for_fallback_text` | 验证Agent 模块中“流式输出 Claw 守护进程对话使用完成工具结果针对兜底文本”这一场景或边界行为。 |
| 137 | `tests/test_agent_service_claw_stream.py::test_stream_claw_daemon_chat_appends_fallback_when_streamed_text_is_only_tool_preamble` | 验证Agent 模块中“流式输出 Claw 守护进程对话追加兜底当 streamed 文本是仅工具前导说明”这一场景或边界行为。 |

### tests/test_agent_session_compaction.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 138 | `tests/test_agent_session_compaction.py::test_session_summarize_route_persists_summary_and_reuses_compacted_history` | 验证Agent 模块中“会话摘要生成路由持久化摘要与复用压缩后的历史记录”这一场景或边界行为。 |
| 139 | `tests/test_agent_session_compaction.py::test_auto_overflow_compaction_creates_replay_message_and_excludes_latest_prompt_from_summary` | 验证Agent 模块中“自动溢出压缩创建重放消息与excludes 最新提示词从摘要”这一场景或边界行为。 |
| 140 | `tests/test_agent_session_compaction.py::test_auto_overflow_compaction_replay_preserves_user_message_meta` | 验证Agent 模块中“自动溢出压缩重放保留用户消息元信息”这一场景或边界行为。 |
| 141 | `tests/test_agent_session_compaction.py::test_preflight_auto_compaction_runs_before_answering_new_prompt` | 验证Agent 模块中“预检自动压缩运行之前 answering 新建提示词”这一场景或边界行为。 |
| 142 | `tests/test_agent_session_compaction.py::test_context_overflow_error_triggers_auto_compaction_and_resume` | 验证Agent 模块中“上下文溢出错误 triggers 自动压缩与恢复执行”这一场景或边界行为。 |
| 143 | `tests/test_agent_session_compaction.py::test_standard_prompt_persists_step_lifecycle_parts` | 验证Agent 模块中“标准提示词持久化步骤生命周期片段”这一场景或边界行为。 |
| 144 | `tests/test_agent_session_compaction.py::test_post_step_auto_compaction_persists_completed_assistant_and_rolls_over_message` | 验证Agent 模块中“之后步骤自动压缩持久化已完成研究助手与rolls 超过消息”这一场景或边界行为。 |

### tests/test_agent_session_facades.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 145 | `tests/test_agent_session_facades.py::test_sse_events_parse_and_format_roundtrip` | 验证Agent 模块中“SSE 事件解析与格式读写闭环”这一场景或边界行为。 |
| 146 | `tests/test_agent_session_facades.py::test_sse_events_coerce_prompt_event_兼容ibility` | 验证Agent 模块中“SSE 事件强制转换提示词事件 兼容ibility”这一场景或边界行为。 |

### tests/test_agent_session_retry.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 147 | `tests/test_agent_session_retry.py::test_retryable_model_error_sets_retry_status_and_re覆盖` | 验证Agent 会话重试中“可重试模型错误设置重试状态与恢复”这一场景或边界行为。 |
| 148 | `tests/test_agent_session_retry.py::test_abort_during_retry_persists_aborted_assistant_error` | 验证Agent 会话重试中“中止期间重试持久化已中止研究助手错误”这一场景或边界行为。 |
| 149 | `tests/test_agent_session_retry.py::test_normalize_error_recognizes_auth_and_context_overflow` | 验证Agent 会话重试中“归一化错误识别认证与上下文溢出”这一场景或边界行为。 |
| 150 | `tests/test_agent_session_retry.py::test_normalize_error_recognizes_connection_reset_retry_metadata` | 验证Agent 会话重试中“归一化错误识别连接重置重试元数据”这一场景或边界行为。 |
| 151 | `tests/test_agent_session_retry.py::test_normalize_error_parses_provider_context_overflow_body` | 验证Agent 会话重试中“归一化错误解析提供商上下文溢出 body”这一场景或边界行为。 |
| 152 | `tests/test_agent_session_retry.py::test_normalize_error_parses_provider_auth_and_retryable_bodies` | 验证Agent 会话重试中“归一化错误解析提供商认证与可重试 bodies”这一场景或边界行为。 |
| 153 | `tests/test_agent_session_retry.py::test_normalize_error_marks_missing_api_key_as_auth_error` | 验证Agent 会话重试中“归一化错误标记缺失 API 密钥作为认证错误”这一场景或边界行为。 |
| 154 | `tests/test_agent_session_retry.py::test_normalize_error_marks_timeout_and_network_transport_errors_retryable` | 验证Agent 会话重试中“归一化错误标记超时与网络传输层错误可重试”这一场景或边界行为。 |
| 155 | `tests/test_agent_session_retry.py::test_normalize_error_maps_typed_sdk_exception_classes` | 验证Agent 会话重试中“归一化错误映射类型化 sdk 异常 classes”这一场景或边界行为。 |
| 156 | `tests/test_agent_session_retry.py::test_normalize_error_reads_structured_http_transport_error` | 验证Agent 会话重试中“归一化错误读取结构化 HTTP 传输层错误”这一场景或边界行为。 |
| 157 | `tests/test_agent_session_retry.py::test_normalize_error_rewrites_html_gateway_auth_pages` | 验证Agent 会话重试中“归一化错误重写 HTML 网关认证 pages”这一场景或边界行为。 |
| 158 | `tests/test_agent_session_retry.py::test_normalize_error_marks_openai_404_transport_errors_as_retryable` | 验证Agent 会话重试中“归一化错误标记 OpenAI 404 传输层错误作为可重试”这一场景或边界行为。 |
| 159 | `tests/test_agent_session_retry.py::test_normalize_error_parses_structured_stream_provider_errors` | 验证Agent 会话重试中“归一化错误解析结构化流式输出提供商错误”这一场景或边界行为。 |
| 160 | `tests/test_agent_session_retry.py::test_normalize_error_preserves_transport_runtime_metadata` | 验证Agent 会话重试中“归一化错误保留传输层运行时元数据”这一场景或边界行为。 |
| 161 | `tests/test_agent_session_retry.py::test_retry_delay_prefers_retry_after_headers` | 验证Agent 会话重试中“重试延迟优先使用重试之后响应头”这一场景或边界行为。 |
| 162 | `tests/test_agent_session_retry.py::test_retry_delay_uses_opencode_backoff_defaults_without_headers` | 验证Agent 会话重试中“重试延迟使用 OpenCode backoff 默认不依赖响应头”这一场景或边界行为。 |
| 163 | `tests/test_agent_session_retry.py::test_retryable_does_not_retry_blocked_gateway_errors` | 验证Agent 会话重试中“可重试不会重试被阻断网关错误”这一场景或边界行为。 |

### tests/test_agent_session_revert_diff.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 164 | `tests/test_agent_session_revert_diff.py::test_session_diff_revert_and_unrevert` | 验证Agent 模块中“会话差异回滚与取消回滚”这一场景或边界行为。 |
| 165 | `tests/test_agent_session_revert_diff.py::test_revert_cleanup_runs_before_next_prompt` | 验证Agent 模块中“回滚 cleanup 运行之前下一步提示词”这一场景或边界行为。 |
| 166 | `tests/test_agent_session_revert_diff.py::test_snapshot_based_diff_and_revert_handles_external_file_change` | 验证Agent 模块中“快照 based 差异与回滚处理外部文件 change”这一场景或边界行为。 |
| 167 | `tests/test_agent_session_revert_diff.py::test_aborted_step_still_persists_snapshot_patch_and_can_revert` | 验证Agent 模块中“已中止步骤仍然持久化快照补丁与可以回滚”这一场景或边界行为。 |
| 168 | `tests/test_agent_session_revert_diff.py::test_remote_workspace_diff_revert_and_unrevert` | 验证Agent 模块中“远程工作区差异回滚与取消回滚”这一场景或边界行为。 |
| 169 | `tests/test_agent_session_revert_diff.py::test_revert_aggregates_multiple_patch_steps_for_same_file` | 验证Agent 模块中“回滚聚合多个补丁步骤针对相同文件”这一场景或边界行为。 |
| 170 | `tests/test_agent_session_revert_diff.py::test_snapshot_patch_ignores_runtime_noise_files` | 验证Agent 模块中“快照补丁忽略运行时噪声文件”这一场景或边界行为。 |
| 171 | `tests/test_agent_session_revert_diff.py::test_snapshot_diff_full_ignores_runtime_noise_files` | 验证Agent 模块中“快照差异完整忽略运行时噪声文件”这一场景或边界行为。 |
| 172 | `tests/test_agent_session_revert_diff.py::test_revert_and_unrevert_reject_busy_session` | 验证Agent 模块中“回滚与取消回滚拒绝忙碌会话”这一场景或边界行为。 |

### tests/test_agent_session_runtime.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 173 | `tests/test_agent_session_runtime.py::test_normalize_messages_injects_hard_output_constraint_prompt` | 验证Agent 会话运行时中“归一化消息注入强制输出约束提示词”这一场景或边界行为。 |
| 174 | `tests/test_agent_session_runtime.py::test_normalize_messages_includes_opencode_provider_environment_and_skills_sections` | 验证Agent 会话运行时中“归一化消息包含 OpenCode 提供商环境与Skills 章节”这一场景或边界行为。 |
| 175 | `tests/test_agent_session_runtime.py::test_prepare_loop_messages_injects_opencode_plan_reminder` | 验证Agent 会话运行时中“准备循环消息注入 OpenCode 计划模式提醒”这一场景或边界行为。 |
| 176 | `tests/test_agent_session_runtime.py::test_build_plan_mode_reminder_materializes_local_plan_parent_directory` | 验证Agent 会话运行时中“构建模式计划模式模式提醒物化本地计划模式父级目录”这一场景或边界行为。 |
| 177 | `tests/test_agent_session_runtime.py::test_prepare_loop_messages_injects_build_switch_reminder_after_plan` | 验证Agent 会话运行时中“准备循环消息注入构建模式 switch 提醒之后计划模式”这一场景或边界行为。 |
| 178 | `tests/test_agent_session_runtime.py::test_session_message_route_passes_agent_backend_and_active_skill_ids` | 验证Agent 会话运行时中“会话消息路由传递 Agent 后端与活跃 Skill ID”这一场景或边界行为。 |
| 179 | `tests/test_agent_session_runtime.py::test_session_create_and_prompt_route_persist_backend_selection` | 验证Agent 会话运行时中“会话创建与提示词路由持久化后端选择”这一场景或边界行为。 |
| 180 | `tests/test_agent_session_runtime.py::test_stream_chat_repairs_explicit_length_constraint` | 验证Agent 会话运行时中“流式输出对话修复显式 length 约束”这一场景或边界行为。 |
| 181 | `tests/test_agent_session_runtime.py::test_stream_chat_injects_opencode_max_steps_prompt_on_last_step` | 验证Agent 会话运行时中“流式输出对话注入 OpenCode 最大步骤提示词在 last 步骤”这一场景或边界行为。 |
| 182 | `tests/test_agent_session_runtime.py::test_session_prompt_route_reloads_latest_transcript_between_steps` | 验证Agent 会话运行时中“会话提示词路由重新加载最新转录记录之间步骤”这一场景或边界行为。 |
| 183 | `tests/test_agent_session_runtime.py::test_plan_exit_confirmation_switches_to_build_and_reloads_transcript` | 验证Agent 会话运行时中“计划模式退出确认 switches到构建模式与重新加载转录记录”这一场景或边界行为。 |
| 184 | `tests/test_agent_session_runtime.py::test_project_and_session_routes` | 验证Agent 会话运行时中“项目与会话路由”这一场景或边界行为。 |
| 185 | `tests/test_agent_session_runtime.py::test_session_create_requires_workspace_binding` | 验证Agent 会话运行时中“会话创建要求工作区绑定”这一场景或边界行为。 |
| 186 | `tests/test_agent_session_runtime.py::test_session_prompt_requires_workspace_for_new_session` | 验证Agent 会话运行时中“会话提示词要求工作区针对新建会话”这一场景或边界行为。 |
| 187 | `tests/test_agent_session_runtime.py::test_session_prompt_reuses_existing_workspace_when_request_omits_workspace` | 验证Agent 会话运行时中“会话提示词复用已有工作区当请求省略工作区”这一场景或边界行为。 |
| 188 | `tests/test_agent_session_runtime.py::test_agent_chat_requires_workspace_binding` | 验证Agent 会话运行时中“Agent 对话要求工作区绑定”这一场景或边界行为。 |
| 189 | `tests/test_agent_session_runtime.py::test_session_delete_message_route_respects_busy_guard` | 验证Agent 会话运行时中“会话删除消息路由遵守忙碌保护”这一场景或边界行为。 |
| 190 | `tests/test_agent_session_runtime.py::test_session_delete_message_and_part_routes` | 验证Agent 会话运行时中“会话删除消息与片段路由”这一场景或边界行为。 |
| 191 | `tests/test_agent_session_runtime.py::test_load_agent_messages_preserves_user_file_parts_system_and_tools` | 验证Agent 会话运行时中“加载 Agent 消息保留用户文件片段系统与工具”这一场景或边界行为。 |
| 192 | `tests/test_agent_session_runtime.py::test_agent_runtime_state_persists_todos` | 验证Agent 会话运行时中“Agent 运行时状态持久化 todos”这一场景或边界行为。 |
| 193 | `tests/test_agent_session_runtime.py::test_agent_chat_persists_into_new_session_store` | 验证Agent 会话运行时中“Agent 对话持久化到新建会话存储”这一场景或边界行为。 |
| 194 | `tests/test_agent_session_runtime.py::test_legacy_agent_chat_preserves_structured_user_parts_and_tools` | 验证Agent 会话运行时中“兼容旧版 Agent 对话保留结构化用户片段与工具”这一场景或边界行为。 |
| 195 | `tests/test_agent_session_runtime.py::test_legacy_agent_chat_persists_active_skill_ids_on_user_message` | 验证Agent 会话运行时中“兼容旧版 Agent 对话持久化活跃 Skill ID 在用户消息”这一场景或边界行为。 |
| 196 | `tests/test_agent_session_runtime.py::test_get_session_turn_state_tracks_latest_pending_user` | 验证Agent 会话运行时中“获取会话轮次状态跟踪最新待处理用户”这一场景或边界行为。 |
| 197 | `tests/test_agent_session_runtime.py::test_session_prompt_route_streams_and_persists` | 验证Agent 会话运行时中“会话提示词路由 streams与持久化”这一场景或边界行为。 |
| 198 | `tests/test_agent_session_runtime.py::test_session_prompt_route_persists_effective_reasoning_level_on_user_message` | 验证Agent 会话运行时中“会话提示词路由持久化 effective 推理级别在用户消息”这一场景或边界行为。 |
| 199 | `tests/test_agent_session_runtime.py::test_session_prompt_route_persists_opencode_user_message_fields` | 验证Agent 会话运行时中“会话提示词路由持久化 OpenCode 用户消息字段”这一场景或边界行为。 |
| 200 | `tests/test_agent_session_runtime.py::test_session_prompt_route_passes_persistence_into_stream_chat` | 验证Agent 会话运行时中“会话提示词路由传递持久化到流式输出对话”这一场景或边界行为。 |
| 201 | `tests/test_agent_session_runtime.py::test_session_prompt_persists_reasoning_parts_and_tokens` | 验证Agent 会话运行时中“会话提示词持久化推理片段与token”这一场景或边界行为。 |
| 202 | `tests/test_agent_session_runtime.py::test_session_prompt_abort_marks_inflight_tool_part_failed` | 验证Agent 会话运行时中“会话提示词中止标记 inflight 工具片段失败”这一场景或边界行为。 |
| 203 | `tests/test_agent_session_runtime.py::test_session_prompt_rolls_over_assistant_message_after_tool_step` | 验证Agent 会话运行时中“会话提示词滚动超过研究助手消息之后工具步骤”这一场景或边界行为。 |
| 204 | `tests/test_agent_session_runtime.py::test_session_prompt_executes_local_shell_and_continues` | 验证Agent 会话运行时中“会话提示词执行本地 Shell与继续s”这一场景或边界行为。 |
| 205 | `tests/test_agent_session_runtime.py::test_wrap_stream_persists_text_part_incrementally_before_stream_completion` | 验证Agent 会话运行时中“包装流式输出持久化文本片段 incrementally 之前流式输出 completion”这一场景或边界行为。 |
| 206 | `tests/test_agent_session_runtime.py::test_wrap_stream_respects_explicit_text_and_reasoning_boundaries` | 验证Agent 会话运行时中“包装流式输出遵守显式文本与推理 boundaries”这一场景或边界行为。 |
| 207 | `tests/test_agent_session_runtime.py::test_agent_project_repository_upsert_reuses_existing_worktree` | 验证Agent 会话运行时中“Agent 项目仓储插入或更新复用已有 worktree”这一场景或边界行为。 |
| 208 | `tests/test_agent_session_runtime.py::test_wrap_stream_persists_empty_reasoning_metadata_parts` | 验证Agent 会话运行时中“包装流式输出持久化空推理元数据片段”这一场景或边界行为。 |
| 209 | `tests/test_agent_session_runtime.py::test_wrap_stream_persists_reasoning_ascii_spacing_across_deltas` | 验证Agent 会话运行时中“包装流式输出持久化推理 ASCII 空格 跨越 增量”这一场景或边界行为。 |
| 210 | `tests/test_agent_session_runtime.py::test_wrap_stream_persists_text_metadata_parts` | 验证Agent 会话运行时中“包装流式输出持久化文本元数据片段”这一场景或边界行为。 |
| 211 | `tests/test_agent_session_runtime.py::test_wrap_stream_persists_tool_metadata_parts` | 验证Agent 会话运行时中“包装流式输出持久化工具元数据片段”这一场景或边界行为。 |
| 212 | `tests/test_agent_session_runtime.py::test_wrap_stream_merges_tool_display_data_into_persisted_tool_part` | 验证Agent 会话运行时中“包装流式输出合并工具展示数据到已持久化工具片段”这一场景或边界行为。 |
| 213 | `tests/test_agent_session_runtime.py::test_wrap_stream_persists_usage_provider_metadata_on_assistant_message` | 验证Agent 会话运行时中“包装流式输出持久化 usage 提供商元数据在研究助手消息”这一场景或边界行为。 |
| 214 | `tests/test_agent_session_runtime.py::test_wrap_stream_persists_tool_input_lifecycle_before_confirmation` | 验证Agent 会话运行时中“包装流式输出持久化工具输入生命周期之前确认”这一场景或边界行为。 |
| 215 | `tests/test_agent_session_runtime.py::test_tool_input_delta_publishes_part_delta_event` | 验证Agent 会话运行时中“工具输入增量发布片段增量事件”这一场景或边界行为。 |
| 216 | `tests/test_agent_session_runtime.py::test_run_model_turn_drops_mirrored_reasoning_text_before_tool_calls` | 验证Agent 会话运行时中“运行模型轮次移除 mirrored 推理文本之前工具调用”这一场景或边界行为。 |
| 217 | `tests/test_agent_session_runtime.py::test_run_model_turn_emits_explicit_content_lifecycle_events` | 验证Agent 会话运行时中“运行模型轮次发出显式内容生命周期事件”这一场景或边界行为。 |
| 218 | `tests/test_agent_session_runtime.py::test_run_model_turn_reasoning_parts_keep_ascii_spacing` | 验证Agent 会话运行时中“运行模型轮次推理片段保留 ASCII 空格”这一场景或边界行为。 |
| 219 | `tests/test_agent_session_runtime.py::test_run_model_turn_preserves_reasoning_metadata` | 验证Agent 会话运行时中“运行模型轮次保留推理元数据”这一场景或边界行为。 |
| 220 | `tests/test_agent_session_runtime.py::test_run_model_turn_preserves_text_metadata` | 验证Agent 会话运行时中“运行模型轮次保留文本元数据”这一场景或边界行为。 |
| 221 | `tests/test_agent_session_runtime.py::test_run_model_turn_preserves_tool_call_metadata` | 验证Agent 会话运行时中“运行模型轮次保留工具调用元数据”这一场景或边界行为。 |
| 222 | `tests/test_agent_session_runtime.py::test_session_fork_copies_messages_up_to_cutoff` | 验证Agent 会话运行时中“会话 fork 复制消息补齐到cutoff”这一场景或边界行为。 |
| 223 | `tests/test_agent_session_runtime.py::test_load_agent_messages_splits_assistant_steps_and_preserves_reasoning` | 验证Agent 会话运行时中“加载 Agent 消息 splits 研究助手步骤与保留推理”这一场景或边界行为。 |
| 224 | `tests/test_agent_session_runtime.py::test_load_agent_messages_preserves_reasoning_metadata_parts` | 验证Agent 会话运行时中“加载 Agent 消息保留推理元数据片段”这一场景或边界行为。 |
| 225 | `tests/test_agent_session_runtime.py::test_load_agent_messages_repairs_ascii_reasoning_spacing` | 验证Agent 会话运行时中“加载 Agent 消息修复 ASCII 推理空格”这一场景或边界行为。 |
| 226 | `tests/test_agent_session_runtime.py::test_merge_reasoning_fragments_keeps_cjk_compact` | 验证Agent 会话运行时中“合并推理片段保持中日韩字符紧凑”这一场景或边界行为。 |
| 227 | `tests/test_agent_session_runtime.py::test_merge_reasoning_fragments_keeps_ascii_words_separated_after_multiword_chunk` | 验证Agent 会话运行时中“合并推理片段保持 ASCII 词分隔之后 multiword chunk”这一场景或边界行为。 |
| 228 | `tests/test_agent_session_runtime.py::test_load_agent_messages_preserves_text_metadata_parts` | 验证Agent 会话运行时中“加载 Agent 消息保留文本元数据片段”这一场景或边界行为。 |
| 229 | `tests/test_agent_session_runtime.py::test_load_agent_messages_reconstructs_tool_calls_and_tool_results` | 验证Agent 会话运行时中“加载 Agent 消息重建工具调用与工具结果”这一场景或边界行为。 |

### tests/test_agent_tools_status.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 230 | `tests/test_agent_tools_status.py::test_get_system_status_serializes_latest_runs_inside_session` | 验证Agent 模块中“获取系统状态序列化最新运行内部会话”这一场景或边界行为。 |

### tests/test_agent_workspace_ssh.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 231 | `tests/test_agent_workspace_ssh.py::test_format_ssh_exception_reports_real_ssh_banner` | 验证Agent 模块中“格式 SSH 异常报告 real SSH 横幅”这一场景或边界行为。 |
| 232 | `tests/test_agent_workspace_ssh.py::test_format_ssh_exception_reports_non_ssh_banner` | 验证Agent 模块中“格式 SSH 异常报告非 SSH 横幅”这一场景或边界行为。 |
| 233 | `tests/test_agent_workspace_ssh.py::test_translate_workspace_error_uses_neutral_banner_message` | 验证Agent 模块中“翻译工作区错误使用 neutral 横幅消息”这一场景或边界行为。 |

### tests/test_analysis_levels.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 234 | `tests/test_analysis_levels.py::test_normalize_analysis_levels_fallback_to_medium` | 验证分析级别中“归一化分析级别兜底到medium”这一场景或边界行为。 |
| 235 | `tests/test_analysis_levels.py::test_normalize_reasoning_levels_fallback_to_default` | 验证分析级别中“归一化推理级别兜底到默认”这一场景或边界行为。 |
| 236 | `tests/test_analysis_levels.py::test_resolve_paper_analysis_levels_syncs_reasoning_to_detail` | 验证分析级别中“解析论文分析级别同步推理到详情”这一场景或边界行为。 |
| 237 | `tests/test_analysis_levels.py::test_resolve_paper_analysis_levels_supports_legacy_reasoning_only_calls` | 验证分析级别中“解析论文分析级别支持兼容旧版推理仅调用”这一场景或边界行为。 |
| 238 | `tests/test_analysis_levels.py::test_detail_profiles_expand_with_higher_levels` | 验证分析级别中“详情配置档 expand 带有 higher 级别”这一场景或边界行为。 |
| 239 | `tests/test_analysis_levels.py::test_reasoning_profile_uses_base_settings_and_level` | 验证分析级别中“推理配置档使用基础设置与级别”这一场景或边界行为。 |
| 240 | `tests/test_analysis_levels.py::test_build_deep_prompt_mentions_selected_detail_level` | 验证分析级别中“构建模式深度提示词 mentions 选中的详情级别”这一场景或边界行为。 |
| 241 | `tests/test_analysis_levels.py::test_build_reasoning_prompt_keeps_full_evidence_text` | 验证分析级别中“构建模式推理提示词保持完整证据文本”这一场景或边界行为。 |

### tests/test_analysis_repository.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 242 | `tests/test_analysis_repository.py::test_upsert_skim_uses_first_innovation_when_one_liner_missing` | 验证分析仓储中“插入或更新粗读使用首个 innovation 当 one liner 缺失”这一场景或边界行为。 |

### tests/test_app_startup.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 243 | `tests/test_app_startup.py::test_api_startup_uses_explicit_bootstrap` | 验证应用 startup中“API 启动使用显式启动初始化”这一场景或边界行为。 |

### tests/test_aris_feature_matrix.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 244 | `tests/test_aris_feature_matrix.py::test_aris_catalog_覆盖_all_workflows_and_actions` | 验证ARIS 功能矩阵中“ARIS 目录 覆盖 全部工作流与动作”这一场景或边界行为。 |
| 245 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[init_repo]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：初始化仓库。 |
| 246 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[autoresearch_claude_code]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：自动研究_Claude Code。 |
| 247 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[literature_review]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：文献综述。 |
| 248 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[想法发现]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：想法发现。 |
| 249 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[novelty_check]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：新颖性_检查。 |
| 250 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[research_review]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：研究评审。 |
| 251 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[run_experiment]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：运行实验。 |
| 252 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[experiment_audit]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：实验审计。 |
| 253 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[auto_review_loop]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：自动评审循环。 |
| 254 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[paper_plan]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：论文规划。 |
| 255 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[paper_figure]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：论文图表。 |
| 256 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[paper_write]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：论文撰写。 |
| 257 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[paper_compile]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：论文编译。 |
| 258 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[paper_writing]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：论文写作。 |
| 259 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[rebuttal]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：回复审稿。 |
| 260 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[paper_improvement]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：论文改进。 |
| 261 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[full_pipeline]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：完整流水线。 |
| 262 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[monitor_experiment]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：实验监控。 |
| 263 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[同步工作区]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：同步工作区。 |
| 264 | `tests/test_aris_feature_matrix.py::test_aris_workflow_smoke_matrix[custom_run]` | 验证ARIS 功能矩阵中“ARIS 工作流烟测矩阵”这一场景或边界行为。参数化样例：自定义运行。 |
| 265 | `tests/test_aris_feature_matrix.py::test_aris_action_smoke_matrix[continue]` | 验证ARIS 功能矩阵中“ARIS 动作烟测矩阵”这一场景或边界行为。参数化样例：继续。 |
| 266 | `tests/test_aris_feature_matrix.py::test_aris_action_smoke_matrix[run_experiment]` | 验证ARIS 功能矩阵中“ARIS 动作烟测矩阵”这一场景或边界行为。参数化样例：运行实验。 |
| 267 | `tests/test_aris_feature_matrix.py::test_aris_action_smoke_matrix[monitor]` | 验证ARIS 功能矩阵中“ARIS 动作烟测矩阵”这一场景或边界行为。参数化样例：监控。 |
| 268 | `tests/test_aris_feature_matrix.py::test_aris_action_smoke_matrix[review]` | 验证ARIS 功能矩阵中“ARIS 动作烟测矩阵”这一场景或边界行为。参数化样例：评审。 |
| 269 | `tests/test_aris_feature_matrix.py::test_aris_action_smoke_matrix[retry]` | 验证ARIS 功能矩阵中“ARIS 动作烟测矩阵”这一场景或边界行为。参数化样例：重试。 |
| 270 | `tests/test_aris_feature_matrix.py::test_aris_action_smoke_matrix[同步工作区]` | 验证ARIS 功能矩阵中“ARIS 动作烟测矩阵”这一场景或边界行为。参数化样例：同步工作区。 |
| 271 | `tests/test_aris_feature_matrix.py::test_aris_action_smoke_matrix[custom]` | 验证ARIS 功能矩阵中“ARIS 动作烟测矩阵”这一场景或边界行为。参数化样例：自定义。 |

### tests/test_aris_prompt_templates.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 272 | `tests/test_aris_prompt_templates.py::test_aris_skill_template_加载器_and_兼容_preamble` | 验证ARIS 提示词模板中“ARIS Skill 模板 加载器与兼容 前导说明”这一场景或边界行为。 |

### tests/test_aris_router_matrix.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 273 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[literature_review]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：文献综述。 |
| 274 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[想法发现]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：想法发现。 |
| 275 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[novelty_check]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：新颖性_检查。 |
| 276 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[research_review]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：研究评审。 |
| 277 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[run_experiment]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：运行实验。 |
| 278 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[experiment_audit]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：实验审计。 |
| 279 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[auto_review_loop]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：自动评审循环。 |
| 280 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[paper_plan]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：论文规划。 |
| 281 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[paper_figure]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：论文图表。 |
| 282 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[paper_write]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：论文撰写。 |
| 283 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[paper_compile]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：论文编译。 |
| 284 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[paper_writing]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：论文写作。 |
| 285 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[rebuttal]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：回复审稿。 |
| 286 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[paper_improvement]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：论文改进。 |
| 287 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[full_pipeline]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：完整流水线。 |
| 288 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[monitor_experiment]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：实验监控。 |
| 289 | `tests/test_aris_router_matrix.py::test_aris_router_active_workflow_create_and_retry[同步工作区]` | 验证ARIS 路由矩阵中“ARIS 路由活跃工作流创建与重试”这一场景或边界行为。参数化样例：同步工作区。 |
| 290 | `tests/test_aris_router_matrix.py::test_aris_router_planned_workflows_rejected[init_repo]` | 验证ARIS 路由矩阵中“ARIS 路由计划中的工作流被拒绝”这一场景或边界行为。参数化样例：初始化仓库。 |
| 291 | `tests/test_aris_router_matrix.py::test_aris_router_planned_workflows_rejected[autoresearch_claude_code]` | 验证ARIS 路由矩阵中“ARIS 路由计划中的工作流被拒绝”这一场景或边界行为。参数化样例：自动研究_Claude Code。 |
| 292 | `tests/test_aris_router_matrix.py::test_aris_router_planned_workflows_rejected[custom_run]` | 验证ARIS 路由矩阵中“ARIS 路由计划中的工作流被拒绝”这一场景或边界行为。参数化样例：自定义运行。 |
| 293 | `tests/test_aris_router_matrix.py::test_aris_router_action_matrix[continue]` | 验证ARIS 路由矩阵中“ARIS 路由动作矩阵”这一场景或边界行为。参数化样例：继续。 |
| 294 | `tests/test_aris_router_matrix.py::test_aris_router_action_matrix[run_experiment]` | 验证ARIS 路由矩阵中“ARIS 路由动作矩阵”这一场景或边界行为。参数化样例：运行实验。 |
| 295 | `tests/test_aris_router_matrix.py::test_aris_router_action_matrix[monitor]` | 验证ARIS 路由矩阵中“ARIS 路由动作矩阵”这一场景或边界行为。参数化样例：监控。 |
| 296 | `tests/test_aris_router_matrix.py::test_aris_router_action_matrix[review]` | 验证ARIS 路由矩阵中“ARIS 路由动作矩阵”这一场景或边界行为。参数化样例：评审。 |
| 297 | `tests/test_aris_router_matrix.py::test_aris_router_action_matrix[retry]` | 验证ARIS 路由矩阵中“ARIS 路由动作矩阵”这一场景或边界行为。参数化样例：重试。 |
| 298 | `tests/test_aris_router_matrix.py::test_aris_router_action_matrix[同步工作区]` | 验证ARIS 路由矩阵中“ARIS 路由动作矩阵”这一场景或边界行为。参数化样例：同步工作区。 |
| 299 | `tests/test_aris_router_matrix.py::test_aris_router_action_matrix[custom]` | 验证ARIS 路由矩阵中“ARIS 路由动作矩阵”这一场景或边界行为。参数化样例：自定义。 |
| 300 | `tests/test_aris_router_matrix.py::test_aris_router_remote_run_plans_session_and_workspace` | 验证ARIS 路由矩阵中“ARIS 路由远程运行规划会话与工作区”这一场景或边界行为。 |
| 301 | `tests/test_aris_router_matrix.py::test_aris_router_remote_run_preserves_custom_gpu_metadata` | 验证ARIS 路由矩阵中“ARIS 路由远程运行保留自定义 GPU 元数据”这一场景或边界行为。 |
| 302 | `tests/test_aris_router_matrix.py::test_aris_router_remote_run_preserves_parallel_experiment_metadata` | 验证ARIS 路由矩阵中“ARIS 路由远程运行保留并行实验元数据”这一场景或边界行为。 |

### tests/test_aris_smoke_productization.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 303 | `tests/test_aris_smoke_productization.py::test_extract_aris_smoke_items_parses_json_payload` | 验证ARIS 烟测 产品化中“提取 ARIS 烟测项目解析 JSON 载荷”这一场景或边界行为。 |
| 304 | `tests/test_aris_smoke_productization.py::test_build_aris_smoke_command_quick_uses_python` | 验证ARIS 烟测 产品化中“构建模式 ARIS 烟测命令 快速 使用 Python”这一场景或边界行为。 |
| 305 | `tests/test_aris_smoke_productization.py::test_build_aris_smoke_command_full_requires_pwsh` | 验证ARIS 烟测 产品化中“构建模式 ARIS 烟测命令完整要求 PowerShell”这一场景或边界行为。 |
| 306 | `tests/test_aris_smoke_productization.py::test_run_aris_smoke_job_writes_task_result` | 验证ARIS 烟测 产品化中“运行 ARIS 烟测 任务 写入任务结果”这一场景或边界行为。 |

### tests/test_arxiv_client.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 307 | `tests/test_arxiv_client.py::test_build_arxiv_query_has_no_default_date_filter` | 验证arxiv 客户端中“构建模式 arxiv 查询具有没有默认日期过滤”这一场景或边界行为。 |
| 308 | `tests/test_arxiv_client.py::test_build_arxiv_query_appends_explicit_date_range` | 验证arxiv 客户端中“构建模式 arxiv 查询追加显式日期 range”这一场景或边界行为。 |
| 309 | `tests/test_arxiv_client.py::test_build_arxiv_query_preserves_existing_submitted_date_filter` | 验证arxiv 客户端中“构建模式 arxiv 查询保留已有 submitted 日期过滤”这一场景或边界行为。 |

### tests/test_auth_security.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 310 | `tests/test_auth_security.py::test_validate_auth_configuration_requires_secret_when_auth_enabled` | 验证认证安全中“校验 认证配置要求 secret 当认证启用”这一场景或边界行为。 |
| 311 | `tests/test_auth_security.py::test_validate_auth_configuration_requires_hashed_password_outside_dev` | 验证认证安全中“校验 认证配置要求 hashed password 外部 dev”这一场景或边界行为。 |
| 312 | `tests/test_auth_security.py::test_authenticate_user_accepts_bcrypt_hash` | 验证认证安全中“authenticate 用户可接受 bcrypt hash”这一场景或边界行为。 |
| 313 | `tests/test_auth_security.py::test_extract_request_token_rejects_query_token_for_regular_api_path` | 验证认证安全中“提取请求令牌拒绝查询令牌针对 regular API 路径”这一场景或边界行为。 |
| 314 | `tests/test_auth_security.py::test_asset_access_token_is_path_scoped_and_short_lived` | 验证认证安全中“asset access 令牌是路径 scoped与短 lived”这一场景或边界行为。 |

### tests/test_brief_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 315 | `tests/test_brief_service.py::test_render_markdown_fragment_renders_lists_and_bold` | 验证日报服务中“渲染 Markdown 片段 渲染列表与bold”这一场景或边界行为。 |
| 316 | `tests/test_brief_service.py::test_render_markdown_fragment_escapes_raw_html` | 验证日报服务中“渲染 Markdown 片段 escapes 原始 HTML”这一场景或边界行为。 |
| 317 | `tests/test_brief_service.py::test_repair_legacy_daily_brief_html_upgrades_plain_markdown_block` | 验证日报服务中“修复兼容旧版日报日报 HTML upgrades plain Markdown 块”这一场景或边界行为。 |

### tests/test_claw_bridge_runtime.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 318 | `tests/test_claw_bridge_runtime.py::test_get_settings_respects_researchos_data_dir` | 验证Claw 桥接运行时中“获取设置遵守 researchos 数据目录”这一场景或边界行为。 |
| 319 | `tests/test_claw_bridge_runtime.py::test_bridge_local_path_to_relative_maps_bridge_workspace_paths` | 验证Claw 桥接运行时中“桥接本地路径到相对 映射桥接工作区路径”这一场景或边界行为。 |

### tests/test_claw_runtime_manager.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 320 | `tests/test_claw_runtime_manager.py::test_build_runtime_spec_uses_bridge_daemon_and_context_env` | 验证Claw 运行时管理器中“构建模式运行时规格使用桥接守护进程与上下文环境变量”这一场景或边界行为。 |

### tests/test_cli_agent_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 321 | `tests/test_cli_agent_service.py::test_claw_normalizes_to_auto_and_keeps_local_execution_when_workspace_server_id_present` | 验证CLI Agent 服务中“Claw 归一化到自动与保持本地执行当工作区服务器 ID present”这一场景或边界行为。 |
| 322 | `tests/test_cli_agent_service.py::test_run_local_claw_remote_bridge_sets_allowed_tools_and_context_env` | 验证CLI Agent 服务中“运行本地 Claw 远程桥接设置 allowed 工具与上下文环境变量”这一场景或边界行为。 |
| 323 | `tests/test_cli_agent_service.py::test_execute_prompt_routes_remote_claw_through_local_bridge` | 验证CLI Agent 服务中“执行提示词路由远程 Claw 通过本地桥接”这一场景或边界行为。 |
| 324 | `tests/test_cli_agent_service.py::test_local_claw_workspace_settings_use_packaged_mcp_flag_when_frozen` | 验证CLI Agent 服务中“本地 Claw 工作区设置使用打包 MCP 标志当冻结”这一场景或边界行为。 |
| 325 | `tests/test_cli_agent_service.py::test_local_claw_workspace_settings_propagate_runtime_storage_overrides` | 验证CLI Agent 服务中“本地 Claw 工作区设置 propagate 运行时 存储 覆盖项”这一场景或边界行为。 |
| 326 | `tests/test_cli_agent_service.py::test_packaged_missing_claw_message_does_not_point_to_meipass_build_dir` | 验证CLI Agent 服务中“打包缺失 Claw 消息不会 point到meipass 构建模式目录”这一场景或边界行为。 |
| 327 | `tests/test_cli_agent_service.py::test_frozen_claw_resolves_bundled_binary_from_env_override` | 验证CLI Agent 服务中“冻结 Claw 解析 打包的 二进制从环境变量覆盖项”这一场景或边界行为。 |
| 328 | `tests/test_cli_agent_service.py::test_frozen_claw_resolves_repo_bundled_binary_without_env_override` | 验证CLI Agent 服务中“冻结 Claw 解析仓库 打包的 二进制不依赖环境变量覆盖项”这一场景或边界行为。 |
| 329 | `tests/test_cli_agent_service.py::test_packaged_temp_runtime_still_prefers_install_dir_claw_binary` | 验证CLI Agent 服务中“打包 temp 运行时仍然优先使用 install 目录 Claw 二进制”这一场景或边界行为。 |

### tests/test_collect_external_ingest.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 330 | `tests/test_collect_external_ingest.py::test_ingest_external_entries_persists_openalex_metadata_and_dedupes` | 验证collect 外部采集入库中“采集入库外部条目持久化 OpenAlex 元数据与去重”这一场景或边界行为。 |
| 331 | `tests/test_collect_external_ingest.py::test_search_external_literature_route_filters_dates_and_sorts` | 验证collect 外部采集入库中“检索外部文献路由过滤 日期与sorts”这一场景或边界行为。 |

### tests/test_feishu_notification.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 332 | `tests/test_feishu_notification.py::test_feishu_config_repository_roundtrip` | 验证飞书通知中“飞书配置仓储读写闭环”这一场景或边界行为。 |
| 333 | `tests/test_feishu_notification.py::test_feishu_service_off_mode_short_circuits` | 验证飞书通知中“飞书服务关闭模式 短 circuits”这一场景或边界行为。 |
| 334 | `tests/test_feishu_notification.py::test_notify_project_run_status_sends_feishu_checkpoint` | 验证飞书通知中“通知 项目运行状态 sends 飞书检查点”这一场景或边界行为。 |
| 335 | `tests/test_feishu_notification.py::test_notify_project_run_status_interactive_starts_waiter` | 验证飞书通知中“通知 项目运行状态交互式 starts 等待方”这一场景或边界行为。 |
| 336 | `tests/test_feishu_notification.py::test_await_interactive_checkpoint_reply_processes_approve` | 验证飞书通知中“await 交互式检查点回复 processes 批准”这一场景或边界行为。 |
| 337 | `tests/test_feishu_notification.py::test_feishu_service_poll_reply_returns_timeout` | 验证飞书通知中“飞书服务 poll 回复返回超时”这一场景或边界行为。 |
| 338 | `tests/test_feishu_notification.py::test_interactive_timeout_auto_approve` | 验证飞书通知中“交互式超时自动 批准”这一场景或边界行为。 |
| 339 | `tests/test_feishu_notification.py::test_interactive_timeout_wait_keeps_paused` | 验证飞书通知中“交互式超时等待保持已暂停”这一场景或边界行为。 |
| 340 | `tests/test_feishu_notification.py::test_feishu_service_marks_nonzero_code_as_error` | 验证飞书通知中“飞书服务标记 nonzero 代码作为错误”这一场景或边界行为。 |

### tests/test_figure_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 341 | `tests/test_figure_service.py::test_resolve_extract_mode_supports_mineru_alias_and_defaults` | 验证figure 服务中“解析提取模式支持 mineru alias与默认”这一场景或边界行为。 |
| 342 | `tests/test_figure_service.py::test_compose_ocr_candidate_markdown_ignores_caption_only_payload` | 验证figure 服务中“组合 OCR 候选 Markdown 忽略标题仅载荷”这一场景或边界行为。 |
| 343 | `tests/test_figure_service.py::test_compose_ocr_candidate_markdown_converts_html_table_to_markdown` | 验证figure 服务中“组合 OCR 候选 Markdown converts HTML table到Markdown”这一场景或边界行为。 |
| 344 | `tests/test_figure_service.py::test_description_payload_roundtrip_preserves_candidate_source` | 验证figure 服务中“description 载荷读写闭环保留候选来源”这一场景或边界行为。 |
| 345 | `tests/test_figure_service.py::test_normalize_stored_candidate_fields_drops_legacy_caption_only_ocr` | 验证figure 服务中“归一化 stored 候选字段移除兼容旧版标题仅 OCR”这一场景或边界行为。 |
| 346 | `tests/test_figure_service.py::test_find_captions_collects_supported_labels` | 验证figure 服务中“find captions 收集 支持 labels”这一场景或边界行为。 |
| 347 | `tests/test_figure_service.py::test_match_pdf_caption_prefers_same_type` | 验证figure 服务中“匹配 PDF 标题优先使用相同类型”这一场景或边界行为。 |
| 348 | `tests/test_figure_service.py::test_collect_source_candidates_extracts_figure_and_eps_references` | 验证figure 服务中“收集来源候选提取图表与eps references”这一场景或边界行为。 |
| 349 | `tests/test_figure_service.py::test_materialize_figure_image_writes_png` | 验证figure 服务中“物化 图表图片写入 png”这一场景或边界行为。 |
| 350 | `tests/test_figure_service.py::test_extract_figures_returns_empty_when_arxiv_and_mineru_are_unavailable` | 验证figure 服务中“提取图表返回空当 arxiv与mineru 会被 不可用”这一场景或边界行为。 |
| 351 | `tests/test_figure_service.py::test_extract_figures_arxiv_mode_does_not_fallback_to_mineru` | 验证figure 服务中“提取图表 arxiv 模式不会兜底到mineru”这一场景或边界行为。 |
| 352 | `tests/test_figure_service.py::test_extract_figures_arxiv_mode_combines_arxiv_figures_with_ocr_tables` | 验证figure 服务中“提取图表 arxiv 模式 combines arxiv 图表带有 OCR tables”这一场景或边界行为。 |
| 353 | `tests/test_figure_service.py::test_extract_figures_mineru_mode_returns_empty_without_mineru_outputs` | 验证figure 服务中“提取图表 mineru 模式返回空不依赖 mineru 输出”这一场景或边界行为。 |
| 354 | `tests/test_figure_service.py::test_collect_mineru_structured_blocks_reads_middle_json` | 验证figure 服务中“收集 mineru 结构化块读取 middle JSON”这一场景或边界行为。 |
| 355 | `tests/test_figure_service.py::test_collect_mineru_structured_blocks_falls_back_to_content_list` | 验证figure 服务中“收集 mineru 结构化块回退到内容列表”这一场景或边界行为。 |
| 356 | `tests/test_figure_service.py::test_collect_mineru_structured_blocks_keeps_original_split_figure_blocks` | 验证figure 服务中“收集 mineru 结构化块保持 original split 图表块”这一场景或边界行为。 |
| 357 | `tests/test_figure_service.py::test_collect_mineru_structured_blocks_does_not_merge_image_run_without_figure_caption` | 验证figure 服务中“收集 mineru 结构化块不会合并图片运行不依赖图表标题”这一场景或边界行为。 |
| 358 | `tests/test_figure_service.py::test_prune_nested_mineru_blocks_prefers_whole_figure` | 验证figure 服务中“prune nested mineru 块优先使用 whole 图表”这一场景或边界行为。 |
| 359 | `tests/test_figure_service.py::test_extract_via_mineru_uses_cached_runtime_bundle` | 验证figure 服务中“提取 via mineru 使用缓存的运行时包”这一场景或边界行为。 |
| 360 | `tests/test_figure_service.py::test_extract_paper_figure_candidates_keeps_all_candidates` | 验证figure 服务中“提取论文图表候选保持全部候选”这一场景或边界行为。 |
| 361 | `tests/test_figure_service.py::test_get_paper_analyses_returns_duplicates_and_analyzed_flag` | 验证figure 服务中“获取论文 analyses 返回重复项与analyzed 标志”这一场景或边界行为。 |

### tests/test_global_routes.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 362 | `tests/test_global_routes.py::test_global_health_reports_version` | 验证全局路由中“全局 health 报告版本”这一场景或边界行为。 |
| 363 | `tests/test_global_routes.py::test_global_event_stream_mirrors_global_bus` | 验证全局路由中“全局事件流式输出镜像全局事件总线”这一场景或边界行为。 |
| 364 | `tests/test_global_routes.py::test_global_event_stream_serializes_session_bus_events` | 验证全局路由中“全局事件流式输出序列化会话事件总线事件”这一场景或边界行为。 |
| 365 | `tests/test_global_routes.py::test_project_current_ensures_instance_project` | 验证全局路由中“项目 当前 ensures 实例项目”这一场景或边界行为。 |
| 366 | `tests/test_global_routes.py::test_session_abort_route_delegates_to_runtime` | 验证全局路由中“会话中止路由委托到运行时”这一场景或边界行为。 |
| 367 | `tests/test_global_routes.py::test_instance_provide_and_state_follow_directory_scope` | 验证全局路由中“实例 provide与状态后续目录 scope”这一场景或边界行为。 |
| 368 | `tests/test_global_routes.py::test_instance_reload_disposes_prompt_sessions_for_directory` | 验证全局路由中“实例 重新加载 disposes 提示词会话针对目录”这一场景或边界行为。 |
| 369 | `tests/test_global_routes.py::test_global_dispose_aborts_prompts_and_broadcasts` | 验证全局路由中“全局 dispose aborts 提示词与broadcasts”这一场景或边界行为。 |
| 370 | `tests/test_global_routes.py::test_instance_dispose_and_reload_do_not_allow_stale_loop_to_restore_lifecycle_state` | 验证全局路由中“实例 dispose与重新加载 执行不允许 stale 循环到恢复生命周期状态”这一场景或边界行为。 |

### tests/test_graph_service_citation_cache.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 371 | `tests/test_graph_service_citation_cache.py::test_citation_detail_uses_persisted_cache_and_force_refresh` | 验证图谱服务 citation 缓存中“citation 详情使用已持久化缓存与force refresh”这一场景或边界行为。 |

### tests/test_llm_client_dispatch.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 372 | `tests/test_llm_client_dispatch.py::test_summary_dispatch_routes_match_current_provider_matrix` | 验证LLM 客户端中“摘要分发路由匹配 当前 提供商矩阵”这一场景或边界行为。 |
| 373 | `tests/test_llm_client_dispatch.py::test_chat_dispatch_distinguishes_official_openai_from_兼容ible_gateways` | 验证LLM 客户端中“对话分发 distinguishes 官方 OpenAI 从兼容 gateways”这一场景或边界行为。 |
| 374 | `tests/test_llm_client_dispatch.py::test_chat_test_dispatch_preserves_openai_zhipu_and_anthropic_routes` | 验证LLM 客户端中“对话 test 分发保留 OpenAI 智谱与Anthropic 路由”这一场景或边界行为。 |
| 375 | `tests/test_llm_client_dispatch.py::test_embedding_dispatch_reports_supported_and_fallback_routes` | 验证LLM 客户端中“嵌入分发报告 支持与兜底路由”这一场景或边界行为。 |
| 376 | `tests/test_llm_client_dispatch.py::test_embedding_test_dispatch_tracks_disabled_and_unsupported_routes` | 验证LLM 客户端中“嵌入 test 分发跟踪 disabled与不支持的路由”这一场景或边界行为。 |

### tests/test_llm_client_embedding.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 377 | `tests/test_llm_client_embedding.py::test_provider_embedding_candidates_add_dashscope_兼容ible_base` | 验证LLM 客户端中“提供商嵌入候选 add dashscope 兼容基础”这一场景或边界行为。 |
| 378 | `tests/test_llm_client_embedding.py::test_provider_embed_openai_兼容ible_or_raise_returns_vector` | 验证LLM 客户端中“提供商嵌入 OpenAI 兼容或 raise 返回 vector”这一场景或边界行为。 |
| 379 | `tests/test_llm_client_embedding.py::test_provider_embed_openai_兼容ible_returns_none_on_error` | 验证LLM 客户端中“提供商嵌入 OpenAI 兼容返回 none 在错误”这一场景或边界行为。 |
| 380 | `tests/test_llm_client_embedding.py::test_provider_pseudo_embedding_is_normalized` | 验证LLM 客户端中“提供商伪嵌入是归一化的”这一场景或边界行为。 |

### tests/test_llm_client_message_transform.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 381 | `tests/test_llm_client_message_transform.py::test_build_openai_chat_messages_filters_empty_anthropic_messages` | 验证LLM 客户端中“构建模式 OpenAI 对话消息过滤空 Anthropic 消息”这一场景或边界行为。 |
| 382 | `tests/test_llm_client_message_transform.py::test_build_openai_chat_messages_normalizes_claude_tool_call_ids` | 验证LLM 客户端中“构建模式 OpenAI 对话消息归一化 Claude 工具调用 ID”这一场景或边界行为。 |
| 383 | `tests/test_llm_client_message_transform.py::test_build_openai_chat_messages_normalizes_mistral_tool_ids_and_sequence` | 验证LLM 客户端中“构建模式 OpenAI 对话消息归一化 mistral 工具 ID与sequence”这一场景或边界行为。 |
| 384 | `tests/test_llm_client_message_transform.py::test_build_openai_chat_messages_preserves_structured_user_text_and_image_parts` | 验证LLM 客户端中“构建模式 OpenAI 对话消息保留结构化用户文本与图片片段”这一场景或边界行为。 |
| 385 | `tests/test_llm_client_message_transform.py::test_build_responses_input_replays_openai_reasoning_metadata` | 验证LLM 客户端中“构建模式响应输入重放 OpenAI 推理元数据”这一场景或边界行为。 |
| 386 | `tests/test_llm_client_message_transform.py::test_build_responses_input_preserves_empty_reasoning_metadata` | 验证LLM 客户端中“构建模式响应输入保留空推理元数据”这一场景或边界行为。 |
| 387 | `tests/test_llm_client_message_transform.py::test_build_responses_input_supports_structured_user_text_and_file_parts` | 验证LLM 客户端中“构建模式响应输入支持结构化用户文本与文件片段”这一场景或边界行为。 |
| 388 | `tests/test_llm_client_message_transform.py::test_build_responses_input_replays_openai_assistant_text_item_ids` | 验证LLM 客户端中“构建模式响应输入重放 OpenAI 研究助手文本项目 ID”这一场景或边界行为。 |
| 389 | `tests/test_llm_client_message_transform.py::test_build_responses_input_replays_openai_tool_call_item_ids` | 验证LLM 客户端中“构建模式响应输入重放 OpenAI 工具调用项目 ID”这一场景或边界行为。 |
| 390 | `tests/test_llm_client_message_transform.py::test_build_responses_input_skips_provider_executed_tool_history_when_store_false` | 验证LLM 客户端中“构建模式响应输入跳过提供商已执行工具历史记录当存储 false”这一场景或边界行为。 |
| 391 | `tests/test_llm_client_message_transform.py::test_build_responses_input_replays_provider_executed_tool_result_as_item_reference_when_store_true` | 验证LLM 客户端中“构建模式响应输入重放提供商已执行工具结果作为项目 reference 当存储 true”这一场景或边界行为。 |
| 392 | `tests/test_llm_client_message_transform.py::test_build_responses_input_replays_reasoning_as_item_reference_when_store_true` | 验证LLM 客户端中“构建模式响应输入重放推理作为项目 reference 当存储 true”这一场景或边界行为。 |
| 393 | `tests/test_llm_client_message_transform.py::test_build_responses_input_replays_local_shell_call_and_output` | 验证LLM 客户端中“构建模式响应输入重放本地 Shell 调用与输出”这一场景或边界行为。 |
| 394 | `tests/test_llm_client_message_transform.py::test_normalize_responses_tools_preserves_openai_provider_defined_builtin_tools` | 验证LLM 客户端中“归一化响应工具保留 OpenAI 提供商已定义内置工具”这一场景或边界行为。 |
| 395 | `tests/test_llm_client_message_transform.py::test_normalize_openai_chat_tools_drops_provider_defined_builtin_tools` | 验证LLM 客户端中“归一化 OpenAI 对话工具移除提供商已定义内置工具”这一场景或边界行为。 |
| 396 | `tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_emits_reasoning_metadata` | 验证LLM 客户端中“对话流式输出 OpenAI 响应发出推理元数据”这一场景或边界行为。 |
| 397 | `tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_emits_tool_call_metadata` | 验证LLM 客户端中“对话流式输出 OpenAI 响应发出工具调用元数据”这一场景或边界行为。 |
| 398 | `tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_emits_provider_executed_builtin_tool_events` | 验证LLM 客户端中“对话流式输出 OpenAI 响应发出提供商已执行内置工具事件”这一场景或边界行为。 |
| 399 | `tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_adds_builtin_include_fields` | 验证LLM 客户端中“对话流式输出 OpenAI 响应会添加内置包含字段”这一场景或边界行为。 |
| 400 | `tests/test_llm_client_message_transform.py::test_chat_stream_openai_兼容ible_strips_provider_defined_tools` | 验证LLM 客户端中“对话流式输出 OpenAI 兼容 strips 提供商已定义工具”这一场景或边界行为。 |
| 401 | `tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_preserves_output_annotations_metadata` | 验证LLM 客户端中“对话流式输出 OpenAI 响应保留输出 annotations 元数据”这一场景或边界行为。 |
| 402 | `tests/test_llm_client_message_transform.py::test_chat_stream_openai_responses_reuses_previous_response_id_when_store_true` | 验证LLM 客户端中“对话流式输出 OpenAI 响应复用之前响应 ID 当存储 true”这一场景或边界行为。 |
| 403 | `tests/test_llm_client_message_transform.py::test_extract_chat_reasoning_text_deduplicates_model_dump_mirror` | 验证LLM 客户端中“提取对话推理文本 deduplicates 模型 dump mirror”这一场景或边界行为。 |
| 404 | `tests/test_llm_client_message_transform.py::test_chat_stream_uses_openai_兼容ible_for_non_official_openai_targets` | 验证LLM 客户端中“对话流式输出使用 OpenAI 兼容针对非官方 OpenAI 目标”这一场景或边界行为。 |

### tests/test_llm_client_probe.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 405 | `tests/test_llm_client_probe.py::test_probe_openai_chat_reports_responses_success` | 验证LLM 客户端中“探测 OpenAI 对话报告响应成功”这一场景或边界行为。 |
| 406 | `tests/test_llm_client_probe.py::test_probe_embedding_openai_兼容ible_reports_dimension` | 验证LLM 客户端中“探测嵌入 OpenAI 兼容报告 dimension”这一场景或边界行为。 |
| 407 | `tests/test_llm_client_probe.py::test_probe_openai_兼容ible_reports_normalized_failure_payload` | 验证LLM 客户端中“探测 OpenAI 兼容报告归一化的失败载荷”这一场景或边界行为。 |
| 408 | `tests/test_llm_client_probe.py::test_probe_openai_兼容ible_falls_back_to_responses_when_legacy_chat_rejected` | 验证LLM 客户端中“探测 OpenAI 兼容回退到响应当兼容旧版对话被拒绝”这一场景或边界行为。 |
| 409 | `tests/test_llm_client_probe.py::test_probe_openai_chat_reports_attempt_chain_across_responses_and_chat` | 验证LLM 客户端中“探测 OpenAI 对话报告 attempt 链路 跨越 响应与对话”这一场景或边界行为。 |
| 410 | `tests/test_llm_client_probe.py::test_probe_anthropic_chat_reports_normalized_failure_payload` | 验证LLM 客户端中“探测 Anthropic 对话报告归一化的失败载荷”这一场景或边界行为。 |

### tests/test_llm_client_provider_options.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 411 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_responses_kwargs_aligns_openai_gpt5_defaults` | 验证LLM 客户端中“应用变体到响应关键字参数对齐 OpenAI GPT-5 默认”这一场景或边界行为。 |
| 412 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_aligns_openai_gpt5_defaults` | 验证LLM 客户端中“应用变体到对话关键字参数对齐 OpenAI GPT-5 默认”这一场景或边界行为。 |
| 413 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_responses_kwargs_sets_openrouter_prompt_cache_key` | 验证LLM 客户端中“应用变体到响应关键字参数设置 openrouter 提示词缓存密钥”这一场景或边界行为。 |
| 414 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_sets_openrouter_prompt_cache_key` | 验证LLM 客户端中“应用变体到对话关键字参数设置 openrouter 提示词缓存密钥”这一场景或边界行为。 |
| 415 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_responses_kwargs_sets_venice_prompt_cache_key` | 验证LLM 客户端中“应用变体到响应关键字参数设置 venice 提示词缓存密钥”这一场景或边界行为。 |
| 416 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_sets_venice_prompt_cache_key` | 验证LLM 客户端中“应用变体到对话关键字参数设置 venice 提示词缓存密钥”这一场景或边界行为。 |
| 417 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_uses_google_thinking_config_for_gemini` | 验证LLM 客户端中“应用变体到对话关键字参数使用 google 思考配置针对 Gemini”这一场景或边界行为。 |
| 418 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_maps_gemini_25_high_to_budget` | 验证LLM 客户端中“应用变体到对话关键字参数映射 Gemini 25 high到预算”这一场景或边界行为。 |
| 419 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_enables_zhipu_thinking` | 验证LLM 客户端中“应用变体到对话关键字参数启用智谱思考”这一场景或边界行为。 |
| 420 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_enables_dashscope_reasoning_and_qwen_sampling` | 验证LLM 客户端中“应用变体到对话关键字参数启用 dashscope 推理与qwen sampling”这一场景或边界行为。 |
| 421 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_sets_minimax_top_k` | 验证LLM 客户端中“应用变体到对话关键字参数设置 minimax top k”这一场景或边界行为。 |
| 422 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_responses_kwargs_uses_small_reasoning_for_skim_gpt5` | 验证LLM 客户端中“应用变体到响应关键字参数使用小型推理针对粗读 GPT-5”这一场景或边界行为。 |
| 423 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_uses_small_reasoning_for_skim_gpt5` | 验证LLM 客户端中“应用变体到对话关键字参数使用小型推理针对粗读 GPT-5”这一场景或边界行为。 |
| 424 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_uses_small_google_thinking_for_skim` | 验证LLM 客户端中“应用变体到对话关键字参数使用小型 google 思考针对粗读”这一场景或边界行为。 |
| 425 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_uses_small_openrouter_reasoning` | 验证LLM 客户端中“应用变体到对话关键字参数使用小型 openrouter 推理”这一场景或边界行为。 |
| 426 | `tests/test_llm_client_provider_options.py::test_apply_variant_to_chat_kwargs_uses_small_venice_disable_thinking` | 验证LLM 客户端中“应用变体到对话关键字参数使用小型 venice disable 思考”这一场景或边界行为。 |
| 427 | `tests/test_llm_client_provider_options.py::test_remap_provider_options_namespace_aligns_gateway_upstream_slug` | 验证LLM 客户端中“remap 提供商选项 namespace 对齐网关 upstream slug”这一场景或边界行为。 |
| 428 | `tests/test_llm_client_provider_options.py::test_raw_chat_http_payload_reuses_provider_option_builder` | 验证LLM 客户端中“原始对话 HTTP 载荷复用提供商 option builder”这一场景或边界行为。 |
| 429 | `tests/test_llm_client_provider_options.py::test_raw_responses_http_payload_includes_prompt_cache_key` | 验证LLM 客户端中“原始响应 HTTP 载荷包含提示词缓存密钥”这一场景或边界行为。 |
| 430 | `tests/test_llm_client_provider_options.py::test_raw_http_transport_raises_structured_provider_error` | 验证LLM 客户端中“原始 HTTP 传输层 raises 结构化提供商错误”这一场景或边界行为。 |
| 431 | `tests/test_llm_client_provider_options.py::test_raw_chat_http_error_carries_runtime_metadata` | 验证LLM 客户端中“原始对话 HTTP 错误携带运行时元数据”这一场景或边界行为。 |
| 432 | `tests/test_llm_client_provider_options.py::test_raw_responses_http_error_carries_runtime_metadata` | 验证LLM 客户端中“原始响应 HTTP 错误携带运行时元数据”这一场景或边界行为。 |

### tests/test_llm_client_resolution.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 433 | `tests/test_llm_client_resolution.py::test_parse_model_target_understands_provider_and_variant_suffix` | 验证LLM 客户端中“解析模型目标 understands 提供商与变体 suffix”这一场景或边界行为。 |
| 434 | `tests/test_llm_client_resolution.py::test_resolve_transport_base_url_normalizes_openai_and_zhipu` | 验证LLM 客户端中“解析传输层基础 URL 归一化 OpenAI与智谱”这一场景或边界行为。 |
| 435 | `tests/test_llm_client_resolution.py::test_resolve_embedding_config_infers_provider_from_embedding_base_url` | 验证LLM 客户端中“解析嵌入配置 infers 提供商从嵌入基础 URL”这一场景或边界行为。 |
| 436 | `tests/test_llm_client_resolution.py::test_resolve_embedding_config_infers_custom_provider_from_generic_http_base_url` | 验证LLM 客户端中“解析嵌入配置 infers 自定义提供商从 generic HTTP 基础 URL”这一场景或边界行为。 |
| 437 | `tests/test_llm_client_resolution.py::test_resolve_model_target_uses_engine_profile_runtime_config` | 验证LLM 客户端中“解析模型目标使用引擎配置档运行时配置”这一场景或边界行为。 |
| 438 | `tests/test_llm_client_resolution.py::test_resolve_model_target_uses_provider_prefixed_override_with_default_provider_credentials` | 验证LLM 客户端中“解析模型目标使用提供商 prefixed 覆盖项带有默认提供商 credentials”这一场景或边界行为。 |

### tests/test_llm_client_runtime.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 439 | `tests/test_llm_client_runtime.py::test_runtime_chat_missing_api_key_returns_structured_error` | 验证LLM 客户端中“运行时对话缺失 API 密钥返回结构化错误”这一场景或边界行为。 |
| 440 | `tests/test_llm_client_runtime.py::test_runtime_embedding_custom_routes_through_openai_兼容ible_probe` | 验证LLM 客户端中“运行时嵌入自定义路由通过 OpenAI 兼容探测”这一场景或边界行为。 |
| 441 | `tests/test_llm_client_runtime.py::test_runtime_vision_returns_diagnostic_message_for_blocked_gateway` | 验证LLM 客户端中“运行时视觉返回 diagnostic 消息针对被阻断网关”这一场景或边界行为。 |

### tests/test_llm_client_stream.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 442 | `tests/test_llm_client_stream.py::test_provider_stream_openai_responses_falls_back_to_兼容ible` | 验证LLM 客户端中“提供商流式输出 OpenAI 响应回退到兼容”这一场景或边界行为。 |
| 443 | `tests/test_llm_client_stream.py::test_provider_stream_openai_responses_preserves_attempt_chain_when_fallback_errors` | 验证LLM 客户端中“提供商流式输出 OpenAI 响应保留 attempt 链路当兜底错误”这一场景或边界行为。 |
| 444 | `tests/test_llm_client_stream.py::test_provider_stream_openai_兼容ible_prefers_raw_http_fallback` | 验证LLM 客户端中“提供商流式输出 OpenAI 兼容优先使用原始 HTTP 兜底”这一场景或边界行为。 |
| 445 | `tests/test_llm_client_stream.py::test_provider_stream_openai_兼容ible_falls_back_to_responses_when_legacy_chat_rejected` | 验证LLM 客户端中“提供商流式输出 OpenAI 兼容回退到响应当兼容旧版对话被拒绝”这一场景或边界行为。 |
| 446 | `tests/test_llm_client_stream.py::test_provider_stream_openai_兼容ible_adds_litellm_noop_tool_for_tool_history` | 验证LLM 客户端中“提供商流式输出 OpenAI 兼容会添加 litellm noop 工具针对工具历史记录”这一场景或边界行为。 |
| 447 | `tests/test_llm_client_stream.py::test_provider_stream_openai_兼容ible_repairs_tool_name_case_to_lowercase_match` | 验证LLM 客户端中“提供商流式输出 OpenAI 兼容修复工具 name case到lowercase 匹配”这一场景或边界行为。 |
| 448 | `tests/test_llm_client_stream.py::test_provider_stream_openai_兼容ible_surfaces_structured_transport_error` | 验证LLM 客户端中“提供商流式输出 OpenAI 兼容 surfaces 结构化传输层错误”这一场景或边界行为。 |
| 449 | `tests/test_llm_client_stream.py::test_provider_stream_pseudo_replays_summary_text` | 验证LLM 客户端中“提供商流式输出伪重放摘要文本”这一场景或边界行为。 |

### tests/test_llm_client_summary.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 450 | `tests/test_llm_client_summary.py::test_provider_summary_openai_responses_success` | 验证LLM 客户端中“提供商摘要 OpenAI 响应成功”这一场景或边界行为。 |
| 451 | `tests/test_llm_client_summary.py::test_provider_summary_openai_responses_falls_back_to_chat_兼容ible` | 验证LLM 客户端中“提供商摘要 OpenAI 响应回退到对话兼容”这一场景或边界行为。 |
| 452 | `tests/test_llm_client_summary.py::test_provider_summary_openai_兼容ible_prefers_raw_http_fallback` | 验证LLM 客户端中“提供商摘要 OpenAI 兼容优先使用原始 HTTP 兜底”这一场景或边界行为。 |
| 453 | `tests/test_llm_client_summary.py::test_provider_summary_openai_兼容ible_falls_back_to_responses_when_legacy_chat_rejected` | 验证LLM 客户端中“提供商摘要 OpenAI 兼容回退到响应当兼容旧版对话被拒绝”这一场景或边界行为。 |
| 454 | `tests/test_llm_client_summary.py::test_provider_summary_anthropic_falls_back_to_pseudo` | 验证LLM 客户端中“提供商摘要 Anthropic 回退到伪”这一场景或边界行为。 |
| 455 | `tests/test_llm_client_summary.py::test_provider_summary_openai_兼容ible_uses_stream_fallback_when_message_empty` | 验证LLM 客户端中“提供商摘要 OpenAI 兼容使用流式输出兜底当消息空”这一场景或边界行为。 |
| 456 | `tests/test_llm_client_summary.py::test_provider_summary_openai_兼容ible_prefers_stream_for_custom_gpt5` | 验证LLM 客户端中“提供商摘要 OpenAI 兼容优先使用流式输出针对自定义 GPT-5”这一场景或边界行为。 |

### tests/test_llm_client_transport_policy.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 457 | `tests/test_llm_client_transport_policy.py::test_supports_chat_reasoning_content_for_zhipu_and_non_official_gateways` | 验证LLM 客户端中“支持对话推理内容针对智谱与非官方 gateways”这一场景或边界行为。 |
| 458 | `tests/test_llm_client_transport_policy.py::test_chat_target_detection_matches_anthropic_and_mistral_families` | 验证LLM 客户端中“对话目标 detection 匹配 Anthropic与mistral families”这一场景或边界行为。 |
| 459 | `tests/test_llm_client_transport_policy.py::test_raw_http_fallback_only_applies_to_blocked_non_official_targets` | 验证LLM 客户端中“原始 HTTP 兜底仅 applies到被阻断非官方目标”这一场景或边界行为。 |

### tests/test_llm_client_vision.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 460 | `tests/test_llm_client_vision.py::test_vision_analyze_openai_responses_success` | 验证LLM 客户端中“视觉分析 OpenAI 响应成功”这一场景或边界行为。 |
| 461 | `tests/test_llm_client_vision.py::test_vision_analyze_openai_falls_back_to_openai_兼容ible` | 验证LLM 客户端中“视觉分析 OpenAI 回退到OpenAI 兼容”这一场景或边界行为。 |
| 462 | `tests/test_llm_client_vision.py::test_vision_analyze_openai_empty_responses_falls_back_to_openai_兼容ible` | 验证LLM 客户端中“视觉分析 OpenAI 空响应回退到OpenAI 兼容”这一场景或边界行为。 |
| 463 | `tests/test_llm_client_vision.py::test_vision_analyze_custom_empty_fallbacks_use_raw_http` | 验证LLM 客户端中“视觉分析自定义空 fallbacks 使用原始 HTTP”这一场景或边界行为。 |
| 464 | `tests/test_llm_client_vision.py::test_vision_analyze_zhipu_uses_openai_兼容ible_path` | 验证LLM 客户端中“视觉分析智谱使用 OpenAI 兼容路径”这一场景或边界行为。 |
| 465 | `tests/test_llm_client_vision.py::test_vision_analyze_empty_across_all_fallbacks_returns_diagnostic_message` | 验证LLM 客户端中“视觉分析空 跨越 全部 fallbacks 返回 diagnostic 消息”这一场景或边界行为。 |

### tests/test_llm_provider_registry.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 466 | `tests/test_llm_provider_registry.py::test_get_openai_client_reuses_cached_client` | 验证LLM 客户端中“获取OpenAI 客户端复用缓存的客户端”这一场景或边界行为。 |
| 467 | `tests/test_llm_provider_registry.py::test_get_anthropic_client_reuses_cache_and_preserves_base_url` | 验证LLM 客户端中“获取Anthropic 客户端复用缓存与保留基础 URL”这一场景或边界行为。 |

### tests/test_llm_provider_transform.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 468 | `tests/test_llm_provider_transform.py::test_coerce_openai_message_text_keeps_ascii_reasoning_words_separated` | 验证LLM 客户端中“强制转换 OpenAI 消息文本保持 ASCII 推理词分隔”这一场景或边界行为。 |
| 469 | `tests/test_llm_provider_transform.py::test_coerce_openai_message_text_keeps_cjk_compact` | 验证LLM 客户端中“强制转换 OpenAI 消息文本保持中日韩字符紧凑”这一场景或边界行为。 |

### tests/test_mineru_runtime.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 470 | `tests/test_mineru_runtime.py::test_mineru_runtime_base_dir_prefers_env` | 验证mineru 运行时中“mineru 运行时基础目录优先使用环境变量”这一场景或边界行为。 |
| 471 | `tests/test_mineru_runtime.py::test_mineru_runtime_resolve_device_mode_prefers_env_override` | 验证mineru 运行时中“mineru 运行时解析设备模式优先使用环境变量覆盖项”这一场景或边界行为。 |
| 472 | `tests/test_mineru_runtime.py::test_mineru_runtime_resolve_device_mode_prefers_cuda_when_available` | 验证mineru 运行时中“mineru 运行时解析设备模式优先使用 cuda 当可用”这一场景或边界行为。 |
| 473 | `tests/test_mineru_runtime.py::test_mineru_runtime_resolve_device_mode_falls_back_to_cpu` | 验证mineru 运行时中“mineru 运行时解析设备模式回退到cpu”这一场景或边界行为。 |
| 474 | `tests/test_mineru_runtime.py::test_mineru_runtime_prepare_runtime_downloads_missing_models` | 验证mineru 运行时中“mineru 运行时准备运行时 downloads 缺失模型”这一场景或边界行为。 |
| 475 | `tests/test_mineru_runtime.py::test_mineru_runtime_returns_cached_bundle_without_rerun` | 验证mineru 运行时中“mineru 运行时返回缓存的包不依赖 rerun”这一场景或边界行为。 |
| 476 | `tests/test_mineru_runtime.py::test_mineru_runtime_force_bypasses_cached_success_bundle` | 验证mineru 运行时中“mineru 运行时 force bypasses 缓存的成功包”这一场景或边界行为。 |
| 477 | `tests/test_mineru_runtime.py::test_mineru_runtime_runs_local_pipeline_and_persists_manifest` | 验证mineru 运行时中“mineru 运行时运行本地流水线与持久化 manifest”这一场景或边界行为。 |

### tests/test_mounted_paper_context.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 478 | `tests/test_mounted_paper_context.py::test_resolve_research_skill_ids_auto_enables_project_research_skills` | 验证挂载的论文上下文中“解析 research Skill ID 自动启用项目 research Skills”这一场景或边界行为。 |
| 479 | `tests/test_mounted_paper_context.py::test_build_mounted_papers_prompt_includes_pdf_and_existing_analysis` | 验证挂载的论文上下文中“构建模式挂载的论文提示词包含 PDF与已有分析”这一场景或边界行为。 |

### tests/test_openalex_client_rerank.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 480 | `tests/test_openalex_client_rerank.py::test_search_works_prefers_exact_title_and_published_venue` | 验证OpenAlex 客户端中“检索可工作优先使用精确 title与published 会议/期刊”这一场景或边界行为。 |
| 481 | `tests/test_openalex_client_rerank.py::test_search_works_prefers_exact_doi_lookup` | 验证OpenAlex 客户端中“检索可工作优先使用精确 doi 查找”这一场景或边界行为。 |
| 482 | `tests/test_openalex_client_rerank.py::test_search_works_does_not_fallback_to_broad_results_for_missing_exact_doi` | 验证OpenAlex 客户端中“检索可工作不会兜底到broad 结果针对缺失精确 doi”这一场景或边界行为。 |
| 483 | `tests/test_openalex_client_rerank.py::test_search_works_prefers_exact_arxiv_lookup` | 验证OpenAlex 客户端中“检索可工作优先使用精确 arxiv 查找”这一场景或边界行为。 |
| 484 | `tests/test_openalex_client_rerank.py::test_search_works_re覆盖_published_variant_from_same_title_family` | 验证OpenAlex 客户端中“检索可工作恢复 published 变体从相同 title family”这一场景或边界行为。 |

### tests/test_openalex_client_source_selection.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 485 | `tests/test_openalex_client_source_selection.py::test_work_to_search_result_prefers_conference_location_over_arxiv_primary_location` | 验证OpenAlex 客户端中“work到检索结果优先使用会议位置超过 arxiv 主位置”这一场景或边界行为。 |
| 486 | `tests/test_openalex_client_source_selection.py::test_work_to_search_result_falls_back_to_primary_location_when_no_better_source_exists` | 验证OpenAlex 客户端中“work到检索结果回退到主位置当没有 better 来源 exists”这一场景或边界行为。 |

### tests/test_paper_analysis_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 487 | `tests/test_paper_analysis_service.py::test_paper_analysis_service_persists_round_bundle` | 验证论文处理中“论文分析服务持久化轮次包”这一场景或边界行为。 |
| 488 | `tests/test_paper_analysis_service.py::test_paper_analysis_service_fails_on_provider_placeholder` | 验证论文处理中“论文分析服务 fails 在提供商占位文本”这一场景或边界行为。 |
| 489 | `tests/test_paper_analysis_service.py::test_paper_analysis_service_retries_transient_provider_errors` | 验证论文处理中“论文分析服务 retries transient 提供商错误”这一场景或边界行为。 |
| 490 | `tests/test_paper_analysis_service.py::test_paper_analysis_service_prefers_mineru_ocr_context` | 验证论文处理中“论文分析服务优先使用 mineru OCR 上下文”这一场景或边界行为。 |
| 491 | `tests/test_paper_analysis_service.py::test_paper_analysis_service_pdf_source_skips_markdown` | 验证论文处理中“论文分析服务 PDF 来源跳过 Markdown”这一场景或边界行为。 |
| 492 | `tests/test_paper_analysis_service.py::test_paper_analysis_service_uses_round_specific_evidence` | 验证论文处理中“论文分析服务使用轮次指定证据”这一场景或边界行为。 |
| 493 | `tests/test_paper_analysis_service.py::test_paper_analysis_service_rough_mode_uses_shared_excerpt` | 验证论文处理中“论文分析服务粗读模式使用共享摘录”这一场景或边界行为。 |

### tests/test_paper_evidence.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 494 | `tests/test_paper_evidence.py::test_load_prepared_paper_evidence_reuses_process_cache_for_pdf` | 验证论文处理中“加载已准备论文证据复用处理缓存针对 PDF”这一场景或边界行为。 |
| 495 | `tests/test_paper_evidence.py::test_load_prepared_paper_evidence_rough_mode_keeps_raw_excerpt` | 验证论文处理中“加载已准备论文证据粗读模式保持原始摘录”这一场景或边界行为。 |
| 496 | `tests/test_paper_evidence.py::test_prepared_paper_evidence_unbounded_mode_keeps_full_structured_context` | 验证论文处理中“已准备论文证据 unbounded 模式保持完整结构化上下文”这一场景或边界行为。 |

### tests/test_paper_reader.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 497 | `tests/test_paper_reader.py::test_reader_query_selection_translate` | 验证论文处理中“阅读器查询选择翻译”这一场景或边界行为。 |
| 498 | `tests/test_paper_reader.py::test_reader_query_paper_uses_context` | 验证论文处理中“阅读器查询论文使用上下文”这一场景或边界行为。 |
| 499 | `tests/test_paper_reader.py::test_reader_query_paper_prefers_mineru_ocr_context` | 验证论文处理中“阅读器查询论文优先使用 mineru OCR 上下文”这一场景或边界行为。 |
| 500 | `tests/test_paper_reader.py::test_reader_query_figure_uses_vision` | 验证论文处理中“阅读器查询图表使用视觉”这一场景或边界行为。 |
| 501 | `tests/test_paper_reader.py::test_reader_query_region_uses_vision_without_figure_id` | 验证论文处理中“阅读器查询 region 使用视觉不依赖图表 ID”这一场景或边界行为。 |
| 502 | `tests/test_paper_reader.py::test_get_paper_figures_uses_cache` | 验证论文处理中“获取论文图表使用缓存”这一场景或边界行为。 |
| 503 | `tests/test_paper_reader.py::test_get_figure_image_sets_cache_header` | 验证论文处理中“获取图表图片设置缓存 header”这一场景或边界行为。 |
| 504 | `tests/test_paper_reader.py::test_reader_query_figure_falls_back_to_text_context_when_vision_unavailable` | 验证论文处理中“阅读器查询图表回退到文本上下文当视觉 不可用”这一场景或边界行为。 |
| 505 | `tests/test_paper_reader.py::test_reader_query_paper_falls_back_when_pdf_prepare_fails` | 验证论文处理中“阅读器查询论文回退回退当 PDF 准备 fails”这一场景或边界行为。 |
| 506 | `tests/test_paper_reader.py::test_serve_pdf_resolves_relative_pdf_path_outside_cwd` | 验证论文处理中“serve PDF 解析 相对 PDF 路径 外部 cwd”这一场景或边界行为。 |
| 507 | `tests/test_paper_reader.py::test_reader_notes_crud` | 验证论文处理中“阅读器 notes CRUD”这一场景或边界行为。 |

### tests/test_paper_reasoning_sync.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 508 | `tests/test_paper_reasoning_sync.py::test_reasoning_service_syncs_variant_to_detail_level` | 验证论文处理中“推理服务同步变体到详情级别”这一场景或边界行为。 |
| 509 | `tests/test_paper_reasoning_sync.py::test_analyze_paper_rounds_syncs_retry_metadata` | 验证论文处理中“分析论文轮次同步重试元数据”这一场景或边界行为。 |
| 510 | `tests/test_paper_reasoning_sync.py::test_reasoning_service_prefers_mineru_ocr_context` | 验证论文处理中“推理服务优先使用 mineru OCR 上下文”这一场景或边界行为。 |
| 511 | `tests/test_paper_reasoning_sync.py::test_reasoning_service_pdf_source_skips_markdown` | 验证论文处理中“推理服务 PDF 来源跳过 Markdown”这一场景或边界行为。 |
| 512 | `tests/test_paper_reasoning_sync.py::test_reasoning_service_rough_mode_uses_smaller_budget` | 验证论文处理中“推理服务粗读模式使用 smaller 预算”这一场景或边界行为。 |

### tests/test_pdf_reader_ai_prompt.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 513 | `tests/test_pdf_reader_ai_prompt.py::test_translate_prompt_requires_chinese_only_translation` | 验证PDF 阅读器 AI 提示词中“翻译提示词要求 chinese 仅 translation”这一场景或边界行为。 |
| 514 | `tests/test_pdf_reader_ai_prompt.py::test_analyze_prompt_requires_structured_chinese_analysis` | 验证PDF 阅读器 AI 提示词中“分析提示词要求结构化 chinese 分析”这一场景或边界行为。 |
| 515 | `tests/test_pdf_reader_ai_prompt.py::test_legacy_summarize_action_is_still_mapped_to_analysis_prompt` | 验证PDF 阅读器 AI 提示词中“兼容旧版摘要生成动作是仍然 mapped到分析提示词”这一场景或边界行为。 |
| 516 | `tests/test_pdf_reader_ai_prompt.py::test_ask_prompt_includes_question_and_excerpt` | 验证PDF 阅读器 AI 提示词中“ask 提示词包含问题与摘录”这一场景或边界行为。 |
| 517 | `tests/test_pdf_reader_ai_prompt.py::test_ask_prompt_requires_question` | 验证PDF 阅读器 AI 提示词中“ask 提示词要求问题”这一场景或边界行为。 |

### tests/test_pipelines_deep_dive.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 518 | `tests/test_pipelines_deep_dive.py::test_deep_dive_prefers_mineru_ocr_context` | 验证流水线 深度 深挖中“深度深挖优先使用 mineru OCR 上下文”这一场景或边界行为。 |
| 519 | `tests/test_pipelines_deep_dive.py::test_deep_dive_pdf_source_skips_cached_markdown` | 验证流水线 深度 深挖中“深度深挖 PDF 来源跳过缓存的 Markdown”这一场景或边界行为。 |
| 520 | `tests/test_pipelines_deep_dive.py::test_deep_dive_rough_mode_skips_focus_stages` | 验证流水线 深度 深挖中“深度深挖粗读模式跳过 focus stages”这一场景或边界行为。 |

### tests/test_project_engine_profiles.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 521 | `tests/test_project_engine_profiles.py::test_llm_client_resolves_engine_profile_to_selected_config` | 验证项目工作流中“LLM 客户端解析引擎配置档到选中的配置”这一场景或边界行为。 |
| 522 | `tests/test_project_engine_profiles.py::test_project_workspace_context_and_run_expose_engine_bindings` | 验证项目工作流中“项目工作区上下文与运行 expose 引擎 bindings”这一场景或边界行为。 |

### tests/test_project_execution_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 523 | `tests/test_project_execution_service.py::test_submit_project_run_prefers_native_for_active_workflows` | 验证项目工作流中“提交项目运行优先使用原生针对活跃工作流”这一场景或边界行为。 |
| 524 | `tests/test_project_execution_service.py::test_submit_project_run_keeps_native_for_active_workflows_even_with_role_overrides` | 验证项目工作流中“提交项目运行保持原生针对活跃工作流 even 带有 role 覆盖项”这一场景或边界行为。 |
| 525 | `tests/test_project_execution_service.py::test_submit_project_run_uses_multi_agent_for_planned_workflows` | 验证项目工作流中“提交项目运行使用多 Agent Agent 针对计划中的工作流”这一场景或边界行为。 |
| 526 | `tests/test_project_execution_service.py::test_submit_project_run_resumes_multi_agent_from_stage_checkpoint` | 验证项目工作流中“提交项目运行恢复执行多 Agent Agent 从阶段检查点”这一场景或边界行为。 |
| 527 | `tests/test_project_execution_service.py::test_submit_project_run_pauses_for_preflight_checkpoint` | 验证项目工作流中“提交项目运行暂停针对预检检查点”这一场景或边界行为。 |
| 528 | `tests/test_project_execution_service.py::test_submit_project_run_dispatches_after_checkpoint_approval` | 验证项目工作流中“提交项目运行 dispatches 之后检查点审批”这一场景或边界行为。 |
| 529 | `tests/test_project_execution_service.py::test_submit_project_run_resumes_from_stage_checkpoint` | 验证项目工作流中“提交项目运行恢复执行从阶段检查点”这一场景或边界行为。 |

### tests/test_project_gpu_lease_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 530 | `tests/test_project_gpu_lease_service.py::test_gpu_lease_service_acquire_conflict_and_release` | 验证项目工作流中“GPU 租约服务 acquire 冲突与释放”这一场景或边界行为。 |
| 531 | `tests/test_project_gpu_lease_service.py::test_gpu_lease_service_reconcile_releases_missing_sessions` | 验证项目工作流中“GPU 租约服务对账释放缺失会话”这一场景或边界行为。 |

### tests/test_project_multi_agent_runner.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 532 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_executes_codex_role_without_cli` | 验证项目工作流中“多 Agent Agent 运行器执行 代码x role 不依赖 CLI”这一场景或边界行为。 |
| 533 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_autoresearch_bootstrap_and_baseline` | 验证项目工作流中“多 Agent Agent 运行器自动研究启动初始化与基线”这一场景或边界行为。 |
| 534 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_materializes_paper_write_workspace` | 验证项目工作流中“多 Agent Agent 运行器物化论文写入工作区”这一场景或边界行为。 |
| 535 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_stage_checkpoint_resumes_standalone_workflow` | 验证项目工作流中“多 Agent Agent 运行器阶段检查点恢复执行 standalone 工作流”这一场景或边界行为。 |
| 536 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_materializes_paper_improvement_with_explicit_review_parsing` | 验证项目工作流中“多 Agent Agent 运行器物化论文改进带有显式评审 parsing”这一场景或边界行为。 |
| 537 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_materializes_experiment_audit_artifacts` | 验证项目工作流中“多 Agent Agent 运行器物化实验审计产物”这一场景或边界行为。 |
| 538 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_paper_compile_collects_generated_pdf_artifact` | 验证项目工作流中“多 Agent Agent 运行器论文编译收集生成的 PDF 产物”这一场景或边界行为。 |
| 539 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_同步工作区_copies_files` | 验证项目工作流中“多 Agent Agent 运行器同步工作区复制文件”这一场景或边界行为。 |
| 540 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_同步工作区_copies_remote_to_remote` | 验证项目工作流中“多 Agent Agent 运行器同步工作区复制远程到远程”这一场景或边界行为。 |
| 541 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_monitor_experiment_collects_screen_state` | 验证项目工作流中“多 Agent Agent 运行器监控实验收集 screen 会话状态”这一场景或边界行为。 |
| 542 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_monitor_experiment_collects_multiple_screen_sessions` | 验证项目工作流中“多 Agent Agent 运行器监控实验收集多个 screen 会话会话”这一场景或边界行为。 |
| 543 | `tests/test_project_multi_agent_runner.py::test_multi_agent_runner_monitor_experiment_collects_structured_results` | 验证项目工作流中“多 Agent Agent 运行器监控实验收集结构化结果”这一场景或边界行为。 |

### tests/test_project_output_sanitizer.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 544 | `tests/test_project_output_sanitizer.py::test_sanitize_project_markdown_removes_tool_trace_and_checkpoint_block` | 验证项目工作流中“清洗项目 Markdown 移除工具轨迹与检查点块”这一场景或边界行为。 |
| 545 | `tests/test_project_output_sanitizer.py::test_sanitize_project_run_metadata_sanitizes_markdown_but_keeps_json_content` | 验证项目工作流中“清洗项目运行元数据清洗 Markdown 但保持 JSON 内容”这一场景或边界行为。 |
| 546 | `tests/test_project_output_sanitizer.py::test_sanitize_project_artifact_preview_only_for_aris_markdown` | 验证项目工作流中“清洗项目产物预览仅针对 ARIS Markdown”这一场景或边界行为。 |

### tests/test_project_paper_artifacts.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 547 | `tests/test_project_paper_artifacts.py::test_parse_review_text_matches_aris_score_and_action_patterns` | 验证项目工作流中“解析评审文本匹配 ARIS 评分与动作模式”这一场景或边界行为。 |
| 548 | `tests/test_project_paper_artifacts.py::test_extract_score_supports_score_of_pattern_without_guessing` | 验证项目工作流中“提取评分支持评分的 pattern 不依赖 guessing”这一场景或边界行为。 |
| 549 | `tests/test_project_paper_artifacts.py::test_extract_review_verdict_follows_aris_keyword_order` | 验证项目工作流中“提取评审结论遵循 ARIS keyword order”这一场景或边界行为。 |
| 550 | `tests/test_project_paper_artifacts.py::test_build_paper_improvement_bundle_persists_verdicts_and_action_items` | 验证项目工作流中“构建模式论文改进包持久化 verdicts与动作项目”这一场景或边界行为。 |

### tests/test_project_report_formatter.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 551 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_想法发现_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化想法发现输出”这一场景或边界行为。 |
| 552 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_run_experiment_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化运行实验输出”这一场景或边界行为。 |
| 553 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_paper_writing_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化论文写作输出”这一场景或边界行为。 |
| 554 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_rebuttal_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化回复审稿输出”这一场景或边界行为。 |
| 555 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_paper_subworkflow_output[paper_plan-metadata0-# \u8bba\u6587\u89c4\u5212\u62a5\u544a-Claims-Evidence Matrix]` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化论文子工作流输出”这一场景或边界行为。参数化样例：论文规划 / metadata0 / # 论文规划报告 / Claims / Evidence Matrix。 |
| 556 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_paper_subworkflow_output[paper_figure-metadata1-# \u56fe\u8868\u89c4\u5212\u62a5\u544a-AnchorCoT overview]` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化论文子工作流输出”这一场景或边界行为。参数化样例：论文图表 / metadata1 / # 图表规划报告 / AnchorCoT 概览。 |
| 557 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_paper_subworkflow_output[paper_write-metadata2-# \u8bba\u6587\u521d\u7a3f\u62a5\u544a-AnchorCoT aligns anchors and process rewards.]` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化论文子工作流输出”这一场景或边界行为。参数化样例：论文撰写 / metadata2 / # 论文初稿报告 / AnchorCoT aligns anchors与处理 rewards.。 |
| 558 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_paper_compile_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化论文编译输出”这一场景或边界行为。 |
| 559 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_paper_improvement_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化论文改进输出”这一场景或边界行为。 |
| 560 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_full_pipeline_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化完整流水线输出”这一场景或边界行为。 |
| 561 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_auto_review_loop_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化自动评审循环输出”这一场景或边界行为。 |
| 562 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_monitor_experiment_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化监控实验输出”这一场景或边界行为。 |
| 563 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_experiment_audit_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化实验审计输出”这一场景或边界行为。 |
| 564 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_同步工作区_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化同步工作区输出”这一场景或边界行为。 |
| 565 | `tests/test_project_report_formatter.py::test_build_workflow_report_markdown_formats_custom_run_output` | 验证项目工作流中“构建模式工作流报告 Markdown 格式化自定义运行输出”这一场景或边界行为。 |
| 566 | `tests/test_project_report_formatter.py::test_workspace_preview_content_rerenders_primary_run_report` | 验证项目工作流中“工作区预览内容 rerenders 主运行报告”这一场景或边界行为。 |
| 567 | `tests/test_project_report_formatter.py::test_merge_artifact_refs_dedupes_same_path_and_keeps_richer_kind` | 验证项目工作流中“合并产物引用 去重 相同路径与保持 richer kind”这一场景或边界行为。 |

### tests/test_project_run_action_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 568 | `tests/test_project_run_action_service.py::test_submit_project_run_action_registers_tracker_metadata` | 验证项目工作流中“提交项目运行动作 registers 任务追踪器元数据”这一场景或边界行为。 |
| 569 | `tests/test_project_run_action_service.py::test_run_project_run_action_writes_remote_files_and_updates_task_metadata` | 验证项目工作流中“运行项目运行动作写入远程文件与更新任务元数据”这一场景或边界行为。 |

### tests/test_project_submit_tracker_regression.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 570 | `tests/test_project_submit_tracker_regression.py::test_submit_project_run_builds_tracker_metadata_without_detached_instance` | 验证项目工作流中“提交项目运行构建任务追踪器元数据不依赖游离实例”这一场景或边界行为。 |
| 571 | `tests/test_project_submit_tracker_regression.py::test_submit_multi_agent_project_run_builds_tracker_metadata_without_detached_instance` | 验证项目工作流中“提交多 Agent Agent 项目运行构建任务追踪器元数据不依赖游离实例”这一场景或边界行为。 |
| 572 | `tests/test_project_submit_tracker_regression.py::test_submit_project_run_includes_remote_execution_metadata` | 验证项目工作流中“提交项目运行包含远程执行元数据”这一场景或边界行为。 |

### tests/test_project_workflow_repository.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 573 | `tests/test_project_workflow_repository.py::test_project_workflow_records_roundtrip` | 验证项目工作流中“项目工作流记录读写闭环”这一场景或边界行为。 |

### tests/test_project_workflow_runner.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 574 | `tests/test_project_workflow_runner.py::test_literature_review_workflow_persists_report` | 验证项目工作流运行器中“文献评审工作流持久化报告”这一场景或边界行为。 |
| 575 | `tests/test_project_workflow_runner.py::test_literature_review_stage_checkpoint_resumes_without_regenerating_review` | 验证项目工作流运行器中“文献评审阶段检查点恢复执行不依赖 regenerating 评审”这一场景或边界行为。 |
| 576 | `tests/test_project_workflow_runner.py::test_想法发现_workflow_falls_back_without_model` | 验证项目工作流运行器中“想法发现工作流回退回退不依赖模型”这一场景或边界行为。 |
| 577 | `tests/test_project_workflow_runner.py::test_paper_writing_materials_include_previous_paper_workflow_outputs` | 验证项目工作流运行器中“论文写作 materials 包含之前论文工作流 输出”这一场景或边界行为。 |
| 578 | `tests/test_project_workflow_runner.py::test_paper_writing_materializes_standard_workspace_artifacts` | 验证项目工作流运行器中“论文写作物化标准工作区产物”这一场景或边界行为。 |
| 579 | `tests/test_project_workflow_runner.py::test_paper_writing_improvement_requires_explicit_scores` | 验证项目工作流运行器中“论文写作改进要求显式 scores”这一场景或边界行为。 |
| 580 | `tests/test_project_workflow_runner.py::test_full_pipeline_stage_checkpoint_resumes_without_repeating_review` | 验证项目工作流运行器中“完整流水线阶段检查点恢复执行不依赖重复评审”这一场景或边界行为。 |
| 581 | `tests/test_project_workflow_runner.py::test_paper_writing_stage_checkpoints_resume_without_regenerating_draft` | 验证项目工作流运行器中“论文写作阶段检查点恢复执行不依赖 regenerating draft”这一场景或边界行为。 |
| 582 | `tests/test_project_workflow_runner.py::test_想法发现_stage_checkpoints_resume_without_repeating_previous_phases` | 验证项目工作流运行器中“想法发现阶段检查点恢复执行不依赖重复之前 phases”这一场景或边界行为。 |
| 583 | `tests/test_project_workflow_runner.py::test_novelty_check_stage_checkpoint_resumes_without_repeating_compare` | 验证项目工作流运行器中“新颖性检查阶段检查点恢复执行不依赖重复 compare”这一场景或边界行为。 |
| 584 | `tests/test_project_workflow_runner.py::test_research_review_stage_checkpoint_resumes_without_repeating_review` | 验证项目工作流运行器中“research 评审阶段检查点恢复执行不依赖重复评审”这一场景或边界行为。 |
| 585 | `tests/test_project_workflow_runner.py::test_research_review_reviewer_uses_workspace_agent` | 验证项目工作流运行器中“research 评审评审器使用工作区 Agent”这一场景或边界行为。 |
| 586 | `tests/test_project_workflow_runner.py::test_auto_review_loop_review_cycle_uses_reviewer_workspace_agent` | 验证项目工作流运行器中“自动评审循环评审 cycle 使用评审器工作区 Agent”这一场景或边界行为。 |
| 587 | `tests/test_project_workflow_runner.py::test_auto_review_loop_persists_aris_state_files` | 验证项目工作流运行器中“自动评审循环持久化 ARIS 状态文件”这一场景或边界行为。 |
| 588 | `tests/test_project_workflow_runner.py::test_literature_review_prompt_includes_library_and_workspace_pdf_matches` | 验证项目工作流运行器中“文献评审提示词包含 library与工作区 PDF 匹配”这一场景或边界行为。 |
| 589 | `tests/test_project_workflow_runner.py::test_paper_writing_auto_detects_compile_command_and_writes_round_pdfs` | 验证项目工作流运行器中“论文写作自动检测编译命令与写入轮次 pdfs”这一场景或边界行为。 |
| 590 | `tests/test_project_workflow_runner.py::test_auto_review_loop_almost_verdict_pauses_and_resumes_next_round` | 验证项目工作流运行器中“自动评审循环 almost 状态结论暂停与恢复执行下一步轮次”这一场景或边界行为。 |
| 591 | `tests/test_project_workflow_runner.py::test_run_experiment_stage_checkpoint_resumes_without_reinspecting_workspace` | 验证项目工作流运行器中“运行实验阶段检查点恢复执行不依赖 reinspecting 工作区”这一场景或边界行为。 |
| 592 | `tests/test_project_workflow_runner.py::test_run_experiment_local_wraps_command_with_claude_runtime_environment` | 验证项目工作流运行器中“运行实验本地包装命令带有 Claude 运行时环境”这一场景或边界行为。 |
| 593 | `tests/test_project_workflow_runner.py::test_experiment_audit_workflow_persists_audit_artifacts` | 验证项目工作流运行器中“实验审计工作流持久化审计产物”这一场景或边界行为。 |
| 594 | `tests/test_project_workflow_runner.py::test_auto_review_loop_wraps_command_with_claude_runtime_environment` | 验证项目工作流运行器中“自动评审循环包装命令带有 Claude 运行时环境”这一场景或边界行为。 |
| 595 | `tests/test_project_workflow_runner.py::test_full_pipeline_wraps_command_with_claude_runtime_environment` | 验证项目工作流运行器中“完整流水线包装命令带有 Claude 运行时环境”这一场景或边界行为。 |
| 596 | `tests/test_project_workflow_runner.py::test_run_experiment_remote_wraps_command_with_claude_runtime_environment` | 验证项目工作流运行器中“运行实验远程包装命令带有 Claude 运行时环境”这一场景或边界行为。 |
| 597 | `tests/test_project_workflow_runner.py::test_run_experiment_remote_launches_screen_session` | 验证项目工作流运行器中“运行实验远程启动 screen 会话会话”这一场景或边界行为。 |
| 598 | `tests/test_project_workflow_runner.py::test_run_experiment_remote_avoids_gpu_leases_between_runs` | 验证项目工作流运行器中“运行实验远程避免 GPU 租约之间运行”这一场景或边界行为。 |
| 599 | `tests/test_project_workflow_runner.py::test_run_experiment_remote_batch_launches_multiple_sessions` | 验证项目工作流运行器中“运行实验远程批量启动多个会话”这一场景或边界行为。 |
| 600 | `tests/test_project_workflow_runner.py::test_run_experiment_remote_batch_releases_failed_gpu_lease` | 验证项目工作流运行器中“运行实验远程批量释放失败 GPU 租约”这一场景或边界行为。 |

### tests/test_projects_router_flows.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 601 | `tests/test_projects_router_flows.py::test_project_crud_touch_and_default_target` | 验证项目路由流程中“项目 CRUD touch与默认目标”这一场景或边界行为。 |
| 602 | `tests/test_projects_router_flows.py::test_project_create_respects_explicit_local_workdir` | 验证项目路由流程中“项目创建遵守显式本地 workdir”这一场景或边界行为。 |
| 603 | `tests/test_projects_router_flows.py::test_touch_project_returns_404_when_row_was_deleted_during_flush` | 验证项目路由流程中“touch 项目返回 404 当行 was deleted 期间 flush”这一场景或边界行为。 |
| 604 | `tests/test_projects_router_flows.py::test_project_paper_link_flow` | 验证项目路由流程中“项目论文 link 流程”这一场景或边界行为。 |
| 605 | `tests/test_projects_router_flows.py::test_project_target_flow_and_remote_validation` | 验证项目路由流程中“项目目标流程与远程校验”这一场景或边界行为。 |
| 606 | `tests/test_projects_router_flows.py::test_project_workspace_context_aggregate` | 验证项目路由流程中“项目工作区上下文 aggregate”这一场景或边界行为。 |
| 607 | `tests/test_projects_router_flows.py::test_workflow_presets_hide_legacy_items` | 验证项目路由流程中“工作流预设 hide 兼容旧版项目”这一场景或边界行为。 |
| 608 | `tests/test_projects_router_flows.py::test_project_companion_overview_includes_latest_run_tasks_and_acp` | 验证项目路由流程中“项目 companion 概览包含最新运行任务与ACP”这一场景或边界行为。 |
| 609 | `tests/test_projects_router_flows.py::test_project_companion_snapshot_includes_tasks_sessions_and_messages` | 验证项目路由流程中“项目 companion 快照包含任务会话与消息”这一场景或边界行为。 |
| 610 | `tests/test_projects_router_flows.py::test_project_repo_flow_and_commit_listing` | 验证项目路由流程中“项目仓库流程与提交 listing”这一场景或边界行为。 |
| 611 | `tests/test_projects_router_flows.py::test_project_idea_manual_sync_and_async_flow` | 验证项目路由流程中“项目想法手动同步与异步流程”这一场景或边界行为。 |
| 612 | `tests/test_projects_router_flows.py::test_project_run_flow_retry_and_actions` | 验证项目路由流程中“项目运行流程重试与动作”这一场景或边界行为。 |
| 613 | `tests/test_projects_router_flows.py::test_project_run_paper_ids_build_full_index_and_link_to_project` | 验证项目路由流程中“项目运行论文 ID 构建模式完整索引与link到项目”这一场景或边界行为。 |
| 614 | `tests/test_projects_router_flows.py::test_project_run_external_literature_candidate_import_links_project` | 验证项目路由流程中“项目运行外部文献候选导入 links 项目”这一场景或边界行为。 |
| 615 | `tests/test_projects_router_flows.py::test_project_run_detail_falls_back_to_persisted_artifact_refs` | 验证项目路由流程中“项目运行详情回退到已持久化产物引用”这一场景或边界行为。 |
| 616 | `tests/test_projects_router_flows.py::test_delete_project_run_removes_records_tasks_and_artifacts` | 验证项目路由流程中“删除项目运行移除记录任务与产物”这一场景或边界行为。 |
| 617 | `tests/test_projects_router_flows.py::test_delete_project_run_can_keep_artifacts` | 验证项目路由流程中“删除项目运行可以保留产物”这一场景或边界行为。 |
| 618 | `tests/test_projects_router_flows.py::test_delete_project_run_rejects_active_run` | 验证项目路由流程中“删除项目运行拒绝活跃运行”这一场景或边界行为。 |
| 619 | `tests/test_projects_router_flows.py::test_project_run_create_accepts_auto_proceed_flag` | 验证项目路由流程中“项目运行创建可接受自动 proceed 标志”这一场景或边界行为。 |
| 620 | `tests/test_projects_router_flows.py::test_retry_legacy_project_run_is_rejected` | 验证项目路由流程中“重试兼容旧版项目运行是被拒绝”这一场景或边界行为。 |
| 621 | `tests/test_projects_router_flows.py::test_project_run_checkpoint_response_flow` | 验证项目路由流程中“项目运行检查点响应流程”这一场景或边界行为。 |
| 622 | `tests/test_projects_router_flows.py::test_project_run_submit_failure_marks_failed` | 验证项目路由流程中“项目运行提交失败标记失败”这一场景或边界行为。 |
| 623 | `tests/test_projects_router_flows.py::test_project_run_and_action_validation_errors` | 验证项目路由流程中“项目运行与动作校验错误”这一场景或边界行为。 |

### tests/test_removed_modules_surface.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 624 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[get-/settings/feishu-config-None]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：获取 / /设置/飞书 / 配置 / None。 |
| 625 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[put-/settings/feishu-config-payload1]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：put / /设置/飞书 / 配置 / payload1。 |
| 626 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[post-/settings/feishu-config/test-payload2]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：后置 / /设置/飞书 / 配置/test / payload2。 |
| 627 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[get-/settings/daily-report-config-None]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：获取 / /设置/日报 / 报告 / 配置 / None。 |
| 628 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[put-/settings/daily-report-config-payload4]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：put / /设置/日报 / 报告 / 配置 / payload4。 |
| 629 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[post-/jobs/daily-report/run-once-payload5]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：后置 / /任务/日报 / 报告/run / once / payload5。 |
| 630 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[post-/jobs/daily-report/send-only-payload6]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：后置 / /任务/日报 / 报告/send / only / payload6。 |
| 631 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[post-/jobs/daily-report/generate-only-payload7]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：后置 / /任务/日报 / 报告/生成 / only / payload7。 |
| 632 | `tests/test_removed_modules_surface.py::test_removed_notification_report_and_manual_maintenance_endpoints_are_absent[post-/jobs/graph/weekly-run-once-payload8]` | 验证已移除模块接口面中“已移除通知报告与手动 维护 端点会被不存在”这一场景或边界行为。参数化样例：后置 / /任务/graph/weekly / run / once / payload8。 |

### tests/test_research_assistant_tools.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 633 | `tests/test_research_assistant_tools.py::test_get_paper_detail_and_analysis_expose_saved_research_metadata` | 验证研究助手工具中“获取论文详情与分析 expose 已保存 research 元数据”这一场景或边界行为。 |
| 634 | `tests/test_research_assistant_tools.py::test_research_wiki_tools_seed_query_and_update_by_workspace_context` | 验证研究助手工具中“research Wiki 工具 seed 查询与更新通过工作区上下文”这一场景或边界行为。 |
| 635 | `tests/test_research_assistant_tools.py::test_ingest_external_literature_tool_imports_openalex_entries` | 验证研究助手工具中“采集入库外部文献工具导入 OpenAlex 条目”这一场景或边界行为。 |
| 636 | `tests/test_research_assistant_tools.py::test_preview_external_paper_tools_return_head_and_section` | 验证研究助手工具中“预览外部论文工具 return 头部与章节”这一场景或边界行为。 |
| 637 | `tests/test_research_assistant_tools.py::test_keyword_service_filters_zero_hit_suggestions` | 验证研究助手工具中“keyword 服务过滤 zero hit suggestions”这一场景或边界行为。 |
| 638 | `tests/test_research_assistant_tools.py::test_analyze_paper_rounds_streams_completed_bundle` | 验证研究助手工具中“分析论文轮次流式输出已完成包”这一场景或边界行为。 |
| 639 | `tests/test_research_assistant_tools.py::test_analyze_paper_rounds_returns_failure_when_bundle_invalid` | 验证研究助手工具中“分析论文轮次返回失败当包无效”这一场景或边界行为。 |
| 640 | `tests/test_research_assistant_tools.py::test_analyze_figures_returns_normalized_items_with_image_refs` | 验证研究助手工具中“分析图表返回归一化的项目带有图片引用”这一场景或边界行为。 |
| 641 | `tests/test_research_assistant_tools.py::test_researchos_mcp_paper_tools_can_use_detached_paper_snapshot` | 验证研究助手工具中“researchos MCP 论文工具可以使用游离论文快照”这一场景或边界行为。 |

### tests/test_research_tool_runtime.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 642 | `tests/test_research_tool_runtime.py::test_research_venue_catalog_matches_ccf_a_aliases` | 验证研究工具中“research 会议/期刊目录匹配 CCF A 类 aliases”这一场景或边界行为。 |
| 643 | `tests/test_research_tool_runtime.py::test_search_literature_filters_ccf_a_conferences` | 验证研究工具中“检索文献过滤 CCF A 类 conferences”这一场景或边界行为。 |
| 644 | `tests/test_research_tool_runtime.py::test_search_literature_merges_openalex_and_arxiv_without_duplicates` | 验证研究工具中“检索文献合并 OpenAlex与arxiv 不依赖重复项”这一场景或边界行为。 |

### tests/test_research_venue_catalog_coverage.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 645 | `tests/test_research_venue_catalog_coverage.py::test_ccf_a_conference_catalog_has_expected_core_coverage` | 验证研究工具中“CCF A 类会议目录具有预期核心 coverage”这一场景或边界行为。 |
| 646 | `tests/test_research_venue_catalog_coverage.py::test_ccf_a_catalog_matches_realistic_openalex_proceedings_names` | 验证研究工具中“CCF A 类目录匹配真实风格 OpenAlex proceedings names”这一场景或边界行为。 |
| 647 | `tests/test_research_venue_catalog_coverage.py::test_ccf_a_catalog_rejects_workshop_and_extended_abstract_variants` | 验证研究工具中“CCF A 类目录拒绝 workshop与extended abstract variants”这一场景或边界行为。 |
| 648 | `tests/test_research_venue_catalog_coverage.py::test_search_literature_returns_only_ccf_a_conferences_for_realistic_venues` | 验证研究工具中“检索文献返回仅 CCF A 类 conferences 针对真实风格 venues”这一场景或边界行为。 |
| 649 | `tests/test_research_venue_catalog_coverage.py::test_search_literature_can_filter_specific_ccf_a_conference_aliases` | 验证研究工具中“检索文献可以过滤指定 CCF A 类会议 aliases”这一场景或边界行为。 |

### tests/test_runtime_safety_regressions.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 650 | `tests/test_runtime_safety_regressions.py::test_ttl_cache_get_removes_expired_key` | 验证运行时安全 回归中“ttl 缓存获取移除 expired 密钥”这一场景或边界行为。 |
| 651 | `tests/test_runtime_safety_regressions.py::test_default_assistant_exec_policy_is_not_full_auto` | 验证运行时安全 回归中“默认研究助手 exec 策略不是完整自动”这一场景或边界行为。 |
| 652 | `tests/test_runtime_safety_regressions.py::test_folder_stats_groups_dates_without_rounding_timezone_offset` | 验证运行时安全 回归中“文件夹统计分组 日期 不依赖 rounding 时区偏移”这一场景或边界行为。 |
| 653 | `tests/test_runtime_safety_regressions.py::test_semantic_candidates_scan_beyond_recent_500` | 验证运行时安全 回归中“语义候选扫描 beyond recent 500”这一场景或边界行为。 |

### tests/test_session_message_v2.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 654 | `tests/test_session_message_v2.py::test_message_v2_page_stream_get_and_parts` | 验证会话消息 v2中“消息 v2 页面流式输出获取与片段”这一场景或边界行为。 |
| 655 | `tests/test_session_message_v2.py::test_message_v2_filter_compacted_and_to_model_messages` | 验证会话消息 v2中“消息 v2 过滤压缩后的与到模型消息”这一场景或边界行为。 |
| 656 | `tests/test_session_message_v2.py::test_message_v2_from_error_adds_provider_context` | 验证会话消息 v2中“消息 v2 从错误会添加提供商上下文”这一场景或边界行为。 |
| 657 | `tests/test_session_message_v2.py::test_message_v2_runtime_info_aligns_user_and_assistant_shapes` | 验证会话消息 v2中“消息 v2 运行时 info 对齐用户与研究助手 shapes”这一场景或边界行为。 |

### tests/test_settings_llm_provider_presets.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 658 | `tests/test_settings_llm_provider_presets.py::test_llm_provider_presets_include_zhipu` | 验证设置 LLM 提供商 预设中“LLM 提供商预设包含智谱”这一场景或边界行为。 |
| 659 | `tests/test_settings_llm_provider_presets.py::test_llm_provider_presets_include_gemini` | 验证设置 LLM 提供商 预设中“LLM 提供商预设包含 Gemini”这一场景或边界行为。 |
| 660 | `tests/test_settings_llm_provider_presets.py::test_cfg_to_out_preserves_zhipu_selection_for_main_and_embedding` | 验证设置 LLM 提供商 预设中“配置到输出保留智谱选择针对 main与嵌入”这一场景或边界行为。 |
| 661 | `tests/test_settings_llm_provider_presets.py::test_cfg_to_out_preserves_gemini_and_image_generation_fields` | 验证设置 LLM 提供商 预设中“配置到输出保留 Gemini与图片 generation 字段”这一场景或边界行为。 |
| 662 | `tests/test_settings_llm_provider_presets.py::test_cfg_to_out_keeps_openai_protocol_for_non_zhipu_兼容ible_targets` | 验证设置 LLM 提供商 预设中“配置到输出保持 OpenAI protocol 针对非智谱兼容目标”这一场景或边界行为。 |

### tests/test_storage_bootstrap.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 663 | `tests/test_storage_bootstrap.py::test_importing_storage_db_does_not_create_schema` | 验证存储层中“importing 存储 db 不会创建 Schema”这一场景或边界行为。 |
| 664 | `tests/test_storage_bootstrap.py::test_explicit_bootstrap_initializes_schema` | 验证存储层中“显式启动初始化初始化 Schema”这一场景或边界行为。 |
| 665 | `tests/test_storage_bootstrap.py::test_explicit_bootstrap_stamps_legacy_runtime_schema` | 验证存储层中“显式启动初始化写入版本标记兼容旧版运行时 Schema”这一场景或边界行为。 |

### tests/test_storage_json_schema.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 666 | `tests/test_storage_json_schema.py::test_with_schema_version_preserves_existing_version` | 验证存储层中“带有 Schema 版本保留已有版本”这一场景或边界行为。 |
| 667 | `tests/test_storage_json_schema.py::test_task_repository_writes_versioned_sidecar_log_rows` | 验证存储层中“任务仓储写入 versioned 旁路日志 rows”这一场景或边界行为。 |

### tests/test_task_tracker.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 668 | `tests/test_task_tracker.py::test_finish_without_total_reports_full_progress` | 验证任务追踪器中“结束不依赖 total 报告完整进度”这一场景或边界行为。 |
| 669 | `tests/test_task_tracker.py::test_list_tasks_returns_latest_first_and_supports_filter` | 验证任务追踪器中“列表任务返回最新 first与支持过滤”这一场景或边界行为。 |
| 670 | `tests/test_task_tracker.py::test_finished_task_elapsed_seconds_stops_at_finish` | 验证任务追踪器中“已完成任务 elapsed seconds 停止 at 结束”这一场景或边界行为。 |
| 671 | `tests/test_task_tracker.py::test_task_tracker_supports_logs_and_retry_metadata` | 验证任务追踪器中“任务任务追踪器支持日志与重试元数据”这一场景或边界行为。 |
| 672 | `tests/test_task_tracker.py::test_task_tracker_persists_tasks_and_results` | 验证任务追踪器中“任务任务追踪器持久化任务与结果”这一场景或边界行为。 |
| 673 | `tests/test_task_tracker.py::test_task_tracker_bootstrap_marks_running_tasks_interrupted` | 验证任务追踪器中“任务任务追踪器启动初始化标记 running 任务 interrupted”这一场景或边界行为。 |
| 674 | `tests/test_task_tracker.py::test_task_tracker_pause_persists_and_survives_bootstrap` | 验证任务追踪器中“任务任务追踪器暂停持久化与可跨越启动初始化”这一场景或边界行为。 |

### tests/test_tool_registry.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 675 | `tests/test_tool_registry.py::test_tool_registry_custom_registration_exposes_and_executes_tool` | 验证工具注册表中“工具注册表自定义 registration 暴露与执行工具”这一场景或边界行为。 |
| 676 | `tests/test_tool_registry.py::test_tool_registry_builtin_definition_carries_permission_spec` | 验证工具注册表中“工具注册表内置定义携带权限规格”这一场景或边界行为。 |
| 677 | `tests/test_tool_registry.py::test_tool_registry_research_tools_share_the_catalog_surface` | 验证工具注册表中“工具注册表 research 工具共享 the 目录接口面”这一场景或边界行为。 |
| 678 | `tests/test_tool_registry.py::test_tool_registry_default_workspace_exposure_comes_from_tool_definition_spec` | 验证工具注册表中“工具注册表默认工作区暴露 comes 从工具定义规格”这一场景或边界行为。 |
| 679 | `tests/test_tool_registry.py::test_researchos_public_bridge_hides_legacy_tools_but_keeps_new_preview_tools` | 验证工具注册表中“researchos public 桥接隐藏兼容旧版工具但保持新建预览工具”这一场景或边界行为。 |
| 680 | `tests/test_tool_registry.py::test_tool_registry_custom_tool_can_embed_permission_spec` | 验证工具注册表中“工具注册表自定义工具可以嵌入权限规格”这一场景或边界行为。 |
| 681 | `tests/test_tool_registry.py::test_tool_registry_builtin_handler_resolution_comes_from_tool_definition` | 验证工具注册表中“工具注册表内置处理器 resolution comes 从工具定义”这一场景或边界行为。 |
| 682 | `tests/test_tool_registry.py::test_all_default_local_build_tools_resolve_to_executable_handlers` | 验证工具注册表中“全部默认本地构建模式工具解析到executable handlers”这一场景或边界行为。 |
| 683 | `tests/test_tool_registry.py::test_tool_registry_glob_and_grep_are_executable_for_local_workspace` | 验证工具注册表中“工具注册表 glob 搜索与grep 搜索会被 executable 针对本地工作区”这一场景或边界行为。 |
| 684 | `tests/test_tool_registry.py::test_local_workspace_core_tools_execute_smoke` | 验证工具注册表中“本地工作区核心工具执行烟测”这一场景或边界行为。 |
| 685 | `tests/test_tool_registry.py::test_plan_mode_edit_can_materialize_missing_plan_file` | 验证工具注册表中“计划模式模式编辑可以 物化 缺失计划模式文件”这一场景或边界行为。 |

### tests/test_topic_subscription_filters.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 686 | `tests/test_topic_subscription_filters.py::test_topic_filters_persist_and_can_be_cleared` | 验证专题订阅中“专题过滤 persist与可以 be 被清空”这一场景或边界行为。 |
| 687 | `tests/test_topic_subscription_filters.py::test_run_topic_ingest_uses_persisted_external_filters` | 验证专题订阅中“运行专题采集入库使用已持久化外部过滤”这一场景或边界行为。 |

### tests/test_topics_cache_invalidation.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 688 | `tests/test_topics_cache_invalidation.py::test_topic_mutations_invalidate_folder_stats_cache` | 验证专题订阅中“专题 mutations in校验 文件夹统计缓存”这一场景或边界行为。 |

### tests/test_web_tool_runtime.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 689 | `tests/test_web_tool_runtime.py::test_search_web_returns_typed_timeout_error` | 验证Web 工具运行时中“检索 Web 返回类型化超时错误”这一场景或边界行为。 |
| 690 | `tests/test_web_tool_runtime.py::test_webfetch_returns_typed_not_found_error` | 验证Web 工具运行时中“WebFetch 返回类型化不 found 错误”这一场景或边界行为。 |
| 691 | `tests/test_web_tool_runtime.py::test_codesearch_returns_typed_network_error` | 验证Web 工具运行时中“代码search 返回类型化网络错误”这一场景或边界行为。 |

### tests/test_worker_schedule.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 692 | `tests/test_worker_schedule.py::test_worker_registers_automatic_maintenance_and_brief_jobs` | 验证Worker 调度中“Worker registers 自动matic 维护与日报任务”这一场景或边界行为。 |
| 693 | `tests/test_worker_schedule.py::test_cron_display_uses_configured_user_timezone` | 验证Worker 调度中“cron 展示使用已配置用户时区”这一场景或边界行为。 |

### tests/test_workspace_executor_paths.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 694 | `tests/test_workspace_executor_paths.py::test_inspect_workspace_does_not_create_missing_directory` | 验证工作区执行器路径处理中“检查工作区不会创建缺失目录”这一场景或边界行为。 |
| 695 | `tests/test_workspace_executor_paths.py::test_inspect_workspace_can_disable_entry_cap` | 验证工作区执行器路径处理中“检查工作区可以 disable 条目 cap”这一场景或边界行为。 |
| 696 | `tests/test_workspace_executor_paths.py::test_workspace_overview_route_accepts_zero_as_unlimited` | 验证工作区执行器路径处理中“工作区概览路由可接受 zero 作为无限制”这一场景或边界行为。 |
| 697 | `tests/test_workspace_executor_paths.py::test_write_workspace_file_repairs_empty_directory_conflict` | 验证工作区执行器路径处理中“写入工作区文件修复空目录冲突”这一场景或边界行为。 |
| 698 | `tests/test_workspace_executor_paths.py::test_edit_path_file_with_empty_old_string_creates_missing_file` | 验证工作区执行器路径处理中“编辑路径文件带有空 old string 创建缺失文件”这一场景或边界行为。 |
| 699 | `tests/test_workspace_executor_paths.py::test_reveal_workspace_does_not_create_missing_file_path` | 验证工作区执行器路径处理中“reveal 工作区不会创建缺失文件路径”这一场景或边界行为。 |
| 700 | `tests/test_workspace_executor_paths.py::test_run_workspace_command_returns_updated_cwd` | 验证工作区执行器路径处理中“运行工作区命令返回 updated cwd”这一场景或边界行为。 |
| 701 | `tests/test_workspace_executor_paths.py::test_create_workspace_git_branch_detects_existing_branch` | 验证工作区执行器路径处理中“创建工作区 Git branch 检测已有 branch”这一场景或边界行为。 |
| 702 | `tests/test_workspace_executor_paths.py::test_grep_path_contents_prioritizes_implementation_paths_over_tests_and_docs` | 验证工作区执行器路径处理中“grep 搜索路径内容优先排序 implementation 路径超过 tests与docs”这一场景或边界行为。 |
| 703 | `tests/test_workspace_executor_paths.py::test_glob_path_entries_prioritizes_implementation_paths_over_tests_and_docs` | 验证工作区执行器路径处理中“glob 搜索路径条目优先排序 implementation 路径超过 tests与docs”这一场景或边界行为。 |
| 704 | `tests/test_workspace_executor_paths.py::test_grep_path_contents_prioritizes_definition_lines_for_identifier_queries` | 验证工作区执行器路径处理中“grep 搜索路径内容优先排序定义 lines 针对标识符查询”这一场景或边界行为。 |
| 705 | `tests/test_workspace_executor_paths.py::test_grep_path_contents_skips_generated_noise_before_falling_back_to_workspace_root` | 验证工作区执行器路径处理中“grep 搜索路径内容跳过生成的噪声之前 falling 回退到工作区根目录”这一场景或边界行为。 |
| 706 | `tests/test_workspace_executor_paths.py::test_grep_path_contents_treats_wildcard_include_as_unscoped_identifier_lookup` | 验证工作区执行器路径处理中“grep 搜索路径内容 treats wildcard 包含作为 unscoped 标识符查找”这一场景或边界行为。 |
| 707 | `tests/test_workspace_executor_paths.py::test_grep_path_contents_treats_star_dot_star_as_unscoped_identifier_lookup` | 验证工作区执行器路径处理中“grep 搜索路径内容 treats star dot star 作为 unscoped 标识符查找”这一场景或边界行为。 |
| 708 | `tests/test_workspace_executor_paths.py::test_read_path_file_supports_line_offset_and_limit_windows` | 验证工作区执行器路径处理中“读取路径文件支持行偏移与limit Windows”这一场景或边界行为。 |
| 709 | `tests/test_workspace_executor_paths.py::test_run_workspace_command_handles_multiline_python_without_outer_command_quoting_breakage` | 验证工作区执行器路径处理中“运行工作区命令处理 multiline Python 不依赖 outer 命令 quoting breakage”这一场景或边界行为。 |

### tests/test_writing_image_service.py

| 序号 | Node ID | 中文描述 |
| ---: | --- | --- |
| 710 | `tests/test_writing_image_service.py::test_generate_image_builds_gemini_request` | 验证写作图片服务中“生成图片构建 Gemini 请求”这一场景或边界行为。 |
| 711 | `tests/test_writing_image_service.py::test_generate_image_rejects_unsupported_aspect_ratio` | 验证写作图片服务中“生成图片拒绝不支持的宽高比例”这一场景或边界行为。 |
