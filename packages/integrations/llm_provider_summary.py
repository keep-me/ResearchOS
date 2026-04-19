from __future__ import annotations

import logging
from typing import Any

from packages.integrations import llm_provider_registry
from packages.integrations.llm_provider_schema import ResolvedModelTarget

logger = logging.getLogger(__name__)


def _prefer_stream_summary_for_openai_compatible(target: ResolvedModelTarget) -> bool:
    provider = str(target.provider or "").strip().lower()
    model = str(target.model or "").strip().lower()
    base_url = str(target.base_url or "").strip().lower()
    if provider == "custom" and model.startswith("gpt-5"):
        return True
    if provider == "custom" and "codex" in base_url:
        return True
    return False


def _collect_openai_compatible_stream_fallback(
    client,
    *,
    prompt: str,
    cfg,
    resolved: ResolvedModelTarget,
    max_tokens: int | None,
) -> tuple[str, str, int | None, int | None, int | None] | None:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None

    try:
        for event in client._chat_stream_openai_compatible(
            [{"role": "user", "content": prompt}],
            None,
            max_tokens or 4096,
            cfg,
            target=resolved,
            session_cache_key=None,
        ):
            event_type = str(getattr(event, "type", "") or "").strip().lower()
            if event_type == "text_delta":
                delta = str(getattr(event, "content", "") or "")
                if delta:
                    text_parts.append(delta)
                continue
            if event_type == "reasoning_delta":
                delta = str(getattr(event, "content", "") or "")
                if delta:
                    reasoning_parts.append(delta)
                continue
            if event_type == "usage":
                input_tokens = getattr(event, "input_tokens", None) or input_tokens
                output_tokens = getattr(event, "output_tokens", None) or output_tokens
                reasoning_tokens = getattr(event, "reasoning_tokens", None) or reasoning_tokens
    except Exception as exc:
        logger.info("openai-compatible stream fallback failed: %s", exc)
        return None

    text = "".join(text_parts).strip()
    reasoning = "".join(reasoning_parts).strip()
    if not text and not reasoning:
        return None
    return text, reasoning, input_tokens, output_tokens, reasoning_tokens


