# 文件作用：定义请求、响应及领域数据的 Pydantic 类型和字段约束。
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.rag.constants import LEGACY_SPLITTER_VERSION
from app.schemas.common import ApiModel, PageQuery, UtcDateTime

# 限制在 0 到 1 之间的评分字段类型。
Score = Annotated[float, Field(ge=0, le=1)]
# 去除首尾空白且至少包含一个字符的字符串类型。
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# 证据片段来源类别的字面量类型。
EvidenceOrigin = Literal["RETRIEVED", "NEIGHBOR"]


# 类作用：上传文件扩展名的受支持枚举。
class FileType(StrEnum):
    PDF = "pdf"
    DOC = "doc"
    DOCX = "docx"
    MD = "md"
    TXT = "txt"


# 类作用：文档导入任务的生命周期状态。
class DocumentStatus(StrEnum):
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


# 类作用：文档来自用户上传还是项目内置资料的来源分类。
class DocumentSourceType(StrEnum):
    UPLOAD = "UPLOAD"
    BUILTIN = "BUILTIN"


# 类作用：内置资料在知识库中的导入和更新状态。
class BuiltinDocumentState(StrEnum):
    MISSING = "MISSING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    OUTDATED = "OUTDATED"


# 类作用：后台任务的统一执行状态。
class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# 类作用：生成回答的表达风格选项。
class AnswerStyle(StrEnum):
    PLAIN = "plain"
    LEGAL = "legal"


# 类作用：大模型回答的速度与质量模式。
class AiMode(StrEnum):
    SPEED = "speed"
    BALANCED = "balanced"
    QUALITY = "quality"


