"""Worker function definition — a named callable exposed by a worker."""

from dataclasses import dataclass, field
from typing import Any, Optional
from collections.abc import Callable, Coroutine


@dataclass
class WorkerFunctionDefinition:
    """Metadata-only definition (for registration/discovery, no handler)."""
    name: str
    description: str = ""
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkerFunction:
    """Full function definition with an executable handler."""
    name: str
    description: str = ""
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    handler: Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]] = None

    def to_definition(self) -> WorkerFunctionDefinition:
        return WorkerFunctionDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )
