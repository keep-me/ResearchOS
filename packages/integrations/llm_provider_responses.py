from __future__ import annotations

import copy
import json
from typing import Any


def build_openai_provider_metadata(
    *,
    item_id: str | None = None,
    reasoning_encrypted_content: str | None = None,
    annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    normalized_item_id = str(item_id or "").strip() or None
    openai_metadata: dict[str, Any] = {}
    if normalized_item_id:
        openai_metadata["itemId"] = normalized_item_id
    if reasoning_encrypted_content is not None:
        openai_metadata["reasoningEncryptedContent"] = reasoning_encrypted_content
    if annotations:
        openai_metadata["annotations"] = copy.deepcopy(annotations)
    if not openai_metadata:
        return None
    return {"openai": openai_metadata}


def build_openai_response_metadata(
    *,
    response_id: str | None = None,
    service_tier: str | None = None,
) -> dict[str, Any] | None:
    normalized_response_id = str(response_id or "").strip() or None
    normalized_service_tier = str(service_tier or "").strip() or None
    openai_metadata: dict[str, Any] = {}
    if normalized_response_id:
        openai_metadata["responseId"] = normalized_response_id
    if normalized_service_tier:
        openai_metadata["serviceTier"] = normalized_service_tier
    if not openai_metadata:
        return None
    return {"openai": openai_metadata}


def extract_openai_item_id(client, part: dict[str, Any]) -> str | None:
    metadata = part.get("metadata")
    if not isinstance(metadata, dict):
        return None
    openai = metadata.get("openai")
    if not isinstance(openai, dict):
        return None
    return str(openai.get("itemId") or openai.get("item_id") or "").strip() or None


def extract_openai_reasoning_metadata(client, part: dict[str, Any]) -> tuple[str | None, str | None]:
    metadata = part.get("metadata")
    if not isinstance(metadata, dict):
        return None, None
    openai = metadata.get("openai")
    if not isinstance(openai, dict):
        return None, None
    item_id = extract_openai_item_id(client, part)
    encrypted_content = openai.get("reasoningEncryptedContent")
    if encrypted_content is None:
        encrypted_content = openai.get("reasoning_encrypted_content")
    if encrypted_content is not None:
        encrypted_content = str(encrypted_content)
    return item_id, encrypted_content


def extract_openai_response_id_from_message(client, message: dict[str, Any]) -> str | None:
    provider_metadata = message.get("provider_metadata")
    if not isinstance(provider_metadata, dict):
        provider_metadata = message.get("providerMetadata")
    if not isinstance(provider_metadata, dict):
        return None
    openai = provider_metadata.get("openai")
    if not isinstance(openai, dict):
        return None
    return str(openai.get("responseId") or openai.get("response_id") or "").strip() or None


def extract_previous_responses_response_id(
    client,
    messages: list[dict[str, Any]],
    *,
    store: bool,
) -> str | None:
    if not store:
        return None
    for message in reversed(messages):
        if str(message.get("role") or "") != "assistant":
            continue
        response_id = extract_openai_response_id_from_message(client, message)
        if response_id:
            return response_id
    return None


def extract_responses_output_parts(client, response: object) -> list[dict[str, Any]]:
    payload = client._to_dict(response)
    output = payload.get("output")
    parts: list[dict[str, Any]] = []

    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "message":
                item_id = str(item.get("id") or "").strip()
                content_items = item.get("content")
                if not isinstance(content_items, list):
                    continue
                for content_item in content_items:
                    if not isinstance(content_item, dict):
                        continue
                    content_type = str(content_item.get("type") or "").strip().lower()
                    if content_type not in {"output_text", "text"}:
                        continue
                    raw_annotations = content_item.get("annotations")
                    annotations = (
                        [annotation for annotation in raw_annotations if isinstance(annotation, dict)]
                        if isinstance(raw_annotations, list)
                        else None
                    )
                    metadata = build_openai_provider_metadata(
                        item_id=item_id,
                        annotations=annotations,
                    )
                    text = client._coerce_openai_message_text(
                        content_item.get("text") or content_item.get("value")
                    ).strip()
                    if not text:
                        continue
                    part: dict[str, Any] = {
                        "type": "text",
                        "text": text,
                    }
                    if item_id:
                        part["part_id"] = item_id
                    if metadata:
                        part["metadata"] = metadata
                    parts.append(part)
                continue
            if item_type != "reasoning":
                continue

            item_id = str(item.get("id") or "").strip()
            encrypted_content = item.get("encrypted_content")
            metadata = build_openai_provider_metadata(
                item_id=item_id or None,
                reasoning_encrypted_content=(None if encrypted_content is None else str(encrypted_content)),
            )
            summary = item.get("summary")
            emitted_summary = False

            def _append_reasoning(text: str, index: int) -> None:
                nonlocal emitted_summary
                part: dict[str, Any] = {
                    "type": "reasoning",
                    "text": text,
                }
                if item_id:
                    part["part_id"] = f"{item_id}:{index}"
                if metadata:
                    part["metadata"] = metadata
                parts.append(part)
                emitted_summary = True

            if isinstance(summary, str):
                text = summary.strip()
                _append_reasoning(text, 0)
            elif isinstance(summary, list):
                for index, summary_item in enumerate(summary):
                    if isinstance(summary_item, dict):
                        text = client._coerce_openai_message_text(
                            summary_item.get("text")
                            or summary_item.get("summary")
                            or summary_item.get("value")
                        ).strip()
                    elif isinstance(summary_item, str):
                        text = summary_item.strip()
                    else:
                        text = ""
                    _append_reasoning(text, index)

            if not emitted_summary and metadata:
                _append_reasoning("", 0)

    if not any(str(part.get("type") or "") == "text" for part in parts):
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            parts.append({"type": "text", "text": output_text.strip()})

    return parts


def extract_responses_tool_calls(client, response: object) -> list[dict[str, Any]]:
    payload = client._to_dict(response)
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "")).lower()
        entry: dict[str, Any] | None = None
        if item_type in ("function_call", "tool_call"):
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            args = item.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args or {}, ensure_ascii=False)
            item_id = str(item.get("id") or "").strip()
            metadata = build_openai_provider_metadata(item_id=item_id or None)
            if name:
                entry = {
                    "call_id": call_id,
                    "name": name,
                    "arguments": args,
                }
                if metadata:
                    entry["metadata"] = metadata
        elif item_type == "web_search_call":
            tool_name = str(item.get("tool_name") or "web_search").strip() or "web_search"
            entry = {
                "call_id": str(item.get("id") or "").strip(),
                "name": tool_name,
                "arguments": json.dumps({"action": item.get("action")}, ensure_ascii=False),
                "provider_executed": True,
                "result": {
                    "status": str(item.get("status") or "").strip() or "completed",
                },
            }
        elif item_type == "computer_call":
            status = str(item.get("status") or "").strip() or "completed"
            entry = {
                "call_id": str(item.get("id") or "").strip(),
                "name": "computer_use",
                "arguments": "",
                "provider_executed": True,
                "result": {
                    "type": "computer_use_tool_result",
                    "status": status,
                },
            }
        elif item_type == "file_search_call":
            raw_results = item.get("results")
            results_payload: list[dict[str, Any]] | None = None
            if isinstance(raw_results, list):
                results_payload = []
                for result in raw_results:
                    if not isinstance(result, dict):
                        continue
                    results_payload.append(
                        {
                            "attributes": result.get("attributes") or {},
                            "fileId": str(result.get("file_id") or "").strip(),
                            "filename": str(result.get("filename") or "").strip(),
                            "score": result.get("score"),
                            "text": str(result.get("text") or ""),
                        }
                    )
            entry = {
                "call_id": str(item.get("id") or "").strip(),
                "name": "file_search",
                "arguments": "{}",
                "provider_executed": True,
                "result": {
                    "queries": list(item.get("queries") or []),
                    "results": results_payload,
                },
            }
        elif item_type == "code_interpreter_call":
            entry = {
                "call_id": str(item.get("id") or "").strip(),
                "name": "code_interpreter",
                "arguments": json.dumps(
                    {
                        "code": item.get("code"),
                        "containerId": str(item.get("container_id") or "").strip(),
                    },
                    ensure_ascii=False,
                ),
                "provider_executed": True,
                "result": {
                    "outputs": list(item.get("outputs") or []) if isinstance(item.get("outputs"), list) else None,
                },
            }
        elif item_type == "image_generation_call":
            entry = {
                "call_id": str(item.get("id") or "").strip(),
                "name": "image_generation",
                "arguments": "{}",
                "provider_executed": True,
                "result": {
                    "result": str(item.get("result") or ""),
                },
            }
        elif item_type == "local_shell_call":
            entry = {
                "call_id": str(item.get("call_id") or item.get("id") or "").strip(),
                "name": "local_shell",
                "arguments": json.dumps(
                    {
                        "action": {
                            "type": "exec",
                            "command": list((item.get("action") or {}).get("command") or []),
                            "timeoutMs": (item.get("action") or {}).get("timeout_ms"),
                            "user": (item.get("action") or {}).get("user"),
                            "workingDirectory": (item.get("action") or {}).get("working_directory"),
                            "env": (item.get("action") or {}).get("env"),
                        }
                    },
                    ensure_ascii=False,
                ),
            }
            item_id = str(item.get("id") or "").strip()
            metadata = build_openai_provider_metadata(item_id=item_id or None)
            if metadata:
                entry["metadata"] = metadata

        if not isinstance(entry, dict):
            continue
        if entry.get("provider_executed"):
            status = str(((entry.get("result") or {}) if isinstance(entry.get("result"), dict) else {}).get("status") or "").strip().lower()
            success = status not in {"failed", "failure", "error", "errored", "cancelled", "canceled"}
            entry["success"] = success
            entry["summary"] = f"{str(entry.get('name') or 'tool')} {'completed' if success else 'failed'}"
        calls.append(entry)
    return calls