# 类作用：缺失知识记录的处理状态。
class MissingKnowledgeStatus(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


# 类作用：评估用例要求回答或拒答的预期类型。
class EvaluationExpectedType(StrEnum):
    ANSWER = "ANSWER"
    REJECT = "REJECT"


# 类作用：评估用例为内置基线还是自定义数据的来源分类。
class EvaluationCaseOrigin(StrEnum):
    BUILTIN = "BUILTIN"
    CUSTOM = "CUSTOM"


# 类作用：评估用例当前是否可用或已归档。
class EvaluationCaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


# 类作用：评估或实验选择用例的范围方式。
class EvaluationCaseScope(StrEnum):
    BUILTIN_BASELINE = "BUILTIN_BASELINE"
    ALL_ACTIVE = "ALL_ACTIVE"
    SELECTED = "SELECTED"


# 类作用：文档列表及详情使用的数据模型。
class DocumentItem(ApiModel):
    # 字段说明：该资源在数据库中的主键。
    id: int
    # 字段说明：来源资料展示名称。
    file_name: str
    # 字段说明：源文件扩展名。
    file_type: FileType
    # 字段说明：文档来源分类。
    source_type: DocumentSourceType
    # 字段说明：内置文档稳定来源键。
    source_key: str | None = None
    # 字段说明：文档当前的导入或筛选状态。
    status: DocumentStatus
    # 字段说明：该文档已生成的文本块数量。
    chunk_count: int
    # 字段说明：错误详情。
    error_message: str | None
    # 字段说明：创建时间。
    created_at: UtcDateTime
    # 字段说明：最近修改时间。
    updated_at: UtcDateTime


# 类作用：文档上传任务受理后返回的数据。
class DocumentUploadAccepted(ApiModel):
    # 字段说明：所属文档的主键。
    document_id: int
    # 字段说明：来源资料展示名称。
    file_name: str
    # 字段说明：文档当前的导入或筛选状态。
    status: DocumentStatus


# 类作用：文档重新导入任务受理后返回的数据。
class DocumentReimportAccepted(ApiModel):
    # 字段说明：所属文档的主键。
    document_id: int
    # 字段说明：文档当前的导入或筛选状态。
    status: DocumentStatus


# 类作用：单个内置文档的导入状态。
class BuiltinDocumentStatusItem(ApiModel):
    # 字段说明：内置文档稳定来源键。
    source_key: str
    # 字段说明：来源资料展示名称。
    file_name: str
    # 字段说明：内置资料当前处理状态。
    state: BuiltinDocumentState
    # 字段说明：所属文档的主键。
    document_id: int | None


# 类作用：内置文档导入状态的统计汇总。
class BuiltinDocumentStatus(ApiModel):
    # 字段说明：分页匹配数或任务总数。
    total: int = Field(ge=0)
    # 字段说明：成功处理或导入的记录数量。
    success: int = Field(ge=0)
    # 字段说明：仍在处理中的记录数量。
    processing: int = Field(ge=0)
    # 字段说明：处理失败的记录数量。
    failed: int = Field(ge=0)
    # 字段说明：尚未导入的内置资料数量。
    missing: int = Field(ge=0)
    # 字段说明：源文件已变化、需要更新的资料数量。
    outdated: int = Field(ge=0)
    # 字段说明：是否所有内置文档均已完成导入。
    all_imported: bool
    # 字段说明：返回的数据项或引用清单。
    items: list[BuiltinDocumentStatusItem]


# 类作用：批量内置文档导入任务的受理统计。
class BuiltinDocumentImportAccepted(ApiModel):
    # 字段说明：分页匹配数或任务总数。
    total: int = Field(ge=0)
    # 字段说明：已存在且无需重新导入的文档数量。
    already_imported: int = Field(ge=0)
    # 字段说明：本次新调度的导入任务数量。
    scheduled: int = Field(ge=0)
    # 字段说明：仍在处理中的记录数量。
    processing: int = Field(ge=0)
    # 字段说明：本次需要跟踪的文档主键列表。
    tracked_document_ids: list[int]
    # 字段说明：是否所有内置文档均已完成导入。
    all_imported: bool


# 类作用：文档列表分页和过滤参数。
class DocumentQuery(PageQuery):
    # 字段说明：文档当前的导入或筛选状态。
    status: DocumentStatus | None = None
    # 字段说明：用于模糊匹配的关键词。
    keyword: str | None = None


# 类作用：知识库中文档文本块的数据模型。
class ChunkItem(ApiModel):
    # 字段说明：该资源在数据库中的主键。
    id: int
    # 字段说明：所属文档的主键。
    document_id: int
    # 字段说明：文档中从 1 开始的文本块序号。
    chunk_no: int
    # 字段说明：消息、文本块或回答的正文内容。
    content: str
    # 字段说明：向量索引中用于定位该文本块的键。
    vector_key: str


# 类作用：聊天会话列表和详情数据。
class SessionItem(ApiModel):
    # 字段说明：聊天会话的 UUID 标识。
    id: UUID
    # 字段说明：会话或任务的可读标题。
    title: str | None
    # 字段说明：创建时间。
    created_at: UtcDateTime
    # 字段说明：最近修改时间。
    updated_at: UtcDateTime


# 类作用：创建聊天会话的请求字段。
class CreateSessionRequest(ApiModel):
    # 字段说明：会话或任务的可读标题。
    title: str | None = Field(default=None, max_length=100)


# 类作用：回答引用的文档片段及其检索评分。
class CitationItem(ApiModel):
    # 字段说明：引用或操作的文本块主键。
    chunk_id: int
    # 字段说明：所属文档的主键。
    document_id: int
    # 字段说明：来源资料展示名称。
    file_name: str
    # 字段说明：文档中从 1 开始的文本块序号。
    chunk_no: int
    # 字段说明：消息、文本块或回答的正文内容。
    content: str
    # 字段说明：归一化后的综合相关性分数。
    score: Score
    # 字段说明：向量检索阶段得到的相关性分数。
    retrieval_score: Score
    # 字段说明：重排模型返回的相关性分数。
    rerank_score: Score | None
    # 字段说明：片段在结果列表中的名次，从 1 开始。
    rank_no: int
    # 字段说明：关键词或词项匹配的相关性分数。
    lexical_score: Score = 0.0
    # 字段说明：来源权重对候选片段产生的评分。
    source_score: Score = 0.0
    # 字段说明：轻量重排阶段计算的评分。
    lightweight_score: Score = 0.0
    # 字段说明：证据来源于直接检索还是相邻片段扩展。
    evidence_origin: EvidenceOrigin = "RETRIEVED"
    # 字段说明：相邻扩展证据所依附的检索锚点文本块。
    anchor_chunk_id: int | None = None
    # 字段说明：该扩展证据锚点的原始检索分数。
    anchor_retrieval_score: Score | None = None


# 类作用：劳动争议材料清单工具的输入参数。
class MaterialChecklistInput(ApiModel):
    # 字段说明：劳动争议材料清单的分类。
    dispute_type: NonEmptyText
    # 字段说明：补充描述文本。
    description: NonEmptyText


# 类作用：劳动争议材料清单工具的结构化输出。
class MaterialChecklistOutput(ApiModel):
    # 字段说明：工具生成的建议材料列表。
    materials: list[str]
    # 字段说明：面向用户或维护者的补充说明。
    note: str


# 类作用：工具名称、输入和执行结果的统一记录。
class ToolExecutionItem(ApiModel):
    # 字段说明：本次执行的工具名称。
    tool_name: Literal["generate_rights_material_checklist"]
    # 字段说明：传递给工具的结构化输入。
    input: MaterialChecklistInput
    # 字段说明：工具执行产生的结构化结果。
    output: MaterialChecklistOutput


# 类作用：聊天消息及其引用和工具记录。
class MessageItem(ApiModel):
    # 字段说明：该资源在数据库中的主键。
    id: int
    # 字段说明：所属聊天会话的唯一标识。
    session_id: UUID
    # 字段说明：消息发送方角色。
    role: Literal["user", "assistant"]
    # 字段说明：消息、文本块或回答的正文内容。
    content: str
    # 字段说明：查询改写后用于检索的问题文本。
    rewritten_question: str | None
    # 字段说明：回答表达风格。
    answer_style: AnswerStyle | None
    # 字段说明：生成回答时使用的 AI 模式。
    ai_mode: AiMode | None
    # 字段说明：回答是否因证据或合规原因拒绝生成。
    refused: bool | None
    # 字段说明：创建时间。
    created_at: UtcDateTime
    # 字段说明：回答关联的引用来源列表。
    citations: list[CitationItem]
    # 字段说明：回答过程中产生的工具执行记录。
    tool_executions: list[ToolExecutionItem]


# 类作用：后台任务的状态、进度和错误信息。
class JobProgress(ApiModel):
    # 字段说明：后台任务或实验当前的执行状态。
    status: JobStatus
    # 字段说明：已完成进度。
    progress_current: int = Field(ge=0)
    # 字段说明：总进度。
    progress_total: int = Field(ge=0)
    # 字段说明：错误详情。
    error_message: str | None


# 类作用：创建流式聊天回答的请求数据。
class ChatRequest(ApiModel):
    # 字段说明：所属聊天会话的唯一标识。
    session_id: UUID
    # 字段说明：用户提问或评估输入的问题文本。
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]
    # 字段说明：回答表达风格。
    answer_style: AnswerStyle
    # 字段说明：生成回答时使用的 AI 模式。
    ai_mode: AiMode = AiMode.BALANCED


