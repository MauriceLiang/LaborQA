# 文件作用：编排聊天请求、检索证据、生成回答、持久化和流式事件。
"""编排会话读取、RAG 检索与判定、工具执行、回答生成和结果持久化，并把处理阶段序列化为 Server-Sent Events，供前端逐步展示。"""

from __future__ import annotations

import asyncio
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

# 聊天请求、模型调用和流式输出过程使用的日志记录器。
logger = logging.getLogger(__name__)
# 知识库证据不足时使用的默认拒答文案。
_REFUSAL = REFUSAL_TEXT
# 回答风格到模型指令文本的映射。
_ANSWER_STYLE_INSTRUCTIONS = ANSWER_STYLE_INSTRUCTIONS
# 本地模型流式输出时每次发送的字符块长度。
_LOCAL_STREAM_CHUNK_SIZE = 8
# 本地模型模拟流式输出时每个字符块之间的间隔。
_LOCAL_STREAM_DELAY_SECONDS = 0.03


async def _stream_local_text(
    text: str,
    *,
    chunk_size: int = _LOCAL_STREAM_CHUNK_SIZE,
    delay_seconds: float = _LOCAL_STREAM_DELAY_SECONDS,
):
    """将本地完整文本拆成小块，用于 SSE 模拟流式展示。

    参数：
    text：当前待解析、检索、清理或嵌入的文本。
    chunk_size：文本切分时单个分块的目标长度。
    delay_seconds：流式输出或重试之间等待的秒数。
    """

    if not text:
        return

    # 局部变量 start：当前文本块或子串的起始字符位置。
    for start in range(0, len(text), chunk_size):
        # 局部变量 chunk：当前文本分块记录。
        chunk = text[start : start + chunk_size]

        if chunk:
            yield chunk

        if delay_seconds > 0 and start + chunk_size < len(text):
            await asyncio.sleep(delay_seconds)


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
    """在模型配置不完整时作为延迟失败占位模型，使应用仍可完成服务初始化；只有真正触发模型调用时才报告配置不可用，避免启动阶段被外部模型配置阻断。"""

    @property
    def _llm_type(self) -> str:
        """返回 LangChain 模型对象的运行时类型名称。
        """
        return "unavailable"

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        """使用聊天模型生成完整消息响应。

        参数：
        args：待传递给目标函数的参数集合。
        kwargs：传递给模型或底层函数的额外关键字参数。
        """
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
        """初始化聊天服务及会话、检索和生成依赖。

        参数：
        config：当前应用或实验配置。
        session_service：负责会话与消息数据转换的服务实例。
        retrieval_service：负责向量召回和证据整理的检索服务。
        chat_model：当前阶段使用的聊天模型。
        chat_models：各问答阶段使用的模型集合。
        missing_knowledge_service：记录和维护知识缺口的服务实例。
        """
        # 成员变量 session_service：负责会话和消息转换的服务实例。
        self.session_service = session_service
        # 成员变量 retrieval_service：负责向量召回和证据整理的检索服务。
        self.retrieval_service = retrieval_service
        # 成员变量 config：应用级生成配置，控制各阶段模型、采样参数、token 上限、嵌入和检索行为；聊天链构建与请求执行均从这里读取配置。
        self.config = config
        if chat_models is not None:
            # 成员变量 chat_models：各处理阶段使用的聊天模型集合。
            self.chat_models = dict(chat_models)
            # 成员变量 chat_model：当前阶段使用的聊天模型。
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
            # 局部变量 high_model：本次请求是否使用高质量模型配置。
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
            # 局部变量 unavailable：当前不可用服务或能力的状态标记。
            unavailable = _UnavailableChatModel()
            self.chat_model = unavailable
            self.chat_models = {
                "default": unavailable,
                "off": unavailable,
                "low": unavailable,
                "high": unavailable,
            }
        # 成员变量 evidence_chat_model：证据判定阶段实际使用的聊天模型。
        self.evidence_chat_model = self.chat_models["low"]
        # 成员变量 answer_chat_model：回答阶段实际使用的聊天模型。
        self.answer_chat_model = self.chat_models["off"]
        # 成员变量 rag_chains：按模型配置索引的 RAG 链缓存。
        # 局部变量 mode：当前模型回答或流程采用的模式。
        self.rag_chains = {
            mode: self._build_rag_chain(retrieval_service, mode)
            for mode in AI_MODE_PROFILES
        }
        # 保留旧版 rag_chain 属性，兼容仍通过该属性替换均衡模式链的调用方；
        # 新逻辑统一从 rag_chains 按 AI 模式选择对应链。
        self.rag_chain = self.rag_chains[DEFAULT_AI_MODE]
        # 成员变量 tool_decision_service：选择并校验业务工具调用的服务。
        self.tool_decision_service = ToolDecisionService()
        # 成员变量 material_checklist_tool：执行劳动争议材料检查的工具实例。
        self.material_checklist_tool = MaterialChecklistTool()
        # 成员变量 compliance_rule_service：判断是否展示合规提示的规则服务。
        self.compliance_rule_service = ComplianceRuleService()
        # 成员变量 missing_knowledge_service：记录及维护知识缺口的业务服务。
        self.missing_knowledge_service = missing_knowledge_service or (
            MissingKnowledgeService(database_path=config.database_path)
        )

    def _build_rag_chain(
        self, retrieval_service: RetrievalService, mode: AiMode
    ) -> RagChain:
        """依据配置构造可复用的检索增强问答链。

        参数：
        mode：当前模型回答或请求处理所采用的模式。
        retrieval_service：负责向量召回和证据整理的检索服务。
        """
        # 局部变量 profile：本次模型或检索运行使用的参数配置。
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
        """构造当前模型和生成配置的缓存键。

        参数：
        thinking_enabled：是否为当前模型请求启用思考模式。
        reasoning_effort：传递给模型 Provider 的推理强度设置。
        """
        if not thinking_enabled:
            return "off"
        return reasoning_effort or "high"

    def _rag_chain_for(self, ai_mode: AiMode | str) -> RagChain:
        """按模型配置取得或创建对应问答链。

        参数：
        ai_mode：本次回答生成使用的 AI 模式。
        """
        return self.rag_chains[AiMode(ai_mode)]

    def create_session(self, title: str | None) -> dict[str, Any]:
        """创建聊天会话并返回会话信息。

        参数：
        title：新建或更新会话的展示标题。
        """
        return self.session_service.create_session(title)

    def get_session(self, session_id: UUID) -> dict[str, Any] | None:
        """读取指定聊天会话。

        参数：
        session_id：目标聊天会话标识。
        """
        return self.session_service.get_session(session_id)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近聊天会话。

        参数：
        limit：返回记录数量上限。
        """
        return self.session_service.list_sessions(limit)

    def list_messages(self, session_id: UUID) -> list[dict[str, Any]]:
        """读取指定会话的消息记录。

        参数：
        session_id：目标聊天会话标识。
        """
        return self.session_service.list_messages(session_id)

    def execute_material_checklist(
        self, payload: MaterialChecklistInput
    ) -> ToolExecutionItem:
        """执行劳动争议材料清单检查流程。

        参数：
        payload：经过校验的业务输入对象。
        """
        return self.material_checklist_tool.execute(payload)

    def list_missing_knowledge(
        self, query: MissingKnowledgeQuery
    ) -> tuple[list[dict[str, Any]], int]:
        """查询知识缺口记录。

        参数：
        query：用户问题文本或数据库筛选条件。
        """
        return self.missing_knowledge_service.list(query)

    def update_missing_knowledge(
        self, item_id: int, payload: MissingKnowledgeUpdate
    ) -> dict[str, Any]:
        """更新知识缺口状态及备注。

        参数：
        payload：经过校验的业务输入对象。
        item_id：目标知识缺口记录的唯一标识。
        """
        return self.missing_knowledge_service.update(item_id, payload)

    async def stream_chat(
        self,
        payload: ChatRequest,
        request: Request,
        *,
        execution_mode: ExecutionMode = ExecutionMode.PRODUCTION,
    ):
        """执行聊天请求并逐步产出状态、答案和引用事件。

        参数：
        payload：经过校验的业务输入对象。
        request：当前 HTTP 请求对象。
        execution_mode：当前工具调用或流程执行采用的模式。
        """
        if execution_mode != ExecutionMode.PRODUCTION:
            raise ValueError("stream_chat only supports PRODUCTION mode")
        # 局部变量 request_started：HTTP 请求开始处理时记录的单调时钟值。
        request_started = perf_counter()
        # 局部变量 session_ms：会话创建或读取阶段耗时。
        session_ms = 0
        # 局部变量 rewrite_ms：查询改写阶段耗时。
        rewrite_ms = 0
        # 局部变量 retrieval_ms：检索阶段耗时。
        retrieval_ms = 0
        # 局部变量 evidence_judge_ms：证据判定阶段耗时，单位为毫秒。
        evidence_judge_ms = 0
        # 局部变量 pre_answer_ms：答案生成前处理阶段耗时。
        pre_answer_ms = 0
        # 局部变量 ttft_ms：从开始请求到首个回答 token 的耗时。
        ttft_ms: int | None = None
        # 局部变量 answer_generation_ms：模型生成答案阶段耗时。
        answer_generation_ms = 0
        # 局部变量 persist_ms：保存回答及会话数据所耗时间。
        persist_ms = 0
        # 局部变量 session_id：聊天会话的唯一标识。
        session_id = payload.session_id
        # 局部变量 question：用户提交或模型改写后的问题文本。
        question = payload.question
        # 局部变量 ai_mode：本次回答生成使用的 AI 模式。
        ai_mode = AiMode(payload.ai_mode)
        # 局部变量 rag_chain：按运行配置创建或缓存的 RAG 链。
        rag_chain = self._rag_chain_for(ai_mode)

        yield _sse(
            "status",
            {
                "stage": "understanding",
                "message": "正在理解问题…",
            },
        )

        # 局部变量 session_started：会话创建流程开始时间。
        session_started = perf_counter()
        try:
            # 局部变量 history：当前会话的历史消息集合。
            history = self.session_service.get_recent_context(session_id, turns=3)
            # 局部变量 user_message：持久化后的用户提问消息。
            user_message = self.session_service.create_user_message(
                session_id, question, ai_mode
            )
            session_ms = round((perf_counter() - session_started) * 1000)
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
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

        # 局部变量 rewrite_started：问题改写流程开始时间。
        rewrite_started = perf_counter()
        # 局部变量 rewritten_question：模型改写后的检索查询文本。
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
            # 局部变量 tool_input：本次工具调用的参数数据。
            tool_input = self.tool_decision_service.decide(
                question, rewritten_question
            )
            # 局部变量 evidence：支持或限制回答的检索证据。
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
            # 局部变量 judgement：模型对证据支持程度的结构化判断。
            # 局部变量 refusal_reason：模型拒答或策略拒答的具体原因。
            judgement, refusal_reason, evidence_judge_ms = (
                await self._judge_for_decision(
                    rewritten_question,
                    evidence,
                    strict=False,
                    rag_chain=rag_chain,
                )
            )
            # 局部变量 prepared：完成预处理并供后续流程使用的数据。
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
        # 局部变量 answer_mode：本次问题采用的回答处理路径。
        answer_mode = prepared.answer_mode
        # 局部变量 refused：本次回答是否因证据不足或合规规则而拒答。
        refused = answer_mode is AnswerMode.REFUSAL
        refusal_reason = prepared.refusal_reason
        # 局部变量 record_missing_knowledge：将拒答问题写入知识缺口记录的回调函数。
        record_missing_knowledge = (
            answer_mode is AnswerMode.REFUSAL
            and refusal_reason is not None
            and execution_mode == ExecutionMode.PRODUCTION
        )

        # 局部变量 compliance_required：当前问题是否要求展示合规提示。
        compliance_required = (
            False
            if answer_mode is AnswerMode.TOOL_ONLY
            else self.compliance_rule_service.requires_notice(
                question, rewritten_question
            )
        )
        # 局部变量 answer_parts：流式生成过程中已收到的答案片段。
        answer_parts: list[str] = []
        # 局部变量 tool_executions：本轮回答关联的工具执行记录。
        tool_executions: list[ToolExecutionItem] = []
        if record_missing_knowledge and refusal_reason is not None:
            try:
                self.missing_knowledge_service.record_refusal(
                    rewritten_question, refusal_reason
                )
            except Exception:
                logger.exception("Failed to record a production evidence-gate refusal")

        # 局部变量 answer_started：模型答案生成流程开始时间。
        answer_started = perf_counter()
        pre_answer_ms = round((answer_started - request_started) * 1000)
        # 局部变量 first_token_received：模型流是否已经产生首个回答 token。
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
                # 局部变量 execution：当前工具调用的执行记录。
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
            # 局部变量 rendered_answer：按输出格式整理后的回答文本。
            rendered_answer = render_material_checklist(tool_executions[0])
            answer_parts.append(rendered_answer)
            # 局部变量 chunk：当前文本分块记录。
            async for chunk in _stream_local_text(rendered_answer):
                if await request.is_disconnected():
                    return
                if not first_token_received:
                    ttft_ms = round((perf_counter() - request_started) * 1000)
                    first_token_received = True
                yield _sse("token", {"content": chunk})
        else:
            try:
                # 局部变量 token：当前模型流返回的文本 token。
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
            # 局部变量 completed_answer：完整生成并整理后的回答文本。
            completed_answer = "".join(answer_parts)
            compliance_required = compliance_required or (
                self.compliance_rule_service.requires_notice(
                    question, rewritten_question, completed_answer
                )
            )
            if compliance_required and COMPLIANCE_NOTICE not in completed_answer:
                # 局部变量 notice：面向用户展示的合规或状态提示。
                notice = f"\n\n{COMPLIANCE_NOTICE}"
                answer_parts.append(notice)
                if not first_token_received:
                    ttft_ms = round((perf_counter() - request_started) * 1000)
                    first_token_received = True
                yield _sse("token", {"content": notice})
        answer_generation_ms = round((perf_counter() - answer_started) * 1000)
        if await request.is_disconnected():
            return

        # 局部变量 citations：回答引用的来源记录列表。
        citations = (
            []
            if answer_mode in (AnswerMode.REFUSAL, AnswerMode.TOOL_ONLY)
            else evidence
        )
        yield _sse("sources", {"items": citations})
        if await request.is_disconnected():
            return

        # 局部变量 persist_started：答案持久化流程开始时间。
        persist_started = perf_counter()
        try:
            # 局部变量 assistant_message：持久化后的助手回答消息。
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

        # 局部变量 total_ms：当前请求从开始到结束的总耗时。
        total_ms = round((perf_counter() - request_started) * 1000)
        # 局部变量 latency：当前请求或处理阶段的耗时。
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
                    "delivery_mode": (
                        "synthetic_stream"
                        if answer_mode is AnswerMode.TOOL_ONLY
                        else "llm_stream"
                    ),
                    "evidence_count": len(evidence),
                    "ai_mode": ai_mode.value,
                    "evidence_thinking": ai_mode_profile(ai_mode).evidence_thinking,
                    "evidence_effort": ai_mode_profile(ai_mode).evidence_effort,
                    "answer_thinking": ai_mode_profile(ai_mode).answer_thinking,
                    "answer_effort": ai_mode_profile(ai_mode).answer_effort,
                    "rewrite_temperature": self.config.llm_rewrite_temperature,
                    "rewrite_max_tokens": self.config.llm_rewrite_max_tokens,
                    "evidence_temperature": self.config.llm_evidence_temperature,
                    "evidence_max_tokens": self.config.llm_evidence_max_tokens,
                    "answer_temperature": self.config.llm_answer_temperature,
                    "answer_max_tokens": self.config.llm_answer_max_tokens,
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
        """走与生产问答相同的检索、证据判断和回答决策路径，但不创建或写入用户会话；用于检索实验预览等需要得到一次性答案的场景。

            参数：
            question：用户提交或模型改写后的问题文本。
            history：当前会话的历史消息集合。
            answer_style：本次生成答案使用的表达风格。
            mode：当前模型回答或请求处理所采用的模式。
            ai_mode：本次回答生成使用的 AI 模式。
        """
        if mode not in (ExecutionMode.EVALUATION, ExecutionMode.EXPERIMENT):
            raise ValueError("answer_once only supports non-production modes")

        # 局部变量 ai_mode：本次回答生成使用的 AI 模式。
        ai_mode = AiMode(ai_mode)
        # 局部变量 rag_chain：按运行配置创建或缓存的 RAG 链。
        rag_chain = self._rag_chain_for(ai_mode)
        # 局部变量 rewritten_question：模型改写后的检索查询文本。
        rewritten_question = await self._rewrite_question(
            history, question, rag_chain=rag_chain
        )
        # 局部变量 prepared：完成预处理并供后续流程使用的数据。
        prepared = await self._prepare_answer_decision(
            question, rewritten_question, strict=True, rag_chain=rag_chain
        )
        # 局部变量 retrieval_ms：检索阶段耗时。
        retrieval_ms = prepared.retrieval_ms
        # 局部变量 answer_mode：本次问题采用的回答处理路径。
        answer_mode = prepared.answer_mode
        # 局部变量 refused：本次回答是否因证据不足或合规规则而拒答。
        refused = answer_mode is AnswerMode.REFUSAL
        # 局部变量 tool_result：当前工具调用产生的结果。
        tool_result = (
            self.material_checklist_tool.execute(prepared.tool_input)
            if prepared.tool_input is not None and not refused
            else None
        )
        if answer_mode is AnswerMode.TOOL_ONLY:
            if tool_result is None:
                raise ModelUnavailableError("材料清单生成失败")
            # 局部变量 answer：模型生成或待评估的回答文本。
            answer = render_material_checklist(tool_result)
        else:
            # 局部变量 compliance_required：当前问题是否要求展示合规提示。
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
        # 局部变量 compliance_shown：本次回答是否已经展示合规提示。
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
            # 即使答案被证据门控拒答，也保留完整候选证据供检索诊断查看；
            # citations 则只对允许展示证据支持内容的回答开放，避免把候选误报为有效引用。
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
        """改写用户问题以提高检索召回相关性。

        参数：
        question：用户提出的问题文本。
        history：当前会话的历史消息集合。
        rag_chain：当前模型配置对应的检索增强问答链。
        """
        return await (rag_chain or self.rag_chain).rewrite_question(history, question)

    async def _retrieve_evidence(
        self, question: str, *, rag_chain: RagChain | None = None
    ) -> list[dict[str, Any]]:
        """检索并整理可用于回答的证据。

        参数：
        question：用户提出的问题文本。
        rag_chain：当前模型配置对应的检索增强问答链。
        """
        return await (rag_chain or self.rag_chain).retrieve(question)

    async def _retrieve_for_decision(
        self,
        rewritten_question: str,
        *,
        rag_chain: RagChain | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """获取回答策略判定所需的检索结果。

        参数：
        rewritten_question：模型改写后的检索查询文本。
        rag_chain：用于当前模型配置的检索增强问答链。
        """
        # 局部变量 started：后台处理任务是否已经启动。
        started = perf_counter()
        # 局部变量 evidence：支持或限制回答的检索证据。
        evidence = await self._retrieve_evidence(
            rewritten_question, rag_chain=rag_chain
        )
        # 局部变量 elapsed_ms：当前阶段已经消耗的时间，单位为毫秒。
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
        """判定当前检索证据与问题的支持关系。

        参数：
        evidence：支持回答的检索证据。
        rewritten_question：模型改写后用于检索的问题文本。
        strict：是否使用严格的证据判定条件。
        rag_chain：当前模型配置对应的检索增强问答链。
        """
        # 局部变量 started：后台处理任务是否已经启动。
        started = perf_counter()
        # 局部变量 judgement：模型对证据支持程度的结构化判断。
        # 局部变量 refusal_reason：模型拒答或策略拒答的具体原因。
        judgement, refusal_reason = await self._judge_evidence(
            rewritten_question,
            evidence,
            strict=strict,
            rag_chain=rag_chain,
        )
        # 局部变量 elapsed_ms：当前阶段已经消耗的时间，单位为毫秒。
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
        """准备生成回答前的证据和模式判定数据。

        参数：
        question：用户提出的问题文本。
        rewritten_question：模型改写后用于检索的问题文本。
        strict：是否使用严格的证据判定条件。
        rag_chain：当前模型配置对应的检索增强问答链。
        """
        # 局部变量 tool_input：本次工具调用的参数数据。
        tool_input = self.tool_decision_service.decide(question, rewritten_question)
        # 局部变量 evidence：支持或限制回答的检索证据。
        # 局部变量 retrieval_ms：检索阶段耗时。
        evidence, retrieval_ms = await self._retrieve_for_decision(
            rewritten_question, rag_chain=rag_chain
        )
        # 局部变量 judgement：模型对证据支持程度的结构化判断。
        # 局部变量 refusal_reason：模型拒答或策略拒答的具体原因。
        # 局部变量 evidence_judge_ms：证据判定阶段耗时，单位为毫秒。
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
        """根据问题和证据决定回答或拒答模式。

        参数：
        judgement：模型对证据支持程度的结构化判断。
        tool_input：本次工具调用的参数数据。
        """
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
        """评估检索证据是否足以支持答案。

        参数：
        question：用户提出的问题文本。
        evidence：支持回答的检索证据。
        strict：是否使用严格的证据判定条件。
        rag_chain：当前模型配置对应的检索增强问答链。
        """
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
        """组装发送给回答模型的消息内容。

        参数：
        answer_style：答案表达风格。
        question：用户提出的问题文本。
        evidence：支持回答的检索证据。
        rewritten_question：模型改写后用于检索的问题文本。
        compliance_required：当前回答是否必须附带合规提示。
        tool_execution：本次工具调用的结构化执行记录。
        answer_mode：当前问题采用的回答路径。
        supported_points：已有证据或答案支持的预期要点。
        missing_points：当前证据或回答尚未覆盖的预期要点。
        """
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
    """将事件名称和数据序列化为服务器推送事件格式。

    参数：
    data：待解析或持久化的原始数据。
    event：当前要推送或处理的事件对象。
    """
    # 局部变量 encoded：模型编码或序列化后的内容。
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"