def call_openai_responses(
    client,
    result_cls,
    *,
    prompt: str,
    stage: str,
    cfg,
    model_override: str | None = None,
    variant_override: str | None = None,
    target: ResolvedModelTarget | None = None,
    max_tokens: int | None = None,
    request_timeout: float | None = None,
    allow_compatible_fallback: bool = True,
):
    resolved = target or client._resolve_model_target(
        stage,
        model_override,
        variant_override=variant_override,
        cfg=cfg,
    )
    try:
        model = resolved.model
        sdk_client = llm_provider_registry.get_openai_client(
            resolved.api_key or "",
            resolved.base_url,
            timeout=request_timeout,
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                    ],
                }
            ],
            "store": False,
        }
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        client._apply_variant_to_responses_kwargs(kwargs, resolved)
        response = sdk_client.responses.create(**kwargs)
        content, reasoning = client._extract_responses_text_and_reasoning(response)
        in_tokens, out_tokens = client._extract_responses_usage(response)
        in_cost, out_cost = client._estimate_cost(
            model=model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        return result_cls(
            content=(content or reasoning or "").strip(),
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=in_cost + out_cost,
            reasoning_content=reasoning or None,
        )
    except Exception as exc:
        last_exc = exc
        if client._should_try_raw_openai_http_fallback(resolved, exc):
            try:
                return client._call_openai_responses_raw_http(
                    prompt=prompt,
                    resolved=resolved,
                    max_tokens=max_tokens,
                    request_timeout=request_timeout,
                )
            except Exception as raw_exc:
                logger.warning(
                    "OpenAI responses raw HTTP fallback failed: %s",
                    raw_exc,
                )
                last_exc = raw_exc
        if not allow_compatible_fallback:
            raise last_exc
        fallback = client._call_openai_compatible(
            prompt,
            stage,
            cfg,
            model_override=model_override,
            variant_override=variant_override,
            target=resolved,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )
        lower = (fallback.content or "").lower()
        if any(token in lower for token in ("模型服务暂不可用", "api key 无效", "connection error", "模型连接异常")):
            logger.warning("OpenAI responses call failed: %s", last_exc)
        else:
            logger.info("OpenAI responses unsupported/unavailable, switched to chat.completions: %s", last_exc)
        return fallback


def call_openai_compatible(
    client,
    result_cls,
    *,
    prompt: str,
    stage: str,
    cfg,
    model_override: str | None = None,
    variant_override: str | None = None,
    target: ResolvedModelTarget | None = None,
    max_tokens: int | None = None,
    request_timeout: float | None = None,
):
    resolved = target or client._resolve_model_target(
        stage,
        model_override,
        variant_override=variant_override,
        cfg=cfg,
    )
    try:
        model = resolved.model
        sdk_client = llm_provider_registry.get_openai_client(
            resolved.api_key or "",
            resolved.base_url,
            timeout=request_timeout,
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        client._apply_variant_to_chat_kwargs(kwargs, resolved)
        if _prefer_stream_summary_for_openai_compatible(resolved):
            stream_fallback = _collect_openai_compatible_stream_fallback(
                client,
                prompt=prompt,
                cfg=cfg,
                resolved=resolved,
                max_tokens=max_tokens,
            )
            if stream_fallback is not None:
                content, reasoning_content, in_tokens, out_tokens, reasoning_tokens = stream_fallback
                in_cost, out_cost = client._estimate_cost(
                    model=model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                )
                return result_cls(
                    content=content,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    input_cost_usd=in_cost,
                    output_cost_usd=out_cost,
                    total_cost_usd=in_cost + out_cost,
                    reasoning_tokens=reasoning_tokens,
                    reasoning_content=reasoning_content if reasoning_content else None,
                )
        response = sdk_client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        content = client._coerce_openai_message_text(getattr(msg, "content", None))
        reasoning_content = client._extract_chat_reasoning_text(msg)
        if not content and reasoning_content:
            content = reasoning_content
        usage = response.usage
        in_tokens = usage.prompt_tokens if usage else None
        out_tokens = usage.completion_tokens if usage else None
        reasoning_tokens = client._extract_reasoning_tokens(usage)
        if not content and not reasoning_content:
            fallback = _collect_openai_compatible_stream_fallback(
                client,
                prompt=prompt,
                cfg=cfg,
                resolved=resolved,
                max_tokens=max_tokens,
            )
            if fallback is not None:
                content, reasoning_content, stream_in, stream_out, stream_reasoning = fallback
                in_tokens = stream_in if stream_in is not None else in_tokens
                out_tokens = stream_out if stream_out is not None else out_tokens
                reasoning_tokens = stream_reasoning if stream_reasoning is not None else reasoning_tokens
            else:
                return client._pseudo_summary(
                    prompt,
                    stage,
                    cfg,
                    model_override,
                    variant_override=variant_override,
                    reason="empty chat completion content",
                    target=resolved,
                )
        in_cost, out_cost = client._estimate_cost(
            model=model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        return result_cls(
            content=content,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=in_cost + out_cost,
            reasoning_tokens=reasoning_tokens,
            reasoning_content=reasoning_content if reasoning_content else None,
        )
    except Exception as exc:
        last_exc = exc
        if client._should_try_openai_responses_fallback(resolved, exc):
            try:
                return client._call_openai_responses(
                    prompt,
                    stage,
                    cfg,
                    model_override=model_override,
                    variant_override=variant_override,
                    target=resolved,
                    max_tokens=max_tokens,
                    request_timeout=request_timeout,
                    allow_compatible_fallback=False,
                )
            except Exception as responses_exc:
                logger.warning(
                    "OpenAI-compatible chat.completions rejected legacy protocol; responses fallback failed: %s",
                    responses_exc,
                )
                last_exc = responses_exc
        if client._should_try_raw_openai_http_fallback(resolved, last_exc):
            try:
                raw_result, _ = client._call_openai_chat_raw_http(
                    messages=[{"role": "user", "content": prompt}],
                    resolved=resolved,
                    max_tokens=max_tokens,
                    request_timeout=request_timeout,
                )
                logger.info(
                    "OpenAI-compatible SDK call blocked; switched to raw HTTP transport for %s",
                    resolved.base_url,
                )
                return raw_result
            except Exception as raw_exc:
                logger.warning(
                    "OpenAI-compatible raw HTTP fallback failed: %s",
                    raw_exc,
                )
                last_exc = raw_exc
        logger.warning("OpenAI-compatible call failed: %s", last_exc)
        return client._pseudo_summary(
            prompt,
            stage,
            cfg,
            model_override,
            variant_override=variant_override,
            reason=str(last_exc),
            target=resolved,
        )


def call_anthropic(
    client,
    result_cls,
    *,
    prompt: str,
    stage: str,
    cfg,
    model_override: str | None = None,
    variant_override: str | None = None,
    target: ResolvedModelTarget | None = None,
    max_tokens: int | None = None,
):
    try:
        resolved = target or client._resolve_model_target(
            stage,
            model_override,
            variant_override=variant_override,
            cfg=cfg,
        )
        model = resolved.model
        sdk_client = llm_provider_registry.get_anthropic_client(
            resolved.api_key or "",
            base_url=resolved.base_url,
        )
        response = sdk_client.messages.create(
            model=model,
            max_tokens=max_tokens or 4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text_blocks: list[str] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text_blocks.append(getattr(block, "text", ""))
        content = "\n".join(text_blocks).strip()
        usage = getattr(response, "usage", None)
        in_tokens = getattr(usage, "input_tokens", None)
        out_tokens = getattr(usage, "output_tokens", None)
        in_cost, out_cost = client._estimate_cost(
            model=model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        return result_cls(
            content=content,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=in_cost + out_cost,
        )
    except Exception as exc:
        return client._pseudo_summary(
            prompt,
            stage,
            cfg,
            model_override,
            variant_override=variant_override,
            reason=str(exc),
            target=target,
        )
