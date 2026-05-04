"""
Lightweight paper figure extraction and analysis service.

Strategy:
1. `arxiv_source` mode uses real figure assets from arXiv source packages and, when OCR is already cached, supplements table candidates from MinerU structured outputs.
2. If arXiv figure assets are unavailable but OCR/Markdown has already been generated, `arxiv_source` mode falls back to OCR-structured figure candidates.
3. `mineru` mode only uses MinerU structured outputs or MinerU-generated assets.
4. Never fall back to direct PDF image extraction.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import html
import io
import json
import logging
import math
import re
import tarfile
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image

from packages.ai.paper.mineru_runtime import MinerUOcrRuntime
from packages.integrations.arxiv_client import ArxivClient
from packages.integrations.llm_client import LLMClient, LLMResult
from packages.storage.db import session_scope
from packages.storage.models import ImageAnalysis
from packages.storage.repositories import PromptTraceRepository

logger = logging.getLogger(__name__)

CAPTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("figure", r"(Figure\s*\d+[.:]\s*[^\n]{4,220})"),
    ("figure", r"(Fig\.\s*\d+[.:]\s*[^\n]{4,220})"),
    ("table", r"(Table\s*\d+[.:]\s*[^\n]{4,220})"),
    ("table", r"(Tab\.\s*\d+[.:]\s*[^\n]{4,220})"),
    ("algorithm", r"(Algorithm\s*\d+[.:]\s*[^\n]{4,220})"),
)

VISION_PROMPT = """\
你是一个学术论文图表分析助手。请根据图片内容和已知题注，用简体中文输出结构化分析。

请严格使用以下 Markdown 小标题：
## 图表类型
## 核心内容
## 关键数据
## 方法解读
## 学术意义

要求：
1. 尽量结合图片中的文本、布局、连线、坐标轴、图例和表头。
2. 如果某一项无法确定，请明确写“未提供”或“无法从图中确定”。
3. 不要输出与这些小标题无关的前言或寒暄。

