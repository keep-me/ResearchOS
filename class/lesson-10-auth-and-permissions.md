# 第 10 课：认证与权限

## 1. 本课定位

这一课讲的是“谁可以访问系统”和“系统如何识别你”。对研究平台来说，认证不仅是登录页是否能输密码，还会影响 API、文件访问、前端状态管理以及后续 Agent 操作的安全边界。最近这轮代码修订里，这一层明显收紧了。

## 2. 学完你应该能回答的问题

- `ResearchOS` 的认证入口和状态检查接口是什么。
- JWT 是如何生成、校验和传播的。
- 为什么现在开启认证时必须显式配置 secret。
- 为什么 PDF 或资源访问会支持 query token，但不是所有接口都支持。
- 前端如何在 401 时清理状态并在认证关闭时直接进入主应用。

## 3. 学习前准备

- 阅读 `apps/api/routers/auth.py`。
- 阅读 `packages/auth.py`。
- 阅读 `apps/api/main.py` 中 `AuthMiddleware`。
- 阅读 `frontend/src/App.tsx`、`frontend/src/pages/Login.tsx` 和 `frontend/src/services/api.ts` 中与认证相关的逻辑。

## 4. 详细讲解

### 4.1 认证开关本身就是一种产品策略

从 `packages/auth.py` 可以看出，现在认证是否开启不是单纯看明文密码，而是：

- `AUTH_PASSWORD` 非空时开启
- 或 `AUTH_PASSWORD_HASH` 非空时开启

这意味着产品支持两种运行方式：

- 个人本地研究环境，不一定强制认证
- 有部署需求时，开启站点密码和 JWT 保护

但和早期相比，当前代码已经不再接受“随便给个默认 secret 也能跑”的宽松状态。

### 4.2 `validate_auth_configuration()` 是这轮改动的关键

当前认证链路里最重要的新约束是 `validate_auth_configuration()`：

- 认证关闭时直接返回
- 认证开启时必须先通过 `require_auth_secret()`
- 非 `dev` 环境必须提供 bcrypt 哈希
- `dev` 环境仍允许明文密码，但会告警

而且它会在两个位置被调用：

- API 启动阶段 `apps/api/main.py`
- 登录接口 `apps/api/routers/auth.py`

这说明认证配置错误现在会被更早暴露，不会等到线上请求来时才发现。

### 4.3 登录接口的职责仍然克制，但更安全了

`/auth/login` 的流程很简单但完整：

1. 先判断认证是否开启
2. 再执行 `validate_auth_configuration()`
3. 调用 `authenticate_user()`
4. 成功后用 `create_access_token()` 生成 JWT

`authenticate_user()` 现在优先走 bcrypt 哈希；只有开发环境且没有配置 hash 时，才回退到明文比较。这是这轮认证加固最实际的一部分。

### 4.4 query token 不是通配白名单

`extract_request_token()` 现在只会在两种情况下接受 query token：

- 调用方显式传入 `allow_query_token=True`
- 或请求路径命中允许列表

当前允许列表只有三类路径：

- `/papers/{id}/pdf`
- `/papers/{paper_id}/figures/{figure_id}/image`
- `/global/event`

这很重要，因为它说明 query token 只是为浏览器资源流和事件流让路，不是把所有 GET 接口都放开。`tests/test_auth_security.py` 还专门验证了 `/papers/latest` 这种普通 API 不应接受 query token。

### 4.5 前端认证态管理是“启动协商 + 请求期清理”

`frontend/src/App.tsx` 和 `frontend/src/services/api.ts` 共同体现了前端认证态管理：

- `authApi.status()` 用于判断后端是否启用认证
- 后端如果关闭认证，前端直接进入主应用
- 启动阶段如果后端还没起来，`App.tsx` 会循环重试并显示等待屏
- 请求遇到 401 时会统一清理本地 token
- 登录成功后才进入主路由树

所以前端并不是“盲信本地 token”，而是先和后端协商认证模式，再进入页面。

## 5. 参考代码对照

### 5.1 `reference/claw-code-main`

`reference/claw-code-main` 更偏本地工作台形态，但同样会面对壳层、助手和本地能力之间的边界问题。对照它能帮助你意识到：今天这里讲的是站点级认证，后面工作区、工具和执行层还会继续引入更细的权限问题。

## 6. 代码精读顺序

1. `apps/api/routers/auth.py`
2. `packages/auth.py`
3. `apps/api/main.py` 中 `AuthMiddleware`
4. `frontend/src/App.tsx`
5. `frontend/src/services/api.ts`
6. `tests/test_auth_security.py`

## 7. 动手任务

1. 画出登录流程图。
2. 画出受保护请求的认证链路图。
3. 解释为什么前端既需要 `auth/status`，又需要本地 token 状态。
4. 列出 query token 允许的三类路径，并说明为什么不能把普通 API 也放进去。

## 8. 验收标准

- 你能完整复述认证链路。
- 你能解释站点级认证和业务级权限不是一回事。
- 你能说明前后端分别在哪一层处理认证状态。
- 你能说清楚这轮安全加固具体收紧了什么。

## 9. 常见误区

- 误区一：把认证只理解成登录页。
- 误区二：忽视资源访问场景和 header token 的局限。
- 误区三：以为 JWT 一加上去，权限问题就都解决了。
