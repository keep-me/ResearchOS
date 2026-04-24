from __future__ import annotations

import asyncio
import importlib


def test_api_startup_uses_explicit_bootstrap(monkeypatch) -> None:
    api_main = importlib.import_module("apps.api.main")
    called: list[str] = []

    monkeypatch.setattr(api_main, "bootstrap_api_runtime", lambda: called.append("api"))
    monkeypatch.setattr(api_main, "validate_auth_configuration", lambda: None)

    asyncio.run(api_main._startup_runtime())

    assert called == ["api"]
