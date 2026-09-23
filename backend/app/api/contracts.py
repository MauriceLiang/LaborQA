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
    get_chat_service,
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
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.evaluation_service import EvaluationService
from app.services.retrieval_experiment_service import RetrievalExperimentService

# 业务路由集合，负责注册劳动问答平台的 API 端点。
router = APIRouter()

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
#   file：客户端上传的原始文件。
#   background_tasks：FastAPI 后台任务队列，用于安排耗时导入或评估工作。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 document：当前正在读取、更新或返回的文档记录。
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
#   query：经过校验的筛选、分页或检索预览请求条件。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 items：本次查询或处理得到的数据项列表。
    # 局部变量 total：符合当前条件的记录总数或项目总数。
    items, total = service.repository.list_documents(
        page=query.page,
        size=query.size,
        status=query.status.value if query.status else None,
        keyword=query.keyword,
    )
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
#   background_tasks：FastAPI 后台任务队列，用于安排耗时导入或评估工作。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 document：当前正在读取、更新或返回的文档记录。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   background_tasks：FastAPI 后台任务队列，用于安排耗时导入或评估工作。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 document：当前正在读取、更新或返回的文档记录。
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
#   query：经过校验的筛选、分页或检索预览请求条件。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 items：本次查询或处理得到的数据项列表。
    # 局部变量 total：符合当前条件的记录总数或项目总数。
    items, total = service.repository.list_chunks(id, page=query.page, size=query.size)
    # 局部变量 item：当前列表或结果集合中的一条数据。
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


# 函数作用：将 Pydantic 模型字段的校验错误路径转换为响应字段映射。
#
# 参数：
#   model：用于读取字段元数据的 Pydantic 模型类型。
#   source：待转换的模型字段错误来源数据。
#
# 返回：执行接口对应操作后的响应数据。
def _schema_fields(model: type, source: dict) -> dict:
    # 局部变量 field：当前模型或数据库记录的字段。
    return {field: source[field] for field in model.model_fields if field in source}


# 函数作用：创建新的聊天会话并返回会话信息。
#
# 参数：
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
) -> ApiResponse[SessionItem]:
    # 局部变量 session：当前聊天会话记录。
    session = service.create_session(payload.title)
    return ApiResponse(
        code=0,
        message="success",
        data=SessionItem.model_validate(session),
    )


# 函数作用：列出最近的聊天会话。
#
# 参数：
#   service：由依赖注入提供的对应业务服务实例。
#   limit：返回记录数量或处理数量上限。
#
# 返回：执行接口对应操作后的响应数据。
@router.get(
    "/sessions",
    response_model=ApiResponse[list[SessionItem]],
    responses=IMPLEMENTED_RESPONSES,
    tags=["sessions"],
    summary="List recent chat sessions",
)
def list_sessions(
    service: Annotated[ChatService, Depends(get_chat_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[list[SessionItem]]:
    # 局部变量 sessions：本次查询返回的聊天会话集合。
    sessions = service.list_sessions(limit)
    # 局部变量 session：当前聊天会话记录。
    return ApiResponse(
        code=0,
        message="success",
        data=[SessionItem.model_validate(session) for session in sessions],
    )


# 函数作用：读取指定聊天会话的消息记录。
#
# 参数：
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
) -> ApiResponse[list[MessageItem]]:
    if service.get_session(id) is None:
        raise AppError(ErrorCode.SESSION_NOT_FOUND, "会话不存在", http_status=404)
    # 局部变量 messages：当前会话的消息记录集合。
    messages = service.list_messages(id)
    # 局部变量 message：需要记录、保存或返回的消息文本。
    return ApiResponse(
        code=0,
        message="success",
        data=[MessageItem.model_validate(message) for message in messages],
    )


# 函数作用：以服务器推送事件流执行聊天问答并返回逐步结果。
#
# 参数：
#   payload：经过请求模型校验的业务输入数据。
#   request：当前 HTTP 请求对象，包含请求上下文和应用状态。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
) -> StreamingResponse:
    if service.get_session(payload.session_id) is None:
        raise AppError(ErrorCode.SESSION_NOT_FOUND, "会话不存在", http_status=404)
    return StreamingResponse(
        service.stream_chat(payload, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 函数作用：根据用户提供的劳动争议材料生成材料检查结果。
#
# 参数：
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
#   query：经过校验的筛选、分页或检索预览请求条件。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 items：本次查询或处理得到的数据项列表。
    # 局部变量 total：符合当前条件的记录总数或项目总数。
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
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   background_tasks：FastAPI 后台任务队列，用于安排耗时导入或评估工作。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
#   query：经过校验的筛选、分页或检索预览请求条件。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 items：本次查询或处理得到的数据项列表。
    # 局部变量 total：符合当前条件的记录总数或项目总数。
    items, total = service.list_runs(
        page=query.page,
        size=query.size,
        status=query.status.value if query.status else None,
    )
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   query：经过校验的筛选、分页或检索预览请求条件。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 items：本次查询或处理得到的数据项列表。
    # 局部变量 total：符合当前条件的记录总数或项目总数。
    items, total = service.list_missing_knowledge(query)
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   include_archived：查询结果是否包含已归档记录。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   version：需要恢复的策略历史版本号。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   query：经过校验的筛选、分页或检索预览请求条件。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 items：本次查询或处理得到的数据项列表。
    # 局部变量 total：符合当前条件的记录总数或项目总数。
    items, total = service.list_experiments(
        page=query.page,
        size=query.size,
        status=query.status.value if query.status else None,
        include_archived=query.include_archived,
    )
    # 局部变量 item：当前列表或结果集合中的一条数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   background_tasks：FastAPI 后台任务队列，用于安排耗时导入或评估工作。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 experiment：当前检索实验记录或运行配置。
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
#   payload：经过请求模型校验的业务输入数据。
#   service：由依赖注入提供的对应业务服务实例。
#
# 返回：执行接口对应操作后的响应数据。
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
#   payload：经过请求模型校验的业务输入数据。
#   background_tasks：FastAPI 后台任务队列，用于安排耗时导入或评估工作。
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
    # 局部变量 experiment：当前检索实验记录或运行配置。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
#   service：由依赖注入提供的对应业务服务实例。
#   id：目标资源的唯一标识。
#
# 返回：执行接口对应操作后的响应数据。
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
