# 文件作用：管理向量索引的创建、加载、更新、检索和签名兼容性。
import json
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

import faiss
import numpy as np
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores import FAISS as LangChainFAISS
from langchain_community.vectorstores.utils import DistanceStrategy
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from app.core.config import Settings, settings
from app.rag.constants import (
    COMPATIBLE_SPLITTER_VERSIONS,
    CURRENT_SPLITTER_VERSION,
)
from app.schemas.contracts import EmbeddingSignature, IndexMeta

# FAISS 索引加载、更新和持久化过程使用的日志记录器。
logger = logging.getLogger(__name__)

# 保护当前进程内 FAISS 索引文件访问的互斥锁。
_PROCESS_LOCK = RLock()


class EmbeddingSignatureProvider(Protocol):
    # 返回当前嵌入模型的签名信息。
    # 参数：dimension 为可选的嵌入向量维度。
    def signature(self, dimension: int | None = None) -> EmbeddingSignature: ...


class VectorStoreError(RuntimeError):
    """向量索引读写或检索流程的业务异常基类，供调用方统一捕获向量库相关故障。"""


class VectorStoreNotInitialized(VectorStoreError):
    """表示请求检索时尚未加载或构建 FAISS 索引；调用方可据此返回知识库未就绪状态。"""


class VectorStoreSignatureMismatch(VectorStoreError):
    """表示磁盘索引记录的嵌入模型签名与当前配置不一致；阻止使用维度或模型语义不同的向量进行检索。"""

    def __init__(self, message: str, meta: IndexMeta | None = None) -> None:
        """初始化向量签名不匹配异常并保留当前索引元数据。

        参数：
        message：供调用方定位签名冲突原因的错误说明。
        meta：读取到的不兼容索引元数据；未知时为空。
        """
        super().__init__(message)
        # 成员变量 meta：当前向量索引的持久化元数据。
        self.meta = meta


class VectorStorePersistenceError(VectorStoreError):
    """表示索引文件或元数据读取、写入、替换失败；保留为独立异常类型，便于 API 层区分存储故障和普通检索错误。"""


