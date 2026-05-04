from __future__ import annotations

import copy
import json
import re
from http.client import responses as HTTP_STATUS_CODES
from typing import Any

_STATUS_CODE_RE = re.compile(r"\b([1-5]\d{2})\b")
_HTML_RESPONSE_RE = re.compile(r"^\s*(<!doctype|<html)\b", re.IGNORECASE)
_STATUS_MESSAGE_RE = re.compile(r"^[1-5]\d{2}\b")
_OVERFLOW_PATTERNS = (
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"input is too long for requested model", re.IGNORECASE),
    re.compile(r"exceeds the context window", re.IGNORECASE),
    re.compile(r"input token count.*exceeds the maximum", re.IGNORECASE),
    re.compile(r"maximum prompt length is \d+", re.IGNORECASE),
    re.compile(r"reduce the length of the messages", re.IGNORECASE),
    re.compile(r"maximum context length is \d+ tokens", re.IGNORECASE),
    re.compile(r"exceeds the limit of \d+", re.IGNORECASE),
    re.compile(r"exceeds the available context size", re.IGNORECASE),
    re.compile(r"greater than the context length", re.IGNORECASE),
    re.compile(r"context window exceeds limit", re.IGNORECASE),
    re.compile(r"exceeded model token limit", re.IGNORECASE),
    re.compile(r"context[_ ]length[_ ]exceeded", re.IGNORECASE),
    re.compile(r"request entity too large", re.IGNORECASE),
)
_AUTH_ERROR_CLASS_NAMES = {
    "authenticationerror",
    "autherror",
    "permissionerror",
    "permissiondenied",
    "permissiondeniederror",
    "forbiddenerror",
    "invalidapikeyerror",
    "invalidtokenerror",
    "unauthenticatederror",
    "unauthorizederror",
}
_TIMEOUT_ERROR_CLASS_NAMES = {
    "connecttimeout",
    "connecttimeouterror",
    "deadlineexceeded",
    "readtimeout",
    "readtimeouterror",
    "writetimeout",
    "pooltimeout",
    "timeoutexception",
    "timeout",
    "timeouterror",
    "requesttimeouterror",
    "apitimeouterror",
}
_NETWORK_ERROR_CLASS_NAMES = {
    "connecterror",
    "networkerror",
    "apiconnectionerror",
    "connectionerror",
    "protocolerror",
    "remoteprotocolerror",
    "requesterror",
    "transporterror",
}
_RETRYABLE_ERROR_CLASS_NAMES = {
    "resourceexhaustederror",
    "ratelimiterror",
    "toomanyrequestserror",
    "internalservererror",
    "servererror",
    "unavailableerror",
    "serviceunavailableerror",
}
_CONNECTION_RESET_CODES = {"ECONNRESET"}
_CONNECTION_RESET_MARKERS = ("econnreset", "connection reset", "socket hang up")


def _transport_kind(transport: str | None) -> str | None:
    raw = str(transport or "").strip().lower()
    if not raw:
        return None
    base = raw.split("(", 1)[0].strip()
    if "chat.completions" in base:
        return "chat.completions"
    if base.startswith("responses"):
        return "responses"
    return base or raw


def _error_class_name(error: Any) -> str:
    return str(
        getattr(error.__class__, "__name__", "") or getattr(error, "__name__", "") or ""
    ).strip()


def _message_from_error(error: Any) -> str:
    if isinstance(error, dict):
        for key in ("message", "detail", "error"):
            value = error.get(key)
            if isinstance(value, dict):
                nested = value.get("message") or value.get("detail")
                if nested:
                    return str(nested).strip()
            if value:
                return str(value).strip()
        return ""
    return str(error or "").strip()


