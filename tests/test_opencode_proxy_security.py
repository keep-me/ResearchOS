from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api.routers.opencode import _validate_provider_url


def test_validate_provider_url_allows_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.api.routers.opencode.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    assert _validate_provider_url("https://api.example.com/v1") == "https://api.example.com/v1"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "http://127.0.0.1:8000/v1",
        "https://localhost/v1",
    ],
)
def test_validate_provider_url_rejects_unsafe_scheme_or_localhost(url: str) -> None:
    with pytest.raises(HTTPException):
        _validate_provider_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",
    ],
)
def test_validate_provider_url_rejects_private_or_link_local_addresses(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    monkeypatch.setattr(
        "apps.api.routers.opencode.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, (address, 443))],
    )

    with pytest.raises(HTTPException, match="本机或内网"):
        _validate_provider_url("https://provider.example.com/v1")
