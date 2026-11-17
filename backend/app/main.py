# 文件作用：创建 FastAPI 应用，管理服务生命周期、中间件、路由和异常处理。
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.contracts import router as contracts_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.database import initialize_database
from app.core.errors import register_exception_handlers
from app.core.logging_config import configure_logging
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.evaluation_service import EvaluationService
from app.services.retrieval import RetrievalService
from app.services.retrieval_experiment_service import RetrievalExperimentService
from app.services.session_service import SessionService

# 当前模块的日志记录器，用于报告应用启动状态。
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """初始化数据库和业务服务，并在应用运行期间维持服务上下文。

    参数：app 为正在启动的 FastAPI 应用实例。
    """
    configure_logging()
    initialize_database()
    # 认证服务与业务仓储共用同一数据库，统一验证账户、Cookie 会话和角色。
    app.state.auth_service = AuthService(database_path=settings.database_path)
    app.state.auth_service.ensure_initial_admin()
    # 文档业务服务，负责恢复未完成导入并提供文档、向量存储能力。
    document_service = DocumentService(settings)
    document_service.recover_interrupted_imports()
    app.state.document_service = document_service
    # 会话服务使用应用配置中的数据库路径持久化聊天记录。
    session_service = SessionService(database_path=settings.database_path)
    app.state.session_service = session_service
    # 聊天服务组合会话存储、检索能力与应用模型配置。
    chat_service = ChatService(
        session_service,
        RetrievalService(
            settings,
            repository=document_service.repository,
            embedding_service=document_service.embedding_service,
            vector_store=document_service.vector_store,
        ),
        settings,
    )
    app.state.chat_service = chat_service
    # 评估服务复用聊天流程并初始化评估相关状态。
    evaluation_service = EvaluationService(chat_service, settings)
    evaluation_service.initialize()
    app.state.evaluation_service = evaluation_service
    # 检索实验服务复用聊天、文档、评估和嵌入组件执行实验。
    experiment_service = RetrievalExperimentService(
        chat_service,
        settings,
        document_repository=document_service.repository,
        evaluation_service=evaluation_service,
        embedding_service=document_service.embedding_service,
    )
    experiment_service.initialize()
    app.state.retrieval_experiment_service = experiment_service
    logger.info("Application startup complete")
    yield


# 供 ASGI 服务器加载的 FastAPI 应用入口实例。
app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(admin_router, prefix=settings.api_prefix)
app.include_router(contracts_router, prefix=settings.api_prefix)
register_exception_handlers(app)


@app.middleware("http")
async def add_request_id(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """为每个 HTTP 请求生成唯一标识并写入响应头。

    参数：request 为当前请求；call_next 为调用后续中间件和路由的函数。
    返回：附带 X-Request-ID 响应头的下游响应。
    """
    # 当前请求的唯一十六进制追踪标识。
    request_id = uuid4().hex
    request.state.request_id = request_id
    # 执行后续请求处理链得到待返回的 HTTP 响应。
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
