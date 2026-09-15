class MappingWorkerError(Exception):
    """Base exception for mapping-data worker."""


class MappingRuleError(MappingWorkerError):
    """Raised when a mapping rule is invalid or cannot be applied."""


class TypeCoercionError(MappingWorkerError):
    """Raised when a value cannot be coerced to the target type."""


class FieldAccessError(MappingWorkerError):
    """Raised when a source field path cannot be resolved."""
