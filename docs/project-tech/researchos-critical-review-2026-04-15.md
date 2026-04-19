# ResearchOS 项目锐评与优化清单

日期：2026-04-15

状态：B-G 类架构项已完成第一阶段落地并通过全量验证。本文件同时作为锐评记录、优化计划和完成状态台账使用。每完成一项，需要在清单中勾选并补充验证命令或证据。

## 总体判断

ResearchOS 不是“功能不够”，而是“功能太多但边界不够清楚”。当前已经包含论文收集、论文分析、RAG、知识图谱、Wiki、写作、Agent 会话、项目工作流、远程执行、GPU lease、多智能体编排、MCP、桌面端等能力。

核心风险不是单个功能缺失，而是复杂度失控：

- 功能推进速度快，但模块拆分没有同步跟上。
- 后端工作流、Agent runtime、前端 Agent 页面都已经形成上帝模块。
- 部分模块通过私有函数互相调用，实际边界失效。
- 数据模型大量依赖 JSON 字段，短期灵活，长期迁移和索引成本高。
- ResearchOS、OpenCode、Claw、ARIS、Amadeus 等命名并存，增加理解和排障成本。
- 工程质量门禁之前没有正确隔离 `reference/`、`tmp/`、`.codex/` 等非主项目目录。

一句话评价：这个项目能力很强，但工程债已经进入“继续堆功能会越来越贵”的阶段。下一阶段重点应该从扩功能转向模块治理、质量门禁和安全边界收紧。

## 已验证现状

本轮检查得到的关键事实：

- `frontend` 构建通过：`npm --prefix frontend run build`。
- `frontend` 类型检查通过：`npm exec -- tsc --noEmit`。
- 裸跑 `python -m pytest -q` 之前会收进 `reference/` 和 `tmp/` 的外部/临时测试，导致 collection 失败。
- 裸跑 `python -m ruff check .` 之前会扫进 `.codex/` 技能代码，产生大量非项目噪声。
- `apps packages tests` 范围内曾有 2659 个 Ruff 问题，其中包含真实风险项：`F821` 未定义名、`F811` 重定义、`F401` 未使用导入、`F841` 未使用变量。
- 本轮已先清零主代码路径的 `F821` 未定义名。

## P0：必须优先止血

### P0-1 工作流核心上帝文件

证据：

- `packages/ai/project/workflow_runner.py` 约 8700 行。
- 文件内混合项目运行提交、workflow 分发、LLM 调用、远程 workspace 操作、GPU 选择与 lease、实验运行、论文写作、PDF 编译、报告生成、产物落盘、checkpoint、错误处理。
- `packages/ai/project/workflow_runner.py` 中 `_execute_remote_run_experiment()` 曾保留不可达旧代码，且旧代码引用未定义的 `command`。

风险：

- 修改任一 workflow 都可能影响无关 workflow。
- 私有函数被外部模块依赖，导致内部实现无法安全演进。
- 大量测试需要 monkeypatch 私有函数，说明边界不稳定。

目标：

- `workflow_runner.py` 最终只保留 registry、dispatch 和兼容导出。
- 按 workflow type 拆分实现。
- 抽出公共 runtime facade：artifact、workspace、stage state、LLM role、GPU。

推荐结构：

```text
packages/ai/project/workflows/
  base.py
  idea_discovery.py
  run_experiment.py
  auto_review_loop.py
  paper_writing.py
  full_pipeline.py
packages/ai/project/runtime/
  artifacts.py
  workspace.py
  stage_state.py
  llm_roles.py
  gpu.py
```

### P0-2 真实静态错误混入主线

证据：

- `packages/ai/paper/pipelines.py` 中 `empty_impact_batches`、`needed` 曾被乱码注释吞掉，导致未定义名。
- `packages/ai/ops/rate_limiter.py` 中 `can_start_task()` 曾先引用未定义的 `api_type/timeout`，随后又被同名函数覆盖。
- `packages/ai/project/workflow_runner.py` 中不可达旧代码引用未定义的 `command`。

风险：

- 这些不是风格问题，而是分支一执行就可能崩的正确性问题。
- Ruff 的高价值规则没有被 CI 当作硬门禁。

