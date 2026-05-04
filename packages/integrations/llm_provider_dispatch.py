from __future__ import annotations

from dataclasses import dataclass

from packages.integrations import llm_provider_transform
from packages.integrations.llm_provider_schema import (
    ResolvedEmbeddingConfig,
    ResolvedModelTarget,
    normalize_provider_name,
)


@dataclass(frozen=True)
class SummaryDispatch:
    route: str
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ChatDispatch:
    route: str


@dataclass(frozen=True)
class ChatTestDispatch:
    route: str


@dataclass(frozen=True)
class EmbeddingDispatch:
    route: str
    fallback_reason: str | None = None


@dataclass(frozen=True)
class EmbeddingTestDispatch:
    route: str


def _provider(value: str | None) -> str:
    return normalize_provider_name(value)


def resolve_summary_dispatch(target: ResolvedModelTarget) -> SummaryDispatch:
    provider = _provider(target.provider)
    if provider in ("", "none"):
        return SummaryDispatch(route="pseudo", fallback_reason="missing_active_config")
    if provider == "openai" and target.api_key:
        return SummaryDispatch(route="openai-responses")
    if provider in ("zhipu", "custom") and target.api_key:
        return SummaryDispatch(route="openai-compatible")
    if provider == "anthropic" and target.api_key:
        return SummaryDispatch(route="anthropic")
    return SummaryDispatch(route="pseudo")


def resolve_chat_dispatch(target: ResolvedModelTarget) -> ChatDispatch:
    provider = _provider(target.provider)
    if provider in ("", "none"):
        return ChatDispatch(route="pseudo")
    if (
        provider == "openai"
        and target.api_key
        and llm_provider_transform.is_official_openai_target(target)
    ):
        return ChatDispatch(route="openai-responses")
    if provider in ("openai", "zhipu", "custom") and target.api_key:
        return ChatDispatch(route="openai-compatible")
    if provider == "anthropic" and target.api_key:
        return ChatDispatch(route="anthropic")
    return ChatDispatch(route="pseudo")


def resolve_chat_test_dispatch(target: ResolvedModelTarget) -> ChatTestDispatch:
    provider = _provider(target.provider)
    if provider in ("", "none"):
        return ChatTestDispatch(route="disabled")
    if not target.api_key:
        return ChatTestDispatch(route="missing_api_key")
    if provider == "openai":
        return ChatTestDispatch(route="openai")
    if provider in ("zhipu", "custom"):
        return ChatTestDispatch(route="openai-compatible")
    return ChatTestDispatch(route="anthropic")


def resolve_embedding_dispatch(
    active_provider: str | None,
    embedding_cfg: ResolvedEmbeddingConfig,
) -> EmbeddingDispatch:
    provider = _provider(active_provider)
    if provider in ("", "none"):
        return EmbeddingDispatch(route="pseudo", fallback_reason="missing_active_config")
    if embedding_cfg.provider in ("openai", "zhipu", "custom") and embedding_cfg.api_key:
        return EmbeddingDispatch(route="openai-compatible")

    fallback_reason = "remote_embedding_failed"
    if embedding_cfg.provider not in ("openai", "zhipu", "custom"):
        fallback_reason = "unsupported_provider"
    elif not embedding_cfg.api_key:
        fallback_reason = "missing_api_key"
    return EmbeddingDispatch(route="pseudo", fallback_reason=fallback_reason)


def resolve_embedding_test_dispatch(
    active_provider: str | None,
    embedding_cfg: ResolvedEmbeddingConfig,
) -> EmbeddingTestDispatch:
    provider = _provider(active_provider)
    if provider in ("", "none"):
        return EmbeddingTestDispatch(route="disabled")
    if embedding_cfg.provider not in ("openai", "zhipu", "custom"):
        return EmbeddingTestDispatch(route="unsupported")
    if not embedding_cfg.api_key:
        return EmbeddingTestDispatch(route="missing_api_key")
    return EmbeddingTestDispatch(route="openai-compatible")
