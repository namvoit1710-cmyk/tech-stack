"""Event publisher decorator that broadcasts domain events via a broker topic.

Wraps an existing ``IEventPublisher`` and, after delegating to the inner
publisher, serializes the event and publishes it to the dedicated realtime
topic (``realtime.events``). Subscribers — ``SocketIORealtimeConsumer`` and
``PushGatewayRealtimeConsumer`` — consume from that topic and fan out to
Socket.IO rooms / Push Gateway respectively.

All realtime delivery flows through the broker so per-run event ordering is
preserved: messages are keyed by ``root_run_id`` (or ``run_id``) so a single
Kafka partition / Event Mesh queue serves the entire run.

```
RealtimeBroadcastingPublisher  (this file)
  ├→ Inner publisher (Kafka / Event Mesh / Console)  → external consumers
  └→ Broker topic "realtime.events" (keyed by run)   → realtime consumers
```
"""
from __future__ import annotations

import dataclasses
import itertools
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

# Monotonic publish sequence — stamped onto every envelope so downstream
# consumer logs can be grep-sorted to verify per-run order is preserved end
# to end.
_PUBLISH_SEQ = itertools.count(1)

from app.layer1_domain.events import EventTopic
from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
from app.layer2_application.interfaces.broker_client_interface import IBrokerClient

logger = logging.getLogger(__name__)

REALTIME_TOPIC = "realtime.events"

# Run ``status`` values that end a run — once the *root* run emits a
# RunStatusChanged with one of these the per-run sequence counter is
# released so it doesn't leak for the process lifetime.
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})

_TOPIC_MAP: dict[str, EventTopic] = {
    "runstatuschanged": EventTopic.RUN_STATUS_CHANGED,
    "nodedispatched": EventTopic.NODE_DISPATCHED,
    "nodecompleted": EventTopic.NODE_COMPLETED,
    "nodefailed": EventTopic.NODE_FAILED,
    "nodecancelled": EventTopic.NODE_CANCELLED,
    "loopiterationcompleted": EventTopic.LOOP_ITERATION_COMPLETED,
    "inputrequested": EventTopic.INPUT_REQUESTED,
    "inputsubmitted": EventTopic.INPUT_SUBMITTED,
    "childrunstarted": EventTopic.CHILD_RUN_STARTED,
    "childruncompleted": EventTopic.CHILD_RUN_COMPLETED,
    "childrunfailed": EventTopic.CHILD_RUN_FAILED,
    "childruncancelled": EventTopic.CHILD_RUN_CANCELLED,
    "contextupdated": EventTopic.CONTEXT_UPDATED,
    "edgetraversed": EventTopic.EDGE_TRAVERSED,
    "artifactadded": EventTopic.ARTIFACT_ADDED,
    "nodeinputedited": EventTopic.NODE_INPUT_EDITED,
    "nodeapprovalflow": EventTopic.NODE_APPROVAL_FLOW,
}


def _normalize_topic(raw_topic: str) -> str:
    if "." in raw_topic:
        return raw_topic
    return _TOPIC_MAP.get(raw_topic.lower(), raw_topic)


def _get_field(event: Any, name: str) -> str | None:
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        return getattr(event, name, None) or None
    if isinstance(event, dict):
        value = event.get(name)
        if value:
            return value
        payload = event.get("payload")
        if isinstance(payload, dict):
            return payload.get(name) or None
    return None


def _extract_run_id(event: Any) -> str | None:
    return _get_field(event, "run_id")


def _extract_parent_run_id(event: Any) -> str | None:
    return _get_field(event, "parent_run_id")


def _extract_root_run_id(event: Any) -> str | None:
    return _get_field(event, "root_run_id")


def _extract_workflow_id(event: Any) -> str | None:
    return _get_field(event, "workflow_id")


def _serialize_event(event: Any, event_type: str) -> dict:
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        payload = dataclasses.asdict(event)
    elif isinstance(event, dict):
        payload = dict(event)
    else:
        payload = {"data": str(event)}
    payload["_event"] = event_type
    payload["_timestamp"] = datetime.now(timezone.utc).isoformat()
    return payload


def _collect_rooms(
    run_id: str | None,
    parent_run_id: str | None,
    root_run_id: str | None,
    workflow_id: str | None,
) -> list[str]:
    rooms: list[str] = []
    if run_id:
        rooms.append(f"run:{run_id}")
    if parent_run_id and parent_run_id != run_id:
        rooms.append(f"run:{parent_run_id}")
    if root_run_id and root_run_id != run_id and root_run_id != parent_run_id:
        rooms.append(f"run:{root_run_id}")
    # if workflow_id:
    #     rooms.append(f"workflow:{workflow_id}")
    return rooms


