"""Domain entity: the outcome of a validation execution.

A proper typed ``@dataclass`` — `from_executor` narrows the raw data-factory
payload into typed fields once, so the rest of the code is fully typed (no `Any`).
Rule/step payloads round-trip verbatim, so they stay `list[dict[str, object]]`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast


@dataclass
class ExecutionResult:
    message: str = ""
    rules: list[dict[str, object]] = field(default_factory=list)
    has_result: bool = False
    invalid_rows: int = 0
    file_id: str = ""
    file_url: str = ""

    @classmethod
    def rule_only(cls, steps: list[dict[str, object]]) -> "ExecutionResult":
        """No-file path: the generated rules, no executed file result."""
        return cls(message="Rules generated successfully.", rules=list(steps), has_result=False)

    @classmethod
    def from_executor(cls, raw: dict[str, object]) -> "ExecutionResult":
        """Narrow the raw column-executor / data-factory response into typed fields."""
        message = raw.get("message")
        rules_value = raw.get("rules")
        result_data = raw.get("result")
        result_data = result_data if isinstance(result_data, dict) else {}
        odata = result_data.get("odata")
        odata = odata if isinstance(odata, dict) else {}
        invalid_rows = result_data.get("invalid_rows", 0)
        file_id = odata.get("result_file_id", "")
        file_url = odata.get("result_file_url", "")
        rules = (
            [cast("dict[str, object]", step) for step in rules_value if isinstance(step, dict)]
            if isinstance(rules_value, list)
            else []
        )
        return cls(
            message=message if isinstance(message, str) else "",
            rules=rules,
            has_result=bool(result_data),
            invalid_rows=invalid_rows if isinstance(invalid_rows, int) else 0,
            file_id=file_id if isinstance(file_id, str) else "",
            file_url=file_url if isinstance(file_url, str) else "",
        )

    @property
    def validate_status(self) -> bool:
        return self.invalid_rows == 0