# 类作用：评估用例期望命中的资料来源。
class ExpectedSource(ApiModel):
    # 字段说明：来源资料展示名称。
    file_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    # 字段说明：文档中从 1 开始的文本块序号。
    chunk_no: int | None = Field(default=None, ge=1)


# 评估用例单轮对话文本的约束类型。
EvaluationTurn = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
# 评估答案预期要点的约束类型。
EvaluationPoint = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


# 类作用：新建评估用例的内容和预期结果。
class EvaluationCasePayload(ApiModel):
    # 字段说明：用户问题或评估用例所属主题。
    topic: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
    ]
    # 字段说明：用例要求系统回答或拒答的类型。
    expected_type: EvaluationExpectedType
    # 字段说明：多轮评估用例中的对话轮次文本。
    turns: list[EvaluationTurn] = Field(min_length=1, max_length=10)
    # 字段说明：评估答案需要覆盖的预期要点。
    expected_points: list[EvaluationPoint] = Field(default_factory=list, max_length=20)
    # 字段说明：评估用例预期命中的资料来源。
    expected_sources: list[ExpectedSource] = Field(default_factory=list, max_length=20)
    # 字段说明：该用例是否预期展示合规提示。
    should_show_compliance: bool = False

    @model_validator(mode="after")
    def validate_expected_points(self) -> "EvaluationCasePayload":
        """校验要求回答的评估用例至少包含一个预期答案要点。
        """
        if (
            self.expected_type is EvaluationExpectedType.ANSWER
            and not self.expected_points
        ):
            raise ValueError("ANSWER cases require at least one expected point")
        return self


# 类作用：创建用户维护的评估用例请求字段。
class EvaluationCaseCreate(EvaluationCasePayload):
    pass


# 类作用：更新评估用例时使用的内容及并发版本。
class EvaluationCaseUpdate(EvaluationCasePayload):
    # 字段说明：版本号。
    version: int = Field(ge=1)


# 类作用：评估用例的内容、来源及生命周期元数据。
class EvaluationCase(EvaluationCasePayload):
    # 字段说明：评估用例的数据库主键。
    id: int
    # 字段说明：记录或用例的来源类别。
    origin: EvaluationCaseOrigin
    # 字段说明：评估用例当前的可用或归档状态。
    status: EvaluationCaseStatus
    # 字段说明：版本号。
    version: int = Field(ge=1)
    # 字段说明：创建时间。
    created_at: UtcDateTime
    # 字段说明：最近修改时间。
    updated_at: UtcDateTime
    # 字段说明：归档时间。
    archived_at: UtcDateTime | None = None


# 类作用：评估用例列表筛选和分页条件。
class EvaluationCaseQuery(PageQuery):
    # 字段说明：用户问题或评估用例所属主题。
    topic: str | None = None
    # 字段说明：用例要求系统回答或拒答的类型。
    expected_type: EvaluationExpectedType | None = None
    # 字段说明：是否筛选多轮对话用例。
    is_multi_turn: bool | None = None
    # 字段说明：记录或用例的来源类别。
    origin: EvaluationCaseOrigin | None = None
    # 字段说明：评估用例当前的可用或归档状态。
    status: EvaluationCaseStatus | None = None
    # 字段说明：是否将已归档记录包含在列表中。
    include_archived: bool = False


# 类作用：创建评估运行时提交的选项。
class EvaluationRunCreate(ApiModel):
    # 字段说明：可读名称。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    # 字段说明：评估运行指定的用例标识列表。
    case_ids: list[int] | None = Field(default=None, min_length=1)
    # 字段说明：回答表达风格。
    answer_style: AnswerStyle
    # 字段说明：评估用例选择范围。
    case_scope: EvaluationCaseScope | None = None
    # 字段说明：生成回答时使用的 AI 模式。
    ai_mode: AiMode = AiMode.BALANCED

    @model_validator(mode="after")
    def unique_case_ids(self) -> "EvaluationRunCreate":
        """校验用例 ID 不重复，并推导和校验用例选择范围。
        """
        if self.case_ids is not None and len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("case_ids must not contain duplicates")
        if self.case_scope is None:
            # 成员变量 case_scope：本次评测或实验选择的用例范围。
            self.case_scope = (
                EvaluationCaseScope.SELECTED
                if self.case_ids is not None
                else EvaluationCaseScope.BUILTIN_BASELINE
            )
        elif self.case_scope is EvaluationCaseScope.SELECTED and not self.case_ids:
            raise ValueError("SELECTED runs require case_ids")
        elif (
            self.case_scope is not EvaluationCaseScope.SELECTED
            and self.case_ids is not None
        ):
            raise ValueError("case_ids is only valid for SELECTED runs")
        return self


