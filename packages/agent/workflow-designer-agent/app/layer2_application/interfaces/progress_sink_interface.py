from __future__ import annotations

from typing import Protocol


class IProgressSink(Protocol):
    """Port the design flow uses to stream progress events to a listener."""

    async def emit(self, event: str, data: dict) -> None: ...
