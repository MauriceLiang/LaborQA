"""仅系统管理员可用的账号创建、角色调整和设备会话撤销接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_auth_service, get_current_user, require_roles
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.schemas.auth import CreateUserRequest, UpdateUserRequest, UserItem
from app.schemas.common import ApiResponse
from app.services.auth_service import AuthService, UserAccount

router = APIRouter(
    dependencies=[Depends(require_roles("SYSTEM_ADMIN"))],
    tags=["admin"],
)


@router.get("/admin/users", response_model=ApiResponse[list[UserItem]])
def list_users(
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[list[UserItem]]:
    """列出账号及当前角色，不返回密码或认证令牌字段。"""
    return ApiResponse(
        code=0,
        message="success",
        data=[UserItem.model_validate(item) for item in service.list_users()],
    )


@router.post("/admin/users", response_model=ApiResponse[UserItem], status_code=201)
def create_user(
    payload: CreateUserRequest,
    request: Request,
    actor: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[UserItem]:
    """创建普通账号或知识管理员；系统管理员只能通过离线命令初始化。"""
    user = service.create_user(payload.login_identifier, payload.password, payload.role)
    service.record_audit(
        actor.id,
        "CREATE_USER",
        "user",
        user.id,
        getattr(request.state, "request_id", None),
    )
    return ApiResponse(
        code=0,
        message="created",
        data=UserItem.model_validate(user.__dict__),
    )


@router.patch("/admin/users/{user_id}", response_model=ApiResponse[UserItem])
def update_user(
    user_id: UUID,
    payload: UpdateUserRequest,
    request: Request,
    actor: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[UserItem]:
    """更新普通账号的角色或启用状态，并立即撤销已有服务端会话。"""
    result = service.update_user(
        str(user_id), actor.id, payload.role, payload.status.value
    )
    service.record_audit(
        actor.id,
        "UPDATE_USER",
        "user",
        str(user_id),
        getattr(request.state, "request_id", None),
    )
    return ApiResponse(code=0, message="updated", data=UserItem.model_validate(result))


@router.post("/admin/users/{user_id}/revoke-sessions", response_model=ApiResponse[None])
def revoke_user_sessions(
    user_id: UUID,
    request: Request,
    actor: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[None]:
    """撤销指定账号所有当前有效的设备登录会话。"""
    user_text = str(user_id)
    if not any(item["id"] == user_text for item in service.list_users()):
        raise AppError(ErrorCode.INVALID_REQUEST, "账号不存在", 404)
    service.revoke_user_sessions(user_text)
    service.record_audit(
        actor.id,
        "REVOKE_USER_SESSIONS",
        "user",
        user_text,
        getattr(request.state, "request_id", None),
    )
    return ApiResponse(code=0, message="success", data=None)
