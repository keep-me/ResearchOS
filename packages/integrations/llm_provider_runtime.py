from __future__ import annotations

from collections.abc import Iterator
import re
from urllib.parse import urlparse

from packages.integrations import llm_provider_error, llm_provider_probe
from packages.integrations.llm_provider_http import ProviderHTTPError
from packages.integrations.llm_provider_schema import normalize_provider_name


def _runtime_failure(
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    transport: str,
    message: str,
    status_code: int | None = None,
) -> dict:
    return llm_provider_error.runtime_failure_result(
        provider=provider,
        model=model,
        base_url=base_url,
        transport=transport,
        message=message,
        provider_id=provider,
        status_code=status_code,
        metadata={"provider": provider, "transport": transport},
    )


def _vision_raw_openai_compatible(
    client,
    *,
    image_base64: str,
    prompt: str,
    max_tokens: int,
    target,
):
    result, _ = client._call_openai_chat_raw_http(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}",
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        resolved=target,
        max_tokens=max_tokens,
    )
    return result


def _vision_failure_message(*, target, exc: Exception | None = None) -> str:
    provider = normalize_provider_name(getattr(target, "provider", None))
    model = str(getattr(target, "model", "") or "").strip() or "未配置模型"
    base_url = str(getattr(target, "base_url", "") or "").strip()
    host = urlparse(base_url).netloc or base_url or provider or "当前提供方"
    detail = ""
    if isinstance(exc, ProviderHTTPError):
        detail = llm_provider_error.extract_response_error_message(exc.response_body, str(exc))
        response_body = str(exc.response_body or "")
        lowered_body = response_body.lower()
        if "<!doctype html" in lowered_body or "<html" in lowered_body:
            if exc.status_code == 502:
                detail = "上游视觉服务返回 502 Bad Gateway。"
            elif exc.status_code == 503:
                detail = "上游视觉服务暂时不可用（503）。"
            else:
                detail = f"上游视觉服务返回了网关错误页面（{exc.status_code or 'unknown'}）。"
    elif exc is not None:
        detail = str(exc).strip()

    lowered = detail.lower()
    match = re.search(r'"message"\s*:\s*"([^"]+)"', detail)
    if match:
        detail = match.group(1).strip()
        lowered = detail.lower()
    if "your request was blocked" in lowered or ("blocked" in lowered and "request" in lowered):
        detail = "当前上游网关拦截了图像请求。"
    elif "service temporarily unavailable" in lowered:
        detail = "上游视觉服务暂时不可用。"
    elif "bad gateway" in lowered:
        detail = "上游视觉服务返回 Bad Gateway。"
    elif "does not represent a valid image" in lowered:
        detail = "上游未能识别当前图片数据。"
    elif "invalid image" in lowered:
        detail = "上游拒绝了解析当前图片数据。"
    elif not detail:
        detail = "当前视觉请求没有拿到可用响应。"

    return (
        f"当前视觉模型不可用：{provider or 'unknown'} / {model} / {host}。"
        f"{detail} 请在系统设置中把“视觉模型”切换到支持图片输入的提供方或模型。"
    )


def _empty_vision_result_error() -> RuntimeError:
    return RuntimeError("上游视觉接口返回空内容。")


def _has_meaningful_vision_content(result) -> bool:
    if result is None:
        return False
    content = str(getattr(result, "content", "") or "").strip()
    reasoning = str(getattr(result, "reasoning_content", "") or "").strip()
    return bool(content or reasoning)


def _should_try_raw_vision_fallback(*, target) -> bool:
    base_url = str(getattr(target, "base_url", "") or "").strip().lower()
    return bool(base_url and "api.openai.com" not in base_url)


def _try_raw_vision_fallback(
    client,
    *,
    image_base64: str,
    prompt: str,
    max_tokens: int,
    target,
):
    if not _should_try_raw_vision_fallback(target=target):
        return None, None
    try:
        result = _vision_raw_openai_compatible(
            client,
            image_base64=image_base64,
            prompt=prompt,
            max_tokens=max_tokens,
            target=target,
        )
    except Exception as exc:
        return None, exc
    if _has_meaningful_vision_content(result):
        return result, None
    return None, _empty_vision_result_error()


