"""
ResearchOS API - FastAPI 入口
@author Color2333
"""

from contextlib import asynccontextmanager
import logging
import time
import uuid as _uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from packages.config import get_settings
from packages.domain.exceptions import AppError
from packages.auth import (
    auth_enabled,
    decode_request_token,
    extract_request_token_with_source,
    validate_auth_configuration,
)
from packages.logging_setup import setup_logging
from packages.storage.bootstrap import bootstrap_api_runtime

setup_logging()
logger = logging.getLogger(__name__)

# ---------- 请求日志中间件 ----------


api_logger = logging.getLogger("researchos.api")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """记录每个请求的方法、路径、状态码、耗时"""

    async def dispatch(self, request: Request, call_next):
        req_id = _uuid.uuid4().hex[:8]
        request.state.request_id = req_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        api_logger.info(
            "[%s] %s %s → %d (%.0fms)",
            req_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        response.headers["X-Request-Id"] = req_id
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件 - 保护所有 API（白名单除外）"""

    # 白名单路径（无需认证）
    WHITELIST = {
        "/health",
        "/auth/login",
        "/auth/status",
    }

    async def dispatch(self, request: Request, call_next):
        # 未配置密码则跳过认证
        if not auth_enabled():
            return await call_next(request)

        # 白名单路径跳过认证
        if request.url.path in self.WHITELIST:
            return await call_next(request)

        # 文档可在生产环境通过 EXPOSE_API_DOCS=false 关闭或改为受认证保护
        if get_settings().expose_api_docs and (
            request.url.path.startswith("/docs") or request.url.path.startswith("/openapi")
        ):
            return await call_next(request)

        token, token_source = extract_request_token_with_source(
            request.headers.get("Authorization"),
            request.query_params.get("token"),
            path=request.url.path,
        )

        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
            )

        payload = decode_request_token(
            token,
            path=request.url.path,
            source=token_source,
        )
        if not payload:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        # 将用户信息存入 request.state
        request.state.user = payload
        return await call_next(request)



settings = get_settings()
async def _startup_runtime() -> None:
    validate_auth_configuration()
    bootstrap_api_runtime()


async def _shutdown_runtime() -> None:
    from packages.agent.runtime.runtime_cleanup import dispose_runtime_state

    await dispose_runtime_state()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup_runtime()
    try:
        yield
    finally:
        await _shutdown_runtime()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url="/docs" if settings.expose_api_docs else None,
    redoc_url="/redoc" if settings.expose_api_docs else None,
    openapi_url="/openapi.json" if settings.expose_api_docs else None,
)
app.add_middleware(RequestLogMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError):
    """统一处理所有业务异常"""
    api_logger.warning("[%s] %s: %s", exc.error_type, exc.__class__.__name__, exc.message)
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


origins = [x.strip() for x in settings.cors_allow_origins.split(",") if x.strip()]
if origins:
    allow_credentials = origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins != ["*"] else ["*"],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------- 注册路由 ----------

from apps.api.routers import (  # noqa: E402
    acp,
    agent,
    agents,
    agent_workspace,
    auth,
    content,
    dashboard,
    global_routes,
    graph,
    jobs,
    mcp,
    opencode,
    papers,
    pipelines,
    projects,
    session_runtime,
    settings as settings_router,
    system,
    topics,
    writing,
)

app.include_router(system.router)
app.include_router(global_routes.router)
app.include_router(papers.router)
app.include_router(topics.router)
app.include_router(graph.router)
app.include_router(acp.router)
app.include_router(agent.router)
app.include_router(agents.router)
app.include_router(agent_workspace.router)
app.include_router(projects.router)
app.include_router(session_runtime.router)
app.include_router(content.router)
app.include_router(dashboard.router)
app.include_router(pipelines.router)
app.include_router(mcp.router)
app.include_router(settings_router.router)
app.include_router(writing.router)
app.include_router(jobs.router)
app.include_router(auth.router)
app.include_router(opencode.router)
