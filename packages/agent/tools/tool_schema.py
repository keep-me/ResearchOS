from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    permission: str | None = None
    managed_permission: bool = False
    default_local_enabled: bool = False
    default_remote_enabled: bool = False
    allow_user_enable: bool = True
    allow_in_read_only: bool = True
    local_only: bool = False


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    requires_confirm: bool = False
    spec: ToolSpec = field(default_factory=ToolSpec)
    handler: str | None = None
    provider_tools: list[dict] = field(default_factory=list)
