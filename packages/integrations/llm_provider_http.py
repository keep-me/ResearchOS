from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from packages.integrations import llm_provider_error
from packages.integrations.llm_provider_schema import ResolvedModelTarget

logger = logging.getLogger(__name__)

_OPENAI_LIKE_ENDPOINT_SUFFIXES = (
    "/chat/completions",
    "/responses",
    "/embeddings",
    "/images/generations",
)


class ProviderHTTPError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
        response_headers: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.response_headers = dict(response_headers or {}) or None
        self.metadata = dict(metadata or {}) or None


def _normalize_metadata(
    metadata: dict[str, Any] | None,
    *,
    url: str | None = None,
) -> dict[str, Any] | None:
    normalized: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if value in (None, ""):
            continue
        normalized[str(key)] = value
    if url:
        normalized.setdefault("url", url)
    return normalized or None


def _raw_http_error_metadata(
    client,
    resolved: ResolvedModelTarget,
    *,
    transport: str,
    bucket: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "provider": str(resolved.provider or "").strip().lower() or None,
        "transport": transport,
        "bucket": bucket,
    }
    if client._is_gateway_target(resolved):
        metadata["gateway"] = "gateway"
        upstream = client._gateway_provider_slug(resolved.model)
        if upstream:
            metadata["provider"] = upstream
    return {str(key): value for key, value in metadata.items() if value not in (None, "")}


def _normalize_headers(headers: Any) -> dict[str, str] | None:
    if headers is None:
        return None
    try:
        items = headers.items()
    except Exception:
        return None
    normalized: dict[str, str] = {}
    for key, value in items:
        key_text = str(key or "").strip().lower()
        value_text = str(value or "").strip()
        if key_text and value_text:
            normalized[key_text] = value_text
    return normalized or None


def raw_openai_compatible_post(
    client,
    *,
    provider: str | None = None,
    base_url: str | None,
    api_key: str | None,
    path: str,
    payload: dict[str, Any],
    timeout: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("Missing API key")
    resolved_provider = str(provider or "openai").strip().lower() or "openai"
    resolved_base = client._resolve_transport_base_url(resolved_provider, base_url)
    if not resolved_base:
        raise RuntimeError("Missing base URL")
    normalized_path = f"/{path.lstrip('/')}"
    lowered_base = resolved_base.lower().rstrip("/")
    if any(lowered_base.endswith(suffix) for suffix in _OPENAI_LIKE_ENDPOINT_SUFFIXES):
        url = resolved_base
    else:
        url = f"{resolved_base.rstrip('/')}{normalized_path}"
    error_metadata = _normalize_metadata(metadata, url=url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "Mozilla/5.0")
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(timeout if timeout is not None else 30),
        ) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ProviderHTTPError(
            llm_provider_error.extract_response_error_message(
                error_body,
                f"{exc.code} {exc.reason}",
            ),
            status_code=int(exc.code),
            response_body=error_body or None,
            response_headers=_normalize_headers(getattr(exc, "headers", None)),
            metadata=error_metadata,
        ) from exc
    except Exception as exc:
        raise ProviderHTTPError(str(exc), metadata=error_metadata) from exc

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON response from {path}: {response_body[:240]}"
        ) from exc


def call_openai_responses_raw_http(
    client,
    result_cls,
    *,
    prompt: str,
    resolved: ResolvedModelTarget,
    max_tokens: int | None = None,
    request_timeout: float | None = None,
    session_cache_key: str | None = None,
):
    error_metadata = _raw_http_error_metadata(
        client,
        resolved,
        transport="responses(raw-http)",
        bucket="summary-runtime",
    )
    payload: dict[str, Any] = {
        "model": resolved.model,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        "store": False,
    }
    if max_tokens is not None:
        payload["max_output_tokens"] = max_tokens
    client._apply_variant_to_responses_kwargs(
        payload,
        resolved,
        session_cache_key=session_cache_key,
    )
    response = client._raw_openai_compatible_post(
        provider=resolved.provider,
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        path="responses",
        payload=payload,
        timeout=request_timeout,
        metadata=error_metadata,
    )
    output_text: list[str] = []
    reasoning = ""
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for content_part in item.get("content") or []:
            if not isinstance(content_part, dict):
                continue
            if content_part.get("type") in {"output_text", "text"}:
                text = client._coerce_openai_message_text(content_part.get("text"))
                if text:
                    output_text.append(text)
    usage = response.get("usage") or {}
    in_tokens = usage.get("input_tokens")
    out_tokens = usage.get("output_tokens")
    reasoning_tokens = client._extract_reasoning_tokens(usage)
    in_cost, out_cost = client._estimate_cost(
        model=resolved.model,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
    )
    return result_cls(
        content=("".join(output_text) or reasoning).strip(),
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        reasoning_tokens=reasoning_tokens,
        input_cost_usd=in_cost,
        output_cost_usd=out_cost,
        total_cost_usd=in_cost + out_cost,
        reasoning_content=reasoning or None,
    )


def call_openai_chat_raw_http(
    client,
    result_cls,
    *,
    messages: list[dict[str, Any]],
    resolved: ResolvedModelTarget,
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    request_timeout: float | None = None,
    session_cache_key: str | None = None,
) -> tuple[Any, list[tuple[str, str, str]]]:
    error_metadata = _raw_http_error_metadata(
        client,
        resolved,
        transport="chat.completions(raw-http)",
        bucket="chat-runtime",
    )
    payload: dict[str, Any] = {
        "model": resolved.model,
        "messages": messages,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    normalized_tools = client._normalize_openai_chat_tools(tools)
    if normalized_tools:
        payload["tools"] = normalized_tools
    client._apply_variant_to_chat_kwargs(
        payload,
        resolved,
        session_cache_key=session_cache_key,
    )
    response = client._raw_openai_compatible_post(
        provider=resolved.provider,
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        path="chat/completions",
        payload=payload,
        timeout=request_timeout,
        metadata=error_metadata,
    )
    choices = response.get("choices") or []
    message = {}
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
    content = client._coerce_openai_message_text(message.get("content"))
    reasoning = client._coerce_openai_message_text(message.get("reasoning_content"))
    tool_calls: list[tuple[str, str, str]] = []
    for item in message.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function") or {}
        tool_calls.append(
            (
                str(item.get("id") or ""),
                str(function_payload.get("name") or ""),
                str(function_payload.get("arguments") or ""),
            )
        )
    usage = response.get("usage") or {}
    in_tokens = usage.get("prompt_tokens")
    out_tokens = usage.get("completion_tokens")
    reasoning_tokens = client._extract_reasoning_tokens(usage)
    in_cost, out_cost = client._estimate_cost(
        model=resolved.model,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
    )
    return (
        result_cls(
            content=(content or reasoning).strip(),
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            reasoning_tokens=reasoning_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=in_cost + out_cost,
            reasoning_content=reasoning or None,
        ),
        tool_calls,
    )
