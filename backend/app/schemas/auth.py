"""认证、账户管理和服务端登录会话使用的 API 数据模型。"""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from app.schemas.common import ApiModel, UtcDateTime


class UserRole(StrEnum):
    USER = "USER"
    KNOWLEDGE_ADMIN = "KNOWLEDGE_ADMIN"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


LoginIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
PasswordValue = Annotated[str, StringConstraints(min_length=12, max_length=128)]
CreateUserPassword = Annotated[str, StringConstraints(min_length=6, max_length=18)]
LoginPassword = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class UserItem(ApiModel):
    id: UUID
    login_identifier: str
    role: UserRole
    status: UserStatus
    created_at: UtcDateTime
    updated_at: UtcDateTime


class LoginRequest(ApiModel):
    login_identifier: LoginIdentifier
    password: LoginPassword


class AuthenticatedUser(ApiModel):
    user: UserItem
    csrf_token: str


class ChangePasswordRequest(ApiModel):
    current_password: LoginPassword
    new_password: PasswordValue


class AuthSessionItem(ApiModel):
    id: UUID
    created_at: UtcDateTime
    last_seen_at: UtcDateTime
    expires_at: UtcDateTime
    device_info: str | None = None
    current: bool


class CreateUserRequest(ApiModel):
    login_identifier: LoginIdentifier
    password: CreateUserPassword
    role: Literal["USER", "KNOWLEDGE_ADMIN"]


class UpdateUserRequest(ApiModel):
    role: Literal["USER", "KNOWLEDGE_ADMIN"]
    status: UserStatus


class UserSessionRevokeResponse(ApiModel):
    revoked: bool = Field(description="是否撤销了该账号的登录会话")
