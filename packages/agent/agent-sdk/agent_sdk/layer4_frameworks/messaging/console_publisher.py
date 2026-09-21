from __future__ import annotations

from typing import Optional


class ConsoleMessagePublisher:
    async def publish(
        self, topic: str, message: dict, key: Optional[str] = None
    ) -> None:
        print(f"[CONSUMER] topic={topic} key={key} message={message}")

    async def close(self) -> None:
        pass