def parse_local_shell_output(content: object) -> str | None:
    payload: object = content
    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    direct = payload.get("output")
    if isinstance(direct, str):
        return direct
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("output")
        if isinstance(nested, str):
            return nested
    return None


def assistant_reasoning_parts(client, message: dict[str, Any]) -> list[dict[str, Any]]:
    raw_parts = message.get("reasoning_parts")
    if not isinstance(raw_parts, list):
        reasoning = str(message.get("reasoning_content") or "").strip()
        return [{"text": reasoning}] if reasoning else []

    normalized: list[dict[str, Any]] = []
    for item in raw_parts:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else None
        if not text and not metadata:
            continue
        entry: dict[str, Any] = {"text": text}
        if metadata:
            entry["metadata"] = metadata
        normalized.append(entry)
    if normalized:
        return normalized

    reasoning = str(message.get("reasoning_content") or "").strip()
    return [{"text": reasoning}] if reasoning else []


def assistant_text_parts(client, message: dict[str, Any]) -> list[dict[str, Any]]:
    raw_parts = message.get("text_parts")
    if not isinstance(raw_parts, list):
        content = str(message.get("content") or "")
        return [{"text": content}] if content else []

    normalized: list[dict[str, Any]] = []
    for item in raw_parts:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else None
        if not text:
            continue
        entry: dict[str, Any] = {"text": text}
        if metadata:
            entry["metadata"] = metadata
        normalized.append(entry)
    if normalized:
        return normalized

    content = str(message.get("content") or "")
    return [{"text": content}] if content else []


