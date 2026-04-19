from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from packages.ai.paper.mineru_runtime import MinerUOcrRuntime


def _patch_runtime_paths(monkeypatch, tmp_path):
    base_dir = tmp_path / "MinerU"
    runtime_dir = base_dir / "runtime"
    cache_dir = base_dir / "cache"
    models_dir = base_dir / "models"
    config_path = base_dir / "mineru.local.json"
    for path in (runtime_dir, cache_dir, models_dir):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(MinerUOcrRuntime, "base_dir", classmethod(lambda cls: base_dir))
    monkeypatch.setattr(MinerUOcrRuntime, "runtime_dir", classmethod(lambda cls: runtime_dir))
    monkeypatch.setattr(MinerUOcrRuntime, "cache_dir", classmethod(lambda cls: cache_dir))
    monkeypatch.setattr(MinerUOcrRuntime, "models_dir", classmethod(lambda cls: models_dir))
    monkeypatch.setattr(MinerUOcrRuntime, "local_config_path", classmethod(lambda cls: config_path))
    return SimpleNamespace(
        base_dir=base_dir,
        runtime_dir=runtime_dir,
        cache_dir=cache_dir,
        models_dir=models_dir,
        config_path=config_path,
    )


def test_mineru_runtime_base_dir_prefers_env(monkeypatch, tmp_path):
    custom_dir = tmp_path / "custom-mineru"
    monkeypatch.setenv("RESEARCHOS_MINERU_DIR", str(custom_dir))

    assert MinerUOcrRuntime.base_dir() == custom_dir.resolve()


def test_mineru_runtime_resolve_device_mode_prefers_env_override(monkeypatch):
    monkeypatch.setenv("MINERU_DEVICE_MODE", "cpu")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
    )

    assert MinerUOcrRuntime._resolve_device_mode() == "cpu"


def test_mineru_runtime_resolve_device_mode_prefers_cuda_when_available(monkeypatch):
    monkeypatch.delenv("MINERU_DEVICE_MODE", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
    )

    assert MinerUOcrRuntime._resolve_device_mode() == "cuda"


def test_mineru_runtime_resolve_device_mode_falls_back_to_cpu(monkeypatch):
    monkeypatch.delenv("MINERU_DEVICE_MODE", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
    )

    assert MinerUOcrRuntime._resolve_device_mode() == "cpu"


def test_mineru_runtime_prepare_runtime_downloads_missing_models(monkeypatch, tmp_path):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(MinerUOcrRuntime, "_pipeline_model_dir_from_home_config", classmethod(lambda cls: None))
    download_calls: list[tuple[str, str]] = []
    progress_messages: list[str] = []

    def _fake_modelscope_download(repo_id, local_dir, **kwargs):
        del kwargs
        download_calls.append((repo_id, local_dir))
        target = Path(local_dir)
        (target / "models").mkdir(parents=True, exist_ok=True)
        (target / ".mdl").write_text("ok", encoding="utf-8")
        (target / ".msc").write_text("ok", encoding="utf-8")
        (target / ".mv").write_text("ok", encoding="utf-8")
        return str(target)

    monkeypatch.setitem(sys.modules, "modelscope", SimpleNamespace(snapshot_download=_fake_modelscope_download))
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not fallback"))),
    )

    model_dir = MinerUOcrRuntime.prepare_runtime(
        progress_callback=lambda message, current, total: progress_messages.append(f"{current}/{total}:{message}"),
    )

    assert model_dir == (paths.models_dir / "pipeline")
    assert (model_dir / "models").exists()
    assert download_calls == [("OpenDataLab/PDF-Extract-Kit-1.0", str(model_dir))]
    assert any("自动下载" in item for item in progress_messages)


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
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        MinerUOcrRuntime,
        "_run_installed_mineru",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(AssertionError("should not rerun"))),
    )
    monkeypatch.setattr(MinerUOcrRuntime, "_sync_paper_metadata", classmethod(lambda cls, pid, manifest: None))

    bundle = MinerUOcrRuntime.ensure_bundle(paper_id, str(pdf_path))

    assert bundle is not None
    assert bundle.available is True
    assert "cached markdown" in bundle.markdown_text


