from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ARISSmokeMode = Literal["quick", "full"]

ROOT = Path(__file__).resolve().parents[2]
TMP_ROOT = ROOT / "tmp" / "aris-smoke-reports"


def build_aris_smoke_command(mode: ARISSmokeMode = "quick") -> list[str]:
    if mode == "full":
        pwsh = shutil.which("pwsh")
        if not pwsh:
            raise RuntimeError("未找到 PowerShell 7（pwsh），无法执行完整项目工作流回归")
        return [
            pwsh,
            "-NoLogo",
            "-File",
            str(ROOT / "scripts" / "run-aris-smoke.ps1"),
        ]
    return [
        sys.executable,
        str(ROOT / "scripts" / "aris_workflow_smoke.py"),
    ]


def extract_aris_smoke_items(output: str) -> list[dict[str, Any]]:
    text = str(output or "").strip()
    if not text:
        return []
    for index, char in enumerate(text):
        if char != "[":
            continue
        candidate = text[index:].strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    return []


def build_aris_smoke_report_path(task_id: str, mode: ARISSmokeMode = "quick") -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    safe_task_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(task_id or "aris-smoke"))
    return TMP_ROOT / f"{timestamp}-{mode}-{safe_task_id}.json"


def run_aris_smoke(
    *,
    mode: ARISSmokeMode = "quick",
    progress_callback=None,
    log_callback=None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    command = build_aris_smoke_command(mode)
    if progress_callback:
        progress_callback(f"准备执行项目工作流回归检查（{mode}）...", 5, 100)
    if log_callback:
        log_callback(f"执行命令: {' '.join(command)}")

    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output_lines: list[str] = []
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            output_lines.append(raw_line)
            if log_callback and line:
                log_callback(line)
    return_code = process.wait()
    finished_at = datetime.now(UTC)
    output_text = "".join(output_lines)
    items = extract_aris_smoke_items(output_text)
    failed_items = [
        item for item in items
        if str(item.get("status") or "").strip().lower() not in {"completed", "succeeded", "success"}
    ]
    success = return_code == 0 and not failed_items
    duration_seconds = round((finished_at - started_at).total_seconds(), 2)
    result = {
        "mode": mode,
        "command": command,
        "root": str(ROOT),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": duration_seconds,
        "return_code": return_code,
        "success": success,
        "status": "succeeded" if success else "failed",
        "workflow_count": len(items),
        "failed_workflow_count": len(failed_items),
        "items": items,
        "stdout_tail": output_text[-12000:],
    }
    if failed_items:
        result["failed_workflows"] = [
            {
                "workflow": item.get("workflow"),
                "status": item.get("status"),
                "excerpt": item.get("excerpt"),
            }
            for item in failed_items
        ]
    target_path = Path(report_path) if report_path else None
    if target_path is not None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report_path"] = str(target_path)
    if progress_callback:
        progress_callback(
            "项目工作流回归检查完成" if success else "项目工作流回归检查失败",
            100 if success else 99,
            100,
        )
    if return_code != 0:
        raise RuntimeError(f"项目工作流回归检查失败，退出码 {return_code}")
    if failed_items:
        names = ", ".join(str(item.get("workflow") or "") for item in failed_items[:5] if item.get("workflow"))
        raise RuntimeError(f"项目工作流回归检查存在失败工作流: {names or 'unknown'}")
    return result
