import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_evaluation_service
from app.core.config import Settings, settings
from app.core.database import initialize_database
from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.main import app
from app.rag.errors import ModelUnavailableError
from app.rag.ai_modes import DEFAULT_AI_MODE
from app.rag.runtime_config import runtime_config_snapshot
from app.schemas.contracts import (
    AiMode,
    EvaluationCaseCreate,
    EvaluationCaseUpdate,
    EvaluationRunCreate,
)
from app.services.evaluation_cases import (
    BASELINE_SOURCE_FILES,
    baseline_version,
    fixed_evaluation_cases,
)
from app.services.evaluation_service import EvaluationService, _calculate_metrics
from tests.support import AsyncClientChatModel


class FakeJudge:
    def __init__(
        self,
        *,
        answer: str = (
            '{"correct":true,"context_retained":true,'
            '"matched_points":[],"missing_points":[],'
            '"contradictions":[],"reason":"测试通过"}'
        ),
    ) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        self.calls.append(
            {"messages": messages, "json_mode": json_mode, "temperature": temperature}
        )
        return self.answer


class FakeChat:
    def __init__(
        self,
        *,
        fail_question: str | None = None,
        model_failure: bool = False,
        answer_mode: str | None = None,
        evidence_decision: str | None = None,
        judge_answer: str | None = None,
    ) -> None:
        self.judge = (
            FakeJudge() if judge_answer is None else FakeJudge(answer=judge_answer)
        )
        self.chat_model = AsyncClientChatModel(self.judge)
        self.fail_question = fail_question
        self.model_failure = model_failure
        self.answer_mode = answer_mode
        self.evidence_decision = evidence_decision
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def answer_once(
        self,
        question: str,
        history: list[dict[str, str]],
        answer_style: Any,
        *,
        mode: Any,
    ) -> dict[str, Any]:
        self.calls.append((question, list(history)))
        if self.model_failure:
            raise ModelUnavailableError("model down")
        if question == self.fail_question:
            raise RuntimeError("single item failed")
        refused = question.startswith("平台今天")
        return {
            "answer": "资料不足，无法可靠判断。"
            if refused
            else "劳动关系自用工之日起建立。",
            "refused": refused,
            "citations": []
            if refused
            else [
                {
                    "chunk_id": 1,
                    "document_id": 1,
                    "file_name": "01_劳动关系与劳动合同.md",
                    "chunk_no": 1,
                    "content": "劳动关系自用工之日起建立。",
                    "score": 0.9,
                    "retrieval_score": 0.9,
                    "rerank_score": None,
                    "rank_no": 1,
                }
            ],
            "retrieved_sources": []
            if refused
            else [
                {
                    "chunk_id": 1,
                    "document_id": 1,
                    "file_name": "01_劳动关系与劳动合同.md",
                    "chunk_no": 1,
                    "content": "劳动关系自用工之日起建立。",
                    "score": 0.9,
                    "retrieval_score": 0.9,
                    "rerank_score": None,
                    "rank_no": 1,
                }
            ],
            "rewritten_question": question,
            "retrieval_ms": 4,
            "compliance_shown": False,
            "answer_mode": self.answer_mode or ("REFUSAL" if refused else "RAG_ANSWER"),
            "evidence_decision": self.evidence_decision
            or ("INSUFFICIENT" if refused else "SUFFICIENT"),
            "evidence_reason": "测试证据判断",
            "supported_points": [],
            "missing_points": [],
        }


class DiagnosticChat(FakeChat):
    async def answer_once(
        self,
        question: str,
        history: list[dict[str, str]],
        answer_style: Any,
        *,
        mode: Any,
    ) -> dict[str, Any]:
        self.calls.append((question, list(history)))
        citation = {
            "chunk_id": 1,
            "document_id": 1,
            "file_name": "01_劳动关系与劳动合同.md",
            "chunk_no": 1,
            "content": "劳动关系自用工之日起建立。",
            "score": 0.9,
            "retrieval_score": 0.9,
            "rerank_score": None,
            "rank_no": 1,
        }
        return {
            "answer": "已检索到相关资料。",
            "refused": False,
            "retrieved_sources": [citation],
            "citations": [citation] if len(self.calls) == 1 else [],
            "rewritten_question": question,
            "retrieval_ms": 4,
            "compliance_shown": False,
            "answer_mode": "RAG_ANSWER",
            "evidence_decision": "SUFFICIENT",
            "evidence_reason": "直接命中规则",
            "supported_points": ["核心要点"],
            "missing_points": [],
        }


