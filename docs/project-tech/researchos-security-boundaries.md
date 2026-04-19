# ResearchOS 安全边界方案

日期：2026-04-15

## Workspace / Server 权限域

远程执行权限按 `workspace_server_id + workspace_path` 组成权限域。任何会写文件、启动命令、上传文件、释放 GPU lease 的操作，都必须在调用层保留这两个字段，并在审计 metadata 中写入：

- `workspace_server_id`
- `workspace_path`
- `permission_domain`
- `operation`
- `requested_by`

本轮代码已保留远程 run 的 workspace/server 元数据，并修复 retry 时 remote session name 复用旧 run 的问题。后续正式执行层应把 permission domain 作为硬校验，而不只是审计字段。

## 短期 Asset Token

长效 JWT 不应长期放在 query string。过渡方案：

- 后端提供 `create_asset_access_token(path)`，只允许 `/papers/{id}/pdf`、figure image、global event 等白名单路径。
- token 默认 5 分钟有效。
- token payload 绑定 `path`，跨路径重放会失败。
- 中间件先验普通 Bearer token，再验 asset token，兼容旧调用。

下一步：前端 `resolveApiAssetUrl` 从同步拼接长效 token，改为异步请求短期签名 URL 并缓存到过期时间前。

## Secret Store 迁移方案

短期：

- `.env` 继续作为开发配置。
- 生产必须显式配置 `AUTH_SECRET_KEY` 和哈希密码。
- LLM/SMTP/SSH secret 不进入普通 API response。

中期：

- 新增本地 secret store 表，只存 key alias 和密文。
- 使用 OS keyring、DPAPI 或部署环境 KMS 加密。
- 业务表只保存 secret alias。

长期：

- 支持 workspace/server 级 secret scope。
- Agent 工具调用只能拿到运行时注入的临时凭证，不能读取原始 secret。

