"""Unit tests for ``MemorySnapshotRepository`` — Phase 4.1.

Pins the ``ISnapshotRepository`` contract: save/load/retention
semantics and schema-version filtering. The HANA backend (Phase 4.5)
will be exercised against the same contract in an integration test.
"""

from __future__ import annotations

import pytest

from app.layer2_application.interfaces.snapshot_repository_interface import (
    Snapshot,
)
from app.layer4_frameworks.persistence.memory.memory_snapshot_repository import (
    MemorySnapshotRepository,
)


def _snap(
    run_id: str = "r1",
    sequence: int = 1,
    schema_version: int = 1,
    state_json: str = "{}",
    created_at: str = "2026-04-22T00:00:00Z",
) -> Snapshot:
    return Snapshot(
        run_id=run_id,
        sequence=sequence,
        schema_version=schema_version,
        state_json=state_json,
        created_at=created_at,
    )


class TestSave:
    def test_save_persists(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=10))
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10

    def test_save_is_idempotent_on_same_sequence(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=10, state_json="{}"))
        repo.save(_snap(sequence=10, state_json='{"updated":true}'))
        got = repo.load_latest("r1", min_schema_version=1)
        assert got.state_json == '{"updated":true}'

    def test_save_preserves_distinct_sequences(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=5))
        repo.save(_snap(sequence=10))
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10


class TestLoadLatest:
    def test_returns_none_when_empty(self):
        repo = MemorySnapshotRepository()
        assert repo.load_latest("r1", min_schema_version=1) is None

    def test_returns_max_sequence(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=3))
        repo.save(_snap(sequence=10))
        repo.save(_snap(sequence=7))
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10

    def test_filters_below_min_schema_version(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=10, schema_version=1))
        repo.save(_snap(sequence=20, schema_version=1))
        assert repo.load_latest("r1", min_schema_version=2) is None

    def test_returns_highest_sequence_at_or_above_schema_version(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=5, schema_version=1))
        repo.save(_snap(sequence=20, schema_version=2))
        repo.save(_snap(sequence=15, schema_version=3))
        got = repo.load_latest("r1", min_schema_version=2)
        assert got.sequence == 20

    def test_scoped_by_run_id(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(run_id="r1", sequence=10))
        repo.save(_snap(run_id="r2", sequence=20))
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10
        assert repo.load_latest("r2", min_schema_version=1).sequence == 20


class TestDeleteOlderThan:
    def test_drops_strictly_older(self):
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=5))
        repo.save(_snap(sequence=10))
        repo.save(_snap(sequence=15))
        removed = repo.delete_older_than("r1", keep_sequence=10)
        assert removed == 1
        assert repo.load_latest("r1", min_schema_version=1).sequence == 15

    def test_keep_boundary_is_inclusive(self):
        """``keep_sequence`` semantics: keep ``sequence >= keep_sequence``."""
        repo = MemorySnapshotRepository()
        repo.save(_snap(sequence=10))
        assert repo.delete_older_than("r1", keep_sequence=10) == 0
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10

    def test_noop_on_unknown_run(self):
        repo = MemorySnapshotRepository()
        assert repo.delete_older_than("ghost", keep_sequence=0) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
