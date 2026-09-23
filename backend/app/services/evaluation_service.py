# 文件作用：编排评估任务、用例执行、指标计算和结果保存。
"""管理评测用例的校验、版本和生命周期，并编排异步评测运行及结果汇总；评测复用问答能力但不会写入生产用户会话。"""

from __future__ import annotations

import json
import logging
from inspect import signature
from pathlib import Path
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import Settings, settings
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.rag.errors import EmbeddingUnavailableError, ModelUnavailableError
from app.rag.ai_modes import DEFAULT_AI_MODE
from app.rag.runtime_config import runtime_config_snapshot
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.contracts import (
    AiMode,
    AnswerStyle,
    EvaluationCaseCreate,
    EvaluationCaseOrigin,
    EvaluationCaseScope,
    EvaluationCaseStatus,
    EvaluationCaseUpdate,
    EvaluationRunCreate,
)
from app.services.chat_service import ChatService
from app.services.evaluation_cases import baseline_version, fixed_evaluation_cases
from app.services.missing_knowledge import ExecutionMode
from app.services.vector_store import (
    VectorStoreNotInitialized,
    VectorStorePersistenceError,
    VectorStoreSignatureMismatch,
)

# 评测任务、模型调用和结果计算使用的日志记录器。
logger = logging.getLogger(__name__)
# 用于标识当前评测提示词结构的版本号。
_EVALUATOR_PROMPT_VERSION = "evaluation_judge_v2"
# 进程重启后用于说明评测任务中断的错误文本。
_RESTART_ERROR = "服务重启导致任务中断"


