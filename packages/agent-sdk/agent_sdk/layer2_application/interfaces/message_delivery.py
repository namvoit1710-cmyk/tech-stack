from __future__ import annotations

from typing import Any, Protocol


class IMessageDelivery(Protocol):
    payload: Any

    async def ack(self) -> None: ...

    async def nack(self, requeue: bool = True) -> None: ...

    async def reject(self) -> None: ...
