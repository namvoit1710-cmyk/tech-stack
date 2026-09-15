"""Tests for the list_runs feature."""

import pytest
from datetime import datetime, timezone, timedelta

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.run_progress import compute_progress_pct, reachable_path_total
from app.layer2_application.features.list_runs.use_cases.list_runs_use_case import (
    ListRunsUseCase,
    ListRunsCommand,
)


@pytest.fixture
def list_runs_uc(logger, run_repo, workflow_repo):
    return ListRunsUseCase(logger=logger, run_repo=run_repo, workflow_repo=workflow_repo)


# --- SA-2650 progress-chip test doubles ---------------------------------


class _FakeTopo:
    def __init__(self, total: int) -> None:
        self.adjacency = {f"node-{i}": [] for i in range(total)}


class _FakeView:
    def __init__(self, completed: int, skipped: int, total: int) -> None:
        self.completed_nodes = frozenset(f"c{i}" for i in range(completed))
        self.skipped_nodes = frozenset(f"s{i}" for i in range(skipped))
        self.topology = _FakeTopo(total)


class _FakeStore:
    """Returns the same view for any run id (single-run tests)."""

    def __init__(self, view) -> None:
        self._view = view

    def get(self, _run_id: str):
        return self._view


class _Env:
    def __init__(self, event_type, node_id, reason=None, run_id="r"):
        self.event_type = event_type
        self.run_id = run_id
        self.payload = {"node_id": node_id, "reason": reason}


class _FakeEventLog:
    """Returns a fixed envelope list for any run id."""

    def __init__(self, envelopes) -> None:
        self._envs = envelopes

    def list_by_run(self, _run_id: str):
        return self._envs


def _make_run(
    workflow_id="wf-1",
    status=RunStatus.RUNNING,
    tenant_id="system",
    is_test=False,
    started_at=None,
):
    run = WorkflowRun(
        workflow_id=workflow_id,
        status=status,
        tenant_id=tenant_id,
        is_test=is_test,
    )
    if started_at:
        run.started_at = started_at
    return run


