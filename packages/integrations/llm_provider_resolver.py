from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from packages.integrations.llm_provider_schema import (
    ParsedModelTarget,
    ResolvedEmbeddingConfig,
    ResolvedModelTarget,
    SUPPORTED_MODEL_PROVIDERS,
    infer_provider_from_base_url,
    normalize_model_variant,
    normalize_provider_name,
)

logger = logging.getLogger(__name__)


class SettingsLike(Protocol):
    zhipu_api_key: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    openai_base_url: str | None


class RuntimeConfigLike(Protocol):
    provider: str
    api_key: str | None
    api_base_url: str | None
    model_skim: str
    model_deep: str
    model_vision: str | None
    embedding_provider: str | None
    embedding_api_key: str | None
    embedding_api_base_url: str | None
    model_embedding: str
    model_fallback: str


@dataclass
class ProviderRuntimeConfig:
    provider: str
    api_key: str | None
    api_base_url: str | None
    model_skim: str
    model_deep: str
    model_vision: str | None
    embedding_provider: str | None
    embedding_api_key: str | None
    embedding_api_base_url: str | None
    model_embedding: str
    model_fallback: str


PROVIDER_BASE_URLS: dict[str, str] = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/",
    "openai": "https://api.openai.com/v1",
    "anthropic": "",
    "custom": "",
}


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def runtime_config_from_payload(payload: dict[str, Any] | None) -> ProviderRuntimeConfig:
    runtime = dict(payload or {})
    return ProviderRuntimeConfig(
        provider=normalize_provider_name(str(runtime.get("provider") or "")),
        api_key=clean_optional_text(runtime.get("api_key")),
        api_base_url=clean_optional_text(runtime.get("api_base_url")),
        model_skim=str(runtime.get("model_skim") or ""),
        model_deep=str(runtime.get("model_deep") or ""),
        model_vision=clean_optional_text(runtime.get("model_vision")),
        embedding_provider=normalize_provider_name(clean_optional_text(runtime.get("embedding_provider"))) or None,
        embedding_api_key=clean_optional_text(runtime.get("embedding_api_key")),
        embedding_api_base_url=clean_optional_text(runtime.get("embedding_api_base_url")),
        model_embedding=str(runtime.get("model_embedding") or ""),
        model_fallback=str(runtime.get("model_fallback") or ""),
    )


def default_api_key_for_provider(provider: str, settings: SettingsLike) -> str | None:
    normalized = normalize_provider_name(provider)
    if normalized == "zhipu":
        return settings.zhipu_api_key
    if normalized == "openai":
        return settings.openai_api_key
    if normalized == "anthropic":
        return settings.anthropic_api_key
    return None


def default_base_url_for_provider(provider: str, settings: SettingsLike) -> str | None:
    normalized = normalize_provider_name(provider)
    if normalized == "zhipu":
        return "https://open.bigmodel.cn/api/paas/v4/"
    if normalized == "openai":
        return settings.openai_base_url
    return None


def resolve_transport_base_url(provider: str, base: str | None) -> str | None:
    normalized_provider = normalize_provider_name(provider)
    resolved = base or PROVIDER_BASE_URLS.get(normalized_provider)
    if not resolved:
        return resolved
    resolved = resolved.strip().rstrip("/")
    if normalized_provider == "openai" and not resolved.lower().endswith("/v1"):
        return f"{resolved}/v1"
    if normalized_provider == "custom":
        return resolved
    if normalized_provider == "zhipu":
        return f"{resolved}/"
    return resolved


def resolve_base_url(cfg: RuntimeConfigLike) -> str | None:
    provider = normalize_provider_name(cfg.provider)
    return resolve_transport_base_url(provider, cfg.api_base_url)


def resolve_embedding_provider(cfg: RuntimeConfigLike) -> str:
    explicit_provider = normalize_provider_name(clean_optional_text(cfg.embedding_provider))
    if explicit_provider:
        return explicit_provider
    inferred_provider = infer_provider_from_base_url(clean_optional_text(cfg.embedding_api_base_url))
    if inferred_provider:
        return inferred_provider
    return normalize_provider_name(cfg.provider)


def resolve_embedding_api_key(cfg: RuntimeConfigLike) -> str | None:
    return clean_optional_text(cfg.embedding_api_key) or cfg.api_key


def resolve_embedding_base_url(cfg: RuntimeConfigLike) -> str | None:
    provider = resolve_embedding_provider(cfg)
    explicit_base = clean_optional_text(cfg.embedding_api_base_url)
    if explicit_base:
        return resolve_transport_base_url(provider, explicit_base)
    explicit_provider = normalize_provider_name(clean_optional_text(cfg.embedding_provider))
    if explicit_provider:
        return resolve_transport_base_url(provider, None)
    return resolve_base_url(cfg)


