"""Database type enum for supported databases."""

from enum import Enum


class DatabaseType(str, Enum):
    """Supported database types."""
    
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    HANA = "hana"
    
    @classmethod
    def from_string(cls, value: str) -> "DatabaseType":
        """Convert string to DatabaseType, case-insensitive."""
        normalized = value.lower().strip()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(
            f"Invalid database type: '{value}'. "
            f"Must be one of: {', '.join(e.value for e in cls)}"
        )
