"""SA-2318 — ``_active_task`` reads one row, not the run's whole task table.

The dispatch-coalescing check asks about ONE node. It used to answer with
``list_by_run`` plus a Python scan, so it read and deserialized every task of
the run to return one of them — once per successor, on every node completion.
Because a run's task count grows as the run progresses, that made a completion
cost O(successors x tasks_in_run) and a whole run quadratic in its node count.

Two things have to hold, and they pull in opposite directions:

* the read must be a point lookup (the performance claim), and
* the row it picks must be the one the scan would have picked (the semantics).

The second is the one worth guarding. A faster query that occasionally returns
a different task would change dispatch coalescing — a node would either
double-dispatch or silently stall — and nothing else in the suite pins that
choice. So the equivalence tests below re-implement the old scan and assert the
new repository method agrees with it, including on the cases that are easy to
get wrong in SQL: no rows, ties, and a NULL timestamp.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository


# --------------------------------------------------------------------------
# The implementation being replaced, kept verbatim as the oracle.
# --------------------------------------------------------------------------

def _legacy_scan(tasks: list[Task], node_id: str) -> Task | None:
    """``_active_task``'s pre-SA-2318 selection, copied line for line.

    Kept here rather than described in prose so the equivalence assertions
    compare against the real thing. If someone later decides the tie-breaking
    should change, this function is what they have to change too — which is the
    point.
    """
    latest = None
    for t in tasks:
        if t.node_id != node_id:
            continue
        if latest is None or (t.created_at or "") > (latest.created_at or ""):
            latest = t
    return latest


def _task(node_id: str, created_at, status=TaskStatus.RUNNING, run_id="r1") -> Task:
    return Task(
        run_id=run_id, node_id=node_id, status=status, created_at=created_at,
    )


# --------------------------------------------------------------------------
# Semantics — the memory repository against the scan it replaces.
# --------------------------------------------------------------------------

class TestLatestByRunNodeMatchesTheScanItReplaced:
    @staticmethod
    def _both(tasks: list[Task], node_id: str, run_id: str = "r1"):
        repo = MemoryTaskRepository()
        for t in tasks:
            repo.save(t)
        return repo.latest_by_run_node(run_id, node_id), _legacy_scan(tasks, node_id)

    def test_no_task_for_the_node(self):
        got, want = self._both([_task("other", "2026-01-01T00:00:00")], "n1")
        assert got is want is None

    def test_no_tasks_at_all(self):
        got, want = self._both([], "n1")
        assert got is want is None

    def test_single_task(self):
        t = _task("n1", "2026-01-01T00:00:00")
        got, want = self._both([t], "n1")
        assert got is want is t

    def test_picks_the_newest_of_several(self):
        old = _task("n1", "2026-01-01T00:00:00")
        new = _task("n1", "2026-01-03T00:00:00")
        mid = _task("n1", "2026-01-02T00:00:00")
        got, want = self._both([old, new, mid], "n1")
        assert got is want is new

    def test_ignores_other_nodes_and_other_runs(self):
        mine = _task("n1", "2026-01-01T00:00:00")
        other_node = _task("n2", "2026-06-01T00:00:00")
        other_run = _task("n1", "2026-06-01T00:00:00", run_id="r2")
        repo = MemoryTaskRepository()
        for t in (mine, other_node, other_run):
            repo.save(t)
        assert repo.latest_by_run_node("r1", "n1") is mine

    def test_a_tie_keeps_the_first_seen(self):
        """Not an arbitrary choice — it is the scan's choice, because the
        comparison is strictly-greater. Pinned so a later rewrite that flips to
        >= is caught rather than shrugged at."""
        first = _task("n1", "2026-01-01T00:00:00")
        second = _task("n1", "2026-01-01T00:00:00")
        got, want = self._both([first, second], "n1")
        assert got is want is first

    def test_a_null_timestamp_sorts_oldest_not_newest(self):
        """The case the HANA query needs ``NULLS LAST`` for. HANA sorts NULL as
        the largest value, so a plain ``ORDER BY CREATED_AT DESC`` would return
        the timestamp-less row ahead of every real one; the scan coalesces None
        to "" and treats it as the oldest. The two must agree."""
        no_ts = _task("n1", None)
        real = _task("n1", "2020-01-01T00:00:00")
        got, want = self._both([no_ts, real], "n1")
        assert got is want is real

    def test_all_timestamps_null_still_returns_a_task(self):
        a, b = _task("n1", None), _task("n1", None)
        got, want = self._both([a, b], "n1")
        assert got is want is a


# --------------------------------------------------------------------------
# The performance claim — no full-table read survives on the dispatch path.
# --------------------------------------------------------------------------

class _CountingTaskRepo(MemoryTaskRepository):
    """A real repository that records which read shape each call used."""

    def __init__(self) -> None:
        super().__init__()
        self.list_by_run_calls = 0
        self.point_lookup_calls = 0

    def list_by_run(self, run_id: str):
        self.list_by_run_calls += 1
        return super().list_by_run(run_id)

    def latest_by_run_node(self, run_id: str, node_id: str):
        self.point_lookup_calls += 1
        return super().latest_by_run_node(run_id, node_id)


def _engine(task_repo):
    return OrchestrationEngine(
        handler_registry=MagicMock(),
        task_factory=TaskFactory(),
        successor_processor=MagicMock(),
        stage_recorder=MagicMock(),
        task_repo=task_repo,
        run_repo=MagicMock(),
        event_publisher=MagicMock(),
    )


class TestActiveTaskDoesNotReadTheWholeRun:
    @pytest.mark.asyncio
    async def test_one_point_lookup_and_no_full_read(self):
        repo = _CountingTaskRepo()
        repo.save(_task("n1", "2026-01-01T00:00:00"))

        await _engine(repo)._active_task("r1", "n1", NodeType.TASK)

        assert repo.point_lookup_calls == 1
        assert repo.list_by_run_calls == 0, (
            "_active_task read the run's entire task table to answer a "
            "single-node question — the SA-2318 regression"
        )

    @pytest.mark.asyncio
    async def test_cost_does_not_grow_with_the_run(self):
        """The quadratic claim, stated as a test: the same question against a
        run holding 2 tasks and a run holding 400 must cost the same number of
        reads. Under the old scan the READ COUNT was constant too — it was the
        rows-per-read that grew — so this asserts the shape, and the assertion
        above asserts that no full read happens at all. Together they pin it."""
        small, large = _CountingTaskRepo(), _CountingTaskRepo()
        small.save(_task("n1", "2026-01-01T00:00:00"))
        large.save(_task("n1", "2026-01-01T00:00:00"))
        for i in range(400):
            large.save(_task(f"filler{i}", f"2026-01-02T00:00:{i % 60:02d}"))

        for repo in (small, large):
            await _engine(repo)._active_task("r1", "n1", NodeType.TASK)

        assert small.point_lookup_calls == large.point_lookup_calls == 1
        assert small.list_by_run_calls == large.list_by_run_calls == 0


# --------------------------------------------------------------------------
# Behaviour the change must not have altered.
# --------------------------------------------------------------------------

class TestCoalescingBehaviourIsUnchanged:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("node_type", [
        NodeType.MERGE, NodeType.PARALLEL, NodeType.CONDITION, NodeType.SWITCH,
    ])
    async def test_exempt_node_types_short_circuit_before_any_read(self, node_type):
        """These four re-invoke on every arrival by design. The early-out must
        stay AHEAD of the repository call, or the change would add a query per
        successor to the very node types that fan in the most."""
        repo = _CountingTaskRepo()
        repo.save(_task("n1", "2026-01-01T00:00:00"))

        assert await _engine(repo)._active_task("r1", "n1", node_type) is None
        assert repo.point_lookup_calls == 0
        assert repo.list_by_run_calls == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        TaskStatus.PENDING, TaskStatus.WAITING, TaskStatus.READY,
        TaskStatus.RUNNING, TaskStatus.RETRYING,
    ])
    async def test_in_flight_task_still_coalesces(self, status):
        repo = _CountingTaskRepo()
        t = _task("n1", "2026-01-01T00:00:00", status=status)
        repo.save(t)
        assert await _engine(repo)._active_task("r1", "n1", NodeType.TASK) is t

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
    ])
    async def test_terminal_task_does_not_coalesce(self, status):
        """SA-846: an edge into a completed node RE-RUNS it. A terminal latest
        task must read as 'nothing in flight'."""
        repo = _CountingTaskRepo()
        repo.save(_task("n1", "2026-01-01T00:00:00", status=status))
        assert await _engine(repo)._active_task("r1", "n1", NodeType.TASK) is None

    @pytest.mark.asyncio
    async def test_a_terminal_newest_hides_an_older_in_flight_task(self):
        """The ordering and the status check compose: the newest task decides,
        even when an older one for the same node is still non-terminal. This is
        the scan's behaviour and the query must not 'helpfully' prefer the
        in-flight row."""
        repo = _CountingTaskRepo()
        repo.save(_task("n1", "2026-01-01T00:00:00", status=TaskStatus.RUNNING))
        repo.save(_task("n1", "2026-01-02T00:00:00", status=TaskStatus.COMPLETED))
        assert await _engine(repo)._active_task("r1", "n1", NodeType.TASK) is None


# --------------------------------------------------------------------------
# The HANA query, checked as text.
# --------------------------------------------------------------------------

class TestHanaQueryShape:
    """No HANA is reachable from the unit suite, so the SQL is asserted rather
    than executed. Thin, but it catches the two mistakes that would make the
    production query wrong in ways the memory repo cannot reveal: dropping the
    NODE_ID predicate (back to a full read) and dropping NULLS LAST (a
    timestamp-less row winning every ordering)."""

    @staticmethod
    def _captured_sql() -> tuple[str, tuple]:
        from app.layer4_frameworks.persistence.hana.hana_task_repo import (
            HanaTaskRepository,
        )
        db = MagicMock()
        db.execute_query.return_value = []
        repo = HanaTaskRepository(db)
        assert repo.latest_by_run_node("r1", "n1") is None
        return db.execute_query.call_args[0][0], db.execute_query.call_args[0][1]

    def test_filters_on_both_run_and_node(self):
        sql, params = self._captured_sql()
        assert "RUN_ID = :p0" in sql and "NODE_ID = :p1" in sql
        assert params == ("r1", "n1")

    def test_returns_one_row_newest_first(self):
        sql, _ = self._captured_sql()
        assert "DESC" in sql
        assert "LIMIT 1" in sql

    def test_null_timestamps_sort_oldest_via_plain_sql(self):
        """HANA sorts NULL as the largest value, so a bare ``ORDER BY
        CREATED_AT DESC`` returns a timestamp-less row ahead of every real one
        — the opposite of ``t.created_at or ""``.

        Asserting COALESCE specifically, not merely "some null handling": the
        alternative, ``NULLS LAST``, is a dialect feature and nothing in this
        suite can execute HANA to confirm it behaves as documented. A wrong
        guess there surfaces as an exception on every dispatch in production.
        COALESCE is SQL-92 and is the literal transcription of the Python."""
        sql, _ = self._captured_sql()
        assert "COALESCE(CREATED_AT, '')" in sql, (
            "the ORDER BY must coalesce NULL to '' so a task with no "
            "CREATED_AT sorts oldest, matching the memory repo and the scan "
            "this query replaced"
        )


class TestEveryWiredBackendImplementsIt:
    """``_active_task`` awaits ``alatest_by_run_node`` on whatever repository
    the container holds. A backend missing it fails at runtime, on the dispatch
    path, in production — not at import."""

    def test_memory_and_hana_expose_sync_and_async_forms(self):
        from app.layer4_frameworks.persistence.hana.hana_task_repo import (
            HanaTaskRepository,
        )
        for repo in (MemoryTaskRepository(), HanaTaskRepository(MagicMock())):
            assert callable(getattr(repo, "latest_by_run_node"))
            assert callable(getattr(repo, "alatest_by_run_node"))

    def test_the_protocol_declares_it(self):
        from app.layer2_application.repositories.task_repository_interface import (
            ITaskRepository,
        )
        assert hasattr(ITaskRepository, "latest_by_run_node")
