# 文件作用：封装文本嵌入模型的创建、调用、校验及可用性处理。
import logging
import math
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from langchain_core.embeddings import Embeddings

from app.core.config import Settings, settings
from app.rag.errors import EmbeddingDimensionUnknown, EmbeddingUnavailableError
from app.rag.providers import build_embeddings
from app.schemas.contracts import EmbeddingSignature

# 嵌入 Provider 创建、请求及向量校验日志记录器。
logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    # 当前嵌入服务提供方的名称标识。
    name: str
    # 当前嵌入服务使用的模型名称。
    model: str
    # 生成向量时是否执行归一化。
    normalize_embeddings: bool
    # Provider 已知的向量维度；未知时为空。
    dimension: int | None

    # 将一组文档文本编码为向量列表。
    # 参数：texts 为待批量编码的文档文本。
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class _LangChainEmbeddingProvider:
    """把底层 LangChain Embeddings 统一适配为项目的嵌入 Provider，并记录 Provider、模型、归一化设置和首次计算出的向量维度，供索引签名校验使用。"""

    def __init__(self, config: Settings, embeddings: Embeddings) -> None:
        """初始化嵌入适配器及其配置或底层 Provider。

        参数：
        config：当前应用或实验配置。
        embeddings：底层文本嵌入模型实例。
        """
        # 成员变量 name：经过规范化或校验的资源名称。
        self.name = config.embedding_provider
        # 成员变量 model：当前使用的模型或模型名称。
        self.model = config.embedding_model
        # 成员变量 normalize_embeddings：是否对生成向量执行归一化。
        self.normalize_embeddings = config.embedding_normalize
        # 成员变量 embeddings：底层文本嵌入模型实例。
        self.embeddings = embeddings
        # 成员变量 dimension：嵌入向量的维度。
        self.dimension: int | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """将一组文档文本编码为向量列表。

        参数：
        texts：待批量处理的文本列表。
        """
        _validate_texts(texts)
        try:
            # 局部变量 vectors：文档或查询文本对应的数值向量集合。
            vectors = self.embeddings.embed_documents(texts)
            # 局部变量 result：已通过数量、维度和数值有效性检查的批量向量；若启用归一化，随后会逐条替换为单位长度向量。
            result = _validate_vectors(vectors, len(texts))
            if self.normalize_embeddings:
                # 局部变量 vector：单条文本对应的数值向量。
                result = [_normalize(vector) for vector in result]
            # 成员变量 dimension：嵌入向量的维度。
            self.dimension = len(result[0])
            return result
        except EmbeddingUnavailableError:
            raise
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except Exception as exc:
            logger.exception("LangChain embedding provider failed")
            raise EmbeddingUnavailableError("向量服务暂不可用") from exc

    def embed_query(self, text: str) -> list[float]:
        """将单条查询文本编码为向量。

        参数：
        text：待处理的文本内容。
        """
        _validate_texts([text])
        try:
            # 局部变量 vector：单条文本对应的数值向量。
            vector = _validate_vectors([self.embeddings.embed_query(text)], 1)[0]
            if self.normalize_embeddings:
                vector = _normalize(vector)
            # 成员变量 dimension：嵌入向量的维度。
            self.dimension = len(vector)
            return vector
        except EmbeddingUnavailableError:
            raise
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except Exception as exc:
            logger.exception("LangChain embedding provider failed")
            raise EmbeddingUnavailableError("向量服务暂不可用") from exc


class _LazyEmbeddings(Embeddings):
    """延迟创建底层嵌入模型：应用初始化时只保存工厂函数，第一次执行文本或查询向量化时才创建模型，并缓存实例供后续调用复用。"""

    def __init__(self, factory: Callable[[], Embeddings]) -> None:
        """初始化嵌入适配器及其配置或底层 Provider。

        参数：
        factory：创建底层模型实例的工厂函数。
        """
        # 成员变量 _factory：延迟创建底层 Provider 的工厂函数。
        self._factory = factory
        # 成员变量 _inner：按需创建并缓存的嵌入模型。
        self._inner: Embeddings | None = None

    @property
    def inner(self) -> Embeddings:
        """按需创建并缓存底层嵌入模型实例。"""
        if self._inner is None:
            # 成员变量 _inner：按需创建并缓存的嵌入模型。
            self._inner = self._factory()
        return self._inner

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """将一组文档文本编码为向量列表。

        参数：
        texts：待批量处理的文本列表。
        """
        return self.inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """将单条查询文本编码为向量。

        参数：
        text：待处理的文本内容。
        """
        return self.inner.embed_query(text)


