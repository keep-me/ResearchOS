from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import agent as agent_router
from apps.api.routers import session_runtime as session_runtime_router
from packages.agent import agent_service
from packages.agent import session_retry
from packages.agent.session.session_errors import normalize_error
from packages.agent.session.session_runtime import get_session_status
from packages.integrations.llm_provider_http import ProviderHTTPError
from packages.integrations.llm_client import StreamEvent
from packages.storage import db
from packages.storage.db import Base


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(session_runtime_router.router)
    app.include_router(agent_router.router)
    return app


def _first_text_part(parts: list[dict]) -> str:
    for part in parts:
        if part.get("type") == "text":
            return str(part.get("text") or "")
    return ""


class FakeRetryLLM:
    attempts: int = 0

    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del messages, tools, max_tokens, variant_override, model_override, session_cache_key
        FakeRetryLLM.attempts += 1
        if FakeRetryLLM.attempts == 1:
            yield StreamEvent(type="error", content="429 Too Many Requests")
            return
        yield StreamEvent(type="text_delta", content="重试后成功。")
        yield StreamEvent(type="usage", model="fake-retry-model", input_tokens=12, output_tokens=4)


class FakeAbortDuringRetryLLM:
    def __init__(self, *_args, **_kwargs):
        self.provider = "fake"

    def _resolve_model_target(self, *_args, **_kwargs):  # noqa: ANN001, ANN201
        return SimpleNamespace(provider="openai", model="gpt-4o")

    def chat_stream(  # noqa: ANN201
        self,
        messages,
        tools=None,
        max_tokens=4096,
        variant_override=None,
        model_override=None,
        session_cache_key=None,
    ):
        del messages, tools, max_tokens, variant_override, model_override, session_cache_key
        yield StreamEvent(type="error", content="429 Too Many Requests")


