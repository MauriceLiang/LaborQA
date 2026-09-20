"""Deterministic, dependency-free ranking helpers for legal retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

LEGAL_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "上班": ("用工", "劳动关系"),
    "入职": ("用工", "劳动关系"),
    "没签合同": ("未订立书面劳动合同",),
    "未签合同": ("未订立书面劳动合同",),
    "没签劳动合同": ("未订立书面劳动合同",),
    "工资": ("劳动报酬",),
    "薪资": ("劳动报酬",),
    "辞职": ("解除劳动合同",),
    "离职": ("解除劳动合同",),
    "怀孕": ("孕期",),
    "合同到期": ("劳动合同期满", "劳动合同终止"),
    "调岗": ("工作岗位", "变更劳动合同"),
    "降薪": ("劳动报酬", "变更劳动合同"),
    "社保": ("社会保险",),
    "加班": ("延长工作时间",),
    "工伤": ("工伤认定",),
    "裁员": ("裁减人员",),
    "拖欠工资": ("未及时足额支付劳动报酬", "拖欠劳动报酬", "无故拖欠工资"),
    "没发工资": ("未及时足额支付劳动报酬", "拖欠劳动报酬"),
    "受伤": ("事故伤害",),
    "工作时间": ("工作时间",),
    "工作场所": ("工作场所",),
    "因工作": ("工作原因",),
    "最低工资": ("最低工资标准",),
    "加班工资": ("延长工作时间工资", "加班加点工资"),
    "约定不明": ("重新协商", "集体合同", "同工同酬"),
    "仲裁时效": ("申请仲裁的时效期间", "时效期间为一年"),
    "劳动监察": ("劳动行政部门", "投诉", "举报"),
    "投诉": ("劳动行政部门", "举报"),
}

_DOMAIN_TERMS = (
    "劳动关系",
    "劳动合同",
    "试用期",
    "劳动报酬",
    "解除",
    "终止",
    "孕期",
    "工作岗位",
    "社会保险",
    "延长工作时间",
    "工伤认定",
    "裁减人员",
)

_QUERY_KEYWORDS = (
    "最低工资",
    "加班工资",
    "工资",
    "劳动报酬",
    "拖欠工资",
    "工作时间",
    "工作场所",
    "工作原因",
    "事故伤害",
    "工伤",
    "工伤认定",
    "仲裁",
    "仲裁时效",
    "投诉",
    "举报",
    "劳动监察",
    "劳动行政部门",
    "集体合同",
    "同工同酬",
    "经济补偿",
    "社会保险",
    "劳动合同",
    "试用期",
    "孕期",
    "产假",
    "年休假",
)


def normalize_text(value: str) -> str:
    """Normalize text for deterministic substring matching."""

    return (
        value.lower()
        .replace(" ", "")
        .replace("\n", "")
        .replace("\t", "")
        .replace("　", "")
    )


class LightweightRerankService:
    """Rank candidates with retrieval, legal-term and source signals only."""

    def expand_query(self, query: str) -> str:
        normalized_query = normalize_text(query)
        terms = [query.strip()]
        for source, expansions in LEGAL_QUERY_EXPANSIONS.items():
            if normalize_text(source) in normalized_query:
                terms.extend(expansions)
        return " ".join(_dedupe(term for term in terms if term))

    def terms_for_query(self, query: str) -> list[str]:
        normalized_query = normalize_text(query)
        terms: list[str] = []
        for source, expansions in LEGAL_QUERY_EXPANSIONS.items():
            if normalize_text(source) in normalized_query:
                terms.append(source)
                terms.extend(expansions)
        terms.extend(
            term for term in _DOMAIN_TERMS if normalize_text(term) in normalized_query
        )
        terms.extend(
            term for term in _QUERY_KEYWORDS if normalize_text(term) in normalized_query
        )
        if not terms:
            terms.extend(part for part in query.split() if len(part.strip()) > 1)
        if not terms and query.strip():
            terms.append(query.strip())
        return _dedupe(term for term in terms if term.strip())

    def lexical_score(self, query: str, content: str) -> float:
        terms = self.terms_for_query(query)
        if not terms:
            return 0.0
        normalized_content = normalize_text(content)
        matched = sum(1 for term in terms if normalize_text(term) in normalized_content)
        return matched / len(terms)

    @staticmethod
    def source_score(file_name: str) -> float:
        name = str(file_name).replace("\\", "/").rsplit("/", 1)[-1]
        if name.startswith(("01_", "02_", "03_", "04_", "05_", "06_", "07_")):
            return 1.0
        if name.startswith("08_"):
            return 0.4
        return 0.6

    def rerank(
        self, query: str, evidence: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not evidence:
            return []

        ranked: list[tuple[int, dict[str, Any], float]] = []
        for index, item in enumerate(evidence):
            retrieval_score = _bounded_score(
                item.get("retrieval_score", item.get("score", 0.0))
            )
            lexical_score = self.lexical_score(query, str(item.get("content", "")))
            source_score = self.source_score(str(item.get("file_name", "")))
            lightweight_score = (
                retrieval_score * 0.75 + lexical_score * 0.20 + source_score * 0.05
            )
            ranked.append(
                (
                    index,
                    {
                        **item,
                        "retrieval_score": retrieval_score,
                        "lexical_score": lexical_score,
                        "source_score": source_score,
                        "lightweight_score": lightweight_score,
                    },
                    lightweight_score,
                )
            )

        ranked.sort(key=lambda result: (-result[2], result[0]))
        return [
            {
                **item,
                "rank_no": rank_no,
            }
            for rank_no, (_, item, _) in enumerate(ranked, start=1)
        ]


def _bounded_score(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _dedupe(values: Sequence[str] | Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if normalized and normalized not in seen:
            result.append(value)
            seen.add(normalized)
    return result
