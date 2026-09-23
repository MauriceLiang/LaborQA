# 文件作用：负责 SQLite 连接、数据库初始化、迁移以及就绪状态检查。
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.core.config import settings

# 随当前 Python 模块定位的建表 SQL 文件路径。
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def initialize_database() -> None:
    """创建数据库目录、启用外键并执行建表脚本和兼容性迁移。
    """
    # 按应用设置解析出的 SQLite 数据库文件或内存库路径。
    database_path = settings.database_path
    if database_path != Path(":memory:"):
        database_path.parent.mkdir(parents=True, exist_ok=True)

    # connection 是本次初始化复用的 SQLite 连接和事务上下文。
    with (
        closing(sqlite3.connect(str(database_path), timeout=5)) as connection,
        connection,
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        # Upgrade the document table before executing schema.sql. The schema
        # creates an index on source_key, which would fail against an older
        # database unless the columns exist first.
        _migrate_document_schema(connection)
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate_message_schema(connection)
        _migrate_citation_schema(connection)
        _migrate_evaluation_schema(connection)
        _migrate_retrieval_experiment_schema(connection)
        _migrate_retrieval_strategy_schema(connection)


def _migrate_document_schema(connection: sqlite3.Connection) -> None:
    """升级文档表结构，使其支持 Markdown、内置来源标识及来源去重。

    参数：connection 为当前数据库迁移连接。
    """
    # sqlite_master 中记录的 document 建表语句查询结果。
    row = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'document'"
    ).fetchone()
    # 规范化为小写的建表 SQL，用于判断旧表是否支持 md 文件类型。
    table_sql = str(row[0] or "").lower() if row else ""
    if not table_sql:
        return

    # document 表当前已存在的列名集合。
    columns = _table_columns(connection, "document")
    if "'md'" not in table_sql:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            # 为旧版本缺少的来源列提供迁移查询所需的默认 SQL 表达式。
            source_type = "source_type" if "source_type" in columns else "'UPLOAD'"
            # 局部变量 source_key：内置资料来源的稳定唯一键。
            source_key = "source_key" if "source_key" in columns else "NULL"
            # 局部变量 source_hash：源文件内容摘要。
            source_hash = "source_hash" if "source_hash" in columns else "NULL"
            connection.executescript(
                f"""
                BEGIN;
                CREATE TABLE document_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name VARCHAR(255) NOT NULL,
                    file_type VARCHAR(20) NOT NULL
                        CHECK (file_type IN ('pdf', 'doc', 'docx', 'md', 'txt')),
                    file_path VARCHAR(500) NOT NULL,
                    source_type VARCHAR(20) NOT NULL DEFAULT 'UPLOAD'
                        CHECK (source_type IN ('UPLOAD', 'BUILTIN')),
                    source_key VARCHAR(255),
                    source_hash VARCHAR(64),
                    status VARCHAR(20) NOT NULL DEFAULT 'PROCESSING'
                        CHECK (status IN ('PROCESSING', 'SUCCESS', 'FAILED')),
                    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
                    error_message VARCHAR(500),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO document_new (
                    id,
                    file_name,
                    file_type,
                    file_path,
                    source_type,
                    source_key,
                    source_hash,
                    status,
                    chunk_count,
                    error_message,
                    created_at,
                    updated_at
                )
                SELECT
                    id,
                    file_name,
                    file_type,
                    file_path,
                    {source_type},
                    {source_key},
                    {source_hash},
                    status,
                    chunk_count,
                    error_message,
                    created_at,
                    updated_at
                FROM document;
                DROP TABLE document;
                ALTER TABLE document_new RENAME TO document;
                COMMIT;
                """
            )
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    # 重新读取迁移后 document 表的列集合，决定是否需要补列。
    columns = _table_columns(connection, "document")
    # 本版本新加的来源元数据列及各列 SQL 类型约束。
    additions = {
        "source_type": (
            "VARCHAR(20) NOT NULL DEFAULT 'UPLOAD' "
            "CHECK (source_type IN ('UPLOAD', 'BUILTIN'))"
        ),
        "source_key": "VARCHAR(255)",
        "source_hash": "VARCHAR(64)",
    }
    # column 是待检查的列名，definition 是对应的 SQLite 列定义。
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE document ADD COLUMN {column} {definition}")

    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_document_source_key "
        "ON document(source_key) WHERE source_key IS NOT NULL"
    )


def _migrate_message_schema(connection: sqlite3.Connection) -> None:
    """为旧消息表增加可空 AI 模式字段，保留已有会话数据。

    参数：connection 为当前数据库迁移连接。
    """

    if "ai_mode" not in _table_columns(connection, "message"):
        connection.execute("ALTER TABLE message ADD COLUMN ai_mode VARCHAR(20)")


