"""定义聊天和离线 RAG 作业共用的 AI 模式推理参数配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.contracts import AiMode

# 推理强度允许的字面值，与模型服务的参数约定保持一致。
ReasoningEffort = Literal["low", "high", "max"]


@dataclass(frozen=True)
class AiModeProfile:
    """描述 AI 模式在证据判断和答案生成阶段的推理设置。"""

    # 是否在证据判断阶段启用模型推理。
    evidence_thinking: bool
    # 证据判断阶段的推理强度；未启用推理时为空。
    evidence_effort: ReasoningEffort | None
    # 是否在答案生成阶段启用模型推理。
    answer_thinking: bool
    # 答案生成阶段的推理强度；未启用推理时为空。
    answer_effort: ReasoningEffort | None


# 未显式指定模式时采用的默认 AI 模式。
DEFAULT_AI_MODE = AiMode.BALANCED

# 各产品模式对应的证据判断和答案生成参数快照。
AI_MODE_PROFILES: dict[AiMode, AiModeProfile] = {
    AiMode.SPEED: AiModeProfile(
        evidence_thinking=False,
        evidence_effort=None,
        answer_thinking=False,
        answer_effort=None,
    ),
    AiMode.BALANCED: AiModeProfile(
        evidence_thinking=True,
        evidence_effort="low",
        answer_thinking=False,
        answer_effort=None,
    ),
    AiMode.QUALITY: AiModeProfile(
        evidence_thinking=True,
        evidence_effort="high",
        answer_thinking=True,
        answer_effort="high",
    ),
}


def ai_mode_profile(mode: AiMode | str) -> AiModeProfile:
    """校验模式值并返回对应的不可变推理参数配置。

    参数：mode 为 AI 模式枚举值或其字符串表示。
    返回：该模式对应的推理配置；无效模式会在枚举转换时抛出异常。
    """

    return AI_MODE_PROFILES[AiMode(mode)]
