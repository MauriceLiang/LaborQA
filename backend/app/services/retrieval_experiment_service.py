# 文件作用：编排检索策略实验、参数变体运行、评价和结果对比。
"""在隔离的临时索引和配置快照上运行检索对比实验，记录逐策略、逐用例的指标与失败原因，不覆盖生产索引或用户会话。"""

from __future__ import annotations

import logging
from inspect import signature
from typing import Any

from langchain_core.documents import Document

from app.core.config import Settings, settings
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.rag.ai_modes import DEFAULT_AI_MODE
from app.rag.decision import AnswerMode
from app.rag.embeddings import LangChainEmbeddingService
from app.rag.errors import (
    EmbeddingDimensionUnknown,
    EmbeddingUnavailableError,
    ModelUnavailableError,
)
from app.rag.loaders import LaborQADocumentLoader
from app.rag.runtime_config import runtime_config_snapshot
from app.rag.splitters import LegalTextSplitter
from app.repositories.document_repository import DocumentRepository
from app.repositories.evaluation_repository import EvaluationRepository
from app.repositories.retrieval_experiment_repository import (
    RetrievalExperimentRepository,
)
from app.repositories.retrieval_strategy_repository import (
    RetrievalStrategyRepository,
)
from app.schemas.contracts import (
    AiMode,
    AnswerStyle,
    EmbeddingSignature,
    EvaluationCaseOrigin,
    EvaluationCaseScope,
    EvaluationCaseStatus,
    ExperimentConfig,
    ExperimentCreate,
    RetrievalPreviewRequest,
    RetrievalStrategyCreate,
    RetrievalStrategyUpdate,
)
from app.services.chat_service import ChatService
from app.services.document_parser import ParserFactory
from app.services.evaluation_service import EvaluationService
from app.services.file_storage import DocumentConverterUnavailable, DocumentParseError
from app.services.missing_knowledge import ExecutionMode
from app.services.rerank import RerankService
from app.services.retrieval import RetrievalService
from app.services.vector_store import (
    VectorStoreNotInitialized,
    VectorStorePersistenceError,
    VectorStoreService,
    VectorStoreSignatureMismatch,
)

# 检索实验创建、执行和结果统计过程使用的日志记录器。
logger = logging.getLogger(__name__)
# 进程重启后用于说明检索实验任务中断的错误文本。
_RESTART_ERROR = "服务重启导致任务中断"