def summarize_text(
    client,
    result_cls,
    *,
    prompt: str,
    stage: str,
    model_override: str | None = None,
    variant_override: str | None = None,
    max_tokens: int | None = None,
    request_timeout: float | None = None,
):
    cfg = client._config()
    target = client._resolve_model_target(
        stage,
        model_override,
        variant_override=variant_override,
        cfg=cfg,
    )
    dispatch = client._resolve_summary_dispatch(target)
    if dispatch.route == "openai-responses":
        return client._call_openai_responses(
            prompt,
            stage,
            cfg,
            model_override,
            variant_override=variant_override,
            target=target,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )
    if dispatch.route == "openai-compatible":
        return client._call_openai_compatible(
            prompt,
            stage,
            cfg,
            model_override,
            variant_override=variant_override,
            target=target,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )
    if dispatch.route == "anthropic":
        return client._call_anthropic(
            prompt,
            stage,
            cfg,
            model_override,
            variant_override=variant_override,
            target=target,
            max_tokens=max_tokens,
        )
    return client._pseudo_summary(
        prompt,
        stage,
        cfg,
        model_override,
        variant_override=variant_override,
        target=target,
        reason=dispatch.fallback_reason,
    )


def vision_analyze(
    client,
    result_cls,
    *,
    image_base64: str,
    prompt: str,
    stage: str = "vision",
    max_tokens: int = 1024,
):
    cfg = client._config()
    target = client._resolve_model_target(stage, None, cfg=cfg)
    provider = normalize_provider_name(target.provider)
    if provider in ("", "none"):
        return result_cls(content="未配置图像分析模型，请先在系统设置中创建并激活 LLM 配置。")
    if provider in {"openai", "custom"} and target.api_key:
        sdk_exc: Exception | None = None
        try:
            sdk_client = client._get_openai_client(target.api_key or "", target.base_url)
            sdk_result = client._vision_with_sdk_client(
                sdk_client=sdk_client,
                image_base64=image_base64,
                prompt=prompt,
                max_tokens=max_tokens,
                target=target,
            )
            if _has_meaningful_vision_content(sdk_result):
                return sdk_result
            sdk_exc = _empty_vision_result_error()
        except Exception as exc:
            sdk_exc = exc
        compat_exc: Exception | None = None
        fallback = client._vision_openai_compatible(
            image_base64=image_base64,
            prompt=prompt,
            max_tokens=max_tokens,
            target=target,
        )
        if _has_meaningful_vision_content(fallback):
            return fallback
        if fallback is not None:
            compat_exc = _empty_vision_result_error()
        raw_result, raw_exc = _try_raw_vision_fallback(
            client,
            image_base64=image_base64,
            prompt=prompt,
            max_tokens=max_tokens,
            target=target,
        )
        if raw_result is not None:
            return raw_result
        return result_cls(
            content=_vision_failure_message(
                target=target,
                exc=raw_exc or compat_exc or sdk_exc or _empty_vision_result_error(),
            )
        )
    if provider == "zhipu" and target.api_key:
        fallback = client._vision_openai_compatible(
            image_base64=image_base64,
            prompt=prompt,
            max_tokens=max_tokens,
            target=target,
        )
        if _has_meaningful_vision_content(fallback):
            return fallback
        compat_exc = _empty_vision_result_error() if fallback is not None else None
        raw_result, raw_exc = _try_raw_vision_fallback(
            client,
            image_base64=image_base64,
            prompt=prompt,
            max_tokens=max_tokens,
            target=target,
        )
        if raw_result is not None:
            return raw_result
        return result_cls(
            content=_vision_failure_message(
                target=target,
                exc=raw_exc or compat_exc or _empty_vision_result_error(),
            )
        )
    return result_cls(content="当前未配置可用的图像分析模型。")


