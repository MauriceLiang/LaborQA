# 文件作用：协调查询处理、向量检索、重排和证据结果组装。
"""实现业务检索流程与 LangChain VectorStore retriever 的衔接：向量库负责召回，SQLite 提供权威文本和来源信息，业务服务负责邻居扩展及重排。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from langchain_core.documents import Document
from langchain_core.runnables import Runnable, RunnableLambda

from app.core.config import Settings, settings
from app.rag.embeddings import LangChainEmbeddingService
from app.repositories.document_repository import DocumentRepository
from app.services.lightweight_rerank import LightweightRerankService
from app.services.rerank import RerankService
from app.services.vector_store import (
    VectorStoreNotInitialized,
    VectorStoreService,
    VectorStoreSignatureMismatch,
)


class DomainRetrievalService:
    """把 LangChain 检索器接入 LaborQA 的业务边界。向量库负责嵌入和 FAISS 召回，SQLite 仍是文本及来源元数据的权威来源，本服务再进行分块映射、邻接扩展和可选重排。"""

    def __init__(
        self,
        config: Settings = settings,
        *,
        repository: DocumentRepository | None = None,
        embedding_service: LangChainEmbeddingService | None = None,
        vector_store: VectorStoreService | None = None,
        rerank_service: RerankService | None = None,
        lightweight_rerank_service: LightweightRerankService | None = None,
    ) -> None:
        """初始化检索器及向量索引、重排和文档仓库依赖。

        参数：
        config：当前应用或实验配置。
        repository：负责当前业务数据持久化的数据仓库实例。
        embedding_service：为文本生成向量的嵌入服务。
        vector_store：负责向量索引读写和相似度检索的服务实例。
        rerank_service：负责对候选证据执行模型重排的服务。
        lightweight_rerank_service：执行轻量词面重排和邻居扩展的服务。
        """
        # 成员变量 config：检索流程使用的应用 Settings，提供 candidate_k、top_k、分数阈值、邻居扩展和重排开关等召回策略参数。
        self.config = config
        # 成员变量 repository：负责当前业务数据持久化的数据仓库。
        self.repository = repository or DocumentRepository(config.database_path)
        # 成员变量 embedding_service：生成文本向量的嵌入服务实例。
        self.embedding_service = embedding_service or LangChainEmbeddingService(config)
        # 成员变量 vector_store：负责 FAISS 索引加载、检索和持久化的服务。
        self.vector_store = vector_store or VectorStoreService(
            config=config,
            embedding_service=self.embedding_service,
            index_dir=config.faiss_path / "production",
        )
        if (
            callable(getattr(self.vector_store, "as_retriever", None))
            and getattr(self.vector_store, "embedding_service", None) is None
        ):
            # 测试替身或自定义 VectorStore 可能未在构造时设置 embedding_service；
            # 在此补入生产嵌入适配器以保持检索签名一致，不改变 VectorStore 的公开调用接口。
            self.vector_store.embedding_service = self.embedding_service
        # 成员变量 rerank_service：根据模型相关度重排候选证据的服务。
        self.rerank_service = rerank_service or RerankService(config)
        # 成员变量 lightweight_rerank_service：计算词面相关度和邻居扩展的轻量重排服务。
        self.lightweight_rerank_service = (
            lightweight_rerank_service or LightweightRerankService()
        )
        # 成员变量 candidate_retriever：初始候选证据召回使用的检索器。
        self.candidate_retriever = self._build_candidate_retriever()
        # 成员变量 retriever：当前 RAG 链使用的 LangChain 检索器。
        self.retriever = self._build_domain_retriever()

    def _build_candidate_retriever(self) -> Runnable[Any, list[Document]]:
        """构造初始向量候选检索器。
        """
        return self.vector_store.as_retriever(
            search_kwargs={"k": self.config.rag_candidate_k}
        )

    def _build_domain_retriever(self) -> Runnable[Any, list[Document]]:
        """构造按劳动领域优化的检索器。
        """
        return RunnableLambda(self._retrieve_documents)

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        """同步执行查询改写后的证据检索 Runnable，并返回按业务规则整理的候选证据，供同步调用方使用。

            参数：
            query：用户问题文本或数据库筛选条件。
        """
        self._ensure_ready()
        # 局部变量 documents：本次检索、解析或批量处理的文档集合。
        documents = self.retriever.invoke(query)
        # 局部变量 document：向量检索返回的 LangChain Document；证据转换函数从其 page_content 和 metadata 中提取正文、分块 ID 与来源字段。
        return [_evidence_from_document(document) for document in documents]

    async def aretrieve(self, query: str) -> list[dict[str, Any]]:
        """异步执行与 retrieve 相同的证据检索 Runnable，避免异步问答和实验流程因同步等待阻塞事件循环。

            参数：
            query：用户问题文本或数据库筛选条件。
        """
        self._ensure_ready()
        # 局部变量 documents：本次检索、解析或批量处理的文档集合。
        documents = await self.retriever.ainvoke(query)
        # 局部变量 document：异步检索返回的 LangChain Document；证据转换函数从其 page_content 和 metadata 中提取正文、分块 ID 与来源字段。
        return [_evidence_from_document(document) for document in documents]

    def _ensure_ready(self) -> None:
        """确认向量索引可用并在需要时触发初始化。
        """
        # 局部变量 status：当前业务对象或任务的处理状态。
        status = self.vector_store.status
        if status == "not_initialized":
            raise VectorStoreNotInitialized("向量知识库尚未初始化")
        if status == "incompatible":
            raise VectorStoreSignatureMismatch("当前 Embedding 签名与生产索引不一致")

    def _documents_to_evidence_documents(
        self, state: Mapping[str, Any]
    ) -> list[Document]:
        """将检索证据转换为带分数的文档对象。

        参数：
        state：运行状态数据。
        """
        # 局部变量 query：当前数据库筛选或向量检索查询。
        query = str(state["query"])
        # 局部变量 candidate_documents：初始检索得到的候选文档列表。
        candidate_documents = list(state.get("documents", []))
        return self._documents_from_evidence(
            self._finalize_evidence(query, candidate_documents)
        )

    def _retrieve_documents(self, query: str) -> list[Document]:
        """根据查询执行向量候选召回。

        参数：
        query：用户问题文本或数据库筛选条件。
        """
        self._ensure_ready()
        # 局部变量 expanded_query：增加领域词后的检索查询。
        expanded_query = self.lightweight_rerank_service.expand_query(query)
        # 局部变量 original_documents：重排或上下文扩展之前的原始检索结果。
        original_documents = list(self.candidate_retriever.invoke(query))
        # 局部变量 expanded_documents：加入邻接文本块后的扩展结果。
        expanded_documents = (
            list(self.candidate_retriever.invoke(expanded_query))
            if expanded_query.strip() != query.strip()
            else []
        )
        # 局部变量 candidate_documents：初始检索得到的候选文档列表。
        candidate_documents = _merge_candidate_documents(
            original_documents, expanded_documents
        )
        return self._documents_from_evidence(
            self._finalize_evidence(query, candidate_documents)
        )

    def _finalize_evidence(
        self, query: str, candidate_documents: list[Document]
    ) -> list[dict[str, Any]]:
        """执行候选过滤、重排、邻居扩展并生成最终证据。

        参数：
        query：用户问题文本或数据库筛选条件。
        candidate_documents：初始召回得到的候选文档集合。
        """
        # 局部变量 evidence：支持或限制回答的检索证据。
        evidence = self._candidate_evidence(candidate_documents)
        # 局部变量 ranked_evidence：按综合相关性排序后的证据列表。
        ranked_evidence = self._apply_rerank(query, evidence)
        # 局部变量 anchors：用于扩展邻近上下文的核心证据集合。
        anchors = ranked_evidence[: self.config.rag_top_k]
        # 局部变量 expanded_evidence：加入相邻文本块后的完整证据列表。
        expanded_evidence = self._expand_neighbors(query, anchors)
        if len(expanded_evidence) == len(anchors):
            return expanded_evidence[: self.config.rag_evidence_limit]
        return self._apply_rerank(query, expanded_evidence)[
            : self.config.rag_evidence_limit
        ]

    def _candidate_evidence(
        self, candidate_documents: list[Document]
    ) -> list[dict[str, Any]]:
        """将候选文档映射为标准证据记录。

        参数：
        candidate_documents：初始检索得到的候选文档列表。
        """
        # 局部变量 chunks_by_id：按文本分块 ID 建立的记录索引。
        # 局部变量 document：一条 LangChain 检索候选文档；其 metadata 中的 chunk_id 用于回查 SQLite 权威文本和来源记录。
        chunks_by_id = self._chunks_by_ids(
            _document_chunk_id(document) for document in candidate_documents
        )
        # 局部变量 evidence：支持或限制回答的检索证据。
        evidence: list[dict[str, Any]] = []
        # 局部变量 seen：用于防止重复处理的已见标识集合。
        seen: set[int] = set()
        for document in candidate_documents:
            # 局部变量 chunk_id：文本分块的唯一标识。
            chunk_id = _document_chunk_id(document)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            # 局部变量 chunk：当前文本分块记录。
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                continue
            # 局部变量 score：当前候选项或检索结果的相关度分数。
            score = float(
                document.metadata.get(
                    "retrieval_score", document.metadata.get("score", 0.0)
                )
            )
            evidence.append(_evidence_item(chunk, score, len(evidence) + 1))
        return evidence

    def _chunks_by_ids(self, chunk_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        """按分块标识批量读取原始文本块。

        参数：
        chunk_ids：需要查询或更新的文本分块标识列表。
        """
        # 局部变量 normalized_ids：去重并规范化后的对象标识集合。
        # 局部变量 chunk_id：文本分块的唯一标识。
        normalized_ids = list(dict.fromkeys(int(chunk_id) for chunk_id in chunk_ids))
        if not normalized_ids:
            return {}

        return self.repository.get_chunks_by_ids(normalized_ids)

    def _apply_rerank(
        self, query: str, evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """根据配置执行可选重排并裁剪结果。

        参数：
        query：用户问题文本或数据库筛选条件。
        evidence：支持回答的检索证据。
        """
        # 局部变量 ranked：按相关性排序后的候选结果集合。
        ranked = (
            self.lightweight_rerank_service.rerank(query, evidence)
            if self.config.lightweight_rerank_enabled
            else evidence
        )
        if self.config.rerank_enabled:
            return self.rerank_service.rerank(query, ranked)
        return ranked

    def _expand_neighbors(
        self, query: str, evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """为高排名证据补充相邻文本块。

        参数：
        query：用户问题文本或数据库筛选条件。
        evidence：支持回答的检索证据。
        """
        # 局部变量 main_evidence：排序后保留的主要证据项。
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        main_evidence = [dict(item) for item in evidence[: self.config.rag_top_k]]
        if not main_evidence or self.config.rag_neighbor_expand_top_n == 0:
            return main_evidence

        # 局部变量 get_neighbors：根据锚点获取相邻文本块的回调函数。
        get_neighbors = getattr(self.repository, "get_neighbor_chunks", None)
        if not callable(get_neighbors):
            return main_evidence

        # 局部变量 expanded：加入相邻分块或上下文后的结果集合。
        expanded = list(main_evidence)
        # 局部变量 seen_chunk_ids：已加入结果的文本分块标识集合。
        seen_chunk_ids = {int(item["chunk_id"]) for item in expanded}
        # 局部变量 anchor：用于扩展邻近上下文的核心证据分块。
        for anchor in main_evidence[: self.config.rag_neighbor_expand_top_n]:
            # 局部变量 neighbors：当前锚点找到的相邻文本块集合。
            neighbors = get_neighbors(
                int(anchor["document_id"]),
                int(anchor["chunk_no"]),
                radius=self.config.rag_neighbor_radius,
            )
            # 局部变量 chunk：当前文本分块记录。
            for chunk in neighbors:
                # 局部变量 chunk_id：文本分块的唯一标识。
                chunk_id = int(chunk["id"])
                if chunk_id in seen_chunk_ids:
                    continue
                # 局部变量 neighbor：锚点文本块附近的相邻分块。
                neighbor = _evidence_item(
                    chunk,
                    float(anchor.get("retrieval_score", anchor.get("score", 0.0))),
                    len(expanded) + 1,
                )
                neighbor["evidence_origin"] = "NEIGHBOR"
                neighbor["anchor_chunk_id"] = int(anchor["chunk_id"])
                neighbor["anchor_retrieval_score"] = float(
                    anchor.get("retrieval_score", anchor.get("score", 0.0))
                )
                expanded.append(neighbor)
                seen_chunk_ids.add(chunk_id)
        return expanded

    @staticmethod
    def _documents_from_evidence(evidence: list[dict[str, Any]]) -> list[Document]:
        """将标准证据记录转换为 LangChain 文档列表。

        参数：
        evidence：支持回答的检索证据。
        """
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        return [_document_from_evidence(item) for item in evidence]


# 保留 RetrievalService 旧导入名称，避免现有调用方因重命名而失效；
# 新代码以 DomainRetrievalService 作为领域实现的规范名称。
RetrievalService = DomainRetrievalService


def _evidence_item(
    chunk: Mapping[str, Any], score: float, rank_no: int
) -> dict[str, Any]:
    """构造包含文本、来源和相关度的证据字典。

    参数：
    score：候选证据或相似度分数。
    chunk：当前处理的文本分块。
    rank_no：当前候选项在排序结果中的名次。
    """
    # 局部变量 retrieval_score：向量检索返回的原始相关度分数。
    retrieval_score = min(1.0, max(0.0, float(score)))
    return {
        "chunk_id": int(chunk["id"]),
        "document_id": int(chunk["document_id"]),
        "file_name": str(chunk["file_name"]),
        "chunk_no": int(chunk["chunk_no"]),
        "content": str(chunk["content"]),
        "score": retrieval_score,
        "retrieval_score": retrieval_score,
        "rerank_score": None,
        "lexical_score": 0.0,
        "source_score": 0.0,
        "lightweight_score": retrieval_score,
        "evidence_origin": "RETRIEVED",
        "anchor_chunk_id": None,
        "anchor_retrieval_score": None,
        "rank_no": rank_no,
    }


def _document_chunk_id(document: Document) -> int:
    """从文档元数据中提取文本分块标识。

    参数：
    document：当前处理的文档对象或记录。
    """
    # 局部变量 value：优先读取 metadata.chunk_id、缺失时回退到 Document.id 的原始标识；随后转换为整数并验证为正数。
    value = document.metadata.get("chunk_id", document.id)
    try:
        return int(value)
    # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
    except (TypeError, ValueError) as exc:
        raise ValueError("检索 Document 缺少有效 chunk_id") from exc


def _document_from_evidence(item: Mapping[str, Any]) -> Document:
    """将证据字典恢复为 LangChain 文档对象。

    参数：
    item：当前遍历或转换的数据项。
    """
    # 局部变量 chunk_id：文本分块的唯一标识。
    chunk_id = int(item["chunk_id"])
    # 局部变量 metadata：从 SQLite 证据记录组装的 LangChain Document 元数据，包含 chunk_id、document_id、文件名、分块序号和检索评分。
    metadata = {
        "chunk_id": chunk_id,
        "document_id": int(item["document_id"]),
        "file_name": str(item["file_name"]),
        "chunk_no": int(item["chunk_no"]),
        "score": float(item["score"]),
        "retrieval_score": float(item["retrieval_score"]),
        "rerank_score": item.get("rerank_score"),
        "lexical_score": float(item.get("lexical_score", 0.0)),
        "source_score": float(item.get("source_score", 0.0)),
        "lightweight_score": float(
            item.get("lightweight_score", item.get("retrieval_score", 0.0))
        ),
        "evidence_origin": str(item.get("evidence_origin", "RETRIEVED")),
        "anchor_chunk_id": item.get("anchor_chunk_id"),
        "anchor_retrieval_score": item.get("anchor_retrieval_score"),
        "rank_no": int(item["rank_no"]),
    }
    return Document(
        id=str(chunk_id),
        page_content=str(item["content"]),
        metadata=metadata,
    )


def _evidence_from_document(document: Document) -> dict[str, Any]:
    """将 LangChain 文档转换为统一证据字典。

    参数：
    document：当前处理的文档对象或记录。
    """
    # 局部变量 metadata：检索 Document 携带的来源和评分字段；据此还原为 API/服务使用的证据字典，不重复读取正文。
    metadata = document.metadata
    return {
        "chunk_id": int(metadata["chunk_id"]),
        "document_id": int(metadata["document_id"]),
        "file_name": str(metadata["file_name"]),
        "chunk_no": int(metadata["chunk_no"]),
        "content": document.page_content,
        "score": float(metadata["score"]),
        "retrieval_score": float(metadata["retrieval_score"]),
        "rerank_score": metadata.get("rerank_score"),
        "lexical_score": float(metadata.get("lexical_score", 0.0)),
        "source_score": float(metadata.get("source_score", 0.0)),
        "lightweight_score": float(
            metadata.get("lightweight_score", metadata["retrieval_score"])
        ),
        "evidence_origin": str(metadata.get("evidence_origin", "RETRIEVED")),
        "anchor_chunk_id": metadata.get("anchor_chunk_id"),
        "anchor_retrieval_score": metadata.get("anchor_retrieval_score"),
        "rank_no": int(metadata["rank_no"]),
    }


def _merge_candidate_documents(
    original_documents: list[Document], expanded_documents: list[Document]
) -> list[Document]:
    """按分块 ID 合并原始召回与邻接扩展两组 LangChain Document；首次出现的记录决定输出位置，重复分块不会重复进入后续重排。

        参数：
        original_documents：重排或上下文扩展之前的原始检索结果。
        expanded_documents：加入邻接文本块后的扩展结果。
    """

    # 局部变量 merged：合并去重后的文本或结果集合。
    merged: list[Document] = []
    # 局部变量 positions：记录文本边界对应的字符偏移位置。
    positions: dict[int, int] = {}
    # 局部变量 document：原始召回或邻接扩展中的 LangChain Document；读取其 chunk_id 后用于去重并保持首次出现的候选顺序。
    for document in [*original_documents, *expanded_documents]:
        # 局部变量 chunk_id：文本分块的唯一标识。
        chunk_id = _document_chunk_id(document)
        # 局部变量 position：当前循环或列表处理中的序号、位置。
        position = positions.get(chunk_id)
        if position is None:
            positions[chunk_id] = len(merged)
            merged.append(document)
            continue
        # 局部变量 current：merged 中已存在的同一 chunk_id 候选文档；与新候选比较 retrieval_score，只在新分数更高时替换原位置。
        current = merged[position]
        if _document_retrieval_score(document) > _document_retrieval_score(current):
            merged[position] = document
    return merged


def _document_retrieval_score(document: Document) -> float:
    """读取文档上的检索相关度分数。

    参数：
    document：当前处理的文档对象或记录。
    """
    # 局部变量 value：候选文档 metadata 中优先使用 retrieval_score、其次使用 score 的原始分值；缺失或格式错误时按 0 分处理。
    value = document.metadata.get(
        "retrieval_score", document.metadata.get("score", 0.0)
    )
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
