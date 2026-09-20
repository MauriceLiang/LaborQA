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

EXPECTED_TOTAL = 60
LEGACY_SOURCE = "法条文件.docx"


def _audit(cases: Sequence[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    questions = [turn for case in cases for turn in case["turns"]]
    duplicates = sorted(
        question for question, count in Counter(questions).items() if count > 1
    )
    sources = [
        source.get("file_name")
        for case in cases
        for source in case["expected_sources"]
    ]
    unknown_sources = sorted(
        source for source in sources if source not in BASELINE_SOURCE_FILES
    )
    pinned_chunks = [
        source
        for case in cases
        for source in case["expected_sources"]
        if source.get("chunk_no") is not None
    ]
    legacy_sources = [source for source in sources if source == LEGACY_SOURCE]
    answer_cases = [case for case in cases if case["expected_type"] == "ANSWER"]
    reject_cases = [case for case in cases if case["expected_type"] == "REJECT"]
    multi_turn_cases = [case for case in cases if len(case["turns"]) > 1]
    tool_only_cases = [case for case in answer_cases if not case["expected_sources"]]
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
    cases = fixed_evaluation_cases()
    answer_cases = [case for case in cases if case["expected_type"] == "ANSWER"]
    reject_cases = [case for case in cases if case["expected_type"] == "REJECT"]
    multi_turn_cases = [case for case in cases if len(case["turns"]) > 1]
    compliance_cases = [case for case in cases if case["should_show_compliance"]]
    source_counts = Counter(
        source["file_name"]
        for case in cases
        for source in case["expected_sources"]
    )
    answers_without_sources = sum(
        not case["expected_sources"] for case in answer_cases
    )
    pinned_chunks = sum(
        source.get("chunk_no") is not None
        for case in cases
        for source in case["expected_sources"]
    )
    legacy_sources = sum(
        source["file_name"] == LEGACY_SOURCE
        for case in cases
        for source in case["expected_sources"]
    )
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
    for file_name in sorted(source_counts):
        print(f"  {file_name}: {source_counts[file_name]}")
    print(f"ANSWER without expected_source: {answers_without_sources}")
    print(f"Pinned chunk_no: {pinned_chunks}")
    print(f"Legacy source: {legacy_sources}")
    print(f"Duplicates: {duplicate_questions}")

    failures = _audit(cases)
    if failures:
        print("AUDIT FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
