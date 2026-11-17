import asyncio
import json
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID

from app.core.config import Settings
from app.rag.decision import AnswerMode, EvidenceDecision, EvidenceJudgement
from app.schemas.contracts import (
    AiMode,
    AnswerStyle,
    ChatRequest,
    MissingKnowledgeQuery,
    ToolExecutionItem,
)
from app.services.chat_service import ChatService, _stream_local_text
from app.services.compliance import COMPLIANCE_NOTICE
from app.services.material_checklist import render_material_checklist
from app.services.missing_knowledge import ExecutionMode, MissingKnowledgeReason
from app.services.session_service import SessionService
from app.services.vector_store import VectorStoreNotInitialized
from tests.support import AsyncClientChatModel


class FakeRequest:
    def __init__(self, disconnected: list[bool] | None = None) -> None:
        self.disconnected = iter(disconnected or [])

    async def is_disconnected(self) -> bool:
        return next(self.disconnected, False)


class FakeRetrieval:
    def __init__(self, evidence: list[dict[str, Any]] | None = None) -> None:
        self.evidence = evidence or []
        self.error: Exception | None = None
        self.queries: list[str] = []

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.evidence


class FakeLlm:
    def __init__(self) -> None:
        self.completions: list[tuple[list[dict[str, str]], bool]] = []
        self.streams: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        self.completions.append((messages, json_mode))
        if json_mode:
            return (
                '{"decision":"SUFFICIENT","reason":"evidence supports the answer",'
                '"supported_points":["证据支持回答"],"missing_points":[]}'
            )
        return "那公司以不符合录用条件解除劳动关系是否合法？"

    async def stream(self, messages: list[dict[str, str]]):
        self.streams.append(messages)
        yield "应结合具体证据判断。"
        yield "建议保留书面材料。"


class EmptyStreamLlm(FakeLlm):
    async def stream(self, messages: list[dict[str, str]]):
        if False:
            yield ""


