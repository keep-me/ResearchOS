"""Shared runtime policy for native/claw loop budgets and compaction."""

from __future__ import annotations

import json
import re
from typing import Any

from packages.config import get_settings

STEP_LIMIT_SUMMARY_PROMPT = (
    "你已达到本轮工具步骤预算上限。"
    "不要继续调用任何工具，也不要假装执行了额外操作。"
    "请仅用简体中文输出当前结果总结，并严格包含以下四部分："
    "1. 已完成内容"
    "2. 当前已获得的关键信息或中间结果"
    "3. 尚未完成内容"
    "4. 建议下一步"
    "如果前面有工具失败、网页抓取失败或信息不足，请明确写出。"
)

CLAW_AUTO_COMPACTION_THRESHOLD_ENV_VAR = "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS"

_DEFAULT_MAX_TOOL_STEPS = 20
_DEFAULT_AUTO_COMPACTION_INPUT_TOKENS_THRESHOLD = 100_000
# Effectively disables claw/native preflight auto-compaction without relying on
# unsupported "off" semantics in the vendored claw env parser.
_DISABLED_AUTO_COMPACTION_INPUT_TOKENS_THRESHOLD = 2_000_000_000

_TOOL_PROGRESS_PLACEHOLDER_PATTERNS = (
    re.compile(
        r"^(?:好的[，,\s]*)?(?:我(?:先|来|会先|先帮你|先去|来帮你)|让我|正在|我正在|稍等|马上)"
        r"(?:.{0,96})$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:ok(?:ay)?[,\s]*)?(?:let me|i(?:'ll| will)|working on it|searching|checking|looking up|analyzing|continuing)"
        r"(?:.{0,120})$",
        re.IGNORECASE,
    ),
)
_TOOL_PROGRESS_RESULT_MARKERS = (
    "已完成",
    "完成了",
    "完成以下",
    "结果",
    "结论",
    "总结",
    "如下",
    "建议",
    "已找到",
    "发现",
    "根据",
    "可以看出",
    "found",
    "result",
    "results",
    "summary",
    "conclusion",
    "completed",
    "based on",
)


def _setting(name: str, default: Any) -> Any:
    try:
        return getattr(get_settings(), name)
    except Exception:
        return default


def normalize_reasoning_level(reasoning_level: str | None = None) -> str:
    normalized = str(reasoning_level or "default").strip().lower()
    return normalized or "default"


def get_max_tool_steps(reasoning_level: str | None = None) -> int:
    try:
        configured = int(_setting("agent_max_tool_steps", _DEFAULT_MAX_TOOL_STEPS) or 0)
    except Exception:
        configured = _DEFAULT_MAX_TOOL_STEPS
    base = max(configured, 1)
    normalized = normalize_reasoning_level(reasoning_level)
    if normalized == "low":
        return max(6, min(base, 10))
    if normalized == "high":
        return min(max(base, 30), 40)
    return base


def resolve_max_tool_steps(reasoning_level: str | None = None) -> int:
    try:
        return get_max_tool_steps(reasoning_level)
    except TypeError:
        return get_max_tool_steps()


def build_reasoning_profile_prompt(
    reasoning_level: str | None = None,
    *,
    max_steps: int | None = None,
) -> str:
    normalized = normalize_reasoning_level(reasoning_level)
    budget = max_steps if max_steps is not None else resolve_max_tool_steps(normalized)
    if normalized == "low":
        return (
            f"Reasoning profile: low. Tool budget this turn: {budget} steps. "
            "Prefer the shortest viable tool chain, reuse existing evidence, avoid broad or repeated searches once you can answer, "
            "and do not use bash for exploration when read/glob/grep already cover the task."
        )
    if normalized == "high":
        return (
            f"Reasoning profile: high. Tool budget this turn: {budget} steps. "
            "You may spend extra steps on validation, cross-checking, comparing alternatives, and using bash for precise verification when that materially reduces uncertainty."
        )
    if normalized == "medium":
        return (
            f"Reasoning profile: medium. Tool budget this turn: {budget} steps. "
            "Use a balanced amount of exploration; go deeper only when the current evidence is insufficient."
        )
    return (
        f"Reasoning profile: default. Tool budget this turn: {budget} steps. "
        "Keep the search chain efficient and avoid repeating the same search once you have useful evidence."
    )


def should_inject_max_steps_prompt(current_step: int, max_steps: int) -> bool:
    return int(current_step) + 1 >= max(int(max_steps or 0), 1)


def should_hard_stop_after_tool_request(
    current_step: int,
    max_steps: int,
    *,
    requested_tool_calls: bool | int = False,
) -> bool:
    return bool(requested_tool_calls) and should_inject_max_steps_prompt(current_step, max_steps)


def build_step_limit_reached_notice(max_steps: int) -> str:
    return (
        f"已达到本轮工具步骤上限（{max(int(max_steps or 0), 1)} 步），"
        "我先停止继续调用工具，并基于当前结果给你一个总结。"
    )


def tool_call_signature(tool_name: str | None, arguments: Any = None) -> str:
    normalized_name = str(tool_name or "").strip() or "tool"
    try:
        serialized_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except Exception:
        serialized_arguments = str(arguments)
    return f"{normalized_name}:{serialized_arguments}"


def should_hard_stop_after_repeated_tool_calls(
    current_step: int,
    previous_signatures: list[str] | tuple[str, ...] | None,
    requested_signatures: list[str] | tuple[str, ...] | None,
) -> bool:
    previous = tuple(
        str(item or "").strip() for item in (previous_signatures or []) if str(item or "").strip()
    )
    current = tuple(
        str(item or "").strip() for item in (requested_signatures or []) if str(item or "").strip()
    )
    return current_step > 0 and bool(current) and current == previous


def build_repeated_tool_call_notice() -> str:
    return "检测到模型连续重复请求相同工具调用，我先停止重复执行，并基于当前结果给你一个总结。"


def is_tool_progress_placeholder_text(text: str | None) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if "\n" in raw or len(raw) > 160:
        return False

    lowered = raw.lower()
    if any(marker in lowered for marker in _TOOL_PROGRESS_RESULT_MARKERS):
        return False

    return any(pattern.match(raw) for pattern in _TOOL_PROGRESS_PLACEHOLDER_PATTERNS)


def is_auto_compaction_enabled() -> bool:
    return bool(_setting("agent_compaction_auto", True))


def get_auto_compaction_input_tokens_threshold() -> int:
    if not is_auto_compaction_enabled():
        return _DISABLED_AUTO_COMPACTION_INPUT_TOKENS_THRESHOLD

    try:
        context_window = int(
            _setting(
                "agent_compaction_fallback_context_window",
                _DEFAULT_AUTO_COMPACTION_INPUT_TOKENS_THRESHOLD,
            )
            or 0
        )
    except Exception:
        context_window = _DEFAULT_AUTO_COMPACTION_INPUT_TOKENS_THRESHOLD
    try:
        reserved_tokens = int(_setting("agent_compaction_reserved_tokens", 0) or 0)
    except Exception:
        reserved_tokens = 0

    threshold = context_window - max(reserved_tokens, 0)
    if threshold > 0:
        return threshold
    return _DEFAULT_AUTO_COMPACTION_INPUT_TOKENS_THRESHOLD


def apply_claw_runtime_policy_env(env: dict[str, str]) -> dict[str, str]:
    updated = dict(env)
    updated[CLAW_AUTO_COMPACTION_THRESHOLD_ENV_VAR] = str(
        get_auto_compaction_input_tokens_threshold()
    )
    return updated
