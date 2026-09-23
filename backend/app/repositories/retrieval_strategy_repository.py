# 文件作用：封装检索策略配置及其版本信息的持久化操作。
"""持久化可编辑的检索策略及其历史版本，并区分内置策略和用户维护内容，供实验创建、版本恢复及策略管理接口使用。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings


class RetrievalStrategyRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        """初始化检索策略数据仓库及数据库文件位置。

        参数：
        database_path：SQLite 数据库文件路径。
        """
        # 成员变量 database_path：SQLite 数据库文件路径。
        self.database_path = database_path or settings.database_path

    def seed_builtins(self, strategies: list[dict[str, Any]]) -> None:
        """按稳定内置键补齐缺失的基线策略；重复启动时不覆盖用户已经修改的名称、说明或参数，确保初始化操作可重复执行。

            参数：
            strategies：当前实验或运行涉及的检索策略集合。
        """

        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 strategy：当前检索策略及其参数配置。
            for strategy in strategies:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO retrieval_strategy (
                        name, description, builtin_key, config_json, is_builtin, version
                    ) VALUES (?, ?, ?, ?, 1, 1)
                    """,
                    (
                        strategy["name"],
                        strategy.get("description", ""),
                        strategy["builtin_key"],
                        json.dumps(strategy["config"], ensure_ascii=False),
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO retrieval_strategy_version (
                        strategy_id, version, name, description, config_json
                    )
                    SELECT id, version, name, description, config_json
                    FROM retrieval_strategy WHERE builtin_key = ?
                    """,
                    (strategy["builtin_key"],),
                )

    def list_strategies(
        self, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        """按启用和归档条件列出检索策略。

        参数：
        include_archived：查询结果是否包括已归档记录。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 archived_filter：当前列表是否筛选已归档记录。
            archived_filter = "" if include_archived else "WHERE archived_at IS NULL"
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                f"""
                SELECT * FROM retrieval_strategy
                {archived_filter}
                ORDER BY is_builtin DESC, updated_at DESC, id ASC
                """
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [self._item(row) for row in rows]

    def get_strategy(self, strategy_id: int) -> dict[str, Any] | None:
        """按主键读取检索策略及其当前配置。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ?", (strategy_id,)
            ).fetchone()
            return self._item(row) if row is not None else None

    def create_strategy(
        self,
        name: str,
        description: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """创建检索策略并保存初始版本。

        参数：
        name：资源名称或模型名称。
        config：当前应用或实验配置。
        description：资源的补充说明文本。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                INSERT INTO retrieval_strategy (
                    name, description, config_json, is_builtin, version, is_active
                ) VALUES (?, ?, ?, 0, 1, 1)
                """,
                (name, description, json.dumps(config, ensure_ascii=False)),
            )
            connection.execute(
                """
                INSERT INTO retrieval_strategy_version (
                    strategy_id, version, name, description, config_json
                ) VALUES (?, 1, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    name,
                    description,
                    json.dumps(config, ensure_ascii=False),
                ),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            assert row is not None
            return self._item(row)

    def update_strategy(
        self,
        strategy_id: int,
        name: str,
        description: str,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """更新检索策略配置并追加历史版本。

        参数：
        strategy_id：目标检索策略标识。
        name：资源名称或模型名称。
        config：当前应用或实验配置。
        description：资源的补充说明文本。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 current：当前事务中按 ID 读取的非内置策略 sqlite3.Row；用其现有状态/版本判断更新或归档操作是否仍有效。
            current = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ? AND is_builtin = 0",
                (strategy_id,),
            ).fetchone()
            if current is None:
                return None
            # 局部变量 next_version：即将写入的下一配置版本号。
            next_version = int(current["version"]) + 1
            # 局部变量 config_json：序列化后的检索配置 JSON 文本。
            config_json = json.dumps(config, ensure_ascii=False)
            connection.execute(
                """
                UPDATE retrieval_strategy
                SET name = ?, description = ?, config_json = ?,
                    version = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    name,
                    description,
                    config_json,
                    next_version,
                    strategy_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO retrieval_strategy_version (
                    strategy_id, version, name, description, config_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (strategy_id, next_version, name, description, config_json),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ?", (strategy_id,)
            ).fetchone()
            return self._item(row) if row is not None else None

    def list_versions(self, strategy_id: int) -> list[dict[str, Any]]:
        """按时间顺序读取检索策略历史版本。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 rows：当前 SELECT 按筛选条件取回的 sqlite3.Row 列表；随后逐条提取所选字段，映射为接口响应或服务层使用的记录结构。
            rows = connection.execute(
                """
                SELECT * FROM retrieval_strategy_version
                WHERE strategy_id = ?
                ORDER BY version DESC
                """,
                (strategy_id,),
            ).fetchall()
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            return [self._version_item(row) for row in rows]

    def restore_version(self, strategy_id: int, version: int) -> dict[str, Any] | None:
        """恢复检索策略的指定历史配置版本。

        参数：
        strategy_id：目标检索策略标识。
        version：目标配置版本号。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 current：当前处理的对象或状态快照。
            current = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ? AND is_builtin = 0",
                (strategy_id,),
            ).fetchone()
            # 局部变量 target：当前需要更新、转换或写入的目标对象。
            target = connection.execute(
                """
                SELECT * FROM retrieval_strategy_version
                WHERE strategy_id = ? AND version = ?
                """,
                (strategy_id, version),
            ).fetchone()
            if current is None or target is None:
                return None
            # 局部变量 next_version：即将写入的下一配置版本号。
            next_version = int(current["version"]) + 1
            connection.execute(
                """
                UPDATE retrieval_strategy
                SET name = ?, description = ?, config_json = ?, version = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    target["name"],
                    target["description"],
                    target["config_json"],
                    next_version,
                    strategy_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO retrieval_strategy_version (
                    strategy_id, version, name, description, config_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    next_version,
                    target["name"],
                    target["description"],
                    target["config_json"],
                ),
            )
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ?", (strategy_id,)
            ).fetchone()
            return self._item(row) if row is not None else None

    def set_active(self, strategy_id: int, active: bool) -> dict[str, Any] | None:
        """更新检索策略的启用状态。

        参数：
        strategy_id：目标检索策略标识。
        active：策略是否启用。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                UPDATE retrieval_strategy
                SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND is_builtin = 0 AND archived_at IS NULL
                """,
                (int(active), strategy_id),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ?", (strategy_id,)
            ).fetchone()
            return self._item(row) if row is not None else None

    def archive_strategy(self, strategy_id: int) -> dict[str, Any] | None:
        """归档检索策略并保存归档时间。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                UPDATE retrieval_strategy
                SET is_active = 0, archived_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND is_builtin = 0 AND archived_at IS NULL
                """,
                (strategy_id,),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ?", (strategy_id,)
            ).fetchone()
            return self._item(row) if row is not None else None

    def restore_strategy(self, strategy_id: int) -> dict[str, Any] | None:
        """恢复已归档的检索策略。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                """
                UPDATE retrieval_strategy
                SET is_active = 1, archived_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND is_builtin = 0 AND archived_at IS NOT NULL
                """,
                (strategy_id,),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：当前 SELECT 返回的一条 sqlite3.Row，可按列名读取所选字段；后续代码据此构建业务对象、判断状态或转换为字典。
            row = connection.execute(
                "SELECT * FROM retrieval_strategy WHERE id = ?", (strategy_id,)
            ).fetchone()
            return self._item(row) if row is not None else None

    def delete_strategy(self, strategy_id: int) -> bool:
        """删除指定检索策略及其版本记录。

        参数：
        strategy_id：目标检索策略标识。
        """
        # 局部变量 connection：当前仓储操作使用的 SQLite Connection；上下文负责提交或回滚事务并在退出时关闭连接，查询行按 sqlite3.Row 映射。
        with self._connection() as connection:
            # 局部变量 cursor：执行当前参数化 SQL 的 sqlite3.Cursor；后续通过它读取 lastrowid、rowcount 或查询结果，以关联新记录并确认写入效果。
            cursor = connection.execute(
                "DELETE FROM retrieval_strategy WHERE id = ? AND is_builtin = 0",
                (strategy_id,),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        """将策略数据库行转换为接口响应字典。

        参数：
        row：从数据库读取的一行记录。
        """
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "builtin_key": row["builtin_key"],
            "config": json.loads(row["config_json"]),
            "is_builtin": bool(row["is_builtin"]),
            "version": int(row["version"]),
            "is_active": bool(row["is_active"]),
            "archived_at": row["archived_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _version_item(row: sqlite3.Row) -> dict[str, Any]:
        """将历史版本数据库行转换为响应字典。

        参数：
        row：从数据库读取的一行记录。
        """
        return {
            "id": int(row["id"]),
            "strategy_id": int(row["strategy_id"]),
            "version": int(row["version"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "config": json.loads(row["config_json"]),
            "created_at": row["created_at"],
        }

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
