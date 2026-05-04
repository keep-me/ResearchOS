from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import papers as papers_router
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.models import ImageAnalysis, Paper


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(papers_router.router)
    return app


def _create_paper(*, metadata: dict | None = None) -> str:
    paper_id = str(uuid4())
    with session_scope() as session:
        session.add(
            Paper(
                id=paper_id,
                arxiv_id=f"paper-{uuid4().hex[:12]}",
                title="Test Paper",
                abstract="This is the abstract.",
                metadata_json=metadata or {},
            )
        )
        session.flush()
    return paper_id


def test_reader_query_selection_translate(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    monkeypatch.setattr(
        papers_router.LLMClient,
        "summarize_text",
        lambda self, prompt, stage, max_tokens=None: LLMResult(content="翻译结果"),
    )

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/query",
        json={
            "scope": "selection",
            "action": "translate",
            "text": "Hello world",
            "page_number": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "selection"
    assert payload["action"] == "translate"
    assert payload["result"] == "翻译结果"
    assert payload["page_number"] == 2


def test_reader_query_paper_uses_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper(
        metadata={
            "title_zh": "测试论文",
            "analysis_rounds": {
                "round_1": {
                    "title": "第一轮",
                    "markdown": "Round one analysis",
                }
            },
        }
    )

    captured: dict[str, str] = {}

    monkeypatch.setattr(
        papers_router,
        "_ensure_paper_pdf",
        lambda session, repo, paper, pid: str(tmp_path / "paper.pdf"),
    )

    from packages.ai.paper import figure_service, pdf_parser

    monkeypatch.setattr(
        pdf_parser.PdfTextExtractor,
        "extract_text",
        lambda self, pdf_path, max_pages=12: "Section 1\nMethod details\nExperiment results",
    )
    monkeypatch.setattr(
        figure_service.FigureService,
        "get_paper_analyses",
        classmethod(
            lambda cls, pid: [
                {
                    "page_number": 3,
                    "image_type": "figure",
                    "caption": "Figure 1",
                    "description": "Shows a trend",
                }
            ]
        ),
    )

    def _fake_summarize(self, prompt, stage, max_tokens=None):
        captured["prompt"] = prompt
        return LLMResult(content="全文问答结果")

    monkeypatch.setattr(papers_router.LLMClient, "summarize_text", _fake_summarize)

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/query",
        json={
            "scope": "paper",
            "action": "ask",
            "question": "这篇论文主要做了什么？",
        },
    )

    assert response.status_code == 200
    assert response.json()["result"] == "全文问答结果"
    assert "测试论文" in captured["prompt"]
    assert "Method details" in captured["prompt"]
    assert "Figure 1" in captured["prompt"]


def test_reader_query_paper_prefers_mineru_ocr_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper(
        metadata={
            "title_zh": "测试论文",
        }
    )
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 ocr")
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        papers_router,
        "_ensure_paper_pdf",
        lambda session, repo, paper, pid: str(pdf_path),
    )

    from packages.ai.paper import figure_service

    monkeypatch.setattr(
        "packages.ai.paper.mineru_runtime.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            build_analysis_context=lambda max_chars=0: "OCR正文 含公式与图表"
        ),
    )
    monkeypatch.setattr(
        "packages.ai.paper.pdf_parser.PdfTextExtractor.extract_text",
        lambda self, pdf_path, max_pages=12: (_ for _ in ()).throw(
            AssertionError("should not fallback")
        ),
    )
    monkeypatch.setattr(
        figure_service.FigureService,
        "get_paper_analyses",
        classmethod(lambda cls, pid: []),
    )

    def _fake_summarize(self, prompt, stage, max_tokens=None):
        captured["prompt"] = prompt
        return LLMResult(content="全文问答结果")

    monkeypatch.setattr(papers_router.LLMClient, "summarize_text", _fake_summarize)

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/query",
        json={
            "scope": "paper",
            "action": "ask",
            "question": "这篇论文主要做了什么？",
        },
    )

    assert response.status_code == 200
    assert response.json()["result"] == "全文问答结果"
    assert "OCR正文 含公式与图表" in captured["prompt"]


