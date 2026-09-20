"""Internal evidence and answer-mode models for the RAG decision path."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class EvidenceDecision(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class AnswerMode(StrEnum):
    RAG_ANSWER = "RAG_ANSWER"
    PARTIAL_ANSWER = "PARTIAL_ANSWER"
    TOOL_ONLY = "TOOL_ONLY"
    REFUSAL = "REFUSAL"


class EvidenceJudgement(BaseModel):
    decision: EvidenceDecision
    reason: str
    supported_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