# 类作用：评估任务受理后返回的任务标识。
class EvaluationRunJob(JobProgress):
    # 字段说明：关联评估运行的主键。
    run_id: int


# 类作用：评估运行列表筛选和分页条件。
class EvaluationRunQuery(PageQuery):
    # 字段说明：后台任务或实验当前的执行状态。
    status: JobStatus | None = None


# 类作用：评估运行列表的摘要信息。
class EvaluationRunSummary(JobProgress):
    # 字段说明：该资源在数据库中的主键。
    id: int
    # 字段说明：可读名称。
    name: str
    # 字段说明：创建时间。
    created_at: UtcDateTime
    # 字段说明：最近修改时间。
    updated_at: UtcDateTime


# 类作用：评估和实验执行时保存的 LangChain 运行配置快照。
class LangChainRuntimeConfig(ApiModel):
    # 字段说明：本次运行使用的 LangChain Core 版本。
    langchain_version: str
    # 字段说明：聊天模型 Provider 的实现标识。
    chat_provider: str
    # 字段说明：聊天生成使用的大语言模型名称。
    llm_model: str
    # 字段说明：生成回答时使用的 AI 模式。
    ai_mode: AiMode
    # 字段说明：查询改写阶段是否启用模型推理。
    rewrite_thinking: bool
    # 字段说明：查询改写阶段使用的推理强度。
    rewrite_effort: Literal["low", "high", "max"] | None
    # 字段说明：查询改写阶段使用的采样温度。
    rewrite_temperature: float = Field(ge=0.0, le=2.0)
    # 字段说明：查询改写阶段使用的 nucleus 采样参数。
    rewrite_top_p: float = Field(ge=0.0, le=1.0)
    # 字段说明：查询改写阶段允许生成的最大 token 数。
    rewrite_max_tokens: int = Field(ge=1)
    # 字段说明：证据判断阶段是否启用模型推理。
    evidence_thinking: bool
    # 字段说明：证据判断阶段使用的推理强度。
    evidence_effort: Literal["low", "high", "max"] | None
    # 字段说明：证据判断阶段使用的采样温度。
    evidence_temperature: float = Field(ge=0.0, le=2.0)
    # 字段说明：证据判断阶段使用的 nucleus 采样参数。
    evidence_top_p: float = Field(ge=0.0, le=1.0)
    # 字段说明：证据判断阶段允许生成的最大 token 数。
    evidence_max_tokens: int = Field(ge=1)
    # 字段说明：答案生成阶段是否启用模型推理。
    answer_thinking: bool
    # 字段说明：答案生成阶段使用的推理强度。
    answer_effort: Literal["low", "high", "max"] | None
    # 字段说明：答案生成阶段使用的采样温度。
    answer_temperature: float = Field(ge=0.0, le=2.0)
    # 字段说明：答案生成阶段使用的 nucleus 采样参数。
    answer_top_p: float = Field(ge=0.0, le=1.0)
    # 字段说明：答案生成阶段允许生成的最大 token 数。
    answer_max_tokens: int = Field(ge=1)
    # 字段说明：嵌入服务类型。
    embedding_provider: Literal["local", "api"]
    # 字段说明：嵌入模型标识。
    embedding_model: str
    # 字段说明：是否对嵌入向量执行归一化。
    embedding_normalize: bool
    # 字段说明：文本切分器实现的标识。
    splitter_type: str
    # 字段说明：文本切分算法版本标识。
    splitter_version: str
    # 字段说明：向量存储实现的标识。
    vectorstore_type: str
    # 字段说明：检索策略实现的类型。
    retrieval_type: str
    # 字段说明：重排阶段使用的模型名称。
    rerank_model: str | None
    # 字段说明：提示词模板版本标识。
    prompt_version: str


# 类作用：评估运行使用的提示词、模型和检索参数快照。
class EvaluationRunConfig(LangChainRuntimeConfig):
    # 字段说明：回答表达风格。
    answer_style: AnswerStyle
    # 字段说明：评估用例选择范围。
    case_scope: EvaluationCaseScope | None = None
    # 字段说明：内置评估基准数据的版本号。
    baseline_version: int | None = None
    # 字段说明：执行自动评估的大模型名称。
    evaluator_model: str
    # 字段说明：评估器提示词的版本标识。
    evaluator_prompt_version: str
    # 字段说明：文本切分时单个文本块的目标长度。
    chunk_size: int
    # 字段说明：相邻文本块之间保留的重叠长度。
    chunk_overlap: int
    # 字段说明：重排后保留的主要检索结果数量。
    top_k: int
    # 字段说明：是否启用模型重排。
    rerank_enabled: bool
    # 字段说明：候选检索结果的最低相关性阈值。
    score_threshold: Score
    # 字段说明：向量检索阶段初筛的候选数量。
    candidate_k: int = Field(default=20, ge=5, le=50)
    # 字段说明：是否启用轻量级重排。
    lightweight_rerank_enabled: bool = True
    # 字段说明：允许扩展相邻证据的前排锚点数量。
    neighbor_expand_top_n: int = Field(default=3, ge=0, le=10)
    # 字段说明：每个检索锚点扩展的相邻文本块距离。
    neighbor_radius: int = Field(default=1, ge=0, le=2)
    # 字段说明：进入证据判断阶段的片段数量上限。
    evidence_limit: int = Field(default=10, ge=5, le=20)


# 类作用：评估对话单轮的回答、证据和检索诊断。
class EvaluationTurnDiagnostic(ApiModel):
    # 字段说明：当前诊断对应的对话轮次序号。
    turn_no: int = Field(ge=1)
    # 字段说明：用户提问或评估输入的问题文本。
    question: str
    # 字段说明：查询改写后用于检索的问题文本。
    rewritten_question: str
    # 字段说明：模型生成或评估使用的回答文本。
    answer: str
    # 字段说明：回答是否因证据或合规原因拒绝生成。
    refused: bool
    # 字段说明：本次采用的回答、工具或拒答路径。
    answer_mode: Literal["RAG_ANSWER", "PARTIAL_ANSWER", "TOOL_ONLY", "REFUSAL"] | None
    # 字段说明：证据充分性判断结果。
    evidence_decision: Literal["SUFFICIENT", "PARTIAL", "INSUFFICIENT"] | None
    # 字段说明：说明证据判断结果的理由。
    evidence_reason: str | None
    # 字段说明：证据或回答已支持的要点。
    supported_points: list[str] = Field(default_factory=list)
    # 字段说明：证据或回答尚未覆盖的要点。
    missing_points: list[str] = Field(default_factory=list)
    # 字段说明：重排前或检索阶段返回的来源列表。
    retrieved_sources: list[CitationItem]
    # 字段说明：回答关联的引用来源列表。
    citations: list[CitationItem]
    # 字段说明：检索和排序阶段耗时，单位为毫秒。
    retrieval_ms: int = Field(ge=0)


# 类作用：单个评估用例的执行结果和评分诊断。
class EvaluationResultItem(ApiModel):
    # 字段说明：该评估结果对应的评估用例标识。
    case_id: int
    # 字段说明：任务或记录状态。
    status: Literal["COMPLETED", "FAILED"]
    # 字段说明：模型生成或评估使用的回答文本。
    answer: str | None
    # 字段说明：回答是否因证据或合规原因拒绝生成。
    refused: bool | None
    # 字段说明：回答是否符合评估预期。
    correct: bool | None
    # 字段说明：引用或检索来源是否命中预期资料。
    source_hit: bool | None
    # 字段说明：检索阶段是否命中预期资料。
    retrieval_hit: bool | None
    # 字段说明：多轮对话上下文是否正确保留。
    context_retained: bool | None
    # 字段说明：多轮用例整体回答是否正确。
    multi_turn_correct: bool | None
    # 字段说明：是否命中预期合规提示。
    compliance_hit: bool | None
    # 字段说明：本次采用的回答、工具或拒答路径。
    answer_mode: Literal["RAG_ANSWER", "PARTIAL_ANSWER", "TOOL_ONLY", "REFUSAL"] | None
    # 字段说明：证据充分性判断结果。
    evidence_decision: Literal["SUFFICIENT", "PARTIAL", "INSUFFICIENT"] | None
    # 字段说明：评估器给出的评分理由。
    judge_reason: str | None = None
    # 字段说明：评估器判定已命中的预期要点。
    matched_points: list[str] = Field(default_factory=list)
    # 字段说明：证据或回答尚未覆盖的要点。
    missing_points: list[str] = Field(default_factory=list)
    # 字段说明：回答与证据或预期之间发现的矛盾项。
    contradictions: list[str] = Field(default_factory=list)
    # 字段说明：逐轮回答和检索诊断信息。
    turn_diagnostics: list[EvaluationTurnDiagnostic] = Field(default_factory=list)
    # 字段说明：回答关联的引用来源列表。
    citations: list[CitationItem]
    # 字段说明：本次运行记录的延迟，单位为毫秒。
    latency_ms: int | None
    # 字段说明：错误详情。
    error_message: str | None


# 类作用：一批评估结果的汇总指标。
class EvaluationMetrics(ApiModel):
    # 字段说明：评估回答正确率。
    accuracy: Score | None
    # 字段说明：系统按预期拒答的比例。
    reject_rate: Score | None
    # 字段说明：回答被拒绝的比例。
    refusal_rate: Score | None
    # 字段说明：引用来源命中预期来源的比例。
    citation_hit_rate: Score | None
    # 字段说明：检索结果命中预期来源的比例。
    retrieval_hit_rate: Score | None
    # 字段说明：多轮上下文信息得到保留的比例。
    context_retention_rate: Score | None
    # 字段说明：评估运行成功完成的比例。
    execution_success_rate: Score | None
    # 字段说明：多轮评估用例通过的比例。
    multi_turn_pass_rate: Score | None
    # 字段说明：合规提示按预期命中的比例。
    compliance_hit_rate: Score | None


# 类作用：评估运行的配置、汇总指标和逐用例结果。
class EvaluationRunDetail(JobProgress):
    # 字段说明：该资源在数据库中的主键。
    id: int
    # 字段说明：可读名称。
    name: str
    # 字段说明：结构化参数配置。
    config: EvaluationRunConfig
    # 字段说明：保存metrics。
    metrics: EvaluationMetrics | None
    # 字段说明：实验或运行中的逐用例结果列表。
    results: list[EvaluationResultItem]


# 类作用：已记录的知识缺口及其处理状态。
class MissingKnowledgeItem(ApiModel):
    # 字段说明：该资源在数据库中的主键。
    id: int
    # 字段说明：知识主题的稳定存储标识。
    topic_key: str
    # 字段说明：该知识缺口下具有代表性的用户问题。
    sample_question: str
    # 字段说明：该类记录累计出现次数。
    count: int
    # 字段说明：尚未被知识库充分覆盖的领域说明。
    missing_area: str
    # 字段说明：知识缺口记录当前的处理状态。
    status: MissingKnowledgeStatus
    # 字段说明：面向用户或维护者的补充说明。
    note: str | None
    # 字段说明：首次发现该问题的时间。
    first_seen_at: UtcDateTime
    # 字段说明：最近一次发现该问题的时间。
    last_seen_at: UtcDateTime


# 类作用：缺失知识列表筛选、分页和排序条件。
class MissingKnowledgeQuery(PageQuery):
    # 字段说明：知识缺口记录当前的处理状态。
    status: MissingKnowledgeStatus | None = None
    # 字段说明：用于模糊匹配的关键词。
    keyword: str | None = None
    # 字段说明：列表记录采用的排序规则。
    sort: Literal["count_desc", "last_seen_desc"] = "count_desc"


# 类作用：更新缺失知识状态及备注的请求字段。
class MissingKnowledgeUpdate(ApiModel):
    # 字段说明：知识缺口记录当前的处理状态。
    status: MissingKnowledgeStatus
    # 字段说明：面向用户或维护者的补充说明。
    note: str | None = None


# 类作用：检索实验可比较的切分、检索、重排参数。
class ExperimentConfig(ApiModel):
    # 字段说明：文本切分时单个文本块的目标长度。
    chunk_size: int = Field(ge=100, le=2000)
    # 字段说明：相邻文本块之间保留的重叠长度。
    chunk_overlap: int = Field(ge=0, le=500)
    # 字段说明：重排后保留的主要检索结果数量。
    top_k: int = Field(ge=1, le=20)
    # 字段说明：是否启用模型重排。
    rerank_enabled: bool
    # 字段说明：模型重排后保留的候选数量。
    rerank_top_n: int = Field(ge=1)
    # 字段说明：候选检索结果的最低相关性阈值。
    score_threshold: Score
    # 字段说明：向量检索阶段初筛的候选数量。
    candidate_k: int = Field(default=20, ge=5, le=50)
    # 字段说明：是否启用轻量级重排。
    lightweight_rerank_enabled: bool = True
    # 字段说明：允许扩展相邻证据的前排锚点数量。
    neighbor_expand_top_n: int = Field(default=3, ge=0, le=10)
    # 字段说明：每个检索锚点扩展的相邻文本块距离。
    neighbor_radius: int = Field(default=1, ge=0, le=2)
    # 字段说明：进入证据判断阶段的片段数量上限。
    evidence_limit: int = Field(default=10, ge=5, le=20)

    @model_validator(mode="after")
    def validate_relationships(self) -> "ExperimentConfig":
        """校验检索实验配置中切分、检索、证据和重排参数之间的约束。
        """
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        if self.top_k > self.candidate_k:
            raise ValueError("top_k must be less than or equal to candidate_k")
        if self.evidence_limit < self.top_k:
            raise ValueError("evidence_limit must be greater than or equal to top_k")
        if self.neighbor_expand_top_n > self.top_k:
            raise ValueError(
                "neighbor_expand_top_n must be less than or equal to top_k"
            )
        if self.rerank_enabled and self.rerank_top_n > self.candidate_k:
            raise ValueError(
                "rerank_top_n must be less than or equal to candidate_k"
            )
        return self


# 类作用：创建检索实验时提交的名称、策略和用例范围。
class ExperimentCreate(ApiModel):
    # 字段说明：可读名称。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    # 字段说明：实验运行时使用的策略标识列表。
    strategy_ids: list[int] = Field(min_length=2)
    # 字段说明：评估用例选择范围。
    case_scope: EvaluationCaseScope
    # 字段说明：评估运行指定的用例标识列表。
    case_ids: list[int] | None = Field(default=None, min_length=1)
    # 字段说明：回答表达风格。
    answer_style: AnswerStyle = AnswerStyle.PLAIN
    # 字段说明：生成回答时使用的 AI 模式。
    ai_mode: AiMode = AiMode.BALANCED

    @model_validator(mode="after")
    def unique_case_ids(self) -> "ExperimentCreate":
        """校验用例 ID 不重复，并推导和校验用例选择范围。
        """
        if self.case_ids is not None and len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("case_ids must not contain duplicates")
        if self.case_scope is EvaluationCaseScope.SELECTED and not self.case_ids:
            raise ValueError("SELECTED experiments require case_ids")
        elif (
            self.case_scope is not EvaluationCaseScope.SELECTED
            and self.case_ids is not None
        ):
            raise ValueError("case_ids is only valid for SELECTED experiments")
        if len(set(self.strategy_ids)) != len(self.strategy_ids):
            raise ValueError("strategy_ids must not contain duplicates")
        return self


