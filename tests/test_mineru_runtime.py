from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from packages.ai.paper.mineru_runtime import MinerUOcrRuntime


def _patch_runtime_paths(monkeypatch, tmp_path):
    base_dir = tmp_path / "MinerU"
    runtime_dir = base_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(MinerUOcrRuntime, "base_dir", classmethod(lambda cls: base_dir))
    monkeypatch.setattr(MinerUOcrRuntime, "runtime_dir", classmethod(lambda cls: runtime_dir))
    return SimpleNamespace(base_dir=base_dir, runtime_dir=runtime_dir)


def test_mineru_runtime_uses_repo_runtime_dir(tmp_path, monkeypatch):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    assert MinerUOcrRuntime.base_dir() == paths.base_dir
    assert MinerUOcrRuntime.runtime_dir() == paths.runtime_dir


def test_mineru_runtime_default_base_dir_is_project_root_mineru():
    project_root = Path(__file__).resolve().parents[1]
    assert MinerUOcrRuntime.repo_root() == project_root
    assert MinerUOcrRuntime.base_dir() == project_root / "MinerU"
    assert MinerUOcrRuntime.runtime_dir() == project_root / "MinerU" / "runtime"


def test_mineru_runtime_reads_api_settings(monkeypatch):
    monkeypatch.setenv("MINERU_API_TOKEN", "token-123")
    monkeypatch.setenv("MINERU_API_BASE_URL", "https://mineru.example.com/")
    monkeypatch.setenv("MINERU_API_MODEL_VERSION", "ocr-v2")

    assert MinerUOcrRuntime._resolve_backend() == "api"
    assert MinerUOcrRuntime._mineru_api_token() == "token-123"
    assert MinerUOcrRuntime._mineru_api_base_url() == "https://mineru.example.com"
    assert MinerUOcrRuntime._mineru_api_model_version() == "ocr-v2"


def test_mineru_runtime_requires_api_token(monkeypatch):
    monkeypatch.delenv("MINERU_API_TOKEN", raising=False)
    monkeypatch.setattr(
        "packages.ai.paper.mineru_runtime.get_settings",
        lambda: SimpleNamespace(
            mineru_api_token=None,
            mineru_api_base_url="https://mineru.net",
            mineru_api_model_version="pipeline",
            mineru_api_poll_interval_seconds=3.0,
            mineru_api_timeout_seconds=300,
            mineru_api_upload_timeout_seconds=600,
        ),
    )

    try:
        MinerUOcrRuntime._resolve_backend()
    except RuntimeError as exc:
        assert "MINERU_API_TOKEN" in str(exc)
    else:
        raise AssertionError("expected runtime error when token is missing")


def test_mineru_runtime_returns_cached_bundle_without_rerun(monkeypatch, tmp_path):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 cached")
    paper_id = uuid4()
    pdf_sha256 = MinerUOcrRuntime._hash_file(pdf_path)
    output_root = paths.runtime_dir / str(paper_id) / pdf_sha256[:16]
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "parsed.md").write_text("# OCR\n\ncached markdown", encoding="utf-8")
    manifest = {
        "paper_id": str(paper_id),
        "pdf_sha256": pdf_sha256,
        "output_root": str(output_root),
        "status": "success",
        "updated_at": "2026-04-09T12:00:00+00:00",
        "backend": "api",
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        MinerUOcrRuntime,
        "_run_remote_api_mineru",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(AssertionError("should not rerun"))),
    )
    monkeypatch.setattr(MinerUOcrRuntime, "_sync_paper_metadata", classmethod(lambda cls, pid, manifest: None))

    bundle = MinerUOcrRuntime.ensure_bundle(paper_id, str(pdf_path))

    assert bundle is not None
    assert bundle.available is True
    assert "cached markdown" in bundle.markdown_text


def test_mineru_runtime_force_bypasses_cached_success_bundle(monkeypatch, tmp_path):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 cached-force")
    paper_id = uuid4()
    pdf_sha256 = MinerUOcrRuntime._hash_file(pdf_path)
    output_root = paths.runtime_dir / str(paper_id) / pdf_sha256[:16]
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "parsed.md").write_text("# OCR\n\ncached markdown", encoding="utf-8")
    manifest = {
        "paper_id": str(paper_id),
        "pdf_sha256": pdf_sha256,
        "output_root": str(output_root),
        "status": "success",
        "updated_at": "2026-04-09T12:00:00+00:00",
        "backend": "api",
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    run_calls: list[str] = []

    def _fake_remote_run(cls, *, paper_id, pdf_path, output_root):
        del cls, paper_id, pdf_path
        run_calls.append(str(output_root))
        (output_root / "fresh.md").write_text("# OCR\n\nfresh markdown", encoding="utf-8")
        (output_root / "paper_content_list.json").write_text("[]", encoding="utf-8")
        return {
            "backend": "api",
            "mineru_api_batch_id": "batch-force",
            "mineru_api_model_version": "pipeline",
        }

    monkeypatch.setattr(MinerUOcrRuntime, "_run_remote_api_mineru", classmethod(_fake_remote_run))
    monkeypatch.setattr(MinerUOcrRuntime, "_sync_paper_metadata", classmethod(lambda cls, pid, manifest: None))
    monkeypatch.setenv("MINERU_API_TOKEN", "token-123")

    bundle = MinerUOcrRuntime.ensure_bundle(paper_id, str(pdf_path), force=True)

    assert bundle is not None
    assert bundle.available is True
    assert "fresh markdown" in bundle.markdown_text
    assert run_calls == [str(output_root)]


def test_mineru_runtime_runs_remote_api_and_persists_manifest(monkeypatch, tmp_path):
    _patch_runtime_paths(monkeypatch, tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 generated")
    paper_id = uuid4()
    synced: dict[str, object] = {}

    def _fake_remote_run(cls, *, paper_id, pdf_path, output_root):
        del cls, paper_id, pdf_path
        (output_root / "bundle").mkdir(parents=True, exist_ok=True)
        (output_root / "bundle" / "paper.md").write_text("# OCR\n\nfresh markdown", encoding="utf-8")
        (output_root / "bundle" / "paper_content_list.json").write_text("[]", encoding="utf-8")
        return {
            "backend": "api",
            "mineru_api_batch_id": "batch-123",
            "mineru_api_model_version": "pipeline",
            "mineru_api_result_url": "https://cdn.example.com/result.zip",
        }

    monkeypatch.setattr(MinerUOcrRuntime, "_run_remote_api_mineru", classmethod(_fake_remote_run))
    monkeypatch.setattr(
        MinerUOcrRuntime,
        "_sync_paper_metadata",
        classmethod(lambda cls, pid, manifest: synced.update({"paper_id": pid, "manifest": dict(manifest)})),
    )
    monkeypatch.setenv("MINERU_API_TOKEN", "token-123")

    bundle = MinerUOcrRuntime.ensure_bundle(paper_id, str(pdf_path), force=True)

    assert bundle is not None
    assert bundle.available is True
    assert "fresh markdown" in bundle.markdown_text
    assert bundle.has_structured_output is True
    manifest_path = bundle.output_root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["backend"] == "api"
    assert payload["mineru_api_batch_id"] == "batch-123"
    assert synced["paper_id"] == paper_id
