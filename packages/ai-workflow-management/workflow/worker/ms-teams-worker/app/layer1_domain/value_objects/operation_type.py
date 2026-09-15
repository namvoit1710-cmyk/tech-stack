"""Operation type value object for MS Teams worker."""

from enum import Enum


class OperationType(str, Enum):
    """Supported MS Teams operations via webhook."""

    SEND_MESSAGE = "send_message"
    SEND_ADAPTIVE_CARD = "send_adaptive_card"

    @classmethod
    def from_string(cls, value: str) -> "OperationType":
        """Convert a string to OperationType enum.

        Args:
            value: String representation of the operation type.

        Returns:
            OperationType enum value.

        Raises:
            ValueError: If the value is not a valid operation type.
        """
        if not value:
            raise ValueError("Operation type cannot be empty")

        normalized = value.lower().strip()

        for operation_type in cls:
            if operation_type.value == normalized:
                return operation_type

        valid_types = [t.value for t in cls]
        raise ValueError(
            f"Invalid operation type: '{value}'. Valid types are: {valid_types}"
        )

    @property
    def display_name(self) -> str:
        """Return a human-readable display name for the operation type."""
        return {
            OperationType.SEND_MESSAGE: "Send Message",
            OperationType.SEND_ADAPTIVE_CARD: "Send Adaptive Card",
        }[self]
