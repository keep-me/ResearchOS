from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any
from uuid import UUID

from packages.ai.paper.content_source import normalize_paper_content_source
from packages.ai.paper.document_context import PaperDocumentContext
from packages.ai.paper.mineru_runtime import MinerUOcrRuntime

_EVIDENCE_CACHE_LOCK = threading.Lock()
_PREPARED_EVIDENCE_CACHE: dict[str, PreparedPaperEvidence] = {}
_MAX_PREPARED_EVIDENCE_CACHE_ITEMS = 24


def normalize_paper_evidence_mode(evidence_mode: str | None) -> str:
    normalized = str(evidence_mode or "full").strip().lower()
    if normalized in {"rough", "legacy", "fast", "lite", "coarse", "粗略"}:
        return "rough"
    if normalized in {"full", "complete", "structured", "完整"}:
        return "full"
    return "full"


def _truncate_text(text: str, limit: int) -> str:
    content = str(text or "").strip()
    if limit <= 0:
        return content
    if len(content) <= limit:
        return content
    if limit <= 200:
        return f"{content[: max(0, limit - 4)].rstrip()} ..."
    head_budget = int(limit * 0.72)
    tail_budget = max(80, limit - head_budget - 7)
    head = content[:head_budget].rstrip()
    tail = content[-tail_budget:].lstrip()
    if not tail:
        return head
    return f"{head}\n...\n{tail}"


@dataclass(slots=True)
class PreparedPaperEvidence:
    source: str
    raw_excerpt: str
    document_context: PaperDocumentContext | None = None
    round_context_builder: Callable[[str, int], str] | None = None
    targeted_context_builder: Callable[..., str] | None = None

    def uses_linear_pdf_evidence(self) -> bool:
        return self.document_context is None and "pdf" in str(self.source or "").strip().lower()

    def _render_linear_pdf_context(
        self,
        *,
        name: str,
        max_chars: int,
        notes: list[str] | None = None,
    ) -> str:
        header = [
            f"- 以下是“{name}”的线性正文证据。",
            f"- 来源：{self.source}。",
            "- 说明：这是按 PDF 正文顺序保留的原始摘录，不是结构化章节/图表/表格/公式证据包。",
            "- 请优先依据原文顺序理解内容；不要把未出现的标题、图表或公式槽位当作不存在。",
        ]
        for note in notes or []:
            note_text = str(note or "").strip()
            if note_text:
                header.append(f"- {note_text}")
        content = _truncate_text(self.raw_excerpt, max_chars)
        return "\n".join([*header, "", content]).strip()

    def build_analysis_context(self, *, max_chars: int = 18000) -> str:
        if self.document_context is not None:
            full_mode = int(max_chars) <= 0
            rendered = self.document_context.build_targeted_context(
                name="全文结构化证据包",
                targets=[
                    "overview",
                    "method",
                    "experiment",
                    "results",
                    "ablation",
                    "limitations",
                    "discussion",
                    "figure",
                    "table",
                    "equation",
                ],
                max_chars=max_chars,
                max_sections=0 if full_mode else 10,
                max_figures=0 if full_mode else 6,
                max_tables=0 if full_mode else 6,
                max_equations=0 if full_mode else 5,
                include_outline=True,
            )
            if rendered.strip():
                return rendered
        if self.uses_linear_pdf_evidence():
            return self._render_linear_pdf_context(
                name="全文线性证据",
                max_chars=max_chars,
            )
        return _truncate_text(self.raw_excerpt, max_chars)

    def build_round_context(self, round_name: str, *, max_chars: int) -> str:
        if self.round_context_builder is not None:
            try:
                rendered = str(self.round_context_builder(round_name, max_chars) or "").strip()
            except Exception:
                rendered = ""
            if rendered:
                return rendered
        if self.document_context is not None:
            rendered = self.document_context.build_round_context(round_name, max_chars=max_chars)
            if rendered.strip():
                return rendered
        if self.uses_linear_pdf_evidence():
            return self._render_linear_pdf_context(
                name=f"{round_name or 'analysis'} 论文证据",
                max_chars=max_chars,
            )
        return _truncate_text(self.raw_excerpt, max_chars)

    def build_targeted_context(
        self,
        *,
        name: str,
        targets: list[str],
        max_chars: int,
        max_sections: int = 6,
        max_figures: int = 4,
        max_tables: int = 4,
        max_equations: int = 3,
        include_outline: bool = True,
        notes: list[str] | None = None,
    ) -> str:
        if self.targeted_context_builder is not None:
            try:
                rendered = str(
                    self.targeted_context_builder(
                        name=name,
                        targets=targets,
                        max_chars=max_chars,
                        max_sections=max_sections,
                        max_figures=max_figures,
                        max_tables=max_tables,
                        max_equations=max_equations,
                        include_outline=include_outline,
                        notes=notes,
                    )
                    or ""
                ).strip()
            except Exception:
                rendered = ""
            if rendered:
                return rendered
        if self.document_context is not None:
            rendered = self.document_context.build_targeted_context(
                name=name,
                targets=targets,
                max_chars=max_chars,
                max_sections=max_sections,
                max_figures=max_figures,
                max_tables=max_tables,
                max_equations=max_equations,
                include_outline=include_outline,
                notes=notes,
            )
            if rendered.strip():
                return rendered
        if self.uses_linear_pdf_evidence():
            return self._render_linear_pdf_context(
                name=name,
                max_chars=max_chars,
                notes=notes,
            )
        return _truncate_text(self.raw_excerpt, max_chars)


def _cache_get(cache_key: str) -> PreparedPaperEvidence | None:
    with _EVIDENCE_CACHE_LOCK:
        return _PREPARED_EVIDENCE_CACHE.get(cache_key)