class EvaluationService:
    def __init__(
        self,
        chat_service: ChatService,
        config: Settings = settings,
        *,
        repository: EvaluationRepository | None = None,
    ) -> None:
        """初始化评估服务及仓库、模型和聊天依赖。

        参数：
        config：当前应用或实验配置。
        repository：负责当前业务数据持久化的数据仓库实例。
        chat_service：提供聊天和会话业务能力的服务实例。
        """
        # 成员变量 chat_service：处理聊天请求的业务服务实例。
        self.chat_service = chat_service
        # 成员变量 config：当前请求、服务或实验所使用的配置。
        self.config = config
        # 成员变量 repository：负责当前业务数据持久化的数据仓库。
        self.repository = repository or EvaluationRepository(config.database_path)
        # 成员变量 evaluator_prompt：评估模型判定答案质量所用的提示模板。
        self.evaluator_prompt = (
            Path(__file__).resolve().parents[1] / "prompts" / "evaluation_judge.txt"
        ).read_text(encoding="utf-8")

    def initialize(self) -> None:
        """初始化评估仓库并恢复遗留任务状态。
        """
        self.repository.seed_cases(
            fixed_evaluation_cases(), baseline_version=baseline_version()
        )
        self.repository.recover_interrupted_runs()

    def list_cases(
        self,
        *,
        page: int,
        size: int,
        topic: str | None,
        expected_type: str | None,
        is_multi_turn: bool | None,
        origin: str | None,
        status: str | None,
        include_archived: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        """按筛选条件查询评估用例。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        topic：评估用例或知识缺口所属的主题。
        expected_type：用例预期的回答或拒答类型。
        is_multi_turn：评估用例是否包含多轮对话。
        origin：评估用例或资料的来源类别。
        include_archived：查询结果是否包含已归档记录。
        """
        return self.repository.list_cases(
            page=page,
            size=size,
            topic=topic,
            expected_type=expected_type,
            is_multi_turn=is_multi_turn,
            origin=origin,
            status=status,
            include_archived=include_archived,
        )

    def create_case(self, payload: EvaluationCaseCreate) -> dict[str, Any]:
        """创建用户自定义评估用例。

        参数：
        payload：经过校验的业务输入对象。
        """
        return self.repository.create_case(payload.model_dump(mode="json"))

    def get_case(
        self, case_id: int, *, include_archived: bool = False
    ) -> dict[str, Any]:
        """读取指定评估用例。

        参数：
        case_id：目标评估用例标识。
        include_archived：查询结果是否包含已归档记录。
        """
        # 局部变量 case：当前评测用例数据，包含问题轮次和预期要点/来源；本流程据这些字段构造输入、执行判定或汇总该用例结果。
        case = self.repository.get_case(case_id, include_archived=include_archived)
        if case is None:
            raise AppError(
                ErrorCode.EVALUATION_CASE_NOT_FOUND,
                "评测用例不存在",
                http_status=404,
            )
        return case

    def update_case(
        self, case_id: int, payload: EvaluationCaseUpdate
    ) -> dict[str, Any]:
        """更新评估用例内容。

        参数：
        case_id：目标评估用例标识。
        payload：经过校验的业务输入对象。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.get_case(case_id, include_archived=True)
        if current["status"] != EvaluationCaseStatus.ACTIVE.value:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "已归档的评测用例不能编辑",
                http_status=409,
            )
        if current["version"] != payload.version:
            raise AppError(
                ErrorCode.CASE_VERSION_CONFLICT,
                "评测用例已被其他操作修改，请刷新后重试",
                http_status=409,
            )
        # 局部变量 updated：本次更新后得到的业务对象。
        updated = self.repository.update_case(
            case_id, payload.version, payload.model_dump(mode="json")
        )
        if updated is None:
            raise AppError(
                ErrorCode.CASE_VERSION_CONFLICT,
                "评测用例已被其他操作修改，请刷新后重试",
                http_status=409,
            )
        return updated

    def archive_case(self, case_id: int) -> dict[str, Any]:
        """归档评估用例。

        参数：
        case_id：目标评估用例标识。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.get_case(case_id, include_archived=True)
        if current["origin"] == EvaluationCaseOrigin.BUILTIN.value:
            raise AppError(
                ErrorCode.BUILTIN_CASE_READ_ONLY,
                "内置评测用例不能删除",
                http_status=409,
            )
        if current["status"] == EvaluationCaseStatus.ARCHIVED.value:
            return current
        if self.repository.has_active_run(case_id):
            raise AppError(
                ErrorCode.CASE_IN_ACTIVE_RUN,
                "评测用例正在运行中的批次中，暂不能删除",
                http_status=409,
            )
        # 局部变量 archived：归档操作返回的最新资源记录。
        archived = self.repository.archive_case(case_id)
        if archived is None:
            raise AppError(
                ErrorCode.EVALUATION_CASE_NOT_FOUND,
                "评测用例不存在",
                http_status=404,
            )
        return archived

    def create_run(self, payload: EvaluationRunCreate) -> dict[str, Any]:
        """创建评测运行并安排后台执行。

        参数：
        payload：经过校验的业务输入对象。
        """
        # 局部变量 case_scope：本次评测或实验选择的用例范围。
        case_scope = payload.case_scope or (
            EvaluationCaseScope.SELECTED
            if payload.case_ids is not None
            else EvaluationCaseScope.BUILTIN_BASELINE
        )
        if case_scope is EvaluationCaseScope.BUILTIN_BASELINE:
            # 局部变量 cases：本次查询或运行使用的评估用例集合。
            cases, _ = self.repository.list_cases(
                page=1,
                size=100,
                topic=None,
                expected_type=None,
                is_multi_turn=None,
                origin=EvaluationCaseOrigin.BUILTIN.value,
                status=EvaluationCaseStatus.ACTIVE.value,
                include_archived=False,
            )
        elif case_scope is EvaluationCaseScope.ALL_ACTIVE:
            cases = self.repository.get_cases()
        else:
            cases = self.repository.get_cases(payload.case_ids)
        if case_scope is EvaluationCaseScope.SELECTED and (
            payload.case_ids is None or len(cases) != len(payload.case_ids)
        ):
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "评测用例不存在",
                http_status=400,
            )
        if not cases:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "评测用例集为空",
                http_status=400,
            )
        # 局部变量 snapshot：任务创建时保存的不可变输入快照。
        snapshot = {
            **runtime_config_snapshot(self.config, ai_mode=payload.ai_mode),
            "answer_style": payload.answer_style.value,
            "case_scope": case_scope.value,
            "baseline_version": (
                baseline_version()
                if case_scope is EvaluationCaseScope.BUILTIN_BASELINE
                else None
            ),
            "evaluator_model": self.config.llm_model,
            "evaluator_prompt_version": _EVALUATOR_PROMPT_VERSION,
            "chunk_size": self.config.chunk_size,
            "chunk_overlap": self.config.chunk_overlap,
            "candidate_k": self.config.rag_candidate_k,
            "top_k": self.config.rag_top_k,
            "lightweight_rerank_enabled": self.config.lightweight_rerank_enabled,
            "neighbor_expand_top_n": self.config.rag_neighbor_expand_top_n,
            "neighbor_radius": self.config.rag_neighbor_radius,
            "evidence_limit": self.config.rag_evidence_limit,
            "rerank_enabled": self.config.rerank_enabled,
            "score_threshold": self.config.rag_score_threshold,
        }
        return self.repository.create_run(
            payload.name,
            cases,
            snapshot,
        )

    def list_runs(
        self, *, page: int, size: int, status: str | None
    ) -> tuple[list[dict[str, Any]], int]:
        """按条件分页查询评测运行。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        """
        return self.repository.list_runs(page=page, size=size, status=status)

    def get_run(self, run_id: int) -> dict[str, Any]:
        """读取评测运行详情。

        参数：
        run_id：目标评测运行标识。
        """
        # 局部变量 run：当前评测任务或运行记录。
        run = self.repository.get_run(run_id)
        if run is None:
            raise AppError(
                ErrorCode.EVALUATION_RUN_NOT_FOUND,
                "评测批次不存在",
                http_status=404,
            )
        run["config"] = {
            **runtime_config_snapshot(
                self.config,
                ai_mode=AiMode(
                    run["config"].get("ai_mode", DEFAULT_AI_MODE.value)
                ),
            ),
            **run["config"],
        }
        return run

    def delete_run(self, run_id: int) -> None:
        """删除评测运行及其评估结果。

        参数：
        run_id：目标评测运行标识。
        """
        # 局部变量 run：当前评测任务或运行记录。
        run = self.repository.delete_run(run_id)
        if run is None:
            raise AppError(
                ErrorCode.EVALUATION_RUN_NOT_FOUND,
                "评测批次不存在",
                http_status=404,
            )
        if run["status"] not in {"COMPLETED", "FAILED"}:
            raise AppError(
                ErrorCode.INVALID_EVALUATION_RUN_STATE,
                "排队中或运行中的评测批次不能删除",
                http_status=409,
            )

    async def execute_run(self, run_id: int) -> None:
        """依次执行评测用例并保存结果及运行汇总。

        参数：
        run_id：目标评测运行标识。
        """
        try:
            self.repository.set_run_status(run_id, "RUNNING")
            # 局部变量 run：当前评测任务或运行记录。
            run = self.repository.get_run(run_id)
            if run is None:
                raise RuntimeError("评测批次不存在")
            # 局部变量 answer_style：本次生成答案使用的表达风格。
            answer_style = AnswerStyle(run["config"]["answer_style"])
            # 局部变量 ai_mode：本次回答生成使用的 AI 模式。
            ai_mode = AiMode(
                run["config"].get("ai_mode", DEFAULT_AI_MODE.value)
            )
            # 局部变量 cases：本次查询或运行使用的评估用例集合。
            cases = self.repository.run_cases(run_id)
            # 局部变量 case：当前评测用例数据，包含问题轮次和预期要点/来源；本流程据这些字段构造输入、执行判定或汇总该用例结果。
            for case in cases:
                try:
                    # 局部变量 result：当前评测用例的执行结果，含回答、拒答、引用和判分字段；若单用例失败则由后续异常分支记录失败信息。
                    result = await self._evaluate_case(case, answer_style, ai_mode)
                # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
                except (
                    ModelUnavailableError,
                    EmbeddingUnavailableError,
                    VectorStoreNotInitialized,
                    VectorStorePersistenceError,
                    VectorStoreSignatureMismatch,
                ) as exc:
                    logger.exception("Evaluation case %s failed", case["id"])
                    result = _failed_result(
                        case["id"], self._system_error_message(exc)
                    )
                except Exception as exc:
                    logger.exception("Evaluation case %s failed", case["id"])
                    result = _failed_result(case["id"], str(exc) or "评测用例执行失败")
                self.repository.add_result(run_id, result)

            run = self.repository.get_run(run_id)
            if run is None:
                raise RuntimeError("评测批次在执行过程中不存在")
            # 局部变量 result_by_case：按评估用例标识索引的结果映射。
            result_by_case = {result["case_id"]: result for result in run["results"]}
            self.repository.complete_run(
                run_id, _calculate_metrics(cases, result_by_case)
            )
        except Exception as exc:
            logger.exception("Evaluation run %s failed", run_id)
            try:
                self.repository.fail_run(run_id, str(exc) or "评测任务失败")
            except Exception:
                logger.exception("Could not mark evaluation run %s as failed", run_id)

    async def _evaluate_case(
        self,
        case: dict[str, Any],
        answer_style: AnswerStyle,
        ai_mode: AiMode = DEFAULT_AI_MODE,
    ) -> dict[str, Any]:
        """运行单条用例并依据预期答案评估结果。

        参数：
        case：当前评测用例数据。
        answer_style：答案表达风格。
        ai_mode：生成回答时采用的 AI 模式。
        """
        # 局部变量 history：当前会话的历史消息集合。
        history: list[dict[str, str]] = []
        # 局部变量 outputs：模型或工具产生的输出内容集合。
        outputs: list[dict[str, Any]] = []
        # 局部变量 turn_diagnostics：按对话轮次保存的检索与回答诊断。
        turn_diagnostics: list[dict[str, Any]] = []
        # 局部变量 latency_ms：端到端或阶段耗时，单位为毫秒。
        latency_ms = 0
        # 局部变量 turn_no：当前对话轮次的序号。
        # 局部变量 question：用户提交或模型改写后的问题文本。
        for turn_no, question in enumerate(case["turns"], start=1):
            # 局部变量 answer_once：对当前问题单次生成的回答。
            answer_once = self.chat_service.answer_once
            # 局部变量 answer_kwargs：回答生成模型使用的调用参数。
            answer_kwargs: dict[str, Any] = {"mode": ExecutionMode.EVALUATION}
            if "ai_mode" in signature(answer_once).parameters:
                answer_kwargs["ai_mode"] = ai_mode
            # 局部变量 output：当前流程生成的输出内容。
            output = await answer_once(
                question, history, answer_style, **answer_kwargs
            )
            outputs.append(output)
            # 局部变量 retrieved_sources：检索阶段返回的来源列表。
            retrieved_sources = output.get(
                "retrieved_sources", output.get("citations", [])
            )
            # 局部变量 citations：回答引用的来源记录列表。
            citations = output.get("citations", [])
            turn_diagnostics.append(
                {
                    "turn_no": turn_no,
                    "question": question,
                    "rewritten_question": output.get("rewritten_question", question),
                    "answer": output.get("answer", ""),
                    "refused": bool(output.get("refused", False)),
                    "answer_mode": output.get("answer_mode"),
                    "evidence_decision": output.get("evidence_decision"),
                    "evidence_reason": output.get("evidence_reason"),
                    "supported_points": output.get("supported_points", []),
                    "missing_points": output.get("missing_points", []),
                    "retrieved_sources": retrieved_sources,
                    "citations": citations,
                    "retrieval_ms": int(output.get("retrieval_ms", 0)),
                }
            )
            history.extend(
                [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": output["answer"]},
                ]
            )
            latency_ms += output["retrieval_ms"]

        # 局部变量 final：所有处理阶段最终合并得到的结果。
        final = outputs[-1]
        # 局部变量 refused：本次回答是否因证据不足或合规规则而拒答。
        refused = final["refused"]
        # 局部变量 final_retrieved_sources：最终回答流程保留的检索来源记录。
        final_retrieved_sources = final.get(
            "retrieved_sources", final.get("citations", [])
        )
        # 局部变量 final_citations：最终回答中采用的引用清单。
        final_citations = final.get("citations", [])
        # 局部变量 judge_reason：评测模型给出的判定理由。
        judge_reason: str | None = None
        # 局部变量 matched_points：本次答案命中的预期要点集合。
        matched_points: list[str] = []
        # 局部变量 missing_points：证据或答案尚未覆盖的预期要点列表。
        missing_points: list[str] = []
        # 局部变量 contradictions：答案中与证据或预期相冲突的内容。
        contradictions: list[str] = []
        # 局部变量 context_retained：多轮会话上下文是否正确保留。
        context_retained: bool | None = None
        if case["expected_type"] == "REJECT":
            # 局部变量 correct：当前回答是否符合评测预期。
            correct = refused and not final_citations
            # 局部变量 multi_turn_correct：当前多轮评估用例是否符合预期。
            multi_turn_correct = None
        else:
            # 局部变量 judgement：模型对证据支持程度的结构化判断。
            judgement = await self.judge_answer(case, final)
            correct = judgement["correct"]
            judge_reason = judgement["reason"]
            matched_points = judgement["matched_points"]
            missing_points = judgement["missing_points"]
            contradictions = judgement["contradictions"]
            context_retained = judgement["context_retained"]
            multi_turn_correct = (
                correct and judgement["context_retained"]
                if len(case["turns"]) > 1
                else None
            )

        # 局部变量 source_hit：回答引用是否命中预期来源。
        source_hit = None
        # 局部变量 retrieval_hit：检索阶段是否命中预期来源。
        retrieval_hit = None
        if case["expected_sources"]:
            source_hit = _source_matches(final_citations, case["expected_sources"])
            retrieval_hit = _source_matches(
                final_retrieved_sources, case["expected_sources"]
            )
        # 局部变量 compliance_hit：回答是否符合合规提示预期。
        compliance_hit = (
            bool(final["compliance_shown"]) if case["should_show_compliance"] else None
        )
        return {
            "case_id": case["id"],
            "status": "COMPLETED",
            "answer": final["answer"],
            "refused": refused,
            "correct": correct,
            "source_hit": source_hit,
            "retrieval_hit": retrieval_hit,
            "context_retained": context_retained,
            "multi_turn_correct": multi_turn_correct,
            "compliance_hit": compliance_hit,
            "answer_mode": final.get("answer_mode"),
            "evidence_decision": final.get("evidence_decision"),
            "judge_reason": judge_reason,
            "matched_points": matched_points,
            "missing_points": missing_points,
            "contradictions": contradictions,
            "turn_diagnostics": turn_diagnostics,
            "citations": final_citations,
            "latency_ms": latency_ms,
            "error_message": None,
        }

    async def judge_answer(
        self, case: dict[str, Any], final: dict[str, Any]
    ) -> dict[str, Any]:
        """判断模型答案是否覆盖预期要点及引用来源。

        参数：
        case：当前评测用例数据。
        final：本次流程最终整理并返回的结果。
        """
        # 局部变量 prompt_input：填充提示模板所需的输入字段集合。
        prompt_input = {
            "turns": case["turns"],
            "expected_points": case["expected_points"],
            "rewritten_question": final["rewritten_question"],
            "answer": final["answer"],
        }
        # 局部变量 chat_model：当前阶段使用的聊天模型。
        chat_model = getattr(self.chat_service, "chat_model", None)
        if chat_model is None:
            raise ModelUnavailableError("评测模型尚未配置")
        # 局部变量 prompt：发送给语言模型的提示文本或模板。
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _escape_fstring_literals(self.evaluator_prompt)),
                ("human", "{payload}"),
            ],
            template_format="f-string",
        )
        try:
            # 局部变量 value：评测模型经 JsonOutputParser 解析出的判分字典；后续读取 correct、reason 等字段并与预期要点结合形成评测结果。
            value = await (
                prompt
                | chat_model.bind(
                    response_format={"type": "json_object"},
                    temperature=0,
                )
                | JsonOutputParser()
            ).ainvoke({"payload": json.dumps(prompt_input, ensure_ascii=False)})
        except ModelUnavailableError:
            raise
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except OutputParserException as exc:
            raise TypeError("评测器未返回有效 JSON") from exc
        except Exception as exc:
            raise ModelUnavailableError("模型服务暂不可用") from exc
        if not isinstance(value, dict) or not isinstance(value.get("correct"), bool):
            raise TypeError("评测器未返回有效的 correct 判断")
        # 局部变量 context_retained：多轮会话上下文是否正确保留。
        context_retained = value.get("context_retained", len(case["turns"]) == 1)
        if not isinstance(context_retained, bool):
            raise TypeError("评测器未返回有效的 context_retained 判断")
        # 局部变量 matched_points：本次答案命中的预期要点集合。
        matched_points = _validated_string_list(
            value.get("matched_points", []), "matched_points"
        )
        # 局部变量 missing_points：证据或答案尚未覆盖的预期要点列表。
        missing_points = _validated_string_list(
            value.get("missing_points", []), "missing_points"
        )
        # 局部变量 contradictions：答案中与证据或预期相冲突的内容。
        contradictions = _validated_string_list(
            value.get("contradictions", []), "contradictions"
        )
        # 局部变量 reason：当前状态、分类或异常的原因说明。
        reason = value.get("reason", "")
        if not isinstance(reason, str):
            raise TypeError("评测器未返回有效的 reason 判断")
        return {
            "correct": value["correct"],
            "context_retained": context_retained,
            "matched_points": matched_points,
            "missing_points": missing_points,
            "contradictions": contradictions,
            "reason": reason,
        }

    @staticmethod
    def _system_error_message(exc: Exception) -> str:
        """将系统异常转换为评测结果中的错误文本。

        参数：
        exc：当前捕获到的异常对象。
        """
        if isinstance(exc, ModelUnavailableError):
            return "模型服务暂不可用，请检查模型配置与服务状态"
        if isinstance(exc, EmbeddingUnavailableError):
            return "Embedding 服务暂不可用，请检查模型配置与服务状态"
        if isinstance(exc, VectorStoreNotInitialized):
            return "知识库尚未就绪，请先导入资料并构建索引"
        if isinstance(exc, VectorStoreSignatureMismatch):
            return "向量索引与当前 Embedding 配置不兼容，请重建索引"
        if isinstance(exc, VectorStorePersistenceError):
            return "知识库索引不可用"
        return str(exc) or "评测任务失败"