class LangChainEmbeddingService(Embeddings):
    """为 LangChain 向量流程提供统一嵌入入口。默认按 Settings 延迟构建本地或远程 Provider，并在输出处校验、归一化向量及记录维度；显式传入 Provider 的入口供离线维护和确定性测试使用。"""

    def __init__(
        self,
        config: Settings = settings,
        *,
        provider: EmbeddingProvider | None = None,
        embeddings: Embeddings | None = None,
        api_client: httpx.Client | None = None,
    ) -> None:
        """初始化嵌入适配器及其配置或底层 Provider。

        参数：
        config：当前应用或实验配置。
        provider：外部模型或数据服务提供方。
        embeddings：底层文本嵌入模型实例。
        api_client：访问远程嵌入 API 使用的 HTTP 客户端。
        """
        if provider is not None and embeddings is not None:
            raise ValueError("provider 与 embeddings 不能同时传入")
        if provider is not None:
            # 成员变量 provider：当前嵌入模型 Provider 实例。
            self.provider = provider
            # 成员变量 embeddings：底层文本嵌入模型实例。
            self.embeddings = None
            return

        # 局部变量 backend：当前选择的解析或运行后端。
        backend = embeddings or _LazyEmbeddings(
            lambda: build_embeddings(config, http_client=api_client)
        )
        self.embeddings = backend
        self.provider = _LangChainEmbeddingProvider(config, backend)

    @property
    def embedding_dimension(self) -> int | None:
        """返回当前嵌入模型已知的向量维度。"""
        return self.provider.dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """将一组文档文本编码为向量列表。

        参数：
        texts：待批量处理的文本列表。
        """
        _validate_texts(texts)
        return self.provider.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """将单条查询文本编码为向量。

        参数：
        text：待处理的文本内容。
        """
        _validate_texts([text])
        # 局部变量 embed_query：底层 Provider 的查询向量化方法。
        embed_query = getattr(self.provider, "embed_query", None)
        if embed_query is None:
            return self.embed_documents([text])[0]
        return embed_query(text)

    def signature(self, embedding_dimension: int | None = None) -> EmbeddingSignature:
        """生成用于识别当前嵌入配置的签名信息。

        参数：
        embedding_dimension：嵌入向量的维度。
        """
        # 局部变量 dimension：嵌入向量的维度。
        dimension = embedding_dimension or self.embedding_dimension
        if dimension is None and isinstance(self.provider, _LangChainEmbeddingProvider):
            try:
                dimension = len(self.provider.embed_query("维度探测"))
            except EmbeddingUnavailableError:
                raise
            # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
            except Exception as exc:
                raise EmbeddingUnavailableError("向量服务暂不可用") from exc
        if dimension is None:
            raise EmbeddingDimensionUnknown("Embedding 维度尚未从 Provider 获取")
        return EmbeddingSignature(
            embedding_provider=self.provider.name,
            embedding_model=self.provider.model,
            embedding_dimension=dimension,
            normalize_embeddings=self.provider.normalize_embeddings,
        )


def _validate_texts(texts: list[str]) -> None:
    """校验待嵌入文本列表非空且内容有效。

    参数：
    texts：待批量处理的文本列表。
    """
    # 局部变量 text：当前待解析、检索、清理或嵌入的文本。
    if not texts or any(
        not isinstance(text, str) or not text.strip() for text in texts
    ):
        raise ValueError("Embedding 输入不能为空")


def _validate_vectors(vectors: list[Any], expected_count: int) -> list[list[float]]:
    """校验向量数量、维度和数值类型。

    参数：
    vectors：待校验或写入的向量列表。
    expected_count：期望的向量数量。
    """
    if len(vectors) != expected_count:
        raise ValueError("embedding response item count does not match request")
    # 局部变量 result：完成格式与有限数值校验的向量列表；循环会进一步确认各向量维度一致后再返回。
    result: list[list[float]] = []
    # 局部变量 dimension：嵌入向量的维度。
    dimension: int | None = None
    # 局部变量 vector：单条文本对应的数值向量。
    for vector in vectors:
        # 局部变量 values：待写入数据库或输出的字段值集合。
        # 局部变量 value：模型返回的单个向量分量；先转换为 float，再由有限值检查拒绝 NaN 或无穷大。
        values = [float(value) for value in vector]
        if not values or any(not math.isfinite(value) for value in values):
            raise ValueError("embedding response contains an invalid vector")
        if dimension is None:
            dimension = len(values)
        elif len(values) != dimension:
            raise ValueError("embedding response dimensions do not match")
        result.append(values)
    return result


def _normalize(vector: list[float]) -> list[float]:
    """对输入向量进行归一化处理。

    参数：
    vector：单条文本对应的数值向量。
    """
    # 局部变量 norm：向量的模长或归一化因子。
    # 局部变量 value：当前向量中的浮点分量；平方和用于计算 L2 范数，以生成单位长度归一化向量。
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("embedding response contains a zero vector")
    return [value / norm for value in vector]