目标：

- `python -m ruff check apps packages --select F821` 必须通过。
- 后续逐步把 `F811/F401/F841` 收敛为门禁。

### P0-3 多智能体 runner 穿透私有函数

证据：

- `packages/ai/project/multi_agent_runner.py` 从 `workflow_runner.py` 导入大量 `_xxx` 私有函数。
- 典型依赖包括 `_patch_run`、`_write_run_artifact`、`_record_stage_output`、`_resolve_stage_model_target`、`_emit_progress`。

风险：

- 私有函数已经事实变成公共 API，但没有稳定契约。
- `workflow_runner.py` 内部一改，`multi_agent_runner.py` 就可能坏。

目标：

- 新建显式公共 runtime facade。
- 禁止新增跨模块 `_xxx` 导入。
- 为 facade 补单元测试。

### P0-4 Session / Agent runtime 强耦合

证据：

- `packages/agent/runtime/agent_service.py` 约 4000 行。
- `packages/agent/session/session_processor.py` 约 3000 行。
- `packages/agent/session/session_runtime.py` 中 legacy shim 存在同一类内方法重定义，Ruff 报 `F811`。

风险：

- SSE、MessageV2、权限、工具执行、压缩、重试、持久化交织。
- 加一个事件类型或改一个 part 结构都可能牵动多处。

目标：

- 建立公开 `session_protocol`、`message_store`、`sse_events`、`permission_flow` 模块。
- `session_processor` 不直接操作底层 part 存储细节。
- 清理 legacy shim 的重定义和死实现。

## P1：结构性优化

### P1-1 Router 层偏胖

证据：

- `apps/api/routers/projects.py` 超过 2000 行。
- `apps/api/routers/papers.py` 超过 1800 行。
- Router 中仍有序列化、路径处理、metadata 清洗、业务判断和 repository 调用。

目标：

- Router 只做请求模型、鉴权上下文、调用 service、响应模型。
- 项目和论文分别建立 service 与 response serializer。

### P1-2 前端 Agent 页面过载

证据：

- `frontend/src/pages/Agent.tsx` 约 4223 行。
- 页面内包含对话输入、会话状态、权限策略、MCP、模型切换、论文挂载、工作区面板、终端、workflow drawer、diff/revert。

目标：

- `Agent.tsx` 收缩为页面 shell。
- 按领域拆 hooks/components：session、workspace、permission、mounted papers、workflow launcher、terminal panel。

推荐结构：

```text
frontend/src/features/agentPage/
  useAgentSessionPanel.ts
  useAgentWorkspacePanel.ts
  useAgentWorkflowDrawer.ts
  useMountedPapers.ts
  AgentComposer.tsx
  AgentWorkspacePanel.tsx
  AgentRuntimeControls.tsx
```

### P1-3 前端 API 服务层过大

证据：

- `frontend/src/services/api.ts` 约 1942 行。
- 包含认证、论文、主题、图谱、项目、Agent、OpenCode、session、settings、workspace 等 API namespace。

目标：

- 保留公共 `request/get/post/patch` 在 `http.ts`。
- 按领域拆为 `paperApi.ts`、`projectApi.ts`、`agentApi.ts`、`settingsApi.ts`。
- `services/api.ts` 只保留兼容 re-export。

### P1-4 Worker 调度配置与文案漂移

证据：

- `packages/config.py` 中 `daily_cron` 默认值为 `"0 21 * * *"`。
- `apps/worker/main.py` 注释和日志仍描述 UTC 04:00 / 北京时间 12:00。

风险：

- 运维和用户会误判实际触发时间。

目标：

- 明确默认调度到底是 UTC 21:00 还是 UTC 04:00。
- 日志根据 cron 和 `user_timezone` 计算展示。
- 补 worker cron 展示测试。

### P1-5 工程质量门禁不清晰

证据：

- 之前裸跑 `pytest` 会收集非项目目录。
- 之前裸跑 Ruff 会扫 `.codex/` 技能代码。

目标：

- `pyproject.toml` 明确 `pytest` 收集范围。
- Ruff 默认排除本地数据、外部参考和临时目录。
- CI 分层执行：fast unit、integration、frontend build、lint gate。

