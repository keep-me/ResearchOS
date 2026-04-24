from __future__ import annotations

import os
from pathlib import Path

import pytest

import packages.config as config


def test_sqlite_database_path_ignores_non_sqlite_urls() -> None:
    assert config._sqlite_database_path("postgresql://user:pass@localhost/researchos") is None


def test_get_settings_does_not_create_directory_for_non_sqlite_database_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("RESEARCHOS_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/researchos")
    monkeypatch.setenv("PDF_STORAGE_ROOT", str(tmp_path / "papers"))
    monkeypatch.setenv("BRIEF_OUTPUT_ROOT", str(tmp_path / "briefs"))
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
    finally:
        config.get_settings.cache_clear()

    assert settings.database_url == "postgresql://user:pass@localhost/researchos"
    assert not (Path.cwd() / "postgresql:" / "user:pass@localhost").exists()


def test_reload_settings_applies_environment_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("RESEARCHOS_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("PDF_STORAGE_ROOT", str(tmp_path / "papers"))
    monkeypatch.setenv("BRIEF_OUTPUT_ROOT", str(tmp_path / "briefs"))
    monkeypatch.setenv("SITE_URL", "http://first.example")
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().site_url == "http://first.example"
        monkeypatch.setenv("SITE_URL", "http://second.example")
        assert config.reload_settings().site_url == "http://second.example"
    finally:
        config.get_settings.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="foreign Windows path rejection is only relevant off Windows")
def test_researchos_data_dir_rejects_foreign_windows_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCHOS_DATA_DIR", r"D:\ResearchOS\data")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="Windows path"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()