def _service(database_path: Path, chat: FakeChat | None = None) -> EvaluationService:
    config = Settings(
        database_url=f"sqlite:///{database_path}",
        llm_api_key="test-key",
        llm_base_url="https://llm.example.test/v1",
        llm_model="test-model",
    )
    service = EvaluationService(chat or FakeChat(), config)
    service.initialize()
    return service


def _create_database(database_path: Path) -> None:
    schema = Path(__file__).parents[1] / "app" / "core" / "schema.sql"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(schema.read_text(encoding="utf-8"))


def test_evaluation_judge_uses_the_base_chat_model() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        chat = FakeChat()
        alternate = FakeJudge(answer='{"correct":false,"reason":"不应使用此模型"}')
        chat.answer_chat_model = AsyncClientChatModel(alternate)
        service = _service(database_path, chat)

        judgement = asyncio.run(
            service.judge_answer(
                {
                    "turns": ["劳动合同需要签订吗？"],
                    "expected_points": ["应当订立书面劳动合同"],
                },
                {
                    "rewritten_question": "劳动合同需要签订吗？",
                    "answer": "应当订立书面劳动合同。",
                },
            )
        )

        assert judgement["correct"] is True
        assert len(chat.judge.calls) == 1
        assert alternate.calls == []


def test_runtime_snapshot_records_thinking_profile() -> None:
    config = Settings(_env_file=None)

    snapshot = runtime_config_snapshot(config, ai_mode=AiMode.SPEED)

    assert snapshot["ai_mode"] == AiMode.SPEED.value
    assert snapshot["rewrite_thinking"] is True
    assert snapshot["rewrite_effort"] == "high"
    assert snapshot["rewrite_temperature"] == config.llm_rewrite_temperature
    assert snapshot["rewrite_top_p"] == config.llm_rewrite_top_p
    assert snapshot["rewrite_max_tokens"] == config.llm_rewrite_max_tokens
    assert snapshot["evidence_thinking"] is False
    assert snapshot["evidence_effort"] is None
    assert snapshot["evidence_temperature"] == config.llm_evidence_temperature
    assert snapshot["evidence_top_p"] == config.llm_evidence_top_p
    assert snapshot["evidence_max_tokens"] == config.llm_evidence_max_tokens
    assert snapshot["answer_thinking"] is False
    assert snapshot["answer_effort"] is None
    assert snapshot["answer_temperature"] == config.llm_answer_temperature
    assert snapshot["answer_top_p"] == config.llm_answer_top_p
    assert snapshot["answer_max_tokens"] == config.llm_answer_max_tokens
    assert DEFAULT_AI_MODE is AiMode.BALANCED


def test_fixed_cases_have_the_documented_distribution_and_json_arrays() -> None:
    cases = fixed_evaluation_cases()
    assert len(cases) == 60
    assert (
        sum(
            case["expected_type"] == "ANSWER" and len(case["turns"]) == 1
            for case in cases
        )
        == 32
    )
    assert (
        sum(
            case["expected_type"] == "ANSWER" and len(case["turns"]) > 1
            for case in cases
        )
        == 10
    )
    assert sum(case["expected_type"] == "REJECT" for case in cases) == 18
    assert all(isinstance(case["turns"], list) for case in cases)
    assert all(
        source["file_name"] in BASELINE_SOURCE_FILES
        for case in cases
        for source in case["expected_sources"]
    )
    assert all(
        source["chunk_no"] is None
        for case in cases
        for source in case["expected_sources"]
    )
    assert any(
        case["topic"] == "欠薪材料" and not case["expected_sources"] for case in cases
    )
    assert any(case["topic"] == "试用期" for case in cases)
    assert any(len(case["turns"]) > 1 for case in cases)
    assert any(case["expected_type"] == "REJECT" for case in cases)
    assert sum(case["should_show_compliance"] for case in cases) == 3
    assert sum(len(case["turns"]) == 3 for case in cases) == 3
    assert baseline_version() == 4


