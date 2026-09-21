import logging
from typing import Any

from agent_sdk.layer2_application.interfaces.observability import ILogger

_LOGGING_KWARGS = {"exc_info", "stack_info", "stacklevel", "extra"}


class StandardLogger(ILogger):
    def __init__(self) -> None:
        self.logger = logging.getLogger("AgentSDK")
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())
            self.logger.setLevel(logging.INFO)

    def _log(self, level: str, message: str, *args: Any, **kwargs: Any) -> None:
        logging_kwargs = {
            key: value for key, value in kwargs.items() if key in _LOGGING_KWARGS
        }
        metadata = {
            key: value for key, value in kwargs.items() if key not in _LOGGING_KWARGS
        }

        rendered_message = message
        if metadata:
            rendered_message = f"{rendered_message} {metadata}"

        log_method = getattr(self.logger, level)
        log_method(rendered_message, *args, **logging_kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("info", message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("error", message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("warning", message, *args, **kwargs)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("debug", message, *args, **kwargs)
