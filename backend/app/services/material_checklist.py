# 文件作用：根据劳动争议场景整理办理事项及所需材料清单。
"""根据劳动争议类型生成通用材料清单，并封装为可审计的工具结果。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.schemas.contracts import (
    MaterialChecklistInput,
    MaterialChecklistOutput,
    ToolExecutionItem,
)

# 注册给聊天流程调用的工具名称。
_TOOL_NAME = "generate_rights_material_checklist"
# 随工具结果返回的用途限制提示。
_TOOL_NOTE = "材料清单仅用于信息整理，具体以实际争议和受理机构要求为准。"

# 常见劳动争议类型对应的通用材料建议。
_CHECKLISTS: dict[str, list[str]] = {
    "欠薪": [
        "劳动合同或能够证明劳动关系的材料",
        "工资条、银行流水等工资支付记录",
        "考勤、排班或工作记录",
        "与用人单位沟通欠薪问题的记录",
    ],
    "未签劳动合同": [
        "能够证明实际用工关系的材料",
        "工资条、银行流水等工资支付记录",
        "考勤、排班或工作记录",
        "与用人单位沟通劳动合同问题的记录",
    ],
    "解除劳动关系": [
        "劳动合同或能够证明劳动关系的材料",
        "解除通知、离职证明或相关沟通记录",
        "考勤、工资及工作表现记录",
        "与解除理由相关的书面材料或沟通记录",
    ],
}


@tool(
    _TOOL_NAME,
    description="根据劳动争议类型整理通用材料清单，不作法律结论。",
    args_schema=MaterialChecklistInput,
)
def _generate_rights_material_checklist(
    dispute_type: str, description: str
) -> dict[str, Any]:
    """依据劳动争议类型返回通用证据材料建议，不作法律结论。

    参数：dispute_type 为识别出的争议类型；description 为补充案情。
    返回：包含材料列表和使用说明的工具结果字典。
    """
    # 优先取对应争议类型的清单，没有匹配类型时使用通用材料建议。
    materials = _CHECKLISTS.get(
        dispute_type,
        [
            "劳动合同或能够证明劳动关系的材料",
            "工资、考勤或工作记录",
            "与争议事项相关的通知、书面材料和沟通记录",
        ],
    )
    return {"materials": materials, "note": _TOOL_NOTE}


class ToolDecisionService:
    """识别用户是否在询问劳动争议办理材料或证据清单。"""

    # 表示用户希望获取材料或证据准备建议的词语集合。
    _MATERIAL_REQUESTS = (
        "什么材料",
        "哪些材料",
        "材料有哪些",
        "需要准备材料",
        "应该准备材料",
        "材料清单",
        "什么证据",
        "哪些证据",
        "证据有哪些",
        "需要什么证据",
        "证据清单",
        "准备证据",
    )

    def decide(
        self, question: str, rewritten_question: str
    ) -> MaterialChecklistInput | None:
        """根据问题文本判断是否应调用材料清单工具。

        参数：question 为原始问题；rewritten_question 为查询改写结果。
        返回：匹配时返回工具输入模型，否则返回 None。
        """
        # 合并原问题和改写问题，避免改写后保留的意图未被识别。
        combined = f"{question}\n{rewritten_question}"
        # 局部变量 term：当前用于关键词匹配的单个词项。
        if not any(term in combined for term in self._MATERIAL_REQUESTS):
            return None

        if any(term in combined for term in ("欠薪", "拖欠工资", "工资未发")):
            # 将薪资拖欠类表达归并为工具支持的争议类别。
            dispute_type = "欠薪"
        elif any(term in combined for term in ("未签劳动合同", "没有签劳动合同")):
            # 将未订立书面劳动合同的表达归并为对应类别。
            dispute_type = "未签劳动合同"
        elif any(term in combined for term in ("辞退", "开除", "解除劳动关系")):
            # 将辞退或解除关系类表达归并为对应类别。
            dispute_type = "解除劳动关系"
        else:
            # 未命中细分类型时采用通用劳动争议类别。
            dispute_type = "劳动争议"

        return MaterialChecklistInput(
            dispute_type=dispute_type,
            description=rewritten_question.strip(),
        )


class MaterialChecklistTool:
    """调用清单工具并将输入、输出封装为标准工具执行记录。"""

    def execute(self, payload: MaterialChecklistInput) -> ToolExecutionItem:
        """执行材料清单生成。

        参数：payload 为已校验的争议类型和补充说明。
        返回：包含工具名、输入和结构化输出的执行记录。
        """
        # output 是工具返回的原始字典，随后转换为 Pydantic 输出模型。
        output = _generate_rights_material_checklist.invoke(payload.model_dump())
        return ToolExecutionItem(
            tool_name=_TOOL_NAME,
            input=payload,
            output=MaterialChecklistOutput.model_validate(output),
        )


def render_material_checklist(execution: ToolExecutionItem) -> str:
    """将工具执行记录渲染为无需再次调用大模型的完整回答。

    参数：execution 为已完成的材料清单工具执行记录。
    返回：包含材料编号和使用说明的中文回答文本。
    """
    # 按序号拼接工具返回的材料清单。
    materials = "\n\n".join(
        f"{index}. {material}"
        for index, material in enumerate(execution.output.materials, start=1)
    )
    return (
        f"根据你描述的“{execution.input.dispute_type}”情况，可先整理以下材料：\n\n"
        f"{materials}\n\n{execution.output.note}"
    )
