"""认证账户、服务端登录会话、限流计数和审计记录的 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings


class AuthRepository:
    """将账户和登录凭据变更封装在 SQLite 事务内。"""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or settings.database_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_user(
        self,
        user_id: str,
        identifier: str,
        password_hash: str,
        role: str,
        *,
        initial_admin: bool = False,
    ) -> dict[str, Any]:
        """创建普通账号，或在单个写事务中创建唯一的首位系统管理员。"""
        with self._connection() as connection:
            if initial_admin:
                connection.execute("BEGIN IMMEDIATE")
                exists = connection.execute(
                    "SELECT 1 FROM app_user WHERE role = 'SYSTEM_ADMIN' LIMIT 1"
                ).fetchone()
                if exists:
                    raise ValueError("系统管理员已初始化")
            connection.execute(
                "INSERT INTO app_user (id, login_identifier, password_hash, role) "
                "VALUES (?, ?, ?, ?)",
                (user_id, identifier, password_hash, role),
            )
            row = connection.execute(
                "SELECT * FROM app_user WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row)

    def get_user_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM app_user WHERE login_identifier = ?", (identifier,)
            ).fetchone()
            return dict(row) if row is not None else None

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM app_user WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def has_system_admin(self) -> bool:
        """判断系统管理员是否已初始化。"""
        with self._connection() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM app_user WHERE role = 'SYSTEM_ADMIN' LIMIT 1"
                ).fetchone()
                is not None
            )

    def list_users(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, login_identifier, role, status, created_at, updated_at "
                "FROM app_user ORDER BY created_at, login_identifier"
            ).fetchall()
            return [dict(row) for row in rows]

    def create_session(
        self,
        session_id: str,
        user_id: str,
        token_hash: str,
        csrf_token_hash: str,
        now: str,
        expires_at: str,
        idle_expires_at: str,
        device_info: str | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """DELETE FROM auth_session
                   WHERE revoked_at IS NOT NULL OR expires_at <= ?
                     OR idle_expires_at <= ?""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO auth_session (
                    id, user_id, token_hash, csrf_token_hash, created_at, last_seen_at,
                    expires_at, idle_expires_at, device_info
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    user_id,
                    token_hash,
                    csrf_token_hash,
                    now,
                    now,
                    expires_at,
                    idle_expires_at,
                    device_info,
                ),
            )

    def authenticate_session(
        self, token_hash: str, now: str, idle_expires_at: str
    ) -> dict[str, Any] | None:
        """验证登录会话并在同一事务内续期空闲过期时间。"""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT s.id AS auth_session_id, u.id AS id, s.user_id,
                          s.csrf_token_hash,
                          u.login_identifier, u.role, u.status, u.created_at, u.updated_at
                   FROM auth_session AS s
                   JOIN app_user AS u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.revoked_at IS NULL
                     AND s.expires_at > ? AND s.idle_expires_at > ?
                     AND u.status = 'ACTIVE'""",
                (token_hash, now, now),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE auth_session
                   SET last_seen_at = ?, idle_expires_at = MIN(expires_at, ?)
                   WHERE id = ? AND revoked_at IS NULL""",
                (now, idle_expires_at, row["auth_session_id"]),
            )
            return dict(row)

    def rotate_csrf_token(self, session_id: str, token_hash: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE auth_session SET csrf_token_hash = ? "
                "WHERE id = ? AND revoked_at IS NULL",
                (token_hash, session_id),
            )
            return cursor.rowcount == 1

    def revoke_session(
        self, session_id: str, now: str, user_id: str | None = None
    ) -> bool:
        with self._connection() as connection:
            sql = "UPDATE auth_session SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL"
            parameters: tuple[Any, ...] = (now, session_id)
            if user_id is not None:
                sql += " AND user_id = ?"
                parameters += (user_id,)
            cursor = connection.execute(sql, parameters)
            return cursor.rowcount == 1

    def revoke_token_hash(self, token_hash: str, now: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE auth_session SET revoked_at = ? "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                (now, token_hash),
            )

    def revoke_user_sessions(
        self, user_id: str, now: str, except_session_id: str | None = None
    ) -> None:
        with self._connection() as connection:
            sql = (
                "UPDATE auth_session SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL"
            )
            parameters: tuple[Any, ...] = (now, user_id)
            if except_session_id is not None:
                sql += " AND id <> ?"
                parameters += (except_session_id,)
            connection.execute(sql, parameters)

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT id, created_at, last_seen_at, expires_at, device_info
                   FROM auth_session WHERE user_id = ? AND revoked_at IS NULL
                     AND expires_at > CURRENT_TIMESTAMP
                     AND idle_expires_at > CURRENT_TIMESTAMP
                   ORDER BY created_at DESC, id DESC""",
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_user(
        self, user_id: str, role: str, status: str, now: str
    ) -> dict[str, Any] | None:
        """更新账号角色或状态，并在同一事务内撤销其已有登录会话。"""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute(
                "SELECT role FROM app_user WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                return None
            if user["role"] == "SYSTEM_ADMIN":
                raise ValueError("系统管理员账号不能通过管理 API 修改")
            connection.execute(
                """UPDATE app_user SET role = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (role, status, now, user_id),
            )
            connection.execute(
                "UPDATE auth_session SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
            row = connection.execute(
                """SELECT id, login_identifier, role, status, created_at, updated_at
                   FROM app_user WHERE id = ?""",
                (user_id,),
            ).fetchone()
            return dict(row)

    def change_password(self, user_id: str, password_hash: str, now: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE app_user SET password_hash = ?, password_changed_at = ?,
                   updated_at = ? WHERE id = ?""",
                (password_hash, now, now, user_id),
            )
            connection.execute(
                "UPDATE auth_session SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )

    def record_login_failure(
        self,
        key_type: str,
        key_hash: str,
        now: str,
        window_started_at: str,
        locked_until: str | None,
        threshold: int,
    ) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """DELETE FROM auth_login_attempt
                   WHERE window_started_at <= ?
                     AND (locked_until IS NULL OR locked_until <= ?)""",
                (window_started_at, now),
            )
            row = connection.execute(
                "SELECT window_started_at, failure_count FROM auth_login_attempt "
                "WHERE key_type = ? AND key_hash = ?",
                (key_type, key_hash),
            ).fetchone()
            if row is None or row["window_started_at"] <= window_started_at:
                count = 1
                start = now
            else:
                count = int(row["failure_count"]) + 1
                start = row["window_started_at"]
            effective_lock = locked_until if count >= threshold else None
            connection.execute(
                """INSERT INTO auth_login_attempt (
                       key_type, key_hash, window_started_at, failure_count, locked_until
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(key_type, key_hash) DO UPDATE SET
                       window_started_at = excluded.window_started_at,
                       failure_count = excluded.failure_count,
                       locked_until = excluded.locked_until""",
                (key_type, key_hash, start, count, effective_lock),
            )
            return count

    def is_login_limited(self, keys: list[tuple[str, str]], now: str) -> bool:
        with self._connection() as connection:
            for key_type, key_hash in keys:
                row = connection.execute(
                    "SELECT locked_until FROM auth_login_attempt "
                    "WHERE key_type = ? AND key_hash = ?",
                    (key_type, key_hash),
                ).fetchone()
                if row and row["locked_until"] and row["locked_until"] > now:
                    return True
        return False

    def clear_identifier_failures(self, key_hash: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM auth_login_attempt WHERE key_type = 'IDENTIFIER' AND key_hash = ?",
                (key_hash,),
            )

    def add_audit_event(
        self,
        actor_user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        result: str,
        request_id: str | None,
        now: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO audit_event (
                    actor_user_id, action, resource_type, resource_id, result,
                    request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    actor_user_id,
                    action,
                    resource_type,
                    resource_id,
                    result,
                    request_id,
                    now,
                ),
            )
