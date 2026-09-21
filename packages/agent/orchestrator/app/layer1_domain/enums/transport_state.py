from enum import Enum


class TransportState(str, Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    WAITING = "waiting"
    RECEIVED = "received"
    RESPONDING = "responding"
    COMPLETED = "completed"
    ERROR = "error"
