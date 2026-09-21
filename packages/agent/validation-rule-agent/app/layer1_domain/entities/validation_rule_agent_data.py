from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from app.layer1_domain.enums.response import RuleValidity


@dataclass
class ValidationRuleAgentData:
    valid: RuleValidity
    message: str
    agent_type: str = "validation-rule"
    expression: str | None = None
    validate_status: bool | None = None
    file_id: str | None = None

    @classmethod
    def success(
        cls,
        rules: Iterable[dict[str, object]],
        *,
        agent_type: str = "validation-rule",
        message: str = "Rule is valid",
        validate_status: bool = True,
        file_id: str | None = None,
    ) -> "ValidationRuleAgentData":
        return cls(
            agent_type=agent_type,
            expression=json.dumps(list(rules)),
            valid=RuleValidity.CORRECT,
            message=message,
            validate_status=validate_status,
            file_id=file_id,
        )

    @classmethod
    def invalid(
        cls,
        *,
        agent_type: str = "validation-rule",
        message: str = "Rule is invalid",
        expression: object = None,
        validate_status: bool | None = False,
        file_id: str | None = None,
    ) -> "ValidationRuleAgentData":
        # `expression` is accepted for caller compatibility but intentionally not persisted.
        return cls(
            agent_type=agent_type,
            expression="",
            valid=RuleValidity.INCORRECT,
            message=message,
            validate_status=False,
            file_id="",
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "agent_type": self.agent_type,
            "expression": self.expression,
            "valid": self.valid,
            "message": self.message,
            "validate_status": self.validate_status,
            "file_id": self.file_id,
        }
        return {key: value for key, value in payload.items() if value is not None}
