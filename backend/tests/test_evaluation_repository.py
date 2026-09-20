import sqlite3
import tempfile
from pathlib import Path

from app.core.database import _migrate_evaluation_schema
from app.repositories.evaluation_repository import EvaluationRepository
from app.services.evaluation_cases import fixed_evaluation_cases


def _create_database(database_path: Path) -> None:
    schema = Path(__file__).parents[1] / "app" / "core" / "schema.sql"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(schema.read_text(encoding="utf-8"))


def _custom_case() -> dict[str, object]:
    return {
        "topic": "自定义回归",
        "expected_type": "ANSWER",
        "turns": ["自定义问题"],
        "expected_points": ["自定义要点"],
        "expected_sources": [],
        "should_show_compliance": False,
    }


def test_seed_new_baseline_archives_old_builtin_and_preserves_custom() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "evaluation.db"
        _create_database(database_path)
        repository = EvaluationRepository(database_path)
        cases = fixed_evaluation_cases()[:2]

        repository.seed_cases(cases, baseline_version=1)
        custom = repository.create_case(_custom_case())
        repository.seed_cases(cases, baseline_version=2)
        repository.seed_cases(cases, baseline_version=2)

        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                """
                SELECT origin, status, builtin_key, archived_at
                FROM evaluation_case
                ORDER BY id
                """
            ).fetchall()

        assert rows[0][0:3] == ("BUILTIN", "ARCHIVED", "baseline-v1-001")
        assert rows[1][0:3] == ("BUILTIN", "ARCHIVED", "baseline-v1-002")
        assert rows[2][0:3] == ("CUSTOM", "ACTIVE", None)
        assert rows[3][0:3] == ("BUILTIN", "ACTIVE", "baseline-v2-001")
        assert rows[4][0:3] == ("BUILTIN", "ACTIVE", "baseline-v2-002")
        assert rows[0][3] is not None
        assert custom["origin"] == "CUSTOM"

        with sqlite3.connect(database_path) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM evaluation_case WHERE builtin_key LIKE 'baseline-v2-%'"
                ).fetchone()[0]
                == 2
            )


def test_result_round_trip_preserves_answer_mode_and_evidence_decision() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "evaluation.db"
        _create_database(database_path)
        repository = EvaluationRepository(database_path)
        repository.seed_cases(fixed_evaluation_cases()[:1], baseline_version=2)
        run = repository.create_run(
            "mode", repository.get_cases(), {"answer_style": "plain"}
        )
        repository.add_result(
            run["id"],
            {
                "case_id": 1,
                "status": "COMPLETED",
                "answer": "回答",
                "refused": False,
                "correct": True,
                "source_hit": True,
                "multi_turn_correct": None,
                "compliance_hit": None,
                "answer_mode": "PARTIAL_ANSWER",
                "evidence_decision": "PARTIAL",
                "judge_reason": "遗漏关键要点",
                "matched_points": ["已覆盖要点"],
                "missing_points": ["遗漏要点"],
                "contradictions": ["冲突内容"],
                "citations": [],
                "latency_ms": 1,
                "error_message": None,
            },
        )

        result = repository.get_run(run["id"])["results"][0]
        assert result["answer_mode"] == "PARTIAL_ANSWER"
        assert result["evidence_decision"] == "PARTIAL"
        assert result["judge_reason"] == "遗漏关键要点"
        assert result["matched_points"] == ["已覆盖要点"]
        assert result["missing_points"] == ["遗漏要点"]
        assert result["contradictions"] == ["冲突内容"]


def test_old_evaluation_result_migration_adds_nullable_mode_columns() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(
            """
            CREATE TABLE evaluation_run (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE evaluation_result (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                refused INTEGER
            );
            CREATE TABLE evaluation_case (
                id INTEGER PRIMARY KEY,
                topic TEXT NOT NULL,
                expected_type TEXT NOT NULL,
                turns_json TEXT NOT NULL,
                expected_points_json TEXT NOT NULL,
                expected_sources_json TEXT NOT NULL,
                should_show_compliance INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE evaluation_run_case (
                run_id INTEGER NOT NULL,
                case_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                case_snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO evaluation_run (id, status) VALUES (1, 'COMPLETED');
            INSERT INTO evaluation_result (id, run_id, status, refused)
            VALUES (1, 1, 'COMPLETED', 0);
            """
        )

        _migrate_evaluation_schema(connection)

        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(evaluation_result)")
        }
        assert {
            "answer_mode",
            "evidence_decision",
            "judge_reason",
            "matched_points_json",
            "missing_points_json",
            "contradictions_json",
            "turn_diagnostics_json",
            "context_retained",
            "retrieval_hit",
        }.issubset(columns)
        assert connection.execute(
            """
            SELECT answer_mode, evidence_decision, judge_reason,
                   matched_points_json, missing_points_json, contradictions_json
            FROM evaluation_result WHERE id = 1
            """
        ).fetchone() == (None, None, None, None, None, None)

        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(evaluation_run)")
        }
        assert {
            "retrieval_hit_rate",
            "context_retention_rate",
            "execution_success_rate",
        }.issubset(run_columns)
