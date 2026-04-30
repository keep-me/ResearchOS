"""
ResearchOS API - FastAPI 入口
"""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import sys
import threading
import time
import uuid as _uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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


class ApiPrefixMiddleware:
    """让生产前端的 /api 前缀兼容现有根路径 API。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            path = scope.get("path") or ""
            if path == "/api" or path.startswith("/api/"):
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                raw_path = scope.get("raw_path")
                if isinstance(raw_path, bytes) and (raw_path == b"/api" or raw_path.startswith(b"/api/")):
                    scope["raw_path"] = raw_path[4:] or b"/"
        await self.app(scope, receive, send)


class FrontendStaticMiddleware:
    """在打包模式下用 FastAPI 进程承载前端静态资源。"""

    def __init__(self, app, dist_dir: str):
        self.app = app
        self.dist_dir = Path(dist_dir).resolve()
        self.index_file = self.dist_dir / "index.html"

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path") or "/"
        if method not in {"GET", "HEAD"} or path.startswith("/api/") or path == "/api":
            await self.app(scope, receive, send)
            return

        candidate = (self.dist_dir / path.lstrip("/")).resolve()
        if candidate.is_file() and candidate.is_relative_to(self.dist_dir):
            await FileResponse(candidate)(scope, receive, send)
            return

        accept = ""
        for name, value in scope.get("headers", []):
            if name.lower() == b"accept":
                accept = value.decode("latin-1")
                break

        if self.index_file.is_file() and ("text/html" in accept or path == "/"):
            await FileResponse(self.index_file)(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_frontend_dist() -> Path | None:
    configured = os.environ.get("RESEARCHOS_FRONTEND_DIST", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        candidates.append(Path(bundle_root) / "frontend" / "dist")
    candidates.append(Path(__file__).resolve().parents[2] / "frontend" / "dist")

    for candidate in candidates:
        if candidate.joinpath("index.html").is_file():
            return candidate
    return None


settings = get_settings()
_embedded_worker_thread: threading.Thread | None = None


def _start_embedded_worker_if_requested() -> None:
    global _embedded_worker_thread

    if not _truthy_env("RESEARCHOS_EMBED_WORKER"):
        return
    if _embedded_worker_thread and _embedded_worker_thread.is_alive():
        return

    from apps.worker.main import run_worker

    _embedded_worker_thread = threading.Thread(
        target=run_worker,
        daemon=True,
        name="scheduler",
    )
    _embedded_worker_thread.start()
    logger.info("Embedded scheduler started after API bootstrap")


async def _startup_runtime() -> None:
    validate_auth_configuration()
    bootstrap_api_runtime()


async def _shutdown_runtime() -> None:
    from packages.agent.runtime.runtime_cleanup import dispose_runtime_state

    await dispose_runtime_state()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup_runtime()
    _start_embedded_worker_if_requested()
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

app.add_middleware(ApiPrefixMiddleware)


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
    research_kg,
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
app.include_router(research_kg.router)
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

if _truthy_env("RESEARCHOS_SERVE_FRONTEND"):
    frontend_dist = _resolve_frontend_dist()
    if frontend_dist is None:
        logger.warning("RESEARCHOS_SERVE_FRONTEND is enabled, but frontend/dist was not found")
    else:
        app.add_middleware(FrontendStaticMiddleware, dist_dir=str(frontend_dist))
        logger.info("Serving frontend from %s", frontend_dist)