class TestListRuns:

    async def test_list_all_no_filters(self, list_runs_uc, run_repo):
        for i in range(3):
            run_repo.save(_make_run())
        result = await list_runs_uc.execute(ListRunsCommand())
        assert result.total == 3
        assert len(result.items) == 3

    async def test_filter_by_workflow_id(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(workflow_id="wf-a"))
        run_repo.save(_make_run(workflow_id="wf-a"))
        run_repo.save(_make_run(workflow_id="wf-b"))

        result = await list_runs_uc.execute(ListRunsCommand(workflow_id="wf-a"))
        assert result.total == 2
        assert all(item["workflow_id"] == "wf-a" for item in result.items)

    async def test_filter_by_single_status(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(status=RunStatus.RUNNING))
        run_repo.save(_make_run(status=RunStatus.COMPLETED))
        run_repo.save(_make_run(status=RunStatus.FAILED))

        result = await list_runs_uc.execute(ListRunsCommand(statuses=["RUNNING"]))
        assert result.total == 1
        assert result.items[0]["status"] == "RUNNING"

    async def test_filter_by_multiple_statuses(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(status=RunStatus.RUNNING))
        run_repo.save(_make_run(status=RunStatus.COMPLETED))
        run_repo.save(_make_run(status=RunStatus.FAILED))

        result = await list_runs_uc.execute(ListRunsCommand(statuses=["RUNNING", "FAILED"]))
        assert result.total == 2
        statuses = {item["status"] for item in result.items}
        assert statuses == {"RUNNING", "FAILED"}

    async def test_filter_by_tenant_id(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(tenant_id="acme"))
        run_repo.save(_make_run(tenant_id="acme"))
        run_repo.save(_make_run(tenant_id="other"))

        result = await list_runs_uc.execute(ListRunsCommand(tenant_id="acme"))
        assert result.total == 2
        assert all(item["tenant_id"] == "acme" for item in result.items)

    async def test_filter_by_is_test(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(is_test=True))
        run_repo.save(_make_run(is_test=True))
        run_repo.save(_make_run(is_test=False))

        result = await list_runs_uc.execute(ListRunsCommand(is_test=False))
        assert result.total == 1
        assert result.items[0]["is_test"] is False

        result = await list_runs_uc.execute(ListRunsCommand(is_test=True))
        assert result.total == 2

    async def test_roots_only_excludes_child_runs(self, list_runs_uc, run_repo):
        root = _make_run()
        run_repo.save(root)
        child = _make_run()
        child.parent_run_id = root.id
        child.depth_level = 1
        run_repo.save(child)

        # Default lists everything (root + child).
        all_runs = await list_runs_uc.execute(ListRunsCommand())
        assert all_runs.total == 2

        # roots_only returns only the top-level run, and exposes the new fields.
        roots = await list_runs_uc.execute(ListRunsCommand(roots_only=True))
        assert roots.total == 1
        assert roots.items[0]["id"] == root.id
        assert roots.items[0]["parent_run_id"] is None
        assert roots.items[0]["depth_level"] == 0

    async def test_pagination_offset_limit(self, list_runs_uc, run_repo):
        now = datetime.now(timezone.utc)
        for i in range(5):
            run_repo.save(_make_run(
                started_at=(now - timedelta(minutes=i)).isoformat(),
            ))

        page1 = await list_runs_uc.execute(ListRunsCommand(offset=0, limit=2))
        assert page1.total == 5
        assert len(page1.items) == 2
        assert page1.offset == 0
        assert page1.limit == 2

        page2 = await list_runs_uc.execute(ListRunsCommand(offset=2, limit=2))
        assert len(page2.items) == 2
        assert page2.total == 5

        page3 = await list_runs_uc.execute(ListRunsCommand(offset=4, limit=2))
        assert len(page3.items) == 1

    async def test_sort_newest_first(self, list_runs_uc, run_repo):
        now = datetime.now(timezone.utc)
        old = _make_run(started_at=(now - timedelta(hours=2)).isoformat())
        mid = _make_run(started_at=(now - timedelta(hours=1)).isoformat())
        new = _make_run(started_at=now.isoformat())
        run_repo.save(old)
        run_repo.save(mid)
        run_repo.save(new)

        result = await list_runs_uc.execute(ListRunsCommand())
        assert result.items[0]["started_at"] >= result.items[1]["started_at"]
        assert result.items[1]["started_at"] >= result.items[2]["started_at"]

    async def test_limit_clamped(self, list_runs_uc, run_repo):
        for _ in range(3):
            run_repo.save(_make_run())

        result = await list_runs_uc.execute(ListRunsCommand(limit=999))
        assert result.limit == 100

        result = await list_runs_uc.execute(ListRunsCommand(limit=-5))
        assert result.limit == 1

    async def test_workflow_name_resolved(self, list_runs_uc, run_repo, workflow_repo):
        wf = Workflow(id="wf-named", name="My Workflow")
        workflow_repo.save(wf)
        run_repo.save(_make_run(workflow_id="wf-named"))

        result = await list_runs_uc.execute(ListRunsCommand())
        assert result.items[0]["workflow_name"] == "My Workflow"

    async def test_workflow_name_missing_workflow(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(workflow_id="wf-deleted"))

        result = await list_runs_uc.execute(ListRunsCommand())
        assert result.items[0]["workflow_name"] == ""

    async def test_combined_filters(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(workflow_id="wf-1", status=RunStatus.RUNNING, tenant_id="acme"))
        run_repo.save(_make_run(workflow_id="wf-1", status=RunStatus.COMPLETED, tenant_id="acme"))
        run_repo.save(_make_run(workflow_id="wf-2", status=RunStatus.RUNNING, tenant_id="acme"))
        run_repo.save(_make_run(workflow_id="wf-1", status=RunStatus.RUNNING, tenant_id="other"))

        result = await list_runs_uc.execute(ListRunsCommand(
            workflow_id="wf-1",
            statuses=["RUNNING"],
            tenant_id="acme",
        ))
        assert result.total == 1
        assert result.items[0]["workflow_id"] == "wf-1"
        assert result.items[0]["status"] == "RUNNING"
        assert result.items[0]["tenant_id"] == "acme"

    async def test_empty_result(self, list_runs_uc):
        result = await list_runs_uc.execute(ListRunsCommand())
        assert result.total == 0
        assert result.items == []

    async def test_contains_workflow_name(self, list_runs_uc, run_repo, workflow_repo):
        wf1 = Workflow(id="wf-invoice", name="Invoice Approval")
        wf2 = Workflow(id="wf-order", name="Order Processing")
        workflow_repo.save(wf1)
        workflow_repo.save(wf2)
        run_repo.save(_make_run(workflow_id="wf-invoice"))
        run_repo.save(_make_run(workflow_id="wf-invoice"))
        run_repo.save(_make_run(workflow_id="wf-order"))

        result = await list_runs_uc.execute(
            ListRunsCommand(contains={"workflow_name": "invoice"})
        )
        assert result.total == 2
        assert all(item["workflow_name"] == "Invoice Approval" for item in result.items)

    async def test_contains_workflow_name_no_match(self, list_runs_uc, run_repo, workflow_repo):
        wf = Workflow(id="wf-1", name="My Workflow")
        workflow_repo.save(wf)
        run_repo.save(_make_run(workflow_id="wf-1"))

        result = await list_runs_uc.execute(
            ListRunsCommand(contains={"workflow_name": "nonexistent"})
        )
        assert result.total == 0
        assert result.items == []

    async def test_contains_workflow_name_case_insensitive(self, list_runs_uc, run_repo, workflow_repo):
        wf = Workflow(id="wf-1", name="APPROVAL Workflow")
        workflow_repo.save(wf)
        run_repo.save(_make_run(workflow_id="wf-1"))

        result = await list_runs_uc.execute(
            ListRunsCommand(contains={"workflow_name": "approval"})
        )
        assert result.total == 1

    async def test_contains_status(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(status=RunStatus.RUNNING))
        run_repo.save(_make_run(status=RunStatus.COMPLETED))
        run_repo.save(_make_run(status=RunStatus.FAILED))

        result = await list_runs_uc.execute(
            ListRunsCommand(contains={"status": "run"})
        )
        assert result.total == 1
        assert result.items[0]["status"] == "RUNNING"

    async def test_contains_combined_with_eq_filters(self, list_runs_uc, run_repo, workflow_repo):
        wf1 = Workflow(id="wf-inv", name="Invoice Flow")
        wf2 = Workflow(id="wf-ord", name="Order Flow")
        workflow_repo.save(wf1)
        workflow_repo.save(wf2)
        run_repo.save(_make_run(workflow_id="wf-inv", status=RunStatus.RUNNING))
        run_repo.save(_make_run(workflow_id="wf-inv", status=RunStatus.COMPLETED))
        run_repo.save(_make_run(workflow_id="wf-ord", status=RunStatus.RUNNING))

        result = await list_runs_uc.execute(
            ListRunsCommand(
                statuses=["RUNNING"],
                contains={"workflow_name": "invoice"},
            )
        )
        assert result.total == 1
        assert result.items[0]["workflow_id"] == "wf-inv"
        assert result.items[0]["status"] == "RUNNING"


