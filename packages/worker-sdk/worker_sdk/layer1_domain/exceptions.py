class WorkerError(Exception):
    """Base exception for all worker errors."""
    pass


class RegistrationError(WorkerError):
    """Raised when worker registration with the registry fails."""
    pass


class HeartbeatError(WorkerError):
    """Raised when a heartbeat to the registry fails."""
    pass


class TaskExecutionError(WorkerError):
    """Raised when task execution fails."""
    pass


class DataReadError(WorkerError):
    """Raised when reading input data fails."""
    pass


class DataWriteError(WorkerError):
    """Raised when writing output data fails."""
    pass


class FileRefResolutionError(WorkerError):
    """Raised when resolving a __file_ref via the file service fails."""
    pass
