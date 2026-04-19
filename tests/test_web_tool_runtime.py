from __future__ import annotations

import httpx

from packages.agent import web_tool_runtime


def test_search_web_returns_typed_timeout_error(monkeypatch) -> None:
    def _raise_timeout(_query: str, max_results: int = 8):  # noqa: ANN001, ARG001
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(web_tool_runtime, "run_web_search", _raise_timeout)

    result = web_tool_runtime._search_web("opencode")

    assert result.success is False
    assert result.data["error"]["code"] == "timeout"
    assert result.data["error"]["retryable"] is True
    assert result.data["query"] == "opencode"


def test_webfetch_returns_typed_not_found_error(monkeypatch) -> None:
    request = httpx.Request("GET", "https://example.com/missing")
    response = httpx.Response(404, request=request)

    def _raise_not_found(*_args, **_kwargs):  # noqa: ANN001, ANN202
        raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)

    monkeypatch.setattr(web_tool_runtime.httpx, "get", _raise_not_found)

    result = web_tool_runtime._webfetch("https://example.com/missing")

    assert result.success is False
    assert result.data["error"]["code"] == "not_found"
    assert result.data["error"]["status_code"] == 404
    assert result.data["error"]["retryable"] is False
    assert result.data["url"] == "https://example.com/missing"


def test_codesearch_returns_typed_network_error(monkeypatch) -> None:
    def _raise_network(_query: str, max_results: int = 8):  # noqa: ANN001, ARG001
        request = httpx.Request("GET", "https://lite.duckduckgo.com/lite/")
        raise httpx.ConnectError("network down", request=request)

    monkeypatch.setattr(web_tool_runtime, "run_web_search", _raise_network)

    result = web_tool_runtime._codesearch("session_processor")

    assert result.success is False
    assert result.data["error"]["code"] == "network_error"
    assert result.data["error"]["retryable"] is True
    assert result.data["query"] == "session_processor"
