from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class CodeExecutionResult:
    """Outcome of running a user script in an isolated runner."""
    success: bool
    result: Any = None
    logs: list[str] = field(default_factory=list)
    error: str | None = None


class ICodeExecutor(Protocol):
    """Interface for running user-provided code in an isolated environment."""

    def run(
        self,
        language: str,
        code: str,
        items: list[Any],
        input_data: dict[str, Any],
        mode: str,
        timeout_seconds: int,
    ) -> CodeExecutionResult:
        """Execute ``code`` against ``items`` / ``input_data`` and return the result.

        Implementations must isolate execution (separate process, resource
        limits, no host filesystem/network access) and never let a failing
        script crash the worker — runtime/timeout failures are reported via
        ``CodeExecutionResult.success = False``.
        """
        ...
