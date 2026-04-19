from __future__ import annotations

import logging

from packages.integrations import llm_provider_registry
from packages.integrations.llm_provider_schema import ResolvedEmbeddingConfig

logger = logging.getLogger(__name__)


def embedding_candidates(
    client,
    *,
    cfg,
    embedding_cfg: ResolvedEmbeddingConfig,
) -> tuple[list[str | None], list[str]]:
    provider = embedding_cfg.provider
    base_url = embedding_cfg.base_url
    model_name = embedding_cfg.model
    primary_base_url = client._resolve_base_url(cfg)
    explicit_embedding_base_url = embedding_cfg.explicit_base_url
    if provider not in ("openai", "zhipu", "custom") or not embedding_cfg.api_key:
        return [], []

    model_candidates = [model_name]
    for alias in (
        "text-embedding-3-large",
        "text-embedding-3-small",
        "text-embedding-ada-002",
    ):
        if alias not in model_candidates:
            model_candidates.append(alias)
    if (base_url or "").lower().find("aliyuncs.com") >= 0 or (
        primary_base_url or ""
    ).lower().find("aliyuncs.com") >= 0:
        for alias in ("text-embedding-v3", "text-embedding-v2"):
            if alias not in model_candidates:
                model_candidates.append(alias)

    base_candidates: list[str | None] = []
    if base_url:
        base_candidates.append(base_url)
    if (
        not explicit_embedding_base_url
        and primary_base_url
        and primary_base_url != base_url
    ):
        base_candidates.append(primary_base_url)
    if not base_candidates:
        base_candidates.append(None)
    for candidate in list(base_candidates):
        raw = (candidate or "").lower()
        if "dashscope.aliyuncs.com" in raw and "compatible-mode/v1" not in raw:
            compat = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if compat not in base_candidates:
                base_candidates.append(compat)
    return base_candidates, model_candidates


def embedding_error_priority(exc: Exception | None) -> int:
    message = str(exc or "").lower()
    if not message:
        return 0
    if "serviceunavailable" in message or "服务不可用" in message:
        return 40
    if "invalid_api_key" in message or "api key 无效" in message:
        return 35
    if "connection error" in message or "connect" in message or "timeout" in message:
        return 30
    if "modelnotfound" in message or "模型未找到" in message:
        return 10
    return 20


def pseudo_embedding(text: str, dimensions: int = 1536) -> list[float]:
    if not text:
        return [0.0] * dimensions
    values = [0.0] * dimensions
    for index, char in enumerate(text.encode("utf-8")):
        values[index % dimensions] += float(char) / 255.0
    scale = max(sum(value * value for value in values) ** 0.5, 1e-6)
    return [value / scale for value in values]


def embed_openai_compatible(
    client,
    *,
    text: str,
    cfg,
    embedding_cfg: ResolvedEmbeddingConfig,
    result_cls,
) -> tuple[list[float], str, str | None] | None:
    if not text:
        return None
    try:
        return embed_openai_compatible_or_raise(
            client,
            text=text,
            cfg=cfg,
            embedding_cfg=embedding_cfg,
            result_cls=result_cls,
        )
    except Exception as exc:
        logger.warning("Embedding call failed: %s", exc)
        return None


def embed_openai_compatible_or_raise(
    client,
    *,
    text: str,
    cfg,
    embedding_cfg: ResolvedEmbeddingConfig,
    result_cls,
) -> tuple[list[float], str, str | None]:
    if not text:
        raise ValueError("Embedding input is empty")

    api_key = embedding_cfg.api_key
    if not api_key:
        raise ValueError("Missing embedding API key")

    base_candidates, model_candidates = embedding_candidates(
        client,
        cfg=cfg,
        embedding_cfg=embedding_cfg,
    )
    last_exc: Exception | None = None
    preferred_exc: Exception | None = None

    for candidate_base in base_candidates:
        for candidate_model in model_candidates:
            try:
                sdk_client = llm_provider_registry.get_openai_client(api_key, candidate_base)
                response = sdk_client.embeddings.create(
                    model=candidate_model,
                    input=text,
                )
                vector = client._extract_embedding_vector(response)
                if not vector:
                    payload = client._to_dict(response)
                    keys = ",".join(sorted(payload.keys()))[:200]
                    raise RuntimeError(
                        "No embedding data received"
                        + (f" (response_keys={keys})" if keys else "")
                    )
                in_tokens = client._extract_embedding_tokens(response)
                in_cost, _ = client._estimate_cost(
                    model=candidate_model,
                    input_tokens=in_tokens,
                    output_tokens=0,
                )
                client.trace_result(
                    result_cls(
                        content="",
                        input_tokens=in_tokens,
                        output_tokens=0,
                        input_cost_usd=in_cost,
                        output_cost_usd=0.0,
                        total_cost_usd=in_cost,
                    ),
                    stage="embed",
                    provider=embedding_cfg.provider,
                    model=candidate_model,
                    prompt_digest=f"embed:{text[:80]}",
                )
                if candidate_model != embedding_cfg.model:
                    logger.info(
                        "Embedding model fallback applied: %s -> %s",
                        embedding_cfg.model,
                        candidate_model,
                    )
                return vector, candidate_model, candidate_base
            except Exception as exc:
                last_exc = exc
                if (
                    preferred_exc is None
                    or embedding_error_priority(exc)
                    > embedding_error_priority(preferred_exc)
                ):
                    preferred_exc = exc

    if preferred_exc is not None:
        raise preferred_exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Embedding call failed without a detailed error")