# 类作用：复制实验时提交的新名称。
class ExperimentCopy(ApiModel):
    # 字段说明：可读名称。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]


# 类作用：检索实验任务受理后返回的标识和状态。
class ExperimentJob(JobProgress):
    # 字段说明：关联检索实验的主键。
    experiment_id: int


# 类作用：检索实验列表筛选及分页条件。
class ExperimentQuery(PageQuery):
    # 字段说明：后台任务或实验当前的执行状态。
    status: JobStatus | None = None
    # 字段说明：是否将已归档记录包含在列表中。
    include_archived: bool = False


# 类作用：检索实验列表的摘要信息。
class ExperimentSummary(JobProgress):
    # 字段说明：检索实验的数据库主键。
    id: int
    # 字段说明：可读名称。
    name: str
    # 字段说明：创建时间。
    created_at: UtcDateTime
    # 字段说明：最近修改时间。
    updated_at: UtcDateTime
    # 字段说明：归档时间。
    archived_at: UtcDateTime | None = None


# 类作用：单组检索参数配置的评估指标。
class ExperimentConfigResult(ApiModel):
    # 字段说明：该结果对应的配置序号。
    config_index: int
    # 字段说明：结构化参数配置。
    config: ExperimentConfig
    # 字段说明：评估回答正确率。
    accuracy: Score | None
    # 字段说明：系统按预期拒答的比例。
    reject_rate: Score | None
    # 字段说明：引用来源命中预期来源的比例。
    citation_hit_rate: Score | None
    # 字段说明：保存avgretrievalms。
    avg_retrieval_ms: float | None


# 类作用：实验启动时使用的检索策略版本快照。
class RetrievalStrategySnapshot(ApiModel):
    # 字段说明：关联检索策略的主键。
    strategy_id: int
    # 字段说明：可读名称。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    # 字段说明：版本号。
    version: int = Field(ge=1)
    # 字段说明：结构化参数配置。
    config: ExperimentConfig


# 类作用：实验中单个用例在某组配置下的运行结果。
class ExperimentCaseResult(ApiModel):
    # 字段说明：该结果对应的配置序号。
    config_index: int
    # 字段说明：该评估结果对应的评估用例标识。
    case_id: int
    # 字段说明：后台任务或实验当前的执行状态。
    status: Literal["COMPLETED", "FAILED"]
    # 字段说明：重排前或检索阶段返回的来源列表。
    retrieved_sources: list[CitationItem]
    # 字段说明：引用或检索来源是否命中预期资料。
    source_hit: bool | None
    # 字段说明：回答是否符合评估预期。
    correct: bool | None
    # 字段说明：回答是否因证据或合规原因拒绝生成。
    refused: bool | None
    # 字段说明：检索和排序阶段耗时，单位为毫秒。
    retrieval_ms: int | None
    # 字段说明：错误详情。
    error_message: str | None


# 类作用：向量索引使用的嵌入模型签名。
class EmbeddingSignature(ApiModel):
    # 字段说明：嵌入服务类型。
    embedding_provider: Literal["local", "api"]
    # 字段说明：嵌入模型标识。
    embedding_model: str
    # 字段说明：嵌入向量的维度数。
    embedding_dimension: int = Field(ge=1)
    # 字段说明：向量生成时是否启用归一化。
    normalize_embeddings: bool


# 类作用：检索实验配置、策略快照及结果明细。
class ExperimentDetail(JobProgress):
    # 字段说明：检索实验的数据库主键。
    id: int
    # 字段说明：可读名称。
    name: str
    # 字段说明：描述当前向量索引嵌入模型的兼容性签名。
    embedding_signature: EmbeddingSignature
    # 字段说明：保存运行时结构化参数配置快照，记录本次实验实际使用的模型及生成参数，供结果复现和配置对照。
    runtime_config: LangChainRuntimeConfig
    # 字段说明：归档时间。
    archived_at: UtcDateTime | None = None
    # 字段说明：综合指标排序最佳配置的索引。
    best_config_index: int | None
    # 字段说明：本次运行包含的评估用例数量。
    case_count: int = Field(ge=0)
    # 字段说明：实验启动时保存的策略配置快照列表。
    strategy_snapshots: list[RetrievalStrategySnapshot] = Field(default_factory=list)
    # 字段说明：各策略或参数配置的汇总结果。
    config_results: list[ExperimentConfigResult]
    # 字段说明：实验或运行中的逐用例结果列表。
    results: list[ExperimentCaseResult]


