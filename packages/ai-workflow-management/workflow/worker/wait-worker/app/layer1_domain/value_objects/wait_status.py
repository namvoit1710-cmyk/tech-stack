from enum import Enum


class WaitStatus(str, Enum):
    """Result status of a wait operation."""
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    ERROR = "error"
