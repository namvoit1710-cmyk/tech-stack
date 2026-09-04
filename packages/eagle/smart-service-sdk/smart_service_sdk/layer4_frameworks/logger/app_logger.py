import logging
from typing import Any

from smart_service_sdk.layer2_application.interfaces.logger_interface import ILogger


class AppLogger(ILogger):
    def __init__(
        self,
        name: str = "ai_eagle",
        level: str = "INFO",
        log_format: str = "text",
    ) -> None:
        del log_format
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    def debug(self, message: str, **context: Any) -> None:
        self._logger.debug(self._format(message, context))

    def info(self, message: str, **context: Any) -> None:
        self._logger.info(self._format(message, context))

    def warning(self, message: str, **context: Any) -> None:
        self._logger.warning(self._format(message, context))

    def log(self, message: str) -> None:
        self.info(message)

    def error(self, message: str, **context: Any) -> None:
        self._logger.error(self._format(message, context))

    def exception(self, message: str, **context: Any) -> None:
        self._logger.exception(self._format(message, context))

    def critical(self, message: str, **context: Any) -> None:
        self._logger.critical(self._format(message, context))

    @staticmethod
    def _format(message: str, context: dict[str, Any]) -> str:
        if not context:
            return message
        suffix = " ".join(f"{key}={value!r}" for key, value in context.items())
        return f"{message} {suffix}"
