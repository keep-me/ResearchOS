"""
LLM 提供者抽象层 - OpenAI / Anthropic / ZhipuAI / Pseudo
支持从数据库动态加载激活的 LLM 配置
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from packages.config import get_settings
from packages.integrations import (
    llm_provider_dispatch,
    llm_provider_embedding,
    llm_provider_error,
    llm_provider_http,
    llm_provider_policy,
    llm_provider_registry,
    llm_provider_resolver,
    llm_provider_responses,
    llm_provider_runtime,
    llm_provider_stream,
    llm_provider_summary,
    llm_provider_transform,
    llm_provider_vision,
)
from packages.integrations.llm_provider_schema import (
    ParsedModelTarget,
    ResolvedEmbeddingConfig,
    ResolvedModelTarget,
    normalize_provider_name as _normalize_provider_name,
)

logger = logging.getLogger(__name__)

# 配置缓存（默认关闭 TTL，确保前后端/worker 切换配置后立刻生效）
_config_cache: LLMConfig | None = None
_config_cache_ts: float = 0.0
_CONFIG_TTL = 0.0
_cache_lock = threading.Lock()


@dataclass
class LLMConfig:
    """当前生效的 LLM 配置"""

    provider: str
    api_key: str | None
    api_base_url: str | None
    model_skim: str
    model_deep: str
    model_vision: str | None
    embedding_provider: str | None
    embedding_api_key: str | None
    embedding_api_base_url: str | None
    model_embedding: str
    model_fallback: str


@dataclass
class LLMResult:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    parsed_json: dict | None = None
    input_cost_usd: float | None = None
    output_cost_usd: float | None = None
    total_cost_usd: float | None = None
    reasoning_content: str | None = None


@dataclass
class EmbeddingResult:
    vector: list[float]
    source: str
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    fallback_reason: str | None = None


@dataclass
class StreamEvent:
    """SSE event from streaming chat"""

    type: str  # "text_delta" | "reasoning_delta" | "tool_call" | "tool_result" | "done" | "usage" | "error"
    content: str = ""
    part_id: str = ""
    metadata: dict[str, Any] | None = None
    tool_call_id: str = ""
    tool_name: str = ""
    tool_arguments: str = ""  # JSON string of args
    provider_executed: bool = False
    tool_success: bool | None = None
    tool_summary: str = ""
    tool_result: Any = None
    # usage fields (only for type="usage")
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _build_unconfigured_config() -> LLMConfig:
    return LLMConfig(
        provider="none",
        api_key=None,
        api_base_url=None,
        model_skim="",
        model_deep="",
        model_vision=None,
        embedding_provider=None,
        embedding_api_key=None,
        embedding_api_base_url=None,
        model_embedding="",
        model_fallback="",
    )


def build_llm_config_from_record(active) -> LLMConfig:
    return LLMConfig(
        provider=_normalize_provider_name(active.provider),
        api_key=active.api_key,
        api_base_url=active.api_base_url,
        model_skim=active.model_skim,
        model_deep=active.model_deep,
        model_vision=active.model_vision,
        embedding_provider=_normalize_provider_name(
            _clean_optional_text(getattr(active, "embedding_provider", ""))
        )
        or None,
        embedding_api_key=_clean_optional_text(
            getattr(active, "embedding_api_key", "")
        ),
        embedding_api_base_url=_clean_optional_text(
            getattr(active, "embedding_api_base_url", "")
        ),
        model_embedding=active.model_embedding,
        model_fallback=active.model_fallback,
    )


def _load_active_config() -> LLMConfig:
    """从数据库加载激活的 LLM 配置，带 TTL 缓存（线程安全）"""
    global _config_cache, _config_cache_ts  # noqa: PLW0603
    now = time.monotonic()
    with _cache_lock:
        if (
            _CONFIG_TTL > 0
            and _config_cache is not None
            and (now - _config_cache_ts) < _CONFIG_TTL
        ):
            return _config_cache

    cfg: LLMConfig | None = None
    try:
        from packages.storage.db import session_scope
        from packages.storage.repositories import (
            LLMConfigRepository,
        )

        with session_scope() as session:
            active = LLMConfigRepository(session).get_active()
            if active:
                cfg = build_llm_config_from_record(active)
    except Exception:
        logger.debug("No active DB config, LLM disabled", exc_info=True)

    if cfg is None:
        cfg = _build_unconfigured_config()

    with _cache_lock:
        _config_cache = cfg
        _config_cache_ts = now
    return cfg


def invalidate_llm_config_cache() -> None:
    """配置变更时调用，清除缓存"""
    global _config_cache, _config_cache_ts  # noqa: PLW0603
    with _cache_lock:
        _config_cache = None
        _config_cache_ts = 0.0

def _get_openai_client(api_key: str, base_url: str | None, timeout: float | None = None):
    """兼容旧调用点，实际委托 provider registry。"""
    return llm_provider_registry.get_openai_client(api_key, base_url, timeout=timeout)


def _get_anthropic_client(
    api_key: str,
    base_url: str | None = None,
    timeout: float | None = None,
):
    """兼容旧调用点，实际委托 provider registry。"""
    return llm_provider_registry.get_anthropic_client(
        api_key,
        base_url=base_url,
        timeout=timeout,
    )


class LLMClient:
    """
    统一 LLM 调用客户端。
    配置带 TTL 缓存，OpenAI 客户端复用。
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def provider(self) -> str:
        return self._config().provider

    def _config(self) -> LLMConfig:
        return _load_active_config()

    @staticmethod
    def _resolve_transport_base_url(provider: str, base: str | None) -> str | None:
        return llm_provider_resolver.resolve_transport_base_url(provider, base)

    def _resolve_base_url(self, cfg: LLMConfig) -> str | None:
        return llm_provider_resolver.resolve_base_url(cfg)

    def _resolve_embedding_provider(self, cfg: LLMConfig) -> str:
        return llm_provider_resolver.resolve_embedding_provider(cfg)

    def _resolve_embedding_api_key(self, cfg: LLMConfig) -> str | None:
        return llm_provider_resolver.resolve_embedding_api_key(cfg)

    def _resolve_embedding_base_url(self, cfg: LLMConfig) -> str | None:
        return llm_provider_resolver.resolve_embedding_base_url(cfg)

    def _resolve_embedding_config(self, cfg: LLMConfig) -> ResolvedEmbeddingConfig:
        return llm_provider_resolver.resolve_embedding_config(cfg)

    @staticmethod
    def _stage_model_string(stage: str, cfg: LLMConfig) -> str:
        return llm_provider_resolver.stage_model_string(stage, cfg)

    def _parse_model_target(self, value: str | None) -> ParsedModelTarget | None:
        return llm_provider_resolver.parse_model_target(value)

    def _resolve_model_target(
        self,
        stage: str,
        model_override: str | None,
        *,
        variant_override: str | None = None,
        cfg: LLMConfig | None = None,
    ) -> ResolvedModelTarget:
        if cfg is None:
            cfg = self._config()
        return llm_provider_resolver.resolve_model_target(
            stage,
            model_override,
            variant_override=variant_override,
            cfg=cfg,
            settings=self.settings,
        )

    def _resolve_model(
        self,
        stage: str,
        model_override: str | None,
        *,
        variant_override: str | None = None,
        cfg: LLMConfig | None = None,
    ) -> str:
        return self._resolve_model_target(
            stage,
            model_override,
            variant_override=variant_override,
            cfg=cfg,
        ).model

    @staticmethod
    def _resolve_summary_dispatch(
        target: ResolvedModelTarget,
    ) -> llm_provider_dispatch.SummaryDispatch:
        return llm_provider_dispatch.resolve_summary_dispatch(target)

    @staticmethod
    def _resolve_chat_dispatch(
        target: ResolvedModelTarget,
    ) -> llm_provider_dispatch.ChatDispatch:
        return llm_provider_dispatch.resolve_chat_dispatch(target)

    @staticmethod
    def _resolve_chat_test_dispatch(
        target: ResolvedModelTarget,
    ) -> llm_provider_dispatch.ChatTestDispatch:
        return llm_provider_dispatch.resolve_chat_test_dispatch(target)

    @staticmethod
    def _resolve_embedding_dispatch(
        active_provider: str | None,
        embedding_cfg: ResolvedEmbeddingConfig,
    ) -> llm_provider_dispatch.EmbeddingDispatch:
        return llm_provider_dispatch.resolve_embedding_dispatch(active_provider, embedding_cfg)

    @staticmethod
    def _resolve_embedding_test_dispatch(
        active_provider: str | None,
        embedding_cfg: ResolvedEmbeddingConfig,
    ) -> llm_provider_dispatch.EmbeddingTestDispatch:
        return llm_provider_dispatch.resolve_embedding_test_dispatch(active_provider, embedding_cfg)

    @staticmethod
    def _supports_dashscope_thinking(base_url: str | None) -> bool:
        return llm_provider_transform.supports_dashscope_thinking(base_url)

    @staticmethod
    def _is_official_openai_target(target: ResolvedModelTarget) -> bool:
        return llm_provider_transform.is_official_openai_target(target)

    @staticmethod
    def _is_google_openai_target(target: ResolvedModelTarget) -> bool:
        return llm_provider_transform.is_google_openai_target(target)

    @staticmethod
    def _is_zhipu_openai_target(target: ResolvedModelTarget) -> bool:
        return llm_provider_transform.is_zhipu_openai_target(target)

    @staticmethod
    def _is_openrouter_target(target: ResolvedModelTarget) -> bool:
        return llm_provider_transform.is_openrouter_target(target)

    @staticmethod
    def _is_venice_target(target: ResolvedModelTarget) -> bool:
        return llm_provider_transform.is_venice_target(target)

    @staticmethod
    def _resolve_model_temperature(model: str) -> float | None:
        return llm_provider_transform.resolve_model_temperature(model)

    @staticmethod
    def _resolve_model_top_p(model: str) -> float | None:
        return llm_provider_transform.resolve_model_top_p(model)

    @staticmethod
    def _resolve_model_top_k(model: str) -> int | None:
        return llm_provider_transform.resolve_model_top_k(model)

    @staticmethod
    def _should_enable_dashscope_reasoning(model: str) -> bool:
        return llm_provider_transform.should_enable_dashscope_reasoning(model)

    @staticmethod
    def _get_extra_body(kwargs: dict) -> dict:
        return llm_provider_transform.get_extra_body(kwargs)

    @staticmethod
    def _setdefault_object(parent: dict, key: str) -> dict:
        return llm_provider_transform.setdefault_object(parent, key)

    def _apply_sampling_kwargs(
        self,
        kwargs: dict,
        target: ResolvedModelTarget,
        *,
        allow_top_k: bool,
    ) -> None:
        llm_provider_transform.apply_sampling_kwargs(
            kwargs,
            target,
            allow_top_k=allow_top_k,
        )

    def _build_google_thinking_config(
        self,
        target: ResolvedModelTarget,
    ) -> dict:
        return llm_provider_transform.build_google_thinking_config(target)

    @staticmethod
    def _is_small_target(target: ResolvedModelTarget) -> bool:
        return llm_provider_transform.is_small_target(target)

    @staticmethod
    def _gateway_provider_slug(model: str) -> str | None:
        return llm_provider_transform.gateway_provider_slug(model)

    @staticmethod
    def _is_gateway_target(target: ResolvedModelTarget) -> bool:
        return llm_provider_transform.is_gateway_target(target)

    def _provider_option_namespace_key(
        self,
        target: ResolvedModelTarget,
    ) -> str | None:
        return llm_provider_transform.provider_option_namespace_key(target)

    def _remap_provider_options_namespace(
        self,
        target: ResolvedModelTarget,
        options: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return llm_provider_transform.remap_provider_options_namespace(target, options)

    def _build_small_provider_options(
        self,
        target: ResolvedModelTarget,
    ) -> dict[str, Any]:
        return llm_provider_transform.build_small_provider_options(target)

    def _apply_provider_options_to_responses_kwargs(
        self,
        kwargs: dict,
        target: ResolvedModelTarget,
        options: dict[str, Any] | None,
    ) -> None:
        llm_provider_transform.apply_provider_options_to_responses_kwargs(kwargs, target, options)

    def _apply_provider_options_to_chat_kwargs(
        self,
        kwargs: dict,
        target: ResolvedModelTarget,
        options: dict[str, Any] | None,
    ) -> None:
        llm_provider_transform.apply_provider_options_to_chat_kwargs(kwargs, target, options)

    @staticmethod
    def _supports_xhigh_effort(model: str) -> bool:
        return llm_provider_transform.supports_xhigh_effort(model)

    def _resolve_reasoning_effort(
        self,
        model: str,
        variant: str | None,
    ) -> str | None:
        return llm_provider_transform.resolve_reasoning_effort(model, variant)

    def _apply_variant_to_responses_kwargs(
        self,
        kwargs: dict,
        target: ResolvedModelTarget,
        *,
        session_cache_key: str | None = None,
    ) -> None:
        llm_provider_transform.apply_variant_to_responses_kwargs(
            kwargs,
            target,
            session_cache_key=session_cache_key,
        )

    def _apply_variant_to_chat_kwargs(
        self,
        kwargs: dict,
        target: ResolvedModelTarget,
        *,
        session_cache_key: str | None = None,
    ) -> None:
        llm_provider_transform.apply_variant_to_chat_kwargs(
            kwargs,
            target,
            session_cache_key=session_cache_key,
        )

    # ---------- 便捷追踪 ----------

    def trace_result(
        self,
        result: LLMResult,
        *,
        stage: str,
        model: str | None = None,
        prompt_digest: str = "",
        paper_id: str | None = None,
        provider: str | None = None,
    ) -> None:
        """将 LLM 调用结果写入 PromptTrace（便捷方法）"""
        try:
            from packages.storage.db import session_scope
            from packages.storage.repositories import PromptTraceRepository

            resolved_model = model or self._resolve_model(stage, None)
            with session_scope() as session:
                PromptTraceRepository(session).create(
                    stage=stage,
                    provider=provider or self.provider,
                    model=resolved_model,
                    prompt_digest=prompt_digest[:500],
                    paper_id=paper_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    input_cost_usd=result.input_cost_usd,
                    output_cost_usd=result.output_cost_usd,
                    total_cost_usd=result.total_cost_usd,
                )
        except Exception as exc:
            logger.debug("trace_result failed: %s", exc)

    # ---------- 公开 API ----------

    def summarize_text(
        self,
        prompt: str,
        stage: str,
        model_override: str | None = None,
        variant_override: str | None = None,
        max_tokens: int | None = None,
        request_timeout: float | None = None,
    ) -> LLMResult:
        return llm_provider_runtime.summarize_text(
            self,
            LLMResult,
            prompt=prompt,
            stage=stage,
            model_override=model_override,
            variant_override=variant_override,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )

    def complete_json(
        self,
        prompt: str,
        stage: str,
        model_override: str | None = None,
        variant_override: str | None = None,
        max_tokens: int | None = None,
        max_retries: int = 2,
        request_timeout: float | None = None,
    ) -> LLMResult:
        wrapped = (
            "请只输出单个 JSON 对象，"
            "不要输出 markdown 代码块包裹，不要输出额外解释。\n"
            "如果信息不足，请根据上下文给出最合理的保守估计，"
            "并保持 JSON 结构完整。\n\n"
            f"{prompt}"
        )
        for attempt in range(max_retries + 1):
            result = self.summarize_text(
                wrapped,
                stage=stage,
                model_override=model_override,
                variant_override=variant_override,
                max_tokens=max_tokens,
                request_timeout=request_timeout,
            )
            if self._is_unrecoverable_provider_error_text(result.content):
                logger.warning(
                    "complete_json: 检测到不可恢复的模型配置错误，停止重试 "
                    "(stage=%s, attempt=%d, content[:160]=%s)",
                    stage,
                    attempt,
                    (result.content or "")[:160],
                )
                parsed = None
                break
            # 多源 JSON 提取：先从 content，再从 reasoning_content
            parsed = self._try_parse_json(result.content)
            if parsed is None and result.reasoning_content:
                parsed = self._try_parse_json(result.reasoning_content)
                if parsed:
                    logger.info(
                        "complete_json: JSON 从 reasoning_content 提取成功 "
                        "(stage=%s, attempt=%d)", stage, attempt
                    )
            if parsed is not None:
                break
            if attempt < max_retries:
                logger.warning(
                    "complete_json: JSON 解析失败，重试 %d/%d (stage=%s)",
                    attempt + 1, max_retries, stage,
                )
            else:
                logger.warning(
                    "complete_json: JSON 解析最终失败 (stage=%s), "
                    "content[:300]=%s",
                    stage, (result.content or "")[:300],
                )
        return LLMResult(
            content=result.content,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            parsed_json=parsed,
            input_cost_usd=result.input_cost_usd,
            output_cost_usd=result.output_cost_usd,
            total_cost_usd=result.total_cost_usd,
            reasoning_content=result.reasoning_content,
        )

    def vision_analyze(
        self,
        image_base64: str,
        prompt: str,
        stage: str = "vision",
        max_tokens: int = 1024,
    ) -> LLMResult:
        """发送图片 + 文本给 Vision 模型（GLM-4.6V 等）"""
        return llm_provider_runtime.vision_analyze(
            self,
            LLMResult,
            image_base64=image_base64,
            prompt=prompt,
            stage=stage,
            max_tokens=max_tokens,
        )

    @staticmethod
    def _get_openai_client(api_key: str, base_url: str | None, timeout: float | None = None):
        return _get_openai_client(api_key, base_url, timeout=timeout)

    def _vision_with_sdk_client(
        self,
        *,
        sdk_client,
        image_base64: str,
        prompt: str,
        max_tokens: int,
        target: ResolvedModelTarget,
    ) -> LLMResult:
        return llm_provider_vision.vision_analyze(
            self,
            LLMResult,
            sdk_client=sdk_client,
            image_base64=image_base64,
            prompt=prompt,
            max_tokens=max_tokens,
            target=target,
        )

    def _vision_openai_compatible(
        self,
        *,
        image_base64: str,
        prompt: str,
        max_tokens: int,
        target: ResolvedModelTarget,
    ) -> LLMResult | None:
        sdk_client = _get_openai_client(target.api_key or "", target.base_url)
        return llm_provider_vision.vision_openai_compatible(
            self,
            LLMResult,
            sdk_client=sdk_client,
            image_base64=image_base64,
            prompt=prompt,
            max_tokens=max_tokens,
            target=target,
        )

    def embed_text_with_info(
        self, text: str, dimensions: int = 1536
    ) -> EmbeddingResult:
        return llm_provider_runtime.embed_text_with_info(
            self,
            EmbeddingResult,
            text=text,
            dimensions=dimensions,
        )

    def embed_text(
        self, text: str, dimensions: int = 1536
    ) -> list[float]:
        return self.embed_text_with_info(text, dimensions).vector

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        variant_override: str | None = None,
        model_override: str | None = None,
        session_cache_key: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream chat completions with optional tool calling support"""
        yield from llm_provider_runtime.chat_stream(
            self,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            variant_override=variant_override,
            model_override=model_override,
            session_cache_key=session_cache_key,
        )

    @staticmethod
    def _to_dict(obj: object) -> dict:
        if isinstance(obj, dict):
            return obj
        model_dump = getattr(obj, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        return {}

    @staticmethod
    def _to_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _extract_embedding_vector(cls, response: object) -> list[float] | None:
        payload = cls._to_dict(response)

        def _to_float_list(values: object) -> list[float] | None:
            if not isinstance(values, list) or not values:
                return None
            try:
                return [float(v) for v in values]
            except (TypeError, ValueError):
                return None

        data = payload.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                vector = _to_float_list(first.get("embedding"))
                if vector:
                    return vector
                vector = _to_float_list(first.get("vector"))
                if vector:
                    return vector
            elif isinstance(first, list):
                vector = _to_float_list(first)
                if vector:
                    return vector

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                vector = _to_float_list(first_choice.get("embedding"))
                if vector:
                    return vector
                message = first_choice.get("message")
                if isinstance(message, dict):
                    vector = _to_float_list(message.get("embedding"))
                    if vector:
                        return vector

        vector = _to_float_list(payload.get("embedding"))
        if vector:
            return vector
        vector = _to_float_list(payload.get("vector"))
        if vector:
            return vector

        embeddings = payload.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            first_embedding = embeddings[0]
            if isinstance(first_embedding, dict):
                vector = _to_float_list(first_embedding.get("embedding"))
                if vector:
                    return vector
                vector = _to_float_list(first_embedding.get("vector"))
                if vector:
                    return vector
            elif isinstance(first_embedding, list):
                vector = _to_float_list(first_embedding)
                if vector:
                    return vector

        return None

    @classmethod
    def _extract_embedding_tokens(cls, response: object) -> int | None:
        payload = cls._to_dict(response)
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return (
                cls._to_int(usage.get("total_tokens"))
                or cls._to_int(usage.get("prompt_tokens"))
                or cls._to_int(usage.get("input_tokens"))
            )
        usage_obj = getattr(response, "usage", None)
        if usage_obj is None:
            return None
        return (
            cls._to_int(getattr(usage_obj, "total_tokens", None))
            or cls._to_int(getattr(usage_obj, "prompt_tokens", None))
            or cls._to_int(getattr(usage_obj, "input_tokens", None))
        )

    @classmethod
    def _extract_responses_usage(cls, response: object) -> tuple[int | None, int | None, int | None]:
        payload = cls._to_dict(response)
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None, None, None
        in_tokens = cls._to_int(usage.get("input_tokens"))
        out_tokens = cls._to_int(usage.get("output_tokens"))
        if in_tokens is None:
            in_tokens = cls._to_int(usage.get("prompt_tokens"))
        if out_tokens is None:
            out_tokens = cls._to_int(usage.get("completion_tokens"))
        return in_tokens, out_tokens, cls._extract_reasoning_tokens(usage)

    @classmethod
    def _extract_reasoning_tokens(cls, usage: object) -> int | None:
        payload = cls._to_dict(usage)
        direct = cls._to_int(payload.get("reasoning_tokens")) or cls._to_int(payload.get("reasoningTokens"))
        if direct is not None:
            return direct

        for key in (
            "output_tokens_details",
            "completion_tokens_details",
            "outputTokensDetails",
            "completionTokensDetails",
        ):
            details = payload.get(key)
            if isinstance(details, dict):
                value = cls._to_int(details.get("reasoning_tokens")) or cls._to_int(details.get("reasoningTokens"))
                if value is not None:
                    return value

        for attr in (
            "reasoning_tokens",
            "reasoningTokens",
        ):
            value = cls._to_int(getattr(usage, attr, None))
            if value is not None:
                return value

        for attr in (
            "output_tokens_details",
            "completion_tokens_details",
            "outputTokensDetails",
            "completionTokensDetails",
        ):
            details = getattr(usage, attr, None)
            if details is None:
                continue
            value = cls._to_int(getattr(details, "reasoning_tokens", None)) or cls._to_int(
                getattr(details, "reasoningTokens", None)
            )
            if value is not None:
                return value
            details_dict = cls._to_dict(details)
            value = cls._to_int(details_dict.get("reasoning_tokens")) or cls._to_int(details_dict.get("reasoningTokens"))
            if value is not None:
                return value

        return None

    @classmethod
    def _extract_responses_text_and_reasoning(cls, response: object) -> tuple[str, str]:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for item in cls._extract_responses_output_parts(response):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            if str(item.get("type") or "") == "reasoning":
                reasoning_parts.append(text)
            elif str(item.get("type") or "") == "text":
                text_parts.append(text)

        text = "\n".join(text_parts).strip()
        reasoning = "\n".join(reasoning_parts).strip()
        return text, reasoning

    @staticmethod
    def _build_openai_provider_metadata(
        *,
        item_id: str | None = None,
        reasoning_encrypted_content: str | None = None,
        annotations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        return llm_provider_responses.build_openai_provider_metadata(
            item_id=item_id,
            reasoning_encrypted_content=reasoning_encrypted_content,
            annotations=annotations,
        )

    @staticmethod
    def _build_openai_response_metadata(
        *,
        response_id: str | None = None,
        service_tier: str | None = None,
    ) -> dict[str, Any] | None:
        return llm_provider_responses.build_openai_response_metadata(
            response_id=response_id,
            service_tier=service_tier,
        )

    @classmethod
    def _extract_responses_output_parts(cls, response: object) -> list[dict[str, Any]]:
        return llm_provider_responses.extract_responses_output_parts(cls, response)

    @staticmethod
    def _has_provider_defined_tool(
        tools: list[dict] | None,
        *tool_ids: str,
    ) -> bool:
        expected = {str(tool_id or "").strip() for tool_id in tool_ids if str(tool_id or "").strip()}
        if not expected or not tools:
            return False
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if str(tool.get("type") or "").strip() != "provider-defined":
                continue
            tool_id = str(tool.get("id") or "").strip()
            if tool_id in expected:
                return True
        return False

    @staticmethod
    def _append_responses_include(kwargs: dict[str, Any], *include_values: str) -> None:
        values = [str(value or "").strip() for value in include_values if str(value or "").strip()]
        if not values:
            return
        current = kwargs.get("include")
        merged: list[str] = []
        if isinstance(current, list):
            merged.extend(str(value).strip() for value in current if str(value).strip())
        elif isinstance(current, str) and current.strip():
            merged.append(current.strip())
        for value in values:
            if value not in merged:
                merged.append(value)
        if merged:
            kwargs["include"] = merged

    @staticmethod
    def _normalize_openai_chat_tools(tools: list[dict] | None) -> list[dict] | None:
        return llm_provider_transform.normalize_openai_chat_tools(tools)

    @staticmethod
    def _extract_openai_item_id(part: dict[str, Any]) -> str | None:
        return llm_provider_responses.extract_openai_item_id(None, part)

    @classmethod
    def _extract_openai_reasoning_metadata(
        cls,
        part: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        return llm_provider_responses.extract_openai_reasoning_metadata(cls, part)

    @staticmethod
    def _extract_openai_response_id_from_message(message: dict[str, Any]) -> str | None:
        return llm_provider_responses.extract_openai_response_id_from_message(None, message)

    @classmethod
    def _extract_previous_responses_response_id(
        cls,
        messages: list[dict[str, Any]],
        *,
        store: bool,
    ) -> str | None:
        return llm_provider_responses.extract_previous_responses_response_id(
            cls,
            messages,
            store=store,
        )

    @staticmethod
    def _parse_local_shell_output(content: object) -> str | None:
        return llm_provider_responses.parse_local_shell_output(content)

    @classmethod
    def _assistant_reasoning_parts(cls, message: dict[str, Any]) -> list[dict[str, Any]]:
        return llm_provider_responses.assistant_reasoning_parts(cls, message)

    @classmethod
    def _assistant_text_parts(cls, message: dict[str, Any]) -> list[dict[str, Any]]:
        return llm_provider_responses.assistant_text_parts(cls, message)

    @classmethod
    def _extract_chat_reasoning_text(cls, payload: object) -> str:
        parts: list[str] = []

        def _append(value: object) -> None:
            if isinstance(value, str):
                text = value.strip()
                if text:
                    parts.append(text)
                return
            if isinstance(value, list):
                for item in value:
                    _append(item)
                return
            if not isinstance(value, dict):
                return

            text = cls._coerce_openai_message_text(
                value.get("text") or value.get("content") or value.get("summary") or value.get("value")
            )
            if text.strip():
                parts.append(text.strip())
                return

            for key in ("summary", "content", "text", "value"):
                inner = value.get(key)
                if inner is not value:
                    _append(inner)

        direct_reasoning_content = getattr(payload, "reasoning_content", None)
        if direct_reasoning_content is not None:
            _append(direct_reasoning_content)
        direct_reasoning = getattr(payload, "reasoning", None)
        if direct_reasoning is not None:
            _append(direct_reasoning)

        payload_dict = cls._to_dict(payload)
        if payload_dict:
            dict_reasoning_content = payload_dict.get("reasoning_content")
            if dict_reasoning_content != direct_reasoning_content:
                _append(dict_reasoning_content)
            dict_reasoning = payload_dict.get("reasoning")
            if dict_reasoning != direct_reasoning:
                _append(dict_reasoning)

        return "\n".join(item for item in parts if item).strip()

    @classmethod
    def _extract_responses_tool_calls(
        cls, response: object
    ) -> list[dict[str, Any]]:
        return llm_provider_responses.extract_responses_tool_calls(cls, response)

    @staticmethod
    def _normalize_responses_tools(tools: list[dict] | None) -> list[dict]:
        return llm_provider_responses.normalize_responses_tools(tools)

    @classmethod
    def _build_responses_input_from_messages(
        cls,
        messages: list[dict],
        *,
        store: bool = False,
    ) -> list[dict]:
        return llm_provider_responses.build_responses_input_from_messages(
            cls,
            messages,
            store=store,
        )

    @staticmethod
    def _supports_chat_reasoning_content(resolved: ResolvedModelTarget) -> bool:
        return llm_provider_policy.supports_chat_reasoning_content(resolved)

    @staticmethod
    def _is_anthropic_chat_target(resolved: ResolvedModelTarget) -> bool:
        return llm_provider_policy.is_anthropic_chat_target(resolved)

    @staticmethod
    def _is_mistral_chat_target(resolved: ResolvedModelTarget) -> bool:
        return llm_provider_policy.is_mistral_chat_target(resolved)

    @staticmethod
    def _normalize_claude_tool_call_id(value: str) -> str:
        return llm_provider_transform.normalize_claude_tool_call_id(value)

    @staticmethod
    def _normalize_mistral_tool_call_id(value: str) -> str:
        return llm_provider_transform.normalize_mistral_tool_call_id(value)

    @classmethod
    def _normalize_openai_chat_messages(
        cls,
        messages: list[dict],
        *,
        resolved: ResolvedModelTarget,
    ) -> list[dict]:
        return llm_provider_transform.normalize_openai_chat_messages(
            cls,
            messages,
            resolved=resolved,
        )

    @classmethod
    def _build_openai_chat_messages(
        cls,
        messages: list[dict],
        *,
        resolved: ResolvedModelTarget,
        include_reasoning_content: bool,
    ) -> list[dict]:
        return llm_provider_transform.build_openai_chat_messages(
            cls,
            messages,
            resolved=resolved,
            include_reasoning_content=include_reasoning_content,
        )

    @staticmethod
    def _should_try_raw_openai_http_fallback(
        resolved: ResolvedModelTarget | None,
        exc: Exception,
    ) -> bool:
        return llm_provider_policy.should_try_raw_openai_http_fallback(resolved, exc)

    @staticmethod
    def _should_try_openai_responses_fallback(
        resolved: ResolvedModelTarget | None,
        exc: Exception,
    ) -> bool:
        return llm_provider_policy.should_try_openai_responses_fallback(resolved, exc)

    @staticmethod
    def _extract_raw_error_message(body: str, fallback: str) -> str:
        return llm_provider_error.extract_response_error_message(body, fallback)

    @staticmethod
    def _coerce_openai_message_text(content: object) -> str:
        return llm_provider_transform.coerce_openai_message_text(content)

    @classmethod
    def _stringify_message_content(cls, content: object) -> str:
        return llm_provider_transform.stringify_message_content(content)

    @classmethod
    def _normalize_user_content_parts(cls, content: object) -> list[dict[str, Any]]:
        return llm_provider_transform.normalize_user_content_parts(content)

    @staticmethod
    def _decode_data_url_bytes(url: str) -> tuple[str | None, bytes | None]:
        return llm_provider_transform.decode_data_url_bytes(url)

    @staticmethod
    def _read_local_file_bytes_from_url(url: str) -> bytes | None:
        return llm_provider_transform.read_local_file_bytes_from_url(url)

    @staticmethod
    def _build_data_url(mime: str, raw: bytes) -> str:
        return llm_provider_transform.build_data_url(mime, raw)

    @classmethod
    def _resolve_model_accessible_file_url(cls, part: dict[str, Any]) -> str | None:
        return llm_provider_transform.resolve_model_accessible_file_url(part)

    @classmethod
    def _extract_text_from_user_file_part(cls, part: dict[str, Any]) -> str | None:
        return llm_provider_transform.extract_text_from_user_file_part(part)

    @classmethod
    def _build_responses_user_content(cls, content: object) -> list[dict[str, Any]] | None:
        return llm_provider_transform.build_responses_user_content(content)

    @classmethod
    def _build_openai_chat_user_content(cls, content: object) -> str | list[dict[str, Any]]:
        return llm_provider_transform.build_openai_chat_user_content(content)

    def _raw_openai_compatible_post(
        self,
        *,
        provider: str | None = None,
        base_url: str | None,
        api_key: str | None,
        path: str,
        payload: dict,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        return llm_provider_http.raw_openai_compatible_post(
            self,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            path=path,
            payload=payload,
            timeout=timeout,
            metadata=metadata,
        )

    def _call_openai_responses_raw_http(
        self,
        *,
        prompt: str,
        resolved: ResolvedModelTarget,
        max_tokens: int | None = None,
        request_timeout: float | None = None,
        session_cache_key: str | None = None,
    ) -> LLMResult:
        return llm_provider_http.call_openai_responses_raw_http(
            self,
            LLMResult,
            prompt=prompt,
            resolved=resolved,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
            session_cache_key=session_cache_key,
        )

    def _call_openai_chat_raw_http(
        self,
        *,
        messages: list[dict],
        resolved: ResolvedModelTarget,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        request_timeout: float | None = None,
        session_cache_key: str | None = None,
    ) -> tuple[LLMResult, list[tuple[str, str, str]]]:
        return llm_provider_http.call_openai_chat_raw_http(
            self,
            LLMResult,
            messages=messages,
            resolved=resolved,
            max_tokens=max_tokens,
            tools=tools,
            request_timeout=request_timeout,
            session_cache_key=session_cache_key,
        )

    def _chat_stream_openai_responses(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        cfg: LLMConfig,
        *,
        target: ResolvedModelTarget | None = None,
        session_cache_key: str | None = None,
        allow_compatible_fallback: bool = True,
        attempts: list[dict[str, object]] | None = None,
    ) -> Iterator[StreamEvent]:
        resolved = target or self._resolve_model_target("rag", None, cfg=cfg)
        sdk_client = _get_openai_client(resolved.api_key or "", resolved.base_url)
        yield from llm_provider_stream.stream_openai_responses(
            self,
            StreamEvent,
            sdk_client=sdk_client,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            cfg=cfg,
            target=resolved,
            session_cache_key=session_cache_key,
            allow_compatible_fallback=allow_compatible_fallback,
            attempts=attempts,
        )

    def _chat_stream_openai_compatible(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        cfg: LLMConfig,
        *,
        target: ResolvedModelTarget | None = None,
        session_cache_key: str | None = None,
    ) -> Iterator[StreamEvent]:
        resolved = target or self._resolve_model_target("rag", None, cfg=cfg)
        sdk_client = _get_openai_client(resolved.api_key or "", resolved.base_url)
        yield from llm_provider_stream.stream_openai_compatible(
            self,
            StreamEvent,
            sdk_client=sdk_client,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            cfg=cfg,
            target=resolved,
            session_cache_key=session_cache_key,
        )

    def _chat_stream_anthropic_fallback(
        self,
        messages: list[dict],
        max_tokens: int,
        cfg: LLMConfig,
        *,
        target: ResolvedModelTarget | None = None,
    ) -> Iterator[StreamEvent]:
        resolved = target or self._resolve_model_target("rag", None, cfg=cfg)
        yield from llm_provider_stream.stream_anthropic_fallback(
            self,
            StreamEvent,
            messages=messages,
            max_tokens=max_tokens,
            cfg=cfg,
            target=resolved,
        )

    def _chat_stream_pseudo(
        self,
        messages: list[dict],
        cfg: LLMConfig,
        *,
        target: ResolvedModelTarget | None = None,
    ) -> Iterator[StreamEvent]:
        resolved = target or self._resolve_model_target("rag", None, cfg=cfg)
        yield from llm_provider_stream.stream_pseudo(
            self,
            StreamEvent,
            messages=messages,
            cfg=cfg,
            target=resolved,
        )

    # ---------- OpenAI 兼容调用（OpenAI / 智谱）----------

    def _call_openai_responses(
        self,
        prompt: str,
        stage: str,
        cfg: LLMConfig,
        model_override: str | None = None,
        variant_override: str | None = None,
        *,
        target: ResolvedModelTarget | None = None,
        max_tokens: int | None = None,
        request_timeout: float | None = None,
        allow_compatible_fallback: bool = True,
    ) -> LLMResult:
        return llm_provider_summary.call_openai_responses(
            self,
            LLMResult,
            prompt=prompt,
            stage=stage,
            cfg=cfg,
            model_override=model_override,
            variant_override=variant_override,
            target=target,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
            allow_compatible_fallback=allow_compatible_fallback,
        )

    def _call_openai_compatible(
        self,
        prompt: str,
        stage: str,
        cfg: LLMConfig,
        model_override: str | None = None,
        variant_override: str | None = None,
        *,
        target: ResolvedModelTarget | None = None,
        max_tokens: int | None = None,
        request_timeout: float | None = None,
    ) -> LLMResult:
        return llm_provider_summary.call_openai_compatible(
            self,
            LLMResult,
            prompt=prompt,
            stage=stage,
            cfg=cfg,
            model_override=model_override,
            variant_override=variant_override,
            target=target,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )

    def _embed_openai_compatible(
        self,
        text: str,
        cfg: LLMConfig,
        embedding_cfg: ResolvedEmbeddingConfig,
    ) -> tuple[list[float], str, str | None] | None:
        return llm_provider_embedding.embed_openai_compatible(
            self,
            text=text,
            cfg=cfg,
            embedding_cfg=embedding_cfg,
            result_cls=LLMResult,
        )

    def test_config(self, cfg: LLMConfig) -> dict:
        return llm_provider_runtime.test_config(self, cfg)

    def _test_chat_config(self, cfg: LLMConfig) -> dict:
        return llm_provider_runtime.test_chat_config(self, cfg)

    def _test_embedding_config(self, cfg: LLMConfig) -> dict:
        return llm_provider_runtime.test_embedding_config(self, cfg)

    def _embedding_candidates(
        self,
        cfg: LLMConfig,
        embedding_cfg: ResolvedEmbeddingConfig,
    ) -> tuple[list[str | None], list[str]]:
        return llm_provider_embedding.embedding_candidates(
            self,
            cfg=cfg,
            embedding_cfg=embedding_cfg,
        )

    def _embed_openai_compatible_or_raise(
        self,
        text: str,
        cfg: LLMConfig,
        embedding_cfg: ResolvedEmbeddingConfig,
    ) -> tuple[list[float], str, str | None]:
        return llm_provider_embedding.embed_openai_compatible_or_raise(
            self,
            text=text,
            cfg=cfg,
            embedding_cfg=embedding_cfg,
            result_cls=LLMResult,
        )

    # ---------- Anthropic ----------

    def _call_anthropic(
        self,
        prompt: str,
        stage: str,
        cfg: LLMConfig,
        model_override: str | None = None,
        variant_override: str | None = None,
        *,
        target: ResolvedModelTarget | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        return llm_provider_summary.call_anthropic(
            self,
            LLMResult,
            prompt=prompt,
            stage=stage,
            cfg=cfg,
            model_override=model_override,
            variant_override=variant_override,
            target=target,
            max_tokens=max_tokens,
        )

    # ---------- Pseudo（无 API Key 回退）----------

    def _pseudo_summary(
        self,
        prompt: str,
        stage: str,
        cfg: LLMConfig,
        model_override: str | None = None,
        variant_override: str | None = None,
        reason: str | None = None,
        *,
        target: ResolvedModelTarget | None = None,
    ) -> LLMResult:
        resolved = target or self._resolve_model_target(
            stage,
            model_override,
            variant_override=variant_override,
            cfg=cfg,
        )
        model = resolved.model
        reason_text = (reason or "").strip()
        reason_lower = reason_text.lower()
        if "missing_active_config" in reason_lower or resolved.provider in ("", "none"):
            pseudo = "未配置模型，请先在系统设置中创建并激活 LLM 配置。"
        elif "invalid_api_key" in reason_lower or "invalid api key" in reason_lower:
            pseudo = (
                "模型 API Key 无效（INVALID_API_KEY）。"
                f"(stage={stage}, provider={resolved.provider}, model={model}) "
                "请在系统设置中更新可用的 API Key。"
            )
        elif any(
            token in reason_lower
            for token in (
                "unauthorized",
                "401",
                "auth failed",
                "authentication",
                "invalid token",
                "token unavailable",
                "token status",
                "token invalid",
                "forbidden",
                "鉴权",
                "令牌状态不可用",
                "令牌不可用",
            )
        ):
            pseudo = (
                "模型鉴权失败（401 Unauthorized / Token 不可用）。"
                f"(stage={stage}, provider={resolved.provider}, model={model}) "
                "请检查 API Key/Token、Provider 与 Base URL 配置。"
            )
        elif "connection error" in reason_lower:
            pseudo = (
                "模型连接异常（Connection error）。"
                f"(stage={stage}, provider={resolved.provider}, model={model}) "
                "请检查网络或代理服务后重试。"
            )
        else:
            pseudo = (
                "模型服务暂不可用。"
                f"(stage={stage}, provider={resolved.provider}, model={model}) "
                "请稍后重试或检查 API 配置。"
            )
        in_tokens = len(prompt) // 4
        out_tokens = len(pseudo) // 4
        in_cost, out_cost = self._estimate_cost(
            model=model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        return LLMResult(
            content=pseudo,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=in_cost + out_cost,
        )

    @staticmethod
    def _is_unrecoverable_provider_error_text(text: str | None) -> bool:
        msg = (text or "").lower()
        tokens = (
            "invalid_api_key",
            "api key 无效",
            "模型鉴权失败",
            "unauthorized",
            "401",
            "auth failed",
            "authentication",
            "invalid token",
            "token unavailable",
            "令牌状态不可用",
            "令牌不可用",
        )
        return any(token in msg for token in tokens)

    @staticmethod
    def _is_provider_error_text(text: str | None) -> bool:
        msg = (text or "").lower()
        tokens = (
            "未配置模型",
            "模型服务暂不可用",
            "api key 无效",
            "invalid_api_key",
            "模型鉴权失败",
            "unauthorized",
            "401",
            "invalid token",
            "token unavailable",
            "令牌状态不可用",
            "令牌不可用",
            "connection error",
            "模型连接异常",
            "请稍后重试或检查 api 配置",
        )
        return any(token in msg for token in tokens)

    @staticmethod
    def _embedding_error_priority(exc: Exception | None) -> int:
        return llm_provider_embedding.embedding_error_priority(exc)

    @staticmethod
    def _pseudo_embedding(
        text: str, dimensions: int = 1536
    ) -> list[float]:
        return llm_provider_embedding.pseudo_embedding(text, dimensions)

    # ---------- 工具 ----------

    @staticmethod
    def _sanitize_json_str(s: str) -> str:
        """修复 LLM 生成 JSON 中的常见问题：未转义的换行、制表符等"""
        # 替换字符串值内部的 literal 换行和制表符
        # 在 JSON string 内（引号之间），将 literal \n \r \t 转为转义序列
        result: list[str] = []
        in_str = False
        esc = False
        for ch in s:
            if esc:
                esc = False
                result.append(ch)
                continue
            if ch == "\\" and in_str:
                esc = True
                result.append(ch)
                continue
            if ch == '"':
                in_str = not in_str
                result.append(ch)
                continue
            if in_str:
                if ch == "\n":
                    result.append("\\n")
                    continue
                if ch == "\r":
                    result.append("\\r")
                    continue
                if ch == "\t":
                    result.append("\\t")
                    continue
                # 去掉其他控制字符 (0x00-0x1F)
                if ord(ch) < 0x20:
                    continue
            result.append(ch)
        return "".join(result)

    @staticmethod
    def _safe_loads(text: str) -> dict | None:
        """json.loads 带净化回退"""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(LLMClient._sanitize_json_str(text))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _try_parse_json(text: str) -> dict | None:
        """从文本中尽力提取 JSON 对象，处理 markdown 代码块和截断"""
        raw = text.strip()
        if not raw:
            return None

        # 1. 直接解析（含净化回退）
        r = LLMClient._safe_loads(raw)
        if r is not None:
            return r

        # 2. 去除 markdown 代码块
        fence_match = re.search(
            r"```(?:json)?\s*\n?(.*?)```",
            raw,
            re.DOTALL,
        )
        if fence_match:
            r = LLMClient._safe_loads(fence_match.group(1).strip())
            if r is not None:
                return r

        # 3. 尝试解析文本中任意平衡的顶层 JSON 对象
        for candidate in LLMClient._extract_balanced_json_objects(raw):
            r = LLMClient._safe_loads(candidate)
            if r is not None:
                return r

        # 4. 提取最外层 {} 块
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            r = LLMClient._safe_loads(raw[start : end + 1])
            if r is not None:
                return r

        # 5. 截断 JSON 修复：模型可能在输出中途停止
        if start != -1:
            candidate = LLMClient._sanitize_json_str(raw[start:])
            repaired = LLMClient._repair_truncated_json(candidate)
            if repaired is not None:
                return repaired

        return None

    @staticmethod
    def _extract_balanced_json_objects(text: str) -> list[str]:
        """提取文本中可能夹杂解释语句时的顶层 JSON 对象片段。"""
        candidates: list[str] = []
        in_string = False
        escape_next = False
        stack: list[str] = []
        start_index: int | None = None

        for index, char in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if char == "\\" and in_string:
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if char == "{":
                if not stack:
                    start_index = index
                stack.append(char)
                continue
            if char == "[" and stack:
                stack.append(char)
                continue
            if char == "}" and stack and stack[-1] == "{":
                stack.pop()
                if not stack and start_index is not None:
                    candidates.append(text[start_index : index + 1])
                    start_index = None
                continue
            if char == "]" and stack and stack[-1] == "[":
                stack.pop()

        unique_candidates = {candidate.strip() for candidate in candidates if candidate.strip()}
        return sorted(unique_candidates, key=len, reverse=True)

    @staticmethod
    def _repair_truncated_json(text: str) -> dict | None:
        """尝试修复被截断的 JSON，补全缺失的括号"""
        closing_map = {"{": "}", "[": "]"}

        def _scan(s: str):
            """扫描 JSON 文本，返回 (stack, in_string, escape_next)"""
            in_str = False
            esc = False
            stk: list[str] = []
            for ch in s:
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch in "{[":
                    stk.append(ch)
                elif ch == "}" and stk and stk[-1] == "{":
                    stk.pop()
                elif ch == "]" and stk and stk[-1] == "[":
                    stk.pop()
            return stk, in_str, esc

        stack, in_string, escape_pending = _scan(text)

        if not stack and not in_string:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None

        # 策略1：直接补全
        closers = "".join(closing_map[b] for b in reversed(stack))
        # 处理各种截断边界
        suffixes: list[str] = []
        if escape_pending:
            # 截断在 \ 后面，去掉尾部 \ 再闭合
            base = text[:-1]
            if in_string:
                suffixes = [f'"{closers}', f'""{closers}']
            else:
                suffixes = [closers]
            for sfx in suffixes:
                try:
                    return json.loads(base + sfx)
                except json.JSONDecodeError:
                    continue

        # 构造 (base_text, suffix) 候选列表
        attempts: list[tuple[str, str]] = []

        if in_string:
            # 截断在字符串中间，去掉末尾不完整转义
            trimmed = text
            if trimmed.endswith("\\"):
                trimmed = trimmed[:-1]
            elif re.search(r'\\u[0-9a-fA-F]{0,3}$', trimmed):
                trimmed = re.sub(r'\\u[0-9a-fA-F]{0,3}$', '', trimmed)
            attempts = [
                (trimmed, f'"{closers}'),
                (trimmed, f'" {closers}'),
            ]
        else:
            clean = text.rstrip().rstrip(",").rstrip()
            attempts = [
                (text, closers),
                (clean, closers),
                (text, f'""{closers}'),
                (text, f'null{closers}'),
            ]

        for base, sfx in attempts:
            try:
                return json.loads(base + sfx)
            except json.JSONDecodeError:
                continue

        # 策略2：回退到最后一个完整的值边界再闭合
        # 找结构性断点: }, ], "后的逗号, 完整数值等
        candidates: list[int] = []
        for m in re.finditer(r'[}\]]\s*,', text):
            candidates.append(m.start() + 1)
        for m in re.finditer(r'"\s*,', text):
            candidates.append(m.start() + 1)
        for m in re.finditer(r'[}\]]\s*$', text):
            candidates.append(m.start() + 1)

        for pos in sorted(set(candidates), reverse=True):
            chunk = text[:pos].rstrip().rstrip(",")
            stk2, in_s2, _ = _scan(chunk)
            if in_s2:
                continue
            cl = "".join(closing_map[b] for b in reversed(stk2))
            try:
                return json.loads(chunk + cl)
            except json.JSONDecodeError:
                continue

        return None

    @staticmethod
    def _estimate_cost(
        *,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> tuple[float, float]:
        model_lower = (model or "").lower()
        price_book: list[tuple[str, float, float]] = [
            # 顺序：更具体的模式放前面
            ("gpt-4.1-mini", 0.4, 1.6),
            ("gpt-4.1", 2.0, 8.0),
            ("gpt-4o-mini", 0.15, 0.6),
            ("gpt-4o", 2.5, 10.0),
            ("claude-3-haiku", 0.25, 1.25),
            ("claude-3-5-sonnet", 3.0, 15.0),
            ("glm-4.6v", 0.14, 0.14),
            ("glm-4.7", 0.1, 0.1),
            ("glm-4-flash", 0.01, 0.01),
            ("glm-4v", 0.14, 0.14),
            ("glm-4", 0.1, 0.1),
            ("embedding", 0.005, 0.0),
        ]
        in_million = 1.0
        out_million = 4.0
        for key, pin, pout in price_book:
            if key in model_lower:
                in_million = pin
                out_million = pout
                break
        in_t = input_tokens or 0
        out_t = output_tokens or 0
        in_cost = float(in_t) * in_million / 1_000_000.0
        out_cost = float(out_t) * out_million / 1_000_000.0
        return in_cost, out_cost

    def estimate_cost(
        self,
        *,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> tuple[float, float, float]:
        in_cost, out_cost = self._estimate_cost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return in_cost, out_cost, in_cost + out_cost
