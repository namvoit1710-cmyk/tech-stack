"""SA-1371 S2 — MemoryTimerRepository contract.

save/get/list_due (with the fire_at boundary) / list_by_run / delete on the
memory backend — the one exercised by the timer dispatcher + use-case tests.
"""

from __future__ import annotations

from app.layer1_domain.entities.timer import Timer
from app.layer4_frameworks.persistence.memory.memory_timer_repo import (
    MemoryTimerRepository,
)


def _timer(timer_id: str, *, run_id: str = "r1", fire_at: str = "",
           node_id: str = "n1", kind: str = "delay") -> Timer:
    return Timer(
        timer_id=timer_id,
        run_id=run_id,
        workflow_id="wf1",
        node_id=node_id,
        kind=kind,
        fire_at=fire_at,
        created_at="2026-07-01T00:00:00Z",
    )


class TestSaveGet:
    def test_save_then_get(self):
        repo = MemoryTimerRepository()
        t = _timer("t1", fire_at="2026-07-01T00:05:00Z")
        repo.save(t)
        assert repo.get("t1") == t

    def test_get_missing_returns_none(self):
        repo = MemoryTimerRepository()
        assert repo.get("nope") is None

    def test_save_same_id_overwrites(self):
        repo = MemoryTimerRepository()
        repo.save(_timer("t1", fire_at="2026-07-01T00:05:00Z"))
        repo.save(_timer("t1", fire_at="2026-07-01T09:00:00Z"))
        got = repo.get("t1")
        assert got is not None
        assert got.fire_at == "2026-07-01T09:00:00Z"


class TestListDue:
    def test_boundary_le_now_is_due(self):
        repo = MemoryTimerRepository()
        repo.save(_timer("past", fire_at="2026-06-30T23:59:59Z"))
        repo.save(_timer("exact", fire_at="2026-07-01T00:00:00Z"))
        repo.save(_timer("future", fire_at="2026-07-01T00:00:01Z"))

        due_ids = {t.timer_id for t in repo.list_due("2026-07-01T00:00:00Z")}
        # fire_at <= now: past + exact due; future not yet.
        assert due_ids == {"past", "exact"}

    def test_empty_fire_at_never_due(self):
        repo = MemoryTimerRepository()
        repo.save(_timer("t1", fire_at=""))
        assert repo.list_due("2026-07-01T00:00:00Z") == []

    def test_limit_caps_and_orders_earliest_first(self):
        repo = MemoryTimerRepository()
        repo.save(_timer("c", fire_at="2026-07-01T00:00:03Z"))
        repo.save(_timer("a", fire_at="2026-07-01T00:00:01Z"))
        repo.save(_timer("b", fire_at="2026-07-01T00:00:02Z"))

        due = repo.list_due("2026-07-01T01:00:00Z", limit=2)
        assert [t.timer_id for t in due] == ["a", "b"]


class TestListByRun:
    def test_filters_by_run(self):
        repo = MemoryTimerRepository()
        repo.save(_timer("t1", run_id="rA"))
        repo.save(_timer("t2", run_id="rA"))
        repo.save(_timer("t3", run_id="rB"))

        assert {t.timer_id for t in repo.list_by_run("rA")} == {"t1", "t2"}
        assert {t.timer_id for t in repo.list_by_run("rB")} == {"t3"}
        assert repo.list_by_run("missing") == []


class TestDelete:
    def test_delete_removes_and_returns_true(self):
        repo = MemoryTimerRepository()
        repo.save(_timer("t1"))
        assert repo.delete("t1") is True
        assert repo.get("t1") is None

    def test_delete_missing_returns_false(self):
        repo = MemoryTimerRepository()
        assert repo.delete("ghost") is False
