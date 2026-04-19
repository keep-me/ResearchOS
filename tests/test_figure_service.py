import base64
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import packages.ai.paper.figure_service as figure_service_module
from packages.ai.paper.figure_service import ExtractedFigure, FigureService


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO0p2uoAAAAASUVORK5CYII="
)


def test_resolve_extract_mode_supports_mineru_alias_and_defaults():
    assert FigureService._resolve_extract_mode("pdf_direct") == "mineru"
    assert FigureService._resolve_extract_mode("mineru") == "mineru"
    assert FigureService._resolve_extract_mode("legacy_mode") == "arxiv_source"


def test_compose_ocr_candidate_markdown_ignores_caption_only_payload():
    caption = "Figure 1. Overview of the system."
    assert FigureService._compose_ocr_candidate_markdown(caption, caption) == ""


def test_compose_ocr_candidate_markdown_converts_html_table_to_markdown():
    markdown = FigureService._compose_ocr_candidate_markdown(
        "Table 1. Main benchmark results.",
        "<table><tr><td>Method</td><td>MME</td></tr><tr><td>GPT-4o</td><td>69.1</td></tr></table>",
    )

    assert "Table 1. Main benchmark results." in markdown
    assert "| Method | MME |" in markdown
    assert "| GPT-4o | 69.1 |" in markdown
    assert "<table>" not in markdown


def test_description_payload_roundtrip_preserves_candidate_source():
    encoded = FigureService._encode_description_payload(
        ocr_markdown="structured text",
        analysis_markdown="## 核心内容\n已分析",
        candidate_source="mineru_structured",
    )

    decoded = FigureService._decode_description_payload(encoded)

    assert decoded["ocr_markdown"] == "structured text"
    assert decoded["analysis_markdown"] == "## 核心内容\n已分析"
    assert decoded["candidate_source"] == "mineru_structured"


def test_normalize_stored_candidate_fields_drops_legacy_caption_only_ocr():
    normalized = FigureService._normalize_stored_candidate_fields(
        caption="Figure 2. Legacy caption only.",
        ocr_markdown="Figure 2. Legacy caption only.",
        analysis_markdown="",
        candidate_source="",
    )

    assert normalized["ocr_markdown"] == ""
    assert normalized["analysis_markdown"] == ""


def test_find_captions_collects_supported_labels():
    text = """
    Figure 1. Main architecture.
    Table 1. Results on benchmark.
    Algorithm 1: Search procedure.
    """
    captions = FigureService._find_captions(text)
    assert captions == [
        "Figure 1. Main architecture.",
        "Table 1. Results on benchmark.",
        "Algorithm 1: Search procedure.",
    ]


def test_match_pdf_caption_prefers_same_type():
    entries = [
        {
            "caption": "Table 4. Comparison with the human fixation on COCO-Search18.",
            "label": "table 4",
            "page_number": 8,
            "type": "table",
            "body": FigureService._normalize_caption_body(
                "Table 4. Comparison with the human fixation on COCO-Search18."
            ),
        },
        {
            "caption": "Figure 6. Comparison with the human fixation on COCO-Search18.",
            "label": "figure 6",
            "page_number": 8,
            "type": "figure",
            "body": FigureService._normalize_caption_body(
                "Figure 6. Comparison with the human fixation on COCO-Search18."
            ),
        },
    ]
    matched = FigureService._match_pdf_caption(
        "Comparison with the human fixation on COCO-Search18.",
        entries,
    )
    assert matched is not None
    assert matched["label"] == "figure 6"


def test_collect_source_candidates_extracts_figure_and_eps_references(tmp_path):
    (tmp_path / "fig1.png").write_bytes(PNG_1X1)
    (tmp_path / "fig01.eps").write_text("%!PS-Adobe-3.0 EPSF-3.0", encoding="utf-8")
    (tmp_path / "main.tex").write_text(
        r"""
        \begin{figure}
        \centering
        \includegraphics[width=\linewidth]{fig1}
        \caption{A compact source-first figure.}
        \end{figure}
        \begin{figure}
        \begin{center}
        \epsfig{file=fig01.eps,height=4cm}
        \end{center}
        \caption{Legacy EPS figure from source.}
        \end{figure}
        """,
        encoding="utf-8",
    )

    candidates = FigureService._collect_source_candidates(tmp_path)

    assert len(candidates) == 2
    assert candidates[0].caption == "A compact source-first figure."
    assert candidates[0].image_paths == [tmp_path / "fig1.png"]
    assert candidates[1].caption == "Legacy EPS figure from source."
    assert candidates[1].image_paths == [tmp_path / "fig01.eps"]


