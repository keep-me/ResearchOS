"""Serialization helpers for paper API responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID


def utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def paper_ocr_status_payload(metadata: dict[str, Any] | None) -> dict[str, Any]:
    payload = metadata.get("mineru_ocr") if isinstance(metadata, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    status = str(payload.get("status") or "").strip().lower() or "idle"
    markdown_chars = int(payload.get("markdown_chars") or 0)
    has_structured_output = bool(payload.get("has_structured_output"))
    return {
        "status": status,
        "available": status == "success" and (markdown_chars > 0 or has_structured_output),
        "updated_at": str(payload.get("updated_at") or "").strip() or None,
        "markdown_chars": markdown_chars,
        "has_structured_output": has_structured_output,
        "error": str(payload.get("error") or "").strip() or None,
        "output_root": str(payload.get("output_root") or "").strip() or None,
        "model_dir": str(payload.get("model_dir") or "").strip() or None,
    }


def attach_figure_image_urls(paper_id: UUID, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        item["image_url"] = f"/papers/{paper_id}/figures/{item['id']}/image" if item.get("has_image") else None
    return items


__all__ = ["attach_figure_image_urls", "paper_ocr_status_payload", "utc_iso"]

