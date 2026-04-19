"""Helpers for attaching token usage to task status payloads."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.storage.db import session_scope
from packages.storage.models import PromptTrace


_TASK_STAGE_MAP: dict[str, tuple[str, ...]] = {
    "skim": ("skim",),
    "deep": ("deep", "deep_dive"),
    "deep_read": ("deep", "deep_dive"),
    "embed": ("embed",),
    "figure": ("paper_reader_figure", "vision_figure"),
    "paper_reader_figure": ("paper_reader_figure", "vision_figure"),
    "writing": ("writing", "writing_refine", "writing_vision"),
    "topic_wiki": ("wiki_outline", "wiki_overview", "wiki_section", "wiki_summary"),
}


def enrich_tasks_with_token_usage(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return task payloads with a best-effort token_usage object attached."""

    if not tasks:
        return []

    with session_scope() as session:
        return [enrich_task_with_token_usage(session, task) for task in tasks]


def enrich_task_with_token_usage(session: Session, task: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(task)
    usage = _usage_from_task_payload(enriched)
    if usage is None or int(usage.get("total_tokens") or 0) <= 0:
        usage = _usage_from_prompt_traces(session, enriched)
    enriched["token_usage"] = usage or _empty_usage(enriched)
    return enriched


def _usage_from_task_payload(task: dict[str, Any]) -> dict[str, Any] | None:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    candidates = [
        task.get("token_usage"),
        metadata.get("token_usage"),
        metadata.get("usage"),
        metadata.get("tokens"),
    ]
    for candidate in candidates:
        usage = _normalize_usage(candidate)
        if usage is not None:
            usage["source"] = "metadata"
            usage["category"] = _category_for_task(task, usage.get("stage"))
            return usage
    return None


def _usage_from_prompt_traces(session: Session, task: dict[str, Any]) -> dict[str, Any] | None:
    stages = _stage_candidates(task)
    start, end = _task_time_bounds(task)
    if start is None or end is None:
        return None

    statement = select(
        func.count(PromptTrace.id),
        func.coalesce(func.sum(PromptTrace.input_tokens), 0),
        func.coalesce(func.sum(PromptTrace.output_tokens), 0),
        func.coalesce(func.sum(PromptTrace.total_cost_usd), 0.0),
    ).where(PromptTrace.created_at >= start, PromptTrace.created_at <= end)

    if stages:
        statement = statement.where(PromptTrace.stage.in_(stages))

    paper_id = _task_paper_id(task)
    if paper_id:
        statement = statement.where(PromptTrace.paper_id == paper_id)
    else:
        paper_prefix = _paper_prefix_from_task_id(task.get("task_id"))
        if paper_prefix:
            statement = statement.where(PromptTrace.paper_id.like(f"{paper_prefix}%"))
        elif not stages:
            return None

    calls, input_tokens, output_tokens, total_cost = session.execute(statement).one()
    input_value = int(input_tokens or 0)
    output_value = int(output_tokens or 0)
    return {
        "input_tokens": input_value,
        "output_tokens": output_value,
        "reasoning_tokens": 0,
        "total_tokens": input_value + output_value,
        "total_cost_usd": float(total_cost or 0.0),
        "calls": int(calls or 0),
        "stage": ",".join(stages) if stages else None,
        "source": "prompt_trace" if int(calls or 0) > 0 else "unavailable",
        "category": _category_for_task(task, stages[0] if stages else None),
    }


def _normalize_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tokens = value.get("tokens") if isinstance(value.get("tokens"), dict) else value
    input_tokens = _to_int(tokens.get("input_tokens", tokens.get("input", tokens.get("prompt_tokens"))))
    output_tokens = _to_int(tokens.get("output_tokens", tokens.get("output", tokens.get("completion_tokens"))))
    reasoning_tokens = _to_int(tokens.get("reasoning_tokens", tokens.get("reasoning")))
    total_tokens = _to_int(tokens.get("total_tokens", tokens.get("total")))
    computed_total = input_tokens + output_tokens + reasoning_tokens
    if total_tokens <= 0:
        total_tokens = computed_total
    if total_tokens <= 0 and computed_total <= 0:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": max(total_tokens, computed_total),
        "total_cost_usd": float(value.get("total_cost_usd") or value.get("cost_usd") or 0.0),
        "calls": _to_int(value.get("calls")) or (1 if max(total_tokens, computed_total) > 0 else 0),
        "stage": value.get("stage"),
    }


def _empty_usage(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "calls": 0,
        "stage": None,
        "source": "unavailable",
        "category": _category_for_task(task, None),
    }


def _stage_candidates(task: dict[str, Any]) -> tuple[str, ...]:
    task_type = str(task.get("task_type") or "").strip().lower()
    if task_type in _TASK_STAGE_MAP:
        return _TASK_STAGE_MAP[task_type]
    if task_type.startswith("wiki"):
        return _TASK_STAGE_MAP["topic_wiki"]
    if task_type.startswith("writing"):
        return _TASK_STAGE_MAP["writing"]
    return (task_type,) if task_type else ()


def _task_paper_id(task: dict[str, Any]) -> str | None:
    direct = str(task.get("paper_id") or "").strip()
    if direct:
        return direct
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    for key in ("paper_id", "source_id"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return None


def _paper_prefix_from_task_id(task_id: Any) -> str | None:
    match = re.match(r"^(?:skim|deep|embed)_([0-9a-fA-F]{8})_", str(task_id or ""))
    return match.group(1).lower() if match else None


def _task_time_bounds(task: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    started = _timestamp_to_datetime(task.get("created_at"))
    if started is None:
        return None, None
    finished = _timestamp_to_datetime(task.get("finished_at") or task.get("updated_at")) or datetime.now()
    return started - timedelta(minutes=2), max(finished, started) + timedelta(minutes=15)


def _timestamp_to_datetime(value: Any) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # SQLite drops timezone info for existing PromptTrace rows in this project,
    # so compare with local naive datetimes to match stored task/trace clocks.
    return datetime.fromtimestamp(number)


def _to_int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _category_for_task(task: dict[str, Any], stage: Any) -> str:
    text = f"{task.get('task_type') or ''} {stage or ''}".lower()
    if "skim" in text:
        return "论文粗读"
    if "deep" in text or "paper_round" in text:
        return "论文精读"
    if "embed" in text:
        return "向量化"
    if "vision" in text or "figure" in text:
        return "图表理解"
    if "wiki" in text or "writing" in text:
        return "写作生成"
    if "graph" in text or "reasoning" in text:
        return "研究洞察"
    if "keyword" in text or "topic" in text:
        return "主题发现"
    return "其他任务"
