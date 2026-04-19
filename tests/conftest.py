from __future__ import annotations

import pytest

from packages.agent import workspace_executor


@pytest.fixture(autouse=True)
def _isolate_assistant_exec_policy_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = tmp_path / "assistant_exec_policy.json"
    monkeypatch.setattr(workspace_executor, "_assistant_exec_policy_store", lambda: store)
