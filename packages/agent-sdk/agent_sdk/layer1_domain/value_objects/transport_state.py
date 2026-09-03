from enum import Enum


class TransportState(Enum):
    IDLE = "IDLE"
    PROCESSING = "PROCESSING"
    RECEIVED = "RECEIVED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