class TestRunProgressHelper:
    """The pure math helper (SA-2650)."""

    def test_basic_ratio(self):
        assert compute_progress_pct(completed=3, skipped=0, total=5) == 60

    def test_completed_plus_skipped(self):
        assert compute_progress_pct(completed=2, skipped=1, total=5) == 60

    def test_hundred(self):
        assert compute_progress_pct(completed=5, skipped=0, total=5) == 100

    def test_zero_total_is_none(self):
        assert compute_progress_pct(completed=0, skipped=0, total=0) is None

    def test_clamped_over_100(self):
        assert compute_progress_pct(completed=9, skipped=0, total=5) == 100


class _Edge:
    def __init__(self, id, target):
        self.id = id
        self.target_node_id = target


class _Topo:
    """trigger → cond → [et: t1→t2→done] [ef: d1→d2]."""
    def __init__(self):
        self.entry_nodes = ["trigger"]
        self.adjacency = {"trigger": ["cond"], "cond": ["t1", "d1"], "t1": ["t2"],
                          "t2": ["done"], "done": [], "d1": ["d2"], "d2": []}
        self.edge_map = {
            "trigger": [_Edge("e1", "cond")],
            "cond": [_Edge("et", "t1"), _Edge("ef", "d1")],
            "t1": [_Edge("e12", "t2")],
            "t2": [_Edge("e2d", "done")],
            "d1": [_Edge("ed12", "d2")],
            "done": [], "d2": [],
        }


