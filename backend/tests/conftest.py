"""为历史业务接口测试提供固定系统管理员身份；认证边界测试使用独立 TestApp。"""

import pytest
from fastapi import Request

from app.api.dependencies import get_current_user
from app.services.auth_service import UserAccount

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


@pytest.fixture(autouse=True)
def authenticated_legacy_contract_tests(request: pytest.FixtureRequest):
    """让既有业务契约测试继续验证业务行为，不把测试身份冒充为安全验收。"""
    if "app" not in vars(request.module):
        yield
        return

    from app.main import app

    if getattr(request.module, "app", None) is not app:
        yield
        return

    previous = app.dependency_overrides.get(get_current_user)
    test_user = UserAccount(
        id=TEST_USER_ID,
        login_identifier="test-system-admin",
        role="SYSTEM_ADMIN",
    )

    def get_legacy_test_user(request: Request) -> UserAccount:
        """确保 owner 外键存在；业务契约测试仍使用模拟的管理员权限。"""
        repository = request.app.state.auth_service.repository
        if repository.get_user(TEST_USER_ID) is None:
            repository.create_user(
                TEST_USER_ID,
                "test-system-admin",
                "test-only-unused-password-hash",
                "USER",
            )
        return test_user

    app.dependency_overrides[get_current_user] = get_legacy_test_user
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous
