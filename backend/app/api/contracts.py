# 文件作用：定义聊天、文档、评估和检索实验等 HTTP 路由及其响应契约。
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    authorize_contract_access,
    get_chat_service,
    get_current_user,
    get_document_service,
    get_evaluation_service,
    get_retrieval_experiment_service,
)
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.schemas.common import ApiResponse, PageQuery, PageResult
from app.schemas.contracts import (
    BuiltinDocumentImportAccepted,
    BuiltinDocumentStatus,
    ChatRequest,
    ChunkItem,
    CreateSessionRequest,
    DocumentItem,
    DocumentQuery,
    DocumentReimportAccepted,
    DocumentUploadAccepted,
    EvaluationCase,
    EvaluationCaseCreate,
    EvaluationCaseQuery,
    EvaluationCaseUpdate,
    EvaluationRunCreate,
    EvaluationRunDetail,
    EvaluationRunJob,
    EvaluationRunQuery,
    EvaluationRunSummary,
    ExperimentCopy,
    ExperimentCreate,
    ExperimentDetail,
    ExperimentJob,
    ExperimentQuery,
    ExperimentSummary,
    JobStatus,
    MaterialChecklistInput,
    MessageItem,
    MissingKnowledgeItem,
    MissingKnowledgeQuery,
    MissingKnowledgeUpdate,
    RetrievalPreviewRequest,
    RetrievalPreviewResponse,
    RetrievalStrategy,
    RetrievalStrategyCreate,
    RetrievalStrategyStatusUpdate,
    RetrievalStrategyUpdate,
    RetrievalStrategyVersion,
    SessionItem,
    ToolExecutionItem,
)
from app.services.auth_service import UserAccount
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.evaluation_service import EvaluationService
from app.services.retrieval_experiment_service import RetrievalExperimentService

# 业务路由集合，负责注册劳动问答平台的 API 端点。
router = APIRouter(
    dependencies=[Depends(get_current_user), Depends(authorize_contract_access)]
)

# 统一声明错误响应的数据模型。
_ERROR_RESPONSE = {"model": ApiResponse[None]}
# 所有接口可返回的标准 HTTP 错误响应定义。
CONTRACT_RESPONSES = {
    400: {**_ERROR_RESPONSE, "description": "Invalid business request"},
    404: {**_ERROR_RESPONSE, "description": "Resource not found"},
    409: {**_ERROR_RESPONSE, "description": "Resource state conflict"},
    413: {**_ERROR_RESPONSE, "description": "Uploaded file is too large"},
    422: {**_ERROR_RESPONSE, "description": "Request validation failed"},
    500: {**_ERROR_RESPONSE, "description": "Internal server error"},
    501: {
        **_ERROR_RESPONSE,
        "description": "Business implementation is scheduled for a later phase",
    },
    503: {**_ERROR_RESPONSE, "description": "A required service is unavailable"},
}
# 业务已实现接口的错误响应定义，不包含仅冻结契约时使用的 501。
IMPLEMENTED_RESPONSES = {
    status: response for status, response in CONTRACT_RESPONSES.items() if status != 501
}


# 函数作用：声明仅用于冻结接口契约的占位处理；调用时始终抛出 501 AppError。
#
def _contract_only() -> NoReturn:
    raise AppError(
        code=ErrorCode.INTERNAL_ERROR,
        message="当前阶段仅冻结接口契约，业务功能将在后续阶段实现",
        http_status=501,
    )


