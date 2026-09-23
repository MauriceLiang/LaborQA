# 文件作用：封装检索实验、运行配置和实验结果的数据库访问。
"""SQLite persistence for retrieval experiments and per-case results."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings


class RetrievalExperimentRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        """初始化检索实验数据仓库及数据库文件位置。

        参数：
        database_path：SQLite 数据库文件路径。
        """
        # 成员变量 database_path：SQLite 数据库文件路径。
        self.database_path = database_path or settings.database_path

    def create_experiment(
        self,
        name: str,
        case_count: int,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """创建检索实验及其初始运行状态。

        参数：
        name：资源名称或模型名称。
        snapshot：实验启动时保存的输入快照。
        case_count：本次实验或评测包含的用例数量。
        """
        # 局部变量 configs：本次比较的检索配置列表。
        configs = snapshot.get("strategy_snapshots") or snapshot.get("configs", [])
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                """
                INSERT INTO retrieval_experiment (
                    name, case_count, progress_total, config_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    name,
                    case_count,
                    case_count * len(configs),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM retrieval_experiment WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return self._summary(row)

    def set_running(self, experiment_id: int) -> None:
        """将检索实验状态更新为运行中。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE retrieval_experiment
                SET status = 'RUNNING', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (experiment_id,),
            )

    def add_result(self, experiment_id: int, result: dict[str, Any]) -> None:
        """保存单条策略配置和用例的实验结果。

        参数：
        experiment_id：目标检索实验标识。
        result：本次操作或模型调用的结果数据。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO retrieval_experiment_result (
                    experiment_id, config_index, case_id, status,
                    retrieved_sources_json, source_hit, correct, refused,
                    retrieval_ms, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    result["config_index"],
                    result["case_id"],
                    result["status"],
                    json.dumps(result["retrieved_sources"], ensure_ascii=False),
                    _db_bool(result.get("source_hit")),
                    _db_bool(result.get("correct")),
                    _db_bool(result.get("refused")),
                    result.get("retrieval_ms"),
                    result.get("error_message"),
                ),
            )
            connection.execute(
                """
                UPDATE retrieval_experiment
                SET progress_current = progress_current + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (experiment_id,),
            )

    def complete_experiment(
        self,
        experiment_id: int,
        best_config_index: int | None,
        metrics: dict[str, float | None] | None,
    ) -> None:
        """完成实验并保存汇总结果。

        参数：
        experiment_id：目标检索实验标识。
        metrics：运行结束后计算的汇总指标。
        best_config_index：表现最佳配置在实验结果中的序号。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE retrieval_experiment
                SET status = 'COMPLETED', error_message = NULL,
                    best_config_index = ?, accuracy = ?, reject_rate = ?,
                    citation_hit_rate = ?, avg_retrieval_ms = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    best_config_index,
                    metrics["accuracy"] if metrics else None,
                    metrics["reject_rate"] if metrics else None,
                    metrics["citation_hit_rate"] if metrics else None,
                    metrics["avg_retrieval_ms"] if metrics else None,
                    experiment_id,
                ),
            )

    def fail_experiment(self, experiment_id: int, message: str) -> None:
        """将实验标记为失败并记录错误信息。

        参数：
        experiment_id：目标检索实验标识。
        message：需要保存或展示的消息内容。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE retrieval_experiment
                SET status = 'FAILED', error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (message[:500], experiment_id),
            )

    def recover_interrupted_experiments(self) -> None:
        """恢复进程中断后遗留的实验运行状态。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE retrieval_experiment
                SET status = 'FAILED', error_message = '服务重启导致任务中断',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('PENDING', 'RUNNING')
                """
            )

    def list_experiments(
        self,
        *,
        page: int,
        size: int,
        status: str | None,
        include_archived: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """按筛选和分页条件查询检索实验。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        include_archived：查询结果是否包含已归档记录。
        """
        # 局部变量 conditions：当前查询使用的筛选条件。
        conditions: list[str] = []
        # 局部变量 parameters：当前操作使用的参数集合。
        parameters: list[Any] = []
        if status:
            conditions.append("status = ?")
            parameters.append(status)
        if not include_archived:
            conditions.append("archived_at IS NULL")
        # 局部变量 where_clause：SQL 查询的筛选子句。
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 total：符合当前条件的记录总数或项目总数。
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM retrieval_experiment {where_clause}",
                    parameters,
                ).fetchone()[0]
            )
            # 局部变量 rows：本次查询返回的数据库记录集合。
            rows = connection.execute(
                f"""
                SELECT * FROM retrieval_experiment
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, size, (page - 1) * size],
            ).fetchall()
            # 局部变量 row：数据库查询返回的当前记录行。
            return [self._summary(row) for row in rows], total

    def archive_experiment(self, experiment_id: int) -> dict[str, Any] | None:
        """归档检索实验并更新时间。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                """
                UPDATE retrieval_experiment
                SET archived_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('COMPLETED', 'FAILED')
                  AND archived_at IS NULL
                """,
                (experiment_id,),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM retrieval_experiment WHERE id = ?", (experiment_id,)
            ).fetchone()
            return self._summary(row) if row is not None else None

    def restore_experiment(self, experiment_id: int) -> dict[str, Any] | None:
        """恢复已归档的检索实验。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                """
                UPDATE retrieval_experiment
                SET archived_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND archived_at IS NOT NULL
                """,
                (experiment_id,),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM retrieval_experiment WHERE id = ?", (experiment_id,)
            ).fetchone()
            return self._summary(row) if row is not None else None

    def get_experiment(self, experiment_id: int) -> dict[str, Any] | None:
        """读取检索实验详情和关联结果。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM retrieval_experiment WHERE id = ?", (experiment_id,)
            ).fetchone()
            if row is None:
                return None
            # 局部变量 results：本次批量处理生成的结果记录集合。
            results = connection.execute(
                """
                SELECT * FROM retrieval_experiment_result
                WHERE experiment_id = ? ORDER BY id
                """,
                (experiment_id,),
            ).fetchall()
            # 局部变量 item：当前列表或结果集合中的一条数据。
            return {
                **self._summary(row),
                "snapshot": json.loads(row["config_json"]),
                "best_config_index": row["best_config_index"],
                "results": [self._result(item) for item in results],
            }

    def delete_experiment(self, experiment_id: int) -> bool:
        """删除检索实验及其结果记录。

        参数：
        experiment_id：目标检索实验标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                "DELETE FROM retrieval_experiment WHERE id = ?", (experiment_id,)
            )
            return cursor.rowcount > 0

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        """将数据库行转换为检索实验摘要字典。

        参数：
        row：从数据库读取的一行记录。
        """
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "status": row["status"],
            "progress_current": int(row["progress_current"]),
            "progress_total": int(row["progress_total"]),
            "error_message": row["error_message"],
            "archived_at": row["archived_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _result(row: sqlite3.Row) -> dict[str, Any]:
        """将数据库行转换为检索实验结果字典。

        参数：
        row：从数据库读取的一行记录。
        """
        return {
            "config_index": int(row["config_index"]),
            "case_id": int(row["case_id"]),
            "status": row["status"],
            "retrieved_sources": json.loads(row["retrieved_sources_json"]),
            "source_hit": _python_bool(row["source_hit"]),
            "correct": _python_bool(row["correct"]),
            "refused": _python_bool(row["refused"]),
            "retrieval_ms": row["retrieval_ms"],
            "error_message": row["error_message"],
        }

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """创建 SQLite 连接上下文并统一管理数据库连接。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _db_bool(value: bool | None) -> int | None:
    """将 Python 布尔值转换为 SQLite 可保存的整数值。

    参数：
    value：需要转换或校验的输入值。
    """
    return None if value is None else int(value)


def _python_bool(value: int | None) -> bool | None:
    """将 SQLite 整数值转换为 Python 布尔值。

    参数：
    value：需要转换或校验的输入值。
    """
    return None if value is None else bool(value)
