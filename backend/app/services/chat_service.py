"""Session-aware RAG orchestration and Server-Sent Events serialization."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from fastapi import Request
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Settings, settings
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.rag.chains import (
    ANSWER_STYLE_INSTRUCTIONS,
    REFUSAL_TEXT,
    RagChain,
)
from app.rag.ai_modes import AI_MODE_PROFILES, DEFAULT_AI_MODE, ai_mode_profile
from app.rag.decision import AnswerMode, EvidenceDecision, EvidenceJudgement
from app.rag.errors import EmbeddingUnavailableError, ModelUnavailableError
from app.rag.providers import build_chat_model
from app.schemas.contracts import (
    AiMode,
    AnswerStyle,
    ChatRequest,
    MaterialChecklistInput,
    MissingKnowledgeQuery,
    MissingKnowledgeUpdate,
    ToolExecutionItem,
)
from app.services.compliance import COMPLIANCE_NOTICE, ComplianceRuleService
from app.services.material_checklist import (
    MaterialChecklistTool,
    ToolDecisionService,
    render_material_checklist,
)
from app.services.missing_knowledge import (
    ExecutionMode,
    MissingKnowledgeReason,
    MissingKnowledgeService,
)
from app.services.retrieval import RetrievalService
from app.services.vector_store import (
    VectorStoreNotInitialized,
    VectorStorePersistenceError,
    VectorStoreSignatureMismatch,
)

logger = logging.getLogger(__name__)
_REFUSAL = REFUSAL_TEXT
_ANSWER_STYLE_INSTRUCTIONS = ANSWER_STYLE_INSTRUCTIONS


@dataclass(frozen=True)
class PreparedAnswerDecision:
    evidence: list[dict[str, Any]]
    retrieval_ms: int
    evidence_judge_ms: int
    judgement: EvidenceJudgement
    refusal_reason: MissingKnowledgeReason | None
    answer_mode: AnswerMode
    tool_input: MaterialChecklistInput | None


class _UnavailableChatModel(BaseChatModel):
    """Keep startup lazy when the existing LLM configuration is incomplete."""

    @property
    def _llm_type(self) -> str:
        return "unavailable"

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise ModelUnavailableError("LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 尚未配置")


class ChatService:
    def __init__(
        self,
        session_service: Any,
        retrieval_service: RetrievalService,
        config: Settings = settings,
        *,
        chat_model: BaseChatModel | None = None,
        chat_models: Mapping[str, BaseChatModel] | None = None,
        missing_knowledge_service: MissingKnowledgeService | None = None,
    ) -> None:
        self.session_service = session_service
        self.retrieval_service = retrieval_service
        self.config = config
        if chat_models is not None:
            self.chat_models = dict(chat_models)
            self.chat_model = self.chat_models["default"]
        elif chat_model is not None:
            self.chat_model = chat_model
            self.chat_models = {
                "default": chat_model,
                "off": chat_model,
                "low": chat_model,
                "high": chat_model,
            }
        elif all((config.llm_api_key, config.llm_base_url, config.llm_model)):
            high_model = build_chat_model(
                config, thinking_mode="enabled", reasoning_effort="high"
            )
            self.chat_model = high_model
            self.chat_models = {
                "default": high_model,
                "off": build_chat_model(config, thinking_mode="disabled"),
                "low": build_chat_model(
                    config, thinking_mode="enabled", reasoning_effort="low"
                ),
                "high": high_model,
            }
        else:
            unavailable = _UnavailableChatModel()
            self.chat_model = unavailable
            self.chat_models = {
                "default": unavailable,
                "off": unavailable,
                "low": unavailable,
                "high": unavailable,
            }
        self.evidence_chat_model = self.chat_models["low"]
        self.answer_chat_model = self.chat_models["off"]
        self.rag_chains = {
            mode: self._build_rag_chain(retrieval_service, mode)
            for mode in AI_MODE_PROFILES
        }
        # Keep the historical attribute for callers/tests that customize the
        # balanced chain with a fake model.
        self.rag_chain = self.rag_chains[DEFAULT_AI_MODE]
        self.tool_decision_service = ToolDecisionService()
        self.material_checklist_tool = MaterialChecklistTool()
        self.compliance_rule_service = ComplianceRuleService()
        self.missing_knowledge_service = missing_knowledge_service or (
            MissingKnowledgeService(database_path=config.database_path)
        )

    def _build_rag_chain(
        self, retrieval_service: RetrievalService, mode: AiMode
    ) -> RagChain:
        profile = ai_mode_profile(mode)
        return RagChain(
            retrieval_service,
            self.chat_models["default"],
            self.config,
            evidence_model=self.chat_models[
                self._model_key(profile.evidence_thinking, profile.evidence_effort)
            ],
            answer_model=self.chat_models[
                self._model_key(profile.answer_thinking, profile.answer_effort)
            ],
        )

    @staticmethod
    def _model_key(
        thinking_enabled: bool, reasoning_effort: str | None
    ) -> str:
        if not thinking_enabled:
            return "off"
        return reasoning_effort or "high"

    def _rag_chain_for(self, ai_mode: AiMode | str) -> RagChain:
        return self.rag_chains[AiMode(ai_mode)]

    def create_session(self, title: str | None) -> dict[str, Any]:
        return self.session_service.create_session(title)

    def get_session(self, session_id: UUID) -> dict[str, Any] | None:
        return self.session_service.get_session(session_id)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.session_service.list_sessions(limit)

    def list_messages(self, session_id: UUID) -> list[dict[str, Any]]:
        return self.session_service.list_messages(session_id)

    def execute_material_checklist(
        self, payload: MaterialChecklistInput
    ) -> ToolExecutionItem:
        return self.material_checklist_tool.execute(payload)

    def list_missing_knowledge(
        self, query: MissingKnowledgeQuery
    ) -> tuple[list[dict[str, Any]], int]:
        return self.missing_knowledge_service.list(query)

    def update_missing_knowledge(
        self, item_id: int, payload: MissingKnowledgeUpdate
    ) -> dict[str, Any]:
        return self.missing_knowledge_service.update(item_id, payload)

    async def stream_chat(
        self,
        payload: ChatRequest,
        request: Request,
        *,
        execution_mode: ExecutionMode = ExecutionMode.PRODUCTION,
    ):
        if execution_mode != ExecutionMode.PRODUCTION:
            raise ValueError("stream_chat only supports PRODUCTION mode")
        request_started = perf_counter()
        session_ms = 0
        rewrite_ms = 0
        retrieval_ms = 0
        evidence_judge_ms = 0
        pre_answer_ms = 0
        ttft_ms: int | None = None
        answer_generation_ms = 0
        persist_ms = 0
        session_id = payload.session_id
        question = payload.question
        ai_mode = AiMode(payload.ai_mode)
        rag_chain = self._rag_chain_for(ai_mode)

        yield _sse(
            "status",
            {
                "stage": "understanding",
                "message": "正在理解问题…",
            },
        )

        session_started = perf_counter()
        try:
            history = self.session_service.get_recent_context(session_id, turns=3)
            user_message = self.session_service.create_user_message(
                session_id, question, ai_mode
            )
            session_ms = round((perf_counter() - session_started) * 1000)
        except AppError as exc:
            yield _sse("error", {"code": int(exc.code), "message": exc.message})
            return
        except Exception:
            logger.exception("Could not read the session or save the user message")
            yield _sse(
                "error",
                {"code": int(ErrorCode.INTERNAL_ERROR), "message": "会话消息保存失败"},
            )
            return

        rewrite_started = perf_counter()
        rewritten_question = await self._rewrite_question(
            history, question, rag_chain=rag_chain
        )
        rewrite_ms = round((perf_counter() - rewrite_started) * 1000)
        try:
            self.session_service.set_rewritten_question(
                int(user_message["id"]), rewritten_question
            )
        except Exception:
            logger.exception("Could not persist the rewritten question")
            yield _sse(
                "error",
                {"code": int(ErrorCode.INTERNAL_ERROR), "message": "问题处理失败"},
            )
            return

        yield _sse(
            "status",
            {
                "stage": "retrieving",
                "message": "正在检索劳动法规…",
            },
        )
        try:
            tool_input = self.tool_decision_service.decide(
                question, rewritten_question
            )
            evidence, retrieval_ms = await self._retrieve_for_decision(
                rewritten_question, rag_chain=rag_chain
            )
            yield _sse(
                "status",
                {
                    "stage": "verifying",
                    "message": "正在核验证据…",
                },
            )
            judgement, refusal_reason, evidence_judge_ms = (
                await self._judge_for_decision(
                    rewritten_question,
                    evidence,
                    strict=False,
                    rag_chain=rag_chain,
                )
            )
            prepared = PreparedAnswerDecision(
                evidence=evidence,
                retrieval_ms=retrieval_ms,
                evidence_judge_ms=evidence_judge_ms,
                judgement=judgement,
                refusal_reason=refusal_reason,
                answer_mode=self._decide_answer_mode(judgement, tool_input),
                tool_input=tool_input,
            )
        except EmbeddingUnavailableError:
            yield _sse(
                "error",
                {
                    "code": int(ErrorCode.EMBEDDING_UNAVAILABLE),
                    "message": "向量服务暂不可用",
                },
            )
            return
        except VectorStoreNotInitialized:
            yield _sse(
                "error",
                {
                    "code": int(ErrorCode.KNOWLEDGE_BASE_NOT_READY),
                    "message": "知识库尚未就绪",
                },
            )
            return
        except VectorStoreSignatureMismatch:
            yield _sse(
                "error",
                {
                    "code": int(ErrorCode.INDEX_EMBEDDING_MISMATCH),
                    "message": "向量索引与当前 Embedding 配置不兼容，请重建索引",
                },
            )
            return
        except VectorStorePersistenceError:
            yield _sse(
                "error",
                {
                    "code": int(ErrorCode.KNOWLEDGE_BASE_NOT_READY),
                    "message": "知识库索引不可用",
                },
            )
            return
        except Exception:
            logger.exception("RAG retrieval failed")
            yield _sse(
                "error",
                {"code": int(ErrorCode.INTERNAL_ERROR), "message": "检索服务内部错误"},
            )
            return

        if await request.is_disconnected():
            return

        evidence = prepared.evidence
        answer_mode = prepared.answer_mode
        refused = answer_mode is AnswerMode.REFUSAL
        refusal_reason = prepared.refusal_reason
        record_missing_knowledge = (
            answer_mode is AnswerMode.REFUSAL
            and refusal_reason is not None
            and execution_mode == ExecutionMode.PRODUCTION
        )

        compliance_required = (
            False
            if answer_mode is AnswerMode.TOOL_ONLY
            else self.compliance_rule_service.requires_notice(
                question, rewritten_question
            )
        )
        answer_parts: list[str] = []
        tool_executions: list[ToolExecutionItem] = []
        if record_missing_knowledge and refusal_reason is not None:
            try:
                self.missing_knowledge_service.record_refusal(
                    rewritten_question, refusal_reason
                )
            except Exception:
                logger.exception("Failed to record a production evidence-gate refusal")

        answer_started = perf_counter()
        pre_answer_ms = round((answer_started - request_started) * 1000)
        first_token_received = False
        yield _sse(
            "status",
            {
                "stage": "generating",
                "message": "正在生成回答…",
            },
        )

        if prepared.tool_input is not None and answer_mode is not AnswerMode.REFUSAL:
            try:
                execution = self.material_checklist_tool.execute(prepared.tool_input)
            except Exception:
                logger.exception("Material checklist tool execution failed")
                yield _sse(
                    "error",
                    {
                        "code": int(ErrorCode.INTERNAL_ERROR),
                        "message": "材料清单生成失败",
                    },
                )
                return
            if await request.is_disconnected():
                return
            tool_executions.append(execution)
            yield _sse("tool", execution.model_dump(mode="json"))

        if answer_mode is AnswerMode.TOOL_ONLY:
            if not tool_executions:
                yield _sse(
                    "error",
                    {
                        "code": int(ErrorCode.INTERNAL_ERROR),
                        "message": "材料清单生成失败",
                    },
                )
                return
            answer_parts.append(render_material_checklist(tool_executions[0]))
            ttft_ms = round((perf_counter() - request_started) * 1000)
            first_token_received = True
            yield _sse("token", {"content": answer_parts[0]})
        else:
            try:
                async for token in rag_chain.stream_answer(
                    payload.answer_style,
                    question,
                    rewritten_question,
                    evidence,
                    compliance_required,
                    tool_executions[0] if tool_executions else None,
                    answer_mode=answer_mode,
                    supported_points=prepared.judgement.supported_points,
                    missing_points=prepared.judgement.missing_points,
                    refused=refused,
                ):
                    if await request.is_disconnected():
                        return
                    if not first_token_received:
                        ttft_ms = round((perf_counter() - request_started) * 1000)
                        first_token_received = True
                    answer_parts.append(token)
                    yield _sse("token", {"content": token})
            except ModelUnavailableError:
                yield _sse(
                    "error",
                    {
                        "code": int(ErrorCode.MODEL_UNAVAILABLE),
                        "message": "模型服务暂不可用",
                    },
                )
                return
            except Exception:
                logger.exception("LLM response stream failed")
                yield _sse(
                    "error",
                    {
                        "code": int(ErrorCode.MODEL_UNAVAILABLE),
                        "message": "模型服务暂不可用",
                    },
                )
                return

        if not answer_parts:
            yield _sse(
                "error",
                {
                    "code": int(ErrorCode.MODEL_UNAVAILABLE),
                    "message": "模型未返回回答内容",
                },
            )
            return

        if answer_mode is not AnswerMode.TOOL_ONLY:
            completed_answer = "".join(answer_parts)
            compliance_required = compliance_required or (
                self.compliance_rule_service.requires_notice(
                    question, rewritten_question, completed_answer
                )
            )
            if compliance_required and COMPLIANCE_NOTICE not in completed_answer:
                notice = f"\n\n{COMPLIANCE_NOTICE}"
                answer_parts.append(notice)
                if not first_token_received:
                    ttft_ms = round((perf_counter() - request_started) * 1000)
                    first_token_received = True
                yield _sse("token", {"content": notice})
        answer_generation_ms = round((perf_counter() - answer_started) * 1000)
        if await request.is_disconnected():
            return

        citations = (
            []
            if answer_mode in (AnswerMode.REFUSAL, AnswerMode.TOOL_ONLY)
            else evidence
        )
        yield _sse("sources", {"items": citations})
        if await request.is_disconnected():
            return

        persist_started = perf_counter()
        try:
            assistant_message = self.session_service.persist_assistant(
                session_id=session_id,
                content="".join(answer_parts),
                answer_style=payload.answer_style.value,
                refused=refused,
                citations=citations,
                tool_executions=tool_executions,
                ai_mode=ai_mode,
            )
            persist_ms = round((perf_counter() - persist_started) * 1000)
        except Exception:
            logger.exception("Could not persist completed assistant response")
            yield _sse(
                "error",
                {"code": int(ErrorCode.INTERNAL_ERROR), "message": "回答保存失败"},
            )
            return

        total_ms = round((perf_counter() - request_started) * 1000)
        latency = {
            "session_ms": session_ms,
            "rewrite_ms": rewrite_ms,
            "retrieval_ms": retrieval_ms,
            "evidence_judge_ms": evidence_judge_ms,
            "pre_answer_ms": pre_answer_ms,
            "ttft_ms": ttft_ms,
            "answer_generation_ms": answer_generation_ms,
            "persist_ms": persist_ms,
            "total_ms": total_ms,
        }
        logger.info(
            "chat_latency %s",
            json.dumps(
                {
                    "session_id": str(session_id),
                    "history_turns": len(history),
                    "answer_mode": answer_mode.value,
                    "evidence_count": len(evidence),
                    "ai_mode": ai_mode.value,
                    "evidence_thinking": ai_mode_profile(ai_mode).evidence_thinking,
                    "evidence_effort": ai_mode_profile(ai_mode).evidence_effort,
                    "answer_thinking": ai_mode_profile(ai_mode).answer_thinking,
                    "answer_effort": ai_mode_profile(ai_mode).answer_effort,
                    **latency,
                },
                ensure_ascii=False,
            ),
        )
        yield _sse(
            "done",
            {
                "message_id": int(assistant_message["id"]),
                "answer_style": payload.answer_style.value,
                "refused": refused,
                "timings": latency,
            },
        )

    async def answer_once(
        self,
        question: str,
        history: list[dict[str, Any]],
        answer_style: AnswerStyle,
        *,
        mode: ExecutionMode,
        ai_mode: AiMode = DEFAULT_AI_MODE,
    ) -> dict[str, Any]:
        """Run the production RAG decision path without persisting chat state."""
        if mode not in (ExecutionMode.EVALUATION, ExecutionMode.EXPERIMENT):
            raise ValueError("answer_once only supports non-production modes")

        ai_mode = AiMode(ai_mode)
        rag_chain = self._rag_chain_for(ai_mode)
        rewritten_question = await self._rewrite_question(
            history, question, rag_chain=rag_chain
        )
        prepared = await self._prepare_answer_decision(
            question, rewritten_question, strict=True, rag_chain=rag_chain
        )
        retrieval_ms = prepared.retrieval_ms
        answer_mode = prepared.answer_mode
        refused = answer_mode is AnswerMode.REFUSAL
        tool_result = (
            self.material_checklist_tool.execute(prepared.tool_input)
            if prepared.tool_input is not None and not refused
            else None
        )
        if answer_mode is AnswerMode.TOOL_ONLY:
            if tool_result is None:
                raise ModelUnavailableError("材料清单生成失败")
            answer = render_material_checklist(tool_result)
        else:
            compliance_required = self.compliance_rule_service.requires_notice(
                question, rewritten_question
            )
            answer = await rag_chain.complete_answer(
                answer_style,
                question,
                rewritten_question,
                prepared.evidence,
                compliance_required,
                tool_result,
                answer_mode=answer_mode,
                supported_points=prepared.judgement.supported_points,
                missing_points=prepared.judgement.missing_points,
                refused=refused,
            )
        if not answer:
            raise ModelUnavailableError("模型未返回回答内容")
        compliance_shown = False
        if answer_mode is not AnswerMode.TOOL_ONLY:
            compliance_required = (
                compliance_required
                or self.compliance_rule_service.requires_notice(
                    question, rewritten_question, answer
                )
            )
            if compliance_required and COMPLIANCE_NOTICE not in answer:
                answer = f"{answer}\n\n{COMPLIANCE_NOTICE}"
            compliance_shown = COMPLIANCE_NOTICE in answer
        return {
            "answer": answer,
            "refused": refused,
            # Keep pre-gate candidates available to diagnostics while only
            # evidence-backed answers expose citations.
            "retrieved_sources": prepared.evidence,
            "citations": (
                []
                if answer_mode in (AnswerMode.REFUSAL, AnswerMode.TOOL_ONLY)
                else prepared.evidence
            ),
            "rewritten_question": rewritten_question,
            "retrieval_ms": retrieval_ms,
            "compliance_shown": compliance_shown,
            "answer_mode": answer_mode.value,
            "evidence_decision": prepared.judgement.decision.value,
            "evidence_reason": prepared.judgement.reason,
            "supported_points": prepared.judgement.supported_points,
            "missing_points": prepared.judgement.missing_points,
            "tool_execution": (
                tool_result.model_dump(mode="json") if tool_result is not None else None
            ),
        }

    async def _rewrite_question(
        self,
        history: list[dict[str, Any]],
        question: str,
        *,
        rag_chain: RagChain | None = None,
    ) -> str:
        return await (rag_chain or self.rag_chain).rewrite_question(history, question)

    async def _retrieve_evidence(
        self, question: str, *, rag_chain: RagChain | None = None
    ) -> list[dict[str, Any]]:
        return await (rag_chain or self.rag_chain).retrieve(question)

    async def _retrieve_for_decision(
        self,
        rewritten_question: str,
        *,
        rag_chain: RagChain | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        started = perf_counter()
        evidence = await self._retrieve_evidence(
            rewritten_question, rag_chain=rag_chain
        )
        elapsed_ms = round((perf_counter() - started) * 1000)
        return evidence, elapsed_ms

    async def _judge_for_decision(
        self,
        rewritten_question: str,
        evidence: list[dict[str, Any]],
        *,
        strict: bool,
        rag_chain: RagChain | None = None,
    ) -> tuple[EvidenceJudgement, MissingKnowledgeReason | None, int]:
        started = perf_counter()
        judgement, refusal_reason = await self._judge_evidence(
            rewritten_question,
            evidence,
            strict=strict,
            rag_chain=rag_chain,
        )
        elapsed_ms = round((perf_counter() - started) * 1000)
        return judgement, refusal_reason, elapsed_ms

    async def _prepare_answer_decision(
        self,
        question: str,
        rewritten_question: str,
        *,
        strict: bool,
        rag_chain: RagChain | None = None,
    ) -> PreparedAnswerDecision:
        tool_input = self.tool_decision_service.decide(question, rewritten_question)
        evidence, retrieval_ms = await self._retrieve_for_decision(
            rewritten_question, rag_chain=rag_chain
        )
        judgement, refusal_reason, evidence_judge_ms = await self._judge_for_decision(
            rewritten_question,
            evidence,
            strict=strict,
            rag_chain=rag_chain,
        )
        return PreparedAnswerDecision(
            evidence=evidence,
            retrieval_ms=retrieval_ms,
            evidence_judge_ms=evidence_judge_ms,
            judgement=judgement,
            refusal_reason=refusal_reason,
            answer_mode=self._decide_answer_mode(judgement, tool_input),
            tool_input=tool_input,
        )

    @staticmethod
    def _decide_answer_mode(
        judgement: EvidenceJudgement,
        tool_input: MaterialChecklistInput | None,
    ) -> AnswerMode:
        if tool_input is not None:
            return (
                AnswerMode.RAG_ANSWER
                if judgement.decision is EvidenceDecision.SUFFICIENT
                else AnswerMode.TOOL_ONLY
            )
        if judgement.decision is EvidenceDecision.SUFFICIENT:
            return AnswerMode.RAG_ANSWER
        if judgement.decision is EvidenceDecision.PARTIAL:
            return AnswerMode.PARTIAL_ANSWER
        return AnswerMode.REFUSAL

    async def _judge_evidence(
        self,
        question: str,
        evidence: list[dict[str, Any]],
        *,
        strict: bool = False,
        rag_chain: RagChain | None = None,
    ) -> tuple[EvidenceJudgement, MissingKnowledgeReason | None]:
        return await (rag_chain or self.rag_chain).judge_evidence(
            question, evidence, strict=strict
        )

    def _answer_messages(
        self,
        answer_style: AnswerStyle,
        question: str,
        rewritten_question: str,
        evidence: list[dict[str, Any]],
        compliance_required: bool,
        tool_execution: ToolExecutionItem | None,
        *,
        answer_mode: AnswerMode = AnswerMode.RAG_ANSWER,
        supported_points: list[str] | None = None,
        missing_points: list[str] | None = None,
    ) -> list[dict[str, str]]:
        return self.rag_chain.answer_messages(
            answer_style,
            question,
            rewritten_question,
            evidence,
            compliance_required,
            tool_execution,
            answer_mode=answer_mode,
            supported_points=supported_points or [],
            missing_points=missing_points or [],
        )


def _sse(event: str, data: dict[str, Any]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"
