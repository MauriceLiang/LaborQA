"""Domain retrieval pipeline built on the LangChain VectorStore retriever."""

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
    """Apply LaborQA's SQLite mapping and reranking around a LangChain retriever.

    The vector store owns embedding and FAISS retrieval. This service keeps the
    business boundary: SQLite remains authoritative for chunk content and
    source metadata, while optional domain reranking produces final evidence.
    ``retriever`` is a LangChain Runnable pipeline so callers do not need a
    custom retriever adapter.
    """

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
        self.config = config
        self.repository = repository or DocumentRepository(config.database_path)
        self.embedding_service = embedding_service or LangChainEmbeddingService(config)
        self.vector_store = vector_store or VectorStoreService(
            config=config,
            embedding_service=self.embedding_service,
            index_dir=config.faiss_path / "production",
        )
        if (
            callable(getattr(self.vector_store, "as_retriever", None))
            and getattr(self.vector_store, "embedding_service", None) is None
        ):
            # Keep injected VectorStore implementations compatible with the
            # production Embeddings facade without changing their public API.
            self.vector_store.embedding_service = self.embedding_service
        self.rerank_service = rerank_service or RerankService(config)
        self.lightweight_rerank_service = (
            lightweight_rerank_service or LightweightRerankService()
        )
        self.candidate_retriever = self._build_candidate_retriever()
        self.retriever = self._build_domain_retriever()

    def _build_candidate_retriever(self) -> Runnable[Any, list[Document]]:
        return self.vector_store.as_retriever(
            search_kwargs={"k": self.config.rag_candidate_k}
        )

    def _build_domain_retriever(self) -> Runnable[Any, list[Document]]:
        return RunnableLambda(self._retrieve_documents)

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        """Synchronously retrieve evidence through the LangChain pipeline."""
        self._ensure_ready()
        documents = self.retriever.invoke(query)
        return [_evidence_from_document(document) for document in documents]

    async def aretrieve(self, query: str) -> list[dict[str, Any]]:
        """Asynchronously invoke the LangChain retrieval pipeline."""
        self._ensure_ready()
        documents = await self.retriever.ainvoke(query)
        return [_evidence_from_document(document) for document in documents]

    def _ensure_ready(self) -> None:
        status = self.vector_store.status
        if status == "not_initialized":
            raise VectorStoreNotInitialized("向量知识库尚未初始化")
        if status == "incompatible":
            raise VectorStoreSignatureMismatch("当前 Embedding 签名与生产索引不一致")

    def _documents_to_evidence_documents(
        self, state: Mapping[str, Any]
    ) -> list[Document]:
        query = str(state["query"])
        candidate_documents = list(state.get("documents", []))
        return self._documents_from_evidence(
            self._finalize_evidence(query, candidate_documents)
        )

    def _retrieve_documents(self, query: str) -> list[Document]:
        self._ensure_ready()
        expanded_query = self.lightweight_rerank_service.expand_query(query)
        original_documents = list(self.candidate_retriever.invoke(query))
        expanded_documents = (
            list(self.candidate_retriever.invoke(expanded_query))
            if expanded_query.strip() != query.strip()
            else []
        )
        candidate_documents = _merge_candidate_documents(
            original_documents, expanded_documents
        )
        return self._documents_from_evidence(
            self._finalize_evidence(query, candidate_documents)
        )

    def _finalize_evidence(
        self, query: str, candidate_documents: list[Document]
    ) -> list[dict[str, Any]]:
        evidence = self._candidate_evidence(candidate_documents)
        ranked_evidence = self._apply_rerank(query, evidence)
        anchors = ranked_evidence[: self.config.rag_top_k]
        expanded_evidence = self._expand_neighbors(query, anchors)
        if len(expanded_evidence) == len(anchors):
            return expanded_evidence[: self.config.rag_evidence_limit]
        return self._apply_rerank(query, expanded_evidence)[
            : self.config.rag_evidence_limit
        ]

    def _candidate_evidence(
        self, candidate_documents: list[Document]
    ) -> list[dict[str, Any]]:
        chunks_by_id = self._chunks_by_ids(
            _document_chunk_id(document) for document in candidate_documents
        )
        evidence: list[dict[str, Any]] = []
        seen: set[int] = set()
        for document in candidate_documents:
            chunk_id = _document_chunk_id(document)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                continue
            score = float(
                document.metadata.get(
                    "retrieval_score", document.metadata.get("score", 0.0)
                )
            )
            evidence.append(_evidence_item(chunk, score, len(evidence) + 1))
        return evidence

    def _chunks_by_ids(self, chunk_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        normalized_ids = list(dict.fromkeys(int(chunk_id) for chunk_id in chunk_ids))
        if not normalized_ids:
            return {}

        return self.repository.get_chunks_by_ids(normalized_ids)

    def _apply_rerank(
        self, query: str, evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
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
        main_evidence = [dict(item) for item in evidence[: self.config.rag_top_k]]
        if not main_evidence or self.config.rag_neighbor_expand_top_n == 0:
            return main_evidence

        get_neighbors = getattr(self.repository, "get_neighbor_chunks", None)
        if not callable(get_neighbors):
            return main_evidence

        expanded = list(main_evidence)
        seen_chunk_ids = {int(item["chunk_id"]) for item in expanded}
        for anchor in main_evidence[: self.config.rag_neighbor_expand_top_n]:
            neighbors = get_neighbors(
                int(anchor["document_id"]),
                int(anchor["chunk_no"]),
                radius=self.config.rag_neighbor_radius,
            )
            for chunk in neighbors:
                chunk_id = int(chunk["id"])
                if chunk_id in seen_chunk_ids:
                    continue
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
        return [_document_from_evidence(item) for item in evidence]


# Keep the existing import path stable while making the domain service the
# canonical implementation name for new code.
RetrievalService = DomainRetrievalService


def _evidence_item(
    chunk: Mapping[str, Any], score: float, rank_no: int
) -> dict[str, Any]:
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
    value = document.metadata.get("chunk_id", document.id)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("检索 Document 缺少有效 chunk_id") from exc


def _document_from_evidence(item: Mapping[str, Any]) -> Document:
    chunk_id = int(item["chunk_id"])
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
    """Merge two retrieval passes while keeping first-seen order stable."""

    merged: list[Document] = []
    positions: dict[int, int] = {}
    for document in [*original_documents, *expanded_documents]:
        chunk_id = _document_chunk_id(document)
        position = positions.get(chunk_id)
        if position is None:
            positions[chunk_id] = len(merged)
            merged.append(document)
            continue
        current = merged[position]
        if _document_retrieval_score(document) > _document_retrieval_score(current):
            merged[position] = document
    return merged


def _document_retrieval_score(document: Document) -> float:
    value = document.metadata.get(
        "retrieval_score", document.metadata.get("score", 0.0)
    )
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
