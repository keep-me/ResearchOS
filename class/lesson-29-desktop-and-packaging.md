# 第 29 课：桌面化链路与打包方式

## 1. 本课定位

这节课非常重要，因为它要求你区分“历史架构印象”和“当前真实仓库状态”。当前仓库里没有 `src-tauri/`，但仍保留了桌面化相关代码和打包链路。你必须学会基于真实代码而不是历史印象来理解系统。

## 2. 学完你应该能回答的问题

- 当前仓库的桌面化链路主要落在哪些文件。
- 为什么 `frontend/src/lib/tauri.ts` 已经退化为兼容层。
- `apps/desktop/server.py` 和 `researchos-server.spec` 在当前架构中分别负责什么。
- 为什么桌面入口会主动改写 `DATABASE_URL`、`PDF_STORAGE_ROOT` 和 `CORS_ALLOW_ORIGINS`。

## 3. 学习前准备

- 阅读 `apps/desktop/server.py`。
- 阅读 `researchos-server.spec`。
- 阅读 `frontend/src/lib/tauri.ts`。
- 直接对照 `apps/desktop/server.py`、`researchos-server.spec`、`frontend/src/lib/tauri.ts` 三处代码。

## 4. 详细讲解

### 4.1 先面对当前事实

当前仓库里：

- 有 `apps/desktop/server.py`
- 有 `researchos-server.spec`
- 有 `frontend/src/lib/tauri.ts`
- 没有 `src-tauri/`

这意味着项目经历过桌面壳层演进，但当前仓库可见部分更接近：

- 保留桌面后端打包能力。
- 前端保留桌面兼容接口表面。
- 真正的 Tauri Rust 壳层源码当前不在仓库中。

这是学习真实工程时非常典型的情况：历史方案与当前代码不完全一致。

### 4.2 `apps/desktop/server.py` 在做什么

这个文件非常有价值，因为它展示了“桌面后端二进制入口”应做的事：

- 选择空闲端口。
- 解析和建立数据目录。
- 注入环境变量，让配置系统读到正确路径。
- 输出端口信息给外部壳层。
- 启动内嵌 scheduler。
- 拉起 FastAPI 应用。

当前它具体会写入：

- `DATABASE_URL`
- `PDF_STORAGE_ROOT`
- `BRIEF_OUTPUT_ROOT`
- `RESEARCHOS_MINERU_DIR`
- `API_HOST`
- `API_PORT`
- `CORS_ALLOW_ORIGINS`

这说明桌面入口不是简单 `uvicorn.run()` 包装，而是运行环境整形器。

### 4.3 为什么它还要处理 CORS 和 Worker

很多人会忽略两个细节：

- 桌面入口会根据随机端口生成一组本地允许来源。
- 它会在后台线程里启动 `apps.worker.main.run_worker`。

前者是为桌面壳层内嵌前端页面能安全访问 API，后者是为了桌面模式下仍然保留调度和后台处理能力。这两个动作都说明桌面化不是“把网页包一下”，而是要重新组织运行时。

### 4.4 `researchos-server.spec` 说明了什么

PyInstaller spec 文件告诉你：

- 桌面化后端被当成独立二进制打包。
- 需要收集 migrations、依赖库、MCP、winpty 等隐藏导入。
- 打包目标名是 `researchos-server`。

这说明桌面交付并不是“顺手打个包”，而是正式考虑了运行环境、依赖打包和 sidecar 交付。

### 4.5 为什么 `tauri.ts` 看起来像“已退役接口”

`frontend/src/lib/tauri.ts` 里明确写着：

- Desktop shell integration is deprecated。
- 保留兼容接口。
- Web 模式默认直接走后端 API。

这说明当前前端主要是 Web 模式优先，桌面端接口保留为兼容表面。这一事实要比历史印象更可信，因为它来自当前真实代码。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 是理解桌面研究工作台很有价值的现成参考，因为它完整保留了 `src/` 和 `rust/` 两条实现线。它能帮助你补足当前仓库里未完全可见的桌面壳层心智。

## 6. 代码精读顺序

1. `apps/desktop/server.py`
2. `packages/config.py`
3. `researchos-server.spec`
4. `frontend/src/lib/tauri.ts`

## 7. 动手任务

1. 用自己的话写出当前可见桌面化链路。
2. 解释 `desktop/server.py` 为什么要设置数据目录、端口和 CORS。
3. 解释为什么桌面模式下仍然要启动后台 worker。
4. 解释为什么课程不能直接照抄历史 `src-tauri` 路径。

## 8. 验收标准

- 你能基于当前仓库描述桌面化链路。
- 你能区分真实代码、兼容层和历史印象。
- 你能理解桌面打包对后端二进制和运行时环境注入的特殊要求。

## 9. 常见误区

- 误区一：看到历史印象就假定当前架构完全不变。
- 误区二：只看前端 helper，不看打包入口和服务端 sidecar。
- 误区三：因为当前没有 `src-tauri/` 就忽视桌面化痕迹的学习价值。
