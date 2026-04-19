# 13 认证流程图

## 覆盖模块

- `packages/auth.py`
- `apps/api/main.py`
- `apps/api/routers/auth.py`
- `frontend/src/App.tsx`
- `frontend/src/services/api.ts`
- `tests/test_auth_security.py`

## 图

```mermaid
flowchart TD
  AppBoot[前端 App.tsx 启动] --> Status["authApi.status"]
  Status -->|auth_disabled| MainUI[直接进入主应用]
  Status -->|auth_enabled| Login[显示 LoginPage]

  Login --> LoginApi["/auth/login"]
  LoginApi --> Validate["validate_auth_configuration"]
  Validate --> AuthUser["authenticate_user"]
  AuthUser --> JWT["create_access_token"]
  JWT --> Store[前端保存 bearer token]

  Store --> Req[后续 API 请求]
  Req --> MW[AuthMiddleware]
  MW --> Extract["extract_request_token"]
  Extract --> Header[Authorization Bearer]
  Extract --> Query["query token\n仅 /papers/{id}/pdf\n/papers/{id}/figures/{id}/image\n/global/event"]
  Header --> Decode["decode_access_token"]
  Query --> Decode
  Decode -->|ok| Pass[request.state.user 注入]
  Decode -->|fail| Reject["401 + 前端 clearAuth"]
```

## 阅读提示

- 当前认证的关键不是“有没有登录页”，而是“启动和请求阶段都在做配置与 token 校验”。
- `tests/test_auth_security.py` 是这张图最好的回归锚点。
