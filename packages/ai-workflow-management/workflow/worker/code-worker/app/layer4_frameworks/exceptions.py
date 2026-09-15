class CodeWorkerError(Exception):
    """Base exception for the code worker."""


class CodeTimeoutError(CodeWorkerError):
    """Raised when a user script exceeds its execution timeout."""


class CodeRuntimeError(CodeWorkerError):
    """Raised when a user script fails at runtime (syntax/exception/crash)."""


class UnsupportedLanguageError(CodeWorkerError):
    """Raised when an unsupported scripting language is requested."""
