"""Unit tests for the Phase 4b.1 event-log foundation.

Covers:
- ``MemoryEventLogRepository`` — append, list, dedupe, clear, per-run
  sequencing, thread-safe under the single-writer expectation.
- ``deterministic_event_id`` — stability across calls, collision
  behavior for different inputs.
- ``EventLogAppendingPublisher`` — delegates to inner, extracts run_id
  via the documented fallback chain, log append failures do NOT break
  publish (shadow log policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from app.layer1_domain.events import (
    ChildRunStarted,
    DomainEvent,
    RunStatusChanged,
    NodeCompleted,
)
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.messaging.event_log_appending_publisher import (
    EventLogAppendingPublisher,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)


# ──────────────────────────── repo ───────────────────────────────


class TestMemoryEventLogRepository:
    def _envelope(self, run_id: str, event_id: str, topic: str = "test.topic") -> EventLogEnvelope:
        return EventLogEnvelope(
            event_id=event_id,
            run_id=run_id,
            topic=topic,
            event_type="TestEvent",
            timestamp="2026-04-11T00:00:00Z",
            payload={"k": "v"},
        )

    def test_append_and_list_preserves_order(self):
        repo = MemoryEventLogRepository()
        repo.append(self._envelope("run-1", "e1"))
        repo.append(self._envelope("run-1", "e2"))
        repo.append(self._envelope("run-1", "e3"))

        entries = repo.list_by_run("run-1")
        assert [e.event_id for e in entries] == ["e1", "e2", "e3"]

    def test_append_assigns_monotonic_sequence_per_run(self):
        repo = MemoryEventLogRepository()
        repo.append(self._envelope("run-1", "e1"))
        repo.append(self._envelope("run-1", "e2"))
        repo.append(self._envelope("run-2", "e3"))
        repo.append(self._envelope("run-1", "e4"))

        run1 = repo.list_by_run("run-1")
        run2 = repo.list_by_run("run-2")
        assert [e.sequence for e in run1] == [1, 2, 3]
        assert [e.sequence for e in run2] == [1]

    def test_dedupe_by_event_id_within_run(self):
        repo = MemoryEventLogRepository()
        inserted_first = repo.append(self._envelope("run-1", "e1"))
        inserted_again = repo.append(self._envelope("run-1", "e1"))

        assert inserted_first is True
        assert inserted_again is False
        assert len(repo.list_by_run("run-1")) == 1

    def test_dedupe_is_per_run_not_global(self):
        """Same event_id in different runs is two distinct logical events."""
        repo = MemoryEventLogRepository()
        repo.append(self._envelope("run-1", "shared-id"))
        inserted = repo.append(self._envelope("run-2", "shared-id"))

        assert inserted is True
        assert len(repo.list_by_run("run-1")) == 1
        assert len(repo.list_by_run("run-2")) == 1

    def test_clear_drops_run_and_resets_sequence(self):
        repo = MemoryEventLogRepository()
        repo.append(self._envelope("run-1", "e1"))
        repo.append(self._envelope("run-1", "e2"))

        removed = repo.clear("run-1")
        assert removed == 2
        assert repo.list_by_run("run-1") == []

        # After clear, sequence starts over at 1 — the run is a fresh slate.
        repo.append(self._envelope("run-1", "new-id"))
        assert repo.list_by_run("run-1")[0].sequence == 1

    def test_empty_run_id_uses_unscoped_bucket(self):
        repo = MemoryEventLogRepository()
        repo.append(self._envelope("", "e1"))
        repo.append(self._envelope("", "e2"))
        assert len(repo.list_by_run("")) == 2

    def test_list_unknown_run_returns_empty(self):
        repo = MemoryEventLogRepository()
        assert repo.list_by_run("missing") == []

    def test_count_and_page(self):
        repo = MemoryEventLogRepository()
        for i in range(1, 8):
            repo.append(self._envelope("run-1", f"e{i}"))

        assert repo.count_by_run("run-1") == 7
        assert repo.count_by_run("missing") == 0
        assert [e.sequence for e in repo.list_by_run_page("run-1", 0, 3)] == [1, 2, 3]
        assert [e.sequence for e in repo.list_by_run_page("run-1", 3, 3)] == [4, 5, 6]
        assert [e.sequence for e in repo.list_by_run_page("run-1", 6, 3)] == [7]
        assert repo.list_by_run_page("run-1", 99, 3) == []
        assert repo.list_by_run_page("run-1", 0, 0) == []


# ───────────────────── deterministic id helper ───────────────────


class TestDeterministicEventId:
    def test_same_inputs_produce_same_id(self):
        a = EventId.derive("cause-1", "wire-a:node-b")
        b = EventId.derive("cause-1", "wire-a:node-b")
        assert a == b

    def test_different_causation_produces_different_id(self):
        a = EventId.derive("cause-1", "wire-a:node-b")
        b = EventId.derive("cause-2", "wire-a:node-b")
        assert a != b

    def test_different_correlation_produces_different_id(self):
        a = EventId.derive("cause-1", "wire-a:node-b")
        b = EventId.derive("cause-1", "wire-a:node-c")
        assert a != b

    def test_returns_valid_uuid_string(self):
        import uuid
        eid = EventId.derive("cause-1", "correlation-x")
        parsed = uuid.UUID(eid)
        assert parsed.version == 5


# ──────────────────────── publisher decorator ────────────────────


@dataclass
class _DictStyleEvent:
    """An event that happens not to be a DomainEvent subclass — still valid."""
    run_id: str = ""
    data: int = 0
    event_id: str = ""


class TestEventLogAppendingPublisher:
    def test_publish_appends_envelope_then_delegates(self):
        repo = MemoryEventLogRepository()
        inner = MagicMock()
        publisher = EventLogAppendingPublisher(inner=inner, event_log_repo=repo)

        event = RunStatusChanged(run_id="run-1", workflow_id="wf-1", status="started")
        publisher.publish("run.status", event)

        # Both side effects happened.
        entries = repo.list_by_run("run-1")
        assert len(entries) == 1
        assert entries[0].event_type == "RunStatusChanged"
        assert entries[0].topic == "run.status"
        assert entries[0].payload["workflow_id"] == "wf-1"
        inner.publish.assert_called_once_with("run.status", event)

    def test_run_id_fallback_parent_run_id(self):
        """``ChildRunStarted`` has no ``run_id`` field — only ``parent_run_id``
        and ``child_run_id``. The envelope must fall back to parent_run_id."""
        repo = MemoryEventLogRepository()
        publisher = EventLogAppendingPublisher(inner=MagicMock(), event_log_repo=repo)

        event = ChildRunStarted(
            parent_run_id="parent-1",
            parent_node_id="node-x",
            child_run_id="child-1",
            child_workflow_id="wf-child",
        )
        publisher.publish("childrunstarted", event)

        entries = repo.list_by_run("parent-1")
        assert len(entries) == 1
        assert entries[0].payload["child_run_id"] == "child-1"

    def test_run_id_fallback_root_run_id(self):
        """When an event has only ``root_run_id`` set (e.g. a bare DomainEvent),
        the envelope should key on root_run_id as last resort."""
        repo = MemoryEventLogRepository()
        publisher = EventLogAppendingPublisher(inner=MagicMock(), event_log_repo=repo)

        # Build an event that truly has neither run_id nor parent_run_id.
        @dataclass
        class _RootOnlyEvent:
            root_run_id: str = "root-42"
            event_id: str = "id-root"

        publisher.publish("topic", _RootOnlyEvent())

        entries = repo.list_by_run("root-42")
        assert len(entries) == 1

    def test_log_failure_does_not_break_publish(self):
        """Shadow log policy: if append raises, publish must still reach
        the inner chain. Otherwise a log bug becomes an availability bug."""
        failing_repo = MagicMock()
        failing_repo.append.side_effect = RuntimeError("log write failed")
        inner = MagicMock()
        logger = MagicMock()

        publisher = EventLogAppendingPublisher(
            inner=inner, event_log_repo=failing_repo, logger=logger,
        )
        publisher.publish("run.status", RunStatusChanged(run_id="r", workflow_id="w", status="started"))

        inner.publish.assert_called_once()
        logger.error.assert_called_once()

    def test_dict_payload_is_serialized(self):
        """Non-dataclass events fall through to a best-effort payload."""
        repo = MemoryEventLogRepository()
        publisher = EventLogAppendingPublisher(inner=MagicMock(), event_log_repo=repo)

        publisher.publish("topic", {"run_id": "run-1", "foo": "bar"})

        entries = repo.list_by_run("run-1")
        assert entries[0].payload["foo"] == "bar"

    def test_preserves_event_id_for_dedupe(self):
        """Redelivery of the same DomainEvent (same event_id) must dedupe."""
        repo = MemoryEventLogRepository()
        publisher = EventLogAppendingPublisher(inner=MagicMock(), event_log_repo=repo)

        event = NodeCompleted(event_id="stable-id", run_id="run-1", node_id="n1")
        publisher.publish("node.completed", event)
        publisher.publish("node.completed", event)  # replay

        assert len(repo.list_by_run("run-1")) == 1
