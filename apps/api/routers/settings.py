"""LLM 配置 / 邮箱配置 / 工作区 / 助手策略路由"""

from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from apps.api.deps import iso_dt
from packages.auth import auth_enabled
from packages.config import get_settings
from packages.domain.schemas import LLMProviderCreate, LLMProviderUpdate
from packages.integrations.llm_provider_schema import (
    infer_protocol_from_base_url,
    normalize_protocol_name,
    resolve_provider_protocol,
)
from packages.storage.db import session_scope
from packages.storage.repositories import EmailConfigRepository, LLMConfigRepository

router = APIRouter()

LLM_PROVIDER_PRESETS = [
    {
        "id": "openai",
        "label": "OpenAI-compatible",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": [
            "gpt-5.4",
            "gpt-4.1",
            "gpt-4o",
            "qwen-plus",
            "kimi-k2-0905-preview",
        ],
        "description": "统一用于 OpenAI 官方以及常见 OpenAI-compatible 网关服务。",
    },
    {
        "id": "gemini",
        "label": "Gemini",
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash-image",
        ],
        "description": "用于 Gemini 官方接口。聊天走 OpenAI-compatible 地址，绘图建议单独配置 Nano Banana 图像通道。",
    },
    {
        "id": "zhipu",
        "label": "智谱 GLM",
        "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "models": [
            "glm-4.7",
            "glm-4.6",
            "glm-4.6v",
            "embedding-3",
        ],
        "description": "用于智谱 BigModel / GLM 系列接口，底层走 OpenAI-compatible 协议。",
    },
    {
        "id": "anthropic",
        "label": "Anthropic-compatible",
        "provider": "anthropic",
        "base_url": "",
        "models": [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ],
        "description": "统一用于 Anthropic / Claude 风格消息接口。",
    },
]


# ---------- Pydantic 模型 ----------


class EmailConfigCreate(BaseModel):
    """创建邮箱配置请求"""

    name: str
    smtp_server: str
    smtp_port: int = 587
    smtp_use_tls: bool = True
    sender_email: str
    sender_name: str = "ResearchOS"
    username: str
    password: str


class EmailConfigUpdate(BaseModel):
    """更新邮箱配置请求"""

    name: str | None = None
    smtp_server: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool | None = None
    sender_email: str | None = None
    sender_name: str | None = None
    username: str | None = None
    password: str | None = None


class WorkspaceRootCreate(BaseModel):
    """新增工作区根目录"""

    path: str
    title: str | None = None


class WorkspaceRootUpdate(BaseModel):
    """重命名工作区根目录"""

    path: str
    title: str


class WorkspaceDefaultRootUpdate(BaseModel):
    """设置默认项目根目录"""

    path: str | None = None


class AssistantExecPolicyUpdate(BaseModel):
    """更新研究助手执行策略"""

    workspace_access: Literal["none", "read", "read_write"] | None = None
    command_execution: Literal["deny", "allowlist", "full"] | None = None
    approval_mode: Literal["always", "on_request", "off"] | None = None
    allowed_command_prefixes: list[str] | None = None


# ---------- 辅助函数 ----------


def _mask_key(key: str) -> str:
    """API Key 脱敏：只显示前4和后4"""
    if len(key) <= 12:
        return key[:2] + "****" + key[-2:]
    return key[:4] + "****" + key[-4:]


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def require_sensitive_settings_access(request: Request) -> None:
    settings = get_settings()
    if not auth_enabled() and settings.app_env == "dev":
        return
    if bool(getattr(settings, "allow_sensitive_settings", False)):
        return
    user = getattr(request.state, "user", {}) or {}
    if str(user.get("role") or "").strip().lower() == "admin":
        return
    raise HTTPException(
        status_code=403,
        detail="Sensitive settings require admin access or ALLOW_SENSITIVE_SETTINGS=true",
    )


