# 文件作用：集中维护普通交互的固定回复文案，避免调用生成模型。
"""Stable, evidence-free response templates for whitelisted interactions."""

from collections.abc import Mapping
from types import MappingProxyType

from app.services.interaction_router import InteractionKind

INTERACTION_REPLY_VERSION = "interaction_v1"

RESPONSES: Mapping[InteractionKind, str] = MappingProxyType(
    {
        InteractionKind.GREETING: (
            "你好，我是 LaborQA 劳动权益咨询助手。你可以向我咨询劳动合同、工资、"
            "加班、社保、工伤和劳动争议等相关问题。"
        ),
        InteractionKind.THANKS: "不客气。如果还有劳动权益方面的问题，可以继续问我。",
        InteractionKind.FAREWELL: "再见。如有劳动权益相关疑问，欢迎再来咨询。",
        InteractionKind.IDENTITY: (
            "我是 LaborQA，一款面向劳动权益咨询的问答助手。我会根据系统已导入的资料"
            "检索可核对的依据；资料不足时会说明限制。我提供的是信息辅助，不替代律师、"
            "仲裁机构或行政机关的正式意见。"
        ),
        InteractionKind.CAPABILITIES: (
            "我可以协助查阅系统已导入的劳动合同、工资支付、加班、社会保险、工伤和劳动"
            "争议相关资料，也支持多轮追问、查看引用来源、生成咨询材料清单，并在依据不足"
            "时说明限制。"
        ),
        InteractionKind.DISCLAIMER: (
            "我不是律师，也不能代替律师、仲裁机构或行政机关提供正式意见。我会依据系统已"
            "导入的资料提供劳动权益信息，供你核对。"
        ),
    }
)


def render_interaction_response(kind: InteractionKind) -> str:
    """返回对应的固定回复模板。"""

    return RESPONSES[kind]
