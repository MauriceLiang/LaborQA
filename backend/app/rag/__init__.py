# 文件作用：声明检索增强生成（RAG）组件包及其公共导出。
"""LangChain integration boundary for the RAG implementation."""

from app.rag.providers import build_chat_model, build_embeddings

# RAG 组件包公开的模型与嵌入构造函数。
__all__ = ["build_chat_model", "build_embeddings"]
