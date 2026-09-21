"""
Orchestrator error taxonomy.

Error codes follow the pattern ORCH_XXX where XXX is a 3-digit number.
Each error code maps to a user-facing message and the component that raises it.
"""
from enum import Enum


class ErrorMessages(str, Enum):
    ORCH_001 = "I couldn't understand your request. Could you rephrase?"
    ORCH_002 = "No agent is available for this type of request."
    ORCH_003 = "The agent took too long to respond. Please try again."
    ORCH_004 = "The response didn't pass quality checks. Please try again."
    ORCH_005 = "You don't have permission to perform this action."
    ORCH_006 = "Your message was flagged by our safety system."
    ORCH_007 = "You've exceeded the request limit. Please wait."
    ORCH_008 = "Connection lost. Please reconnect."
    ORCH_009 = "An error occurred during the system. Please try again."
    ORCH_010 = "Your session has expired. Starting a new session."
    ORCH_011 = "Your request is not currently supported by our system. Please try rephrasing your request."
    ORCH_012 = "The system has already completed and cannot continue."
    ORCH_013 = "Waiting for user to confirm reopen conversation."
    ORCH_014 = "Quota exceeded. Please upgrade your plan or wait for the quota to reset."
    ORCH_015 = "Invalid request payload. 'type' must be absent/empty, 'AGENT', or 'RENAME', and 'parameters.agent_id' is required (non-empty) when type is 'AGENT'."
    ORCH_016 = "The specified agent could not be found in Agent Hub."

    @classmethod
    def get_message(cls, code: "ErrorMessages") -> str:
        if isinstance(code, ErrorMessages):
            return code.value


class AppError(Exception):
    """Base application error with optional error code."""

    def __init__(self, error_code: ErrorMessages):
        self.error_code = error_code
        self.user_message = ErrorMessages.get_message(error_code)
        super().__init__(self.user_message)


class PermissionDeniedError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_005)


class IntentClassificationError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_001)


class AgentRoutingError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_002)


class SessionNotFoundError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_010)


class InputGuardError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_006)


class OutputGuardError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_004)


class AgentTimeoutError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_003)


class RateLimitError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_007)


class ConversationFlowError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_009)


class UnSupportedError(AppError):
    def __init__(self,):
        super().__init__(error_code=ErrorMessages.ORCH_011)

class QuotaExceededError(AppError):
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_014)


class RequestValidationAppError(AppError):
    """SA-2114: the async request payload failed validation (e.g. type=AGENT without parameters.agent_id)."""
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_015)


class AgentNotFoundError(AppError):
    """SA-2114: a directly-targeted agent_id was not found in Agent Hub (no fallback)."""
    def __init__(self):
        super().__init__(error_code=ErrorMessages.ORCH_016)
