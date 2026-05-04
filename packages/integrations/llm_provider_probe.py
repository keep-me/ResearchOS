from __future__ import annotations

import time
from typing import Any

from packages.integrations import llm_provider_error, llm_provider_registry
from packages.integrations.llm_provider_schema import (
    ResolvedEmbeddingConfig,
    ResolvedModelTarget,
)


def _probe_failure_result(
    *,
    provider: str,
    model: str,
    base_url: str | None,
    transport: str,
    started_at: float,
    error: Exception,
    provider_id: str | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return llm_provider_error.runtime_failure_result(
        provider=provider,
        model=model,
        base_url=base_url,
        transport=transport,
        message=str(error),
        latency_ms=round((time.perf_counter() - started_at) * 1000),
        error=error,
        provider_id=provider_id,
        attempts=attempts,
    )


def probe_openai_chat(client: Any, target: ResolvedModelTarget) -> dict[str, Any]:
    provider = target.provider
    model = target.model
    base_url = target.base_url
    start = time.perf_counter()
    sdk_client = llm_provider_registry.get_openai_client(
        target.api_key or "",
        base_url,
        timeout=30,
    )
    responses_exc: Exception | None = None
    attempts: list[dict[str, Any]] = []

    try:
        kwargs = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "只回复OK"}],
                }
            ],
            "store": False,
            "max_output_tokens": 16,
        }
        client._apply_variant_to_responses_kwargs(kwargs, target)
        response = sdk_client.responses.create(**kwargs)
        content, reasoning = client._extract_responses_text_and_reasoning(response)
        preview = (content or reasoning or "").strip() or "[空响应]"
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "transport": "responses",
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "preview": preview[:160],
            "message": "聊天测试成功（Responses API）。",
        }
    except Exception as exc:
        responses_exc = exc
        attempts.append(
            llm_provider_error.error_attempt(
                exc,
                provider_id=provider,
                transport="responses",
            )
        )
        if client._should_try_raw_openai_http_fallback(target, exc):
            try:
                raw_result = client._call_openai_responses_raw_http(
                    prompt="只回复OK",
                    resolved=target,
                    max_tokens=16,
                    request_timeout=30,
                )
                return {
                    "ok": True,
                    "provider": provider,
                    "model": model,
                    "base_url": base_url,
                    "transport": "responses(raw-http)",
                    "latency_ms": round((time.perf_counter() - start) * 1000),
                    "preview": (raw_result.content or "[空响应]")[:160],
                    "message": "聊天测试成功（Responses API，raw HTTP 回退）。",
                }
            except Exception as raw_exc:
                responses_exc = raw_exc
                attempts.append(
                    llm_provider_error.error_attempt(
                        raw_exc,
                        provider_id=provider,
                        transport="responses(raw-http)",
                    )
                )

    try:
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": "只回复OK"}],
            "max_tokens": 16,
        }
        client._apply_variant_to_chat_kwargs(kwargs, target)
        response = sdk_client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        preview = (msg.content or getattr(msg, "reasoning_content", "") or "").strip() or "[空响应]"
        message = "聊天测试成功（chat.completions）。"
        if responses_exc is not None:
            message = (
                "聊天测试成功（已自动回退到 chat.completions；"
                f"Responses 不兼容：{responses_exc}）。"
            )
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "transport": "chat.completions",
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "preview": preview[:160],
            "message": message,
        }
    except Exception as exc:
        prior_attempts = list(attempts)
        if client._should_try_raw_openai_http_fallback(target, exc):
            prior_attempts.append(
                llm_provider_error.error_attempt(
                    exc,
                    provider_id=provider,
                    transport="chat.completions",
                )
            )
            try:
                raw_result, _ = client._call_openai_chat_raw_http(
                    messages=[{"role": "user", "content": "只回复OK"}],
                    resolved=target,
                    max_tokens=16,
                    request_timeout=30,
                )
                message = "聊天测试成功（chat.completions，raw HTTP 回退）。"
                if responses_exc is not None:
                    message = (
                        "聊天测试成功（已自动回退到 raw HTTP chat.completions；"
                        f"Responses 不兼容：{responses_exc}）。"
                    )
                return {
                    "ok": True,
                    "provider": provider,
                    "model": model,
                    "base_url": base_url,
                    "transport": "chat.completions(raw-http)",
                    "latency_ms": round((time.perf_counter() - start) * 1000),
                    "preview": (raw_result.content or "[空响应]")[:160],
                    "message": message,
                }
            except Exception as raw_exc:
                exc = raw_exc
        detail = _probe_failure_result(
            provider=provider,
            model=model,
            base_url=base_url,
            transport="chat.completions",
            started_at=start,
            error=exc,
            provider_id=provider,
            attempts=prior_attempts,
        )
        return detail


