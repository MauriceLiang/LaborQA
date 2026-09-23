"""生成评估和检索实验中保存的稳定运行时配置元数据。"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from app.core.config import Settings
from app.rag.constants import CURRENT_SPLITTER_VERSION
from app.rag.ai_modes import DEFAULT_AI_MODE, ai_mode_profile
from app.services.rerank import DEFAULT_RERANK_MODEL
from app.schemas.contracts import AiMode

# 聊天模型调用所使用的 LangChain Provider 标识。
DEFAULT_CHAT_PROVIDER = "langchain_openai.ChatOpenAI"
# 当前文本切分器的 Python 导入路径，用于实验结果溯源。
DEFAULT_SPLITTER_TYPE = "app.rag.splitters.LegalTextSplitter"
# 当前向量存储实现的导入路径，用于实验结果溯源。
DEFAULT_VECTORSTORE_TYPE = "langchain_community.vectorstores.FAISS"
# 当前默认检索策略的标识。
DEFAULT_RETRIEVAL_TYPE = "similarity"
# 当前默认提示词模板版本，用于区分不同实验配置。
DEFAULT_PROMPT_VERSION = "labor_langchain_v1"


@lru_cache(maxsize=1)
def langchain_version() -> str:
    """读取 LangChain Core 版本，供运行配置和实验结果复现使用。
    """

    try:
        return package_version("langchain-core")
    except PackageNotFoundError:
        return "unknown"


def runtime_config_snapshot(
    config: Settings,
    *,
    ai_mode: AiMode = DEFAULT_AI_MODE,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> dict[str, Any]:
    """合并模型、AI 模式、嵌入及检索器信息为可持久化的配置快照。

    参数：config 为当前服务设置；ai_mode 为本次执行采用的 AI 模式；
    prompt_version 为提示词模板的版本标识。
    返回：包含关键运行参数的字典，供评估和检索实验记录使用。
    """

    # 获取 AI 模式对应的推理开关和推理强度配置。
    profile = ai_mode_profile(ai_mode)
    return {
        "langchain_version": langchain_version(),
        "chat_provider": DEFAULT_CHAT_PROVIDER,
        "llm_model": config.llm_model,
        "ai_mode": AiMode(ai_mode).value,
        "rewrite_thinking": True,
        "rewrite_effort": "high",
        "rewrite_temperature": config.llm_rewrite_temperature,
        "rewrite_top_p": config.llm_rewrite_top_p,
        "rewrite_max_tokens": config.llm_rewrite_max_tokens,
        "evidence_thinking": profile.evidence_thinking,
        "evidence_effort": profile.evidence_effort,
        "evidence_temperature": config.llm_evidence_temperature,
        "evidence_top_p": config.llm_evidence_top_p,
        "evidence_max_tokens": config.llm_evidence_max_tokens,
        "answer_thinking": profile.answer_thinking,
        "answer_effort": profile.answer_effort,
        "answer_temperature": config.llm_answer_temperature,
        "answer_top_p": config.llm_answer_top_p,
        "answer_max_tokens": config.llm_answer_max_tokens,
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "embedding_normalize": config.embedding_normalize,
        "splitter_type": DEFAULT_SPLITTER_TYPE,
        "splitter_version": CURRENT_SPLITTER_VERSION,
        "vectorstore_type": DEFAULT_VECTORSTORE_TYPE,
        "retrieval_type": DEFAULT_RETRIEVAL_TYPE,
        "rerank_model": (DEFAULT_RERANK_MODEL if config.rerank_enabled else None),
        "prompt_version": prompt_version,
    }
