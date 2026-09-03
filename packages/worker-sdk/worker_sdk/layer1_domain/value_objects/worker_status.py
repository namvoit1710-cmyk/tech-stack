from enum import Enum


class WorkerStatus(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    BUSY = "busy"
    DRAINING = "draining"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"
