import logging
from typing import Any

from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer4_frameworks.config.app_config import settings


class StandardLogger(ILogger):
    def __init__(self) -> None:
        level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        logging.basicConfig(level=level)
        self.logger = logging.getLogger("WorkerSDK")
        self.logger.setLevel(level)

        # Suppress noisy httpx HTTP request logs
        if settings.DISABLE_HTTPX_LOG:
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)

    def info(self, message: str, **kwargs: Any) -> None:
        self.logger.info(f"{message} {kwargs}" if kwargs else message)

    def error(self, message: str, **kwargs: Any) -> None:
        self.logger.error(f"{message} {kwargs}" if kwargs else message)

    def warning(self, message: str, **kwargs: Any) -> None:
        self.logger.warning(f"{message} {kwargs}" if kwargs else message)

    def debug(self, message: str, **kwargs: Any) -> None:
        self.logger.debug(f"{message} {kwargs}" if kwargs else message)
