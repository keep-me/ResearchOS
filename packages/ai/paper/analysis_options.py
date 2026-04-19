from __future__ import annotations

VALID_ANALYSIS_DETAIL_LEVELS = ("low", "medium", "high")
VALID_REASONING_LEVELS = ("default", "low", "medium", "high", "xhigh")


def normalize_analysis_detail_level(detail_level: str | None) -> str:
    normalized = str(detail_level or "medium").strip().lower()
    if normalized in VALID_ANALYSIS_DETAIL_LEVELS:
        return normalized
    return "medium"


def normalize_reasoning_level(reasoning_level: str | None) -> str:
    normalized = str(reasoning_level or "default").strip().lower()
    if normalized in VALID_REASONING_LEVELS:
        return normalized
    return "default"


def _detail_from_reasoning_level(reasoning_level: str | None) -> str:
    normalized_reasoning = normalize_reasoning_level(reasoning_level)
    if normalized_reasoning in VALID_ANALYSIS_DETAIL_LEVELS:
        return normalized_reasoning
    if normalized_reasoning == "xhigh":
        return "high"
    return "medium"


def resolve_paper_analysis_levels(
    detail_level: str | None,
    reasoning_level: str | None = None,
) -> tuple[str, str]:
    raw_detail = str(detail_level or "").strip().lower()
    if raw_detail in VALID_ANALYSIS_DETAIL_LEVELS:
        normalized_detail = raw_detail
    else:
        normalized_detail = _detail_from_reasoning_level(reasoning_level)
    # Paper-domain analysis now uses a single detail switch. Reasoning
    # complexity is synchronized to the same level to keep deep read,
    # reasoning-chain, and three-round analysis aligned.
    return normalized_detail, normalized_detail


def get_deep_detail_profile(detail_level: str | None) -> dict[str, int | str]:
    level = normalize_analysis_detail_level(detail_level)
    profiles = {
        "low": {
            "label": "低",
            "vision_pages": 4,
            "text_pages": 4,
            "text_chars": 3500,
            "max_tokens": 1400,
        },
        "medium": {
            "label": "中",
            "vision_pages": 8,
            "text_pages": 10,
            "text_chars": 8000,
            "max_tokens": 2200,
        },
        "high": {
            "label": "高",
            "vision_pages": 12,
            "text_pages": 14,
            "text_chars": 12000,
            "max_tokens": 3200,
        },
    }
    return {"level": level, **profiles[level]}


def get_reasoning_detail_profile(
    detail_level: str | None,
    *,
    base_pages: int,
    base_tokens: int,
    base_timeout: int,
) -> dict[str, int | str]:
    level = normalize_analysis_detail_level(detail_level)
    pages = max(2, int(base_pages))
    tokens = max(1024, int(base_tokens))
    timeout = max(30, int(base_timeout))
    profiles = {
        "low": {
            "label": "低",
            "pages": min(pages, 4),
            "excerpt_chars": 3200,
            "analysis_chars": 1200,
            "max_tokens": min(tokens, 2048),
            "timeout_seconds": max(30, int(timeout * 0.75)),
        },
        "medium": {
            "label": "中",
            "pages": pages,
            "excerpt_chars": 6000,
            "analysis_chars": 2000,
            "max_tokens": tokens,
            "timeout_seconds": timeout,
        },
        "high": {
            "label": "高",
            "pages": max(pages + 4, int(pages * 1.5)),
            "excerpt_chars": 9500,
            "analysis_chars": 3200,
            "max_tokens": max(tokens + 1024, 4096),
            "timeout_seconds": max(timeout + 30, int(timeout * 1.4)),
        },
    }
    return {"level": level, **profiles[level]}
