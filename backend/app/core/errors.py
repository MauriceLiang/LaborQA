# 文件作用：定义应用级异常类型，并将异常统一转换为 API 响应。
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.error_codes import ErrorCode

# 当前模块使用的日志记录器，用于记录未处理异常。
logger = logging.getLogger(__name__)


class AppError(Exception):
    """携带稳定业务错误码、用户提示和 HTTP 状态的应用异常。"""

    def __init__(self, code: int, message: str, http_status: int) -> None:
        """初始化业务异常及其 API 响应信息。

        参数：code 为业务错误码；message 为面向调用方的错误提示；
        http_status 为响应使用的 HTTP 状态码。
        """
        super().__init__(message)
        # 成员变量 code：业务状态、错误或分类代码。
        self.code = code  # 对外返回的稳定业务错误码。
        # 成员变量 message：需要记录、保存或返回的消息文本。
        self.message = message  # 可安全展示给 API 调用方的错误说明。
        # 成员变量 http_status：异常响应使用的 HTTP 状态码。
        self.http_status = http_status  # 对应的 HTTP 响应状态码。


def _error_body(code: int, message: str) -> dict[str, Any]:
    """构造所有错误响应共用的 code/message/data 外层结构。

    参数：code 为业务错误码；message 为错误说明。
    返回：符合统一 API 响应格式的错误字典。
    """
    return {"code": int(code), "message": message, "data": None}


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 应用中注册业务、校验、HTTP 和未预期异常处理器。

    参数：app 为需要安装异常处理逻辑的 FastAPI 实例。
    """

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """将 AppError 转换为包含业务错误码的 JSON 响应。

        参数：request 为触发异常的 HTTP 请求；exc 为业务异常实例。
        返回：状态码取自异常、内容遵循统一错误结构的 JSON 响应。
        """
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """将请求体校验失败转换为统一格式，并区分实验配置错误。

        参数：request 为触发校验失败的请求；exc 为 FastAPI 校验错误。
        返回：包含对应业务错误码和状态码的 JSON 响应。
        """
        # 对检索实验创建接口使用专属配置错误码，其余使用通用请求错误码。
        code = (
            ErrorCode.INVALID_EXPERIMENT_CONFIG
            if request.method == "POST"
            and request.url.path.endswith("/retrieval-experiments")
            else ErrorCode.INVALID_REQUEST
        )
        return JSONResponse(
            status_code=400 if code == ErrorCode.INVALID_EXPERIMENT_CONFIG else 422,
            content=_error_body(code, "请求参数不合法"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """将 Starlette HTTP 异常转换为项目统一错误响应。

        参数：request 为触发异常的请求；exc 为原始 HTTP 异常。
        返回：保留原 HTTP 状态及响应头的 JSON 错误响应。
        """
        # 根据 HTTP 状态确定稳定业务错误码及面向调用方的提示文本。
        if exc.status_code == 404:
            # 局部变量 code：业务状态、错误或分类代码。
            code = ErrorCode.INVALID_REQUEST
            # 局部变量 message：需要记录、保存或返回的消息文本。
            message = "请求路径不存在"
        else:
            code = (
                ErrorCode.INVALID_REQUEST
                if exc.status_code in (400, 422)
                else ErrorCode.INTERNAL_ERROR
            )
            message = str(exc.detail) if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, message),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """记录未预期异常的堆栈和请求标识，并返回通用 500 响应。

        参数：request 为触发异常的请求；exc 为未捕获的异常对象。
        返回：不泄漏内部细节的服务端错误 JSON 响应。
        """
        logger.exception(
            "Unhandled request error",
            exc_info=exc,
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(ErrorCode.INTERNAL_ERROR, "服务内部错误"),
        )
