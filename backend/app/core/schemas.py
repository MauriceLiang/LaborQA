# 文件作用：汇总并导出 API 通用响应及健康检查数据模型。
from app.schemas.common import ApiResponse, HealthData

# 模块对外公开的响应和健康状态模型名称。
__all__ = ["ApiResponse", "HealthData"]
