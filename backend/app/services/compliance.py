# 文件作用：执行回答合规性检查并识别需要拒答的内容。
"""根据问题主题和计划回答内容判断是否需要附加劳动权益合规提示。"""

from __future__ import annotations

import re

# 涉及办理流程或期限时需要向用户展示的通用合规提示。
COMPLIANCE_NOTICE = "具体办理要求可能因地区和实际情况不同，请咨询当地劳动监察部门、劳动争议仲裁机构或法律援助机构。"


class ComplianceRuleService:
    """识别需要提醒用户向当地主管机构核实的咨询场景。"""

    # 用户问题或改写问题中用于识别流程、期限和投诉咨询的关键词。
    _QUESTION_TERMS = (
        "仲裁时效",
        "仲裁期限",
        "申请仲裁的期限",
        "申请仲裁",
        "申请期限",
        "劳动监察",
        "劳动保障监察",
        "劳动仲裁",
        "仲裁机构",
        "仲裁委员会",
        "法律援助",
        "怎么投诉",
        "如何投诉",
        "去哪投诉",
        "维权流程",
        "维权路径",
        "办理流程",
        "办理路径",
    )
    # 计划回答中表示具体办理渠道或机构的关键词。
    _ANSWER_PATH_TERMS = (
        "劳动监察部门",
        "劳动保障监察部门",
        "劳动保障监察大队",
        "劳动争议仲裁机构",
        "劳动争议仲裁委员会",
        "法律援助机构",
        "法律援助中心",
        "申请仲裁",
        "提交仲裁申请",
        "投诉至",
        "向劳动监察",
        "到劳动监察",
        "12333",
    )
    # 匹配数字或中文数字形式的具体办理期限。
    _CONCRETE_DEADLINE = re.compile(
        r"(?:\d+|[零〇一二三四五六七八九十百]+)\s*"
        r"(?:个?工作日|日|天|个月|年)\s*(?:内|以内|期限|时效)"
    )
    # 匹配“时效/期限”后紧跟具体时间长度的文本模式。
    _DEADLINE_WITH_LEADING_TERM = re.compile(
        r"(?:时效|期限).{0,10}(?:\d+|[零〇一二三四五六七八九十百]+)\s*"
        r"(?:个?工作日|日|天|个月|年)"
    )

    def requires_notice(
        self,
        question: str,
        rewritten_question: str = "",
        final_plan_info: str = "",
    ) -> bool:
        """判断输入问题或回答计划是否需要附加合规提示。

        参数：question 为原始问题；rewritten_question 为改写问题；
        final_plan_info 为回答计划中的机构、路径和期限信息。
        返回：命中办理路径或具体期限规则时为 true。
        """
        # 合并原始问题与改写问题，统一检查用户实际诉求。
        query = f"{question}\n{rewritten_question}"
        # 转为字符串后检查回答计划中提到的具体办理渠道和时限。
        plan = f"{final_plan_info}"
        # 局部变量 term：当前用于关键词匹配的单个词项。
        if any(term in query for term in self._QUESTION_TERMS):
            return True
        if any(term in plan for term in self._ANSWER_PATH_TERMS):
            return True
        return bool(
            self._CONCRETE_DEADLINE.search(plan)
            or self._DEADLINE_WITH_LEADING_TERM.search(plan)
        )
