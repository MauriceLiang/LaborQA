"""第一方账号登录、服务端会话续期、密码哈希和角色账户管理。"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.repositories.auth_repository import AuthRepository

logger = logging.getLogger(__name__)
PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)
_DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class UserAccount:
    """鉴权依赖在请求内传递的最小身份信息。"""

    id: str
    login_identifier: str
    role: str
    status: str = "ACTIVE"
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class AuthSessionResult:
    """只在登录或密码修改响应中返回一次的原始 Cookie 与 CSRF 令牌。"""

    user: UserAccount
    session_id: str
    session_token: str
    csrf_token: str


class AuthService:
    """在仓储事务之上执行密码验证、登录限流和账户生命周期规则。"""

    def __init__(
        self,
        *,
        repository: AuthRepository | None = None,
        database_path: Path | None = None,
    ) -> None:
        self.repository = repository or AuthRepository(database_path)
        # 限流键使用 HMAC；生产部署设置稳定密钥，使限流记录在重启后仍有效。
        self._rate_limit_secret = (
            settings.auth_rate_limit_secret.encode("utf-8")
            if settings.auth_rate_limit_secret
            else secrets.token_bytes(32)
        )
        if not settings.auth_rate_limit_secret:
            logger.warning(
                "AUTH_RATE_LIMIT_SECRET is unset; login rate limits reset on restart"
            )

    def create_user(
        self,
        identifier: str,
        password: str,
        role: str,
        *,
        initial_admin: bool = False,
        development_seed: bool = False,
    ) -> UserAccount:
        """规范化账号并创建哈希密码；公开 API 不允许新建系统管理员。"""
        normalized = self._normalize_identifier(identifier)
        if role not in {"USER", "KNOWLEDGE_ADMIN", "SYSTEM_ADMIN"}:
            raise AppError(ErrorCode.INVALID_REQUEST, "账号角色不合法", 422)
        if initial_admin and role != "SYSTEM_ADMIN":
            raise AppError(
                ErrorCode.INVALID_REQUEST, "离线初始化账号必须是系统管理员", 422
            )
        if not initial_admin and role == "SYSTEM_ADMIN":
            raise AppError(
                ErrorCode.AUTH_FORBIDDEN, "系统管理员只能通过离线命令初始化", 403
            )
        if development_seed:
            if not (
                initial_admin
                and settings.app_env == "development"
                and settings.auth_dev_seed_admin_enabled
                and 6 <= len(password) <= 128
            ):
                raise AppError(ErrorCode.AUTH_FORBIDDEN, "开发测试账号配置不合法", 403)
        elif initial_admin:
            self._validate_password(password)
        else:
            self._validate_managed_user_password(password)
        try:
            row = self.repository.create_user(
                str(uuid4()),
                normalized,
                PASSWORD_HASHER.hash(password),
                role,
                initial_admin=initial_admin,
            )
        except sqlite3.IntegrityError as exc:
            raise AppError(ErrorCode.AUTH_CONFLICT, "账号已存在", 409) from exc
        except ValueError as exc:
            raise AppError(ErrorCode.AUTH_CONFLICT, str(exc), 409) from exc
        return self._account(row)

    def ensure_development_admin(self) -> bool:
        """首次启动时按显式开发配置创建本地测试管理员；生产环境不执行。"""
        if not settings.auth_dev_seed_admin_enabled:
            return False
        if settings.app_env != "development":
            raise RuntimeError(
                "Development admin seeding is disabled outside development"
            )
        if self.repository.has_system_admin():
            return False
        try:
            user = self.create_user(
                settings.auth_dev_seed_admin_identifier,
                settings.auth_dev_seed_admin_password,
                "SYSTEM_ADMIN",
                initial_admin=True,
                development_seed=True,
            )
        except AppError as exc:
            if (
                exc.code == ErrorCode.AUTH_CONFLICT
                and self.repository.has_system_admin()
            ):
                return False
            raise
        logger.warning(
            "Created development-only administrator account: %s",
            user.login_identifier,
        )
        return True

    def ensure_initial_admin(self) -> bool:
        """已有管理员时不变更；空库按显式开发 seed 或一次性引导凭据创建首位管理员。"""
        if self.repository.has_system_admin():
            self._clear_bootstrap_admin_credentials()
            return False
        if settings.auth_dev_seed_admin_enabled:
            return self.ensure_development_admin()

        identifier = settings.auth_bootstrap_admin_identifier.strip()
        password = settings.auth_bootstrap_admin_password
        if not identifier or not password:
            raise RuntimeError(
                "No system administrator exists. Set "
                "AUTH_BOOTSTRAP_ADMIN_IDENTIFIER and "
                "AUTH_BOOTSTRAP_ADMIN_PASSWORD in the deployment environment "
                "(deploy/.env for Docker Compose), then restart."
            )

        try:
            user = self.create_user(
                identifier,
                password,
                "SYSTEM_ADMIN",
                initial_admin=True,
            )
        except AppError as exc:
            if (
                exc.code == ErrorCode.AUTH_CONFLICT
                and self.repository.has_system_admin()
            ):
                self._clear_bootstrap_admin_credentials()
                return False
            raise RuntimeError(
                f"Unable to initialize the first system administrator: {exc.message}"
            ) from exc

        self._clear_bootstrap_admin_credentials()
        logger.warning(
            "Automatically initialized the first system administrator account: %s",
            user.login_identifier,
        )
        return True

    @staticmethod
    def _clear_bootstrap_admin_credentials() -> None:
        """清除进程内设置对象中的一次性引导凭据。"""
        settings.auth_bootstrap_admin_identifier = ""
        settings.auth_bootstrap_admin_password = ""

    def login(
        self,
        identifier: str,
        password: str,
        client_ip: str,
        device_info: str | None,
        *,
        previous_token: str | None = None,
    ) -> AuthSessionResult:
        """使用通用失败消息验证凭据，按账号和来源地址维度累计失败次数。"""
        normalized = self._normalize_identifier(identifier)
        keys = self._rate_limit_keys(normalized, client_ip)
        now = self._now()
        if self.repository.is_login_limited(keys, now):
            self._verify_password(password, _DUMMY_PASSWORD_HASH)
            raise AppError(
                ErrorCode.AUTH_RATE_LIMITED, "登录暂时不可用，请稍后重试", 429
            )

        row = self.repository.get_user_by_identifier(normalized)
        stored_hash = row["password_hash"] if row else _DUMMY_PASSWORD_HASH
        verified = self._verify_password(password, stored_hash)
        if not row or row["status"] != "ACTIVE" or not verified:
            window_start = self._format_time(datetime.now(UTC) - timedelta(minutes=15))
            for key_type, key_hash, threshold in (
                ("IDENTIFIER", keys[0][1], 5),
                ("IP", keys[1][1], 20),
            ):
                self.repository.record_login_failure(
                    key_type,
                    key_hash,
                    now,
                    window_start,
                    self._format_time(datetime.now(UTC) + timedelta(minutes=15)),
                    threshold,
                )
            raise AppError(ErrorCode.AUTH_LOGIN_FAILED, "账号或密码错误", 401)

        self.repository.clear_identifier_failures(keys[0][1])
        if previous_token:
            self.repository.revoke_token_hash(self._sha256(previous_token), now)
        result = self._create_session(self._account(row), device_info)
        return result

    def authenticate(
        self, session_token: str | None
    ) -> tuple[UserAccount, dict[str, Any]]:
        """验证 Cookie 令牌摘要、账户状态和绝对/空闲过期时间。"""
        if not session_token or len(session_token) > 256:
            raise self._unauthenticated()
        now = datetime.now(UTC)
        row = self.repository.authenticate_session(
            self._sha256(session_token),
            self._format_time(now),
            self._format_time(
                now + timedelta(seconds=settings.auth_session_idle_seconds)
            ),
        )
        if row is None:
            raise self._unauthenticated()
        return self._account(row), row

    def issue_csrf_token(self, session_id: str) -> str:
        """轮换当前认证会话的 CSRF token，数据库只保存其 SHA-256 摘要。"""
        token = secrets.token_urlsafe(32)
        if not self.repository.rotate_csrf_token(session_id, self._sha256(token)):
            raise self._unauthenticated()
        return token

    def verify_csrf_token(self, expected_hash: str, supplied_token: str | None) -> bool:
        """用常量时间比较验证请求头中的 CSRF token。"""
        if not supplied_token or len(supplied_token) > 256:
            return False
        return hmac.compare_digest(self._sha256(supplied_token), expected_hash)

    def logout(self, session_id: str) -> None:
        self.repository.revoke_session(session_id, self._now())

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        return self.repository.list_sessions(user_id)

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        return self.repository.revoke_session(session_id, self._now(), user_id)

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        device_info: str | None,
    ) -> AuthSessionResult:
        """验证旧密码后更新 Argon2id 摘要，撤销旧设备並签发当前新会话。"""
        row = self.repository.get_user(user_id)
        if not row or not self._verify_password(current_password, row["password_hash"]):
            raise AppError(ErrorCode.INVALID_REQUEST, "当前密码不正确", 400)
        self._validate_password(new_password)
        self.repository.change_password(
            user_id, PASSWORD_HASHER.hash(new_password), self._now()
        )
        fresh_user = self.repository.get_user(user_id)
        if fresh_user is None:
            raise self._unauthenticated()
        return self._create_session(self._account(fresh_user), device_info)

    def list_users(self) -> list[dict[str, Any]]:
        return self.repository.list_users()

    def update_user(
        self, user_id: str, actor_user_id: str, role: str, status: str
    ) -> dict[str, Any]:
        """限制管理 API 只调整普通/知识管理员，并在角色变化时立即撤销旧会话。"""
        if user_id == actor_user_id:
            raise AppError(ErrorCode.AUTH_CONFLICT, "不能修改当前登录账号", 409)
        if role not in {"USER", "KNOWLEDGE_ADMIN"} or status not in {
            "ACTIVE",
            "DISABLED",
        }:
            raise AppError(ErrorCode.INVALID_REQUEST, "账号角色或状态不合法", 422)
        try:
            row = self.repository.update_user(user_id, role, status, self._now())
        except ValueError as exc:
            raise AppError(ErrorCode.AUTH_FORBIDDEN, str(exc), 403) from exc
        if row is None:
            raise AppError(ErrorCode.INVALID_REQUEST, "账号不存在", 404)
        return row

    def revoke_user_sessions(self, user_id: str) -> None:
        self.repository.revoke_user_sessions(user_id, self._now())

    def record_audit(
        self,
        actor_user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        request_id: str | None,
        *,
        result: str = "SUCCESS",
    ) -> None:
        self.repository.add_audit_event(
            actor_user_id,
            action,
            resource_type,
            resource_id,
            result,
            request_id,
            self._now(),
        )

    def _create_session(
        self, user: UserAccount, device_info: str | None
    ) -> AuthSessionResult:
        now = datetime.now(UTC)
        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session_id = str(uuid4())
        expires_at = now + timedelta(seconds=settings.auth_session_max_age_seconds)
        idle_expires_at = min(
            expires_at,
            now + timedelta(seconds=settings.auth_session_idle_seconds),
        )
        self.repository.create_session(
            session_id,
            user.id,
            self._sha256(raw_token),
            self._sha256(csrf_token),
            self._format_time(now),
            self._format_time(expires_at),
            self._format_time(idle_expires_at),
            device_info[:255] if device_info else None,
        )
        return AuthSessionResult(user, session_id, raw_token, csrf_token)

    def _rate_limit_keys(
        self, identifier: str, client_ip: str
    ) -> list[tuple[str, str]]:
        return [
            ("IDENTIFIER", self._hmac("identifier:" + identifier)),
            ("IP", self._hmac("ip:" + (client_ip or "unknown"))),
        ]

    def _hmac(self, value: str) -> str:
        return hmac.new(
            self._rate_limit_secret,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        normalized = identifier.strip().casefold()
        if not normalized or len(normalized) > 80:
            raise AppError(ErrorCode.INVALID_REQUEST, "请输入有效账号", 422)
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if not 12 <= len(password) <= 128:
            raise AppError(ErrorCode.INVALID_REQUEST, "密码长度需为 12 至 128 位", 422)

    @staticmethod
    def _validate_managed_user_password(password: str) -> None:
        if not 6 <= len(password) <= 18:
            raise AppError(ErrorCode.INVALID_REQUEST, "密码长度需为 6 至 18 位", 422)

    @staticmethod
    def _verify_password(password: str, password_hash: str) -> bool:
        try:
            return PASSWORD_HASHER.verify(password_hash, password)
        except (VerificationError, InvalidHashError, TypeError):
            return False

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _now() -> str:
        return AuthService._format_time(datetime.now(UTC))

    @staticmethod
    def _account(row: dict[str, Any]) -> UserAccount:
        return UserAccount(
            id=str(row["id"]),
            login_identifier=str(row["login_identifier"]),
            role=str(row["role"]),
            status=str(row.get("status", "ACTIVE")),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
        )

    @staticmethod
    def _unauthenticated() -> AppError:
        return AppError(ErrorCode.AUTHENTICATION_REQUIRED, "请先登录", 401)