@pytest.mark.parametrize(
    ("case_id", "answer_mode", "evidence_decision"),
    [
        (1, "RAG_ANSWER", "SUFFICIENT"),
        (32, "PARTIAL_ANSWER", "PARTIAL"),
        (31, "TOOL_ONLY", "INSUFFICIENT"),
        (43, "REFUSAL", "INSUFFICIENT"),
    ],
)
def test_run_persists_all_answer_modes(
    case_id: int, answer_mode: str, evidence_decision: str
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(
            database_path,
            FakeChat(
                answer_mode=answer_mode,
                evidence_decision=evidence_decision,
            ),
        )
        run = service.create_run(
            EvaluationRunCreate(
                name="answer modes",
                case_ids=[case_id],
                answer_style="plain",
            )
        )

        asyncio.run(service.execute_run(run["id"]))

        result = service.get_run(run["id"])["results"][0]
        assert result["answer_mode"] == answer_mode
        assert result["evidence_decision"] == evidence_decision


def test_judge_diagnostics_are_returned_and_persisted() -> None:
    judge_answer = json.dumps(
        {
            "correct": False,
            "context_retained": True,
            "matched_points": ["已覆盖要点"],
            "missing_points": ["遗漏要点"],
            "contradictions": ["冲突内容"],
            "reason": "遗漏要点且存在冲突",
        },
        ensure_ascii=False,
    )
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(database_path, FakeChat(judge_answer=judge_answer))
        run = service.create_run(
            EvaluationRunCreate(
                name="judge diagnostics", case_ids=[1], answer_style="plain"
            )
        )

        asyncio.run(service.execute_run(run["id"]))

        result = service.get_run(run["id"])["results"][0]
        assert result["correct"] is False
        assert result["judge_reason"] == "遗漏要点且存在冲突"
        assert result["matched_points"] == ["已覆盖要点"]
        assert result["missing_points"] == ["遗漏要点"]
        assert result["contradictions"] == ["冲突内容"]


def test_evaluation_persists_per_turn_diagnostics_and_final_turn_hits() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        chat = DiagnosticChat()
        service = _service(database_path, chat)
        run = service.create_run(
            EvaluationRunCreate(
                name="turn diagnostics", case_ids=[35], answer_style="plain"
            )
        )

        asyncio.run(service.execute_run(run["id"]))

        result = service.get_run(run["id"])["results"][0]
        assert result["retrieval_hit"] is True
        assert result["source_hit"] is False
        assert result["context_retained"] is True
        assert result["citations"] == []
        assert len(result["turn_diagnostics"]) == 3
        assert result["turn_diagnostics"][0]["citations"]
        assert result["turn_diagnostics"][-1]["retrieved_sources"]
        assert result["turn_diagnostics"][-1]["citations"] == []
        assert service.get_run(run["id"])["metrics"]["retrieval_hit_rate"] == 1.0
        assert service.get_run(run["id"])["metrics"]["citation_hit_rate"] == 0.0


def test_context_retention_rate_is_independent_from_answer_accuracy() -> None:
    cases = [
        {
            "id": 1,
            "expected_type": "ANSWER",
            "turns": ["第一轮", "第二轮"],
            "expected_sources": [],
            "should_show_compliance": False,
        }
    ]
    metrics = _calculate_metrics(
        cases,
        {
            1: {
                "status": "COMPLETED",
                "correct": False,
                "refused": False,
                "source_hit": None,
                "retrieval_hit": None,
                "context_retained": True,
                "multi_turn_correct": False,
                "compliance_hit": None,
            }
        },
    )

    assert metrics["context_retention_rate"] == 1.0
    assert metrics["multi_turn_pass_rate"] == 0.0


def test_invalid_judge_diagnostics_fail_the_case() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(
            database_path,
            FakeChat(judge_answer='{"correct":false,"matched_points":"错误类型"}'),
        )
        run = service.create_run(
            EvaluationRunCreate(
                name="invalid judge", case_ids=[1], answer_style="plain"
            )
        )

        asyncio.run(service.execute_run(run["id"]))

        result = service.get_run(run["id"])["results"][0]
        assert result["status"] == "FAILED"
        assert "matched_points 列表" in result["error_message"]


def test_invalid_judge_json_fails_the_case() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(database_path, FakeChat(judge_answer="not-json"))
        run = service.create_run(
            EvaluationRunCreate(name="invalid json", case_ids=[1], answer_style="plain")
        )

        asyncio.run(service.execute_run(run["id"]))

        result = service.get_run(run["id"])["results"][0]
        assert result["status"] == "FAILED"
        assert "未返回有效 JSON" in result["error_message"]


def test_run_executes_subset_calculates_metrics_and_does_not_write_chat_tables() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        chat = FakeChat()
        service = _service(database_path, chat)
        run = service.create_run(
            EvaluationRunCreate(name="subset", case_ids=[1, 43], answer_style="legal")
        )

        config_snapshot = service.get_run(run["id"])["config"]
        assert config_snapshot["langchain_version"]
        assert config_snapshot["chat_provider"] == "langchain_openai.ChatOpenAI"
        assert config_snapshot["splitter_type"] == (
            "app.rag.splitters.LegalTextSplitter"
        )
        assert config_snapshot["splitter_version"] == "legal-text-splitter-v1"
        assert config_snapshot["vectorstore_type"] == (
            "langchain_community.vectorstores.FAISS"
        )
        assert config_snapshot["retrieval_type"] == "similarity"
        assert config_snapshot["rerank_model"] is None
        assert config_snapshot["baseline_version"] is None

        asyncio.run(service.execute_run(run["id"]))

        detail = service.get_run(run["id"])
        assert detail["status"] == "COMPLETED"
        assert detail["progress_current"] == detail["progress_total"] == 2
        assert [item["status"] for item in detail["results"]] == [
            "COMPLETED",
            "COMPLETED",
        ]
        assert detail["results"][0]["source_hit"] is True
        assert detail["results"][0]["answer_mode"] == "RAG_ANSWER"
        assert detail["results"][0]["evidence_decision"] == "SUFFICIENT"
        assert detail["results"][1]["correct"] is True
        assert detail["results"][1]["answer_mode"] == "REFUSAL"
        assert detail["results"][1]["evidence_decision"] == "INSUFFICIENT"
        assert detail["results"][1]["judge_reason"] is None
        assert detail["results"][1]["matched_points"] == []
        assert detail["results"][1]["missing_points"] == []
        assert detail["results"][1]["contradictions"] == []
        assert detail["metrics"] == {
            "accuracy": 1.0,
            "reject_rate": 1.0,
            "refusal_rate": 0.5,
            "citation_hit_rate": 1.0,
            "retrieval_hit_rate": 1.0,
            "context_retention_rate": None,
            "execution_success_rate": 1.0,
            "multi_turn_pass_rate": None,
            "compliance_hit_rate": None,
        }
        assert chat.judge.calls[0]["temperature"] == 0
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 0
            assert (
                connection.execute("SELECT COUNT(*) FROM citation").fetchone()[0] == 0
            )
            assert (
                connection.execute("SELECT COUNT(*) FROM missing_knowledge").fetchone()[
                    0
                ]
                == 0
            )


def test_multiturn_replays_prior_user_and_assistant_messages_in_memory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        chat = FakeChat()
        service = _service(database_path, chat)
        run = service.create_run(
            EvaluationRunCreate(name="multi", case_ids=[33], answer_style="plain")
        )

        asyncio.run(service.execute_run(run["id"]))

        assert len(chat.calls) == 2
        assert [item["role"] for item in chat.calls[1][1]] == ["user", "assistant"]
        assert chat.calls[1][1][1]["content"] == "劳动关系自用工之日起建立。"
        result = service.get_run(run["id"])["results"][0]
        assert result["multi_turn_correct"] is True
        assert service.get_run(run["id"])["metrics"]["multi_turn_pass_rate"] == 1.0


def test_delete_run_rejects_active_jobs_and_cascades_persisted_results() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(database_path)
        run = service.create_run(
            EvaluationRunCreate(name="delete", case_ids=[1], answer_style="plain")
        )

        with pytest.raises(AppError) as error:
            service.delete_run(run["id"])
        assert error.value.code == ErrorCode.INVALID_EVALUATION_RUN_STATE

        asyncio.run(service.execute_run(run["id"]))
        service.delete_run(run["id"])

        assert service.repository.get_run(run["id"]) is None
        with sqlite3.connect(database_path) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM evaluation_run_case WHERE run_id = ?",
                    (run["id"],),
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM evaluation_result WHERE run_id = ?",
                    (run["id"],),
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute("SELECT COUNT(*) FROM evaluation_case").fetchone()[0]
                == 60
            )


