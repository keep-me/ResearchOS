"""
认证工具模块 - JWT 生成/验证，密码验证
@author Color2333
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import logging
import re
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from packages.config import get_settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7 days
ASSET_TOKEN_EXPIRE_MINUTES = 5
QUERY_ACCESS_TOKEN_EXPIRE_SECONDS = 90
_DEFAULT_AUTH_SECRET = "researchos-secret-key-change-in-production"
_BCRYPT_HASH_RE = re.compile(r"^\$2[aby]\$")
_QUERY_TOKEN_ALLOWED_PATHS = (
    re.compile(r"^/papers/[^/]+/pdf$"),
    re.compile(r"^/papers/[^/]+/figures/[^/]+/image$"),
    re.compile(r"^/global/event$"),
    re.compile(r"^/global/ws$"),
    re.compile(r"^/agent/workspace/terminal/session/[^/]+/ws$"),
)


def _looks_like_password_hash(value: str | None) -> bool:
    return bool(_BCRYPT_HASH_RE.match(str(value or "").strip()))


def auth_enabled() -> bool:
    settings = get_settings()
    return bool(str(settings.auth_password or "").strip() or str(settings.auth_password_hash or "").strip())


def configured_password_hash() -> str | None:
    settings = get_settings()
    explicit_hash = str(settings.auth_password_hash or "").strip()
    if explicit_hash:
        return explicit_hash
    inline_value = str(settings.auth_password or "").strip()
    if _looks_like_password_hash(inline_value):
        return inline_value
    return None


def query_token_allowed_for_path(path: str) -> bool:
    normalized = str(path or "").strip()
    return any(pattern.match(normalized) for pattern in _QUERY_TOKEN_ALLOWED_PATHS)


def extract_request_token(
    auth_header: str | None,
    query_token: str | None = None,
    *,
    path: str | None = None,
    allow_query_token: bool = False,
) -> str | None:
    header_value = str(auth_header or "").strip()
    if header_value.lower().startswith("bearer "):
        token = header_value[7:].strip()
        if token:
            return token
    if allow_query_token and query_token:
        return str(query_token).strip() or None
    if path and query_token_allowed_for_path(path) and query_token:
        return str(query_token).strip() or None
    return None


def require_auth_secret() -> str:
    settings = get_settings()
    secret = str(settings.auth_secret_key or "").strip()
    if not secret or secret == _DEFAULT_AUTH_SECRET:
        raise RuntimeError("AUTH_SECRET_KEY must be explicitly configured when authentication is enabled")
    return secret


def validate_auth_configuration() -> None:
    settings = get_settings()
    if not auth_enabled():
        if settings.app_env != "dev" and not bool(getattr(settings, "allow_unauthenticated", False)):
            raise RuntimeError(
                "Non-dev deployments must configure authentication or set ALLOW_UNAUTHENTICATED=true"
            )
        return

    require_auth_secret()

    if settings.app_env != "dev" and not configured_password_hash():
        raise RuntimeError(
            "Non-dev deployments must configure AUTH_PASSWORD_HASH or provide a bcrypt hash in AUTH_PASSWORD"
        )

    if settings.app_env == "dev" and not configured_password_hash():
        logger.warning("Authentication is using a plaintext AUTH_PASSWORD in dev mode")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""

    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        import bcrypt

        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )


def get_password_hash(password: str) -> str:
    """生成密码哈希"""

    try:
        return pwd_context.hash(password)
    except Exception:
        import bcrypt

        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """创建 JWT token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, require_auth_secret(), algorithm=ALGORITHM)
    return encoded_jwt


def create_asset_access_token(path: str, *, expires_delta: timedelta | None = None) -> str:
    normalized_path = str(path or "").strip()
    if not query_token_allowed_for_path(normalized_path):
        raise ValueError("path is not eligible for signed asset access")
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(seconds=QUERY_ACCESS_TOKEN_EXPIRE_SECONDS)
    )
    return jwt.encode(
        {
            "sub": "asset",
            "typ": "path_access",
            "path": normalized_path,
            "exp": expire,
        },
        require_auth_secret(),
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> dict[str, Any] | None:
    """解码 JWT token，失败返回 None"""
    try:
        payload = jwt.decode(token, require_auth_secret(), algorithms=[ALGORITHM])
        return payload
    except (JWTError, RuntimeError):
        return None


def decode_asset_access_token(token: str, *, path: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, require_auth_secret(), algorithms=[ALGORITHM])
    except (JWTError, RuntimeError):
        return None
    if payload.get("typ") != "asset_access":
        if payload.get("typ") != "path_access":
            return None
    if str(payload.get("path") or "").strip() != str(path or "").strip():
        return None
    return payload


def authenticate_user(password: str) -> bool:
    """
    验证站点密码。
    优先走 bcrypt 哈希，开发环境保留明文兼容路径。
    """

    settings = get_settings()
    hash_value = configured_password_hash()
    if hash_value:
        return verify_password(password, hash_value)

    plain_password = str(settings.auth_password or "")
    if not plain_password:
        return False
    return hmac.compare_digest(password, plain_password)
