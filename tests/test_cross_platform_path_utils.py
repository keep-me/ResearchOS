from __future__ import annotations

import os

import pytest

from packages.path_utils import (
    join_path_string,
    local_relative_path,
    normalize_local_path_string,
    path_name_string,
    sqlite_url_for_path,
)


@pytest.mark.skipif(os.name == "nt", reason="foreign Windows path behavior is only relevant off Windows")
def test_foreign_windows_paths_are_not_resolved_as_posix_paths() -> None:
    data_dir = r"D:\ResearchOS\data"

    assert normalize_local_path_string(data_dir) == data_dir
    assert path_name_string(data_dir) == "data"
    assert join_path_string(data_dir, "papers", "paper.pdf") == r"D:\ResearchOS\data\papers\paper.pdf"
    assert sqlite_url_for_path(r"D:\ResearchOS\data\researchos.db") == "sqlite:///D:/ResearchOS/data/researchos.db"


@pytest.mark.skipif(os.name == "nt", reason="foreign Windows path behavior is only relevant off Windows")
def test_foreign_windows_relative_paths_use_windows_boundaries() -> None:
    workspace = r"D:\ResearchOS\data\workspace"

    assert local_relative_path(workspace, r"D:\ResearchOS\data\workspace") == "."
    assert local_relative_path(workspace, r"D:\ResearchOS\data\workspace\reports\out.md") == "reports/out.md"
    with pytest.raises(ValueError):
        local_relative_path(workspace, r"D:\ResearchOS\data\other\out.md")


@pytest.mark.skipif(os.name == "nt", reason="foreign Windows path behavior is only relevant off Windows")
def test_posix_style_windows_drive_paths_keep_posix_separators() -> None:
    data_dir = "D:/ResearchOS/data"

    assert normalize_local_path_string(data_dir) == data_dir
    assert path_name_string(data_dir) == "data"
    assert join_path_string(data_dir, "papers", "paper.pdf") == "D:/ResearchOS/data/papers/paper.pdf"
