"""不可跳过的校验阶段：校验器不注册为工具，模型无法请求跳过。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pmagent.contracts import LocalizationReport, ProblemContext

ValidationStatus = Literal["passed", "failed", "skipped"]
Validator = Callable[[LocalizationReport, ProblemContext | None], "ValidationOutcome"]


@dataclass
class ValidationOutcome:
    name: str
    status: ValidationStatus
    detail: str = ""


class ValidationSuite:
    """校验器由代码持有；没有"跳过校验"的入参或工具。"""

    def __init__(self, validators: dict[str, Validator]) -> None:
        self._validators = dict(validators)

    def run(
        self, report: LocalizationReport, context: ProblemContext | None = None
    ) -> list[ValidationOutcome]:
        return [validator(report, context) for validator in self._validators.values()]

    @staticmethod
    def tool_names() -> frozenset[str]:
        return frozenset()

    def names(self) -> list[str]:
        return list(self._validators)


def default_suite(code_index=None) -> ValidationSuite:  # noqa: ANN001
    def references(report: LocalizationReport, context: ProblemContext | None) -> ValidationOutcome:
        if code_index is None:
            return ValidationOutcome("references", "skipped", "无代码索引")
        files = {
            row["path"]
            for row in code_index.conn.execute("SELECT path FROM files").fetchall()
        }
        for hypothesis in report.hypotheses:
            for location in hypothesis.code_locations:
                if location.file not in files:
                    return ValidationOutcome(
                        "references", "failed", f"引用了不存在的文件：{location.file}"
                    )
        return ValidationOutcome("references", "passed", f"{len(files)} 个文件可核验")

    def self_consistency(
        report: LocalizationReport, context: ProblemContext | None
    ) -> ValidationOutcome:
        if not report.hypotheses:
            return ValidationOutcome("self_consistency", "failed", "报告没有假设")
        if report.top_root_cause_ref not in {item.id for item in report.hypotheses}:
            return ValidationOutcome("self_consistency", "failed", "首要结论不在假设列表")
        if any(item.confidence > 0.9 for item in report.hypotheses):
            return ValidationOutcome("self_consistency", "failed", "置信度超过上限")
        return ValidationOutcome("self_consistency", "passed", "首要结论与置信度一致")

    def compile_check(
        report: LocalizationReport, context: ProblemContext | None
    ) -> ValidationOutcome:
        return ValidationOutcome("compile", "skipped", "本机无 C++ 编译器，未做编译校验")

    return ValidationSuite(
        {
            "references": references,
            "self_consistency": self_consistency,
            "compile": compile_check,
        }
    )


__all__ = ["ValidationOutcome", "ValidationSuite", "default_suite"]