def test_reader_query_figure_uses_vision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"fake-image")
    figure_id = str(uuid4())

    with session_scope() as session:
        session.add(
            ImageAnalysis(
                id=figure_id,
                paper_id=paper_id,
                page_number=5,
                image_index=0,
                image_type="figure",
                caption="Figure 5",
                description="Existing figure summary",
                image_path=str(image_path),
            )
        )
        session.flush()

    captured: dict[str, str] = {}

    def _fake_vision(self, image_base64, prompt, stage="vision", max_tokens=1024):
        captured["image"] = image_base64
        captured["prompt"] = prompt
        return LLMResult(content="图表问答结果")

    monkeypatch.setattr(papers_router.LLMClient, "vision_analyze", _fake_vision)

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/query",
        json={
            "scope": "figure",
            "action": "ask",
            "figure_id": figure_id,
            "question": "这个图表达了什么？",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"] == "图表问答结果"
    assert payload["page_number"] == 5
    assert base64.b64decode(captured["image"]) == b"fake-image"
    assert "Figure 5" in captured["prompt"]


def test_reader_query_region_uses_vision_without_figure_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    payload_image = base64.b64encode(b"region-image").decode("utf-8")
    captured: dict[str, str] = {}

    def _fake_vision(self, image_base64, prompt, stage="vision", max_tokens=1024):
        captured["image"] = image_base64
        captured["prompt"] = prompt
        return LLMResult(content="区域解释结果")

    monkeypatch.setattr(papers_router.LLMClient, "vision_analyze", _fake_vision)

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/query",
        json={
            "scope": "figure",
            "action": "analyze",
            "image_base64": payload_image,
            "page_number": 3,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "区域解释结果"
    assert body["figure_id"] is None
    assert body["page_number"] == 3
    assert captured["image"] == payload_image
    assert "框选区域" in captured["prompt"]
    assert "分析这张图、表或框选区域" in captured["prompt"]


def test_get_paper_figures_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    papers_router.cache.invalidate_prefix("paper_figures_")

    from packages.ai.paper import figure_service

    call_counter = {"count": 0}

    def _fake_get_paper_analyses(cls, pid):
        call_counter["count"] += 1
        return [
            {
                "id": "fig-1",
                "has_image": False,
                "caption": "Figure 1",
            }
        ]

    monkeypatch.setattr(
        figure_service.FigureService,
        "get_paper_analyses",
        classmethod(_fake_get_paper_analyses),
    )

    client = TestClient(_build_app())
    first = client.get(f"/papers/{paper_id}/figures")
    second = client.get(f"/papers/{paper_id}/figures")

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_counter["count"] == 1
    assert second.json()["items"][0]["image_url"] is None


def test_get_figure_image_sets_cache_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    image_path = tmp_path / "cacheable-figure.png"
    image_path.write_bytes(b"figure-image")
    figure_id = str(uuid4())

    with session_scope() as session:
        session.add(
            ImageAnalysis(
                id=figure_id,
                paper_id=paper_id,
                page_number=1,
                image_index=0,
                image_type="figure",
                caption="Figure Cache",
                description="cache test",
                image_path=str(image_path),
            )
        )
        session.flush()

    client = TestClient(_build_app())
    response = client.get(f"/papers/{paper_id}/figures/{figure_id}/image")

    assert response.status_code == 200
    assert (
        response.headers.get("cache-control")
        == f"public, max-age={papers_router._FIGURE_IMAGE_CACHE_TTL_SEC}, immutable"
    )


def test_reader_query_figure_falls_back_to_text_context_when_vision_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"fake-image")
    figure_id = str(uuid4())

    with session_scope() as session:
        session.add(
            ImageAnalysis(
                id=figure_id,
                paper_id=paper_id,
                page_number=6,
                image_index=0,
                image_type="figure",
                caption="Figure 6",
                description="Shows an improving trend over time",
                image_path=str(image_path),
            )
        )
        session.flush()

    captured: dict[str, str] = {}

    def _fake_vision(self, image_base64, prompt, stage="vision", max_tokens=1024):
        captured["vision_prompt"] = prompt
        return LLMResult(
            content="当前视觉模型不可用：openai / gpt-5.4 / gmncode.com。上游视觉服务返回 502 Bad Gateway。请在系统设置中把“视觉模型”切换到支持图片输入的提供方或模型。"
        )

    def _fake_summary(self, prompt, stage, max_tokens=None):
        captured["summary_prompt"] = prompt
        return LLMResult(content="基于题注和已有解析的图表总结")

    monkeypatch.setattr(papers_router.LLMClient, "vision_analyze", _fake_vision)
    monkeypatch.setattr(papers_router.LLMClient, "summarize_text", _fake_summary)

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/query",
        json={
            "scope": "figure",
            "action": "summarize",
            "figure_id": figure_id,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"] == "基于题注和已有解析的图表总结"
    assert payload["figure_id"] == figure_id
    assert (
        "当前视觉模型不可用" in captured["vision_prompt"]
        or "附加上下文" in captured["vision_prompt"]
    )
    assert "Figure 6" in captured["summary_prompt"]
    assert "已有解析" in captured["summary_prompt"]


def test_reader_query_paper_falls_back_when_pdf_prepare_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper(
        metadata={
            "analysis_rounds": {
                "round_1": {
                    "title": "第一轮",
                    "markdown": "Round one analysis",
                }
            },
        }
    )
    captured: dict[str, str] = {}

    def _fake_summarize(self, prompt, stage, max_tokens=None):
        captured["prompt"] = prompt
        return LLMResult(content="全文总结结果")

    monkeypatch.setattr(
        papers_router,
        "_ensure_paper_pdf",
        lambda session, repo, paper, pid: (_ for _ in ()).throw(RuntimeError("pdf missing")),
    )
    monkeypatch.setattr(
        papers_router.LLMClient,
        "summarize_text",
        _fake_summarize,
    )

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/query",
        json={
            "scope": "paper",
            "action": "summarize",
        },
    )

    assert response.status_code == 200
    assert response.json()["result"] == "全文总结结果"
    assert "Round one analysis" in captured["prompt"]


def test_serve_pdf_resolves_relative_pdf_path_outside_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    data_dir = tmp_path / "runtime-data"
    pdf_dir = data_dir / "papers"
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "relative-paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n%test pdf\n")

    paper_id = str(uuid4())
    with session_scope() as session:
        session.add(
            Paper(
                id=paper_id,
                arxiv_id="paper-relative-path",
                title="Relative PDF Paper",
                abstract="Abstract",
                pdf_path="data/papers/relative-paper.pdf",
                metadata_json={},
            )
        )
        session.flush()

    sandbox_cwd = tmp_path / "other-cwd"
    sandbox_cwd.mkdir(parents=True)
    monkeypatch.chdir(sandbox_cwd)
    monkeypatch.setattr(
        papers_router,
        "get_settings",
        lambda: SimpleNamespace(pdf_storage_root=pdf_dir),
    )

    client = TestClient(_build_app())
    response = client.get(f"/papers/{paper_id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF-1.4")


