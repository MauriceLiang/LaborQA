from __future__ import annotations

from typing import Any

import pytest
from langchain_core.documents import Document
from langchain_core.runnables import Runnable, RunnableLambda

from app.core.config import Settings
from app.services.lightweight_rerank import LightweightRerankService
from app.services.retrieval import RetrievalService


class QueryAwareEmbedding:
    def embed_query(self, query: str) -> list[float]:
        return [0.1, 0.2]


class QueryAwareVectorStore:
    status = "ready"

    def __init__(
        self,
        original_hits: list[tuple[int, float]],
        expanded_hits: list[tuple[int, float]],
    ) -> None:
        self.original_hits = original_hits
        self.expanded_hits = expanded_hits
        self.embedding_service: QueryAwareEmbedding | None = None
        self.queries: list[str] = []

    def as_retriever(
        self, *, search_kwargs: dict[str, Any]
    ) -> Runnable[Any, list[Document]]:
        return RunnableLambda(self._retrieve)

    def _retrieve(self, query: str) -> list[Document]:
        self.queries.append(query)
        hits = self.original_hits if len(self.queries) == 1 else self.expanded_hits
        return [
            Document(
                id=str(chunk_id),
                page_content="",
                metadata={
                    "chunk_id": chunk_id,
                    "score": score,
                    "retrieval_score": score,
                },
            )
            for chunk_id, score in hits
        ]


class QualityRepository:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks

    def get_chunks_by_ids(self, chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
        requested = {int(chunk_id) for chunk_id in chunk_ids}
        return {
            int(chunk["id"]): chunk
            for chunk in self.chunks
            if int(chunk["id"]) in requested
        }

    def get_neighbor_chunks(
        self, document_id: int, chunk_no: int, *, radius: int = 1
    ) -> list[dict[str, Any]]:
        return [
            chunk
            for chunk in self.chunks
            if int(chunk["document_id"]) == document_id
            and abs(int(chunk["chunk_no"]) - chunk_no) <= radius
            and int(chunk["chunk_no"]) != chunk_no
        ]


def _chunk(
    chunk_id: int,
    chunk_no: int,
    content: str,
    *,
    file_name: str = "01_劳动关系与劳动合同.md",
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "document_id": 1,
        "file_name": file_name,
        "chunk_no": chunk_no,
        "content": content,
    }


def test_original_and_expanded_query_candidates_are_merged(tmp_path: Any) -> None:
    chunks = [_chunk(1, 1, "普通片段"), _chunk(2, 2, "另一片段")]
    vector_store = QueryAwareVectorStore([(1, 0.80)], [(2, 0.90), (1, 0.60)])
    config = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'retrieval.db'}",
        lightweight_rerank_enabled=False,
        rag_top_k=3,
        rag_evidence_limit=5,
        rag_neighbor_expand_top_n=0,
    )
    service = RetrievalService(
        config,
        repository=QualityRepository(chunks),
        embedding_service=QueryAwareEmbedding(),
        vector_store=vector_store,
    )

    evidence = service.retrieve("拖欠工资")

    assert vector_store.queries == [
        "拖欠工资",
        service.lightweight_rerank_service.expand_query("拖欠工资"),
    ]
    assert [item["chunk_id"] for item in evidence] == [1, 2]
    assert evidence[0]["retrieval_score"] == pytest.approx(0.80)


def test_duplicate_candidates_keep_the_best_retrieval_score(tmp_path: Any) -> None:
    chunks = [_chunk(1, 1, "普通片段"), _chunk(2, 2, "另一片段")]
    vector_store = QueryAwareVectorStore([(1, 0.80)], [(1, 0.95), (2, 0.60)])
    config = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'retrieval.db'}",
        lightweight_rerank_enabled=False,
        rag_top_k=3,
        rag_evidence_limit=5,
        rag_neighbor_expand_top_n=0,
    )
    service = RetrievalService(
        config,
        repository=QualityRepository(chunks),
        embedding_service=QueryAwareEmbedding(),
        vector_store=vector_store,
    )

    evidence = service.retrieve("拖欠工资")

    assert [item["chunk_id"] for item in evidence] == [1, 2]
    assert evidence[0]["retrieval_score"] == pytest.approx(0.95)


