from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_ARXIV_ID_RE = re.compile(r"(?P<id>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))(?:v\d+)?$", re.IGNORECASE)
_HEADING_PREFIX_RE = re.compile(r"^(?:section\s+)?(?:\d+(?:\.\d+)*[.)]?\s+)+", re.IGNORECASE)


def _clean_text(value: str | None) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _normalize_heading(value: str | None) -> str:
    cleaned = _clean_text(value).lower()
    cleaned = _HEADING_PREFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", cleaned)
    return " ".join(cleaned.split())


def _truncate_text(value: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


class ExternalPaperPreviewService:
    """Fetch lightweight previews for external arXiv papers without ingesting them."""

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "ResearchOS/1.0 (+https://arxiv.org)",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ExternalPaperPreviewService:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    @staticmethod
    def normalize_arxiv_id(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("arxiv_id 不能为空")
        cleaned = raw.split("?", 1)[0].split("#", 1)[0].strip().rstrip("/")
        for prefix in (
            "https://arxiv.org/abs/",
            "http://arxiv.org/abs/",
            "https://arxiv.org/pdf/",
            "http://arxiv.org/pdf/",
            "https://ar5iv.labs.arxiv.org/html/",
            "http://ar5iv.labs.arxiv.org/html/",
        ):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        cleaned = cleaned.removesuffix(".pdf").strip().rstrip("/")
        match = _ARXIV_ID_RE.search(cleaned)
        if match:
            return match.group("id")
        raise ValueError(f"无法识别 arXiv ID：{raw}")

    def fetch_head(self, arxiv_id: str) -> dict:
        normalized_id = self.normalize_arxiv_id(arxiv_id)
        abs_html = self._get_html(f"https://arxiv.org/abs/{normalized_id}")
        head_payload = self._parse_abs_page(abs_html, normalized_id)

        sections: list[dict] = []
        ar5iv_available = False
        ar5iv_error = ""
        try:
            ar5iv_html = self._get_html(f"https://ar5iv.labs.arxiv.org/html/{normalized_id}")
            sections = self._extract_sections(ar5iv_html)
            ar5iv_available = True
        except Exception as exc:  # pragma: no cover - network variability
            ar5iv_error = str(exc)
            logger.info("External preview ar5iv unavailable for %s: %s", normalized_id, exc)

        return {
            **head_payload,
            "arxiv_id": normalized_id,
            "source_url": f"https://arxiv.org/abs/{normalized_id}",
            "html_url": f"https://ar5iv.labs.arxiv.org/html/{normalized_id}",
            "sections": sections,
            "section_count": len(sections),
            "ar5iv_available": ar5iv_available,
            "ar5iv_error": ar5iv_error,
        }

    def fetch_section(self, arxiv_id: str, section_name: str) -> dict:
        normalized_id = self.normalize_arxiv_id(arxiv_id)
        requested = _clean_text(section_name)
        if not requested:
            raise ValueError("section_name 不能为空")

        html = self._get_html(f"https://ar5iv.labs.arxiv.org/html/{normalized_id}")
        soup = BeautifulSoup(html, "html.parser")
        headings = self._find_section_headings(soup)
        section_catalog = self._extract_sections(html)
        matched = self._match_heading(headings, requested)
        if matched is None:
            available = [item["title"] for item in section_catalog[:12]]
            raise ValueError(
                "未找到匹配章节："
                f"{requested}"
                + (f"。可选章节示例：{', '.join(available)}" if available else "")
            )

        section_tag = matched.find_parent("section")
        if isinstance(section_tag, Tag):
            markdown = self._section_markdown(section_tag)
            child_sections = [
                _clean_text(heading.get_text(" ", strip=True))
                for heading in section_tag.find_all(["h3", "h4"], recursive=True)
                if heading is not matched
            ]
        else:
            markdown = self._collect_section_siblings(matched)
            child_sections = []

        content = markdown.strip()
        if not content:
            raise ValueError(f"章节《{requested}》暂无可提取正文")

        return {
            "arxiv_id": normalized_id,
            "requested_section": requested,
            "matched_section": _clean_text(matched.get_text(" ", strip=True)),
            "matched_anchor": matched.get("id") or "",
            "source_url": f"https://ar5iv.labs.arxiv.org/html/{normalized_id}",
            "markdown": _truncate_text(content, limit=10000),
            "child_sections": child_sections[:12],
            "available_sections": section_catalog[:24],
        }

    def _get_html(self, url: str) -> str:
        response = self._client.get(url)
        response.raise_for_status()
        return response.text

    def _parse_abs_page(self, html: str, arxiv_id: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        title = _clean_text(
            self._meta_content(soup, "citation_title")
            or self._find_text(soup, "h1.title")
            or self._find_text(soup, "h1")
        )
        abstract = _clean_text(
            self._meta_content(soup, "description")
            or self._find_text(soup, "blockquote.abstract")
        )
        if abstract.lower().startswith("abstract:"):
            abstract = abstract.split(":", 1)[1].strip()
        authors = [
            _clean_text(meta.get("content"))
            for meta in soup.find_all("meta", attrs={"name": "citation_author"})
            if _clean_text(meta.get("content"))
        ]

        submission_meta = _clean_text(self._find_text(soup, "div.dateline"))
        comments = ""
        subjects = ""
        for cell in soup.select("td.tablecell.comments, td.comments"):
            comments = _clean_text(cell.get_text(" ", strip=True))
            if comments:
                break
        for cell in soup.select("td.tablecell.subjects, td.subjects"):
            subjects = _clean_text(cell.get_text(" ", strip=True))
            if subjects:
                break
        if not subjects:
            primary_subject = _clean_text(self._meta_content(soup, "citation_keywords"))
            if primary_subject:
                subjects = primary_subject

        return {
            "title": title or f"arXiv:{arxiv_id}",
            "abstract": abstract,
            "authors": authors,
            "submission_info": submission_meta,
            "comments": comments,
            "subjects": subjects,
        }

    @staticmethod
    def _meta_content(soup: BeautifulSoup, name: str) -> str:
        node = soup.find("meta", attrs={"name": name})
        if isinstance(node, Tag):
            return _clean_text(node.get("content"))
        return ""

    @staticmethod
    def _find_text(soup: BeautifulSoup, selector: str) -> str:
        node = soup.select_one(selector)
        return _clean_text(node.get_text(" ", strip=True) if isinstance(node, Tag) else "")

    def _extract_sections(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        headings = self._find_section_headings(soup)
        sections: list[dict] = []
        seen: set[str] = set()
        for heading in headings:
            title = _clean_text(heading.get_text(" ", strip=True))
            normalized = _normalize_heading(title)
            if not title or not normalized or normalized in seen:
                continue
            seen.add(normalized)
            level = int(heading.name[1]) if heading.name and len(heading.name) == 2 else 2
            sections.append(
                {
                    "title": title,
                    "anchor": heading.get("id") or "",
                    "level": level,
                }
            )
        return sections

    @staticmethod
    def _find_section_headings(soup: BeautifulSoup) -> list[Tag]:
        return [
            heading
            for heading in soup.find_all(["h2", "h3", "h4"])
            if isinstance(heading, Tag) and _clean_text(heading.get_text(" ", strip=True))
        ]

    @staticmethod
    def _match_heading(headings: list[Tag], requested: str) -> Tag | None:
        normalized_requested = _normalize_heading(requested)
        if not normalized_requested:
            return None
        best_score = -1
        best_heading: Tag | None = None
        for heading in headings:
            title = _clean_text(heading.get_text(" ", strip=True))
            normalized_title = _normalize_heading(title)
            score = -1
            if normalized_title == normalized_requested:
                score = 100
            elif normalized_requested in normalized_title:
                score = 80 - max(0, len(normalized_title) - len(normalized_requested))
            elif normalized_title in normalized_requested:
                score = 60 - max(0, len(normalized_requested) - len(normalized_title))
            elif normalized_requested.replace(" ", "") == normalized_title.replace(" ", ""):
                score = 90
            if score > best_score:
                best_score = score
                best_heading = heading
        return best_heading if best_score >= 0 else None

    @staticmethod
    def _section_markdown(section_tag: Tag) -> str:
        parts: list[str] = []
        for node in section_tag.find_all(["p", "li", "figcaption"], recursive=True):
            text = _clean_text(node.get_text(" ", strip=True))
            if not text:
                continue
            if node.name == "li":
                parts.append(f"- {text}")
            else:
                parts.append(text)
        return "\n\n".join(parts)

    @staticmethod
    def _collect_section_siblings(heading: Tag) -> str:
        parts: list[str] = []
        current_level = int(heading.name[1]) if heading.name and len(heading.name) == 2 else 2
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"h2", "h3", "h4"}:
                next_level = int(sibling.name[1]) if len(sibling.name) == 2 else current_level
                if next_level <= current_level:
                    break
            if not isinstance(sibling, Tag):
                continue
            for node in sibling.find_all(["p", "li", "figcaption"], recursive=True):
                text = _clean_text(node.get_text(" ", strip=True))
                if not text:
                    continue
                if node.name == "li":
                    parts.append(f"- {text}")
                else:
                    parts.append(text)
        return "\n\n".join(parts)
