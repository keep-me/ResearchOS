from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve_chat_model_target(llm: Any, reasoning_level: str = "default") -> Any | None:
    resolver = getattr(llm, "_resolve_model_target", None)
    if not callable(resolver):
        return None
    try:
        return resolver("chat", None, variant_override=reasoning_level)
    except TypeError:
        try:
            return resolver("chat", None)
        except Exception:
            return None
    except Exception:
        return None


def is_official_openai_target(llm: Any, reasoning_level: str = "default") -> bool:
    target = resolve_chat_model_target(llm, reasoning_level)
    if target is None:
        return False
    checker = getattr(llm, "_is_official_openai_target", None)
    if callable(checker):
        try:
            return bool(checker(target))
        except Exception:
            logger.debug("Failed to evaluate official OpenAI target", exc_info=True)
    provider = str(getattr(target, "provider", "") or "").strip().lower()
    base_url = str(getattr(target, "base_url", "") or "").strip().lower()
    return provider == "openai" and (not base_url or "api.openai.com" in base_url)


def prefer_apply_patch_tool(llm: Any, reasoning_level: str = "default") -> bool | None:
    target = resolve_chat_model_target(llm, reasoning_level)
    if target is None:
        return None
    model = str(
        getattr(target, "model", "")
        or getattr(target, "model_id", "")
        or getattr(target, "modelID", "")
        or ""
    ).strip().lower()
    if not model:
        return None
    return "gpt-" in model and "oss" not in model and "gpt-4" not in model


def function_tool_name(entry: dict[str, Any]) -> str:
    if not isinstance(entry, dict) or str(entry.get("type") or "").strip() != "function":
        return ""
    function = entry.get("function")
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "").strip()