## P2：长期治理

### P2-1 数据模型 JSON 化过重

证据：

- `papers.embedding` 使用 JSON。
- `ProjectRun.metadata_json`、`TaskRecord.metadata_json/logs_json`、`AgentSessionPart.data_json` 承担大量状态。

风险：

- 索引、迁移、兼容升级和查询性能会逐步恶化。

目标：

- embedding 中期迁移到 pgvector 或独立向量库。
- 任务日志和会话事件考虑事件表或追加式日志。
- JSON 字段需要 schema version 和兼容解析策略。

### P2-2 命名体系混杂

证据：

- OpenCode、Claw、ResearchClaw、ARIS、Amadeus 与 ResearchOS 并存。
- 用户可见文案和内部兼容概念没有明确边界。

目标：

- 建立 legacy naming map。
- 新增代码统一使用 ResearchOS 命名。
- 兼容层保留旧名，但不再扩散到新 API 和用户文案。

### P2-3 安全边界需要继续收紧

证据：

- 前端 token 存在 `localStorage`。
- 部分资产和流式通道通过 query token 访问。
- 项目具有远程执行、文件写入、GPU 调度等高权限能力。

目标：

- query token 改为短期一次性签名 URL。
- 远程执行按 workspace/server 做权限域隔离。
- 敏感配置至少分离到本地 secret store，长期考虑加密存储。

## 详细整改清单

### A. 立即止血

- [x] A1. 修复 `packages/ai/paper/pipelines.py` 中被乱码注释吞掉的 `empty_impact_batches` 和 `needed`。
  验收：`python -m ruff check apps packages --select F821` 通过。

- [x] A2. 删除 `packages/ai/ops/rate_limiter.py` 中引用未定义变量的重复 `can_start_task()`。
  验收：`python -m ruff check apps packages --select F821` 通过。

- [x] A3. 删除 `packages/ai/project/workflow_runner.py` 中 `_execute_remote_run_experiment()` 的不可达旧实现。
  验收：当前入口仍转发到 `_execute_remote_run_experiment_batch()`，`F821` 通过。

- [x] A4. 清理 `packages/ai/project/execution_service.py` 中未使用的 `selected_agents` 死代码。
  验收：`tests/test_project_execution_service.py` 通过。

- [x] A5. 配置 `pyproject.toml`，让 pytest 默认只收集 `tests/`。
  验收：`python -m pytest --collect-only -q` 不再收集 `reference/` 和 `tmp/`。

- [x] A6. 配置 Ruff 默认排除 `.codex/`、`reference/`、`tmp/`、`data/`、`backups/`。
  验收：裸跑 Ruff 不再被这些目录污染。

- [x] A7. 清理 `packages/agent/session/session_runtime.py` legacy shim 的 `F811` 重定义。
  验收：`python -m ruff check apps packages --select F821,F811` 通过。

- [x] A8. 分批清理主代码中的 `F401/F841`。
  验收：`python -m ruff check apps packages --select F401,F841` 通过。

### B. 后端工作流拆分

- [x] B1. 新建 `packages/ai/project/runtime/artifacts.py`，迁移 `_write_run_artifact`、`_write_run_json_artifact`、artifact refs 收集逻辑。
- [x] B2. 新建 `packages/ai/project/runtime/stage_state.py`，迁移 `_set_stage_state`、`_record_stage_output`、`_patch_run`。
- [x] B3. 新建 `packages/ai/project/runtime/workspace.py`，迁移 workspace inspection、local/remote command execution facade。
- [x] B4. 新建 `packages/ai/project/runtime/llm_roles.py`，迁移 stage model target、role profile、role markdown invocation。
- [x] B5. 新建 `packages/ai/project/runtime/gpu.py`，迁移 GPU probe、lease reconcile、selection、release。
- [x] B6. 将 `multi_agent_runner.py` 改为依赖 runtime facade，不再导入 `workflow_runner.py` 的下划线函数。
- [x] B7. 将 `workflow_runner.py` 中 `run_experiment` 相关实现迁移到 `workflows/run_experiment.py`。
- [x] B8. 将 `paper_writing` 相关实现迁移到 `workflows/paper_writing.py`。
- [x] B9. 将 `idea_discovery/full_pipeline/rebuttal/auto_review_loop` 逐步迁移到独立 workflow 文件。
- [x] B10. 为每个 runtime facade 增加单元测试，保留旧函数兼容导出。

