from types import SimpleNamespace

from apps.api.routers.settings import LLM_PROVIDER_PRESETS, _cfg_to_out


def _cfg(**overrides):
    base = {
        "id": "cfg-1",
        "name": "config",
        "provider": "openai",
        "api_key": "test-api-key-123456",
        "api_base_url": "https://api.openai.com/v1",
        "model_skim": "gpt-4o-mini",
        "model_deep": "gpt-5.4",
        "model_vision": "gpt-4o",
        "embedding_provider": None,
        "embedding_api_key": None,
        "embedding_api_base_url": None,
        "model_embedding": "text-embedding-3-small",
        "model_fallback": "gpt-4o-mini",
        "is_active": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_llm_provider_presets_include_zhipu() -> None:
    preset = next(item for item in LLM_PROVIDER_PRESETS if item["id"] == "zhipu")

    assert preset["provider"] == "zhipu"
    assert preset["base_url"] == "https://open.bigmodel.cn/api/paas/v4/"
    assert "glm-4.7" in preset["models"]
    assert "embedding-3" in preset["models"]


def test_llm_provider_presets_include_gemini() -> None:
    preset = next(item for item in LLM_PROVIDER_PRESETS if item["id"] == "gemini")

    assert preset["provider"] == "gemini"
    assert preset["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert "gemini-2.5-flash" in preset["models"]
    assert "gemini-2.5-flash-image" in preset["models"]


def test_cfg_to_out_preserves_zhipu_selection_for_main_and_embedding() -> None:
    out = _cfg_to_out(
        _cfg(
            provider="zhipu",
            api_key="zhipu-api-key-123456",
            api_base_url="https://open.bigmodel.cn/api/paas/v4/",
            model_skim="glm-4.7",
            model_deep="glm-4.7",
            model_vision="glm-4.6v",
            embedding_provider="zhipu",
            embedding_api_key="zhipu-embedding-key-654321",
            embedding_api_base_url="https://open.bigmodel.cn/api/paas/v4/",
            model_embedding="embedding-3",
            model_fallback="glm-4.7",
        )
    )

    assert out["provider"] == "zhipu"
    assert out["provider_family"] == "zhipu"
    assert out["provider_protocol"] == "openai"
    assert out["embedding_provider"] == "zhipu"
    assert out["embedding_provider_family"] == "zhipu"
    assert out["embedding_provider_protocol"] == "openai"


def test_cfg_to_out_preserves_gemini_and_image_generation_fields() -> None:
    out = _cfg_to_out(
        _cfg(
            provider="gemini",
            api_key="gemini-api-key-123456",
            api_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            model_skim="gemini-2.5-flash",
            model_deep="gemini-2.5-pro",
            model_vision="gemini-2.5-flash",
            image_provider="gemini",
            image_api_key="gemini-image-key-654321",
            image_api_base_url="https://generativelanguage.googleapis.com/v1beta",
            model_image="gemini-2.5-flash-image",
        )
    )

    assert out["provider"] == "gemini"
    assert out["provider_family"] == "gemini"
    assert out["provider_protocol"] == "openai"
    assert out["image_provider"] == "gemini"
    assert out["image_api_base_url"] == "https://generativelanguage.googleapis.com/v1beta"
    assert out["image_api_key_masked"]
    assert out["model_image"] == "gemini-2.5-flash-image"


def test_cfg_to_out_keeps_openai_protocol_for_non_zhipu_compatible_targets() -> None:
    out = _cfg_to_out(
        _cfg(
            provider="custom",
            api_base_url="https://openrouter.ai/api/v1",
        )
    )

    assert out["provider"] == "openai"
    assert out["provider_family"] == "custom"
    assert out["provider_protocol"] == "openai"
