# 文件作用：记录并管理回答过程中发现的知识缺口及其后续处置。
"""Classification and management of production evidence-gate refusals."""

from __future__ import annotations

import logging
import re
import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.error_codes import ErrorCode
from app.core.errors import AppError
from app.repositories.missing_knowledge_repository import MissingKnowledgeRepository
from app.schemas.contracts import (
    MissingKnowledgeQuery,
    MissingKnowledgeUpdate,
)

# 当前模块的日志记录器，用于记录无法分类的生产拒答。
logger = logging.getLogger(__name__)


class MissingKnowledgeReason(StrEnum):
    """生产回答被拒绝或无法回答时使用的原因分类。"""

    NO_RETRIEVAL_RESULT = "NO_RETRIEVAL_RESULT"
    LOW_RELEVANCE = "LOW_RELEVANCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ExecutionMode(StrEnum):
    """产生知识缺口记录的运行环境类别。"""

    PRODUCTION = "PRODUCTION"
    EVALUATION = "EVALUATION"
    EXPERIMENT = "EXPERIMENT"


# 按关键词将问题归入知识领域的键、展示名及触发词配置。
_TOPICS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("wage_payment", "工资支付与欠薪", ("工资", "欠薪", "薪资", "加班费")),
    (
        "termination",
        "劳动关系解除与经济补偿",
        ("解除", "辞退", "开除", "裁员", "经济补偿", "赔偿金"),
    ),
    ("labor_contract", "劳动合同与劳动关系", ("劳动合同", "劳动关系", "未签合同")),
    ("social_insurance", "社会保险与住房公积金", ("社保", "社会保险", "公积金")),
    (
        "working_time",
        "工作时间与休息休假",
        ("加班", "工时", "工作时间", "休息", "年假", "休假"),
    ),
    ("work_injury", "工伤认定与待遇", ("工伤", "职业病")),
)


class MissingKnowledgeService:
    """将生产拒答归类为知识缺口，并提供记录查询和更新能力。"""

    def __init__(
        self,
        *,
        repository: MissingKnowledgeRepository | None = None,
        database_path: Path | None = None,
    ) -> None:
        """创建知识缺口服务并使用注入或默认仓储。

        参数：repository 为可选仓储；database_path 为默认仓储使用的数据库路径。
        """
        # 用于缺口分类记录、查询和更新的持久化仓储。
        self.repository = repository or MissingKnowledgeRepository(database_path)

    def record_refusal(
        self, question: str, reason: MissingKnowledgeReason
    ) -> dict[str, Any]:
        """归类并持久化一次生产回答拒绝产生的知识缺口。

        参数：question 为用户问题；reason 为拒答或证据不足原因。
        返回：新建或合并后的知识缺口记录。
        """
        if not isinstance(reason, MissingKnowledgeReason):
            raise TypeError(f"Unsupported missing-knowledge reason: {reason}")
        # 去除格式差异后的标准化问题，用作缺口聚合键。
        normalized = _normalize_question(question)
        # 根据主题关键词得到领域键和展示名称。
        topic = _classify(normalized)
        if topic is None:
            logger.error(
                "Could not classify a production refusal; using the other topic bucket"
            )
            # topic_key 用于数据聚合，missing_area 用于界面显示。
            topic_key, missing_area = "other", "其他劳动权益问题"
        else:
            # 解包分类结果中的持久化键和中文领域名称。
            topic_key, missing_area = topic
        return self.repository.record(topic_key, normalized, missing_area)

    def list(self, query: MissingKnowledgeQuery) -> tuple[list[dict[str, Any]], int]:
        """按请求分页和筛选条件查询知识缺口记录。

        参数：query 为已校验的分页、状态、关键词和排序条件。
        返回：当前页记录列表及符合条件的总数。
        """
        return self.repository.list(
            page=query.page,
            size=query.size,
            status=query.status.value if query.status else None,
            keyword=query.keyword,
            sort=query.sort,
        )

    def update(self, item_id: int, payload: MissingKnowledgeUpdate) -> dict[str, Any]:
        """更新知识缺口状态及备注，不存在时抛出统一业务错误。

        参数：item_id 为记录主键；payload 为新的状态和备注。
        返回：更新后的知识缺口记录。
        """
        # item 是仓储更新后的记录；不存在时仓储返回 None。
        item = self.repository.update(item_id, payload.status.value, payload.note)
        if item is None:
            raise AppError(
                ErrorCode.MISSING_KNOWLEDGE_NOT_FOUND,
                "缺失知识记录不存在",
                http_status=404,
            )
        return item


def _normalize_question(question: str) -> str:
    """统一 Unicode 兼容字符、空白和问题末尾标点。

    参数：question 为原始用户问题。
    返回：便于分类和去重的规范化文本。
    """
    # 先统一全角、兼容字符表示，再处理空白和句末标点。
    normalized = unicodedata.normalize("NFKC", question)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.rstrip(" ?？。.!！,，;；:：")


def _classify(question: str) -> tuple[str, str] | None:
    """按领域词表识别劳动权益问题主题。

    参数：question 为规范化后的问题文本。
    返回：领域键和中文名称；没有关键词匹配时返回 None。
    """
    # key 是持久化主题标识，area 是中文展示名，keywords 为匹配词。
    for key, area, keywords in _TOPICS:
        # 局部变量 keyword：当前查询使用的关键词。
        if any(keyword in question for keyword in keywords):
            return key, area
    return None
