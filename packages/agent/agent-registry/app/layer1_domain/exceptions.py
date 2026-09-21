"""Domain exceptions for Agent Registry Service.

These exceptions represent business rule violations in the domain layer.
"""


class DomainException(Exception):
    """Base exception for all domain errors."""

    pass


class NotFoundException(DomainException):
    """Raised when an entity is not found in the registry."""

    def __init__(self, entity: str, entity_id: str | None = None, entity_name: str | None = None):
        if entity_id:
            message = f"{entity} with ID '{entity_id}' not found"
        elif entity_name:
            message = f"{entity} with name '{entity_name}' not found"
        else:
            message = f"{entity} not found"
        super().__init__(message)
        self.entity = entity
        self.entity_id = entity_id
        self.entity_name = entity_name


class AlreadyExistsException(DomainException):
    """Raised when attempting to register an entity with a duplicate name."""

    def __init__(self, entity: str, entity_name: str):
        super().__init__(f"{entity} with name '{entity_name}' already exists")
        self.entity = entity
        self.entity_name = entity_name


class InvalidDataException(DomainException):
    """Raised when entity data violates business rules."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class EndpointRequiredException(DomainException):
    """Raised when endpoint is required but not provided (for technical agents)."""

    def __init__(self, entity_name: str):
        super().__init__(
            f"Health check endpoint is required for technical agent '{entity_name}'"
        )
        self.entity_name = entity_name


class InvalidAgentKindException(DomainException):
    """Raised when agent kind is invalid."""

    def __init__(self, kind: str):
        super().__init__(
            f"Invalid agent kind '{kind}'. Must be 'business' or 'technical'"
        )
        self.kind = kind


class InvalidAgentStatusException(DomainException):
    """Raised when agent status is invalid."""

    def __init__(self, status: str):
        super().__init__(
            f"Invalid agent status '{status}'. "
            f"Must be 'active' or 'inactive'"
        )
        self.status = status


class InvalidAgentConfigTypeException(DomainException):
    """Raised when agent config type is invalid."""

    def __init__(self, config_type: str):
        super().__init__(
            f"Invalid agent config type '{config_type}'. "
            f"Must be 'default' or 'custom'"
        )
        self.config_type = config_type


class InvalidOperationException(DomainException):
    """Raised when an operation on an entity violates business rules."""

    def __init__(self, message: str):
        super().__init__(message)


class UnauthenticatedException(DomainException):
    """Raised when a request cannot be authenticated (maps to HTTP 401).

    Used by RBAC principal resolution: a token-less call while the Phase-1 fail-open
    flag is off, or a token that carries no user identity.
    """

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message)


class ForbiddenException(DomainException):
    """Raised when an authenticated caller lacks a required permission (HTTP 403)."""

    def __init__(self, message: str = "Permission denied"):
        super().__init__(message)


class ConflictException(DomainException):
    """Raised when an operation conflicts with current state (HTTP 409).

    Used by RBAC: deleting a role still assigned to users, and the anti-lockout rule
    R3 (removing the last super_admin).
    """

    def __init__(self, message: str):
        super().__init__(message)
