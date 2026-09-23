# 文件作用：管理聊天会话、消息、引用及工具执行数据的业务转换。
"""Session and message operations for chat workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.repositories.session_repository import (
    SessionNotFoundError,
    SessionRepository,
)
from app.schemas.contracts import (
    AiMode,
    AnswerStyle,
    CitationItem,
    MessageItem,
    SessionItem,
    ToolExecutionItem,
)


class SessionService:
    """将会话仓储操作转换为 API 所需的数据结构并处理领域错误。"""

    def __init__(
        self,
        *,
        repository: SessionRepository | None = None,
        database_path: Path | None = None,
    ) -> None:
        """创建会话服务并注入仓储或由数据库路径构造默认仓储。

        参数：repository 为可选的会话仓储；database_path 为默认仓储使用的数据库路径。
        """
        # 后续会话读写统一使用的仓储实例。
        self.repository = repository or SessionRepository(database_path)

    def create_session(self, title: str | None = None) -> dict[str, Any]:
        """创建会话并将仓储记录转换为 JSON 兼容的数据项。

        参数：title 为可选的会话标题。
        """
        return self._session_item(self.repository.create_session(title))

    def get_session(self, session_id: UUID | str) -> dict[str, Any] | None:
        """按会话标识查询单个会话，不存在时返回 None。

        参数：session_id 为 UUID 对象或字符串形式的会话标识。
        """
        # 仓储返回的会话数据库记录；记录不存在时为 None。
        row = self.repository.get_session(str(session_id))
        return self._session_item(row) if row is not None else None

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """按仓储默认顺序获取最近会话并转换为 API 数据项。

        参数：limit 为最多返回的会话数量。
        """
        # 局部变量 row：数据库查询返回的当前记录行。
        return [self._session_item(row) for row in self.repository.list_sessions(limit)]

    def list_messages(self, session_id: UUID | str) -> list[dict[str, Any]]:
        """列出会话的完整消息历史，不存在的会话会转换为业务错误。

        参数：session_id 为待查询会话的 UUID 或字符串标识。
        """
        # 仓储统一使用的字符串会话标识。
        normalized_id = str(session_id)
        self._require_session(normalized_id)
        # 局部变量 row：数据库查询返回的当前记录行。
        return [
            self._message_item(row)
            for row in self.repository.list_messages(normalized_id)
        ]

    def get_recent_context(
        self, session_id: UUID | str, turns: int = 3
    ) -> list[dict[str, Any]]:
        """获取供上下文窗口使用的最近若干轮消息。

        参数：session_id 为会话标识；turns 为需要保留的对话轮数。
        """
        # 仓储统一使用的字符串会话标识。
        normalized_id = str(session_id)
        self._require_session(normalized_id)
        # 局部变量 row：数据库查询返回的当前记录行。
        return [
            self._message_item(row)
            for row in self.repository.get_recent_context(normalized_id, turns)
        ]

    def create_user_message(
        self,
        session_id: UUID | str,
        question: str,
        ai_mode: AiMode | str = AiMode.BALANCED,
    ) -> dict[str, Any]:
        """持久化用户问题及 AI 模式，并转换仓储异常为应用错误。

        参数：session_id 为会话标识；question 为用户原始问题；ai_mode 为回答模式。
        """
        try:
            # 新建的用户消息数据库记录。
            row = self.repository.create_user_message(
                str(session_id), question, ai_mode
            )
        # 局部变量 exc：当前捕获到的异常对象。
        except SessionNotFoundError as exc:
            raise self._session_not_found() from exc
        return self._message_item(row)

    def set_rewritten_question(self, message_id: int, text: str) -> None:
        """更新用户消息的查询改写文本，目标消息不存在时抛出 ValueError。

        参数：message_id 为消息主键；text 为改写后的检索问题。
        """
        if not self.repository.set_rewritten_question(message_id, text):
            raise ValueError("user message not found")

    def persist_assistant(
        self,
        session_id: UUID | str,
        content: str,
        answer_style: AnswerStyle | str,
        refused: bool,
        citations: Sequence[Mapping[str, Any] | CitationItem],
        tool_executions: Sequence[Mapping[str, Any] | ToolExecutionItem] = (),
        ai_mode: AiMode | str = AiMode.BALANCED,
    ) -> dict[str, Any]:
        """校验引用和工具记录后保存助手回答及其生成模式。

        参数：session_id、content、answer_style 和 refused 描述回答及其会话；
        citations 与 tool_executions 为需持久化的证据和工具结果；ai_mode 为回答模式。
        """
        # 将调用方传入的字典或模型对象规范化为引用模型。
        citation_items = [
            CitationItem.model_validate(self._mapping(citation))
            for citation in citations
        ]
        # 将工具执行记录规范化为统一的数据模型。
        execution_items = [
            ToolExecutionItem.model_validate(self._mapping(execution))
            for execution in tool_executions
        ]
        try:
            # 保存后的助手消息记录，包含引用、工具调用及模式快照。
            row = self.repository.persist_assistant(
                str(session_id),
                content,
                AnswerStyle(answer_style).value,
                refused,
                [
                    {
                        "chunk_id": citation.chunk_id,
                        "score": citation.score,
                        "retrieval_score": citation.retrieval_score,
                        "rerank_score": citation.rerank_score,
                        "lexical_score": citation.lexical_score,
                        "source_score": citation.source_score,
                        "lightweight_score": citation.lightweight_score,
                        "evidence_origin": citation.evidence_origin,
                        "anchor_chunk_id": citation.anchor_chunk_id,
                        "anchor_retrieval_score": citation.anchor_retrieval_score,
                        "rank_no": citation.rank_no,
                    }
                    for citation in citation_items
                ],
                [execution.model_dump(mode="json") for execution in execution_items],
                AiMode(ai_mode).value,
            )
        # 局部变量 exc：当前捕获到的异常对象。
        except SessionNotFoundError as exc:
            raise self._session_not_found() from exc
        return self._message_item(row)

    def _require_session(self, session_id: str) -> None:
        """确认会话存在，不存在时抛出统一的会话未找到错误。

        参数：session_id 为已规范化的字符串会话标识。
        """
        if self.repository.get_session(session_id) is None:
            raise self._session_not_found()

    @staticmethod
    def _session_not_found() -> AppError:
        """构造统一的会话不存在应用异常。
        """
        return AppError(ErrorCode.SESSION_NOT_FOUND, "会话不存在", http_status=404)

    @staticmethod
    def _session_item(row: Mapping[str, Any]) -> dict[str, Any]:
        """校验并序列化一条会话记录。

        参数：row 为仓储返回的会话记录映射。
        """
        return SessionItem.model_validate(row).model_dump(mode="json")

    @staticmethod
    def _message_item(row: Mapping[str, Any]) -> dict[str, Any]:
        """校验并序列化一条消息记录。

        参数：row 为仓储返回的消息记录映射。
        """
        return MessageItem.model_validate(row).model_dump(mode="json")

    @staticmethod
    def _mapping(value: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
        """将 Pydantic 模型或映射统一转换为 JSON 兼容字典。

        参数：value 为待转换的引用或工具执行数据。
        """
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return dict(value)