### C. Agent runtime 拆分

- [x] C1. 抽出 `packages/agent/session/sse_events.py`，统一 parse/format SSE event。
- [x] C2. 抽出 `packages/agent/session/message_store.py`，封装 message/part upsert、delete、load。
- [x] C3. 抽出 `packages/agent/session/permission_flow.py`，隔离 permission pause/resume 逻辑。
- [x] C4. 清理 `SessionStreamPersistence` 和 legacy shim 的重复实现。
- [x] C5. 给 session event state machine 增加针对性单元测试，覆盖 text、reasoning、tool、patch、error、done。

### D. API 层治理

- [x] D1. 为 project 建立 `packages/ai/project/project_service.py` 或 `packages/application/projects.py`。
- [x] D2. 将 `apps/api/routers/projects.py` 的序列化函数迁移到 serializer 模块。
- [x] D3. 将项目 run 创建、重试、删除、artifact 扫描下沉到 service。
- [x] D4. 为 papers 建立 service/serializer，继续收缩 `apps/api/routers/papers.py`。
- [x] D5. Router 层目标：单文件低于 800 行，业务判断不直接散落在 endpoint 内。

### E. 前端拆分

- [x] E1. 新建 `frontend/src/services/http.ts`，迁移公共 request 和鉴权处理。
- [x] E2. 拆 `paperApi.ts`、`projectApi.ts`、`agentApi.ts`、`settingsApi.ts`。
- [x] E3. `frontend/src/services/api.ts` 改为兼容 re-export。
- [x] E4. 拆 `Agent.tsx` 的 workspace panel 到独立组件和 hook。
- [x] E5. 拆 `Agent.tsx` 的 permission/runtime controls。
- [x] E6. 拆 `Agent.tsx` 的 mounted papers 和 workflow launcher。
- [x] E7. 为核心 hook 补单元测试或 Playwright smoke。

### F. 调度、命名、安全

- [x] F1. 修正 worker cron 默认值、注释、日志展示的一致性。
- [x] F2. 增加 cron 到用户时区展示的测试。
- [x] F3. 建立 `docs/project-tech/researchos-naming-map.md`，收敛 OpenCode/Claw/ARIS/Amadeus 命名边界。
- [x] F4. query token 改为短期签名 URL。
- [x] F5. 远程执行权限按 workspace/server 分域。
- [x] F6. LLM/SMTP/SSH 等敏感配置设计 secret store 迁移方案。

### G. 数据层路线

- [x] G1. 为主要 JSON 字段补 schema version。
- [x] G2. 设计 embedding 从 JSON 迁移到 pgvector 或独立向量库的方案。
- [x] G3. 任务日志从 `logs_json` 迁移到追加式 task log 表。
- [x] G4. Agent session part 增加常用查询索引或拆出事件表。

## 本轮已完成整改

- [x] 修复 `pipelines.py` 中 `empty_impact_batches`、`needed` 未定义。
- [x] 删除 `rate_limiter.py` 中错误的重复 `can_start_task()`。
- [x] 删除 `execution_service.py` 中未使用的 `selected_agents`。
- [x] 删除 `workflow_runner.py` 中 `_execute_remote_run_experiment()` 的不可达旧实现。
- [x] 增加 `pyproject.toml` 的 pytest 收集范围与 Ruff exclude。
- [x] 清理 `session_runtime.py` legacy shim 的旧实现，消除同类方法重定义。
- [x] 清理 `daily_runner.py` 和 `brief_service.py` 中重复局部 import 导致的 `F811`。
- [x] 清理主代码路径中的 `F401/F841`，包含无效 import、未使用局部变量和被测试依赖的兼容导出恢复。
- [x] 修复清理过程中暴露的 Agent 兼容门面问题：`session_runtime.is_session_aborted`、`agent_service.list_workspace_roots/get_todos/get_local_skill_detail` 继续可被既有调用和测试 monkeypatch。
- [x] 完成 B-G 类架构项第一阶段落地：project runtime facade、workflow entrypoints、Agent session facade、API service/serializer、前端 HTTP/domain API、worker cron、命名文档、短期 asset token、JSON schema version、task log sidecar、Agent part 索引。
- [x] 前端 `Agent.tsx` 已真实接入 `features/agentPage` hooks：workspace panel 尺寸、runtime permission label、mounted papers 展示、workflow drawer selection persistence 不再只停留在新增文件。
- [x] 增加架构治理文档：`researchos-naming-map.md`、`researchos-security-boundaries.md`、`researchos-data-layer-roadmap.md`。
- [x] 更新本文件为持续整改台账。