def _migrate_citation_schema(connection: sqlite3.Connection) -> None:
    """为旧引用表补充轻量检索评分、证据来源和锚点信息。

    参数：connection 为当前数据库迁移连接。
    """
    # citation 表当前已经存在的列名集合。
    columns = _table_columns(connection, "citation")
    # 待补充的检索评分及证据来源列定义。
    additions = {
        "lexical_score": "REAL NOT NULL DEFAULT 0",
        "source_score": "REAL NOT NULL DEFAULT 0",
        "lightweight_score": "REAL NOT NULL DEFAULT 0",
        "evidence_origin": "VARCHAR(20) NOT NULL DEFAULT 'RETRIEVED'",
        "anchor_chunk_id": "INTEGER",
        "anchor_retrieval_score": "REAL",
    }
    # 逐列补充旧表尚未具备的 citation 元数据。
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE citation ADD COLUMN {column} {definition}")


def _migrate_evaluation_schema(connection: sqlite3.Connection) -> None:
    """升级评估运行、结果、用例及运行快照相关表结构。

    参数：connection 为当前数据库迁移连接。
    """
    # 评估运行表中现有的列名。
    run_columns = _table_columns(connection, "evaluation_run")
    # 标记本次迁移是否新加拒答率，以决定是否回填历史结果。
    refusal_rate_added = "refusal_rate" not in run_columns
    if refusal_rate_added:
        connection.execute(
            "ALTER TABLE evaluation_run ADD COLUMN refusal_rate REAL"
        )
        connection.execute(
            """
            UPDATE evaluation_run
            SET refusal_rate = (
                SELECT CASE
                    WHEN COUNT(*) = 0 THEN NULL
                    ELSE CAST(SUM(CASE WHEN refused = 1 THEN 1 ELSE 0 END) AS REAL)
                        / COUNT(*)
                END
                FROM evaluation_result
                WHERE evaluation_result.run_id = evaluation_run.id
                  AND evaluation_result.status = 'COMPLETED'
            )
            WHERE status = 'COMPLETED'
            """
        )
    # 评估运行表需要补充的检索命中、上下文保留和执行成功指标。
    run_additions = {
        "retrieval_hit_rate": "REAL",
        "context_retention_rate": "REAL",
        "execution_success_rate": "REAL",
    }
    # column 为指标列名，definition 为该指标列的 SQLite 类型。
    for column, definition in run_additions.items():
        if column not in run_columns:
            connection.execute(
                f"ALTER TABLE evaluation_run ADD COLUMN {column} {definition}"
            )

    # 评估结果表中现有的列名。
    result_columns = _table_columns(connection, "evaluation_result")
    # 结果诊断、证据判断和逐轮命中情况等新增列定义。
    result_additions = {
        "answer_mode": "VARCHAR(30)",
        "evidence_decision": "VARCHAR(30)",
        "judge_reason": "TEXT",
        "matched_points_json": "TEXT",
        "missing_points_json": "TEXT",
        "contradictions_json": "TEXT",
        "turn_diagnostics_json": "TEXT NOT NULL DEFAULT '[]'",
        "context_retained": "INTEGER",
        "retrieval_hit": "INTEGER",
    }
    for column, definition in result_additions.items():
        if column not in result_columns:
            connection.execute(
                f"ALTER TABLE evaluation_result ADD COLUMN {column} {definition}"
            )

    # 评估用例表中现有的列名。
    case_columns = _table_columns(connection, "evaluation_case")
    # 用例来源、生命周期、版本和维护信息等新增列定义。
    case_additions = {
        "origin": "VARCHAR(20) NOT NULL DEFAULT 'BUILTIN'",
        "status": "VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'",
        "version": "INTEGER NOT NULL DEFAULT 1",
        "builtin_key": "VARCHAR(100)",
        "created_by": "VARCHAR(100)",
        # SQLite does not allow CURRENT_TIMESTAMP as an ALTER TABLE default.
        "updated_at": "TEXT",
        "archived_at": "TEXT",
    }
    # 逐项为 evaluation_case 增加缺失的生命周期字段。
    for column, definition in case_additions.items():
        if column not in case_columns:
            connection.execute(
                f"ALTER TABLE evaluation_case ADD COLUMN {column} {definition}"
            )

    connection.execute(
        """
        UPDATE evaluation_case
        SET origin = COALESCE(origin, 'BUILTIN'),
            status = COALESCE(status, 'ACTIVE'),
            version = COALESCE(version, 1),
            updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
            builtin_key = CASE
                WHEN COALESCE(origin, 'BUILTIN') = 'BUILTIN'
                    THEN COALESCE(builtin_key, 'baseline-' || id)
                ELSE builtin_key
            END
        """
    )

    # 评估运行用例关联表中现有的列名。
    run_case_columns = _table_columns(connection, "evaluation_run_case")
    # 关联记录需要保存的用例版本号和执行时内容快照列定义。
    run_case_additions = {
        "case_version": "INTEGER NOT NULL DEFAULT 1",
        "case_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    # 逐项补齐关联表的版本和快照字段。
    for column, definition in run_case_additions.items():
        if column not in run_case_columns:
            connection.execute(
                f"ALTER TABLE evaluation_run_case ADD COLUMN {column} {definition}"
            )

    # 找出尚未写入用例快照的历史评估运行关联记录。
    rows = connection.execute(
        """
        SELECT run_case.run_id, run_case.case_id, evaluation_case.version,
               evaluation_case.topic, evaluation_case.expected_type,
               evaluation_case.turns_json, evaluation_case.expected_points_json,
               evaluation_case.expected_sources_json,
               evaluation_case.should_show_compliance
        FROM evaluation_run_case AS run_case
        JOIN evaluation_case ON evaluation_case.id = run_case.case_id
        WHERE run_case.case_snapshot_json IS NULL
           OR run_case.case_snapshot_json = '{}'
        """
    ).fetchall()
    # row 保存一次查询结果；snapshot 转成运行时可复现的用例内容。
    for row in rows:
        # 局部变量 snapshot：任务创建时保存的不可变输入快照。
        snapshot = {
            "topic": row[3],
            "expected_type": row[4],
            "turns": json.loads(row[5]),
            "expected_points": json.loads(row[6]),
            "expected_sources": json.loads(row[7]),
            "should_show_compliance": bool(row[8]),
        }
        connection.execute(
            """
            UPDATE evaluation_run_case
            SET case_version = COALESCE(case_version, ?),
                case_snapshot_json = ?
            WHERE run_id = ? AND case_id = ?
            """,
            (int(row[2] or 1), json.dumps(snapshot, ensure_ascii=False), row[0], row[1]),
        )

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_evaluation_case_status_origin "
        "ON evaluation_case(status, origin, id)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluation_case_builtin_key "
        "ON evaluation_case(builtin_key) WHERE builtin_key IS NOT NULL"
    )