class TestReachablePathTotal:
    """SA-2650: denominator drops un-taken CONDITION/SWITCH branches."""

    def test_undecided_split_counts_all(self):
        # No routing decision yet → whole graph is still reachable (7).
        assert reachable_path_total(_Topo(), {}) == 7

    def test_decided_split_drops_untaken_branch(self):
        # cond took edge 'et' (→t1) → the false subtree d1,d2 drops out → 5.
        assert reachable_path_total(_Topo(), {"cond": {"et": "true_branch"}}) == 5

    def test_no_edge_map_falls_back_to_all_nodes(self):
        class _Bare:
            adjacency = {"a": [], "b": [], "c": []}
            entry_nodes = ["a"]
            edge_map = {}
        assert reachable_path_total(_Bare(), None) == 3

    def test_none_topology(self):
        assert reachable_path_total(None, None) == 0


class TestListRunsProgress:
    """SA-2650: /runs surfaces live progress for RUNNING runs only."""

    def _uc(self, logger, run_repo, workflow_repo, view):
        return ListRunsUseCase(
            logger=logger, run_repo=run_repo, workflow_repo=workflow_repo,
            run_context_view_store=_FakeStore(view),
        )

    async def test_running_run_reports_progress(self, logger, run_repo, workflow_repo):
        run_repo.save(_make_run(status=RunStatus.RUNNING))
        uc = self._uc(logger, run_repo, workflow_repo, _FakeView(completed=2, skipped=1, total=5))
        result = await uc.execute(ListRunsCommand())
        item = result.items[0]
        assert item["progress_pct"] == 60
        assert item["completed_nodes"] == 3
        assert item["total_nodes"] == 5

    async def test_running_at_100(self, logger, run_repo, workflow_repo):
        run_repo.save(_make_run(status=RunStatus.RUNNING))
        uc = self._uc(logger, run_repo, workflow_repo, _FakeView(completed=4, skipped=0, total=4))
        item = (await uc.execute(ListRunsCommand())).items[0]
        assert item["progress_pct"] == 100
        assert item["total_nodes"] == 4

    async def test_failed_run_reports_how_far_it_got(self, logger, run_repo, workflow_repo):
        # A FAILED run shows the % it reached before stopping (SA-2650).
        run_repo.save(_make_run(status=RunStatus.FAILED))
        uc = self._uc(logger, run_repo, workflow_repo, _FakeView(completed=2, skipped=0, total=5))
        item = (await uc.execute(ListRunsCommand())).items[0]
        assert item["progress_pct"] == 40
        assert item["completed_nodes"] == 2
        assert item["total_nodes"] == 5

    async def test_failed_run_excludes_failed_node_from_pct(self, logger, run_repo, workflow_repo):
        # SA-2650: a failed node must NOT count toward progress, so a run that
        # attempted all nodes but failed the last one reads <100%, never 100%.
        run_repo.save(_make_run(status=RunStatus.FAILED))
        # 4-node graph: 3 succeeded, 1 failed → 3/4 = 75%, not 100%.
        events = [
            _Env("NodeCompleted", "trigger"),
            _Env("NodeCompleted", "h1"),
            _Env("NodeCompleted", "h2"),
            _Env("NodeFailed", "h3"),
        ]
        uc = ListRunsUseCase(
            logger=logger, run_repo=run_repo, workflow_repo=workflow_repo,
            run_context_view_store=_FakeStore(_FakeView(completed=99, skipped=0, total=4)),
            event_log_repo=_FakeEventLog(events),
        )
        item = (await uc.execute(ListRunsCommand())).items[0]
        assert item["progress_pct"] == 75          # 3 success / 4 total (failed h3 excluded)
        assert item["completed_nodes"] == 3
        assert item["progress_pct"] < 100          # a failed run is never 100%

    async def test_completed_run_has_null_progress(self, logger, run_repo, workflow_repo):
        # COMPLETED / CANCELLED / PAUSED carry no chip (only RUNNING + FAILED do).
        run_repo.save(_make_run(status=RunStatus.COMPLETED))
        uc = self._uc(logger, run_repo, workflow_repo, _FakeView(completed=4, skipped=0, total=4))
        item = (await uc.execute(ListRunsCommand())).items[0]
        assert item["progress_pct"] is None
        assert item["completed_nodes"] is None
        assert item["total_nodes"] is None

    async def test_cancelled_run_has_null_progress(self, logger, run_repo, workflow_repo):
        run_repo.save(_make_run(status=RunStatus.CANCELLED))
        uc = self._uc(logger, run_repo, workflow_repo, _FakeView(completed=1, skipped=0, total=4))
        item = (await uc.execute(ListRunsCommand())).items[0]
        assert item["progress_pct"] is None

    async def test_no_view_store_yields_null(self, list_runs_uc, run_repo):
        # Default fixture wires no view store → progress fields present but null.
        run_repo.save(_make_run(status=RunStatus.RUNNING))
        item = (await list_runs_uc.execute(ListRunsCommand())).items[0]
        assert item["progress_pct"] is None


