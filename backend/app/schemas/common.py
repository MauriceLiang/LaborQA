# 文件作用：定义通用 API 响应、分页、健康状态和 UTC 时间序列化模型。
from datetime import UTC, datetime
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

# API 响应和分页模型使用的通用数据类型参数。
T = TypeVar("T")


def _as_utc(value: datetime) -> datetime:
    """将无时区时间按 UTC 解释，并把带时区时间转换为 UTC。

    参数：
    value：当前待校验、转换或展示的值。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    """将时间序列化为以 Z 结尾的 UTC ISO 8601 字符串。

    参数：
    value：当前待校验、转换或展示的值。
    """
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# 统一按 UTC 校验并序列化的日期时间字段类型。
UtcDateTime = Annotated[
    datetime,
    AfterValidator(_as_utc),
    PlainSerializer(_format_utc, return_type=str, when_used="json"),
]


# 类作用：所有 API 数据模型共用的校验基类。
class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# 类作用：业务接口统一返回的状态码、消息和数据结构。
class ApiResponse(ApiModel, Generic[T]):
    # 字段说明：业务响应状态码。
    code: int
    # 字段说明：服务健康信息或响应提示文本。
    message: str
    # 字段说明：响应或请求中的业务数据。
    data: T | None


# 类作用：通用分页结果及页码统计信息。
class PageResult(ApiModel, Generic[T]):
    # 字段说明：当前页返回的数据记录列表。
    items: list[T]
    # 字段说明：请求页码。
    page: int = Field(ge=1)
    # 字段说明：每页条数。
    size: int = Field(ge=1, le=100)
    # 字段说明：符合查询条件的记录总数。
    total: int = Field(ge=0)
    # 字段说明：总页数。
    pages: int = Field(ge=0)


# 类作用：服务依赖组件和模型的健康状态。
class HealthData(ApiModel):
    # 字段说明：组件健康状态。
    service: str
    # 字段说明：数据库状态。
    database: str
    # 字段说明：向量存储状态。
    vector_store: str
    # 字段说明：文档转换器状态。
    doc_converter: str
    # 字段说明：大模型配置是否齐全。
    llm_configured: bool
    # 字段说明：嵌入服务类型。
    embedding_provider: str
    # 字段说明：嵌入模型标识。
    embedding_model: str
    # 字段说明：嵌入服务是否可用。
    embedding_ready: bool
    # 字段说明：索引兼容性检查结果。
    embedding_index_compatible: bool


# 类作用：通用分页查询的页码和页大小。
class PageQuery(ApiModel):
    # 字段说明：请求页码。
    page: int = Field(default=1, ge=1)
    # 字段说明：每页条数。
    size: int = Field(default=20, ge=1, le=100)