def _migrate_retrieval_strategy_schema(connection: sqlite3.Connection) -> None:
    """为检索策略补充版本、启用状态及版本历史表。

    参数：connection 为当前数据库迁移连接。
    """
    # retrieval_strategy 表当前已有的列名。
    columns = _table_columns(connection, "retrieval_strategy")
    # 需要兼容补齐的策略生命周期字段及其 SQLite 类型。
    additions = {
        "version": "INTEGER NOT NULL DEFAULT 1",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "archived_at": "TEXT",
    }
    # 逐项添加数据库中尚不存在的策略生命周期列。
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(
                f"ALTER TABLE retrieval_strategy ADD COLUMN {column} {definition}"
            )

    connection.execute(
        "UPDATE retrieval_strategy SET version = COALESCE(version, 1), "
        "is_active = COALESCE(is_active, 1)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_retrieval_strategy_archive_status "
        "ON retrieval_strategy(archived_at, is_active, id)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS retrieval_strategy_version ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "strategy_id INTEGER NOT NULL REFERENCES retrieval_strategy(id) ON DELETE CASCADE, "
        "version INTEGER NOT NULL CHECK (version >= 1), "
        "name VARCHAR(100) NOT NULL, "
        "description VARCHAR(255) NOT NULL DEFAULT '', "
        "config_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE (strategy_id, version))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_retrieval_strategy_version_strategy "
        "ON retrieval_strategy_version(strategy_id, version DESC)"
    )


def _migrate_retrieval_experiment_schema(connection: sqlite3.Connection) -> None:
    """为旧检索实验表补充归档状态并创建归档查询索引。

    参数：connection 为当前数据库迁移连接。
    """
    # retrieval_experiment 表当前已有的列名。
    columns = _table_columns(connection, "retrieval_experiment")
    if "archived_at" not in columns:
        connection.execute(
            "ALTER TABLE retrieval_experiment ADD COLUMN archived_at TEXT"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_retrieval_experiment_archive_created_at "
        "ON retrieval_experiment(archived_at, created_at DESC, id DESC)"
    )


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    """读取指定 SQLite 表的全部列名。

    参数：connection 为数据库连接；table_name 为待检查的表名。
    返回：表的列名集合；表不存在时由 SQLite 返回空集合。
    """
    # 查询结果中的每个 row 表示一列的 PRAGMA 元数据。
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def database_is_ready() -> bool:
    """通过轻量查询确认配置的 SQLite 数据库当前可连接。
    """
    try:
        # 短超时连接仅用于探活，不创建数据库结构或修改业务数据。
        with closing(
            sqlite3.connect(str(settings.database_path), timeout=2)
        ) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            return connection.execute("SELECT 1").fetchone() == (1,)
    except (OSError, sqlite3.Error, ValueError):
        return False
