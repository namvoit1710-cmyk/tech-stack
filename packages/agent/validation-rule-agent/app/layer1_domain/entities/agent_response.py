"""Domain entity: the agent's outward response envelope.

Rich entity (AGENTS.md §3). `agent_data` is a serialised wire payload (a polar
result or a ValidationRuleAgentData dict), so it is `dict[str, object]` at that
boundary. Controlled fields use enums.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.layer1_domain.entities.execution_result import ExecutionResult
from app.layer1_domain.entities.validation_rule_agent_data import ValidationRuleAgentData
from app.layer1_domain.enums.response import ResponseStatus, TransportState


@dataclass
class AgentResponse:
    content: str
    status: ResponseStatus
    agent_type: str
    agent_data: dict[str, object]
    transport_state: TransportState = TransportState.RECEIVED

    def to_dict(self) -> dict[str, object]:
        formatted: dict[str, object] = {
            "content": self.content,
            "status": self.status,
            "agent_type": self.agent_type,
            "agent_data": self.agent_data,
            "transport_state": self.transport_state,
        }
        return {"formatted_response": formatted}

    @classmethod
    def polar(cls, polar: dict[str, object], agent_type: str) -> "AgentResponse":
        raw_message = polar.get("error") or polar.get("details") or polar.get("message")
        error_message = raw_message if isinstance(raw_message, str) else ""
        is_error = polar.get("valid") == "incorrect"
        return cls(
            content=error_message or "Transform generated successfully.",
            status=ResponseStatus.ERROR if is_error else ResponseStatus.SUCCESS,
            agent_type=agent_type,
            agent_data=polar,
        )

    @classmethod
    def success(cls, message_text: str, result: ExecutionResult, agent_type: str) -> "AgentResponse":
        content = message_text
        if result.file_url:
            content = f"{message_text}\n\nDownload detailed report: {result.file_url}"
        return cls(content, ResponseStatus.SUCCESS, agent_type, cls._success_agent_data(result, agent_type))

    @classmethod
    def error(cls, error: Exception, agent_type: str) -> "AgentResponse":
        message = f"Validation Rule Agent execute fail. {error}"
        return cls(message, ResponseStatus.ERROR, agent_type, cls._error_agent_data(agent_type, message))

    @classmethod
    def not_supported(cls, agent_type: str, message: str | None) -> "AgentResponse":
        text = message or "Validation Rule Agent not supported."
        return cls(text, ResponseStatus.ERROR, agent_type, cls._error_agent_data(agent_type, text))

    # ---------------- helpers ---------------- #

    @staticmethod
    def _success_agent_data(result: ExecutionResult, agent_type: str) -> dict[str, object]:
        return ValidationRuleAgentData.success(
            result.rules,
            agent_type=agent_type,
            file_id=result.file_id,
            validate_status=result.validate_status,
        ).to_dict()

    @staticmethod
    def _error_agent_data(agent_type: str, message: str) -> dict[str, object]:
        return ValidationRuleAgentData.invalid(
            agent_type=agent_type,
            message=message,
            validate_status=None,
        ).to_dict()
