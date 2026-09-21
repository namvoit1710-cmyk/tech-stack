from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.layer1_domain.entities.odata_query import ODataQuery

# Planner statuses that mean "the request cannot be executed".
UNSUPPORTED_STATUSES = ("not_supported", "error")


@dataclass
class PlanStep:
    """A single scoped step of an execution plan."""

    scope: str
    odata_query: ODataQuery


@dataclass
class ExecutionPlan:
    """The planner's output as a domain entity.

    Replaces the raw ``{"status", "execution_plan", "user_prompt"}`` dict that
    used to flow through the layer-4 processor.
    """

    status: str
    steps: List[PlanStep] = field(default_factory=list)
    user_prompt: str = ""

    @property
    def is_supported(self) -> bool:
        return self.status not in UNSUPPORTED_STATUSES

    @classmethod
    def from_planner_dict(cls, data: Dict[str, Any]) -> "ExecutionPlan":
        """Parse the LLM/planner JSON shape into an ``ExecutionPlan``."""
        raw_steps = data.get("execution_plan") or []
        steps = [
            PlanStep(
                scope=step["scope"],
                odata_query=ODataQuery(**(step.get("odata_query") or {})),
            )
            for step in raw_steps
        ]
        return cls(
            status=data.get("status", ""),
            steps=steps,
            user_prompt=data.get("user_prompt", ""),
        )
