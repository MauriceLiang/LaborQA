# 文件作用：提供最小公共存活探针和受系统管理员保护的详细健康检查。
from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_roles
from app.core.config import settings
from app.core.database import database_is_ready
from app.core.schemas import ApiResponse, HealthData
from app.rag.providers import local_embedding_is_cached
from app.services.document_parser import is_doc_parser_available
from app.services.vector_store import VectorStoreSignatureMismatch

router = APIRouter()


@router.get("/health/live", response_model=ApiResponse[dict[str, str]])
def get_liveness() -> ApiResponse[dict[str, str]]:
    """提供不包含内部配置和组件详情的最小存活探针。"""
    return ApiResponse(code=0, message="success", data={"service": "ok"})


@router.get(
    "/health",
    response_model=ApiResponse[HealthData],
    dependencies=[Depends(require_roles("SYSTEM_ADMIN"))],
)
def get_health(request: Request) -> ApiResponse[HealthData]:
    """汇总数据库、向量索引、嵌入和文档转换器的健康状态。"""
    document_service = request.app.state.document_service
    vector_store = document_service.vector_store
    production_index = vector_store.index_path
    index_exists = production_index.is_file()
    converter_ready = is_doc_parser_available(settings)
    embedding_ready = (
        local_embedding_is_cached()
        if settings.embedding_provider == "local"
        else bool(
            settings.embedding_api_key
            and settings.embedding_base_url
            and settings.embedding_api_model
        )
    )
    try:
        compatible = vector_store.is_signature_compatible()
    except VectorStoreSignatureMismatch:
        compatible = False
    store_status = vector_store.status

    return ApiResponse(
        code=0,
        message="success",
        data=HealthData(
            service="ok",
            database="ok" if database_is_ready() else "unavailable",
            vector_store=(
                "ready" if index_exists and store_status == "ready" else store_status
            ),
            doc_converter="ready" if converter_ready else "unavailable",
            llm_configured=bool(settings.llm_api_key),
            embedding_provider=settings.embedding_provider,
            embedding_model=settings.embedding_model,
            embedding_ready=embedding_ready,
            embedding_index_compatible=compatible if index_exists else True,
        ),
    )
