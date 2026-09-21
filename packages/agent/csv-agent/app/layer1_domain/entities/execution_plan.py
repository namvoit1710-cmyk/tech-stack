from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from app.layer1_domain.exceptions import InvalidInputError

KNOWN_CSV_TOOLS = frozenset(
    {
        "convert_json_to_csv",
        "convert_csv_to_json",
        "query_file_data",
        "get_first_n_rows",
        "create_file",
        "transform_column",
        "transform_row",
        "revert_file",
        "import_file_to_node",
        "plus",
        "minus",
    }
)


@dataclass
class ExecutionStep:
    """Represents a single step in an execution plan."""

    order: int
    tool: str
    input: Dict[str, Any]
    reason: str

    def validate(self, allowed_tools: Iterable[str] | None = None) -> None:
        if self.order < 1:
            raise InvalidInputError(
                "execution_plan.steps.order", "Must be >= 1", self.order
            )
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise InvalidInputError(
                "execution_plan.steps.tool", "Must be a non-empty string", self.tool
            )
        if self.tool == "unknown":
            raise InvalidInputError(
                "execution_plan.steps.tool", "Unknown tool is not executable", self.tool
            )
        if allowed_tools is not None and self.tool not in set(allowed_tools):
            raise InvalidInputError(
                "execution_plan.steps.tool",
                f"Tool {self.tool!r} is not available in the current tool catalog",
                self.tool,
            )
        if not isinstance(self.input, dict):
            raise InvalidInputError(
                "execution_plan.steps.input", "Must be a dictionary", self.input
            )


@dataclass
class ExecutionPlan:
    """Domain entity representing a validated CSV processing execution plan."""

    goal: str
    steps: List[ExecutionStep]
    workflow_id: Optional[str] = None

    def is_empty(self) -> bool:
        return not self.steps

    def ordered_steps(self) -> list[ExecutionStep]:
        return sorted(self.steps, key=lambda step: step.order)

    def validate(self, allowed_tools: Iterable[str] | None = None) -> None:
        allowed = set(allowed_tools) if allowed_tools is not None else KNOWN_CSV_TOOLS
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise InvalidInputError(
                "execution_plan.goal", "Must be a non-empty string", self.goal
            )
        if not self.steps:
            raise InvalidInputError(
                "execution_plan.steps",
                "Plan must contain at least one step",
                self.steps,
            )

        orders = [step.order for step in self.steps]
        expected = list(range(1, len(self.steps) + 1))
        if sorted(orders) != expected:
            raise InvalidInputError(
                "execution_plan.steps.order",
                f"Step orders must be unique and contiguous starting at 1; expected {expected}",
                orders,
            )

        for step in self.steps:
            step.validate(allowed)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "goal": self.goal,
            "steps": [
                {
                    "order": step.order,
                    "tool": step.tool,
                    "input": step.input,
                    "reason": step.reason,
                }
                for step in self.ordered_steps()
            ],
            "workflow_id": self.workflow_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionPlan":
        """Create from dictionary representation."""
        raw_steps = data.get("steps", []) or []
        steps = []

        for index, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                continue

            raw_order = step.get("order", index)
            try:
                normalized_order = int(raw_order)
            except (TypeError, ValueError):
                normalized_order = index

            raw_input = step.get("input", {}) or {}
            steps.append(
                ExecutionStep(
                    order=normalized_order,
                    tool=str(step.get("tool", "unknown") or "unknown"),
                    input=raw_input if isinstance(raw_input, dict) else {},
                    reason=str(step.get("reason", "") or ""),
                )
            )

        return cls(
            goal=str(data.get("goal", "") or ""),
            steps=steps,
            workflow_id=data.get("workflow_id"),
        )
