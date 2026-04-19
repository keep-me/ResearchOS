from __future__ import annotations

from packages.integrations.llm_provider_schema import ResolvedModelTarget

_RESPONSES_FALLBACK_MARKERS = (
    "unsupported legacy protocol",
    "/chat/completions is not supported",
    "please use /v1/responses",
    "please use /responses",
)


def supports_chat_reasoning_content(resolved: ResolvedModelTarget) -> bool:
    provider = str(resolved.provider or "").strip().lower()
    if provider == "zhipu":
        return True
    base_url = str(resolved.base_url or "").strip().lower()
    return bool(base_url and "api.openai.com" not in base_url)


def is_anthropic_chat_target(resolved: ResolvedModelTarget) -> bool:
    provider = str(resolved.provider or "").strip().lower()
    model = str(resolved.model or "").strip().lower()
    base_url = str(resolved.base_url or "").strip().lower()
    return provider == "anthropic" or "anthropic" in base_url or "claude" in model


def is_mistral_chat_target(resolved: ResolvedModelTarget) -> bool:
    provider = str(resolved.provider or "").strip().lower()
    model = str(resolved.model or "").strip().lower()
    base_url = str(resolved.base_url or "").strip().lower()
    return (
        provider == "mistral"
        or "mistral" in model
        or "devstral" in model
        or "mistral" in base_url
        or "devstral" in base_url
    )


def should_try_raw_openai_http_fallback(
    resolved: ResolvedModelTarget | None,
    exc: Exception,
) -> bool:
    if resolved is None:
        return False
    base_url = (resolved.base_url or "").lower()
    if not base_url or "api.openai.com" in base_url:
        return False
    message = str(exc).lower()
    return (
        "your request was blocked" in message
        or "permissiondeniederror" in message
        or ("blocked" in message and "request" in message)
    )


def should_try_openai_responses_fallback(
    resolved: ResolvedModelTarget | None,
    exc: Exception,
) -> bool:
    if resolved is None:
        return False
    provider = str(resolved.provider or "").strip().lower()
    if provider not in {"openai", "custom", "zhipu"}:
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _RESPONSES_FALLBACK_MARKERS)
