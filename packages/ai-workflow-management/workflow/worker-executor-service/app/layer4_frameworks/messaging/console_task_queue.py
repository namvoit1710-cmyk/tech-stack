from typing import Any
from collections.abc import Callable

from app.layer2_application.interfaces.task_queue_interface import ITaskQueue


class ConsoleTaskQueue(ITaskQueue):
    def consume(self, callback: Callable) -> None:
        print("[TaskQueue] Consumer started (placeholder)")

    def acknowledge(self, message: Any) -> None:
        print(f"[TaskQueue] Acknowledged: {message}")
