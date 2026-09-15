class WaitWorkerError(Exception):
    """Base exception for wait worker."""


class WaitTimeoutError(WaitWorkerError):
    """Raised when a wait operation exceeds its timeout."""


class WaitCancelledError(WaitWorkerError):
    """Raised when a wait operation is cancelled."""
