import logging
from app.layer2_application.interfaces.logger_interface import ILogger
from typing import Any


def _safe_extra(kwargs: dict) -> dict | None:
    """Prefix extra keys with 'ctx_' to avoid collisions with LogRecord reserved attributes."""
    if not kwargs:
        return None
    return {f"ctx_{k}": v for k, v in kwargs.items()}


class StandardLogger(ILogger):
    def __init__(self) -> None:
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger("WorkflowControlPlane")

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.info(message, *args, extra=_safe_extra(kwargs))

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.error(message, *args, extra=_safe_extra(kwargs))

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.warning(message, *args, extra=_safe_extra(kwargs))

    def log(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Alias for info() — matches blueprint ILogger.log() contract."""
        self.info(message, *args, **kwargs)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.debug(message, *args, extra=_safe_extra(kwargs))
