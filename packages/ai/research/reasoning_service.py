"""
推理链深度分析服务
引导 LLM 进行分步推理，提供方法论推导链、实验验证链、创新性评估
@author Bamzc
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from packages.ai.paper.analysis_options import (
    get_reasoning_detail_profile,
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
from packages.ai.paper.prompts import build_reasoning_prompt
from packages.config import get_settings
from packages.integrations.llm_client import LLMClient, LLMResult
from packages.storage.db import session_scope
from packages.storage.models import AnalysisReport
from packages.storage.repositories import (
    PaperRepository,
    PromptTraceRepository,
)

logger = logging.getLogger(__name__)


class ReasoningService:
    """推理链深度分析"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()
        self.pdf_extractor = PdfTextExtractor()

    def analyze(
        self,
        paper_id: UUID,
        reasoning_level: str = "default",
        detail_level: str = "medium",
        content_source: str = "auto",
        evidence_mode: str = "full",
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict:
        """对论文进行推理链深度分析"""
        normalized_detail, normalized_reasoning = resolve_paper_analysis_levels(
            detail_level,
            reasoning_level,
        )
        normalized_evidence_mode = normalize_paper_evidence_mode(evidence_mode)
        detail_profile = get_reasoning_detail_profile(
            normalized_detail,
            base_pages=int(self.settings.reasoning_max_pages),
            base_tokens=int(self.settings.reasoning_max_tokens),
            base_timeout=int(self.settings.reasoning_llm_timeout_seconds),
        )
        profile_pages = int(detail_profile["pages"])
        profile_excerpt_chars = int(detail_profile["excerpt_chars"])
        profile_max_tokens = int(detail_profile["max_tokens"])
        profile_timeout_seconds = int(detail_profile["timeout_seconds"])
        if normalized_evidence_mode == "full":
            profile_pages = 0
            profile_excerpt_chars = 0
            profile_max_tokens = max(profile_max_tokens, 4096 if normalized_detail == "high" else 3072)
            profile_timeout_seconds = max(profile_timeout_seconds, 240 if normalized_detail == "high" else 180)
        else:
            profile_pages = max(2, min(profile_pages, 6))
            profile_excerpt_chars = max(2600, min(profile_excerpt_chars, 4200))
            profile_max_tokens = max(1400, min(profile_max_tokens, 2200))
            profile_timeout_seconds = max(45, min(profile_timeout_seconds, 150))

        def _report(message: str, current: int) -> None:
            if progress_callback:
                progress_callback(message, current, 100)

        def _reference_block(
            label: str,
            text: str | None,
            *,
            full_limit: int,
            rough_limit: int,
        ) -> str:
            content = str(text or "").strip()
            if not content:
                return ""
            limit = full_limit if normalized_evidence_mode == "full" else rough_limit
            if limit > 0 and len(content) > limit:
                content = f"{content[:limit].rstrip()}\n..."
            return f"[弱参考 | {label}]\n{content}"

        _report("正在准备推理链分析...", 6)

        # 1) 在 session 内取出所有需要的数据
        with session_scope() as session:
            from sqlalchemy import select

            paper = PaperRepository(session).get_by_id(paper_id)
            paper_title = paper.title
            paper_abstract = paper.abstract
            pdf_path = paper.pdf_path

            existing = session.execute(
                select(AnalysisReport).where(AnalysisReport.paper_id == str(paper_id))
            ).scalar_one_or_none()
            analysis_context = ""
            analysis_blocks: list[str] = []
            if existing:
                skim_block = _reference_block(
                    "粗读",
                    existing.summary_md,
                    full_limit=900,
                    rough_limit=600,
                )
                if skim_block:
                    analysis_blocks.append(skim_block)
                deep_block = _reference_block(
                    "精读",
                    existing.deep_dive_md,
                    full_limit=1400,
                    rough_limit=900,
                )
                if deep_block:
                    analysis_blocks.append(deep_block)
            if analysis_blocks:
                analysis_context = (
                    "以下已有分析仅作弱参考，用于术语对齐和快速回忆；"
                    "如果与本轮结构化证据冲突，必须以结构化证据为准，并直接纠正旧结论。\n\n"
                    + "\n\n".join(analysis_blocks)
                )

        # 2) 提取 PDF 文本（session 外）
        _report(
            f"正在提取论文文本与上下文（详略: {detail_profile['label']}，证据: {'完整' if normalized_evidence_mode == 'full' else '粗略'}）...",
            26,
        )
        extracted_text = ""
        effective_content_source = resolve_effective_paper_content_source(content_source, None)
        effective_source_detail = ""
        if pdf_path:
            evidence = load_prepared_paper_evidence(
                paper_id=paper_id,
                pdf_path=pdf_path,
                content_source=content_source,
                evidence_mode=normalized_evidence_mode,
                pdf_extractor=self.pdf_extractor,
                pdf_text_pages=profile_pages,
                pdf_text_chars=0 if normalized_evidence_mode == "full" else profile_excerpt_chars * 2,
            )
            effective_content_source = resolve_effective_paper_content_source(
                content_source,
                evidence.source,
            )
            effective_source_detail = evidence.source
            if normalized_evidence_mode == "full":
                extracted_text = evidence.build_targeted_context(
                    name="推理链结构化证据包",
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
                    max_chars=0,
                    max_sections=12,
                    max_figures=6,
                    max_tables=6,
                    max_equations=5,
                    include_outline=True,
                    notes=[
                        "用于推理链分析，证据跨全文选取，不代表正文只到某一节。",
                        "这是面向推理链任务筛选的结构化证据包；未出现的细节不代表原文不存在。",
                    ],
                )
            else:
                extracted_text = evidence.build_analysis_context(max_chars=profile_excerpt_chars)

        _report(
            f"推理链证据已就绪（来源: {paper_content_source_label(effective_content_source)}，证据: {'完整' if normalized_evidence_mode == 'full' else '粗略'}）...",
            42,
        )

        # 3) LLM 调用
        _report("正在调用模型生成推理链（复杂论文可能需要 1-3 分钟）...", 56)
        prompt = build_reasoning_prompt(
            title=paper_title,
            abstract=paper_abstract,
            extracted_text=extracted_text,
            analysis_context=analysis_context,
            detail_level=normalized_detail,
        )
        active_cfg = self.llm._config()
        active_deep_model = active_cfg.model_deep
        degraded = False
        degraded_reason = ""

        result = self._complete_json_with_deadline(
            prompt,
            model_override=active_deep_model,
            variant_override=normalized_reasoning,
            max_tokens=profile_max_tokens,
            request_timeout=profile_timeout_seconds,
            max_wait_seconds=max(
                profile_timeout_seconds + 180,
                int(float(profile_timeout_seconds) * 2.8),
            ),
            max_retries=2,
        )
        if not isinstance(result.parsed_json, dict):
            degraded = True
            degraded_reason = (
                "invalid_api_key"
                if self._is_invalid_api_key_result(result)
                else "model_timeout_or_invalid_json"
            )
            if degraded_reason == "invalid_api_key":
                _report("模型配置异常（API Key 无效），已生成降级结果...", 72)
            else:
                _report("模型响应超时或输出异常，已降级生成基础推理结果...", 72)

        parsed = self._normalize_reasoning(result.parsed_json, paper_title)

        # 4) 保存 token 追踪 + 结果持久化
        _report("正在保存推理链结果...", 86)
        with session_scope() as session:
            PromptTraceRepository(session).create(
                stage="reasoning_chain",
                provider=self.llm.provider,
                model=active_deep_model,
                prompt_digest=prompt[:500],
                paper_id=paper_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                input_cost_usd=result.input_cost_usd,
                output_cost_usd=result.output_cost_usd,
                total_cost_usd=result.total_cost_usd,
            )

            paper2 = PaperRepository(session).get_by_id(paper_id)
            meta = dict(paper2.metadata_json or {})
            meta["reasoning_chain"] = parsed
            meta["reasoning_chain_meta"] = {
                "content_source": effective_content_source,
                "content_source_detail": effective_source_detail,
                "detail_level": normalized_detail,
                "reasoning_level": normalized_reasoning,
                "evidence_mode": normalized_evidence_mode,
                "updated_at": datetime.now().isoformat(),
            }
            paper2.metadata_json = meta

        _report("推理链分析完成", 100)
        return {
            "paper_id": str(paper_id),
            "title": paper_title,
            "reasoning": parsed,
            "content_source": effective_content_source,
            "content_source_detail": effective_source_detail or None,
            "degraded": degraded,
            "degraded_reason": degraded_reason or None,
            "model": active_deep_model,
        }

    @staticmethod
    def _is_invalid_api_key_result(result: LLMResult) -> bool:
        text = (result.content or "").lower()
        return "invalid_api_key" in text or "api key 无效" in text

    def _complete_json_with_deadline(
        self,
        prompt: str,
        *,
        model_override: str,
        variant_override: str,
        max_tokens: int,
        request_timeout: int,
        max_wait_seconds: int,
        max_retries: int,
    ) -> LLMResult:
        deadline = time.monotonic() + float(max_wait_seconds)
        last_result = LLMResult(content="", parsed_json=None)

        for attempt in range(max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            wait_seconds = min(
                max(float(request_timeout) * 2.25, float(request_timeout) + 90.0),
                remaining,
            )
            result_queue: queue.Queue[LLMResult | Exception] = queue.Queue(maxsize=1)

            def _worker() -> None:
                try:
                    result = self.llm.complete_json(
                        prompt,
                        stage="deep",
                        model_override=model_override,
                        variant_override=variant_override,
                        max_tokens=max_tokens,
                        max_retries=0,
                        request_timeout=request_timeout,
                    )
                    result_queue.put(result)
                except Exception as exc:
                    result_queue.put(exc)

            thread = threading.Thread(
                target=_worker,
                daemon=True,
                name=f"reasoning-llm-{attempt + 1}",
            )
            thread.start()

            try:
                outcome = result_queue.get(timeout=wait_seconds)
            except queue.Empty:
                logger.warning(
                    "Reasoning LLM attempt %s/%s timed out after %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    wait_seconds,
                )
                continue

            if isinstance(outcome, Exception):
                logger.warning(
                    "Reasoning LLM attempt %s/%s failed: %s",
                    attempt + 1,
                    max_retries + 1,
                    outcome,
                )
                continue

            last_result = outcome
            if isinstance(outcome.parsed_json, dict):
                return outcome
            if self._is_invalid_api_key_result(outcome):
                return outcome

            logger.warning(
                "Reasoning LLM attempt %s/%s returned non-JSON content; retrying",
                attempt + 1,
                max_retries + 1,
            )

        logger.warning(
            "Reasoning LLM exhausted retries or timed out after %ss; fallback will be used",
            max_wait_seconds,
        )
        return last_result

    @classmethod
    def _normalize_reasoning(cls, payload: dict | None, title: str) -> dict:
        base = cls._fallback(title)
        if not isinstance(payload, dict):
            return base

        out = deepcopy(base)

        steps = payload.get("reasoning_steps")
        if isinstance(steps, list):
            normalized_steps: list[dict] = []
            for idx, step in enumerate(steps[:12]):
                if not isinstance(step, dict):
                    continue
                normalized_steps.append(
                    {
                        "step": cls._as_text(step.get("step"), f"步骤 {idx + 1}", 120),
                        "thinking": cls._as_text(step.get("thinking"), "", 4000),
                        "conclusion": cls._as_text(step.get("conclusion"), "", 1200),
                    }
                )
            if normalized_steps:
                out["reasoning_steps"] = normalized_steps

        method_chain = payload.get("method_chain")
        if isinstance(method_chain, dict):
            out["method_chain"] = {
                "problem_definition": cls._as_text(
                    method_chain.get("problem_definition"), "", 2000
                ),
                "core_hypothesis": cls._as_text(
                    method_chain.get("core_hypothesis"), "", 2000
                ),
                "method_derivation": cls._as_text(
                    method_chain.get("method_derivation"), "", 3000
                ),
                "theoretical_basis": cls._as_text(
                    method_chain.get("theoretical_basis"), "", 3000
                ),
                "innovation_analysis": cls._as_text(
                    method_chain.get("innovation_analysis"), "", 3000
                ),
            }

        experiment_chain = payload.get("experiment_chain")
        if isinstance(experiment_chain, dict):
            out["experiment_chain"] = {
                "experimental_design": cls._as_text(
                    experiment_chain.get("experimental_design"), "", 2400
                ),
                "baseline_fairness": cls._as_text(
                    experiment_chain.get("baseline_fairness"), "", 2400
                ),
                "result_validation": cls._as_text(
                    experiment_chain.get("result_validation"), "", 2400
                ),
                "ablation_insights": cls._as_text(
                    experiment_chain.get("ablation_insights"), "", 2400
                ),
            }

        impact = payload.get("impact_assessment")
        if isinstance(impact, dict):
            out["impact_assessment"] = {
                "novelty_score": cls._as_score(impact.get("novelty_score")),
                "rigor_score": cls._as_score(impact.get("rigor_score")),
                "impact_score": cls._as_score(impact.get("impact_score")),
                "overall_assessment": cls._as_text(
                    impact.get("overall_assessment"), "", 3000
                ),
                "strengths": cls._as_str_list(impact.get("strengths"), 8, 500),
                "weaknesses": cls._as_str_list(impact.get("weaknesses"), 8, 500),
                "future_suggestions": cls._as_str_list(
                    impact.get("future_suggestions"), 8, 500
                ),
            }
        else:
            out["impact_assessment"] = cls._estimate_impact_assessment(out)

        return out

    @staticmethod
    def _as_text(value: object, default: str, max_len: int) -> str:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        return text[:max_len]

    @staticmethod
    def _as_score(value: object) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 0.0
        return min(max(score, 0.0), 1.0)

    @staticmethod
    def _as_str_list(value: object, max_items: int, max_len: int) -> list[str]:
        if isinstance(value, list):
            out = [str(x).strip()[:max_len] for x in value if str(x).strip()]
            return out[:max_items]
        if value is None:
            return []
        text = str(value).strip()
        return [text[:max_len]] if text else []

    @staticmethod
    def _fallback(title: str) -> dict:
        return {
            "reasoning_steps": [
                {
                    "step": "分析未完成",
                    "thinking": f"论文「{title}」的推理链分析需要更多信息。",
                    "conclusion": "当前结果不足以形成稳定推理链，请在论文内容更完整后重新执行。",
                }
            ],
            "method_chain": {
                "problem_definition": "",
                "core_hypothesis": "",
                "method_derivation": "",
                "theoretical_basis": "",
                "innovation_analysis": "",
            },
            "experiment_chain": {
                "experimental_design": "",
                "baseline_fairness": "",
                "result_validation": "",
                "ablation_insights": "",
            },
            "impact_assessment": {
                "novelty_score": 0,
                "rigor_score": 0,
                "impact_score": 0,
                "overall_assessment": "",
                "strengths": [],
                "weaknesses": [],
                "future_suggestions": [],
            },
        }

    @classmethod
    def _estimate_impact_assessment(cls, normalized: dict) -> dict:
        steps = normalized.get("reasoning_steps") if isinstance(normalized, dict) else []
        if isinstance(steps, list) and steps:
            first_step = steps[0] if isinstance(steps[0], dict) else {}
            if str(first_step.get("step") or "").strip() == "分析未完成":
                return {
                    "novelty_score": 0.0,
                    "rigor_score": 0.0,
                    "impact_score": 0.0,
                    "overall_assessment": "",
                    "strengths": [],
                    "weaknesses": [],
                    "future_suggestions": [],
                }

        method = normalized.get("method_chain") if isinstance(normalized, dict) else {}
        experiment = normalized.get("experiment_chain") if isinstance(normalized, dict) else {}
        method_len = sum(len(str(v or "")) for v in method.values()) if isinstance(method, dict) else 0
        experiment_len = sum(len(str(v or "")) for v in experiment.values()) if isinstance(experiment, dict) else 0
        step_len = 0
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_len += len(str(step.get("thinking") or "")) + len(str(step.get("conclusion") or ""))

        total_len = method_len + experiment_len + step_len
        if total_len < 120:
            return {
                "novelty_score": 0.0,
                "rigor_score": 0.0,
                "impact_score": 0.0,
                "overall_assessment": "",
                "strengths": [],
                "weaknesses": [],
                "future_suggestions": [],
            }

        novelty = cls._as_score(0.35 + min(method_len, 3200) / 9000 + min(step_len, 2600) / 12000)
        rigor = cls._as_score(0.32 + min(experiment_len, 3600) / 8200 + min(step_len, 2200) / 13000)
        impact = cls._as_score(0.33 + min(total_len, 7200) / 13000)

        return {
            "novelty_score": novelty,
            "rigor_score": rigor,
            "impact_score": impact,
            "overall_assessment": "模型未返回量化分数，已基于推理内容完整度进行估算。",
            "strengths": [],
            "weaknesses": [],
            "future_suggestions": [],
        }
