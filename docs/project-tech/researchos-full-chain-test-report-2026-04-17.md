# ResearchOS 全功能全链路测试报告

测试日期：2026-04-17
测试地点与时区：Asia/Shanghai
测试对象：ResearchOS 本地开发工作区 `D:\Desktop\ResearchOS`
测试方法：黑盒功能测试、白盒结构回归测试、前后端全链路烟测、构建验证、缺陷修正后回归

## 1. 测试结论

本轮测试覆盖 ResearchOS 的论文收集与论文库、研究助手、项目工作区、项目工作流、文献上下文注入、外部/候选论文补充、任务后台、图谱洞察、设置页、工作区文件/Git/终端 API、ARIS 项目工作流兼容链路以及前端生产构建。

结论：核心功能与全链路测试均通过。后端全量 `pytest`、ARIS 烟测、前端生产构建、前端 Playwright 端到端烟测、本次论文/项目工作流回归测试、真实 SSH/ACP 远程链路补测均通过。

本轮发现的问题主要集中在测试环境和烟测断言：未启动 API/Vite 时前端端到端测试会连接失败；空论文库状态下图谱页文案与历史断言不一致；终端/Git 用例依赖执行权限策略；项目页“新建”按钮选择器不够精确。上述问题已修正并完成回归。

## 2. 测试环境

| 项目 | 配置 |
| --- | --- |
| 操作系统 | Windows，本地工作区路径 `D:\Desktop\ResearchOS` |
| Shell | PowerShell 7.5.5 |
| Python | 3.12.7 |
| Node.js | v22.16.0 |
| npm | 10.9.2 |
| Playwright | 1.58.2 |
| 后端服务 | `python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000` |
| 前端服务 | Vite，`http://127.0.0.1:3002`，代理到 `http://127.0.0.1:8000` |
| E2E 数据隔离 | `tmp/full-chain-smoke-final/data` |
| 认证设置 | E2E 烟测使用隔离环境并清空 `AUTH_PASSWORD`、`AUTH_PASSWORD_HASH`、`AUTH_SECRET_KEY`，避免污染用户真实配置 |

## 3. 测试范围

### 3.1 黑盒测试范围

黑盒测试从用户可见行为出发，验证页面、按钮、路由、API 返回和完整交互结果。

| 编号 | 功能域 | 测试点 | 结果 |
| --- | --- | --- | --- |
| B-01 | 主路由导航 | `/assistant`、`/workbench`、`/collect`、`/papers`、`/projects`、`/graph`、`/wiki`、`/brief`、`/tasks`、`/settings`、`/writing` 均可渲染，无前端运行时错误和 API 失败 | 通过 |
| B-02 | 设置中心 | 模型与嵌入、MCP 服务、工作区与 SSH 服务器、Skills 配置切换 | 通过 |
| B-03 | LLM 配置 | 新建测试 LLM provider，编辑时切换 OpenAI-compatible preset，校验 base URL 和模型字段 | 通过 |
| B-04 | 图谱洞察 | 图谱页、领域洞察、空论文库占位态、全局概览、旧 `/operations` 重定向 | 通过 |
| B-05 | 移动端可用性 | 移动端设置页存在可滚动容器，避免内容被遮挡 | 通过 |
| B-06 | 研究助手工作区 | 助手页展示模型、推理、权限、模式、目标、MCP；可展开工作区侧栏；可显示文件、Git、终端 | 通过 |
| B-07 | 研究助手会话 | 活跃 conversation 与 URL 同步；新对话按钮创建路由化会话 shell；刷新后状态保留 | 通过 |
| B-08 | 助手模式传递 | build/plan、reasoning level、backend id 被正确传给 session create 和 prompt request | 通过 |
| B-09 | 助手问答卡片 | 结构化 answer 通过 session permission API 提交，UI 更新结果 | 通过 |
| B-10 | ACP 设置 | 设置页展示 ACP registry；mock ACP server 可连接并执行测试命令 | 通过 |
| B-11 | 论文与任务 | 论文页网格/列表切换、任务后台、pipelines 到 tasks 的兼容重定向、论文详情基本交互 | 通过 |
| B-12 | 项目工作区 | UI 新建项目、展示项目工作台、目标/运行/论文区块、项目助手入口 | 通过 |
| B-13 | 工作区 API | 本地 workspace 文件写入/读取、Git init、commit、diff、branch、upload、terminal run、SSH server 配置和 probe | 通过 |
| B-14 | 真实 SSH/远程 ACP | 接入 Bitahub SSH 工作区后，验证远程 workspace、远程绑定、ACP confirm/reject/abort 流程 | 通过 |