def resolve_embedding_config(cfg: RuntimeConfigLike) -> ResolvedEmbeddingConfig:
    provider = resolve_embedding_provider(cfg)
    api_key = resolve_embedding_api_key(cfg)
    base_url = resolve_embedding_base_url(cfg)
    return ResolvedEmbeddingConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=cfg.model_embedding,
        explicit_provider=clean_optional_text(cfg.embedding_provider) is not None,
        explicit_api_key=clean_optional_text(cfg.embedding_api_key) is not None,
        explicit_base_url=clean_optional_text(cfg.embedding_api_base_url) is not None,
    )


def stage_model_string(stage: str, cfg: RuntimeConfigLike) -> str:
    if stage in ("skim", "rag"):
        return cfg.model_skim
    if stage == "vision":
        return cfg.model_vision or cfg.model_deep
    if stage == "embedding":
        return cfg.model_embedding
    return cfg.model_deep


def parse_model_target(value: str | None) -> ParsedModelTarget | None:
    raw = clean_optional_text(value)
    if not raw:
        return None

    parts = [part.strip() for part in raw.split("/") if part.strip()]
    if not parts:
        return None

    provider: str | None = None
    remaining = parts
    provider_candidate = normalize_provider_name(parts[0])
    if provider_candidate in SUPPORTED_MODEL_PROVIDERS and len(parts) >= 2:
        provider = provider_candidate
        remaining = parts[1:]

    variant: str | None = None
    if len(remaining) >= 2:
        candidate_variant = normalize_model_variant(remaining[-1])
        if candidate_variant is not None:
            variant = candidate_variant
            remaining = remaining[:-1]

    model = "/".join(remaining).strip()
    if not model:
        model = raw.strip()
        provider = None
        variant = None

    return ParsedModelTarget(
        provider=provider,
        model=model,
        variant=variant,
        raw=raw,
    )


def resolve_model_target(
    stage: str,
    model_override: str | None,
    *,
    variant_override: str | None = None,
    cfg: RuntimeConfigLike,
    settings: SettingsLike,
    engine_profile_resolver=None,
) -> ResolvedModelTarget:
    resolver = engine_profile_resolver
    if resolver is None:
        try:
            from packages.integrations.llm_engine_profiles import resolve_llm_engine_profile

            resolver = resolve_llm_engine_profile
        except Exception:
            logger.debug("resolve_llm_engine_profile import failed", exc_info=True)
            resolver = None

    if model_override and callable(resolver):
        try:
            engine_profile = resolver(model_override)
        except Exception:
            logger.debug("resolve_llm_engine_profile failed", exc_info=True)
            engine_profile = None
        if engine_profile is not None:
            engine_cfg = runtime_config_from_payload(engine_profile.get("runtime_config"))
            provider = normalize_provider_name(str(engine_profile.get("provider") or engine_cfg.provider or "none"))
            model = str(engine_profile.get("model") or "").strip() or stage_model_string(stage, engine_cfg) or engine_cfg.model_fallback
            variant = normalize_model_variant(variant_override) or normalize_model_variant(str(engine_profile.get("default_variant") or ""))
            return ResolvedModelTarget(
                provider=provider,
                api_key=engine_cfg.api_key,
                base_url=resolve_transport_base_url(provider, engine_cfg.api_base_url),
                model=model,
                variant=variant,
                stage=stage,
            )

    target_spec = model_override or stage_model_string(stage, cfg) or cfg.model_fallback
    parsed = parse_model_target(target_spec) or ParsedModelTarget(
        provider=None,
        model="",
        variant=None,
        raw=target_spec or "",
    )

    cfg_provider = normalize_provider_name(cfg.provider)
    provider = normalize_provider_name(parsed.provider or cfg_provider or "none")
    variant = normalize_model_variant(variant_override) or parsed.variant
    model = parsed.model or stage_model_string(stage, cfg) or cfg.model_fallback

    if provider == cfg_provider:
        api_key = cfg.api_key
        base_url = resolve_transport_base_url(provider, cfg.api_base_url)
    else:
        api_key = default_api_key_for_provider(provider, settings)
        base_url = resolve_transport_base_url(
            provider,
            default_base_url_for_provider(provider, settings),
        )

    return ResolvedModelTarget(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        variant=variant,
        stage=stage,
    )
