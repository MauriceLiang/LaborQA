# 文件作用：提供服务健康检查接口并汇总数据库、索引、模型和文档转换状态。
from fastapi import APIRouter, Request

from app.core.config import settings
from app.core.database import database_is_ready
from app.core.schemas import ApiResponse, HealthData
from app.rag.providers import local_embedding_is_cached
from app.services.document_parser import is_doc_parser_available
from app.services.vector_store import VectorStoreSignatureMismatch

# 健康检查路由集合，由主应用统一挂载 API 前缀。
router = APIRouter()


@router.get("/health", response_model=ApiResponse[HealthData])
def get_health(request: Request) -> ApiResponse[HealthData]:
    """汇总数据库、向量索引、嵌入和文档转换器的健康状态。

    参数：request 为当前请求，用于读取应用级服务实例。
    返回：遵循统一 API 格式的健康状态数据。
    """
    # 从应用状态读取已初始化的文档服务。
    document_service = request.app.state.document_service
    # 文档服务持有的向量存储实例。
    vector_store = document_service.vector_store
    # 正式环境向量索引所在的文件路径。
    production_index = vector_store.index_path
    # 用于判断索引是否已经落盘。
    index_exists = production_index.is_file()
    # 当前配置对应的 DOC 文档解析或转换组件是否可用。
    converter_ready = is_doc_parser_available(settings)
    # 按当前 Provider 检查嵌入模型缓存或远程 API 配置是否齐全。
    embedding_ready = (
        local_embedding_is_cached()
        if settings.embedding_provider == "local"
        else bool(
            settings.embedding_api_key
            and settings.embedding_base_url
            and settings.embedding_api_model
        )
    )
    # 索引签名与当前嵌入设置是否兼容；不兼容时按未兼容处理。
    try:
        # 局部变量 compatible：当前模型与已有索引是否兼容。
        compatible = vector_store.is_signature_compatible()
    except VectorStoreSignatureMismatch:
        compatible = False
    # 向量存储报告的当前初始化或可用状态。
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
