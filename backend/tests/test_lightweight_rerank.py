from typing import Any

import pytest

from app.services.lightweight_rerank import LightweightRerankService


def test_expand_query_adds_legal_terms_without_answer_rules() -> None:
    service = LightweightRerankService()

    expanded = service.expand_query("我在试用期想离职")

    assert "解除劳动合同" in expanded
    assert "提前三日" not in expanded


def test_expand_query_adds_employment_and_written_contract_terms() -> None:
    service = LightweightRerankService()

    expanded = service.expand_query("没签劳动合同已经上班")

    assert "未订立书面劳动合同" in expanded
    assert "用工" in expanded
    assert "劳动关系" in expanded


def test_lightweight_score_can_promote_lexically_supported_formal_source() -> None:
    service = LightweightRerankService()
    evidence: list[dict[str, Any]] = [
        {
            "chunk_id": 1,
            "file_name": "01_劳动关系与劳动合同.md",
            "content": "劳动者在试用期内想离职的，可以提前三日通知并解除劳动合同。",
            "retrieval_score": 0.82,
            "rank_no": 1,
        },
        {
            "chunk_id": 2,
            "file_name": "08_辅助案例与政策解读.md",
            "content": "这是一个与问题关联较弱的案例说明。",
            "retrieval_score": 0.84,
            "rank_no": 2,
        },
    ]

    ranked = service.rerank("试用期想离职", evidence)

    assert [item["chunk_id"] for item in ranked] == [1, 2]
    assert ranked[0]["lexical_score"] > ranked[1]["lexical_score"]
    assert ranked[0]["source_score"] == 1.0
    assert ranked[0]["lightweight_score"] == pytest.approx(0.865)
    assert ranked[1]["lightweight_score"] == pytest.approx(0.65)


def test_source_score_prefers_formal_documents_only_when_other_signals_match() -> None:
    service = LightweightRerankService()

    assert service.source_score("01_劳动关系与劳动合同.md") == 1.0
    assert service.source_score("08_辅助案例与政策解读.md") == 0.4
    assert service.source_score("其他资料.md") == 0.6