### 3.2 白盒测试范围

白盒测试从代码结构、仓储层、runner、权限、上下文构建、数据持久化和边界条件出发，验证内部实现契约。

| 编号 | 模块 | 测试点 | 结果 |
| --- | --- | --- | --- |
| W-01 | 后端全量测试 | `tests/` 下 89 个文件，其中 83 个 pytest 测试模块实际收集到 711 个测试用例并运行 | 通过 |
| W-02 | Project router | 项目创建、项目论文、workflow run、candidate/import 等路由流 | 通过 |
| W-03 | Project workflow runner | 文献综述阶段聚合项目论文、workspace PDF 匹配、候选外部论文持久化 | 通过 |
| W-04 | 论文上下文注入 | 工作流 prompt 只注入论文元信息和 id，不把论文全文或完整摘要直接拼入 prompt | 通过 |
| W-05 | Mounted paper context | 挂载论文上下文按需读取，避免启动时批量塞入大文本 | 通过 |
| W-06 | ARIS feature matrix | 项目工作流目录、阶段 runner、actions、报告格式、checkpoint/resume 等兼容矩阵 | 通过 |
| W-07 | Agent permission | ACP permission pause/resume、命令权限策略、session permission API | 通过 |
| W-08 | Workspace executor | 本地/远程 workspace tool exposure、文件相对路径越界、防覆盖、Git/terminal 操作契约 | 通过 |
| W-09 | 存储与迁移 | SQLite/Alembic 启动迁移、repository 写入与读取、任务状态持久化 | 通过 |
| W-10 | 前端 smoke harness | 客户端 pageerror、requestfailed、API 4xx/5xx 监控 | 通过 |

## 4. 执行记录

| 测试项 | 命令 | 结果 | 耗时 |
| --- | --- | --- | --- |
| 测试收集 | `python -m pytest --collect-only -q` | 收集 711 个测试 | 未单独计时 |
| 后端全量测试 | `python -m pytest -q` | 711 个测试全部通过 | 178.06s |
| ARIS 烟测 | `npm run smoke:aris` | 通过 | 107.25s |
| 前端生产构建 | `npm run build` | 构建成功，Vite 内部耗时 21.89s | 23.77s |
| 前端端到端烟测 | `npx playwright test -c playwright.config.ts`，并接入 Bitahub SSH 工作区补测远程依赖链路 | 19 个 Playwright 用例全部通过 | 114.74s + 36.83s + 20.52s |
| 失败用例定点回归 | `npx playwright test -c playwright.config.ts --grep 'assistant page exposes\|restored workspace api'` | 2 passed | 21.76s |
| 远程 SSH/ACP 补测 | `npx playwright test -c playwright.config.ts --grep 'assistant custom ACP\|assistant shell can bind'` | 3 passed | 36.83s |
| 远程 ACP abort 补测 | `npx playwright test -c playwright.config.ts --grep 'external abort'` | 1 passed | 20.52s |
| 本次论文/工作流定点回归 | `python -m pytest tests/test_projects_router_flows.py tests/test_project_workflow_runner.py::test_literature_review_prompt_includes_library_and_workspace_pdf_matches tests/test_mounted_paper_context.py -q` | 26 passed | 10.06s |