def _cache_put(cache_key: str, payload: PreparedPaperEvidence) -> PreparedPaperEvidence:
    with _EVIDENCE_CACHE_LOCK:
        _PREPARED_EVIDENCE_CACHE[cache_key] = payload
        if len(_PREPARED_EVIDENCE_CACHE) > _MAX_PREPARED_EVIDENCE_CACHE_ITEMS:
            oldest_key = next(iter(_PREPARED_EVIDENCE_CACHE.keys()), None)
            if oldest_key is not None and oldest_key != cache_key:
                _PREPARED_EVIDENCE_CACHE.pop(oldest_key, None)
    return payload


def _pdf_identity(pdf_path: str) -> str:
    try:
        resolved = Path(pdf_path).expanduser().resolve()
        stat = resolved.stat()
        return f"{resolved}|{stat.st_size}|{getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000))}"
    except Exception:
        return str(pdf_path or "").strip()


def load_prepared_paper_evidence(
    *,
    paper_id: UUID,
    pdf_path: str,
    content_source: str,
    evidence_mode: str = "full",
    pdf_extractor: Any,
    pdf_text_pages: int,
    pdf_text_chars: int,
    vision_reader: Any | None = None,
    vision_pages: int = 0,
) -> PreparedPaperEvidence:
    normalized_source = normalize_paper_content_source(content_source)
    normalized_evidence_mode = normalize_paper_evidence_mode(evidence_mode)
    if normalized_source != "pdf":
        ocr_bundle = MinerUOcrRuntime.get_cached_bundle(paper_id, pdf_path)
        if ocr_bundle is not None:
            manifest = getattr(ocr_bundle, "manifest", {}) or {}
            ocr_cache_key = "|".join(
                [
                    "ocr",
                    normalized_evidence_mode,
                    str(paper_id),
                    _pdf_identity(pdf_path),
                    str(getattr(ocr_bundle, "pdf_sha256", "") or "").strip(),
                    str(manifest.get("updated_at") or "").strip() if isinstance(manifest, dict) else "",
                ]
            )
            cached = _cache_get(ocr_cache_key)
            if cached is not None:
                return cached

            document_context = None
            if hasattr(ocr_bundle, "build_document_context"):
                try:
                    document_context = ocr_bundle.build_document_context()
                except Exception:
                    document_context = None

            raw_excerpt = ""
            if hasattr(ocr_bundle, "build_analysis_context"):
                try:
                    raw_excerpt = str(
                        ocr_bundle.build_analysis_context(max_chars=max(pdf_text_chars, 6000)) or ""
                    ).strip()
                except Exception:
                    raw_excerpt = ""

            source = "MinerU OCR Markdown"
            if normalized_evidence_mode != "full":
                return _cache_put(
                    ocr_cache_key,
                    PreparedPaperEvidence(
                        source=source,
                        raw_excerpt=raw_excerpt,
                    ),
                )

            if document_context is None and raw_excerpt:
                document_context = PaperDocumentContext.from_text(
                    raw_excerpt,
                    source="OCR 结构化摘录",
                )

            round_context_builder = None
            if hasattr(ocr_bundle, "build_round_context"):
                round_context_builder = lambda round_name, max_chars: ocr_bundle.build_round_context(  # noqa: E731
                    round_name,
                    max_chars=max_chars,
                )

            targeted_context_builder = None
            if hasattr(ocr_bundle, "build_targeted_context"):
                targeted_context_builder = lambda **kwargs: ocr_bundle.build_targeted_context(**kwargs)  # noqa: E731

            source = getattr(document_context, "source", None) or source
            return _cache_put(
                ocr_cache_key,
                PreparedPaperEvidence(
                    source=source,
                    raw_excerpt=raw_excerpt,
                    document_context=document_context,
                    round_context_builder=round_context_builder,
                    targeted_context_builder=targeted_context_builder,
                ),
            )

    pdf_cache_key = "|".join(
        [
            "pdf",
            normalized_evidence_mode,
            _pdf_identity(pdf_path),
            f"text_pages={int(pdf_text_pages)}",
            f"vision_pages={int(vision_pages)}",
        ]
    )
    cached_pdf = _cache_get(pdf_cache_key)
    if cached_pdf is not None:
        return cached_pdf

    visual_excerpt = ""
    if vision_reader is not None and int(vision_pages) > 0:
        try:
            visual_excerpt = str(
                vision_reader.extract_page_descriptions(
                    pdf_path,
                    max_pages=int(vision_pages),
                )
                or ""
            ).strip()
        except Exception:
            visual_excerpt = ""

    pdf_text = str(
        pdf_extractor.extract_text(
            pdf_path,
            max_pages=int(pdf_text_pages),
        )
        or ""
    ).strip()
    if int(pdf_text_chars) > 0 and len(pdf_text) > int(pdf_text_chars):
        pdf_text = pdf_text[: int(pdf_text_chars)]

    blocks: list[str] = []
    if visual_excerpt:
        blocks.append(f"[页面视觉摘要]\n{visual_excerpt}")
    if pdf_text:
        blocks.append(f"[PDF正文]\n{pdf_text}")
    raw_excerpt = "\n\n".join(blocks).strip()
    source = "PDF 文本 + 页面视觉摘要" if visual_excerpt else "PDF 文本"
    if normalized_evidence_mode != "full":
        return _cache_put(
            pdf_cache_key,
            PreparedPaperEvidence(
                source=source,
                raw_excerpt=raw_excerpt,
            ),
        )
    return _cache_put(
        pdf_cache_key,
        PreparedPaperEvidence(
            source=source,
            raw_excerpt=raw_excerpt,
        ),
    )
