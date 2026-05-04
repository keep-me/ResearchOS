from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from apps.api.routers import jobs as jobs_router
from packages.ai.project.aris_smoke_service import (
    build_aris_smoke_command,
    extract_aris_smoke_items,
)
from packages.domain.task_tracker import global_tracker


def test_extract_aris_smoke_items_parses_json_payload() -> None:
    payload = '[{"workflow":"sync_workspace","status":"completed"}]'
    output = f"ARIS workflow smoke\nRoot: D:/Desktop/ResearchOS\n{payload}\n"

    items = extract_aris_smoke_items(output)

    assert items == [{"workflow": "sync_workspace", "status": "completed"}]


def test_build_aris_smoke_command_quick_uses_python() -> None:
    command = build_aris_smoke_command("quick")

    assert command[0]
    assert command[-1].endswith("scripts\\aris_workflow_smoke.py") or command[-1].endswith(
        "scripts/aris_workflow_smoke.py"
    )


def test_build_aris_smoke_command_full_requires_pwsh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("packages.ai.project.aris_smoke_service.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="pwsh"):
        build_aris_smoke_command("full")


def test_run_aris_smoke_job_writes_task_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report_path = tmp_path / "aris-smoke-report.json"
    expected_result = {
        "mode": "quick",
        "success": True,
        "status": "succeeded",
        "workflow_count": 2,
        "failed_workflow_count": 0,
        "items": [
            {"workflow": "sync_workspace", "status": "completed", "artifacts": ["sync-report.md"]},
            {"workflow": "paper_compile", "status": "completed", "artifacts": ["paper/main.pdf"]},
        ],
        "duration_seconds": 1.23,
        "report_path": str(report_path),
    }

    def _fake_report_path(task_id: str, mode: str = "quick") -> Path:
        assert task_id.startswith("aris_smoke_quick_")
        assert mode == "quick"
        return report_path

    def _fake_run_aris_smoke(
        *, mode="quick", progress_callback=None, log_callback=None, report_path=None
    ):
        assert mode == "quick"
        if progress_callback:
            progress_callback("fake run", 50, 100)
        if log_callback:
            log_callback("fake smoke log")
        Path(str(report_path)).write_text(
            json.dumps(expected_result, ensure_ascii=False), encoding="utf-8"
        )
        return expected_result

    monkeypatch.setattr(jobs_router, "build_aris_smoke_report_path", _fake_report_path)
    monkeypatch.setattr(jobs_router, "run_aris_smoke", _fake_run_aris_smoke)

    response = jobs_router.run_aris_smoke_once(mode="quick")
    task_id = str(response["task_id"])
    try:
        deadline = time.time() + 3
        status = global_tracker.get_task(task_id)
        while status and not status.get("finished") and time.time() < deadline:
            time.sleep(0.05)
            status = global_tracker.get_task(task_id)

        assert status is not None
        assert status["task_type"] == "aris_smoke"
        assert status["finished"] is True
        assert status["success"] is True
        assert status["retry_supported"] is True
        assert status["metadata"]["report_path"] == str(report_path)
        assert status["artifact_refs"][0]["path"] == str(report_path)

        result = global_tracker.get_result(task_id)
        assert result == expected_result
        logs = global_tracker.list_logs(task_id, limit=10)
        assert any("fake smoke log" in item["message"] for item in logs)
    finally:
        global_tracker.forget_task(task_id, delete_persisted=False)