def test_retryable_model_error_sets_retry_status_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    FakeRetryLLM.attempts = 0
    observed_statuses: list[dict] = []

    def _fake_sleep(session_id: str, delay_ms: int) -> bool:
        del delay_ms
        observed_statuses.append(get_session_status(session_id))
        return True

    monkeypatch.setattr(agent_service, "LLMClient", FakeRetryLLM)
    monkeypatch.setattr(agent_service.session_retry, "sleep", _fake_sleep)
    monkeypatch.setattr(agent_service.session_retry, "delay", lambda attempt, error_value=None: 1)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "retry_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/retry_session/message",
        json={
            "parts": [{"type": "text", "text": "请继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "重试后成功" in prompt_resp.text
    assert "event: error" not in prompt_resp.text
    assert FakeRetryLLM.attempts == 2

    assert observed_statuses
    assert observed_statuses[0]["type"] == "retry"
    assert observed_statuses[0]["attempt"] == 1

    history = client.get("/session/retry_session/message").json()
    assert len(history) == 2
    assert _first_text_part(history[1]["parts"]) == "重试后成功。"
    retry_parts = [part for part in history[1]["parts"] if part["type"] == "retry"]
    assert retry_parts
    assert retry_parts[0]["attempt"] == 1
    assert retry_parts[0]["error"]["name"] == "APIError"
    assert retry_parts[0]["error"]["message"] == "429 Too Many Requests"
    assert retry_parts[0]["error"]["isRetryable"] is True


def test_abort_during_retry_persists_aborted_assistant_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(agent_service, "LLMClient", FakeAbortDuringRetryLLM)
    monkeypatch.setattr(agent_service.session_retry, "sleep", lambda session_id, delay_ms: False)
    monkeypatch.setattr(agent_service.session_retry, "delay", lambda attempt, error_value=None: 1)
    client = TestClient(_build_app())

    created = client.post(
        "/session",
        json={
            "id": "retry_abort_session",
            "directory": str(tmp_path),
            "workspace_path": str(tmp_path),
            "mode": "build",
        },
    )
    assert created.status_code == 200

    prompt_resp = client.post(
        "/session/retry_abort_session/message",
        json={
            "parts": [{"type": "text", "text": "请继续"}],
            "mode": "build",
            "workspace_path": str(tmp_path),
        },
    )
    assert prompt_resp.status_code == 200
    assert "会话已中止" in prompt_resp.text

    history = client.get("/session/retry_abort_session/message").json()
    assistant = history[1]
    assert assistant["info"]["error"]["name"] == "AbortedError"
    assert assistant["info"]["finish"] == "aborted"
    retry_parts = [part for part in assistant["parts"] if part["type"] == "retry"]
    assert retry_parts
    assert retry_parts[0]["error"]["name"] == "APIError"
    assert retry_parts[0]["error"]["message"] == "429 Too Many Requests"


def test_normalize_error_recognizes_auth_and_context_overflow() -> None:
    auth_error = normalize_error("401 Unauthorized: invalid API key")
    assert auth_error["name"] == "AuthError"
    assert auth_error["statusCode"] == 401
    assert auth_error["isRetryable"] is False

    overflow_error = normalize_error(
        {
            "message": "context length exceeded",
            "responseBody": '{"error":"too many tokens"}',
        }
    )
    assert overflow_error["name"] == "ContextOverflowError"
    assert overflow_error["responseBody"] == '{"error":"too many tokens"}'


def test_normalize_error_recognizes_connection_reset_retry_metadata() -> None:
    class _ConnectionResetError(Exception):
        code = "ECONNRESET"
        syscall = "read"

    normalized = normalize_error(_ConnectionResetError("socket hang up"))

    assert normalized["name"] == "APIError"
    assert normalized["message"] == "Connection reset by server"
    assert normalized["isRetryable"] is True
    assert normalized["metadata"] == {"code": "ECONNRESET", "syscall": "read"}


def test_normalize_error_parses_provider_context_overflow_body() -> None:
    normalized = normalize_error(
        {
            "message": "400 Bad Request",
            "responseBody": (
                '{"error":{"message":"This model\'s maximum context length is 128000 tokens.",'
                '"type":"invalid_request_error","code":"context_length_exceeded"}}'
            ),
        }
    )

    assert normalized["name"] == "ContextOverflowError"
    assert normalized["message"] == "This model's maximum context length is 128000 tokens."
    assert normalized["responseBody"]


def test_normalize_error_parses_provider_auth_and_retryable_bodies() -> None:
    auth_error = normalize_error(
        {
            "responseBody": (
                '{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'
            ),
            "statusCode": 401,
        }
    )
    assert auth_error["name"] == "AuthError"
    assert auth_error["message"] == "invalid x-api-key"
    assert auth_error["statusCode"] == 401

    retryable_error = normalize_error(
        {
            "responseBody": (
                '{"error":{"message":"rate limit exceeded","type":"rate_limit_error","code":"rate_limit"}}'
            ),
            "statusCode": 429,
        }
    )
    assert retryable_error["name"] == "APIError"
    assert retryable_error["message"] == "rate limit exceeded"
    assert retryable_error["isRetryable"] is True
    assert retryable_error["metadata"]["type"] == "rate_limit_error"
    assert retryable_error["metadata"]["code"] == "rate_limit"


def test_normalize_error_marks_missing_api_key_as_auth_error() -> None:
    normalized = normalize_error(RuntimeError("Missing API key"))

    assert normalized["name"] == "AuthError"
    assert normalized["message"] == "Missing API key"
    assert normalized["isRetryable"] is False


def test_normalize_error_marks_timeout_and_network_transport_errors_retryable() -> None:
    class _TimeoutError(Exception):
        pass

    class _ConnectError(Exception):
        pass

    timeout_error = normalize_error(_TimeoutError("request timed out while contacting upstream"))
    network_error = normalize_error(_ConnectError("network unreachable"))

    assert timeout_error["name"] == "APIError"
    assert timeout_error["isRetryable"] is True
    assert timeout_error["metadata"]["code"] == "TIMEOUT"

    assert network_error["name"] == "APIError"
    assert network_error["isRetryable"] is True
    assert network_error["metadata"]["code"] == "NETWORK_ERROR"


def test_normalize_error_maps_typed_sdk_exception_classes() -> None:
    AuthenticationError = type("AuthenticationError", (Exception,), {})
    PermissionDeniedError = type("PermissionDeniedError", (Exception,), {})
    RateLimitError = type("RateLimitError", (Exception,), {})
    TooManyRequestsError = type("TooManyRequestsError", (Exception,), {})
    ServiceUnavailableError = type("ServiceUnavailableError", (Exception,), {})
    TransportError = type("TransportError", (Exception,), {})
    APIConnectionError = type("APIConnectionError", (Exception,), {})
    DeadlineExceeded = type("DeadlineExceeded", (Exception,), {})
    APITimeoutError = type("APITimeoutError", (Exception,), {})

    auth_error = normalize_error(AuthenticationError("invalid token"))
    forbidden_error = normalize_error(PermissionDeniedError("permission denied"))
    rate_limit_error = normalize_error(RateLimitError("rate limit exceeded"))
    too_many_requests_error = normalize_error(TooManyRequestsError("too many requests"))
    service_unavailable_error = normalize_error(ServiceUnavailableError("service unavailable"))
    transport_error = normalize_error(TransportError("protocol broke"))
    connection_error = normalize_error(APIConnectionError("upstream disconnected"))
    deadline_error = normalize_error(DeadlineExceeded("deadline exceeded"))
    timeout_error = normalize_error(APITimeoutError("request timed out"))

    assert auth_error["name"] == "AuthError"
    assert auth_error["isRetryable"] is False

    assert forbidden_error["name"] == "AuthError"
    assert forbidden_error["isRetryable"] is False

    assert rate_limit_error["name"] == "APIError"
    assert rate_limit_error["isRetryable"] is True

    assert too_many_requests_error["name"] == "APIError"
    assert too_many_requests_error["isRetryable"] is True

    assert service_unavailable_error["name"] == "APIError"
    assert service_unavailable_error["isRetryable"] is True

    assert transport_error["name"] == "APIError"
    assert transport_error["isRetryable"] is True
    assert transport_error["metadata"]["code"] == "NETWORK_ERROR"

    assert connection_error["name"] == "APIError"
    assert connection_error["isRetryable"] is True
    assert connection_error["metadata"]["code"] == "NETWORK_ERROR"

    assert deadline_error["name"] == "APIError"
    assert deadline_error["isRetryable"] is True
    assert deadline_error["metadata"]["code"] == "TIMEOUT"

    assert timeout_error["name"] == "APIError"
    assert timeout_error["isRetryable"] is True
    assert timeout_error["metadata"]["code"] == "TIMEOUT"


def test_normalize_error_reads_structured_http_transport_error() -> None:
    normalized = normalize_error(
        ProviderHTTPError(
            "rate limit exceeded",
            status_code=429,
            response_body='{"error":{"message":"rate limit exceeded","type":"rate_limit_error","code":"rate_limit"}}',
            response_headers={"retry-after": "2"},
        )
    )

    assert normalized["name"] == "APIError"
    assert normalized["message"] == "rate limit exceeded"
    assert normalized["statusCode"] == 429
    assert normalized["responseHeaders"]["retry-after"] == "2"
    assert normalized["metadata"]["type"] == "rate_limit_error"


def test_normalize_error_rewrites_html_gateway_auth_pages() -> None:
    unauthorized = normalize_error(
        ProviderHTTPError(
            "401 Unauthorized",
            status_code=401,
            response_body="<!doctype html><html><body>proxy auth page</body></html>",
        )
    )
    assert unauthorized["name"] == "AuthError"
    assert "gateway or proxy" in unauthorized["message"]

    forbidden = normalize_error(
        ProviderHTTPError(
            "403 Forbidden",
            status_code=403,
            response_body="<html><body>blocked</body></html>",
        )
    )
    assert forbidden["name"] == "AuthError"
    assert "gateway or proxy" in forbidden["message"]


def test_normalize_error_marks_openai_404_transport_errors_as_retryable() -> None:
    normalized = normalize_error(
        ProviderHTTPError(
            "404 Not Found",
            status_code=404,
            metadata={"provider": "openai", "transport": "chat.completions"},
        )
    )

    assert normalized["name"] == "APIError"
    assert normalized["statusCode"] == 404
    assert normalized["isRetryable"] is True
    assert normalized["metadata"]["provider"] == "openai"
    assert normalized["providerID"] == "openai"
    assert normalized["transport"] == "chat.completions"
    assert normalized["transportKind"] == "chat.completions"


def test_normalize_error_parses_structured_stream_provider_errors() -> None:
    normalized = normalize_error(
        {
            "type": "error",
            "error": {
                "code": "invalid_prompt",
                "message": "Prompt violated tool schema.",
            },
        }
    )

    assert normalized["name"] == "APIError"
    assert normalized["message"] == "Prompt violated tool schema."
    assert normalized["isRetryable"] is False


def test_normalize_error_preserves_transport_runtime_metadata() -> None:
    normalized = normalize_error(
        {
            "message": "upstream temporarily unavailable",
            "statusCode": 503,
            "transport": "responses(raw-http)",
            "gateway": "vercel-ai-gateway",
            "bucket": "chat-runtime",
            "url": "https://example.test/v1/responses",
        }
    )

    assert normalized["name"] == "APIError"
    assert normalized["transport"] == "responses(raw-http)"
    assert normalized["transportKind"] == "responses"
    assert normalized["gateway"] == "vercel-ai-gateway"
    assert normalized["bucket"] == "chat-runtime"
    assert normalized["url"] == "https://example.test/v1/responses"
    assert normalized["metadata"]["transport"] == "responses(raw-http)"
    assert normalized["metadata"]["gateway"] == "vercel-ai-gateway"
    assert normalized["metadata"]["bucket"] == "chat-runtime"
    assert normalized["metadata"]["url"] == "https://example.test/v1/responses"


def test_retry_delay_prefers_retry_after_headers() -> None:
    assert session_retry.delay(
        1,
        {
            "message": "429 Too Many Requests",
            "responseHeaders": {"retry-after-ms": "2500"},
        },
    ) == 2500

    assert session_retry.delay(
        1,
        {
            "message": "429 Too Many Requests",
            "responseHeaders": {"retry-after": "3"},
        },
    ) == 3000


def test_retry_delay_uses_opencode_backoff_defaults_without_headers() -> None:
    assert session_retry.delay(1) == 2000
    assert session_retry.delay(2) == 4000
    assert session_retry.delay(3) == 8000


def test_retryable_does_not_retry_blocked_gateway_errors() -> None:
    assert (
        session_retry.retryable(
            {
                "message": "Your request was blocked by the upstream gateway",
                "statusCode": 503,
                "isRetryable": True,
            }
        )
        is None
    )

