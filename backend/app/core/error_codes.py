"""集中定义 API 业务错误码，供服务层和接口层统一返回错误标识。"""

from enum import IntEnum


class ErrorCode(IntEnum):
    """跨接口稳定使用的业务错误码枚举。"""

    UNSUPPORTED_FILE_TYPE = 40001  # 上传文件的扩展名不在允许范围内。
    EMPTY_FILE = 40002  # 上传文件为空或没有可处理内容。
    INVALID_REQUEST = 40003  # 请求字段、格式或文件 MIME 类型无效。
    INVALID_EXPERIMENT_CONFIG = 40005  # 检索实验配置不符合约束。
    DOCUMENT_NOT_FOUND = 40401  # 指定文档不存在。
    SESSION_NOT_FOUND = 40402  # 指定聊天会话不存在。
    EVALUATION_RUN_NOT_FOUND = 40403  # 指定评估运行记录不存在。
    EXPERIMENT_NOT_FOUND = 40404  # 指定检索实验不存在。
    MISSING_KNOWLEDGE_NOT_FOUND = 40405  # 指定缺失知识记录不存在。
    EVALUATION_CASE_NOT_FOUND = 40406  # 指定评估用例不存在。
    RETRIEVAL_STRATEGY_NOT_FOUND = 40407  # 指定检索策略不存在。
    INVALID_DOCUMENT_STATE = 40901  # 文档当前状态不允许执行请求操作。
    JOB_ALREADY_RUNNING = 40902  # 对应后台任务已经在运行。
    INVALID_EVALUATION_RUN_STATE = 40903  # 评估运行状态不允许当前操作。
    BUILTIN_CASE_READ_ONLY = 40904  # 内置评估用例不允许修改。
    CASE_IN_ACTIVE_RUN = 40905  # 评估用例正在运行中，暂不能修改。
    CASE_VERSION_CONFLICT = 40906  # 用例版本与提交操作时的版本不一致。
    FILE_TOO_LARGE = 41301  # 上传文件超过大小限制。
    TOOL_VALIDATION_ERROR = 42202  # 工具调用参数未通过业务校验。
    KNOWLEDGE_BASE_NOT_READY = 50301  # 知识库或向量索引尚未就绪。
    MODEL_UNAVAILABLE = 50302  # 大语言模型服务当前不可用。
    EMBEDDING_UNAVAILABLE = 50303  # 文本向量化服务当前不可用。
    DOC_CONVERTER_UNAVAILABLE = 50304  # DOC 文档转换组件当前不可用。
    INDEX_EMBEDDING_MISMATCH = 50305  # 当前向量模型与已有索引配置不兼容。
    INTERNAL_ERROR = 50000  # 未分类的服务端内部错误。
