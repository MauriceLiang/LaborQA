# 文件作用：从 FastAPI 请求上下文中取得已初始化的业务服务实例。
from fastapi import Request

from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.evaluation_service import EvaluationService
from app.services.retrieval_experiment_service import RetrievalExperimentService


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
