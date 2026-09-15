"""``queue_enqueue`` outbox handler — Phase 3 Sub 3.2.

Performs ``IOrchestrationQueue.enqueue`` on behalf of HITL resume and
DATA_EDIT rerun paths. The dispatcher LRU-dedupes ``outbox_id``s
already enqueued on this pod so replay-after-resolve does not
re-enqueue. Cold-start replay relies on the projector's
``pending_outbox`` view — once an entry is resolved, the dispatcher
will never see it again.

Wiring of action types is producer-side: the producing handler
encodes the dataclass into ``payload.action_payload`` and tags
``payload.action_type`` so this handler can rehydrate. Sub 3.3 / 3.4
ship the producer migrations; here we only know how to round-trip
the supported action shapes.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Protocol

from app.layer4_frameworks.outbox.outbox_errors import (
    PermanentError,
    RetryableError,
)


_LRU_CAPACITY = 1024


class IOrchestrationQueueLike(Protocol):
    async def enqueue(self, action: Any) -> None: ...


class QueueEnqueueHandler:
    def __init__(self, queue: IOrchestrationQueueLike) -> None:
        self._queue = queue
        self._enqueued_lru: OrderedDict[str, None] = OrderedDict()

    async def perform(self, entry: Any) -> dict[str, Any]:
        if entry.outbox_id in self._enqueued_lru:
            # Already enqueued on this pod — short-circuit. Move to MRU.
            self._enqueued_lru.move_to_end(entry.outbox_id)
            return {}
        action = self._build_action(entry)
        try:
            await self._queue.enqueue(action)
        except Exception as exc:
            raise RetryableError(f"queue.enqueue failed: {exc}") from exc
        self._enqueued_lru[entry.outbox_id] = None
        if len(self._enqueued_lru) > _LRU_CAPACITY:
            self._enqueued_lru.popitem(last=False)
        return {}

    def _build_action(self, entry: Any) -> Any:
        action_type = str(entry.payload.get("action_type") or "")
        action_payload = entry.payload.get("action_payload") or {}
        if not action_type:
            raise PermanentError("queue_enqueue payload missing action_type")
        # Lazy import — keeps this module independent of the queue
        # action zoo at import time and avoids a circular dependency
        # if the queue interface ever needs to import outbox helpers.
        from app.layer2_application.interfaces import orchestration_queue_interface as q

        cls = getattr(q, action_type, None)
        if cls is None:
            raise PermanentError(
                f"queue_enqueue: unknown action_type {action_type!r}",
            )
        try:
            return cls(**action_payload)
        except TypeError as exc:
            raise PermanentError(
                f"queue_enqueue: action_payload mismatch for {action_type}: {exc}",
            ) from exc