def test_neighbor_expansion_collects_all_anchors_before_global_rerank(
    tmp_path: Any,
) -> None:
    chunks = [
        _chunk(1, 10, "普通片段"),
        _chunk(2, 20, "普通片段"),
        _chunk(12, 9, "另一普通邻接片段"),
        _chunk(11, 11, "普通邻接片段"),
        _chunk(22, 19, "另一普通邻接片段"),
        _chunk(21, 21, "劳动行政部门投诉举报规则"),
    ]
    vector_store = QueryAwareVectorStore([(1, 0.90), (2, 0.89)], [])
    config = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'neighbor.db'}",
        rag_top_k=2,
        rag_evidence_limit=5,
        rag_neighbor_expand_top_n=2,
        rag_neighbor_radius=1,
        lightweight_rerank_enabled=True,
        rerank_enabled=False,
    )
    service = RetrievalService(
        config,
        repository=QualityRepository(chunks),
        embedding_service=QueryAwareEmbedding(),
        vector_store=vector_store,
    )

    evidence = service.retrieve("投诉")

    chunk_ids = [item["chunk_id"] for item in evidence]
    assert 21 in chunk_ids
    assert 11 in chunk_ids
    assert [item["rank_no"] for item in evidence] == [1, 2, 3, 4, 5]
    assert (
        next(item for item in evidence if item["chunk_id"] == 21)["anchor_chunk_id"]
        == 2
    )


def test_legal_query_expansion_contains_reusable_concepts() -> None:
    from app.services.lightweight_rerank import LightweightRerankService

    service = LightweightRerankService()

    expanded = service.expand_query("约定不明的工资和拖欠工资能否解除？")
    terms = service.terms_for_query("累计工作8年，年休假有几天？")

    assert "重新协商" in expanded
    assert "集体合同" in expanded
    assert "同工同酬" in expanded
    assert "未及时足额支付劳动报酬" in expanded
    assert "年休假" in terms


@pytest.mark.parametrize(
    ("query", "expected_terms"),
    [
        ("工作时间和工作场所因工作原因受伤", ["工作原因", "事故伤害"]),
        ("最低工资和加班工资怎么算？", ["最低工资标准", "延长工作时间工资"]),
        ("劳动仲裁时效多久？", ["申请仲裁的时效期间", "时效期间为一年"]),
        ("拖欠工资可以向劳动监察投诉吗？", ["劳动行政部门", "举报"]),
    ],
)
def test_legal_query_expansion_covers_golden_retrieval_concepts(
    query: str, expected_terms: list[str]
) -> None:
    from app.services.lightweight_rerank import LightweightRerankService

    expanded = LightweightRerankService().expand_query(query)

    assert all(term in expanded for term in expected_terms)


def test_work_location_expands_to_contract_change_terms() -> None:
    service = LightweightRerankService()
    query = "公司能单方面把劳动合同的工作地点改到外省吗？"

    expanded = service.expand_query(query)

    assert all(term in expanded for term in ("工作地点", "变更劳动合同", "协商一致"))


def test_wage_arrears_variants_expand() -> None:
    service = LightweightRerankService()
    queries = (
        "公司拖欠工资，我可以解除吗？",
        "公司拖欠我两个月工资，我可以解除吗？",
        "单位一直拖欠我的劳动报酬，我能解除合同吗？",
        "工资已经拖了三个月",
    )

    for query in queries:
        expanded = service.expand_query(query)
        terms = service.terms_for_query(query)

        assert "未及时足额支付劳动报酬" in expanded
        assert "未及时足额支付劳动报酬" in terms
        if "解除" in query:
            assert "解除劳动合同" in expanded
            assert "解除劳动合同" in terms


def test_wage_arrears_compensation_query_expands_both_rules() -> None:
    service = LightweightRerankService()
    query = "公司拖欠我两个月工资，我能解除并主张补偿吗？"

    expanded = service.expand_query(query)

    assert "未及时足额支付劳动报酬" in expanded
    assert "经济补偿" in expanded


def test_labor_rule_query_expands_democratic_procedure_terms() -> None:
    service = LightweightRerankService()
    query = "公司制定涉及工资和工时的制度，可以老板自己决定吗？"

    expanded = service.expand_query(query)

    assert all(
        term in expanded
        for term in (
            "直接涉及劳动者切身利益",
            "职工代表大会",
            "全体职工讨论",
            "平等协商确定",
        )
    )
    assert "职工代表大会" not in service.expand_query("公司的服务器备份制度怎么设计？")


def test_wage_arrears_complaint_expands_general_complaint_rule() -> None:
    service = LightweightRerankService()
    query = "单位长期拖欠工资，我应该向劳动监察部门投诉吗？"

    expanded = service.expand_query(query)

    assert "劳动者可以向劳动行政部门投诉" in expanded
    assert "劳动行政部门应当依法处理" in expanded