def test_item_error_is_saved_and_remaining_cases_continue() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        case = fixed_evaluation_cases()[0]
        chat = FakeChat(fail_question=case["turns"][0])
        service = _service(database_path, chat)
        run = service.create_run(
            EvaluationRunCreate(
                name="item failure", case_ids=[1, 2], answer_style="plain"
            )
        )

        asyncio.run(service.execute_run(run["id"]))

        detail = service.get_run(run["id"])
        assert detail["status"] == "COMPLETED"
        assert [item["status"] for item in detail["results"]] == ["FAILED", "COMPLETED"]
        assert detail["results"][0]["error_message"] == "single item failed"
        assert detail["metrics"]["accuracy"] == 1.0


def test_model_error_is_saved_and_remaining_cases_continue() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        chat = FakeChat(model_failure=True)
        service = _service(database_path, chat)
        run = service.create_run(
            EvaluationRunCreate(
                name="model failure", case_ids=[1, 2], answer_style="plain"
            )
        )

        asyncio.run(service.execute_run(run["id"]))

        detail = service.get_run(run["id"])
        assert detail["status"] == "COMPLETED"
        assert detail["progress_current"] == 2
        assert len(detail["results"]) == 2
        assert [item["status"] for item in detail["results"]] == [
            "FAILED",
            "FAILED",
        ]
        assert all(
            item["error_message"] == "模型服务暂不可用，请检查模型配置与服务状态"
            for item in detail["results"]
        )
        assert detail["metrics"]["accuracy"] is None


