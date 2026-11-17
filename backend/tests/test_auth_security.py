"""验证登录、CSRF、角色权限以及用户级会话归属。"""

import sqlite3
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.contracts import router as contracts_router
from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.database import initialize_database
from app.core.error_codes import ErrorCode
from app.core.errors import AppError, register_exception_handlers
from app.services.auth_service import AuthService, UserAccount
from app.services.session_service import SessionService


@pytest.fixture
def auth_test_client(tmp_path: Path):
    database_path = tmp_path / "auth.db"
    previous_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    initialize_database()

    service = AuthService(database_path=database_path)
    session_service = SessionService(database_path=database_path)
    app = FastAPI()
    app.state.auth_service = service

    @app.post("/api/test/sessions", status_code=201)
    def create_test_session(
        user: Annotated[UserAccount, Depends(get_current_user)],
    ):
        return session_service.create_session(owner_user_id=user.id)

    @app.get("/api/test/sessions/{session_id}")
    def read_test_session(
        session_id: str,
        user: Annotated[UserAccount, Depends(get_current_user)],
    ):
        session = session_service.get_session(session_id, owner_user_id=user.id)
        if session is None:
            raise AppError(ErrorCode.SESSION_NOT_FOUND, "会话不存在", 404)
        return session

    @app.post("/api/test/sessions/{session_id}/messages")
    def write_test_message(
        session_id: str,
        user: Annotated[UserAccount, Depends(get_current_user)],
    ):
        return session_service.create_user_message(
            session_id, "仅本账号可写入", owner_user_id=user.id
        )

    app.include_router(auth_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(contracts_router, prefix="/api")
    register_exception_handlers(app)
    try:
        with TestClient(app) as client:
            yield client, service, database_path
    finally:
        settings.database_url = previous_database_url


def _origin() -> str:
    return settings.cors_origins[0]


def _login(client: TestClient, identifier: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={"login_identifier": identifier, "password": password},
        headers={"Origin": _origin()},
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_login_uses_argon2id_http_only_cookie_and_csrf_protection(auth_test_client):
    client, service, database_path = auth_test_client
    service.create_user("worker", "correct-horse-1234", "USER")

    result = _login(client, "worker", "correct-horse-1234")

    cookie = client.cookies.get("laborqa_session")
    assert cookie
    assert result["user"]["login_identifier"] == "worker"
    assert "password_hash" not in result["user"]
    set_cookie = client.cookies.jar
    cookie_item = next(item for item in set_cookie if item.name == "laborqa_session")
    assert cookie_item.has_nonstandard_attr("HttpOnly")
    assert cookie_item.get_nonstandard_attr("SameSite") == "lax"

    with sqlite3.connect(database_path) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM app_user WHERE login_identifier = 'worker'"
        ).fetchone()[0]
        token_hash = connection.execute(
            "SELECT token_hash FROM auth_session"
        ).fetchone()[0]
    assert password_hash.startswith("$argon2id$")
    assert cookie not in {password_hash, token_hash}

    current = client.get("/api/auth/me")
    assert current.status_code == 200
    current_csrf_token = current.json()["data"]["csrf_token"]

    rejected = client.post("/api/auth/logout", headers={"Origin": _origin()})
    assert rejected.status_code == 403
    assert rejected.json()["code"] == ErrorCode.CSRF_VALIDATION_FAILED

    accepted = client.post(
        "/api/auth/logout",
        headers={"Origin": _origin(), "X-CSRF-Token": current_csrf_token},
    )
    assert accepted.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_change_password_rotates_current_session_and_revokes_other_sessions(
    auth_test_client,
):
    client, service, _ = auth_test_client
    service.create_user("worker", "correct-horse-1234", "USER")
    _login(client, "worker", "correct-horse-1234")
    other_client = TestClient(client.app)
    _login(other_client, "worker", "correct-horse-1234")
    payload = {
        "current_password": "correct-horse-1234",
        "new_password": "new-correct-horse-5678",
    }

    rejected = client.post(
        "/api/auth/change-password",
        json=payload,
        headers={"Origin": _origin()},
    )
    assert rejected.status_code == 403
    current = client.get("/api/auth/me")
    assert current.status_code == 200

    changed = client.post(
        "/api/auth/change-password",
        json=payload,
        headers={
            "Origin": _origin(),
            "X-CSRF-Token": current.json()["data"]["csrf_token"],
        },
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["user"]["login_identifier"] == "worker"
    assert client.get("/api/auth/me").status_code == 200
    assert other_client.get("/api/auth/me").status_code == 401
    old_password_client = TestClient(client.app)
    old_password_login = old_password_client.post(
        "/api/auth/login",
        json={"login_identifier": "worker", "password": "correct-horse-1234"},
        headers={"Origin": _origin()},
    )
    assert old_password_login.status_code == 401
    new_password_login = _login(
        TestClient(client.app), "worker", "new-correct-horse-5678"
    )
    assert new_password_login["user"]["login_identifier"] == "worker"


def test_roles_and_session_owner_are_enforced_server_side(auth_test_client):
    client, service, _ = auth_test_client
    service.create_user("worker-a", "correct-horse-1234", "USER")
    service.create_user("worker-b", "another-horse-1234", "USER")
    service.create_user("knowledge-admin", "know-admin-123456", "KNOWLEDGE_ADMIN")
    user_a = _login(client, "worker-a", "correct-horse-1234")
    session_a = client.post(
        "/api/test/sessions",
        headers={"Origin": _origin(), "X-CSRF-Token": user_a["csrf_token"]},
    )
    assert session_a.status_code == 201
    session_id = session_a.json()["id"]

    worker_b_client = TestClient(client.app)
    user_b = _login(worker_b_client, "worker-b", "another-horse-1234")
    assert user_b["user"]["role"] == "USER"
    assert worker_b_client.get(f"/api/test/sessions/{session_id}").status_code == 404
    denied_write = worker_b_client.post(
        f"/api/test/sessions/{session_id}/messages",
        headers={"Origin": _origin(), "X-CSRF-Token": user_b["csrf_token"]},
    )
    assert denied_write.status_code == 404
    assert worker_b_client.get("/api/admin/users").status_code == 403
    assert (
        worker_b_client.get("/api/evaluations/cases?page=1&size=10").status_code == 403
    )

    knowledge_admin_client = TestClient(client.app)
    _login(knowledge_admin_client, "knowledge-admin", "know-admin-123456")
    assert (
        knowledge_admin_client.get("/api/evaluations/cases?page=1&size=10").status_code
        == 403
    )


def test_disabling_a_user_revokes_existing_sessions(auth_test_client):
    client, service, _ = auth_test_client
    admin = service.create_user(
        "root-admin",
        "strong administrator passphrase",
        "SYSTEM_ADMIN",
        initial_admin=True,
    )
    user = service.create_user("worker", "correct-horse-1234", "USER")
    admin_identity = _login(
        client, admin.login_identifier, "strong administrator passphrase"
    )
    user_client = TestClient(client.app)
    _login(user_client, user.login_identifier, "correct-horse-1234")

    response = client.patch(
        f"/api/admin/users/{user.id}",
        json={"role": "USER", "status": "DISABLED"},
        headers={
            "Origin": _origin(),
            "X-CSRF-Token": admin_identity["csrf_token"],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "DISABLED"
    assert user_client.get("/api/auth/me").status_code == 401


def test_login_failure_limit_is_keyed_by_account_and_ip(auth_test_client):
    client, service, _ = auth_test_client
    service.create_user("worker", "correct-horse-1234", "USER")
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={"login_identifier": "worker", "password": "wrong password"},
            headers={"Origin": _origin()},
        )
        assert response.status_code == 401
        assert response.json()["message"] == "账号或密码错误"

    limited = client.post(
        "/api/auth/login",
        json={"login_identifier": "worker", "password": "correct-horse-1234"},
        headers={"Origin": _origin()},
    )
    assert limited.status_code == 429


def test_admin_created_user_password_must_be_between_6_and_18_characters(
    auth_test_client,
):
    client, service, _ = auth_test_client
    admin = service.create_user(
        "root-admin",
        "strong administrator passphrase",
        "SYSTEM_ADMIN",
        initial_admin=True,
    )
    identity = _login(client, admin.login_identifier, "strong administrator passphrase")
    headers = {
        "Origin": _origin(),
        "X-CSRF-Token": identity["csrf_token"],
    }

    accepted_passwords = (
        ("short-password", "123456"),
        ("long-password", "a" * 18),
    )
    for identifier, password in accepted_passwords:
        response = client.post(
            "/api/admin/users",
            json={
                "login_identifier": identifier,
                "password": password,
                "role": "USER",
            },
            headers=headers,
        )
        assert response.status_code == 201

    invalid_short = client.post(
        "/api/admin/users",
        json={
            "login_identifier": "too-short",
            "password": "12345",
            "role": "USER",
        },
        headers=headers,
    )
    invalid_long = client.post(
        "/api/admin/users",
        json={
            "login_identifier": "too-long",
            "password": "a" * 19,
            "role": "USER",
        },
        headers=headers,
    )
    assert invalid_short.status_code == 422
    assert invalid_long.status_code == 422

    short_account = _login(TestClient(client.app), "short-password", "123456")
    assert short_account["user"]["role"] == "USER"


def test_development_admin_is_seeded_once_with_short_login_password(
    auth_test_client, monkeypatch
):
    client, service, _ = auth_test_client
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_dev_seed_admin_enabled", True)
    monkeypatch.setattr(settings, "auth_dev_seed_admin_identifier", "admin1")
    monkeypatch.setattr(settings, "auth_dev_seed_admin_password", "123456")

    assert service.ensure_development_admin() is True
    assert service.ensure_development_admin() is False

    result = _login(client, "admin1", "123456")
    assert result["user"]["role"] == "SYSTEM_ADMIN"


def test_development_admin_seed_is_disabled_in_production(
    auth_test_client, monkeypatch
):
    _, service, _ = auth_test_client
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_dev_seed_admin_enabled", True)

    with pytest.raises(RuntimeError, match="outside development"):
        service.ensure_development_admin()
    assert service.repository.has_system_admin() is False


def test_legacy_sessions_are_quarantined_instead_of_claimed(tmp_path: Path):
    database_path = tmp_path / "legacy.db"
    previous_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """CREATE TABLE session (
                       id VARCHAR(36) PRIMARY KEY, title VARCHAR(100),
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            connection.execute(
                "INSERT INTO session (id, title) VALUES ('legacy-id', '匿名历史')"
            )
        initialize_database()
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT owner_user_id, ownership_state FROM session WHERE id = 'legacy-id'"
            ).fetchone()
        assert row == (None, "LEGACY_QUARANTINED")
    finally:
        settings.database_url = previous_database_url
