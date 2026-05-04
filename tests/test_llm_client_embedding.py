from types import SimpleNamespace

from packages.integrations import llm_provider_embedding
from packages.integrations.llm_client import LLMClient, LLMConfig, LLMResult
from packages.integrations.llm_provider_schema import ResolvedEmbeddingConfig


def _config() -> LLMConfig:
    return LLMConfig(
        provider="openai",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_skim="gpt-5-mini",
        model_deep="gpt-5.2",
        model_vision="gpt-4o",
        embedding_provider="openai",
        embedding_api_key="embed-key",
        embedding_api_base_url="https://api.openai.com/v1",
        model_embedding="text-embedding-3-small",
        model_fallback="gpt-4o-mini",
    )


def _embedding(
    *,
    provider: str = "openai",
    api_key: str | None = "embed-key",
    base_url: str = "https://api.openai.com/v1",
    model: str = "text-embedding-3-small",
    explicit_base_url: bool = False,
) -> ResolvedEmbeddingConfig:
    return ResolvedEmbeddingConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        explicit_provider=False,
        explicit_api_key=False,
        explicit_base_url=explicit_base_url,
    )


def test_provider_embedding_candidates_add_dashscope_compatible_base() -> None:
    client = LLMClient()
    cfg = _config()
    cfg.api_base_url = "https://dashscope.aliyuncs.com/api/v1"
    embedding_cfg = _embedding(
        base_url="https://dashscope.aliyuncs.com/api/v1",
        explicit_base_url=True,
    )

    base_candidates, model_candidates = llm_provider_embedding.embedding_candidates(
        client,
        cfg=cfg,
        embedding_cfg=embedding_cfg,
    )

    assert "https://dashscope.aliyuncs.com/api/v1" in base_candidates
    assert "https://dashscope.aliyuncs.com/compatible-mode/v1" in base_candidates
    assert "text-embedding-v3" in model_candidates


def test_provider_embed_openai_compatible_or_raise_returns_vector(monkeypatch) -> None:
    client = LLMClient()
    traced: list[tuple[str, str, str]] = []

    class _FakeEmbeddings:
        def create(self, **kwargs):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}], "usage": {"prompt_tokens": 8}}

    monkeypatch.setattr(
        llm_provider_embedding.llm_provider_registry,
        "get_openai_client",
        lambda *_args, **_kwargs: SimpleNamespace(embeddings=_FakeEmbeddings()),
    )
    monkeypatch.setattr(client, "_extract_embedding_vector", lambda _response: [0.1, 0.2, 0.3])
    monkeypatch.setattr(client, "_extract_embedding_tokens", lambda _response: 8)
    monkeypatch.setattr(client, "_estimate_cost", lambda **_kwargs: (0.05, 0.0))
    monkeypatch.setattr(
        client,
        "trace_result",
        lambda result, *, stage, provider, model, prompt_digest: traced.append(
            (stage, provider, model)
        ),
    )

    vector, used_model, used_base_url = llm_provider_embedding.embed_openai_compatible_or_raise(
        client,
        text="hello embedding",
        cfg=_config(),
        embedding_cfg=_embedding(),
        result_cls=LLMResult,
    )

    assert vector == [0.1, 0.2, 0.3]
    assert used_model == "text-embedding-3-small"
    assert used_base_url == "https://api.openai.com/v1"
    assert traced == [("embed", "openai", "text-embedding-3-small")]


def test_provider_embed_openai_compatible_returns_none_on_error(monkeypatch) -> None:
    client = LLMClient()
    monkeypatch.setattr(
        llm_provider_embedding,
        "embed_openai_compatible_or_raise",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = llm_provider_embedding.embed_openai_compatible(
        client,
        text="hello embedding",
        cfg=_config(),
        embedding_cfg=_embedding(),
        result_cls=LLMResult,
    )

    assert result is None


def test_provider_pseudo_embedding_is_normalized() -> None:
    vector = llm_provider_embedding.pseudo_embedding("hello", 8)

    assert len(vector) == 8
    norm = sum(value * value for value in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    assert llm_provider_embedding.pseudo_embedding("", 4) == [0.0, 0.0, 0.0, 0.0]
