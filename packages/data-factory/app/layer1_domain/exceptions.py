"""Domain exception hierarchy.

Pure-stdlib domain errors shared across the application. Inner layers raise
these; the framework layer maps them to transport-safe responses. Keeping a
single base (`DataFactoryError`) lets adapters catch the whole family and lets
each subclass carry the semantics needed for correct status-code mapping.
"""

from typing import Optional


class DataFactoryError(Exception):
    """Base class for all domain/application errors in the Data Factory."""


class ValidationError(DataFactoryError):
    """Input or business-rule validation failed."""


class NotFoundError(DataFactoryError):
    """A requested resource does not exist."""


class ConflictError(DataFactoryError):
    """The request conflicts with the current state of a resource."""


class AuthorizationError(DataFactoryError):
    """The caller is not permitted to perform the requested action."""


class OperationTimeoutError(DataFactoryError):
    """An operation exceeded its allotted time budget."""


class ExternalServiceError(DataFactoryError):
    """An upstream/external dependency (storage, provider) failed."""

    def __init__(self, message: str, *, service: Optional[str] = None):
        self.service = service
        super().__init__(message)