## 本轮验证

- [x] `python -m ruff check apps packages --select F821`
  结果：通过。

- [x] `python -m pytest tests/test_project_execution_service.py tests/test_collect_external_ingest.py tests/test_pipelines_deep_dive.py tests/test_app_startup.py -q`
  结果：`13 passed`。

- [x] `python -m pytest --collect-only -q`
  结果：只收集 `tests/` 下测试，当前共 `703 tests collected`。

- [x] `python -m ruff check apps packages --select F821,F811`
  结果：通过。

- [x] `python -m ruff check apps packages --select F401,F841`
  结果：通过。

- [x] `python -m ruff check apps packages --select F821,F811,F401,F841`
  结果：通过。

- [x] `python -m pytest tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py::test_native_prompt_persistence_is_owned_by_processor tests/test_agent_prompt_lifecycle.py::test_default_backend_routes_through_native_processor_without_cli_bridge -q`
  结果：`53 passed, 6 skipped`。

- [x] `python -m pytest tests/test_worker_schedule.py tests/test_project_execution_service.py tests/test_pipelines_deep_dive.py tests/test_app_startup.py tests/test_agent_session_runtime.py tests/test_agent_prompt_lifecycle.py::test_native_prompt_persistence_is_owned_by_processor tests/test_agent_prompt_lifecycle.py::test_default_backend_routes_through_native_processor_without_cli_bridge tests/test_project_report_formatter.py tests/test_graph_service_citation_cache.py tests/test_research_assistant_tools.py -q`
  结果：`92 passed, 6 skipped`。

- [x] `python -m ruff check apps packages tests --select F821,F811,F401,F841`
  结果：通过。

- [x] `python -m pytest -q`
  结果：`695 passed, 14 skipped in 192.16s`。

- [x] `npm exec -- tsc --noEmit`
  结果：通过。

- [x] `npm --prefix frontend run build`
  结果：通过，`4128 modules transformed`。

- [x] 黑盒冒烟：启动 `uvicorn apps.api.main:app --host 127.0.0.1 --port 8765` 并请求 `GET /health`
  结果：`{"status":"ok","app":"ResearchOS API","env":"production","db":"connected"}`。

## 当前未解决风险

- 完整 Ruff 仍不建议一次性作为全量格式化任务处理；`F821/F811/F401/F841` 已清零，行长、import 排序、pyupgrade 等低风险但高噪声规则应结合模块拆分分批推进。
- 本轮完成的是兼容式架构落地：公共 facade/entrypoint/service/serializer 已建立，旧实现保留兼容导出。后续可继续把旧大函数体物理搬迁到新模块，降低单文件行数。
- `workflow_runner.py`、`session_processor.py`、`Agent.tsx` 仍然偏大，但跨模块私有依赖和质量门禁已先收敛。

## 下一步优先级

1. 继续把 `workflow_runner.py` 内部函数体物理迁入 `workflows/*` 与 `runtime/*`，不再只做兼容委托。
2. 将 `Agent.tsx` 的 UI 片段继续搬到 `features/agentPage/*`，用截图/交互 smoke 防止回归。
3. 为短期 asset token 增加前端异步签名 URL 获取流程，逐步停止把长效 JWT 放进 query string。
4. 设计正式数据库 migration，把新增 `tracker_task_logs` 和索引同步到既有部署。
