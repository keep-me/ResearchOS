from __future__ import annotations

import base64
import copy
import re
import urllib.parse
import urllib.request
from typing import Any

from packages.integrations.llm_provider_schema import (
    ResolvedModelTarget,
    normalize_model_variant,
)

_ATTACH_RIGHT_PUNCT = set("([{/\\")
_ATTACH_LEFT_PUNCT = set(",.!?;:%)]}/\\")
_WORD_GAP_AFTER_PUNCT = set(",.;:!?")


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def supports_dashscope_thinking(base_url: str | None) -> bool:
    raw = (base_url or "").strip().lower()
    return "dashscope" in raw or "aliyuncs.com" in raw or "bailian" in raw


def is_official_openai_target(target: ResolvedModelTarget) -> bool:
    provider = str(target.provider or "").strip().lower()
    base_url = str(target.base_url or "").strip().lower()
    return provider == "openai" and (not base_url or "api.openai.com" in base_url)


def is_google_openai_target(target: ResolvedModelTarget) -> bool:
    model = str(target.model or "").strip().lower()
    base_url = str(target.base_url or "").strip().lower()
    return "gemini" in model or "generativelanguage.googleapis.com" in base_url


def is_zhipu_openai_target(target: ResolvedModelTarget) -> bool:
    provider = str(target.provider or "").strip().lower()
    base_url = str(target.base_url or "").strip().lower()
    return (
        provider == "zhipu"
        or "bigmodel.cn" in base_url
        or "open.bigmodel.cn" in base_url
        or "zhipu" in base_url
    )


def is_openrouter_target(target: ResolvedModelTarget) -> bool:
    provider = str(target.provider or "").strip().lower()
    base_url = str(target.base_url or "").strip().lower()
    return provider == "openrouter" or "openrouter.ai" in base_url


def is_venice_target(target: ResolvedModelTarget) -> bool:
    provider = str(target.provider or "").strip().lower()
    base_url = str(target.base_url or "").strip().lower()
    return provider == "venice" or "venice.ai" in base_url


def resolve_model_temperature(model: str) -> float | None:
    model_lower = (model or "").strip().lower()
    if "qwen" in model_lower:
        return 0.55
    if "claude" in model_lower:
        return None
    if "gemini" in model_lower:
        return 1.0
    if "glm-4.6" in model_lower or "glm-4.7" in model_lower:
        return 1.0
    if "minimax-m2" in model_lower:
        return 1.0
    if "kimi-k2" in model_lower:
        if any(token in model_lower for token in ("thinking", "k2.", "k2p", "k2-5")):
            return 1.0
        return 0.6
    return None


def resolve_model_top_p(model: str) -> float | None:
    model_lower = (model or "").strip().lower()
    if "qwen" in model_lower:
        return 1.0
    if any(
        token in model_lower
        for token in ("minimax-m2", "gemini", "kimi-k2.5", "kimi-k2p5", "kimi-k2-5")
    ):
        return 0.95
    return None


def resolve_model_top_k(model: str) -> int | None:
    model_lower = (model or "").strip().lower()
    if "minimax-m2" in model_lower:
        if any(token in model_lower for token in ("m2.", "m25", "m21")):
            return 40
        return 20
    return None


def normalize_claude_tool_call_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value or "").strip())


def normalize_mistral_tool_call_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]", "", str(value or "").strip())[:9]
    return normalized.ljust(9, "0") or "000000000"


