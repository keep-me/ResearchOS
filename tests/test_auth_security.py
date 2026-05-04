from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import apps.api.routers.auth as auth_router
import apps.api.routers.settings as settings_router
import packages.auth as auth


def _request_with_user(user: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user=user or {}))


def test_sensitive_settings_access_allows_dev_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_router, "auth_enabled", lambda: False, raising=False)
    monkeypatch.setattr(
        settings_router,
        "get_settings",
        lambda: SimpleNamespace(app_env="dev"),
        raising=False,
    )

    settings_router.require_sensitive_settings_access(_request_with_user())


def test_sensitive_settings_access_rejects_non_admin_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_router, "auth_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        settings_router,
        "get_settings",
        lambda: SimpleNamespace(app_env="prod"),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        settings_router.require_sensitive_settings_access(_request_with_user({"role": "user"}))

    assert exc_info.value.status_code == 403


def test_sensitive_settings_access_allows_admin_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_router, "auth_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        settings_router,
        "get_settings",
        lambda: SimpleNamespace(app_env="prod", allow_sensitive_settings=False),
        raising=False,
    )

    settings_router.require_sensitive_settings_access(_request_with_user({"role": "admin"}))


def test_sensitive_settings_access_allows_explicit_configuration_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_router, "auth_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        settings_router,
        "get_settings",
        lambda: SimpleNamespace(app_env="prod", allow_sensitive_settings=True),
        raising=False,
    )

    settings_router.require_sensitive_settings_access(_request_with_user({"role": "user"}))


def test_login_token_does_not_grant_admin_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_router, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth_router, "validate_auth_configuration", lambda: None)
    monkeypatch.setattr(auth_router, "authenticate_user", lambda password: password == "correct")
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(auth_secret_key="super-secret"),
    )

    response = asyncio.run(auth_router.login(auth_router.LoginRequest(password="correct")))
    payload = auth.decode_access_token(response.access_token)

    assert payload is not None
    assert payload.get("sub") == "researchos-user"
    assert "role" not in payload


def test_validate_auth_configuration_requires_secret_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="dev-password",
            auth_password_hash="",
            auth_secret_key="",
            app_env="prod",
        ),
    )

    with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY"):
        auth.validate_auth_configuration()


def test_validate_auth_configuration_rejects_unauthenticated_non_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="",
            auth_password_hash="",
            auth_secret_key="",
            app_env="prod",
            allow_unauthenticated=False,
        ),
    )

    with pytest.raises(RuntimeError, match="ALLOW_UNAUTHENTICATED"):
        auth.validate_auth_configuration()


def test_validate_auth_configuration_allows_explicit_unauthenticated_non_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="",
            auth_password_hash="",
            auth_secret_key="",
            app_env="prod",
            allow_unauthenticated=True,
        ),
    )

    auth.validate_auth_configuration()


def test_validate_auth_configuration_requires_hashed_password_outside_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="plain-password",
            auth_password_hash="",
            auth_secret_key="super-secret",
            app_env="prod",
        ),
    )

    with pytest.raises(RuntimeError, match="AUTH_PASSWORD_HASH"):
        auth.validate_auth_configuration()


def test_authenticate_user_accepts_bcrypt_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "correct-horse"
    password_hash = auth.get_password_hash(password)
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="",
            auth_password_hash=password_hash,
            auth_secret_key="super-secret",
            app_env="prod",
        ),
    )

    auth.validate_auth_configuration()
    assert auth.authenticate_user(password) is True
    assert auth.authenticate_user("wrong-password") is False


def test_extract_request_token_rejects_query_token_for_regular_api_path() -> None:
    assert auth.extract_request_token(None, "query-token", path="/papers/latest") is None
    assert (
        auth.extract_request_token(None, "query-token", path="/papers/abc123/pdf") == "query-token"
    )


def test_asset_access_token_is_path_scoped_and_short_lived(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="dev-password",
            auth_password_hash="",
            auth_secret_key="super-secret",
            app_env="dev",
        ),
    )

    token = auth.create_asset_access_token("/papers/abc123/pdf")

    assert auth.decode_asset_access_token(token, path="/papers/abc123/pdf") is not None
    assert auth.decode_asset_access_token(token, path="/papers/other/pdf") is None
    with pytest.raises(ValueError):
        auth.create_asset_access_token("/papers/latest")


def test_path_access_token_is_not_regular_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="dev-password",
            auth_password_hash="",
            auth_secret_key="super-secret",
            app_env="dev",
        ),
    )

    token = auth.create_asset_access_token("/papers/abc123/pdf")

    assert auth.decode_access_token(token) is None
    assert auth.decode_request_token(token, path="/papers/other/pdf", source="header") is None
    assert auth.decode_request_token(token, path="/papers/abc123/pdf", source="header") is not None


def test_query_token_source_rejects_regular_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="dev-password",
            auth_password_hash="",
            auth_secret_key="super-secret",
            app_env="dev",
        ),
    )

    token = auth.create_access_token({"sub": "researchos-user"})

    assert auth.decode_access_token(token) is not None
    assert auth.decode_request_token(token, path="/papers/abc123/pdf", source="query") is None
    assert auth.decode_request_token(token, path="/papers/latest", source="header") is not None


def test_path_access_token_supports_websocket_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            auth_password="dev-password",
            auth_password_hash="",
            auth_secret_key="super-secret",
            app_env="dev",
        ),
    )

    token = auth.create_asset_access_token("/agent/workspace/terminal/session/session-1/ws")

    assert auth.decode_asset_access_token(
        token,
        path="/agent/workspace/terminal/session/session-1/ws",
    )
    assert auth.decode_asset_access_token(token, path="/global/ws") is None


def test_create_path_token_rejects_ineligible_path_with_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_router, "auth_enabled", lambda: True)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auth_router.create_path_token(auth_router.PathTokenRequest(path="/papers/latest"))
        )

    assert exc_info.value.status_code == 400
