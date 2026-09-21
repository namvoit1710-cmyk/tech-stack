"""
Response formatting — formats agent responses, errors, and disambiguation prompts.

Moved from L2 features to L1 services (pure domain logic, not a use case).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer1_domain.enums.response_type import ResponseType
from app.layer1_domain.entities.exceptions import AppError, ErrorMessages


@dataclass
class FormattedResponse:
    """Standardized response to be delivered to the user."""
    response_type: ResponseType
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    agent_data: dict[str, Any] = field(default_factory=dict)
    conversation_id: str = ""
    agent_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_type": self.response_type.value,
            "content": self.content,
            "metadata": self.metadata,
            "agent_data": self.agent_data,
            "conversation_id": self.conversation_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
        }


class ResponseFormatter:
    """Formats various response types for delivery."""

    def __init__(self, log=None):
        self._log = log

    def format_response(
        self,
        response_type: ResponseType,
        message: str,
        data: dict | None = None,
        conversation_id: str = "",
        agent_id: str = "",
    ) -> dict:
        """Generic response formatting helper."""
        return {
            "type": response_type.value,
            "message": message,
            "data": data or {},
            "conversation_id": conversation_id,
            "agent_id": agent_id,
        }

    def format_agent_response(
        self,
        agent_result: dict,
        conversation_id: str = "",
        agent_id: str = "",
    ) -> FormattedResponse:
        """Format a successful agent response."""
        message = agent_result.get("message", "")
        agent_data = agent_result.get("agent_data", "")
        response_type = (
            ResponseType.MARKDOWN if self._looks_like_markdown(message)
            else ResponseType.TEXT
        )
        return FormattedResponse(
            response_type=response_type,
            content=message,
            metadata={
                "status": agent_result.get("status", "success"),
                "data": agent_result.get("data"),
            },
            agent_data=agent_data,
            conversation_id=conversation_id,
            agent_id=agent_id,
        )

    def format_multi_agent_response(
        self,
        agent_results: dict[str, dict],
        aggregated_result: dict | None = None,
        conversation_id: str = "",
    ) -> FormattedResponse:
        """Format an aggregated response for multiple agent/task results."""
        aggregate = aggregated_result or {}
        lines: list[str] = []
        for task_id, result in agent_results.items():
            label = result.get("agent_name") or result.get("agent_id") or task_id
            message = result.get("message") or result.get("error") or ""
            if message:
                lines.append(f"### {label}\n{message}")

        content = aggregate.get("message", "").strip()
        if lines:
            content = "\n\n".join(lines)
        if not content:
            content = "The requested tasks completed, but no agent returned a user-visible message."

        response_type = (
            ResponseType.MARKDOWN if self._looks_like_markdown(content)
            else ResponseType.TEXT
        )

        return FormattedResponse(
            response_type=response_type,
            content=content,
            metadata={
                "status": aggregate.get("status", "completed"),
                "data": aggregate.get("data"),
                "agent_results": agent_results,
                "result_count": len(agent_results),
            },
            agent_data=aggregate.get("agent_data", {}),
            conversation_id=conversation_id,
        )

    def format_disambiguation(
        self,
        options: list[dict],
        conversation_id: str = "",
    ) -> FormattedResponse:
        """Format a disambiguation prompt with options."""
        lines = ["I found multiple possible interpretations. Which one did you mean?\n"]
        for i, option in enumerate(options, 1):
            domain = option.get("domain", "unknown")
            action = option.get("action", "unknown")
            confidence = option.get("confidence", 0.0)
            lines.append(f"{i}. **{domain}.{action}** (confidence: {confidence:.0%})")

        return FormattedResponse(
            response_type=ResponseType.DISAMBIGUATION,
            content="\n".join(lines),
            metadata={"options": options},
            conversation_id=conversation_id,
        )

    def format_error(
        self,
        error: Exception,
        conversation_id: str = "",
    ) -> FormattedResponse:
        """Format an error response."""
        from app.layer1_domain.entities.exceptions import AppError

        if isinstance(error, AppError):
            content = error.user_message
            metadata = {"error_code": error.error_code}
        else:
            content = "An unexpected error occurred. Please try again."
            metadata = {"error_code": "ORCH_999"}

        return FormattedResponse(
            response_type=ResponseType.ERROR,
            content=content,
            metadata=metadata,
            conversation_id=conversation_id,
        )

    def format_error_from_state(
        self,
        error: AppError,
        conversation_id: str = "",
    ) -> FormattedResponse:
        """Format error from graph state."""

        code = ErrorMessages.ORCH_009  # default fallback

        try:
            if isinstance(error.error_code, ErrorMessages):
                code = error.error_code
            elif isinstance(error.error_code, str):
                code = ErrorMessages[error.error_code]
        except KeyError:
            code = ErrorMessages.ORCH_009

        content = ErrorMessages.get_message(code)

        return FormattedResponse(
            response_type=ResponseType.ERROR,
            content=content,
            metadata={"error_code": code.name},
            conversation_id=conversation_id,
        )

    def format_low_confidence(
        self,
        message: str = "",
        conversation_id: str = "",
    ) -> FormattedResponse:
        """Format a low-confidence fallback response."""
        content = message or (
            "I'm not confident enough in my understanding of your request. "
            "Could you provide more details?"
        )
        return FormattedResponse(
            response_type=ResponseType.TEXT,
            content=content,
            conversation_id=conversation_id,
        )

    def format_streaming_chunk(
        self,
        chunk: str,
        conversation_id: str = "",
        is_final: bool = False,
    ) -> FormattedResponse:
        """Format a streaming response chunk."""
        return FormattedResponse(
            response_type=ResponseType.STREAM_END if is_final else ResponseType.STREAM_CHUNK,
            content=chunk,
            conversation_id=conversation_id,
        )

    @staticmethod
    def _looks_like_markdown(text: str) -> bool:
        """Simple heuristic for markdown detection."""
        md_markers = ["```", "**", "##", "- ", "1. ", "|", "---"]
        return any(marker in text for marker in md_markers)
