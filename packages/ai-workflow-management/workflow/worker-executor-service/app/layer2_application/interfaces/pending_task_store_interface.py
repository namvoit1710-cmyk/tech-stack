"""Interface for pending task storage — tracks dispatched tasks awaiting callback."""

from dataclasses import dataclass, field
from typing import Protocol, Optional, Any


@dataclass
class PendingTaskEntry:
    task_id: str
    run_id: str
    node_id: str
    worker_type: str
    dispatched_at: str          # ISO 8601
    timeout_at: str             # ISO 8601
    payload: dict[str, Any] = field(default_factory=dict)
    node_config: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    attempt_count: int = 0
    # SA-1951 — set when a callback already released the worker's concurrency
    # slot but the entry had to be put BACK (its result could not be delivered),
    # so the sweeper can still time the task out. Whoever re-inserts the entry
    # owns this flag; readers must not release the slot a second time.
    concurrency_released: bool = False


class IPendingTaskStore(Protocol):
    def store(self, entry: PendingTaskEntry) -> None: ...
    def store_if_absent(self, entry: PendingTaskEntry) -> bool: ...
    def get(self, task_id: str) -> Optional[PendingTaskEntry]: ...
    def remove(self, task_id: str) -> Optional[PendingTaskEntry]: ...
    def get_timed_out(self) -> list[PendingTaskEntry]: ...
    def count(self) -> int: ...
