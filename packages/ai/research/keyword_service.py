"""
AI 检索词建议服务 - 自然语言 → 更适合实际检索的关键词
"""

from __future__ import annotations

import logging

from packages.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)

_VALID_SOURCE_SCOPE = {"hybrid", "arxiv", "openalex"}
_VALID_SEARCH_FIELD = {"all", "title", "keywords", "authors", "arxiv_id"}

SUGGEST_PROMPT = """\
你是学术论文检索策略专家。用户描述了一个研究方向，请生成 6-8 组高质量检索建议。

当前检索源：{source_scope}
当前搜索字段：{search_field}

要求：
1. 先把中文研究方向转成更适合实际检索的英文技术表达，再扩展常见别名、缩写、任务名、方法名或应用场景。
2. 每组输出：
   - name: 简短中文主题名
   - query: 可直接用于当前检索源的检索表达式
   - reason: 说明它覆盖的角度、变体或适用场景
3. 如果当前检索源是 hybrid 或 openalex：
   - query 必须是自然英文检索短语
   - 不要使用 arXiv API 语法，不要写 all: / ti: / abs:
   - 尽量使用 2-6 个核心英文词或 1-2 个短语组合
4. 如果当前检索源是 arxiv：
   - query 必须使用 arXiv API 检索语法
   - title 字段优先 ti:
   - authors 字段优先 au:
   - keywords 字段优先 abs: 或 all:
   - all 字段可混合 all:/ti:/abs:
5. 覆盖不同角度：核心任务、关键方法、热门别名、细分问题、近邻表达。
6. 避免空泛词，避免只返回中文，避免过长长句，避免过窄导致几乎搜不到。

用户描述：
{description}

请严格输出 JSON：
{{"suggestions":[{{"name":"...","query":"...","reason":"..."}}]}}
"""


def _normalize_source_scope(value: str | None) -> str:
    normalized = str(value or "hybrid").strip().lower()
    return normalized if normalized in _VALID_SOURCE_SCOPE else "hybrid"


def _normalize_search_field(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in _VALID_SEARCH_FIELD else "all"


def _clean_query_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _extract_items(parsed: object) -> list[dict]:
    items: list = []
    if isinstance(parsed, dict):
        for key in ("suggestions", "keywords", "items"):
            if isinstance(parsed.get(key), list):
                items = parsed[key]
                break
        if not items and all(k in parsed for k in ("name", "query")):
            items = [parsed]
    elif isinstance(parsed, list):
        items = parsed

    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        query = _clean_query_text(str(item.get("query", "")))
        if not query:
            continue
        normalized.append(
            {
                "name": _clean_query_text(str(item.get("name", ""))),
                "query": query,
                "reason": _clean_query_text(str(item.get("reason", ""))),
            }
        )
    return normalized


def _dedupe_items(items: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("query") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


class KeywordService:
    """将自然语言研究兴趣转换为更适合检索的关键词建议。"""

    def __init__(self) -> None:
        self.llm = LLMClient()

    def suggest(
        self,
        description: str,
        *,
        source_scope: str = "hybrid",
        search_field: str = "all",
    ) -> list[dict]:
        cleaned_description = str(description or "").strip()
        if not cleaned_description:
            return []

        normalized_scope = _normalize_source_scope(source_scope)
        normalized_field = _normalize_search_field(search_field)
        prompt = SUGGEST_PROMPT.format(
            description=cleaned_description,
            source_scope=normalized_scope,
            search_field=normalized_field,
        )
        result = self.llm.complete_json(
            prompt,
            stage="keyword_suggest",
            max_tokens=4096,
        )
        self.llm.trace_result(
            result,
            stage="keyword_suggest",
            prompt_digest=(
                f"suggest:{normalized_scope}:{normalized_field}:{cleaned_description[:80]}"
            ),
        )

        parsed = result.parsed_json
        if parsed is None:
            logger.warning("AI keyword suggestion JSON parse failed")
            return []

        suggestions = _dedupe_items(_extract_items(parsed))
        if not suggestions:
            return []

        validated = self._validate_suggestions(
            suggestions,
            source_scope=normalized_scope,
        )
        if validated:
            return validated[:6]
        return suggestions[:4]

    def _validate_suggestions(
        self,
        suggestions: list[dict],
        *,
        source_scope: str,
    ) -> list[dict]:
        from packages.agent import research_tool_runtime

        ranked: list[tuple[int, dict]] = []
        for item in suggestions[:8]:
            query = str(item.get("query") or "").strip()
            if not query:
                continue
            try:
                result = research_tool_runtime._search_literature(
                    query=query,
                    max_results=8,
                    source_scope=source_scope,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.info("Keyword suggestion validation failed for %s: %s", query, exc)
                continue
            if not result.success:
                continue
            papers = result.data.get("papers") if isinstance(result.data, dict) else None
            hit_count = (
                len(papers) if isinstance(papers, list) else int(result.data.get("count") or 0)
            )
            if hit_count <= 0:
                continue

            reason = str(item.get("reason") or "").strip()
            if f"验证命中 {hit_count} 篇" not in reason:
                reason = f"{reason} · 验证命中 {hit_count} 篇".strip(" ·")
            ranked.append(
                (
                    hit_count,
                    {
                        "name": item.get("name") or query,
                        "query": query,
                        "reason": reason,
                    },
                )
            )

        ranked.sort(key=lambda item: (-item[0], len(str(item[1].get("query") or ""))))
        return [item for _, item in ranked]
