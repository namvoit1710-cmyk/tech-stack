from enum import Enum


class ContextStatus(str, Enum):
    ACTIVATED = "ACTIVATED"
    DEACTIVATED = "DEACTIVATED"
    REOPEN = "REOPEN"