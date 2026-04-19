from __future__ import annotations

import html
import re
from urllib.parse import urlparse

import httpx

from packages.agent.tools.tool_runtime import ToolResult
from packages.ai.research.web_search_service import search_web as run_web_search

_WEBFETCH_MAX_TEXT_CHARS = 50000
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_DROP_RE = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _error_message(exc: Exception, fallback: str) -> str:
    message = str(exc or "").strip()
    return message or fallback


def _web_error_result(
    *,
    prefix: str,
    code: str,
    message: str,
    retryable: bool,
    status_code: int | None = None,
    query: str | None = None,
    url: str | None = None,
) -> ToolResult:
    error_payload: dict[str, object] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if status_code is not None:
        error_payload["status_code"] = int(status_code)
    payload: dict[str, object] = {
        "error": error_payload,
    }
    if query is not None:
        payload["query"] = query
    if url is not None:
        payload["url"] = url
    return ToolResult(
        success=False,
        summary=f"{prefix}：{message}",
        data=payload,
    )


def _normalize_web_exception(exc: Exception) -> tuple[str, str, bool, int | None]:
    if isinstance(exc, ValueError):
        return "invalid_input", _error_message(exc, "输入不合法"), False, None
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", _error_message(exc, "请求超时"), True, None
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = int(exc.response.status_code)
        if status_code == 404:
            code = "not_found"
        elif status_code == 429:
            code = "rate_limited"
        else:
            code = "http_error"
        retryable = status_code >= 500 or status_code in {408, 409, 425, 429}
        message = _error_message(exc, f"HTTP {status_code}")
        return code, message, retryable, status_code
    if isinstance(exc, httpx.RequestError):
        return "network_error", _error_message(exc, "网络请求失败"), True, None
    return "unknown_error", _error_message(exc, "未知错误"), False, None


def _search_web(query: str, max_results: int = 8) -> ToolResult:
    cleaned_query = query.strip()
    if not cleaned_query:
        return _web_error_result(
            prefix="网页搜索失败",
            code="invalid_input",
            message="搜索关键词不能为空",
            retryable=False,
            query=cleaned_query,
        )

    try:
        payload = run_web_search(cleaned_query, max_results=max_results)
    except Exception as exc:
        code, message, retryable, status_code = _normalize_web_exception(exc)
        return _web_error_result(
            prefix="网页搜索失败",
            code=code,
            message=message,
            retryable=retryable,
            status_code=status_code,
            query=cleaned_query,
        )

    return ToolResult(
        success=True,
        data=payload,
        summary=f"网页搜索找到 {payload.get('count', 0)} 条结果",
    )


def _websearch(query: str, max_results: int = 8) -> ToolResult:
    return _search_web(query, max_results=max_results)


def _extract_html_title(payload: str) -> str:
    match = _HTML_TITLE_RE.search(payload or "")
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def _html_to_text(payload: str) -> str:
    if not payload:
        return ""
    cleaned = _HTML_DROP_RE.sub(" ", payload)
    cleaned = re.sub(r"(?i)<br\\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|section|article|main|header|footer)>", "\n", cleaned)
    cleaned = html.unescape(_HTML_TAG_RE.sub(" ", cleaned))
    cleaned = cleaned.replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _webfetch(url: str, format: str = "markdown", timeout_sec: int = 30) -> ToolResult:
    cleaned_url = str(url or "").strip()
    if not cleaned_url.startswith(("http://", "https://")):
        return _web_error_result(
            prefix="网页抓取失败",
            code="invalid_input",
            message="URL 必须以 http:// 或 https:// 开头",
            retryable=False,
            url=cleaned_url,
        )

    fmt = str(format or "markdown").strip().lower()
    if fmt not in {"text", "markdown", "html"}:
        fmt = "markdown"

    timeout_value = max(5, min(int(timeout_sec or 30), 120))
    try:
        response = httpx.get(
            cleaned_url,
            timeout=timeout_value,
            follow_redirects=True,
            headers={
                "User-Agent": "ResearchOS/1.0 (+native-agent)",
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
            },
        )
        response.raise_for_status()
    except Exception as exc:
        code, message, retryable, status_code = _normalize_web_exception(exc)
        return _web_error_result(
            prefix="网页抓取失败",
            code=code,
            message=message,
            retryable=retryable,
            status_code=status_code,
            url=cleaned_url,
        )

    content_type = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
    text_like = (
        content_type.startswith("text/")
        or "json" in content_type
        or "xml" in content_type
        or content_type.endswith("+json")
    )
    if not text_like:
        return ToolResult(
            success=True,
            data={
                "url": cleaned_url,
                "final_url": str(response.url),
                "status_code": response.status_code,
                "content_type": content_type or "application/octet-stream",
                "binary": True,
                "size_bytes": len(response.content),
            },
            summary=f"已抓取二进制资源 {urlparse(str(response.url)).netloc}",
        )

    raw_text = response.text
    title = _extract_html_title(raw_text) if "html" in content_type else ""
    if fmt == "html":
        output = raw_text
    else:
        output = _html_to_text(raw_text) if "html" in content_type else raw_text.strip()
    truncated = len(output) > _WEBFETCH_MAX_TEXT_CHARS
    if truncated:
        output = output[:_WEBFETCH_MAX_TEXT_CHARS]

    return ToolResult(
        success=True,
        data={
            "url": cleaned_url,
            "final_url": str(response.url),
            "status_code": response.status_code,
            "content_type": content_type or "text/plain",
            "format": fmt,
            "title": title,
            "content": output,
            "truncated": truncated,
            "size_bytes": len(response.content),
        },
        summary=f"已抓取网页 {urlparse(str(response.url)).netloc}",
    )


def _codesearch(query: str, max_results: int = 8) -> ToolResult:
    cleaned_query = str(query or "").strip()
    if not cleaned_query:
        return _web_error_result(
            prefix="代码搜索失败",
            code="invalid_input",
            message="代码搜索关键词不能为空",
            retryable=False,
            query=cleaned_query,
        )

    tuned_query = cleaned_query
    lower_query = cleaned_query.lower()
    if "site:" not in lower_query:
        tuned_query = f"{cleaned_query} github docs api reference example"

    try:
        payload = run_web_search(tuned_query, max_results=max_results)
    except Exception as exc:
        code, message, retryable, status_code = _normalize_web_exception(exc)
        return _web_error_result(
            prefix="代码搜索失败",
            code=code,
            message=message,
            retryable=retryable,
            status_code=status_code,
            query=cleaned_query,
        )

    return ToolResult(
        success=True,
        data={
            **payload,
            "original_query": cleaned_query,
            "search_query": tuned_query,
        },
        summary=f"代码搜索找到 {payload.get('count', 0)} 条结果",
    )

