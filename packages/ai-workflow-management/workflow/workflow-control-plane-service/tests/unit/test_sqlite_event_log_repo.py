"""Conformance tests for ``SqliteEventLogRepository`` — Phase 4b.6.

Mirrors the behavior assertions in
``test_event_log.py::TestMemoryEventLogRepository`` against the SQLite
backend, plus two SQLite-specific tests:

- **Durability** — write via one connection, close it, reopen the DB
  file, and verify the events survive.
- **Payload JSON round-trip** — nested dicts and lists in the payload
  must serialise and deserialise byte-for-byte.

The conformance mirror is deliberate: both backends implement the same
``IEventLogRepository`` Protocol, and any drift in semantics between
them is a migration hazard. Future persistent backends (HANA) will
extend this suite with a third set of identical assertions.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.persistence.sqlite.sqlite_event_log_repo import (
    SqliteEventLogRepository,
)


def _envelope(run_id: str, event_id: str, topic: str = "test.topic", **extra) -> EventLogEnvelope:
    return EventLogEnvelope(
        event_id=event_id,
        run_id=run_id,
        topic=topic,
        event_type=extra.get("event_type", "TestEvent"),
        timestamp=extra.get("timestamp", "2026-04-11T00:00:00Z"),
        payload=extra.get("payload", {"k": "v"}),
        source=extra.get("source", ""),
        causation_id=extra.get("causation_id", ""),
    )


@pytest.fixture
def repo():
    r = SqliteEventLogRepository(":memory:")
    try:
        yield r
    finally:
        r.close()


# ───────────────────── conformance mirror ───────────────────────


class TestSqliteAppendAndList:
    def test_append_and_list_preserves_order(self, repo):
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))
        repo.append(_envelope("run-1", "e3"))

        entries = repo.list_by_run("run-1")
        assert [e.event_id for e in entries] == ["e1", "e2", "e3"]

    def test_append_assigns_monotonic_sequence_per_run(self, repo):
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))
        repo.append(_envelope("run-2", "e3"))
        repo.append(_envelope("run-1", "e4"))

        run1 = repo.list_by_run("run-1")
        run2 = repo.list_by_run("run-2")
        assert [e.sequence for e in run1] == [1, 2, 3]
        assert [e.sequence for e in run2] == [1]

    def test_count_and_page(self, repo):
        for i in range(1, 8):
            repo.append(_envelope("run-1", f"e{i}"))

        assert repo.count_by_run("run-1") == 7
        assert repo.count_by_run("missing") == 0
        assert [e.sequence for e in repo.list_by_run_page("run-1", 0, 3)] == [1, 2, 3]
        assert [e.sequence for e in repo.list_by_run_page("run-1", 3, 3)] == [4, 5, 6]
        assert [e.sequence for e in repo.list_by_run_page("run-1", 6, 3)] == [7]
        assert repo.list_by_run_page("run-1", 99, 3) == []
        assert repo.list_by_run_page("run-1", 0, 0) == []


class TestSqliteDedupe:
    def test_dedupe_by_event_id_within_run(self, repo):
        first = repo.append(_envelope("run-1", "e1"))
        second = repo.append(_envelope("run-1", "e1"))

        assert first is True
        assert second is False
        assert len(repo.list_by_run("run-1")) == 1

    def test_dedupe_is_per_run_not_global(self, repo):
        """Same event_id in different runs is two distinct logical events."""
        assert repo.append(_envelope("run-1", "shared-id")) is True
        assert repo.append(_envelope("run-2", "shared-id")) is True
        assert len(repo.list_by_run("run-1")) == 1
        assert len(repo.list_by_run("run-2")) == 1


class TestSqliteClear:
    def test_clear_drops_run_and_resets_sequence(self, repo):
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))

        removed = repo.clear("run-1")
        assert removed == 2
        assert repo.list_by_run("run-1") == []

        # After clear, next append starts at 1 — mirrors the memory repo's
        # observable behavior even though SQLite's MAX(sequence)+1 scheme
        # achieves it differently.
        repo.append(_envelope("run-1", "new-id"))
        assert repo.list_by_run("run-1")[0].sequence == 1

    def test_clear_returns_zero_for_unknown_run(self, repo):
        assert repo.clear("never-existed") == 0


class TestSqliteEdgeCases:
    def test_empty_run_id_uses_unscoped_bucket(self, repo):
        repo.append(_envelope("", "e1"))
        repo.append(_envelope("", "e2"))
        entries = repo.list_by_run("")
        assert len(entries) == 2
        # list_by_run reconstructs the empty run_id in returned envelopes
        assert all(e.run_id == "" for e in entries)

    def test_list_unknown_run_returns_empty(self, repo):
        assert repo.list_by_run("missing") == []

    def test_payload_with_nested_dict_and_list(self, repo):
        """JSON round-trip must preserve nested structures exactly.
        The event log captures ``dataclasses.asdict`` output which
        routinely contains nested state-update dicts and artifact lists.
        """
        payload = {
            "node_id": "compute-1",
            "state_updates": {
                "compute-1": {
                    "output": {"result": 42},
                    "metadata": {"type": "compute"},
                },
                "double_seven": {"output": {"result": 42}},
            },
            "artifacts": [{"file_id": "f1", "row_count": 100}],
            "completed": True,
        }
        repo.append(_envelope("run-1", "e1", payload=payload))

        entries = repo.list_by_run("run-1")
        assert len(entries) == 1
        assert entries[0].payload == payload


# ──────────────────── SQLite-specific: durability ────────────────


class TestSqliteDurability:
    def test_events_survive_connection_close_and_reopen(self):
        """Events written to a file-backed DB must be readable after
        a full close + reopen cycle. This is what justifies the SQLite
        backend over the memory backend: durability."""
        # Use a real file, not :memory:, so data can persist across
        # connections.
        fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            # First session — write events.
            writer = SqliteEventLogRepository(db_path)
            writer.append(_envelope("run-1", "e1"))
            writer.append(_envelope("run-1", "e2"))
            writer.append(_envelope("run-2", "e3"))
            writer.close()

            # Second session — reopen the same file and read.
            reader = SqliteEventLogRepository(db_path)
            try:
                run1_entries = reader.list_by_run("run-1")
                run2_entries = reader.list_by_run("run-2")
                assert [e.event_id for e in run1_entries] == ["e1", "e2"]
                assert [e.event_id for e in run2_entries] == ["e3"]
                # Sequences survive the round-trip
                assert [e.sequence for e in run1_entries] == [1, 2]
                assert [e.sequence for e in run2_entries] == [1]
            finally:
                reader.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_dedupe_survives_connection_reopen(self):
        """Replaying the same event across a restart must still dedupe
        via the unique index. This is the property that guarantees
        idempotency under worker restart / Kafka redelivery."""
        fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            writer = SqliteEventLogRepository(db_path)
            assert writer.append(_envelope("run-1", "stable-id")) is True
            writer.close()

            # Reopen and try to re-append the same event.
            writer2 = SqliteEventLogRepository(db_path)
            try:
                assert writer2.append(_envelope("run-1", "stable-id")) is False
                assert len(writer2.list_by_run("run-1")) == 1
            finally:
                writer2.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
