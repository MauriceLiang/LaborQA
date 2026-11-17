# 文件作用：编排文档上传、解析、切分、嵌入、索引和生命周期操作。
"""编排文件上传校验、文档记录创建、后台解析切块、向量写入和导入状态更新；数据库记录是处理状态的依据，文件及向量清理由服务层协调。"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock
from typing import BinaryIO

from langchain_core.documents import Document

from app.core.config import Settings, settings
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.rag.embeddings import LangChainEmbeddingService
from app.rag.errors import EmbeddingUnavailableError
from app.rag.loaders import LaborQADocumentLoader
from app.rag.splitters import LegalTextSplitter
from app.repositories.document_repository import DocumentRepository
from app.schemas.contracts import BuiltinDocumentState
from app.services.builtin_documents import (
    BuiltinDocumentSpec,
    BuiltinImportPlan,
    list_builtin_documents,
    sha256_file,
)
from app.services.document_parser import ParserFactory, is_doc_parser_available
from app.services.file_storage import (
    DocumentConverterUnavailable,
    DocumentParseError,
    EmptyFileError,
    FileStorage,
    UploadValidationError,
)
from app.services.vector_store import VectorStoreError, VectorStoreService

# 文档上传、解析和索引操作使用的日志记录器。
logger = logging.getLogger(__name__)
# 进程重启后用于说明文档导入任务中断的错误文本。
_RESTART_ERROR = "服务重启导致任务中断"


class DocumentService:
    def __init__(
        self,
        config: Settings = settings,
        *,
        repository: DocumentRepository | None = None,
        file_storage: FileStorage | None = None,
        parser: type[ParserFactory] = ParserFactory,
        embedding_service: LangChainEmbeddingService | None = None,
        vector_store: VectorStoreService | None = None,
    ) -> None:
        """初始化文档服务及文件存储、解析、仓库和向量索引依赖。

        参数：
        config：当前应用或实验配置。
        repository：负责当前业务数据持久化的数据仓库实例。
        file_storage：负责上传文件校验和保存的存储实例。
        parser：负责读取和解析不同文档格式的组件。
        embedding_service：为文本生成向量的嵌入服务。
        vector_store：负责向量索引读写和相似度检索的服务实例。
        """
        # 成员变量 config：应用级 Settings，集中提供数据库、上传/内置资料目录、DOC 解析器和嵌入配置；文档导入各阶段共享同一配置来源。
        self.config = config
        # 成员变量 repository：负责当前业务数据持久化的数据仓库。
        self.repository = repository or DocumentRepository(config.database_path)
        # 成员变量 file_storage：校验并保存上传文件的存储组件。
        self.file_storage = file_storage or FileStorage(
            config.upload_path, max_size_bytes=config.max_upload_size_mb * 1024 * 1024
        )
        # 成员变量 parser：负责解析文档内容并返回文本及元数据的解析器实例。
        self.parser = parser
        # 成员变量 embedding_service：生成文本向量的嵌入服务实例。
        self.embedding_service = embedding_service or LangChainEmbeddingService(config)
        # 成员变量 vector_store：负责 FAISS 索引加载、检索和持久化的服务。
        self.vector_store = vector_store or VectorStoreService(
            config=config,
            embedding_service=self.embedding_service,
            index_dir=config.faiss_path / "production",
        )
        # 成员变量 splitter：将文档切分为文本块的切分器实例。
        self.splitter = LegalTextSplitter(config.chunk_size, config.chunk_overlap)
        # 成员变量 _import_lock：串行化文档导入的互斥锁。
        self._import_lock = RLock()

    def upload(
        self,
        source: BinaryIO,
        filename: str | None,
        content_type: str | None,
    ) -> dict:
        """保存上传文件并创建待处理文档记录。

        参数：
        filename：上传或读取文件时提供的文件名。
        source：待处理文本或记录的来源信息。
        content_type：上传请求声明的文件 MIME 类型。
        """
        try:
            # 局部变量 stored_file：文件存储组件返回的已保存文件信息。
            stored_file = self.file_storage.save(source, filename, content_type)
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except UploadValidationError as exc:
            raise AppError(exc.code, str(exc), exc.http_status) from exc

        if stored_file.file_type == "doc" and not is_doc_parser_available(self.config):
            self.file_storage.delete(stored_file.path)
            raise AppError(
                ErrorCode.DOC_CONVERTER_UNAVAILABLE,
                "DOC 解析器未安装或不可用",
                http_status=503,
            )

        try:
            return self.repository.create_document(
                file_name=stored_file.file_name,
                file_type=stored_file.file_type,
                file_path=str(stored_file.path),
                status="PROCESSING",
            )
        except Exception:
            self.file_storage.delete(stored_file.path)
            raise

    def get_builtin_status(self) -> dict:
        """扫描内置资料并汇总导入状态。"""
        with self._import_lock:
            # 局部变量 specs：扫描或注册得到的内置文档定义集合。
            specs = list_builtin_documents(self.config)
            # 局部变量 items：按当前筛选、状态整理或分页逻辑形成的返回条目集合；列表中的每项会继续映射为接口约定的数据结构。
            items: list[dict] = []
            # 局部变量 counts：本次统计得到的数量汇总。
            # 局部变量 state：当前文档、任务或服务处理状态。
            counts = {state.value.lower(): 0 for state in BuiltinDocumentState}
            # 局部变量 spec：当前内置文档的来源和元数据定义。
            for spec in specs:
                # 局部变量 document：文档仓储返回的数据库记录，包含文档 ID、来源和导入状态；当前流程据此比较内置文件版本并决定是否需要导入。
                document = self._get_or_claim_builtin_document(spec)
                # 局部变量 state：当前内置资料与源文件的状态比较结果。
                state = self._builtin_state(document, spec)
                counts[state.value.lower()] += 1
                items.append(
                    {
                        "source_key": spec.source_key,
                        "file_name": spec.file_name,
                        "state": state,
                        "document_id": document["id"] if document else None,
                    }
                )

            # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
            total = len(specs)
            # 局部变量 success：当前任务或操作是否成功。
            success = counts["success"]
            return {
                "total": total,
                "success": success,
                "processing": counts["processing"],
                "failed": counts["failed"],
                "missing": counts["missing"],
                "outdated": counts["outdated"],
                "all_imported": total > 0 and success == total,
                "items": items,
            }

    def prepare_builtin_import(self) -> BuiltinImportPlan:
        """扫描内置资料目录并对照已登记文档，选出缺失、失败或源文件已更新的项目，创建或领取待处理记录并生成后台导入计划。"""
        with self._import_lock:
            # 局部变量 specs：扫描或注册得到的内置文档定义集合。
            specs = list_builtin_documents(self.config)
            # 局部变量 scheduled_ids：已安排异步执行的对象标识列表。
            scheduled_ids: list[int] = []
            # 局部变量 processing_ids：仍处于处理中状态的文档 ID。
            processing_ids: list[int] = []
            # 局部变量 already_imported：已经存在且无需重复导入的资料数量。
            already_imported = 0

            # 局部变量 spec：当前内置文档的来源和元数据定义。
            for spec in specs:
                # 局部变量 document：文档仓储返回的数据库记录，包含文档 ID、来源和导入状态；当前流程据此比较内置文件版本并决定是否需要导入。
                document = self._get_or_claim_builtin_document(spec)
                # 局部变量 state：当前文档、任务或服务处理状态。
                state = self._builtin_state(document, spec)
                if state is BuiltinDocumentState.SUCCESS:
                    already_imported += 1
                    continue
                if state is BuiltinDocumentState.PROCESSING:
                    if document is not None:
                        processing_ids.append(int(document["id"]))
                    continue
                if state is BuiltinDocumentState.MISSING:
                    document = self._create_builtin_document(spec)
                else:
                    if document is None:
                        raise RuntimeError(
                            f"系统资料状态缺少 document: {spec.source_key}"
                        )
                    document = self._prepare_existing_builtin_document(document, spec)
                scheduled_ids.append(int(document["id"]))

            return BuiltinImportPlan(
                total=len(specs),
                already_imported=already_imported,
                scheduled_ids=tuple(scheduled_ids),
                processing_ids=tuple(processing_ids),
            )

    def import_builtin_batch(self, document_ids: list[int]) -> None:
        """按传入 ID 顺序复用单文档导入流程；单个任务若发生未预期异常会记录上下文并继续处理后续资料，避免批量导入被单项故障中断。

        参数：
        document_ids：本次批量操作涉及的文档标识集合。
        """
        # 局部变量 document_id：文档记录的唯一标识。
        for document_id in document_ids:
            try:
                self.import_document(document_id)
            except Exception:
                # 单文档导入流程通常会自行把解析或存储错误写成 FAILED 状态；
                # 若仍有基础设施异常逸出，则记录当前文档并继续批次，避免后续内置资料被跳过。
                logger.exception("Builtin document import raised for %s", document_id)

    def _get_or_claim_builtin_document(
        self,
        spec: BuiltinDocumentSpec,
    ) -> dict | None:
        """查找或原子认领一个内置资料文档记录。

        参数：
        spec：当前内置文档文件的来源和元数据定义。
        """
        # 局部变量 document：按 source_key 查到的内置文档记录；若记录已存在则复用其数据库 ID 和状态，避免重复创建来源条目。
        document = self.repository.get_document_by_source_key(spec.source_key)
        if document is not None:
            return document

        # 局部变量 candidate：当前待评估或排序的候选项。
        for candidate in self.repository.list_documents_by_file_name(spec.file_name):
            if candidate.get("source_type") != "UPLOAD":
                continue
            # 局部变量 candidate_path：当前候选的本地文件路径。
            candidate_path = Path(str(candidate["file_path"]))
            if not candidate_path.is_file():
                continue
            try:
                # 局部变量 candidate_hash：候选源文件的内容摘要值。
                candidate_hash = sha256_file(candidate_path)
            except OSError:
                continue
            if candidate_hash != spec.source_hash:
                continue
            # 局部变量 claimed：当前资料是否已被本进程成功认领。
            claimed = self.repository.update_document_source(
                int(candidate["id"]),
                source_type="BUILTIN",
                source_key=spec.source_key,
                source_hash=spec.source_hash,
            )
            if claimed is not None:
                return claimed
        return None

    @staticmethod
    def _builtin_state(
        document: dict | None,
        spec: BuiltinDocumentSpec,
    ) -> BuiltinDocumentState:
        """判断现有内置文档记录是否与源文件状态一致。

        参数：
        document：当前处理的文档对象或记录。
        spec：内置文档文件对应的来源定义。
        """
        if document is None:
            return BuiltinDocumentState.MISSING
        # 局部变量 status：当前业务对象或任务的处理状态。
        status = str(document["status"])
        if status == "PROCESSING":
            return BuiltinDocumentState.PROCESSING
        if status == "FAILED":
            return BuiltinDocumentState.FAILED
        if status == "SUCCESS" and document.get("source_hash") == spec.source_hash:
            return BuiltinDocumentState.SUCCESS
        return BuiltinDocumentState.OUTDATED

    def _create_builtin_document(self, spec: BuiltinDocumentSpec) -> dict:
        """根据内置资料文件创建文档记录和源信息。

        参数：
        spec：当前内置文档文件的来源和元数据定义。
        """
        # 局部变量 stored_file：文件存储组件返回的已保存文件信息。
        stored_file = self.file_storage.copy_from_path(
            spec.source_path,
            filename=spec.file_name,
        )
        try:
            return self.repository.create_document(
                file_name=spec.file_name,
                file_type=spec.file_type,
                file_path=str(stored_file.path),
                status="PROCESSING",
                source_type="BUILTIN",
                source_key=spec.source_key,
                source_hash=spec.source_hash,
            )
        except Exception:
            self.file_storage.delete(stored_file.path)
            raise

    def _prepare_existing_builtin_document(
        self,
        document: dict,
        spec: BuiltinDocumentSpec,
    ) -> dict:
        """准备已有内置文档记录以重新导入。

        参数：
        document：当前处理的文档对象或记录。
        spec：内置文档文件对应的来源定义。
        """
        # 局部变量 document_id：文档记录的唯一标识。
        document_id = int(document["id"])
        # 局部变量 old_path：需要替换或清理的旧文件路径。
        old_path = Path(str(document["file_path"]))
        # 局部变量 new_path：本次生成或替换后的新文件路径。
        new_path: Path | None = None
        if self._is_upload_path(old_path) and old_path.is_file():
            try:
                if sha256_file(old_path) == spec.source_hash:
                    new_path = old_path
            except OSError:
                new_path = None
        if new_path is None:
            # 局部变量 stored_file：文件存储组件返回的已保存文件信息。
            stored_file = self.file_storage.copy_from_path(
                spec.source_path,
                filename=spec.file_name,
            )
            new_path = stored_file.path

        try:
            # 局部变量 stale_ids：需要从索引或列表清理的过期标识。
            stale_ids = self.repository.reset_document_content(document_id)
            self._remove_vectors(stale_ids, "stale vectors before builtin refresh")
            # 局部变量 updated_source：更新后的文档来源信息。
            updated_source = self.repository.update_document_source(
                document_id,
                file_path=str(new_path),
                source_type="BUILTIN",
                source_key=spec.source_key,
                source_hash=spec.source_hash,
            )
            if updated_source is None:
                raise RuntimeError("系统资料来源更新失败")
            # 局部变量 updated：本次更新后得到的业务对象。
            updated = self.repository.update_document(
                document_id,
                status="PROCESSING",
                error_message=None,
                chunk_count=0,
            )
            if updated is None:
                raise RuntimeError("系统资料状态更新失败")
        except Exception:
            if new_path != old_path:
                self._delete_upload_copy(new_path)
            raise

        if new_path != old_path:
            self._delete_upload_copy(old_path)
        return updated

    def _is_upload_path(self, path: Path) -> bool:
        """判断给定路径是否属于用户上传文件目录。

        参数：
        path：输入文件或索引路径。
        """
        try:
            return (
                path.expanduser().resolve().is_relative_to(self.file_storage.upload_dir)
            )
        except OSError:
            return False

    def _delete_upload_copy(self, path: Path) -> None:
        """删除文档关联的上传文件副本。

        参数：
        path：输入文件或索引路径。
        """
        if not path.exists() or not self._is_upload_path(path):
            return
        try:
            self.file_storage.delete(path)
        except Exception:
            logger.exception("Could not remove builtin working copy %s", path)

    def reimport(self, document_id: int) -> dict:
        """重新解析并索引指定文档。

        参数：
        document_id：文档记录的唯一标识。
        """
        with self._import_lock:
            # 局部变量 document：按主键读取的文档数据库记录；本方法先校验存在性，再据其文件路径和分块信息执行删除或重导入操作。
            document = self.repository.get_document(document_id)
            if document is None:
                raise AppError(
                    ErrorCode.DOCUMENT_NOT_FOUND, "文档不存在", http_status=404
                )
            if document["status"] != "FAILED":
                raise AppError(
                    ErrorCode.INVALID_DOCUMENT_STATE,
                    "只有导入失败的文档可以重新导入",
                    http_status=409,
                )

            # 局部变量 stale_ids：需要从索引或列表清理的过期标识。
            stale_ids = self.repository.reset_document_content(document_id)
            self._remove_vectors(stale_ids, "stale vectors before reimport")
            return self.repository.update_document(
                document_id,
                status="PROCESSING",
                error_message=None,
                chunk_count=0,
            )

    def delete_document(self, document_id: int) -> dict:
        """协调删除文档主记录、分块、引用、向量和源文件；数据库事务先确定删除结果，后续向量或文件清理失败会留下日志以便运维发现孤儿数据。

        参数：
        document_id：文档记录的唯一标识。
        """
        with self._import_lock:
            # 局部变量 deleted：当前文件是否已经删除。
            deleted = self.repository.delete_document(document_id)
            if deleted is None:
                raise AppError(
                    ErrorCode.DOCUMENT_NOT_FOUND, "文档不存在", http_status=404
                )

            # 局部变量 document：仓储删除事务返回的文档记录；后续用它的 ID 清理向量索引，并用 file_path 删除已不再被数据库引用的源文件。
            document = deleted["document"]
            self._remove_vectors(
                deleted["chunk_ids"], "vectors after document deletion"
            )
            try:
                self.file_storage.delete(document["file_path"])
            except Exception:
                # 数据库事务已完成删除，文档记录是系统状态的权威来源；
                # 源文件清理失败不回滚删除请求，但必须记录路径和文档 ID 以便后续排查孤儿文件。
                logger.exception(
                    "Could not remove source file for deleted document %s",
                    document_id,
                )
            return document

    def import_document(self, document_id: int) -> None:
        """作为后台任务处理一份文档：读取已保存文件、解析并切分正文、写入数据库和向量索引，同时更新处理状态；可恢复错误会转换为失败状态供界面查看。

        参数：
        document_id：文档记录的唯一标识。
        """
        with self._import_lock:
            # 局部变量 document：按任务 ID 重新读取的文档记录；只有仍处于 PROCESSING 状态时才继续解析，避免重复执行已结束的导入任务。
            document = self.repository.get_document(document_id)
            if document is None or document["status"] != "PROCESSING":
                return

            # 局部变量 inserted_rows：本次数据库写入的记录数。
            inserted_rows: list[dict] = []
            # 局部变量 added_vector_ids：本次新增向量在索引中的标识。
            added_vector_ids: list[int] = []
            try:
                # 局部变量 source_documents：本次批量处理使用的原始文档集合。
                source_documents = self._load_documents(document)
                # 局部变量 chunks：当前文档或任务涉及的文本分块集合。
                chunks = self.splitter.split_documents(source_documents)
                if not chunks:
                    raise EmptyFileError()

                # 局部变量 vectors：文档或查询文本对应的数值向量集合。
                # 局部变量 chunk：当前文本分块记录。
                vectors = self.embedding_service.embed_documents(
                    [chunk.page_content for chunk in chunks]
                )
                if len(vectors) != len(chunks):
                    raise EmbeddingUnavailableError("向量服务返回的结果数量不正确")

                inserted_rows = self.repository.insert_chunks(
                    document_id,
                    [
                        {
                            "chunk_no": int(chunk.metadata["chunk_no"]),
                            "content": chunk.page_content,
                        }
                        for chunk in chunks
                    ],
                )
                # 局部变量 vector_documents：待加入向量索引的 LangChain 文档集合。
                # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
                vector_documents = [
                    Document(
                        id=str(row["id"]),
                        page_content=chunk.page_content,
                        metadata={
                            "chunk_id": int(row["id"]),
                            "document_id": document_id,
                            "file_name": str(document["file_name"]),
                            "file_type": str(document["file_type"]),
                            "chunk_no": int(row["chunk_no"]),
                        },
                    )
                    for row, chunk in zip(inserted_rows, chunks, strict=True)
                ]
                # 局部变量 added_ids：本次成功写入索引的标识列表。
                added_ids = self.vector_store.add_documents(
                    vector_documents,
                    vectors=vectors,
                    ids=[str(row["id"]) for row in inserted_rows],
                )
                # 局部变量 chunk_id：文本分块的唯一标识。
                added_vector_ids = [int(chunk_id) for chunk_id in added_ids]

                # 局部变量 updated：本次更新后得到的业务对象。
                updated = self.repository.update_document(
                    document_id,
                    status="SUCCESS",
                    error_message=None,
                    chunk_count=len(inserted_rows),
                )
                if updated is None:
                    raise RuntimeError("文档状态更新失败")
            # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
            except Exception as exc:
                self._rollback_import(document_id, inserted_rows, added_vector_ids)
                # 局部变量 message：需要记录、保存或返回的消息文本。
                message = self._error_message(exc)
                try:
                    self.repository.update_document(
                        document_id,
                        status="FAILED",
                        error_message=message[:500],
                        chunk_count=0,
                    )
                except Exception:
                    logger.exception("Could not mark failed document %s", document_id)
                logger.exception("Document import failed for document %s", document_id)

    def _load_documents(self, document: dict) -> list[Document]:
        """从数据库加载可用于检索的文档内容。

        参数：
        document：当前处理的文档对象或记录。
        """
        # 局部变量 metadata：写入每个文本块的文档来源元数据，包含 document_id、file_name 和 file_type，供向量检索命中后回查原始记录。
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

    def recover_interrupted_imports(self) -> None:
        """应用启动时查找上次进程退出后仍标记为处理中或正在删除的文档，将中断任务恢复到明确失败状态，并清理不完整的分块记录。"""
        # 局部变量 stale_vector_ids：需要从索引清理的旧向量 ID。
        stale_vector_ids: list[int] = []
        # 局部变量 page：当前查询使用的页码。
        page = 1
        while True:
            # 局部变量 documents：本次检索、解析或批量处理的文档集合。
            # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
            documents, total = self.repository.list_documents(
                page=page, size=100, status="PROCESSING"
            )
            if not documents:
                break
            # 局部变量 document：本批次中需要重新整理的文档记录；根据其 ID 清除旧分块，并把旧向量标识加入后续索引清理集合。
            for document in documents:
                stale_vector_ids.extend(
                    self.repository.reset_document_content(document["id"])
                )
                self.repository.update_document(
                    document["id"],
                    status="FAILED",
                    error_message=_RESTART_ERROR,
                    chunk_count=0,
                )
            if page * 100 >= total:
                break
            page += 1

        try:
            # 局部变量 compatible：当前模型与已有索引是否兼容。
            compatible = self.vector_store.load()
        except VectorStoreError:
            compatible = False
            logger.exception("Production vector index could not be loaded")
        if stale_vector_ids and compatible:
            try:
                self.vector_store.remove(stale_vector_ids)
            except Exception:
                logger.exception("Could not remove vectors from interrupted imports")
                self._rebuild_index()

    def _rollback_import(
        self,
        document_id: int,
        inserted_rows: list[dict],
        added_vector_ids: list[int],
    ) -> None:
        """导入失败时回滚文档状态和部分写入结果。

        参数：
        document_id：文档记录的唯一标识。
        inserted_rows：本次数据库写入的记录数。
        added_vector_ids：本次新增向量在索引中的标识。
        """
        try:
            # 局部变量 deleted_ids：本次从存储中删除的对象标识。
            deleted_ids = self.repository.delete_chunks(document_id)
        except Exception:
            logger.exception("Could not remove chunks after import failure")
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            deleted_ids = [row["id"] for row in inserted_rows]

        # 局部变量 ids_to_remove：本次需要从索引或数据库删除的标识集合。
        ids_to_remove = list(dict.fromkeys(added_vector_ids + deleted_ids))
        self._remove_vectors(ids_to_remove, "vectors after import failure")

    def _remove_vectors(self, chunk_ids: list[int], context: str) -> None:
        """移除指定文本分块对应的向量。

        参数：
        chunk_ids：需要查询或更新的文本分块标识列表。
        context：生成回答时使用的历史对话上下文。
        """
        if not chunk_ids:
            return
        try:
            if not self.vector_store.is_signature_compatible():
                self.vector_store.load()
            if self.vector_store.is_signature_compatible():
                self.vector_store.remove(chunk_ids)
        except Exception:
            logger.exception("Could not remove %s", context)
            self._rebuild_index()

    def _rebuild_index(self) -> None:
        """根据有效文档重建向量索引。"""
        try:
            # 局部变量 chunks：当前文档或任务涉及的文本分块集合。
            chunks = self.repository.list_success_chunks()
            if not chunks:
                self.vector_store.rebuild_from_success_chunks([])
                return
            # 局部变量 vectors：文档或查询文本对应的数值向量集合。
            # 局部变量 chunk：当前文本分块记录。
            vectors = self.embedding_service.embed_documents(
                [chunk["content"] for chunk in chunks]
            )
            if len(vectors) != len(chunks):
                raise EmbeddingUnavailableError("向量服务返回的结果数量不正确")
            # 局部变量 vector：单条文本对应的数值向量。
            self.vector_store.rebuild_from_success_chunks(
                [
                    (chunk["id"], vector)
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )
        except Exception:
            logger.exception("Could not rebuild the production vector index")

    @staticmethod
    def _error_message(exc: Exception) -> str:
        """把导入异常转换为面向接口的错误说明。

        参数：
        exc：当前捕获到的异常对象。
        """
        if isinstance(exc, DocumentConverterUnavailable):
            return "DOC 解析器未安装或不可用"
        if isinstance(exc, DocumentParseError | EmptyFileError):
            return str(exc)
        if isinstance(exc, EmbeddingUnavailableError):
            return str(exc)
        return "文档导入失败"
