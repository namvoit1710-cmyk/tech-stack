import logging
from typing import Any


class AppLogger:
    def __init__(
        self,
        name: str = "governance_smart_api",
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

    def info(self, message: str, **context: Any) -> None:
        if context:
            suffix = " ".join(f"{key}={value!r}" for key, value in context.items())
            message = f"{message} {suffix}"
        self._logger.info(message)