def _provider_label(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    labels = {
        "openai": "OpenAI-compatible",
        "anthropic": "Anthropic-compatible",
        "zhipu": "Zhipu",
        "gemini": "Gemini",
        "qwen": "Qwen",
        "kimi": "Kimi",
        "minimax": "MiniMax",
        "custom": "自定义",
    }
    return labels.get(normalized, normalized or "unknown")


def _provider_value_for_ui(
    provider_family: str | None, provider_protocol: str | None
) -> str | None:
    family = (provider_family or "").strip().lower()
    protocol = (provider_protocol or "").strip().lower()
    if family in {"zhipu", "gemini", "qwen", "kimi", "minimax"}:
        return family
    return protocol or family or None


def _normalize_provider_for_ui(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    aliases = {
        "openai-compatible": "custom",
        "openai_compatible": "custom",
        "dashscope": "qwen",
        "aliyun": "qwen",
        "bailian": "qwen",
        "google": "gemini",
        "googleai": "gemini",
        "vertex": "gemini",
        "vertex_ai": "gemini",
        "moonshot": "kimi",
        "moonshotai": "kimi",
        "minimaxai": "minimax",
        "openrouter": "custom",
        "siliconflow": "custom",
        "zhipuai": "zhipu",
        "bigmodel": "zhipu",
        "glm": "zhipu",
    }
    return aliases.get(normalized, normalized)


def _normalize_protocol_for_ui(value: str | None, *, base_url: str | None = None) -> str:
    normalized = normalize_protocol_name(value)
    if normalized:
        return normalized
    return infer_protocol_from_base_url(base_url) or "openai"


def _normalize_image_provider_name(value: str | None) -> str | None:
    normalized = _normalize_provider_for_ui(value)
    return normalized or None


def _infer_provider_from_base_url(base_url: str | None) -> str | None:
    raw = _clean_optional_text(base_url)
    if not raw:
        return None
    host = urlparse(raw).netloc.lower() or raw.lower()
    if "open.bigmodel.cn" in host or "bigmodel.cn" in host or "zhipu" in host:
        return "zhipu"
    if "generativelanguage.googleapis.com" in host or "googleapis.com" in host:
        return "gemini"
    if "dashscope.aliyuncs.com" in host or "aliyuncs.com" in host:
        return "qwen"
    if "moonshot.cn" in host or "moonshot.ai" in host:
        return "kimi"
    if "minimax.io" in host or "minimax.chat" in host:
        return "minimax"
    if "anthropic.com" in host:
        return "anthropic"
    custom_hosts = (
        "dashscope.aliyuncs.com",
        "siliconflow.cn",
        "openrouter.ai",
        "edgefn.net",
        "gmncode.com",
    )
    if any(item in host for item in custom_hosts):
        return "custom"
    if "openai.com" in host:
        return "openai"
    return "custom" if host else None


def _resolve_internal_provider(
    *,
    protocol: str | None,
    base_url: str | None,
    fallback_provider: str | None = None,
) -> str | None:
    normalized_protocol = _normalize_protocol_for_ui(protocol, base_url=base_url)
    inferred_provider = _infer_provider_from_base_url(base_url)
    fallback_family = _normalize_provider_for_ui(fallback_provider)

    if normalized_protocol == "anthropic":
        return "anthropic"
    if inferred_provider == "anthropic":
        return fallback_family if fallback_family not in {"", "anthropic"} else "custom"
    if inferred_provider:
        return inferred_provider
    if fallback_family and _normalize_protocol_for_ui(fallback_family) == normalized_protocol:
        return fallback_family
    return normalized_protocol or None


def _normalize_service_base_url(base_url: str | None) -> str | None:
    raw = _clean_optional_text(base_url)
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    path = parsed.path.rstrip("/")
    endpoint_suffixes = (
        "/chat/completions",
        "/responses",
        "/embeddings",
        "/images/generations",
    )
    for suffix in endpoint_suffixes:
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path:
        path = ""
    return parsed._replace(path=path, params="", query="", fragment="").geturl().rstrip("/")


def _normalize_image_service_base_url(base_url: str | None) -> str | None:
    raw = _clean_optional_text(base_url)
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    path = parsed.path.rstrip("/")
    lowered = path.lower()
    models_index = lowered.find("/models/")
    if models_index >= 0:
        path = path[:models_index]
        lowered = path.lower()
    if lowered.endswith("/openai"):
        path = path[:-7]
    return parsed._replace(path=path, params="", query="", fragment="").geturl().rstrip("/")


def _looks_like_endpoint_url(base_url: str | None) -> bool:
    raw = _clean_optional_text(base_url)
    if not raw:
        return False
    path = (urlparse(raw).path or raw).lower().rstrip("/")
    endpoint_suffixes = (
        "/chat/completions",
        "/responses",
        "/embeddings",
        "/images/generations",
    )
    return any(path.endswith(suffix) for suffix in endpoint_suffixes)


def _normalize_provider_payload(
    *,
    provider: str | None,
    api_base_url: str | None,
    embedding_provider: str | None,
    embedding_api_base_url: str | None,
) -> dict[str, str | None]:
    normalized_protocol = _normalize_protocol_for_ui(provider, base_url=api_base_url)
    normalized_api_base_url = _normalize_service_base_url(api_base_url)
    normalized_provider = _resolve_internal_provider(
        protocol=normalized_protocol,
        base_url=normalized_api_base_url,
        fallback_provider=provider,
    )
    normalized_embedding_api_base_url = _normalize_service_base_url(embedding_api_base_url)
    normalized_embedding_protocol = normalize_protocol_name(embedding_provider)
    normalized_embedding_provider = None
    if (
        normalized_embedding_api_base_url
        or normalized_embedding_protocol
        or _clean_optional_text(embedding_provider)
    ):
        effective_embedding_protocol = (
            normalized_embedding_protocol
            or infer_protocol_from_base_url(normalized_embedding_api_base_url)
            or normalized_protocol
        )
        normalized_embedding_provider = _resolve_internal_provider(
            protocol=effective_embedding_protocol,
            base_url=normalized_embedding_api_base_url,
            fallback_provider=embedding_provider,
        )

    return {
        "provider": normalized_provider or None,
        "provider_protocol": normalized_protocol or None,
        "api_base_url": normalized_api_base_url,
        "embedding_provider": normalized_embedding_provider or None,
        "embedding_provider_protocol": (
            _normalize_protocol_for_ui(
                normalized_embedding_provider or normalized_protocol,
                base_url=normalized_embedding_api_base_url or normalized_api_base_url,
            )
            if normalized_embedding_provider
            or normalized_embedding_api_base_url
            or normalized_embedding_protocol
            else None
        ),
        "embedding_api_base_url": normalized_embedding_api_base_url,
    }


def _analyze_provider_config(cfg) -> dict:
    provider_family = _normalize_provider_for_ui(getattr(cfg, "provider", None))
    provider_protocol = (
        resolve_provider_protocol(
            getattr(cfg, "provider", None),
            getattr(cfg, "api_base_url", None),
        )
        or "openai"
    )
    embedding_provider_family = (
        _normalize_provider_for_ui(getattr(cfg, "embedding_provider", None)) or provider_family
    )
    api_base_url = _clean_optional_text(getattr(cfg, "api_base_url", None))
    embedding_api_base_url = _clean_optional_text(getattr(cfg, "embedding_api_base_url", None))
    inferred_provider = _infer_provider_from_base_url(api_base_url)
    inferred_embedding_provider = _infer_provider_from_base_url(embedding_api_base_url)
    inferred_protocol = infer_protocol_from_base_url(api_base_url)
    inferred_embedding_protocol = infer_protocol_from_base_url(embedding_api_base_url)
    embedding_protocol = (
        resolve_provider_protocol(
            getattr(cfg, "embedding_provider", None),
            embedding_api_base_url,
        )
        or provider_protocol
    )
    warnings: list[str] = []

    if inferred_protocol and provider_protocol and inferred_protocol != provider_protocol:
        warnings.append(
            f"主通道当前选择的是 {_provider_label(provider_protocol)}，但 Base URL 更像 {_provider_label(inferred_protocol)} 接口。"
        )
    elif inferred_provider and provider_family and inferred_provider != provider_family:
        warnings.append(
            f"主通道识别为 {_provider_label(provider_family)}，但 Base URL 更像 {_provider_label(inferred_provider)} 提供方的接口。"
        )

    if api_base_url and "coding.dashscope.aliyuncs.com" in api_base_url.lower():
        warnings.append(
            "当前主通道指向阿里 Coding / DashScope 聊天端点，这类地址通常只适合聊天生成，不建议直接复用为 embedding。"
        )

    if (
        api_base_url
        and "dashscope.aliyuncs.com" in api_base_url.lower()
        and not embedding_api_base_url
    ):
        warnings.append(
            "如果要启用向量检索，建议单独配置 embedding Base URL 与 embedding API Key，不要直接沿用 DashScope 聊天地址。"
        )

    if (
        inferred_embedding_protocol
        and embedding_protocol
        and inferred_embedding_protocol != embedding_protocol
    ):
        warnings.append(
            f"嵌入通道当前选择的是 {_provider_label(embedding_protocol)}，但嵌入 Base URL 更像 {_provider_label(inferred_embedding_protocol)} 接口。"
        )
    elif (
        embedding_api_base_url
        and inferred_embedding_provider
        and inferred_embedding_provider != embedding_provider_family
    ):
        warnings.append(
            f"嵌入通道识别为 {_provider_label(embedding_provider_family)}，但嵌入 Base URL 更像 {_provider_label(inferred_embedding_provider)} 提供方的接口。"
        )

    if provider_protocol == "openai" and provider_family == "custom" and not api_base_url:
        warnings.append("OpenAI-compatible 通道在使用自定义服务时，建议明确填写 Base URL。")

    if _looks_like_endpoint_url(api_base_url):
        warnings.append(
            "主通道 Base URL 看起来像完整接口路径，请填写服务根地址，不要直接填 `/chat/completions` 或 `/responses`。是否带 `/v1` 取决于目标服务本身。"
        )

    if _looks_like_endpoint_url(embedding_api_base_url):
        warnings.append(
            "嵌入 Base URL 看起来像完整接口路径，请填写服务根地址，不要直接填 `/embeddings`。是否带 `/v1` 取决于目标服务本身。"
        )

    return {
        "provider_family": provider_family or "unknown",
        "embedding_provider_family": embedding_provider_family or "unknown",
        "provider_protocol": provider_protocol,
        "embedding_provider_protocol": embedding_protocol,
        "base_url_inferred_provider": inferred_provider,
        "embedding_base_url_inferred_provider": inferred_embedding_provider,
        "api_base_url": api_base_url,
        "embedding_api_base_url": embedding_api_base_url,
        "compatibility_warnings": warnings,
    }


def _build_troubleshooting(chat: dict, embedding: dict, analysis: dict) -> list[str]:
    notes = list(analysis.get("compatibility_warnings") or [])
    chat_message = str(chat.get("message") or "").lower()
    embedding_message = str(embedding.get("message") or "").lower()

    if "invalid_api_key" in chat_message or "invalid access token" in chat_message:
        notes.append("聊天 API Key 无效、已过期，或与当前聊天服务不匹配。")
    if (
        "invalidtoken" in embedding_message
        or "invalid_api_key" in embedding_message
        or "无效的token" in embedding_message
    ):
        notes.append("嵌入 API Key 无效、已过期，或与当前嵌入服务不匹配。")
    if "401" in chat_message and "token" in chat_message:
        notes.append("401 通常表示主通道的令牌无效，而不是前端没有保存。")
    if "403" in embedding_message and "token" in embedding_message:
        notes.append(
            "403 通常表示嵌入服务拒绝了当前 Token，请检查是否填入了对应平台的独立 embedding key。"
        )
    if "not found" in embedding_message and _looks_like_endpoint_url(
        analysis.get("embedding_api_base_url")
    ):
        notes.append(
            "嵌入 Base URL 可能填成了完整 `/embeddings` 接口地址；请改成服务根地址，例如 `https://api.siliconflow.cn/v1`。"
        )
    if "not found" in chat_message and _looks_like_endpoint_url(analysis.get("api_base_url")):
        notes.append("主通道 Base URL 可能填成了完整接口地址；请改成服务根地址，例如 `.../v1`。")

    deduped: list[str] = []
    for note in notes:
        if note and note not in deduped:
            deduped.append(note)
    return deduped


def _cfg_to_out(cfg) -> dict:
    embedding_api_key = _clean_optional_text(getattr(cfg, "embedding_api_key", None))
    image_api_key = _clean_optional_text(getattr(cfg, "image_api_key", None))
    has_embedding_override = bool(
        _clean_optional_text(getattr(cfg, "embedding_provider", None))
        or _clean_optional_text(getattr(cfg, "embedding_api_base_url", None))
        or embedding_api_key
    )
    analysis = _analyze_provider_config(cfg)
    provider_value = _provider_value_for_ui(
        analysis["provider_family"],
        analysis["provider_protocol"],
    )
    embedding_provider_value = None
    if has_embedding_override:
        embedding_provider_value = _provider_value_for_ui(
            analysis["embedding_provider_family"],
            analysis["embedding_provider_protocol"],
        )
    return {
        "id": cfg.id,
        "name": cfg.name,
        "provider": provider_value,
        "api_key_masked": _mask_key(cfg.api_key),
        "api_base_url": cfg.api_base_url,
        "model_skim": cfg.model_skim,
        "model_deep": cfg.model_deep,
        "model_vision": cfg.model_vision,
        "embedding_provider": embedding_provider_value,
        "embedding_api_base_url": _clean_optional_text(
            getattr(cfg, "embedding_api_base_url", None)
        ),
        "embedding_api_key_masked": _mask_key(embedding_api_key) if embedding_api_key else None,
        "model_embedding": cfg.model_embedding,
        "model_fallback": cfg.model_fallback,
        "image_provider": _normalize_image_provider_name(
            _clean_optional_text(getattr(cfg, "image_provider", None))
        ),
        "image_api_base_url": _clean_optional_text(getattr(cfg, "image_api_base_url", None)),
        "image_api_key_masked": _mask_key(image_api_key) if image_api_key else None,
        "model_image": _clean_optional_text(getattr(cfg, "model_image", None)),
        "is_active": cfg.is_active,
        **analysis,
    }


# ---------- LLM 配置管理 ----------


@router.get("/settings/llm-providers")
def list_llm_providers() -> dict:
    with session_scope() as session:
        cfgs = LLMConfigRepository(session).list_all()
        return {"items": [_cfg_to_out(c) for c in cfgs]}


@router.get("/settings/llm-provider-presets")
def list_llm_provider_presets() -> dict:
    return {"items": LLM_PROVIDER_PRESETS}


@router.get("/settings/llm-providers/active")
def get_active_llm_config() -> dict:
    """获取当前生效的 LLM 配置信息（固定路径，必须在动态路径之前）"""
    with session_scope() as session:
        active = LLMConfigRepository(session).get_active()
        if active:
            return {
                "source": "database",
                "config": _cfg_to_out(active),
            }
    return {
        "source": "none",
        "config": None,
    }


@router.post("/settings/llm-providers/deactivate")
def deactivate_llm_providers() -> dict:
    """取消所有配置激活"""
    from packages.integrations.llm_client import invalidate_llm_config_cache

    with session_scope() as session:
        LLMConfigRepository(session).deactivate_all()
        invalidate_llm_config_cache()
        return {
            "status": "ok",
            "message": "已取消所有 LLM 配置的激活状态",
        }


@router.post("/settings/llm-providers")
def create_llm_provider(req: LLMProviderCreate) -> dict:
    normalized = _normalize_provider_payload(
        provider=req.provider,
        api_base_url=req.api_base_url,
        embedding_provider=req.embedding_provider,
        embedding_api_base_url=req.embedding_api_base_url,
    )
    with session_scope() as session:
        try:
            cfg = LLMConfigRepository(session).create(
                name=req.name,
                provider=normalized["provider"] or req.provider,
                api_key=req.api_key,
                api_base_url=normalized["api_base_url"],
                model_skim=req.model_skim,
                model_deep=req.model_deep,
                model_vision=req.model_vision,
                embedding_provider=normalized["embedding_provider"],
                embedding_api_key=req.embedding_api_key,
                embedding_api_base_url=normalized["embedding_api_base_url"],
                model_embedding=req.model_embedding,
                model_fallback=req.model_fallback,
                image_provider=_normalize_image_provider_name(req.image_provider),
                image_api_key=req.image_api_key,
                image_api_base_url=_normalize_image_service_base_url(req.image_api_base_url),
                model_image=_clean_optional_text(req.model_image),
            )
        except IntegrityError as exc:
            if "llm_provider_configs.name" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail="配置名称已存在，请更换名称或编辑已有配置。",
                ) from exc
            raise
        return _cfg_to_out(cfg)


@router.patch("/settings/llm-providers/{config_id}")
def update_llm_provider(config_id: str, req: LLMProviderUpdate) -> dict:
    from packages.integrations.llm_client import invalidate_llm_config_cache

    with session_scope() as session:
        try:
            existing = LLMConfigRepository(session).get_by_id(config_id)
            normalized = _normalize_provider_payload(
                provider=req.provider if req.provider is not None else existing.provider,
                api_base_url=req.api_base_url
                if req.api_base_url is not None
                else existing.api_base_url,
                embedding_provider=(
                    req.embedding_provider
                    if req.embedding_provider is not None
                    else existing.embedding_provider
                ),
                embedding_api_base_url=(
                    req.embedding_api_base_url
                    if req.embedding_api_base_url is not None
                    else existing.embedding_api_base_url
                ),
            )
            normalized_image_provider = _normalize_image_provider_name(
                req.image_provider if req.image_provider is not None else existing.image_provider
            )
            normalized_image_api_base_url = _normalize_image_service_base_url(
                req.image_api_base_url
                if req.image_api_base_url is not None
                else existing.image_api_base_url
            )
            cfg = LLMConfigRepository(session).update(
                config_id,
                name=req.name,
                provider=normalized["provider"],
                api_key=req.api_key,
                api_base_url=normalized["api_base_url"] if req.api_base_url is not None else None,
                model_skim=req.model_skim,
                model_deep=req.model_deep,
                model_vision=req.model_vision,
                embedding_provider=normalized["embedding_provider"]
                if req.embedding_provider is not None or req.embedding_api_base_url is not None
                else None,
                embedding_api_key=req.embedding_api_key,
                embedding_api_base_url=normalized["embedding_api_base_url"]
                if req.embedding_api_base_url is not None
                else None,
                model_embedding=req.model_embedding,
                model_fallback=req.model_fallback,
                image_provider=(
                    normalized_image_provider
                    if req.image_provider is not None or req.image_api_base_url is not None
                    else None
                ),
                image_api_key=req.image_api_key,
                image_api_base_url=normalized_image_api_base_url
                if req.image_api_base_url is not None
                else None,
                model_image=req.model_image,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except IntegrityError as exc:
            if "llm_provider_configs.name" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail="配置名称已存在，请更换名称。",
                ) from exc
            raise
        invalidate_llm_config_cache()
        return _cfg_to_out(cfg)


@router.delete("/settings/llm-providers/{config_id}")
def delete_llm_provider(config_id: str) -> dict:
    from packages.integrations.llm_client import invalidate_llm_config_cache

    with session_scope() as session:
        LLMConfigRepository(session).delete(config_id)
        invalidate_llm_config_cache()
        return {"deleted": config_id}


@router.post("/settings/llm-providers/{config_id}/activate")
def activate_llm_provider(config_id: str) -> dict:
    from packages.integrations.llm_client import invalidate_llm_config_cache

    with session_scope() as session:
        try:
            cfg = LLMConfigRepository(session).activate(config_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        invalidate_llm_config_cache()
        return _cfg_to_out(cfg)


@router.post("/settings/llm-providers/{config_id}/test")
def test_llm_provider(config_id: str) -> dict:
    from packages.integrations.llm_client import (
        LLMClient,
        build_llm_config_from_record,
    )

    with session_scope() as session:
        try:
            stored = LLMConfigRepository(session).get_by_id(config_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        cfg_name = stored.name
        cfg = build_llm_config_from_record(stored)
        analysis = _analyze_provider_config(stored)
        config_out = _cfg_to_out(stored)

    result = LLMClient().test_config(cfg)
    return {
        "config_id": config_id,
        "name": cfg_name,
        "config": config_out,
        "warnings": _build_troubleshooting(
            result.get("chat", {}), result.get("embedding", {}), analysis
        ),
        **result,
    }


@router.get("/settings/workspace-roots")
def list_workspace_roots() -> dict:
    from packages.agent.workspace.workspace_executor import (
        default_projects_root as _default_projects_root,
    )
    from packages.agent.workspace.workspace_executor import (
        list_workspace_roots as _list_workspace_roots,
    )

    return {
        "items": _list_workspace_roots(),
        "default_projects_root": str(_default_projects_root()),
    }


@router.post("/settings/workspace-roots")
def create_workspace_root(body: WorkspaceRootCreate, request: Request) -> dict:
    require_sensitive_settings_access(request)
    from packages.agent.workspace.workspace_executor import (
        WorkspaceAccessError,
        add_workspace_root,
        default_projects_root,
    )
    from packages.agent.workspace.workspace_executor import (
        list_workspace_roots as _list_workspace_roots,
    )

    try:
        item = add_workspace_root(body.path, body.title)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "item": item,
        "items": _list_workspace_roots(),
        "default_projects_root": str(default_projects_root()),
    }


@router.put("/settings/workspace-roots")
def update_workspace_root(body: WorkspaceRootUpdate, request: Request) -> dict:
    require_sensitive_settings_access(request)
    from packages.agent.workspace.workspace_executor import (
        WorkspaceAccessError,
        default_projects_root,
    )
    from packages.agent.workspace.workspace_executor import (
        list_workspace_roots as _list_workspace_roots,
    )
    from packages.agent.workspace.workspace_executor import (
        update_workspace_root as _update_workspace_root,
    )

    try:
        item = _update_workspace_root(body.path, body.title)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "item": item,
        "items": _list_workspace_roots(),
        "default_projects_root": str(default_projects_root()),
    }


@router.put("/settings/workspace-roots/default")
def update_default_workspace_root(body: WorkspaceDefaultRootUpdate, request: Request) -> dict:
    require_sensitive_settings_access(request)
    from packages.agent.workspace.workspace_executor import (
        WorkspaceAccessError,
        default_projects_root,
        set_default_projects_root,
    )
    from packages.agent.workspace.workspace_executor import (
        list_workspace_roots as _list_workspace_roots,
    )

    try:
        resolved = set_default_projects_root(body.path)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "default_projects_root": str(resolved or default_projects_root()),
        "items": _list_workspace_roots(),
    }


