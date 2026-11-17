# 文件作用：定义 RAG 组件内部可识别的模型和嵌入异常类型。
"""定义 LangChain RAG 基础设施共用的模型和嵌入运行时异常。"""

from __future__ import annotations


class ModelUnavailableError(RuntimeError):
    """配置的聊天模型无法完成请求时抛出的异常。"""


class EmbeddingUnavailableError(RuntimeError):
    """配置的嵌入 Provider 无法将文本转换为向量时抛出的异常。"""


class EmbeddingDimensionUnknown(RuntimeError):
    """尚未获得嵌入向量、无法确定向量维度时抛出的异常。"""
