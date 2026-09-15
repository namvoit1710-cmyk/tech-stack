from enum import Enum


class WaitType(str, Enum):
    """Types of wait operations supported by the wait worker."""
    FIXED_DURATION = "fixed_duration"
    UNTIL_DATETIME = "until_datetime"
    MANUAL_APPROVAL = "manual_approval"