# 函数作用：接收上传文件、创建文档记录，并将文档导入安排为后台任务。
#
# 参数：
#   file：FastAPI 接收的上传文件对象，提供客户端文件名、MIME 类型和二进制流；本路由将流与元数据交给文档服务校验并落盘。
#   background_tasks：FastAPI 后台任务队列；上传记录创建成功后，将解析和向量索引工作加入响应后的后台队列，使接口可先返回受理信息。
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#
# 返回：ApiResponse[DocumentUploadAccepted]；data 表示已受理文档的 ID、文件名和初始处理状态，前端可据此展示导入排队情况。
@router.post(
    "/documents/upload",
    response_model=ApiResponse[DocumentUploadAccepted],
    status_code=202,
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="Upload a document and start its import job",
)
async def upload_document(
    file: Annotated[UploadFile, File(description="PDF, DOC, DOCX, MD or TXT document")],
    background_tasks: BackgroundTasks,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[DocumentUploadAccepted]:
    # 局部变量 document：上传服务创建的文档数据库记录；其中 id 用于安排后台导入，文件名和状态则返回给前端显示受理结果。
    document = service.upload(file.file, file.filename, file.content_type)
    background_tasks.add_task(service.import_document, document["id"])
    return ApiResponse(
        code=0,
        message="accepted",
        data=DocumentUploadAccepted(
            document_id=document["id"],
            file_name=document["file_name"],
            status=document["status"],
        ),
    )


# 函数作用：按筛选与分页条件返回文档列表。
#
# 参数：
#   query：已通过 DocumentQuery 校验的文档列表条件：page/size 控制分页，status 和 keyword 分别筛选处理状态与文件名。
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#
# 返回：ApiResponse[PageResult[DocumentItem]]；data 表示分页结果，包含本页 items、page/size 及未按当前页截断的 total/pages。
@router.get(
    "/documents",
    response_model=ApiResponse[PageResult[DocumentItem]],
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="List documents",
)
def list_documents(
    query: Annotated[DocumentQuery, Query()],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[PageResult[DocumentItem]]:
    # 局部变量 items：按当前筛选、状态整理或分页逻辑形成的返回条目集合；列表中的每项会继续映射为接口约定的数据结构。
    # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
    items, total = service.repository.list_documents(
        page=query.page,
        size=query.size,
        status=query.status.value if query.status else None,
        keyword=query.keyword,
    )
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=PageResult(
            items=[
                DocumentItem.model_validate(_schema_fields(DocumentItem, item))
                for item in items
            ],
            page=query.page,
            size=query.size,
            total=total,
            pages=(total + query.size - 1) // query.size,
        ),
    )


# 函数作用：汇总内置资料文件及其导入处理状态。
#
# 参数：
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#
# 返回：ApiResponse[BuiltinDocumentStatus]；data 表示内置资料总量、成功/处理中/失败/缺失/过期统计，以及逐文件的来源键和导入状态。
@router.get(
    "/documents/builtin/status",
    response_model=ApiResponse[BuiltinDocumentStatus],
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="Get builtin document import status",
)
def get_builtin_document_status(
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[BuiltinDocumentStatus]:
    return ApiResponse(
        code=0,
        message="success",
        data=BuiltinDocumentStatus.model_validate(service.get_builtin_status()),
    )


# 函数作用：触发内置资料批量导入并返回任务受理结果。
#
# 参数：
#   background_tasks：FastAPI 后台任务队列；将计划导入的内置文档批次加入响应后的后台队列，使接口先返回各文档的受理及导入状态。
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#
# 返回：ApiResponse[BuiltinDocumentImportAccepted]；data 表示本次计划导入数量、已导入数量及受理状态；包含已跟踪文档 ID 供前端刷新进度。
@router.post(
    "/documents/builtin/import",
    response_model=ApiResponse[BuiltinDocumentImportAccepted],
    status_code=202,
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="Import repository-provided builtin documents",
)
def import_builtin_documents(
    background_tasks: BackgroundTasks,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[BuiltinDocumentImportAccepted]:
    # 局部变量 plan：内置资料扫描后生成的导入计划，包含待导入文档和任务标识。
    plan = service.prepare_builtin_import()
    if plan.scheduled_ids:
        background_tasks.add_task(
            service.import_builtin_batch,
            list(plan.scheduled_ids),
        )
    return ApiResponse(
        code=0,
        message="accepted",
        data=BuiltinDocumentImportAccepted(
            total=plan.total,
            already_imported=plan.already_imported,
            scheduled=len(plan.scheduled_ids),
            processing=len(plan.processing_ids),
            tracked_document_ids=plan.tracked_document_ids,
            all_imported=plan.all_imported,
        ),
    )


# 函数作用：按文档标识读取文档元数据和处理状态。
#
# 参数：
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#   id：文档主键，用于读取指定文档详情。
#
# 返回：ApiResponse[DocumentItem]；data 表示文档 ID、文件名、类型、来源、处理状态、分块数和时间戳等元数据，不包含文件正文。
@router.get(
    "/documents/{id}",
    response_model=ApiResponse[DocumentItem],
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="Get a document and its import status",
)
def get_document(
    id: int,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[DocumentItem]:
    # 局部变量 document：按 URL 中的文档 ID 读取的数据库记录；不存在时转换为 404，存在时映射为文档详情响应。
    document = service.repository.get_document(id)
    if document is None:
        raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "文档不存在", http_status=404)
    return ApiResponse(
        code=0,
        message="success",
        data=DocumentItem.model_validate(_schema_fields(DocumentItem, document)),
    )


# 函数作用：删除指定文档及其关联的分块、向量和文件数据。
#
# 参数：
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#   id：文档主键，用于定位并删除文档及其关联数据。
#
# 返回：ApiResponse[None]；data 表示统一成功响应，data 为 null；操作失败时由 AppError 映射为相应 HTTP 错误。
@router.delete(
    "/documents/{id}",
    response_model=ApiResponse[None],
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="Delete a document and its derived data",
)
def delete_document(
    id: int,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[None]:
    service.delete_document(id)
    return ApiResponse(code=0, message="deleted", data=None)


# 函数作用：重新提交指定文档的解析与索引任务。
#
# 参数：
#   background_tasks：FastAPI 后台任务队列；将指定文档的重新解析和索引工作加入响应后的后台队列，使接口先返回任务受理状态。
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#   id：文档主键，用于重新提交该文档的导入任务。
#
# 返回：ApiResponse[DocumentReimportAccepted]；data 表示重新导入任务受理信息，包含目标文档 ID 和当前处理状态。
@router.post(
    "/documents/{id}/reimport",
    response_model=ApiResponse[DocumentReimportAccepted],
    status_code=202,
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="Reimport a failed document",
)
def reimport_document(
    id: int,
    background_tasks: BackgroundTasks,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[DocumentReimportAccepted]:
    # 局部变量 document：重新导入服务返回的文档记录；其 ID 用于提交后台解析任务，状态用于告知前端当前受理进度。
    document = service.reimport(id)
    background_tasks.add_task(service.import_document, id)
    return ApiResponse(
        code=0,
        message="accepted",
        data=DocumentReimportAccepted(document_id=id, status=document["status"]),
    )


# 函数作用：分页读取指定文档的文本分块。
#
# 参数：
#   query：已通过 PageQuery 校验的分页参数：page 为从 1 开始的页码，size 为每页返回的分块数量。
#   service：文档业务服务实例，协调上传文件、文档记录、解析切块、向量索引及导入状态；当前路由通过它执行对应文档操作。
#   id：文档主键，用于限定要读取分块的所属文档。
#
# 返回：ApiResponse[PageResult[ChunkItem]]；data 表示分页结果，包含本页 items、page/size 及未按当前页截断的 total/pages。
@router.get(
    "/documents/{id}/chunks",
    response_model=ApiResponse[PageResult[ChunkItem]],
    responses=CONTRACT_RESPONSES,
    tags=["documents"],
    summary="List a document's chunks",
)
def list_document_chunks(
    id: int,
    query: Annotated[PageQuery, Query()],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[PageResult[ChunkItem]]:
    if service.repository.get_document(id) is None:
        raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "文档不存在", http_status=404)
    # 局部变量 items：按当前筛选、状态整理或分页逻辑形成的返回条目集合；列表中的每项会继续映射为接口约定的数据结构。
    # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
    items, total = service.repository.list_chunks(id, page=query.page, size=query.size)
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=PageResult(
            items=[
                ChunkItem.model_validate(_schema_fields(ChunkItem, item))
                for item in items
            ],
            page=query.page,
            size=query.size,
            total=total,
            pages=(total + query.size - 1) // query.size,
        ),
    )


# 函数作用：从仓储或服务返回的记录中筛出响应模型允许公开的字段，避免内部数据库列进入 API 响应。
#
# 参数：
#   model：目标 Pydantic 响应模型；其 model_fields 定义该接口允许输出的字段名集合。
#   source：仓储或业务服务返回的原始字段映射，可能含有响应模型不公开的内部字段。
#
# 返回：仅保留 source 中由 model.model_fields 声明的键值对，供调用方使用 model_validate 构建响应模型。
def _schema_fields(model: type, source: dict) -> dict:
    # 局部变量 field：响应模型允许公开的字段名；只有 source 含有同名键时，才会复制到输出映射。
    return {field: source[field] for field in model.model_fields if field in source}


# 函数作用：创建新的聊天会话并返回会话信息。
#
# 参数：
#   payload：已校验的会话创建请求，仅接收可选标题；未提供标题时由服务根据后续问题生成默认标题。
#   service：聊天业务服务实例，负责会话和消息、流式问答、材料清单及知识缺口操作；由应用启动时创建并通过请求上下文复用。
#
# 返回：ApiResponse[SessionItem]；data 表示会话 ID、标题及创建/更新时间，用于会话列表展示。
@router.post(
    "/sessions",
    response_model=ApiResponse[SessionItem],
    status_code=201,
    responses=IMPLEMENTED_RESPONSES,
    tags=["sessions"],
    summary="Create a chat session",
)
def create_session(
    payload: CreateSessionRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    current_user: Annotated[UserAccount, Depends(get_current_user)],
) -> ApiResponse[SessionItem]:
    # 局部变量 session：当前聊天会话记录。
    session = service.create_session(payload.title, owner_user_id=current_user.id)
    return ApiResponse(
        code=0,
        message="success",
        data=SessionItem.model_validate(session),
    )


# 函数作用：列出最近的聊天会话。
#
# 参数：
#   service：聊天业务服务实例，负责会话和消息、流式问答、材料清单及知识缺口操作；由应用启动时创建并通过请求上下文复用。
#   limit：会话列表本次最多返回的记录数；由 Query 限制在 1 至 100 之间。
#
# 返回：ApiResponse[list[SessionItem]]；data 表示按更新时间排序的会话摘要列表。
@router.get(
    "/sessions",
    response_model=ApiResponse[list[SessionItem]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["sessions"],
    summary="List recent chat sessions",
)
def list_sessions(
    service: Annotated[ChatService, Depends(get_chat_service)],
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[list[SessionItem]]:
    # 局部变量 sessions：本次查询返回的聊天会话集合。
    sessions = service.list_sessions(limit, owner_user_id=current_user.id)
    # 局部变量 session：当前聊天会话记录。
    return ApiResponse(
        code=0,
        message="success",
        data=[SessionItem.model_validate(session) for session in sessions],
    )


# 函数作用：读取指定聊天会话的消息记录。
#
# 参数：
#   service：聊天业务服务实例，负责会话和消息、流式问答、材料清单及知识缺口操作；由应用启动时创建并通过请求上下文复用。
#   id：UUID 格式的会话标识，用于读取该会话的消息历史。
#
# 返回：ApiResponse[list[MessageItem]]；data 表示指定会话的历史消息列表，包含引用和工具执行信息。
@router.get(
    "/sessions/{id}/messages",
    response_model=ApiResponse[list[MessageItem]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["sessions"],
    summary="List messages for a session",
)
def list_session_messages(
    id: UUID,
    service: Annotated[ChatService, Depends(get_chat_service)],
    current_user: Annotated[UserAccount, Depends(get_current_user)],
) -> ApiResponse[list[MessageItem]]:
    if service.get_session(id, owner_user_id=current_user.id) is None:
        raise AppError(ErrorCode.SESSION_NOT_FOUND, "会话不存在", http_status=404)
    # 局部变量 messages：当前会话的消息记录集合。
    messages = service.list_messages(id, owner_user_id=current_user.id)
    # 局部变量 message：需要记录、保存或返回的消息文本。
    return ApiResponse(
        code=0,
        message="success",
        data=[MessageItem.model_validate(message) for message in messages],
    )


# 函数作用：以服务器推送事件流执行聊天问答并返回逐步结果。
#
# 参数：
#   payload：已校验的聊天请求，包含会话 ID、问题文本、回答风格和 AI 模式；服务据此加载上下文并启动流式问答。
#   request：当前 FastAPI 请求上下文；用于读取应用级状态，并在流式输出期间检查客户端是否已断开。
#   service：聊天业务服务实例，负责会话和消息、流式问答、材料清单及知识缺口操作；由应用启动时创建并通过请求上下文复用。
#
# 返回：StreamingResponse；data 表示 SSE 流式响应，按 status、tool、token、sources、done 或 error 事件逐步返回问答进度与结果。
@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "SSE events: status stages, optional tool, one or more token events, "
                "sources, then done; "
                "errors use a terminal error event."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **IMPLEMENTED_RESPONSES,
    },
    tags=["chat"],
    summary="Stream a RAG answer using Server-Sent Events",
)
async def stream_chat(
    payload: ChatRequest,
    request: Request,
    service: Annotated[ChatService, Depends(get_chat_service)],
    current_user: Annotated[UserAccount, Depends(get_current_user)],
) -> StreamingResponse:
    if service.get_session(payload.session_id, owner_user_id=current_user.id) is None:
        raise AppError(ErrorCode.SESSION_NOT_FOUND, "会话不存在", http_status=404)
    return StreamingResponse(
        service.stream_chat(payload, request, owner_user_id=current_user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 函数作用：根据用户提供的劳动争议材料生成材料检查结果。
#
# 参数：
#   payload：已校验的材料检查输入，包含争议类型和事实描述；工具据此生成相应的证据材料清单。
#   service：聊天业务服务实例，负责会话和消息、流式问答、材料清单及知识缺口操作；由应用启动时创建并通过请求上下文复用。
#
# 返回：ApiResponse[ToolExecutionItem]；data 表示材料检查工具的输入、生成的材料清单及补充说明。
@router.post(
    "/tools/material-checklist",
    response_model=ApiResponse[ToolExecutionItem],
    responses=IMPLEMENTED_RESPONSES,
    tags=["tools"],
    summary="Generate a material checklist",
)
def material_checklist(
    payload: MaterialChecklistInput,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ApiResponse[ToolExecutionItem]:
    return ApiResponse(
        code=0,
        message="success",
        data=service.execute_material_checklist(payload),
    )


# 函数作用：按筛选和分页条件查询评估用例。
#
# 参数：
#   query：已校验的评测用例筛选条件：可按主题、用例类型、是否多轮、来源、状态和归档范围过滤，并携带分页参数。
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#
# 返回：ApiResponse[PageResult[EvaluationCase]]；data 表示分页结果，包含本页 items、page/size 及未按当前页截断的 total/pages。
@router.get(
    "/evaluations/cases",
    response_model=ApiResponse[PageResult[EvaluationCase]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="List evaluation cases",
)
def list_evaluation_cases(
    query: Annotated[EvaluationCaseQuery, Query()],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[PageResult[EvaluationCase]]:
    # 局部变量 items：按当前筛选、状态整理或分页逻辑形成的返回条目集合；列表中的每项会继续映射为接口约定的数据结构。
    # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
    items, total = service.list_cases(
        page=query.page,
        size=query.size,
        topic=query.topic,
        expected_type=query.expected_type.value if query.expected_type else None,
        is_multi_turn=query.is_multi_turn,
        origin=query.origin.value if query.origin else None,
        status=query.status.value if query.status else None,
        include_archived=query.include_archived,
    )
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=PageResult(
            items=[EvaluationCase.model_validate(item) for item in items],
            page=query.page,
            size=query.size,
            total=total,
            pages=(total + query.size - 1) // query.size,
        ),
    )


# 函数作用：创建一个用户维护的评估用例。
#
# 参数：
#   payload：已校验的新评测用例数据，包含主题、预期类型、多轮问题、预期要点、预期来源及合规提示要求；服务会先执行跨字段校验再持久化。
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#
# 返回：ApiResponse[EvaluationCase]；data 表示评测用例内容、来源、状态和版本等信息。
@router.post(
    "/evaluations/cases",
    response_model=ApiResponse[EvaluationCase],
    status_code=201,
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="Create a custom evaluation case",
)
def create_evaluation_case(
    payload: EvaluationCaseCreate,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[EvaluationCase]:
    return ApiResponse(
        code=0,
        message="created",
        data=EvaluationCase.model_validate(service.create_case(payload)),
    )


# 函数作用：读取指定评估用例。
#
# 参数：
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#   id：评测用例主键。
#
# 返回：ApiResponse[EvaluationCase]；data 表示评测用例内容、来源、状态和版本等信息。
@router.get(
    "/evaluations/cases/{id}",
    response_model=ApiResponse[EvaluationCase],
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="Get an evaluation case",
)
def get_evaluation_case(
    id: int,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[EvaluationCase]:
    return ApiResponse(
        code=0,
        message="success",
        data=EvaluationCase.model_validate(service.get_case(id, include_archived=True)),
    )


# 函数作用：更新指定评估用例的内容。
#
# 参数：
#   payload：已校验的评测用例更新数据，包含新的用例内容和版本号；版本用于拒绝基于过期详情提交的并发覆盖。
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#   id：待更新评测用例的主键。
#
# 返回：ApiResponse[EvaluationCase]；data 表示评测用例内容、来源、状态和版本等信息。
@router.patch(
    "/evaluations/cases/{id}",
    response_model=ApiResponse[EvaluationCase],
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="Update a custom evaluation case",
)
def update_evaluation_case(
    id: int,
    payload: EvaluationCaseUpdate,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[EvaluationCase]:
    return ApiResponse(
        code=0,
        message="updated",
        data=EvaluationCase.model_validate(service.update_case(id, payload)),
    )


# 函数作用：归档指定评估用例。
#
# 参数：
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#   id：待归档评测用例的主键。
#
# 返回：ApiResponse[None]；data 表示统一成功响应，data 为 null；操作失败时由 AppError 映射为相应 HTTP 错误。
@router.delete(
    "/evaluations/cases/{id}",
    response_model=ApiResponse[None],
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="Archive a custom evaluation case",
)
def delete_evaluation_case(
    id: int,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[None]:
    service.archive_case(id)
    return ApiResponse(code=0, message="deleted", data=None)


# 函数作用：创建评测运行并安排后台执行。
#
# 参数：
#   payload：已校验的评测任务配置，包含任务名称、可选用例 ID、回答风格、用例范围和 AI 模式；成功创建后由后台执行。
#   background_tasks：FastAPI 后台任务队列；将已创建评测运行的逐用例计算加入响应后的后台队列，使接口可先返回运行 ID 供前端轮询。
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#
# 返回：ApiResponse[EvaluationRunJob]；data 表示已创建评测任务的运行 ID，前端可据此轮询任务状态。
@router.post(
    "/evaluations/runs",
    response_model=ApiResponse[EvaluationRunJob],
    status_code=202,
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="Create an evaluation run",
)
def create_evaluation_run(
    payload: EvaluationRunCreate,
    background_tasks: BackgroundTasks,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[EvaluationRunJob]:
    # 局部变量 run：当前评测任务或运行记录。
    run = service.create_run(payload)
    background_tasks.add_task(service.execute_run, int(run["id"]))
    return ApiResponse(
        code=0,
        message="accepted",
        data=EvaluationRunJob(
            run_id=int(run["id"]),
            status=JobStatus.PENDING,
            progress_current=0,
            progress_total=int(run["progress_total"]),
            error_message=None,
        ),
    )


# 函数作用：按分页条件查询评测运行摘要。
#
# 参数：
#   query：已校验的评测运行查询条件：status 可选地限制运行状态，page/size 决定返回的摘要页范围。
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#
# 返回：ApiResponse[PageResult[EvaluationRunSummary]]；data 表示分页结果，包含本页 items、page/size 及未按当前页截断的 total/pages。
@router.get(
    "/evaluations/runs",
    response_model=ApiResponse[PageResult[EvaluationRunSummary]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="List evaluation runs",
)
def list_evaluation_runs(
    query: Annotated[EvaluationRunQuery, Query()],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[PageResult[EvaluationRunSummary]]:
    # 局部变量 items：按当前筛选、状态整理或分页逻辑形成的返回条目集合；列表中的每项会继续映射为接口约定的数据结构。
    # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
    items, total = service.list_runs(
        page=query.page,
        size=query.size,
        status=query.status.value if query.status else None,
    )
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=PageResult(
            items=[EvaluationRunSummary.model_validate(item) for item in items],
            page=query.page,
            size=query.size,
            total=total,
            pages=(total + query.size - 1) // query.size,
        ),
    )


# 函数作用：读取指定评测运行的状态、指标和用例结果。
#
# 参数：
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#   id：评测运行主键。
#
# 返回：ApiResponse[EvaluationRunDetail]；data 表示评测运行配置、汇总指标和逐用例结果。
@router.get(
    "/evaluations/runs/{id}",
    response_model=ApiResponse[EvaluationRunDetail],
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="Get an evaluation run",
)
def get_evaluation_run(
    id: int,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[EvaluationRunDetail]:
    return ApiResponse(
        code=0,
        message="success",
        data=EvaluationRunDetail.model_validate(
            _schema_fields(EvaluationRunDetail, service.get_run(id))
        ),
    )


# 函数作用：删除指定评测运行及其结果。
#
# 参数：
#   service：评测业务服务实例，负责用例校验与生命周期、评测运行创建和结果查询；耗时评测由后台任务执行。
#   id：待删除评测运行的主键。
#
# 返回：ApiResponse[None]；data 表示统一成功响应，data 为 null；操作失败时由 AppError 映射为相应 HTTP 错误。
@router.delete(
    "/evaluations/runs/{id}",
    response_model=ApiResponse[None],
    responses=IMPLEMENTED_RESPONSES,
    tags=["evaluations"],
    summary="Delete a completed or failed evaluation run",
)
def delete_evaluation_run(
    id: int,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> ApiResponse[None]:
    service.delete_run(id)
    return ApiResponse(code=0, message="deleted", data=None)


# 函数作用：按状态、关键词和排序条件查询知识缺口记录。
#
# 参数：
#   query：已校验的知识缺口筛选条件：status 和 keyword 过滤处理状态/主题内容，sort 决定按出现次数或最近发现时间排序，另含分页参数。
#   service：聊天业务服务实例，负责会话和消息、流式问答、材料清单及知识缺口操作；由应用启动时创建并通过请求上下文复用。
#
# 返回：ApiResponse[PageResult[MissingKnowledgeItem]]；data 表示分页结果，包含本页 items、page/size 及未按当前页截断的 total/pages。
@router.get(
    "/missing-knowledge",
    response_model=ApiResponse[PageResult[MissingKnowledgeItem]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["missing-knowledge"],
    summary="List missing-knowledge topics",
)
def list_missing_knowledge(
    query: Annotated[MissingKnowledgeQuery, Query()],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ApiResponse[PageResult[MissingKnowledgeItem]]:
    # 局部变量 items：按当前筛选、状态整理或分页逻辑形成的返回条目集合；列表中的每项会继续映射为接口约定的数据结构。
    # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
    items, total = service.list_missing_knowledge(query)
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=PageResult(
            items=[
                MissingKnowledgeItem.model_validate(
                    _schema_fields(MissingKnowledgeItem, item)
                )
                for item in items
            ],
            page=query.page,
            size=query.size,
            total=total,
            pages=(total + query.size - 1) // query.size,
        ),
    )


# 函数作用：更新知识缺口记录的处理状态和备注。
#
# 参数：
#   payload：已校验的知识缺口维护数据，包含新的处理状态和可选备注；记录 ID 来自路径参数。
#   service：聊天业务服务实例，负责会话和消息、流式问答、材料清单及知识缺口操作；由应用启动时创建并通过请求上下文复用。
#   id：知识缺口记录主键。
#
# 返回：ApiResponse[MissingKnowledgeItem]；data 表示知识缺口主题、样例问题、出现次数、处理状态、备注及首次/最近发现时间。
@router.patch(
    "/missing-knowledge/{id}",
    response_model=ApiResponse[MissingKnowledgeItem],
    responses=IMPLEMENTED_RESPONSES,
    tags=["missing-knowledge"],
    summary="Update a missing-knowledge topic",
)
def update_missing_knowledge(
    id: int,
    payload: MissingKnowledgeUpdate,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ApiResponse[MissingKnowledgeItem]:
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    item = service.update_missing_knowledge(id, payload)
    return ApiResponse(
        code=0,
        message="success",
        data=MissingKnowledgeItem.model_validate(
            _schema_fields(MissingKnowledgeItem, item)
        ),
    )


# 函数作用：列出检索策略及其配置。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   include_archived：是否把已归档资源纳入列表；false 时仅返回当前有效记录。
#
# 返回：ApiResponse[list[RetrievalStrategy]]；data 表示策略配置、当前版本、启用状态和归档状态等详情。
@router.get(
    "/retrieval-strategies",
    response_model=ApiResponse[list[RetrievalStrategy]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="List retrieval strategies",
)
def list_retrieval_strategies(
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
    include_archived: bool = Query(default=False),
) -> ApiResponse[list[RetrievalStrategy]]:
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=[
            RetrievalStrategy.model_validate(item)
            for item in service.list_strategies(include_archived=include_archived)
        ],
    )


# 函数作用：创建检索策略及其初始版本。
#
# 参数：
#   payload：已校验的新策略数据，包含策略名称、说明和完整检索参数；创建后会生成初始版本记录。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#
# 返回：ApiResponse[RetrievalStrategy]；data 表示策略配置、当前版本、启用状态和归档状态等详情。
@router.post(
    "/retrieval-strategies",
    response_model=ApiResponse[RetrievalStrategy],
    status_code=201,
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Create a retrieval strategy",
)
def create_retrieval_strategy(
    payload: RetrievalStrategyCreate,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[RetrievalStrategy]:
    return ApiResponse(
        code=0,
        message="created",
        data=RetrievalStrategy.model_validate(service.create_strategy(payload)),
    )


# 函数作用：启用或停用指定检索策略。
#
# 参数：
#   payload：已校验的策略状态变更，仅包含 is_active；内置策略或已归档策略仍受服务层规则约束。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：检索策略主键。
#
# 返回：ApiResponse[RetrievalStrategy]；data 表示策略配置、当前版本、启用状态和归档状态等详情。
@router.patch(
    "/retrieval-strategies/{id}/status",
    response_model=ApiResponse[RetrievalStrategy],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Enable or disable a custom retrieval strategy",
)
def update_retrieval_strategy_status(
    id: int,
    payload: RetrievalStrategyStatusUpdate,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[RetrievalStrategy]:
    return ApiResponse(
        code=0,
        message="success",
        data=RetrievalStrategy.model_validate(
            service.set_strategy_active(id, payload.is_active)
        ),
    )


# 函数作用：列出指定策略的历史版本。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：检索策略主键，用于读取其历史版本。
#
# 返回：ApiResponse[list[RetrievalStrategyVersion]]；data 表示指定策略某一历史版本的名称、说明、参数快照和创建时间。
@router.get(
    "/retrieval-strategies/{id}/versions",
    response_model=ApiResponse[list[RetrievalStrategyVersion]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="List retrieval strategy versions",
)
def list_retrieval_strategy_versions(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[list[RetrievalStrategyVersion]]:
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=[
            RetrievalStrategyVersion.model_validate(item)
            for item in service.list_strategy_versions(id)
        ],
    )


# 函数作用：将指定策略恢复到历史版本配置。
#
# 参数：
#   version：要恢复的检索策略历史版本号；与路径中的策略 ID 共同确定恢复快照。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：检索策略主键；与 version 一起确定要恢复的策略快照。
#
# 返回：ApiResponse[RetrievalStrategy]；data 表示策略配置、当前版本、启用状态和归档状态等详情。
@router.post(
    "/retrieval-strategies/{id}/versions/{version}/restore",
    response_model=ApiResponse[RetrievalStrategy],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Restore a retrieval strategy version",
)
def restore_retrieval_strategy_version(
    id: int,
    version: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[RetrievalStrategy]:
    return ApiResponse(
        code=0,
        message="restored",
        data=RetrievalStrategy.model_validate(
            service.restore_strategy_version(id, version)
        ),
    )


# 函数作用：归档指定检索策略。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：待归档检索策略的主键。
#
# 返回：ApiResponse[RetrievalStrategy]；data 表示策略配置、当前版本、启用状态和归档状态等详情。
@router.post(
    "/retrieval-strategies/{id}/archive",
    response_model=ApiResponse[RetrievalStrategy],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Archive a custom retrieval strategy",
)
def archive_retrieval_strategy(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[RetrievalStrategy]:
    return ApiResponse(
        code=0,
        message="archived",
        data=RetrievalStrategy.model_validate(service.archive_strategy(id)),
    )


# 函数作用：恢复已归档的检索策略。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：待恢复检索策略的主键。
#
# 返回：ApiResponse[RetrievalStrategy]；data 表示策略配置、当前版本、启用状态和归档状态等详情。
@router.post(
    "/retrieval-strategies/{id}/restore",
    response_model=ApiResponse[RetrievalStrategy],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Restore an archived custom retrieval strategy",
)
def restore_retrieval_strategy(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[RetrievalStrategy]:
    return ApiResponse(
        code=0,
        message="restored",
        data=RetrievalStrategy.model_validate(service.restore_strategy(id)),
    )


# 函数作用：更新策略配置并记录新版本。
#
# 参数：
#   payload：已校验的策略编辑数据，包含名称、说明和检索参数；服务会保存新版本以保留变更历史。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：待编辑检索策略的主键。
#
# 返回：ApiResponse[RetrievalStrategy]；data 表示策略配置、当前版本、启用状态和归档状态等详情。
@router.patch(
    "/retrieval-strategies/{id}",
    response_model=ApiResponse[RetrievalStrategy],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Update a custom retrieval strategy",
)
def update_retrieval_strategy(
    id: int,
    payload: RetrievalStrategyUpdate,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[RetrievalStrategy]:
    return ApiResponse(
        code=0,
        message="success",
        data=RetrievalStrategy.model_validate(service.update_strategy(id, payload)),
    )


# 函数作用：删除指定检索策略。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：待删除检索策略的主键。
#
# 返回：ApiResponse[None]；data 表示统一成功响应，data 为 null；操作失败时由 AppError 映射为相应 HTTP 错误。
@router.delete(
    "/retrieval-strategies/{id}",
    response_model=ApiResponse[None],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Delete a custom retrieval strategy",
)
def delete_retrieval_strategy(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[None]:
    service.delete_strategy(id)
    return ApiResponse(code=0, message="deleted", data=None)


# 函数作用：按分页和归档条件查询检索实验。
#
# 参数：
#   query：已校验的检索实验查询条件：status 筛选运行状态，include_archived 决定是否合并已归档记录，并由 page/size 控制分页。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#
# 返回：ApiResponse[PageResult[ExperimentSummary]]；data 表示分页结果，包含本页 items、page/size 及未按当前页截断的 total/pages。
@router.get(
    "/retrieval-experiments",
    response_model=ApiResponse[PageResult[ExperimentSummary]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="List retrieval experiments",
)
def list_experiments(
    query: Annotated[ExperimentQuery, Query()],
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[PageResult[ExperimentSummary]]:
    # 局部变量 items：按当前筛选、状态整理或分页逻辑形成的返回条目集合；列表中的每项会继续映射为接口约定的数据结构。
    # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
    items, total = service.list_experiments(
        page=query.page,
        size=query.size,
        status=query.status.value if query.status else None,
        include_archived=query.include_archived,
    )
    # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
    return ApiResponse(
        code=0,
        message="success",
        data=PageResult(
            items=[ExperimentSummary.model_validate(item) for item in items],
            page=query.page,
            size=query.size,
            total=total,
            pages=(total + query.size - 1) // query.size,
        ),
    )


# 函数作用：创建检索实验并安排后台执行。
#
# 参数：
#   payload：已校验的实验配置，包含名称、策略 ID、用例范围/可选用例、回答风格和 AI 模式；受理后在隔离配置下后台运行。
#   background_tasks：FastAPI 后台任务队列；将已受理的检索策略对比任务加入响应后的后台队列，使接口可先返回实验 ID 供前端轮询。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#
# 返回：ApiResponse[ExperimentJob]；data 表示已受理实验任务的 ID，前端据此刷新运行状态。
@router.post(
    "/retrieval-experiments",
    response_model=ApiResponse[ExperimentJob],
    status_code=202,
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Create a retrieval experiment",
)
def create_experiment(
    payload: ExperimentCreate,
    background_tasks: BackgroundTasks,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[ExperimentJob]:
    # 局部变量 experiment：刚创建并持久化的实验记录；其 ID 用于排入后台执行，受理响应则返回该 ID 供前端查询状态。
    experiment = service.create_experiment(payload)
    background_tasks.add_task(service.execute_experiment, int(experiment["id"]))
    return ApiResponse(
        code=0,
        message="accepted",
        data=ExperimentJob(
            experiment_id=int(experiment["id"]),
            status=JobStatus.PENDING,
            progress_current=0,
            progress_total=int(experiment["progress_total"]),
            error_message=None,
        ),
    )


# 函数作用：使用指定策略和参数执行一次检索预览。
#
# 参数：
#   payload：已校验的单次预览请求，包含问题、回答风格、策略 ID 和 AI 模式；不会创建持久化实验任务。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#
# 返回：ApiResponse[RetrievalPreviewResponse]；data 表示单次预览的回答、证据、检索轨迹、配置和限制说明，不附带持久化实验运行记录。
@router.post(
    "/retrieval-experiments/preview",
    response_model=ApiResponse[RetrievalPreviewResponse],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Preview one retrieval experiment query",
)
async def preview_retrieval_experiment(
    payload: RetrievalPreviewRequest,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[RetrievalPreviewResponse]:
    # 局部变量 result：当前函数调用或计算产生、供后续处理或返回的结果数据。
    result = await service.preview(payload)
    return ApiResponse(
        code=0,
        message="success",
        data=RetrievalPreviewResponse.model_validate(result),
    )


# 函数作用：复制已有实验配置并创建新的实验任务。
#
# 参数：
#   payload：已校验的实验复制请求，指定新实验名称；策略快照和配置由服务从路径 ID 指向的源实验复制。
#   background_tasks：FastAPI 后台任务队列；将复制生成的新实验加入响应后的后台队列，使接口先返回任务受理信息。
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：作为复制来源的检索实验主键。
#
# 返回：ApiResponse[ExperimentJob]；data 表示已受理实验任务的 ID，前端据此刷新运行状态。
@router.post(
    "/retrieval-experiments/{id}/copy",
    response_model=ApiResponse[ExperimentJob],
    status_code=202,
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Copy a retrieval experiment",
)
def copy_experiment(
    id: int,
    payload: ExperimentCopy,
    background_tasks: BackgroundTasks,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[ExperimentJob]:
    # 局部变量 experiment：由源实验复制生成的新记录；其 ID 用于安排独立运行，原实验配置和历史结果不会被覆盖。
    experiment = service.copy_experiment(id, payload.name)
    background_tasks.add_task(service.execute_experiment, int(experiment["id"]))
    return ApiResponse(
        code=0,
        message="accepted",
        data=ExperimentJob(
            experiment_id=int(experiment["id"]),
            status=JobStatus.PENDING,
            progress_current=0,
            progress_total=int(experiment["progress_total"]),
            error_message=None,
        ),
    )


# 函数作用：导出指定实验结果为 CSV 内容。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：检索实验主键，用于定位要导出的运行结果。
#
# 返回：ApiResponse[str]；data 表示统一响应中的 CSV 文本，供前端下载实验结果。
@router.get(
    "/retrieval-experiments/{id}/export",
    response_model=ApiResponse[str],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Export retrieval experiment results",
)
def export_experiment(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[str]:
    return ApiResponse(
        code=0,
        message="success",
        data=service.export_experiment(id),
    )


# 函数作用：归档指定检索实验。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：待归档检索实验的主键。
#
# 返回：ApiResponse[ExperimentSummary]；data 表示实验名称、状态、创建和更新时间及归档信息等列表摘要。
@router.post(
    "/retrieval-experiments/{id}/archive",
    response_model=ApiResponse[ExperimentSummary],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Archive a completed retrieval experiment",
)
def archive_experiment(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[ExperimentSummary]:
    return ApiResponse(
        code=0,
        message="archived",
        data=ExperimentSummary.model_validate(service.archive_experiment(id)),
    )


# 函数作用：恢复已归档的检索实验。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：待恢复检索实验的主键。
#
# 返回：ApiResponse[ExperimentSummary]；data 表示实验名称、状态、创建和更新时间及归档信息等列表摘要。
@router.post(
    "/retrieval-experiments/{id}/restore",
    response_model=ApiResponse[ExperimentSummary],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Restore an archived retrieval experiment",
)
def restore_experiment(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[ExperimentSummary]:
    return ApiResponse(
        code=0,
        message="restored",
        data=ExperimentSummary.model_validate(service.restore_experiment(id)),
    )


# 函数作用：读取指定检索实验及其执行结果。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：检索实验主键，用于读取详情和逐用例结果。
#
# 返回：ApiResponse[ExperimentDetail]；data 表示实验运行时的嵌入签名、配置快照、策略版本、配置指标和逐用例结果。
@router.get(
    "/retrieval-experiments/{id}",
    response_model=ApiResponse[ExperimentDetail],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Get a retrieval experiment",
)
def get_experiment(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[ExperimentDetail]:
    return ApiResponse(
        code=0,
        message="success",
        data=ExperimentDetail.model_validate(
            _schema_fields(ExperimentDetail, service.get_experiment(id))
        ),
    )


# 函数作用：删除指定检索实验及其结果。
#
# 参数：
#   service：检索实验业务服务实例，管理策略及版本、实验任务、单次预览与结果导出；具体召回流程交给其检索依赖执行。
#   id：待删除检索实验的主键。
#
# 返回：ApiResponse[None]；data 表示统一成功响应，data 为 null；操作失败时由 AppError 映射为相应 HTTP 错误。
@router.delete(
    "/retrieval-experiments/{id}",
    response_model=ApiResponse[None],
    responses=IMPLEMENTED_RESPONSES,
    tags=["retrieval-experiments"],
    summary="Delete a completed retrieval experiment",
)
def delete_experiment(
    id: int,
    service: Annotated[
        RetrievalExperimentService, Depends(get_retrieval_experiment_service)
    ],
) -> ApiResponse[None]:
    service.delete_experiment(id)
    return ApiResponse(code=0, message="deleted", data=None)
