"""Standalone ACP management routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from packages.agent.runtime.acp_service import get_acp_registry_service

router = APIRouter()


class AcpServerConfigPayload(BaseModel):
    label: str | None = None
    transport: Literal["stdio", "http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    workspace_server_id: str | None = None
    timeout_sec: int = Field(default=60, ge=5, le=900)


class AcpConfigPayload(BaseModel):
    default_server: str | None = None
    servers: dict[str, AcpServerConfigPayload] = Field(default_factory=dict)


class AcpTestPayload(BaseModel):
    prompt: str = "Reply with exactly OK"
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    timeout_sec: int = Field(default=180, ge=10, le=900)


@router.get("/acp/runtime")
def acp_runtime() -> dict[str, Any]:
    return get_acp_registry_service().runtime_snapshot()


@router.get("/acp/servers")
def list_acp_servers() -> dict[str, Any]:
    return {"items": get_acp_registry_service().list_servers()}


@router.get("/acp/config")
def get_acp_config() -> dict[str, Any]:
    return get_acp_registry_service().get_config()


@router.put("/acp/config")
def update_acp_config(payload: AcpConfigPayload) -> dict[str, Any]:
    try:
        return get_acp_registry_service().update_config(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/acp/servers/{name}/connect")
def connect_acp_server(name: str) -> dict[str, Any]:
    try:
        item = get_acp_registry_service().connect_server(name)
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/acp/servers/{name}/disconnect")
def disconnect_acp_server(name: str) -> dict[str, Any]:
    try:
        item = get_acp_registry_service().disconnect_server(name)
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/acp/servers/{name}/test")
def test_acp_server(name: str, payload: AcpTestPayload) -> dict[str, Any]:
    try:
        item = get_acp_registry_service().test_server(
            name,
            prompt=payload.prompt,
            workspace_path=payload.workspace_path,
            workspace_server_id=payload.workspace_server_id,
            timeout_sec=payload.timeout_sec,
        )
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
