# 文件作用：将明确的普通交互短句与需要劳动权益证据的问题分流。
"""Deterministic, fail-closed routing for a small set of ordinary interactions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class InteractionKind(StrEnum):
    """允许直接使用固定模板回复的普通交互类别。"""

    GREETING = "greeting"
    THANKS = "thanks"
    FAREWELL = "farewell"
    IDENTITY = "identity"
    CAPABILITIES = "capabilities"
    DISCLAIMER = "disclaimer"


@dataclass(frozen=True, slots=True)
class InteractionDecision:
    """普通交互的分类结果；kind 为空时必须走原有 RAG 流程。"""

    kind: InteractionKind | None
    reason: str

    @property
    def is_direct(self) -> bool:
        """判断本次输入是否命中受控的直接回复白名单。"""

        return self.kind is not None


_MAX_DIRECT_INTERACTION_LENGTH = 40
_EDGE_PUNCTUATION = " \t\r\n。！？!?，,.~～、；;：:\"'“”‘’"
_PATTERNS: dict[InteractionKind, tuple[str, ...]] = {
    InteractionKind.GREETING: (
        r"你好",
        r"您好",
        r"嗨",
        r"在吗",
        r"早上好",
        r"下午好",
        r"晚上好",
    ),
    InteractionKind.THANKS: (
        r"谢谢",
        r"谢谢你",
        r"感谢",
        r"感谢你的帮助",
        r"多谢",
    ),
    InteractionKind.FAREWELL: (r"再见", r"拜拜", r"下次见"),
    InteractionKind.IDENTITY: (
        r"你是谁",
        r"介绍一下你自己",
        r"请介绍一下你自己",
        r"你叫什么",
        r"你叫什么名字",
    ),
    InteractionKind.CAPABILITIES: (
        r"你能做什么",
        r"你有哪些功能",
        r"支持哪些功能",
        r"你能帮我什么",
        r"怎么使用你",
    ),
    InteractionKind.DISCLAIMER: (r"你是律师吗", r"你的回答是法律意见吗"),
}


def classify_interaction(question: str) -> InteractionDecision:
    """仅将完整匹配的短交互句放行，其余输入均保留原 RAG 路径。"""

    normalized = unicodedata.normalize("NFKC", question).strip()
    normalized = normalized.strip(_EDGE_PUNCTUATION)
    if not normalized or len(normalized) > _MAX_DIRECT_INTERACTION_LENGTH:
        return InteractionDecision(None, "empty_or_long")

    for kind, patterns in _PATTERNS.items():
        if any(re.fullmatch(pattern, normalized) for pattern in patterns):
            return InteractionDecision(kind, "exact_safe_phrase")

    return InteractionDecision(None, "not_whitelisted")
