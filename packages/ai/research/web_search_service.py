from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_RESULT_PATTERN = re.compile(
    r"<a rel=\"nofollow\" href=\"(?P<href>[^\"]+)\" class='result-link'>(?P<title>.*?)</a>"
    r"(?P<body>.*?)(?=<tr>\s*<td valign=\"top\">|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_PATTERN = re.compile(
    r"<td class='result-snippet'>\s*(?P<snippet>.*?)\s*</td>",
    re.IGNORECASE | re.DOTALL,
)
_LINK_TEXT_PATTERN = re.compile(
    r"<span class='link-text'>(?P<display>.*?)</span>",
    re.IGNORECASE | re.DOTALL,
)
_ZERO_CLICK_PATTERN = re.compile(
    r"Zero-click info:\s*<a[^>]+href=\"(?P<href>[^\"]+)\"[^>]*>(?P<title>.*?)</a>.*?"
    r"<tr>\s*<td>\s*(?P<snippet>.*?)(?:<a[^>]*>More at|</td>)",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_HTML_TAG_RE.sub(" ", text or ""))).strip()


def _decode_duckduckgo_href(raw_href: str) -> str:
    href = html.unescape(str(raw_href or "").strip())
    if not href:
        return ""
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


def parse_zero_click(payload: str) -> dict | None:
    match = _ZERO_CLICK_PATTERN.search(payload)
    if not match:
        return None
    title = _strip_html(match.group("title"))
    snippet = _strip_html(match.group("snippet"))
    url = _decode_duckduckgo_href(match.group("href"))
    if not title and not snippet:
        return None
    return {
        "title": title or url,
        "url": url,
        "snippet": snippet,
        "display_url": urlparse(url).netloc if url else "",
        "source": "duckduckgo_instant_answer",
    }


def parse_duckduckgo_results(payload: str, max_results: int) -> list[dict]:
    items: list[dict] = []
    for match in _RESULT_PATTERN.finditer(payload):
        block = match.group(0)
        if "result-sponsored" in block:
            continue

        url = _decode_duckduckgo_href(match.group("href"))
        if "duckduckgo.com/y.js?ad_domain=" in url:
            continue

        title = _strip_html(match.group("title"))
        if not title or not url:
            continue

        snippet_match = _SNIPPET_PATTERN.search(match.group("body"))
        display_match = _LINK_TEXT_PATTERN.search(match.group("body"))
        items.append(
            {
                "title": title,
                "url": url,
                "snippet": _strip_html(snippet_match.group("snippet")) if snippet_match else "",
                "display_url": _strip_html(display_match.group("display")) if display_match else urlparse(url).netloc,
                "source": "duckduckgo_lite",
            }
        )
        if len(items) >= max_results:
            break
    return items


def search_web(query: str, max_results: int = 8) -> dict:
    cleaned_query = str(query or "").strip()
    if not cleaned_query:
        raise ValueError("搜索关键词不能为空")

    limit = max(1, min(int(max_results), 10))
    response = httpx.get(
        _DDG_LITE_URL,
        params={"q": cleaned_query},
        timeout=15,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    instant_answer = parse_zero_click(response.text)
    items = parse_duckduckgo_results(response.text, limit)
    if not items and instant_answer:
        items = [instant_answer]

    return {
        "query": cleaned_query,
        "items": items,
        "count": len(items),
        "engine": "DuckDuckGo Lite",
        "instant_answer": instant_answer,
    }
