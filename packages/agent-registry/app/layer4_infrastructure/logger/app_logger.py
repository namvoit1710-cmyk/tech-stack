import logging
import json
import sys
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer4_infrastructure.settings import Settings


DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class AppLogFormatter(logging.Formatter):
    """Formatter that appends structured context to stdlib log records."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        context = getattr(record, "context", None)

        if context:
            serialized_context = json.dumps(context, default=str, ensure_ascii=True)
            return f"{message} | {serialized_context}"

        return message


def setup_logging(settings: Settings | None = None) -> None:
    """Configure application logging using stdlib logging only."""
    level_name = settings.log_level.upper() if settings else "INFO"
    level = getattr(logging, level_name, logging.INFO)

    formatter = AppLogFormatter(DEFAULT_LOG_FORMAT)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(stream_handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        named_logger = logging.getLogger(logger_name)
        named_logger.handlers.clear()
        named_logger.setLevel(level)
        named_logger.propagate = True

    logging.captureWarnings(True)


def get_logger(name: str | None = None) -> "AppLogger":
    """Return an application logger bound to the given logger name."""
    return AppLogger(name=name)


class AppLogger(ILogger):
    """Concrete logger implementation backed by Python's built-in logging module."""

    def __init__(self, name: str | None = None):
        self.logger = logging.getLogger(name or "App")
        self.logger.propagate = True

    @staticmethod
    def _build_context(event: str, context: dict[str, Any]) -> dict[str, Any]:
        normalized_context = {key: value for key, value in context.items() if value is not None}
        normalized_context.setdefault("event", event)
        return normalized_context

    def _emit(
        self,
        level: str,
        event: str,
        *args: Any,
        exc_info: bool = False,
        **context: Any,
    ) -> None:
        message = context.pop("message", event)
        extra = {"context": self._build_context(event, context)}
        getattr(self.logger, level)(message, *args, exc_info=exc_info, extra=extra)

    def debug(self, event: str, *args: Any, exc_info: bool = False, **context: Any) -> None:
        self._emit("debug", event, *args, exc_info=exc_info, **context)

    def info(self, event: str, *args: Any, exc_info: bool = False, **context: Any) -> None:
        self._emit("info", event, *args, exc_info=exc_info, **context)

    def log(self, event: str, *args: Any, exc_info: bool = False, **context: Any) -> None:
        self.info(event, *args, exc_info=exc_info, **context)

    def warning(self, event: str, *args: Any, exc_info: bool = False, **context: Any) -> None:
        self._emit("warning", event, *args, exc_info=exc_info, **context)

    def error(self, event: str, *args: Any, exc_info: bool = False, **context: Any) -> None:
        self._emit("error", event, *args, exc_info=exc_info, **context)