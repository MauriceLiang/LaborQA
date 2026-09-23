# 文件作用：封装评估用例、评估运行及其结果的数据库读写。
"""SQLite persistence for evaluation cases, batches, and per-case results."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings


class EvaluationRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        """初始化评估数据仓库及数据库文件位置。

        参数：
        database_path：SQLite 数据库文件路径。
        """
        # 成员变量 database_path：SQLite 数据库文件路径。
        self.database_path = database_path or settings.database_path

    def seed_cases(
        self, cases: Sequence[dict[str, Any]], *, baseline_version: int = 1
    ) -> None:
        """将内置基准评估用例写入数据库。

        参数：
        cases：本次查询或运行使用的评估用例集合。
        baseline_version：当前内置评估基准数据的版本标识。
        """
        # 局部变量 key_prefix：当前缓存键使用的前缀。
        key_prefix = f"baseline-v{baseline_version}-"
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE evaluation_case
                SET status = 'ARCHIVED',
                    archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE origin = 'BUILTIN'
                  AND status = 'ACTIVE'
                  AND (
                      builtin_key IS NULL
                      OR builtin_key NOT LIKE ?
                  )
                """,
                (f"{key_prefix}%",),
            )
            # 局部变量 position：当前循环或列表处理中的序号、位置。
            # 局部变量 case：当前评估用例记录。
            for position, case in enumerate(cases, start=1):
                # 局部变量 builtin_key：内置资料的稳定来源标识。
                builtin_key = f"{key_prefix}{position:03d}"
                # 局部变量 exists：目标文件、表或记录是否存在。
                exists = connection.execute(
                    "SELECT 1 FROM evaluation_case WHERE builtin_key = ?",
                    (builtin_key,),
                ).fetchone()
                if exists:
                    continue
                connection.execute(
                    """
                    INSERT INTO evaluation_case (
                        topic, expected_type, turns_json, expected_points_json,
                        expected_sources_json, should_show_compliance, origin,
                        status, version, builtin_key
                    ) VALUES (?, ?, ?, ?, ?, ?, 'BUILTIN', 'ACTIVE', 1, ?)
                    """,
                    (
                        case["topic"],
                        case["expected_type"],
                        json.dumps(case["turns"], ensure_ascii=False),
                        json.dumps(case["expected_points"], ensure_ascii=False),
                        json.dumps(case["expected_sources"], ensure_ascii=False),
                        int(case["should_show_compliance"]),
                        builtin_key,
                    ),
                )

    def list_cases(
        self,
        *,
        page: int,
        size: int,
        topic: str | None,
        expected_type: str | None,
        is_multi_turn: bool | None,
        origin: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """按筛选、状态和分页条件查询评估用例。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        topic：评估用例或知识缺口所属的主题。
        expected_type：用例预期的回答或拒答类型。
        is_multi_turn：评估用例是否包含多轮对话。
        origin：评估用例或资料的来源类别。
        include_archived：查询结果是否包含已归档记录。
        """
        # 局部变量 conditions：当前查询使用的筛选条件。
        conditions: list[str] = []
        # 局部变量 parameters：当前操作使用的参数集合。
        parameters: list[Any] = []
        if topic:
            # 局部变量 escaped：已完成转义的安全文本。
            escaped = (
                topic.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            conditions.append("topic LIKE ? ESCAPE char(92)")
            parameters.append(f"%{escaped}%")
        if expected_type:
            conditions.append("expected_type = ?")
            parameters.append(expected_type)
        if is_multi_turn is not None:
            conditions.append(
                "json_array_length(turns_json) > 1"
                if is_multi_turn
                else "json_array_length(turns_json) = 1"
            )
        if origin:
            conditions.append("origin = ?")
            parameters.append(origin)
        if status:
            conditions.append("status = ?")
            parameters.append(status)
        elif not include_archived:
            conditions.append("status = 'ACTIVE'")

        # 局部变量 where_clause：SQL 查询的筛选子句。
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 total：符合当前条件的记录总数或项目总数。
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM evaluation_case {where_clause}", parameters
                ).fetchone()[0]
            )
            # 局部变量 rows：本次查询返回的数据库记录集合。
            rows = connection.execute(
                f"""
                SELECT * FROM evaluation_case
                {where_clause}
                ORDER BY id
                LIMIT ? OFFSET ?
                """,
                [*parameters, size, (page - 1) * size],
            ).fetchall()
            # 局部变量 row：数据库查询返回的当前记录行。
            return [self._case_item(row) for row in rows], total

    def get_cases(
        self,
        case_ids: Sequence[int] | None = None,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """按一组用例标识批量读取评估用例。

        参数：
        case_ids：评估用例标识列表。
        include_archived：查询结果是否包含已归档记录。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 status_clause：根据任务状态生成的 SQL 条件。
            status_clause = "" if include_archived else " AND status = 'ACTIVE'"
            if case_ids is None:
                # 局部变量 rows：本次查询返回的数据库记录集合。
                rows = connection.execute(
                    f"SELECT * FROM evaluation_case WHERE 1 = 1{status_clause} ORDER BY id"
                ).fetchall()
            elif not case_ids:
                return []
            else:
                # 局部变量 placeholders：提示模板中待填充的变量名集合。
                placeholders = ",".join("?" for _ in case_ids)
                # 局部变量 found：是否成功找到目标记录。
                # 局部变量 row：数据库查询返回的当前记录行。
                found = {
                    int(row["id"]): row
                    for row in connection.execute(
                        f"SELECT * FROM evaluation_case WHERE id IN ({placeholders}){status_clause}",
                        list(case_ids),
                    ).fetchall()
                }
                # 局部变量 case_id：当前评估用例的数据库标识。
                rows = [found[case_id] for case_id in case_ids if case_id in found]
            return [self._case_item(row) for row in rows]

    def get_case(
        self, case_id: int, *, include_archived: bool = False
    ) -> dict[str, Any] | None:
        """按主键读取单条评估用例。

        参数：
        case_id：目标评估用例标识。
        include_archived：查询结果是否包含已归档记录。
        """
        # 局部变量 cases：本次查询或运行使用的评估用例集合。
        cases = self.get_cases([case_id], include_archived=include_archived)
        return cases[0] if cases else None

    def create_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """创建用户维护的评估用例记录。

        参数：
        case：当前评测用例数据。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                """
                INSERT INTO evaluation_case (
                    topic, expected_type, turns_json, expected_points_json,
                    expected_sources_json, should_show_compliance, origin,
                    status, version
                ) VALUES (?, ?, ?, ?, ?, ?, 'CUSTOM', 'ACTIVE', 1)
                """,
                _case_values(case),
            )
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM evaluation_case WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self._case_item(row)

    def update_case(
        self, case_id: int, expected_version: int, case: dict[str, Any]
    ) -> dict[str, Any] | None:
        """更新评估用例内容并推进版本号。

        参数：
        case_id：目标评估用例标识。
        case：当前评测用例数据。
        expected_version：提交更新时客户端读取到的用例版本。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                """
                UPDATE evaluation_case
                SET topic = ?, expected_type = ?, turns_json = ?,
                    expected_points_json = ?, expected_sources_json = ?,
                    should_show_compliance = ?, version = version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'ACTIVE' AND version = ?
                """,
                (*_case_values(case), case_id, expected_version),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM evaluation_case WHERE id = ?", (case_id,)
            ).fetchone()
            return self._case_item(row)

    def archive_case(self, case_id: int) -> dict[str, Any] | None:
        """归档评估用例并返回更新后的记录。

        参数：
        case_id：目标评估用例标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                """
                UPDATE evaluation_case
                SET status = 'ARCHIVED', archived_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND origin = 'CUSTOM' AND status = 'ACTIVE'
                """,
                (case_id,),
            )
            if cursor.rowcount == 0:
                return None
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM evaluation_case WHERE id = ?", (case_id,)
            ).fetchone()
            return self._case_item(row)

    def has_active_run(self, case_id: int) -> bool:
        """检查评估用例是否被正在执行的任务占用。

        参数：
        case_id：目标评估用例标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            return (
                connection.execute(
                    """
                    SELECT 1
                    FROM evaluation_run_case
                    JOIN evaluation_run ON evaluation_run.id = evaluation_run_case.run_id
                    WHERE evaluation_run_case.case_id = ?
                      AND evaluation_run.status IN ('PENDING', 'RUNNING')
                    LIMIT 1
                    """,
                    (case_id,),
                ).fetchone()
                is not None
            )

    def create_run(
        self,
        name: str,
        cases: Sequence[dict[str, Any]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """创建评估运行及其选中用例快照。

        参数：
        name：资源名称或模型名称。
        config：当前应用或实验配置。
        cases：当前任务使用的评估用例集合。
        """
        # 局部变量 case_ids：本次评估选择的用例标识列表。
        # 局部变量 case：当前评估用例记录。
        case_ids = [int(case["id"]) for case in cases]
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 cursor：负责执行本次 SQL 操作的数据库游标。
            cursor = connection.execute(
                """
                INSERT INTO evaluation_run (name, case_count, progress_total, config_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    name,
                    len(case_ids),
                    len(case_ids),
                    json.dumps(config, ensure_ascii=False),
                ),
            )
            # 局部变量 run_id：当前评估运行的数据库标识。
            run_id = int(cursor.lastrowid)
            # 局部变量 position：当前循环或列表处理中的序号、位置。
            # 局部变量 case_id：当前评估用例的数据库标识。
            connection.executemany(
                """
                INSERT INTO evaluation_run_case (
                    run_id, case_id, position, case_version, case_snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        case_id,
                        position,
                        int(case.get("version", 1)),
                        json.dumps(_case_snapshot(case), ensure_ascii=False),
                    )
                    for position, (case_id, case) in enumerate(
                        zip(case_ids, cases, strict=True)
                    )
                ],
            )
            return dict(
                connection.execute(
                    "SELECT * FROM evaluation_run WHERE id = ?", (run_id,)
                ).fetchone()
            )

    def delete_run(self, run_id: int) -> dict[str, Any] | None:
        """删除评估运行和关联结果记录。

        参数：
        run_id：目标评测运行标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM evaluation_run WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            # 局部变量 run：当前评测任务或运行记录。
            run = dict(row)
            if run["status"] not in {"COMPLETED", "FAILED"}:
                return run
            connection.execute("DELETE FROM evaluation_run WHERE id = ?", (run_id,))
            return run

    def run_case_ids(self, run_id: int) -> list[int]:
        """读取评估运行包含的用例标识。

        参数：
        run_id：目标评测运行标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 rows：本次查询返回的数据库记录集合。
            rows = connection.execute(
                "SELECT case_id FROM evaluation_run_case WHERE run_id = ? ORDER BY position",
                (run_id,),
            ).fetchall()
            # 局部变量 row：数据库查询返回的当前记录行。
            return [int(row["case_id"]) for row in rows]

    def run_cases(self, run_id: int) -> list[dict[str, Any]]:
        """读取评估运行时保存的用例快照。

        参数：
        run_id：目标评测运行标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 rows：本次查询返回的数据库记录集合。
            rows = connection.execute(
                """
                SELECT case_id, case_version, case_snapshot_json
                FROM evaluation_run_case
                WHERE run_id = ? ORDER BY position
                """,
                (run_id,),
            ).fetchall()
            # 局部变量 cases：本次查询或运行使用的评估用例集合。
            cases: list[dict[str, Any]] = []
            # 局部变量 row：数据库查询返回的当前记录行。
            for row in rows:
                # 局部变量 snapshot：任务创建时保存的不可变输入快照。
                snapshot = json.loads(row["case_snapshot_json"] or "{}")
                if not snapshot:
                    # 局部变量 current：当前处理的对象或状态快照。
                    current = self.get_case(int(row["case_id"]), include_archived=True)
                    if current is None:
                        continue
                    snapshot = _case_snapshot(current)
                cases.append(
                    {
                        "id": int(row["case_id"]),
                        "version": int(row["case_version"] or 1),
                        **snapshot,
                    }
                )
            return cases

    def set_run_status(self, run_id: int, status: str) -> None:
        """更新评估运行的生命周期状态。

        参数：
        run_id：目标评测运行标识。
        status：需要读取或设置的处理状态。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                "UPDATE evaluation_run SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, run_id),
            )

    def add_result(self, run_id: int, result: dict[str, Any]) -> None:
        """保存单条评估用例执行结果。

        参数：
        run_id：目标评测运行标识。
        result：本次操作或模型调用的结果数据。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_result (
                    run_id, case_id, status, answer, refused, correct, source_hit,
                    retrieval_hit, context_retained, multi_turn_correct,
                    compliance_hit, answer_mode,
                    evidence_decision, judge_reason, matched_points_json,
                    missing_points_json, contradictions_json, turn_diagnostics_json,
                    citations_json, latency_ms, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result["case_id"],
                    result["status"],
                    result.get("answer"),
                    _db_bool(result.get("refused")),
                    _db_bool(result.get("correct")),
                    _db_bool(result.get("source_hit")),
                    _db_bool(result.get("retrieval_hit")),
                    _db_bool(result.get("context_retained")),
                    _db_bool(result.get("multi_turn_correct")),
                    _db_bool(result.get("compliance_hit")),
                    result.get("answer_mode"),
                    result.get("evidence_decision"),
                    result.get("judge_reason"),
                    json.dumps(result.get("matched_points") or [], ensure_ascii=False),
                    json.dumps(result.get("missing_points") or [], ensure_ascii=False),
                    json.dumps(result.get("contradictions") or [], ensure_ascii=False),
                    json.dumps(
                        result.get("turn_diagnostics") or [], ensure_ascii=False
                    ),
                    json.dumps(result.get("citations", []), ensure_ascii=False),
                    result.get("latency_ms"),
                    result.get("error_message"),
                ),
            )
            connection.execute(
                "UPDATE evaluation_run SET progress_current = progress_current + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (run_id,),
            )

    def complete_run(self, run_id: int, metrics: dict[str, float | None]) -> None:
        """将评估运行标记为完成并保存汇总指标。

        参数：
        run_id：目标评测运行标识。
        metrics：运行结束后计算的汇总指标。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE evaluation_run SET status = 'COMPLETED', error_message = NULL,
                    accuracy = ?, reject_rate = ?, refusal_rate = ?, citation_hit_rate = ?,
                    retrieval_hit_rate = ?, context_retention_rate = ?,
                    execution_success_rate = ?, multi_turn_pass_rate = ?,
                    compliance_hit_rate = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    metrics["accuracy"],
                    metrics["reject_rate"],
                    metrics["refusal_rate"],
                    metrics["citation_hit_rate"],
                    metrics.get("retrieval_hit_rate"),
                    metrics.get("context_retention_rate"),
                    metrics.get("execution_success_rate"),
                    metrics.get("multi_turn_pass_rate"),
                    metrics.get("compliance_hit_rate"),
                    run_id,
                ),
            )

    def fail_run(self, run_id: int, message: str) -> None:
        """将评估运行标记为失败并记录错误信息。

        参数：
        run_id：目标评测运行标识。
        message：需要保存或展示的消息内容。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE evaluation_run SET status = 'FAILED', error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (message[:500], run_id),
            )

    def recover_interrupted_runs(self) -> None:
        """将进程中断后遗留的运行恢复为可解释状态。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE evaluation_run
                SET status = 'FAILED', error_message = '服务重启导致任务中断',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('PENDING', 'RUNNING')
                """
            )

    def list_runs(
        self, *, page: int, size: int, status: str | None
    ) -> tuple[list[dict[str, Any]], int]:
        """按条件和分页读取评估运行摘要。

        参数：
        page：查询页码。
        size：每页数据条数。
        status：需要读取或设置的处理状态。
        """
        # 局部变量 where_clause：SQL 查询的筛选子句。
        where_clause = "WHERE status = ?" if status else ""
        # 局部变量 parameters：当前操作使用的参数集合。
        parameters: list[Any] = [status] if status else []
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 total：符合当前条件的记录总数或项目总数。
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM evaluation_run {where_clause}", parameters
                ).fetchone()[0]
            )
            # 局部变量 rows：本次查询返回的数据库记录集合。
            rows = connection.execute(
                f"""
                SELECT * FROM evaluation_run
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, size, (page - 1) * size],
            ).fetchall()
            # 局部变量 row：数据库查询返回的当前记录行。
            return [self._run_summary(row) for row in rows], total

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        """读取评估运行详情及其结果。

        参数：
        run_id：目标评测运行标识。
        """
        # 局部变量 connection：当前操作使用的数据库连接。
        with self._connection() as connection:
            # 局部变量 row：数据库查询返回的当前记录行。
            row = connection.execute(
                "SELECT * FROM evaluation_run WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            # 局部变量 results：本次批量处理生成的结果记录集合。
            results = connection.execute(
                """
                SELECT * FROM evaluation_result
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
            # 局部变量 summary：当前对象的摘要信息。
            summary = self._run_summary(row)
            # 局部变量 metrics：本次评测或实验的汇总指标。
            metrics = None
            if row["status"] == "COMPLETED":
                # 局部变量 key：当前映射使用的键或稳定标识。
                metrics = {
                    key: row[key]
                    for key in (
                        "accuracy",
                        "reject_rate",
                        "refusal_rate",
                        "citation_hit_rate",
                        "retrieval_hit_rate",
                        "context_retention_rate",
                        "execution_success_rate",
                        "multi_turn_pass_rate",
                        "compliance_hit_rate",
                    )
                }
            # 局部变量 item：当前列表或结果集合中的一条数据。
            return {
                **summary,
                "config": json.loads(row["config_json"]),
                "metrics": metrics,
                "results": [self._result_item(item) for item in results],
            }

    @staticmethod
    def _case_item(row: sqlite3.Row) -> dict[str, Any]:
        """将数据库行转换为评估用例响应字典。

        参数：
        row：从数据库读取的一行记录。
        """
        # 局部变量 columns：当前数据库表或结果包含的列名集合。
        columns = set(row.keys())
        return {
            "id": int(row["id"]),
            "topic": row["topic"],
            "expected_type": row["expected_type"],
            "turns": json.loads(row["turns_json"]),
            "expected_points": json.loads(row["expected_points_json"]),
            "expected_sources": json.loads(row["expected_sources_json"]),
            "should_show_compliance": bool(row["should_show_compliance"]),
            "origin": row["origin"] if "origin" in columns else "BUILTIN",
            "status": row["status"] if "status" in columns else "ACTIVE",
            "version": int(row["version"] if "version" in columns else 1),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
            if "updated_at" in columns and row["updated_at"]
            else row["created_at"],
            "archived_at": row["archived_at"] if "archived_at" in columns else None,
        }

    @staticmethod
    def _run_summary(row: sqlite3.Row) -> dict[str, Any]:
        """将数据库行转换为评估运行摘要字典。

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
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _result_item(row: sqlite3.Row) -> dict[str, Any]:
        """将数据库行转换为评估用例结果字典。

        参数：
        row：从数据库读取的一行记录。
        """
        # 局部变量 columns：当前数据库表或结果包含的列名集合。
        columns = set(row.keys())
        return {
            "case_id": int(row["case_id"]),
            "status": row["status"],
            "answer": row["answer"],
            "refused": _python_bool(row["refused"]),
            "correct": _python_bool(row["correct"]),
            "source_hit": _python_bool(row["source_hit"]),
            "retrieval_hit": _python_bool(row["retrieval_hit"])
            if "retrieval_hit" in columns
            else None,
            "context_retained": _python_bool(row["context_retained"])
            if "context_retained" in columns
            else None,
            "multi_turn_correct": _python_bool(row["multi_turn_correct"]),
            "compliance_hit": _python_bool(row["compliance_hit"]),
            "answer_mode": row["answer_mode"] if "answer_mode" in columns else None,
            "evidence_decision": row["evidence_decision"]
            if "evidence_decision" in columns
            else None,
            "judge_reason": row["judge_reason"] if "judge_reason" in columns else None,
            "matched_points": _json_list(row, columns, "matched_points_json"),
            "missing_points": _json_list(row, columns, "missing_points_json"),
            "contradictions": _json_list(row, columns, "contradictions_json"),
            "turn_diagnostics": _json_list(row, columns, "turn_diagnostics_json"),
            "citations": json.loads(row["citations_json"] or "[]"),
            "latency_ms": row["latency_ms"],
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


def _json_list(row: sqlite3.Row, columns: set[str], column: str) -> list[Any]:
    """从数据库行中解码指定 JSON 列表字段。

    参数：
    row：从数据库读取的一行记录。
    columns：数据库记录包含的列名集合。
    column：当前数据库列名。
    """
    if column not in columns:
        return []
    return json.loads(row[column] or "[]")


def _case_values(case: dict[str, Any]) -> tuple[Any, ...]:
    """按数据库写入顺序提取评估用例字段值。

    参数：
    case：当前评测用例数据。
    """
    return (
        case["topic"],
        case["expected_type"],
        json.dumps(case["turns"], ensure_ascii=False),
        json.dumps(case["expected_points"], ensure_ascii=False),
        json.dumps(case["expected_sources"], ensure_ascii=False),
        int(case["should_show_compliance"]),
    )


def _case_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    """构造评估运行使用的用例内容快照。

    参数：
    case：当前评测用例数据。
    """
    return {
        "topic": case["topic"],
        "expected_type": case["expected_type"],
        "turns": case["turns"],
        "expected_points": case["expected_points"],
        "expected_sources": case["expected_sources"],
        "should_show_compliance": bool(case["should_show_compliance"]),
    }