class _ExperimentChunkRepository:
    """为单个实验配置提供内存中的文本块读取适配，使 RetrievalService 能复用生产检索流程；数据来自实验索引快照，不查询或修改生产 SQLite。"""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        """初始化检索实验服务及策略、实验和评估数据依赖。

        参数：
        chunks：文本分块记录集合。
        """
        # 成员变量 chunks：当前文档或任务涉及的文本分块集合。
        self.chunks = chunks

    def list_success_chunks(self) -> list[dict[str, Any]]:
        """列出成功导入文档的有效分块。"""
        return self.chunks

    def get_chunks_by_ids(self, chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
        """按分块标识获取文本内容。

        参数：
        chunk_ids：需要查询或更新的文本分块标识列表。
        """
        # 局部变量 requested_ids：本次接口或仓库调用指定的资源 ID 列表。
        # 局部变量 chunk_id：文本分块的唯一标识。
        requested_ids = {int(chunk_id) for chunk_id in chunk_ids}
        # 局部变量 chunk：当前文本分块记录。
        return {
            int(chunk["id"]): chunk
            for chunk in self.chunks
            if int(chunk["id"]) in requested_ids
        }

    def get_neighbor_chunks(
        self,
        document_id: int,
        chunk_no: int,
        *,
        radius: int = 1,
    ) -> list[dict[str, Any]]:
        """获取指定分块附近的相邻文本块。

        参数：
        document_id：文档记录的唯一标识。
        chunk_no：文档中从 1 开始的文本块序号。
        radius：锚点前后需要读取的相邻文本块数量。
        """
        if radius < 0:
            raise ValueError("radius must not be negative")
        # 局部变量 chunk：当前文本分块记录。
        return sorted(
            (
                chunk
                for chunk in self.chunks
                if int(chunk["document_id"]) == int(document_id)
                and abs(int(chunk["chunk_no"]) - int(chunk_no)) <= radius
            ),
            key=lambda chunk: (int(chunk["chunk_no"]), int(chunk["id"])),
        )


class RetrievalExperimentService:
    def __init__(
        self,
        chat_service: ChatService,
        config: Settings = settings,
        *,
        document_repository: DocumentRepository | None = None,
        evaluation_repository: EvaluationRepository | None = None,
        repository: RetrievalExperimentRepository | None = None,
        strategy_repository: RetrievalStrategyRepository | None = None,
        embedding_service: LangChainEmbeddingService | None = None,
        parser: type[ParserFactory] = ParserFactory,
        evaluation_service: EvaluationService | None = None,
    ) -> None:
        """初始化检索实验服务及策略、实验和评估数据依赖。

        参数：
        config：当前应用或实验配置。
        repository：负责当前业务数据持久化的数据仓库实例。
        chat_service：提供聊天和会话业务能力的服务实例。
        document_repository：负责文档数据读写的数据仓库。
        evaluation_repository：负责评估用例和运行结果持久化的数据仓库。
        strategy_repository：负责检索策略及其历史版本持久化的数据仓库。
        embedding_service：为文本生成向量的嵌入服务。
        parser：负责读取和解析不同文档格式的组件。
        evaluation_service：提供评估用例及评测任务能力的服务。
        """
        # 成员变量 chat_service：处理聊天请求的业务服务实例。
        self.chat_service = chat_service
        # 成员变量 config：应用级 Settings 基础配置，提供模型、数据库和索引目录等默认值；创建实验时据此生成独立参数快照和隔离索引路径。
        self.config = config
        # 成员变量 document_repository：负责文档及分块持久化的数据仓库。
        self.document_repository = document_repository or DocumentRepository(
            config.database_path
        )
        # 成员变量 evaluation_service：负责评估用例和任务的业务服务。
        self.evaluation_service = evaluation_service or EvaluationService(
            chat_service,
            config,
            repository=evaluation_repository,
        )
        # 成员变量 repository：负责当前业务数据持久化的数据仓库。
        self.repository = repository or RetrievalExperimentRepository(
            config.database_path
        )
        # 成员变量 strategy_repository：负责策略和版本持久化的数据仓库。
        self.strategy_repository = strategy_repository or RetrievalStrategyRepository(
            config.database_path
        )
        # 成员变量 embedding_service：生成文本向量的嵌入服务实例。
        self.embedding_service = embedding_service or (
            chat_service.retrieval_service.embedding_service
        )
        # 成员变量 production_vector_store：生产问答流程共用的向量存储实例。
        self.production_vector_store = chat_service.retrieval_service.vector_store
        # 成员变量 parser：负责解析文档内容并返回文本及元数据的解析器实例。
        self.parser = parser

    def initialize(self) -> None:
        """初始化实验和策略仓库并准备内置策略。"""
        self.repository.recover_interrupted_experiments()
        self.strategy_repository.seed_builtins(_builtin_strategies())

    def list_strategies(
        self, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        """按状态和名称查询检索策略。

        参数：
        include_archived：查询结果是否包括已归档记录。
        """
        return self.strategy_repository.list_strategies(
            include_archived=include_archived
        )

    def create_strategy(self, payload: RetrievalStrategyCreate) -> dict[str, Any]:
        """创建可复用的检索策略。

        参数：
        payload：经过校验的业务输入对象。
        """
        return self.strategy_repository.create_strategy(
            payload.name,
            payload.description,
            payload.config.model_dump(mode="json"),
        )

    def update_strategy(
        self, strategy_id: int, payload: RetrievalStrategyUpdate
    ) -> dict[str, Any]:
        """更新检索策略并保存版本快照。

        参数：
        strategy_id：目标检索策略标识。
        payload：经过校验的业务输入对象。
        """
        # 局部变量 current：按 strategy_id 从仓储重新读取的最新策略记录，包含内置、归档、启用、版本及参数字段；后续据此校验当前状态并决定能否编辑或变更生命周期。
        current = self.strategy_repository.get_strategy(strategy_id)
        if current is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        if current["is_builtin"]:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "内置检索策略不可直接修改，请先复制后编辑",
                http_status=409,
            )
        # 局部变量 updated：本次更新后得到的业务对象。
        updated = self.strategy_repository.update_strategy(
            strategy_id,
            payload.name,
            payload.description,
            payload.config.model_dump(mode="json"),
        )
        if updated is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        return updated

    def list_strategy_versions(self, strategy_id: int) -> list[dict[str, Any]]:
        """查询指定策略的历史版本。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.strategy_repository.get_strategy(strategy_id)
        if current is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        return self.strategy_repository.list_versions(strategy_id)

    def restore_strategy_version(
        self, strategy_id: int, version: int
    ) -> dict[str, Any]:
        """恢复策略到指定历史版本。

        参数：
        strategy_id：目标检索策略标识。
        version：目标配置版本号。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.strategy_repository.get_strategy(strategy_id)
        if current is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        if current["is_builtin"]:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "内置检索策略不可恢复历史版本",
                http_status=409,
            )
        # 局部变量 restored：恢复操作返回的最新资源记录。
        restored = self.strategy_repository.restore_version(strategy_id, version)
        if restored is None:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "策略版本不存在",
                http_status=404,
            )
        return restored

    def set_strategy_active(self, strategy_id: int, active: bool) -> dict[str, Any]:
        """启用或停用检索策略。

        参数：
        strategy_id：目标检索策略标识。
        active：策略是否启用。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.strategy_repository.get_strategy(strategy_id)
        if current is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        if current["is_builtin"]:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "内置检索策略始终启用，不支持停用",
                http_status=409,
            )
        if current["archived_at"] is not None:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "已归档策略请先恢复后再启用",
                http_status=409,
            )
        # 局部变量 updated：本次更新后得到的业务对象。
        updated = self.strategy_repository.set_active(strategy_id, active)
        if updated is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        return updated

    def archive_strategy(self, strategy_id: int) -> dict[str, Any]:
        """归档检索策略。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.strategy_repository.get_strategy(strategy_id)
        if current is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        if current["is_builtin"]:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "内置检索策略不可归档",
                http_status=409,
            )
        # 局部变量 archived：归档操作返回的最新资源记录。
        archived = self.strategy_repository.archive_strategy(strategy_id)
        if archived is None:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "检索策略已经归档",
                http_status=409,
            )
        return archived

    def restore_strategy(self, strategy_id: int) -> dict[str, Any]:
        """恢复已归档检索策略。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.strategy_repository.get_strategy(strategy_id)
        if current is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        if current["is_builtin"]:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "内置检索策略不需要恢复",
                http_status=409,
            )
        # 局部变量 restored：恢复操作返回的最新资源记录。
        restored = self.strategy_repository.restore_strategy(strategy_id)
        if restored is None:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "检索策略未处于归档状态",
                http_status=409,
            )
        return restored

    def delete_strategy(self, strategy_id: int) -> None:
        """删除检索策略。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 current：当前处理的对象或状态快照。
        current = self.strategy_repository.get_strategy(strategy_id)
        if current is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        if current["is_builtin"]:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "内置检索策略不可删除",
                http_status=409,
            )
        self.strategy_repository.delete_strategy(strategy_id)

    def archive_experiment(self, experiment_id: int) -> dict[str, Any]:
        """归档检索实验。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 experiment：按 ID 从实验仓储读取的持久化记录，含运行状态、配置快照和归档信息；后续先确认资源存在，再校验是否允许当前生命周期操作。
        experiment = self.repository.get_experiment(experiment_id)
        if experiment is None:
            raise AppError(
                ErrorCode.EXPERIMENT_NOT_FOUND,
                "检索实验不存在",
                http_status=404,
            )
        if experiment["status"] in ("PENDING", "RUNNING"):
            raise AppError(
                ErrorCode.JOB_ALREADY_RUNNING,
                "实验运行中，完成后才能归档",
                http_status=409,
            )
        # 局部变量 archived：归档操作返回的最新资源记录。
        archived = self.repository.archive_experiment(experiment_id)
        if archived is None:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "实验已经归档",
                http_status=409,
            )
        return archived

    def restore_experiment(self, experiment_id: int) -> dict[str, Any]:
        """恢复已归档的检索实验。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 experiment：当前检索实验记录或运行配置。
        experiment = self.repository.get_experiment(experiment_id)
        if experiment is None:
            raise AppError(
                ErrorCode.EXPERIMENT_NOT_FOUND,
                "检索实验不存在",
                http_status=404,
            )
        # 局部变量 restored：恢复操作返回的最新资源记录。
        restored = self.repository.restore_experiment(experiment_id)
        if restored is None:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "实验未处于归档状态",
                http_status=409,
            )
        return restored

    def delete_experiment(self, experiment_id: int) -> None:
        """删除检索实验和结果。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 experiment：当前检索实验记录或运行配置。
        experiment = self.repository.get_experiment(experiment_id)
        if experiment is None:
            raise AppError(
                ErrorCode.EXPERIMENT_NOT_FOUND,
                "检索实验不存在",
                http_status=404,
            )
        if experiment["status"] in ("PENDING", "RUNNING"):
            raise AppError(
                ErrorCode.JOB_ALREADY_RUNNING,
                "实验运行中，完成后才能删除",
                http_status=409,
            )
        self.repository.delete_experiment(experiment_id)
        # 局部变量 experiment_dir：检索实验相关文件存储目录。
        experiment_dir = (
            self.config.faiss_path / "experiments" / f"experiment_{experiment_id}"
        )
        try:
            import shutil

            shutil.rmtree(experiment_dir, ignore_errors=True)
        except OSError:
            logger.warning(
                "Could not remove retrieval experiment index directory %s",
                experiment_dir,
                exc_info=True,
            )

    def copy_experiment(self, experiment_id: int, name: str) -> dict[str, Any]:
        """复制现有实验配置并创建新实验。

        参数：
        experiment_id：目标检索实验标识。
        name：资源名称或模型名称。
        """
        # 局部变量 experiment：当前检索实验记录或运行配置。
        experiment = self.repository.get_experiment(experiment_id)
        if experiment is None:
            raise AppError(
                ErrorCode.EXPERIMENT_NOT_FOUND,
                "检索实验不存在",
                http_status=404,
            )
        # 局部变量 snapshot：任务创建时保存的不可变输入快照。
        snapshot = experiment["snapshot"]
        # 局部变量 case_count：本次评测用例数量。
        case_count = len(snapshot.get("case_snapshots", [])) or int(
            experiment.get("case_count", 0)
        )
        if case_count <= 0:
            raise AppError(ErrorCode.INVALID_REQUEST, "实验没有可复制的评测用例", 400)
        return self.repository.create_experiment(name, case_count, snapshot)

    def _load_runnable_strategy(self, strategy_id: int) -> dict[str, Any]:
        """读取并校验可参与实验运行的策略配置。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 strategy：当前检索策略及其参数配置。
        strategy = self.strategy_repository.get_strategy(strategy_id)
        if strategy is None:
            raise AppError(
                ErrorCode.RETRIEVAL_STRATEGY_NOT_FOUND,
                "检索策略不存在",
                http_status=404,
            )
        if strategy["archived_at"] is not None:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "检索策略已归档，无法运行",
                http_status=409,
            )
        if not strategy["is_active"]:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "检索策略已停用，无法运行",
                http_status=409,
            )
        return strategy

    async def preview(self, payload: RetrievalPreviewRequest) -> dict[str, Any]:
        """使用指定策略和当前生产索引对单个问题执行一次完整检索与回答，汇总阶段轨迹、证据和限制说明，不创建持久化实验运行。

        参数：
        payload：经过校验的请求数据或持久化内容。
        """

        # 局部变量 strategy：当前检索策略及其参数配置。
        strategy = self._load_runnable_strategy(payload.strategy_id)
        # 局部变量 strategy_config：当前运行使用的检索策略参数。
        strategy_config = ExperimentConfig.model_validate(strategy["config"])
        if not self.document_repository.list_success_documents():
            raise AppError(
                ErrorCode.KNOWLEDGE_BASE_NOT_READY,
                "没有可用于试跑的成功资料",
                http_status=503,
            )
        # 局部变量 runtime_config：覆盖本次实验参数的运行时配置副本。
        runtime_config = _runtime_config(self.config, strategy_config)
        # 局部变量 retrieval_service：负责向量召回和证据整理的检索服务。
        retrieval_service = RetrievalService(
            runtime_config,
            repository=self.document_repository,
            embedding_service=self.embedding_service,
            vector_store=self.production_vector_store,
            rerank_service=RerankService(runtime_config),
        )
        # 局部变量 chat_models：各处理阶段使用的聊天模型集合。
        chat_models = getattr(self.chat_service, "chat_models", None)
        # 局部变量 chat_kwargs：创建聊天模型时传入的关键字参数。
        chat_kwargs: dict[str, Any] = {
            "missing_knowledge_service": self.chat_service.missing_knowledge_service,
        }
        if chat_models is not None and self.chat_service.chat_model is chat_models.get(
            "default"
        ):
            chat_kwargs["chat_models"] = chat_models
        else:
            chat_kwargs["chat_model"] = self.chat_service.chat_model
        # 局部变量 variant_chat：针对当前策略参数创建的聊天服务。
        variant_chat = ChatService(
            self.chat_service.session_service,
            retrieval_service,
            runtime_config,
            **chat_kwargs,
        )
        try:
            # 局部变量 output：当前流程生成的输出内容。
            output = await variant_chat.answer_once(
                payload.question,
                [],
                payload.answer_style,
                mode=ExecutionMode.EXPERIMENT,
                ai_mode=payload.ai_mode,
            )
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except ModelUnavailableError as exc:
            raise AppError(
                ErrorCode.MODEL_UNAVAILABLE,
                "模型暂不可用，暂时无法完成试跑",
                http_status=503,
            ) from exc
        except EmbeddingUnavailableError as exc:
            raise AppError(
                ErrorCode.EMBEDDING_UNAVAILABLE,
                "向量服务暂不可用，暂时无法完成试跑",
                http_status=503,
            ) from exc
        except (
            VectorStoreNotInitialized,
            VectorStorePersistenceError,
            VectorStoreSignatureMismatch,
        ) as exc:
            raise AppError(
                ErrorCode.KNOWLEDGE_BASE_NOT_READY,
                "生产索引尚未就绪，暂时无法完成试跑",
                http_status=503,
            ) from exc

        # 局部变量 retrieved_sources：检索阶段返回的来源列表。
        retrieved_sources = output.get("retrieved_sources", [])
        # 局部变量 primary_sources：主要检索命中的资料来源。
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        primary_sources = [
            item
            for item in retrieved_sources
            if item.get("evidence_origin", "RETRIEVED") == "RETRIEVED"
        ]
        # 局部变量 neighbor_sources：相邻分块补充的来源记录。
        neighbor_sources = [
            item
            for item in retrieved_sources
            if item.get("evidence_origin") == "NEIGHBOR"
        ]
        # 局部变量 refused：本次回答是否因证据不足或合规规则而拒答。
        refused = bool(output["refused"])
        # 局部变量 answer_mode：本次问题采用的回答处理路径。
        answer_mode = str(output["answer_mode"])
        direct_response = answer_mode == AnswerMode.DIRECT_RESPONSE.value
        # 局部变量 evidence_decision：依据检索证据得到的回答路径判定。
        evidence_decision = (
            str(output["evidence_decision"])
            if output["evidence_decision"] is not None
            else None
        )
        # 局部变量 evidence_reason：证据充分性判定的解释文本。
        evidence_reason = (
            str(output["evidence_reason"])
            if output["evidence_reason"] is not None
            else None
        )
        # 局部变量 supported_points：已被证据或答案支持的预期要点。
        supported_points = [str(item) for item in output["supported_points"]]
        # 局部变量 missing_points：证据或答案尚未覆盖的预期要点列表。
        missing_points = [str(item) for item in output["missing_points"]]
        # 局部变量 tool_execution：本次工具调用的执行结果记录。
        tool_execution = output.get("tool_execution")
        # 局部变量 max_retrieval_score：当前候选集合中的最高检索分数。
        max_retrieval_score = max(
            (float(item["retrieval_score"]) for item in retrieved_sources),
            default=0.0,
        )
        # 局部变量 threshold_passed：候选结果是否通过最低分数阈值。
        threshold_passed = bool(retrieved_sources) and (
            max_retrieval_score >= strategy_config.score_threshold
        )
        # 局部变量 score_summary：当前评分结果的汇总信息。
        score_summary = (
            "；".join(
                f"#{int(item['rank_no'])} {item['file_name']} "
                f"检索分 {float(item['retrieval_score']):.3f}"
                for item in retrieved_sources
            )
            or "没有候选片段"
        )
        limitations = (
            ["普通交互使用固定模板，不执行改写、检索、重排或证据判断"]
            if direct_response
            else [
                "快速试跑复用生产索引",
                "Chunk Size 与 Chunk Overlap 不会重新切分",
            ]
        )
        return {
            "strategy_id": int(strategy["id"]),
            "strategy_name": strategy["name"],
            "strategy_version": int(strategy["version"]),
            "index_mode": (
                "NOT_APPLICABLE_DIRECT_RESPONSE"
                if direct_response
                else "PRODUCTION_INDEX_REUSE"
            ),
            "limitations": limitations,
            "question": payload.question,
            "rewritten_question": output["rewritten_question"],
            "answer": output["answer"],
            "refused": refused,
            "answer_mode": answer_mode,
            "evidence_decision": evidence_decision,
            "evidence_reason": evidence_reason,
            "supported_points": supported_points,
            "missing_points": missing_points,
            "tool_execution": tool_execution,
            "retrieval_ms": output["retrieval_ms"],
            "retrieved_sources": retrieved_sources,
            "citations": output["citations"],
            "config": strategy_config.model_dump(mode="json"),
            "trace": [
                {
                    "stage": "问题改写",
                    "status": "skipped" if direct_response else "completed",
                    "detail": (
                        "普通交互直接使用固定回复，未改写问题"
                        if direct_response
                        else f"检索查询：{output['rewritten_question']}"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "文本分块",
                    "status": "skipped" if direct_response else "completed",
                    "detail": (
                        "普通交互不涉及资料分块"
                        if direct_response
                        else "试跑复用生产索引；正式实验会按策略重新分块"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "初步召回",
                    "status": (
                        "completed"
                        if not direct_response and retrieved_sources
                        else "skipped"
                    ),
                    "detail": (
                        "普通交互未执行向量检索"
                        if direct_response
                        else f"FAISS 候选上限 {strategy_config.candidate_k} 个片段"
                    ),
                    "duration_ms": output["retrieval_ms"],
                },
                {
                    "stage": "轻量重排",
                    "status": "completed"
                    if not direct_response
                    and strategy_config.lightweight_rerank_enabled
                    else "skipped",
                    "detail": (
                        "普通交互未执行重排"
                        if direct_response
                        else "使用检索分、法律术语命中和资料来源优先级排序"
                        if strategy_config.lightweight_rerank_enabled
                        else "当前策略未启用轻量重排"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "重排",
                    "status": "completed"
                    if not direct_response and strategy_config.rerank_enabled
                    else "skipped",
                    "detail": (
                        "普通交互未执行重排"
                        if direct_response
                        else f"已启用旧 CrossEncoder，Top-N {strategy_config.rerank_top_n}"
                        if strategy_config.rerank_enabled
                        else "未启用旧 CrossEncoder"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "最终 Top-K",
                    "status": (
                        "completed"
                        if not direct_response and primary_sources
                        else "skipped"
                    ),
                    "detail": (
                        "普通交互未执行 Top-K 筛选"
                        if direct_response
                        else f"保留主证据 {len(primary_sources)} 个，目标 Top-K {strategy_config.top_k}"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "上下文扩展",
                    "status": (
                        "completed"
                        if not direct_response and neighbor_sources
                        else "skipped"
                    ),
                    "detail": (
                        "普通交互未扩展检索上下文"
                        if direct_response
                        else f"扩展 {len(neighbor_sources)} 个同文档邻接片段，证据上限 "
                        f"{strategy_config.evidence_limit}"
                        if neighbor_sources
                        else "没有新增邻接片段"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "相似度阈值",
                    "status": (
                        "completed"
                        if not direct_response and retrieved_sources
                        else "skipped"
                    ),
                    "detail": (
                        "普通交互未计算检索相似度"
                        if direct_response
                        else f"候选分数：{score_summary}；最高分 {max_retrieval_score:.3f}，"
                        f"当前阈值 {strategy_config.score_threshold:.2f}；"
                        f"阈值{'通过' if threshold_passed else '未通过'}"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "证据充分性",
                    "status": (
                        "completed"
                        if not direct_response and threshold_passed
                        else "skipped"
                    ),
                    "detail": (
                        "普通交互没有执行证据判断"
                        if direct_response
                        else f"判定 {evidence_decision}；支持要点："
                        f"{'、'.join(supported_points) or '无'}；缺失要点："
                        f"{'、'.join(missing_points) or '无'}；{evidence_reason}"
                        if threshold_passed
                        else "相似度阈值未通过，未执行模型证据判断"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "Tool 决策",
                    "status": (
                        "completed"
                        if not direct_response and tool_execution
                        else "skipped"
                    ),
                    "detail": (
                        "普通交互不执行业务工具"
                        if direct_response
                        else "命中材料清单工具，争议类型："
                        f"{tool_execution['input']['dispute_type']}"
                        if tool_execution
                        else "未命中材料清单工具"
                    ),
                    "duration_ms": None,
                },
                {
                    "stage": "回答模式",
                    "status": "completed",
                    "detail": f"{answer_mode}；已生成本次试跑回答",
                    "duration_ms": None,
                },
            ],
        }

    def create_experiment(self, payload: ExperimentCreate) -> dict[str, Any]:
        """校验策略和用例范围，保存实验快照并启动后台任务。

        参数：
        payload：经过校验的业务输入对象。
        """
        # 局部变量 strategies：本次实验可用的检索策略集合。
        # 局部变量 strategy_id：当前检索策略的数据库标识。
        strategies = [
            self._load_runnable_strategy(strategy_id)
            for strategy_id in payload.strategy_ids
        ]
        # 局部变量 strategy_snapshots：本次实验引用的策略配置版本快照。
        # 局部变量 strategy：当前检索策略及其参数配置。
        strategy_snapshots = [
            {
                "strategy_id": int(strategy["id"]),
                "name": strategy["name"],
                "version": int(strategy["version"]),
                "config": ExperimentConfig.model_validate(
                    strategy["config"]
                ).model_dump(mode="json"),
            }
            for strategy in strategies
        ]
        if payload.case_scope is EvaluationCaseScope.BUILTIN_BASELINE:
            # 局部变量 cases：本次查询或运行使用的评估用例集合。
            cases, _ = self.evaluation_service.repository.list_cases(
                page=1,
                size=100,
                topic=None,
                expected_type=None,
                is_multi_turn=None,
                origin=EvaluationCaseOrigin.BUILTIN.value,
                status=EvaluationCaseStatus.ACTIVE.value,
                include_archived=False,
            )
        elif payload.case_scope is EvaluationCaseScope.ALL_ACTIVE:
            cases = self.evaluation_service.repository.get_cases()
        else:
            cases = self.evaluation_service.repository.get_cases(payload.case_ids)
        if payload.case_scope is EvaluationCaseScope.SELECTED and (
            payload.case_ids is None or len(cases) != len(payload.case_ids)
        ):
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "评测用例不存在",
                http_status=400,
            )
        if not cases:
            raise AppError(ErrorCode.INVALID_REQUEST, "实验用例集为空", 400)
        if not self.document_repository.list_success_documents():
            raise AppError(
                ErrorCode.KNOWLEDGE_BASE_NOT_READY,
                "没有可用于检索实验的成功资料",
                http_status=503,
            )

        # 局部变量 signature：描述嵌入模型和向量维度的配置签名。
        signature = self._embedding_signature()
        # 局部变量 snapshot：任务创建时保存的不可变输入快照。
        # 局部变量 case：当前评测用例数据，包含问题轮次和预期要点/来源；本流程据这些字段构造输入、执行判定或汇总该用例结果。
        snapshot = {
            "answer_style": payload.answer_style.value,
            "ai_mode": payload.ai_mode.value,
            "case_scope": payload.case_scope.value,
            "case_ids": [case["id"] for case in cases],
            "case_snapshots": [
                {
                    "id": case["id"],
                    "version": case.get("version", 1),
                    "topic": case["topic"],
                    "expected_type": case["expected_type"],
                    "turns": case["turns"],
                    "expected_points": case["expected_points"],
                    "expected_sources": case["expected_sources"],
                    "should_show_compliance": case["should_show_compliance"],
                }
                for case in cases
            ],
            "strategy_snapshots": strategy_snapshots,
            "embedding_signature": signature.model_dump(mode="json"),
            "runtime_config": runtime_config_snapshot(
                self.config, ai_mode=payload.ai_mode
            ),
        }
        return self.repository.create_experiment(
            payload.name,
            len(cases),
            snapshot,
        )

    def list_experiments(
        self,
        *,
        page: int,
        size: int,
        status: str | None,
        include_archived: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """按分页和归档条件查询检索实验。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        include_archived：查询结果是否包含已归档记录。
        """
        return self.repository.list_experiments(
            page=page,
            size=size,
            status=status,
            include_archived=include_archived,
        )

    def export_experiment(self, experiment_id: int) -> str:
        """将实验配置和结果导出为 CSV。

        参数：
        experiment_id：目标检索实验标识。
        """
        import csv
        import io

        # 局部变量 experiment：当前检索实验记录或运行配置。
        experiment = self.repository.get_experiment(experiment_id)
        if experiment is None:
            raise AppError(
                ErrorCode.EXPERIMENT_NOT_FOUND,
                "检索实验不存在",
                http_status=404,
            )
        # 局部变量 output：当前流程生成的输出内容。
        output = io.StringIO()
        # 局部变量 writer：当前用于写入文件或序列化输出的对象。
        writer = csv.writer(output)
        writer.writerow(
            [
                "实验名称",
                "配置序号",
                "用例 ID",
                "状态",
                "引用命中",
                "回答正确",
                "拒答",
                "检索耗时(ms)",
                "引用文件",
            ]
        )
        # 局部变量 result：当前实验的一条用例结果，包含检索来源、命中情况和耗时等字段；导出时会将其整理为 CSV 中的一行。
        for result in experiment["results"]:
            # 局部变量 source_files：本次扫描或导入的源文件列表。
            # 局部变量 source：文档、引用或记录的来源信息。
            source_files = "、".join(
                dict.fromkeys(
                    str(source.get("file_name", ""))
                    for source in result["retrieved_sources"]
                    if source.get("file_name")
                )
            )
            writer.writerow(
                [
                    experiment["name"],
                    result["config_index"] + 1,
                    result["case_id"],
                    result["status"],
                    _export_bool(result["source_hit"]),
                    _export_bool(result["correct"]),
                    _export_bool(result["refused"]),
                    result["retrieval_ms"]
                    if result["retrieval_ms"] is not None
                    else "",
                    source_files,
                ]
            )
        return output.getvalue()

    def get_experiment(self, experiment_id: int) -> dict[str, Any]:
        """读取实验详情、策略快照和汇总结果。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 experiment：当前检索实验记录或运行配置。
        experiment = self.repository.get_experiment(experiment_id)
        if experiment is None:
            raise AppError(
                ErrorCode.EXPERIMENT_NOT_FOUND,
                "检索实验不存在",
                http_status=404,
            )
        # 局部变量 cases：本次查询或运行使用的评估用例集合。
        cases = _snapshot_cases(
            experiment["snapshot"], self.evaluation_service.repository
        )
        # 局部变量 configs：本次比较的检索配置列表。
        configs = _snapshot_configs(experiment["snapshot"])
        # 局部变量 config_results：各检索配置聚合计算得到的实验结果。
        config_results = _calculate_config_results(
            cases,
            configs,
            experiment["results"],
        )
        return {
            **experiment,
            "embedding_signature": experiment["snapshot"]["embedding_signature"],
            "runtime_config": {
                **runtime_config_snapshot(
                    self.config,
                    ai_mode=AiMode(
                        experiment["snapshot"].get("ai_mode", DEFAULT_AI_MODE.value)
                    ),
                ),
                **experiment["snapshot"].get("runtime_config", {}),
            },
            "case_count": len(cases),
            "strategy_snapshots": _strategy_snapshots(experiment["snapshot"]),
            "config_results": config_results,
        }

    async def execute_experiment(self, experiment_id: int) -> None:
        """依次运行各策略与评测用例组合并持久化结果。

        参数：
        experiment_id：目标检索实验标识。
        """
        try:
            self.repository.set_running(experiment_id)
            # 局部变量 experiment：当前检索实验记录或运行配置。
            experiment = self.repository.get_experiment(experiment_id)
            if experiment is None:
                raise RuntimeError("检索实验不存在")
            # 局部变量 snapshot：任务创建时保存的不可变输入快照。
            snapshot = experiment["snapshot"]
            # 局部变量 ai_mode：本次回答生成使用的 AI 模式。
            ai_mode = AiMode(snapshot.get("ai_mode", DEFAULT_AI_MODE.value))
            # 局部变量 cases：本次查询或运行使用的评估用例集合。
            cases = _snapshot_cases(snapshot, self.evaluation_service.repository)
            # 局部变量 signature：描述嵌入模型和向量维度的配置签名。
            signature = EmbeddingSignature.model_validate(
                snapshot["embedding_signature"]
            )
            # 局部变量 profiles：本次运行使用的模型或检索配置集合。
            profiles: dict[
                tuple[int, int], tuple[list[dict[str, Any]], VectorStoreService]
            ] = {}
            # 局部变量 configs：本次比较的检索配置列表。
            configs = _snapshot_configs(snapshot)

            # 局部变量 config_index：当前配置在实验对照组中的序号。
            # 局部变量 config_data：从持久化快照解析出的实验配置。
            for config_index, config_data in enumerate(configs):
                # 局部变量 config：从当前实验对照组配置列表中取出的单组参数，决定该组的切分、召回、重排和证据扩展设置。
                config = ExperimentConfig.model_validate(config_data)
                # 局部变量 profile_key：当前模型配置对应的缓存或识别键。
                profile_key = (config.chunk_size, config.chunk_overlap)
                if profile_key not in profiles:
                    profiles[profile_key] = self._build_profile(
                        experiment_id, config, signature
                    )
                # 局部变量 chunks：当前文档或任务涉及的文本分块集合。
                # 局部变量 vector_store：负责 FAISS 索引加载、检索和持久化的服务。
                chunks, vector_store = profiles[profile_key]
                # 局部变量 variant_chat：针对当前策略参数创建的聊天服务。
                variant_chat = self._build_chat_service(config, chunks, vector_store)

                # 局部变量 case：当前评测用例数据，包含问题轮次和预期要点/来源；本流程据这些字段构造输入、执行判定或汇总该用例结果。
                for case in cases:
                    try:
                        # 局部变量 result：当前用例的一次检索与回答评估结果，包含执行状态、引用和指标；成功后会与配置序号一起持久化。
                        result = await self._evaluate_case(
                            variant_chat,
                            case,
                            config_index,
                            AnswerStyle(snapshot["answer_style"]),
                            ai_mode,
                        )
                    # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
                    except (
                        ModelUnavailableError,
                        EmbeddingUnavailableError,
                        EmbeddingDimensionUnknown,
                        VectorStoreNotInitialized,
                        VectorStorePersistenceError,
                        VectorStoreSignatureMismatch,
                    ) as exc:
                        logger.exception(
                            "Experiment %s config %s case %s failed",
                            experiment_id,
                            config_index,
                            case["id"],
                        )
                        result = _failed_result(
                            config_index,
                            case["id"],
                            self._system_error_message(exc),
                        )
                    except Exception as exc:
                        logger.exception(
                            "Experiment %s config %s case %s failed",
                            experiment_id,
                            config_index,
                            case["id"],
                        )
                        result = _failed_result(
                            config_index,
                            case["id"],
                            str(exc) or "检索实验用例执行失败",
                        )
                    self.repository.add_result(experiment_id, result)

            # 局部变量 detail：当前选中资源的详细信息。
            detail = self.repository.get_experiment(experiment_id)
            if detail is None:
                raise RuntimeError("检索实验在执行过程中不存在")
            # 局部变量 config_results：各检索配置聚合计算得到的实验结果。
            config_results = _calculate_config_results(
                cases,
                configs,
                detail["results"],
            )
            # 局部变量 best：当前比较结果中暂时最优的候选配置。
            best = _best_config(config_results, detail["results"])
            # 局部变量 best_metrics：最佳实验配置当前的统计指标。
            best_metrics = config_results[best] if best is not None else None
            self.repository.complete_experiment(
                experiment_id,
                best,
                best_metrics,
            )
        except Exception as exc:
            logger.exception("Retrieval experiment %s failed", experiment_id)
            try:
                self.repository.fail_experiment(
                    experiment_id, self._system_error_message(exc)
                )
            except Exception:
                logger.exception(
                    "Could not mark retrieval experiment %s as failed", experiment_id
                )

    def _embedding_signature(self) -> EmbeddingSignature:
        """读取当前嵌入模型签名以构造实验索引。"""
        # 局部变量 profile：本次模型或检索运行使用的参数配置。
        profile = self.production_vector_store.meta
        # 局部变量 dimension：嵌入向量的维度。
        dimension = None
        if profile is not None:
            # 局部变量 provider_matches：当前 Provider 与索引签名是否匹配。
            provider_matches = (
                profile.embedding_provider == self.config.embedding_provider
                and profile.embedding_model == self.config.embedding_model
                and profile.normalize_embeddings == self.config.embedding_normalize
            )
            if provider_matches:
                dimension = profile.embedding_dimension
        try:
            return self.embedding_service.signature(dimension)
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except (EmbeddingDimensionUnknown, EmbeddingUnavailableError) as exc:
            raise AppError(
                ErrorCode.EMBEDDING_UNAVAILABLE,
                "无法确定当前 Embedding 签名，请先成功导入一份资料",
                http_status=503,
            ) from exc

    def _build_profile(
        self,
        experiment_id: int,
        config: ExperimentConfig,
        signature: EmbeddingSignature,
    ) -> tuple[list[dict[str, Any]], VectorStoreService]:
        """根据实验配置创建检索运行参数。

        参数：
        experiment_id：目标检索实验标识。
        config：当前应用或实验配置。
        signature：嵌入模型配置签名。
        """
        # 局部变量 chunks：当前文档或任务涉及的文本分块集合。
        chunks = self._chunk_success_documents(config)
        if not chunks:
            raise ValueError("成功资料未生成可检索的 Chunk")

        # 局部变量 vectors：文档或查询文本对应的数值向量集合。
        vectors: list[list[float]] = []
        # 局部变量 batch_size：本次向量嵌入批次的文本数量上限。
        batch_size = self.config.embedding_batch_size
        # 局部变量 texts：本批次需要处理的文本列表。
        # 局部变量 chunk：当前文本分块记录。
        texts = [chunk["content"] for chunk in chunks]
        # 局部变量 start：当前文本块或子串的起始字符位置。
        for start in range(0, len(texts), batch_size):
            # 局部变量 batch：当前提交给嵌入模型的一批文本。
            batch = self.embedding_service.embed_documents(
                texts[start : start + batch_size]
            )
            if len(batch) != len(texts[start : start + batch_size]):
                raise EmbeddingUnavailableError("Embedding 返回数量与 Chunk 不一致")
            vectors.extend(batch)

        # 局部变量 actual_signature：当前运行模型计算得到的嵌入签名。
        actual_signature = self.embedding_service.signature(len(vectors[0]))
        if actual_signature != signature:
            raise VectorStoreSignatureMismatch("实验 Embedding 签名在执行期间发生变化")

        # 局部变量 runtime_config：覆盖本次实验参数的运行时配置副本。
        runtime_config = _runtime_config(self.config, config)
        # 局部变量 index_dir：FAISS 索引所在目录。
        index_dir = (
            self.config.faiss_path
            / "experiments"
            / f"experiment_{experiment_id}"
            / f"chunk_{config.chunk_size}_overlap_{config.chunk_overlap}"
        )
        # 局部变量 vector_store：负责 FAISS 索引加载、检索和持久化的服务。
        vector_store = VectorStoreService(
            config=runtime_config,
            embedding_service=self.embedding_service,
            index_dir=index_dir,
        )
        # 局部变量 vector：单条文本对应的数值向量。
        vector_store.rebuild_from_success_chunks(
            [
                (chunk["id"], vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            ],
            signature,
        )
        return chunks, vector_store

    def _chunk_success_documents(
        self, config: ExperimentConfig
    ) -> list[dict[str, Any]]:
        """将成功处理的分块按文档整理。

        参数：
        config：当前应用或实验配置。
        """
        # 局部变量 splitter：将文档切分为文本块的切分器实例。
        splitter = LegalTextSplitter(config.chunk_size, config.chunk_overlap)
        # 局部变量 chunks：当前文档或任务涉及的文本分块集合。
        chunks: list[dict[str, Any]] = []
        # 局部变量 document：仓储查询得到的一份已成功导入文档记录；实验流程据其 ID 和文件元数据读取源资料，再构造隔离的实验文本块。
        for document in self.document_repository.list_success_documents():
            # 局部变量 source_documents：本次批量处理使用的原始文档集合。
            source_documents = self._load_documents(document)
            # 局部变量 chunk：当前文本分块记录。
            for chunk in splitter.split_documents(source_documents):
                chunks.append(
                    {
                        "id": len(chunks) + 1,
                        "document_id": int(document["id"]),
                        "file_name": str(document["file_name"]),
                        "chunk_no": int(chunk.metadata["chunk_no"]),
                        "content": chunk.page_content,
                    }
                )
        return chunks

    def _load_documents(self, document: dict[str, Any]) -> list[Document]:
        """从分块记录构造检索使用的文档对象。

        参数：
        document：当前处理的文档对象或记录。
        """
        # 局部变量 metadata：随实验文本块保存的来源映射，包含文档 ID、文件名和类型；检索命中后用它把内存索引结果关联回原始资料。
        metadata = {
            "document_id": int(document["id"]),
            "file_name": str(document["file_name"]),
            "file_type": str(document["file_type"]),
        }
        if self.parser is ParserFactory:
            return LaborQADocumentLoader(
                document["file_path"],
                document["file_type"],
                config=self.config,
                metadata=metadata,
            ).load()

        # 局部变量 source_text：待解析的源文档正文。
        source_text = self.parser.parse(document["file_path"], document["file_type"])
        return [
            Document(
                page_content=source_text,
                metadata={
                    "source": str(document["file_path"]),
                    "page": None,
                    **metadata,
                },
            )
        ]

    def _build_chat_service(
        self,
        config: ExperimentConfig,
        chunks: list[dict[str, Any]],
        vector_store: VectorStoreService,
    ) -> ChatService:
        """为当前实验配置创建独立聊天服务。

        参数：
        config：当前应用或实验配置。
        chunks：文本分块记录集合。
        vector_store：负责向量索引读写和相似度检索的服务实例。
        """
        # 局部变量 runtime_config：覆盖本次实验参数的运行时配置副本。
        runtime_config = _runtime_config(self.config, config)
        # 局部变量 retrieval_service：负责向量召回和证据整理的检索服务。
        retrieval_service = RetrievalService(
            runtime_config,
            repository=_ExperimentChunkRepository(chunks),  # type: ignore[arg-type]
            embedding_service=self.embedding_service,
            vector_store=vector_store,
            rerank_service=RerankService(runtime_config),
        )
        # 局部变量 chat_models：各处理阶段使用的聊天模型集合。
        chat_models = getattr(self.chat_service, "chat_models", None)
        # 局部变量 chat_kwargs：创建聊天模型时传入的关键字参数。
        chat_kwargs: dict[str, Any] = {
            "missing_knowledge_service": self.chat_service.missing_knowledge_service,
        }
        if chat_models is not None:
            if self.chat_service.chat_model is not chat_models.get("default"):
                chat_kwargs["chat_model"] = self.chat_service.chat_model
            else:
                chat_kwargs["chat_models"] = chat_models
        else:
            chat_kwargs["chat_model"] = self.chat_service.chat_model
        return ChatService(
            self.chat_service.session_service,
            retrieval_service,
            runtime_config,
            **chat_kwargs,
        )

    async def _evaluate_case(
        self,
        chat_service: ChatService,
        case: dict[str, Any],
        config_index: int,
        answer_style: AnswerStyle,
        ai_mode: AiMode = DEFAULT_AI_MODE,
    ) -> dict[str, Any]:
        """使用指定检索配置运行并评估一条用例。

        参数：
        case：当前评测用例数据。
        config_index：实验配置在本次比较中的序号。
        answer_style：答案表达风格。
        chat_service：提供聊天和会话业务能力的服务实例。
        ai_mode：生成回答时采用的 AI 模式。
        """
        # 局部变量 history：当前会话的历史消息集合。
        history: list[dict[str, str]] = []
        # 局部变量 outputs：模型或工具产生的输出内容集合。
        outputs: list[dict[str, Any]] = []
        # 局部变量 citations：回答引用的来源记录列表。
        citations: list[dict[str, Any]] = []
        # 局部变量 seen_chunks：用于去重的已处理分块标识集合。
        seen_chunks: set[int] = set()
        # 局部变量 retrieval_ms：检索阶段耗时。
        retrieval_ms = 0
        # 局部变量 question：用户提交或模型改写后的问题文本。
        for question in case["turns"]:
            # 局部变量 answer_once：对当前问题单次生成的回答。
            answer_once = chat_service.answer_once
            # 局部变量 answer_kwargs：回答生成模型使用的调用参数。
            answer_kwargs: dict[str, Any] = {"mode": ExecutionMode.EXPERIMENT}
            if "ai_mode" in signature(answer_once).parameters:
                answer_kwargs["ai_mode"] = ai_mode
            # 局部变量 output：当前流程生成的输出内容。
            output = await answer_once(question, history, answer_style, **answer_kwargs)
            outputs.append(output)
            history.extend(
                [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": output["answer"]},
                ]
            )
            retrieval_ms += output["retrieval_ms"]
            # 局部变量 citation：当前回答引用的资料来源记录。
            for citation in output["citations"]:
                # 局部变量 chunk_id：文本分块的唯一标识。
                chunk_id = int(citation["chunk_id"])
                if chunk_id not in seen_chunks:
                    citations.append(citation)
                    seen_chunks.add(chunk_id)

        # 局部变量 final：所有处理阶段最终合并得到的结果。
        final = outputs[-1]
        # 局部变量 refused：本次回答是否因证据不足或合规规则而拒答。
        refused = bool(final["refused"])
        if case["expected_type"] == "REJECT":
            # 局部变量 correct：当前回答是否符合评测预期。
            correct = refused and not citations
        else:
            # 局部变量 judgement：模型对证据支持程度的结构化判断。
            judgement = await self.evaluation_service.judge_answer(case, final)
            correct = judgement["correct"]

        # 局部变量 source_hit：回答引用是否命中预期来源。
        source_hit = None
        if case["expected_sources"]:
            # 不同实验配置可能采用不同切分边界，导致同一资料的 chunk_no 变化；
            # 来源命中应按文件名等文档级身份比较，而不能要求分块序号完全相同。
            source_hit = any(
                actual["file_name"] == expected["file_name"]
                for actual in citations
                for expected in case["expected_sources"]
            )
        return {
            "config_index": config_index,
            "case_id": case["id"],
            "status": "COMPLETED",
            "retrieved_sources": citations,
            "source_hit": source_hit,
            "correct": correct,
            "refused": refused,
            "retrieval_ms": retrieval_ms,
            "error_message": None,
        }

    @staticmethod
    def _system_error_message(exc: Exception) -> str:
        """将系统异常转换为实验结果错误文本。

        参数：
        exc：当前捕获到的异常对象。
        """
        if isinstance(exc, ModelUnavailableError):
            return "模型服务暂不可用，请检查模型配置与服务状态"
        if isinstance(exc, (EmbeddingUnavailableError, EmbeddingDimensionUnknown)):
            return "Embedding 服务暂不可用，请检查模型配置与服务状态"
        if isinstance(exc, VectorStoreNotInitialized):
            return "实验向量索引尚未初始化"
        if isinstance(exc, VectorStoreSignatureMismatch):
            return "实验 Embedding 签名不一致，请检查 Embedding 配置"
        if isinstance(exc, VectorStorePersistenceError):
            return "实验向量索引不可用"
        if isinstance(exc, DocumentConverterUnavailable):
            return "实验无法解析 DOC 资料，请检查 LibreOffice 配置"
        if isinstance(exc, DocumentParseError):
            return "实验无法解析成功资料，请检查原始文件"
        return str(exc)[:500] or "检索实验失败"


def _builtin_strategies() -> list[dict[str, Any]]:
    """构造系统内置的检索策略定义。"""
    # 局部变量 strategies：本次实验可用的检索策略集合。
    strategies = [
        {
            "builtin_key": "baseline_a",
            "name": "基线 A · 平衡",
            "description": "默认分块与召回数量，适合作为起点。",
            "config": {
                "chunk_size": 600,
                "chunk_overlap": 100,
                "top_k": 5,
                "rerank_enabled": False,
                "rerank_top_n": 5,
                "score_threshold": 0.35,
            },
        },
        {
            "builtin_key": "baseline_b",
            "name": "基线 B · 小分块",
            "description": "增加分块粒度，适合短条款和精确定位。",
            "config": {
                "chunk_size": 400,
                "chunk_overlap": 100,
                "top_k": 5,
                "rerank_enabled": False,
                "rerank_top_n": 5,
                "score_threshold": 0.35,
            },
        },
        {
            "builtin_key": "baseline_c",
            "name": "基线 C · 大分块",
            "description": "保留更完整的上下文，适合跨段落问题。",
            "config": {
                "chunk_size": 800,
                "chunk_overlap": 100,
                "top_k": 5,
                "rerank_enabled": False,
                "rerank_top_n": 5,
                "score_threshold": 0.35,
            },
        },
        {
            "builtin_key": "baseline_d",
            "name": "基线 D · 快速",
            "description": "减少召回数量，优先控制响应耗时。",
            "config": {
                "chunk_size": 600,
                "chunk_overlap": 100,
                "top_k": 3,
                "rerank_enabled": False,
                "rerank_top_n": 3,
                "score_threshold": 0.35,
            },
        },
        {
            "builtin_key": "baseline_e",
            "name": "基线 E · 高召回",
            "description": "扩大候选范围，适合资料分散的问题。",
            "config": {
                "chunk_size": 600,
                "chunk_overlap": 100,
                "top_k": 8,
                "rerank_enabled": False,
                "rerank_top_n": 8,
                "score_threshold": 0.35,
            },
        },
        {
            "builtin_key": "baseline_f",
            "name": "基线 F · 重排",
            "description": "对候选片段进行重排，优先提升相关性。",
            "config": {
                "chunk_size": 600,
                "chunk_overlap": 100,
                "top_k": 5,
                "rerank_enabled": True,
                "rerank_top_n": 5,
                "score_threshold": 0.35,
            },
        },
    ]
    # 局部变量 defaults：当前配置采用的默认参数集合。
    defaults = {
        "candidate_k": 20,
        "lightweight_rerank_enabled": True,
        "neighbor_expand_top_n": 3,
        "neighbor_radius": 1,
        "evidence_limit": 10,
    }
    # 局部变量 strategy：当前检索策略及其参数配置。
    for strategy in strategies:
        strategy["config"] = {**defaults, **strategy["config"]}
    return strategies


def _runtime_config(config: Settings, experiment: ExperimentConfig) -> Settings:
    """将实验参数合并到运行时配置副本。

    参数：
    config：当前应用或实验配置。
    experiment：当前检索实验记录或输入配置。
    """
    return config.model_copy(
        update={
            "chunk_size": experiment.chunk_size,
            "chunk_overlap": experiment.chunk_overlap,
            "rag_candidate_k": experiment.candidate_k,
            "rag_top_k": experiment.top_k,
            "lightweight_rerank_enabled": experiment.lightweight_rerank_enabled,
            "rag_neighbor_expand_top_n": experiment.neighbor_expand_top_n,
            "rag_neighbor_radius": experiment.neighbor_radius,
            "rag_evidence_limit": experiment.evidence_limit,
            "rerank_enabled": experiment.rerank_enabled,
            "rerank_top_n": experiment.rerank_top_n,
            "rag_score_threshold": experiment.score_threshold,
        }
    )


def _failed_result(config_index: int, case_id: int, message: str) -> dict[str, Any]:
    """构造失败的策略与用例组合结果。

    参数：
    config_index：实验配置在本次比较中的序号。
    case_id：目标评估用例标识。
    message：需要保存或展示的消息内容。
    """
    return {
        "config_index": config_index,
        "case_id": case_id,
        "status": "FAILED",
        "retrieved_sources": [],
        "source_hit": None,
        "correct": None,
        "refused": None,
        "retrieval_ms": None,
        "error_message": message[:500],
    }


def _strategy_snapshots(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """从实验快照读取策略版本信息。

    参数：
    snapshot：实验启动时保存的输入快照。
    """
    # 局部变量 snapshots：实验开始时冻结保存的策略快照集合。
    snapshots = snapshot.get("strategy_snapshots")
    return snapshots if isinstance(snapshots, list) else []


def _snapshot_configs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """从实验快照提取待比较的检索配置。

    参数：
    snapshot：实验启动时保存的输入快照。
    """
    # 局部变量 snapshots：实验开始时冻结保存的策略快照集合。
    snapshots = _strategy_snapshots(snapshot)
    if snapshots:
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        return [item["config"] for item in snapshots]
    # 局部变量 legacy_configs：兼容旧版格式时读取的配置集合。
    legacy_configs = snapshot.get("configs", [])
    return legacy_configs if isinstance(legacy_configs, list) else []


def _snapshot_cases(snapshot: dict[str, Any], repository: Any) -> list[dict[str, Any]]:
    """从实验快照和仓库读取待执行的评测用例。

    参数：
    snapshot：实验启动时保存的输入快照。
    repository：负责当前业务数据持久化的数据仓库实例。
    """
    # 局部变量 case_snapshots：本次实验冻结保存的评估用例集合。
    case_snapshots = snapshot.get("case_snapshots")
    if isinstance(case_snapshots, list) and case_snapshots:
        return case_snapshots
    return repository.get_cases(snapshot.get("case_ids"), include_archived=True)


def _calculate_config_results(
    cases: list[dict[str, Any]],
    configs: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按检索配置汇总准确率、引用命中和耗时指标。

    参数：
    results：本次处理生成的结果列表。
    cases：当前任务使用的评估用例集合。
    configs：本次实验对照使用的配置集合。
    """
    # 局部变量 results_by_config：按检索配置序号归组的评估结果。
    results_by_config: dict[int, list[dict[str, Any]]] = {}
    # 局部变量 result：单个用例的评估结果字典；按 config_index 将它放入对应检索配置的结果组，供后续汇总指标。
    for result in results:
        results_by_config.setdefault(result["config_index"], []).append(result)

    # 局部变量 summaries：按配置汇总的实验结果列表。
    summaries: list[dict[str, Any]] = []
    # 局部变量 config_index：当前配置在实验对照组中的序号。
    # 局部变量 config：通过 ExperimentConfig 校验的当前实验参数快照；评测此配置时使用其参数构建隔离索引和检索服务。
    for config_index, config in enumerate(configs):
        # 局部变量 result_by_case：按评估用例标识索引的结果映射。
        result_by_case = {
            result["case_id"]: result
            for result in results_by_config.get(config_index, [])
        }
        # 局部变量 completed：当前任务是否已达到终态。
        # 局部变量 case_id：当前评估用例的数据库标识。
        completed = {
            case_id: result
            for case_id, result in result_by_case.items()
            if result["status"] == "COMPLETED"
        }
        # 局部变量 processed_cases：本轮已经处理的用例数量。
        # 局部变量 case：当前评测用例数据，包含问题轮次和预期要点/来源；本流程据这些字段构造输入、执行判定或汇总该用例结果。
        processed_cases = [case for case in cases if case["id"] in result_by_case]
        # 局部变量 answer_cases：预期系统生成实质性答案的评估用例。
        answer_cases = [
            case for case in processed_cases if case["expected_type"] == "ANSWER"
        ]
        # 局部变量 reject_cases：预期系统拒答的评估用例集合。
        reject_cases = [
            case for case in processed_cases if case["expected_type"] == "REJECT"
        ]
        # 局部变量 citation_cases：用于计算引用命中率的评估结果集合。
        citation_cases = [case for case in answer_cases if case["expected_sources"]]
        # 局部变量 times：本次评估或调用记录的耗时数值集合。
        times = [
            result["retrieval_ms"]
            for result in completed.values()
            if result["retrieval_ms"] is not None
        ]
        summaries.append(
            {
                "config_index": config_index,
                "config": config,
                "accuracy": _rate(
                    sum(
                        completed.get(case["id"], {}).get("correct") is True
                        for case in answer_cases
                    ),
                    len(answer_cases),
                ),
                "reject_rate": _rate(
                    sum(
                        completed.get(case["id"], {}).get("correct") is True
                        for case in reject_cases
                    ),
                    len(reject_cases),
                ),
                "citation_hit_rate": _rate(
                    sum(
                        completed.get(case["id"], {}).get("source_hit") is True
                        for case in citation_cases
                    ),
                    len(citation_cases),
                ),
                "avg_retrieval_ms": sum(times) / len(times) if times else None,
            }
        )
    return summaries


def _rate(successes: int, total: int) -> float | None:
    """计算比例并处理总数为零的情况。

    参数：
    successes：成功的数量。
    total：记录总数或待处理项总数。
    """
    return successes / total if total else None


def _export_bool(value: bool | None) -> str:
    """将可空布尔值转换为导出文件中的文本。

    参数：
    value：需要转换或校验的输入值。
    """
    if value is None:
        return ""
    return "是" if value else "否"


def _best_config(
    config_results: list[dict[str, Any]], results: list[dict[str, Any]]
) -> int | None:
    """根据准确率、引用率和耗时排序选择最佳配置。

    参数：
    results：本次处理生成的结果列表。
    config_results：按实验配置汇总的结果列表。
    """
    # 局部变量 completed_by_config：按实验配置统计的已完成用例数。
    # 局部变量 result：单个用例结果；此处读取其 config_index 和 status，筛选出至少有已完成用例的实验配置。
    completed_by_config = {
        result["config_index"] for result in results if result["status"] == "COMPLETED"
    }
    if not completed_by_config:
        return None

    def sort_key(item: dict[str, Any]) -> tuple[float, float, float, float, int]:
        """生成用于比较实验配置结果的排序键。

        参数：
        item：当前遍历或转换的数据项。
        """
        return (
            -(
                item["citation_hit_rate"]
                if item["citation_hit_rate"] is not None
                else -1
            ),
            -(item["accuracy"] if item["accuracy"] is not None else -1),
            -(item["reject_rate"] if item["reject_rate"] is not None else -1),
            item["avg_retrieval_ms"]
            if item["avg_retrieval_ms"] is not None
            else float("inf"),
            item["config_index"],
        )

    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return min(
        (
            item
            for item in config_results
            if item["config_index"] in completed_by_config
        ),
        key=sort_key,
    )["config_index"]
