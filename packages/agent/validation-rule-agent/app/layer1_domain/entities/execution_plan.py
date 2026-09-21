"""Domain entity: a validation execution plan.

Rich entity (AGENTS.md §3) — builds from caller rules or planner output and owns
scope grouping. Step payloads are dynamic external JSON (`dict[str, object]`, no
`Any`); narrowing lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby
from typing import Iterator, cast


def _step_scope(step: dict[str, object]) -> str:
    scope = step.get("scope")
    if not isinstance(scope, str) or not scope:
        raise ValueError("Each execution plan step must include 'scope'")
    return scope


@dataclass
class ExecutionPlan:
    steps: list[dict[str, object]] = field(default_factory=list)
    status: str | None = None
    message: str | None = None

    @classmethod
    def from_rules(cls, rules: object) -> "ExecutionPlan":
        """Build from caller-supplied ``parameters.rules`` (list or object)."""
        if isinstance(rules, dict):
            if "execution_plan" in rules:
                return cls(
                    steps=cls._as_steps(rules.get("execution_plan")),
                    status=_as_str_or_none(rules.get("status")),
                    message=_as_str_or_none(rules.get("message")),
                )
            raise ValueError("parameters.rules must include 'execution_plan' when provided as an object")

        if isinstance(rules, list):
            return cls(steps=cls._as_steps(rules))

        raise ValueError("parameters.rules must be a list or an object containing 'execution_plan'")

    @classmethod
    def from_planner(cls, plan: dict[str, object]) -> "ExecutionPlan":
        """Wrap the planner's raw output."""
        return cls(
            steps=cls._as_steps(plan.get("execution_plan")),
            status=_as_str_or_none(plan.get("status")),
            message=_as_str_or_none(plan.get("message")),
        )

    def is_not_supported(self) -> bool:
        return self.status == "not_supported"

    def grouped_by_scope(self) -> Iterator[tuple[str, list[dict[str, object]]]]:
        for scope, group in groupby(self.steps, key=_step_scope):
            yield scope, list(group)

    def single_scope(self) -> str | None:
        """Sole scope if every step shares one, else None. Raises if a step lacks ``scope``."""
        scopes = {_step_scope(step) for step in self.steps}
        if len(scopes) != 1:
            return None
        return next(iter(scopes))

    @staticmethod
    def _as_steps(value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        return [cast("dict[str, object]", step) for step in value if isinstance(step, dict)]


def _as_str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