def test_materialize_figure_image_writes_png(tmp_path):
    fig_dir = tmp_path / "figures"
    fig_dir.mkdir()

    result = FigureService._materialize_figure_image(
        ExtractedFigure(
            page_number=1,
            image_index=0,
            image_bytes=PNG_1X1,
            image_type="figure",
            caption="Figure 1. Example.",
            bbox=None,
        ),
        fig_dir,
    )

    assert result.exists()
    assert result.read_bytes() == PNG_1X1


def test_extract_figures_returns_empty_when_arxiv_and_mineru_are_unavailable(monkeypatch, tmp_path):
    service = FigureService.__new__(FigureService)
    service.llm = None
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        FigureService,
        "_extract_via_arxiv_source",
        classmethod(lambda _cls, *args, **kwargs: []),
    )
    monkeypatch.setattr(FigureService, "_extract_via_mineru_tables", classmethod(lambda _cls, *args, **kwargs: []))

    results = service.extract_figures(uuid4(), str(pdf_path), arxiv_id="1234.5678")

    assert results == []


def test_extract_figures_arxiv_mode_does_not_fallback_to_mineru(monkeypatch, tmp_path):
    service = FigureService.__new__(FigureService)
    service.llm = None
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        FigureService,
        "_extract_via_arxiv_source",
        classmethod(lambda _cls, *args, **kwargs: []),
    )
    monkeypatch.setattr(FigureService, "_extract_via_mineru_tables", classmethod(lambda _cls, *args, **kwargs: []))

    def _unexpected_mineru_call(_cls, *args, **kwargs):
        raise AssertionError("MinerU should not be called when arxiv_source extraction fails")

    monkeypatch.setattr(FigureService, "_extract_via_mineru", classmethod(_unexpected_mineru_call))

    results = service.extract_figures(
        uuid4(),
        str(pdf_path),
        arxiv_id="1234.5678",
        extract_mode="arxiv_source",
    )

    assert results == []


