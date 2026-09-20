"""LangChain orchestration for the application's evidence-first RAG flow."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableBranch, RunnableLambda

from app.core.config import Settings, settings
from app.rag.decision import AnswerMode, EvidenceDecision, EvidenceJudgement
from app.rag.errors import ModelUnavailableError
from app.schemas.contracts import AnswerStyle, ToolExecutionItem
from app.services.compliance import COMPLIANCE_NOTICE
from app.services.missing_knowledge import MissingKnowledgeReason
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)
_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

REFUSAL_TEXT = (
    "目前知识库中的资料不足以支持对这个问题作出可靠判断。请补充相关资料后再咨询。"
)
ANSWER_STYLE_INSTRUCTIONS = {
    "plain": "面向普通劳动者，少用专业术语，先给简明结论，再解释依据和可执行建议。",
    "legal": "使用严谨、客观的表达，明确说明证据支持的适用条件、事实前提与结论边界。",
}


class RagChain:
    """Shared LangChain retriever, prompt and model orchestration.

    The existing retrieval service remains responsible for the project's FAISS
    index, SQLite metadata and reranking rules. This class makes the production
    query path use LangChain's Retriever, Prompt and Runnable interfaces while
    preserving the application's API and persistence contracts.
    """

    def __init__(
        self,
        retrieval_service: RetrievalService,
        chat_model: BaseChatModel,
        config: Settings = settings,
        *,
        evidence_model: BaseChatModel | None = None,
        answer_model: BaseChatModel | None = None,
    ) -> None:
        self.retrieval_service = retrieval_service
        self.retriever = getattr(retrieval_service, "retriever", None)
        self.chat_model = chat_model
        self.config = config
        self.answer_prompt = _answer_prompt()
        self.rewrite_prompt = _rewrite_prompt()
        self.evidence_prompt = _evidence_prompt()
        self._set_chat_models(
            chat_model,
            evidence_model=evidence_model,
            answer_model=answer_model,
        )

    def _set_chat_models(
        self,
        model: BaseChatModel,
        *,
        evidence_model: BaseChatModel | None = None,
        answer_model: BaseChatModel | None = None,
    ) -> None:
        self.chat_model = model
        self.evidence_model = evidence_model or model
        self.answer_model = answer_model or model
        self.rewrite_chain = self.rewrite_prompt | self.chat_model | StrOutputParser()
        self.evidence_chain = (
            self.evidence_prompt
            | self.evidence_model.bind(response_format={"type": "json_object"})
            | JsonOutputParser()
        )
        self.answer_chain = RunnableBranch(
            (lambda state: bool(state["refused"]), RunnableLambda(_refusal)),
            self.answer_prompt | self.answer_model | StrOutputParser(),
        )

    def set_chat_model(self, model: BaseChatModel) -> None:
        """Bind all RAG roles to one model for compatibility and tests."""

        self._set_chat_models(model, evidence_model=model, answer_model=model)

    async def rewrite_question(
        self, history: list[dict[str, Any]], question: str
    ) -> str:
        if not history:
            return question
        context = [
            {"role": item["role"], "content": item["content"]} for item in history
        ]
        try:
            result = await self.rewrite_chain.ainvoke(
                {
                    "context": json.dumps(
                        {"history": context, "current_question": question},
                        ensure_ascii=False,
                    )
                }
            )
            result = result.strip().strip('"“”')
            return result if result else question
        except Exception:
            logger.warning(
                "Question rewrite failed; using the original question", exc_info=True
            )
            return question

    async def retrieve(self, question: str) -> list[dict[str, Any]]:
        if self.retriever is not None:
            documents = await self.retriever.ainvoke(question)
            return [_evidence_from_document(document) for document in documents]
        aretrieve = getattr(self.retrieval_service, "aretrieve", None)
        if callable(aretrieve):
            return await aretrieve(question)
        evidence = await asyncio.to_thread(self.retrieval_service.retrieve, question)
        return [dict(item) for item in evidence]

    async def judge_evidence(
        self,
        question: str,
        evidence: list[dict[str, Any]],
        *,
        strict: bool = False,
    ) -> tuple[EvidenceJudgement, MissingKnowledgeReason | None]:
        if not evidence:
            return (
                _evidence_judgement(
                    EvidenceDecision.INSUFFICIENT,
                    "没有召回到可用于判断的知识库片段。",
                    missing_points=["问题对应的知识库证据"],
                ),
                MissingKnowledgeReason.NO_RETRIEVAL_RESULT,
            )
        max_retrieval_score = max(
            float(item.get("retrieval_score", item.get("score", 0.0)))
            for item in evidence
        )
        if (
            max_retrieval_score
            < self.config.rag_score_threshold
        ):
            return (
                _evidence_judgement(
                    EvidenceDecision.INSUFFICIENT,
                    (
                        f"候选片段最高检索分 {max_retrieval_score:.3f} 低于阈值 "
                        f"{self.config.rag_score_threshold:.3f}。"
                    ),
                    missing_points=["与问题足够相关的知识库证据"],
                ),
                MissingKnowledgeReason.LOW_RELEVANCE,
            )

        try:
            judgement = await self.evidence_chain.ainvoke(
                {
                    "payload": json.dumps(
                        {"question": question, "evidence": evidence},
                        ensure_ascii=False,
                    ),
                }
            )
            parsed = _parse_evidence_judgement(judgement)
            if parsed.decision is EvidenceDecision.INSUFFICIENT:
                return parsed, MissingKnowledgeReason.INSUFFICIENT_EVIDENCE
            return parsed, None
        except ModelUnavailableError:
            if strict:
                raise
            logger.warning("Evidence judge failed; failing closed", exc_info=True)
            return _failed_closed_judgement(), None
        except OutputParserException as exc:
            if strict:
                raise ModelUnavailableError("模型服务暂不可用") from exc
            logger.warning("Evidence judge returned invalid JSON", exc_info=True)
            return _failed_closed_judgement(), None
        except Exception as exc:
            if strict:
                raise ModelUnavailableError("模型服务暂不可用") from exc
            logger.warning("Evidence judge failed; failing closed", exc_info=True)
            return _failed_closed_judgement(), None

    def answer_prompt_value(
        self,
        answer_style: AnswerStyle,
        question: str,
        rewritten_question: str,
        evidence: list[dict[str, Any]],
        compliance_required: bool,
        tool_execution: ToolExecutionItem | None,
        *,
        answer_mode: AnswerMode = AnswerMode.RAG_ANSWER,
        supported_points: Sequence[str] = (),
        missing_points: Sequence[str] = (),
    ) -> Any:
        compliance_instruction = (
            f"回答末尾必须逐字包含以下提示：{COMPLIANCE_NOTICE}"
            if compliance_required
            else "当前规则未要求固定提示；回答仍须遵守证据约束。"
        )
        return self.answer_prompt.invoke(
            {
                "answer_style": answer_style.value,
                "answer_style_instructions": ANSWER_STYLE_INSTRUCTIONS[
                    answer_style.value
                ],
                "answer_mode": answer_mode.value,
                "supported_points": _format_points(supported_points),
                "missing_points": _format_points(missing_points),
                "compliance_required": str(compliance_required).lower(),
                "compliance_instruction": compliance_instruction,
                "tool_result": _tool_result(tool_execution),
                "question": question,
                "rewritten_question": rewritten_question,
                "context": _format_evidence(evidence),
            }
        )

    async def complete_answer(
        self,
        answer_style: AnswerStyle,
        question: str,
        rewritten_question: str,
        evidence: list[dict[str, Any]],
        compliance_required: bool,
        tool_execution: ToolExecutionItem | None,
        *,
        answer_mode: AnswerMode = AnswerMode.RAG_ANSWER,
        supported_points: Sequence[str] = (),
        missing_points: Sequence[str] = (),
        refused: bool | None = None,
    ) -> str:
        try:
            return (
                await self.answer_chain.ainvoke(
                    _answer_input(
                        answer_style,
                        question,
                        rewritten_question,
                        evidence,
                        compliance_required,
                        tool_execution,
                        answer_mode=answer_mode,
                        supported_points=supported_points,
                        missing_points=missing_points,
                        refused=refused,
                    )
                )
            ).strip()
        except ModelUnavailableError:
            raise
        except Exception as exc:
            raise ModelUnavailableError("模型服务暂不可用") from exc

    async def stream_answer(
        self,
        answer_style: AnswerStyle,
        question: str,
        rewritten_question: str,
        evidence: list[dict[str, Any]],
        compliance_required: bool,
        tool_execution: ToolExecutionItem | None,
        *,
        answer_mode: AnswerMode = AnswerMode.RAG_ANSWER,
        supported_points: Sequence[str] = (),
        missing_points: Sequence[str] = (),
        refused: bool | None = None,
    ) -> AsyncIterator[str]:
        async for token in self.answer_chain.astream(
            _answer_input(
                answer_style,
                question,
                rewritten_question,
                evidence,
                compliance_required,
                tool_execution,
                answer_mode=answer_mode,
                supported_points=supported_points,
                missing_points=missing_points,
                refused=refused,
            )
        ):
            if token:
                yield token

    def answer_messages(
        self,
        answer_style: AnswerStyle,
        question: str,
        rewritten_question: str,
        evidence: list[dict[str, Any]],
        compliance_required: bool,
        tool_execution: ToolExecutionItem | None,
        *,
        answer_mode: AnswerMode = AnswerMode.RAG_ANSWER,
        supported_points: Sequence[str] = (),
        missing_points: Sequence[str] = (),
    ) -> list[dict[str, str]]:
        """Return rendered messages for compatibility with diagnostics and tests."""

        prompt_value = self.answer_prompt_value(
            answer_style,
            question,
            rewritten_question,
            evidence,
            compliance_required,
            tool_execution,
            answer_mode=answer_mode,
            supported_points=supported_points,
            missing_points=missing_points,
        )
        return _message_dicts(prompt_value.messages)


def _evidence_from_document(document: Document) -> dict[str, Any]:
    metadata = document.metadata
    return {
        "chunk_id": int(metadata["chunk_id"]),
        "document_id": int(metadata["document_id"]),
        "file_name": str(metadata["file_name"]),
        "chunk_no": int(metadata["chunk_no"]),
        "content": document.page_content,
        "score": float(metadata["score"]),
        "retrieval_score": float(metadata["retrieval_score"]),
        "rerank_score": metadata.get("rerank_score"),
        "lexical_score": float(metadata.get("lexical_score", 0.0)),
        "source_score": float(metadata.get("source_score", 0.0)),
        "lightweight_score": float(
            metadata.get("lightweight_score", metadata["retrieval_score"])
        ),
        "evidence_origin": str(metadata.get("evidence_origin", "RETRIEVED")),
        "rank_no": int(metadata["rank_no"]),
    }


def _answer_prompt() -> ChatPromptTemplate:
    template = (_PROMPT_DIR / "answer_system.txt").read_text(encoding="utf-8")
    for name in (
        "answer_style",
        "answer_style_instructions",
        "answer_mode",
        "supported_points",
        "missing_points",
        "compliance_required",
        "compliance_instruction",
        "tool_result",
        "question",
        "rewritten_question",
        "context",
    ):
        template = template.replace(f"{{{{{name}}}}}", f"{{{name}}}")
    return ChatPromptTemplate.from_messages(
        [
            ("system", template),
            ("human", "请根据上述约束与证据回答问题。"),
        ],
        template_format="f-string",
    )


def _rewrite_prompt() -> ChatPromptTemplate:
    template = _escape_fstring_literals(
        (_PROMPT_DIR / "question_rewrite.txt").read_text(encoding="utf-8")
    )
    return ChatPromptTemplate.from_messages(
        [
            ("system", template),
            ("human", "对话上下文与当前问题：\n{context}"),
        ],
        template_format="f-string",
    )


def _evidence_prompt() -> ChatPromptTemplate:
    template = _escape_fstring_literals(
        (_PROMPT_DIR / "evidence_judge.txt").read_text(encoding="utf-8")
    )
    return ChatPromptTemplate.from_messages(
        [
            ("system", template),
            ("human", "{payload}"),
        ],
        template_format="f-string",
    )


def _escape_fstring_literals(value: str) -> str:
    return value.replace("{", "{{").replace("}", "}}")


def _format_evidence(evidence: Sequence[Mapping[str, Any]]) -> str:
    return "\n\n".join(
        f"[Evidence {item['rank_no']}] {item['content']}" for item in evidence
    )


def _tool_result(tool_execution: ToolExecutionItem | None) -> str:
    if tool_execution is None:
        return "无"
    return json.dumps(tool_execution.output.model_dump(mode="json"), ensure_ascii=False)


def _format_points(points: Sequence[str]) -> str:
    return json.dumps(list(points), ensure_ascii=False)


def _evidence_judgement(
    decision: EvidenceDecision,
    reason: str,
    *,
    supported_points: Sequence[str] = (),
    missing_points: Sequence[str] = (),
) -> EvidenceJudgement:
    return EvidenceJudgement(
        decision=decision,
        reason=reason,
        supported_points=list(supported_points),
        missing_points=list(missing_points),
    )


def _failed_closed_judgement() -> EvidenceJudgement:
    return _evidence_judgement(
        EvidenceDecision.INSUFFICIENT,
        "证据充分性判断未成功，无法安全确认回答范围。",
        missing_points=["证据充分性判断"],
    )


def _parse_evidence_judgement(value: Any) -> EvidenceJudgement:
    if not isinstance(value, dict):
        raise TypeError("evidence judgement must be a JSON object")
    # Accept the pre-enum shape for old configured providers while all new
    # prompts and returned values use EvidenceJudgement.
    if "decision" not in value and isinstance(value.get("sufficient"), bool):
        value = {
            "decision": (
                EvidenceDecision.SUFFICIENT.value
                if value["sufficient"]
                else EvidenceDecision.INSUFFICIENT.value
            ),
            "reason": str(value.get("reason") or "未提供判断理由"),
            "supported_points": [],
            "missing_points": [],
        }
    return EvidenceJudgement.model_validate(value)


def _message_dicts(messages: Sequence[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in messages:
        role = {"human": "user", "ai": "assistant"}.get(message.type, message.type)
        content = message.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        result.append({"role": role, "content": content})
    return result


def _answer_input(
    answer_style: AnswerStyle,
    question: str,
    rewritten_question: str,
    evidence: Sequence[Mapping[str, Any]],
    compliance_required: bool,
    tool_execution: ToolExecutionItem | None,
    *,
    answer_mode: AnswerMode,
    supported_points: Sequence[str],
    missing_points: Sequence[str],
    refused: bool | None,
) -> dict[str, Any]:
    return {
        "refused": (
            answer_mode is AnswerMode.REFUSAL if refused is None else refused
        ),
        "answer_style": answer_style.value,
        "answer_style_instructions": ANSWER_STYLE_INSTRUCTIONS[answer_style.value],
        "answer_mode": answer_mode.value,
        "supported_points": _format_points(supported_points),
        "missing_points": _format_points(missing_points),
        "compliance_required": str(compliance_required).lower(),
        "compliance_instruction": (
            f"回答末尾必须逐字包含以下提示：{COMPLIANCE_NOTICE}"
            if compliance_required
            else "当前规则未要求固定提示；回答仍须遵守证据约束。"
        ),
        "tool_result": _tool_result(tool_execution),
        "question": question,
        "rewritten_question": rewritten_question,
        "context": _format_evidence(evidence),
    }


def _refusal(_state: Mapping[str, Any]) -> str:
    return REFUSAL_TEXT