def test_mineru_runtime_force_bypasses_cached_success_bundle(monkeypatch, tmp_path):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    pipeline_model_dir = paths.models_dir / "pipeline"
    (pipeline_model_dir / "models").mkdir(parents=True, exist_ok=True)
    (pipeline_model_dir / ".mdl").write_text("ok", encoding="utf-8")
    (pipeline_model_dir / ".msc").write_text("ok", encoding="utf-8")
    (pipeline_model_dir / ".mv").write_text("ok", encoding="utf-8")
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
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        MinerUOcrRuntime,
        "_resolve_pipeline_model_dir",
        classmethod(lambda cls: pipeline_model_dir),
    )

    run_calls: list[str] = []

    def _fake_run(cls, *, pdf_path, output_root, config_path, device_mode):
        del cls, pdf_path, config_path
        run_calls.append(str(output_root))
        assert device_mode == "cuda"
        (output_root / "fresh.md").write_text("# OCR\n\nfresh markdown", encoding="utf-8")

    monkeypatch.setattr(MinerUOcrRuntime, "_run_installed_mineru", classmethod(_fake_run))
    monkeypatch.setattr(MinerUOcrRuntime, "_sync_paper_metadata", classmethod(lambda cls, pid, manifest: None))
    monkeypatch.setattr(MinerUOcrRuntime, "_resolve_device_mode", classmethod(lambda cls: "cuda"))

    bundle = MinerUOcrRuntime.ensure_bundle(paper_id, str(pdf_path), force=True)

    assert bundle is not None
    assert bundle.available is True
    assert "fresh markdown" in bundle.markdown_text
    assert run_calls == [str(output_root)]


def test_mineru_runtime_runs_local_pipeline_and_persists_manifest(monkeypatch, tmp_path):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    pipeline_model_dir = paths.models_dir / "pipeline"
    (pipeline_model_dir / "models").mkdir(parents=True, exist_ok=True)
    (pipeline_model_dir / ".mdl").write_text("ok", encoding="utf-8")
    (pipeline_model_dir / ".msc").write_text("ok", encoding="utf-8")
    (pipeline_model_dir / ".mv").write_text("ok", encoding="utf-8")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 generated")
    paper_id = uuid4()
    synced: dict[str, object] = {}

    monkeypatch.setattr(
        MinerUOcrRuntime,
        "_resolve_pipeline_model_dir",
        classmethod(lambda cls: pipeline_model_dir),
    )

    def _fake_run(cls, *, pdf_path, output_root, config_path, device_mode):
        del cls, pdf_path
        (output_root / "bundle").mkdir(parents=True, exist_ok=True)
        (output_root / "bundle" / "paper.md").write_text("# OCR\n\nfresh markdown", encoding="utf-8")
        (output_root / "bundle" / "paper_content_list.json").write_text("[]", encoding="utf-8")
        assert config_path == paths.config_path
        assert device_mode == "cuda"

    monkeypatch.setattr(MinerUOcrRuntime, "_run_installed_mineru", classmethod(_fake_run))
    monkeypatch.setattr(
        MinerUOcrRuntime,
        "_sync_paper_metadata",
        classmethod(lambda cls, pid, manifest: synced.update({"paper_id": pid, "manifest": dict(manifest)})),
    )
    monkeypatch.setattr(MinerUOcrRuntime, "_resolve_device_mode", classmethod(lambda cls: "cuda"))

    bundle = MinerUOcrRuntime.ensure_bundle(paper_id, str(pdf_path), force=True)

    assert bundle is not None
    assert bundle.available is True
    assert "fresh markdown" in bundle.markdown_text
    assert bundle.has_structured_output is True
    assert paths.config_path.exists()
    manifest_path = bundle.output_root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["device_mode"] == "cuda"
    assert synced["paper_id"] == paper_id
