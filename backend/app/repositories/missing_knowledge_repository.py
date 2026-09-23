# 文件作用：封装缺失知识记录的持久化、查询和状态更新。
"""持久化生产问答中因证据不足产生的知识缺口，包括主题聚合、出现次数、样例问题及人工处理状态，供后台维护页面查询和更新。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings


class MissingKnowledgeRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        """初始化知识缺口数据仓库及数据库文件位置。

        参数：
        database_path：SQLite 数据库文件路径。
        """
        # 成员变量 database_path：SQLite 数据库文件路径。
        self.database_path = database_path or settings.database_path

    def record(
        self, topic_key: str, sample_question: str, missing_area: str
    ) -> dict[str, Any]:
        """记录或合并一条因证据不足产生的知识缺口。

        参数：
        topic_key：用于归类知识缺口的主题键。
        sample_question：该知识缺口下具有代表性的问题。
        missing_area：知识库当前缺少的主题或资料方向。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO missing_knowledge (topic_key, sample_question, missing_area)
                VALUES (?, ?, ?)
                ON CONFLICT(topic_key) DO UPDATE SET
                    count = missing_knowledge.count + 1,
                    last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (topic_key, sample_question, missing_area),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM missing_knowledge WHERE topic_key = ?", (topic_key,)
            ).fetchone()
            return dict(row)

    def list(
        self,
        *,
        page: int,
        size: int,
        status: str | None,
        keyword: str | None,
        sort: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """按状态、关键词、排序和分页条件查询知识缺口。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        keyword：当前查询使用的关键词。
        sort：列表结果使用的排序方式。
        """
        # 局部变量 conditions：当前查询使用的筛选条件。
        conditions: list[str] = []
        # 局部变量 parameters：当前操作使用的参数集合。
        parameters: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)
        if keyword:
            # 局部变量 escaped：已完成转义的安全文本。
            escaped = (
                keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            conditions.append(
                "(topic_key LIKE ? ESCAPE char(92) "
                "OR sample_question LIKE ? ESCAPE char(92) "
                "OR missing_area LIKE ? ESCAPE char(92))"
            )
            # 局部变量 pattern：当前匹配或校验所使用的模式。
            pattern = f"%{escaped}%"
            parameters.extend((pattern, pattern, pattern))

        # 局部变量 where_clause：SQL 查询的筛选子句。
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        # 局部变量 order_clause：SQL 查询的排序子句。
        order_clause = {
            "count_desc": "count DESC, last_seen_at DESC, id DESC",
            "last_seen_desc": "last_seen_at DESC, id DESC",
        }[sort]
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 total：按当前筛选范围统计的记录/项目总量；分页接口用它计算总页数，数值可能大于本页实际返回的 items 长度。
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM missing_knowledge {where_clause}", parameters
                ).fetchone()[0]
            )
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                f"""
                SELECT * FROM missing_knowledge
                {where_clause}
                ORDER BY {order_clause}
                LIMIT ? OFFSET ?
                """,
                [*parameters, size, (page - 1) * size],
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows], total

    def update(
        self, item_id: int, status: str, note: str | None
    ) -> dict[str, Any] | None:
        """更新知识缺口的处理状态和备注。

        参数：
        status：需要读取或设置的处理状态。
        item_id：目标知识缺口记录的唯一标识。
        note：知识缺口记录的处理备注。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                UPDATE missing_knowledge
                SET status = ?, note = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, note, item_id),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM missing_knowledge WHERE id = ?", (item_id,)
            ).fetchone()
            return dict(row)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """创建 SQLite 连接上下文并统一管理数据库连接。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
