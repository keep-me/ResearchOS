from __future__ import annotations

from pathlib import Path

from packages import config
from packages.agent import claw_mcp_runtime


def test_get_settings_respects_researchos_data_dir(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "researchos-data"
    monkeypatch.setenv("RESEARCHOS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PDF_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("BRIEF_OUTPUT_ROOT", raising=False)
    config.get_settings.cache_clear()

    try:
        settings = config.get_settings()
        assert settings.pdf_storage_root == data_dir / "papers"
        assert settings.brief_output_root == data_dir / "briefs"
        assert (
            Path(settings.database_url.replace("sqlite:///", "")).resolve()
            == (data_dir / "researchos.db").resolve()
        )
    finally:
        config.get_settings.cache_clear()


def test_bridge_local_path_to_relative_maps_bridge_workspace_paths(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge-workspace"
    nested_file = bridge_dir / "src" / "main.py"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.chdir(bridge_dir)

    assert claw_mcp_runtime._bridge_local_path_to_relative(str(bridge_dir)) == ""
    assert claw_mcp_runtime._bridge_local_path_to_relative(str(nested_file)) == "src/main.py"