前端端到端烟测明细：19 个 Playwright 用例全部通过，覆盖本地核心 UI、项目工作区、研究助手、workspace 文件/Git/终端、设置页、论文/任务入口，以及接入 Bitahub SSH 工作区后的真实远程 SSH/ACP 链路。

## 5. 缺陷与修正记录

| 编号 | 现象 | 原因分析 | 修正方式 | 回归结果 |
| --- | --- | --- | --- | --- |
| F-01 | 直接运行前端 smoke 时 19 个用例连接失败 | 端到端测试需要同时启动后端 API 和 Vite，本地没有服务监听 `8000/3002` | 使用隔离数据目录启动 API 和 Vite，再执行 Playwright | 连接问题消失 |
| F-02 | 图谱页“领域洞察”用例在空论文库环境失败 | 历史断言只匹配有论文数据时的“快速探索”文案，空库时实际展示占位态 | 烟测断言改为兼容空论文库占位和有数据探索态 | 通过 |
| F-03 | 助手终端用例出现 `POST /agent/workspace/terminal/session` 400 | 交互式终端必须在命令执行权限为 full 时才能创建，测试没有显式设置权限 | 用例执行前保存当前执行策略，临时切换到 `full_access`，结束后恢复原策略 | 通过 |
| F-04 | 项目页点击“新建”时 locator 匹配两个按钮 | 页面同时存在“新建”和“新建项目”，旧选择器不够精确 | 改为在项目列表面板内查找 `{ name: "新建", exact: true }` | 通过 |
| F-05 | workspace Git 初始化链路在默认权限策略下失败 | `git init`、commit 组合命令属于命令执行能力，不能假设本机策略为 full | workspace API smoke 执行前临时切到 `full_access`，结束后恢复原策略 | 通过 |
| F-06 | 项目工作流论文上下文存在 prompt 膨胀风险 | 工作流不应在启动 prompt 中拼入论文全文或完整摘要 | 回归测试锁定“只注入论文元信息和 id，按需读取分析内容”的行为 | 通过 |

## 6. 关键链路验证

### 6.1 论文与项目工作流链路

已验证项目工作流不再依赖“前 8 篇显式论文”的固定注入策略。工作流入口只传入论文元信息、论文 id、标题、arXiv id、是否有摘要/分析资产等轻量上下文。粗读、精读、三轮分析、PDF、向量等重内容应通过工具和上下文挂载机制按需读取。

外部论文候选链路已纳入测试：工作流可从外部检索或从论文库匹配补充候选论文，并在项目运行元数据中持久化，前端可展示候选并允许用户选择是否导入论文库。当前自动化主要验证候选产生、持久化、API 返回和前端工作台基础展示；真实外部检索源的网络质量和返回内容没有在本轮做 live 依赖测试。

### 6.2 研究助手与工作区链路

已验证研究助手从会话、模式选择、workspace 绑定、workspace 侧栏、文件浏览、Git、终端、ACP 设置到权限确认的本地链路。交互式终端被权限策略保护，只有命令执行权限为 full 时可创建。测试用例已避免污染用户当前权限配置：执行前读取原策略，执行后恢复。

### 6.3 项目工作区链路

已验证用户可以从项目工作区页面创建项目，并看到项目工作台、目标区、运行区、论文区和项目助手入口。项目工作区作为项目级工作流入口更符合功能聚合逻辑；研究助手仍适合作为对话式入口，但项目相关运行、候选论文和论文资产状态应优先在项目工作台集中呈现。

### 6.4 工作区文件/Git/终端链路

已验证 workspace API 的本地文件写入、读取、Git 初始化、提交、diff、branch、上传和 terminal run。测试同时覆盖远程服务器配置和 probe 的失败返回，确认不可达 SSH 目标会给出失败消息而不是导致前端崩溃。

## 7. 技术指标