def probe_openai_compatible_chat(client: Any, target: ResolvedModelTarget) -> dict[str, Any]:
    provider = target.provider
    model = target.model
    base_url = target.base_url
    start = time.perf_counter()
    try:
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": "只回复OK"}],
            "max_tokens": 16,
        }
        client._apply_variant_to_chat_kwargs(kwargs, target)
        response = llm_provider_registry.get_openai_client(
            target.api_key or "",
            base_url,
            timeout=30,
        ).chat.completions.create(**kwargs)
        msg = response.choices[0].message
        preview = (msg.content or getattr(msg, "reasoning_content", "") or "").strip() or "[空响应]"
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "transport": "chat.completions",
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "preview": preview[:160],
            "message": "聊天测试成功。",
        }
    except Exception as exc:
        should_try_responses_fallback = getattr(
            client, "_should_try_openai_responses_fallback", None
        )
        if callable(should_try_responses_fallback) and should_try_responses_fallback(target, exc):
            result = probe_openai_chat(client, target)
            if result.get("ok"):
                return result
        should_try_raw_http_fallback = getattr(client, "_should_try_raw_openai_http_fallback", None)
        if callable(should_try_raw_http_fallback) and should_try_raw_http_fallback(target, exc):
            try:
                raw_result, _ = client._call_openai_chat_raw_http(
                    messages=[{"role": "user", "content": "只回复OK"}],
                    resolved=target,
                    max_tokens=16,
                    request_timeout=30,
                )
                return {
                    "ok": True,
                    "provider": provider,
                    "model": model,
                    "base_url": base_url,
                    "transport": "chat.completions(raw-http)",
                    "latency_ms": round((time.perf_counter() - start) * 1000),
                    "preview": (raw_result.content or "[空响应]")[:160],
                    "message": "聊天测试成功（raw HTTP 回退）。",
                }
            except Exception as raw_exc:
                exc = raw_exc
        return _probe_failure_result(
            provider=provider,
            model=model,
            base_url=base_url,
            transport="chat.completions",
            started_at=start,
            error=exc,
            provider_id=provider,
        )


def probe_anthropic_chat(
    client: Any,
    cfg: Any,
    target: ResolvedModelTarget,
) -> dict[str, Any]:
    del client, cfg
    provider = target.provider
    model = target.model
    base_url = target.base_url
    start = time.perf_counter()
    try:
        response = llm_provider_registry.get_anthropic_client(
            target.api_key or "",
            base_url=target.base_url,
            timeout=30,
        ).messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "只回复OK"}],
        )
        text_blocks: list[str] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text_blocks.append(getattr(block, "text", ""))
        preview = "\n".join(text_blocks).strip() or "[空响应]"
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "transport": "anthropic.messages",
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "preview": preview[:160],
            "message": "聊天测试成功。",
        }
    except Exception as exc:
        return _probe_failure_result(
            provider=provider,
            model=model,
            base_url=base_url,
            transport="anthropic.messages",
            started_at=start,
            error=exc,
            provider_id=provider,
        )


def probe_embedding_openai_compatible(
    client: Any,
    cfg: Any,
    embedding_cfg: ResolvedEmbeddingConfig,
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        vector, used_model, used_base = client._embed_openai_compatible_or_raise(
            "ResearchOS embedding probe",
            cfg,
            embedding_cfg,
        )
        return {
            "ok": True,
            "provider": embedding_cfg.provider,
            "model": used_model,
            "base_url": used_base,
            "transport": "embeddings",
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "dimension": len(vector),
            "message": f"嵌入测试成功，向量维度 {len(vector)}。",
        }
    except Exception as exc:
        return _probe_failure_result(
            provider=embedding_cfg.provider or "",
            model=embedding_cfg.model,
            base_url=embedding_cfg.base_url,
            transport="embeddings",
            started_at=start,
            error=exc,
            provider_id=embedding_cfg.provider,
        )
