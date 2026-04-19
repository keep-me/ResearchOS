# 第 09 课：配置系统与环境变量

## 1. 本课定位

配置系统是很多学习者最容易忽略、但后期最容易踩坑的部分。`ResearchOS` 不只是读取几个 API Key，而是把数据库路径、数据目录、模型策略、调度时间、认证策略、桌面模式兼容都放进了统一配置层。最近这轮修订里，认证安全策略的变化也直接落在这里。

## 2. 学完你应该能回答的问题

- `packages/config.py` 为什么是核心文件之一。
- 环境变量如何影响数据库、数据目录和模型调用。
- 为什么同一个项目既支持仓库内 `data/`，又支持外部自定义数据目录。
- 桌面模式兼容为什么会影响配置设计。
- 为什么 `.env.example` 不是配置事实的最终来源，`Settings` 类才是。

## 3. 学习前准备

- 阅读 `.env.example`。
- 通读 `packages/config.py`。
- 结合 `apps/desktop/server.py` 看环境变量如何在运行前被覆盖和注入。

## 4. 详细讲解

### 4.1 配置层解决的不是“方便改值”，而是“隔离运行环境差异”

在小项目里，配置常常只是少数常量。但在这个项目里，配置层同时解决：

- 开发环境与生产环境差异
- 仓库内数据目录与外部数据目录差异
- Web 运行与桌面化运行差异
- 不同模型提供商差异
- 不同安全策略差异

所以 `config.py` 不是附属文件，而是系统稳定运行的前提。

### 4.2 `get_settings()` 不是纯读取，它还会“准备环境”

这个函数并不是“返回一个配置对象”这么简单。它还做了：

- `lru_cache` 缓存配置实例
- 根据 `RESEARCHOS_DATA_DIR` 改写默认数据库和存储路径
- 确保 `pdf_storage_root` 和 `brief_output_root` 存在
- 确保数据库父目录存在

也就是说，配置层不仅提供值，还在为运行时做目录准备。

### 4.3 当前最关键的路径变量有哪些

结合 `packages/config.py` 和 `apps/desktop/server.py`，你应该重点盯住这些变量：

- `RESEARCHOS_ENV_FILE`
- `RESEARCHOS_DATA_DIR`
- `DATABASE_URL`
- `PDF_STORAGE_ROOT`
- `BRIEF_OUTPUT_ROOT`
- `CORS_ALLOW_ORIGINS`

桌面入口 `apps/desktop/server.py` 会主动注入这些值，让 API、Worker、数据库和文件落盘都指向桌面数据目录，而不是仓库默认路径。

### 4.4 认证配置已经从“可用即可”变成“显式安全”

这轮代码修订里，配置层有两个非常重要的变化：

- 新增 `auth_password_hash`
- `auth_secret_key` 默认值改成空字符串，不再给出可直接上线的宽松默认

这意味着：

- 认证可以用 bcrypt 哈希，而不必依赖明文密码
- 只要启用认证，JWT secret 就必须显式配置

注意一个很容易踩坑的现实：`.env.example` 可能没有完全跟上 `Settings` 的新增字段，所以最终应以 `packages/config.py` 为准。

### 4.5 配置项本身就是能力地图

观察 `Settings` 里的字段，你会看到产品边界：

- 认证：`auth_password`、`auth_password_hash`、`auth_secret_key`
- 模型：`llm_provider`、`llm_model_*`、`image_model`
- 嵌入：`embedding_*`
- 调度：`daily_cron`、`weekly_cron`
- 成本控制：`cost_guard_enabled`、`per_call_budget_usd`、`daily_budget_usd`
- 运行策略：`agent_max_tool_steps`、`agent_retry_max_attempts`
- 集成：`semantic_scholar_api_key`、`openalex_email`
- 时区：`user_timezone`

这意味着配置文件本身也是学习项目功能的入口。

## 5. 参考代码对照

### 5.1 `reference/claw-code-main`

`reference/claw-code-main` 的目录分层提醒你：一旦工作台、本地原生层和助手功能共存，配置就不可能只是几个常量。当前 `ResearchOS` 的 `packages/config.py` 与 `apps/desktop/server.py` 已经体现了这种趋势。

## 6. 代码精读顺序

1. `.env.example`
2. `packages/config.py`
3. `apps/desktop/server.py`
4. 对照 `packages/auth.py` 看配置如何影响认证策略

## 7. 动手任务

1. 列出你认为最关键的 15 个配置项。
2. 给这些配置项分组：存储、模型、认证、调度、成本、网络。
3. 解释 `RESEARCHOS_DATA_DIR` 会连带影响哪些默认路径。
4. 解释为什么 `.env.example` 只能当参考，`Settings` 才是最终解释器。

## 8. 验收标准

- 你能说明配置层在这个项目里的系统性价值。
- 你能追踪几个关键路径值是如何被计算出来的。
- 你能通过配置字段大致判断产品能力版图。
- 你能指出认证安全策略为什么必须从配置层开始收口。

## 9. 常见误区

- 误区一：只把配置理解成 API Key 列表。
- 误区二：忽视路径与目录相关配置。
- 误区三：看到默认值就以为线上和桌面模式都会完全一样。
