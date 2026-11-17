# 文件作用：定义检索、证据判断和生成流程中使用的决策数据结构。
"""定义 RAG 证据充分性判断和回答路径选择使用的数据类型。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class EvidenceDecision(StrEnum):
    """证据判断阶段可返回的充分性分类。"""

    SUFFICIENT = "SUFFICIENT"  # 现有证据足以支持回答用户问题。
    PARTIAL = "PARTIAL"  # 证据仅能支持问题的部分内容。
    INSUFFICIENT = "INSUFFICIENT"  # 证据不足以给出可靠回答。


class AnswerMode(StrEnum):
    """聊天流程最终采用的回答或拒答路径。"""

    RAG_ANSWER = "RAG_ANSWER"  # 基于检索证据生成常规 RAG 回答。
    PARTIAL_ANSWER = "PARTIAL_ANSWER"  # 仅回答证据能够支持的部分。
    TOOL_ONLY = "TOOL_ONLY"  # 直接返回结构化工具生成的结果。
    REFUSAL = "REFUSAL"  # 因证据不足或合规规则拒绝回答。


class EvidenceJudgement(BaseModel):
    """保存证据充分性判断及其支持、缺失要点。"""

    decision: EvidenceDecision  # 本次判断的证据充分性分类。
    reason: str  # 说明为何选择该分类的理由。
    supported_points: list[str] = Field(default_factory=list)  # 当前证据已支持的要点。
    missing_points: list[str] = Field(default_factory=list)  # 当前证据尚未支持的要点。