论文页码：第 {page} 页
题注：{caption_hint}
MinerU OCR / 结构化提取补充：{ocr_hint}
"""


@dataclass
class ExtractedFigure:
    page_number: int
    image_index: int
    image_bytes: bytes
    image_type: str
    caption: str
    bbox: dict | None
    source_image_path: str | None = None
    content_markdown: str | None = None
    candidate_source: str | None = None


@dataclass
class FigureAnalysis:
    page_number: int
    image_index: int
    image_type: str
    caption: str
    description: str
    bbox: dict | None
    image_path: str | None = None
    ocr_markdown: str | None = None
    candidate_source: str | None = None


@dataclass
class SourceFigureCandidate:
    caption: str
    image_paths: list[Path]


class FigureService:
    """Figure extraction and vision analysis with lightweight dependencies only."""

    _DESCRIPTION_PAYLOAD_PREFIX = "__researchos_figure_payload__:"

    def __init__(self) -> None:
        self.llm = LLMClient()

    @classmethod
    def _looks_like_ai_analysis_markdown(cls, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        markers = (
            "图表类型",
            "核心内容",
            "关键数据",
            "方法解读",
            "学术意义",
            "chart type",
            "core content",
            "key data",
            "method interpretation",
            "academic significance",
        )
        return any(marker in normalized for marker in markers)

    @classmethod
    def _encode_description_payload(
        cls,
        *,
        ocr_markdown: str | None = None,
        analysis_markdown: str | None = None,
        candidate_source: str | None = None,
    ) -> str:
        payload = {
            "v": 2,
            "ocr_markdown": str(ocr_markdown or "").strip(),
            "analysis_markdown": str(analysis_markdown or "").strip(),
            "candidate_source": str(candidate_source or "").strip(),
        }
        return f"{cls._DESCRIPTION_PAYLOAD_PREFIX}{json.dumps(payload, ensure_ascii=False)}"

    @classmethod
    def _decode_description_payload(cls, raw: str | None) -> dict[str, str]:
        text = str(raw or "").strip()
        if not text:
            return {"ocr_markdown": "", "analysis_markdown": "", "candidate_source": ""}
        if text.startswith(cls._DESCRIPTION_PAYLOAD_PREFIX):
            payload_text = text[len(cls._DESCRIPTION_PAYLOAD_PREFIX) :].strip()
            try:
                payload = json.loads(payload_text)
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                return {
                    "ocr_markdown": cls._clean_markdown_fences(
                        str(payload.get("ocr_markdown") or "")
                    ),
                    "analysis_markdown": cls._clean_markdown_fences(
                        str(payload.get("analysis_markdown") or "")
                    ),
                    "candidate_source": str(payload.get("candidate_source") or "").strip(),
                }
        cleaned = cls._clean_markdown_fences(text)
        if cls._looks_like_ai_analysis_markdown(cleaned):
            return {"ocr_markdown": "", "analysis_markdown": cleaned, "candidate_source": ""}
        return {"ocr_markdown": cleaned, "analysis_markdown": "", "candidate_source": ""}

    @staticmethod
    def _resolve_extract_mode(preferred_mode: str | None = None) -> str:
        from packages.config import get_settings

        source = (
            preferred_mode if preferred_mode is not None else get_settings().figure_extract_mode
        )
        raw = str(source or "arxiv_source").strip().lower()
        if raw in {
            "mineru",
            "magic_pdf",
            "magic-pdf",
            "pdf_direct",
            "pdf",
            "direct",
            "pymupdf",
            "pdfimages",
        }:
            return "mineru"
        return "arxiv_source"

    @staticmethod
    def _normalize_caption_text(text: str | None) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "").strip())
        return normalized[:300]

    @classmethod
    def _normalize_candidate_plain_text(cls, text: str | None) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip()).lower()

    @classmethod
    def _html_fragment_to_text(cls, value: str | None) -> str:
        text = str(value or "")
        if not text:
            return ""
        normalized = re.sub(r"(?is)<br\s*/?>", "\n", text)
        normalized = re.sub(
            r"(?is)</(?:p|div|section|article|blockquote|ul|ol|li|h[1-6])\s*>", "\n", normalized
        )
        normalized = re.sub(r"(?is)<li\b[^>]*>", "- ", normalized)
        normalized = re.sub(r"(?is)<[^>]+>", " ", normalized)
        normalized = html.unescape(normalized)
        lines: list[str] = []
        for raw_line in normalized.replace("\r\n", "\n").split("\n"):
            line = re.sub(r"[ \t]+", " ", raw_line).strip()
            if line:
                lines.append(line)
            elif lines and lines[-1] != "":
                lines.append("")
        return "\n".join(lines).strip()

    @classmethod
    def _html_table_to_markdown(cls, raw_html: str) -> str:
        rows: list[list[str]] = []
        for row_html in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", raw_html):
            cells = [
                cls._html_fragment_to_text(cell_html).replace("|", "\\|")
                for cell_html in re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html)
            ]
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        if not rows:
            return cls._html_fragment_to_text(raw_html)

        width = max(len(row) for row in rows)
        padded_rows = [row + [""] * (width - len(row)) for row in rows]
        header = padded_rows[0]
        separator = ["---"] * width
        lines = [
            f"| {' | '.join(header)} |",
            f"| {' | '.join(separator)} |",
        ]
        for row in padded_rows[1:]:
            lines.append(f"| {' | '.join(row)} |")
        return "\n".join(lines).strip()

    @classmethod
    def _normalize_candidate_markdown(cls, text: str | None) -> str:
        normalized = cls._clean_markdown_fences(str(text or ""))
        if not normalized:
            return ""

        if "<table" in normalized.lower():
            normalized = re.sub(
                r"(?is)<table\b[^>]*>.*?</table>",
                lambda match: f"\n{cls._html_table_to_markdown(match.group(0))}\n",
                normalized,
            )

        normalized = cls._html_fragment_to_text(normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
        return normalized[:4000]

    @classmethod
    def _compose_ocr_candidate_markdown(cls, caption: str | None, content: str | None) -> str:
        normalized_caption = cls._normalize_caption_text(caption)
        normalized_content = cls._normalize_candidate_markdown(content)
        if not normalized_content:
            return ""

        if normalized_caption:
            content_plain = cls._normalize_candidate_plain_text(normalized_content)
            caption_plain = cls._normalize_candidate_plain_text(normalized_caption)
            if content_plain == caption_plain:
                return ""
            if content_plain.startswith(caption_plain):
                return normalized_content[:4000]
            return f"{normalized_caption}\n\n{normalized_content}"[:4000]
        return normalized_content[:4000]

    @classmethod
    def _normalize_stored_candidate_fields(
        cls,
        *,
        caption: str | None,
        ocr_markdown: str | None,
        analysis_markdown: str | None,
        candidate_source: str | None,
    ) -> dict[str, str]:
        normalized_caption = cls._normalize_caption_text(caption)
        normalized_ocr = cls._normalize_candidate_markdown(ocr_markdown)
        normalized_analysis = cls._clean_markdown_fences(str(analysis_markdown or ""))
        normalized_source = str(candidate_source or "").strip()

        if not normalized_analysis and normalized_ocr:
            if cls._normalize_candidate_plain_text(
                normalized_ocr
            ) == cls._normalize_candidate_plain_text(normalized_caption):
                normalized_ocr = ""

        return {
            "ocr_markdown": normalized_ocr,
            "analysis_markdown": normalized_analysis,
            "candidate_source": normalized_source,
        }

    def extract_figures(
        self,
        paper_id: UUID,
        pdf_path: str,
        max_figures: int = 20,
        arxiv_id: str | None = None,
        extract_mode: str | None = None,
    ) -> list[ExtractedFigure]:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        mode = self._resolve_extract_mode(extract_mode)
        if mode == "arxiv_source":
            source_candidates = self._extract_via_arxiv_source(
                pdf_path=pdf_path,
                arxiv_id=arxiv_id,
                max_figures=max_figures,
            )
            source_figures = [
                item
                for item in source_candidates
                if str(item.image_type or "figure").strip().lower() != "table"
            ]
            seen_hashes: set[str] = set()
            for figure in source_figures:
                cls = type(self)
                cls._register_image_hashes(figure.image_bytes, seen_hashes)

            ocr_fallback_figures: list[ExtractedFigure] = []
            if not source_figures:
                ocr_fallback_figures = self._extract_via_mineru_non_tables(
                    paper_id=paper_id,
                    pdf_path=pdf_path,
                    max_figures=max_figures,
                    seen_hashes=seen_hashes,
                    allow_generate=True,
                )

            ocr_tables = self._extract_via_mineru_tables(
                paper_id=paper_id,
                pdf_path=pdf_path,
                max_figures=max_figures,
                seen_hashes=seen_hashes,
                allow_generate=not source_figures,
            )
            combined_figures = source_figures or ocr_fallback_figures
            combined = [*combined_figures, *ocr_tables]
            if combined:
                combined.sort(key=lambda fig: (fig.page_number, fig.image_index, fig.image_type))
                logger.info(
                    "Using %d arXiv figure candidates, %d OCR fallback figure candidates, and %d OCR table candidates for %s",
                    len(source_figures),
                    len(ocr_fallback_figures),
                    len(ocr_tables),
                    pdf_path,
                )
                return self._reindex_figures(combined[:max_figures])
            logger.info(
                "No figure candidates available for %s using mode=arxiv_source",
                pdf_path,
            )
            return []

        seen_hashes: set[str] = set()
        mineru_figures = self._extract_via_mineru(
            paper_id=paper_id,
            pdf_path=pdf_path,
            max_figures=max_figures,
            seen_hashes=seen_hashes,
            allow_generate=False,
        )
        if mineru_figures:
            logger.info("Using %d figure candidates from MinerU", len(mineru_figures))
            mineru_figures.sort(key=lambda fig: (fig.page_number, fig.image_index))
            return mineru_figures[:max_figures]

        logger.info(
            "No figure candidates available for %s using mode=mineru (PDF direct disabled)",
            pdf_path,
        )
        return []

    @classmethod
    def _extract_via_arxiv_source(
        cls,
        pdf_path: str,
        arxiv_id: str | None,
        max_figures: int,
    ) -> list[ExtractedFigure]:
        if not arxiv_id:
            return []

        caption_entries: list[dict] = []
        try:
            import fitz  # type: ignore

            doc = fitz.open(pdf_path)
            caption_entries = cls._pdf_caption_entries(doc)
            doc.close()
        except Exception as exc:
            logger.debug("Failed to read PDF captions for arXiv source matching: %s", exc)

        try:
            archive_path = Path(ArxivClient().download_source_archive(arxiv_id))
        except Exception as exc:
            logger.info("Skip arXiv source extraction for %s: %s", arxiv_id, exc)
            return []

        results: list[ExtractedFigure] = []
        seen_hashes: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="researchos-arxiv-src-") as tmp_dir:
            source_root = Path(tmp_dir)
            if not cls._unpack_source_archive(archive_path, source_root):
                return []

            for candidate in cls._collect_source_candidates(source_root):
                if len(results) >= max_figures:
                    break

                image_variants = [
                    image_bytes
                    for image_bytes in (
                        cls._load_source_image(path) for path in candidate.image_paths
                    )
                    if image_bytes
                ]
                image_bytes = cls._compose_source_images(image_variants)
                if not image_bytes or not cls._register_image_hashes(image_bytes, seen_hashes):
                    continue

                matched = cls._match_pdf_caption(candidate.caption, caption_entries)
                caption = str((matched or {}).get("caption") or candidate.caption).strip()
                page_number = int((matched or {}).get("page_number") or 1)
                image_type = (
                    str((matched or {}).get("type") or cls._infer_type(caption, "")).strip()
                    or "figure"
                )

                results.append(
                    ExtractedFigure(
                        page_number=page_number,
                        image_index=0,
                        image_bytes=image_bytes,
                        image_type=image_type,
                        caption=caption,
                        bbox=None,
                        candidate_source="arxiv_source",
                    )
                )

        return cls._reindex_figures(results)

    @classmethod
    def _extract_via_mineru(
        cls,
        paper_id: UUID,
        pdf_path: str,
        max_figures: int,
        seen_hashes: set[str],
        allow_generate: bool = False,
    ) -> list[ExtractedFigure]:
        bundle = (
            MinerUOcrRuntime.ensure_bundle(paper_id, pdf_path, force=False)
            if allow_generate
            else MinerUOcrRuntime.get_cached_bundle(paper_id, pdf_path)
        )
        if bundle is None:
            return []

        output_root = bundle.output_root
        has_structured_outputs = cls._has_mineru_structured_outputs(output_root)
        structured_figures = cls._extract_via_mineru_structured(
            pdf_path=pdf_path,
            output_root=output_root,
            max_figures=max_figures,
            seen_hashes=seen_hashes,
        )
        if structured_figures:
            logger.info(
                "MinerU structured outputs extracted %d figure candidates from %s",
                len(structured_figures),
                pdf_path,
            )
            return structured_figures[:max_figures]

        if has_structured_outputs:
            logger.info(
                "MinerU produced structured outputs but no usable whole-figure crops; skip fallback because PDF direct extraction is disabled"
            )
            return []

        caption_map = cls._collect_mineru_markdown_captions(output_root)
        results: list[ExtractedFigure] = []
        for image_path in cls._collect_mineru_image_assets(output_root):
            if len(results) >= max_figures:
                break
            image_bytes = cls._normalize_path_to_png_bytes(image_path)
            if not image_bytes or not cls._register_image_hashes(image_bytes, seen_hashes):
                continue
            caption = cls._resolve_mineru_caption(image_path, output_root, caption_map)
            page_number = cls._infer_page_number_from_text(str(image_path.relative_to(output_root)))
            results.append(
                ExtractedFigure(
                    page_number=page_number or 1,
                    image_index=0,
                    image_bytes=image_bytes,
                    image_type=cls._infer_type(caption, image_path.name),
                    caption=caption,
                    bbox=None,
                    candidate_source="mineru_asset",
                )
            )

        if results:
            logger.info("MinerU extracted %d figure candidates from %s", len(results), pdf_path)
        return cls._reindex_figures(results)

    @classmethod
    def _extract_via_mineru_tables(
        cls,
        paper_id: UUID,
        pdf_path: str,
        max_figures: int,
        seen_hashes: set[str],
        allow_generate: bool = False,
    ) -> list[ExtractedFigure]:
        bundle = (
            MinerUOcrRuntime.ensure_bundle(paper_id, pdf_path, force=False)
            if allow_generate
            else MinerUOcrRuntime.get_cached_bundle(paper_id, pdf_path)
        )
        if bundle is None:
            return []

        tables = cls._extract_via_mineru_structured(
            pdf_path=pdf_path,
            output_root=bundle.output_root,
            max_figures=max_figures,
            seen_hashes=seen_hashes,
            include_image_types={"table"},
        )
        if tables:
            logger.info(
                "MinerU OCR supplemented %d table candidates from %s", len(tables), pdf_path
            )
        return tables

    @classmethod
    def _extract_via_mineru_non_tables(
        cls,
        paper_id: UUID,
        pdf_path: str,
        max_figures: int,
        seen_hashes: set[str],
        allow_generate: bool = False,
    ) -> list[ExtractedFigure]:
        bundle = (
            MinerUOcrRuntime.ensure_bundle(paper_id, pdf_path, force=False)
            if allow_generate
            else MinerUOcrRuntime.get_cached_bundle(paper_id, pdf_path)
        )
        if bundle is None:
            return []

        figures = cls._extract_via_mineru_structured(
            pdf_path=pdf_path,
            output_root=bundle.output_root,
            max_figures=max_figures,
            seen_hashes=seen_hashes,
            include_image_types={"figure", "algorithm", "equation"},
        )
        if figures:
            logger.info(
                "MinerU OCR supplied %d fallback figure candidates from %s", len(figures), pdf_path
            )
        return figures

    @classmethod
    def _extract_via_mineru_structured(
        cls,
        pdf_path: str,
        output_root: Path,
        max_figures: int,
        seen_hashes: set[str],
        include_image_types: set[str] | None = None,
    ) -> list[ExtractedFigure]:
        structured_blocks = cls._collect_mineru_structured_blocks(output_root)
        if not structured_blocks:
            return []

        try:
            import fitz  # type: ignore
        except Exception:
            return []

        try:
            doc = fitz.open(pdf_path)
        except Exception as exc:
            logger.info("Failed to open PDF for MinerU structured crop: %s", exc)
            return []

        results: list[ExtractedFigure] = []
        try:
            for block in structured_blocks:
                if len(results) >= max_figures:
                    break
                page_number = int(block.get("page_number") or 0)
                if page_number <= 0 or page_number > len(doc):
                    continue
                bbox = block.get("bbox")
                if not isinstance(bbox, list):
                    continue
                image_type = str(block.get("image_type") or "figure")
                if include_image_types and image_type not in include_image_types:
                    continue
                page = doc.load_page(page_number - 1)
                rect = cls._mineru_bbox_to_page_rect(
                    page,
                    bbox,
                    normalized=bool(block.get("normalized_bbox")),
                )
                if rect is None:
                    continue
                image_bytes = cls._crop_mineru_page_bbox_to_png_bytes(page, rect)
                if not image_bytes or not cls._register_image_hashes(image_bytes, seen_hashes):
                    continue
                caption = str(block.get("caption") or "")[:300]
                results.append(
                    ExtractedFigure(
                        page_number=page_number,
                        image_index=0,
                        image_bytes=image_bytes,
                        image_type=image_type,
                        caption=caption,
                        bbox=cls._rect_to_bbox(rect),
                        content_markdown=str(block.get("content_markdown") or "").strip() or None,
                        candidate_source="mineru_structured",
                    )
                )
        finally:
            doc.close()

        return cls._reindex_figures(results)

    @classmethod
    def _collect_mineru_structured_blocks(cls, output_root: Path) -> list[dict]:
        middle_blocks = cls._collect_mineru_middle_blocks(output_root)
        if middle_blocks:
            return cls._prune_nested_mineru_blocks(middle_blocks)
        return cls._prune_nested_mineru_blocks(cls._collect_mineru_content_list_blocks(output_root))

    @classmethod
    def _has_mineru_structured_outputs(cls, output_root: Path) -> bool:
        return bool(
            cls._collect_mineru_structured_json_files(output_root, "_middle.json")
            or cls._collect_mineru_structured_json_files(output_root, "_content_list.json")
        )

    @classmethod
    def _collect_mineru_middle_blocks(cls, output_root: Path) -> list[dict]:
        results: list[dict] = []
        for middle_path in cls._collect_mineru_structured_json_files(output_root, "_middle.json"):
            try:
                payload = json.loads(middle_path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            pdf_info = payload.get("pdf_info") if isinstance(payload, dict) else None
            if not isinstance(pdf_info, list):
                continue
            for page_info in pdf_info:
                if not isinstance(page_info, dict):
                    continue
                page_number = int(page_info.get("page_idx") or 0) + 1
                for field_name, image_type in (("images", "figure"), ("tables", "table")):
                    blocks = page_info.get(field_name)
                    if not isinstance(blocks, list):
                        continue
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        bbox = cls._normalize_mineru_bbox_list(block.get("bbox"))
                        if bbox is None:
                            continue
                        caption = cls._resolve_mineru_structured_block_caption(block, image_type)
                        results.append(
                            {
                                "page_number": page_number,
                                "bbox": bbox,
                                "normalized_bbox": False,
                                "image_type": image_type,
                                "caption": caption,
                                "content_markdown": cls._compose_ocr_candidate_markdown(
                                    caption,
                                    cls._resolve_mineru_structured_block_content(block, image_type),
                                ),
                            }
                        )
        return results

    @classmethod
    def _collect_mineru_content_list_blocks(cls, output_root: Path) -> list[dict]:
        results: list[dict] = []
        for content_path in cls._collect_mineru_structured_json_files(
            output_root, "_content_list.json"
        ):
            try:
                payload = json.loads(content_path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if not isinstance(payload, list):
                continue
            results.extend(cls._collect_mineru_content_list_item_blocks(payload))
        return results

    @classmethod
    def _collect_mineru_content_list_item_blocks(cls, payload: list[dict]) -> list[dict]:
        results: list[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            raw_type = str(item.get("type") or "").strip().lower()
            if raw_type not in {"image", "table", "chart"}:
                continue
            bbox = cls._normalize_mineru_bbox_list(item.get("bbox"))
            if bbox is None:
                continue
            caption_key = "table_caption" if raw_type == "table" else "image_caption"
            footnote_key = "table_footnote" if raw_type == "table" else "image_footnote"
            caption = cls._join_mineru_caption_parts(item.get(caption_key))
            footnote = cls._join_mineru_caption_parts(item.get(footnote_key))
            results.append(
                {
                    "page_number": int(item.get("page_idx") or 0) + 1,
                    "bbox": bbox,
                    "normalized_bbox": True,
                    "image_type": "table" if raw_type == "table" else "figure",
                    "caption": caption or footnote,
                    "content_markdown": cls._compose_ocr_candidate_markdown(
                        caption or footnote,
                        cls._resolve_mineru_content_list_body(item),
                    ),
                }
            )
        return results

    @classmethod
    def _collect_mineru_content_list_composite_blocks(cls, payload: list[dict]) -> list[dict]:
        entries = [
            entry
            for index, item in enumerate(payload)
            if isinstance(item, dict)
            if (entry := cls._normalize_mineru_content_list_entry(index, item)) is not None
        ]
        if not entries:
            return []

        results: list[dict] = []
        cursor = 0
        while cursor < len(entries):
            entry = entries[cursor]
            if entry.get("type") not in {"image", "chart"} or not isinstance(
                entry.get("bbox"), list
            ):
                cursor += 1
                continue

            run: list[dict] = [entry]
            page_number = int(entry.get("page_number") or 0)
            lookahead = cursor + 1
            while lookahead < len(entries):
                candidate = entries[lookahead]
                if int(candidate.get("page_number") or 0) != page_number:
                    break
                if candidate.get("type") not in {"image", "chart"} or not isinstance(
                    candidate.get("bbox"), list
                ):
                    break
                run.append(candidate)
                lookahead += 1

            composite = cls._build_mineru_content_list_composite_block(run, entries, lookahead)
            if composite is not None:
                results.append(composite)

            cursor = max(lookahead, cursor + 1)

        return results

    @classmethod
    def _build_mineru_content_list_composite_block(
        cls,
        run: list[dict],
        entries: list[dict],
        run_end: int,
    ) -> dict | None:
        if len(run) < 4:
            return None

        bboxes = [bbox for item in run if isinstance((bbox := item.get("bbox")), list)]
        if len(bboxes) < 4:
            return None

        union_bbox = cls._merge_mineru_bboxes(bboxes)
        if union_bbox is None:
            return None

        union_area = cls._mineru_bbox_area(union_bbox)
        if union_area <= 0:
            return None

        child_areas = [cls._mineru_bbox_area(bbox) for bbox in bboxes]
        max_child_area = max(child_areas) if child_areas else 0.0
        filled_ratio = sum(child_areas) / union_area if union_area else 0.0
        widths = [max(0.0, float(bbox[2]) - float(bbox[0])) for bbox in bboxes]
        heights = [max(0.0, float(bbox[3]) - float(bbox[1])) for bbox in bboxes]
        tolerance_x = max(12.0, min(40.0, (sum(widths) / max(len(widths), 1)) * 0.28))
        tolerance_y = max(12.0, min(40.0, (sum(heights) / max(len(heights), 1)) * 0.28))
        column_count = cls._count_mineru_axis_clusters(
            [float(bbox[0]) for bbox in bboxes], tolerance_x
        )
        row_count = cls._count_mineru_axis_clusters(
            [float(bbox[1]) for bbox in bboxes], tolerance_y
        )
        caption_entry = cls._find_mineru_composite_caption_entry(entries, run_end, union_bbox)

        if column_count < 2 or row_count < 2:
            return None
        if max_child_area >= union_area * 0.72:
            return None
        if filled_ratio < 0.22:
            return None
        if caption_entry is None:
            return None

        page_number = int(run[0].get("page_number") or 0)
        nearby_entries = [
            entry
            for entry in entries
            if int(entry.get("page_number") or 0) == page_number
            and entry.get("type") in {"text", "aside_text", "list"}
            and isinstance(entry.get("bbox"), list)
        ]
        expanded_bbox = cls._expand_mineru_composite_bbox(union_bbox, nearby_entries, caption_entry)

        caption = cls._normalize_caption_text(str(caption_entry.get("text") or ""))
        body_parts: list[str] = []
        for item in run:
            raw_item = item.get("item")
            if isinstance(raw_item, dict):
                part = cls._resolve_mineru_content_list_body(raw_item)
                if part:
                    body_parts.append(part)

        for entry in nearby_entries:
            if entry is caption_entry:
                continue
            text = cls._normalize_caption_text(str(entry.get("text") or ""))
            if not text or len(text) > 180 or cls._looks_like_mineru_figure_caption(text):
                continue
            bbox = entry.get("bbox")
            if not isinstance(bbox, list):
                continue
            if not cls._mineru_bbox_contains(expanded_bbox, bbox, tolerance=8.0):
                continue
            body_parts.append(text)

        return {
            "page_number": page_number,
            "bbox": expanded_bbox,
            "normalized_bbox": True,
            "image_type": "figure",
            "caption": caption,
            "content_markdown": cls._compose_ocr_candidate_markdown(
                caption,
                cls._join_unique_text_parts(body_parts),
            ),
        }

    @classmethod
    def _normalize_mineru_content_list_entry(cls, index: int, item: dict) -> dict | None:
        raw_type = str(item.get("type") or "").strip().lower()
        if not raw_type:
            return None

        bbox = cls._normalize_mineru_bbox_list(item.get("bbox"))
        page_number = int(item.get("page_idx") or 0) + 1
        caption_key = "table_caption" if raw_type == "table" else "image_caption"
        caption = cls._join_mineru_caption_parts(item.get(caption_key))
        text_value = ""
        if raw_type in {"text", "aside_text", "list"}:
            text_value = cls._normalize_caption_text(str(item.get("text") or ""))
        elif raw_type in {"image", "table", "chart"}:
            text_value = caption or cls._normalize_caption_text(
                cls._resolve_mineru_content_list_body(item)
            )

        return {
            "index": index,
            "type": raw_type,
            "page_number": page_number,
            "bbox": bbox,
            "caption": caption,
            "text": text_value,
            "item": item,
        }

    @classmethod
    def _find_mineru_composite_caption_entry(
        cls,
        entries: list[dict],
        run_end: int,
        union_bbox: list[float],
    ) -> dict | None:
        max_scan = min(len(entries), run_end + 4)
        for index in range(run_end, max_scan):
            entry = entries[index]
            if entry.get("type") not in {"text", "aside_text", "list"}:
                continue
            text = cls._normalize_caption_text(str(entry.get("text") or ""))
            if not cls._looks_like_mineru_figure_caption(text):
                continue
            bbox = entry.get("bbox")
            if not isinstance(bbox, list):
                return entry
            if cls._mineru_bbox_overlap_ratio(union_bbox, bbox, axis="x") < 0.45:
                continue
            if cls._mineru_bbox_gap(union_bbox, bbox, axis="y") > 140.0:
                continue
            return entry
        return None

    @classmethod
    def _expand_mineru_composite_bbox(
        cls,
        union_bbox: list[float],
        nearby_entries: list[dict],
        caption_entry: dict | None,
    ) -> list[float]:
        expanded = [float(value) for value in union_bbox]
        base_width = max(1.0, expanded[2] - expanded[0])
        base_height = max(1.0, expanded[3] - expanded[1])
        max_vertical_gap = max(28.0, min(80.0, base_height * 0.18))
        max_horizontal_gap = max(18.0, min(60.0, base_width * 0.1))

        for entry in nearby_entries:
            if entry is caption_entry:
                continue
            bbox = entry.get("bbox")
            if not isinstance(bbox, list):
                continue
            text = cls._normalize_caption_text(str(entry.get("text") or ""))
            if text and len(text) > 160:
                continue
            width = max(0.0, float(bbox[2]) - float(bbox[0]))
            height = max(0.0, float(bbox[3]) - float(bbox[1]))
            if height > max(180.0, base_height * 0.45):
                continue
            if (
                cls._mineru_bbox_overlap_ratio(expanded, bbox, axis="x") >= 0.6
                and cls._mineru_bbox_gap(expanded, bbox, axis="y") <= max_vertical_gap
            ):
                expanded = cls._merge_mineru_bboxes([expanded, bbox]) or expanded
                continue
            if (
                width <= max(220.0, base_width * 0.32)
                and cls._mineru_bbox_overlap_ratio(expanded, bbox, axis="y") >= 0.5
                and cls._mineru_bbox_gap(expanded, bbox, axis="x") <= max_horizontal_gap
            ):
                expanded = cls._merge_mineru_bboxes([expanded, bbox]) or expanded
        return expanded

    @staticmethod
    def _count_mineru_axis_clusters(values: list[float], tolerance: float) -> int:
        if not values:
            return 0
        ordered = sorted(float(value) for value in values)
        count = 1
        anchor = ordered[0]
        for value in ordered[1:]:
            if value - anchor > tolerance:
                count += 1
                anchor = value
        return count

    @staticmethod
    def _looks_like_mineru_figure_caption(text: str | None) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        return bool(re.match(r"^(?:figure|fig\.)\s*\d+\s*[:.]", normalized, flags=re.IGNORECASE))

    @staticmethod
    def _merge_mineru_bboxes(bboxes: list[list[float]]) -> list[float] | None:
        valid = [bbox for bbox in bboxes if isinstance(bbox, list) and len(bbox) >= 4]
        if not valid:
            return None
        return [
            min(float(bbox[0]) for bbox in valid),
            min(float(bbox[1]) for bbox in valid),
            max(float(bbox[2]) for bbox in valid),
            max(float(bbox[3]) for bbox in valid),
        ]

    @staticmethod
    def _mineru_bbox_gap(first, second, *, axis: str) -> float:
        if (
            not isinstance(first, list)
            or not isinstance(second, list)
            or len(first) < 4
            or len(second) < 4
        ):
            return math.inf
        if axis == "x":
            return max(
                0.0, max(float(second[0]) - float(first[2]), float(first[0]) - float(second[2]))
            )
        return max(0.0, max(float(second[1]) - float(first[3]), float(first[1]) - float(second[3])))

    @staticmethod
    def _mineru_bbox_overlap_ratio(first, second, *, axis: str) -> float:
        if (
            not isinstance(first, list)
            or not isinstance(second, list)
            or len(first) < 4
            or len(second) < 4
        ):
            return 0.0
        if axis == "x":
            overlap = min(float(first[2]), float(second[2])) - max(
                float(first[0]), float(second[0])
            )
            span = min(float(first[2]) - float(first[0]), float(second[2]) - float(second[0]))
        else:
            overlap = min(float(first[3]), float(second[3])) - max(
                float(first[1]), float(second[1])
            )
            span = min(float(first[3]) - float(first[1]), float(second[3]) - float(second[1]))
        if span <= 0:
            return 0.0
        return max(0.0, overlap) / span

    @classmethod
    def _join_unique_text_parts(cls, parts: list[str]) -> str:
        seen: set[str] = set()
        unique: list[str] = []
        for part in parts:
            normalized = cls._normalize_candidate_plain_text(part)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(str(part).strip())
        return "\n\n".join(unique).strip()[:4000]

    @classmethod
    def _prune_nested_mineru_blocks(cls, blocks: list[dict]) -> list[dict]:
        if len(blocks) <= 1:
            return blocks

        kept: list[dict] = []
        sorted_blocks = sorted(
            blocks,
            key=lambda item: (
                int(item.get("page_number") or 0),
                str(item.get("image_type") or ""),
                -cls._mineru_bbox_area(item.get("bbox")),
                -len(str(item.get("caption") or "").strip()),
            ),
        )
        for block in sorted_blocks:
            bbox = block.get("bbox")
            if not isinstance(bbox, list):
                continue
            should_skip = False
            for existing in kept:
                if int(existing.get("page_number") or 0) != int(block.get("page_number") or 0):
                    continue
                if str(existing.get("image_type") or "") != str(block.get("image_type") or ""):
                    continue
                existing_bbox = existing.get("bbox")
                if not isinstance(existing_bbox, list):
                    continue
                if (
                    cls._mineru_bbox_contains(existing_bbox, bbox)
                    and cls._mineru_bbox_area(existing_bbox) >= cls._mineru_bbox_area(bbox) * 1.18
                ):
                    should_skip = True
                    break
            if not should_skip:
                kept.append(block)
        kept.sort(
            key=lambda item: (
                int(item.get("page_number") or 0),
                -cls._mineru_bbox_area(item.get("bbox")),
            )
        )
        return kept

    @staticmethod
    def _mineru_bbox_area(bbox) -> float:
        if not isinstance(bbox, list) or len(bbox) < 4:
            return 0.0
        try:
            return max(0.0, float(bbox[2]) - float(bbox[0])) * max(
                0.0, float(bbox[3]) - float(bbox[1])
            )
        except Exception:
            return 0.0

    @staticmethod
    def _mineru_bbox_contains(outer, inner, tolerance: float = 4.0) -> bool:
        if (
            not isinstance(outer, list)
            or not isinstance(inner, list)
            or len(outer) < 4
            or len(inner) < 4
        ):
            return False
        try:
            return (
                float(outer[0]) <= float(inner[0]) + tolerance
                and float(outer[1]) <= float(inner[1]) + tolerance
                and float(outer[2]) >= float(inner[2]) - tolerance
                and float(outer[3]) >= float(inner[3]) - tolerance
            )
        except Exception:
            return False

    @staticmethod
    def _collect_mineru_structured_json_files(output_root: Path, suffix: str) -> list[Path]:
        seen: set[str] = set()
        files: list[Path] = []
        for path in sorted(output_root.rglob(f"*{suffix}")):
            key = str(path.resolve()).lower()
            if key not in seen:
                seen.add(key)
                files.append(path)
        fallback_name = suffix.removeprefix("_")
        for path in sorted(output_root.rglob(fallback_name)):
            key = str(path.resolve()).lower()
            if key not in seen:
                seen.add(key)
                files.append(path)
        return files

    @staticmethod
    def _normalize_mineru_bbox_list(raw_bbox) -> list[float] | None:
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) < 4:
            return None
        try:
            x0, y0, x1, y1 = [float(raw_bbox[idx]) for idx in range(4)]
        except Exception:
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        return [x0, y0, x1, y1]

    @classmethod
    def _resolve_mineru_structured_block_caption(cls, block: dict, image_type: str) -> str:
        prefix = "table_" if image_type == "table" else "image_"
        blocks = block.get("blocks")
        if not isinstance(blocks, list):
            return ""
        captions: list[str] = []
        footnotes: list[str] = []
        for sub_block in blocks:
            if not isinstance(sub_block, dict):
                continue
            block_type = str(sub_block.get("type") or "")
            text = cls._extract_mineru_line_text(sub_block)
            if not text:
                continue
            if block_type == f"{prefix}caption":
                captions.append(text)
            elif block_type == f"{prefix}footnote":
                footnotes.append(text)
        caption = " ".join(part for part in captions if part).strip()
        if not caption:
            caption = " ".join(part for part in footnotes if part).strip()
        return cls._normalize_caption_text(caption)

    @classmethod
    def _resolve_mineru_structured_block_content(cls, block: dict, image_type: str) -> str:
        prefix = "table_" if image_type == "table" else "image_"
        blocks = block.get("blocks")
        if not isinstance(blocks, list):
            return ""
        contents: list[str] = []
        for block_type in (f"{prefix}body", f"{prefix}footnote", f"{prefix}caption"):
            for sub_block in blocks:
                if not isinstance(sub_block, dict):
                    continue
                if str(sub_block.get("type") or "") != block_type:
                    continue
                text = cls._extract_mineru_line_text(sub_block)
                if text:
                    contents.append(text)
        return "\n".join(contents).strip()[:4000]

    @staticmethod
    def _extract_mineru_line_text(block: dict) -> str:
        lines = block.get("lines")
        if not isinstance(lines, list):
            return ""
        parts: list[str] = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            spans = line.get("spans")
            if not isinstance(spans, list):
                continue
            for span in spans:
                if not isinstance(span, dict):
                    continue
                content = str(span.get("content") or "").strip()
                if content:
                    parts.append(content)
        return " ".join(parts).strip()[:300]

    @staticmethod
    def _join_mineru_caption_parts(value) -> str:
        if isinstance(value, str):
            return FigureService._normalize_caption_text(value)
        if not isinstance(value, list):
            return ""
        parts = [str(item).strip() for item in value if str(item).strip()]
        return FigureService._normalize_caption_text(" ".join(parts))

    @staticmethod
    def _resolve_mineru_content_list_body(item: dict) -> str:
        parts: list[str] = []
        for key in (
            "table_body",
            "image_body",
            "table_caption",
            "image_caption",
            "table_footnote",
            "image_footnote",
        ):
            value = item.get(key)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    parts.append(text)
            elif isinstance(value, list):
                normalized = [str(entry).strip() for entry in value if str(entry).strip()]
                if normalized:
                    parts.append("\n".join(normalized))
        return "\n".join(parts).strip()[:4000]

    @classmethod
    def _mineru_bbox_to_page_rect(
        cls, page, bbox: list[float], *, normalized: bool
    ) -> object | None:
        try:
            import fitz  # type: ignore
        except Exception:
            return None

        x0, y0, x1, y1 = bbox
        page_rect = page.rect
        if normalized:
            scale_x = float(page_rect.width) / 1000.0
            scale_y = float(page_rect.height) / 1000.0
            x0 = float(page_rect.x0) + x0 * scale_x
            x1 = float(page_rect.x0) + x1 * scale_x
            y0 = float(page_rect.y0) + y0 * scale_y
            y1 = float(page_rect.y0) + y1 * scale_y

        pad_x = max((x1 - x0) * 0.02, 4.0)
        pad_y = max((y1 - y0) * 0.02, 4.0)
        rect = fitz.Rect(x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y) & page_rect
        if rect.is_empty or rect.width < 40 or rect.height < 40:
            return None
        return rect

    @classmethod
    def _crop_mineru_page_bbox_to_png_bytes(cls, page, rect) -> bytes | None:
        try:
            import fitz  # type: ignore
        except Exception:
            return None
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2), clip=rect, alpha=False)
        except Exception:
            return None
        image_bytes = cls._pixmap_to_png_bytes(pix)
        if not image_bytes:
            return None
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                if not cls._passes_image_size_gate(image.width, image.height):
                    return None
        except Exception:
            return None
        return image_bytes

    @staticmethod
    def _collect_mineru_image_assets(output_root: Path) -> list[Path]:
        allowed_suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        blocked_markers = ("_layout", "_mask", "_thumb", "_thumbnail")
        candidates = [
            path
            for path in sorted(output_root.rglob("*"))
            if path.is_file()
            and path.suffix.lower() in allowed_suffixes
            and not any(marker in path.name.lower() for marker in blocked_markers)
        ]
        return candidates

    @classmethod
    def _collect_mineru_markdown_captions(cls, output_root: Path) -> dict[str, str]:
        caption_map: dict[str, str] = {}
        for markdown_path in sorted(output_root.rglob("*.md")):
            try:
                text = markdown_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            lines = text.splitlines()
            for index, line in enumerate(lines):
                matches = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", line)
                for alt_text, raw_ref in matches:
                    caption = cls._resolve_mineru_caption_text(lines, index, alt_text)
                    cls._register_mineru_caption_ref(caption_map, raw_ref, caption)
        return caption_map

    @staticmethod
    def _resolve_mineru_caption_text(lines: list[str], index: int, alt_text: str) -> str:
        alt = str(alt_text or "").strip()
        if alt:
            return alt[:300]
        for offset in (1, 2):
            if index - offset < 0:
                break
            candidate = str(lines[index - offset] or "").strip()
            if not candidate:
                continue
            if candidate.startswith("!"):
                continue
            return candidate[:300]
        for offset in (1, 2):
            if index + offset >= len(lines):
                break
            candidate = str(lines[index + offset] or "").strip()
            if not candidate:
                continue
            if candidate.startswith("!"):
                continue
            return candidate[:300]
        return ""

    @staticmethod
    def _register_mineru_caption_ref(
        caption_map: dict[str, str], raw_ref: str, caption: str
    ) -> None:
        normalized_ref = str(raw_ref or "").strip().strip("\"'")
        if not normalized_ref:
            return
        path_ref = Path(normalized_ref)
        keys = {
            normalized_ref.replace("\\", "/").lower(),
            path_ref.name.lower(),
        }
        for key in keys:
            if key and key not in caption_map:
                caption_map[key] = caption

    @classmethod
    def _resolve_mineru_caption(
        cls,
        image_path: Path,
        output_root: Path,
        caption_map: dict[str, str],
    ) -> str:
        relative_key = str(image_path.relative_to(output_root)).replace("\\", "/").lower()
        caption = caption_map.get(relative_key) or caption_map.get(image_path.name.lower()) or ""
        return caption[:300]

    @staticmethod
    def _infer_page_number_from_text(value: str) -> int | None:
        match = re.search(r"(?:page|p)[_\-\s]?(\d{1,4})", value, re.IGNORECASE)
        if match is not None:
            return int(match.group(1))
        match = re.search(r"[_\-](\d{1,4})[_\-]", value)
        if match is not None:
            return int(match.group(1))
        return None

    @staticmethod
    def _rect_to_bbox(rect) -> dict | None:
        if rect is None:
            return None
        return {
            "x0": round(float(rect.x0), 2),
            "y0": round(float(rect.y0), 2),
            "x1": round(float(rect.x1), 2),
            "y1": round(float(rect.y1), 2),
        }

    @staticmethod
    def _passes_image_size_gate(width: int, height: int) -> bool:
        if width <= 0 or height <= 0:
            return False
        if width * height < 4_096:
            return False
        return max(width, height) >= 80

    @staticmethod
    def _pixmap_to_png_bytes(pix) -> bytes | None:
        try:
            if getattr(pix, "colorspace", None) is not None and getattr(pix, "n", 0) > 4:
                import fitz  # type: ignore

                rgb = fitz.Pixmap(fitz.csRGB, pix)
                try:
                    return rgb.tobytes("png")
                finally:
                    rgb = None
            return pix.tobytes("png")
        except Exception:
            return None

    @staticmethod
    def _normalize_path_to_png_bytes(path: Path) -> bytes | None:
        try:
            with Image.open(path) as image:
                canvas = image.convert("RGB")
                output = io.BytesIO()
                canvas.save(output, format="PNG", optimize=True)
                return output.getvalue()
        except Exception:
            return None

    @classmethod
    def _reindex_figures(cls, figures: list[ExtractedFigure]) -> list[ExtractedFigure]:
        page_counters: dict[int, int] = defaultdict(int)
        normalized: list[ExtractedFigure] = []
        for figure in sorted(figures, key=lambda item: (item.page_number, item.image_index)):
            index = page_counters[figure.page_number]
            page_counters[figure.page_number] += 1
            normalized.append(
                ExtractedFigure(
                    page_number=figure.page_number,
                    image_index=index,
                    image_bytes=figure.image_bytes,
                    image_type=figure.image_type,
                    caption=cls._normalize_caption_text(figure.caption),
                    bbox=figure.bbox,
                    source_image_path=figure.source_image_path,
                    content_markdown=figure.content_markdown,
                    candidate_source=figure.candidate_source,
                )
            )
        return normalized

    @staticmethod
    def _caption_prefix(caption: str) -> str:
        match = re.match(
            r"(Figure\s*\d+|Fig\.\s*\d+|Table\s*\d+|Tab\.\s*\d+|Algorithm\s*\d+)",
            caption or "",
            re.IGNORECASE,
        )
        return match.group(1) if match else ""

    @staticmethod
    def _caption_label(caption: str | None) -> str:
        prefix = FigureService._caption_prefix(caption or "")
        if not prefix:
            return ""
        normalized = re.sub(r"\s+", " ", prefix).strip().lower()
        normalized = normalized.replace("fig.", "figure").replace("tab.", "table")
        return normalized

    @classmethod
    def _pdf_caption_entries(cls, doc) -> list[dict]:
        entries: list[dict] = []
        for page_index in range(len(doc)):
            page_text = doc.load_page(page_index).get_text("text")
            for caption in cls._find_captions(page_text):
                entries.append(
                    {
                        "caption": caption,
                        "label": cls._caption_label(caption),
                        "page_number": page_index + 1,
                        "type": cls._infer_type(caption, page_text),
                        "body": cls._normalize_caption_body(caption),
                    }
                )
        return entries

    @classmethod
    def _find_captions(cls, page_text: str) -> list[str]:
        found: list[str] = []
        for _, pattern in CAPTION_PATTERNS:
            found.extend(re.findall(pattern, page_text, re.IGNORECASE))

        unique: list[str] = []
        seen: set[str] = set()
        for item in found:
            cleaned = str(item or "").strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                unique.append(cleaned)
        return unique

    @staticmethod
    def _strip_tex_comments(content: str) -> str:
        lines: list[str] = []
        for raw in content.splitlines():
            lines.append(re.sub(r"(?<!\\)%.*$", "", raw))
        return "\n".join(lines)

    @staticmethod
    def _extract_braced(text: str, start: int) -> tuple[str, int] | None:
        if start >= len(text) or text[start] != "{":
            return None
        depth = 0
        buffer: list[str] = []
        for idx in range(start, len(text)):
            char = text[idx]
            if char == "{":
                depth += 1
                if depth > 1:
                    buffer.append(char)
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    return "".join(buffer), idx + 1
                buffer.append(char)
                continue
            buffer.append(char)
        return None

    @classmethod
    def _sanitize_latex_caption(cls, text: str) -> str:
        cleaned = re.sub(r"\\label\{[^{}]*\}", " ", text)
        cleaned = re.sub(r"\\ref\{[^{}]*\}", " ", cleaned)
        cleaned = re.sub(r"\\cite\{[^{}]*\}", " ", cleaned)
        cleaned = re.sub(r"\\[a-zA-Z*]+\[[^\]]*\]\{([^{}]*)\}", r"\1", cleaned)
        cleaned = re.sub(r"\\[a-zA-Z*]+\{([^{}]*)\}", r"\1", cleaned)
        cleaned = cleaned.replace("~", " ").replace("$", " ")
        cleaned = cleaned.replace("{", " ").replace("}", " ")
        cleaned = re.sub(r"\\[a-zA-Z*]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @classmethod
    def _normalize_caption_body(cls, caption: str) -> str:
        text = cls._sanitize_latex_caption(caption or "")
        text = re.sub(
            r"^(figure|fig\.|table|tab\.|algorithm)\s*\d+[.:]?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"[^0-9a-zA-Z]+", " ", text).lower()
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _match_pdf_caption(cls, source_caption: str, caption_entries: list[dict]) -> dict | None:
        source_body = cls._normalize_caption_body(source_caption)
        if not source_body:
            return None

        source_type = cls._infer_type(source_caption, "")
        best: dict | None = None
        best_score = 0.0
        for entry in caption_entries:
            body = str(entry.get("body") or "")
            if not body:
                continue
            score = SequenceMatcher(None, source_body, body).ratio()
            if source_body in body or body in source_body:
                score += 0.15
            if entry.get("type") == source_type:
                score += 0.05
            if score > best_score:
                best_score = score
                best = entry
        return best if best_score >= 0.55 else None

    @classmethod
    def _extract_caption_from_tex(cls, env_text: str) -> str:
        match = re.search(r"\\caption(?:\[[^\]]*\])?\s*\{", env_text)
        if not match:
            return ""
        parsed = cls._extract_braced(env_text, match.end() - 1)
        if not parsed:
            return ""
        return cls._sanitize_latex_caption(parsed[0])[:300]

    @staticmethod
    def _extract_includegraphics_refs(env_text: str) -> list[str]:
        refs: list[str] = []
        patterns = [
            r"\\includegraphics\*?(?:\[[^\]]*\])?\s*\{([^{}]+)\}",
            r"\\(?:epsfig|psfig)\s*\{\s*(?:file|figure)\s*=\s*([^\s,}]+)",
            r"\\epsffile\s*\{([^{}]+)\}",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, env_text):
                ref = str(match.group(1) or "").strip()
                if ref:
                    refs.append(ref)
        return refs

    @staticmethod
    def _safe_extract_tar(archive: tarfile.TarFile, target_dir: Path) -> None:
        target_root = target_dir.resolve()
        for member in archive.getmembers():
            member_path = (target_dir / member.name).resolve()
            if not str(member_path).startswith(str(target_root)):
                raise ValueError("Unsafe arXiv source archive path")
        archive.extractall(target_dir)

    @classmethod
    def _unpack_source_archive(cls, archive_path: Path, target_dir: Path) -> bool:
        try:
            with tarfile.open(archive_path, "r:*") as archive:
                cls._safe_extract_tar(archive, target_dir)
                return True
        except tarfile.TarError:
            data = archive_path.read_bytes()

        payloads = [data]
        try:
            payloads.insert(0, gzip.decompress(data))
        except OSError:
            pass

        for payload in payloads:
            try:
                with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
                    cls._safe_extract_tar(archive, target_dir)
                    return True
            except tarfile.TarError:
                pass
            if cls._looks_like_tex_source(payload):
                (target_dir / "main.tex").write_bytes(payload)
                return True
        return False

    @staticmethod
    def _looks_like_tex_source(payload: bytes) -> bool:
        probe = payload[:4096].decode("utf-8", errors="ignore").lower()
        return (
            "\\documentclass" in probe or "\\includegraphics" in probe or "\\begin{figure" in probe
        )

    @staticmethod
    def _load_tex_file(path: Path) -> str:
        for encoding in ("utf-8", "latin-1"):
            try:
                return path.read_text(encoding=encoding, errors="ignore")
            except Exception:
                continue
        return ""

    @classmethod
    def _resolve_graphics_path(cls, tex_dir: Path, ref: str, root: Path) -> Path | None:
        cleaned = ref.strip().strip('"').replace("\\", "/")
        if not cleaned:
            return None

        base = Path(cleaned)
        candidates: list[Path] = []
        if base.suffix:
            candidates.extend([(tex_dir / base).resolve(), (root / base).resolve()])
        else:
            for ext in (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".eps", ".ps"):
                candidates.extend(
                    [
                        (tex_dir / f"{cleaned}{ext}").resolve(),
                        (root / f"{cleaned}{ext}").resolve(),
                    ]
                )

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate

        base_name = Path(cleaned).name
        if not base_name:
            return None
        for ext in ("", ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".eps", ".ps"):
            for candidate in root.rglob(f"{base_name}{ext}"):
                if candidate.is_file():
                    return candidate.resolve()
        return None

    @classmethod
    def _collect_source_candidates(cls, source_root: Path) -> list[SourceFigureCandidate]:
        candidates: list[SourceFigureCandidate] = []
        seen_labels: set[str] = set()
        tex_files = sorted(
            source_root.rglob("*.tex"), key=lambda path: (len(path.parts), str(path))
        )
        for tex_path in tex_files:
            content = cls._strip_tex_comments(cls._load_tex_file(tex_path))
            if not content:
                continue
            for match in re.finditer(
                r"\\begin\{(?:figure|table)\*?\}(.*?)\\end\{(?:figure|table)\*?\}",
                content,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                env_text = match.group(1)
                caption = cls._extract_caption_from_tex(env_text)
                refs = cls._extract_includegraphics_refs(env_text)
                if not caption or not refs:
                    continue

                label = cls._caption_label(caption)
                if label and label in seen_labels:
                    continue

                resolved = []
                for ref in refs:
                    image_path = cls._resolve_graphics_path(tex_path.parent, ref, source_root)
                    if image_path is not None:
                        resolved.append(image_path)
                if not resolved:
                    continue

                if label:
                    seen_labels.add(label)
                candidates.append(SourceFigureCandidate(caption=caption, image_paths=resolved))
        return candidates

    @staticmethod
    def _normalize_source_png(image: Image.Image) -> bytes:
        canvas = image.convert("RGB")
        output = io.BytesIO()
        canvas.save(output, format="PNG", optimize=True)
        return output.getvalue()

    @classmethod
    def _load_source_image(cls, image_path: Path) -> bytes | None:
        suffix = image_path.suffix.lower()
        try:
            if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".eps", ".ps"}:
                with Image.open(image_path) as image:
                    image.load()
                    return cls._normalize_source_png(image)

            import fitz  # type: ignore

            doc = fitz.open(str(image_path))
            try:
                page = doc.load_page(0)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                return pix.tobytes("png")
            finally:
                doc.close()
        except Exception:
            return None

    @classmethod
    def _compose_source_images(cls, images: list[bytes]) -> bytes | None:
        if not images:
            return None
        if len(images) == 1:
            return images[0]

        opened: list[Image.Image] = []
        try:
            for raw in images:
                opened.append(Image.open(io.BytesIO(raw)).convert("RGB"))

            cols = 1 if len(opened) <= 2 else 2
            rows = math.ceil(len(opened) / cols)
            cell_width = min(1200, max(image.width for image in opened))
            cell_height = min(900, max(image.height for image in opened))
            gap = 18
            canvas = Image.new(
                "RGB",
                (
                    cols * cell_width + max(0, cols - 1) * gap,
                    rows * cell_height + max(0, rows - 1) * gap,
                ),
                "white",
            )

            for idx, image in enumerate(opened):
                row = idx // cols
                col = idx % cols
                thumb = image.copy()
                thumb.thumbnail((cell_width, cell_height))
                x = col * (cell_width + gap) + (cell_width - thumb.width) // 2
                y = row * (cell_height + gap) + (cell_height - thumb.height) // 2
                canvas.paste(thumb, (x, y))

            output = io.BytesIO()
            canvas.save(output, format="PNG", optimize=True)
            return output.getvalue()
        finally:
            for image in opened:
                image.close()

    @classmethod
    def _register_image_hashes(cls, image_bytes: bytes, seen_hashes: set[str]) -> bool:
        exact = hashlib.md5(image_bytes).hexdigest()
        if exact in seen_hashes:
            return False
        seen_hashes.add(exact)

        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                gray = image.convert("L").resize((16, 16))
                pixels = list(gray.tobytes())
        except Exception:
            return True

        avg = sum(pixels) / max(len(pixels), 1)
        ahash = "".join("1" if pixel >= avg else "0" for pixel in pixels)
        hash_key = f"ah:{ahash}"
        if hash_key in seen_hashes:
            return False
        seen_hashes.add(hash_key)
        return True

    @staticmethod
    def _infer_type(caption: str, page_text: str) -> str:
        caption_lower = (caption or "").lower()
        if "table" in caption_lower or "tab." in caption_lower:
            return "table"
        if "algorithm" in caption_lower:
            return "algorithm"
        if "figure" in caption_lower or "fig." in caption_lower:
            return "figure"

        page_lower = (page_text or "").lower()
        if "table" in page_lower or "tab." in page_lower:
            return "table"
        if "algorithm" in page_lower:
            return "algorithm"
        return "figure"

    def analyze_figure(self, figure: ExtractedFigure) -> FigureAnalysis:
        b64 = base64.b64encode(figure.image_bytes).decode("utf-8")
        caption_hint = figure.caption.strip() if figure.caption else "未检测到题注"
        ocr_hint = (figure.content_markdown or "").strip()
        if len(ocr_hint) > 1600:
            ocr_hint = f"{ocr_hint[:1600]}..."
        prompt = VISION_PROMPT.format(
            page=figure.page_number,
            caption_hint=caption_hint,
            ocr_hint=ocr_hint or "未提供",
        )

        result: LLMResult = self.llm.vision_analyze(
            image_base64=b64,
            prompt=prompt,
            stage="vision_figure",
            max_tokens=1024,
        )

        try:
            with session_scope() as session:
                cfg = self.llm._config()
                PromptTraceRepository(session).create(
                    stage="vision_figure",
                    provider=self.llm.provider,
                    model=cfg.model_vision or cfg.model_deep,
                    prompt_digest=f"page={figure.page_number} caption={figure.caption[:100]}",
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    input_cost_usd=result.input_cost_usd,
                    output_cost_usd=result.output_cost_usd,
                    total_cost_usd=result.total_cost_usd,
                )
        except Exception:
            pass

        return FigureAnalysis(
            page_number=figure.page_number,
            image_index=figure.image_index,
            image_type=figure.image_type,
            caption=figure.caption,
            description=self._clean_markdown_fences(result.content),
            bbox=figure.bbox,
            candidate_source=figure.candidate_source,
        )

    @staticmethod
    def _clean_markdown_fences(text: str) -> str:
        stripped = str(text or "").strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines).strip()
        return stripped

    def analyze_paper_figures(
        self,
        paper_id: UUID,
        pdf_path: str,
        max_figures: int = 10,
        arxiv_id: str | None = None,
    ) -> list[FigureAnalysis]:
        self.extract_paper_figure_candidates(
            paper_id=paper_id,
            pdf_path=pdf_path,
            max_figures=max_figures,
            arxiv_id=arxiv_id,
        )
        with session_scope() as session:
            from sqlalchemy import select

            rows = list(
                session.execute(
                    select(ImageAnalysis)
                    .where(ImageAnalysis.paper_id == str(paper_id))
                    .order_by(ImageAnalysis.page_number, ImageAnalysis.image_index)
                    .limit(max_figures)
                ).scalars()
            )
            selected_ids = [str(row.id) for row in rows]
        return self.analyze_selected_figures(paper_id, selected_ids)

    def extract_paper_figure_candidates(
        self,
        paper_id: UUID,
        pdf_path: str,
        max_figures: int = 80,
        arxiv_id: str | None = None,
        extract_mode: str | None = None,
    ) -> list[FigureAnalysis]:
        figures = self.extract_figures(
            paper_id,
            pdf_path,
            max_figures=max_figures,
            arxiv_id=arxiv_id,
            extract_mode=extract_mode,
        )
        fig_dir = self._ensure_figure_dir(paper_id)
        analyses = [
            FigureAnalysis(
                page_number=fig.page_number,
                image_index=fig.image_index,
                image_type=fig.image_type,
                caption=fig.caption,
                description="",
                bbox=fig.bbox,
                image_path=str(self._materialize_figure_image(fig, fig_dir)),
                ocr_markdown=(fig.content_markdown or "").strip(),
                candidate_source=fig.candidate_source,
            )
            for fig in figures
        ]
        self._save_analyses(paper_id, analyses)
        logger.info("Saved %d figure candidates for paper %s", len(analyses), paper_id)
        return analyses

    def analyze_selected_figures(
        self,
        paper_id: UUID,
        figure_ids: list[str] | None = None,
    ) -> list[FigureAnalysis]:
        selected = [
            str(figure_id).strip() for figure_id in (figure_ids or []) if str(figure_id).strip()
        ]

        with session_scope() as session:
            from sqlalchemy import select

            query = (
                select(ImageAnalysis)
                .where(ImageAnalysis.paper_id == str(paper_id))
                .order_by(ImageAnalysis.page_number, ImageAnalysis.image_index)
            )
            if selected:
                query = query.where(ImageAnalysis.id.in_(selected))

            rows = list(session.execute(query).scalars())
            if not rows:
                return []

            row_map = {str(row.id): row for row in rows}
            candidates: list[tuple[str, ExtractedFigure]] = []
            for row in rows:
                candidate = self._row_to_extracted_figure(row)
                if candidate is not None:
                    candidates.append((str(row.id), candidate))
            if not candidates:
                return []

            def _analyze_one(
                item: tuple[str, ExtractedFigure],
            ) -> tuple[str, FigureAnalysis] | None:
                figure_id, figure = item
                try:
                    analysis = self.analyze_figure(figure)
                    analysis.image_path = row_map[figure_id].image_path
                    return figure_id, analysis
                except Exception as exc:
                    logger.warning(
                        "Failed to analyze figure %s on page %d: %s",
                        figure_id,
                        figure.page_number,
                        exc,
                    )
                    return None

            results: list[tuple[str, FigureAnalysis]] = []
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {pool.submit(_analyze_one, item): item[0] for item in candidates}
                for future in as_completed(futures):
                    result = future.result()
                    if result is not None:
                        results.append(result)

            results.sort(key=lambda item: (item[1].page_number, item[1].image_index))
            for figure_id, analysis in results:
                row = row_map[figure_id]
                payload = self._decode_description_payload(row.description or "")
                row.image_type = analysis.image_type
                row.caption = analysis.caption
                row.description = self._encode_description_payload(
                    ocr_markdown=payload.get("ocr_markdown"),
                    analysis_markdown=analysis.description,
                    candidate_source=payload.get("candidate_source"),
                )
                row.bbox_json = analysis.bbox
                if analysis.image_path:
                    row.image_path = analysis.image_path
            session.flush()

            return [
                FigureAnalysis(
                    page_number=analysis.page_number,
                    image_index=analysis.image_index,
                    image_type=analysis.image_type,
                    caption=analysis.caption,
                    description=analysis.description,
                    bbox=analysis.bbox,
                    image_path=analysis.image_path,
                    candidate_source=analysis.candidate_source,
                )
                for _, analysis in results
            ]

    @staticmethod
    def _ensure_figure_dir(paper_id: UUID) -> Path:
        from packages.config import get_settings

        base = get_settings().pdf_storage_root.resolve().parent / "figures" / str(paper_id)
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _materialize_figure_image(fig: ExtractedFigure, fig_dir: Path) -> Path:
        img_path = fig_dir / f"p{fig.page_number}_i{fig.image_index}.png"
        img_path.write_bytes(fig.image_bytes)
        return img_path

    @staticmethod
    def _save_analyses(paper_id: UUID, analyses: list[FigureAnalysis]) -> None:
        with session_scope() as session:
            session.execute(
                ImageAnalysis.__table__.delete().where(ImageAnalysis.paper_id == str(paper_id))
            )
            for analysis in analyses:
                session.add(
                    ImageAnalysis(
                        id=str(uuid4()),
                        paper_id=str(paper_id),
                        page_number=analysis.page_number,
                        image_index=analysis.image_index,
                        image_type=analysis.image_type,
                        caption=analysis.caption,
                        description=FigureService._encode_description_payload(
                            ocr_markdown=analysis.ocr_markdown,
                            analysis_markdown=analysis.description,
                            candidate_source=analysis.candidate_source,
                        ),
                        image_path=analysis.image_path,
                        bbox_json=analysis.bbox,
                    )
                )

    @staticmethod
    def resolve_stored_image_path(image_path: str | None) -> Path | None:
        if not image_path:
            return None

        raw = Path(str(image_path)).expanduser()
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            project_root = Path(__file__).resolve().parents[2]
            candidates.append(raw)
            candidates.append(Path.cwd() / raw)
            candidates.append(project_root / raw)

            from packages.config import get_settings

            settings = get_settings()
            pdf_root = settings.pdf_storage_root
            if pdf_root.is_absolute():
                candidates.append(pdf_root.parent.parent / raw)

        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists():
                return candidate.resolve()

        if raw.is_absolute():
            return raw.resolve(strict=False)
        return (Path(__file__).resolve().parents[2] / raw).resolve(strict=False)

    @classmethod
    def _row_to_extracted_figure(cls, row: ImageAnalysis) -> ExtractedFigure | None:
        path = cls.resolve_stored_image_path(row.image_path)
        if path is None or not path.exists():
            logger.warning("Stored figure image missing for %s: %s", row.id, row.image_path)
            return None
        try:
            image_bytes = path.read_bytes()
        except Exception as exc:
            logger.warning("Failed to read stored figure image %s: %s", path, exc)
            return None
        payload = cls._decode_description_payload(row.description or "")
        normalized_payload = cls._normalize_stored_candidate_fields(
            caption=row.caption or "",
            ocr_markdown=payload.get("ocr_markdown"),
            analysis_markdown=payload.get("analysis_markdown"),
            candidate_source=payload.get("candidate_source"),
        )
        return ExtractedFigure(
            page_number=row.page_number,
            image_index=row.image_index,
            image_bytes=image_bytes,
            image_type=row.image_type,
            caption=row.caption or "",
            bbox=row.bbox_json,
            source_image_path=str(path),
            content_markdown=(normalized_payload.get("ocr_markdown") or None),
            candidate_source=(normalized_payload.get("candidate_source") or None),
        )

    @classmethod
    def delete_paper_figure(cls, paper_id: UUID, figure_id: str) -> bool:
        with session_scope() as session:
            from sqlalchemy import select

            row = session.execute(
                select(ImageAnalysis).where(
                    ImageAnalysis.id == str(figure_id),
                    ImageAnalysis.paper_id == str(paper_id),
                )
            ).scalar_one_or_none()
            if row is None:
                return False

            image_path = row.image_path
            session.delete(row)
            session.flush()

            if image_path:
                remaining = session.execute(
                    select(ImageAnalysis.id).where(ImageAnalysis.image_path == image_path).limit(1)
                ).scalar_one_or_none()
                if remaining is None:
                    cls._delete_managed_image(image_path)
            return True

    @classmethod
    def delete_paper_figures(cls, paper_id: UUID, figure_ids: list[str]) -> list[str]:
        normalized_ids = list(
            {str(figure_id).strip() for figure_id in figure_ids if str(figure_id).strip()}
        )
        if not normalized_ids:
            return []

        with session_scope() as session:
            from sqlalchemy import select

            rows = list(
                session.execute(
                    select(ImageAnalysis).where(
                        ImageAnalysis.paper_id == str(paper_id),
                        ImageAnalysis.id.in_(normalized_ids),
                    )
                ).scalars()
            )
            if not rows:
                return []

            deleted_ids: list[str] = []
            image_paths: set[str] = set()
            for row in rows:
                deleted_ids.append(str(row.id))
                if row.image_path:
                    image_paths.add(str(row.image_path))
                session.delete(row)
            session.flush()

            for image_path in image_paths:
                remaining = session.execute(
                    select(ImageAnalysis.id).where(ImageAnalysis.image_path == image_path).limit(1)
                ).scalar_one_or_none()
                if remaining is None:
                    cls._delete_managed_image(image_path)

            return deleted_ids

    @staticmethod
    def _delete_managed_image(image_path: str) -> None:
        path = FigureService.resolve_stored_image_path(image_path)
        if path is None or not path.exists():
            return

        from packages.config import get_settings

        settings = get_settings()
        managed_roots = [
            (settings.pdf_storage_root.resolve().parent / "figures").resolve(),
        ]
        resolved = path.resolve()
        if not any(str(resolved).startswith(str(root)) for root in managed_roots):
            return
        try:
            resolved.unlink(missing_ok=True)
        except Exception:
            pass

    @classmethod
    def get_paper_analyses(cls, paper_id: UUID) -> list[dict]:
        with session_scope() as session:
            from sqlalchemy import select

            query = (
                select(ImageAnalysis)
                .where(ImageAnalysis.paper_id == str(paper_id))
                .order_by(ImageAnalysis.page_number, ImageAnalysis.image_index)
            )
            try:
                rows = list(session.execute(query).scalars().all())
            except Exception as exc:
                logger.warning("Failed to fetch image_analyses for %s: %s", str(paper_id)[:8], exc)
                rows = []

            items: list[dict] = []
            for row in rows:
                image_path = cls.resolve_stored_image_path(row.image_path)
                payload = cls._decode_description_payload(row.description or "")
                normalized_payload = cls._normalize_stored_candidate_fields(
                    caption=row.caption or "",
                    ocr_markdown=payload.get("ocr_markdown"),
                    analysis_markdown=payload.get("analysis_markdown"),
                    candidate_source=payload.get("candidate_source"),
                )
                ocr_markdown = normalized_payload.get("ocr_markdown") or ""
                analysis_markdown = normalized_payload.get("analysis_markdown") or ""
                description = analysis_markdown or ocr_markdown
                items.append(
                    {
                        "id": row.id,
                        "page_number": row.page_number,
                        "image_index": row.image_index,
                        "image_type": row.image_type,
                        "figure_label": cls._caption_prefix(row.caption or "") or None,
                        "caption": row.caption,
                        "description": description,
                        "ocr_markdown": ocr_markdown,
                        "analysis_markdown": analysis_markdown,
                        "candidate_source": normalized_payload.get("candidate_source") or None,
                        "analyzed": bool(analysis_markdown.strip()),
                        "has_image": image_path is not None and image_path.exists(),
                    }
                )
            return items