def _escape_fstring_literals(value: str) -> str:
    """转义答案模板中的字面花括号。

    参数：
    value：需要转换或校验的输入值。
    """
    return value.replace("{", "{{").replace("}", "}}")


def _validated_string_list(value: Any, field_name: str) -> list[str]:
    """校验并规范化非空字符串列表。

    参数：
    value：需要转换或校验的输入值。
    field_name：当前需要读取或校验的字段名称。
    """
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"评测器未返回有效的 {field_name} 列表")
    return value


def _source_matches(
    actual_sources: list[dict[str, Any]], expected_sources: list[dict[str, Any]]
) -> bool:
    """判断实际引用来源是否匹配评估用例的预期来源。

    参数：
    actual_sources：模型回答实际引用的来源集合。
    expected_sources：当前用例要求命中的资料来源。
    """
    # 局部变量 actual：实际运行得到的值或结果。
    # 局部变量 expected：评测或校验预期值。
    return any(
        actual.get("file_name") == expected.get("file_name")
        and (
            expected.get("chunk_no") is None
            or actual.get("chunk_no") == expected.get("chunk_no")
        )
        for actual in actual_sources
        for expected in expected_sources
    )


def _failed_result(case_id: int, error_message: str) -> dict[str, Any]:
    """构造一条失败的评估用例结果记录。

    参数：
    case_id：目标评估用例标识。
    error_message：需要记录或返回的错误说明。
    """
    return {
        "case_id": case_id,
        "status": "FAILED",
        "answer": None,
        "refused": None,
        "correct": None,
        "source_hit": None,
        "retrieval_hit": None,
        "context_retained": None,
        "multi_turn_correct": None,
        "compliance_hit": None,
        "answer_mode": None,
        "evidence_decision": None,
        "judge_reason": None,
        "matched_points": [],
        "missing_points": [],
        "contradictions": [],
        "turn_diagnostics": [],
        "citations": [],
        "latency_ms": None,
        "error_message": error_message[:500],
    }


