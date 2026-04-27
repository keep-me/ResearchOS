"""
OpenAlex API 客户端
高速率引用数据源（10 req/s, 100k/day），覆盖 4.7 亿论文
"""
from __future__ import annotations

import difflib
import logging
import re
import time
import unicodedata

import httpx

from packages.integrations.semantic_scholar_client import (
    CitationEdge,
    RichCitationInfo,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.openalex.org"
_MAX_RETRIES = 3
_RETRY_DELAY = 1.0
_DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", flags=re.IGNORECASE)


class OpenAlexClient:
    """OpenAlex REST API 封装，复用 CitationEdge/RichCitationInfo 数据结构"""

    def __init__(self, email: str | None = None) -> None:
        self.email = email
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=_BASE_URL,
                timeout=20,
                follow_redirects=True,
            )
        return self._client

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        params = dict(params or {})
        if self.email:
            params["mailto"] = self.email
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.client.get(path, params=params)
                if resp.status_code == 429:
                    delay = _RETRY_DELAY * (2 ** attempt)
                    logger.warning("OpenAlex 429, retry %d/%d in %.1fs", attempt + 1, _MAX_RETRIES, delay)
                    time.sleep(delay)
                    continue
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
            except httpx.TimeoutException:
                logger.warning("OpenAlex timeout for %s, retry %d", path, attempt + 1)
                time.sleep(_RETRY_DELAY)
            except Exception as exc:
                logger.warning("OpenAlex error for %s: %s", path, exc)
                return None
        logger.error("OpenAlex exhausted retries for %s", path)
        return None

    # ------------------------------------------------------------------
    # 论文查找
    # ------------------------------------------------------------------

    def _resolve_work(self, *, arxiv_id: str | None = None, title: str | None = None) -> dict | None:
        """通过 arXiv ID 或标题找到 OpenAlex Work"""
        if arxiv_id:
            clean = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
            data = self._get(f"/works/https://arxiv.org/abs/{clean}")
            if data and data.get("id"):
                return data

        if title:
            data = self._get("/works", params={
                "filter": f'title.search:"{title[:200]}"',
                "per_page": 1,
                "select": (
                    "id,title,publication_year,cited_by_count,primary_location,"
                    "referenced_works,related_works,ids,locations,open_access,best_oa_location"
                ),
            })
            if data:
                results = data.get("results", [])
                if results:
                    return results[0]
        return None

    def _resolve_work_id(self, *, arxiv_id: str | None = None, title: str | None = None) -> str | None:
        work = self._resolve_work(arxiv_id=arxiv_id, title=title)
        if work:
            return work.get("id")
        return None

    def fetch_work(
        self,
        *,
        work_id: str | None = None,
        arxiv_id: str | None = None,
        title: str | None = None,
    ) -> dict | None:
        if work_id:
            return self._get(self._work_api_path(work_id))
        return self._resolve_work(arxiv_id=arxiv_id, title=title)

    @staticmethod
    def _work_api_path(work_id: str) -> str:
        if work_id.startswith("/works/"):
            return work_id
        if work_id.startswith(_BASE_URL):
            return work_id.replace(_BASE_URL, "")
        if work_id.startswith("https://openalex.org/"):
            suffix = work_id.rsplit("/", 1)[-1]
            return f"/works/{suffix}"
        suffix = work_id.rsplit("/", 1)[-1]
        return f"/works/{suffix}"

    # ------------------------------------------------------------------
    # 引用边（兼容 CitationEdge）
    # ------------------------------------------------------------------

    def fetch_edges_by_title(
        self, title: str, limit: int = 8, *, arxiv_id: str | None = None,
    ) -> list[CitationEdge]:
        work = self._resolve_work(arxiv_id=arxiv_id, title=title)
        if not work:
            return []

        work_id = work.get("id", "")
        edges: list[CitationEdge] = []

        # 参考文献（referenced_works 是 OpenAlex ID 列表）
        ref_ids = (work.get("referenced_works") or [])[:limit]
        if ref_ids:
            ref_works = self._fetch_works_by_ids(ref_ids)
            for rw in ref_works:
                t = (rw.get("title") or "").strip()
                if t:
                    edges.append(CitationEdge(source_title=title, target_title=t, context="reference"))

        # 被引用（cited_by → 用 filter 查询）
        cited_data = self._get("/works", params={
            "filter": f"cites:{work_id}",
            "per_page": min(limit, 50),
            "select": "id,title",
        })
        if cited_data:
            for cw in (cited_data.get("results") or [])[:limit]:
                t = (cw.get("title") or "").strip()
                if t:
                    edges.append(CitationEdge(source_title=t, target_title=title, context="citation"))

        return edges

    # ------------------------------------------------------------------
    # 丰富引用信息（兼容 RichCitationInfo）
    # ------------------------------------------------------------------

    def fetch_rich_citations(
        self,
        title: str,
        ref_limit: int = 30,
        cite_limit: int = 30,
        *,
        arxiv_id: str | None = None,
    ) -> list[RichCitationInfo]:
        work = self._resolve_work(arxiv_id=arxiv_id, title=title)
        if not work:
            return []

        work_id = work.get("id", "")
        results: list[RichCitationInfo] = []

        # 参考文献
        ref_ids = (work.get("referenced_works") or [])[:ref_limit]
        if ref_ids:
            ref_works = self._fetch_works_by_ids(ref_ids, detailed=True)
            for rw in ref_works:
                info = self._work_to_rich_info(rw, direction="reference")
                if info:
                    results.append(info)

        # 被引
        cited_data = self._get("/works", params={
            "filter": f"cites:{work_id}",
            "per_page": min(cite_limit, 50),
            "select": "id,title,publication_year,cited_by_count,primary_location,authorships,abstract_inverted_index",
        })
        if cited_data:
            for cw in (cited_data.get("results") or [])[:cite_limit]:
                info = self._work_to_rich_info(cw, direction="citation")
                if info:
                    results.append(info)

        return results

    # ------------------------------------------------------------------
    # 批量元数据（兼容 fetch_batch_metadata）
    # ------------------------------------------------------------------

    def fetch_batch_metadata(self, titles: list[str], max_papers: int = 10) -> list[dict]:
        results: list[dict] = []
        for title in titles[:max_papers]:
            work = self._resolve_work(title=title)
            if not work:
                continue
            _, src = OpenAlexClient._pick_best_source_location(work)
            venue = ""
            if src:
                venue = src.get("display_name", "")
            results.append({
                "title": (work.get("title") or "").strip(),
                "year": work.get("publication_year"),
                "citationCount": work.get("cited_by_count"),
                "influentialCitationCount": None,
                "venue": venue or None,
                "fieldsOfStudy": [],
                "tldr": None,
            })
        return results

    def fetch_paper_metadata(self, title: str, *, arxiv_id: str | None = None) -> dict | None:
        work = self._resolve_work(arxiv_id=arxiv_id, title=title)
        if not work:
            return None

        work_id = work.get("id", "")
        if not work_id:
            return None

        path = self._work_api_path(work_id)
        detail = self._get(path, params={
            "select": "id,title,publication_year,cited_by_count,primary_location,concepts",
        })
        _, venue_info = OpenAlexClient._pick_best_source_location(detail or work)
        concepts = (detail or work).get("concepts") or []
        fields = [
            (concept.get("display_name") or "").strip()
            for concept in concepts
            if (concept.get("display_name") or "").strip()
        ][:5]

        payload = detail or work
        return {
            "title": (payload.get("title") or title).strip(),
            "year": payload.get("publication_year"),
            "citationCount": payload.get("cited_by_count"),
            "influentialCitationCount": None,
            "venue": venue_info.get("display_name") or None,
            "fieldsOfStudy": fields,
            "tldr": None,
            "source": "openalex",
        }

    def search_works(
        self,
        query: str,
        *,
        max_results: int = 20,
    ) -> list[dict]:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return []

        requested = max(1, min(int(max_results), 100))
        fetch_limit = max(20, min(requested * 3, 100))
        select = (
            "id,title,display_name,publication_year,publication_date,cited_by_count,"
            "primary_location,authorships,abstract_inverted_index,ids,locations,"
            "open_access,best_oa_location"
        )

        merged: dict[str, dict] = {}

        def _merge_work(work: dict | None) -> None:
            if not isinstance(work, dict):
                return
            work_id = str(work.get("id") or "").strip()
            key = work_id or f"title:{str(work.get('title') or work.get('display_name') or '').strip().lower()}"
            if not key:
                return
            merged[key] = work

        doi = OpenAlexClient.extract_doi_from_text(cleaned_query)
        arxiv_id = _extract_arxiv_id_from_url_or_id(cleaned_query)

        if doi:
            for work in self._lookup_works_by_doi(doi):
                _merge_work(work)
        if arxiv_id:
            for work in self._lookup_works_by_arxiv_id(arxiv_id):
                _merge_work(work)
        if (doi or arxiv_id) and not merged:
            return []

        broad = self._get(
            "/works",
            params={
                "search": cleaned_query,
                "per_page": fetch_limit,
                "select": select,
            },
        )
        for work in (broad or {}).get("results") or []:
            _merge_work(work)

        if not doi and not arxiv_id and _looks_like_title_query(cleaned_query):
            quoted_title = cleaned_query.replace('"', "")
            title_data = self._get(
                "/works",
                params={
                    "filter": f'title.search:"{quoted_title[:200]}"',
                    "per_page": min(fetch_limit, 50),
                    "select": select,
                },
            )
            for work in (title_data or {}).get("results") or []:
                _merge_work(work)

        for work in self._recover_published_variants(
            query=cleaned_query,
            merged_works=list(merged.values()),
            doi=doi,
            arxiv_id=arxiv_id,
            select=select,
            per_page=min(fetch_limit, 25),
        ):
            _merge_work(work)

        ranked = sorted(
            merged.values(),
            key=lambda work: OpenAlexClient._search_rank_key(work, cleaned_query),
            reverse=True,
        )

        results: list[dict] = []
        for work in ranked:
            item = self._work_to_search_result(work)
            if item is not None:
                results.append(item)
            if len(results) >= requested:
                break
        return results

    def _lookup_works_by_doi(self, doi: str) -> list[dict]:
        normalized = str(doi or "").strip().lower()
        if not normalized:
            return []
        data = self._get(
            "/works",
            params={
                "filter": f"doi:{normalized}",
                "per_page": 5,
                "select": (
                    "id,title,display_name,publication_year,publication_date,cited_by_count,"
                    "primary_location,authorships,abstract_inverted_index,ids,locations,"
                    "open_access,best_oa_location"
                ),
            },
        )
        return list((data or {}).get("results") or [])

    def _lookup_works_by_arxiv_id(self, arxiv_id: str) -> list[dict]:
        normalized = _extract_arxiv_id_from_url_or_id(arxiv_id)
        if not normalized:
            return []

        def _is_exact_arxiv_work(work: dict, expected_id: str) -> bool:
            extracted_arxiv = OpenAlexClient.extract_arxiv_id(work)
            if extracted_arxiv and extracted_arxiv.lower() == expected_id.lower():
                return True
            doi_value = OpenAlexClient.extract_doi(work)
            return bool(doi_value and doi_value.lower() == f"10.48550/arxiv.{expected_id.lower()}")

        direct = self._get(self._work_api_path(f"https://arxiv.org/abs/{normalized}"))
        merged: list[dict] = []
        seen: set[str] = set()
        if isinstance(direct, dict) and direct.get("id") and _is_exact_arxiv_work(direct, normalized):
            direct_id = str(direct.get("id") or "").strip()
            if direct_id:
                seen.add(direct_id)
            merged.append(direct)
        candidates = [
            f"10.48550/arxiv.{normalized.lower()}",
            f"10.48550/arXiv.{normalized}",
        ]
        for candidate in candidates:
            for work in self._lookup_works_by_doi(candidate):
                if not _is_exact_arxiv_work(work, normalized):
                    continue
                key = str(work.get("id") or "").strip()
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                merged.append(work)
        return merged

    def _recover_published_variants(
        self,
        *,
        query: str,
        merged_works: list[dict],
        doi: str | None,
        arxiv_id: str | None,
        select: str,
        per_page: int,
    ) -> list[dict]:
        seed_titles: list[tuple[str, bool]] = []
        seen_titles: set[tuple[str, bool]] = set()

        def _push_seed_title(value: str | None, *, prefer_published: bool = False) -> None:
            title = str(value or "").strip()
            normalized = _normalize_match_text(title)
            key = (normalized, prefer_published)
            if not normalized or key in seen_titles:
                return
            seen_titles.add(key)
            seed_titles.append((title, prefer_published))

        if not doi and not arxiv_id and _looks_like_title_query(query):
            _push_seed_title(query.replace('"', ""))

        normalized_query = _normalize_match_text(query)
        for work in sorted(
            merged_works,
            key=lambda item: OpenAlexClient._search_rank_key(item, query),
            reverse=True,
        )[:8]:
            if not isinstance(work, dict):
                continue
            work_title = str(work.get("title") or work.get("display_name") or "").strip()
            normalized_title = _normalize_match_text(work_title)
            similarity = difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio()
            has_identifier_match = (
                (doi and OpenAlexClient.extract_doi(work) and OpenAlexClient.extract_doi(work) == doi.lower())
                or (arxiv_id and OpenAlexClient.extract_arxiv_id(work) and OpenAlexClient.extract_arxiv_id(work) == arxiv_id)
            )
            if has_identifier_match or similarity >= 0.92:
                _push_seed_title(work_title, prefer_published=bool(has_identifier_match))

        recovered: list[dict] = []
        seen_ids: set[str] = set()
        for title, prefer_published in seed_titles[:3]:
            title_data = self._get(
                "/works",
                params={
                    "filter": f'title.search:"{title[:200].replace(chr(34), "")}"',
                    "per_page": max(5, min(per_page, 25)),
                    "select": select,
                },
            )
            for work in (title_data or {}).get("results") or []:
                if not OpenAlexClient._looks_like_same_work_family(title, work):
                    continue
                work_id = str(work.get("id") or "").strip()
                if work_id and work_id in seen_ids:
                    continue
                if work_id:
                    seen_ids.add(work_id)
                payload = dict(work)
                if prefer_published:
                    payload["_researchos_family_match"] = "identifier"
                recovered.append(payload)
        return recovered

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _fetch_works_by_ids(self, openalex_ids: list[str], detailed: bool = False) -> list[dict]:
        """批量获取 works（OpenAlex 支持 filter 用 | 分隔多 ID）"""
        if not openalex_ids:
            return []

        # OpenAlex 的 filter 一次最多支持 ~50 个 ID
        all_works: list[dict] = []
        for i in range(0, len(openalex_ids), 50):
            batch = openalex_ids[i:i + 50]
            id_filter = "|".join(batch)
            select = "id,title,publication_year,cited_by_count,primary_location"
            if detailed:
                select += ",authorships,abstract_inverted_index"
            select += ",ids,locations,open_access,best_oa_location"
            data = self._get("/works", params={
                "filter": f"openalex:{id_filter}",
                "per_page": 50,
                "select": select,
            })
            if data:
                all_works.extend(data.get("results") or [])
        return all_works

    @staticmethod
    def _work_to_search_result(work: dict) -> dict | None:
        title = (work.get("title") or work.get("display_name") or "").strip()
        if not title:
            return None

        loc, src = OpenAlexClient._pick_best_source_location(work)
        authors = [
            str((item.get("author") or {}).get("display_name") or "").strip()
            for item in (work.get("authorships") or [])
            if str((item.get("author") or {}).get("display_name") or "").strip()
        ]
        abstract = None
        inv_idx = work.get("abstract_inverted_index")
        if isinstance(inv_idx, dict) and inv_idx:
            abstract = _reconstruct_abstract(inv_idx)

        return {
            "title": title,
            "abstract": abstract or "",
            "publication_year": work.get("publication_year"),
            "publication_date": str(work.get("publication_date") or "").strip() or None,
            "citation_count": work.get("cited_by_count") or 0,
            "venue": str(src.get("display_name") or "").strip() or None,
            "venue_type": str(src.get("type") or "").strip() or None,
            "authors": authors,
            "arxiv_id": OpenAlexClient.extract_arxiv_id(work),
            "openalex_id": str(work.get("id") or "").strip() or None,
            "source_url": OpenAlexClient.extract_source_url(work) or str(work.get("id") or "").strip() or None,
            "pdf_url": OpenAlexClient.extract_pdf_url(work),
            "source": "openalex",
        }

    @staticmethod
    def _pick_best_source_location(work: dict) -> tuple[dict, dict]:
        candidates: list[tuple[int, dict, dict]] = []

        def _push_candidate(loc: dict | None, *, is_primary: bool = False, is_best_oa: bool = False) -> None:
            if not isinstance(loc, dict):
                return
            src = loc.get("source") or {}
            if not isinstance(src, dict):
                src = {}
            display_name = str(src.get("display_name") or "").strip()
            source_type = str(src.get("type") or "").strip().lower()
            if not display_name:
                return
            score = 0
            if source_type in {"conference", "journal"}:
                score += 100
            elif source_type == "repository":
                score -= 100
            if is_best_oa:
                score += 5
            if is_primary:
                score += 3
            candidates.append((score, loc, src))

        _push_candidate(work.get("primary_location"), is_primary=True)
        _push_candidate(work.get("best_oa_location"), is_best_oa=True)
        for loc in work.get("locations") or []:
            _push_candidate(loc)

        if not candidates:
            return {}, {}

        candidates.sort(key=lambda item: item[0], reverse=True)
        _, best_loc, best_src = candidates[0]
        return best_loc, best_src

    @staticmethod
    def _looks_like_same_work_family(seed_title: str, work: dict) -> bool:
        normalized_seed = _normalize_match_text(seed_title)
        work_title = str(work.get("title") or work.get("display_name") or "").strip()
        normalized_title = _normalize_match_text(work_title)
        if not normalized_seed or not normalized_title:
            return False
        if normalized_seed == normalized_title:
            return True
        if normalized_seed in normalized_title or normalized_title in normalized_seed:
            return True
        similarity = difflib.SequenceMatcher(None, normalized_seed, normalized_title).ratio()
        return similarity >= 0.94

    @staticmethod
    def _search_rank_key(work: dict, query: str) -> tuple[float, ...]:
        cleaned_query = str(query or "").strip()
        normalized_query = _normalize_match_text(cleaned_query)
        work_title = str(work.get("title") or work.get("display_name") or "").strip()
        normalized_title = _normalize_match_text(work_title)
        doi_query = OpenAlexClient.extract_doi_from_text(cleaned_query)
        arxiv_query = _extract_arxiv_id_from_url_or_id(cleaned_query)
        work_doi = OpenAlexClient.extract_doi(work)
        work_arxiv = OpenAlexClient.extract_arxiv_id(work)
        _, src = OpenAlexClient._pick_best_source_location(work)
        source_type = str(src.get("type") or "").strip().lower()
        publication_year = int(work.get("publication_year") or 0)
        cited_by = int(work.get("cited_by_count") or 0)

        identifier_score = 0.0
        if doi_query and work_doi and work_doi.lower() == doi_query.lower():
            identifier_score = 1000.0
        elif arxiv_query and work_arxiv and work_arxiv.lower() == arxiv_query.lower():
            identifier_score = 950.0

        title_score = 0.0
        similarity = 0.0
        if normalized_query and normalized_title:
            if normalized_query == normalized_title:
                title_score = 3000.0
                similarity = 1.0
            else:
                similarity = difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio()
                if normalized_title.startswith(normalized_query) or normalized_query.startswith(normalized_title):
                    title_score = 900.0
                elif normalized_query in normalized_title or normalized_title in normalized_query:
                    title_score = 650.0
                else:
                    overlap = _token_overlap_score(normalized_query, normalized_title)
                    title_score = overlap * 220.0

        published_score = 0.0
        if source_type == "conference":
            published_score = 220.0
        elif source_type == "journal":
            published_score = 210.0
        elif source_type == "book-series":
            published_score = 160.0
        elif source_type == "repository":
            published_score = -120.0

        exact_non_repository_bonus = 0.0
        if similarity >= 0.92 and source_type in {"conference", "journal", "book-series"}:
            exact_non_repository_bonus = 120.0
        family_bonus = 0.0
        if work.get("_researchos_family_match") == "identifier" and source_type in {"conference", "journal", "book-series"}:
            family_bonus = 900.0

        return (
            identifier_score + title_score + published_score + exact_non_repository_bonus + family_bonus,
            similarity,
            1.0 if source_type in {"conference", "journal", "book-series"} else 0.0,
            float(cited_by),
            float(publication_year),
        )

    @staticmethod
    def extract_doi(work: dict) -> str | None:
        ids = work.get("ids") or {}
        if isinstance(ids, dict):
            direct = ids.get("doi") or ids.get("DOI")
            extracted = OpenAlexClient.extract_doi_from_text(direct)
            if extracted:
                return extracted
        for url in _iter_candidate_urls(work):
            extracted = OpenAlexClient.extract_doi_from_text(url)
            if extracted:
                return extracted
        return None

    @staticmethod
    def extract_doi_from_text(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        match = _DOI_RE.search(text)
        if not match:
            return None
        return match.group(1).rstrip(" .);,]").lower()

    @staticmethod
    def _work_to_rich_info(work: dict, direction: str) -> RichCitationInfo | None:
        title = (work.get("title") or "").strip()
        if not title:
            return None

        # 提取 arXiv ID
        arxiv_id = OpenAlexClient.extract_arxiv_id(work)

        # 提取摘要（OpenAlex 用倒排索引存储摘要）
        abstract = None
        inv_idx = work.get("abstract_inverted_index")
        if inv_idx and isinstance(inv_idx, dict):
            abstract = _reconstruct_abstract(inv_idx)[:500] if inv_idx else None

        # 提取 venue
        _, src = OpenAlexClient._pick_best_source_location(work)
        venue = None
        if src:
            venue = src.get("display_name")

        return RichCitationInfo(
            scholar_id=work.get("id"),
            title=title,
            year=work.get("publication_year"),
            venue=venue,
            citation_count=work.get("cited_by_count"),
            arxiv_id=arxiv_id,
            abstract=abstract,
            direction=direction,
        )

    @staticmethod
    def extract_arxiv_id(work: dict) -> str | None:
        ids = work.get("ids") or {}
        if isinstance(ids, dict):
            direct = ids.get("arxiv") or ids.get("ArXiv")
            extracted = _extract_arxiv_id_from_url_or_id(direct)
            if extracted:
                return extracted

        for url in _iter_candidate_urls(work):
            extracted = _extract_arxiv_id_from_url_or_id(url)
            if extracted:
                return extracted
        return None

    @staticmethod
    def extract_pdf_url(work: dict) -> str | None:
        for loc_key in ("best_oa_location", "primary_location"):
            loc = work.get(loc_key) or {}
            pdf_url = loc.get("pdf_url")
            if isinstance(pdf_url, str) and pdf_url.strip():
                return pdf_url.strip()

        for loc in work.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            pdf_url = loc.get("pdf_url")
            if isinstance(pdf_url, str) and pdf_url.strip():
                return pdf_url.strip()

        open_access = work.get("open_access") or {}
        oa_url = open_access.get("oa_url")
        if isinstance(oa_url, str) and oa_url.strip():
            return oa_url.strip()
        return None

    @staticmethod
    def extract_source_url(work: dict) -> str | None:
        for loc_key in ("best_oa_location", "primary_location"):
            loc = work.get(loc_key) or {}
            landing_url = loc.get("landing_page_url")
            if isinstance(landing_url, str) and landing_url.strip():
                return landing_url.strip()

        for loc in work.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            landing_url = loc.get("landing_page_url")
            if isinstance(landing_url, str) and landing_url.strip():
                return landing_url.strip()

        ids = work.get("ids") or {}
        if isinstance(ids, dict):
            doi = ids.get("doi")
            if isinstance(doi, str) and doi.strip():
                return doi.strip()
        return None

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __del__(self) -> None:
        self.close()


def _reconstruct_abstract(inverted_index: dict) -> str:
    """从 OpenAlex 的倒排索引重建摘要文本"""
    if not inverted_index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(w for _, w in word_positions)


def _iter_candidate_urls(work: dict) -> list[str]:
    urls: list[str] = []
    ids = work.get("ids") or {}
    if isinstance(ids, dict):
        for value in ids.values():
            if isinstance(value, str) and value.strip():
                urls.append(value.strip())

    open_access = work.get("open_access") or {}
    oa_url = open_access.get("oa_url")
    if isinstance(oa_url, str) and oa_url.strip():
        urls.append(oa_url.strip())

    for loc_key in ("best_oa_location", "primary_location"):
        loc = work.get(loc_key) or {}
        for key in ("landing_page_url", "pdf_url"):
            value = loc.get(key)
            if isinstance(value, str) and value.strip():
                urls.append(value.strip())

    for loc in work.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        for key in ("landing_page_url", "pdf_url"):
            value = loc.get(key)
            if isinstance(value, str) and value.strip():
                urls.append(value.strip())

    return urls


def _extract_arxiv_id_from_url_or_id(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    match = re.search(
        r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9._/-]+?)(?:\.pdf)?(?:[?#].*)?$",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return re.sub(r"v\d+$", "", match.group(1))

    if re.fullmatch(
        r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
        text,
        flags=re.IGNORECASE,
    ):
        return re.sub(r"v\d+$", "", text)
    return None


def _normalize_match_text(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()


def _token_overlap_score(query_text: str, title_text: str) -> float:
    query_tokens = {token for token in query_text.split() if token}
    title_tokens = {token for token in title_text.split() if token}
    if not query_tokens or not title_tokens:
        return 0.0
    overlap = len(query_tokens & title_tokens)
    return overlap / max(len(query_tokens), len(title_tokens), 1)


def _looks_like_title_query(value: str) -> bool:
    cleaned = str(value or "").strip()
    if len(cleaned) < 8:
        return False
    if _extract_arxiv_id_from_url_or_id(cleaned):
        return False
    if OpenAlexClient.extract_doi_from_text(cleaned):
        return False
    token_count = len([token for token in re.split(r"\s+", cleaned) if token])
    return token_count >= 2
