class HttpRequestWorkerError(Exception):
    """Base exception for http-request worker."""


class HttpRequestTimeoutError(HttpRequestWorkerError):
    """Raised when an HTTP request exceeds its timeout."""


class HttpRequestConnectionError(HttpRequestWorkerError):
    """Raised when an HTTP connection cannot be established."""
