from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_MODEL_VARIANTS = {
    "default",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
}
SUPPORTED_MODEL_PROTOCOLS = {"openai", "anthropic"}
SUPPORTED_MODEL_PROVIDERS = {"openai", "anthropic", "zhipu", "custom"}


@dataclass
class ParsedModelTarget:
    provider: str | None
    model: str
    variant: str | None
    raw: str


@dataclass
class ResolvedModelTarget:
    provider: str
    api_key: str | None
    base_url: str | None
    model: str
    variant: str | None
    stage: str


@dataclass
class ResolvedEmbeddingConfig:
    provider: str
    api_key: str | None
    base_url: str | None
    model: str
    explicit_provider: bool
    explicit_api_key: bool
    explicit_base_url: bool


def normalize_provider_name(provider: str | None) -> str:
    raw = (provider or "").strip().lower()
    aliases = {
        "openai-compatible": "custom",
        "openai_compatible": "custom",
        "dashscope": "custom",
        "aliyun": "custom",
        "bailian": "custom",
        "gemini": "custom",
        "google": "custom",
        "googleai": "custom",
        "vertex": "custom",
        "vertex_ai": "custom",
        "qwen": "custom",
        "kimi": "custom",
        "moonshot": "custom",
        "moonshotai": "custom",
        "minimax": "custom",
        "minimaxai": "custom",
        "openrouter": "custom",
        "siliconflow": "custom",
        "zhipuai": "zhipu",
        "bigmodel": "zhipu",
        "glm": "zhipu",
    }
    return aliases.get(raw, raw)


def normalize_protocol_name(value: str | None) -> str:
    raw = (value or "").strip().lower()
    aliases = {
        "openai": "openai",
        "openai-compatible": "openai",
        "openai_compatible": "openai",
        "compat": "openai",
        "custom": "openai",
        "zhipu": "openai",
        "zhipuai": "openai",
        "glm": "openai",
        "bigmodel": "openai",
        "gemini": "openai",
        "google": "openai",
        "googleai": "openai",
        "vertex": "openai",
        "vertex_ai": "openai",
        "qwen": "openai",
        "dashscope": "openai",
        "aliyun": "openai",
        "bailian": "openai",
        "kimi": "openai",
        "moonshot": "openai",
        "moonshotai": "openai",
        "minimax": "openai",
        "minimaxai": "openai",
        "openrouter": "openai",
        "siliconflow": "openai",
        "anthropic": "anthropic",
        "anthropic-compatible": "anthropic",
        "anthropic_compatible": "anthropic",
        "claude": "anthropic",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in SUPPORTED_MODEL_PROTOCOLS else ""


def normalize_model_variant(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    aliases = {
        "default": "default",
        "min": "minimal",
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "med": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "ultra": "xhigh",
        "max": "max",
        "none": "none",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in SUPPORTED_MODEL_VARIANTS:
        return None
    return normalized


def infer_provider_from_base_url(base_url: str | None) -> str | None:
    raw = (base_url or "").strip().lower()
    if not raw:
        return None
    if "open.bigmodel.cn" in raw or "bigmodel.cn" in raw or "zhipu" in raw:
        return "zhipu"
    if "anthropic.com" in raw:
        return "anthropic"
    if "openai.com" in raw:
        return "openai"
    if raw.startswith("http://") or raw.startswith("https://"):
        return "custom"
    return None


def infer_protocol_from_base_url(base_url: str | None) -> str | None:
    raw = (base_url or "").strip().lower()
    if not raw:
        return None
    if "anthropic.com" in raw:
        return "anthropic"
    if "/messages" in raw and "/chat/completions" not in raw and "/responses" not in raw:
        return "anthropic"
    if raw.startswith("http://") or raw.startswith("https://"):
        return "openai"
    return None


def resolve_provider_protocol(
    provider: str | None,
    base_url: str | None = None,
) -> str | None:
    normalized_provider = normalize_provider_name(provider)
    protocol = normalize_protocol_name(normalized_provider)
    if protocol:
        return protocol
    return infer_protocol_from_base_url(base_url)
