# 文件作用：使用轻量级词项与特征打分对候选检索片段重新排序。
"""Deterministic, dependency-free ranking helpers for legal retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# 常见劳动权益查询词到相关检索表达的扩展映射。
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

# 用于判定劳动领域相关性的关键词集合。
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

# 从查询中抽取核心词项时忽略的通用词集合。
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
    """Normalize text for deterministic substring matching.

    参数：
    value：当前待校验、转换或展示的值。
    """

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
        """从问题中提取适合劳动领域检索扩展的关键词。

        参数：
        query：用户问题文本或数据库筛选条件。
        """
        # 局部变量 normalized_query：规范化后的检索关键词文本。
        normalized_query = normalize_text(query)
        # 局部变量 terms：从问题或文本中提取的关键词集合。
        terms: list[str] = []
        # 局部变量 source：文档、引用或记录的来源信息。
        # 局部变量 expansions：当前查询可追加的检索扩展词集合。
        for source, expansions in LEGAL_QUERY_EXPANSIONS.items():
            if normalize_text(source) in normalized_query:
                terms.extend(expansions)
        terms.extend(_conditional_expansions(normalized_query))
        return _dedupe(terms)

    def expand_query(self, query: str) -> str:
        """为用户问题补充领域相关检索词。

        参数：
        query：用户问题文本或数据库筛选条件。
        """
        return " ".join(
            _dedupe([query.strip(), *self._expansion_terms(query)])
        )

    def terms_for_query(self, query: str) -> list[str]:
        """将问题拆分为用于词项匹配的标准词集合。

        参数：
        query：用户问题文本或数据库筛选条件。
        """
        # 局部变量 normalized_query：规范化后的检索关键词文本。
        normalized_query = normalize_text(query)
        # 局部变量 terms：从问题或文本中提取的关键词集合。
        terms = self._expansion_terms(query)
        # 局部变量 term：当前用于关键词匹配的单个词项。
        terms.extend(
            term for term in _DOMAIN_TERMS if normalize_text(term) in normalized_query
        )
        terms.extend(
            term for term in _QUERY_KEYWORDS if normalize_text(term) in normalized_query
        )
        if not terms:
            # 局部变量 part：当前处理的文本片段。
            terms.extend(part for part in query.split() if len(part.strip()) > 1)
        if not terms and query.strip():
            terms.append(query.strip())
        return _dedupe(term for term in terms if term.strip())

    def lexical_score(self, query: str, content: str) -> float:
        """计算查询词与候选文本之间的词面相关性分数。

        参数：
        query：用户问题文本或数据库筛选条件。
        content：需要处理或保存的正文内容。
        """
        # 局部变量 terms：从问题或文本中提取的关键词集合。
        terms = self.terms_for_query(query)
        if not terms:
            return 0.0
        # 局部变量 normalized_content：清理并规范化后的文档或消息正文。
        normalized_content = normalize_text(content)
        # 局部变量 matched：当前条件是否匹配。
        # 局部变量 term：当前用于关键词匹配的单个词项。
        matched = sum(1 for term in terms if normalize_text(term) in normalized_content)
        return matched / len(terms)

    @staticmethod
    def source_score(file_name: str) -> float:
        """根据来源类型和元数据计算证据可信度分数。

        参数：
        file_name：规范化后的文档文件名。
        """
        # 局部变量 name：经过规范化或校验的资源名称。
        name = str(file_name).replace("\\", "/").rsplit("/", 1)[-1]
        if name.startswith(("01_", "02_", "03_", "04_", "05_", "06_", "07_")):
            return 1.0
        if name.startswith("08_"):
            return 0.4
        return 0.6

    def rerank(
        self, query: str, evidence: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """综合语义、词面和来源信号对检索结果重新排序。

        参数：
        query：用户问题文本或数据库筛选条件。
        evidence：支持回答的检索证据。
        """
        if not evidence:
            return []

        # 局部变量 ranked：按相关性排序后的候选结果集合。
        ranked: list[tuple[int, dict[str, Any], float]] = []
        # 局部变量 index：当前记录在列表中的位置或索引序号。
        # 局部变量 item：当前列表或结果集合中的一条数据。
        for index, item in enumerate(evidence):
            # 局部变量 retrieval_score：向量检索返回的原始相关度分数。
            retrieval_score = _bounded_score(
                item.get("retrieval_score", item.get("score", 0.0))
            )
            # 局部变量 lexical_score：查询词与文本词项匹配得到的分数。
            lexical_score = self.lexical_score(query, str(item.get("content", "")))
            # 局部变量 source_score：来源可信度计算得到的分数。
            source_score = self.source_score(str(item.get("file_name", "")))
            # 局部变量 lightweight_score：轻量重排规则计算出的分数。
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
        # 局部变量 rank_no：当前候选项在重排结果中的名次。
        return [
            {
                **item,
                "rank_no": rank_no,
            }
            for rank_no, (_, item, _) in enumerate(ranked, start=1)
        ]


def _bounded_score(value: Any) -> float:
    """将重排分数限制在有效上下界内。

    参数：
    value：需要转换或校验的输入值。
    """
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _dedupe(values: Sequence[str] | Any) -> list[str]:
    """去除重复词项并保持原有顺序。

    参数：
    values：待写入数据库或输出的字段值集合。
    """
    # 局部变量 seen：用于防止重复处理的已见标识集合。
    seen: set[str] = set()
    # 局部变量 result：当前函数调用或计算产生、供后续处理或返回的结果数据。
    result: list[str] = []
    # 局部变量 value：当前待校验、转换或展示的值。
    for value in values:
        # 局部变量 normalized：经过规范化处理后的数据。
        normalized = normalize_text(value)
        if normalized and normalized not in seen:
            result.append(value)
            seen.add(normalized)
    return result


def _conditional_expansions(normalized_query: str) -> list[str]:
    """Expand reusable legal concepts that require multiple query signals.

    参数：
    normalized_query：规范化后的检索关键词文本。
    """

    # 局部变量 terms：从问题或文本中提取的关键词集合。
    terms: list[str] = []

    if "工作地点" in normalized_query:
        terms.extend(("变更劳动合同", "协商一致"))

    # 局部变量 has_rule：当前问题是否命中劳动规则关键词。
    # 局部变量 token：当前模型流返回的文本 token。
    has_rule = any(token in normalized_query for token in ("制度", "规章", "规定"))
    # 局部变量 has_labor_interest：当前问题是否涉及劳动权益。
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

    # 局部变量 has_arrears_marker：当前文本是否包含欠薪相关标记。
    has_arrears_marker = any(
        token in normalized_query
        for token in ("拖欠", "拖了", "欠薪", "未发", "没发")
    )
    # 局部变量 has_wage_arrears：当前问题是否与工资拖欠有关。
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

    # 局部变量 has_complaint_intent：当前问题是否表达投诉或维权意图。
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
