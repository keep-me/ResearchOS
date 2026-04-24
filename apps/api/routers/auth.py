"""
认证路由 - 登录接口
@author Color2333
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from packages.auth import (
    QUERY_ACCESS_TOKEN_EXPIRE_SECONDS,
    auth_enabled,
    authenticate_user,
    create_access_token,
    create_asset_access_token,
    validate_auth_configuration,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthStatusResponse(BaseModel):
    auth_enabled: bool


class PathTokenRequest(BaseModel):
    path: str


class PathTokenResponse(BaseModel):
    access_token: str
    token_type: str = "path"
    expires_in: int = QUERY_ACCESS_TOKEN_EXPIRE_SECONDS


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    站点密码登录
    成功返回 JWT token
    """
    # 如果未配置密码，返回错误
    if not auth_enabled():
        raise HTTPException(status_code=403, detail="Authentication is disabled")

    validate_auth_configuration()

    # 验证密码
    if not authenticate_user(request.password):
        raise HTTPException(status_code=401, detail="Incorrect password")

    # 生成 token
    access_token = create_access_token(data={"sub": "researchos-user"})
    return LoginResponse(access_token=access_token)


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status():
    """
    检查认证是否启用
    """
    return AuthStatusResponse(auth_enabled=auth_enabled())


@router.post("/path-token", response_model=PathTokenResponse)
async def create_path_token(request: PathTokenRequest):
    """
    Mint a short-lived, path-scoped token for browser surfaces that cannot attach
    Authorization headers, such as image/PDF tags and WebSocket handshakes.
    """
    if not auth_enabled():
        raise HTTPException(status_code=403, detail="Authentication is disabled")
    try:
        token = create_asset_access_token(request.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PathTokenResponse(access_token=token)
