# 文件作用：审计评估基线用例和结果，辅助确认基准数据的完整性。
"""Static validation for the official evaluation baseline."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.services.evaluation_cases import (
    BASELINE_SOURCE_FILES,
    baseline_version,
    fixed_evaluation_cases,
)

# 内置评估基准用例应有的条目总数。
EXPECTED_TOTAL = 60
# 基准数据兼容检查使用的旧版来源名称。
LEGACY_SOURCE = "法条文件.docx"


def _audit(cases: Sequence[dict[str, Any]]) -> list[str]:
    """检查内置基准用例的结构、内容和唯一性问题。

    参数：
    cases：本次查询或运行使用的评估用例集合。
    """
    # 局部变量 failures：本次运行中收集到的失败记录。
    failures: list[str] = []
    # 局部变量 questions：当前批次需要处理的问题集合。
    # 局部变量 case：当前评估用例记录。
    # 局部变量 turn：多轮对话中的当前一轮消息。
    questions = [turn for case in cases for turn in case["turns"]]
    # 局部变量 duplicates：重复的问题或记录列表。
    # 局部变量 question：用户提交或模型改写后的问题文本。
    # 局部变量 count：当前循环、批次或类别的计数值。
    duplicates = sorted(
        question for question, count in Counter(questions).items() if count > 1
    )
    # 局部变量 sources：当前答案或记录关联的资料来源。
    # 局部变量 source：文档、引用或记录的来源信息。
    sources = [
        source.get("file_name") for case in cases for source in case["expected_sources"]
    ]
    # 局部变量 unknown_sources：无法匹配到已知资料的来源集合。
    unknown_sources = sorted(
        source for source in sources if source not in BASELINE_SOURCE_FILES
    )
    # 局部变量 pinned_chunks：当前保留的核心文本分块。
    pinned_chunks = [
        source
        for case in cases
        for source in case["expected_sources"]
        if source.get("chunk_no") is not None
    ]
    # 局部变量 legacy_sources：兼容旧版数据时读取的来源集合。
    legacy_sources = [source for source in sources if source == LEGACY_SOURCE]
    # 局部变量 answer_cases：预期系统生成实质性答案的评估用例。
    answer_cases = [case for case in cases if case["expected_type"] == "ANSWER"]
    # 局部变量 reject_cases：预期系统拒答的评估用例集合。
    reject_cases = [case for case in cases if case["expected_type"] == "REJECT"]
    # 局部变量 multi_turn_cases：用于计算多轮对话指标的评估用例。
    multi_turn_cases = [case for case in cases if len(case["turns"]) > 1]
    # 局部变量 tool_only_cases：预期只调用工具而不生成答案的用例。
    tool_only_cases = [case for case in answer_cases if not case["expected_sources"]]
    # 局部变量 partial_cases：部分回答类评估用例集合。
    partial_cases = [case for case in answer_cases if case["topic"] == "试用期"]

    if len(cases) != EXPECTED_TOTAL:
        failures.append(f"总数应为 {EXPECTED_TOTAL}，实际为 {len(cases)}")
    if not reject_cases:
        failures.append("没有 REJECT 用例")
    if not multi_turn_cases:
        failures.append("没有多轮用例")
    if duplicates:
        failures.append(f"存在重复问题：{duplicates}")
    if legacy_sources:
        failures.append(f"存在旧知识文件引用：{len(legacy_sources)}")
    if unknown_sources:
        failures.append(f"存在未知知识文件：{unknown_sources}")
    if pinned_chunks:
        failures.append(f"存在固定 chunk_no：{len(pinned_chunks)}")
    if not tool_only_cases:
        failures.append("没有 Tool-only 回归用例")
    if not partial_cases:
        failures.append("没有 Partial 回归用例")

    return failures


def main() -> int:
    """读取并审计基准用例数据，输出问题并返回退出码。
    """
    # 局部变量 cases：本次查询或运行使用的评估用例集合。
    cases = fixed_evaluation_cases()
    # 局部变量 answer_cases：预期系统生成实质性答案的评估用例。
    # 局部变量 case：当前评估用例记录。
    answer_cases = [case for case in cases if case["expected_type"] == "ANSWER"]
    # 局部变量 reject_cases：预期系统拒答的评估用例集合。
    reject_cases = [case for case in cases if case["expected_type"] == "REJECT"]
    # 局部变量 multi_turn_cases：用于计算多轮对话指标的评估用例。
    multi_turn_cases = [case for case in cases if len(case["turns"]) > 1]
    # 局部变量 compliance_cases：用于计算合规指标的评估结果集合。
    compliance_cases = [case for case in cases if case["should_show_compliance"]]
    # 局部变量 source_counts：按来源统计的命中数量。
    # 局部变量 source：文档、引用或记录的来源信息。
    source_counts = Counter(
        source["file_name"] for case in cases for source in case["expected_sources"]
    )
    # 局部变量 answers_without_sources：缺少引用来源的回答数量。
    answers_without_sources = sum(not case["expected_sources"] for case in answer_cases)
    # 局部变量 pinned_chunks：当前保留的核心文本分块。
    pinned_chunks = sum(
        source.get("chunk_no") is not None
        for case in cases
        for source in case["expected_sources"]
    )
    # 局部变量 legacy_sources：兼容旧版数据时读取的来源集合。
    legacy_sources = sum(
        source["file_name"] == LEGACY_SOURCE
        for case in cases
        for source in case["expected_sources"]
    )
    # 局部变量 duplicate_questions：检测到的重复问题集合。
    # 局部变量 count：当前循环、批次或类别的计数值。
    # 局部变量 turn：多轮对话中的当前一轮消息。
    duplicate_questions = sum(
        count > 1
        for count in Counter(turn for case in cases for turn in case["turns"]).values()
    )

    print(f"Baseline v{baseline_version()}")
    print(f"Total: {len(cases)}")
    print(f"ANSWER: {len(answer_cases)}")
    print(f"REJECT: {len(reject_cases)}")
    print(f"Multi-turn: {len(multi_turn_cases)}")
    print(f"Compliance: {len(compliance_cases)}")
    print("Expected source distribution:")
    # 局部变量 file_name：经过校验的源文件名称。
    for file_name in sorted(source_counts):
        print(f"  {file_name}: {source_counts[file_name]}")
    print(f"ANSWER without expected_source: {answers_without_sources}")
    print(f"Pinned chunk_no: {pinned_chunks}")
    print(f"Legacy source: {legacy_sources}")
    print(f"Duplicates: {duplicate_questions}")

    # 局部变量 failures：本次运行中收集到的失败记录。
    failures = _audit(cases)
    if failures:
        print("AUDIT FAILED")
        # 局部变量 failure：当前运行或单个用例失败的错误记录。
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
