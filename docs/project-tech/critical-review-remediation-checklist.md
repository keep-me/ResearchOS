# ResearchOS 锐评整改清单

更新时间：2026-04-14
执行分支：`codex/critical-review-remediation`

## 背景

本轮整改基于两部分输入：

1. 现有锐评中列出的 P0 / P1 / P2 问题。
2. 对当前仓库的二次核查，确认问题是否属实，并补充遗漏项。

当前核查结论：

- 现有 P0 / P1 基本都成立。
- 最严重的根因不是单个 bug，而是“同一职责有多份实现”：
  - 数据库 schema 同时由 Alembic、`Base.metadata.create_all()`、运行时手写 DDL 维护。
  - API 启动逻辑在 [apps/api/main.py](/D:/Desktop/ResearchOS/apps/api/main.py) 和 [apps/api/routers/__init__.py](/D:/Desktop/ResearchOS/apps/api/routers/__init__.py) 各复制了一份。
  - 桌面端和服务端各自维护数据库默认路径残留逻辑。
- Alembic 迁移链本身并不完整，当前模型里的多张表/多组列根本没有进入迁移历史；这也是运行时 DDL 被塞回启动流程的直接原因。

## 本轮目标

本轮不是做“大重写”，而是把高风险点先降到可维护状态：

- 让 schema 回到单轨治理。
- 让认证和执行权限默认不再等价于“站点密码换远程执行”。
- 让上传 / 替换 PDF 不再在 async 路由里直接阻塞事件循环。
- 修掉用户可见 bug 和明显逻辑残渣。
- 顺手拆掉几处已经产生漂移的重复实现。

## 详细开发清单

### A. Schema 治理

- [x] A1. 补一版 Alembic reconciliation migration，覆盖当前模型与迁移链之间缺失的表、列和索引。
  验收标准：
  - 空数据库执行 `alembic upgrade head` 后，核心表结构可支撑当前代码启动。
  - 不再依赖启动时逐条 `ALTER TABLE` / `CREATE TABLE IF NOT EXISTS` 补洞。

- [x] A2. 把运行时 schema bootstrap 改成 Alembic 单轨。
  验收标准：
  - 启动时不再走手写 DDL。
  - 空数据库走 Alembic upgrade。
  - 老库在无 `alembic_version` 的情况下有明确的兼容升级路径和日志。

- [x] A3. 将数据补齐逻辑与 schema 迁移逻辑分离。
  验收标准：
  - `initial_import` 这类数据初始化不再和 DDL 混在一个函数里。
  - 失败时有日志，不静默吞异常。

- [x] A4. 为 bootstrap / 迁移兼容路径补测试。
  验收标准：
  - 覆盖空库初始化。
  - 覆盖 legacy 库补 stamp / reconcile 的路径。

### B. 安全模型

- [x] B1. 修正认证配置模型，禁止“固定默认 JWT 密钥 + 开启认证”的不安全组合。
  验收标准：
  - 开启认证时，必须使用显式配置的密钥。
  - 默认常量密钥不再可直接用于受保护部署。

- [x] B2. 支持哈希密码校验，并限制明文密码只作为兼容路径存在。
  验收标准：
  - 支持 bcrypt 哈希配置。
  - 非开发环境下不再接受不安全认证配置。

- [x] B3. 收紧 query-token 使用范围。
  验收标准：
  - 常规 API 请求只接受 `Authorization: Bearer ...`。
  - query token 仅保留给确实无法加 header 的资产/流式通道。

- [x] B4. 收紧默认助手执行权限。
  验收标准：
  - 默认策略不再是 `command_execution=full + approval_mode=off`。
  - 权限推导逻辑与默认策略保持一致。

- [x] B5. 修正 CORS `*` 与 `allow_credentials=True` 的不合规组合。
  验收标准：
  - wildcard origin 时不再开启 credentials。

- [x] B6. 为认证 / 中间件 / 权限策略补测试。
  验收标准：
  - 覆盖安全配置校验。
  - 覆盖 query token 限制。
  - 覆盖默认权限策略。

### C. API 阻塞与 Router 过胖

- [x] C1. 把 PDF 上传 / 替换的核心逻辑下沉到服务层。
  验收标准：
  - [apps/api/routers/papers.py](/D:/Desktop/ResearchOS/apps/api/routers/papers.py) 不再直接承担整段文件存储和元数据写回逻辑。

- [x] C2. 让 async 上传接口不再直接做阻塞文件 I/O 和同步数据库操作。
  验收标准：
  - 文件落盘、PDF 元数据提取、同步 DB 写入均移出事件循环主线程。

- [x] C3. 修复用户可见乱码错误文案。
  验收标准：
  - API 返回给用户的错误信息不再出现 `????`。

### D. 正确性与性能

- [x] D1. 修复 `AgentMessage.created_at` 重复定义。
  验收标准：
  - ORM 字段定义单一且语义清晰。

- [x] D2. 修复 `TTLCache.get()` 不清理过期 key。
  验收标准：
  - 读取过期值时会删除缓存项。

- [x] D3. 修复数据库默认路径的 legacy/current 残留逻辑。
  验收标准：
  - 不再存在两个变量指向同一文件的伪兼容逻辑。

- [x] D4. 修复半小时 / 45 分钟时区统计错误。
  验收标准：
  - 按日期聚合不再依赖 `+.0f hours` 四舍五入。

- [x] D5. 改善语义检索候选召回逻辑。
  验收标准：
  - 不再只扫最近 500 条 embedding 再排序。
  - 结果优先级不再被“最近写入时间”硬截断。

- [x] D6. 为上述修复补测试。
  验收标准：
  - 覆盖 TTL 过期清理。
  - 覆盖非整点时区统计。
  - 覆盖 embedding 召回不被最近 500 条截断。

### E. 模块治理与打包

- [x] E1. 把 `setuptools` 从手工包名切到包发现。
  验收标准：
  - `packages/agent/...`、`packages/ai/...` 等子包不会因为漏写清单而在 wheel 安装时缺失。

- [x] E2. 移除 API 启动逻辑的重复副本。
  验收标准：
  - [apps/api/routers/__init__.py](/D:/Desktop/ResearchOS/apps/api/routers/__init__.py) 不再复制 FastAPI app bootstrap。

### F. 验证与提交

- [x] F1. 跑针对性测试并记录结果。
- [x] F2. 创建隔离整改分支。
- [x] F3. 提交 Git 变更。

## 本轮额外补充的锐评点

- [x] X1. Alembic 迁移链与当前模型面严重漂移，这是当前 schema 风险的根因，不修这个，其它 DB 修补都是临时止痛。
- [x] X2. [apps/api/routers/__init__.py](/D:/Desktop/ResearchOS/apps/api/routers/__init__.py) 复制整套 app 入口，已经和 [apps/api/main.py](/D:/Desktop/ResearchOS/apps/api/main.py) 出现变量名漂移。
- [x] X3. 桌面端与服务端的数据库路径选择逻辑重复且残留“伪 legacy 分支”。

## 本轮验证

- [x] `python -m pytest tests/test_storage_bootstrap.py tests/test_auth_security.py tests/test_runtime_safety_regressions.py tests/test_app_startup.py tests/test_topics_cache_invalidation.py -q`
  结果：`13 passed`
- [x] Git 提交：`e0f7c5080` (`fix: harden bootstrap auth and paper workflows`)
