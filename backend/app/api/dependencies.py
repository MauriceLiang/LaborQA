# 文件作用：从 FastAPI 请求上下文中取得已初始化的业务服务实例。
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.services.auth_service import AuthService, UserAccount
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.evaluation_service import EvaluationService
from app.services.retrieval_experiment_service import RetrievalExperimentService

AUTH_COOKIE_NAME = "laborqa_session"


def get_auth_service(request: Request) -> AuthService:
    """读取应用生命周期中初始化的认证服务。"""
    return request.app.state.auth_service


def get_current_user(request: Request) -> UserAccount:
    """验证 HttpOnly 会话 Cookie，并对 Cookie 认证写请求执行来源和 CSRF 校验。"""
    service = get_auth_service(request)
    user, auth_session = service.authenticate(request.cookies.get(AUTH_COOKIE_NAME))
    request.state.auth_session_id = auth_session["auth_session_id"]

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _require_trusted_origin(request)
        if not service.verify_csrf_token(
            auth_session["csrf_token_hash"], request.headers.get("X-CSRF-Token")
        ):
            raise AppError(
                ErrorCode.CSRF_VALIDATION_FAILED, "请求校验失败，请刷新页面后重试", 403
            )
    return user


def require_roles(*roles: str):
    """创建限定角色集合的 API 依赖；服务端角色判断独立于前端导航。"""

    def dependency(
        user: Annotated[UserAccount, Depends(get_current_user)],
    ) -> UserAccount:
        if user.role not in roles:
            raise AppError(
                ErrorCode.AUTH_FORBIDDEN, "当前账号没有执行此操作的权限", 403
            )
        return user

    return dependency


def authorize_contract_access(
    request: Request,
    user: Annotated[UserAccount, Depends(get_current_user)],
) -> None:
    """按现有业务路由分组授权，保护知识维护、评测与系统监控接口。"""
    path = request.url.path.removeprefix(settings.api_prefix)
    method = request.method.upper()
    system_admin_paths = (
        "/evaluations",
        "/retrieval-strategies",
        "/retrieval-experiments",
        "/health",
    )
    if path.startswith(system_admin_paths):
        if user.role != "SYSTEM_ADMIN":
            raise AppError(
                ErrorCode.AUTH_FORBIDDEN, "当前账号没有执行此操作的权限", 403
            )
        return

    knowledge_admin_action = path.startswith("/missing-knowledge") or (
        path.startswith("/documents") and method != "GET"
    )
    if knowledge_admin_action and user.role not in {"KNOWLEDGE_ADMIN", "SYSTEM_ADMIN"}:
        raise AppError(ErrorCode.AUTH_FORBIDDEN, "当前账号没有执行此操作的权限", 403)


def require_trusted_origin(request: Request) -> None:
    """拒绝缺少或不允许的 Origin/Referer，登录入口也调用此校验。"""
    _require_trusted_origin(request)


def _require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("Origin")
    if not origin:
        referer = request.headers.get("Referer")
        if referer:
            parsed = urlsplit(referer)
            origin = (
                f"{parsed.scheme}://{parsed.netloc}"
                if parsed.scheme and parsed.netloc
                else None
            )
    candidate = origin.rstrip("/") if origin else None
    allowed = {value.rstrip("/") for value in settings.cors_origins}
    if candidate is None or candidate not in allowed:
        raise AppError(ErrorCode.CSRF_VALIDATION_FAILED, "请求来源校验失败", 403)


def get_document_service(request: Request) -> DocumentService:
    """取得应用启动时注册的文档业务服务。

    参数：request 为当前请求，用于访问应用状态。
    返回：文档服务实例。
    """
    return request.app.state.document_service


def get_chat_service(request: Request) -> ChatService:
    """取得应用启动时注册的聊天业务服务。

    参数：request 为当前请求，用于访问应用状态。
    返回：聊天服务实例。
    """
    return request.app.state.chat_service


def get_evaluation_service(request: Request) -> EvaluationService:
    """取得应用启动时注册的评估业务服务。

    参数：request 为当前请求，用于访问应用状态。
    返回：评估服务实例。
    """
    return request.app.state.evaluation_service


def get_retrieval_experiment_service(request: Request) -> RetrievalExperimentService:
    """取得应用启动时注册的检索实验业务服务。

    参数：request 为当前请求，用于访问应用状态。
    返回：检索实验服务实例。
    """
    return request.app.state.retrieval_experiment_service