@router.delete("/settings/workspace-roots")
def delete_workspace_root(
    request: Request,
    path: str = Query(..., description="要删除的工作区根目录"),
) -> dict:
    require_sensitive_settings_access(request)
    from packages.agent.workspace.workspace_executor import (
        WorkspaceAccessError,
        default_projects_root,
        remove_workspace_root,
    )
    from packages.agent.workspace.workspace_executor import (
        list_workspace_roots as _list_workspace_roots,
    )

    try:
        remove_workspace_root(path)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "deleted": path,
        "items": _list_workspace_roots(),
        "default_projects_root": str(default_projects_root()),
    }


@router.get("/settings/assistant-exec-policy")
def get_assistant_exec_policy() -> dict:
    from packages.agent.workspace.workspace_executor import (
        get_assistant_exec_policy as _get_assistant_exec_policy,
    )

    return _get_assistant_exec_policy()


@router.put("/settings/assistant-exec-policy")
def put_assistant_exec_policy(body: AssistantExecPolicyUpdate, request: Request) -> dict:
    require_sensitive_settings_access(request)
    from packages.agent.workspace.workspace_executor import update_assistant_exec_policy

    payload = body.model_dump(exclude_none=True)
    return update_assistant_exec_policy(payload)


@router.get("/settings/assistant-skills")
def list_assistant_skills() -> dict:
    from packages.agent.tools.skill_registry import list_local_skills, list_skill_scan_roots

    return {
        "items": list_local_skills(),
        "roots": list_skill_scan_roots(),
    }


# ---------- 邮箱配置 ----------


@router.get("/settings/email-configs")
def list_email_configs():
    """获取所有邮箱配置"""
    with session_scope() as session:
        repo = EmailConfigRepository(session)
        configs = repo.list_all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "smtp_server": c.smtp_server,
                "smtp_port": c.smtp_port,
                "smtp_use_tls": c.smtp_use_tls,
                "sender_email": c.sender_email,
                "sender_name": c.sender_name,
                "username": c.username,
                "is_active": c.is_active,
                "created_at": iso_dt(c.created_at),
            }
            for c in configs
        ]


@router.post("/settings/email-configs")
def create_email_config(body: EmailConfigCreate):
    """创建邮箱配置"""
    with session_scope() as session:
        repo = EmailConfigRepository(session)
        config = repo.create(
            name=body.name,
            smtp_server=body.smtp_server,
            smtp_port=body.smtp_port,
            smtp_use_tls=body.smtp_use_tls,
            sender_email=body.sender_email,
            sender_name=body.sender_name,
            username=body.username,
            password=body.password,
        )
        return {"id": config.id, "message": "邮箱配置创建成功"}


@router.patch("/settings/email-configs/{config_id}")
def update_email_config(config_id: str, body: EmailConfigUpdate):
    """更新邮箱配置"""
    with session_scope() as session:
        repo = EmailConfigRepository(session)
        update_data = {k: v for k, v in body.model_dump().items() if v is not None}
        config = repo.update(config_id, **update_data)
        if not config:
            raise HTTPException(status_code=404, detail="邮箱配置不存在")
        return {"message": "邮箱配置更新成功"}


