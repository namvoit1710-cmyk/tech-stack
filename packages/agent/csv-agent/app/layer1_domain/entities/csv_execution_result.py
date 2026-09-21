from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.layer1_domain.exceptions import InvalidInputError


@dataclass
class StepResult:
    """Result of a single execution step."""

    order: int
    tool: str
    success: bool
    result: Dict[str, Any]
    error: Optional[str] = None

    def validate(self) -> None:
        if self.order < 1:
            raise InvalidInputError(
                "execution_result.step_results.order", "Must be >= 1", self.order
            )
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise InvalidInputError(
                "execution_result.step_results.tool",
                "Must be a non-empty string",
                self.tool,
            )
        if not isinstance(self.result, dict):
            raise InvalidInputError(
                "execution_result.step_results.result",
                "Must be a dictionary",
                self.result,
            )
        if not self.success and not self.error:
            raise InvalidInputError(
                "execution_result.step_results.error",
                "Failed steps must include an error",
                self.error,
            )


@dataclass
class CSVExecutionResult:
    """Domain entity representing the result of CSV processing execution."""

    success: bool
    step_results: List[StepResult]
    final_file_id: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[str] = None

    @property
    def successful_steps(self) -> int:
        return sum(1 for step in self.step_results if step.success)

    @property
    def failed_steps(self) -> int:
        return sum(1 for step in self.step_results if not step.success)

    def is_complete(self) -> bool:
        return bool(self.step_results) and self.failed_steps == 0

    def validate(self) -> None:
        if not self.step_results:
            raise InvalidInputError(
                "execution_result.step_results",
                "Must contain at least one step result",
                self.step_results,
            )
        orders = [step.order for step in self.step_results]
        if len(orders) != len(set(orders)):
            raise InvalidInputError(
                "execution_result.step_results.order",
                "Step result orders must be unique",
                orders,
            )
        for step in self.step_results:
            step.validate()
        if self.success != self.is_complete():
            raise InvalidInputError(
                "execution_result.success",
                "Success must match whether all step results succeeded",
                self.success,
            )
        if not self.success and not self.error:
            raise InvalidInputError(
                "execution_result.error",
                "Failed execution results must include an error",
                self.error,
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "success": self.success,
            "step_results": [
                {
                    "order": result.order,
                    "tool": result.tool,
                    "success": result.success,
                    "result": result.result,
                    "error": result.error,
                }
                for result in sorted(self.step_results, key=lambda item: item.order)
            ],
            "final_file_id": self.final_file_id,
            "error": self.error,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CSVExecutionResult":
        """Create from dictionary representation."""
        raw_step_results = data.get("step_results", []) or []
        step_results = []

        for index, result in enumerate(raw_step_results, start=1):
            if not isinstance(result, dict):
                continue

            raw_result = result.get("result", {}) or {}
            step_results.append(
                StepResult(
                    order=int(result.get("order", index) or index),
                    tool=str(result.get("tool", "unknown") or "unknown"),
                    success=bool(result.get("success", False)),
                    result=raw_result if isinstance(raw_result, dict) else {},
                    error=result.get("error"),
                )
            )

        return cls(
            success=bool(data.get("success", False)),
            step_results=step_results,
            final_file_id=data.get("final_file_id"),
            error=data.get("error"),
            summary=data.get("summary"),
        )
