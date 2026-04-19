from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from xml.etree import ElementTree

import httpx

from packages.ai.ops.rate_limiter import acquire_api, record_rate_limit_error
from packages.config import get_settings
from packages.domain.schemas import PaperCreate
from packages.integrations.citation_provider import CitationProvider

ARXIV_API_URL = "https://export.arxiv.org/api/query"
logger = logging.getLogger(__name__)
_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "all",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "need",
    "of",
    "on",
    "or",
    "the",
    "to",
    "via",
    "with",
    "you",
}
_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _build_date_filter(
    days_back: int | None = None,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    if date_from is not None or date_to is not None:
        from packages.timezone import user_date_range_to_utc_bounds

        start_bound, end_bound = user_date_range_to_utc_bounds(date_from, date_to)
        from_date = start_bound or datetime(1970, 1, 1)
        to_date = (end_bound - timedelta(seconds=1)) if end_bound else (datetime.utcnow() + timedelta(days=1))
        return (
            f" AND submittedDate:[{from_date.strftime('%Y%m%d%H%M%S')} "
            f"TO {to_date.strftime('%Y%m%d%H%M%S')}]"
        )

    if not days_back or days_back <= 0:
        return ""

    from_date = datetime.utcnow() - timedelta(days=days_back)
    to_date = datetime.utcnow() + timedelta(days=1)
    return (
        f" AND submittedDate:[{from_date.strftime('%Y%m%d%H%M%S')} "
        f"TO {to_date.strftime('%Y%m%d%H%M%S')}]"
    )


def _build_arxiv_query(
    raw: str,
    days_back: int | None = None,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    """Build an arXiv API query string from user input."""
    raw = raw.strip()
    if not raw:
        return raw

    date_filter = _build_date_filter(days_back, date_from=date_from, date_to=date_to)

    # Keep structured query as-is, only append date filter when absent.
    if re.search(r"\b(all|ti|au|abs|cat|co|jr|rn|id):", raw):
        if "submittedDate:" not in raw and date_filter:
            return raw + date_filter
        return raw

    normalized_phrase = re.sub(r"\s+", " ", raw).strip()
    raw_tokens = re.findall(r"[A-Za-z0-9.+_-]+", raw.lower())
    tokens = [token for token in raw_tokens if len(token) >= 2 and token not in _QUERY_STOPWORDS][:5]

    if tokens:
        return " AND ".join(f"all:{token}" for token in tokens) + date_filter
    return f'all:"{normalized_phrase.replace(chr(34), "")}"' + date_filter


def _normalize_match_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _match_tokens(value: str | None) -> list[str]:
    normalized = _normalize_match_text(value)
    return _TITLE_TOKEN_RE.findall(normalized)


def _title_match_score(query: str, title: str | None) -> float:
    normalized_query = _normalize_match_text(query)
    normalized_title = _normalize_match_text(title)
    if not normalized_query or not normalized_title:
        return 0.0

    score = 0.0
    if normalized_title == normalized_query:
        score += 100.0
    elif normalized_title.startswith(normalized_query):
        score += 60.0
    elif normalized_query in normalized_title:
        score += 45.0

    query_tokens = _match_tokens(normalized_query)
    title_tokens = _match_tokens(normalized_title)
    if query_tokens and title_tokens:
        query_set = set(query_tokens)
        title_set = set(title_tokens)
        overlap = len(query_set & title_set)
        score += (overlap / max(len(query_set), 1)) * 20.0
        ordered_prefix_hits = sum(
            1 for index, token in enumerate(query_tokens[:8]) if index < len(title_tokens) and title_tokens[index] == token
        )
        score += ordered_prefix_hits * 2.0

    score += SequenceMatcher(None, normalized_query, normalized_title).ratio() * 10.0
    return score


class ArxivClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.Client | None = None
        self._citation_provider: CitationProvider | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=60, follow_redirects=True)
        return self._client

    @property
    def citation_provider(self) -> CitationProvider:
        if self._citation_provider is None:
            self._citation_provider = CitationProvider()
        return self._citation_provider

    def fetch_latest(
        self,
        query: str,
        max_results: int = 20,
        sort_by: str = "submittedDate",
        start: int = 0,
        days_back: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        enrich_impact: bool = True,
    ) -> list[PaperCreate]:
        """sort_by: submittedDate / relevance / lastUpdatedDate / impact"""
        if not acquire_api("arxiv", timeout=10.0):
            raise httpx.TimeoutException("ArXiv rate limiter timeout")

        structured_query = _build_arxiv_query(
            query,
            days_back,
            date_from=date_from,
            date_to=date_to,
        )
        fallback_query = _build_arxiv_query(query, None)
        using_date_filter = structured_query != fallback_query
        arxiv_sort = "relevance" if sort_by == "impact" else sort_by

        logger.info(
            "ArXiv search: %s -> %s (sort=%s start=%d days_back=%s date_from=%s date_to=%s)",
            query,
            structured_query,
            sort_by,
            start,
            days_back,
            date_from,
            date_to,
        )

        params = {
            "search_query": structured_query,
            "sortBy": arxiv_sort,
            "sortOrder": "descending",
            "start": start,
            "max_results": max_results,
        }

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.get(ARXIV_API_URL, params=params)
                response.raise_for_status()
                papers = self._parse_atom(response.text)
                if not enrich_impact:
                    return papers
                # Always enrich citation metadata so library impact info is visible
                # even when the fetch sort mode is not "impact".
                return self._enrich_with_impact_metadata(
                    papers,
                    sort_results=(sort_by == "impact"),
                )
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code

                if status == 429:
                    record_rate_limit_error("arxiv")
                    wait = 3 * (attempt + 1)
                    logger.warning("ArXiv 429 rate limited, wait %ds and retry", wait)
                    time.sleep(wait)
                    continue

                if using_date_filter and status >= 500:
                    logger.warning(
                        "ArXiv %s for date-filtered query, retrying without date filter",
                        status,
                    )
                    params["search_query"] = fallback_query
                    using_date_filter = False
                    continue

                raise
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("ArXiv request timeout (attempt %d)", attempt + 1)
                time.sleep(2)
                continue

        raise last_exc or RuntimeError("ArXiv fetch failed")

    def search_candidates(
        self,
        query: str,
        *,
        max_results: int = 20,
        fetch_limit: int | None = None,
    ) -> list[PaperCreate]:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return []

        requested = max(1, min(int(max_results), 50))
        effective_fetch_limit = fetch_limit if fetch_limit is not None else max(requested * 4, 30)
        effective_fetch_limit = max(requested, min(int(effective_fetch_limit), 100))

        papers = self.fetch_latest(
            cleaned_query,
            max_results=effective_fetch_limit,
            sort_by="relevance",
            days_back=None,
            enrich_impact=False,
        )

        ranked = sorted(
            papers,
            key=lambda paper: (
                _title_match_score(cleaned_query, paper.title),
                paper.publication_date or date.min,
                str(paper.arxiv_id or ""),
            ),
            reverse=True,
        )

        deduped: list[PaperCreate] = []
        seen_ids: set[str] = set()
        for paper in ranked:
            key = str(paper.arxiv_id or "").strip().lower()
            if key and key in seen_ids:
                continue
            if key:
                seen_ids.add(key)
            deduped.append(paper)
            if len(deduped) >= requested:
                break
        return deduped

    def _enrich_with_impact_metadata(
        self,
        papers: list[PaperCreate],
        *,
        sort_results: bool,
    ) -> list[PaperCreate]:
        ranked = list(papers)

        def enrich(paper: PaperCreate) -> None:
            metadata = dict(paper.metadata or {})
            try:
                impact = self.citation_provider.fetch_paper_metadata(
                    paper.title,
                    arxiv_id=paper.arxiv_id,
                    allow_fallback=False,
                )
            except Exception as exc:
                logger.warning("Impact metadata lookup failed for %s: %s", paper.arxiv_id, exc)
                impact = None

            if impact:
                metadata["citation_count"] = impact.get("citationCount") or 0
                metadata["influential_citation_count"] = (
                    impact.get("influentialCitationCount") or 0
                )
                metadata["citation_venue"] = impact.get("venue")
                metadata["fields_of_study"] = impact.get("fieldsOfStudy") or []
                metadata["impact_source"] = impact.get("source") or "citation_provider"
            else:
                metadata.setdefault("citation_count", 0)
                metadata.setdefault("influential_citation_count", 0)

            paper.metadata = metadata

        max_workers = min(8, max(1, len(ranked)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(enrich, paper) for paper in ranked]
            for future in as_completed(futures):
                future.result()

        if sort_results:
            ranked.sort(
                key=lambda paper: (
                    int((paper.metadata or {}).get("citation_count") or 0),
                    int((paper.metadata or {}).get("influential_citation_count") or 0),
                    paper.publication_date.toordinal() if paper.publication_date else 0,
                ),
                reverse=True,
            )
        return ranked

    def fetch_by_ids(self, arxiv_ids: list[str]) -> list[PaperCreate]:
        """Batch fetch metadata by arXiv IDs."""
        if not arxiv_ids:
            return []

        clean_ids = [aid.split("v")[0] if "v" in aid else aid for aid in arxiv_ids]
        id_list = ",".join(clean_ids)
        params = {"id_list": id_list, "max_results": len(clean_ids)}

        if not acquire_api("arxiv", timeout=10.0):
            raise httpx.TimeoutException("ArXiv rate limiter timeout")

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.client.get(ARXIV_API_URL, params=params)
                resp.raise_for_status()
                return self._parse_atom(resp.text)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 429:
                    record_rate_limit_error("arxiv")
                    wait = 3 * (attempt + 1)
                    logger.warning("ArXiv 429 rate limited, wait %ds and retry", wait)
                    time.sleep(wait)
                    continue
                raise
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("ArXiv request timeout (attempt %d)", attempt + 1)
                time.sleep(2)
                continue

        raise last_exc or RuntimeError("ArXiv fetch_by_ids failed")

    def download_pdf(self, arxiv_id: str) -> str:
        """Download PDF to local storage."""
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        target = self.settings.pdf_storage_root / f"{arxiv_id}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)

        response = self.client.get(url, timeout=90)
        response.raise_for_status()
        target.write_bytes(response.content)
        return str(target)

    def download_source_archive(self, arxiv_id: str) -> str:
        """Download arXiv source bundle for source-first figure extraction."""
        clean_id = arxiv_id.strip()
        if not clean_id:
            raise ValueError("arxiv_id is required")

        target_dir = self.settings.pdf_storage_root.parent / "source_archives"
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", clean_id).strip("-") or "paper"
        target = target_dir / f"{safe_id}.src"
        if target.exists() and target.stat().st_size > 0:
            return str(target)

        # `/src/<id>` is more stable than `/e-print/<id>` for source bundle downloads
        # and still returns the source tarball / gzip payload we need here.
        url = f"https://arxiv.org/src/{clean_id}"
        response = self.client.get(
            url,
            timeout=45,
            headers={"User-Agent": "ResearchOS/1.0"},
        )
        response.raise_for_status()

        content = response.content
        if not content or content[:64].lower().startswith(b"<html"):
            raise ValueError(f"Unexpected arXiv source payload for {clean_id}")

        target.write_bytes(content)
        return str(target)

    def _parse_atom(self, payload: str) -> list[PaperCreate]:
        root = ElementTree.fromstring(payload)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        papers: list[PaperCreate] = []

        for entry in root.findall("atom:entry", ns):
            id_text = self._text(entry, "atom:id", ns)
            if not id_text:
                continue

            arxiv_id = id_text.rsplit("/", 1)[-1]
            title = self._text(entry, "atom:title", ns).replace("\n", " ").strip()
            summary = self._text(entry, "atom:summary", ns).strip()

            published_raw = self._text(entry, "atom:published", ns)
            published: date | None = None
            if published_raw:
                published = datetime.fromisoformat(published_raw.replace("Z", "+00:00")).date()

            categories: list[str] = []
            for cat_el in entry.findall("atom:category", ns):
                term = cat_el.get("term")
                if term:
                    categories.append(term)

            authors: list[str] = []
            for author_el in entry.findall("atom:author", ns):
                name = self._text(author_el, "atom:name", ns)
                if name:
                    authors.append(name)

            papers.append(
                PaperCreate(
                    arxiv_id=arxiv_id,
                    title=title,
                    abstract=summary,
                    publication_date=published,
                    metadata={
                        "source": "arxiv",
                        "categories": categories,
                        "authors": authors,
                        "primary_category": categories[0] if categories else None,
                    },
                )
            )

        return papers

    @staticmethod
    def _text(entry: ElementTree.Element, path: str, ns: dict[str, str]) -> str:
        node = entry.find(path, ns)
        return node.text if node is not None and node.text else ""
