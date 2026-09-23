# 文件作用：封装文档、文本块及导入任务相关的持久化查询和更新。
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings


class DocumentRepository:
    """负责文档主记录、文本分块、引用以及导入状态的 SQLite 持久化。仓储层封装事务、来源键去重和分页查询，不负责解析文件或调用嵌入模型。"""

    def __init__(self, database_path: Path | None = None) -> None:
        """初始化文档数据仓库及数据库文件位置。

        参数：
        database_path：SQLite 数据库文件路径。
        """
        # 成员变量 database_path：SQLite 数据库文件路径。
        self.database_path = database_path or settings.database_path

    def create_document(
        self,
        *,
        file_name: str,
        file_type: str,
        file_path: str,
        status: str = "PROCESSING",
        source_type: str = "UPLOAD",
        source_key: str | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        """在数据库中创建文档记录。

        参数：
        file_name：规范化后的文档文件名。
        status：需要读取或设置的处理状态。
        source_key：内置资料的稳定来源标识。
        file_type：根据扩展名识别的文档类型。
        file_path：源文件或保存文件的完整路径。
        source_type：文档来源类别，例如用户上传或系统内置。
        source_hash：源文件内容计算得到的摘要值。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                INSERT INTO document (
                    file_name, file_type, file_path, status,
                    source_type, source_key, source_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_name,
                    file_type,
                    file_path,
                    status,
                    source_type,
                    source_key,
                    source_hash,
                ),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM document WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        """按主键读取单份文档记录。

        参数：
        document_id：文档记录的唯一标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM document WHERE id = ?", (document_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def get_document_by_source_key(self, source_key: str) -> dict[str, Any] | None:
        """按内置资料来源键读取文档记录。

        参数：
        source_key：内置资料的稳定来源标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM document WHERE source_key = ?", (source_key,)
            ).fetchone()
            return dict(row) if row is not None else None

    def list_documents_by_file_name(self, file_name: str) -> list[dict[str, Any]]:
        """按文件名查找已有文档记录。

        参数：
        file_name：规范化后的文档文件名。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                "SELECT * FROM document WHERE file_name = ? "
                "ORDER BY id",
                (file_name,),
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows]

    def delete_document(self, document_id: int) -> dict[str, Any] | None:
        """在一个 SQLite 事务中读取待删文档及其分块标识，显式移除引用和分块后再删除文档记录；返回删除前的文档与分块信息，供服务层清理向量和源文件。

            参数：
            document_id：文档记录的唯一标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM document WHERE id = ?", (document_id,)
            ).fetchone()
            if row is None:
                return None

            # 局部变量 chunk_ids：当前操作涉及的文本分块标识列表。
            # 局部变量 chunk_row：数据库中的文本分块记录。
            chunk_ids = [
                int(chunk_row[0])
                for chunk_row in connection.execute(
                    "SELECT id FROM chunk WHERE document_id = ? ORDER BY id",
                    (document_id,),
                ).fetchall()
            ]
            # citation.chunk_id 不设置 ON DELETE CASCADE，因为正常情况下引用必须指向有效文本块；
            # 删除文档时先显式删除关联引用，再删除文本块，避免留下无效引用或触发外键错误。
            connection.execute(
                """
                DELETE FROM citation
                WHERE chunk_id IN (
                    SELECT id FROM chunk WHERE document_id = ?
                )
                """,
                (document_id,),
            )
            connection.execute(
                "DELETE FROM chunk WHERE document_id = ?", (document_id,)
            )
            connection.execute("DELETE FROM document WHERE id = ?", (document_id,))
            return {"document": dict(row), "chunk_ids": chunk_ids}

    def list_documents(
        self,
        *,
        page: int = 1,
        size: int = 20,
        status: str | None = None,
        keyword: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """按筛选与分页条件查询文档记录。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        keyword：当前查询使用的关键词。
        """
        if page < 1 or size < 1:
            raise ValueError("page and size must be positive")

        # 局部变量 conditions：当前查询使用的筛选条件。
        conditions: list[str] = []
        # 局部变量 parameters：当前操作使用的参数集合。
        parameters: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)
        if keyword:
            conditions.append("file_name LIKE ? ESCAPE '\\'")
            # 局部变量 escaped_keyword：转义后可安全用于 SQL LIKE 查询的关键词。
            escaped_keyword = (
                keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            parameters.append(f"%{escaped_keyword}%")
        # 局部变量 where：当前 SQL 查询的筛选子句。
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
            total = connection.execute(
                f"SELECT COUNT(*) FROM document {where}", parameters
            ).fetchone()[0]
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                f"SELECT * FROM document {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*parameters, size, (page - 1) * size],
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows], int(total)

    def update_document(
        self,
        document_id: int,
        *,
        status: str | None = None,
        error_message: str | None = None,
        chunk_count: int | None = None,
    ) -> dict[str, Any] | None:
        """更新文档处理状态及解析元数据。

        参数：
        status：需要读取或设置的处理状态。
        document_id：目标文档的唯一标识。
        error_message：需要记录或返回的错误说明。
        chunk_count：当前文档包含的分块数量。
        """
        # 局部变量 assignments：由非空可选字段生成的 SQL SET 子句列表；没有提供的字段不会覆盖数据库原值。
        assignments = ["error_message = ?", "updated_at = CURRENT_TIMESTAMP"]
        # 局部变量 values：待写入数据库或输出的字段值集合。
        values: list[Any] = [error_message]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if chunk_count is not None:
            assignments.append("chunk_count = ?")
            values.append(chunk_count)
        values.append(document_id)

        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            connection.execute(
                f"UPDATE document SET {', '.join(assignments)} WHERE id = ?", values
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM document WHERE id = ?", (document_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def update_document_source(
        self,
        document_id: int,
        *,
        file_path: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any] | None:
        """更新内置文档的来源和内容摘要信息。

        参数：
        source_key：内置资料的稳定来源标识。
        document_id：目标文档的唯一标识。
        file_path：源文件或保存文件的完整路径。
        source_type：文档来源类别，例如用户上传或系统内置。
        source_hash：源文件内容计算得到的摘要值。
        """
        # 局部变量 values_by_column：按数据库列顺序排列的写入字段值。
        values_by_column = {
            "file_path": file_path,
            "source_type": source_type,
            "source_key": source_key,
            "source_hash": source_hash,
        }
        # 局部变量 assignments：数据库迁移中需要执行的 SQL 语句集合。
        # 局部变量 column：当前处理的数据库列名。
        # 局部变量 value：某个可选文档元数据字段的新值；仅非空值会保留为参数并加入对应的 UPDATE 子句。
        assignments = [
            f"{column} = ?"
            for column, value in values_by_column.items()
            if value is not None
        ]
        # 局部变量 values：待写入数据库或输出的字段值集合。
        values = [
            value
            for value in values_by_column.values()
            if value is not None
        ]
        if not assignments:
            return self.get_document(document_id)

        assignments.append("updated_at = CURRENT_TIMESTAMP")
        values.append(document_id)
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            connection.execute(
                f"UPDATE document SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM document WHERE id = ?", (document_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def insert_chunks(
        self,
        document_id: int,
        chunks: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """批量保存文档解析得到的文本分块。

        参数：
        chunks：文本分块记录集合。
        document_id：目标文档的唯一标识。
        """
        # 局部变量 inserted：当前新建或写入的数据库记录。
        inserted: list[dict[str, Any]] = []
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 chunk：当前文本分块记录。
            for chunk in chunks:
                # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
                cursor = connection.execute(
                    """
                    INSERT INTO chunk (document_id, chunk_no, content, vector_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        int(chunk["chunk_no"]),
                        str(chunk["content"]),
                        f"pending-{uuid4()}",
                    ),
                )
                # 局部变量 chunk_id：文本分块的唯一标识。
                chunk_id = int(cursor.lastrowid)
                # 局部变量 vector_key：向量索引中的稳定存储键。
                vector_key = f"chunk:{chunk_id}"
                connection.execute(
                    "UPDATE chunk SET vector_key = ? WHERE id = ?",
                    (vector_key, chunk_id),
                )
                # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
                row = connection.execute(
                    "SELECT * FROM chunk WHERE id = ?", (chunk_id,)
                ).fetchone()
                inserted.append(dict(row))
        return inserted

    def delete_chunks(self, document_id: int) -> list[int]:
        """删除指定文档或分块关联的文本分块记录。

        参数：
        document_id：文档记录的唯一标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 ids：当前批量操作涉及的对象标识集合。
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM chunk WHERE document_id = ? ORDER BY id",
                    (document_id,),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM chunk WHERE document_id = ?", (document_id,)
            )
            return ids

    def reset_document_content(self, document_id: int) -> list[int]:
        """清除指定文档旧的分块和引用记录，但保留文档主记录，以便重新导入流程继续更新其状态并写入新解析结果。

            参数：
            document_id：文档记录的唯一标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 ids：当前批量操作涉及的对象标识集合。
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM chunk WHERE document_id = ? ORDER BY id",
                    (document_id,),
                ).fetchall()
            ]
            connection.execute(
                """
                DELETE FROM citation
                WHERE chunk_id IN (
                    SELECT id FROM chunk WHERE document_id = ?
                )
                """,
                (document_id,),
            )
            connection.execute(
                "DELETE FROM chunk WHERE document_id = ?", (document_id,)
            )
            return ids

    def list_chunks(
        self, document_id: int, *, page: int = 1, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """分页读取指定文档的文本分块。

        参数：
        page：查询页码。
        size：每页数据条数。
        document_id：目标文档的唯一标识。
        """
        if page < 1 or size < 1:
            raise ValueError("page and size must be positive")
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM chunk WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            )
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                SELECT * FROM chunk WHERE document_id = ?
                ORDER BY chunk_no, id LIMIT ? OFFSET ?
                """,
                (document_id, size, (page - 1) * size),
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows], total

    def list_success_chunks(self) -> list[dict[str, Any]]:
        """读取成功处理文档的有效分块。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                SELECT c.*, d.file_name, d.file_type
                FROM chunk AS c
                JOIN document AS d ON d.id = c.document_id
                WHERE d.status = 'SUCCESS'
                ORDER BY c.document_id, c.chunk_no, c.id
                """
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows]

    def get_chunks_by_ids(self, chunk_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        """按给定分块 ID 批量读取数据库中状态为成功的文本块；过滤失效或尚未完成的记录，供检索结果映射回权威正文及来源元数据。

            参数：
            chunk_ids：需要查询或更新的文本分块标识列表。
        """
        # 局部变量 normalized_ids：去重并规范化后的对象标识集合。
        # 局部变量 chunk_id：文本分块的唯一标识。
        normalized_ids = list(dict.fromkeys(int(chunk_id) for chunk_id in chunk_ids))
        if not normalized_ids:
            return {}

        # 局部变量 placeholders：提示模板中待填充的变量名集合。
        placeholders = ", ".join("?" for _ in normalized_ids)
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                f"""
                SELECT c.*, d.file_name, d.file_type
                FROM chunk AS c
                JOIN document AS d ON d.id = c.document_id
                WHERE d.status = 'SUCCESS' AND c.id IN ({placeholders})
                ORDER BY c.id
                """,
                normalized_ids,
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return {int(row["id"]): dict(row) for row in rows}

    def get_neighbor_chunks(
        self,
        document_id: int,
        chunk_no: int,
        *,
        radius: int = 1,
    ) -> list[dict[str, Any]]:
        """只从同一文档中读取指定锚点附近且导入成功的文本块；radius 控制前后扩展范围，避免把相邻文档内容混入证据上下文。

            参数：
            document_id：文档记录的唯一标识。
            chunk_no：文档中从 1 开始的文本块序号。
            radius：锚点前后需要读取的相邻文本块数量。
        """
        if radius < 0:
            raise ValueError("radius must not be negative")
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                SELECT c.*, d.file_name, d.file_type
                FROM chunk AS c
                JOIN document AS d ON d.id = c.document_id
                WHERE d.status = 'SUCCESS'
                  AND c.document_id = ?
                  AND c.chunk_no BETWEEN ? AND ?
                ORDER BY c.chunk_no, c.id
                """,
                (document_id, chunk_no - radius, chunk_no + radius),
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows]

    def list_success_documents(self) -> list[dict[str, Any]]:
        """读取处理成功且可用于检索的文档。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                SELECT id, file_name, file_type, file_path
                FROM document
                WHERE status = 'SUCCESS'
                ORDER BY id
                """
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows]

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """创建 SQLite 连接上下文并统一管理数据库连接。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
