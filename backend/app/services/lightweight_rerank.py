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
    "工作地点": ("变更劳动合同", "协商一致"),
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
    "最低工资": ("最低工资标准", "最低工资保障制度"),
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
    "工作地点",
    "变更劳动合同",
    "协商一致",
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

    def _expansion_terms(self, query: str) -> list[str]:
        normalized_query = normalize_text(query)
        terms: list[str] = []
        for source, expansions in LEGAL_QUERY_EXPANSIONS.items():
            if normalize_text(source) in normalized_query:
                terms.extend(expansions)
        terms.extend(_conditional_expansions(normalized_query))
        return _dedupe(terms)

    def expand_query(self, query: str) -> str:
        return " ".join(
            _dedupe([query.strip(), *self._expansion_terms(query)])
        )

    def terms_for_query(self, query: str) -> list[str]:
        normalized_query = normalize_text(query)
        terms = self._expansion_terms(query)
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


def _conditional_expansions(normalized_query: str) -> list[str]:
    """Expand reusable legal concepts that require multiple query signals."""

    terms: list[str] = []

    if "工作地点" in normalized_query:
        terms.extend(("变更劳动合同", "协商一致"))

    has_rule = any(token in normalized_query for token in ("制度", "规章", "规定"))
    has_labor_interest = any(
        token in normalized_query
        for token in (
            "工资",
            "劳动报酬",
            "工时",
            "工作时间",
            "休息休假",
            "考勤",
            "保险福利",
            "劳动纪律",
        )
    )
    if has_rule and has_labor_interest:
        terms.extend(
            (
                "直接涉及劳动者切身利益",
                "职工代表大会",
                "全体职工讨论",
                "工会",
                "职工代表",
                "平等协商确定",
            )
        )

    has_arrears_marker = any(
        token in normalized_query
        for token in ("拖欠", "拖了", "欠薪", "未发", "没发")
    )
    has_wage_arrears = has_arrears_marker and any(
        token in normalized_query for token in ("工资", "劳动报酬")
    )
    if has_wage_arrears:
        terms.extend(
            (
                "未及时足额支付劳动报酬",
                "拖欠劳动报酬",
                "无故拖欠工资",
            )
        )
        if "解除" in normalized_query:
            terms.extend(("解除劳动合同", "劳动合同法第三十八条"))
        if any(token in normalized_query for token in ("补偿", "经济补偿")):
            terms.extend(("经济补偿", "劳动合同法第四十六条"))

    has_complaint_intent = any(
        token in normalized_query
        for token in ("投诉", "劳动监察", "劳动行政部门", "有关部门", "反映", "举报")
    )
    if has_wage_arrears and has_complaint_intent:
        terms.extend(
            (
                "拖欠或者未足额支付劳动报酬",
                "劳动者可以向劳动行政部门投诉",
                "劳动行政部门应当依法处理",
            )
        )

    return _dedupe(terms)
