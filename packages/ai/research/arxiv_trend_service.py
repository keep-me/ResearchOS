"""arXiv CS trend snapshot for the dashboard home page."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from xml.etree import ElementTree

import httpx

ARXIV_API_URL = "https://export.arxiv.org/api/query"

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

_DIRECTION_RULES = [
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


class ArxivTrendService:
    def today_snapshot(self, *, sample_limit: int = 1000, fallback_days: int = 3) -> dict:
        """Fetch a lightweight arXiv Computer Science trend snapshot.

        arXiv's public API does not provide a dedicated trends endpoint. We use
        a CS category query (`cat:cs.*`) and walk backward until the latest
        UTC submittedDate day with parsed entries is found. This avoids showing
        "today unavailable" during weekends, holidays, or arXiv API indexing
        gaps.
        """

        limit = max(10, min(int(sample_limit), 2000))
        today_utc = datetime.now(UTC).date()
        last_error = ""

        for offset in range(max(1, fallback_days + 1)):
            target_day = today_utc - timedelta(days=offset)
            try:
                total, papers = self._fetch_day(target_day, limit)
            except Exception as exc:  # pragma: no cover - external dependency
                last_error = str(exc)
                continue
            if papers:
                return self._build_snapshot(target_day, total, papers, offset=offset)

        return {
            "available": False,
            "source": "arxiv_api",
            "scope": "cs",
            "query": "cat:cs.*",
            "message": last_error or f"最近 {fallback_days + 1} 天暂未解析到 arXiv CS 投稿",
            "query_date": today_utc.isoformat(),
            "window_label": f"最近 {fallback_days + 1} 天",
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

    def _fetch_day(self, day: date, sample_limit: int) -> tuple[int, list[dict]]:
        start = day.strftime("%Y%m%d000000")
        end = day.strftime("%Y%m%d235959")
        query = f"cat:cs.* AND submittedDate:[{start} TO {end}]"
        page_size = min(200, sample_limit)
        papers: list[dict] = []
        total = 0
        with httpx.Client(
            timeout=14.0,
            follow_redirects=True,
            headers={"User-Agent": "ResearchOS/1.0 (dashboard trend; contact: local)"},
        ) as client:
            for start_index in range(0, sample_limit, page_size):
                params = {
                    "search_query": query,
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
                        total = int(total_text.strip())
                    except ValueError:
                        total = 0
                page_items = self._parse_entries(root)
                papers.extend(page_items)
                target = min(total or sample_limit, sample_limit)
                if len(papers) >= target or not page_items:
                    break
        return total, papers[:sample_limit]

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

    def _build_snapshot(self, day: date, total: int, papers: list[dict], *, offset: int) -> dict:
        category_counter: Counter[str] = Counter()
        keyword_counter: Counter[str] = Counter()
        keyword_examples: dict[str, str] = {}
        direction_counter: Counter[str] = Counter()
        direction_keywords: dict[str, Counter[str]] = {}
        direction_examples: dict[str, str] = {}

        for paper in papers:
            categories = [
                str(item)
                for item in paper.get("categories") or []
                if str(item).startswith("cs.")
            ]
            if categories:
                category_counter.update(categories[:2])
            phrases = _extract_keywords(
                str(paper.get("title") or ""),
                str(paper.get("abstract") or ""),
            )
            keyword_counter.update(phrases)
            for phrase in phrases:
                keyword_examples.setdefault(phrase, str(paper.get("title") or ""))
            direction_keys = _match_directions(
                str(paper.get("title") or ""),
                str(paper.get("abstract") or ""),
            )
            for direction_key in direction_keys:
                direction_counter[direction_key] += 1
                direction_examples.setdefault(direction_key, str(paper.get("title") or ""))
                direction_keywords.setdefault(direction_key, Counter()).update(phrases)

        categories = _counter_rows(category_counter, len(papers), limit=10)
        keywords = [
            {
                "keyword": keyword,
                "term": keyword,
                "count": count,
                "example_title": keyword_examples.get(keyword, ""),
            }
            for keyword, count in keyword_counter.most_common(14)
        ]
        directions = _direction_rows(
            direction_counter,
            direction_keywords,
            direction_examples,
            len(papers),
            limit=8,
        )
        # Keep legacy names for compatibility with older frontend bundles.
        top_terms = [{"term": item["keyword"], "count": item["count"]} for item in keywords]
        direction = _direction_sentence(directions, keywords)

        return {
            "available": True,
            "source": "arxiv_api",
            "scope": "cs",
            "query": "cat:cs.*",
            "query_date": day.isoformat(),
            "window_label": "UTC 今日" if offset == 0 else f"最近非空发布日 UTC {day.isoformat()}",
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


def _extract_keywords(title: str, abstract: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for text, weight in ((title, 3), (abstract, 1)):
        tokens = _tokenize(text)
        local_phrases: Counter[str] = Counter()
        for size in (2, 3):
            for index in range(0, max(0, len(tokens) - size + 1)):
                phrase_tokens = tokens[index : index + size]
                if len(set(phrase_tokens)) < len(phrase_tokens):
                    continue
                if phrase_tokens[0] in _GENERIC_PHRASE_WORDS or phrase_tokens[-1] in _GENERIC_PHRASE_WORDS:
                    continue
                phrase = " ".join(phrase_tokens)
                if len(phrase) <= 52:
                    local_phrases[phrase] += 1
        for phrase, count in local_phrases.most_common(18):
            counter[phrase] += weight * min(count, 2)
    return counter


def _match_directions(title: str, abstract: str) -> list[str]:
    text = f"{title} {abstract}".lower()
    matches = []
    for rule in _DIRECTION_RULES:
        score = sum(1 for pattern in rule["patterns"] if re.search(pattern, text))
        if score > 0:
            matches.append((str(rule["key"]), score))
    if not matches:
        return []
    return [key for key, _score in sorted(matches, key=lambda item: item[1], reverse=True)[:3]]


def _direction_rows(
    counter: Counter[str],
    keyword_map: dict[str, Counter[str]],
    examples: dict[str, str],
    sample_size: int,
    *,
    limit: int,
) -> list[dict]:
    denominator = max(1, sample_size)
    label_map = {str(rule["key"]): str(rule["label"]) for rule in _DIRECTION_RULES}
    rows = []
    for key, count in counter.most_common(limit):
        keywords = [
            {"keyword": keyword, "count": keyword_count}
            for keyword, keyword_count in keyword_map.get(key, Counter()).most_common(4)
        ]
        rows.append(
            {
                "key": key,
                "label": label_map.get(key, key),
                "count": int(count),
                "sample_ratio": round(count / denominator, 4),
                "keywords": keywords,
                "example_title": examples.get(key, ""),
            }
        )
    return rows


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
    leaders = [
        str(item.get("label") or item.get("key"))
        for item in directions[:3]
    ]
    keywords = [str(item.get("keyword") or item.get("term")) for item in terms[:3]]
    leader_text = "、".join(leaders)
    if keywords:
        return f"{leader_text}是今天投稿里的主要方向，关键词集中在 {' / '.join(keywords)}。"
    return f"{leader_text}占今天投稿前列。"
