"""Product-level AI mode profiles used by chat and offline RAG jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.contracts import AiMode

ReasoningEffort = Literal["low", "high", "max"]


@dataclass(frozen=True)
class AiModeProfile:
    evidence_thinking: bool
    evidence_effort: ReasoningEffort | None
    answer_thinking: bool
    answer_effort: ReasoningEffort | None


DEFAULT_AI_MODE = AiMode.BALANCED

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
    """Return a validated immutable profile for a request or saved snapshot."""

    return AI_MODE_PROFILES[AiMode(mode)]

