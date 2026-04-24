"""arXiv CS trend snapshot for the dashboard home page."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from xml.etree import ElementTree

import httpx

from packages.integrations.llm_client import LLMClient
from packages.storage.db import session_scope
from packages.storage.repositories import GeneratedContentRepository
from packages.timezone import user_date_str

ARXIV_API_URL = "https://export.arxiv.org/api/query"
logger = logging.getLogger(__name__)
_TREND_CONTENT_TYPE = "arxiv_trend_snapshot"
_TREND_SNAPSHOT_VERSION = 4
_TREND_MAX_SAMPLE_LIMIT = 160

_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

_TERM_STOPWORDS = {
    "a",
    "an",
    "and",
    "also",
    "approach",
    "approaches",
    "are",
    "as",
    "at",
    "based",
    "by",
    "can",
    "datasets",
    "different",
    "efficient",
    "evaluation",
    "framework",
    "for",
    "from",
    "has",
    "have",
    "large",
    "learning",
    "method",
    "methods",
    "model",
    "models",
    "new",
    "in",
    "into",
    "is",
    "its",
    "paper",
    "performance",
    "problem",
    "of",
    "on",
    "or",
    "our",
    "propose",
    "proposed",
    "results",
    "show",
    "shows",
    "study",
    "task",
    "tasks",
    "that",
    "these",
    "the",
    "this",
    "to",
    "towards",
    "two",
    "using",
    "via",
    "we",
    "while",
    "which",
    "with",
}

_GENERIC_PHRASE_WORDS = {
    "analysis",
    "benchmark",
    "data",
    "deep",
    "generative",
    "large",
    "machine",
    "neural",
    "novel",
    "robust",
    "towards",
    "using",
}

_TOPIC_BOUNDARY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "via",
    "with",
}

_TOPIC_GENERIC_TOKENS = {
    "analysis",
    "approach",
    "approaches",
    "benchmark",
    "benchmarks",
    "dataset",
    "datasets",
    "efficient",
    "framework",
    "frameworks",
    "general",
    "large",
    "learning",
    "method",
    "methods",
    "model",
    "models",
    "module",
    "modules",
    "network",
    "networks",
    "novel",
    "problem",
    "problems",
    "representation",
    "representations",
    "research",
    "robust",
    "scale",
    "scalable",
    "study",
    "studies",
    "system",
    "systems",
    "task",
    "tasks",
    "towards",
    "understanding",
    "unified",
    "using",
}

_TOPIC_GENERIC_PREFIXES = {
    "adaptive",
    "automatic",
    "efficient",
    "general",
    "novel",
    "robust",
    "scalable",
    "towards",
    "unified",
}

_TOPIC_REJECT_KEYS = {
    "ai model",
    "case study",
    "language model",
    "language models",
    "machine learning",
    "deep learning",
    "neural network",
    "neural networks",
    "computer vision",
}

_SEED_REJECT_KEYS = {
    "agent",
    "agents",
    "artificial intelligence",
    "computer vision",
    "deep learning",
    "generative ai",
    "language model",
    "language models",
    "llm",
    "machine learning",
    "model",
    "models",
    "multimodal llm",
    "natural language processing",
    "robot",
    "robots",
    "system",
    "systems",
    "video",
    "vision",
}

_TOPIC_REJECT_PATTERNS = [
    re.compile(r"\bsemeval\b", flags=re.IGNORECASE),
    re.compile(r"\bshared task\b", flags=re.IGNORECASE),
    re.compile(r"\bcase study\b", flags=re.IGNORECASE),
    re.compile(r"\bchallenge\b", flags=re.IGNORECASE),
]

_CS_CATEGORY_LABELS_ZH = {
    "cs.AI": "人工智能",
    "cs.AR": "体系结构",
    "cs.CC": "计算复杂性",
    "cs.CE": "计算工程",
    "cs.CG": "计算几何",
    "cs.CL": "计算语言学",
    "cs.CR": "密码与安全",
    "cs.CV": "计算机视觉",
    "cs.CY": "计算机与社会",
    "cs.DB": "数据库",
    "cs.DC": "分布式计算",
    "cs.DL": "数字图书馆",
    "cs.DM": "离散数学",
    "cs.DS": "数据结构与算法",
    "cs.ET": "新兴技术",
    "cs.FL": "形式语言",
    "cs.GL": "通用文献",
    "cs.GR": "图形学",
    "cs.GT": "博弈论",
    "cs.HC": "人机交互",
    "cs.IR": "信息检索",
    "cs.IT": "信息论",
    "cs.LG": "机器学习",
    "cs.LO": "逻辑",
    "cs.MA": "多智能体",
    "cs.MM": "多媒体",
    "cs.MS": "数学软件",
    "cs.NA": "数值分析",
    "cs.NE": "神经与演化计算",
    "cs.NI": "网络与互联网",
    "cs.OH": "其他计算机科学",
    "cs.OS": "操作系统",
    "cs.PF": "性能",
    "cs.PL": "编程语言",
    "cs.RO": "机器人",
    "cs.SC": "符号计算",
    "cs.SD": "声音",
    "cs.SE": "软件工程",
    "cs.SI": "社会与信息网络",
    "cs.SY": "系统与控制",
}

_FALLBACK_DIRECTION_RULES = [
    {
        "key": "multimodal_llm",
        "label": "多模态大模型",
        "patterns": [
            r"\bmultimodal\b",
            r"\bmulti-modal\b",
            r"\bvision[- ]language\b",
            r"\bvisual[- ]language\b",
            r"\bvlm\b",
            r"\blvlm\b",
            r"\bmllm\b",
            r"\bmultimodal language\b",
        ],
    },
    {
        "key": "vla_embodied",
        "label": "VLA / 具身智能",
        "patterns": [
            r"\bvision[- ]language[- ]action\b",
            r"\bvla\b",
            r"\bembodied\b",
            r"\bmanipulation\b",
            r"\brobot\b",
            r"\brobotic\b",
            r"\bnavigation\b",
            r"\bpolicy\b",
            r"\baffordance\b",
        ],
    },
    {
        "key": "video_understanding",
        "label": "长视频理解",
        "patterns": [
            r"\blong video\b",
            r"\bvideo understanding\b",
            r"\bvideo\b",
            r"\btemporal\b",
            r"\bframe\b",
            r"\bstreaming video\b",
        ],
    },
    {
        "key": "vision_generation",
        "label": "视觉生成模型",
        "patterns": [
            r"\bdiffusion\b",
            r"\bgenerative\b",
            r"\bgeneration\b",
            r"\bimage synthesis\b",
            r"\btext[- ]to[- ]image\b",
            r"\bimage generation\b",
            r"\bautoregressive image\b",
        ],
    },
    {
        "key": "vision_model",
        "label": "视觉模型",
        "patterns": [
            r"\bvision transformer\b",
            r"\bvisual representation\b",
            r"\bsegmentation\b",
            r"\bdetection\b",
            r"\bobject\b",
            r"\bimage\b",
            r"\bvisual\b",
        ],
    },
    {
        "key": "three_d_spatial",
        "label": "3D / 空间重建",
        "patterns": [
            r"\b3d\b",
            r"\breconstruction\b",
            r"\bpoint cloud\b",
            r"\bgaussian splatting\b",
            r"\bnerf\b",
            r"\bspatial\b",
            r"\bgeometric\b",
            r"\bscene\b",
        ],
    },
    {
        "key": "llm_reasoning_agent",
        "label": "LLM 推理 / Agent",
        "patterns": [
            r"\bllm\b",
            r"\blarge language model\b",
            r"\breasoning\b",
            r"\bagent\b",
            r"\bplanning\b",
            r"\btool use\b",
            r"\bchain[- ]of[- ]thought\b",
        ],
    },
    {
        "key": "efficient_training",
        "label": "高效训练 / 压缩",
        "patterns": [
            r"\bcompression\b",
            r"\bquantization\b",
            r"\bpruning\b",
            r"\bdistillation\b",
            r"\befficient\b",
            r"\bpre[- ]train\b",
            r"\bfine[- ]tuning\b",
            r"\blora\b",
        ],
    },
    {
        "key": "retrieval_rag",
        "label": "检索增强 / RAG",
        "patterns": [
            r"\bretrieval\b",
            r"\brag\b",
            r"\bsearch\b",
            r"\brecommendation\b",
            r"\bindexing\b",
        ],
    },
    {
        "key": "safety_eval",
        "label": "安全评测 / 对齐",
        "patterns": [
            r"\bsafety\b",
            r"\balignment\b",
            r"\bjailbreak\b",
            r"\bred teaming\b",
        ],
    },
]

_TOPIC_ALIAS_PATTERNS = [
    (re.compile(r"\bmultimodal large language models?\b", flags=re.IGNORECASE), "multimodal llm"),
    (re.compile(r"\bmultimodal large language\b", flags=re.IGNORECASE), "multimodal llm"),
    (re.compile(r"\blarge language models?\b", flags=re.IGNORECASE), "llm"),
    (re.compile(r"\blarge language\b", flags=re.IGNORECASE), "llm"),
    (re.compile(r"\bllms\b", flags=re.IGNORECASE), "llm"),
    (re.compile(r"\bvision[- ]language[- ]action\b", flags=re.IGNORECASE), "vla"),
    (re.compile(r"\bvision[- ]language models?\b", flags=re.IGNORECASE), "vlm"),
    (re.compile(r"\bvision[- ]language model\b", flags=re.IGNORECASE), "vlm"),
    (re.compile(r"\bretrieval[- ]augmented generation\b", flags=re.IGNORECASE), "rag"),
    (re.compile(r"\btest[- ]time adaptation\b", flags=re.IGNORECASE), "test-time adaptation"),
    (re.compile(r"\bchain[- ]of[- ]thought\b", flags=re.IGNORECASE), "chain-of-thought"),
    (re.compile(r"\bworld models?\b", flags=re.IGNORECASE), "world model"),
]

_TOPIC_DISPLAY_LABELS = {
    "llm": "LLM",
    "multimodal llm": "Multimodal LLM",
    "vlm": "VLM",
    "vla": "VLA",
    "rag": "RAG",
    "asr": "ASR",
    "ocr": "OCR",
    "rl": "RL",
    "nerf": "NeRF",
    "moe": "MoE",
    "test-time adaptation": "Test-Time Adaptation",
    "chain-of-thought": "Chain-of-Thought",
    "world model": "World Model",
}


@dataclass(slots=True)
class _TopicAggregate:
    paper_support: int = 0
    weighted_score: float = 0.0
    surfaces: Counter[str] = field(default_factory=Counter)
    co_topics: Counter[str] = field(default_factory=Counter)
    example_title: str = ""


@dataclass(slots=True)
class _PaperTrendRecord:
    title: str
    abstract: str
    primary_category: str
    categories: list[str]
    topics: dict[str, dict]


@dataclass(frozen=True, slots=True)
class _TrendSubdomainPreset:
    key: str
    label: str
    query: str
    scope: str
    primary_categories: tuple[str, ...] | None = None


def _build_subdomain_presets() -> tuple[_TrendSubdomainPreset, ...]:
    presets = [
        _TrendSubdomainPreset(
            key="all",
            label="全部计算机科学",
            query="cat:cs.*",
            scope="cs",
            primary_categories=None,
        )
    ]
    curated_groups = (
        (
            "ai_language",
            "人工智能、语言与检索",
            ("cs.AI", "cs.CL", "cs.IR", "cs.MA", "cs.NE"),
        ),
        (
            "machine_learning",
            "机器学习",
            ("cs.LG",),
        ),
        (
            "vision_multimedia",
            "计算机视觉与多媒体",
            ("cs.CV", "cs.GR", "cs.MM", "cs.SD"),
        ),
        (
            "robotics_control",
            "机器人与控制",
            ("cs.RO", "cs.SY"),
        ),
        (
            "security_systems",
            "安全、系统与软件",
            ("cs.AR", "cs.CE", "cs.CR", "cs.DB", "cs.DC", "cs.NI", "cs.OS", "cs.PF", "cs.SE"),
        ),
        (
            "theory_algorithms",
            "理论、算法与程序语言",
            ("cs.CC", "cs.CG", "cs.DM", "cs.DS", "cs.FL", "cs.GT", "cs.IT", "cs.LO", "cs.MS", "cs.NA", "cs.PL", "cs.SC"),
        ),
        (
            "interaction_society",
            "交互、社会与交叉计算",
            ("cs.CY", "cs.DL", "cs.ET", "cs.GL", "cs.HC", "cs.OH", "cs.SI"),
        ),
    )
    covered_categories: set[str] = set()
    all_categories = set(_CS_CATEGORY_LABELS_ZH.keys())
    for key, label, categories in curated_groups:
        category_tuple = tuple(categories)
        duplicates = covered_categories & set(category_tuple)
        if duplicates:
            raise ValueError(f"duplicate curated categories: {sorted(duplicates)}")
        covered_categories.update(category_tuple)
        presets.append(
            _TrendSubdomainPreset(
                key=key,
                label=label,
                query="cat:cs.*",
                scope=key,
                primary_categories=category_tuple,
            )
        )
    missing_categories = sorted(all_categories - covered_categories)
    if missing_categories:
        raise ValueError(f"missing curated categories: {missing_categories}")
    return tuple(presets)


class ArxivTrendService:
    def __init__(self) -> None:
        self.llm = LLMClient()

    _SUBDOMAIN_PRESETS = _build_subdomain_presets()

    @classmethod
    def list_subdomains(cls) -> list[dict]:
        return [
            {
                "key": preset.key,
                "label": preset.label,
            }
            for preset in cls._SUBDOMAIN_PRESETS
        ]

    @classmethod
    def _preset_for(cls, subdomain_key: str | None) -> _TrendSubdomainPreset:
        requested = str(subdomain_key or "").strip().lower()
        for preset in cls._SUBDOMAIN_PRESETS:
            if preset.key == requested:
                return preset
        return cls._SUBDOMAIN_PRESETS[0]

    def get_snapshot(
        self,
        *,
        subdomain_key: str = "all",
        sample_limit: int = _TREND_MAX_SAMPLE_LIMIT,
        fallback_days: int = 7,
        allow_compute: bool = True,
    ) -> dict:
        preset = self._preset_for(subdomain_key)
        today_local = user_date_str()
        latest_any: dict | None = None
        latest_available: dict | None = None
        today_available: dict | None = None
        today_unavailable: dict | None = None
        with session_scope() as session:
            repo = GeneratedContentRepository(session)
            rows = repo.list_by_type_and_keyword(_TREND_CONTENT_TYPE, preset.key, limit=12)
            for row in rows:
                metadata = dict(getattr(row, "metadata_json", None) or {})
                if int(metadata.get("snapshot_version") or 0) != _TREND_SNAPSHOT_VERSION:
                    continue
                snapshot = metadata.get("snapshot")
                if not isinstance(snapshot, dict):
                    continue
                hydrated = self._hydrate_stored_snapshot(
                    snapshot,
                    preset=preset,
                    generated_content_id=str(getattr(row, "id", "") or ""),
                    generated_for_user_date=str(metadata.get("generated_for_user_date") or ""),
                )
                if latest_any is None:
                    latest_any = hydrated
                if hydrated.get("available") and latest_available is None:
                    latest_available = hydrated
                if str(metadata.get("generated_for_user_date") or "") == today_local:
                    if hydrated.get("available"):
                        today_available = hydrated
                        break
                    if today_unavailable is None:
                        today_unavailable = hydrated
        if today_available is not None:
            return today_available
        if latest_available is not None and not allow_compute:
            return latest_available
        if allow_compute:
            computed = self.generate_and_store_snapshot(
                subdomain_key=preset.key,
                sample_limit=sample_limit,
                fallback_days=fallback_days,
            )
            if computed.get("available"):
                return computed
        return latest_available or today_unavailable or latest_any or self._empty_snapshot(preset, message="当前还没有可用趋势快照")

    def generate_and_store_snapshot(
        self,
        *,
        subdomain_key: str = "all",
        sample_limit: int = _TREND_MAX_SAMPLE_LIMIT,
        fallback_days: int = 7,
    ) -> dict:
        preset = self._preset_for(subdomain_key)
        snapshot = self.today_snapshot(
            sample_limit=sample_limit,
            fallback_days=fallback_days,
            subdomain_key=preset.key,
        )
        if not snapshot.get("available"):
            fallback = self.get_snapshot(
                subdomain_key=preset.key,
                sample_limit=sample_limit,
                fallback_days=fallback_days,
                allow_compute=False,
            )
            if fallback.get("available"):
                return fallback
            return snapshot
        return self._store_snapshot(snapshot, preset=preset)

    def precompute_all_subdomains(
        self,
        *,
        sample_limit: int = _TREND_MAX_SAMPLE_LIMIT,
        fallback_days: int = 7,
    ) -> dict:
        results: list[dict] = []
        for preset in self._SUBDOMAIN_PRESETS:
            try:
                snapshot = self.generate_and_store_snapshot(
                    subdomain_key=preset.key,
                    sample_limit=sample_limit,
                    fallback_days=fallback_days,
                )
                results.append(
                    {
                        "subdomain_key": preset.key,
                        "subdomain_label": preset.label,
                        "query_date": snapshot.get("query_date"),
                        "sample_size": snapshot.get("sample_size"),
                        "direction": snapshot.get("direction"),
                        "status": "ok",
                    }
                )
            except Exception as exc:
                logger.exception("precompute arxiv trend failed for %s", preset.key)
                results.append(
                    {
                        "subdomain_key": preset.key,
                        "subdomain_label": preset.label,
                        "status": "error",
                        "error": str(exc),
                    }
                )
        return {
            "generated_for_user_date": user_date_str(),
            "subdomains": results,
        }

    def today_snapshot(
        self,
        *,
        sample_limit: int = _TREND_MAX_SAMPLE_LIMIT,
        fallback_days: int = 3,
        subdomain_key: str = "all",
    ) -> dict:
        """Fetch a lightweight arXiv Computer Science trend snapshot.

        arXiv's public API does not provide a dedicated trends endpoint. We use
        a CS category query (`cat:cs.*`) and walk backward until the latest
        UTC submittedDate day with parsed entries is found. This avoids showing
        "today unavailable" during weekends, holidays, or arXiv API indexing
        gaps.
        """

        preset = self._preset_for(subdomain_key)
        limit = max(10, min(int(sample_limit), _TREND_MAX_SAMPLE_LIMIT))
        today_utc = datetime.now(UTC).date()
        last_error = ""

        for offset in range(max(1, fallback_days + 1)):
            target_day = today_utc - timedelta(days=offset)
            try:
                total, papers = self._fetch_day(
                    target_day,
                    limit,
                    query=preset.query,
                    primary_categories=preset.primary_categories,
                )
            except Exception as exc:  # pragma: no cover - external dependency
                last_error = str(exc)
                continue
            if papers:
                return self._build_snapshot(target_day, total, papers, offset=offset, preset=preset)

        return self._empty_snapshot(
            preset,
            message=last_error or f"最近 {fallback_days + 1} 天暂未解析到 {preset.label} 投稿",
        )

    def _empty_snapshot(self, preset: _TrendSubdomainPreset, *, message: str) -> dict:
        return {
            "available": False,
            "source": "arxiv_api",
            "scope": preset.scope,
            "query": preset.query,
            "subdomain_key": preset.key,
            "subdomain_label": preset.label,
            "subdomains": self.list_subdomains(),
            "message": message,
            "query_date": datetime.now(UTC).date().isoformat(),
            "window_label": f"{preset.label} 趋势待生成",
            "total_submissions": 0,
            "sample_size": 0,
            "archives": [],
            "categories": [],
            "directions": [],
            "keywords": [],
            "top_terms": [],
            "recent_papers": [],
            "direction": "暂无可用趋势",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    def _fetch_day(
        self,
        day: date,
        sample_limit: int,
        *,
        query: str,
        primary_categories: tuple[str, ...] | None = None,
    ) -> tuple[int, list[dict]]:
        start = day.strftime("%Y%m%d000000")
        end = day.strftime("%Y%m%d235959")
        query_expr = f"({query}) AND submittedDate:[{start} TO {end}]"
        page_size = min(100 if primary_categories else 200, sample_limit)
        papers: list[dict] = []
        raw_total = 0
        filtered_total = 0
        category_set = set(primary_categories or ())
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "ResearchOS/1.0 (dashboard trend; contact: local)"},
        ) as client:
            start_index = 0
            while True:
                params = {
                    "search_query": query_expr,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "start": start_index,
                    "max_results": page_size,
                }
                response = client.get(ARXIV_API_URL, params=params)
                response.raise_for_status()
                root = ElementTree.fromstring(response.text)
                if start_index == 0:
                    total_text = root.findtext("opensearch:totalResults", namespaces=_ATOM_NS) or "0"
                    try:
                        raw_total = int(total_text.strip())
                    except ValueError:
                        raw_total = 0
                page_items = self._parse_entries(root)
                if not page_items:
                    break
                if primary_categories:
                    page_items = [
                        item
                        for item in page_items
                        if str(item.get("primary_category") or "") in category_set
                    ]
                    filtered_total += len(page_items)
                    if len(papers) < sample_limit:
                        papers.extend(page_items[: max(0, sample_limit - len(papers))])
                    start_index += page_size
                    if start_index >= raw_total:
                        break
                    continue

                papers.extend(page_items)
                target = min(raw_total or sample_limit, sample_limit)
                if len(papers) >= target:
                    break
                start_index += page_size
                if start_index >= raw_total:
                    break
        if primary_categories:
            return filtered_total, papers[:sample_limit]
        return raw_total, papers[:sample_limit]

    def _parse_entries(self, root: ElementTree.Element) -> list[dict]:
        items: list[dict] = []
        for entry in root.findall("atom:entry", _ATOM_NS):
            title = _clean_text(entry.findtext("atom:title", namespaces=_ATOM_NS))
            abstract = _clean_text(entry.findtext("atom:summary", namespaces=_ATOM_NS))
            arxiv_id = str(entry.findtext("atom:id", namespaces=_ATOM_NS) or "").rsplit("/", 1)[-1]
            published = str(entry.findtext("atom:published", namespaces=_ATOM_NS) or "")
            categories = [
                str(category.get("term") or "").strip()
                for category in entry.findall("atom:category", _ATOM_NS)
                if str(category.get("term") or "").strip()
            ]
            items.append(
                {
                    "arxiv_id": arxiv_id,
                    "title": title,
                    "abstract": abstract,
                    "published_at": published,
                    "categories": categories,
                    "primary_category": categories[0] if categories else "",
                }
            )
        return items

    def _build_snapshot(
        self,
        day: date,
        total: int,
        papers: list[dict],
        *,
        offset: int,
        preset: _TrendSubdomainPreset,
    ) -> dict:
        category_counter: Counter[str] = Counter()
        topic_aggregates: dict[str, _TopicAggregate] = {}
        paper_records: list[_PaperTrendRecord] = []

        for paper in papers:
            categories = [
                str(item)
                for item in paper.get("categories") or []
                if str(item).startswith("cs.")
            ]
            if categories:
                category_counter.update(categories[:2])
            title = str(paper.get("title") or "")
            abstract = str(paper.get("abstract") or "")
            paper_topics = _extract_paper_topics(
                title,
                abstract,
            )
            paper_records.append(
                _PaperTrendRecord(
                    title=title,
                    abstract=abstract,
                    primary_category=str(paper.get("primary_category") or ""),
                    categories=list(paper.get("categories") or []),
                    topics=paper_topics,
                )
            )
            if not paper_topics:
                continue
            paper_title = title
            topic_keys = list(paper_topics.keys())
            for topic_key, payload in paper_topics.items():
                aggregate = topic_aggregates.setdefault(topic_key, _TopicAggregate())
                aggregate.paper_support += 1
                aggregate.weighted_score += float(payload.get("score") or 0.0)
                aggregate.surfaces.update(payload.get("surfaces") or {})
                if not aggregate.example_title:
                    aggregate.example_title = paper_title
                for other_key in topic_keys:
                    if other_key != topic_key:
                        aggregate.co_topics[other_key] += 1

        categories = _counter_rows(category_counter, len(papers), limit=10)
        keywords = _keyword_rows_from_topics(topic_aggregates, len(papers), limit=14)
        directions = self._partition_directions_with_llm(
            day=day,
            sample_size=len(papers),
            categories=categories,
            keywords=keywords,
            paper_records=paper_records,
            subdomain_label=preset.label,
        )
        if len(directions) < 3:
            partitioned_directions = _partition_direction_rows(
                paper_records,
                topic_aggregates,
                sample_size=len(papers),
                limit=8,
            )
            directions = self._refine_directions_with_llm(
                day=day,
                sample_size=len(papers),
                categories=categories,
                keywords=keywords,
                direction_rows=partitioned_directions,
                subdomain_label=preset.label,
            )
            if len(directions) < 3:
                directions = _merge_with_fallback_directions(
                    dynamic_rows=partitioned_directions,
                    papers=papers,
                    sample_size=len(papers),
                    limit=8,
                )
        # Keep legacy names for compatibility with older frontend bundles.
        top_terms = [{"term": item["keyword"], "count": item["count"]} for item in keywords]
        direction = _direction_sentence(directions, keywords)

        return {
            "available": True,
            "source": "arxiv_api",
            "scope": preset.scope,
            "query": preset.query,
            "subdomain_key": preset.key,
            "subdomain_label": preset.label,
            "subdomains": self.list_subdomains(),
            "query_date": day.isoformat(),
            "window_label": (
                f"{preset.label} · UTC 今日"
                if offset == 0
                else f"{preset.label} · 最近非空发布日 UTC {day.isoformat()}"
            ),
            "total_submissions": int(total),
            "sample_size": len(papers),
            "archives": categories,
            "categories": categories,
            "directions": directions,
            "keywords": keywords,
            "top_terms": top_terms,
            "recent_papers": [
                {
                    "arxiv_id": paper.get("arxiv_id"),
                    "title": paper.get("title"),
                    "primary_category": paper.get("primary_category"),
                    "categories": list(paper.get("categories") or [])[:3],
                    "published_at": paper.get("published_at"),
                }
                for paper in papers[:6]
            ],
            "direction": direction,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    def _partition_directions_with_llm(
        self,
        *,
        day: date,
        sample_size: int,
        categories: list[dict],
        keywords: list[dict],
        paper_records: list[_PaperTrendRecord],
        subdomain_label: str,
    ) -> list[dict]:
        if len(paper_records) < 6:
            return []
        target_direction_count = _llm_partition_target_direction_count(sample_size)
        prompt = _build_llm_partition_prompt(
            day=day,
            sample_size=sample_size,
            categories=categories,
            keywords=keywords,
            paper_records=paper_records,
            subdomain_label=subdomain_label,
        )
        cfg = self.llm._config()
        model_override = str(getattr(cfg, "model_fallback", "") or "").strip() or None
        try:
            result = self.llm.complete_json(
                prompt,
                stage="dashboard_trend_partition",
                model_override=model_override,
                max_tokens=4200,
                max_retries=1,
                request_timeout=90,
            )
            self.llm.trace_result(
                result,
                stage="dashboard_trend_partition",
                prompt_digest=f"arxiv_trend_partition:{subdomain_label}:{day.isoformat()}:{sample_size}",
            )
        except Exception as exc:
            logger.warning("dashboard trend LLM partition failed: %s", exc)
            return []

        parsed = result.parsed_json
        if not isinstance(parsed, dict):
            logger.warning("dashboard trend LLM partition returned invalid JSON")
            return []
        raw_directions = parsed.get("directions")
        if not isinstance(raw_directions, list):
            return []
        if len(raw_directions) != target_direction_count:
            logger.warning(
                "dashboard trend LLM partition returned %d directions, expected %d",
                len(raw_directions),
                target_direction_count,
            )
            return []

        total_papers = len(paper_records)
        valid_indexes = set(range(1, total_papers + 1))
        seen_indexes: set[int] = set()
        direction_rows: list[dict] = []

        for position, item in enumerate(raw_directions, start=1):
            if not isinstance(item, dict):
                continue
            raw_indexes = item.get("paper_indexes")
            if not isinstance(raw_indexes, list):
                return []
            indexes: list[int] = []
            local_seen: set[int] = set()
            for raw_index in raw_indexes:
                try:
                    paper_index = int(raw_index)
                except (TypeError, ValueError):
                    return []
                if paper_index not in valid_indexes or paper_index in seen_indexes or paper_index in local_seen:
                    return []
                local_seen.add(paper_index)
                indexes.append(paper_index)
            if not indexes:
                continue
            seen_indexes.update(indexes)
            label = str(item.get("label") or "").strip() or f"方向 {position}"
            if label in {"其他交叉主题", "其他", "杂项", "长尾"}:
                return []
            summary = str(item.get("summary") or "").strip()
            raw_keywords = item.get("keywords")
            normalized_keywords: list[dict] = []
            if isinstance(raw_keywords, list):
                seen_keyword_values: set[str] = set()
                for entry in raw_keywords[:4]:
                    keyword = str(entry or "").strip()
                    if not keyword or keyword in seen_keyword_values:
                        continue
                    seen_keyword_values.add(keyword)
                    normalized_keywords.append({"keyword": keyword, "count": 0})
            example_titles = [
                paper_records[index - 1].title
                for index in indexes[:4]
                if 1 <= index <= total_papers and paper_records[index - 1].title
            ]
            direction_rows.append(
                {
                    "key": f"direction-{position}",
                    "label": label[:40],
                    "count": len(indexes),
                    "sample_ratio": round(len(indexes) / max(1, sample_size), 4),
                    "keywords": normalized_keywords,
                    "example_title": example_titles[0] if example_titles else "",
                    "example_titles": example_titles,
                    "summary": summary[:220] if summary else "",
                }
            )

        if seen_indexes != valid_indexes:
            logger.warning(
                "dashboard trend LLM partition missing assignments: assigned=%d expected=%d",
                len(seen_indexes),
                len(valid_indexes),
            )
            return []

        direction_rows.sort(
            key=lambda item: (-int(item.get("count") or 0), str(item.get("label") or "")),
        )
        direction_sentence = str(parsed.get("direction_sentence") or "").strip()
        if direction_sentence:
            for row in direction_rows:
                row["_direction_sentence"] = direction_sentence[:280]
        return direction_rows

    def _refine_directions_with_llm(
        self,
        *,
        day: date,
        sample_size: int,
        categories: list[dict],
        keywords: list[dict],
        direction_rows: list[dict],
        subdomain_label: str,
    ) -> list[dict]:
        if not direction_rows:
            return []
        prompt = _build_llm_direction_prompt(
            day=day,
            sample_size=sample_size,
            categories=categories,
            keywords=keywords,
            direction_rows=direction_rows,
            subdomain_label=subdomain_label,
        )
        cfg = self.llm._config()
        model_override = str(getattr(cfg, "model_fallback", "") or "").strip() or None
        try:
            result = self.llm.complete_json(
                prompt,
                stage="dashboard_trend",
                model_override=model_override,
                max_tokens=2200,
                max_retries=1,
                request_timeout=45,
            )
            self.llm.trace_result(
                result,
                stage="dashboard_trend",
                prompt_digest=f"arxiv_trend:{subdomain_label}:{day.isoformat()}:{sample_size}",
            )
        except Exception as exc:
            logger.warning("dashboard trend LLM refinement failed: %s", exc)
            return direction_rows

        parsed = result.parsed_json
        if not isinstance(parsed, dict):
            logger.warning("dashboard trend LLM returned invalid JSON")
            return direction_rows

        items = parsed.get("directions")
        if not isinstance(items, list):
            return direction_rows

        by_key = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            cluster_key = str(item.get("cluster_key") or "").strip()
            if cluster_key:
                by_key[cluster_key] = item

        if not by_key:
            return direction_rows

        refined: list[dict] = []
        for row in direction_rows:
            updated = dict(row)
            payload = by_key.get(str(row.get("key") or ""))
            if isinstance(payload, dict):
                label = str(payload.get("label") or "").strip()
                summary = str(payload.get("summary") or "").strip()
                raw_keywords = payload.get("keywords")
                if label:
                    updated["label"] = label[:40]
                if summary:
                    updated["summary"] = summary[:220]
                if isinstance(raw_keywords, list):
                    normalized_keywords = []
                    seen_keywords: set[str] = set()
                    for entry in raw_keywords[:4]:
                        keyword = str(entry or "").strip()
                        if not keyword or keyword in seen_keywords:
                            continue
                        seen_keywords.add(keyword)
                        normalized_keywords.append({"keyword": keyword, "count": 0})
                    if normalized_keywords:
                        updated["keywords"] = normalized_keywords
            refined.append(updated)

        direction_sentence = str(parsed.get("direction_sentence") or "").strip()
        if direction_sentence:
            for row in refined:
                row.setdefault("_direction_sentence", direction_sentence[:280])
        return refined

    def _hydrate_stored_snapshot(
        self,
        snapshot: dict,
        *,
        preset: _TrendSubdomainPreset,
        generated_content_id: str,
        generated_for_user_date: str,
    ) -> dict:
        hydrated = dict(snapshot)
        hydrated["subdomain_key"] = preset.key
        hydrated["subdomain_label"] = preset.label
        hydrated["subdomains"] = self.list_subdomains()
        hydrated["generated_content_id"] = generated_content_id
        hydrated["generated_for_user_date"] = generated_for_user_date
        hydrated["precomputed"] = True
        return hydrated

    def _store_snapshot(self, snapshot: dict, *, preset: _TrendSubdomainPreset) -> dict:
        stored_snapshot = dict(snapshot)
        stored_snapshot["subdomain_key"] = preset.key
        stored_snapshot["subdomain_label"] = preset.label
        stored_snapshot["subdomains"] = self.list_subdomains()
        generated_for_user_date = user_date_str()
        metadata = {
            "snapshot_version": _TREND_SNAPSHOT_VERSION,
            "generated_for_user_date": generated_for_user_date,
            "subdomain_key": preset.key,
            "subdomain_label": preset.label,
            "snapshot": stored_snapshot,
        }
        title = f"arXiv 趋势快照 · {preset.label} · {stored_snapshot.get('query_date') or generated_for_user_date}"
        with session_scope() as session:
            repo = GeneratedContentRepository(session)
            existing = None
            for row in repo.list_by_type_and_keyword(_TREND_CONTENT_TYPE, preset.key, limit=12):
                row_meta = dict(getattr(row, "metadata_json", None) or {})
                if (
                    int(row_meta.get("snapshot_version") or 0) == _TREND_SNAPSHOT_VERSION
                    and str(row_meta.get("generated_for_user_date") or "") == generated_for_user_date
                ):
                    existing = row
                    break
            if existing is not None:
                existing.title = title
                existing.markdown = str(stored_snapshot.get("direction") or "")
                existing.metadata_json = metadata
                session.flush()
                content_id = str(existing.id)
            else:
                created = repo.create(
                    content_type=_TREND_CONTENT_TYPE,
                    title=title,
                    markdown=str(stored_snapshot.get("direction") or ""),
                    keyword=preset.key,
                    metadata_json=metadata,
                )
                content_id = str(created.id)
        return self._hydrate_stored_snapshot(
            stored_snapshot,
            preset=preset,
            generated_content_id=content_id,
            generated_for_user_date=generated_for_user_date,
        )


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text.lower())
    return [
        token
        for token in tokens
        if token not in _TERM_STOPWORDS
        and not token.isdigit()
        and 3 <= len(token) <= 28
    ]


def _topic_tokens(text: str) -> list[tuple[str, str]]:
    return [(token, token.lower()) for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", text or "")]


def _singularize_token(token: str) -> str:
    if len(token) <= 4:
        return token
    if token.endswith("ies") and len(token) > 5:
        return f"{token[:-3]}y"
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _canonicalize_topic_phrase(phrase: str) -> str:
    normalized = _clean_text(phrase).lower()
    if not normalized:
        return ""
    normalized = normalized.replace("/", " ")
    normalized = re.sub(r"[_]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for pattern, replacement in _TOPIC_ALIAS_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    normalized = " ".join(_singularize_token(token) for token in normalized.split())
    normalized = re.sub(r"\s+", " ", normalized).strip(" -")
    if normalized in _TOPIC_REJECT_KEYS:
        return ""
    if any(pattern.search(normalized) for pattern in _TOPIC_REJECT_PATTERNS):
        return ""
    return normalized


def _is_signal_singleton(raw: str, token: str) -> bool:
    if not token or token in _TOPIC_BOUNDARY_STOPWORDS or token in _TOPIC_GENERIC_TOKENS:
        return False
    if re.fullmatch(r"[A-Z]{2,8}s?", raw or ""):
        return True
    if re.fullmatch(r"[A-Za-z]+-\d+", raw or ""):
        return True
    return False


def _is_valid_topic_ngram(raw_tokens: list[str], tokens: list[str]) -> bool:
    if not tokens:
        return False
    if any(token in _TOPIC_BOUNDARY_STOPWORDS for token in tokens):
        return False
    phrase = " ".join(tokens)
    if len(phrase) < 4 or len(phrase) > 64:
        return False
    if len(tokens) == 1:
        return _is_signal_singleton(raw_tokens[0], tokens[0])
    if tokens[0] in _TOPIC_GENERIC_PREFIXES and len(tokens) <= 2:
        return False
    signal_tokens = [token for token in tokens if token not in _TOPIC_GENERIC_TOKENS]
    if len(signal_tokens) < 1:
        return False
    if len(tokens) >= 3 and sum(1 for token in signal_tokens if len(token) >= 4) < 2:
        return False
    if len(tokens) == 2 and all(token in _TOPIC_GENERIC_TOKENS for token in tokens):
        return False
    return True


def _topic_phrase_score(raw_tokens: list[str], tokens: list[str], *, source: str) -> float:
    signal_count = sum(1 for token in tokens if token not in _TOPIC_GENERIC_TOKENS)
    acronym_bonus = 0.45 if any(re.fullmatch(r"[A-Z]{2,8}s?", raw or "") for raw in raw_tokens) else 0.0
    size_bonus = {
        1: 0.7,
        2: 1.1,
        3: 1.25,
        4: 1.15,
    }.get(len(tokens), 1.0)
    source_multiplier = 2.4 if source == "title" else 0.95
    return source_multiplier * (size_bonus + signal_count * 0.14 + acronym_bonus)


def _iter_topic_phrases(text: str, *, source: str) -> list[tuple[str, float]]:
    token_pairs = _topic_tokens(text)
    if not token_pairs:
        return []
    raw_tokens = [raw for raw, _token in token_pairs]
    tokens = [token for _raw, token in token_pairs]
    min_size = 1 if source == "title" else 2
    local: dict[str, float] = {}
    for size in range(min_size, 5):
        for index in range(0, max(0, len(tokens) - size + 1)):
            raw_chunk = raw_tokens[index : index + size]
            chunk = tokens[index : index + size]
            if not _is_valid_topic_ngram(raw_chunk, chunk):
                continue
            phrase = " ".join(chunk)
            score = _topic_phrase_score(raw_chunk, chunk, source=source)
            previous = local.get(phrase, 0.0)
            if score > previous:
                local[phrase] = score
    return sorted(local.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))


def _extract_paper_topics(title: str, abstract: str) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for source, text in (("title", title), ("abstract", abstract)):
        for phrase, score in _iter_topic_phrases(text, source=source):
            key = _canonicalize_topic_phrase(phrase)
            if not key:
                continue
            payload = candidates.setdefault(
                key,
                {
                    "score": 0.0,
                    "surfaces": Counter(),
                },
            )
            if source == "title":
                payload["score"] = max(float(payload["score"] or 0.0), score)
            else:
                payload["score"] = float(payload["score"] or 0.0) + min(score, 1.4)
            payload["surfaces"][phrase] += score
    ordered = sorted(
        candidates.items(),
        key=lambda item: (-float(item[1].get("score") or 0.0), -len(item[0]), item[0]),
    )
    return {key: payload for key, payload in ordered[:12]}


def _humanize_topic_label(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    mapped = _TOPIC_DISPLAY_LABELS.get(cleaned.lower())
    if mapped:
        return mapped

    def _humanize_token(token: str) -> str:
        normalized = token.lower()
        mapped_token = _TOPIC_DISPLAY_LABELS.get(normalized)
        if mapped_token:
            return mapped_token
        if normalized in {"llm", "vlm", "vla", "rag", "asr", "ocr", "rl", "moe"}:
            return normalized.upper()
        if normalized == "nerf":
            return "NeRF"
        if "-" in token:
            return "-".join(_humanize_token(part) for part in token.split("-"))
        if token.isupper():
            return token
        if re.fullmatch(r"\d+d", normalized):
            return normalized.upper()
        return normalized.capitalize()

    return " ".join(_humanize_token(part) for part in cleaned.split())


def _topic_display_label(key: str, surfaces: Counter[str]) -> str:
    mapped = _TOPIC_DISPLAY_LABELS.get(str(key or "").lower())
    if mapped:
        return mapped
    for surface, _count in surfaces.most_common():
        canonical = _canonicalize_topic_phrase(surface)
        if canonical == key:
            return _humanize_topic_label(surface)
    return _humanize_topic_label(key)


def _topic_signal_tokens(key: str) -> set[str]:
    tokens = [
        token
        for token in re.split(r"[\s/-]+", str(key or "").lower())
        if token
        and token not in _TOPIC_BOUNDARY_STOPWORDS
        and token not in _TOPIC_GENERIC_TOKENS
    ]
    return set(tokens)


def _topic_rows_too_similar(left_key: str, right_key: str) -> bool:
    left_tokens = _topic_signal_tokens(left_key)
    right_tokens = _topic_signal_tokens(right_key)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    if overlap <= 0:
        return False
    smaller = min(len(left_tokens), len(right_tokens))
    larger = max(len(left_tokens), len(right_tokens))
    if smaller >= 2 and overlap == smaller:
        return True
    return overlap / larger >= 0.75


def _min_topic_support(sample_size: int) -> int:
    if sample_size < 600:
        return 2
    if sample_size < 1200:
        return 3
    return min(8, max(4, int(round(sample_size * 0.01))))


def _is_valid_seed_topic(key: str, aggregate: _TopicAggregate) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized or normalized in _SEED_REJECT_KEYS:
        return False
    tokens = _topic_signal_tokens(normalized)
    if not tokens:
        return False
    if len(tokens) == 1:
        token = next(iter(tokens))
        if token in _SEED_REJECT_KEYS:
            return False
        if aggregate.paper_support >= 18 and len(token) <= 4:
            return False
    return True


def _keyword_rows_from_topics(
    aggregates: dict[str, _TopicAggregate],
    sample_size: int,
    *,
    limit: int,
) -> list[dict]:
    denominator = max(1, sample_size)
    rows: list[dict] = []
    for key, aggregate in sorted(
        aggregates.items(),
        key=lambda item: (-item[1].paper_support, -item[1].weighted_score, item[0]),
    ):
        if aggregate.paper_support < max(2, _min_topic_support(sample_size) - 1):
            continue
        rows.append(
            {
                "keyword": _topic_display_label(key, aggregate.surfaces),
                "term": _topic_display_label(key, aggregate.surfaces),
                "count": int(aggregate.paper_support),
                "example_title": aggregate.example_title,
                "_key": key,
                "_score": float(aggregate.weighted_score),
            }
        )
    selected: list[dict] = []
    for row in rows:
        if any(_topic_rows_too_similar(str(row.get("_key") or ""), str(item.get("_key") or "")) for item in selected):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    for row in selected:
        row.pop("_key", None)
        row.pop("_score", None)
    return selected


def _select_direction_seed_topics(
    aggregates: dict[str, _TopicAggregate],
    sample_size: int,
    *,
    limit: int,
) -> list[str]:
    min_support = _min_topic_support(sample_size)
    seeds: list[str] = []
    ranked = sorted(
        aggregates.items(),
        key=lambda item: (-item[1].paper_support, -item[1].weighted_score, item[0]),
    )
    for key, aggregate in ranked:
        if aggregate.paper_support < min_support:
            continue
        if not _is_valid_seed_topic(key, aggregate):
            continue
        if any(_topic_rows_too_similar(key, existing) for existing in seeds):
            continue
        seeds.append(key)
        if len(seeds) >= limit:
            break
    return seeds


def _best_seed_for_paper(
    seeds: list[str],
    topics: dict[str, dict],
) -> tuple[str | None, float]:
    best_seed: str | None = None
    best_score = 0.0
    for seed in seeds:
        score = _score_seed_for_paper(seed, topics)
        if best_seed is None or score > best_score:
            best_seed = seed
            best_score = score
    return best_seed, best_score


def _paper_seed_candidate(
    paper: _PaperTrendRecord,
    aggregates: dict[str, _TopicAggregate],
    existing_seeds: list[str],
) -> str:
    for topic_key in paper.topics.keys():
        aggregate = aggregates.get(topic_key)
        if aggregate is None or not _is_valid_seed_topic(topic_key, aggregate):
            continue
        if any(_topic_rows_too_similar(topic_key, existing) for existing in existing_seeds):
            continue
        return topic_key
    return ""


def _score_seed_for_paper(seed_key: str, topics: dict[str, dict]) -> float:
    if not topics:
        return 0.0
    seed_tokens = _topic_signal_tokens(seed_key)
    best = 0.0
    for topic_key, payload in topics.items():
        topic_score = float(payload.get("score") or 0.0)
        if topic_key == seed_key:
            best = max(best, topic_score + 3.0)
            continue
        if _topic_rows_too_similar(seed_key, topic_key):
            best = max(best, topic_score + 1.6)
            continue
        overlap = len(seed_tokens & _topic_signal_tokens(topic_key))
        if overlap > 0:
            best = max(best, topic_score * (0.42 + 0.24 * overlap))
    return best


def _seed_cluster_keywords(
    cluster_key: str,
    keyword_counter: Counter[str],
    *,
    limit: int = 4,
) -> list[dict]:
    items: list[dict] = []
    for keyword, count in keyword_counter.most_common(10):
        if _topic_rows_too_similar(cluster_key, keyword):
            continue
        items.append({"keyword": _humanize_topic_label(keyword), "count": int(count)})
        if len(items) >= limit:
            break
    return items


def _partition_direction_rows(
    papers: list[_PaperTrendRecord],
    aggregates: dict[str, _TopicAggregate],
    *,
    sample_size: int,
    limit: int,
) -> list[dict]:
    if not papers:
        return []

    seeds = _select_direction_seed_topics(aggregates, sample_size, limit=max(limit, 1))
    if not seeds:
        return _dynamic_direction_rows(aggregates, sample_size, limit=limit)

    seed_counts: Counter[str] = Counter()
    seed_keywords: dict[str, Counter[str]] = {seed: Counter() for seed in seeds}
    seed_examples: dict[str, list[str]] = {seed: [] for seed in seeds}
    unassigned: list[_PaperTrendRecord] = []

    def _assign(paper: _PaperTrendRecord, seed: str) -> None:
        seed_counts[seed] += 1
        if paper.title and len(seed_examples[seed]) < 4:
            seed_examples[seed].append(paper.title)
        for keyword in list(paper.topics.keys())[:6]:
            seed_keywords[seed][keyword] += 1

    for paper in papers:
        best_seed, best_score = _best_seed_for_paper(seeds, paper.topics)
        if best_seed and best_score > 0:
            _assign(paper, best_seed)
            continue

        candidate_seed = _paper_seed_candidate(paper, aggregates, seeds)
        if candidate_seed and len(seeds) < limit:
            seeds.append(candidate_seed)
            seed_keywords[candidate_seed] = Counter()
            seed_examples[candidate_seed] = []
            _assign(paper, candidate_seed)
            continue

        unassigned.append(paper)

    for paper in unassigned:
        best_seed, best_score = _best_seed_for_paper(seeds, paper.topics)
        if best_seed and best_score > 0:
            _assign(paper, best_seed)
            continue
        fallback_seed = min(
            seeds,
            key=lambda seed: (
                int(seed_counts.get(seed, 0)),
                -float((aggregates.get(seed) or _TopicAggregate()).weighted_score),
                seed,
            ),
        )
        _assign(paper, fallback_seed)

    ranked_rows: list[dict] = []
    for seed in seeds:
        count = int(seed_counts.get(seed, 0))
        if count <= 0:
            continue
        aggregate = aggregates.get(seed, _TopicAggregate())
        ranked_rows.append(
            {
                "key": seed,
                "label": _topic_display_label(seed, aggregate.surfaces),
                "count": count,
                "sample_ratio": round(count / max(1, sample_size), 4),
                "keywords": _seed_cluster_keywords(seed, seed_keywords.get(seed, Counter()), limit=4),
                "example_title": (seed_examples.get(seed) or [""])[0],
                "example_titles": list(seed_examples.get(seed) or []),
                "_score": float(aggregate.weighted_score),
            }
        )

    ranked_rows.sort(key=lambda item: (-int(item.get("count") or 0), -float(item.get("_score") or 0.0), str(item.get("key") or "")))
    final_rows: list[dict] = []
    running_total = 0
    selected = ranked_rows[:limit]
    for index, row in enumerate(selected):
        item = dict(row)
        count = int(item.get("count") or 0)
        if index == len(selected) - 1:
            count = max(0, sample_size - running_total)
        running_total += count
        item["count"] = count
        item["sample_ratio"] = round(count / max(1, sample_size), 4)
        item.pop("_score", None)
        final_rows.append(item)
    return final_rows


def _dynamic_direction_rows(
    aggregates: dict[str, _TopicAggregate],
    sample_size: int,
    *,
    limit: int,
) -> list[dict]:
    denominator = max(1, sample_size)
    min_support = _min_topic_support(sample_size)
    rows: list[dict] = []
    for key, aggregate in sorted(
        aggregates.items(),
        key=lambda item: (-item[1].paper_support, -item[1].weighted_score, item[0]),
    ):
        if aggregate.paper_support < min_support:
            continue
        keywords = []
        for related_key, related_count in aggregate.co_topics.most_common(6):
            if _topic_rows_too_similar(key, related_key):
                continue
            related_label = _topic_display_label(related_key, aggregates.get(related_key, _TopicAggregate()).surfaces)
            if not related_label:
                continue
            keywords.append({"keyword": related_label, "count": int(related_count)})
            if len(keywords) >= 4:
                break
        rows.append(
            {
                "key": key,
                "label": _topic_display_label(key, aggregate.surfaces),
                "count": int(aggregate.paper_support),
                "sample_ratio": round(aggregate.paper_support / denominator, 4),
                "keywords": keywords,
                "example_title": aggregate.example_title,
                "_score": float(aggregate.weighted_score),
            }
        )

    selected: list[dict] = []
    for row in rows:
        if any(_topic_rows_too_similar(str(row.get("key") or ""), str(item.get("key") or "")) for item in selected):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    if len(selected) < min(limit, 4):
        for row in rows:
            if row in selected:
                continue
            selected.append(row)
            if len(selected) >= limit:
                break
    for row in selected:
        row.pop("_score", None)
    return selected


def _match_fallback_directions(title: str, abstract: str) -> list[str]:
    text = f"{title} {abstract}".lower()
    matches = []
    for rule in _FALLBACK_DIRECTION_RULES:
        score = sum(1 for pattern in rule["patterns"] if re.search(pattern, text))
        if score > 0:
            matches.append((str(rule["key"]), score))
    if not matches:
        return []
    return [key for key, _score in sorted(matches, key=lambda item: item[1], reverse=True)[:3]]


def _fallback_direction_rows(papers: list[dict], sample_size: int, *, limit: int) -> list[dict]:
    counter: Counter[str] = Counter()
    examples: dict[str, str] = {}
    label_map = {str(rule["key"]): str(rule["label"]) for rule in _FALLBACK_DIRECTION_RULES}
    denominator = max(1, sample_size)
    for paper in papers:
        title = str(paper.get("title") or "")
        abstract = str(paper.get("abstract") or "")
        for key in _match_fallback_directions(title, abstract):
            counter[key] += 1
            examples.setdefault(key, title)
    rows = []
    for key, count in counter.most_common(limit):
        rows.append(
            {
                "key": key,
                "label": label_map.get(key, key),
                "count": int(count),
                "sample_ratio": round(count / denominator, 4),
                "keywords": [],
                "example_title": examples.get(key, ""),
            }
        )
    return rows


def _merge_with_fallback_directions(
    *,
    dynamic_rows: list[dict],
    papers: list[dict],
    sample_size: int,
    limit: int,
) -> list[dict]:
    selected = list(dynamic_rows)
    fallback_rows = _fallback_direction_rows(papers, sample_size, limit=limit * 2)
    for row in fallback_rows:
        if any(_topic_rows_too_similar(str(row.get("key") or ""), str(item.get("key") or "")) for item in selected):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _llm_partition_target_direction_count(sample_size: int) -> int:
    if sample_size >= 140:
        return 8
    if sample_size >= 90:
        return 7
    if sample_size >= 45:
        return 6
    return 5


def _build_llm_partition_prompt(
    *,
    day: date,
    sample_size: int,
    categories: list[dict],
    keywords: list[dict],
    paper_records: list[_PaperTrendRecord],
    subdomain_label: str,
) -> str:
    target_direction_count = _llm_partition_target_direction_count(sample_size)
    category_lines = [
        f"- {item.get('key')}: {item.get('count')} 篇"
        for item in categories[:6]
    ]
    keyword_lines = [
        f"- {item.get('keyword')}: {item.get('count')}"
        for item in keywords[:10]
    ]
    paper_lines = []
    for index, paper in enumerate(paper_records, start=1):
        topic_text = " / ".join(
            _humanize_topic_label(topic_key)
            for topic_key in list(paper.topics.keys())[:4]
        ) or "无"
        categories_text = " / ".join(str(item).strip() for item in paper.categories[:3] if str(item).strip()) or "无"
        paper_lines.append(
            f"{index}. title={paper.title or '无'} | categories={categories_text} | top_topics={topic_text}"
        )
    return (
        "你是 arXiv 研究趋势编辑。现在需要对同一天、同一个子域的一批论文做方向划分。\n"
        "你的任务不是简单改名，而是要把全部论文按研究方向做一次完整分组。\n\n"
        "硬性要求：\n"
        f"- 必须把全部 {sample_size} 篇论文分成 {target_direction_count} 个方向。\n"
        "- 每篇论文必须且只能出现一次，不能遗漏、不能重复、不能新增不存在的 index。\n"
        "- 方向名必须具体，尽量体现共同研究问题、方法或应用场景。\n"
        "- 方向名优先使用学界常见叫法；如果没有统一叫法，就用并列写法如“持续学习 / 参数高效微调”，不要生造新术语。\n"
        "- 不要使用“其他交叉主题”“其他”“杂项”“长尾”之类兜底标签。\n"
        "- 不要使用过泛标签，如“人工智能”“机器学习”“模型方法”。\n"
        "- 不要把几类关系松散的论文硬拼成一个新名词，例如“学习理论与适配”“结构学习与行业应用”这种标签是禁止的。\n"
        "- label 用中文，控制在 2-12 个字；LLM、VLM、RAG、RL、ASR、OCR 等缩写可保留。\n"
        "- summary 用中文 1 句话，说明该方向今天的论文主要在做什么。\n"
        "- keywords 给 2-4 个短词，不要写成长句。\n"
        "- direction_sentence 用中文 1-2 句，概括这个子域今天最明显的趋势变化。\n\n"
        "请严格输出 JSON 对象，格式如下：\n"
        "{\n"
        '  "direction_sentence": "......",\n'
        '  "directions": [\n'
        '    {\n'
        '      "label": "方向名",\n'
        '      "summary": "一句话摘要",\n'
        '      "keywords": ["词1", "词2", "词3"],\n'
        '      "paper_indexes": [1, 2, 3]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"子域: {subdomain_label}\n"
        f"统计日期: {day.isoformat()}\n"
        f"论文总量: {sample_size}\n"
        f"目标方向数: {target_direction_count}\n\n"
        "[主要分类]\n"
        + ("\n".join(category_lines) if category_lines else "- 无")
        + "\n\n[高频关键词]\n"
        + ("\n".join(keyword_lines) if keyword_lines else "- 无")
        + "\n\n[论文列表]\n"
        + ("\n".join(paper_lines) if paper_lines else "无")
    )


def _build_llm_direction_prompt(
    *,
    day: date,
    sample_size: int,
    categories: list[dict],
    keywords: list[dict],
    direction_rows: list[dict],
    subdomain_label: str,
) -> str:
    category_lines = []
    for item in categories[:6]:
        category_lines.append(
            f"- {item.get('key')}: {item.get('count')} 篇"
        )
    keyword_lines = []
    for item in keywords[:12]:
        keyword_lines.append(
            f"- {item.get('keyword')}: {item.get('count')}"
        )
    cluster_lines = []
    for index, row in enumerate(direction_rows, start=1):
        cluster_key = str(row.get("key") or "").strip()
        cluster_lines.append(
            "\n".join(
                [
                    f"{index}. cluster_key={cluster_key}",
                    f"   provisional_label={row.get('label')}",
                    f"   paper_count={row.get('count')}",
                    f"   share_pct={round(float(row.get('sample_ratio') or 0.0) * 100, 1)}",
                    "   cluster_keywords="
                    + (" / ".join(str((entry or {}).get("keyword") or "").strip() for entry in (row.get("keywords") or []) if str((entry or {}).get("keyword") or "").strip()) or "无"),
                    "   example_titles="
                    + (" || ".join(str(title).strip() for title in (row.get("example_titles") or []) if str(title).strip()) or "无"),
                ]
            )
        )
    return (
        "你是研究趋势编辑。请基于下面已经完成“单论文唯一归属”的 arXiv CS 日趋势聚类结果，"
        "只做两件事：\n"
        "1. 给每个 cluster 重新命名，使方向名更自然、更学术、更像人会写的方向标题。\n"
        "2. 生成一条整体趋势洞察。\n\n"
        "硬性要求：\n"
        "- 绝对不要修改 cluster_key。\n"
        "- 绝对不要修改或重算 paper_count；这些计数已经由系统算好。\n"
        "- 不要合并、拆分或删除 cluster。\n"
        "- label 要短，优先 2-10 个字；常见缩写如 LLM、VLM、RAG、RL、ASR 可以保留。\n"
        "- 允许出现新方向名，不要被固定词表限制。\n"
        "- 避免过泛标签，例如“人工智能”“机器学习”“模型方法”。\n"
        "- 不要输出“其他交叉主题”“杂项”“长尾”等兜底标签，要尽量给出能反映论文共同主题的具体方向名。\n"
        "- direction_sentence 用中文 1-2 句，概括今天的主要热点和一个明显变化，不要空话。\n\n"
        "请严格输出 JSON 对象，格式如下：\n"
        "{\n"
        '  "direction_sentence": "......",\n'
        '  "directions": [\n'
        '    {\n'
        '      "cluster_key": "cluster_key_from_input",\n'
        '      "label": "方向名",\n'
        '      "summary": "一句话概括该方向今天在研究什么",\n'
        '      "keywords": ["关键词1", "关键词2", "关键词3"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"子域: {subdomain_label}\n"
        f"统计日期: {day.isoformat()}\n"
        f"论文总量: {sample_size}\n\n"
        "[主要分类]\n"
        + ("\n".join(category_lines) if category_lines else "- 无")
        + "\n\n[高频关键词]\n"
        + ("\n".join(keyword_lines) if keyword_lines else "- 无")
        + "\n\n[聚类结果]\n"
        + ("\n\n".join(cluster_lines) if cluster_lines else "无")
    )


def _counter_rows(
    counter: Counter[str],
    sample_size: int,
    *,
    labels: dict[str, str] | None = None,
    limit: int,
) -> list[dict]:
    denominator = max(1, sample_size)
    rows = []
    for key, count in counter.most_common(limit):
        rows.append(
            {
                "key": key,
                "label": (labels or {}).get(key, key),
                "label_zh": _CS_CATEGORY_LABELS_ZH.get(key),
                "count": int(count),
                "sample_ratio": round(count / denominator, 4),
            }
        )
    return rows


def _direction_sentence(directions: list[dict], terms: list[dict]) -> str:
    if not directions:
        return "暂无可用趋势"
    first_sentence = str((directions[0] or {}).get("_direction_sentence") or "").strip() if directions else ""
    if first_sentence:
        return first_sentence
    leaders = [
        str(item.get("label") or item.get("key"))
        for item in directions[:3]
    ]
    keywords = [str(item.get("keyword") or item.get("term")) for item in terms[:3]]
    leader_text = "、".join(leaders)
    if keywords:
        return f"{leader_text}是今天投稿里的主要方向，关键词集中在 {' / '.join(keywords)}。"
    return f"{leader_text}占今天投稿前列。"