def normalize_responses_tools(tools: list[dict] | None) -> list[dict]:
    if not tools:
        return []
    normalized: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "").strip()
        if tool_type == "provider-defined":
            tool_id = str(tool.get("id") or "").strip()
            args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
            if tool_id == "openai.file_search":
                entry: dict[str, Any] = {
                    "type": "file_search",
                    "vector_store_ids": list(args.get("vectorStoreIds") or []),
                }
                if args.get("maxNumResults") is not None:
                    entry["max_num_results"] = args.get("maxNumResults")
                ranking = args.get("ranking") if isinstance(args.get("ranking"), dict) else None
                if ranking:
                    entry["ranking_options"] = {
                        "ranker": ranking.get("ranker"),
                        "score_threshold": ranking.get("scoreThreshold"),
                    }
                if args.get("filters") is not None:
                    entry["filters"] = args.get("filters")
                normalized.append(entry)
                continue
            if tool_id == "openai.local_shell":
                normalized.append({"type": "local_shell"})
                continue
            if tool_id == "openai.web_search_preview":
                entry = {"type": "web_search_preview"}
                if args.get("searchContextSize") is not None:
                    entry["search_context_size"] = args.get("searchContextSize")
                if args.get("userLocation") is not None:
                    entry["user_location"] = args.get("userLocation")
                normalized.append(entry)
                continue
            if tool_id == "openai.web_search":
                entry = {"type": "web_search"}
                filters = args.get("filters") if isinstance(args.get("filters"), dict) else None
                if filters is not None:
                    entry["filters"] = {
                        "allowed_domains": list(filters.get("allowedDomains") or []),
                    }
                if args.get("searchContextSize") is not None:
                    entry["search_context_size"] = args.get("searchContextSize")
                if args.get("userLocation") is not None:
                    entry["user_location"] = args.get("userLocation")
                normalized.append(entry)
                continue
            if tool_id == "openai.code_interpreter":
                container = args.get("container")
                normalized.append(
                    {
                        "type": "code_interpreter",
                        "container": (
                            {"type": "auto", "file_ids": None}
                            if container is None
                            else container
                            if isinstance(container, str)
                            else {
                                "type": "auto",
                                "file_ids": list((container or {}).get("fileIds") or []),
                            }
                        ),
                    }
                )
                continue
            if tool_id == "openai.image_generation":
                entry = {"type": "image_generation"}
                mapping = {
                    "background": "background",
                    "inputFidelity": "input_fidelity",
                    "model": "model",
                    "moderation": "moderation",
                    "partialImages": "partial_images",
                    "quality": "quality",
                    "outputCompression": "output_compression",
                    "outputFormat": "output_format",
                    "size": "size",
                }
                for source_key, target_key in mapping.items():
                    if args.get(source_key) is not None:
                        entry[target_key] = args.get(source_key)
                input_image_mask = args.get("inputImageMask") if isinstance(args.get("inputImageMask"), dict) else None
                if input_image_mask is not None:
                    entry["input_image_mask"] = {
                        "file_id": input_image_mask.get("fileId"),
                        "image_url": input_image_mask.get("imageUrl"),
                    }
                normalized.append(entry)
                continue
            continue
        if tool_type != "function":
            continue
        fn = tool.get("function")
        if isinstance(fn, dict):
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            normalized.append(
                {
                    "type": "function",
                    "name": name,
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters") or {},
                }
            )
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "type": "function",
                "name": name,
                "description": str(tool.get("description") or ""),
                "parameters": tool.get("parameters") or {},
            }
        )
    return normalized