def _create_database(path: Path) -> None:
    schema = Path(__file__).parents[1] / "app" / "core" / "schema.sql"
    with sqlite3.connect(path) as connection:
        connection.executescript(schema.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO document (file_name, file_type, file_path, status, chunk_count)
            VALUES ('劳动合同法.pdf', 'pdf', '/tmp/labor-law.pdf', 'SUCCESS', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO chunk (document_id, chunk_no, content, vector_key)
            VALUES (1, 1, '解除劳动合同应符合法定条件。', 'chunk:1')
            """
        )


def _service(directory: str, *, score: float = 0.82):
    database_path = Path(directory) / "test.db"
    _create_database(database_path)
    config = Settings(
        database_url=f"sqlite:///{database_path}",
        llm_api_key="test-key",
        llm_base_url="https://llm.example.test/v1",
        llm_model="test-model",
    )
    session_service = SessionService(database_path=database_path)
    retrieval = FakeRetrieval(
        [
            {
                "chunk_id": 1,
                "document_id": 1,
                "file_name": "劳动合同法.pdf",
                "chunk_no": 1,
                "content": "解除劳动合同应符合法定条件。",
                "score": score,
                "retrieval_score": score,
                "rerank_score": None,
                "rank_no": 1,
            }
        ]
    )
    llm = FakeLlm()
    service = ChatService(
        session_service,
        retrieval,
        config,
        chat_model=AsyncClientChatModel(llm),
    )
    return service, session_service, retrieval, llm


def _events(
    service: ChatService,
    session_id: str,
    request: FakeRequest | None = None,
    *,
    question: str = "那公司这样解除劳动关系呢？",
    answer_style: str = "plain",
    execution_mode: ExecutionMode = ExecutionMode.PRODUCTION,
):
    payload = ChatRequest(
        session_id=UUID(session_id),
        question=question,
        answer_style=answer_style,
    )
    return asyncio.run(
        _collect(
            service.stream_chat(
                payload, request or FakeRequest(), execution_mode=execution_mode
            )
        )
    )


async def _collect(stream):
    return [event async for event in stream]


def _event_types(events: list[str]) -> list[str]:
    return [event.splitlines()[0] for event in events]


def _event_payload(event: str) -> dict[str, Any]:
    return json.loads(event.splitlines()[1].removeprefix("data: ").strip())


def test_stream_local_text_reassembles_original_text() -> None:
    text = "根据你描述的欠薪情况，可先整理以下材料。"

    chunks = asyncio.run(
        _collect(
            _stream_local_text(
                text,
                chunk_size=4,
                delay_seconds=0,
            )
        )
    )

    assert len(chunks) > 1
    assert "".join(chunks) == text


def test_stream_local_text_handles_short_text() -> None:
    chunks = asyncio.run(
        _collect(
            _stream_local_text(
                "测试",
                chunk_size=8,
                delay_seconds=0,
            )
        )
    )

    assert chunks == ["测试"]


def test_stream_local_text_handles_empty_text() -> None:
    chunks = asyncio.run(
        _collect(
            _stream_local_text(
                "",
                delay_seconds=0,
            )
        )
    )

    assert chunks == []


def _assert_done_timings(events: list[str]) -> None:
    timings = _event_payload(events[-1])["timings"]
    expected_keys = {
        "session_ms",
        "rewrite_ms",
        "retrieval_ms",
        "evidence_judge_ms",
        "pre_answer_ms",
        "ttft_ms",
        "answer_generation_ms",
        "persist_ms",
        "total_ms",
    }
    assert set(timings) == expected_keys
    assert all(
        value >= 0
        for key, value in timings.items()
        if key != "ttft_ms" and value is not None
    )
    if timings["ttft_ms"] is not None:
        assert timings["ttft_ms"] >= 0
        assert timings["total_ms"] >= timings["ttft_ms"]


def test_injected_chat_model_is_used_for_all_rag_roles() -> None:
    with tempfile.TemporaryDirectory() as directory:
        with patch("app.services.chat_service.build_chat_model") as build_model:
            service, _, _, _ = _service(directory)

        build_model.assert_not_called()
        assert service.chat_model is service.evidence_chat_model
        assert service.chat_model is service.answer_chat_model
        assert service.rag_chain.chat_model is service.chat_model
        assert service.rag_chain.evidence_model is service.chat_model
        assert service.rag_chain.answer_model is service.chat_model


def test_configured_chat_service_builds_role_specific_models() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config = Settings(
            _env_file=None,
            database_url=f"sqlite:///{Path(directory) / 'service.db'}",
            llm_api_key="test-key",
            llm_base_url="https://llm.example.test/v1",
            llm_model="test-model",
        )
        base_model = AsyncClientChatModel(FakeLlm())
        evidence_model = AsyncClientChatModel(FakeLlm())
        answer_model = AsyncClientChatModel(FakeLlm())
        with patch(
            "app.services.chat_service.build_chat_model",
            side_effect=[base_model, evidence_model, answer_model],
        ) as build_model:
            service = ChatService(
                SessionService(database_path=config.database_path),
                FakeRetrieval(),
                config,
            )

        assert build_model.call_count == 3
        assert build_model.call_args_list[0].args == (config,)
        assert build_model.call_args_list[0].kwargs == {
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
        }
        assert build_model.call_args_list[1].kwargs == {"thinking_mode": "disabled"}
        assert build_model.call_args_list[2].kwargs == {
            "thinking_mode": "enabled",
            "reasoning_effort": "low",
        }
        assert service.chat_model is base_model
        assert service.chat_models["off"] is evidence_model
        assert service.chat_models["low"] is answer_model
        assert service.evidence_chat_model is answer_model
        assert service.answer_chat_model is evidence_model
        assert service.rag_chains[AiMode.SPEED].evidence_model is evidence_model
        assert service.rag_chains[AiMode.SPEED].answer_model is evidence_model
        assert service.rag_chains[AiMode.BALANCED].evidence_model is answer_model
        assert service.rag_chains[AiMode.BALANCED].answer_model is evidence_model
        assert service.rag_chains[AiMode.QUALITY].evidence_model is base_model
        assert service.rag_chains[AiMode.QUALITY].answer_model is base_model


def test_rag_stream_persists_answer_and_only_retrieved_citations(caplog) -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, retrieval, llm = _service(directory)
        session = sessions.create_session()
        caplog.set_level(logging.INFO, logger="app.services.chat_service")

        events = _events(service, session["id"])

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: token",
            "event: token",
            "event: sources",
            "event: done",
        ]
        assert [_event_payload(event)["stage"] for event in events[:4]] == [
            "understanding",
            "retrieving",
            "verifying",
            "generating",
        ]
        _assert_done_timings(events)
        assert retrieval.queries == ["那公司这样解除劳动关系呢？"]
        assert len(llm.completions) == 1
        messages = sessions.list_messages(session["id"])
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[0]["rewritten_question"] == "那公司这样解除劳动关系呢？"
        assert messages[1]["content"] == "应结合具体证据判断。建议保留书面材料。"
        assert messages[1]["citations"][0]["chunk_id"] == 1
        latency_logs = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("chat_latency ")
        ]
        assert len(latency_logs) == 1
        latency_payload = json.loads(latency_logs[0].removeprefix("chat_latency "))
        assert latency_payload["ai_mode"] == "balanced"
        assert latency_payload["delivery_mode"] == "llm_stream"
        assert latency_payload["evidence_thinking"] is True
        assert latency_payload["evidence_effort"] == "low"
        assert latency_payload["answer_thinking"] is False
        assert latency_payload["answer_effort"] is None
        assert latency_payload["rewrite_temperature"] == 0.1
        assert latency_payload["rewrite_max_tokens"] == 512
        assert latency_payload["evidence_temperature"] == 0.0
        assert latency_payload["evidence_max_tokens"] == 1024
        assert latency_payload["answer_temperature"] == 0.2
        assert latency_payload["answer_max_tokens"] == 4096
        assert "那公司这样解除劳动关系呢？" not in latency_logs[0]
        assert "应结合具体证据判断。建议保留书面材料。" not in latency_logs[0]
        assert "解除劳动合同应符合法定条件。" not in latency_logs[0]
        assert "test-key" not in latency_logs[0]


def test_evaluation_answer_once_reuses_rag_without_production_persistence() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, retrieval, _ = _service(directory)

        result = asyncio.run(
            service.answer_once(
                "劳动仲裁时效是多少？",
                [],
                AnswerStyle.PLAIN,
                mode=ExecutionMode.EVALUATION,
            )
        )

        assert result["refused"] is False
        assert result["citations"][0]["chunk_id"] == 1
        assert result["compliance_shown"] is True
        assert COMPLIANCE_NOTICE in result["answer"]
        assert retrieval.queries == ["劳动仲裁时效是多少？"]
        with sqlite3.connect(sessions.repository.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 0
            assert (
                connection.execute("SELECT COUNT(*) FROM missing_knowledge").fetchone()[
                    0
                ]
                == 0
            )


def test_answer_once_exposes_answer_modes_and_diagnostics() -> None:
    class PartialJudge(FakeLlm):
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            json_mode: bool = False,
            temperature: float | None = None,
        ) -> str:
            self.completions.append((messages, json_mode))
            if json_mode:
                return (
                    '{"decision":"PARTIAL","reason":"仅支持部分事实",'
                    '"supported_points":["已知事实"],"missing_points":["关键条件"]}'
                )
            return "仅依据已知事实说明。"

    class InsufficientJudge(FakeLlm):
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            json_mode: bool = False,
            temperature: float | None = None,
        ) -> str:
            self.completions.append((messages, json_mode))
            if json_mode:
                return (
                    '{"decision":"INSUFFICIENT","reason":"证据不足",'
                    '"supported_points":[],"missing_points":["关键条件"]}'
                )
            return "不应生成普通回答。"

    cases = (
        ("劳动仲裁时效是多少？", 0.82, FakeLlm(), AnswerMode.RAG_ANSWER),
        ("劳动仲裁时效是多少？", 0.82, PartialJudge(), AnswerMode.PARTIAL_ANSWER),
        ("劳动仲裁时效是多少？", 0.82, InsufficientJudge(), AnswerMode.REFUSAL),
        (
            "公司拖欠工资，我应该准备什么材料？",
            0.1,
            FakeLlm(),
            AnswerMode.TOOL_ONLY,
        ),
    )

    for question, score, llm, expected_mode in cases:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, _ = _service(directory, score=score)
            service.chat_model = AsyncClientChatModel(llm)
            service.rag_chain.set_chat_model(service.chat_model)

            result = asyncio.run(
                service.answer_once(
                    question,
                    [],
                    AnswerStyle.PLAIN,
                    mode=ExecutionMode.EVALUATION,
                )
            )

            assert result["answer_mode"] == expected_mode.value
            assert result["evidence_decision"] in {
                EvidenceDecision.SUFFICIENT.value,
                EvidenceDecision.PARTIAL.value,
                EvidenceDecision.INSUFFICIENT.value,
            }
            if expected_mode is AnswerMode.TOOL_ONLY:
                assert result["refused"] is False
                assert result["tool_execution"]["tool_name"] == (
                    "generate_rights_material_checklist"
                )
                assert result["citations"] == []
            elif expected_mode is AnswerMode.REFUSAL:
                assert result["refused"] is True
                assert result["tool_execution"] is None
            else:
                assert result["refused"] is False


def test_answer_mode_mapping_is_pure_and_prioritizes_tool_requests() -> None:
    sufficient = EvidenceJudgement(
        decision=EvidenceDecision.SUFFICIENT,
        reason="完整",
    )
    partial = EvidenceJudgement(decision=EvidenceDecision.PARTIAL, reason="部分")
    insufficient = EvidenceJudgement(
        decision=EvidenceDecision.INSUFFICIENT,
        reason="不足",
    )
    with tempfile.TemporaryDirectory() as directory:
        service, _, _, _ = _service(directory)
        checklist = service.tool_decision_service.decide(
            "公司拖欠工资，我应该准备什么材料？",
            "公司拖欠工资，我应该准备什么材料？",
        )
        assert checklist is not None
        assert (
            service._decide_answer_mode(sufficient, checklist) is AnswerMode.RAG_ANSWER
        )
        assert service._decide_answer_mode(partial, checklist) is AnswerMode.TOOL_ONLY
        assert (
            service._decide_answer_mode(insufficient, checklist) is AnswerMode.TOOL_ONLY
        )
        assert service._decide_answer_mode(sufficient, None) is AnswerMode.RAG_ANSWER
        assert service._decide_answer_mode(partial, None) is AnswerMode.PARTIAL_ANSWER
        assert service._decide_answer_mode(insufficient, None) is AnswerMode.REFUSAL


def test_low_similarity_evidence_is_refused_without_calling_model() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, llm = _service(directory, score=0.1)
        record = Mock(wraps=service.missing_knowledge_service.record_refusal)
        service.missing_knowledge_service.record_refusal = record
        session = sessions.create_session()

        events = _events(service, session["id"])

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: token",
            "event: sources",
            "event: done",
        ]
        assert '"items":[]' in events[5]
        assert '"refused":true' in events[6]
        _assert_done_timings(events)
        assert llm.completions == []
        rows, total = service.missing_knowledge_service.list(MissingKnowledgeQuery())
        assert total == 1
        assert rows[0]["topic_key"] == "termination"
        assert rows[0]["count"] == 1
        assert record.call_args.args[1] is MissingKnowledgeReason.LOW_RELEVANCE
        messages = sessions.list_messages(session["id"])
        assert messages[-1]["refused"] is True
        assert messages[-1]["citations"] == []


def test_empty_retrieval_result_is_recorded_with_its_evidence_reason() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, retrieval, _ = _service(directory)
        retrieval.evidence = []
        record = Mock(wraps=service.missing_knowledge_service.record_refusal)
        service.missing_knowledge_service.record_refusal = record
        session = sessions.create_session()

        _events(service, session["id"], question="劳动关系解除后能否获得补偿？")

        assert record.call_args.args[1].value == "NO_RETRIEVAL_RESULT"


def test_insufficient_evidence_judgement_is_recorded() -> None:
    class InsufficientJudge(FakeLlm):
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            json_mode: bool = False,
            temperature: float | None = None,
        ) -> str:
            return (
                '{"decision":"INSUFFICIENT","reason":"not supported",'
                '"supported_points":[],"missing_points":["关键事实"]}'
            )

    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, _ = _service(directory)
        service.chat_model = AsyncClientChatModel(InsufficientJudge())
        service.rag_chain.set_chat_model(service.chat_model)
        record = Mock(wraps=service.missing_knowledge_service.record_refusal)
        service.missing_knowledge_service.record_refusal = record
        session = sessions.create_session()

        _events(service, session["id"], question="劳动关系解除后能否获得补偿？")

        assert record.call_args.args[1].value == "INSUFFICIENT_EVIDENCE"


def test_partial_evidence_generates_scoped_answer_without_missing_record() -> None:
    class PartialJudge(FakeLlm):
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            json_mode: bool = False,
            temperature: float | None = None,
        ) -> str:
            self.completions.append((messages, json_mode))
            if json_mode:
                return (
                    '{"decision":"PARTIAL","reason":"仅覆盖部分事实",'
                    '"supported_points":["已知事实"],"missing_points":["关键条件"]}'
                )
            return "普通回答不应使用这一路径。"

    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, llm = _service(directory)
        partial_llm = PartialJudge()
        service.chat_model = AsyncClientChatModel(partial_llm)
        service.rag_chain.set_chat_model(service.chat_model)
        record = Mock(wraps=service.missing_knowledge_service.record_refusal)
        service.missing_knowledge_service.record_refusal = record
        session = sessions.create_session()

        events = _events(
            service,
            session["id"],
            question="我应该如何保存工资记录？",
        )

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: token",
            "event: token",
            "event: sources",
            "event: done",
        ]
        _assert_done_timings(events)
        prompt = partial_llm.streams[0][0]["content"]
        assert "回答模式：PARTIAL_ANSWER" in prompt
        assert '"已知事实"' in prompt
        assert '"关键条件"' in prompt
        assert record.call_count == 0
        assistant = sessions.list_messages(session["id"])[-1]
        assert assistant["refused"] is False
        assert assistant["citations"]
        assert llm.completions == []


def test_failure_to_record_does_not_break_refusal_stream() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, _ = _service(directory, score=0.1)
        session = sessions.create_session()

        def fail_record(*_args, **_kwargs):
            raise RuntimeError("database unavailable")

        service.missing_knowledge_service.record_refusal = fail_record
        events = _events(service, session["id"])

        assert events[4].startswith("event: token")
        assert '"refused":true' in events[-1]


def test_evidence_judge_failure_refuses_without_recording_missing_knowledge() -> None:
    class BrokenJudge(FakeLlm):
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            json_mode: bool = False,
            temperature: float | None = None,
        ) -> str:
            raise RuntimeError("judge unavailable")

    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, _ = _service(directory)
        service.chat_model = AsyncClientChatModel(BrokenJudge())
        service.rag_chain.set_chat_model(service.chat_model)
        session = sessions.create_session()

        events = _events(service, session["id"])
        rows, total = service.missing_knowledge_service.list(MissingKnowledgeQuery())

        assert '"refused":true' in events[-1]
        assert total == 0
        assert rows == []


def test_non_production_refusal_does_not_record_missing_knowledge() -> None:
    for mode in (ExecutionMode.EVALUATION, ExecutionMode.EXPERIMENT):
        with tempfile.TemporaryDirectory() as directory:
            service, sessions, _, _ = _service(directory, score=0.1)
            session = sessions.create_session()

            try:
                _events(service, session["id"], execution_mode=mode)
            except ValueError as error:
                assert str(error) == "stream_chat only supports PRODUCTION mode"
            else:
                raise AssertionError(
                    "Expected non-production stream mode to be rejected"
                )
            _, total = service.missing_knowledge_service.list(MissingKnowledgeQuery())

            assert total == 0
            assert sessions.list_messages(session["id"]) == []


def test_recent_session_turns_are_used_to_rewrite_the_retrieval_query() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, retrieval, llm = _service(directory)
        session = sessions.create_session()
        sessions.create_user_message(session["id"], "试用期被辞退有补偿吗？")
        sessions.persist_assistant(
            session["id"], "要看解除理由和证据。", "plain", False, []
        )

        _events(service, session["id"])

        assert retrieval.queries == ["那公司以不符合录用条件解除劳动关系是否合法？"]
        assert len(llm.completions) == 2
        latest_user = sessions.list_messages(session["id"])[2]
        assert latest_user["rewritten_question"] == retrieval.queries[0]
        rewrite_payload = llm.completions[0][0][1]["content"]
        assert "试用期被辞退有补偿吗？" in rewrite_payload


def test_index_not_ready_returns_terminal_error_and_keeps_user_message() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, retrieval, _ = _service(directory)
        retrieval.error = VectorStoreNotInitialized("not ready")
        session = sessions.create_session()

        events = _events(service, session["id"])

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: error",
        ]
        assert '"code":50301' in events[2]
        assert [
            message["role"] for message in sessions.list_messages(session["id"])
        ] == ["user"]


def test_client_disconnect_does_not_persist_partial_assistant_message() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, _ = _service(directory)
        session = sessions.create_session()

        events = _events(
            service,
            session["id"],
            FakeRequest(disconnected=[False, False, True]),
        )

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: token",
        ]
        assert [
            message["role"] for message in sessions.list_messages(session["id"])
        ] == ["user"]


def test_empty_model_stream_emits_a_terminal_error() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, _ = _service(directory)
        service.chat_model = AsyncClientChatModel(EmptyStreamLlm())
        service.rag_chain.set_chat_model(service.chat_model)
        session = sessions.create_session()

        events = _events(service, session["id"])

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: error",
        ]
        assert '"code":50302' in events[4]
        assert [
            message["role"] for message in sessions.list_messages(session["id"])
        ] == ["user"]


def test_material_request_emits_tool_before_answer_and_persists_tool_result() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, llm = _service(directory)
        session = sessions.create_session()

        events = _events(
            service,
            session["id"],
            question="公司拖欠工资，我应该准备什么材料？",
        )

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: tool",
            "event: token",
            "event: token",
            "event: sources",
            "event: done",
        ]
        tool_event = json.loads(events[4].splitlines()[1][6:])
        assert tool_event["tool_name"] == "generate_rights_material_checklist"
        assert tool_event["input"] == {
            "dispute_type": "欠薪",
            "description": "公司拖欠工资，我应该准备什么材料？",
        }
        assert tool_event["output"]["materials"] == [
            "劳动合同或能够证明劳动关系的材料",
            "工资条、银行流水等工资支付记录",
            "考勤、排班或工作记录",
            "与用人单位沟通欠薪问题的记录",
        ]
        assert "工资条、银行流水等工资支付记录" in llm.streams[0][0]["content"]
        assistant = sessions.list_messages(session["id"])[-1]
        assert assistant["tool_executions"] == [tool_event]


def test_tool_failure_emits_only_terminal_error_without_fabricated_result() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, _ = _service(directory)
        session = sessions.create_session()

        def fail_tool(_payload):
            raise RuntimeError("tool unavailable")

        service.material_checklist_tool.execute = fail_tool
        events = _events(
            service,
            session["id"],
            question="公司拖欠工资，我应该准备什么材料？",
        )

        assert _event_types(events) == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: error",
        ]
        assert '"message":"材料清单生成失败"' in events[4]
        assert [
            message["role"] for message in sessions.list_messages(session["id"])
        ] == ["user"]


def test_material_checklist_runs_as_tool_only_when_evidence_gate_refuses(caplog):
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, llm = _service(directory, score=0.1)
        session = sessions.create_session()
        caplog.set_level(logging.INFO, logger="app.services.chat_service")

        async def should_not_be_called(*args: Any, **kwargs: Any):
            raise AssertionError("TOOL_ONLY must not invoke answer LLM")

        with patch.object(service.rag_chain, "stream_answer", should_not_be_called):
            events = _events(
                service,
                session["id"],
                question="公司拖欠工资，我应该准备什么材料？",
            )

        assert _event_types(events)[:5] == [
            "event: status",
            "event: status",
            "event: status",
            "event: status",
            "event: tool",
        ]
        assert _event_types(events)[-2:] == ["event: sources", "event: done"]
        token_events = events[5:-2]
        assert len(token_events) > 1
        assert all(event.startswith("event: token") for event in token_events)
        tool_event = json.loads(events[4].splitlines()[1][6:])
        assert tool_event["tool_name"] == "generate_rights_material_checklist"
        rendered_answer = render_material_checklist(
            ToolExecutionItem.model_validate(tool_event)
        )
        assert "".join(_event_payload(event)["content"] for event in token_events) == (
            rendered_answer
        )
        assert '"items":[]' in events[-2]
        assert '"refused":false' in events[-1]
        _assert_done_timings(events)
        assert llm.completions == []
        assert llm.streams == []
        latency_logs = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("chat_latency ")
        ]
        assert len(latency_logs) == 1
        latency_payload = json.loads(latency_logs[0].removeprefix("chat_latency "))
        assert latency_payload["delivery_mode"] == "synthetic_stream"
        rows, total = service.missing_knowledge_service.list(MissingKnowledgeQuery())
        assert rows == []
        assert total == 0
        assistant = sessions.list_messages(session["id"])[-1]
        assert assistant["refused"] is False
        assert assistant["content"] == rendered_answer
        assert assistant["citations"] == []
        assert assistant["tool_executions"] == [tool_event]


def test_compliance_notice_is_forced_for_both_styles_without_changing_sources():
    outputs = []
    for answer_style in ("plain", "legal"):
        with tempfile.TemporaryDirectory() as directory:
            service, sessions, retrieval, llm = _service(directory)
            session = sessions.create_session()
            events = _events(
                service,
                session["id"],
                question="劳动仲裁的申请期限是多久？",
                answer_style=answer_style,
            )
            response = sessions.list_messages(session["id"])[-1]
            prompt = llm.streams[0][0]["content"]
            sources_event = next(
                event for event in events if event.startswith("event: sources")
            )
            sources = json.loads(sources_event.splitlines()[1][6:])
            outputs.append((response, sources, retrieval.queries, prompt))

            assert COMPLIANCE_NOTICE in response["content"]
            assert "需要合规提示：true" in prompt
            assert COMPLIANCE_NOTICE in prompt

    assert outputs[0][1] == outputs[1][1]
    assert outputs[0][2] == outputs[1][2]
    assert outputs[0][0]["citations"] == outputs[1][0]["citations"]
    assert "面向普通劳动者" in outputs[0][3]
    assert "严谨、客观" in outputs[1][3]


def test_concrete_route_in_final_answer_adds_notice_even_if_question_did_not():
    class RouteAnswerLlm(FakeLlm):
        async def stream(self, messages: list[dict[str, str]]):
            self.streams.append(messages)
            yield "可以向当地劳动监察部门投诉。"

    with tempfile.TemporaryDirectory() as directory:
        service, sessions, _, _ = _service(directory)
        llm = RouteAnswerLlm()
        service.chat_model = AsyncClientChatModel(llm)
        service.rag_chain.set_chat_model(service.chat_model)
        session = sessions.create_session()

        events = _events(service, session["id"], question="我现在应该如何处理？")

        assistant = sessions.list_messages(session["id"])[-1]
        assert COMPLIANCE_NOTICE in assistant["content"]
        assert events[-3].startswith("event: token")


def test_compliance_notice_is_not_triggered_by_retrieved_evidence_alone():
    with tempfile.TemporaryDirectory() as directory:
        service, sessions, retrieval, llm = _service(directory)
        retrieval.evidence[0]["content"] = "可以向当地劳动监察部门了解情况。"
        session = sessions.create_session()

        _events(service, session["id"], question="我应该如何保存工资记录？")

        assistant = sessions.list_messages(session["id"])[-1]
        prompt = llm.streams[0][0]["content"]
        assert COMPLIANCE_NOTICE not in assistant["content"]
        assert "需要合规提示：false" in prompt
