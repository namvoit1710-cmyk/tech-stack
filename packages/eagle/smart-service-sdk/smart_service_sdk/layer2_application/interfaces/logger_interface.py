from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ILogger(Protocol):
    def debug(self, message: str, **context: Any) -> None: ...
    def info(self, message: str, **context: Any) -> None: ...
    def warning(self, message: str, **context: Any) -> None: ...
    def log(self, message: str) -> None: ...
    def error(self, message: str, **context: Any) -> None: ...
    def exception(self, message: str, **context: Any) -> None: ...
    def critical(self, message: str, **context: Any) -> None: ...


class NullLogger:
    def debug(self, message: str, **context: Any) -> None:
        return None

    def info(self, message: str, **context: Any) -> None:
        return None

    def warning(self, message: str, **context: Any) -> None:
        return None

    def log(self, message: str) -> None:
        return None

    def error(self, message: str, **context: Any) -> None:
        return None

    def exception(self, message: str, **context: Any) -> None:
        return None

    def critical(self, message: str, **context: Any) -> None:
        return None
