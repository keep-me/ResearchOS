from __future__ import annotations

from typing import Any

import pytest

from packages.ai.research.writing_service import WritingImageConfig, WritingService


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.text = "ok"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, *, timeout: float):
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "已按论文风格生成图像。"},
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": "ZmFrZS1pbWFnZS1iYXNlNjQ=",
                                    }
                                },
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 123,
                    "candidatesTokenCount": 45,
                },
            }
        )


def test_generate_image_builds_gemini_request(monkeypatch: pytest.MonkeyPatch) -> None:
    service = WritingService()
    fake_client = _FakeClient(timeout=120.0)

    monkeypatch.setattr(
        service,
        "_resolve_image_generation_config",
        lambda: WritingImageConfig(
            provider="gemini",
            api_key="gemini-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model="gemini-2.5-flash-image",
        ),
    )
    monkeypatch.setattr(
        "packages.ai.research.writing_service.httpx.Client",
        lambda timeout: fake_client,
    )

    result = service.generate_image(
        "画一个三阶段检索增强生成框架图",
        image_base64="ZmFrZS1yZWY=",
        aspect_ratio="16:9",
    )

    assert result["kind"] == "image"
    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-2.5-flash-image"
    assert result["aspect_ratio"] == "16:9"
    assert result["image_base64"] == "ZmFrZS1pbWFnZS1iYXNlNjQ="
    assert result["mime_type"] == "image/png"
    assert result["input_tokens"] == 123
    assert result["output_tokens"] == 45

    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    assert call["headers"]["x-goog-api-key"] == "gemini-key"
    assert call["json"]["generationConfig"]["imageConfig"]["aspectRatio"] == "16:9"
    parts = call["json"]["contents"][0]["parts"]
    assert parts[0]["text"]
    assert parts[1]["inlineData"]["data"] == "ZmFrZS1yZWY="


def test_generate_image_rejects_unsupported_aspect_ratio() -> None:
    service = WritingService()

    with pytest.raises(ValueError, match="不支持的画布比例"):
        service.generate_image("画一个方法总览图", aspect_ratio="2:1")