class TestListRunsComparisonOperators:

    async def test_eq_filter_correlation_id(self, list_runs_uc, run_repo):
        r1 = _make_run()
        r1.correlation_id = "corr-A"
        r2 = _make_run()
        r2.correlation_id = "corr-B"
        run_repo.save(r1)
        run_repo.save(r2)

        result = await list_runs_uc.execute(
            ListRunsCommand(eq={"correlation_id": "corr-A"})
        )
        assert result.total == 1
        assert result.items[0]["id"] == r1.id

    async def test_ne_filter_status(self, list_runs_uc, run_repo):
        run_repo.save(_make_run(status=RunStatus.RUNNING))
        run_repo.save(_make_run(status=RunStatus.COMPLETED))
        run_repo.save(_make_run(status=RunStatus.FAILED))

        result = await list_runs_uc.execute(
            ListRunsCommand(ne={"status": "RUNNING"})
        )
        assert result.total == 2
        assert all(item["status"] != "RUNNING" for item in result.items)

    async def test_gt_lt_filter_started_at(self, list_runs_uc, run_repo):
        now = datetime.now(timezone.utc)
        run_repo.save(_make_run(started_at=(now - timedelta(hours=3)).isoformat()))
        mid = _make_run(started_at=(now - timedelta(hours=1)).isoformat())
        run_repo.save(mid)
        run_repo.save(_make_run(started_at=(now + timedelta(hours=3)).isoformat()))

        lower = (now - timedelta(hours=2)).isoformat()
        upper = (now + timedelta(hours=2)).isoformat()
        result = await list_runs_uc.execute(
            ListRunsCommand(
                gt={"started_at": lower},
                lt={"started_at": upper},
            )
        )
        assert result.total == 1
        assert result.items[0]["id"] == mid.id

    async def test_eq_filter_unknown_attr_ignored(self, list_runs_uc, run_repo):
        run_repo.save(_make_run())
        run_repo.save(_make_run())

        result = await list_runs_uc.execute(
            ListRunsCommand(eq={"foo": "bar"})
        )
        assert result.total == 2

    async def test_ge_filter_depth_level(self, list_runs_uc, run_repo):
        r1 = _make_run()
        r1.depth_level = 0
        r2 = _make_run()
        r2.depth_level = 2
        r3 = _make_run()
        r3.depth_level = 5
        run_repo.save(r1)
        run_repo.save(r2)
        run_repo.save(r3)

        result = await list_runs_uc.execute(
            ListRunsCommand(ge={"depth_level": "2"})
        )
        assert result.total == 2
        assert {item["id"] for item in result.items} == {r2.id, r3.id}