| 指标 | 结论 | 证据与说明 |
| --- | --- | --- |
| 运行速度 | 达到本地开发验收要求 | 后端 711 个测试 178.06s，平均约 0.25s/用例；前端完整烟测 114.74s；前端生产构建 23.77s；ARIS 烟测 107.25s |
| 安全性 | 基础权限边界通过，仍建议补充专项安全审计 | 终端创建需要 full command 权限；ACP permission pause/resume 有回归；文件相对路径越界由 workspace file resolver 限制；E2E 使用隔离 auth 关闭环境，不代表生产鉴权专项测试 |
| 扩展性 | 模块化扩展能力良好 | 项目工作流通过 workflow catalog/runner/action 分层；论文上下文改为 id + 元信息 + 按需读取，降低 prompt 膨胀；外部论文候选与论文库导入解耦 |
| 部署方便性 | 本地部署路径可复现 | 后端 uvicorn、前端 Vite、生产 build 均可运行；E2E 只需要设置 `RESEARCHOS_DATA_DIR`、`RESEARCHOS_ENV_FILE`、`VITE_PROXY_TARGET` 等环境变量 |
| 可用性 | 核心 UI 可用 | 主要页面均可渲染；项目创建、助手工作区、设置页、论文/任务入口均通过浏览器级测试；移动端设置页滚动可用 |
| 可维护性 | 自动化守门能力较强 | 83 个 pytest 测试模块、711 个后端测试、19 个前端 smoke 用例、ARIS feature matrix 和项目工作流定点回归可持续防止回归 |

## 8. 风险与限制

| 风险项 | 当前状态 | 建议 |
| --- | --- | --- |
| 真实外部论文检索 | 本轮主要使用 mock/fixture 和本地候选验证，没有依赖真实 arXiv/OpenAlex/Semantic Scholar 网络返回 | 增加可选 live smoke，低频运行，并记录第三方服务超时/限流 |
| 真实 SSH/远程 ACP | 已使用 Bitahub SSH 工作区完成补测；该能力仍依赖外部服务器可用性 | 在 CI 或验收机配置一台稳定 SSH workspace，纳入 nightly |
| 生产鉴权 | E2E 为隔离测试关闭 auth，避免污染本机配置 | 增加一组开启 auth 的浏览器测试，覆盖登录、token 过期、未授权 API |
| 压力与负载 | 本轮是功能与链路测试，不是并发压测 | 对论文导入、向量化、项目 workflow queue、agent session stream 增加负载测试 |
| 工作区根目录授权 | 本轮主要验证相对路径越界和执行权限；根目录白名单策略需要专项审计 | 补充 workspace root allowlist 的白盒测试与前端错误提示测试 |

## 9. 后续改进建议

1. 给 Playwright 配置 `webServer` 或新增 `scripts/run-frontend-smoke.ps1`，自动启动 API/Vite，避免手工忘记服务导致连接失败。
2. 将 `python -m pytest -q`、`npm run smoke:aris`、`npm run build`、`npx playwright test -c playwright.config.ts` 组合成 CI 验收流水线。
3. 增加开启生产鉴权的端到端用例，覆盖登录态、无 token、过期 token 和权限不足。
4. 为外部论文检索增加可选 live 测试，并将第三方 API 不稳定与产品缺陷区分记录。
5. 对项目工作流引入性能基线：论文数量、候选论文数量、workspace 文件数量、prompt token 规模和运行耗时都应进入指标面板。

## 10. 验收结论

截至 2026-04-17，本项目在本地可控环境与真实 Bitahub SSH 工作区组合下完成全功能、全链路测试。后端、前端构建、前端端到端、本次论文/项目工作流改造回归、远程 SSH/ACP 补测均通过。当前版本可以进入下一阶段验收；仍建议在真实外部论文源和生产鉴权配置环境中补充专项验收，以覆盖第三方检索稳定性和生产安全性。
