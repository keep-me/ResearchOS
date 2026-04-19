from __future__ import annotations

from types import SimpleNamespace

import pytest

import packages.auth as auth


def test_validate_auth_configuration_requires_secret_when_auth_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
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
        auth.extract_request_token(None, "query-token", path="/papers/abc123/pdf")
        == "query-token"
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
