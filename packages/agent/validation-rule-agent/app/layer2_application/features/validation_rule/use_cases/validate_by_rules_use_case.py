"""Dedicated use-case: validate/execute a caller-supplied rule plan.

Deterministic path — ``rules`` is required and the LLM planner is never invoked.
Reuses the shared engine (ExecuteValidationUseCase). No `Any`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from app.layer1_domain.entities.agent_response import AgentResponse
from app.layer2_application.features.validation_rule.use_cases.execute_validation_use_case import (
    ExecuteValidationInput,
    ExecuteValidationUseCase,
)


@dataclass
class ValidateByRulesInput:
    rules: list[dict[str, object]] | dict[str, object] | None = None
    file_id: str | None = None
    header: dict[str, object] | None = None
    template_id: str = ""

    @classmethod
    def from_params(cls, params: dict[str, object]) -> "ValidateByRulesInput":
        rules = params.get("rules")
        file_id = params.get("file_id")
        header = params.get("header")
        template_id = params.get("template_id")
        return cls(
            rules=cast("list[dict[str, object]] | dict[str, object]", rules) if isinstance(rules, (list, dict)) else None,
            file_id=file_id if isinstance(file_id, str) else None,
            header=cast("dict[str, object]", header) if isinstance(header, dict) else None,
            template_id=template_id if isinstance(template_id, str) else "",
        )


class ValidateByRulesUseCase:
    def __init__(self, execute_validation_use_case: ExecuteValidationUseCase, agent_type: str) -> None:
        self._engine = execute_validation_use_case
        self._agent_type = agent_type

    async def execute(self, request: ValidateByRulesInput) -> AgentResponse:
        if request.rules is None:
            return AgentResponse.error(
                ValueError("parameters.rules is required for the validate-by-rules flow"),
                self._agent_type,
            )
        # rules present -> the shared engine takes the deterministic (no-planner) path.
        return await self._engine.execute(
            ExecuteValidationInput(
                message="",
                file_id=request.file_id,
                rules=request.rules,
                header=request.header,
                template_id=request.template_id,
            )
        )
