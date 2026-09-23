# 文件作用：声明检索增强生成（RAG）组件包及其公共导出。
"""说明本包在 LaborQA 检索增强生成（RAG）流程中的集成边界：LangChain 链、嵌入与切分实现归属本包，上层业务编排和数据持久化分别由服务层与仓储层负责。"""

from app.rag.providers import build_chat_model, build_embeddings

# RAG 组件包公开的模型与嵌入构造函数。
__all__ = ["build_chat_model", "build_embeddings"]