def test_reader_notes_crud(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    client = TestClient(_build_app())

    create_response = client.put(
        f"/papers/{paper_id}/reader/notes",
        json={
            "kind": "text",
            "title": "关键段落",
            "content": "这里记录我的理解",
            "quote": "original selection",
            "page_number": 4,
            "tags": ["method", "important"],
            "pinned": True,
            "source": "ai_draft",
            "anchor_source": "ocr_block",
            "anchor_id": "block_12",
            "section_id": "section_3",
            "section_title": "3 Method",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()["item"]
    assert created["title"] == "关键段落"
    assert created["pinned"] is True
    assert created["status"] == "saved"
    assert created["source"] == "ai_draft"
    assert created["anchor_source"] == "ocr_block"
    assert created["anchor_id"] == "block_12"
    assert created["section_id"] == "section_3"
    assert created["section_title"] == "3 Method"

    list_response = client.get(f"/papers/{paper_id}/reader/notes")
    assert list_response.status_code == 200
    assert len(list_response.json()["items"]) == 1

    delete_response = client.delete(f"/papers/{paper_id}/reader/notes/{created['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["items"] == []


def test_reader_document_returns_structured_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 structured")
    output_root = tmp_path / "ocr"
    output_root.mkdir(parents=True)
    content_list_path = output_root / "paper_content_list.json"
    content_list_path.write_text(
        json.dumps(
            [
                {
                    "type": "text",
                    "text": "Abstract",
                    "text_level": 1,
                    "page_idx": 0,
                    "bbox": [120, 180, 260, 220],
                },
                {
                    "type": "text",
                    "text": "This paper studies a compact multimodal encoder.",
                    "page_idx": 0,
                    "bbox": [120, 230, 860, 320],
                },
                {
                    "type": "text",
                    "text": "1 Method",
                    "text_level": 1,
                    "page_idx": 1,
                    "bbox": [120, 160, 320, 200],
                },
                {
                    "type": "list",
                    "list_items": ["contrastive alignment", "fusion decoder"],
                    "page_idx": 1,
                    "bbox": [140, 220, 520, 320],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        papers_router,
        "_ensure_paper_pdf",
        lambda session, repo, paper, pid: str(pdf_path),
    )
    monkeypatch.setattr(
        "packages.ai.paper.mineru_runtime.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            output_root=output_root,
            markdown_text="# Abstract\n\nThis paper studies a compact multimodal encoder.",
            has_structured_output=True,
            available=True,
        ),
    )

    client = TestClient(_build_app())
    response = client.get(f"/papers/{paper_id}/reader/document")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["source"] == "mineru_structured"
    assert len(payload["sections"]) >= 2
    assert payload["sections"][0]["title"] == "Abstract"
    assert payload["blocks"][0]["type"] == "heading"
    assert payload["blocks"][1]["section_id"] == payload["sections"][0]["id"]
    assert payload["blocks"][1]["bbox_normalized"] is True
    assert payload["blocks"][1]["bbox"]["width"] > 0
    assert payload["blocks"][-1]["type"] == "list"


def test_reader_document_falls_back_to_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 markdown")

    monkeypatch.setattr(
        papers_router,
        "_ensure_paper_pdf",
        lambda session, repo, paper, pid: str(pdf_path),
    )
    monkeypatch.setattr(
        "packages.ai.paper.mineru_runtime.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            output_root=tmp_path / "missing-structured",
            markdown_text="# Abstract\n\nA concise summary.\n\n## Method\n\nThe model uses two encoders.",
            has_structured_output=False,
            available=True,
        ),
    )

    client = TestClient(_build_app())
    response = client.get(f"/papers/{paper_id}/reader/document")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["source"] == "mineru_markdown"
    assert payload["sections"][0]["title"] == "Abstract"
    assert payload["blocks"][0]["type"] == "heading"
    assert any(block["text"] == "The model uses two encoders." for block in payload["blocks"])


def test_reader_document_uses_output_root_metadata_when_cached_bundle_misses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    output_root = tmp_path / "ocr-output"
    output_root.mkdir(parents=True)
    images_dir = output_root / "images"
    images_dir.mkdir()
    (images_dir / "fig.png").write_bytes(b"fake-image")
    (output_root / "paper.md").write_text(
        "# Abstract\n\nRecovered from output_root metadata.\n\n![](images/fig.png)",
        encoding="utf-8",
    )
    (output_root / "manifest.json").write_text(
        json.dumps(
            {"status": "success", "pdf_sha256": "abc123", "output_root": str(output_root)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    paper_id = _create_paper(
        metadata={
            "mineru_ocr": {
                "status": "success",
                "output_root": str(output_root),
                "pdf_sha256": "abc123",
                "markdown_chars": 38,
                "has_structured_output": False,
            }
        }
    )
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 metadata-fallback")

    monkeypatch.setattr(
        papers_router,
        "_ensure_paper_pdf",
        lambda session, repo, paper, pid: str(pdf_path),
    )
    monkeypatch.setattr(
        "packages.ai.paper.mineru_runtime.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: None,
    )

    client = TestClient(_build_app())
    response = client.get(f"/papers/{paper_id}/reader/document")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["source"] == "mineru_markdown"
    assert payload["markdown"].startswith("# Abstract")
    assert f"/papers/{paper_id}/ocr/assets/images/fig.png" in payload["markdown"]

    asset_response = client.get(f"/papers/{paper_id}/ocr/assets/images/fig.png")
    assert asset_response.status_code == 200
    assert asset_response.content == b"fake-image"


def test_reader_ocr_asset_resolves_relative_to_markdown_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_test_db(monkeypatch)
    output_root = tmp_path / "ocr-output"
    auto_dir = output_root / "paper.pdf" / "auto"
    images_dir = auto_dir / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "fig.png").write_bytes(b"nested-image")
    auto_dir.joinpath("paper.md").write_text(
        "# Figure\n\n![](images/fig.png)",
        encoding="utf-8",
    )
    output_root.joinpath("manifest.json").write_text(
        json.dumps(
            {"status": "success", "pdf_sha256": "abc123", "output_root": str(output_root)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    paper_id = _create_paper(
        metadata={
            "mineru_ocr": {
                "status": "success",
                "output_root": str(output_root),
                "pdf_sha256": "abc123",
                "has_structured_output": False,
            }
        }
    )
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 nested-asset")

    monkeypatch.setattr(
        papers_router,
        "_ensure_paper_pdf",
        lambda session, repo, paper, pid: str(pdf_path),
    )
    monkeypatch.setattr(
        "packages.ai.paper.mineru_runtime.MinerUOcrRuntime.get_cached_bundle",
        lambda *args, **kwargs: None,
    )

    client = TestClient(_build_app())
    document_response = client.get(f"/papers/{paper_id}/reader/document")
    assert document_response.status_code == 200
    assert f"/papers/{paper_id}/ocr/assets/images/fig.png" in document_response.json()["markdown"]

    asset_response = client.get(f"/papers/{paper_id}/ocr/assets/images/fig.png")
    assert asset_response.status_code == 200
    assert asset_response.content == b"nested-image"


def test_reader_structured_visual_uses_generic_image_alt() -> None:
    paper_id = uuid4()
    caption = "Fig. 1. Detailed caption"
    block_type, text, markdown = papers_router._resolve_reader_structured_visual_block(
        {
            "type": "image",
            "image_caption": [caption],
            "img_path": "images/fig.png",
        },
        paper_id,
    )

    assert block_type == "image"
    assert text == caption
    assert markdown.startswith(f"![figure](/papers/{paper_id}/ocr/assets/images/fig.png)")
    assert markdown.count(caption) == 1


def test_reader_note_draft_returns_unsaved_ai_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()

    monkeypatch.setattr(
        papers_router.LLMClient,
        "complete_json",
        lambda self, prompt, stage, max_tokens=None, max_retries=1: LLMResult(
            content='{"title":"方法抓手","content":"- 提出了新的融合策略\\n- 需要核实训练细节","tags":["method","todo"],"color":"blue"}',
            parsed_json={
                "title": "方法抓手",
                "content": "- 提出了新的融合策略\n- 需要核实训练细节",
                "tags": ["method", "todo"],
                "color": "blue",
            },
        ),
    )

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/note-draft",
        json={
            "text": "The model aligns image and text representations before fusion.",
            "quote": "aligns image and text representations before fusion",
            "page_number": 5,
            "anchor_source": "ocr_block",
            "anchor_id": "block_9",
            "section_id": "section_2",
            "section_title": "2 Method",
        },
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["status"] == "draft"
    assert item["source"] == "ai_draft"
    assert item["title"] == "方法抓手"
    assert item["color"] == "blue"
    assert item["anchor_source"] == "ocr_block"
    assert item["anchor_id"] == "block_9"
    assert item["section_id"] == "section_2"


def test_reader_note_draft_falls_back_to_structured_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        papers_router.LLMClient,
        "complete_json",
        lambda self, prompt, stage, max_tokens=None, max_retries=1: LLMResult(
            content="",
            parsed_json=None,
        ),
    )

    def _fake_summarize(self, prompt, stage, max_tokens=None, **kwargs):
        captured["stage"] = stage
        return LLMResult(
            content=(
                "标题：结果对比线索\n"
                "标签：实验, 结果\n"
                "颜色：emerald\n"
                "正文：\n"
                "- 该段给出了跨任务的对比结果线索。\n"
                "- 需要结合 Table 2 继续核对具体指标。"
            )
        )

    monkeypatch.setattr(papers_router.LLMClient, "summarize_text", _fake_summarize)

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/note-draft",
        json={
            "text": "We report the detailed comparison on each task in Table 2.",
            "quote": "We report the detailed comparison on each task in Table 2.",
            "page_number": 9,
            "anchor_source": "pdf_selection",
        },
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert captured["stage"] == "paper_reader_note_draft_fallback"
    assert item["title"] == "结果对比线索"
    assert item["color"] == "emerald"
    assert item["content"].startswith("- 该段给出了跨任务的对比结果线索。")
    assert item["tags"] == ["实验", "结果"]


def test_reader_note_draft_returns_503_when_model_service_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id = _create_paper()

    monkeypatch.setattr(
        papers_router.LLMClient,
        "complete_json",
        lambda self, prompt, stage, max_tokens=None, max_retries=1: LLMResult(
            content="模型服务暂不可用。(stage=paper_reader_note_draft, provider=custom, model=gpt-5.4) 请稍后重试或检查 API 配置。",
            parsed_json=None,
        ),
    )
    monkeypatch.setattr(
        papers_router.LLMClient,
        "summarize_text",
        lambda self, prompt, stage, max_tokens=None, **kwargs: LLMResult(
            content="模型服务暂不可用。(stage=paper_reader_note_draft_fallback, provider=custom, model=gpt-5.4) 请稍后重试或检查 API 配置。",
        ),
    )

    client = TestClient(_build_app())
    response = client.post(
        f"/papers/{paper_id}/reader/note-draft",
        json={
            "text": "We report the detailed comparison on each task in Table 2.",
            "quote": "We report the detailed comparison on each task in Table 2.",
            "page_number": 9,
            "anchor_source": "pdf_selection",
        },
    )

    assert response.status_code == 503
    assert "模型服务暂不可用" in response.json()["detail"]