@router.delete("/settings/email-configs/{config_id}")
def delete_email_config(config_id: str):
    """删除邮箱配置"""
    with session_scope() as session:
        repo = EmailConfigRepository(session)
        success = repo.delete(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="邮箱配置不存在")
        return {"message": "邮箱配置删除成功"}


@router.post("/settings/email-configs/{config_id}/activate")
def activate_email_config(config_id: str):
    """激活邮箱配置"""
    with session_scope() as session:
        repo = EmailConfigRepository(session)
        config = repo.set_active(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="邮箱配置不存在")
        return {"message": "邮箱配置已激活"}


@router.post("/settings/email-configs/{config_id}/test")
async def test_email_config(config_id: str):
    """测试邮箱配置（发送测试邮件）"""
    from packages.integrations.email_service import create_test_email

    with session_scope() as session:
        repo = EmailConfigRepository(session)
        config = repo.get_by_id(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="邮箱配置不存在")

        # 在session内发送测试邮件
        try:
            success = create_test_email(config)
            if success:
                return {"message": "测试邮件发送成功"}
            else:
                raise HTTPException(status_code=500, detail="测试邮件发送失败")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"测试邮件发送失败: {str(e)}")


# ---------- SMTP 配置预设 ----------


@router.get("/settings/smtp-presets")
def get_smtp_presets():
    """获取常见邮箱服务商的 SMTP 配置预设"""
    from packages.integrations.email_service import get_default_smtp_config

    providers: list[Literal["gmail", "qq", "163", "outlook"]] = ["gmail", "qq", "163", "outlook"]
    return {provider: get_default_smtp_config(provider) for provider in providers}
