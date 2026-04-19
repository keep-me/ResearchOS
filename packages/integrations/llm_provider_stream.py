from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx

from packages.integrations import llm_provider_error

logger = logging.getLogger(__name__)


def _function_tool_names(tools: list[dict] | None) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("type") or "").strip() != "function":
            continue
        function_payload = tool.get("function")
        if not isinstance(function_payload, dict):
            continue
        name = str(function_payload.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _history_contains_tool_messages(messages: list[dict[str, object]]) -> bool:
    for message in messages:
        role = str(message.get("role") or "").strip()
        if role == "tool":
            return True
        if role != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return True
    return False


def _is_litellm_proxy_target(target) -> bool:  # noqa: ANN001
    provider = str(getattr(target, "provider", "") or "").strip().lower()
    base_url = str(getattr(target, "base_url", "") or "").strip().lower()
    model = str(getattr(target, "model", "") or "").strip().lower()
    return "litellm" in provider or "litellm" in base_url or "litellm" in model


def _noop_tool_definition() -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "_noop",
            "description": (
                "Placeholder tool for LiteLLM compatibility when transcript history already contains tool calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    }


def _error_event_with_attempts(
    event_cls,
    event,
    *,
    attempts: list[dict[str, object]] | None = None,
    provider_id: str | None = None,
):
    if getattr(event, "type", "") != "error" or not attempts:
        return event
    base_payload = {
        "message": str(getattr(event, "content", "") or "").strip() or "Unknown error",
    }
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, dict):
        base_payload.update(metadata)
    payload = llm_provider_error.attach_error_attempts(
        llm_provider_error.to_session_error_payload(base_payload, provider_id=provider_id),
        attempts,
        include_current=True,
    )
    normalized_metadata = {
        str(key): value
        for key, value in payload.items()
        if key not in {"message"}
    }
    return event_cls(
        type="error",
        content=str(payload.get("message") or getattr(event, "content", "") or "Unknown error"),
        metadata=normalized_metadata or None,
    )


def _yield_stream_with_attempts(
    event_cls,
    stream,
    *,
    attempts: list[dict[str, object]] | None = None,
    provider_id: str | None = None,
) -> Iterator[object]:
    for event in stream:
        yield _error_event_with_attempts(
            event_cls,
            event,
            attempts=attempts,
            provider_id=provider_id,
        )


def _yield_transport_error(
    event_cls,
    error: Exception,
    *,
    attempts: list[dict[str, object]] | None = None,
    provider_id: str | None = None,
) -> Iterator[object]:
    payload = llm_provider_error.attach_error_attempts(
        llm_provider_error.to_session_error_payload(error, provider_id=provider_id),
        attempts,
        include_current=bool(attempts),
    )
    if not isinstance(payload, dict):
        yield event_cls(type="error", content=str(error))
        return
    metadata = {
        str(key): value
        for key, value in payload.items()
        if key not in {"message"}
    }
    yield event_cls(
        type="error",
        content=str(payload.get("message") or str(error)),
        metadata=metadata or None,
    )


def _raw_stream_chat_completions_url(client, target) -> str:  # noqa: ANN001
    resolved_base = client._resolve_transport_base_url(target.provider, target.base_url)
    if not resolved_base:
        raise RuntimeError("Missing base URL")
    lowered = resolved_base.lower().rstrip("/")
    if lowered.endswith("/chat/completions"):
        return resolved_base
    return f"{resolved_base.rstrip('/')}/chat/completions"


def _yield_raw_openai_chat_stream(
    event_cls,
    client,
    *,
    messages: list[dict],
    tools: list[dict] | None,
    max_tokens: int,
    target,
    session_cache_key: str | None,
) -> Iterator[object]:  # noqa: ANN001
    normalized_messages = client._build_openai_chat_messages(
        messages,
        resolved=target,
        include_reasoning_content=client._supports_chat_reasoning_content(target),
    )
    normalized_tools = client._normalize_openai_chat_tools(tools)
    payload: dict[str, object] = {
        "model": target.model,
        "messages": normalized_messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if normalized_tools:
        payload["tools"] = normalized_tools
    client._apply_variant_to_chat_kwargs(
        payload,
        target,
        session_cache_key=session_cache_key,
    )
    payload["stream"] = True

    request_headers = {
        "Authorization": f"Bearer {target.api_key or ''}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0",
    }
    timeout = httpx.Timeout(connect=30.0, read=None, write=120.0, pool=None)
    url = _raw_stream_chat_completions_url(client, target)
    tools_buffer: dict[int, dict[str, str]] = {}
    input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0

    with httpx.Client(timeout=timeout, follow_redirects=True, headers=request_headers) as http_client:
        with http_client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                body = response.read().decode("utf-8", errors="replace")
                detail = client._extract_raw_error_message(body, f"{response.status_code} {response.reason_phrase}")
                raise RuntimeError(detail)

            for line in response.iter_lines():
                if not line:
                    continue
                raw_line = line.strip()
                if not raw_line.startswith("data:"):
                    continue
                data = raw_line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                usage = chunk.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or input_tokens or 0)
                output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or output_tokens or 0)
                reasoning_tokens = int(client._extract_reasoning_tokens(usage) or reasoning_tokens or 0)

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning_delta = client._extract_chat_reasoning_text(delta)
                if reasoning_delta:
                    yield event_cls(type="reasoning_delta", content=reasoning_delta)

                text_delta = client._coerce_openai_message_text(delta.get("content"))
                if text_delta:
                    yield event_cls(type="text_delta", content=text_delta)

                for tool_call in delta.get("tool_calls") or []:
                    index = int(tool_call.get("index", 0) or 0)
                    if index not in tools_buffer:
                        tools_buffer[index] = {"id": "", "name": "", "arguments": ""}
                    buffer = tools_buffer[index]
                    if tool_call.get("id"):
                        buffer["id"] = str(tool_call.get("id") or "")
                    function_payload = tool_call.get("function") or {}
                    if function_payload.get("name"):
                        buffer["name"] += str(function_payload.get("name") or "")
                    if function_payload.get("arguments"):
                        buffer["arguments"] += str(function_payload.get("arguments") or "")

    for index in sorted(tools_buffer.keys()):
        buffer = tools_buffer[index]
        if buffer["id"] or buffer["name"] or buffer["arguments"]:
            yield event_cls(
                type="tool_call",
                tool_call_id=buffer["id"],
                tool_name=buffer["name"],
                tool_arguments=buffer["arguments"],
            )

    if input_tokens or output_tokens or reasoning_tokens:
        yield event_cls(
            type="usage",
            model=target.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
    yield event_cls(type="done")


def stream_openai_responses(
    client,
    event_cls,
    *,
    sdk_client,
    messages: list[dict],
    tools: list[dict] | None,
    max_tokens: int,
    cfg,
    target,
    session_cache_key: str | None = None,
    allow_compatible_fallback: bool = True,
    attempts: list[dict[str, object]] | None = None,
) -> Iterator[object]:
    try:
        model = target.model
        kwargs: dict = {
            "model": model,
            "store": False,
        }
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        client._apply_variant_to_responses_kwargs(
            kwargs,
            target,
            session_cache_key=session_cache_key,
        )
        if client._has_provider_defined_tool(tools, "openai.web_search", "openai.web_search_preview"):
            client._append_responses_include(kwargs, "web_search_call.action.sources")
        if client._has_provider_defined_tool(tools, "openai.code_interpreter"):
            client._append_responses_include(kwargs, "code_interpreter_call.outputs")
        store = bool(kwargs.get("store"))
        kwargs["input"] = client._build_responses_input_from_messages(messages, store=store)
        previous_response_id = client._extract_previous_responses_response_id(
            messages,
            store=store,
        )
        if previous_response_id and not kwargs.get("previous_response_id"):
            kwargs["previous_response_id"] = previous_response_id
        normalized_tools = client._normalize_responses_tools(tools)
        if normalized_tools:
            kwargs["tools"] = normalized_tools
            kwargs["tool_choice"] = "auto"

        response = sdk_client.responses.create(**kwargs)
        response_parts = client._extract_responses_output_parts(response)
        tool_calls = client._extract_responses_tool_calls(response)
        response_payload = client._to_dict(response)
        usage_metadata = client._build_openai_response_metadata(
            response_id=str(response_payload.get("id") or "").strip() or None,
            service_tier=(
                str(response_payload.get("service_tier") or "").strip() or None
            ),
        )
        emitted_content = False
        for part in response_parts:
            part_type = str(part.get("type") or "")
            text = str(part.get("text") or "")
            part_id = str(part.get("part_id") or "")
            metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else None
            if part_type == "reasoning":
                yield event_cls(
                    type="reasoning_delta",
                    content=text,
                    part_id=part_id,
                    metadata=metadata,
                )
                emitted_content = emitted_content or bool(text) or bool(part_id) or bool(metadata)
            elif part_type == "text" and text:
                yield event_cls(
                    type="text_delta",
                    content=text,
                    part_id=part_id,
                    metadata=metadata,
                )
                emitted_content = True
        if not emitted_content and not tool_calls:
            last_user = next(
                (
                    client._stringify_message_content(message.get("content"))
                    for message in reversed(messages)
                    if message.get("role") == "user"
                ),
                "",
            )
            pseudo = client._pseudo_summary(last_user, "rag", cfg, None)
            if pseudo.content:
                yield event_cls(type="text_delta", content=pseudo.content)
        for call in tool_calls:
            call_id = str(call.get("call_id") or "")
            name = str(call.get("name") or "")
            arguments = str(call.get("arguments") or "")
            metadata = call.get("metadata") if isinstance(call.get("metadata"), dict) else None
            yield event_cls(
                type="tool_call",
                tool_call_id=call_id,
                tool_name=name,
                tool_arguments=arguments,
                metadata=metadata,
                provider_executed=bool(call.get("provider_executed")),
            )
            if call.get("provider_executed"):
                yield event_cls(
                    type="tool_result",
                    tool_call_id=call_id,
                    tool_name=name,
                    metadata=metadata,
                    provider_executed=True,
                    tool_success=bool(call.get("success", True)),
                    tool_summary=str(call.get("summary") or ""),
                    tool_result=call.get("result"),
                )
        in_tokens, out_tokens, reasoning_tokens = client._extract_responses_usage(response)
        if (
            usage_metadata
            or (in_tokens or 0) > 0
            or (out_tokens or 0) > 0
            or (reasoning_tokens or 0) > 0
        ):
            yield event_cls(
                type="usage",
                model=model,
                input_tokens=in_tokens or 0,
                output_tokens=out_tokens or 0,
                reasoning_tokens=reasoning_tokens or 0,
                metadata=usage_metadata,
            )
        yield event_cls(type="done")
    except Exception as exc:
        logger.warning("chat_stream OpenAI responses failed: %s", exc)
        provider_id = str(getattr(target, "provider", "") or "").strip().lower() or None
        response_attempts = list(attempts or [])
        response_attempts.append(
            llm_provider_error.error_attempt(
                exc,
                provider_id=provider_id,
                transport="responses",
            )
        )
        if not allow_compatible_fallback:
            yield from _yield_transport_error(
                event_cls,
                exc,
                attempts=list(attempts or []),
                provider_id=provider_id,
            )
            return
        try:
            yield from _yield_stream_with_attempts(
                event_cls,
                client._chat_stream_openai_compatible(
                    messages,
                    tools,
                    max_tokens,
                    cfg,
                    target=target,
                    session_cache_key=session_cache_key,
                ),
                attempts=response_attempts,
                provider_id=provider_id,
            )
        except Exception as fallback_exc:  # pragma: no cover - defensive path
            yield from _yield_transport_error(
                event_cls,
                fallback_exc,
                attempts=response_attempts,
                provider_id=provider_id,
            )


def stream_openai_compatible(
    client,
    event_cls,
    *,
    sdk_client,
    messages: list[dict],
    tools: list[dict] | None,
    max_tokens: int,
    cfg,
    target,
    session_cache_key: str | None = None,
) -> Iterator[object]:
    try:
        model = target.model
        normalized_chat_tools = client._normalize_openai_chat_tools(tools) or []
        if (
            _is_litellm_proxy_target(target)
            and not normalized_chat_tools
            and _history_contains_tool_messages(messages)
        ):
            normalized_chat_tools = [_noop_tool_definition()]
        allowed_tool_names = _function_tool_names(normalized_chat_tools)
        kwargs: dict = {
            "model": model,
            "messages": client._build_openai_chat_messages(
                messages,
                resolved=target,
                include_reasoning_content=client._supports_chat_reasoning_content(target),
            ),
            "stream": True,
            "max_tokens": max_tokens,
            "stream_options": {"include_usage": True},
        }
        if normalized_chat_tools:
            kwargs["tools"] = normalized_chat_tools
        client._apply_variant_to_chat_kwargs(
            kwargs,
            target,
            session_cache_key=session_cache_key,
        )

        stream = sdk_client.chat.completions.create(**kwargs)
        tools_buffer: dict[int, dict[str, str]] = {}
        input_tokens, output_tokens = 0, 0
        usage = None

        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", 0) or 0

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue

            reasoning_delta = client._extract_chat_reasoning_text(delta)
            if reasoning_delta:
                yield event_cls(type="reasoning_delta", content=reasoning_delta)

            if delta.content:
                yield event_cls(type="text_delta", content=delta.content)

            if delta.tool_calls:
                for tool_call in delta.tool_calls:
                    index = getattr(tool_call, "index", 0)
                    if index not in tools_buffer:
                        tools_buffer[index] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    buffer = tools_buffer[index]
                    if getattr(tool_call, "id", None):
                        buffer["id"] = tool_call.id
                    function = getattr(tool_call, "function", None)
                    if function:
                        if getattr(function, "name", None):
                            buffer["name"] += function.name or ""
                        if getattr(function, "arguments", None):
                            buffer["arguments"] += function.arguments or ""

        for index in sorted(tools_buffer.keys()):
            buffer = tools_buffer[index]
            normalized_name = str(buffer["name"] or "").strip()
            if normalized_name not in allowed_tool_names:
                lowered = normalized_name.lower()
                if lowered in allowed_tool_names:
                    buffer["name"] = lowered
            if buffer["id"] or buffer["name"] or buffer["arguments"]:
                yield event_cls(
                    type="tool_call",
                    tool_call_id=buffer["id"],
                    tool_name=buffer["name"],
                    tool_arguments=buffer["arguments"],
                )

        reasoning_tokens = client._extract_reasoning_tokens(usage) if usage else 0
        if input_tokens or output_tokens or reasoning_tokens:
            yield event_cls(
                type="usage",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens or 0,
            )
        yield event_cls(type="done")
    except Exception as exc:
        provider_id = str(getattr(target, "provider", "") or "").strip().lower() or None
        if client._should_try_openai_responses_fallback(target, exc):
            logger.info(
                "chat_stream OpenAI-compatible rejected legacy chat protocol; switching to Responses fallback: %s",
                exc,
            )
            yield from client._chat_stream_openai_responses(
                messages,
                tools,
                max_tokens,
                cfg,
                target=target,
                session_cache_key=session_cache_key,
                allow_compatible_fallback=False,
                attempts=[
                    llm_provider_error.error_attempt(
                        exc,
                        provider_id=provider_id,
                        transport="chat.completions",
                    )
                ],
            )
            return
        should_try_raw_fallback = client._should_try_raw_openai_http_fallback(target, exc)
        if should_try_raw_fallback:
            logger.info("chat_stream OpenAI-compatible blocked; switching to raw SSE fallback: %s", exc)
            attempts = [
                llm_provider_error.error_attempt(
                    exc,
                    provider_id=provider_id,
                    transport="chat.completions",
                )
            ]
            try:
                yield from _yield_raw_openai_chat_stream(
                    event_cls,
                    client,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    target=target,
                    session_cache_key=session_cache_key,
                )
                return
            except Exception as raw_exc:
                logger.warning("chat_stream raw HTTP fallback failed: %s", raw_exc)
                yield from _yield_transport_error(
                    event_cls,
                    raw_exc,
                    attempts=attempts,
                    provider_id=provider_id,
                )
                return
        logger.warning("chat_stream OpenAI-compatible failed: %s", exc)
        yield from _yield_transport_error(
            event_cls,
            exc,
            provider_id=provider_id,
        )


def stream_anthropic_fallback(
    client,
    event_cls,
    *,
    messages: list[dict],
    max_tokens: int,
    cfg,
    target,
) -> Iterator[object]:
    try:
        prompt = "\n\n".join(
            f"{message.get('role', 'user')}: {client._stringify_message_content(message.get('content'))}"
            for message in messages
            if client._stringify_message_content(message.get("content")).strip()
        )
        result = client._call_anthropic(
            prompt,
            "rag",
            cfg,
            None,
            target=target,
            max_tokens=max_tokens,
        )
        if result.content:
            yield event_cls(type="text_delta", content=result.content)
        yield event_cls(type="done")
    except Exception as exc:
        logger.warning("chat_stream Anthropic fallback failed: %s", exc)
        provider_id = str(getattr(target, "provider", "") or "").strip().lower() or None
        yield from _yield_transport_error(event_cls, exc, provider_id=provider_id)


def stream_pseudo(
    client,
    event_cls,
    *,
    messages: list[dict],
    cfg,
    target,
) -> Iterator[object]:
    prompt = "\n\n".join(
        f"{message.get('role', 'user')}: {client._stringify_message_content(message.get('content'))}"
        for message in messages
        if client._stringify_message_content(message.get("content")).strip()
    )
    result = client._pseudo_summary(prompt, "rag", cfg, None, target=target)
    if result.content:
        yield event_cls(type="text_delta", content=result.content)
    yield event_cls(type="done")