def embed_text_with_info(
    client,
    embedding_result_cls,
    *,
    text: str,
    dimensions: int = 1536,
):
    cfg = client._config()
    embedding_cfg = client._resolve_embedding_config(cfg)
    dispatch = client._resolve_embedding_dispatch(cfg.provider, embedding_cfg)
    if dispatch.route == "openai-compatible":
        maybe = client._embed_openai_compatible(text, cfg, embedding_cfg)
        if maybe:
            vector, used_model, used_base_url = maybe
            return embedding_result_cls(
                vector=vector,
                source="provider",
                provider=embedding_cfg.provider,
                model=used_model,
                base_url=used_base_url,
            )

    preserve_embedding_target = not (
        dispatch.route == "pseudo" and dispatch.fallback_reason == "missing_active_config"
    )

    return embedding_result_cls(
        vector=client._pseudo_embedding(text, dimensions),
        source="pseudo_fallback",
        provider=embedding_cfg.provider or None if preserve_embedding_target else None,
        model=embedding_cfg.model if preserve_embedding_target else None,
        base_url=embedding_cfg.base_url if preserve_embedding_target else None,
        fallback_reason=dispatch.fallback_reason,
    )


def chat_stream(
    client,
    *,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 4096,
    variant_override: str | None = None,
    model_override: str | None = None,
    session_cache_key: str | None = None,
) -> Iterator:
    cfg = client._config()
    target = client._resolve_model_target(
        "rag",
        model_override,
        variant_override=variant_override,
        cfg=cfg,
    )
    dispatch = client._resolve_chat_dispatch(target)
    if dispatch.route == "openai-responses":
        yield from client._chat_stream_openai_responses(
            messages,
            tools,
            max_tokens,
            cfg,
            target=target,
            session_cache_key=session_cache_key,
        )
        return
    if dispatch.route == "openai-compatible":
        yield from client._chat_stream_openai_compatible(
            messages,
            tools,
            max_tokens,
            cfg,
            target=target,
            session_cache_key=session_cache_key,
        )
        return
    if dispatch.route == "anthropic":
        yield from client._chat_stream_anthropic_fallback(
            messages,
            max_tokens,
            cfg,
            target=target,
        )
        return
    yield from client._chat_stream_pseudo(messages, cfg, target=target)


def test_config(client, cfg) -> dict:
    return {
        "chat": test_chat_config(client, cfg),
        "embedding": test_embedding_config(client, cfg),
    }


def test_chat_config(client, cfg) -> dict:
    target = client._resolve_model_target("skim", None, cfg=cfg)
    provider = target.provider
    model = target.model
    base_url = target.base_url
    dispatch = client._resolve_chat_test_dispatch(target)
    if dispatch.route == "disabled":
        return _runtime_failure(
            provider=None,
            model=None,
            base_url=None,
            transport="disabled",
            message="未激活任何 LLM 配置，请先在设置中创建并激活配置。",
        )
    if dispatch.route == "missing_api_key":
        return _runtime_failure(
            provider=provider,
            model=model,
            base_url=base_url,
            transport="missing_api_key",
            message="缺少 API Key。",
            status_code=401,
        )
    if dispatch.route == "openai":
        return llm_provider_probe.probe_openai_chat(client, target)
    if dispatch.route == "openai-compatible":
        return llm_provider_probe.probe_openai_compatible_chat(client, target)
    return llm_provider_probe.probe_anthropic_chat(client, cfg, target)


def test_embedding_config(client, cfg) -> dict:
    provider = normalize_provider_name(cfg.provider)
    embedding_cfg = client._resolve_embedding_config(cfg)
    dispatch = client._resolve_embedding_test_dispatch(provider, embedding_cfg)
    if dispatch.route == "disabled":
        return _runtime_failure(
            provider=None,
            model=None,
            base_url=None,
            transport="disabled",
            message="未激活任何 LLM 配置，请先创建并激活配置。",
        )
    if dispatch.route == "unsupported":
        return _runtime_failure(
            provider=embedding_cfg.provider,
            model=embedding_cfg.model,
            base_url=embedding_cfg.base_url,
            transport="embeddings",
            message="当前嵌入提供方暂不支持自动测试。",
        )
    if dispatch.route == "missing_api_key":
        return _runtime_failure(
            provider=embedding_cfg.provider,
            model=embedding_cfg.model,
            base_url=embedding_cfg.base_url,
            transport="embeddings",
            message="缺少嵌入 API Key。",
            status_code=401,
        )
    return llm_provider_probe.probe_embedding_openai_compatible(
        client,
        cfg,
        embedding_cfg,
    )
