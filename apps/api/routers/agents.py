from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from packages.agent.runtime.cli_agent_service import get_cli_agent_service

router = APIRouter()


class AgentConfigPayload(BaseModel):
    agent_type: str
    label: str | None = None
    enabled: bool = True
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    workspace_server_id: str | None = None
    execution_mode: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentConfigTestPayload(BaseModel):
    prompt: str = "Reply with exactly OK"
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    timeout_sec: int = Field(default=180, ge=10, le=900)


@router.get("/agents/configs")
def list_agent_configs() -> dict[str, list[dict[str, Any]]]:
    return {"items": get_cli_agent_service().list_configs()}


@router.post("/agents/configs")
def save_agent_config(body: AgentConfigPayload) -> dict[str, dict[str, Any]]:
    service = get_cli_agent_service()
    try:
        item = service.upsert_config(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@router.patch("/agents/configs/{config_id}")
def update_agent_config(config_id: str, body: AgentConfigPayload) -> dict[str, dict[str, Any]]:
    service = get_cli_agent_service()
    payload = body.model_dump()
    payload["agent_type"] = config_id
    try:
        item = service.upsert_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@router.delete("/agents/configs/{config_id}")
def delete_agent_config(config_id: str) -> dict[str, str]:
    if not get_cli_agent_service().delete_config(config_id):
        raise HTTPException(status_code=404, detail="智能体配置不存在")
    return {"deleted": config_id}


@router.post("/agents/detect")
def detect_agents() -> dict[str, list[dict[str, Any]]]:
    return {"items": get_cli_agent_service().detect_agents()}


@router.post("/agents/configs/{config_id}/test")
def test_agent_config(config_id: str, body: AgentConfigTestPayload) -> dict[str, dict[str, Any]]:
    service = get_cli_agent_service()
    try:
        item = service.test_config(
            config_id,
            prompt=body.prompt,
            workspace_path=body.workspace_path,
            workspace_server_id=body.workspace_server_id,
            timeout_sec=body.timeout_sec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"item": item}

