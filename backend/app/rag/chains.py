# 文件作用：组合查询改写、检索、证据核验和答案生成等 RAG 执行链。
"""定义证据优先的检索增强生成链，把问题改写、证据检索、充分性判断和答案生成串联为可复用流程；链负责模型与提示模板编排，不直接拥有数据库或向量索引。"""

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

# 本模块问答链执行过程使用的日志记录器。
logger = logging.getLogger(__name__)
# 问答链提示词模板文件所在目录。
_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

# 证据不足时向用户展示的保守拒答文本。
REFUSAL_TEXT = (
    "目前知识库中的资料不足以支持对这个问题作出可靠判断。请补充相关资料后再咨询。"
)
# 不同回答风格对应的模型生成指引。
ANSWER_STYLE_INSTRUCTIONS = {
    "plain": "面向普通劳动者，少用专业术语，先给简明结论，再解释依据和可执行建议。",
    "legal": "使用严谨、客观的表达，明确说明证据支持的适用条件、事实前提与结论边界。",
}


class RagChain:
    """组合问题改写、证据判断和最终回答三个阶段的 LangChain Runnable。检索结果由 RetrievalService 提供，模型按阶段参数配置，因而同一条链可以被生产问答与实验预览复用。"""

    def __init__(
        self,
        retrieval_service: RetrievalService,
        chat_model: BaseChatModel,
        config: Settings = settings,
        *,
        evidence_model: BaseChatModel | None = None,
        answer_model: BaseChatModel | None = None,
    ) -> None:
        """初始化问答链及其检索、模型和提示词依赖。

        参数：
        config：当前应用或实验配置。
        retrieval_service：负责向量召回和证据整理的检索服务。
        chat_model：当前阶段使用的聊天模型。
        evidence_model：证据充分性判定阶段使用的模型。
        answer_model：回答阶段使用的聊天模型；可选时表示覆盖默认模型。
        """
        # 成员变量 retrieval_service：负责向量召回和证据整理的检索服务。
        self.retrieval_service = retrieval_service
        # 成员变量 retriever：当前 RAG 链使用的 LangChain 检索器。
        self.retriever = getattr(retrieval_service, "retriever", None)
        # 成员变量 chat_model：当前阶段使用的聊天模型。
        self.chat_model = chat_model
        # 成员变量 config：RAG 各阶段共用的 Settings；问题改写、证据判断和答案生成分别读取其温度、top_p 与 token 上限。
        self.config = config
        # 成员变量 answer_prompt：组织最终答案输入的提示模板。
        self.answer_prompt = _answer_prompt()
        # 成员变量 rewrite_prompt：组织问题改写请求的提示模板。
        self.rewrite_prompt = _rewrite_prompt()
        # 成员变量 evidence_prompt：组织证据判定请求的提示模板。
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
        """根据运行配置创建并设置各阶段所需的聊天模型。

        参数：
        model：当前使用的模型。
        evidence_model：证据充分性判定阶段使用的模型。
        answer_model：回答阶段使用的聊天模型；可选时表示覆盖默认模型。
        """
        # 成员变量 chat_model：当前阶段使用的聊天模型。
        self.chat_model = model
        # 成员变量 evidence_model：证据判定阶段使用的模型。
        self.evidence_model = evidence_model or model
        # 成员变量 answer_model：回答阶段使用的模型实例。
        self.answer_model = answer_model or model
        # 局部变量 rewrite_model：问题改写阶段使用的聊天模型。
        rewrite_model = self.chat_model.bind(
            temperature=self.config.llm_rewrite_temperature,
            top_p=self.config.llm_rewrite_top_p,
            max_tokens=self.config.llm_rewrite_max_tokens,
        )
        # 局部变量 evidence_model：证据判定阶段使用的模型。
        evidence_model = self.evidence_model.bind(
            temperature=self.config.llm_evidence_temperature,
            top_p=self.config.llm_evidence_top_p,
            max_tokens=self.config.llm_evidence_max_tokens,
            response_format={"type": "json_object"},
        )
        # 局部变量 answer_model：回答阶段使用的模型实例。
        answer_model = self.answer_model.bind(
            temperature=self.config.llm_answer_temperature,
            top_p=self.config.llm_answer_top_p,
            max_tokens=self.config.llm_answer_max_tokens,
        )
        # 成员变量 rewrite_chain：将用户问题改写为检索查询的处理链。
        self.rewrite_chain = self.rewrite_prompt | rewrite_model | StrOutputParser()
        # 成员变量 evidence_chain：判断证据充分性的处理链。
        self.evidence_chain = self.evidence_prompt | evidence_model | JsonOutputParser()
        # 成员变量 answer_chain：负责生成最终回答的 RAG 链。
        self.answer_chain = RunnableBranch(
            (lambda state: bool(state["refused"]), RunnableLambda(_refusal)),
            self.answer_prompt | answer_model | StrOutputParser(),
        )

    def set_chat_model(self, model: BaseChatModel) -> None:
        """将同一个聊天模型实例绑定到改写、证据判断和回答阶段，供旧接口或显式单模型调用方继续使用；各阶段仍按当前配置分别应用采样参数。

        参数：
        model：当前使用的模型或模型名称。
        """

        self._set_chat_models(model, evidence_model=model, answer_model=model)

    async def rewrite_question(
        self, history: list[dict[str, Any]], question: str
    ) -> str:
        """根据聊天上下文生成适合检索的改写问题。

        参数：
        question：用户提出的问题文本。
        history：当前会话的历史消息集合。
        """
        if not history:
            return question
        # 局部变量 context：模型生成或评估使用的上下文内容。
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        context = [
            {"role": item["role"], "content": item["content"]} for item in history
        ]
        try:
            # 局部变量 result：问题改写模型生成的检索查询文本；后续会清理该文本并传给检索器，而不是直接作为最终回答返回。
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
        """执行问题检索并返回候选证据。

        参数：
        question：用户提出的问题文本。
        """
        if self.retriever is not None:
            # 局部变量 documents：本次检索、解析或批量处理的文档集合。
            documents = await self.retriever.ainvoke(question)
            # 局部变量 document：检索器返回的 LangChain Document；证据映射会从正文和 metadata 恢复分块标识、来源及检索分数。
            return [_evidence_from_document(document) for document in documents]
        # 局部变量 aretrieve：执行异步检索的可调用对象。
        aretrieve = getattr(self.retrieval_service, "aretrieve", None)
        if callable(aretrieve):
            return await aretrieve(question)
        # 局部变量 evidence：支持或限制回答的检索证据。
        evidence = await asyncio.to_thread(self.retrieval_service.retrieve, question)
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        return [dict(item) for item in evidence]

    async def judge_evidence(
        self,
        question: str,
        evidence: list[dict[str, Any]],
        *,
        strict: bool = False,
    ) -> tuple[EvidenceJudgement, MissingKnowledgeReason | None]:
        """判断候选证据是否足以支持针对用户问题的回答。

        参数：
        question：用户提出的问题文本。
        evidence：支持回答的检索证据。
        strict：是否使用严格的证据判定条件。
        """
        if not evidence:
            return (
                _evidence_judgement(
                    EvidenceDecision.INSUFFICIENT,
                    "没有召回到可用于判断的知识库片段。",
                    missing_points=["问题对应的知识库证据"],
                ),
                MissingKnowledgeReason.NO_RETRIEVAL_RESULT,
            )
        # 局部变量 max_retrieval_score：当前候选集合中的最高检索分数。
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        max_retrieval_score = max(
            float(item.get("retrieval_score", item.get("score", 0.0)))
            for item in evidence
        )
        if max_retrieval_score < self.config.rag_score_threshold:
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
            # 局部变量 judgement：模型对证据支持程度的结构化判断。
            judgement = await self.evidence_chain.ainvoke(
                {
                    "payload": json.dumps(
                        {"question": question, "evidence": evidence},
                        ensure_ascii=False,
                    ),
                }
            )
            # 局部变量 parsed：模型输出解析后的结构化数据。
            parsed = _parse_evidence_judgement(judgement)
            if parsed.decision is EvidenceDecision.INSUFFICIENT:
                return parsed, MissingKnowledgeReason.INSUFFICIENT_EVIDENCE
            return parsed, None
        except ModelUnavailableError:
            if strict:
                raise
            logger.warning("Evidence judge failed; failing closed", exc_info=True)
            return _failed_closed_judgement(), None
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
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
        """构造包含问题、历史消息和证据的最终回答提示。

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
        # 局部变量 compliance_instruction：附加到模型输入中的合规生成指引。
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
        """调用回答模型生成完整答案。

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
        refused：模型回答是否因为证据不足或合规规则而拒答。
        """
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
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
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
        """以流式方式生成答案并逐段产出内容。

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
        refused：模型回答是否因为证据不足或合规规则而拒答。
        """
        # 局部变量 token：当前模型流返回的文本 token。
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
        """渲染最终答案链所需的消息序列，并把问题、证据、回答风格、合规提示及工具结果填入对应模板；此方法只准备模型输入，不执行生成。

        参数：
        answer_style：本次生成答案使用的表达风格。
        question：用户提交或模型改写后的问题文本。
        rewritten_question：模型改写后的检索查询文本。
        evidence：支持或限制回答的检索证据。
        compliance_required：当前问题是否要求展示合规提示。
        tool_execution：本次工具调用的执行结果记录。
        answer_mode：本次问题采用的回答处理路径。
        supported_points：已被证据或答案支持的预期要点。
        missing_points：证据或答案尚未覆盖的预期要点列表。
        """

        # 局部变量 prompt_value：填充后的模型提示内容对象。
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
    """将检索到的 LangChain 文档转换为证据响应字典。

    参数：
    document：当前处理的文档对象或记录。
    """
    # 局部变量 metadata：检索器随 LangChain Document 返回的分块、来源和评分字段；证据转换时用它恢复业务层需要的信息。
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
    """构造生成最终答案时使用的提示模板。"""
    # 局部变量 template：当前模型调用使用的提示词模板。
    template = (_PROMPT_DIR / "answer_system.txt").read_text(encoding="utf-8")
    # 局部变量 name：经过规范化或校验的资源名称。
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
    """构造将用户问题改写为检索查询时使用的提示模板。"""
    # 局部变量 template：当前模型调用使用的提示词模板。
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
    """构造判断检索证据是否充分时使用的提示模板。"""
    # 局部变量 template：当前模型调用使用的提示词模板。
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
    """转义字符串模板中的花括号，避免被误解析为格式占位符。

    参数：
    value：需要转换或校验的输入值。
    """
    return value.replace("{", "{{").replace("}", "}}")


def _format_evidence(evidence: Sequence[Mapping[str, Any]]) -> str:
    """将证据列表格式化为模型可读的文本块。

    参数：
    evidence：支持回答的检索证据。
    """
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return "\n\n".join(
        f"[Evidence {item['rank_no']}] {item['content']}" for item in evidence
    )


def _tool_result(tool_execution: ToolExecutionItem | None) -> str:
    """把工具调用结果转换为提示词中的展示文本。

    参数：
    tool_execution：本次工具调用的执行结果记录。
    """
    if tool_execution is None:
        return "无"
    return json.dumps(tool_execution.output.model_dump(mode="json"), ensure_ascii=False)


def _format_points(points: Sequence[str]) -> str:
    """将预期要点列表格式化为文本。

    参数：
    points：评估答案需要覆盖的预期要点列表。
    """
    return json.dumps(list(points), ensure_ascii=False)


def _evidence_judgement(
    decision: EvidenceDecision,
    reason: str,
    *,
    supported_points: Sequence[str] = (),
    missing_points: Sequence[str] = (),
) -> EvidenceJudgement:
    """调用模型判断当前证据能否支持回答。

    参数：
    decision：证据充足性判定结果。
    reason：当前判定或失败状态的原因说明。
    supported_points：已有证据或答案支持的预期要点。
    missing_points：当前证据或回答尚未覆盖的预期要点。
    """
    return EvidenceJudgement(
        decision=decision,
        reason=reason,
        supported_points=list(supported_points),
        missing_points=list(missing_points),
    )


def _failed_closed_judgement() -> EvidenceJudgement:
    """构造保守拒答的证据判定结果。"""
    return _evidence_judgement(
        EvidenceDecision.INSUFFICIENT,
        "证据充分性判断未成功，无法安全确认回答范围。",
        missing_points=["证据充分性判断"],
    )


def _parse_evidence_judgement(value: Any) -> EvidenceJudgement:
    """解析并校验模型生成的证据判定结构。

    参数：
    value：需要转换或校验的输入值。
    """
    if not isinstance(value, dict):
        raise TypeError("evidence judgement must be a JSON object")
    # 兼容旧 Provider 返回的 sufficient 布尔字段，避免升级后历史配置立即失效；
    # 新提示词及内部后续流程统一使用 EvidenceJudgement 结构。
    if "decision" not in value and isinstance(value.get("sufficient"), bool):
        # 局部变量 value：模型输出的证据判定对象；兼容分支会把旧版 sufficient 布尔值转成 decision 枚举并补齐要点列表，再交由 Pydantic 校验。
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
    """将消息对象转换为角色与文本组成的字典列表。

    参数：
    messages：聊天消息列表。
    """
    # 局部变量 result：按输入消息顺序整理出的聊天消息列表；每项统一包含 role 与 content，供提示模板或模型调用使用。
    result: list[dict[str, str]] = []
    # 局部变量 message：需要记录、保存或返回的消息文本。
    for message in messages:
        # 局部变量 role：当前对话消息的角色标识。
        role = {"human": "user", "ai": "assistant"}.get(message.type, message.type)
        # 局部变量 content：当前文档、消息或文件的主体内容。
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
    """组装生成回答所需的上下文和工具数据。

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
    refused：模型回答是否因为证据不足或合规规则而拒答。
    """
    return {
        "refused": (answer_mode is AnswerMode.REFUSAL if refused is None else refused),
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
    """返回证据不足时使用的拒答文案。

    参数：
    _state：当前 RAG 流程的状态映射，包含问题与证据判定结果。
    """
    return REFUSAL_TEXT
