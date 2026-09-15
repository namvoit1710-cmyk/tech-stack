"""Operation type enum for SQL operations."""

from enum import Enum


class OperationType(str, Enum):
    """SQL operation types."""
    
    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    DDL = "ddl"  # CREATE, ALTER, DROP - blocked by default
    
    @classmethod
    def from_string(cls, value: str) -> "OperationType":
        """Convert string to OperationType, case-insensitive."""
        normalized = value.lower().strip()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(
            f"Invalid operation type: '{value}'. "
            f"Must be one of: {', '.join(e.value for e in cls)}"
        )
    
    @property
    def is_read_only(self) -> bool:
        """Check if operation is read-only."""
        return self == OperationType.SELECT
    
    @property
    def is_destructive(self) -> bool:
        """Check if operation can modify/delete data."""
        return self in (OperationType.UPDATE, OperationType.DELETE, OperationType.DDL)