def test_startup_recovery_marks_interrupted_run_failed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(database_path)
        run = service.create_run(
            EvaluationRunCreate(name="interrupted", case_ids=[1], answer_style="plain")
        )

        service.initialize()

        detail = service.get_run(run["id"])
        assert detail["status"] == "FAILED"
        assert detail["error_message"] == "服务重启导致任务中断"


def test_custom_case_can_be_updated_and_archived_without_changing_run_snapshot() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(database_path)
        case = service.create_case(
            EvaluationCaseCreate(
                topic="自定义主题",
                expected_type="ANSWER",
                turns=["原问题"],
                expected_points=["原要点"],
                expected_sources=[],
                should_show_compliance=False,
            )
        )

        updated = service.update_case(
            case["id"],
            EvaluationCaseUpdate(
                version=1,
                topic="更新主题",
                expected_type="ANSWER",
                turns=["新问题"],
                expected_points=["新要点"],
                expected_sources=[],
                should_show_compliance=True,
            ),
        )
        assert updated["version"] == 2
        run = service.create_run(
            EvaluationRunCreate(
                name="snapshot",
                case_ids=[case["id"]],
                answer_style="plain",
            )
        )
        snapshot = service.repository.run_cases(run["id"])[0]
        service.repository.complete_run(
            run["id"],
            {
                "accuracy": None,
                "reject_rate": None,
                "refusal_rate": None,
                "citation_hit_rate": None,
                "multi_turn_pass_rate": None,
                "compliance_hit_rate": None,
            },
        )

        archived = service.archive_case(case["id"])
        assert archived["status"] == "ARCHIVED"
        assert service.repository.get_cases()[-1]["id"] == 60
        assert (
            service.repository.get_case(case["id"], include_archived=True)["status"]
            == "ARCHIVED"
        )
        assert snapshot["topic"] == "更新主题"
        assert snapshot["version"] == 2


