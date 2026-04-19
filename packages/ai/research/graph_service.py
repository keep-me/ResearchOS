"""
图谱分析服务 - 引用树、时间线、质量评估、演化分析、综述生成
@author Bamzc
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from packages.ai.paper.prompts import (
    build_evolution_prompt,
    build_paper_wiki_prompt,
    build_research_gaps_prompt,
    build_survey_prompt,
    build_wiki_outline_prompt,
    build_wiki_section_prompt,
)
from packages.ai.research.wiki_context import WikiContextGatherer
from packages.config import get_settings
from packages.domain.schemas import PaperCreate
from packages.integrations.citation_provider import CitationProvider
from packages.integrations.llm_client import LLMClient
from packages.integrations.semantic_scholar_client import RichCitationInfo
from packages.storage.db import session_scope
from packages.storage.models import PaperTopic, TopicSubscription
from packages.storage.repositories import (
    CitationRepository,
    PaperRepository,
    TopicRepository,
)

logger = logging.getLogger(__name__)
_CITATION_TITLE_ZH_CACHE: dict[str, str] = {}
_CITATION_DETAIL_CACHE_VERSION = 1
_CITATION_DETAIL_CACHE_TTL = timedelta(days=14)


class GraphService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.citations = CitationProvider(
            openalex_email=self.settings.openalex_email,
            scholar_api_key=self.settings.semantic_scholar_api_key,
        )
        # 保留 self.scholar 兼容别名
        self.scholar = self.citations
        self.llm = LLMClient()
        self.context_gatherer = WikiContextGatherer()

    def _active_skim_model(self) -> str:
        return self.llm._config().model_skim

    def _active_deep_model(self) -> str:
        return self.llm._config().model_deep

    @staticmethod
    def _normalize_title_cache_key(title: str | None) -> str:
        return re.sub(r"\s+", " ", str(title or "").strip())

    @staticmethod
    def _should_translate_title(title: str | None) -> bool:
        text = str(title or "").strip()
        return bool(text) and not re.search(r"[\u4e00-\u9fff]", text)

    def _citation_detail_cache_dir(self) -> Path:
        root = self.settings.pdf_storage_root.parent / "cache" / "citation-detail"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _citation_detail_cache_path(self, paper_id: str) -> Path:
        safe_paper_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(paper_id).strip()) or "unknown"
        return self._citation_detail_cache_dir() / f"{safe_paper_id}.json"

    @staticmethod
    def _deserialize_rich_citation_items(items: list[dict] | None) -> list[RichCitationInfo]:
        restored: list[RichCitationInfo] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            year = item.get("year")
            citation_count = item.get("citation_count")
            try:
                normalized_year = int(year) if year is not None else None
            except (TypeError, ValueError):
                normalized_year = None
            try:
                normalized_citation_count = int(citation_count) if citation_count is not None else None
            except (TypeError, ValueError):
                normalized_citation_count = None
            restored.append(
                RichCitationInfo(
                    scholar_id=str(item.get("scholar_id") or "").strip() or None,
                    title=str(item.get("title") or "").strip(),
                    year=normalized_year,
                    venue=str(item.get("venue") or "").strip() or None,
                    citation_count=normalized_citation_count,
                    arxiv_id=str(item.get("arxiv_id") or "").strip() or None,
                    abstract=str(item.get("abstract") or "").strip() or None,
                    direction=str(item.get("direction") or "reference").strip() or "reference",
                )
            )
        return restored

    def _read_cached_rich_citations(
        self,
        paper_id: str,
        *,
        allow_stale: bool = False,
    ) -> list[RichCitationInfo] | None:
        cache_path = self._citation_detail_cache_path(paper_id)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Failed to read citation detail cache for %s: %s", paper_id, exc)
            return None
        if not isinstance(payload, dict):
            return None
        if int(payload.get("version") or 0) != _CITATION_DETAIL_CACHE_VERSION:
            return None
        saved_at_raw = str(payload.get("saved_at") or "").strip()
        try:
            saved_at = datetime.fromisoformat(saved_at_raw) if saved_at_raw else None
        except ValueError:
            saved_at = None
        if saved_at is None:
            return None
        if saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=UTC)
        is_fresh = (datetime.now(UTC) - saved_at) <= _CITATION_DETAIL_CACHE_TTL
        if not allow_stale and not is_fresh:
            return None
        items = payload.get("items")
        if not isinstance(items, list):
            return None
        return self._deserialize_rich_citation_items(items)

    def _write_cached_rich_citations(
        self,
        paper_id: str,
        *,
        source_title: str,
        source_arxiv_id: str | None,
        items: list[RichCitationInfo],
    ) -> None:
        cache_path = self._citation_detail_cache_path(paper_id)
        payload = {
            "version": _CITATION_DETAIL_CACHE_VERSION,
            "saved_at": datetime.now(UTC).isoformat(),
            "paper_id": str(paper_id),
            "paper_title": str(source_title or "").strip(),
            "paper_arxiv_id": str(source_arxiv_id or "").strip() or None,
            "items": [asdict(item) for item in items],
        }
        try:
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("Failed to write citation detail cache for %s: %s", paper_id, exc)

    def _load_rich_citations(
        self,
        paper_id: str,
        *,
        source_title: str,
        source_arxiv_id: str | None,
        force_refresh: bool = False,
    ) -> list[RichCitationInfo]:
        if not force_refresh:
            cached = self._read_cached_rich_citations(paper_id)
            if cached is not None:
                return cached

        stale_cached = None if force_refresh else self._read_cached_rich_citations(paper_id, allow_stale=True)
        try:
            rich_list = self.scholar.fetch_rich_citations(
                source_title,
                ref_limit=50,
                cite_limit=50,
                arxiv_id=source_arxiv_id,
            )
        except Exception as exc:
            logger.warning("fetch_rich_citations failed: %s", exc)
            if stale_cached is not None:
                logger.info("Using stale citation detail cache for %s", paper_id)
                return stale_cached
            rich_list = []

        self._write_cached_rich_citations(
            paper_id,
            source_title=source_title,
            source_arxiv_id=source_arxiv_id,
            items=rich_list,
        )
        return rich_list

    def _translate_citation_titles(self, titles: list[str], max_titles: int = 60) -> dict[str, str]:
        translated: dict[str, str] = {}
        pending: list[str] = []
        seen: set[str] = set()

        for raw_title in titles:
            key = self._normalize_title_cache_key(raw_title)
            if not key or key in seen:
                continue
            seen.add(key)
            cached = _CITATION_TITLE_ZH_CACHE.get(key)
            if cached:
                translated[raw_title] = cached
                continue
            if self._should_translate_title(raw_title):
                pending.append(raw_title)

        for offset in range(0, min(len(pending), max_titles), 12):
            chunk = pending[offset : offset + 12]
            prompt = (
                "请将下面论文标题翻译成简体中文，只输出 JSON 对象，格式为："
                '{"items":[{"index":1,"title_zh":"..."}]}。\n'
                "要求：\n"
                "1. 保留模型名、数据集名、缩写和专有名词。\n"
                "2. 译文简洁准确，不要附加解释。\n"
                "3. 无法准确翻译时返回尽量自然的中文短句。\n\n"
                + "\n".join(f"{idx}. {title}" for idx, title in enumerate(chunk, start=1))
            )
            try:
                result = self.llm.complete_json(
                    prompt,
                    stage="citation_title_translate",
                    max_tokens=1200,
                    max_retries=1,
                )
            except Exception as exc:
                logger.debug("citation title translation failed: %s", exc)
                continue

            parsed = result.parsed_json or {}
            items = parsed.get("items") if isinstance(parsed, dict) else None
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("index", 0)) - 1
                except (TypeError, ValueError):
                    continue
                if index < 0 or index >= len(chunk):
                    continue
                source_title = chunk[index]
                title_zh = str(item.get("title_zh", "") or "").strip()
                if not title_zh:
                    continue
                translated[source_title] = title_zh[:300]
                _CITATION_TITLE_ZH_CACHE[self._normalize_title_cache_key(source_title)] = title_zh[:300]

        return translated

    @staticmethod
    def _contains_chinese(text: str | None) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))

    @staticmethod
    def _should_translate_free_text(text: str | None) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if GraphService._contains_chinese(raw):
            return False
        if re.fullmatch(r"[Pp]\d+", raw):
            return False
        if re.fullmatch(r"[a-z]{2}\.[A-Za-z]+", raw):
            return False
        if re.fullmatch(r"[\d\W_]+", raw):
            return False
        return bool(re.search(r"[A-Za-z]", raw))

    def _translate_texts_to_zh(self, texts: list[str], max_items: int = 80) -> dict[str, str]:
        unique_texts: list[str] = []
        seen: set[str] = set()
        for text in texts:
            raw = str(text or "").strip()
            if not raw or raw in seen:
                continue
            if not self._should_translate_free_text(raw):
                continue
            seen.add(raw)
            unique_texts.append(raw)
            if len(unique_texts) >= max_items:
                break

        translated: dict[str, str] = {}
        for offset in range(0, len(unique_texts), 16):
            chunk = unique_texts[offset : offset + 16]
            prompt = (
                "请将下列文本翻译成简体中文，只输出 JSON 对象："
                '{"items":[{"index":1,"text_zh":"..."}]}。\n'
                "要求：\n"
                "1. 保留术语、模型名、数据集名、缩写和专有名词。\n"
                "2. 译文自然、简洁，不添加额外解释。\n"
                "3. 如果原文已经是中文则原样返回。\n\n"
                + "\n".join(f"{idx}. {line}" for idx, line in enumerate(chunk, start=1))
            )
            try:
                result = self.llm.complete_json(
                    prompt,
                    stage="insight_translate",
                    max_tokens=1800,
                    max_retries=1,
                )
            except Exception as exc:
                logger.debug("insight text translation failed: %s", exc)
                continue

            parsed = result.parsed_json or {}
            items = parsed.get("items") if isinstance(parsed, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("index", 0)) - 1
                except (TypeError, ValueError):
                    continue
                if index < 0 or index >= len(chunk):
                    continue
                text_zh = str(item.get("text_zh", "") or "").strip()
                if not text_zh:
                    continue
                translated[chunk[index]] = text_zh[:600]
        return translated

    def _normalize_evolution_summary(self, parsed: object) -> dict:
        default = {
            "trend_summary": "数据样本不足，建议增加领域样本后重试。",
            "phase_shift_signals": "阶段变化信号暂不明显。",
            "next_week_focus": "建议继续跟踪最新论文与方法变体。",
        }
        if not isinstance(parsed, dict):
            return default

        def _to_text(value: object) -> str:
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, list):
                return "；".join(
                    str(item).strip()
                    for item in value
                    if str(item).strip()
                )
            return ""

        trend = _to_text(parsed.get("trend_summary")) or default["trend_summary"]
        phase = _to_text(parsed.get("phase_shift_signals")) or default["phase_shift_signals"]
        focus = _to_text(parsed.get("next_week_focus")) or default["next_week_focus"]
        translated = self._translate_texts_to_zh([trend, phase, focus], max_items=6)
        return {
            "trend_summary": translated.get(trend, trend),
            "phase_shift_signals": translated.get(phase, phase),
            "next_week_focus": translated.get(focus, focus),
        }

    def _normalize_research_gap_analysis(self, parsed: object) -> dict:
        if not isinstance(parsed, dict):
            parsed = {}

        raw_gaps = parsed.get("research_gaps", [])
        research_gaps: list[dict] = []
        if isinstance(raw_gaps, list):
            for item in raw_gaps:
                if not isinstance(item, dict):
                    continue
                gap_title = str(item.get("gap_title", "") or "").strip()
                description = str(item.get("description", "") or "").strip()
                if not gap_title and not description:
                    continue
                difficulty = str(item.get("difficulty", "medium")).strip().lower()
                if difficulty not in {"easy", "medium", "hard"}:
                    difficulty = "medium"
                try:
                    confidence = float(item.get("confidence", 0.55))
                except (TypeError, ValueError):
                    confidence = 0.55
                confidence = max(0.0, min(1.0, confidence))
                research_gaps.append(
                    {
                        "gap_title": gap_title,
                        "description": description,
                        "evidence": str(item.get("evidence", "") or "").strip(),
                        "potential_impact": str(item.get("potential_impact", "") or "").strip(),
                        "suggested_approach": str(item.get("suggested_approach", "") or "").strip(),
                        "difficulty": difficulty,
                        "confidence": confidence,
                    }
                )

        method_comparison = parsed.get("method_comparison", {})
        if not isinstance(method_comparison, dict):
            method_comparison = {}
        dimensions = method_comparison.get("dimensions", [])
        if not isinstance(dimensions, list):
            dimensions = []
        methods = method_comparison.get("methods", [])
        if not isinstance(methods, list):
            methods = []
        underexplored = method_comparison.get("underexplored_combinations", [])
        if not isinstance(underexplored, list):
            underexplored = []

        cleaned_methods: list[dict] = []
        for item in methods:
            if not isinstance(item, dict):
                continue
            scores = item.get("scores", {})
            if not isinstance(scores, dict):
                scores = {}
            papers = item.get("papers", [])
            if not isinstance(papers, list):
                papers = []
            cleaned_methods.append(
                {
                    "name": str(item.get("name", "") or "").strip(),
                    "scores": {str(k): str(v) for k, v in scores.items()},
                    "papers": [str(x) for x in papers if str(x).strip()],
                }
            )

        trend_analysis = parsed.get("trend_analysis", {})
        if not isinstance(trend_analysis, dict):
            trend_analysis = {}
        hot_directions = trend_analysis.get("hot_directions", [])
        if not isinstance(hot_directions, list):
            hot_directions = []
        declining_areas = trend_analysis.get("declining_areas", [])
        if not isinstance(declining_areas, list):
            declining_areas = []
        emerging_opportunities = trend_analysis.get("emerging_opportunities", [])
        if not isinstance(emerging_opportunities, list):
            emerging_opportunities = []

        overall_summary = str(parsed.get("overall_summary", "") or "").strip()
        if not overall_summary:
            overall_summary = "数据不足，无法完成分析。"

        candidates: list[str] = [overall_summary]
        for gap in research_gaps:
            candidates.extend(
                [
                    gap["gap_title"],
                    gap["description"],
                    gap["evidence"],
                    gap["potential_impact"],
                    gap["suggested_approach"],
                ]
            )
        candidates.extend(str(x) for x in dimensions if str(x).strip())
        candidates.extend(str(x) for x in underexplored if str(x).strip())
        candidates.extend(str(x) for x in hot_directions if str(x).strip())
        candidates.extend(str(x) for x in declining_areas if str(x).strip())
        candidates.extend(str(x) for x in emerging_opportunities if str(x).strip())
        for method in cleaned_methods:
            candidates.append(method["name"])
            for key, value in method["scores"].items():
                candidates.append(str(key))
                candidates.append(str(value))

        translated = self._translate_texts_to_zh(candidates, max_items=140)

        def _tr(text: str) -> str:
            value = str(text or "").strip()
            if not value:
                return value
            return translated.get(value, value)

        for gap in research_gaps:
            gap["gap_title"] = _tr(gap["gap_title"])
            gap["description"] = _tr(gap["description"])
            gap["evidence"] = _tr(gap["evidence"])
            gap["potential_impact"] = _tr(gap["potential_impact"])
            gap["suggested_approach"] = _tr(gap["suggested_approach"])

        translated_dimensions = [_tr(str(x)) for x in dimensions if str(x).strip()]
        translated_underexplored = [_tr(str(x)) for x in underexplored if str(x).strip()]
        translated_hot = [_tr(str(x)) for x in hot_directions if str(x).strip()]
        translated_declining = [_tr(str(x)) for x in declining_areas if str(x).strip()]
        translated_emerging = [_tr(str(x)) for x in emerging_opportunities if str(x).strip()]

        for method in cleaned_methods:
            method["name"] = _tr(method["name"])
            method["scores"] = {
                _tr(str(k)): _tr(str(v))
                for k, v in method["scores"].items()
            }

        return {
            "research_gaps": research_gaps,
            "method_comparison": {
                "dimensions": translated_dimensions,
                "methods": cleaned_methods,
                "underexplored_combinations": translated_underexplored,
            },
            "trend_analysis": {
                "hot_directions": translated_hot,
                "declining_areas": translated_declining,
                "emerging_opportunities": translated_emerging,
            },
            "overall_summary": _tr(overall_summary),
        }

    @staticmethod
    def _is_placeholder_research_summary(text: str | None) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return True
        lowered = raw.lower()
        placeholders = (
            "数据不足",
            "无法完成分析",
            "样本不足",
            "insufficient",
            "not enough",
        )
        return any(token in lowered for token in placeholders)

    def _should_use_research_gap_fallback(self, parsed: dict, network_stats: dict) -> bool:
        try:
            total_papers = int(network_stats.get("total_papers", 0) or 0)
        except (TypeError, ValueError):
            total_papers = 0
        if total_papers <= 0:
            return False

        research_gaps = parsed.get("research_gaps", [])
        valid_gaps = [
            g for g in research_gaps
            if isinstance(g, dict)
            and (
                str(g.get("gap_title", "") or "").strip()
                or str(g.get("description", "") or "").strip()
            )
        ] if isinstance(research_gaps, list) else []

        method_comparison = parsed.get("method_comparison", {})
        methods = (
            method_comparison.get("methods", [])
            if isinstance(method_comparison, dict)
            else []
        )
        methods_count = len(methods) if isinstance(methods, list) else 0

        trend_analysis = parsed.get("trend_analysis", {})
        has_trend = False
        if isinstance(trend_analysis, dict):
            has_trend = any(
                isinstance(trend_analysis.get(key), list)
                and len(trend_analysis.get(key) or []) > 0
                for key in (
                    "hot_directions",
                    "declining_areas",
                    "emerging_opportunities",
                )
            )

        summary = str(parsed.get("overall_summary", "") or "").strip()

        if total_papers >= 5 and len(valid_gaps) == 0:
            return True
        if total_papers >= 3 and self._is_placeholder_research_summary(summary):
            if len(valid_gaps) == 0 or (methods_count == 0 and not has_trend):
                return True
        return False

    def _build_research_gap_fallback(
        self,
        keyword: str,
        papers_data: list[dict],
        network_stats: dict,
    ) -> dict:
        def _safe_int(value: object) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def _safe_float(value: object) -> float:
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def _title_from_paper(paper: dict) -> str:
            title = str(paper.get("title", "") or "").strip()
            if len(title) <= 42:
                return title
            return f"{title[:39]}..."

        def _sample_titles(items: list[dict], max_count: int = 3) -> str:
            titles = []
            seen: set[str] = set()
            for item in items:
                title = _title_from_paper(item)
                if not title or title in seen:
                    continue
                seen.add(title)
                titles.append(title)
                if len(titles) >= max_count:
                    break
            return "、".join(titles)

        def _score_level(value: float, high: float, medium: float) -> str:
            if value >= high:
                return "强"
            if value >= medium:
                return "中"
            return "弱"

        total_papers = max(_safe_int(network_stats.get("total_papers", 0)), len(papers_data))
        edge_count = _safe_int(network_stats.get("edge_count", 0))
        density = _safe_float(network_stats.get("density", 0.0))
        connected_ratio = _safe_float(network_stats.get("connected_ratio", 0.0))

        isolated_papers = [
            p for p in papers_data
            if _safe_int(p.get("indegree", 0)) == 0 and _safe_int(p.get("outdegree", 0)) == 0
        ]
        low_connected_papers = [
            p for p in papers_data
            if (_safe_int(p.get("indegree", 0)) + _safe_int(p.get("outdegree", 0))) <= 1
        ]

        years = [
            _safe_int(p.get("year", 0))
            for p in papers_data
            if _safe_int(p.get("year", 0)) > 1900
        ]
        latest_year = max(years) if years else None
        recent_papers = [
            p for p in papers_data
            if latest_year is not None and _safe_int(p.get("year", 0)) >= latest_year - 1
        ]

        keyword_counter: Counter[str] = Counter()
        keyword_display: dict[str, str] = {}
        keyword_paper_map: dict[str, list[str]] = defaultdict(list)
        keyword_years: dict[str, list[int]] = defaultdict(list)
        paper_keywords_norm: list[set[str]] = []

        for paper in papers_data:
            kw_values = paper.get("keywords", [])
            if not isinstance(kw_values, list):
                kw_values = []
            normalized_set: set[str] = set()
            paper_title = str(paper.get("title", "") or "").strip()
            paper_year = _safe_int(paper.get("year", 0))
            for kw in kw_values:
                raw = str(kw or "").strip()
                if len(raw) < 2:
                    continue
                norm = raw.lower()
                if norm in {"and", "or", "not"}:
                    continue
                normalized_set.add(norm)
                keyword_display.setdefault(norm, raw)
            for norm in normalized_set:
                keyword_counter[norm] += 1
                if paper_title:
                    keyword_paper_map[norm].append(paper_title)
                if paper_year > 1900:
                    keyword_years[norm].append(paper_year)
            paper_keywords_norm.append(normalized_set)

        ranked_keywords = [kw for kw, _ in keyword_counter.most_common(10)]
        hot_keywords = [keyword_display.get(kw, kw) for kw in ranked_keywords[:4]]
        rare_keywords = [kw for kw in ranked_keywords if keyword_counter[kw] == 1][:6]

        research_gaps: list[dict] = []
        isolated_ratio = len(isolated_papers) / max(total_papers, 1)
        if len(isolated_papers) >= 2 and isolated_ratio >= 0.12:
            examples = _sample_titles(isolated_papers, max_count=3)
            research_gaps.append(
                {
                    "gap_title": "跨方向连接与知识传递不足",
                    "description": (
                        "当前主题内存在较多彼此没有引用连接的论文簇，说明不同子方向之间缺少共同评测基线、"
                        "术语映射或方法迁移研究，导致知识难以在子社区间传播。"
                    ),
                    "evidence": (
                        f"库内样本 {total_papers} 篇中有 {len(isolated_papers)} 篇孤立论文；"
                        + (f"典型样本包括：{examples}。" if examples else "")
                    ),
                    "potential_impact": "若建立跨方向对齐基准，可显著提升研究复用效率并减少重复工作。",
                    "suggested_approach": "优先构建统一任务定义与共享评测协议，补充跨子方向对比实验。",
                    "difficulty": "medium",
                    "confidence": 0.74,
                }
            )

        if density < 0.008 or connected_ratio < 0.45:
            combo = " + ".join(hot_keywords[:2]) if len(hot_keywords) >= 2 else keyword
            research_gaps.append(
                {
                    "gap_title": "方法融合与系统化评估不足",
                    "description": (
                        "领域网络整体稀疏，说明方法多以各自路线独立推进，融合型研究与统一评估报告偏少。"
                    ),
                    "evidence": (
                        f"引用网络密度为 {density:.4f}，连通率为 {connected_ratio * 100:.1f}% ，"
                        "显示方法之间互证关系偏弱。"
                    ),
                    "potential_impact": "填补该空白有助于形成可复现、可比较的技术路线图。",
                    "suggested_approach": f"围绕 {combo} 开展组合方法实验，并引入统一消融与统计检验。",
                    "difficulty": "hard",
                    "confidence": 0.71,
                }
            )

        if rare_keywords:
            rare_display = [keyword_display.get(kw, kw) for kw in rare_keywords[:4]]
            rare_text = "、".join(rare_display)
            research_gaps.append(
                {
                    "gap_title": "长尾问题验证不足",
                    "description": (
                        "若干仅出现一次的细分关键词尚未形成连续研究线索，可能代表潜在高价值但低关注方向。"
                    ),
                    "evidence": (
                        f"长尾关键词占比较高，代表性主题包括：{rare_text}。"
                    ),
                    "potential_impact": "系统探索长尾主题可发现新的性能瓶颈与应用场景。",
                    "suggested_approach": "以长尾主题为中心补充小规模基准集，并与主流方法做迁移对比。",
                    "difficulty": "easy",
                    "confidence": 0.66,
                }
            )

        recent_ratio = len(recent_papers) / max(total_papers, 1)
        if latest_year is not None and total_papers >= 8 and recent_ratio >= 0.55:
            research_gaps.append(
                {
                    "gap_title": "纵向比较与复现基线不足",
                    "description": (
                        "近两年论文占比较高，但跨年份可比实验较少，难以判断改进是方法创新还是数据/设置差异导致。"
                    ),
                    "evidence": (
                        f"最近两年论文占比约 {recent_ratio * 100:.1f}% ，"
                        "显示研究集中于近期增量改进。"
                    ),
                    "potential_impact": "建立纵向基线可提升结论可信度并减少“看似提升”误判。",
                    "suggested_approach": "选取代表性经典方法与最新方法，在同一协议下做跨年份复现实验。",
                    "difficulty": "medium",
                    "confidence": 0.69,
                }
            )

        if not research_gaps and low_connected_papers:
            examples = _sample_titles(low_connected_papers, max_count=3)
            research_gaps.append(
                {
                    "gap_title": "低连通论文群的系统梳理不足",
                    "description": "部分论文与主干引用网络联系较弱，缺少统一框架解释其技术价值与边界条件。",
                    "evidence": (
                        f"低连通论文数量为 {len(low_connected_papers)}；"
                        + (f"示例：{examples}。" if examples else "")
                    ),
                    "potential_impact": "梳理低连通论文可发现被忽视的方法分支并拓展问题定义。",
                    "suggested_approach": "对低连通论文做专题复现与误差分析，归纳其适用场景。",
                    "difficulty": "medium",
                    "confidence": 0.61,
                }
            )

        research_gaps = research_gaps[:5]

        dimensions = ["学术影响", "网络连通", "研究成熟度"]
        methods: list[dict] = []
        for kw in ranked_keywords[:4]:
            related: list[dict] = []
            for idx, paper in enumerate(papers_data):
                if kw in paper_keywords_norm[idx]:
                    related.append(paper)
            if not related:
                continue

            avg_indegree = sum(_safe_int(p.get("indegree", 0)) for p in related) / max(len(related), 1)
            avg_link = sum(
                _safe_int(p.get("indegree", 0)) + _safe_int(p.get("outdegree", 0))
                for p in related
            ) / max(len(related), 1)
            if latest_year is not None:
                recent_share = sum(
                    1 for p in related if _safe_int(p.get("year", 0)) >= latest_year - 1
                ) / max(len(related), 1)
            else:
                recent_share = 0.5

            sample_titles = []
            for title in keyword_paper_map.get(kw, [])[:2]:
                trimmed = title if len(title) <= 24 else f"{title[:21]}..."
                sample_titles.append(trimmed)
            if not sample_titles:
                sample_titles = ["库内样本"]

            methods.append(
                {
                    "name": keyword_display.get(kw, kw),
                    "scores": {
                        "学术影响": _score_level(avg_indegree, high=2.5, medium=1.0),
                        "网络连通": _score_level(avg_link, high=3.0, medium=1.5),
                        "研究成熟度": _score_level(1 - recent_share, high=0.6, medium=0.35),
                    },
                    "papers": sample_titles,
                }
            )

        if not methods:
            methods = [
                {
                    "name": "核心方向样本",
                    "scores": {
                        "学术影响": _score_level(edge_count / max(total_papers, 1), high=2.0, medium=0.8),
                        "网络连通": _score_level(connected_ratio * 4, high=2.4, medium=1.2),
                        "研究成熟度": "中",
                    },
                    "papers": ["库内论文集合"],
                }
            ]

        underexplored_combinations: list[str] = []
        top_for_pair = ranked_keywords[:4]
        for i in range(len(top_for_pair)):
            for j in range(i + 1, len(top_for_pair)):
                a = top_for_pair[i]
                b = top_for_pair[j]
                papers_a = set(keyword_paper_map.get(a, []))
                papers_b = set(keyword_paper_map.get(b, []))
                overlap = len(papers_a & papers_b)
                if overlap <= 1:
                    underexplored_combinations.append(
                        f"{keyword_display.get(a, a)} + {keyword_display.get(b, b)}"
                    )
                if len(underexplored_combinations) >= 3:
                    break
            if len(underexplored_combinations) >= 3:
                break
        if not underexplored_combinations and len(top_for_pair) >= 2:
            underexplored_combinations.append(
                f"{keyword_display.get(top_for_pair[0], top_for_pair[0])} + "
                f"{keyword_display.get(top_for_pair[1], top_for_pair[1])}"
            )

        hot_directions = [
            f"{keyword_display.get(kw, kw)}（{keyword_counter[kw]}篇）"
            for kw in ranked_keywords[:3]
        ]

        declining_areas: list[str] = []
        if latest_year is not None:
            for kw in ranked_keywords:
                years_for_kw = keyword_years.get(kw, [])
                if len(years_for_kw) >= 2 and max(years_for_kw) <= latest_year - 2:
                    declining_areas.append(keyword_display.get(kw, kw))
                if len(declining_areas) >= 3:
                    break

        emerging_opportunities: list[str] = []
        for kw in rare_keywords[:3]:
            emerging_opportunities.append(f"{keyword_display.get(kw, kw)} 与主流框架融合")
        if not emerging_opportunities and hot_keywords:
            emerging_opportunities.append(f"{hot_keywords[0]} 的跨场景泛化验证")

        summary_parts = [
            (
                f"基于库内 {total_papers} 篇论文与 {edge_count} 条引用边，"
                f"当前主题网络连通率为 {connected_ratio * 100:.1f}% ，密度为 {density:.4f}。"
            )
        ]
        if research_gaps:
            summary_parts.append(
                f"已识别 {len(research_gaps)} 个优先研究空白，重点集中在「{research_gaps[0]['gap_title']}」等方向。"
            )
        if hot_directions:
            summary_parts.append(f"当前热点集中在：{'、'.join(hot_directions[:3])}。")
        if underexplored_combinations:
            summary_parts.append(
                f"建议优先尝试未充分覆盖的组合：{'、'.join(underexplored_combinations[:2])}。"
            )
        overall_summary = "".join(summary_parts)

        return {
            "research_gaps": research_gaps,
            "method_comparison": {
                "dimensions": dimensions,
                "methods": methods,
                "underexplored_combinations": underexplored_combinations,
            },
            "trend_analysis": {
                "hot_directions": hot_directions,
                "declining_areas": declining_areas,
                "emerging_opportunities": emerging_opportunities,
            },
            "overall_summary": overall_summary,
        }

    def sync_citations_for_paper(
        self, paper_id: str, limit: int = 8
    ) -> dict:
        with session_scope() as session:
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)
            source = paper_repo.get_by_id(paper_id)
            lib_by_title = self._build_library_title_map(
                paper_repo.list_all(limit=50000)
            )
            edges = self.scholar.fetch_edges_by_title(
                source.title,
                limit=limit,
                arxiv_id=self._lookup_remote_arxiv_id(source.arxiv_id),
            )
            inserted = 0
            for edge in edges:
                src = self._match_or_create_paper(
                    paper_repo,
                    edge.source_title,
                    lib_by_title,
                )
                dst = self._match_or_create_paper(
                    paper_repo,
                    edge.target_title,
                    lib_by_title,
                )
                cit_repo.upsert_edge(
                    src.id, dst.id, context=edge.context
                )
                inserted += 1
            return {
                "paper_id": paper_id,
                "edges_inserted": inserted,
            }

    def sync_citations_for_topic(
        self,
        topic_id: str,
        paper_limit: int = 30,
        edge_limit_per_paper: int = 6,
    ) -> dict:
        total_edges = 0
        paper_count = 0
        with session_scope() as session:
            topic = TopicRepository(session).get_by_id(topic_id)
            if topic is None:
                raise ValueError(f"topic {topic_id} not found")
            papers = PaperRepository(session).list_by_topic(
                topic_id, limit=paper_limit
            )
            paper_ids = [p.id for p in papers]
        for pid in paper_ids:
            result = self.sync_citations_for_paper(
                pid, limit=edge_limit_per_paper
            )
            total_edges += int(result.get("edges_inserted", 0))
            paper_count += 1
        return {
            "topic_id": topic_id,
            "papers_processed": paper_count,
            "edges_inserted": total_edges,
        }

    def auto_link_citations(self, paper_ids: list[str]) -> dict:
        """入库后自动关联引用 — 轻量版，只匹配已在库的论文"""
        norm = self._normalize_arxiv_id
        linked = 0
        errors = 0
        with session_scope() as session:
            paper_repo = PaperRepository(session)
            all_papers = paper_repo.list_all(limit=50000)
            lib_norm: dict[str, str] = {}
            lib_title: dict[str, str] = {}
            for p in all_papers:
                pn = norm(p.arxiv_id)
                if pn:
                    lib_norm[pn] = p.id
                title_norm = self._normalize_title(p.title)
                if title_norm and title_norm not in lib_title:
                    lib_title[title_norm] = p.id

        for pid in paper_ids:
            try:
                with session_scope() as session:
                    paper = PaperRepository(session).get_by_id(pid)
                    if not paper:
                        continue
                    title = paper.title
                    arxiv_id = paper.arxiv_id

                rich = self.scholar.fetch_rich_citations(
                    title,
                    ref_limit=50,
                    cite_limit=50,
                    arxiv_id=self._lookup_remote_arxiv_id(arxiv_id),
                )
                with session_scope() as session:
                    cit_repo = CitationRepository(session)
                    for info in rich:
                        target_id = self._match_library_paper_id(
                            info.arxiv_id,
                            info.title,
                            lib_norm,
                            lib_title,
                        )
                        if not target_id or target_id == pid:
                            continue
                        if info.direction == "reference":
                            cit_repo.upsert_edge(
                                pid, target_id, context="auto-ingest",
                            )
                        else:
                            cit_repo.upsert_edge(
                                target_id, pid, context="auto-ingest",
                            )
                        linked += 1
            except Exception as exc:
                logger.warning("auto_link_citations error for %s: %s", pid, exc)
                errors += 1

        logger.info("auto_link_citations: %d edges, %d errors", linked, errors)
        return {"papers": len(paper_ids), "edges_linked": linked, "errors": errors}

    def library_overview(self) -> dict:
        """全库概览 — 节点 + 引用边 + PageRank + 统计"""
        with session_scope() as session:
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)
            topic_repo = TopicRepository(session)

            papers = paper_repo.list_all(limit=50000)
            edges = cit_repo.list_all()
            topics = topic_repo.list_topics(kind="folder")
            topic_map = {t.id: t.name for t in topics}

            paper_ids = {p.id for p in papers}
            valid_edges = [
                e for e in edges
                if e.source_paper_id in paper_ids
                and e.target_paper_id in paper_ids
            ]

            in_deg: dict[str, int] = defaultdict(int)
            out_deg: dict[str, int] = defaultdict(int)
            for e in valid_edges:
                out_deg[e.source_paper_id] += 1
                in_deg[e.target_paper_id] += 1

            pagerank = self._pagerank(list(paper_ids), valid_edges)

            from sqlalchemy import select as sa_select
            pt_rows = (
                session.execute(
                    sa_select(PaperTopic)
                    .join(TopicSubscription, TopicSubscription.id == PaperTopic.topic_id)
                    .where(TopicSubscription.kind == "folder")
                )
                .scalars()
                .all()
            )
            paper_topics: dict[str, list[str]] = defaultdict(list)
            for pt in pt_rows:
                tn = topic_map.get(pt.topic_id, "未分配")
                paper_topics[pt.paper_id].append(tn)

            nodes = []
            for p in papers:
                yr = (
                    p.publication_date.year
                    if isinstance(p.publication_date, date) else None
                )
                nodes.append({
                    "id": p.id,
                    "title": p.title,
                    "arxiv_id": p.arxiv_id,
                    "year": yr,
                    "in_degree": in_deg.get(p.id, 0),
                    "out_degree": out_deg.get(p.id, 0),
                    "pagerank": round(pagerank.get(p.id, 0), 6),
                    "topics": paper_topics.get(p.id, []),
                    "read_status": p.read_status.value if p.read_status else "unread",
                })

            edge_list = [
                {"source": e.source_paper_id, "target": e.target_paper_id}
                for e in valid_edges
            ]

            pr_sorted = sorted(nodes, key=lambda n: n["pagerank"], reverse=True)
            top_papers = pr_sorted[:10]

            topic_stats = defaultdict(lambda: {"count": 0, "edges": 0})
            for n in nodes:
                for t in n["topics"]:
                    topic_stats[t]["count"] += 1

            n_papers = len(nodes)
            max_e = n_papers * (n_papers - 1) if n_papers > 1 else 1

        return {
            "total_papers": n_papers,
            "total_edges": len(edge_list),
            "density": round(len(edge_list) / max_e, 6) if max_e else 0,
            "nodes": nodes,
            "edges": edge_list,
            "top_papers": top_papers,
            "topic_stats": dict(topic_stats),
        }

    def cross_topic_bridges(self) -> dict:
        """跨主题桥接论文 — 被多个主题的论文引用的关键论文"""
        with session_scope() as session:
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)
            topic_repo = TopicRepository(session)

            papers = paper_repo.list_all(limit=50000)
            edges = cit_repo.list_all()
            topics = topic_repo.list_topics(kind="folder")
            topic_map = {t.id: t.name for t in topics}

            from sqlalchemy import select as sa_select
            pt_rows = (
                session.execute(
                    sa_select(PaperTopic)
                    .join(TopicSubscription, TopicSubscription.id == PaperTopic.topic_id)
                    .where(TopicSubscription.kind == "folder")
                )
                .scalars()
                .all()
            )
            paper_topic: dict[str, set[str]] = defaultdict(set)
            for pt in pt_rows:
                paper_topic[pt.paper_id].add(pt.topic_id)

            paper_ids = {p.id for p in papers}
            cited_by_topics: dict[str, set[str]] = defaultdict(set)
            for e in edges:
                if e.source_paper_id not in paper_ids:
                    continue
                if e.target_paper_id not in paper_ids:
                    continue
                src_topics = paper_topic.get(e.source_paper_id, set())
                for tid in src_topics:
                    cited_by_topics[e.target_paper_id].add(tid)

            bridges = []
            paper_map = {p.id: p for p in papers}
            for pid, tids in cited_by_topics.items():
                if len(tids) >= 2:
                    p = paper_map.get(pid)
                    if not p:
                        continue
                    bridges.append({
                        "id": pid,
                        "title": p.title,
                        "arxiv_id": p.arxiv_id,
                        "topics_citing": [
                            topic_map.get(t, t) for t in tids
                        ],
                        "cross_topic_count": len(tids),
                        "own_topics": [
                            topic_map.get(t, t)
                            for t in paper_topic.get(pid, set())
                        ],
                    })

            bridges.sort(key=lambda b: b["cross_topic_count"], reverse=True)

        return {"bridges": bridges[:30], "total": len(bridges)}

    def research_frontier(self, days: int = 90) -> dict:
        """研究前沿检测 — 近期高被引 + 引用速度快的论文"""
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=days)

        with session_scope() as session:
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)

            papers = paper_repo.list_all(limit=50000)
            edges = cit_repo.list_all()
            paper_ids = {p.id for p in papers}

            in_deg: dict[str, int] = defaultdict(int)
            for e in edges:
                if e.target_paper_id in paper_ids:
                    in_deg[e.target_paper_id] += 1

            recent = [
                p for p in papers
                if isinstance(p.publication_date, date)
                and p.publication_date >= cutoff
            ]

            frontier = []
            for p in recent:
                age_days = max((date.today() - p.publication_date).days, 1)
                citations = in_deg.get(p.id, 0)
                velocity = round(citations / age_days * 30, 2)
                frontier.append({
                    "id": p.id,
                    "title": p.title,
                    "arxiv_id": p.arxiv_id,
                    "year": p.publication_date.year,
                    "publication_date": p.publication_date.isoformat(),
                    "citations_in_library": citations,
                    "citation_velocity": velocity,
                    "read_status": p.read_status.value if p.read_status else "unread",
                })

            frontier.sort(key=lambda f: f["citation_velocity"], reverse=True)

        return {
            "period_days": days,
            "total_recent": len(recent),
            "frontier": frontier[:30],
        }

    def cocitation_clusters(self, min_cocite: int = 2) -> dict:
        """共引聚类 — 被同一批论文引用的论文会聚在一起"""
        with session_scope() as session:
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)

            papers = paper_repo.list_all(limit=50000)
            edges = cit_repo.list_all()
            paper_ids = {p.id for p in papers}
            paper_map = {p.id: p for p in papers}

            cited_by_map: dict[str, set[str]] = defaultdict(set)
            for e in edges:
                if (
                    e.source_paper_id in paper_ids
                    and e.target_paper_id in paper_ids
                ):
                    cited_by_map[e.target_paper_id].add(e.source_paper_id)

            target_ids = list(cited_by_map.keys())
            cocite_pairs: dict[tuple[str, str], int] = defaultdict(int)

            for i, a in enumerate(target_ids):
                citers_a = cited_by_map[a]
                for b in target_ids[i + 1:]:
                    citers_b = cited_by_map[b]
                    overlap = len(citers_a & citers_b)
                    if overlap >= min_cocite:
                        cocite_pairs[(a, b)] = overlap

            clusters: list[set[str]] = []
            assigned: set[str] = set()
            sorted_pairs = sorted(
                cocite_pairs.items(), key=lambda x: x[1], reverse=True,
            )
            for (a, b), strength in sorted_pairs:
                found = None
                for cl in clusters:
                    if a in cl or b in cl:
                        found = cl
                        break
                if found:
                    found.add(a)
                    found.add(b)
                else:
                    clusters.append({a, b})
                assigned.add(a)
                assigned.add(b)

            result_clusters = []
            for cl in clusters:
                members = []
                for pid in cl:
                    p = paper_map.get(pid)
                    if not p:
                        continue
                    members.append({
                        "id": pid,
                        "title": p.title,
                        "arxiv_id": p.arxiv_id,
                    })
                if len(members) >= 2:
                    result_clusters.append({
                        "size": len(members),
                        "papers": members,
                    })

            result_clusters.sort(key=lambda c: c["size"], reverse=True)

        return {
            "total_clusters": len(result_clusters),
            "clusters": result_clusters[:20],
            "cocitation_pairs": len(cocite_pairs),
        }

    def sync_incremental(
        self,
        paper_limit: int = 40,
        edge_limit_per_paper: int = 6,
    ) -> dict:
        with session_scope() as session:
            papers = PaperRepository(session).list_latest(
                limit=paper_limit * 3
            )
            edges = CitationRepository(session).list_all()
            touched = set()
            for e in edges:
                touched.add(e.source_paper_id)
                touched.add(e.target_paper_id)
            # 在 session 内提取 id，避免 DetachedInstanceError
            target_ids = [
                p.id for p in papers if p.id not in touched
            ][:paper_limit]
        processed = 0
        inserted = 0
        for pid in target_ids:
            try:
                out = self.sync_citations_for_paper(
                    pid, limit=edge_limit_per_paper
                )
                processed += 1
                inserted += int(out.get("edges_inserted", 0))
            except Exception as exc:
                logger.warning("sync_incremental skip %s: %s", pid[:8], exc)
        return {
            "processed_papers": processed,
            "edges_inserted": inserted,
            "strategy": "papers_without_existing_citation_edges",
        }

    def similarity_map(
        self, topic_id: str | None = None, limit: int = 200,
    ) -> dict:
        """用 UMAP 将论文 embedding 降维到 2D，返回散点图数据"""
        import numpy as np

        with session_scope() as session:
            repo = PaperRepository(session)
            papers = repo.list_with_embedding(topic_id=topic_id, limit=limit)
            if len(papers) < 5:
                return {"points": [], "message": "论文数量不足（至少需要 5 篇有向量的论文）"}

            topic_map = repo.get_topic_names_for_papers([str(p.id) for p in papers], kind="folder")

            # 提取 embedding 矩阵
            dim = len(papers[0].embedding)
            vectors = []
            valid_papers = []
            for p in papers:
                if p.embedding and len(p.embedding) == dim:
                    vectors.append(p.embedding)
                    valid_papers.append(p)

            if len(valid_papers) < 5:
                return {"points": [], "message": "有效向量不足"}

            mat = np.array(vectors, dtype=np.float64)

            # UMAP 降维
            try:
                from umap import UMAP
                n_neighbors = min(15, len(valid_papers) - 1)
                reducer = UMAP(n_components=2, random_state=42, n_neighbors=n_neighbors, min_dist=0.1)
                coords = reducer.fit_transform(mat)
            except Exception as exc:
                logger.warning("UMAP failed: %s, falling back to PCA", exc)
                from sklearn.decomposition import PCA
                coords = PCA(n_components=2, random_state=42).fit_transform(mat)

            points = []
            for i, p in enumerate(valid_papers):
                meta = p.metadata_json or {}
                topics = topic_map.get(str(p.id), [])
                points.append({
                    "id": str(p.id),
                    "title": p.title,
                    "x": float(coords[i][0]),
                    "y": float(coords[i][1]),
                    "year": p.publication_date.year if p.publication_date else None,
                    "read_status": p.read_status.value if p.read_status else "unread",
                    "topics": topics,
                    "topic": topics[0] if topics else "未分类",
                    "arxiv_id": p.arxiv_id,
                    "title_zh": meta.get("title_zh", ""),
                })

        return {"points": points, "total": len(points)}

    def citation_tree(
        self, root_paper_id: str, depth: int = 2
    ) -> dict:
        with session_scope() as session:
            papers = {
                p.id: p
                for p in PaperRepository(session).list_all(
                    limit=10000
                )
            }
            edges = CitationRepository(session).list_all()
            out_edges: dict[str, list[str]] = defaultdict(list)
            in_edges: dict[str, list[str]] = defaultdict(list)
            for e in edges:
                out_edges[e.source_paper_id].append(
                    e.target_paper_id
                )
                in_edges[e.target_paper_id].append(
                    e.source_paper_id
                )

            def bfs(
                start: str, graph: dict[str, list[str]]
            ) -> list[dict]:
                visited = {start}
                q: deque[tuple[str, int]] = deque(
                    [(start, 0)]
                )
                result: list[dict] = []
                while q:
                    node, d = q.popleft()
                    if d >= depth:
                        continue
                    for nxt in graph.get(node, []):
                        result.append(
                            {
                                "source": node,
                                "target": nxt,
                                "depth": d + 1,
                            }
                        )
                        if nxt not in visited:
                            visited.add(nxt)
                            q.append((nxt, d + 1))
                return result

            ancestors = bfs(root_paper_id, out_edges)
            descendants = bfs(root_paper_id, in_edges)
            all_node_ids = {root_paper_id}
            for e in ancestors + descendants:
                all_node_ids.add(e["source"])
                all_node_ids.add(e["target"])
            nodes = [
                {
                    "id": pid,
                    "title": (
                        papers[pid].title
                        if pid in papers
                        else None
                    ),
                    "year": (
                        papers[pid].publication_date.year
                        if pid in papers
                        and isinstance(
                            papers[pid].publication_date,
                            date,
                        )
                        else None
                    ),
                }
                for pid in all_node_ids
            ]
            root_paper = papers.get(root_paper_id)
            root_title = (
                root_paper.title if root_paper else None
            )
        return {
            "root": root_paper_id,
            "root_title": root_title,
            "ancestors": ancestors,
            "descendants": descendants,
            "nodes": nodes,
            "edge_count": len(ancestors) + len(descendants),
        }

    def citation_detail(self, paper_id: str, *, force_refresh: bool = False) -> dict:
        """获取单篇论文的丰富引用详情"""
        with session_scope() as session:
            paper_repo = PaperRepository(session)
            source = paper_repo.get_by_id(paper_id)
            if source is None:
                return {
                    "paper_id": paper_id, "paper_title": "",
                    "references": [], "cited_by": [],
                    "stats": {
                        "total_references": 0, "total_cited_by": 0,
                        "in_library_references": 0, "in_library_cited_by": 0,
                    },
                }
            source_title = source.title
            source_arxiv_id = source.arxiv_id

        rich_list = self._load_rich_citations(
            paper_id,
            source_title=source_title,
            source_arxiv_id=source_arxiv_id,
            force_refresh=force_refresh,
        )

        with session_scope() as session:
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)
            norm = self._normalize_arxiv_id
            ext_normed = {
                norm(r.arxiv_id): r.arxiv_id
                for r in rich_list if r.arxiv_id
            }
            lib_norm_map: dict[str, str] = {}
            lib_title_map: dict[str, str] = {}
            lib_title_zh_map: dict[str, str] = {}
            for p in paper_repo.list_all(limit=50000):
                pn = norm(p.arxiv_id)
                if pn and pn in ext_normed:
                    lib_norm_map[pn] = p.id
                title_norm = self._normalize_title(p.title)
                if title_norm and title_norm not in lib_title_map:
                    lib_title_map[title_norm] = p.id
                title_zh = str((p.metadata_json or {}).get("title_zh", "") or "").strip()
                if title_zh:
                    lib_title_zh_map[p.id] = title_zh

            references: list[dict] = []
            cited_by: list[dict] = []

            for info in rich_list:
                library_paper_id = self._match_library_paper_id(
                    info.arxiv_id,
                    info.title,
                    lib_norm_map,
                    lib_title_map,
                )
                in_library = library_paper_id is not None
                entry = {
                    "scholar_id": info.scholar_id,
                    "title": info.title,
                    "title_zh": lib_title_zh_map.get(library_paper_id or "", ""),
                    "year": info.year,
                    "venue": info.venue,
                    "citation_count": info.citation_count,
                    "arxiv_id": info.arxiv_id,
                    "abstract": info.abstract,
                    "in_library": in_library,
                    "library_paper_id": library_paper_id,
                }
                if info.direction == "reference":
                    references.append(entry)
                    if in_library and library_paper_id:
                        cit_repo.upsert_edge(
                            paper_id, library_paper_id,
                            context="reference",
                        )
                else:
                    cited_by.append(entry)
                    if in_library and library_paper_id:
                        cit_repo.upsert_edge(
                            library_paper_id, paper_id,
                            context="citation",
                        )

        translation_candidates = [
            entry["title"]
            for entry in sorted(
                references + cited_by,
                key=lambda entry: int(entry.get("citation_count") or 0),
                reverse=True,
            )
            if entry.get("title") and not entry.get("title_zh")
        ]
        translated_titles = self._translate_citation_titles(translation_candidates)
        if translated_titles:
            for entry in references + cited_by:
                if not entry.get("title_zh"):
                    entry["title_zh"] = translated_titles.get(entry.get("title", ""), "")

        return {
            "paper_id": paper_id,
            "paper_title": source_title,
            "references": references,
            "cited_by": cited_by,
            "stats": {
                "total_references": len(references),
                "total_cited_by": len(cited_by),
                "in_library_references": sum(
                    1 for r in references if r["in_library"]
                ),
                "in_library_cited_by": sum(
                    1 for c in cited_by if c["in_library"]
                ),
            },
        }

    def topic_citation_network(self, topic_id: str) -> dict:
        """获取主题内论文的互引网络"""
        with session_scope() as session:
            topic_repo = TopicRepository(session)
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)

            topic = topic_repo.get_by_id(topic_id)
            if topic is None:
                raise ValueError(f"topic {topic_id} not found")
            topic_name = topic.name

            papers = paper_repo.list_by_topic(topic_id, limit=500)
            paper_ids = {p.id for p in papers}

            all_edges = cit_repo.list_for_paper_ids(list(paper_ids))
            internal_edges = [
                e for e in all_edges
                if e.source_paper_id in paper_ids
                and e.target_paper_id in paper_ids
            ]

            in_degree: dict[str, int] = defaultdict(int)
            out_degree: dict[str, int] = defaultdict(int)
            for e in internal_edges:
                out_degree[e.source_paper_id] += 1
                in_degree[e.target_paper_id] += 1

            degrees = [
                in_degree.get(pid, 0) for pid in paper_ids
            ]
            median_deg = sorted(degrees)[len(degrees) // 2] if degrees else 0
            hub_threshold = max(median_deg * 2, 2)

            nodes = []
            for p in papers:
                ind = in_degree.get(p.id, 0)
                outd = out_degree.get(p.id, 0)
                nodes.append({
                    "id": p.id,
                    "title": p.title,
                    "year": (
                        p.publication_date.year
                        if isinstance(p.publication_date, date)
                        else None
                    ),
                    "arxiv_id": p.arxiv_id,
                    "in_degree": ind,
                    "out_degree": outd,
                    "is_hub": ind >= hub_threshold,
                    "is_external": False,
                })

            edges = [
                {
                    "source": e.source_paper_id,
                    "target": e.target_paper_id,
                }
                for e in internal_edges
            ]

            hub_count = sum(1 for n in nodes if n["is_hub"])
            n_papers = len(nodes)
            max_edges = n_papers * (n_papers - 1) if n_papers > 1 else 1
            density = round(len(edges) / max_edges, 4) if max_edges else 0

        return {
            "topic_id": topic_id,
            "topic_name": topic_name,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_papers": n_papers,
                "total_edges": len(edges),
                "density": density,
                "hub_papers": hub_count,
            },
        }

    def topic_deep_trace(self, topic_id: str, max_concurrency: int = 3) -> dict:
        """对主题内论文执行深度溯源，拉取外部引用并进行共引分析"""
        with session_scope() as session:
            papers = PaperRepository(session).list_by_topic(
                topic_id, limit=500,
            )
            paper_ids = [p.id for p in papers]
            topic = TopicRepository(session).get_by_id(topic_id)
            if topic is None:
                raise ValueError(f"topic {topic_id} not found")
            topic_name = topic.name

        synced = 0
        with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
            futures = {
                pool.submit(self.citation_detail, pid): pid
                for pid in paper_ids
            }
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                    synced += (
                        result["stats"]["total_references"]
                        + result["stats"]["total_cited_by"]
                    )
                except Exception as exc:
                    logger.warning("deep-trace sync error: %s", exc)

        with session_scope() as session:
            paper_repo = PaperRepository(session)
            cit_repo = CitationRepository(session)

            topic_papers = paper_repo.list_by_topic(topic_id, limit=500)
            topic_ids_set = {p.id for p in topic_papers}
            all_edges = cit_repo.list_for_paper_ids(list(topic_ids_set))

            external_ref_count: dict[str, int] = defaultdict(int)
            internal_edges = []
            external_edges = []

            for e in all_edges:
                src_in = e.source_paper_id in topic_ids_set
                tgt_in = e.target_paper_id in topic_ids_set
                if src_in and tgt_in:
                    internal_edges.append(e)
                elif src_in and not tgt_in:
                    external_edges.append(e)
                    external_ref_count[e.target_paper_id] += 1
                elif not src_in and tgt_in:
                    external_edges.append(e)
                    external_ref_count[e.source_paper_id] += 1

            co_cited = sorted(
                external_ref_count.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:30]
            co_cited_ids = [pid for pid, _ in co_cited]
            co_cited_papers = {
                p.id: p
                for p in paper_repo.list_by_ids(co_cited_ids)
            }

            in_degree: dict[str, int] = defaultdict(int)
            out_degree: dict[str, int] = defaultdict(int)
            for e in internal_edges:
                out_degree[e.source_paper_id] += 1
                in_degree[e.target_paper_id] += 1

            all_node_ids = set(topic_ids_set)

            nodes = []
            for p in topic_papers:
                nodes.append({
                    "id": p.id,
                    "title": p.title,
                    "year": (
                        p.publication_date.year
                        if isinstance(p.publication_date, date)
                        else None
                    ),
                    "arxiv_id": p.arxiv_id,
                    "in_degree": in_degree.get(p.id, 0),
                    "out_degree": out_degree.get(p.id, 0),
                    "is_hub": in_degree.get(p.id, 0) >= 2,
                    "is_external": False,
                })

            for pid, count in co_cited:
                p = co_cited_papers.get(pid)
                nodes.append({
                    "id": pid,
                    "title": p.title if p else f"external-{pid[:8]}",
                    "year": (
                        p.publication_date.year
                        if p and isinstance(p.publication_date, date)
                        else None
                    ),
                    "arxiv_id": p.arxiv_id if p else None,
                    "in_degree": 0,
                    "out_degree": 0,
                    "is_hub": False,
                    "is_external": True,
                    "co_citation_count": count,
                })
                all_node_ids.add(pid)

            edges = [
                {"source": e.source_paper_id, "target": e.target_paper_id}
                for e in internal_edges
            ]
            for e in external_edges:
                if (
                    e.source_paper_id in all_node_ids
                    and e.target_paper_id in all_node_ids
                ):
                    edges.append({
                        "source": e.source_paper_id,
                        "target": e.target_paper_id,
                    })

            n_papers = len(nodes)
            max_edges = n_papers * (n_papers - 1) if n_papers > 1 else 1
            density = round(len(edges) / max_edges, 4) if max_edges else 0

            key_external = [
                {
                    "id": pid,
                    "title": (
                        co_cited_papers[pid].title
                        if pid in co_cited_papers
                        else f"external-{pid[:8]}"
                    ),
                    "co_citation_count": count,
                }
                for pid, count in co_cited
            ]

        return {
            "topic_id": topic_id,
            "topic_name": topic_name,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_papers": n_papers,
                "internal_papers": len(topic_ids_set),
                "external_papers": len(co_cited),
                "total_edges": len(edges),
                "internal_edges": len(internal_edges),
                "density": density,
                "new_edges_synced": synced,
            },
            "key_external_papers": key_external,
        }

    def timeline(self, keyword: str, limit: int = 100) -> dict:
        with session_scope() as session:
            papers = PaperRepository(
                session
            ).full_text_candidates(keyword, limit=limit)
            edges = CitationRepository(session).list_all()
            nodes = {p.id: p for p in papers}
            indegree: dict[str, int] = {
                p.id: 0 for p in papers
            }
            outdegree: dict[str, int] = {
                p.id: 0 for p in papers
            }
            for e in edges:
                if (
                    e.target_paper_id in nodes
                    and e.source_paper_id in nodes
                ):
                    indegree[e.target_paper_id] += 1
                    outdegree[e.source_paper_id] += 1
            pagerank = self._pagerank(
                nodes=list(nodes.keys()), edges=edges
            )
            items = []
            for p in papers:
                year = (
                    p.publication_date.year
                    if isinstance(p.publication_date, date)
                    else 1900
                )
                pr = pagerank.get(p.id, 0.0)
                ind = indegree.get(p.id, 0)
                score = 0.65 * ind + 0.35 * pr * 100.0
                items.append(
                    {
                        "paper_id": p.id,
                        "title": p.title,
                        "year": year,
                        "indegree": ind,
                        "outdegree": outdegree.get(
                            p.id, 0
                        ),
                        "pagerank": pr,
                        "seminal_score": score,
                        "why_seminal": (
                            f"库内被引 {ind}，"
                            f"PageRank {pr:.4f}，"
                            f"综合得分 {score:.3f}"
                        ),
                    }
                )
        title_zh_map = self._translate_citation_titles(
            [x["title"] for x in items],
            max_titles=min(max(len(items), 20), 80),
        )
        for item in items:
            item["title_zh"] = title_zh_map.get(item["title"])
        items.sort(
            key=lambda x: (
                x["year"],
                -x["indegree"],
                x["title"],
            )
        )
        seminal = sorted(
            items,
            key=lambda x: (-x["seminal_score"], x["year"]),
        )[:10]
        milestones = self._milestones_by_year(items)
        return {
            "keyword": keyword,
            "timeline": items,
            "seminal": seminal,
            "milestones": milestones,
        }

    def quality_metrics(
        self, keyword: str, limit: int = 120
    ) -> dict:
        with session_scope() as session:
            papers = PaperRepository(
                session
            ).full_text_candidates(keyword, limit=limit)
            paper_ids = [p.id for p in papers]
            edges = CitationRepository(
                session
            ).list_for_paper_ids(paper_ids)
            node_set = set(paper_ids)
            internal_edges = [
                e
                for e in edges
                if e.source_paper_id in node_set
                and e.target_paper_id in node_set
            ]
            connected_nodes: set[str] = set()
            for e in internal_edges:
                connected_nodes.add(e.source_paper_id)
                connected_nodes.add(e.target_paper_id)
            with_pub = sum(
                1
                for p in papers
                if p.publication_date is not None
            )
        n = max(len(paper_ids), 1)
        ie = len(internal_edges)
        return {
            "keyword": keyword,
            "node_count": len(paper_ids),
            "edge_count": ie,
            "density": ie / max(n * max(n - 1, 1), 1),
            "connected_node_ratio": (
                len(connected_nodes) / n
            ),
            "publication_date_coverage": with_pub / n,
        }

    def weekly_evolution(
        self, keyword: str, limit: int = 160
    ) -> dict:
        tl = self.timeline(keyword=keyword, limit=limit)
        by_year: dict[int, list[dict]] = defaultdict(list)
        for item in tl["timeline"]:
            by_year[item["year"]].append(item)
        year_buckets = []
        for year in sorted(by_year.keys())[-6:]:
            group = by_year[year]
            avg = sum(x["seminal_score"] for x in group) / max(
                len(group), 1
            )
            top_group = sorted(
                group, key=lambda t: -t["seminal_score"]
            )[:3]
            top_titles = [
                x["title"]
                for x in top_group
            ]
            top_titles_zh = [
                str(x.get("title_zh", "") or "")
                for x in top_group
            ]
            year_buckets.append(
                {
                    "year": year,
                    "paper_count": len(group),
                    "avg_seminal_score": avg,
                    "top_titles": top_titles,
                    "top_titles_zh": top_titles_zh,
                }
            )
        prompt = build_evolution_prompt(
            keyword=keyword, year_buckets=year_buckets
        )
        llm_result = self.llm.complete_json(
            prompt,
            stage="rag",
            model_override=self._active_skim_model(),
        )
        self.llm.trace_result(llm_result, stage="graph_evolution", prompt_digest=f"evolution:{keyword}")
        summary = self._normalize_evolution_summary(llm_result.parsed_json)
        return {
            "keyword": keyword,
            "year_buckets": year_buckets,
            "summary": summary,
        }

    def survey(self, keyword: str, limit: int = 120) -> dict:
        base = self.timeline(keyword=keyword, limit=limit)
        prompt = build_survey_prompt(
            keyword, base["milestones"], base["seminal"]
        )
        result = self.llm.complete_json(
            prompt,
            stage="rag",
            model_override=self._active_skim_model(),
        )
        self.llm.trace_result(result, stage="graph_survey", prompt_digest=f"survey:{keyword}")
        survey_obj = result.parsed_json or {
            "overview": "当前样本不足以生成高质量综述。",
            "stages": [],
            "reading_list": [
                x["title"] for x in base["seminal"][:5]
            ],
            "open_questions": [],
        }
        return {
            "keyword": keyword,
            "summary": survey_obj,
            "milestones": base["milestones"],
            "seminal": base["seminal"],
        }

    def detect_research_gaps(
        self, keyword: str, limit: int = 120,
    ) -> dict:
        """分析引用网络的稀疏区域，识别研究空白"""
        tl = self.timeline(keyword=keyword, limit=limit)
        quality = self.quality_metrics(keyword=keyword, limit=limit)

        # 构造论文数据（含 indegree/outdegree/keywords）
        papers_data = []
        for item in tl["timeline"]:
            papers_data.append({
                "title": item["title"],
                "year": item["year"],
                "indegree": item["indegree"],
                "outdegree": item["outdegree"],
                "seminal_score": item["seminal_score"],
                "keywords": [],
                "abstract": "",
            })

        # 补充 abstract 和 keywords
        with session_scope() as session:
            repo = PaperRepository(session)
            candidates = repo.full_text_candidates(keyword, limit=limit)
            paper_map = {p.title: p for p in candidates}
            for pd in papers_data:
                p = paper_map.get(pd["title"])
                if p:
                    pd["abstract"] = p.abstract[:400]
                    pd["keywords"] = (p.metadata_json or {}).get("keywords", [])

        # 计算孤立论文数（入度+出度=0）
        isolated = sum(
            1 for item in tl["timeline"]
            if item["indegree"] == 0 and item["outdegree"] == 0
        )

        network_stats = {
            "total_papers": quality["node_count"],
            "edge_count": quality["edge_count"],
            "density": quality["density"],
            "connected_ratio": quality["connected_node_ratio"],
            "isolated_count": isolated,
        }

        prompt = build_research_gaps_prompt(
            keyword=keyword,
            papers_data=papers_data,
            network_stats=network_stats,
        )
        parsed: dict = {}
        try:
            result = self.llm.complete_json(
                prompt,
                stage="deep",
                model_override=self._active_deep_model(),
                max_tokens=8192,
            )
            self.llm.trace_result(result, stage="graph_research_gaps", prompt_digest=f"gaps:{keyword}")
            parsed = self._normalize_research_gap_analysis(result.parsed_json)
        except Exception as exc:
            logger.warning("research gaps llm failed, fallback enabled: %s", exc)
            parsed = self._normalize_research_gap_analysis({})

        if self._should_use_research_gap_fallback(parsed, network_stats):
            parsed = self._build_research_gap_fallback(
                keyword=keyword,
                papers_data=papers_data,
                network_stats=network_stats,
            )

        return {
            "keyword": keyword,
            "network_stats": network_stats,
            "analysis": parsed,
        }

    def paper_wiki(self, paper_id: str) -> dict:
        tree = self.citation_tree(
            root_paper_id=paper_id, depth=2
        )

        # 1. 富化上下文收集（向量搜索 + 引用上下文 + PDF）
        ctx = self.context_gatherer.gather_paper_context(paper_id)
        p_title = ctx["paper"].get("title", "")
        p_abstract = ctx["paper"].get("abstract", "")
        p_arxiv = ctx["paper"].get("arxiv_id", "")
        analysis = ctx["paper"].get("analysis", "")

        # 2. Semantic Scholar 元数据
        scholar_meta: list[dict] = []
        try:
            all_titles = [p_title] + ctx.get("ancestor_titles", [])[:5]
            scholar_meta = self.scholar.fetch_batch_metadata(
                all_titles, max_papers=6
            )
        except Exception as exc:
            logger.warning("Scholar metadata fetch failed: %s", exc)

        # 3. LLM 生成结构化 wiki
        prompt = build_paper_wiki_prompt(
            title=p_title,
            abstract=p_abstract,
            analysis=analysis,
            related_papers=ctx.get("related_papers", [])[:10],
            ancestors=ctx.get("ancestor_titles", []),
            descendants=ctx.get("descendant_titles", []),
        )
        # 注入引用上下文 + PDF + Scholar 到 prompt
        extra_context = self._build_extra_context(
            citation_contexts=ctx.get("citation_contexts", []),
            pdf_excerpt=ctx.get("pdf_excerpt", ""),
            scholar_metadata=scholar_meta,
        )
        full_prompt = prompt + extra_context

        result = self.llm.complete_json(
            full_prompt,
            stage="rag",
            model_override=self._active_deep_model(),
            max_tokens=8192,
        )
        self.llm.trace_result(result, stage="wiki_paper", paper_id=paper_id, prompt_digest=f"paper_wiki:{p_title[:60]}")
        wiki_content = result.parsed_json or {
            "summary": analysis or "暂无分析。",
            "contributions": [],
            "methodology": "",
            "significance": "",
            "limitations": [],
            "related_work_analysis": "",
            "reading_suggestions": [],
        }

        # 注入额外元数据供前端展示
        wiki_content["citation_contexts"] = ctx.get(
            "citation_contexts", []
        )[:20]
        wiki_content["pdf_excerpts"] = (
            [{"title": p_title, "excerpt": ctx.get("pdf_excerpt", "")[:2000]}]
            if ctx.get("pdf_excerpt")
            else []
        )
        wiki_content["scholar_metadata"] = scholar_meta

        # 备用 markdown
        md_parts = [
            f"# {p_title}",
            f"\narXiv: {p_arxiv}",
            f"\n## 摘要\n\n{wiki_content.get('summary', '')}",
        ]
        if wiki_content.get("methodology"):
            md_parts.append(
                f"\n## 方法论\n\n{wiki_content['methodology']}"
            )
        if wiki_content.get("significance"):
            md_parts.append(
                f"\n## 学术意义\n\n{wiki_content['significance']}"
            )
        markdown = "\n".join(md_parts)

        return {
            "paper_id": paper_id,
            "title": p_title,
            "markdown": markdown,
            "wiki_content": wiki_content,
            "graph": tree,
        }

    def topic_wiki(
        self,
        keyword: str,
        limit: int = 120,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> dict:
        def _progress(pct: float, msg: str):
            if progress_callback:
                progress_callback(pct, msg)

        # Phase 0: 并行收集数据
        _progress(0.05, "收集时间线和综述数据...")
        tl = self.timeline(keyword=keyword, limit=limit)
        survey_data = self.survey(keyword=keyword, limit=limit)

        _progress(0.15, "收集论文上下文和引用关系...")
        # Phase 1: 富化上下文（向量搜索 + 引用上下文 + PDF）
        ctx = self.context_gatherer.gather_topic_context(
            keyword, limit=limit
        )
        paper_contexts = ctx.get("paper_contexts", [])[:25]
        citation_contexts = ctx.get("citation_contexts", [])[:30]
        pdf_excerpts = ctx.get("pdf_excerpts", [])[:5]

        # Phase 2: Semantic Scholar 元数据增强
        scholar_meta: list[dict] = []
        try:
            top_titles = [
                s["title"]
                for s in tl.get("seminal", [])[:8]
                if s.get("title")
            ]
            scholar_meta = self.scholar.fetch_batch_metadata(
                top_titles, max_papers=8
            )
        except Exception as exc:
            logger.warning("Scholar metadata fetch failed: %s", exc)

        _progress(0.25, "生成文章大纲...")
        # Phase 3: 多轮生成 — 先生成大纲
        outline_prompt = build_wiki_outline_prompt(
            keyword=keyword,
            paper_summaries=paper_contexts,
            citation_contexts=citation_contexts,
            scholar_metadata=scholar_meta,
            pdf_excerpts=pdf_excerpts,
        )
        outline_result = self.llm.complete_json(
            outline_prompt,
            stage="rag",
            model_override=self._active_deep_model(),
            max_tokens=8192,
        )
        self.llm.trace_result(outline_result, stage="wiki_outline", prompt_digest=f"outline:{keyword}")
        outline = outline_result.parsed_json or {
            "title": keyword,
            "outline": [],
            "total_sections": 0,
        }

        # Phase 4: 并行章节生成（直接输出 markdown 文本）
        all_sources_text = self._build_all_sources_text(
            paper_contexts,
            citation_contexts,
            scholar_meta,
            pdf_excerpts,
        )
        sec_plans = outline.get("outline", [])[:5]
        _progress(0.35, f"并行生成 {len(sec_plans)} 个章节...")
        sections = self._generate_sections_parallel(
            keyword, sec_plans, all_sources_text,
        )

        _progress(0.75, "生成概述和总结...")
        # Phase 5: 生成概述（直接输出文本）+ 结构化汇总（JSON）
        # 5a: 文本概述
        section_titles = ", ".join(
            s.get("title", "") for s in sections
        )
        survey_overview = (
            survey_data.get("summary", {}).get("overview", "")[:600]
        )
        overview_prompt = (
            "你是世界顶级学术综述作者。"
            f"请为「{keyword}」主题撰写一段 300-500 字的概述，"
            "涵盖该主题的定义、重要性、核心思想和发展脉络。\n"
            "直接输出文本，不要用 JSON 或代码块包裹。\n\n"
            f"已有章节: {section_titles}\n"
            f"参考综述: {survey_overview}\n"
        )
        overview_result = self.llm.summarize_text(
            overview_prompt,
            stage="wiki_overview",
            model_override=self._active_deep_model(),
            max_tokens=2048,
        )
        self.llm.trace_result(
            overview_result, stage="wiki_overview",
            prompt_digest=f"overview:{keyword}",
        )
        overview_text = (overview_result.content or "").strip()
        overview_text = re.sub(
            r'^```(?:markdown)?\s*\n?', '', overview_text
        )
        overview_text = re.sub(r'\n?```\s*$', '', overview_text)

        # 5b: 结构化汇总（key_findings + future_directions）
        summary_prompt = (
            "请只输出单个 JSON 对象，不要代码块。\n"
            f"根据以下「{keyword}」综述内容，提取关键发现和未来方向：\n"
            f"概述: {overview_text[:300]}\n"
            f"章节: {section_titles}\n"
            f"参考: {survey_overview[:300]}\n\n"
            '输出: {"key_findings": ["发现1","发现2","发现3"],'
            ' "future_directions": ["方向1","方向2","方向3"],'
            ' "reading_list": ["论文1","论文2"]}'
        )
        summary_result = self.llm.complete_json(
            summary_prompt,
            stage="wiki_summary",
            model_override=self._active_deep_model(),
            max_tokens=2048,
        )
        self.llm.trace_result(
            summary_result, stage="wiki_summary",
            prompt_digest=f"summary:{keyword}",
        )
        summary_data = summary_result.parsed_json or {}

        # 组装最终 wiki_content
        wiki_content: dict = {
            "overview": overview_text,
            "sections": sections,
            "key_findings": summary_data.get("key_findings", []),
            "methodology_evolution": "",
            "future_directions": summary_data.get(
                "future_directions", []
            ),
            "reading_list": summary_data.get("reading_list", []),
            "citation_contexts": citation_contexts[:20],
            "pdf_excerpts": pdf_excerpts,
            "scholar_metadata": scholar_meta,
        }

        # 备用 markdown
        md_parts = [
            f"# {keyword}\n\n{wiki_content.get('overview', '')}"
        ]
        for sec in sections:
            md_parts.append(
                f"\n## {sec.get('title', '')}\n\n"
                f"{sec.get('content', '')}"
            )
        if wiki_content.get("methodology_evolution"):
            md_parts.append(
                f"\n## 方法论演化\n\n"
                f"{wiki_content['methodology_evolution']}"
            )
        markdown = "\n".join(md_parts)

        _progress(1.0, "Wiki 生成完成")
        return {
            "keyword": keyword,
            "markdown": markdown,
            "wiki_content": wiki_content,
            "timeline": tl,
            "survey": survey_data,
        }

    @staticmethod
    def _build_extra_context(
        *,
        citation_contexts: list[str],
        pdf_excerpt: str,
        scholar_metadata: list[dict],
    ) -> str:
        """拼装额外上下文注入到 paper wiki prompt"""
        parts: list[str] = []
        if citation_contexts:
            parts.append("\n## 引用关系上下文:")
            for i, c in enumerate(citation_contexts[:15], 1):
                parts.append(f"[C{i}] {c}")
        if pdf_excerpt:
            parts.append(
                f"\n## PDF 全文摘录（前 2000 字）:\n"
                f"{pdf_excerpt[:2000]}"
            )
        if scholar_metadata:
            parts.append("\n## Semantic Scholar 外部元数据:")
            for i, s in enumerate(scholar_metadata[:6], 1):
                parts.append(
                    f"[S{i}] {s.get('title', 'N/A')} "
                    f"({s.get('year', '?')}) "
                    f"引用数={s.get('citationCount', 'N/A')} "
                    f"Venue={s.get('venue', 'N/A')}"
                )
                if s.get("tldr"):
                    parts.append(f"  TLDR: {s['tldr'][:200]}")
        return "\n".join(parts)

    def _generate_one_section(
        self, keyword: str, sec_plan: dict, all_sources_text: str,
    ) -> dict:
        """生成单个 wiki 章节"""
        sec_title = sec_plan.get("section_title", "")
        sec_prompt = build_wiki_section_prompt(
            keyword=keyword,
            section_title=sec_title,
            key_points=sec_plan.get("key_points", []),
            source_refs=sec_plan.get("source_refs", []),
            all_sources_text=all_sources_text,
        )
        sec_result = self.llm.summarize_text(
            sec_prompt,
            stage="wiki_section",
            model_override=self._active_deep_model(),
            max_tokens=4096,
        )
        self.llm.trace_result(
            sec_result, stage="wiki_section",
            prompt_digest=f"section:{sec_title[:60]}",
        )
        content = sec_result.content or ""
        content = re.sub(
            r'^```(?:markdown)?\s*\n?', '', content.strip()
        )
        content = re.sub(r'\n?```\s*$', '', content.strip())
        return {
            "title": sec_title,
            "content": content,
            "key_insight": "",
        }

    def _generate_sections_parallel(
        self,
        keyword: str,
        sec_plans: list[dict],
        all_sources_text: str,
        max_workers: int = 3,
    ) -> list[dict]:
        """并行生成多个 wiki 章节"""
        if not sec_plans:
            return []
        sections: list[dict] = [{}] * len(sec_plans)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(
                    self._generate_one_section,
                    keyword, plan, all_sources_text,
                ): idx
                for idx, plan in enumerate(sec_plans)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    sections[idx] = future.result()
                    logger.info(
                        "wiki section %d/%d 完成: %s",
                        idx + 1, len(sec_plans),
                        sections[idx].get("title", "")[:40],
                    )
                except Exception as exc:
                    logger.warning("wiki section %d 失败: %s", idx, exc)
                    sections[idx] = {
                        "title": sec_plans[idx].get("section_title", ""),
                        "content": "",
                        "key_insight": "",
                    }
        return sections

    @staticmethod
    def _build_all_sources_text(
        paper_contexts: list[dict],
        citation_contexts: list[str],
        scholar_metadata: list[dict],
        pdf_excerpts: list[dict],
    ) -> str:
        """拼装所有来源文本供逐章节生成使用"""
        parts: list[str] = []
        for i, p in enumerate(paper_contexts[:25], 1):
            parts.append(
                f"[P{i}] {p.get('title', 'N/A')} "
                f"({p.get('year', '?')})\n"
                f"Abstract: {p.get('abstract', '')[:400]}\n"
                f"Analysis: {p.get('analysis', '')[:400]}"
            )
        for i, c in enumerate(citation_contexts[:20], 1):
            parts.append(f"[C{i}] {c}")
        for i, s in enumerate(scholar_metadata[:8], 1):
            line = (
                f"[S{i}] {s.get('title', 'N/A')} "
                f"({s.get('year', '?')}) "
                f"citations={s.get('citationCount', '?')}"
            )
            if s.get("tldr"):
                line += f" TLDR: {s['tldr'][:200]}"
            parts.append(line)
        for i, ex in enumerate(pdf_excerpts[:5], 1):
            parts.append(
                f"[PDF{i}] {ex.get('title', 'N/A')}\n"
                f"Excerpt: {ex.get('excerpt', '')[:500]}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _normalize_arxiv_id(arxiv_id: str | None) -> str | None:
        """去版本号归一化: '2502.12082v2' -> '2502.12082'"""
        if not arxiv_id:
            return None
        return re.sub(r"v\d+$", "", arxiv_id.strip())

    @staticmethod
    def _normalize_title(title: str | None) -> str | None:
        if not title:
            return None
        normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        return normalized or None

    @staticmethod
    def _lookup_remote_arxiv_id(arxiv_id: str | None) -> str | None:
        normalized = GraphService._normalize_arxiv_id(arxiv_id)
        if not normalized:
            return None
        if normalized.startswith(("ss-", "upload-", "local-")):
            return None
        return normalized

    @classmethod
    def _build_library_title_map(cls, papers: list) -> dict[str, object]:
        lib_by_title: dict[str, object] = {}
        for paper in papers:
            normalized = cls._normalize_title(getattr(paper, "title", ""))
            if normalized and normalized not in lib_by_title:
                lib_by_title[normalized] = paper
        return lib_by_title

    @classmethod
    def _match_library_paper_id(
        cls,
        arxiv_id: str | None,
        title: str | None,
        lib_norm_map: dict[str, str],
        lib_title_map: dict[str, str],
    ) -> str | None:
        info_norm = cls._normalize_arxiv_id(arxiv_id)
        if info_norm and info_norm in lib_norm_map:
            return lib_norm_map[info_norm]
        title_norm = cls._normalize_title(title)
        if title_norm and title_norm in lib_title_map:
            return lib_title_map[title_norm]
        return None

    @classmethod
    def _match_or_create_paper(
        cls,
        paper_repo: PaperRepository,
        title: str,
        lib_by_title: dict[str, object],
    ):
        title_norm = cls._normalize_title(title)
        if title_norm and title_norm in lib_by_title:
            return lib_by_title[title_norm]
        created = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id=cls._title_to_id(title),
                title=title,
                abstract="",
                metadata={"source": "semantic_scholar"},
            )
        )
        if title_norm:
            lib_by_title[title_norm] = created
        return created

    @staticmethod
    def _title_to_id(title: str) -> str:
        normalized = "".join(
            ch.lower() if ch.isalnum() else "-" for ch in title
        ).strip("-")
        return f"ss-{normalized[:48]}"

    @staticmethod
    def _pagerank(
        nodes: list[str], edges: list
    ) -> dict[str, float]:
        if not nodes:
            return {}
        node_set = set(nodes)
        outgoing: dict[str, list[str]] = defaultdict(list)
        for e in edges:
            if (
                e.source_paper_id in node_set
                and e.target_paper_id in node_set
            ):
                outgoing[e.source_paper_id].append(
                    e.target_paper_id
                )
        n = len(nodes)
        rank = {node: 1.0 / n for node in nodes}
        damping = 0.85
        for _ in range(20):
            next_rank = {
                node: (1.0 - damping) / n for node in nodes
            }
            for node in nodes:
                refs = outgoing.get(node, [])
                if not refs:
                    continue
                share = rank[node] / len(refs)
                for dst in refs:
                    next_rank[dst] += damping * share
            rank = next_rank
        return rank

    @staticmethod
    def _milestones_by_year(
        items: list[dict],
    ) -> list[dict]:
        best_per_year: dict[int, dict] = {}
        for x in items:
            year = x["year"]
            if (
                year not in best_per_year
                or x["seminal_score"]
                > best_per_year[year]["seminal_score"]
            ):
                best_per_year[year] = x
        return [
            best_per_year[y] for y in sorted(best_per_year.keys())
        ]
