from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class GenerationMessage:
    role: str
    content: str


@dataclass(frozen=True)
class GenerationRequest:
    messages: tuple[GenerationMessage, ...]
    model: str | None = None
    temperature: float = 0.0
    max_output_tokens: int | None = None
    response_format: str = "text"
    tools: tuple[object, ...] = field(default_factory=tuple)
    tool_choice: object | None = None


@dataclass(frozen=True)
class GenerationResponse:
    output_text: str
    response_id: str | None = None


@dataclass(frozen=True)
class GenerationStreamEvent:
    event: str
    data: str = ""


class IGenerationClient(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResponse: ...

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationStreamEvent]: ...
