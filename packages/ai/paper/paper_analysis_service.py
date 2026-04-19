from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import time
from uuid import UUID

from packages.ai.paper.analysis_options import (
    get_deep_detail_profile,
    resolve_paper_analysis_levels,
)
from packages.ai.paper.content_source import (
    paper_content_source_label,
    resolve_effective_paper_content_source,
)
from packages.ai.paper.paper_evidence import (
    load_prepared_paper_evidence,
    normalize_paper_evidence_mode,
)
from packages.ai.paper.pdf_parser import PdfTextExtractor
from packages.integrations.llm_client import LLMClient, LLMResult
from packages.storage.db import session_scope
from packages.storage.models import AnalysisReport
from packages.storage.repositories import PaperRepository

ProgressCallback = Callable[[str, int, int], None]


class PaperAnalysisService:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.extractor = PdfTextExtractor()

    def analyze(
        self,
        paper_id: UUID,
        *,
        detail_level: str = "medium",
        reasoning_level: str = "default",
        content_source: str = "auto",
        evidence_mode: str = "full",
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        normalized_detail, normalized_reasoning = resolve_paper_analysis_levels(
            detail_level,
            reasoning_level,
        )
        normalized_evidence_mode = normalize_paper_evidence_mode(evidence_mode)
        profile = get_deep_detail_profile(normalized_detail)
        profile_text_pages = int(profile["text_pages"])
        profile_text_chars = int(profile["text_chars"])
        if normalized_evidence_mode == "full":
            profile_text_pages = 0
            profile_text_chars = 28000 if normalized_detail == "high" else 22000 if normalized_detail == "medium" else 14000
        else:
            profile_text_pages = max(2, min(profile_text_pages, 6))
            profile_text_chars = max(2600, min(profile_text_chars, 5200))

        def _report(message: str, current: int) -> None:
            if progress_callback:
                progress_callback(message, current, 100)

        with session_scope() as session:
            from sqlalchemy import select

            repo = PaperRepository(session)
            paper = repo.get_by_id(paper_id)
            title = paper.title
            abstract = paper.abstract
            pdf_path = paper.pdf_path
            metadata = dict(paper.metadata_json or {})
            existing = session.execute(
                select(AnalysisReport).where(AnalysisReport.paper_id == str(paper_id))
            ).scalar_one_or_none()
            skim_report = existing.summary_md if existing and existing.summary_md else metadata.get("skim_report")
            deep_report = existing.deep_dive_md if existing and existing.deep_dive_md else metadata.get("deep_report")
            reasoning_chain = metadata.get("reasoning_chain")

        _report("正在提取论文文本...", 10)
        evidence = None
        effective_content_source = resolve_effective_paper_content_source(content_source, None)
        effective_source_detail = ""
        if pdf_path:
            evidence = load_prepared_paper_evidence(
                paper_id=paper_id,
                pdf_path=pdf_path,
                content_source=content_source,
                evidence_mode=normalized_evidence_mode,
                pdf_extractor=self.extractor,
                pdf_text_pages=profile_text_pages,
                pdf_text_chars=0 if normalized_evidence_mode == "full" else profile_text_chars * 2,
            )
            effective_content_source = resolve_effective_paper_content_source(
                content_source,
                evidence.source,
            )
            effective_source_detail = evidence.source

        _report(
            f"三轮分析证据已就绪（来源: {paper_content_source_label(effective_content_source)}，证据: {'完整' if normalized_evidence_mode == 'full' else '粗略'}）...",
            16,
        )

        context_blocks = [
            f"标题: {title}",
            f"摘要: {abstract or '暂无摘要'}",
            f"详略等级: {normalized_detail}",
            f"推理等级: {normalized_reasoning}",
            f"证据模式: {'完整' if normalized_evidence_mode == 'full' else '粗略'}",
            f"分析来源: {paper_content_source_label(effective_content_source)}",
        ]
        if skim_report:
            skim_text = str(skim_report)
            context_blocks.append(
                f"已有粗读: {skim_text if normalized_evidence_mode == 'full' else skim_text[:1200]}"
            )
        if deep_report:
            deep_text = str(deep_report)
            context_blocks.append(
                f"已有精读: {deep_text if normalized_evidence_mode == 'full' else deep_text[:1800]}"
            )
        if reasoning_chain:
            reasoning_text = str(reasoning_chain)
            context_blocks.append(
                f"已有推理链: {reasoning_text if normalized_evidence_mode == 'full' else reasoning_text[:1800]}"
            )
        evidence_notice = (
            "证据说明：下面的结构化证据包是按全文结构跨章节选择的内容，"
            "不代表论文只截到某一节。除非证据明确显示缺页/截断，否则不要写“正文仅覆盖到 Sec.x.x”。"
            " 只有当某个具体判断确实没有直接证据时，才写“当前证据不足”，不要据此臆断全文不存在该内容。"
        )
        context_text = "\n\n".join(context_blocks)
        if evidence is None:
            overview_evidence = "未提取到 PDF 正文"
            comprehension_evidence = "未提取到 PDF 正文"
            deep_analysis_evidence = "未提取到 PDF 正文"
        elif normalized_evidence_mode == "full":
            overview_evidence = evidence.build_round_context(
                "overview",
                max_chars=0,
            )
            comprehension_evidence = evidence.build_round_context(
                "comprehension",
                max_chars=0,
            )
            deep_analysis_evidence = evidence.build_round_context(
                "deep_analysis",
                max_chars=0,
            )
        else:
            rough_evidence = evidence.build_analysis_context(max_chars=max(3200, profile_text_chars))
            overview_evidence = rough_evidence[: max(2200, min(len(rough_evidence), 3200))]
            comprehension_evidence = rough_evidence[: max(3000, min(len(rough_evidence), 4200))]
            deep_analysis_evidence = rough_evidence[: max(3600, min(len(rough_evidence), 5200))]

        rounds = {}

        _report("第 1 轮：鸟瞰扫描...", 28)
        round_1 = self._run_round(
            stage="paper_round_1_overview",
            prompt=(
                "请对下面论文做第 1 轮鸟瞰扫描，输出中文 Markdown。\n"
                "必须覆盖：论文结构、核心页面/章节、元数据、图表与公式分布、最重要的 3 个观察。\n\n"
                f"{evidence_notice}\n\n"
                f"{context_text}\n\n"
                f"[第 1 轮结构化证据包]\n{overview_evidence}"
            ),
            reasoning_level=normalized_reasoning,
            max_tokens=min(int(profile["max_tokens"]), 1800),
        )
        rounds["round_1"] = {
            "title": "第 1 轮：鸟瞰扫描",
            "markdown": self._normalize_round_markdown(
                round_1,
                title,
                "鸟瞰扫描",
                stage="paper_round_1_overview",
            ),
            "updated_at": self._iso_now(),
        }

        _report("第 2 轮：内容理解...", 52)
        round_2 = self._run_round(
            stage="paper_round_2_comprehension",
            prompt=(
                "请对下面论文做第 2 轮内容理解，输出中文 Markdown。\n"
                "必须覆盖：问题定义、方法要点、实验设置、主要结果、关键图表或表格的文字化复现。\n\n"
                f"{evidence_notice}\n\n"
                f"{context_text}\n\n"
                f"[第 2 轮结构化证据包]\n{comprehension_evidence}\n\n"
                f"[第 1 轮结果]\n{rounds['round_1']['markdown']}"
            ),
            reasoning_level=normalized_reasoning,
            max_tokens=int(profile["max_tokens"]),
        )
        rounds["round_2"] = {
            "title": "第 2 轮：内容理解",
            "markdown": self._normalize_round_markdown(
                round_2,
                title,
                "内容理解",
                stage="paper_round_2_comprehension",
            ),
            "updated_at": self._iso_now(),
        }

        _report("第 3 轮：深度分析...", 74)
        round_3 = self._run_round(
            stage="paper_round_3_deep_analysis",
            prompt=(
                "请对下面论文做第 3 轮深度分析，输出中文 Markdown。\n"
                "必须覆盖：数学框架、算法流程、实现要点、局限性、复现实验建议。\n"
                "如果适合，请给出 Mermaid 流程图和 KaTeX 公式片段。\n\n"
                f"{evidence_notice}\n\n"
                f"{context_text}\n\n"
                f"[第 3 轮结构化证据包]\n{deep_analysis_evidence}\n\n"
                f"[第 1 轮结果]\n{rounds['round_1']['markdown']}\n\n"
                f"[第 2 轮结果]\n{rounds['round_2']['markdown']}"
            ),
            reasoning_level=normalized_reasoning,
            max_tokens=max(int(profile["max_tokens"]), 2600),
        )
        rounds["round_3"] = {
            "title": "第 3 轮：深度分析",
            "markdown": self._normalize_round_markdown(
                round_3,
                title,
                "深度分析",
                stage="paper_round_3_deep_analysis",
            ),
            "updated_at": self._iso_now(),
        }

        _report("正在汇总结构化笔记...", 90)
        final_notes = self._run_round(
            stage="paper_round_final_notes",
            prompt=(
                "请把下面三轮分析汇总为最终结构化笔记，输出中文 Markdown。\n"
                "结构至少包含：一句话总结、核心贡献、方法机制、实验结论、可复现要点、风险与开放问题。\n\n"
                f"[第 1 轮]\n{rounds['round_1']['markdown']}\n\n"
                f"[第 2 轮]\n{rounds['round_2']['markdown']}\n\n"
                f"[第 3 轮]\n{rounds['round_3']['markdown']}"
            ),
            reasoning_level=normalized_reasoning,
            max_tokens=max(int(profile["max_tokens"]), 2600),
        )
        rounds["final_notes"] = {
            "title": "最终结构化笔记",
            "markdown": self._normalize_round_markdown(
                final_notes,
                title,
                "最终结构化笔记",
                stage="paper_round_final_notes",
            ),
            "updated_at": self._iso_now(),
        }

        bundle = {
            "detail_level": normalized_detail,
            "reasoning_level": normalized_reasoning,
            "evidence_mode": normalized_evidence_mode,
            "content_source": effective_content_source,
            "content_source_detail": effective_source_detail,
            "updated_at": self._iso_now(),
            **rounds,
        }

        with session_scope() as session:
            repo = PaperRepository(session)
            paper = repo.get_by_id(paper_id)
            metadata = dict(paper.metadata_json or {})
            metadata["analysis_rounds"] = bundle
            paper.metadata_json = metadata

        _report("论文三轮分析完成", 100)
        return {
            "paper_id": str(paper_id),
            "analysis_rounds": bundle,
        }

    def _run_round(
        self,
        *,
        stage: str,
        prompt: str,
        reasoning_level: str,
        max_tokens: int,
    ) -> LLMResult:
        max_retries = 2
        for attempt in range(max_retries + 1):
            result = self.llm.summarize_text(
                prompt,
                stage=stage,
                variant_override=reasoning_level,
                max_tokens=max_tokens,
                request_timeout=240,
            )
            content = str(result.content or "").strip()
            if not content:
                content = str(result.reasoning_content or "").strip()
            if content and not self.llm._is_provider_error_text(content):
                return result
            if attempt >= max_retries:
                return result
            if self.llm._is_unrecoverable_provider_error_text(content):
                return result
            if not self._is_retryable_provider_error(content):
                return result
            time.sleep(1.2 * (attempt + 1))
        return self.llm.summarize_text(
            prompt,
            stage=stage,
            variant_override=reasoning_level,
            max_tokens=max_tokens,
            request_timeout=240,
        )

    def _normalize_round_markdown(
        self,
        result: LLMResult,
        title: str,
        heading: str,
        *,
        stage: str,
    ) -> str:
        content = str(result.content or "").strip()
        if not content:
            content = str(result.reasoning_content or "").strip()
        if not content:
            raise RuntimeError(f"{heading}失败：模型未返回有效内容。(stage={stage})")
        if self.llm._is_provider_error_text(content):
            raise RuntimeError(content)
        if self._contains_invalid_placeholder(content):
            raise RuntimeError(f"{heading}失败：返回内容为占位/错误提示，请重试。(stage={stage})")
        return content

    @staticmethod
    def _contains_invalid_placeholder(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return True
        markers = (
            "当前模型未返回有效内容",
            "模型服务暂不可用",
            "模型鉴权失败",
            "未配置模型",
            "请稍后重试或检查 api 配置",
            "token unavailable",
            "令牌状态不可用",
            "unauthorized",
            "401",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _is_retryable_provider_error(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return True
        markers = (
            "429",
            "并发限制",
            "rate limit",
            "too many requests",
            "service unavailable",
            "connection error",
            "模型连接异常",
            "请稍后重试",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat()
