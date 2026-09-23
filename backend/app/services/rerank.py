# 文件作用：封装候选文档片段的重排模型及默认配置。
"""Optional local cross-encoder reranking with retrieval-order fallback."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import Settings, settings

# 当前模块的日志记录器，用于记录重排回退情况。
logger = logging.getLogger(__name__)
# 默认本地 cross-encoder 重排模型名称。
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"
# 重排服务内部加载的默认模型标识。
_DEFAULT_MODEL = DEFAULT_RERANK_MODEL


class CrossEncoder(Protocol):
    """本地交叉编码器需要实现的最小预测接口。"""

    def predict(self, sentences: list[tuple[str, str]]) -> Sequence[float]:
        """为查询与候选文本对生成相关性分数。

        参数：
        sentences：从原文中识别出的句子列表。
        """
        ...


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str) -> Any:
    """从 Hugging Face 本地缓存加载交叉编码器模型。

    参数：model_name 为模型仓库标识。
    返回：可预测查询与文本相关性的模型实例。
    """
    from huggingface_hub import snapshot_download
    from sentence_transformers import CrossEncoder as SentenceCrossEncoder

    # model_path 是缓存中模型文件所在的本地目录。
    model_path = snapshot_download(model_name, local_files_only=True)
    return SentenceCrossEncoder(model_path)


class RerankService:
    """对向量检索候选进行交叉编码重排，失败时保留原顺序。"""

    def __init__(
        self,
        config: Settings = settings,
        *,
        model: CrossEncoder | None = None,
    ) -> None:
        """创建重排服务并允许注入设置和模型实例。

        参数：config 为重排数量等应用配置；model 为可选的交叉编码器。
        """
        # 成员变量 config：当前请求、服务或实验所使用的配置。
        self.config = config  # 控制重排结果保留数量的应用配置。
        # 成员变量 model：当前使用的模型或模型名称。
        self.model = model  # 注入的模型；为空时按需从本地缓存加载。

    def rerank(
        self, query: str, evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """按查询相关性重排候选证据并更新分数和名次。

        参数：query 为待检索问题；evidence 为含文本内容的候选片段列表。
        返回：按重排分数排序并截断后的证据列表；重排失败时返回原顺序副本。
        """
        if not evidence:
            return []
        try:
            # 本次使用的模型实例，优先采用调用方注入的模型。
            model = self.model or _load_cross_encoder(_DEFAULT_MODEL)
            # logits 保存模型为每个 query/evidence 对返回的原始分数。
            logits = model.predict([(query, str(item["content"])) for item in evidence])
            if len(logits) != len(evidence):
                raise ValueError("reranker result count does not match evidence count")
            # ranked 暂存原始位置、证据对象和归一化后的重排分数。
            ranked = [
                (index, item, _sigmoid(float(logit)))
                for index, (item, logit) in enumerate(
                    zip(evidence, logits, strict=True)
                )
            ]
            # result 元组中的分数用于降序排序，原位置用于稳定打破平分。
            ranked.sort(key=lambda result: (-result[2], result[0]))
            # 局部变量 rank_no：当前候选项在重排结果中的名次。
            # 局部变量 score：当前候选项或检索结果的相关度分数。
            return [
                {
                    **item,
                    "score": score,
                    "rerank_score": score,
                    "rank_no": rank_no,
                }
                for rank_no, (_, item, score) in enumerate(
                    ranked[: self.config.rerank_top_n], start=1
                )
            ]
        except Exception:
            logger.warning(
                "Rerank failed; retaining vector retrieval order", exc_info=True
            )
            return [dict(item) for item in evidence]


def _sigmoid(value: float) -> float:
    """将有限的模型原始分数压缩到 0 到 1 区间。

    参数：value 为交叉编码器返回的原始分数。
    返回：对应的 sigmoid 归一化分数。
    """
    if not math.isfinite(value):
        raise ValueError("reranker returned a non-finite score")
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    # 对负数分支先计算指数，避免直接计算导致数值溢出。
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)