def _is_cjk(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _should_insert_ascii_gap(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    last = previous[-1]
    first = current[0]
    if last.isspace() or first.isspace():
        return False
    if last in _WORD_GAP_AFTER_PUNCT and first.isascii() and first.isalnum():
        return True
    if last in _ATTACH_RIGHT_PUNCT or first in _ATTACH_LEFT_PUNCT:
        return False
    if _is_cjk(last) or _is_cjk(first):
        return False
    if not last.isascii() or not first.isascii():
        return False
    if not last.isalnum() or not first.isalnum():
        return False
    previous_compact = previous.strip()
    current_compact = current.strip()
    if not previous_compact or not current_compact:
        return False
    if previous_compact.endswith(("-", "_", "/")) or current_compact.startswith(("-", "_", "/")):
        return False
    return True


def _merge_text_fragments(parts: list[str]) -> str:
    merged = ""
    for part in parts:
        text = str(part or "")
        if not text:
            continue
        if not merged:
            merged = text
            continue
        merged = f"{merged}{' ' if _should_insert_ascii_gap(merged, text) else ''}{text}"
    return merged


def normalize_openai_chat_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    normalized: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("type") or "").strip() == "provider-defined":
            continue
        normalized.append(copy.deepcopy(tool))
    return normalized or None


def normalize_openai_chat_messages(
    client,
    messages: list[dict[str, Any]],
    *,
    resolved: ResolvedModelTarget,
) -> list[dict[str, Any]]:
    is_anthropic = client._is_anthropic_chat_target(resolved)
    is_mistral = client._is_mistral_chat_target(resolved)
    normalized: list[dict[str, Any]] = []

    def _normalize_tool_call_id(value: str) -> str:
        if is_mistral:
            return normalize_mistral_tool_call_id(value)
        if is_anthropic:
            return normalize_claude_tool_call_id(value)
        return str(value or "").strip()

    for message in messages:
        item = dict(message)
        role = str(item.get("role") or "user")
        if role == "assistant":
            tool_calls = item.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                normalized_calls: list[dict[str, Any]] = []
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    updated_call = dict(call)
                    call_id = str(updated_call.get("id") or "").strip()
                    if call_id:
                        updated_call["id"] = _normalize_tool_call_id(call_id)
                    function_payload = updated_call.get("function")
                    if isinstance(function_payload, dict):
                        updated_call["function"] = dict(function_payload)
                    normalized_calls.append(updated_call)
                item["tool_calls"] = normalized_calls
        elif role == "tool":
            tool_call_id = str(item.get("tool_call_id") or "").strip()
            if tool_call_id:
                item["tool_call_id"] = _normalize_tool_call_id(tool_call_id)

        if is_anthropic:
            content = stringify_message_content(item.get("content"))
            reasoning = str(item.get("reasoning_content") or "").strip()
            tool_calls = item.get("tool_calls")
            if not content and not reasoning and not (isinstance(tool_calls, list) and tool_calls):
                continue
        normalized.append(item)

    if is_mistral:
        fixed: list[dict[str, Any]] = []
        for index, item in enumerate(normalized):
            fixed.append(item)
            next_item = normalized[index + 1] if index + 1 < len(normalized) else None
            if (
                str(item.get("role") or "") == "tool"
                and str((next_item or {}).get("role") or "") == "user"
            ):
                fixed.append(
                    {
                        "role": "assistant",
                        "content": "Done.",
                    }
                )
        normalized = fixed

    return normalized


def build_openai_chat_messages(
    client,
    messages: list[dict[str, Any]],
    *,
    resolved: ResolvedModelTarget,
    include_reasoning_content: bool,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"

        content: object
        if role == "user":
            content = build_openai_chat_user_content(message.get("content"))
        else:
            content = str(message.get("content") or "")
        item: dict[str, object] = {
            "role": role,
            "content": content,
        }
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            if tool_call_id:
                item["tool_call_id"] = tool_call_id
            tool_name = str(message.get("name") or "").strip()
            if tool_name:
                item["name"] = tool_name

        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                item["tool_calls"] = tool_calls
            if include_reasoning_content:
                reasoning = str(message.get("reasoning_content") or "").strip()
                if reasoning:
                    item["reasoning_content"] = reasoning

        has_content = False
        if isinstance(item.get("content"), list):
            has_content = bool(item.get("content"))
        else:
            has_content = bool(str(item.get("content") or "").strip())
        if (
            has_content
            or role in {"system", "user", "tool"}
            or item.get("tool_calls")
            or item.get("reasoning_content")
        ):
            payload.append(item)
    return normalize_openai_chat_messages(client, payload, resolved=resolved)


def coerce_openai_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            inner = item.get("content")
            if isinstance(inner, str):
                parts.append(inner)
        return _merge_text_fragments(parts)
    return ""


def stringify_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    chunks.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "text":
                text = str(item.get("text") or item.get("content") or "")
                if text:
                    chunks.append(text)
                continue
            if item_type == "file":
                mime = str(item.get("mime") or "file").strip() or "file"
                filename = str(item.get("filename") or "file").strip() or "file"
                chunks.append(f"[Attached {mime}: {filename}]")
        return "\n\n".join(chunk for chunk in chunks if chunk.strip()).strip()
    if isinstance(content, dict):
        text = coerce_openai_message_text(content.get("text") or content.get("content"))
        if text:
            return text
    return coerce_openai_message_text(content)


def normalize_user_content_parts(content: object) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "text":
            text = str(item.get("text") or item.get("content") or "")
            if text:
                normalized.append({"type": "text", "text": text})
            continue
        if item_type != "file":
            continue
        url = str(item.get("url") or "").strip()
        filename = str(item.get("filename") or "").strip()
        mime = str(item.get("mime") or "").strip() or "application/octet-stream"
        if not url and not filename:
            continue
        part: dict[str, Any] = {
            "type": "file",
            "mime": mime,
        }
        if url:
            part["url"] = url
        if filename:
            part["filename"] = filename
        normalized.append(part)
    return normalized


def decode_data_url_bytes(url: str) -> tuple[str | None, bytes | None]:
    raw = str(url or "").strip()
    if not raw.startswith("data:") or "," not in raw:
        return None, None
    header, payload = raw[5:].split(",", 1)
    mime = header.split(";", 1)[0].strip() or None
    try:
        if ";base64" in header:
            return mime, base64.b64decode(payload)
        return mime, urllib.parse.unquote_to_bytes(payload)
    except Exception:
        return mime, None


def read_local_file_bytes_from_url(url: str) -> bytes | None:
    raw = str(url or "").strip()
    if not raw.lower().startswith("file://"):
        return None
    path = raw[7:]
    if path.startswith("/") and re.match(r"^/[a-zA-Z]:", path):
        path = path[1:]
    try:
        resolved = urllib.request.url2pathname(path)
        with open(resolved, "rb") as handle:
            return handle.read()
    except Exception:
        return None


def build_data_url(mime: str, raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def resolve_model_accessible_file_url(part: dict[str, Any]) -> str | None:
    url = str(part.get("url") or "").strip()
    if not url:
        return None
    if url.lower().startswith("file://"):
        raw = read_local_file_bytes_from_url(url)
        if raw is None:
            return None
        mime = str(part.get("mime") or "").strip() or "application/octet-stream"
        return build_data_url(mime, raw)
    return url


def extract_text_from_user_file_part(part: dict[str, Any]) -> str | None:
    mime = str(part.get("mime") or "").strip().lower()
    if not (
        mime.startswith("text/")
        or mime in {"application/json", "application/xml", "application/yaml", "application/x-yaml"}
    ):
        return None
    url = str(part.get("url") or "").strip()
    raw: bytes | None = None
    if url.startswith("data:"):
        _, raw = decode_data_url_bytes(url)
    elif url.lower().startswith("file://"):
        raw = read_local_file_bytes_from_url(url)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def build_responses_user_content(content: object) -> list[dict[str, Any]] | None:
    parts = normalize_user_content_parts(content)
    if not parts:
        return None
    payload: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        part_type = str(part.get("type") or "").strip().lower()
        if part_type == "text":
            text = str(part.get("text") or "")
            if text:
                payload.append({"type": "input_text", "text": text})
            continue

        mime = str(part.get("mime") or "").strip().lower()
        if mime.startswith("image/"):
            resolved_url = resolve_model_accessible_file_url(part)
            if resolved_url:
                payload.append({"type": "input_image", "image_url": resolved_url})
                continue
        elif mime == "application/pdf":
            url = str(part.get("url") or "").strip()
            if url.lower().startswith("file://"):
                raw = read_local_file_bytes_from_url(url)
                if raw is not None:
                    payload.append(
                        {
                            "type": "input_file",
                            "filename": str(part.get("filename") or f"part-{index}.pdf"),
                            "file_data": build_data_url("application/pdf", raw),
                        }
                    )
                    continue
            if url:
                payload.append({"type": "input_file", "file_url": url})
                continue

        text = extract_text_from_user_file_part(part)
        if text:
            payload.append({"type": "input_text", "text": text})
            continue
        placeholder = stringify_message_content([part]).strip()
        if placeholder:
            payload.append({"type": "input_text", "text": placeholder})
    return payload or None


def build_openai_chat_user_content(content: object) -> str | list[dict[str, Any]]:
    parts = normalize_user_content_parts(content)
    if not parts:
        return stringify_message_content(content)
    payload: list[dict[str, Any]] = []
    for part in parts:
        part_type = str(part.get("type") or "").strip().lower()
        if part_type == "text":
            text = str(part.get("text") or "")
            if text:
                payload.append({"type": "text", "text": text})
            continue

        mime = str(part.get("mime") or "").strip().lower()
        if mime.startswith("image/"):
            resolved_url = resolve_model_accessible_file_url(part)
            if resolved_url:
                payload.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": resolved_url},
                    }
                )
                continue

        text = extract_text_from_user_file_part(part)
        fallback = text.strip() if text else stringify_message_content([part]).strip()
        if fallback:
            payload.append({"type": "text", "text": fallback})

    return payload or stringify_message_content(content)


def should_enable_dashscope_reasoning(model: str) -> bool:
    model_lower = (model or "").strip().lower()
    if "kimi-k2-thinking" in model_lower:
        return False
    return any(
        token in model_lower
        for token in (
            "qwen",
            "qwq",
            "deepseek",
            "r1",
            "reason",
            "thinking",
            "kimi",
            "k2",
            "glm",
        )
    )


def get_extra_body(kwargs: dict) -> dict:
    extra_body = dict(kwargs.get("extra_body") or {})
    kwargs["extra_body"] = extra_body
    return extra_body


def setdefault_object(parent: dict, key: str) -> dict:
    nested = dict(parent.get(key) or {})
    parent[key] = nested
    return nested


def apply_sampling_kwargs(
    kwargs: dict,
    target: ResolvedModelTarget,
    *,
    allow_top_k: bool,
) -> None:
    temperature = resolve_model_temperature(target.model)
    if temperature is not None:
        kwargs.setdefault("temperature", temperature)

    top_p = resolve_model_top_p(target.model)
    if top_p is not None:
        kwargs.setdefault("top_p", top_p)

    top_k = resolve_model_top_k(target.model)
    if allow_top_k and top_k is not None:
        extra_body = get_extra_body(kwargs)
        extra_body.setdefault("top_k", top_k)


def build_google_thinking_config(target: ResolvedModelTarget) -> dict:
    model_lower = (target.model or "").strip().lower()
    normalized = normalize_model_variant(target.variant)
    config: dict[str, object] = {
        "include_thoughts": True,
    }

    if "gemini-2.5" in model_lower:
        if normalized in {"none", "minimal"}:
            config["thinking_budget"] = 0
        elif normalized == "high":
            config["thinking_budget"] = 16_000
        elif normalized in {"max", "xhigh"}:
            config["thinking_budget"] = 24_576
        return config

    level: str | None = None
    if normalized in (None, "default"):
        if "gemini-3" in model_lower:
            level = "high"
    elif normalized in {"none", "minimal"}:
        level = "minimal"
    elif normalized == "low":
        level = "low"
    elif normalized == "medium":
        if "gemini-3.1" in model_lower or "gemini-3-1" in model_lower:
            level = "medium"
    elif normalized in {"high", "xhigh", "max"}:
        level = "high"

    if level:
        config["thinking_level"] = level
    return config


def is_small_target(target: ResolvedModelTarget) -> bool:
    return str(target.stage or "").strip().lower() == "skim"


def gateway_provider_slug(model: str) -> str | None:
    raw_model = str(model or "").strip()
    if "/" not in raw_model:
        return None
    raw_slug = raw_model.split("/", 1)[0].strip().lower()
    if not raw_slug:
        return None
    overrides = {
        "amazon": "bedrock",
    }
    return overrides.get(raw_slug, raw_slug)


def is_gateway_target(target: ResolvedModelTarget) -> bool:
    provider = str(target.provider or "").strip().lower()
    if provider == "gateway":
        return True
    base_url = str(target.base_url or "").strip().lower()
    if not base_url:
        return False
    host = urllib.parse.urlparse(base_url).netloc
    return "gateway" in host


def provider_option_namespace_key(target: ResolvedModelTarget) -> str | None:
    if is_gateway_target(target):
        return None
    provider = str(target.provider or "").strip().lower()
    if is_openrouter_target(target):
        return "openrouter"
    if is_venice_target(target):
        return "venice"
    if is_google_openai_target(target):
        return "google"
    if is_zhipu_openai_target(target):
        return "zhipu"
    if provider == "anthropic":
        return "anthropic"
    if provider in {"openai", "azure"} or is_official_openai_target(target):
        return "openai"
    return provider or None


def remap_provider_options_namespace(
    target: ResolvedModelTarget,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(options, dict) or not options:
        return {}

    copied = copy.deepcopy(options)
    if is_gateway_target(target):
        gateway = copied.pop("gateway", None)
        result: dict[str, Any] = {}
        if gateway is not None:
            result["gateway"] = gateway
        if copied:
            slug = gateway_provider_slug(target.model)
            if slug:
                result[slug] = copied
            elif isinstance(gateway, dict):
                merged = copy.deepcopy(gateway)
                merged.update(copied)
                result["gateway"] = merged
            else:
                result["gateway"] = copied
        return result

    key = provider_option_namespace_key(target)
    if key:
        return {key: copied}
    return copied


def build_small_provider_options(target: ResolvedModelTarget) -> dict[str, Any]:
    model_lower = str(target.model or "").strip().lower()

    if is_google_openai_target(target):
        if "gemini-3" in model_lower:
            return {"thinkingConfig": {"thinkingLevel": "minimal"}}
        return {"thinkingConfig": {"thinkingBudget": 0}}

    if is_openrouter_target(target):
        if "google" in model_lower or "gemini" in model_lower:
            return {"reasoning": {"enabled": False}}
        return {"reasoningEffort": "minimal"}

    if is_venice_target(target):
        return {"veniceParameters": {"disableThinking": True}}

    if str(target.provider or "").strip().lower() == "openai" or is_official_openai_target(target):
        if "gpt-5" in model_lower:
            effort = "low" if "gpt-5." in model_lower else "minimal"
            return {"store": False, "reasoningEffort": effort}
        return {"store": False}

    return {}


def apply_provider_options_to_responses_kwargs(
    kwargs: dict,
    target: ResolvedModelTarget,
    options: dict[str, Any] | None,
) -> None:
    namespaced = remap_provider_options_namespace(target, options)
    if not namespaced:
        return

    for namespace, payload in namespaced.items():
        if not isinstance(payload, dict):
            continue
        if namespace == "openai":
            if payload.get("store") is not None:
                kwargs["store"] = bool(payload.get("store"))
            effort = clean_optional_text(payload.get("reasoningEffort"))
            if effort:
                reasoning = dict(kwargs.get("reasoning") or {})
                reasoning["effort"] = effort
                kwargs["reasoning"] = reasoning
            continue
        if namespace == "openrouter":
            reasoning_payload = payload.get("reasoning")
            reasoning = dict(kwargs.get("reasoning") or {})
            if isinstance(reasoning_payload, dict):
                reasoning.update(copy.deepcopy(reasoning_payload))
            effort = clean_optional_text(payload.get("reasoningEffort"))
            if effort:
                reasoning["effort"] = effort
            if reasoning:
                kwargs["reasoning"] = reasoning
            continue
        if namespace == "google":
            thinking_config = payload.get("thinkingConfig")
            if isinstance(thinking_config, dict):
                extra_body = get_extra_body(kwargs)
                google = setdefault_object(extra_body, "google")
                normalized = setdefault_object(google, "thinking_config")
                if thinking_config.get("includeThoughts") is not None:
                    normalized["include_thoughts"] = bool(thinking_config.get("includeThoughts"))
                if thinking_config.get("thinkingLevel") is not None:
                    normalized["thinking_level"] = thinking_config.get("thinkingLevel")
                if thinking_config.get("thinkingBudget") is not None:
                    normalized["thinking_budget"] = thinking_config.get("thinkingBudget")
            continue
        if namespace == "venice":
            venice_parameters = payload.get("veniceParameters")
            if isinstance(venice_parameters, dict):
                extra_body = get_extra_body(kwargs)
                normalized = setdefault_object(extra_body, "venice_parameters")
                if venice_parameters.get("disableThinking") is not None:
                    normalized["disable_thinking"] = bool(venice_parameters.get("disableThinking"))
            continue
        extra_body = get_extra_body(kwargs)
        extra_body[namespace] = copy.deepcopy(payload)


def apply_provider_options_to_chat_kwargs(
    kwargs: dict,
    target: ResolvedModelTarget,
    options: dict[str, Any] | None,
) -> None:
    namespaced = remap_provider_options_namespace(target, options)
    if not namespaced:
        return

    for namespace, payload in namespaced.items():
        if not isinstance(payload, dict):
            continue
        if namespace == "openai":
            if payload.get("store") is not None:
                kwargs["store"] = bool(payload.get("store"))
            effort = clean_optional_text(payload.get("reasoningEffort"))
            if effort:
                kwargs["reasoning_effort"] = effort
            continue
        if namespace == "openrouter":
            effort = clean_optional_text(payload.get("reasoningEffort"))
            if effort:
                kwargs["reasoning_effort"] = effort
            reasoning_payload = payload.get("reasoning")
            if isinstance(reasoning_payload, dict):
                extra_body = get_extra_body(kwargs)
                reasoning = setdefault_object(extra_body, "reasoning")
                reasoning.clear()
                reasoning.update(copy.deepcopy(reasoning_payload))
            continue
        if namespace == "google":
            thinking_config = payload.get("thinkingConfig")
            if isinstance(thinking_config, dict):
                extra_body = get_extra_body(kwargs)
                google = setdefault_object(extra_body, "google")
                normalized = setdefault_object(google, "thinking_config")
                if thinking_config.get("includeThoughts") is not None:
                    normalized["include_thoughts"] = bool(thinking_config.get("includeThoughts"))
                if thinking_config.get("thinkingLevel") is not None:
                    normalized["thinking_level"] = thinking_config.get("thinkingLevel")
                if thinking_config.get("thinkingBudget") is not None:
                    normalized["thinking_budget"] = thinking_config.get("thinkingBudget")
            continue
        if namespace == "venice":
            venice_parameters = payload.get("veniceParameters")
            if isinstance(venice_parameters, dict):
                extra_body = get_extra_body(kwargs)
                normalized = setdefault_object(extra_body, "venice_parameters")
                if venice_parameters.get("disableThinking") is not None:
                    normalized["disable_thinking"] = bool(venice_parameters.get("disableThinking"))
            continue
        extra_body = get_extra_body(kwargs)
        extra_body[namespace] = copy.deepcopy(payload)


def supports_xhigh_effort(model: str) -> bool:
    model_lower = (model or "").lower()
    return any(token in model_lower for token in ("gpt-5.2", "gpt-5.3", "gpt-5.4", "codex"))


def resolve_reasoning_effort(model: str, variant: str | None) -> str | None:
    normalized = normalize_model_variant(variant)
    model_lower = (model or "").lower()
    if normalized in (None, "default"):
        if "gpt-5" in model_lower and "gpt-5-pro" not in model_lower:
            return "medium"
        return None
    if normalized == "max":
        return "high"
    if normalized == "xhigh":
        return "xhigh" if supports_xhigh_effort(model) else "high"
    return normalized


def apply_variant_to_responses_kwargs(
    kwargs: dict,
    target: ResolvedModelTarget,
    *,
    session_cache_key: str | None = None,
) -> None:
    apply_sampling_kwargs(kwargs, target, allow_top_k=False)
    model_lower = target.model.lower()
    effort = resolve_reasoning_effort(target.model, target.variant)
    if is_official_openai_target(target):
        kwargs.setdefault("store", False)
        if session_cache_key:
            kwargs.setdefault("prompt_cache_key", session_cache_key)
    elif session_cache_key:
        if is_openrouter_target(target):
            kwargs.setdefault("prompt_cache_key", session_cache_key)
        elif is_venice_target(target):
            kwargs.setdefault("promptCacheKey", session_cache_key)
    if effort:
        reasoning = dict(kwargs.get("reasoning") or {})
        reasoning["effort"] = effort
        if "gpt-5" in model_lower or "codex" in model_lower or is_official_openai_target(target):
            reasoning.setdefault("summary", "auto")
        kwargs["reasoning"] = reasoning

    if "gpt-5" in model_lower:
        include = list(kwargs.get("include") or [])
        if "reasoning.encrypted_content" not in include:
            include.append("reasoning.encrypted_content")
        kwargs["include"] = include
        if (
            "gpt-5." in model_lower
            and "codex" not in model_lower
            and "-chat" not in model_lower
            and "gpt-5-pro" not in model_lower
        ):
            kwargs["text"] = {"verbosity": "low"}

    if is_small_target(target):
        apply_provider_options_to_responses_kwargs(
            kwargs, target, build_small_provider_options(target)
        )


def apply_variant_to_chat_kwargs(
    kwargs: dict,
    target: ResolvedModelTarget,
    *,
    session_cache_key: str | None = None,
) -> None:
    apply_sampling_kwargs(kwargs, target, allow_top_k=True)
    if is_official_openai_target(target):
        kwargs.setdefault("store", False)
        if session_cache_key:
            kwargs.setdefault("prompt_cache_key", session_cache_key)
    elif session_cache_key:
        if is_openrouter_target(target):
            kwargs.setdefault("prompt_cache_key", session_cache_key)
        elif is_venice_target(target):
            kwargs.setdefault("promptCacheKey", session_cache_key)

    effort = resolve_reasoning_effort(target.model, target.variant)
    model_lower = target.model.lower()
    extra_body = dict(kwargs.get("extra_body") or {})
    if is_google_openai_target(target):
        google = setdefault_object(extra_body, "google")
        thinking_config = setdefault_object(google, "thinking_config")
        for key, value in build_google_thinking_config(target).items():
            thinking_config.setdefault(key, value)
    elif effort:
        kwargs["reasoning_effort"] = effort

    if is_zhipu_openai_target(target):
        thinking = setdefault_object(extra_body, "thinking")
        thinking.setdefault("type", "enabled")
        thinking.setdefault("clear_thinking", False)
    if effort and (
        "gpt-5" in model_lower or "codex" in model_lower or is_official_openai_target(target)
    ):
        extra_body.setdefault("reasoning_summary", "auto")
    if "gpt-5" in model_lower or "codex" in model_lower:
        include = list(extra_body.get("include") or [])
        if "reasoning.encrypted_content" not in include:
            include.append("reasoning.encrypted_content")
        extra_body["include"] = include
    if supports_dashscope_thinking(target.base_url) and should_enable_dashscope_reasoning(
        target.model
    ):
        extra_body.setdefault("enable_thinking", True)
    if extra_body:
        kwargs["extra_body"] = extra_body
    else:
        kwargs.pop("extra_body", None)

    if is_small_target(target):
        apply_provider_options_to_chat_kwargs(kwargs, target, build_small_provider_options(target))
