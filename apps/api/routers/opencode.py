"""opencode runtime bridge routes."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from packages.agent.runtime.opencode_manager import (
    _ensure_v1_suffix,
    _requires_custom_tls_compat,
    get_opencode_llm_record,
    get_opencode_runtime_manager,
)

router = APIRouter()
_ROOT = Path(__file__).resolve().parents[3]


def _runtime_base_url() -> str:
    state = get_opencode_runtime_manager().snapshot()
    if not state.get("available"):
        raise HTTPException(status_code=404, detail="当前环境未发现 opencode 运行时")
    if state.get("phase") != "ready" or not state.get("url"):
        raise HTTPException(status_code=503, detail="opencode 运行时尚未就绪")
    return str(state["url"]).rstrip("/")


@router.get("/opencode/runtime")
def opencode_runtime_status() -> dict:
    """Get current opencode runtime status."""
    return get_opencode_runtime_manager().snapshot()


@router.post("/opencode/runtime/start")
def opencode_runtime_start(force_restart: bool = False) -> dict:
    """Start or reuse the local opencode runtime."""
    return get_opencode_runtime_manager().start(force_restart=force_restart)


@router.post("/opencode/runtime/stop")
def opencode_runtime_stop() -> dict:
    """Stop the local opencode runtime."""
    return get_opencode_runtime_manager().stop()


@router.get("/mcp/health/researchos")
async def researchos_mcp_health() -> dict:
    """Report whether the built-in ResearchOS MCP server is available."""
    endpoint = _ROOT / "scripts" / "researchos_mcp_server.py"
    try:
        from packages.agent.mcp.researchos_mcp import server
        from packages.agent.mcp.researchos_mcp_registry import filter_public_tool_names

        tools = await server.list_tools()
        tool_names = filter_public_tool_names(
            [getattr(tool, "name", "") for tool in tools if getattr(tool, "name", "")]
        )
        return {
            "ok": endpoint.exists(),
            "name": "researchos",
            "transport": "stdio",
            "endpoint": str(endpoint),
            "tool_count": len(tool_names),
            "tools": tool_names,
            "auth_required": False,
        }
    except Exception as exc:  # pragma: no cover - health fallback
        return {
            "ok": False,
            "name": "researchos",
            "transport": "stdio",
            "endpoint": str(endpoint),
            "tool_count": 0,
            "tools": [],
            "auth_required": False,
            "message": str(exc),
        }


@router.api_route(
    "/opencode/provider/{subpath:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def opencode_provider_proxy(subpath: str, request: Request):
    """Proxy OpenAI-compatible requests for the local opencode sidecar."""
    active = get_opencode_llm_record()
    base_url = _ensure_v1_suffix(getattr(active, "api_base_url", None))
    api_key = getattr(active, "api_key", None)
    if not base_url or not api_key:
        raise HTTPException(status_code=400, detail="当前没有可用的 OpenAI 兼容模型配置")

    target_url = urljoin(base_url.rstrip("/") + "/", subpath.lstrip("/"))
    verify_tls = not _requires_custom_tls_compat(base_url)

    upstream_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in {"host", "content-length", "authorization"}:
            continue
        upstream_headers[key] = value
    upstream_headers["Authorization"] = f"Bearer {api_key}"

    body = await request.body()
    timeout = httpx.Timeout(connect=30.0, read=None, write=120.0, pool=None)

    try:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=verify_tls,
        )
        upstream_request = client.build_request(
            request.method,
            target_url,
            params=request.query_params.multi_items(),
            headers=upstream_headers,
            content=body,
        )
        upstream = await client.send(upstream_request, stream=True)
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() in {"content-type", "cache-control", "x-request-id"}
        }

        async def iter_bytes():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            iter_bytes(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )
    except httpx.HTTPError as exc:
        reason = str(exc).strip() or target_url
        detail = f"模型代理请求失败: {type(exc).__name__}: {reason}"
        raise HTTPException(status_code=502, detail=detail) from exc


@router.api_route(
    "/opencode/api/{subpath:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def opencode_runtime_proxy(subpath: str, request: Request):
    """Proxy requests to the local opencode runtime API."""
    target_url = urljoin(_runtime_base_url().rstrip("/") + "/", subpath.lstrip("/"))

    upstream_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in {"host", "content-length", "authorization"}:
            continue
        upstream_headers[key] = value

    body = await request.body()
    timeout = httpx.Timeout(connect=30.0, read=None, write=120.0, pool=None)

    try:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        )
        upstream_request = client.build_request(
            request.method,
            target_url,
            params=request.query_params.multi_items(),
            headers=upstream_headers,
            content=body,
        )
        upstream = await client.send(upstream_request, stream=True)
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() in {"content-type", "cache-control", "x-request-id", "x-next-cursor"}
        }

        async def iter_bytes():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            iter_bytes(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )
    except httpx.HTTPError as exc:
        reason = str(exc).strip() or target_url
        detail = f"opencode 运行时代理失败: {type(exc).__name__}: {reason}"
        raise HTTPException(status_code=502, detail=detail) from exc

