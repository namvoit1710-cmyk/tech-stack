from __future__ import annotations

from typing import Any, Mapping


class AgentSDKError(Exception):
    def __init__(
        self,
        message: str = "",
        *,
        error_code: str | None = None,
        related_step_id: str | None = None,
        is_critical: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.related_step_id = related_step_id
        self.is_critical = is_critical
        self.metadata = dict(metadata) if metadata is not None else None

    def to_error_context(self) -> dict[str, Any]:
        return {
            "exception_type": type(self).__name__,
            "message": self.message,
            "error_code": self.error_code,
            "related_step_id": self.related_step_id,
            "is_critical": self.is_critical,
            "metadata": self.metadata,
        }


class GraphCompilationError(AgentSDKError):
    pass


class NodeExecutionError(AgentSDKError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        related_step_id: str | None = None,
        is_critical: bool = False,
        metadata: Mapping[str, Any] | None = None,
        error_context=None,
    ):
        super().__init__(
            message,
            error_code=error_code,
            related_step_id=related_step_id,
            is_critical=is_critical,
            metadata=metadata,
        )
        self.error_context = error_context


class RegistrationError(AgentSDKError):
    pass


class DependencyError(AgentSDKError):
    pass


class BusinessRuleException(AgentSDKError):
    pass


class WorkflowKnownIssueException(AgentSDKError):
    pass


class DelegationTimeoutException(AgentSDKError):
    pass


class QueueDeliveryException(AgentSDKError):
    pass