def _calculate_metrics(
    cases: list[dict[str, Any]], results: dict[int, dict[str, Any]]
) -> dict[str, float | None]:
    """根据评估结果计算准确率、拒答率和引用指标。

    参数：
    results：本次处理生成的结果列表。
    cases：当前任务使用的评估用例集合。
    """
    def rate(successes: int, total: int) -> float | None:
        """计算成功数占总数的比例。

        参数：
        successes：成功的数量。
        total：记录总数或待处理项总数。
        """
        return successes / total if total else None

    # 局部变量 completed：当前任务是否已达到终态。
    # 局部变量 case_id：当前评估用例的数据库标识。
    # 局部变量 result：单个 case_id 对应的评测结果；此处只保留 status 为 COMPLETED 的结果，用于恢复或汇总已完成用例。
    completed = {
        case_id: result
        for case_id, result in results.items()
        if result["status"] == "COMPLETED"
    }
    # 局部变量 answers：模型回答或评估结果中的答案集合。
    # 局部变量 case：当前评测用例数据，包含问题轮次和预期要点/来源；本流程据这些字段构造输入、执行判定或汇总该用例结果。
    answers = [
        case
        for case in cases
        if case["expected_type"] == "ANSWER" and case["id"] in completed
    ]
    # 局部变量 rejects：本次运行中拒答的用例结果集合。
    rejects = [
        case
        for case in cases
        if case["expected_type"] == "REJECT" and case["id"] in completed
    ]
    # 局部变量 citation_cases：用于计算引用命中率的评估结果集合。
    citation_cases = [case for case in answers if case["expected_sources"]]
    # 局部变量 compliance_cases：用于计算合规指标的评估结果集合。
    compliance_cases = [
        case
        for case in cases
        if case["should_show_compliance"] and case["id"] in completed
    ]
    # 局部变量 context_cases：用于计算上下文指标的评估结果集合。
    context_cases = [
        case
        for case in cases
        if len(case["turns"]) > 1
        and case["id"] in completed
        and completed[case["id"]]["context_retained"] is not None
    ]
    return {
        "execution_success_rate": rate(len(completed), len(cases)),
        "accuracy": rate(
            sum(completed[case["id"]]["correct"] is True for case in answers),
            len(answers),
        ),
        "reject_rate": rate(
            sum(completed[case["id"]]["correct"] is True for case in rejects),
            len(rejects),
        ),
        "refusal_rate": rate(
            sum(result["refused"] is True for result in completed.values()),
            len(completed),
        ),
        "citation_hit_rate": rate(
            sum(completed[case["id"]]["source_hit"] is True for case in citation_cases),
            len(citation_cases),
        ),
        "retrieval_hit_rate": rate(
            sum(
                completed[case["id"]]["retrieval_hit"] is True
                for case in citation_cases
            ),
            len(citation_cases),
        ),
        "context_retention_rate": rate(
            sum(
                completed[case["id"]]["context_retained"] is True
                for case in context_cases
            ),
            len(context_cases),
        ),
        "multi_turn_pass_rate": rate(
            sum(
                completed[case["id"]]["multi_turn_correct"] is True
                for case in context_cases
            ),
            len(context_cases),
        ),
        "compliance_hit_rate": rate(
            sum(
                completed[case["id"]]["compliance_hit"] is True
                for case in compliance_cases
            ),
            len(compliance_cases),
        ),
    }
