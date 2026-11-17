# 文件作用：封装聊天会话、消息、引用和工具执行记录的数据库读写。
"""封装聊天会话和消息的 SQLite 创建、读取、更新与删除；服务层负责问答流程，仓储层保证消息及引用的持久化关系。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.schemas.contracts import AiMode


class SessionNotFoundError(LookupError):
    """表示写入或读取操作引用了数据库中不存在的会话；服务层可据此返回明确的资源不存在响应，而不是把缺失记录误当作成功。"""


class SessionRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        """初始化聊天会话数据仓库及数据库文件位置。

        参数：
        database_path：SQLite 数据库文件路径。
        """
        # 成员变量 database_path：SQLite 数据库文件路径。
        self.database_path = database_path or settings.database_path

    def create_session(
        self, title: str | None = None, owner_user_id: str | None = None
    ) -> dict[str, Any]:
        """创建聊天会话记录。

        参数：
        title：新建或更新会话的展示标题。
        """
        # 局部变量 session_id：聊天会话的唯一标识。
        session_id = str(uuid4())
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO session (id, title, owner_user_id, ownership_state) "
                "VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    title,
                    owner_user_id,
                    "OWNED" if owner_user_id else "LEGACY_QUARANTINED",
                ),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM session WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row)

    def get_session(
        self, session_id: str, owner_user_id: str | None = None
    ) -> dict[str, Any] | None:
        """按会话标识读取会话元数据。

        参数：
        session_id：目标聊天会话标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                """SELECT * FROM session WHERE id = ? AND owner_user_id IS ?
                   AND ownership_state = ?""",
                (session_id, owner_user_id, self._ownership_state(owner_user_id)),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_sessions(
        self, limit: int = 20, owner_user_id: str | None = None
    ) -> list[dict[str, Any]]:
        """按更新时间倒序读取最近会话。

        参数：
        limit：返回记录数量上限。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                SELECT s.*
                FROM session AS s
                WHERE s.owner_user_id IS ?
                  AND s.ownership_state = ?
                  AND EXISTS (
                    SELECT 1
                    FROM message AS m
                    WHERE m.session_id = s.id
                )
                ORDER BY s.updated_at DESC, s.created_at DESC, s.id DESC
                LIMIT ?
                """,
                (owner_user_id, self._ownership_state(owner_user_id), limit),
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [dict(row) for row in rows]

    def list_messages(
        self, session_id: str, owner_user_id: str | None = None
    ) -> list[dict[str, Any]]:
        """读取指定会话的消息列表。

        参数：
        session_id：目标聊天会话标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            if not self._session_owned(connection, session_id, owner_user_id):
                raise SessionNotFoundError(session_id)
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                SELECT * FROM message
                WHERE session_id = ?
                ORDER BY created_at, id
                """,
                (session_id,),
            ).fetchall()
            return self._load_messages(connection, rows)

    def get_recent_context(
        self,
        session_id: str,
        turns: int = 3,
        owner_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """读取生成回答所需的最近上下文消息。

        参数：
        session_id：目标聊天会话标识。
        turns：需要读取或处理的对话轮次数。
        """
        if turns < 0:
            raise ValueError("turns must not be negative")
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            if not self._session_owned(connection, session_id, owner_user_id):
                raise SessionNotFoundError(session_id)
            if turns == 0:
                return []
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                WITH ordered_messages AS (
                    SELECT
                        id,
                        role,
                        created_at,
                        LAG(id) OVER (ORDER BY created_at, id) AS previous_id,
                        LAG(role) OVER (ORDER BY created_at, id) AS previous_role
                    FROM message
                    WHERE session_id = ?
                ), recent_turns AS (
                    SELECT previous_id AS user_id, id AS assistant_id
                    FROM ordered_messages
                    WHERE role = 'assistant' AND previous_role = 'user'
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                )
                SELECT * FROM message
                WHERE id IN (
                    SELECT user_id FROM recent_turns
                    UNION ALL
                    SELECT assistant_id FROM recent_turns
                )
                ORDER BY created_at, id
                """,
                (session_id, turns),
            ).fetchall()
            return self._load_messages(connection, rows)

    def create_user_message(
        self,
        session_id: str,
        question: str,
        ai_mode: AiMode | str = AiMode.BALANCED,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        """保存用户提交的问题消息。

        参数：
        session_id：目标聊天会话标识。
        question：用户提出的问题文本。
        ai_mode：生成回答时采用的 AI 模式。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            if (
                connection.execute(
                    """SELECT 1 FROM session WHERE id = ? AND owner_user_id IS ?
                       AND ownership_state = ?""",
                    (session_id, owner_user_id, self._ownership_state(owner_user_id)),
                ).fetchone()
                is None
            ):
                raise SessionNotFoundError(session_id)
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                INSERT INTO message (
                    session_id, role, content, rewritten_question, ai_mode
                )
                VALUES (?, 'user', ?, ?, ?)
                """,
                (session_id, question, question, AiMode(ai_mode).value),
            )
            connection.execute(
                """UPDATE session SET updated_at = CURRENT_TIMESTAMP WHERE id = ?
                   AND owner_user_id IS ? AND ownership_state = ?""",
                (session_id, owner_user_id, self._ownership_state(owner_user_id)),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM message WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self._load_messages(connection, [row])[0]

    def set_rewritten_question(
        self, message_id: int, text: str, owner_user_id: str | None = None
    ) -> bool:
        """保存问题改写后的检索文本。

        参数：
        text：待处理的文本内容。
        message_id：目标聊天消息的数据库标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                UPDATE message SET rewritten_question = ?
                WHERE id = ? AND role = 'user' AND EXISTS (
                    SELECT 1 FROM session AS s
                    WHERE s.id = message.session_id AND s.owner_user_id IS ?
                      AND s.ownership_state = ?
                )
                """,
                (text, message_id, owner_user_id, self._ownership_state(owner_user_id)),
            )
            return cursor.rowcount == 1

    def persist_assistant(
        self,
        session_id: str,
        content: str,
        answer_style: str,
        refused: bool,
        citations: Sequence[Mapping[str, Any]],
        tool_executions: Sequence[Mapping[str, Any]] = (),
        ai_mode: AiMode | str = AiMode.BALANCED,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        """保存助手回答、引用和工具执行信息。

        参数：
        session_id：目标聊天会话标识。
        answer_style：答案表达风格。
        content：需要处理或保存的正文内容。
        refused：模型回答是否因为证据不足或合规规则而拒答。
        citations：回答中引用的来源记录集合。
        tool_executions：本轮回答关联的工具执行记录集合。
        ai_mode：生成回答时采用的 AI 模式。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            if (
                connection.execute(
                    """SELECT 1 FROM session WHERE id = ? AND owner_user_id IS ?
                       AND ownership_state = ?""",
                    (session_id, owner_user_id, self._ownership_state(owner_user_id)),
                ).fetchone()
                is None
            ):
                raise SessionNotFoundError(session_id)

            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                INSERT INTO message (
                    session_id, role, content, answer_style, ai_mode, refused
                )
                VALUES (?, 'assistant', ?, ?, ?, ?)
                """,
                (
                    session_id,
                    content,
                    answer_style,
                    AiMode(ai_mode).value,
                    int(refused),
                ),
            )
            # 局部变量 message_id：当前会话消息的数据库标识。
            message_id = int(cursor.lastrowid)

            # 局部变量 citation：当前回答引用的资料来源记录。
            for citation in citations:
                connection.execute(
                    """
                    INSERT INTO citation (
                        message_id, chunk_id, score, retrieval_score,
                        rerank_score, lexical_score, source_score,
                        lightweight_score, evidence_origin, anchor_chunk_id,
                        anchor_retrieval_score, rank_no
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        citation["chunk_id"],
                        citation["score"],
                        citation["retrieval_score"],
                        citation.get("rerank_score"),
                        citation.get("lexical_score", 0.0),
                        citation.get("source_score", 0.0),
                        citation.get("lightweight_score", 0.0),
                        citation.get("evidence_origin", "RETRIEVED"),
                        citation.get("anchor_chunk_id"),
                        citation.get("anchor_retrieval_score"),
                        citation["rank_no"],
                    ),
                )

            # 局部变量 execution：当前工具调用的执行记录。
            for execution in tool_executions:
                connection.execute(
                    """
                    INSERT INTO tool_execution (
                        message_id, tool_name, input_json, output_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        execution["tool_name"],
                        json.dumps(execution["input"], ensure_ascii=False),
                        json.dumps(execution["output"], ensure_ascii=False),
                    ),
                )

            connection.execute(
                """UPDATE session SET updated_at = CURRENT_TIMESTAMP WHERE id = ?
                   AND owner_user_id IS ? AND ownership_state = ?""",
                (session_id, owner_user_id, self._ownership_state(owner_user_id)),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM message WHERE id = ?", (message_id,)
            ).fetchone()
            return self._load_messages(connection, [row])[0]

    @staticmethod
    def _load_messages(
        connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
    ) -> list[dict[str, Any]]:
        """按数据库存储顺序批量加载消息行。

        参数：
        connection：当前使用的 SQLite 数据库连接。
        rows：数据库查询返回的记录行集合。
        """
        # 局部变量 messages：当前会话的消息记录集合。
        # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
        messages = [
            {
                "id": int(row["id"]),
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "rewritten_question": row["rewritten_question"],
                "answer_style": row["answer_style"],
                "ai_mode": row["ai_mode"],
                "refused": bool(row["refused"]) if row["refused"] is not None else None,
                "created_at": row["created_at"],
                "citations": [],
                "tool_executions": [],
            }
            for row in rows
        ]
        if not messages:
            return messages

        # 局部变量 by_id：按资源标识建立的查询映射。
        # 局部变量 message：需要记录、保存或返回的消息文本。
        by_id = {message["id"]: message for message in messages}
        # 局部变量 placeholders：提示模板中待填充的变量名集合。
        placeholders = ", ".join("?" for _ in by_id)
        # 局部变量 message_ids：当前批量操作涉及的消息标识集合。
        message_ids = list(by_id)
        # 局部变量 citation_rows：当前消息引用来源对应的数据库记录。
        citation_rows = connection.execute(
            f"""
            SELECT
                c.message_id, c.chunk_id, ch.document_id, d.file_name,
                ch.chunk_no, ch.content, c.score, c.retrieval_score,
                c.rerank_score, c.lexical_score, c.source_score,
                c.lightweight_score, c.evidence_origin, c.anchor_chunk_id,
                c.anchor_retrieval_score, c.rank_no
            FROM citation AS c
            JOIN chunk AS ch ON ch.id = c.chunk_id
            JOIN document AS d ON d.id = ch.document_id
            WHERE c.message_id IN ({placeholders})
            ORDER BY c.message_id, c.rank_no, c.id
            """,
            message_ids,
        ).fetchall()
        # 局部变量 row：当前消息引用对应的数据库记录行。
        for row in citation_rows:
            by_id[int(row["message_id"])]["citations"].append(
                {
                    "chunk_id": int(row["chunk_id"]),
                    "document_id": int(row["document_id"]),
                    "file_name": row["file_name"],
                    "chunk_no": int(row["chunk_no"]),
                    "content": row["content"],
                    "score": float(row["score"]),
                    "retrieval_score": float(row["retrieval_score"]),
                    "rerank_score": (
                        float(row["rerank_score"])
                        if row["rerank_score"] is not None
                        else None
                    ),
                    "lexical_score": float(row["lexical_score"]),
                    "source_score": float(row["source_score"]),
                    "lightweight_score": float(row["lightweight_score"]),
                    "evidence_origin": str(row["evidence_origin"]),
                    "anchor_chunk_id": (
                        int(row["anchor_chunk_id"])
                        if row["anchor_chunk_id"] is not None
                        else None
                    ),
                    "anchor_retrieval_score": (
                        float(row["anchor_retrieval_score"])
                        if row["anchor_retrieval_score"] is not None
                        else None
                    ),
                    "rank_no": int(row["rank_no"]),
                }
            )

        # 局部变量 execution_rows：当前消息工具执行记录对应的数据库行。
        execution_rows = connection.execute(
            f"""
            SELECT message_id, tool_name, input_json, output_json
            FROM tool_execution
            WHERE message_id IN ({placeholders})
            ORDER BY message_id, created_at, id
            """,
            message_ids,
        ).fetchall()
        for row in execution_rows:
            by_id[int(row["message_id"])]["tool_executions"].append(
                {
                    "tool_name": row["tool_name"],
                    "input": json.loads(row["input_json"]),
                    "output": json.loads(row["output_json"]),
                }
            )
        return messages

    @staticmethod
    def _ownership_state(owner_user_id: str | None) -> str:
        """将有账号的会话与隔离的匿名历史数据区分开。"""
        return "OWNED" if owner_user_id else "LEGACY_QUARANTINED"

    @classmethod
    def _session_owned(
        cls,
        connection: sqlite3.Connection,
        session_id: str,
        owner_user_id: str | None,
    ) -> bool:
        """在当前数据事务中检查目标会话所有者，统一对外隐藏他人会话存在性。"""
        return (
            connection.execute(
                """SELECT 1 FROM session WHERE id = ? AND owner_user_id IS ?
               AND ownership_state = ?""",
                (session_id, owner_user_id, cls._ownership_state(owner_user_id)),
            ).fetchone()
            is not None
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """创建 SQLite 连接上下文并统一管理数据库连接。"""
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