def test_builtin_case_can_be_updated_and_run_scope_is_explicit() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "eval.db"
        _create_database(database_path)
        service = _service(database_path)

        updated = service.update_case(
            1,
            EvaluationCaseUpdate(
                version=1,
                topic="已修改的内置用例",
                expected_type="ANSWER",
                turns=["问题"],
                expected_points=["要点"],
                expected_sources=[],
                should_show_compliance=False,
            ),
        )
        assert updated["origin"] == "BUILTIN"
        assert updated["version"] == 2

        with pytest.raises(AppError) as error:
            service.archive_case(1)
        assert error.value.code == ErrorCode.BUILTIN_CASE_READ_ONLY

        baseline = service.create_run(
            EvaluationRunCreate(
                name="baseline",
                case_ids=None,
                answer_style="plain",
                case_scope="BUILTIN_BASELINE",
            )
        )
        assert baseline["case_count"] == 60
        assert (
            service.repository.get_run(baseline["id"])["config"]["case_scope"]
            == "BUILTIN_BASELINE"
        )
        assert (
            service.repository.get_run(baseline["id"])["config"]["baseline_version"]
            == 4
        )


def test_api_lists_seed_cases_and_returns_accepted_run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "api.db"
        original_database_url = settings.database_url
        original_overrides = app.dependency_overrides.copy()
        settings.database_url = f"sqlite:///{database_path}"
        try:
            initialize_database()
            service = _service(database_path)
            app.dependency_overrides[get_evaluation_service] = lambda: service
            with TestClient(app) as client:
                cases_response = client.get("/api/evaluations/cases?size=100")
                assert cases_response.status_code == 200
                assert cases_response.json()["data"]["total"] == 60
                assert isinstance(
                    cases_response.json()["data"]["items"][0]["turns"], list
                )
                multi_turn_response = client.get(
                    "/api/evaluations/cases?size=100&is_multi_turn=true"
                )
                assert multi_turn_response.json()["data"]["total"] == 10
                reject_response = client.get(
                    "/api/evaluations/cases?size=100&expected_type=REJECT"
                )
                assert reject_response.json()["data"]["total"] == 18

                invalid_run = client.post(
                    "/api/evaluations/runs",
                    json={
                        "name": "invalid",
                        "case_ids": [999],
                        "answer_style": "plain",
                    },
                )
                assert invalid_run.status_code == 400
                assert invalid_run.json()["code"] == 40003

                response = client.post(
                    "/api/evaluations/runs",
                    json={
                        "name": "api subset",
                        "case_ids": [1],
                        "answer_style": "plain",
                    },
                )
                assert response.status_code == 202
                assert response.json()["data"]["status"] == "PENDING"
                assert response.json()["data"]["progress_total"] == 1
                run_id = response.json()["data"]["run_id"]
                runs_response = client.get("/api/evaluations/runs?page=1&size=10")
                assert runs_response.status_code == 200
                assert runs_response.json()["data"]["total"] == 1
                assert client.get(f"/api/evaluations/runs/{run_id}").status_code == 200
                assert client.get("/api/evaluations/runs/999").status_code == 404

                delete_response = client.delete(f"/api/evaluations/runs/{run_id}")
                assert delete_response.status_code == 200
                assert delete_response.json() == {
                    "code": 0,
                    "message": "deleted",
                    "data": None,
                }
                assert client.get(f"/api/evaluations/runs/{run_id}").status_code == 404

                active_run = service.create_run(
                    EvaluationRunCreate(
                        name="active", case_ids=[1], answer_style="plain"
                    )
                )
                active_delete = client.delete(
                    f"/api/evaluations/runs/{active_run['id']}"
                )
                assert active_delete.status_code == 409
                assert active_delete.json()["code"] == 40903
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)
            settings.database_url = original_database_url