class RealtimeBroadcastingPublisher(IEventPublisher):
    """Publishes realtime envelopes to the broker so per-run order is preserved.

    The envelope ``{event_type, rooms, payload}`` matches what
    ``SocketIORealtimeConsumer`` and ``PushGatewayRealtimeConsumer`` expect.
    Events are keyed by ``root_run_id or run_id or workflow_id`` so all events
    for one run flow through a single Kafka partition / Event Mesh queue —
    this is what makes the broker path strictly ordered per run.
    """

    def __init__(
        self,
        inner_publisher: IEventPublisher | None,
        broker: IBrokerClient | None,
        realtime_consumer: Any = None,
    ) -> None:
        self._inner = inner_publisher
        self._broker = broker
        self._realtime_consumers: list[Any] = []
        if realtime_consumer is not None:
            self._realtime_consumers.append(realtime_consumer)
        # Per-run-key monotonic sequence. The broker already pins a run to a
        # single partition via ``partition_key``; this contiguous counter is
        # what lets the consumer detect gaps and resequence within that run.
        self._run_seq: dict[str, int] = {}
        self._run_seq_lock = threading.Lock()

    def _next_run_seq(self, run_key: str) -> int:
        """Return the next contiguous sequence number for ``run_key``."""
        with self._run_seq_lock:
            nxt = self._run_seq.get(run_key, 0) + 1
            self._run_seq[run_key] = nxt
            return nxt

    def _release_run_seq(self, run_key: str) -> None:
        """Drop the counter for a finished run so it doesn't leak."""
        with self._run_seq_lock:
            self._run_seq.pop(run_key, None)

    def set_realtime_consumer(self, consumer: Any) -> None:
        """Late-bind the realtime consumer (breaks bootstrap ordering dependency)."""
        self.add_realtime_consumer(consumer)

    def add_realtime_consumer(self, consumer: Any) -> None:
        if consumer is None:
            return
        if consumer not in self._realtime_consumers:
            self._realtime_consumers.append(consumer)

    def publish(self, topic: str, event: Any) -> None:
        if self._inner is not None:
            try:
                self._inner.publish(topic, event)
            except Exception as exc:  # noqa: BLE001
                logger.error("Inner publisher failed for topic %s: %s", topic, exc)

        normalized = _normalize_topic(topic)
        run_id = _extract_run_id(event)
        parent_run_id = _extract_parent_run_id(event)
        root_run_id = _extract_root_run_id(event)
        workflow_id = _extract_workflow_id(event)

        rooms = _collect_rooms(run_id, parent_run_id, root_run_id, workflow_id)
        if not rooms:
            return

        # Partition key: root_run_id preferred so nested runs share a partition
        # with their parent, then run_id, then workflow_id. This is what pins
        # all events for one run to a single broker partition / queue.
        partition_key = root_run_id or run_id or workflow_id

        seq = next(_PUBLISH_SEQ)
        # Contiguous per-run sequence — the consumer resequences on this so the
        # frontend sees events in the exact order they were published, even
        # when priority lanes or queue backpressure would otherwise reorder
        # them. ``_seq`` stays as a process-global counter for log grepping.
        run_seq = self._next_run_seq(partition_key) if partition_key else 0
        envelope = {
            "event_type": normalized,
            "rooms": rooms,
            "payload": _serialize_event(event, normalized),
            "_seq": seq,
            "_run_key": partition_key,
            "_run_seq": run_seq,
            "_published_monotonic": time.monotonic(),
        }

        # Release the counter once the *root* run finishes. A child run
        # terminating must not reset the shared root counter, so this only
        # fires when the terminating run_id is itself the partition key.
        is_run_terminal = (
            normalized == EventTopic.RUN_STATUS_CHANGED
            and str(_get_field(event, "status") or "").lower()
            in _TERMINAL_RUN_STATUSES
        )
        if is_run_terminal and run_id and run_id == partition_key:
            self._release_run_seq(partition_key)

        if self._broker is None:
            # Direct-notify fallback: push to each consumer queue, skipping the broker.
            logger.debug(
                "[REALTIME] PUBLISH seq=%d run_seq=%d event=%s rooms=%s key=%s path=direct",
                seq, run_seq, normalized, rooms, partition_key,
            )
            for consumer in self._realtime_consumers:
                try:
                    consumer.notify(envelope)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Direct-notify failed for %s via %s: %s",
                        normalized,
                        type(consumer).__name__,
                        exc,
                    )
            return

        try:
            self._broker.publish_to_topic(REALTIME_TOPIC, envelope, key=partition_key)
            logger.debug(
                "[REALTIME] PUBLISH seq=%d run_seq=%d event=%s rooms=%s key=%s path=broker topic=%s",
                seq, run_seq, normalized, rooms, partition_key, REALTIME_TOPIC,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[REALTIME] PUBLISH FAILED seq=%d event=%s key=%s: %s",
                seq, normalized, partition_key, exc,
            )
