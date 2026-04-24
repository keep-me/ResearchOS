"""Global event and disposal routes aligned with OpenCode's global surface."""

from __future__ import annotations

import asyncio
import json
import logging
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse

from packages.agent import global_bus
from packages.agent.runtime.runtime_cleanup import dispose_runtime_state
from packages.auth import auth_enabled, decode_request_token, extract_request_token_with_source

router = APIRouter()
_ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}


def _make_sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _app_version() -> str:
    pyproject = _ROOT / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "0.0.0"
    project = payload.get("project")
    if not isinstance(project, dict):
        return "0.0.0"
    version = str(project.get("version") or "").strip()
    return version or "0.0.0"


@router.get("/global/health")
def global_health() -> dict:
    return {
        "healthy": True,
        "version": _app_version(),
    }


@router.get("/global/event")
async def global_event(request: Request):
    return StreamingResponse(
        _iter_global_event_stream(request.is_disconnected),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def _authenticate_global_websocket(websocket: WebSocket) -> dict | None:
    if not auth_enabled():
        return None

    token, token_source = extract_request_token_with_source(
        websocket.headers.get("authorization"),
        websocket.query_params.get("token"),
        allow_query_token=True,
    )
    if not token:
        raise PermissionError("未认证，全局事件连接被拒绝")

    payload = decode_request_token(token, path="/global/ws", source=token_source)
    if not payload:
        raise PermissionError("全局事件令牌无效或已过期")
    return payload


@router.websocket("/global/ws")
async def global_event_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        _authenticate_global_websocket(websocket)
        await _forward_global_events_websocket(websocket)
    except WebSocketDisconnect:
        return
    except PermissionError as exc:
        detail = str(exc).strip() or "全局事件连接认证失败"
        try:
            await websocket.send_json({"type": "error", "message": detail})
        except Exception:
            pass
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=detail[:120])
    except Exception:
        logger.exception("global websocket stream failed")
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="global websocket stream failed")
        except Exception:
            pass


@router.post("/global/dispose")
async def global_dispose() -> bool:
    await dispose_runtime_state()
    global_bus.publish_event(
        "global",
        {
            "type": "global.disposed",
            "properties": {},
        },
    )
    return True


async def _iter_global_event_stream(
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    heartbeat_interval: float = 10.0,
):
    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _handler(event) -> None:  # noqa: ANN001
        payload = {
            "directory": event.directory,
            "payload": event.payload,
        }
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    unsubscribe = global_bus.subscribe_all(_handler)
    try:
        yield _make_sse_data(
            {
                "payload": {
                    "type": "server.connected",
                    "properties": {},
                }
            }
        )
        while True:
            if await is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield _make_sse_data(
                    {
                        "payload": {
                            "type": "server.heartbeat",
                            "properties": {},
                        }
                    }
                )
                continue
            yield _make_sse_data(payload)
    finally:
        unsubscribe()


async def _forward_global_events_websocket(
    websocket: WebSocket,
    *,
    heartbeat_interval: float = 10.0,
) -> None:
    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _handler(event) -> None:  # noqa: ANN001
        payload = {
            "directory": event.directory,
            "payload": event.payload,
        }
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    unsubscribe = global_bus.subscribe_all(_handler)
    try:
        await websocket.send_json(
            {
                "payload": {
                    "type": "server.connected",
                    "properties": {},
                }
            }
        )
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "payload": {
                            "type": "server.heartbeat",
                            "properties": {},
                        }
                    }
                )
                continue
            await websocket.send_json(payload)
    finally:
        unsubscribe()
