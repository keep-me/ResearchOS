from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import jobs, settings as settings_router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_router.router)
    app.include_router(jobs.router)
    return app


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/settings/feishu-config", None),
        ("put", "/settings/feishu-config", {}),
        ("post", "/settings/feishu-config/test", {}),
        ("get", "/settings/daily-report-config", None),
        ("put", "/settings/daily-report-config", {}),
        ("post", "/jobs/daily-report/run-once", {}),
        ("post", "/jobs/daily-report/send-only", {}),
        ("post", "/jobs/daily-report/generate-only", {}),
        ("post", "/jobs/graph/weekly-run-once", {}),
    ],
)
def test_removed_notification_report_and_manual_maintenance_endpoints_are_absent(
    method: str,
    path: str,
    payload: dict | None,
) -> None:
    client = TestClient(_build_app())
    request = getattr(client, method)
    response = request(path, json=payload) if payload is not None else request(path)
    assert response.status_code == 404