def build_responses_input_from_messages(
    client,
    messages: list[dict[str, Any]],
    *,
    store: bool = False,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        content = msg.get("content", "")
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            provider_executed = bool(
                msg.get("provider_executed") or msg.get("providerExecuted")
            )
            tool_name = str(msg.get("name") or msg.get("tool_name") or "").strip()
            if call_id:
                if provider_executed:
                    if store:
                        payload.append({"type": "item_reference", "id": call_id})
                    continue
                if tool_name == "local_shell":
                    local_shell_output = parse_local_shell_output(content)
                    if local_shell_output is not None:
                        payload.append(
                            {
                                "type": "local_shell_call_output",
                                "call_id": call_id,
                                "output": local_shell_output,
                            }
                        )
                        continue
                payload.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": str(content or ""),
                    }
                )
            else:
                payload.append(
                    {
                        "role": "user",
                        "content": f"工具执行结果：{str(content or '')}",
                    }
                )
            continue
        if role not in ("system", "user", "assistant"):
            role = "user"
        if role == "user":
            structured_content = client._build_responses_user_content(content)
            if structured_content is not None:
                payload.append(
                    {
                        "role": "user",
                        "content": structured_content,
                    }
                )
                continue
        if role == "assistant":
            text_parts = assistant_text_parts(client, msg)
            reasoning_parts = assistant_reasoning_parts(client, msg)
            replayed_reasoning_ids: set[str] = set()
            reasoning_payloads: dict[str, dict[str, Any]] = {}
            for part in reasoning_parts:
                item_id, encrypted_content = extract_openai_reasoning_metadata(client, part)
                if not item_id:
                    continue
                if store:
                    if item_id not in replayed_reasoning_ids:
                        payload.append({"type": "item_reference", "id": item_id})
                        replayed_reasoning_ids.add(item_id)
                    continue
                reasoning_payload = reasoning_payloads.get(item_id)
                if reasoning_payload is None:
                    reasoning_payload = {
                        "type": "reasoning",
                        "id": item_id,
                        "summary": [],
                    }
                    if encrypted_content is not None:
                        reasoning_payload["encrypted_content"] = encrypted_content
                    payload.append(reasoning_payload)
                    reasoning_payloads[item_id] = reasoning_payload
                    replayed_reasoning_ids.add(item_id)
                elif (
                    encrypted_content is not None
                    and reasoning_payload.get("encrypted_content") is None
                ):
                    reasoning_payload["encrypted_content"] = encrypted_content
                text = str(part.get("text") or "")
                if text:
                    reasoning_payload["summary"].append(
                        {
                            "type": "summary_text",
                            "text": text,
                        }
                    )

            for part in text_parts:
                text = str(part.get("text") or "")
                if not text:
                    continue
                item_id = extract_openai_item_id(client, part)
                assistant_payload: dict[str, Any] = {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
                if item_id:
                    assistant_payload["id"] = item_id
                payload.append(assistant_payload)

            if not text_parts:
                assistant_content = str(content or "")
                if not assistant_content:
                    if not reasoning_parts:
                        assistant_content = str(msg.get("reasoning_content") or "")
                    elif not replayed_reasoning_ids:
                        assistant_content = str(msg.get("reasoning_content") or "")
                if assistant_content:
                    payload.append(
                        {
                            "role": "assistant",
                            "content": assistant_content,
                        }
                    )
            for item in msg.get("tool_calls") or []:
                if not isinstance(item, dict):
                    continue
                function_payload = item.get("function")
                if not isinstance(function_payload, dict):
                    continue
                if item.get("provider_executed") or item.get("providerExecuted"):
                    continue
                tool_name = str(function_payload.get("name") or "").strip()
                if not tool_name:
                    continue
                if tool_name == "local_shell":
                    try:
                        parsed_arguments = json.loads(str(function_payload.get("arguments") or "{}"))
                    except json.JSONDecodeError:
                        parsed_arguments = {}
                    action = parsed_arguments.get("action") if isinstance(parsed_arguments, dict) else {}
                    if isinstance(action, dict):
                        local_shell_payload: dict[str, Any] = {
                            "type": "local_shell_call",
                            "call_id": str(item.get("id") or ""),
                            "id": extract_openai_item_id(client, item) or str(item.get("id") or ""),
                            "action": {
                                "type": "exec",
                                "command": list(action.get("command") or []),
                            },
                        }
                        if action.get("timeoutMs") is not None:
                            local_shell_payload["action"]["timeout_ms"] = action.get("timeoutMs")
                        if action.get("user") is not None:
                            local_shell_payload["action"]["user"] = action.get("user")
                        if action.get("workingDirectory") is not None:
                            local_shell_payload["action"]["working_directory"] = action.get("workingDirectory")
                        if action.get("env") is not None:
                            local_shell_payload["action"]["env"] = action.get("env")
                        payload.append(local_shell_payload)
                        continue
                tool_payload: dict[str, Any] = {
                    "type": "function_call",
                    "call_id": str(item.get("id") or ""),
                    "name": tool_name,
                    "arguments": str(function_payload.get("arguments") or "{}"),
                }
                item_id = extract_openai_item_id(client, item)
                if item_id:
                    tool_payload["id"] = item_id
                payload.append(tool_payload)
            continue
        payload.append(
            {
                "role": role,
                "content": client._stringify_message_content(content) if role == "user" else str(content or ""),
            }
        )
    return payload
