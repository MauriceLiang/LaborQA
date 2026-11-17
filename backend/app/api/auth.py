"""登录、本人资料、登出和本人服务端会话管理接口。"""

import ipaddress
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import (
    AUTH_COOKIE_NAME,
    get_auth_service,
    get_current_user,
    require_trusted_origin,
)
from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.schemas.auth import (
    AuthenticatedUser,
    AuthSessionItem,
    ChangePasswordRequest,
    LoginRequest,
    UserItem,
)
from app.schemas.common import ApiResponse
from app.services.auth_service import AuthService, UserAccount

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=ApiResponse[AuthenticatedUser])
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[AuthenticatedUser]:
    """验证账号密码并轮换浏览器收到的 HttpOnly 服务端会话 Cookie。"""
    require_trusted_origin(request)
    client_ip = _client_ip(request)
    try:
        result = service.login(
            payload.login_identifier,
            payload.password,
            client_ip,
            request.headers.get("User-Agent"),
            previous_token=request.cookies.get(AUTH_COOKIE_NAME),
        )
    except AppError as exc:
        if exc.http_status in {401, 429}:
            service.record_audit(
                None,
                "LOGIN",
                "auth",
                None,
                getattr(request.state, "request_id", None),
                result="DENIED",
            )
        raise
    service.record_audit(
        result.user.id,
        "LOGIN",
        "auth_session",
        result.session_id,
        getattr(request.state, "request_id", None),
    )
    _set_auth_cookie(response, result.session_token)
    return ApiResponse(
        code=0,
        message="success",
        data=AuthenticatedUser(
            user=_user_item(result.user), csrf_token=result.csrf_token
        ),
    )


@router.get("/auth/me", response_model=ApiResponse[AuthenticatedUser])
def get_me(
    request: Request,
    user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[AuthenticatedUser]:
    """返回当前身份并轮换 CSRF token，供刷新后的页面重新建立安全请求状态。"""
    csrf_token = service.issue_csrf_token(request.state.auth_session_id)
    return ApiResponse(
        code=0,
        message="success",
        data=AuthenticatedUser(user=_user_item(user), csrf_token=csrf_token),
    )


@router.post("/auth/logout", response_model=ApiResponse[None])
def logout(
    request: Request,
    response: Response,
    user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[None]:
    """撤销当前服务端会话并删除浏览器 Cookie。"""
    session_id = request.state.auth_session_id
    service.logout(session_id)
    service.record_audit(
        user.id,
        "LOGOUT",
        "auth_session",
        session_id,
        getattr(request.state, "request_id", None),
    )
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path=settings.api_prefix,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
    )
    return ApiResponse(code=0, message="success", data=None)


@router.post("/auth/change-password", response_model=ApiResponse[AuthenticatedUser])
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[AuthenticatedUser]:
    """修改本人密码，撤销旧设备会话并为当前浏览器签发新会话。"""
    result = service.change_password(
        user.id,
        payload.current_password,
        payload.new_password,
        request.headers.get("User-Agent"),
    )
    service.record_audit(
        user.id,
        "CHANGE_PASSWORD",
        "user",
        user.id,
        getattr(request.state, "request_id", None),
    )
    _set_auth_cookie(response, result.session_token)
    return ApiResponse(
        code=0,
        message="success",
        data=AuthenticatedUser(
            user=_user_item(result.user), csrf_token=result.csrf_token
        ),
    )


@router.get("/auth/sessions", response_model=ApiResponse[list[AuthSessionItem]])
def list_my_sessions(
    request: Request,
    user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[list[AuthSessionItem]]:
    sessions = service.list_sessions(user.id)
    return ApiResponse(
        code=0,
        message="success",
        data=[
            AuthSessionItem(
                **session,
                current=session["id"] == request.state.auth_session_id,
            )
            for session in sessions
        ],
    )


@router.delete("/auth/sessions/{session_id}", response_model=ApiResponse[None])
def revoke_my_session(
    session_id: UUID,
    request: Request,
    response: Response,
    user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[None]:
    session_text = str(session_id)
    if not service.revoke_session(user.id, session_text):
        raise AppError(ErrorCode.INVALID_REQUEST, "登录会话不存在", 404)
    service.record_audit(
        user.id,
        "REVOKE_SESSION",
        "auth_session",
        session_text,
        getattr(request.state, "request_id", None),
    )
    if session_text == request.state.auth_session_id:
        response.delete_cookie(
            AUTH_COOKIE_NAME,
            path=settings.api_prefix,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
        )
    return ApiResponse(code=0, message="success", data=None)


def _set_auth_cookie(response: Response, token: str) -> None:
    """只将高熵原始会话令牌写入 HttpOnly Cookie；服务端数据库保存其摘要。"""
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=settings.auth_session_max_age_seconds,
        path=settings.api_prefix,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _user_item(user: UserAccount) -> UserItem:
    """将依赖层的身份信息映射到公开资料模型。"""
    if user.created_at is None or user.updated_at is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "账号资料不完整", 500)
    return UserItem(
        id=user.id,
        login_identifier=user.login_identifier,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _client_ip(request: Request) -> str:
    """只有明确配置且由反向代理覆盖写入时才使用专用来源头，不采信 X-Forwarded-For。"""
    peer_ip = request.client.host if request.client else "unknown"
    if not settings.auth_trust_proxy_client_ip:
        return peer_ip
    forwarded_ip = request.headers.get("X-Auth-Client-IP", "")
    try:
        return ipaddress.ip_address(forwarded_ip).compressed
    except ValueError:
        return peer_ip