# 类作用：可复用检索策略的配置和生命周期信息。
class RetrievalStrategy(ApiModel):
    # 字段说明：检索策略的数据库主键。
    id: int
    # 字段说明：可读名称。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    # 字段说明：补充描述文本。
    description: str
    # 字段说明：内置策略或用例的稳定唯一标识。
    builtin_key: str | None
    # 字段说明：结构化参数配置。
    config: ExperimentConfig
    # 字段说明：该策略是否由系统预置且不可编辑。
    is_builtin: bool
    # 字段说明：版本号。
    version: int = Field(ge=1)
    # 字段说明：当前是否启用。
    is_active: bool
    # 字段说明：归档时间。
    archived_at: UtcDateTime | None
    # 字段说明：创建时间。
    created_at: UtcDateTime
    # 字段说明：最近修改时间。
    updated_at: UtcDateTime


# 类作用：新建检索策略时提交的名称和配置。
class RetrievalStrategyCreate(ApiModel):
    # 字段说明：可读名称。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    # 字段说明：补充描述文本。
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=255)
    ] = ""
    # 字段说明：结构化参数配置。
    config: ExperimentConfig


# 类作用：更新检索策略名称、说明和参数配置的请求字段。
class RetrievalStrategyUpdate(RetrievalStrategyCreate):
    pass


# 类作用：启用或停用检索策略的请求字段。
class RetrievalStrategyStatusUpdate(ApiModel):
    # 字段说明：当前是否启用。
    is_active: bool


# 类作用：检索策略历史版本及配置快照。
class RetrievalStrategyVersion(ApiModel):
    # 字段说明：检索策略的数据库主键。
    id: int
    # 字段说明：关联检索策略的主键。
    strategy_id: int
    # 字段说明：版本号。
    version: int = Field(ge=1)
    # 字段说明：可读名称。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    # 字段说明：补充描述文本。
    description: str
    # 字段说明：结构化参数配置。
    config: ExperimentConfig
    # 字段说明：创建时间。
    created_at: UtcDateTime


# 类作用：检索预览请求的问题、策略和回答模式。
class RetrievalPreviewRequest(ApiModel):
    # 字段说明：用户提问或评估输入的问题文本。
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]
    # 字段说明：回答表达风格。
    answer_style: AnswerStyle = AnswerStyle.PLAIN
    # 字段说明：关联检索策略的主键。
    strategy_id: int
    # 字段说明：生成回答时使用的 AI 模式。
    ai_mode: AiMode = AiMode.BALANCED


# 类作用：检索预览流程中单个阶段的执行诊断。
class RetrievalTraceStage(ApiModel):
    # 字段说明：处理阶段名称。
    stage: str
    # 字段说明：任务或记录状态。
    status: Literal["completed", "skipped", "failed"]
    # 字段说明：该阶段的结果或诊断说明。
    detail: str
    # 字段说明：该阶段耗时，单位为毫秒。
    duration_ms: int | None = Field(default=None, ge=0)


# 类作用：单次检索预览返回的回答、引用和阶段轨迹。
class RetrievalPreviewResponse(ApiModel):
    # 字段说明：关联检索策略的主键。
    strategy_id: int
    # 字段说明：实验执行时使用的策略名称快照。
    strategy_name: str
    # 字段说明：实验执行时使用的策略版本号。
    strategy_version: int = Field(ge=1)
    # 字段说明：本次预览使用的索引或检索模式。
    index_mode: str
    # 字段说明：本次预览或运行的限制说明列表。
    limitations: list[str]
    # 字段说明：用户提问或评估输入的问题文本。
    question: str
    # 字段说明：查询改写后用于检索的问题文本。
    rewritten_question: str
    # 字段说明：模型生成或评估使用的回答文本。
    answer: str
    # 字段说明：回答是否因证据或合规原因拒绝生成。
    refused: bool
    # 字段说明：本次采用的回答、工具或拒答路径。
    answer_mode: Literal[
        "RAG_ANSWER", "PARTIAL_ANSWER", "TOOL_ONLY", "REFUSAL"
    ]
    # 字段说明：证据充分性判断结果。
    evidence_decision: Literal["SUFFICIENT", "PARTIAL", "INSUFFICIENT"]
    # 字段说明：说明证据判断结果的理由。
    evidence_reason: str
    # 字段说明：证据或回答已支持的要点。
    supported_points: list[str]
    # 字段说明：证据或回答尚未覆盖的要点。
    missing_points: list[str]
    # 字段说明：保存toolexecution。
    tool_execution: ToolExecutionItem | None
    # 字段说明：检索和排序阶段耗时，单位为毫秒。
    retrieval_ms: int = Field(ge=0)
    # 字段说明：重排前或检索阶段返回的来源列表。
    retrieved_sources: list[CitationItem]
    # 字段说明：回答关联的引用来源列表。
    citations: list[CitationItem]
    # 字段说明：本次处理各阶段的执行轨迹。
    trace: list[RetrievalTraceStage]
    # 字段说明：结构化参数配置。
    config: ExperimentConfig


# 类作用：向量索引构建时使用的切分参数和版本信息。
class IndexMeta(EmbeddingSignature):
    # 字段说明：文本切分时单个文本块的目标长度。
    chunk_size: int = Field(ge=1)
    # 字段说明：相邻文本块之间保留的重叠长度。
    chunk_overlap: int = Field(ge=0)
    # 字段说明：文本切分算法版本标识。
    splitter_version: str = LEGACY_SPLITTER_VERSION
    # 字段说明：创建时间。
    created_at: UtcDateTime