def test_extract_figures_arxiv_mode_combines_arxiv_figures_with_ocr_tables(monkeypatch, tmp_path):
    service = FigureService.__new__(FigureService)
    service.llm = None
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        FigureService,
        "_extract_via_arxiv_source",
        classmethod(
            lambda _cls, *args, **kwargs: [
                ExtractedFigure(
                    page_number=2,
                    image_index=0,
                    image_bytes=PNG_1X1,
                    image_type="figure",
                    caption="Figure 2. Architecture overview.",
                    bbox=None,
                ),
                ExtractedFigure(
                    page_number=3,
                    image_index=0,
                    image_bytes=PNG_1X1,
                    image_type="table",
                    caption="Table 1. Source-side table image.",
                    bbox=None,
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        FigureService,
        "_extract_via_mineru_tables",
        classmethod(
            lambda _cls, *args, **kwargs: [
                ExtractedFigure(
                    page_number=1,
                    image_index=0,
                    image_bytes=PNG_1X1,
                    image_type="table",
                    caption="Table 1. OCR table.",
                    bbox=None,
                    content_markdown="| A | B |",
                )
            ]
        ),
    )

    results = service.extract_figures(
        uuid4(),
        str(pdf_path),
        arxiv_id="1234.5678",
        extract_mode="arxiv_source",
    )

    assert len(results) == 2
    assert [(item.page_number, item.caption) for item in results] == [
        (1, "Table 1. OCR table."),
        (2, "Figure 2. Architecture overview."),
    ]


def test_extract_figures_mineru_mode_returns_empty_without_mineru_outputs(monkeypatch, tmp_path):
    service = FigureService.__new__(FigureService)
    service.llm = None
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(FigureService, "_extract_via_mineru", classmethod(lambda _cls, *args, **kwargs: []))

    results = service.extract_figures(uuid4(), str(pdf_path), extract_mode="mineru")

    assert results == []


def test_collect_mineru_structured_blocks_reads_middle_json(tmp_path):
    payload = {
        "pdf_info": [
            {
                "page_idx": 0,
                "images": [
                    {
                        "type": "image",
                        "bbox": [10, 20, 210, 220],
                        "blocks": [
                            {
                                "type": "image_caption",
                                "lines": [
                                    {
                                        "spans": [
                                            {"type": "text", "content": "Figure 1. Overview of the system."},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "tables": [
                    {
                        "type": "table",
                        "bbox": [30, 40, 230, 260],
                        "blocks": [
                            {
                                "type": "table_caption",
                                "lines": [
                                    {
                                        "spans": [
                                            {"type": "text", "content": "Table 1. Main benchmark results."},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    (tmp_path / "sample_middle.json").write_text(json.dumps(payload), encoding="utf-8")

    blocks = FigureService._collect_mineru_structured_blocks(tmp_path)

    assert len(blocks) == 2
    assert {block["page_number"] for block in blocks} == {1}
    assert {block["image_type"] for block in blocks} == {"figure", "table"}
    assert {block["caption"] for block in blocks} == {
        "Figure 1. Overview of the system.",
        "Table 1. Main benchmark results.",
    }
    assert all(block["normalized_bbox"] is False for block in blocks)


def test_collect_mineru_structured_blocks_falls_back_to_content_list(tmp_path):
    payload = [
        {
            "type": "table",
            "page_idx": 2,
            "bbox": [100, 120, 900, 960],
            "table_caption": ["Table 2. Ablation study."],
        }
    ]
    (tmp_path / "sample_content_list.json").write_text(json.dumps(payload), encoding="utf-8")

    blocks = FigureService._collect_mineru_structured_blocks(tmp_path)

    assert len(blocks) == 1
    assert blocks[0]["page_number"] == 3
    assert blocks[0]["image_type"] == "table"
    assert blocks[0]["caption"] == "Table 2. Ablation study."
    assert blocks[0]["normalized_bbox"] is True


def test_collect_mineru_structured_blocks_keeps_original_split_figure_blocks(tmp_path):
    payload = [
        {
            "type": "text",
            "page_idx": 0,
            "bbox": [210, 150, 790, 280],
            "text": "",
        }
    ]
    for row in range(4):
        for col in range(6):
            x0 = 220 + col * 94
            y0 = 320 + row * 102
            payload.append(
                {
                    "type": "image",
                    "page_idx": 0,
                    "bbox": [x0, y0, x0 + 88, y0 + 70],
                    "image_caption": ["Current Image"] if row == 0 and col == 0 else None,
                }
            )
    payload.append(
        {
            "type": "text",
            "page_idx": 0,
            "bbox": [214, 780, 787, 838],
            "text": "Fig. 5: Qualitative comparison on the text-guided forecasting task.",
        }
    )
    (tmp_path / "sample_content_list.json").write_text(json.dumps(payload), encoding="utf-8")

    blocks = FigureService._collect_mineru_structured_blocks(tmp_path)

    assert len(blocks) == 24
    assert all(block["image_type"] == "figure" for block in blocks)
    assert not any(block["caption"].startswith("Fig. 5:") for block in blocks)
    assert any(block["caption"] == "Current Image" for block in blocks)


def test_collect_mineru_structured_blocks_does_not_merge_image_run_without_figure_caption(tmp_path):
    payload = []
    for row in range(2):
        for col in range(2):
            x0 = 120 + col * 150
            y0 = 220 + row * 150
            payload.append(
                {
                    "type": "image",
                    "page_idx": 0,
                    "bbox": [x0, y0, x0 + 100, y0 + 100],
                    "image_caption": [f"panel {row}-{col}"],
                }
            )
    payload.append(
        {
            "type": "text",
            "page_idx": 0,
            "bbox": [118, 540, 520, 590],
            "text": "Qualitative comparison across different prompts.",
        }
    )
    (tmp_path / "sample_content_list.json").write_text(json.dumps(payload), encoding="utf-8")

    blocks = FigureService._collect_mineru_structured_blocks(tmp_path)

    assert len(blocks) == 4
    assert all(block["caption"].startswith("panel ") for block in blocks)


def test_prune_nested_mineru_blocks_prefers_whole_figure():
    blocks = [
        {
            "page_number": 1,
            "bbox": [0, 0, 400, 400],
            "normalized_bbox": False,
            "image_type": "figure",
            "caption": "Figure 1. Whole pipeline.",
        },
        {
            "page_number": 1,
            "bbox": [40, 60, 180, 200],
            "normalized_bbox": False,
            "image_type": "figure",
            "caption": "",
        },
        {
            "page_number": 1,
            "bbox": [420, 80, 700, 320],
            "normalized_bbox": False,
            "image_type": "table",
            "caption": "Table 1. Main results.",
        },
    ]

    pruned = FigureService._prune_nested_mineru_blocks(blocks)

    assert len(pruned) == 2
    assert any(item["caption"] == "Figure 1. Whole pipeline." for item in pruned)
    assert any(item["caption"] == "Table 1. Main results." for item in pruned)


def test_extract_via_mineru_uses_cached_runtime_bundle(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    output_root = tmp_path / "mineru-runtime-out"
    output_root.mkdir()
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        figure_service_module.MinerUOcrRuntime,
        "get_cached_bundle",
        classmethod(
            lambda cls, paper_id, current_pdf_path: (
                seen.update(
                    {
                        "paper_id": paper_id,
                        "pdf_path": current_pdf_path,
                    }
                )
                or SimpleNamespace(output_root=output_root)
            )
        ),
    )
    monkeypatch.setattr(
        FigureService,
        "_has_mineru_structured_outputs",
        classmethod(lambda cls, root: root == output_root),
    )
    monkeypatch.setattr(
        FigureService,
        "_extract_via_mineru_structured",
        classmethod(
            lambda cls, **kwargs: [
                ExtractedFigure(
                    page_number=1,
                    image_index=0,
                    image_bytes=PNG_1X1,
                    image_type="figure",
                    caption="Figure 1. Runtime figure.",
                    bbox=None,
                    content_markdown="OCR content",
                )
            ]
        ),
    )
    result = FigureService._extract_via_mineru(uuid4(), str(pdf_path), 6, set(), allow_generate=False)

    assert len(result) == 1
    assert result[0].caption == "Figure 1. Runtime figure."
    assert seen["pdf_path"] == str(pdf_path)


def test_extract_paper_figure_candidates_keeps_all_candidates(monkeypatch, tmp_path):
    service = FigureService.__new__(FigureService)
    service.llm = None

    figures = [
        ExtractedFigure(
            page_number=1,
            image_index=0,
            image_bytes=PNG_1X1,
            image_type="figure",
            caption="Figure 1. Proposed framework overview.",
            bbox=None,
        ),
        ExtractedFigure(
            page_number=2,
            image_index=0,
            image_bytes=PNG_1X1,
            image_type="figure",
            caption="Figure 2. Remote sensing image examples.",
            bbox=None,
        ),
    ]
    saved: list = []

    monkeypatch.setattr(service, "extract_figures", lambda *args, **kwargs: figures)
    monkeypatch.setattr(FigureService, "_ensure_figure_dir", staticmethod(lambda _paper_id: tmp_path))
    monkeypatch.setattr(FigureService, "_save_analyses", staticmethod(lambda _paper_id, analyses: saved.extend(analyses)))

    result = service.extract_paper_figure_candidates(uuid4(), str(tmp_path / "paper.pdf"))

    assert len(result) == 2
    assert [item.caption for item in saved] == [
        "Figure 1. Proposed framework overview.",
        "Figure 2. Remote sensing image examples.",
    ]


def test_get_paper_analyses_returns_duplicates_and_analyzed_flag(monkeypatch, tmp_path):
    image_path = tmp_path / "candidate.png"
    image_path.write_bytes(PNG_1X1)
    rows = [
        SimpleNamespace(
            id="fig-1",
            page_number=1,
            image_index=0,
            image_type="figure",
            caption="Figure 1. Example figure.",
            description=FigureService._encode_description_payload(
                ocr_markdown="Figure 1. OCR candidate",
                analysis_markdown="",
            ),
            image_path=str(image_path),
            bbox_json=None,
        ),
        SimpleNamespace(
            id="fig-2",
            page_number=1,
            image_index=1,
            image_type="figure",
            caption="Figure 1. Example figure.",
            description=FigureService._encode_description_payload(
                ocr_markdown="Figure 1. OCR candidate",
                analysis_markdown="## 图表类型\n已分析内容",
            ),
            image_path=str(image_path),
            bbox_json=None,
        ),
    ]

    class FakeScalarResult:
        def __init__(self, payload):
            self.payload = payload

        def all(self):
            return self.payload

    class FakeExecuteResult:
        def __init__(self, payload):
            self.payload = payload

        def scalars(self):
            return FakeScalarResult(self.payload)

    class FakeSession:
        def execute(self, _query):
            return FakeExecuteResult(rows)

    @contextmanager
    def fake_session_scope():
        yield FakeSession()

    monkeypatch.setattr(figure_service_module, "session_scope", fake_session_scope)
    monkeypatch.setattr(FigureService, "resolve_stored_image_path", staticmethod(lambda _path: image_path))

    items = FigureService.get_paper_analyses(uuid4())

    assert len(items) == 2
    assert [item["id"] for item in items] == ["fig-1", "fig-2"]
    assert items[0]["analyzed"] is False
    assert items[1]["analyzed"] is True
    assert items[0]["ocr_markdown"] == "Figure 1. OCR candidate"
    assert items[1]["analysis_markdown"] == "## 图表类型\n已分析内容"