def _status_code_from_error(error: Any, message: str) -> int | None:
    for candidate in (
        getattr(error, "status_code", None),
        getattr(error, "statusCode", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            return value
    if isinstance(error, dict):
        for key in ("statusCode", "status_code", "status", "code"):
            candidate = error.get(key)
            try:
                value = int(candidate)
            except (TypeError, ValueError):
                continue
            if 100 <= value <= 599:
                return value
    match = _STATUS_CODE_RE.search(message)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _response_body_from_error(error: Any) -> str | None:
    for attr in ("response_body", "responseBody", "body"):
        value = getattr(error, attr, None)
        if value:
            return str(value)
    if isinstance(error, dict):
        value = error.get("responseBody") or error.get("response_body") or error.get("body")
        if value:
            return str(value)
        return None
    response = getattr(error, "response", None)
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    return None


def _response_headers_from_error(error: Any) -> dict[str, str] | None:
    headers: Any = None
    if isinstance(error, dict):
        headers = (
            error.get("responseHeaders") or error.get("response_headers") or error.get("headers")
        )
    else:
        response = getattr(error, "response", None)
        headers = (
            getattr(error, "response_headers", None)
            or getattr(error, "responseHeaders", None)
            or getattr(response, "headers", None)
            or getattr(error, "headers", None)
        )
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


def _metadata_from_error(error: Any) -> dict[str, Any] | None:
    metadata_attr = getattr(error, "metadata", None)
    if isinstance(metadata_attr, dict) and metadata_attr:
        return {str(key): value for key, value in metadata_attr.items()}
    if isinstance(error, dict):
        metadata = error.get("metadata")
        if isinstance(metadata, dict) and metadata:
            return {str(key): value for key, value in metadata.items()}
        fields = (
            "code",
            "syscall",
            "errno",
            "type",
            "param",
            "url",
            "transport",
            "provider",
            "gateway",
            "bucket",
        )
        payload = {
            str(field): error.get(field) for field in fields if error.get(field) not in (None, "")
        }
        return payload or None
    fields = (
        "code",
        "syscall",
        "errno",
        "type",
        "param",
        "url",
        "transport",
        "provider",
        "gateway",
        "bucket",
    )
    payload = {
        str(field): getattr(error, field)
        for field in fields
        if getattr(error, field, None) not in (None, "")
    }
    return payload or None


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text[:1] not in {"{", "["}:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _provider_error_candidates(error: Any, response_body: str | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(error, dict):
        candidates.append(error)
    for raw in (response_body, _message_from_error(error)):
        parsed = _json_object(raw)
        if isinstance(parsed, dict):
            candidates.append(parsed)
    expanded: list[dict[str, Any]] = []
    for candidate in candidates:
        expanded.append(candidate)
        nested = candidate.get("error")
        if isinstance(nested, dict):
            expanded.append(nested)
    return expanded


def _normalized_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    normalized = {
        str(key): value for key, value in (metadata or {}).items() if value not in (None, "")
    }
    return normalized or None


def _provider_error_details(error: Any, response_body: str | None) -> dict[str, Any] | None:
    details: dict[str, Any] = {}
    for candidate in _provider_error_candidates(error, response_body):
        error_payload = (
            candidate.get("error") if isinstance(candidate.get("error"), dict) else candidate
        )
        if not isinstance(error_payload, dict):
            continue
        message = str(error_payload.get("message") or candidate.get("message") or "").strip()
        if message and (
            not details.get("message")
            or _STATUS_MESSAGE_RE.match(str(details.get("message") or "").strip()) is not None
        ):
            details["message"] = message
        for source_key, target_key in (
            ("type", "type"),
            ("code", "code"),
            ("param", "param"),
            ("status", "status"),
        ):
            value = error_payload.get(source_key)
            if value not in (None, "") and target_key not in details:
                details[target_key] = str(value)
        candidate_status = (
            candidate.get("status")
            or candidate.get("status_code")
            or candidate.get("statusCode")
            or candidate.get("code")
        )
        if candidate_status not in (None, "") and "status_code" not in details:
            try:
                status_value = int(candidate_status)
            except (TypeError, ValueError):
                status_value = None
            if status_value is not None and 100 <= status_value <= 599:
                details["status_code"] = status_value
        error_code = error_payload.get("code")
        if "status_code" not in details:
            try:
                status_value = int(error_code)
            except (TypeError, ValueError):
                status_value = None
            if status_value is not None and 100 <= status_value <= 599:
                details["status_code"] = status_value
    return details or None


def extract_response_error_message(body: str | None, fallback: str) -> str:
    if not isinstance(body, str):
        return str(fallback or "").strip()
    try:
        payload = json.loads(body)
    except Exception:
        return body.strip() or fallback
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or fallback).strip()
    if isinstance(error, str):
        return error.strip()
    return str(payload.get("message") or fallback).strip()


def _friendly_gateway_message(
    message: str, status_code: int | None, response_body: str | None
) -> str:
    if not isinstance(response_body, str) or not _HTML_RESPONSE_RE.search(response_body):
        return message
    if status_code == 401:
        return (
            "Unauthorized: request was blocked by a gateway or proxy. "
            "Your authentication token may be missing or expired."
        )
    if status_code == 403:
        return (
            "Forbidden: request was blocked by a gateway or proxy. "
            "You may not have permission to access this resource."
        )
    return message


def _status_message(status_code: int | None) -> str | None:
    if status_code is None:
        return None
    return HTTP_STATUS_CODES.get(status_code)


def _compose_error_message(
    *,
    message: str,
    status_code: int | None,
    response_body: str | None,
    provider_details: dict[str, Any] | None,
) -> str:
    text = str(message or "").strip()
    if text == "":
        if response_body:
            text = response_body
        elif status_code is not None:
            text = _status_message(status_code) or "Unknown error"
        else:
            text = "Unknown error"

    if (
        isinstance(provider_details, dict)
        and str(provider_details.get("message") or "").strip()
        and (
            _STATUS_MESSAGE_RE.match(text) is not None
            or (status_code is not None and text == (_status_message(status_code) or ""))
            or text == str(response_body or "").strip()
        )
    ):
        text = str(provider_details["message"]).strip()

    if not response_body or (
        status_code is not None and text != (_status_message(status_code) or "")
    ):
        return _friendly_gateway_message(text, status_code, response_body)

    extracted = extract_response_error_message(response_body, "")
    if extracted:
        composed = f"{text}: {extracted}"
    else:
        composed = f"{text}: {response_body}"
    return _friendly_gateway_message(composed.strip(), status_code, response_body)


def extract_error_context(error: Any) -> dict[str, Any]:
    base_message = _message_from_error(error)
    response_body = _response_body_from_error(error)
    provider_details = _provider_error_details(error, response_body)
    status_code = (
        int(provider_details["status_code"])
        if isinstance(provider_details, dict) and provider_details.get("status_code") is not None
        else _status_code_from_error(error, base_message)
    )
    response_headers = _response_headers_from_error(error)
    metadata = _metadata_from_error(error)
    if isinstance(provider_details, dict):
        metadata = dict(metadata or {})
        for key in ("code", "type", "param", "status"):
            value = provider_details.get(key)
            if value not in (None, ""):
                metadata[str(key)] = value
        metadata = metadata or None
    provider_id = str((metadata or {}).get("provider") or "").strip().lower() or None
    message = _compose_error_message(
        message=base_message,
        status_code=status_code,
        response_body=response_body,
        provider_details=provider_details,
    )
    return {
        "message": message,
        "status_code": status_code,
        "response_body": response_body,
        "response_headers": response_headers,
        "metadata": metadata,
        "provider_id": provider_id,
        "provider_details": provider_details,
    }


def transport_semantics(
    metadata: dict[str, Any] | None,
    *,
    provider_id: str | None = None,
) -> dict[str, str]:
    source = dict(metadata or {})
    provider = str(source.get("provider") or provider_id or "").strip().lower()
    transport = str(source.get("transport") or "").strip()
    gateway = str(source.get("gateway") or "").strip()
    bucket = str(source.get("bucket") or "").strip()
    url = str(source.get("url") or "").strip()
    transport_kind = _transport_kind(transport)
    payload = {
        "providerID": provider,
        "transport": transport,
        "transportKind": transport_kind or "",
        "gateway": gateway,
        "bucket": bucket,
        "url": url,
    }
    return {str(key): value for key, value in payload.items() if str(value or "").strip()}


def _provider_error_message_name(parsed: dict[str, Any]) -> str:
    explicit = str(parsed.get("name") or "").strip()
    if explicit:
        return explicit
    if bool(parsed.get("auth")):
        return "AuthError"
    if str(parsed.get("type") or "").strip() == "context_overflow":
        return "ContextOverflowError"
    return "APIError"


def _provider_error_payload_like(value: Any) -> bool:
    return isinstance(value, dict) and (
        isinstance(value.get("is_retryable"), bool)
        or str(value.get("type") or "").strip() in {"api_error", "context_overflow"}
        or str(value.get("name") or "").strip() in {"APIError", "AuthError", "ContextOverflowError"}
        or isinstance(value.get("provider_id"), str)
    )


def _session_error_payload_like(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and str(value.get("message") or "").strip() != ""
        and (
            isinstance(value.get("isRetryable"), bool)
            or str(value.get("name") or "").strip()
            in {"APIError", "AuthError", "ContextOverflowError", "UnknownError"}
            or isinstance(value.get("providerID"), str)
            or isinstance(value.get("transport"), str)
            or value.get("auth") is not None
            or isinstance(value.get("attempts"), list)
        )
    )


def _normalize_session_error_payload(
    payload: dict[str, Any],
    *,
    provider_id: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    message = str(result.get("message") or "").strip() or "Unknown error"
    result["message"] = message
    resolved_provider = str(result.get("providerID") or provider_id or "").strip().lower()
    if resolved_provider:
        result["providerID"] = resolved_provider
    transport = str(result.get("transport") or "").strip()
    if transport and not str(result.get("transportKind") or "").strip():
        transport_kind = _transport_kind(transport)
        if transport_kind:
            result["transportKind"] = transport_kind
    metadata = result.get("metadata")
    normalized_metadata = dict(metadata or {}) if isinstance(metadata, dict) else {}
    if resolved_provider and not normalized_metadata.get("provider"):
        normalized_metadata["provider"] = resolved_provider
    for key in ("transport", "gateway", "bucket", "url"):
        value = str(result.get(key) or "").strip()
        if value and not normalized_metadata.get(key):
            normalized_metadata[key] = value
    if normalized_metadata:
        result["metadata"] = normalized_metadata
    else:
        result.pop("metadata", None)
    status_code: int | None = None
    try:
        if result.get("statusCode") is not None:
            status_code = int(result["statusCode"])
            result["statusCode"] = status_code
    except (TypeError, ValueError):
        result.pop("statusCode", None)
    response_body = str(result.get("responseBody") or "").strip() or None
    auth = (
        bool(result.get("auth"))
        if result.get("auth") is not None
        else _is_auth_error(message, status_code, None)
    )
    overflow = _is_overflow(message, status_code, response_body, None)
    if not str(result.get("name") or "").strip():
        if overflow:
            result["name"] = "ContextOverflowError"
        elif auth:
            result["name"] = "AuthError"
        else:
            result["name"] = "APIError"
    if result.get("auth") is None:
        result["auth"] = auth
    if not isinstance(result.get("isRetryable"), bool):
        result["isRetryable"] = (
            False
            if (overflow or auth)
            else _is_retryable_provider_error(
                resolved_provider,
                result,
                message=message,
                status_code=status_code,
                details=None,
            )
        )
    return result


def provider_event_metadata(parsed: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": _provider_error_message_name(parsed),
        "isRetryable": bool(parsed.get("is_retryable")),
    }
    if parsed.get("auth") is not None:
        payload["auth"] = bool(parsed.get("auth"))
    if parsed.get("status_code") is not None:
        payload["statusCode"] = int(parsed["status_code"])
    if parsed.get("response_headers"):
        payload["responseHeaders"] = parsed["response_headers"]
    if parsed.get("response_body"):
        payload["responseBody"] = parsed["response_body"]
    if parsed.get("metadata"):
        payload["metadata"] = parsed["metadata"]
    if parsed.get("provider_id"):
        payload["providerID"] = str(parsed["provider_id"])
    for source_key, target_key in (
        ("transport", "transport"),
        ("transport_kind", "transportKind"),
        ("gateway", "gateway"),
        ("bucket", "bucket"),
        ("url", "url"),
    ):
        value = str(parsed.get(source_key) or "").strip()
        if value:
            payload[target_key] = value
    return payload


def _fallback_session_error_payload(
    *,
    message: str,
    provider_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    status_code: int | None = None,
    response_headers: dict[str, str] | None = None,
    response_body: str | None = None,
) -> dict[str, Any]:
    normalized_metadata = _normalized_metadata(metadata)
    semantics = transport_semantics(
        normalized_metadata,
        provider_id=provider_id,
    )
    payload: dict[str, Any] = {
        "name": "UnknownError",
        "message": str(message or "").strip() or "Unknown error",
        "isRetryable": False,
    }
    if status_code is not None:
        payload["statusCode"] = int(status_code)
    if response_headers:
        payload["responseHeaders"] = response_headers
    if response_body:
        payload["responseBody"] = response_body
    if normalized_metadata:
        payload["metadata"] = normalized_metadata
    if semantics.get("providerID"):
        payload["providerID"] = semantics["providerID"]
    for key in ("transport", "transportKind", "gateway", "bucket", "url"):
        value = semantics.get(key)
        if value:
            payload[key] = value
    return payload


def to_session_error_payload(
    error: Any,
    *,
    provider_id: str | None = None,
) -> dict[str, Any]:
    if _session_error_payload_like(error):
        return _normalize_session_error_payload(error, provider_id=provider_id)
    parsed = (
        error
        if _provider_error_payload_like(error)
        else normalize_provider_error(error, provider_id=provider_id)
    )
    if not isinstance(parsed, dict):
        context = extract_error_context(error)
        metadata = dict(context.get("metadata") or {})
        if provider_id and not metadata.get("provider"):
            metadata["provider"] = str(provider_id).strip()
        return _fallback_session_error_payload(
            message=str(context.get("message") or _message_from_error(error) or "Unknown error"),
            provider_id=provider_id or str(context.get("provider_id") or "").strip() or None,
            metadata=metadata or None,
            status_code=context.get("status_code"),
            response_headers=context.get("response_headers"),
            response_body=context.get("response_body"),
        )
    payload = provider_event_metadata(parsed)
    payload["message"] = str(parsed.get("message") or "Unknown error")
    return payload


def error_attempt(
    error: Any,
    *,
    provider_id: str | None = None,
    transport: str | None = None,
) -> dict[str, Any]:
    payload = to_session_error_payload(error, provider_id=provider_id)
    if transport and not str(payload.get("transport") or "").strip():
        payload = dict(payload)
        payload["transport"] = str(transport).strip()
        transport_kind = _transport_kind(transport)
        if transport_kind and not str(payload.get("transportKind") or "").strip():
            payload["transportKind"] = transport_kind
    attempt: dict[str, Any] = {
        "message": str(payload.get("message") or _message_from_error(error) or "Unknown error"),
    }
    for key in (
        "name",
        "auth",
        "isRetryable",
        "statusCode",
        "responseHeaders",
        "responseBody",
        "providerID",
        "transport",
        "transportKind",
        "gateway",
        "bucket",
        "url",
        "metadata",
    ):
        value = payload.get(key)
        if value in (None, "", {}):
            continue
        attempt[key] = copy.deepcopy(value)
    return attempt


def attach_error_attempts(
    payload: dict[str, Any],
    attempts: list[dict[str, Any]] | None,
    *,
    include_current: bool = False,
) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    merged: list[dict[str, Any]] = [
        copy.deepcopy(item)
        for item in (result.get("attempts") or [])
        if isinstance(item, dict) and item
    ]
    for item in attempts or []:
        if isinstance(item, dict) and item:
            merged.append(copy.deepcopy(item))
    if include_current:
        merged.append(error_attempt(result))
    if not merged:
        return result
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in merged:
        try:
            signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        except TypeError:
            signature = repr(item)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    result["attempts"] = deduped
    return result


def runtime_failure_result(
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    transport: str,
    message: str,
    latency_ms: int | None = None,
    error: Any | None = None,
    provider_id: str | None = None,
    status_code: int | None = None,
    response_headers: dict[str, str] | None = None,
    response_body: str | None = None,
    metadata: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    seed_metadata = dict(metadata or {})
    resolved_provider = str(provider_id or provider or "").strip().lower() or None
    if resolved_provider and not seed_metadata.get("provider"):
        seed_metadata["provider"] = resolved_provider
    if transport and not seed_metadata.get("transport"):
        seed_metadata["transport"] = transport
    normalized = (
        to_session_error_payload(error, provider_id=resolved_provider)
        if error is not None
        else to_session_error_payload(
            {
                "message": message,
                "statusCode": status_code,
                "responseHeaders": response_headers,
                "responseBody": response_body,
                "metadata": seed_metadata or None,
            },
            provider_id=resolved_provider,
        )
    )
    normalized = attach_error_attempts(
        normalized,
        attempts,
        include_current=bool(attempts),
    )
    payload: dict[str, Any] = {
        "ok": False,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "transport": str(normalized.get("transport") or transport),
        "message": str(normalized.get("message") or message),
        "error": normalized,
    }
    if normalized.get("attempts"):
        payload["attempts"] = copy.deepcopy(normalized["attempts"])
    if latency_ms is not None:
        payload["latency_ms"] = int(latency_ms)
    for key in ("gateway", "bucket", "url"):
        value = normalized.get(key)
        if value:
            payload[key] = value
    return payload


def _parsed_error_name(error_type: str, auth: bool) -> str:
    if error_type == "context_overflow":
        return "ContextOverflowError"
    if auth:
        return "AuthError"
    return "APIError"


def _parsed_provider_error_payload(
    *,
    error_type: str,
    message: str,
    is_retryable: bool,
    auth: bool,
    status_code: int | None = None,
    response_headers: dict[str, str] | None = None,
    response_body: str | None = None,
    metadata: dict[str, Any] | None = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    normalized_message = str(message or "").strip() or "Unknown error"
    normalized_metadata = dict(metadata or {}) or None
    semantics = transport_semantics(
        normalized_metadata,
        provider_id=provider_id,
    )
    payload: dict[str, Any] = {
        "type": error_type,
        "name": _parsed_error_name(error_type, auth),
        "message": normalized_message,
        "is_retryable": bool(is_retryable),
        "auth": bool(auth),
    }
    if status_code is not None:
        payload["status_code"] = int(status_code)
    if response_headers:
        payload["response_headers"] = response_headers
    if response_body:
        payload["response_body"] = response_body
    if normalized_metadata:
        payload["metadata"] = normalized_metadata
    if semantics.get("providerID"):
        payload["provider_id"] = semantics["providerID"]
    for source_key, target_key in (
        ("transport", "transport"),
        ("transportKind", "transport_kind"),
        ("gateway", "gateway"),
        ("bucket", "bucket"),
        ("url", "url"),
    ):
        value = semantics.get(source_key)
        if value:
            payload[target_key] = value
    return payload


def _merge_parsed_error(
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(primary, dict):
        return secondary if isinstance(secondary, dict) else None
    if not isinstance(secondary, dict):
        return primary
    merged = dict(primary)
    for key, value in secondary.items():
        if key == "metadata":
            metadata = dict(secondary.get("metadata") or {})
            if not metadata:
                continue
            merged_metadata = dict(merged.get("metadata") or {})
            for meta_key, meta_value in metadata.items():
                merged_metadata.setdefault(meta_key, meta_value)
            if merged_metadata:
                merged["metadata"] = merged_metadata
            continue
        if key not in merged or merged.get(key) in (None, "", False):
            merged[key] = value
    return merged


def _is_overflow(
    message: str, status_code: int | None, response_body: str | None, details: dict[str, Any] | None
) -> bool:
    haystack = "\n".join(
        item
        for item in (
            str(message or ""),
            str(response_body or ""),
            str((details or {}).get("code") or ""),
            str((details or {}).get("type") or ""),
            str((details or {}).get("status") or ""),
        )
        if item
    )
    if status_code == 413:
        return True
    if re.match(r"^4(00|13)\s*(status code)?\s*\(no body\)", str(message or ""), re.IGNORECASE):
        return True
    return any(pattern.search(haystack) for pattern in _OVERFLOW_PATTERNS)


def _is_auth_error(message: str, status_code: int | None, details: dict[str, Any] | None) -> bool:
    haystack = "\n".join(
        item.lower()
        for item in (
            message,
            str((details or {}).get("code") or ""),
            str((details or {}).get("type") or ""),
            str((details or {}).get("status") or ""),
        )
        if item
    )
    markers = (
        "api key",
        "authentication",
        "unauthorized",
        "forbidden",
        "invalid_api_key",
        "authentication_error",
        "permission_error",
        "invalid x-api-key",
    )
    return status_code in {401, 403} or any(marker in haystack for marker in markers)


def _raw_retryable_flag(error: Any) -> bool | None:
    for candidate in (
        getattr(error, "is_retryable", None),
        getattr(error, "isRetryable", None),
        getattr(error, "should_retry", None),
    ):
        if isinstance(candidate, bool):
            return candidate
    if isinstance(error, dict):
        for key in ("isRetryable", "is_retryable", "retryable"):
            value = error.get(key)
            if isinstance(value, bool):
                return value
    return None


def _is_retryable_provider_error(
    provider_id: str | None,
    error: Any,
    *,
    message: str,
    status_code: int | None,
    details: dict[str, Any] | None,
) -> bool:
    retryable = _raw_retryable_flag(error)
    if provider_id and provider_id.startswith("openai") and status_code == 404:
        return True
    if retryable is True:
        return True
    if retryable is False:
        return False
    haystack = "\n".join(
        item.lower()
        for item in (
            message,
            str((details or {}).get("code") or ""),
            str((details or {}).get("type") or ""),
            str((details or {}).get("status") or ""),
        )
        if item
    )
    markers = (
        "rate_limit",
        "too many requests",
        "overloaded",
        "service unavailable",
        "temporarily unavailable",
        "server_error",
        "internal_server_error",
        "resource_exhausted",
        "unavailable",
        "timeout",
        "timed out",
        "api_connection_error",
        "bad gateway",
        "gateway timeout",
        "connection reset",
    )
    return status_code in {408, 409, 429, 500, 502, 503, 504} or any(
        marker in haystack for marker in markers
    )


def parse_stream_error(input_value: Any) -> dict[str, Any] | None:
    body = _json_object(input_value)
    if not body or body.get("type") != "error":
        return None
    response_body = json.dumps(body, ensure_ascii=False)
    error_payload = body.get("error") if isinstance(body.get("error"), dict) else {}
    context = extract_error_context({"responseBody": response_body, **body})
    status_code = context["status_code"]
    response_headers = context["response_headers"]
    metadata = context["metadata"]
    provider_id = context["provider_id"]
    code = str(error_payload.get("code") or "").strip()
    if code == "context_length_exceeded":
        return _parsed_provider_error_payload(
            error_type="context_overflow",
            message="Input exceeds context window of this model",
            is_retryable=False,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=metadata,
            provider_id=provider_id,
        )
    if code == "insufficient_quota":
        return _parsed_provider_error_payload(
            error_type="api_error",
            message="Quota exceeded. Check your plan and billing details.",
            is_retryable=False,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=metadata,
            provider_id=provider_id,
        )
    if code == "usage_not_included":
        return _parsed_provider_error_payload(
            error_type="api_error",
            message="To use Codex with your ChatGPT plan, upgrade to Plus: https://chatgpt.com/explore/plus.",
            is_retryable=False,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=metadata,
            provider_id=provider_id,
        )
    if code == "invalid_prompt":
        return _parsed_provider_error_payload(
            error_type="api_error",
            message=str(error_payload.get("message") or "Invalid prompt.").strip(),
            is_retryable=False,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=metadata,
            provider_id=provider_id,
        )

    if _is_overflow(
        context["message"],
        context["status_code"],
        context["response_body"],
        context["provider_details"],
    ):
        return _parsed_provider_error_payload(
            error_type="context_overflow",
            message=context["message"],
            is_retryable=False,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=metadata,
            provider_id=provider_id,
        )
    return _parsed_provider_error_payload(
        error_type="api_error",
        message=context["message"],
        is_retryable=_is_retryable_provider_error(
            context["provider_id"],
            body,
            message=context["message"],
            status_code=context["status_code"],
            details=context["provider_details"],
        ),
        auth=_is_auth_error(
            context["message"],
            context["status_code"],
            context["provider_details"],
        ),
        status_code=status_code,
        response_headers=response_headers,
        response_body=response_body,
        metadata=metadata,
        provider_id=provider_id,
    )


def parse_api_call_error(provider_id: str | None, error: Any) -> dict[str, Any] | None:
    parsed = parse_transport_error(error)
    if parsed is None:
        return None
    if provider_id:
        metadata = dict(parsed.get("metadata") or {})
        metadata.setdefault("provider", str(provider_id).strip())
        return _parsed_provider_error_payload(
            error_type=str(parsed.get("type") or "api_error"),
            message=str(parsed.get("message") or "Unknown error"),
            is_retryable=bool(parsed.get("is_retryable")),
            auth=bool(parsed.get("auth")),
            status_code=parsed.get("status_code"),
            response_headers=parsed.get("response_headers"),
            response_body=parsed.get("response_body"),
            metadata=metadata,
            provider_id=str(provider_id).strip(),
        )
    return parsed


def parse_transport_error(error: Any) -> dict[str, Any] | None:
    context = extract_error_context(error)
    message = str(context.get("message") or "").strip()
    status_code = context.get("status_code")
    response_body = context.get("response_body")
    response_headers = context.get("response_headers")
    metadata = context.get("metadata")
    provider_details = context.get("provider_details")
    provider_id = context.get("provider_id")
    class_name = _error_class_name(error).lower()
    lowered_message = message.lower()
    normalized_metadata = _normalized_metadata(metadata)
    error_code = str((normalized_metadata or {}).get("code") or "").strip().upper()

    if (
        error_code in _CONNECTION_RESET_CODES
        or class_name == "connectionreseterror"
        or any(marker in lowered_message for marker in _CONNECTION_RESET_MARKERS)
    ):
        reset_metadata = dict(normalized_metadata or {})
        reset_metadata.setdefault("code", "ECONNRESET")
        syscall = getattr(error, "syscall", None)
        if syscall not in (None, ""):
            reset_metadata.setdefault("syscall", str(syscall))
        return _parsed_provider_error_payload(
            error_type="api_error",
            message="Connection reset by server",
            is_retryable=True,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=reset_metadata,
            provider_id=provider_id,
        )

    if (
        "missing api key" in lowered_message
        or "api key is required" in lowered_message
        or "no api key" in lowered_message
    ):
        return _parsed_provider_error_payload(
            error_type="api_error",
            message=message or "Missing API key",
            is_retryable=False,
            auth=True,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=normalized_metadata,
            provider_id=provider_id,
        )

    if class_name in _AUTH_ERROR_CLASS_NAMES:
        return _parsed_provider_error_payload(
            error_type="api_error",
            message=message or "Authentication failed",
            is_retryable=False,
            auth=True,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=normalized_metadata,
            provider_id=provider_id,
        )

    if class_name in _TIMEOUT_ERROR_CLASS_NAMES or (
        status_code is None
        and any(marker in lowered_message for marker in ("timed out", "timeout"))
    ):
        timeout_metadata = dict(normalized_metadata or {})
        timeout_metadata.setdefault("code", "TIMEOUT")
        return _parsed_provider_error_payload(
            error_type="api_error",
            message=message or "Request timed out",
            is_retryable=True,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=timeout_metadata,
            provider_id=provider_id,
        )

    if (
        class_name in _NETWORK_ERROR_CLASS_NAMES
        or class_name.endswith("connecterror")
        or class_name.endswith("networkerror")
        or (
            status_code is None
            and any(
                marker in lowered_message for marker in ("network unreachable", "connection failed")
            )
        )
    ):
        network_metadata = dict(normalized_metadata or {})
        network_metadata.setdefault("code", "NETWORK_ERROR")
        return _parsed_provider_error_payload(
            error_type="api_error",
            message=message or "Network connection failed",
            is_retryable=True,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=network_metadata,
            provider_id=provider_id,
        )

    if not message and status_code is None and not response_body and not metadata:
        return None
    if _is_overflow(message, status_code, response_body, provider_details):
        return _parsed_provider_error_payload(
            error_type="context_overflow",
            message=message or "Input exceeds context window of this model",
            is_retryable=False,
            auth=False,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            metadata=normalized_metadata,
            provider_id=provider_id,
        )
    return _parsed_provider_error_payload(
        error_type="api_error",
        message=message or "Unknown error",
        is_retryable=(
            class_name in _RETRYABLE_ERROR_CLASS_NAMES
            or _is_retryable_provider_error(
                provider_id,
                error,
                message=message,
                status_code=status_code,
                details=provider_details,
            )
        ),
        auth=class_name in _AUTH_ERROR_CLASS_NAMES
        or _is_auth_error(message, status_code, provider_details),
        status_code=status_code,
        response_headers=response_headers,
        response_body=response_body,
        metadata=normalized_metadata,
        provider_id=provider_id,
    )


def normalize_provider_error(
    error: Any, *, provider_id: str | None = None
) -> dict[str, Any] | None:
    explicit_provider = str(provider_id or "").strip() or None
    context = extract_error_context(error)
    resolved_provider = explicit_provider or str(context.get("provider_id") or "").strip() or None
    stream_parsed = parse_stream_error(error)
    transport_parsed = parse_api_call_error(resolved_provider, error)
    merged = _merge_parsed_error(stream_parsed, transport_parsed)
    if merged is not None:
        return merged
    return parse_transport_error(error)