class _UnavailableEmbeddings(Embeddings):
    """在调用方已经提供预计算向量、无需文本编码时作为 Embeddings 占位实现；若误触发文本嵌入则立即报错，避免静默生成无效数据。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """将文本列表编码为向量。

        参数：
        texts：待批量处理的文本列表。
        """
        del texts
        raise ValueError("该向量库未配置文本 Embeddings")

    def embed_query(self, text: str) -> list[float]:
        """将查询文本编码为向量。

        参数：
        text：待处理的文本内容。
        """
        del text
        raise ValueError("该向量库未配置查询 Embeddings")


class VectorStoreService(VectorStore):
    """将持久化 FAISS 索引包装为 LangChain VectorStore，同时保留项目的签名校验、原子文件替换和生命周期管理。索引中的 Document 只保存稳定分块 ID，正文与来源字段仍以 SQLite 为准。"""

    def __init__(
        self,
        config: Settings = settings,
        embedding_service: EmbeddingSignatureProvider | None = None,
        index_dir: Path | None = None,
    ) -> None:
        """初始化向量存储及索引目录、嵌入模型和锁。

        参数：
        config：当前应用或实验配置。
        embedding_service：为文本生成向量的嵌入服务。
        index_dir：FAISS 索引文件所在的目录。
        """
        # 成员变量 config：向量库使用的应用 Settings，决定 FAISS 文件目录、嵌入模型签名及向量校验规则。
        self.config = config
        # 成员变量 embedding_service：生成文本向量的嵌入服务实例。
        self.embedding_service = embedding_service
        # 成员变量 index_dir：FAISS 索引所在目录。
        self.index_dir = index_dir or config.faiss_path / "production"
        # 成员变量 index_path：持久化 FAISS 索引文件路径。
        self.index_path = self.index_dir / "index.faiss"
        # 成员变量 meta_path：持久化向量索引元数据文件路径。
        self.meta_path = self.index_dir / "index_meta.json"
        # 成员变量 _index：当前加载的 FAISS 向量索引。
        self._index: faiss.IndexIDMap2 | None = None
        # 成员变量 _langchain_store：封装 FAISS 索引的 LangChain 向量存储实例。
        self._langchain_store: LangChainFAISS | None = None
        # 成员变量 _documents：按分块标识保存的文档映射。
        self._documents: dict[int, Document] = {}
        # 成员变量 _meta：向量索引的签名和版本元数据。
        self._meta: IndexMeta | None = None
        # 成员变量 _lock：保护索引读写的互斥锁。
        self._lock = _PROCESS_LOCK

    @property
    def meta(self) -> IndexMeta | None:
        """返回当前 FAISS 索引元数据。"""
        with self._lock:
            return self._meta

    @property
    def status(self) -> str:
        """计算向量索引的当前就绪状态。"""
        with self._lock:
            if self._meta is not None and not self.is_signature_compatible():
                return "incompatible"
            return (
                "ready"
                if self._index is not None and self._index.ntotal > 0
                else "not_initialized"
            )

    @property
    def is_ready(self) -> bool:
        """判断向量索引是否已加载且可检索。"""
        return self.status == "ready"

    def load(self) -> bool:
        """从磁盘读取 FAISS 索引与元数据，并校验二者及当前嵌入签名是否匹配；索引文件尚不存在时返回 False，避免把首次启动误报为损坏。"""
        with self._lock:
            # 局部变量 index_exists：持久化向量索引文件是否存在。
            index_exists = self.index_path.is_file()
            # 局部变量 meta_exists：持久化索引元数据文件是否存在。
            meta_exists = self.meta_path.is_file()
            # 成员变量 _index：当前加载的 FAISS 向量索引。
            self._index = None
            # 成员变量 _langchain_store：封装 FAISS 索引的 LangChain 向量存储实例。
            self._langchain_store = None
            # 成员变量 _documents：按分块标识保存的文档映射。
            self._documents = {}
            # 成员变量 _meta：向量索引的签名和版本元数据。
            self._meta = None
            if not index_exists and not meta_exists:
                return False
            if index_exists != meta_exists:
                raise VectorStorePersistenceError(
                    "向量索引文件与元数据不完整，需重建索引"
                )

            try:
                # 局部变量 payload：经过校验的请求数据或持久化内容。
                payload = json.loads(self.meta_path.read_text(encoding="utf-8"))
                # 局部变量 meta：当前向量索引的持久化元数据。
                meta = IndexMeta.model_validate(payload)
            # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise VectorStorePersistenceError("向量索引元数据无效") from exc

            self._meta = meta
            if not self.is_signature_compatible():
                raise VectorStoreSignatureMismatch(
                    "当前 Embedding 签名与生产索引不一致", meta
                )

            try:
                # 局部变量 loaded_index：从文件读取并加载的 FAISS 索引。
                loaded_index = faiss.read_index(str(self.index_path))
                if not isinstance(loaded_index, faiss.IndexIDMap2):
                    loaded_index = faiss.downcast_index(loaded_index)
                if not isinstance(loaded_index, faiss.IndexIDMap2):
                    raise TypeError("FAISS index does not preserve external ids")
                if loaded_index.d != meta.embedding_dimension:
                    raise ValueError("FAISS dimension does not match index metadata")
                if loaded_index.metric_type != faiss.METRIC_INNER_PRODUCT:
                    raise ValueError("FAISS index metric is not inner product")
                # 局部变量 ids：当前批量操作涉及的对象标识集合。
                ids = faiss.vector_to_array(loaded_index.id_map)
                if len(ids) != loaded_index.ntotal or len(set(ids.tolist())) != len(
                    ids
                ):
                    raise ValueError("FAISS id mapping is invalid")
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                raise VectorStorePersistenceError("FAISS 向量索引无效") from exc

            if loaded_index.ntotal == 0:
                self._meta = meta
                return False
            self._index = loaded_index
            # 局部变量 chunk_id：文本分块的唯一标识。
            self._documents = {
                chunk_id: self._document_for_chunk_id(chunk_id)
                for chunk_id in self._index_ids(loaded_index)
            }
            self._langchain_store = self._build_langchain_store(
                loaded_index, self._documents
            )
            return True

    def is_signature_compatible(
        self, signature: EmbeddingSignature | None = None
    ) -> bool:
        """比较现有索引和当前嵌入配置是否兼容。

        参数：
        signature：嵌入模型配置签名。
        """
        with self._lock:
            if self._meta is None:
                return False
            # 局部变量 expected：评测或校验预期值。
            expected = signature or self._signature_for(self._meta.embedding_dimension)
            return self._same_embedding_signature(self._meta, expected) and (
                self._meta.chunk_size == self.config.chunk_size
                and self._meta.chunk_overlap == self.config.chunk_overlap
                and self._meta.splitter_version in COMPATIBLE_SPLITTER_VERSIONS
            )

    def add(
        self,
        chunks_with_vectors: Sequence[tuple[int, Sequence[float]]],
        *,
        documents: Sequence[Document] | None = None,
    ) -> None:
        """保留旧调用方一次提交分块及预计算向量的兼容入口；新导入流程优先使用 add_documents，由嵌入服务生成并校验向量。

        参数：
        chunks_with_vectors：已完成向量化并准备写入索引的文本分块。
        documents：本次检索、解析或批量处理的文档集合。
        """
        self._add_with_documents(chunks_with_vectors, documents)

    def add_documents(self, documents: list[Document], **kwargs: Any) -> list[str]:
        """实现 LangChain 文档写入契约：接受调用方提供的向量或通过当前嵌入服务计算向量，补齐稳定分块元数据后更新并持久化索引。

        参数：
        documents：本次检索、解析或批量处理的文档集合。
        kwargs：传递给模型或底层函数的额外关键字参数。
        """
        if not documents:
            return []
        # 局部变量 vectors：文档或查询文本对应的数值向量集合。
        vectors = kwargs.pop("vectors", None)
        if vectors is None:
            if self.embedding_service is None or not hasattr(
                self.embedding_service, "embed_documents"
            ):
                raise ValueError("该向量库未配置文本 Embeddings")
            # 局部变量 document：待写入索引的 LangChain Document；从 page_content 批量生成向量，并保留 metadata 中的稳定 chunk_id 供检索回查。
            vectors = self.embedding_service.embed_documents(
                [document.page_content for document in documents]
            )
        if len(vectors) != len(documents):
            raise ValueError("文档数量与向量数量不一致")
        # 局部变量 ids：当前批量操作涉及的对象标识集合。
        ids = kwargs.pop("ids", None)
        if ids is not None and len(ids) != len(documents):
            raise ValueError("文档数量与 ids 数量不一致")
        if kwargs:
            # 局部变量 unexpected：当前未预料到的异常对象。
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"不支持的向量库参数: {unexpected}")
        # 局部变量 next_id：下一个可分配的记录标识。
        next_id = max(self._documents, default=0) + 1
        # 局部变量 chunk_ids：当前操作涉及的文本分块标识列表。
        chunk_ids: list[int] = []
        # 局部变量 index：当前记录在列表中的位置或索引序号。
        for index, document in enumerate(documents):
            # 局部变量 explicit_id：请求中显式指定的记录标识。
            explicit_id = ids[index] if ids is not None else None
            # 局部变量 chunk_id：文本分块的唯一标识。
            chunk_id = self._document_chunk_id(document, explicit_id, next_id)
            if explicit_id is None and not document.metadata.get("chunk_id"):
                next_id = chunk_id + 1
            chunk_ids.append(chunk_id)
        self._add_with_documents(list(zip(chunk_ids, vectors, strict=True)), documents)
        return [str(chunk_id) for chunk_id in chunk_ids]

    def add_texts(
        self,
        texts: Sequence[str],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """实现 LangChain VectorStore 的文本插入接口，将文本、元数据和可选 ID 转换为 Document 后交给统一写入路径处理。

        参数：
        texts：本批次需要处理的文本列表。
        metadatas：与文本或向量对应的元数据集合。
        ids：当前批量操作涉及的对象标识集合。
        kwargs：传递给模型或底层函数的额外关键字参数。
        """
        if kwargs:
            # 局部变量 unexpected：当前未预料到的异常对象。
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"不支持的向量库参数: {unexpected}")
        if self.embedding_service is None or not hasattr(
            self.embedding_service, "embed_documents"
        ):
            raise ValueError("该向量库未配置文本 Embeddings")
        # 局部变量 values：待写入数据库或输出的字段值集合。
        values = list(texts)
        # 局部变量 metadata_values：需要写入元数据文件的字段集合。
        metadata_values = metadatas or [{} for _ in values]
        if len(metadata_values) != len(values):
            raise ValueError("文本数量与 metadata 数量不一致")
        # 局部变量 documents：本次检索、解析或批量处理的文档集合。
        # 局部变量 text：当前待解析、检索、清理或嵌入的文本。
        # 局部变量 metadata：复制原 Document 的来源字段后补入 score 和 retrieval_score，供后续重排及证据诊断区分原始相似度。
        documents = [
            Document(page_content=text, metadata=dict(metadata))
            for text, metadata in zip(values, metadata_values, strict=True)
        ]
        return self.add_documents(documents, ids=ids)

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> "VectorStoreService":
        """实现 LangChain 标准工厂接口：先用给定 Embeddings 生成文本向量，再创建带项目持久化配置的向量库实例并写入索引。

        参数：
        texts：本批次需要处理的文本列表。
        embedding：生成文本向量的嵌入模型实例。
        metadatas：与文本或向量对应的元数据集合。
        ids：当前批量操作涉及的对象标识集合。
        kwargs：传递给模型或底层函数的额外关键字参数。
        """
        # 局部变量 config：从 LangChain 工厂 kwargs 中取出的 Settings；若调用方未显式传入，则使用应用默认配置创建持久化索引。
        config = kwargs.pop("config", settings)
        # 局部变量 index_dir：FAISS 索引所在目录。
        index_dir = kwargs.pop("index_dir", None)
        if kwargs:
            # 局部变量 unexpected：当前未预料到的异常对象。
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"不支持的向量库参数: {unexpected}")
        if ids is not None and len(ids) != len(texts):
            raise ValueError("文本数量与 ids 数量不一致")
        # 局部变量 metadata_values：需要写入元数据文件的字段集合。
        metadata_values = metadatas or [{} for _ in texts]
        if len(metadata_values) != len(texts):
            raise ValueError("文本数量与 metadata 数量不一致")
        # 局部变量 documents：本次检索、解析或批量处理的文档集合。
        # 局部变量 index：当前记录在列表中的位置或索引序号。
        # 局部变量 text：当前待解析、检索、清理或嵌入的文本。
        documents = [
            Document(
                id=(ids[index] if ids is not None else str(index + 1)),
                page_content=text,
                metadata=dict(metadata_values[index]),
            )
            for index, text in enumerate(texts)
        ]
        # 局部变量 vectors：文档或查询文本对应的数值向量集合。
        vectors = embedding.embed_documents(texts)
        # 局部变量 store：当前持久化、向量或文档存储实例。
        store = cls(
            config=config,
            embedding_service=embedding,
            index_dir=index_dir,
        )
        store.add_documents(documents, vectors=vectors, ids=ids)
        return store

    def similarity_search(
        self, query: str, k: int = 4, **kwargs: Any
    ) -> list[Document]:
        """按查询文本执行相似度检索。

        参数：
        query：用户问题文本或数据库筛选条件。
        k：本次检索保留的候选结果数量。
        kwargs：传递给底层调用的额外关键字参数。
        """
        # 局部变量 document：与相似度分数配对返回的 LangChain Document；此处只取文档本体，分数由带分数的检索接口单独提供。
        return [
            document
            for document, _ in self.similarity_search_with_score(query, k=k, **kwargs)
        ]

    def similarity_search_with_score(
        self, query: str, k: int = 4, **kwargs: Any
    ) -> list[tuple[Document, float]]:
        """执行相似度检索并返回分数。

        参数：
        query：用户问题文本或数据库筛选条件。
        k：本次检索保留的候选结果数量。
        kwargs：传递给底层调用的额外关键字参数。
        """
        if self.embedding_service is None or not hasattr(
            self.embedding_service, "embed_query"
        ):
            raise ValueError("该向量库未配置查询 Embeddings")
        return self.similarity_search_with_score_by_vector(
            self.embedding_service.embed_query(query), k=k, **kwargs
        )

    def similarity_search_by_vector(
        self, embedding: list[float], k: int = 4, **kwargs: Any
    ) -> list[Document]:
        """按已计算的查询向量执行相似度检索。

        参数：
        embedding：生成文本向量的嵌入模型实例。
        k：本次检索保留的候选结果数量。
        kwargs：传递给底层调用的额外关键字参数。
        """
        # 局部变量 document：向量检索得到的 LangChain Document；此处剥离相似度分数，只向调用方返回文档内容及 metadata。
        return [
            document
            for document, _ in self.similarity_search_with_score_by_vector(
                embedding, k=k, **kwargs
            )
        ]

    def similarity_search_with_score_by_vector(
        self, embedding: list[float], k: int = 4, **kwargs: Any
    ) -> list[tuple[Document, float]]:
        """按查询向量检索并返回相似度分数。

        参数：
        embedding：生成文本向量的嵌入模型实例。
        k：本次检索保留的候选结果数量。
        kwargs：传递给底层调用的额外关键字参数。
        """
        if k < 1:
            return []
        # 局部变量 score_threshold：检索结果保留所需的最低相关度阈值。
        score_threshold = kwargs.pop("score_threshold", None)
        # 局部变量 filter_value：当前查询使用的过滤值。
        filter_value = kwargs.pop("filter", None)
        # 局部变量 fetch_k：初始向量召回候选数量。
        fetch_k = int(kwargs.pop("fetch_k", k))
        if kwargs:
            # 局部变量 unexpected：当前未预料到的异常对象。
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"不支持的向量库参数: {unexpected}")
        # 局部变量 hits：向量检索命中的结果集合。
        hits = self.search(embedding, max(k, fetch_k))
        # 局部变量 results：本次批量处理生成的结果记录集合。
        # 局部变量 chunk_id：文本分块的唯一标识。
        # 局部变量 score：当前候选项或检索结果的相关度分数。
        results = [
            (self._document_with_score(self._documents[chunk_id], score), score)
            for chunk_id, score in hits
            if chunk_id in self._documents
        ]
        if filter_value is not None:
            if callable(filter_value):
                # 局部变量 document：传入 filter 回调检查的候选 LangChain Document；回调按其 metadata 决定是否保留对应文档和分数。
                results = [
                    (document, score)
                    for document, score in results
                    if filter_value(document.metadata)
                ]
            elif isinstance(filter_value, dict):
                # 局部变量 key：调用方要求精确匹配的 Document metadata 字段名。
                # 局部变量 value：显式传入的 chunk ID 候选值；缺失时依次使用 Document metadata 中的 chunk_id、Document.id 或 fallback，再统一校验为正整数。
                results = [
                    (document, score)
                    for document, score in results
                    if all(
                        document.metadata.get(key) == value
                        for key, value in filter_value.items()
                    )
                ]
            else:
                raise TypeError("filter 必须是字典或可调用对象")
        if score_threshold is not None:
            results = [
                (document, score)
                for document, score in results
                if score >= float(score_threshold)
            ]
        return results[:k]

    @staticmethod
    def _document_with_score(document: Document, score: float) -> Document:
        """将检索分数写入文档元数据并返回副本。

        参数：
        document：当前处理的文档对象或记录。
        score：候选证据或相似度分数。
        """
        # 局部变量 metadata：在原有来源信息上补入稳定 chunk_id，保证 LangChain Document 能回查 SQLite 中的权威文本块。
        metadata = dict(document.metadata)
        metadata["score"] = float(score)
        metadata["retrieval_score"] = float(score)
        return Document(
            id=document.id,
            page_content=document.page_content,
            metadata=metadata,
        )

    @staticmethod
    def _index_ids(index: faiss.IndexIDMap2) -> list[int]:
        """读取 FAISS 索引中保存的文档标识。

        参数：
        index：当前记录在列表中的位置或索引序号。
        """
        # 局部变量 ids：当前批量操作涉及的对象标识集合。
        ids = faiss.vector_to_array(index.id_map).astype(np.int64, copy=False)
        # 局部变量 chunk_id：文本分块的唯一标识。
        return [int(chunk_id) for chunk_id in ids.tolist()]

    @staticmethod
    def _document_for_chunk_id(chunk_id: int) -> Document:
        """构造指定文本块对应的最小文档对象。

        参数：
        chunk_id：目标文本分块标识。
        """
        return Document(
            id=str(chunk_id),
            page_content="",
            metadata={"chunk_id": chunk_id},
        )

    @staticmethod
    def _normalize_document(document: Document, chunk_id: int) -> Document:
        """规范化文档元数据并确保包含分块标识。

        参数：
        document：当前处理的文档对象或记录。
        chunk_id：目标文本分块标识。
        """
        # 局部变量 metadata：内存 Docstore 中各 chunk_id 对应的最小 Document 投影；正文和稳定 ID 留在索引，业务来源字段从数据库恢复。
        metadata = dict(document.metadata)
        metadata["chunk_id"] = chunk_id
        return Document(
            id=str(chunk_id),
            page_content=document.page_content,
            metadata=metadata,
        )

    @staticmethod
    def _document_chunk_id(
        document: Document, explicit_id: str | None, fallback: int | None = None
    ) -> int:
        """从文档元数据解析有效的文本块标识。

        参数：
        document：当前处理的文档对象或记录。
        explicit_id：调用方显式指定的资源标识。
        fallback：主要处理方式不可用时使用的备用值或逻辑。
        """
        # 局部变量 value：与 key 配对的期望 metadata 值；只有所有键值条件都相等时才保留该检索结果。
        value: Any = explicit_id
        if value is None:
            value = document.metadata.get("chunk_id", document.id)
        if value is None and fallback is not None:
            value = fallback
        try:
            # 局部变量 chunk_id：文本分块的唯一标识。
            chunk_id = int(value)
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except (TypeError, ValueError) as exc:
            raise ValueError("LangChain Document 必须包含正整数 chunk_id") from exc
        if chunk_id <= 0:
            raise ValueError("chunk_id 必须是正整数")
        return chunk_id

    def _build_langchain_store(
        self,
        index: faiss.IndexIDMap2,
        documents: dict[int, Document],
    ) -> LangChainFAISS:
        """构造与当前 FAISS 索引关联的 LangChain 包装器。

        参数：
        documents：需要处理的文档列表。
        index：当前列表中的位置或向量索引对象。
        """
        # 局部变量 document_store：文档标识到文档内容的内存映射存储。
        # 局部变量 chunk_id：文本分块的唯一标识。
        # 局部变量 document：从 SQLite 文本块映射构造的 LangChain Document 投影；索引仅保存正文和 chunk_id，完整业务元数据仍从数据库读取。
        document_store = InMemoryDocstore(
            {str(chunk_id): document for chunk_id, document in documents.items()}
        )
        # 局部变量 index_to_docstore_id：FAISS 内部编号到文档存储键的映射。
        index_to_docstore_id = {
            chunk_id: str(chunk_id) for chunk_id in self._index_ids(index)
        }
        # 局部变量 embedding_function：供向量存储调用的文本嵌入函数。
        embedding_function = (
            self.embedding_service
            if isinstance(self.embedding_service, Embeddings)
            else _UnavailableEmbeddings()
        )
        return LangChainFAISS(
            embedding_function=embedding_function,
            index=index,
            docstore=document_store,
            index_to_docstore_id=index_to_docstore_id,
            normalize_L2=False,
            distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
        )

    def _add_with_documents(
        self,
        chunks_with_vectors: Sequence[tuple[int, Sequence[float]]],
        documents: Sequence[Document] | None = None,
    ) -> None:
        """将文档和向量写入 FAISS 并更新索引元数据。

        参数：
        documents：需要处理的文档列表。
        chunks_with_vectors：已完成嵌入并准备写入向量索引的分块。
        """
        if not chunks_with_vectors:
            return
        with self._lock:
            # 局部变量 ids：当前批量操作涉及的对象标识集合。
            # 局部变量 chunk_id：文本分块的唯一标识。
            ids = [int(chunk_id) for chunk_id, _ in chunks_with_vectors]
            if any(chunk_id <= 0 for chunk_id in ids) or len(set(ids)) != len(ids):
                raise ValueError("chunk ids must be unique positive integers")
            if documents is not None and len(documents) != len(ids):
                raise ValueError("文档数量与向量数量不一致")
            # 局部变量 vectors：文档或查询文本对应的数值向量集合。
            # 局部变量 vector：单条文本对应的数值向量。
            vectors = self._matrix([vector for _, vector in chunks_with_vectors])
            # 局部变量 dimension：嵌入向量的维度。
            dimension = int(vectors.shape[1])
            if self._index is not None:
                if self._meta is None:
                    raise VectorStorePersistenceError("向量索引缺少元数据")
                if dimension != self._meta.embedding_dimension:
                    raise VectorStoreSignatureMismatch(
                        "Embedding 向量维度与生产索引不一致", self._meta
                    )
                if not self.is_signature_compatible():
                    raise VectorStoreSignatureMismatch(
                        "当前 Embedding 签名与生产索引不一致", self._meta
                    )
                # 局部变量 candidate：当前待评估或排序的候选项。
                candidate = faiss.clone_index(self._index)
                # 局部变量 existing_ids：数据库或索引中已经存在的对象标识。
                existing_ids = set(
                    faiss.vector_to_array(candidate.id_map).astype(np.int64).tolist()
                )
                if existing_ids.intersection(ids):
                    raise ValueError("one or more chunk ids already exist in FAISS")
                # 局部变量 metadata_signature：已有索引保存的嵌入配置签名。
                metadata_signature = self._signature_for(dimension)
            else:
                if self._meta is not None:
                    if not self.is_signature_compatible():
                        raise VectorStoreSignatureMismatch(
                            "当前 Embedding 签名与生产索引不一致", self._meta
                        )
                    if self._meta.embedding_dimension != dimension:
                        raise VectorStoreSignatureMismatch(
                            "Embedding 向量维度与生产索引不一致", self._meta
                        )
                metadata_signature = self._signature_for(dimension)
                candidate = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))

            candidate.add_with_ids(vectors, np.asarray(ids, dtype=np.int64))
            # 局部变量 projected_documents：映射到目标检索空间的文档集合。
            projected_documents = dict(self._documents)
            # 局部变量 index：当前记录在列表中的位置或索引序号。
            for index, chunk_id in enumerate(ids):
                projected_documents[chunk_id] = (
                    self._normalize_document(documents[index], chunk_id)
                    if documents is not None
                    else self._document_for_chunk_id(chunk_id)
                )
            # 局部变量 meta：当前向量索引的持久化元数据。
            meta = self._new_meta(metadata_signature)
            self._persist(candidate, meta)
            # 成员变量 _index：当前加载的 FAISS 向量索引。
            self._index = candidate
            # 成员变量 _documents：按分块标识保存的文档映射。
            self._documents = projected_documents
            # 成员变量 _langchain_store：封装 FAISS 索引的 LangChain 向量存储实例。
            self._langchain_store = self._build_langchain_store(
                candidate, projected_documents
            )
            # 成员变量 _meta：向量索引的签名和版本元数据。
            self._meta = meta

    def remove(self, chunk_ids: Sequence[int]) -> None:
        """删除指定文本块对应的向量索引项。

        参数：
        chunk_ids：需要查询或更新的文本分块标识列表。
        """
        # 局部变量 ids：当前批量操作涉及的对象标识集合。
        # 局部变量 chunk_id：文本分块的唯一标识。
        ids = sorted({int(chunk_id) for chunk_id in chunk_ids})
        if not ids:
            return
        with self._lock:
            if self._index is None or self._meta is None:
                return
            if not self.is_signature_compatible():
                raise VectorStoreSignatureMismatch(
                    "当前 Embedding 签名与生产索引不一致", self._meta
                )
            # 局部变量 candidate：当前待评估或排序的候选项。
            candidate = faiss.clone_index(self._index)
            # 局部变量 existing_ids：数据库或索引中已经存在的对象标识。
            existing_ids = set(faiss.vector_to_array(candidate.id_map).tolist())
            # 局部变量 selected：当前选中或筛选出的对象集合。
            selected = [chunk_id for chunk_id in ids if chunk_id in existing_ids]
            if not selected:
                return
            candidate.remove_ids(np.asarray(selected, dtype=np.int64))
            # 局部变量 meta：当前向量索引的持久化元数据。
            meta = self._new_meta(self._signature_for(self._meta.embedding_dimension))
            self._persist(candidate, meta)
            # 成员变量 _index：当前加载的 FAISS 向量索引。
            self._index = candidate
            # 成员变量 _documents：按分块标识保存的文档映射。
            # 局部变量 document：与分块 ID 关联的内存 Document 投影；写入新索引时保留其 page_content 和 metadata 映射关系。
            self._documents = {
                chunk_id: document
                for chunk_id, document in self._documents.items()
                if chunk_id not in selected
            }
            # 成员变量 _langchain_store：封装 FAISS 索引的 LangChain 向量存储实例。
            self._langchain_store = self._build_langchain_store(
                candidate, self._documents
            )
            # 成员变量 _meta：向量索引的签名和版本元数据。
            self._meta = meta

    def search(
        self, query_vector: Sequence[float], top_k: int
    ) -> list[tuple[int, float]]:
        """执行向量相似度检索并映射为应用证据结构。

        参数：
        query_vector：由查询文本生成的向量表示。
        top_k：重排后保留的证据片段数量。
        """
        if top_k < 1:
            return []
        with self._lock:
            if self._index is None or self._meta is None or self._index.ntotal == 0:
                raise VectorStoreNotInitialized("向量知识库尚未初始化")
            if not self.is_signature_compatible():
                raise VectorStoreSignatureMismatch(
                    "当前 Embedding 签名与生产索引不一致", self._meta
                )
            # 局部变量 vector：单条文本对应的数值向量。
            vector = self._matrix([query_vector])
            if vector.shape[1] != self._meta.embedding_dimension:
                raise VectorStoreSignatureMismatch(
                    "查询向量维度与生产索引不一致", self._meta
                )
            # 局部变量 count：当前循环、批次或类别的计数值。
            count = min(top_k, self._index.ntotal)
            if count == 0:
                return []
            if sys.platform == "darwin":
                # 局部变量 similarities：向量检索得到的相似度分数集合。
                # 局部变量 indices：向量索引内部标识或候选位置集合。
                similarities, indices = self._search_without_openmp(
                    self._index, vector, count
                )
                # 局部变量 scored_ids：当前检索结果对应的分块标识集合。
                scored_ids = zip(indices[0], similarities[0], strict=True)
            else:
                if self._langchain_store is None:
                    # 成员变量 _langchain_store：封装 FAISS 索引的 LangChain 向量存储实例。
                    self._langchain_store = self._build_langchain_store(
                        self._index, self._documents
                    )
                # 局部变量 documents_with_scores：附带相似度分数的检索文档列表。
                documents_with_scores = (
                    self._langchain_store.similarity_search_with_score_by_vector(
                        vector[0].tolist(), count
                    )
                )
                # 局部变量 document：检索结果中的 LangChain Document；根据 metadata 中的 chunk_id 读取对应向量 ID，并与分数一起恢复稳定结果顺序。
                # 局部变量 score：当前候选项或检索结果的相关度分数。
                scored_ids = (
                    (
                        int(document.metadata["chunk_id"]),
                        score,
                    )
                    for document, score in documents_with_scores
                )
            # 局部变量 results：本次批量处理生成的结果记录集合。
            results: list[tuple[int, float]] = []
            # 局部变量 chunk_id：文本分块的唯一标识。
            # 局部变量 cosine：当前计算得到的余弦相似度。
            for chunk_id, cosine in scored_ids:
                if chunk_id < 0:
                    continue
                # 局部变量 retrieval_score：向量检索返回的原始相关度分数。
                retrieval_score = min(1.0, max(0.0, (float(cosine) + 1.0) / 2.0))
                results.append((int(chunk_id), retrieval_score))
            return results

    @staticmethod
    def _search_without_openmp(
        index: faiss.IndexIDMap2, query: np.ndarray, count: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """在 macOS 上隔离小型索引的 FAISS 搜索调用，避免与 PyTorch 加载的另一套 OpenMP 运行时竞争线程资源。

        参数：
        index：当前记录在列表中的位置或索引序号。
        query：用户问题文本或数据库筛选条件。
        count：当前统计或处理的记录数量。
        """
        # 局部变量 vectors：文档或查询文本对应的数值向量集合。
        vectors = np.asarray(
            index.index.reconstruct_n(0, index.ntotal), dtype=np.float32
        )
        # 局部变量 ids：当前批量操作涉及的对象标识集合。
        ids = faiss.vector_to_array(index.id_map).astype(np.int64, copy=False)
        # 局部变量 scores：与检索候选项对应的相似度分数。
        scores = vectors @ query[0]
        # 局部变量 order：当前列表或结果的排序顺序。
        order = np.argsort(-scores, kind="stable")[:count]
        return scores[order][None, :], ids[order][None, :]

    def save(self) -> None:
        """将当前向量索引和元数据持久化到磁盘。"""
        with self._lock:
            if self._index is None or self._meta is None:
                raise VectorStoreNotInitialized("向量知识库尚未初始化")
            self._persist(self._index, self._meta)

    def rebuild_from_success_chunks(
        self,
        chunks_with_vectors: Sequence[tuple[int, Sequence[float]]],
        signature: EmbeddingSignature | None = None,
    ) -> bool:
        """仅从数据库中已成功导入的文本块重新生成向量，并用新的嵌入签名构建完整索引；完成后原子替换旧索引，避免读到半成品。

        参数：
        chunks_with_vectors：已完成向量化并准备写入索引的文本分块。
        signature：描述嵌入模型和向量维度的配置签名。
        """
        with self._lock:
            if not chunks_with_vectors:
                # 局部变量 path：当前文件或索引所在的路径。
                for path in (self.index_path, self.meta_path):
                    try:
                        path.unlink(missing_ok=True)
                    # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
                    except OSError as exc:
                        raise VectorStorePersistenceError("清理旧向量索引失败") from exc
                # 成员变量 _index：当前加载的 FAISS 向量索引。
                self._index = None
                # 成员变量 _langchain_store：封装 FAISS 索引的 LangChain 向量存储实例。
                self._langchain_store = None
                # 成员变量 _documents：按分块标识保存的文档映射。
                self._documents = {}
                # 成员变量 _meta：向量索引的签名和版本元数据。
                self._meta = None
                return False

            # 局部变量 ids：当前批量操作涉及的对象标识集合。
            # 局部变量 chunk_id：文本分块的唯一标识。
            ids = [int(chunk_id) for chunk_id, _ in chunks_with_vectors]
            if any(chunk_id <= 0 for chunk_id in ids) or len(set(ids)) != len(ids):
                raise ValueError("chunk ids must be unique positive integers")
            # 局部变量 vectors：文档或查询文本对应的数值向量集合。
            # 局部变量 vector：单条文本对应的数值向量。
            vectors = self._matrix([vector for _, vector in chunks_with_vectors])
            # 局部变量 dimension：嵌入向量的维度。
            dimension = int(vectors.shape[1])
            # 局部变量 selected_signature：当前选择用于比较的嵌入签名。
            selected_signature = signature or self._signature_for(dimension)
            if selected_signature.embedding_dimension != dimension:
                raise VectorStoreSignatureMismatch(
                    "重建向量维度与 Embedding 签名不一致", self._meta
                )
            if not self._same_embedding_signature(
                selected_signature,
                self._signature_for(dimension),
            ):
                raise VectorStoreSignatureMismatch(
                    "重建签名与当前 Embedding Provider 不一致"
                )

            # 局部变量 candidate：当前待评估或排序的候选项。
            candidate = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
            candidate.add_with_ids(vectors, np.asarray(ids, dtype=np.int64))
            # 局部变量 meta：当前向量索引的持久化元数据。
            meta = self._new_meta(selected_signature)
            self._persist(candidate, meta)
            self._index = candidate
            self._documents = {
                chunk_id: self._document_for_chunk_id(chunk_id) for chunk_id in ids
            }
            self._langchain_store = self._build_langchain_store(
                candidate, self._documents
            )
            self._meta = meta
            return True

    def _signature_for(self, dimension: int) -> EmbeddingSignature:
        """根据向量维度创建当前嵌入签名。

        参数：
        dimension：嵌入向量的维度。
        """
        # 局部变量 signature：描述嵌入模型和向量维度的配置签名。
        signature = getattr(self.embedding_service, "signature", None)
        if signature is not None:
            return signature(dimension)
        return EmbeddingSignature(
            embedding_provider=self.config.embedding_provider,
            embedding_model=self.config.embedding_model,
            embedding_dimension=dimension,
            normalize_embeddings=self.config.embedding_normalize,
        )

    def _new_meta(self, signature: EmbeddingSignature) -> IndexMeta:
        """创建描述当前索引状态的元数据对象。

        参数：
        signature：嵌入模型配置签名。
        """
        return IndexMeta(
            **signature.model_dump(),
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            splitter_version=CURRENT_SPLITTER_VERSION,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _same_embedding_signature(
        actual: EmbeddingSignature, expected: EmbeddingSignature
    ) -> bool:
        """判断两个嵌入签名是否完全一致。

        参数：
        actual：实际运行得到的值或结果。
        expected：评测或校验预期值。
        """
        return (
            actual.embedding_provider == expected.embedding_provider
            and actual.embedding_model == expected.embedding_model
            and actual.embedding_dimension == expected.embedding_dimension
            and actual.normalize_embeddings == expected.normalize_embeddings
        )

    @staticmethod
    def _matrix(vectors: Sequence[Sequence[float]]) -> np.ndarray:
        """将向量序列转换为适用于 FAISS 的矩阵。

        参数：
        vectors：待校验或写入的向量列表。
        """
        if not vectors:
            raise ValueError("at least one embedding vector is required")
        # 局部变量 matrix：传给 FAISS 的连续浮点向量矩阵。
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] == 0:
            raise ValueError(
                "embedding vectors must have a consistent positive dimension"
            )
        if not np.isfinite(matrix).all():
            raise ValueError("embedding vectors must contain finite values")
        # 局部变量 norms：向量归一化计算得到的模长。
        norms = np.linalg.norm(matrix, axis=1)
        if np.any(norms == 0):
            raise ValueError("embedding vectors cannot be zero vectors")
        matrix = matrix / norms[:, None]
        return np.ascontiguousarray(matrix, dtype=np.float32)

    def _persist(self, index: faiss.IndexIDMap2, meta: IndexMeta) -> None:
        """将指定索引和元数据原子持久化。

        参数：
        index：当前记录在列表中的位置或索引序号。
        meta：当前向量索引的持久化元数据。
        """
        # 局部变量 temporary_paths：本轮操作创建的临时路径集合。
        temporary_paths: list[Path] = []
        try:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            # 局部变量 index_fd：打开的临时 FAISS 索引文件描述符。
            # 局部变量 index_temp_name：临时 FAISS 索引文件名。
            index_fd, index_temp_name = tempfile.mkstemp(
                prefix=".index-", suffix=".faiss.tmp", dir=self.index_dir
            )
            os.close(index_fd)
            # 局部变量 index_temp：待原子替换的临时向量索引文件。
            index_temp = Path(index_temp_name)
            temporary_paths.append(index_temp)
            faiss.write_index(index, str(index_temp))

            # 局部变量 meta_fd：打开的临时索引元数据文件描述符。
            # 局部变量 meta_temp_name：临时索引元数据文件名。
            meta_fd, meta_temp_name = tempfile.mkstemp(
                prefix=".index-meta-", suffix=".json.tmp", dir=self.index_dir
            )
            # 局部变量 meta_temp：待原子替换的临时索引元数据文件。
            meta_temp = Path(meta_temp_name)
            temporary_paths.append(meta_temp)
            # 局部变量 file：当前上传或读取的文件对象。
            with os.fdopen(meta_fd, "w", encoding="utf-8") as file:
                json.dump(
                    meta.model_dump(mode="json"), file, ensure_ascii=False, indent=2
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())

            # 局部变量 backups：本次事务保存的原文件备份清单。
            backups: dict[Path, Path | None] = {}
            # 局部变量 target：当前需要更新、转换或写入的目标对象。
            for target in (self.index_path, self.meta_path):
                if not target.exists():
                    backups[target] = None
                    continue
                # 局部变量 backup_fd：临时备份文件的文件描述符。
                # 局部变量 backup_name：临时备份文件名称。
                backup_fd, backup_name = tempfile.mkstemp(
                    prefix=f".{target.name}-backup-", dir=self.index_dir
                )
                os.close(backup_fd)
                # 局部变量 backup_path：原索引文件的临时备份路径。
                backup_path = Path(backup_name)
                temporary_paths.append(backup_path)
                backup_path.unlink()
                try:
                    os.link(target, backup_path)
                except OSError:
                    shutil.copy2(target, backup_path)
                backups[target] = backup_path

            try:
                # 临时文件与目标文件位于同一文件系统时，os.replace 可原子替换单个文件；
                # 在两份文件都写完前不暴露半成品索引，异常时由下方备份恢复旧文件。
                os.replace(index_temp, self.index_path)
                os.replace(meta_temp, self.meta_path)
            except OSError:
                # 局部变量 backup：索引文件替换前的备份路径。
                for target, backup in backups.items():
                    if backup is None:
                        target.unlink(missing_ok=True)
                    elif backup.exists():
                        os.replace(backup, target)
                raise
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.exception("Unable to persist FAISS index")
            raise VectorStorePersistenceError("保存向量索引失败") from exc
        finally:
            # 局部变量 path：当前文件或索引所在的路径。
            for path in temporary_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Unable to remove temporary vector index file")


class KnowledgeVectorStore(VectorStoreService):
    """为持久化知识向量库提供面向 LangChain 调用方的稳定别名；实际索引行为和存储约束由 VectorStoreService 实现。"""
