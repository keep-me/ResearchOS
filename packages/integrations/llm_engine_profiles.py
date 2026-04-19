from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from packages.storage.db import session_scope
from packages.storage.repositories import LLMConfigRepository

_PROFILE_PREFIX = "llmcfg"
_CHANNEL_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("deep", "model_deep", "精读", "medium"),
    ("skim", "model_skim", "粗读", "low"),
    ("fallback", "model_fallback", "回退", "low"),
    ("vision", "model_vision", "视觉", "medium"),
)


def build_llm_engine_profile_id(config_id: str, channel: str) -> str:
    return f"{_PROFILE_PREFIX}:{str(config_id).strip()}:{str(channel).strip().lower()}"


def parse_llm_engine_profile_id(value: str | None) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 3 or parts[0] != _PROFILE_PREFIX:
        return None
    config_id = str(parts[1]).strip()
    channel = str(parts[2]).strip().lower()
    if not config_id or channel not in {item[0] for item in _CHANNEL_SPECS}:
        return None
    return config_id, channel


def list_llm_engine_profiles(session: Session | None = None) -> list[dict[str, Any]]:
    def _build(current_session: Session) -> list[dict[str, Any]]:
        repo = LLMConfigRepository(current_session)
        items: list[dict[str, Any]] = []
        for config in repo.list_all():
            items.extend(_profiles_for_config(config))
        return items

    if session is not None:
        return _build(session)
    with session_scope() as current_session:
        return _build(current_session)


def resolve_llm_engine_profile(
    profile_id: str | None,
    *,
    session: Session | None = None,
) -> dict[str, Any] | None:
    parsed = parse_llm_engine_profile_id(profile_id)
    if parsed is None:
        return None
    config_id, channel = parsed

    def _resolve(current_session: Session) -> dict[str, Any] | None:
        repo = LLMConfigRepository(current_session)
        try:
            config = repo.get_by_id(config_id)
        except ValueError:
            return None
        for item in _profiles_for_config(config, include_runtime_config=True):
            if str(item.get("id")) == build_llm_engine_profile_id(config_id, channel):
                return item
        return None

    if session is not None:
        return _resolve(session)
    with session_scope() as current_session:
        return _resolve(current_session)


def recommend_llm_engine_profiles(
    profiles: list[dict[str, Any]] | None,
) -> dict[str, str | None]:
    ordered = [dict(item) for item in (profiles or []) if isinstance(item, dict)]
    executor = _pick_profile(
        ordered,
        [
            lambda item: bool(item.get("is_active")) and item.get("channel") == "deep",
            lambda item: bool(item.get("is_active")) and item.get("channel") == "skim",
            lambda item: item.get("channel") == "deep",
            lambda item: item.get("channel") == "skim",
            lambda item: item.get("channel") == "fallback",
            lambda item: True,
        ],
    )
    reviewer = _pick_profile(
        ordered,
        [
            lambda item: bool(item.get("is_active")) and item.get("provider") == "openai" and item.get("channel") == "deep",
            lambda item: item.get("provider") == "openai" and item.get("channel") == "deep",
            lambda item: bool(item.get("is_active")) and item.get("channel") == "deep",
            lambda item: item.get("channel") == "deep",
            lambda item: item.get("channel") == "fallback",
            lambda item: True,
        ],
    )
    return {
        "executor_engine_id": str(executor.get("id")) if executor else None,
        "reviewer_engine_id": str((reviewer or executor or {}).get("id")) if (reviewer or executor) else None,
    }


def public_engine_profile_payload(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(profile, dict):
        return None
    return {
        "id": profile.get("id"),
        "config_id": profile.get("config_id"),
        "config_name": profile.get("config_name"),
        "provider": profile.get("provider"),
        "channel": profile.get("channel"),
        "channel_label": profile.get("channel_label"),
        "label": profile.get("label"),
        "model": profile.get("model"),
        "default_variant": profile.get("default_variant"),
        "is_active": bool(profile.get("is_active")),
    }


def _pick_profile(
    items: list[dict[str, Any]],
    selectors: list,
) -> dict[str, Any] | None:
    for selector in selectors:
        for item in items:
            try:
                if selector(item):
                    return item
            except Exception:
                continue
    return None


def _profiles_for_config(config, *, include_runtime_config: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for channel, field_name, channel_label, default_variant in _CHANNEL_SPECS:
        model = str(getattr(config, field_name, "") or "").strip()
        if not model:
            continue
        payload: dict[str, Any] = {
            "id": build_llm_engine_profile_id(config.id, channel),
            "config_id": config.id,
            "config_name": config.name,
            "provider": str(getattr(config, "provider", "") or "").strip().lower(),
            "channel": channel,
            "channel_label": channel_label,
            "label": f"{config.name} · {channel_label}",
            "model": model,
            "default_variant": default_variant,
            "is_active": bool(getattr(config, "is_active", False)),
        }
        if include_runtime_config:
            payload["runtime_config"] = {
                "provider": str(getattr(config, "provider", "") or "").strip(),
                "api_key": str(getattr(config, "api_key", "") or "").strip() or None,
                "api_base_url": str(getattr(config, "api_base_url", "") or "").strip() or None,
                "model_skim": str(getattr(config, "model_skim", "") or "").strip(),
                "model_deep": str(getattr(config, "model_deep", "") or "").strip(),
                "model_vision": str(getattr(config, "model_vision", "") or "").strip() or None,
                "embedding_provider": str(getattr(config, "embedding_provider", "") or "").strip() or None,
                "embedding_api_key": str(getattr(config, "embedding_api_key", "") or "").strip() or None,
                "embedding_api_base_url": str(getattr(config, "embedding_api_base_url", "") or "").strip() or None,
                "model_embedding": str(getattr(config, "model_embedding", "") or "").strip(),
                "model_fallback": str(getattr(config, "model_fallback", "") or "").strip(),
            }
        items.append(payload)
    return items
