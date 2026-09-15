"""Automatic triggers must route from the **PUBLISHED** row only.

``AIW_WORKFLOWS`` holds **one row per VERSION** (``hana_workflow_repo.py:229``),
and publishing does not delete the old row — it demotes it to ``DRAFT``
(``publish_workflow_use_case.py:94-97``). The routing scan was

    SELECT * FROM AIW_WORKFLOWS WHERE MAIN_FLOW = 1

with no status filter and no per-ID current-row window, so **every version ever
saved stayed routable forever**. Measured on tenant-1 (2026-08-11):

* the scan iterated **150 rows** covering **20** logical workflows — 130 of them
  non-current;
* **8** live event routes, of which **7 were stale rows**; one of those was a
  topic that only ever existed in a DRAFT (``t.jirakkk``);
* two workflows whose PUBLISHED version had ``trigger_type=manual`` were still
  matched on ``t.jira`` by 3 and 4 old DRAFT rows respectively. The only thing
  stopping them from starting a run was the *old row's own* ``event_filter`` —
  an expression the author can no longer see or edit, because the editor only
  shows the current version. Green by luck, not by contract.

Two separate queries decided two things that must agree:

    "should this fire?"   list_main_flows()                 -> EVERY version
    "what runs?"          get_by_latest_publish_version()   -> exactly one row

This module pins the rule that makes them agree: **only a PUBLISHED row of a
main flow produces a trigger route.** Live check before choosing strict-
PUBLISHED over a merely-published-preferred window: 0/20 logical workflows had
more than one PUBLISHED row, so "the row that routes" IS "the row StartRun
executes" — provable, not incidental. And 0 of the 11 never-published main
flows carried an event/schedule trigger, so nothing went dark on the flip.

The scan cost drops out of the same fix: 2568 KB -> 215 KB per scan
(150 rows SELECT * -> 9 rows, 3 columns).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer2_application.repositories.workflow_repository_interface import (
    TriggerRoute,
)
from app.layer4_frameworks.persistence.hana.hana_workflow_repo import (
    HanaWorkflowRepository,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)

pytestmark = pytest.mark.unit


# ── the table, modelled as it really is ──────────────────────────────────

def _trigger_node(node_id: str, trigger_type: str, *, topic: str = "",
                  event_filter: str = "", cron: str = "") -> dict:
    """A TRIGGER node as it is SERIALISED into the NODES NCLOB."""
    return {
        "id": node_id,
        "name": node_id,
        "node_type": "TRIGGER",
        "config": {
            "trigger_type": trigger_type,
            "event_topic": topic,
            "event_filter": event_filter,
            "schedule_cron": cron,
        },
    }


def _row(wf_id: str, version: str, status: str, nodes: list[dict], *,
         revision: int = 1, main_flow: int = 1, name: str = "wf") -> dict:
    """One AIW_WORKFLOWS row. ``ID`` is the LOGICAL id, shared across versions
    (verified live: the 5 rows of 8728b765 all carry ID=8728b765 while
    WORKFLOW_ID is a fresh uuid per save, i.e. junk)."""
    return {
        "id": wf_id,
        "workflow_id": f"junk-uuid-{version}",
        "version": version,
        "status": status,
        "revision": revision,
        "main_flow": main_flow,
        "name": name,
        "updated_at": f"2026-08-{revision:02d}T00:00:00",
        "created_at": "2026-08-01T00:00:00",
        "nodes": json.dumps(nodes),
        # The bulk the old SELECT * dragged back: measured at 9.9% (edges) plus
        # 2.0% (everything else) of 2568 KB. Present so the projection assertion
        # below measures something real.
        "edges": json.dumps([{"id": f"e{i}", "source_node_id": "a",
                              "target_node_id": "b"} for i in range(40)]),
        "input_schema": json.dumps([{"name": f"f{i}"} for i in range(20)]),
        "output_schema": "[]",
        "description": "x" * 200,
        "metadata": "{}",
        "variables": "{}",
        "agents_json": "[]",
        "routing_path": "",
        "purpose": "",
        "schema_version": "1.0.0",
        "test_run_id": None,
        "action": "",
        "instruction": "",
    }


class _FakeWorkflowDb:
    """Answers only the SQL these repo methods issue — and APPLIES its WHERE.

    Deliberately strict on both axes, because a lenient fake is how a
    routing test passes while the production query is wrong:

    * an unrecognised WHERE fragment RAISES rather than being ignored, so a
      query that quietly drops ``STATUS = 'PUBLISHED'`` cannot slip through as
      "no filter, return everything";
    * only the columns named in the SELECT list come back, so a ``SELECT *``
      regression shows up in ``payload_bytes`` as a number.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[str] = []
        self.columns_read: list[list[str] | None] = []
        self.payload_bytes = 0

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _columns(sql: str) -> list[str] | None:
        head = sql[sql.upper().index("SELECT") + 6: sql.upper().index(" FROM")]
        if "*" in head:
            return None
        return [c.strip().strip('"').lower() for c in head.split(",")]

    def _apply_where(self, sql: str, params: tuple) -> list[dict]:
        upper = sql.upper()
        if " WHERE " not in upper:
            return list(self.rows)
        where = sql[upper.index(" WHERE ") + 7:]
        for tail in (" ORDER BY", " LIMIT", " GROUP BY"):
            if tail in where.upper():
                where = where[: where.upper().index(tail)]
        rows = list(self.rows)
        consumed = 0
        for clause in [c.strip() for c in where.split(" AND ")]:
            norm = " ".join(clause.split()).upper()
            if norm in ('MAIN_FLOW = 1', '"MAIN_FLOW" = 1'):
                rows = [r for r in rows if r["main_flow"] == 1]
            elif norm in ("STATUS = 'PUBLISHED'", "\"STATUS\" = 'PUBLISHED'"):
                rows = [r for r in rows if r["status"] == "PUBLISHED"]
            elif norm.replace(" ", "") in ("STATUSIN('PUBLISHED','DRAFT')",
                                           '"STATUS"IN(\'PUBLISHED\',\'DRAFT\')'):
                rows = [r for r in rows if r["status"] in ("PUBLISHED", "DRAFT")]
            elif norm in ('ID = :P0', '"ID" = :P0', "ID = ?", '"ID" = ?'):
                rows = [r for r in rows if r["id"] == params[consumed]]
                consumed += 1
            else:
                raise AssertionError(
                    f"_FakeWorkflowDb does not model this predicate: {clause!r}. "
                    "Teach it the predicate rather than loosening the fake — a "
                    "fake that ignores a WHERE it does not understand is how a "
                    "routing bug ships green."
                )
        return rows

    # -- IDatabaseConnection surface -------------------------------------

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        self.queries.append(sql)
        rows = self._apply_where(sql, params)
        cols = self._columns(sql)
        self.columns_read.append(cols)
        if cols is None:
            out = [dict(r) for r in rows]
        else:
            out = [{c: r.get(c) for c in cols} for r in rows]
        self.payload_bytes += sum(
            len(json.dumps(r, default=str).encode()) for r in out
        )
        return out

    def execute_write(self, sql: str, params: tuple = ()) -> None:  # pragma: no cover
        raise AssertionError("the trigger scan must never write")


def _repo(rows: list[dict]) -> tuple[HanaWorkflowRepository, _FakeWorkflowDb]:
    db = _FakeWorkflowDb(rows)
    return HanaWorkflowRepository(db), db


# ── the shapes measured on tenant-1 ──────────────────────────────────────

def _wf_8728b765() -> list[dict]:
    """'listen jira v2 (active)' — the reported workflow, exactly as on disk.

    4 DRAFT rows still carrying an ``event`` trigger (3 on t.jira, 1 on the
    typo t.jirakkk) behind a PUBLISHED row whose trigger is ``manual``.
    """
    n = "trigger-listenjira-uniq"
    return [
        _row("8728b765", "fd1a65da", "DRAFT",
             [_trigger_node(n, "event", topic="t.jira")], revision=6),
        _row("8728b765", "5b939393", "DRAFT",
             [_trigger_node(n, "event", topic="t.jira")], revision=16),
        _row("8728b765", "66fb8582", "DRAFT",
             [_trigger_node(n, "event", topic="t.jirakkk")], revision=19),
        _row("8728b765", "a3ccfe6b", "DRAFT",
             [_trigger_node(n, "event", topic="t.jira")], revision=22),
        _row("8728b765", "70f2e539", "PUBLISHED",
             [_trigger_node(n, "manual", topic="t.jirav")], revision=23),
    ]


def _wf_61a19c96() -> list[dict]:
    """'listen jira v3 (test -> subflow)' — same shape, 3 stale event rows."""
    n = "trigger-v3-jira"
    return [
        _row("61a19c96", "f44032f8", "DRAFT",
             [_trigger_node(n, "event", topic="t.jira")], revision=6),
        _row("61a19c96", "11f71727", "DRAFT",
             [_trigger_node(n, "manual")], revision=9),
        _row("61a19c96", "a61db2ec", "DRAFT",
             [_trigger_node(n, "event", topic="t.jira")], revision=19),
        _row("61a19c96", "3f2118a7", "DRAFT",
             [_trigger_node(n, "event", topic="t.jira")], revision=22),
        _row("61a19c96", "8d2eee66", "PUBLISHED",
             [_trigger_node(n, "manual")], revision=23),
    ]


def _wf_354ffa98() -> list[dict]:
    """'listen jira v5' — the ONE workflow that legitimately fires on t.jira."""
    return [
        _row("354ffa98", "6c6031ba", "PUBLISHED",
             [_trigger_node("trigger-v5-jira", "event", topic="t.jira",
                            event_filter="{{$event.webhookEvent}} == 'x'")],
             revision=5),
    ]


# ══ 1. the rule ══════════════════════════════════════════════════════════

class TestOnlyPublishedRowsRoute:

    def test_a_published_event_trigger_routes(self):
        repo, _ = _repo(_wf_354ffa98())
        routes = repo.list_trigger_routes(("event",))
        assert [(r.workflow_id, r.version, r.event_topic) for r in routes] == [
            ("354ffa98", "6c6031ba", "t.jira")
        ]
        assert routes[0].event_filter == "{{$event.webhookEvent}} == 'x'"
        assert routes[0].node_id == "trigger-v5-jira"

    def test_a_draft_only_workflow_never_routes(self):
        """The strict half of the rule: publishing is what arms a trigger.

        A never-published draft firing on a live Jira event is a side effect on
        real data that the author never asked for. Verified safe on tenant-1:
        11/20 main flows have no published row and NONE of them carries an
        event or schedule trigger.
        """
        rows = [_row("draft-only", "v1", "DRAFT",
                     [_trigger_node("t", "event", topic="t.jira")])]
        repo, _ = _repo(rows)
        assert repo.list_trigger_routes(("event",)) == []

    def test_the_reported_bug_v2_five_versions(self):
        """8728b765: 4 stale event rows behind a PUBLISHED manual trigger."""
        repo, _ = _repo(_wf_8728b765())
        assert repo.list_trigger_routes(("event",)) == []

    def test_the_reported_bug_v3_five_versions(self):
        repo, _ = _repo(_wf_61a19c96())
        assert repo.list_trigger_routes(("event",)) == []

    def test_a_topic_that_only_ever_existed_in_a_draft_is_not_a_route(self):
        """``t.jirakkk`` — a typo saved once, never published, routable for
        nine days. Nothing in the product could remove it."""
        repo, _ = _repo(_wf_8728b765())
        topics = {r.event_topic for r in repo.list_trigger_routes(("event",))}
        assert "t.jirakkk" not in topics

    def test_the_whole_tenant_1_picture(self):
        """8 live routes -> 1, and it is the right one."""
        rows = _wf_8728b765() + _wf_61a19c96() + _wf_354ffa98()
        repo, _ = _repo(rows)
        routes = repo.list_trigger_routes(("event",))
        assert len(routes) == 1
        assert routes[0].workflow_id == "354ffa98"

    def test_main_flow_zero_never_routes(self):
        rows = [_row("sub", "v1", "PUBLISHED",
                     [_trigger_node("t", "event", topic="t.jira")], main_flow=0)]
        repo, _ = _repo(rows)
        assert repo.list_trigger_routes(("event",)) == []

    def test_non_trigger_nodes_are_ignored(self):
        rows = [_row("w", "v1", "PUBLISHED", [
            {"id": "task-1", "node_type": "TASK",
             "config": {"trigger_type": "event", "event_topic": "t.jira"}},
            _trigger_node("trig", "event", topic="t.jira"),
        ])]
        repo, _ = _repo(rows)
        routes = repo.list_trigger_routes(("event",))
        assert [r.node_id for r in routes] == ["trig"]

    def test_every_trigger_node_on_one_row_yields_its_own_route(self):
        rows = [_row("w", "v1", "PUBLISHED", [
            _trigger_node("a", "event", topic="t.one"),
            _trigger_node("b", "event", topic="t.two"),
        ])]
        repo, _ = _repo(rows)
        assert {(r.node_id, r.event_topic) for r in repo.list_trigger_routes(("event",))} == {
            ("a", "t.one"), ("b", "t.two"),
        }


# ══ 2. schedule shares the fix ═══════════════════════════════════════════

class TestScheduleSharesTheRule:
    """``fire_due_schedules_usecase.py:55`` calls the same scan. It is the
    WORSE half: event routing dedupes on ``EventId.derive(event_id, wf.id)``,
    but a schedule has no idempotency at all — a stale cron row fires a fresh
    run every boundary, forever."""

    def test_a_published_cron_routes(self):
        rows = [_row("w", "v1", "PUBLISHED",
                     [_trigger_node("t", "schedule", cron="*/5 * * * *")])]
        repo, _ = _repo(rows)
        routes = repo.list_trigger_routes(("schedule",))
        assert [(r.workflow_id, r.schedule_cron) for r in routes] == [("w", "*/5 * * * *")]

    def test_a_stale_draft_cron_does_not_route(self):
        rows = [
            _row("w", "old", "DRAFT",
                 [_trigger_node("t", "schedule", cron="* * * * *")], revision=1),
            _row("w", "new", "PUBLISHED", [_trigger_node("t", "manual")], revision=2),
        ]
        repo, _ = _repo(rows)
        assert repo.list_trigger_routes(("schedule",)) == []

    def test_types_are_filtered_not_merged(self):
        rows = [_row("w", "v1", "PUBLISHED", [
            _trigger_node("e", "event", topic="t.jira"),
            _trigger_node("s", "schedule", cron="*/5 * * * *"),
        ])]
        repo, _ = _repo(rows)
        assert {r.node_id for r in repo.list_trigger_routes(("event",))} == {"e"}
        assert {r.node_id for r in repo.list_trigger_routes(("schedule",))} == {"s"}
        assert {r.node_id for r in repo.list_trigger_routes(("event", "schedule"))} == {"e", "s"}


# ══ 3. it holds as the data grows ════════════════════════════════════════

class TestItHoldsAtSize:
    """The defect grows with VERSION COUNT, not workflow count — 150 rows for
    20 workflows on tenant-1 today, and nothing ever prunes them."""

    @pytest.mark.parametrize("n_versions", [1, 10, 400, 2000])
    def test_one_route_regardless_of_how_many_stale_versions_exist(self, n_versions):
        rows = [
            _row("w", f"v{i}", "DRAFT",
                 [_trigger_node("t", "event", topic="t.jira")], revision=i)
            for i in range(n_versions)
        ]
        rows.append(_row("w", "current", "PUBLISHED",
                         [_trigger_node("t", "event", topic="t.jira")],
                         revision=n_versions + 1))
        repo, _ = _repo(rows)

        routes = repo.list_trigger_routes(("event",))
        assert len(routes) == 1, (
            f"{n_versions} stale versions produced {len(routes)} routes — "
            "the scan is still version-blind"
        )
        assert routes[0].version == "current"

    @pytest.mark.parametrize("n_versions", [1, 10, 400, 2000])
    def test_a_removed_trigger_stays_removed_at_any_version_count(self, n_versions):
        rows = [
            _row("w", f"v{i}", "DRAFT",
                 [_trigger_node("t", "event", topic="t.jira")], revision=i)
            for i in range(n_versions)
        ]
        rows.append(_row("w", "current", "PUBLISHED",
                         [_trigger_node("t", "manual")], revision=n_versions + 1))
        repo, _ = _repo(rows)
        assert repo.list_trigger_routes(("event",)) == []

    @pytest.mark.parametrize("n_versions", [10, 400, 2000])
    def test_bytes_read_track_published_rows_not_total_rows(self, n_versions):
        """The ROW half of the cost. Measured on tenant-1: SELECT * over 150
        rows was 2568 KB per scan; 9 published rows is 243 KB.

        Asserted as a RATIO against the same fixture read whole, so the test
        states the property (cost follows published rows) rather than a
        machine-specific byte count.
        """
        rows = [
            _row("w", f"v{i}", "DRAFT",
                 [_trigger_node("t", "event", topic="t.jira")], revision=i)
            for i in range(n_versions)
        ]
        rows.append(_row("w", "current", "PUBLISHED",
                         [_trigger_node("t", "event", topic="t.jira")],
                         revision=n_versions + 1))

        repo, db = _repo(rows)
        repo.list_trigger_routes(("event",))
        scan_bytes = db.payload_bytes

        whole_repo, whole_db = _repo(rows)
        whole_repo.list_main_flows()
        whole_bytes = whole_db.payload_bytes

        assert scan_bytes < whole_bytes / n_versions * 2, (
            f"scan read {scan_bytes} B of {whole_bytes} B across "
            f"{n_versions + 1} rows — it is not filtering to published rows"
        )

    def test_the_scan_does_not_read_columns_it_does_not_use(self):
        """The COLUMN half — asserted separately, and on the column list
        rather than on a byte total.

        A byte assertion alone does NOT catch this: the row filter already
        shrinks the payload by ~17x, so a ``SELECT *`` regression hides
        comfortably underneath any total-bytes threshold. (It did — this test
        exists because mutation M3, reverting the projection to ``SELECT *``,
        survived the byte test above.)

        ``EDGES`` was 9.9% of the 2568 KB tenant-1 scan and routing never
        looks at an edge; the schemas, metadata and variables are another 2%.
        """
        repo, db = _repo(_wf_354ffa98())
        repo.list_trigger_routes(("event",))

        assert db.columns_read == [["id", "version", "nodes"]], (
            f"the scan asked for {db.columns_read} — a projected read is the "
            "point; SELECT * drags EDGES and both schemas back per row"
        )

    def test_the_projection_is_cheaper_than_select_star_on_the_same_rows(self):
        """Same rows, both queries — isolates the column saving from the row
        saving. Tenant-1: 243 KB -> 215 KB over the 9 published rows."""
        rows = _wf_354ffa98()
        repo, db = _repo(rows)
        repo.list_trigger_routes(("event",))

        star_db = _FakeWorkflowDb(rows)
        star_db.execute_query("SELECT * FROM AIW_WORKFLOWS WHERE MAIN_FLOW = 1 "
                              "AND STATUS = 'PUBLISHED'")

        assert db.payload_bytes < star_db.payload_bytes


# ══ 4. the shape of what comes back ══════════════════════════════════════

class TestTheRouteTypeIsHonest:

    def test_a_route_is_not_a_half_filled_workflow(self):
        """The projection reads 3 columns. Returning a ``Workflow`` built from
        them would hand callers an entity whose ``edges``/``name``/schemas are
        silently empty — the exact failure shape of the DROP CONTEXT
        regressions. A distinct type cannot lie that way."""
        repo, _ = _repo(_wf_354ffa98())
        route = repo.list_trigger_routes(("event",))[0]
        assert isinstance(route, TriggerRoute)
        assert not isinstance(route, Workflow)
        assert not hasattr(route, "edges")
        assert not hasattr(route, "nodes")
        assert not hasattr(route, "input_schema")

    def test_routes_are_immutable(self):
        repo, _ = _repo(_wf_354ffa98())
        route = repo.list_trigger_routes(("event",))[0]
        with pytest.raises(Exception):
            route.event_topic = "t.hijacked"  # type: ignore[misc]

    def test_the_scan_never_writes(self):
        repo, db = _repo(_wf_354ffa98())
        repo.list_trigger_routes(("event",))
        assert all("SELECT" in q.upper() for q in db.queries)


# ══ 5. the invariant the whole fix exists for ════════════════════════════

class TestRoutingRowIsTheRowStartRunExecutes:
    """Live check that licensed strict-PUBLISHED: 0/20 logical workflows had
    more than one PUBLISHED row, so the row that routes and the row
    ``StartRunUseCase`` resolves (``start_run_use_case.py:110`` ->
    ``get_by_latest_publish_version``) are the same row — provable, not
    incidental. Before the fix they were two different queries over two
    different row sets, which is the bug in one sentence."""

    @pytest.mark.parametrize("rows,expected", [
        (_wf_354ffa98(), "6c6031ba"),
        (_wf_8728b765() + _wf_354ffa98(), "6c6031ba"),
        (_wf_61a19c96() + _wf_354ffa98(), "6c6031ba"),
    ])
    def test_every_route_points_at_the_version_that_will_execute(self, rows, expected):
        repo, _ = _repo(rows)
        routes = repo.list_trigger_routes(("event",))
        assert routes, "nothing routed — this assertion would prove nothing"
        for route in routes:
            executed = repo.get_by_latest_publish_version(route.workflow_id)
            assert executed is not None
            assert executed.version == route.version == expected


# ══ 6. memory repo parity ════════════════════════════════════════════════

class TestMemoryRepoParity:
    """``MemoryWorkflowRepository`` keys ``_store`` by entity id, so version
    rows live under DISTINCT keys sharing ``workflow_id``. It must apply the
    same rule or the unit suite drifts from production."""

    @staticmethod
    def _wf(row_id: str, logical: str, status: WorkflowStatus,
            trigger_type: str, topic: str = "", main: bool = True) -> Workflow:
        node = WorkflowNode(
            id="trig", workflow_id=logical, node_type=NodeType.TRIGGER,
            parameters={"trigger_type": trigger_type, "event_topic": topic},
        )
        return Workflow(id=row_id, workflow_id=logical, name=row_id,
                        version=row_id, status=status, nodes=[node],
                        main_flow=main)

    def test_published_routes(self):
        repo = MemoryWorkflowRepository()
        repo.save(self._wf("w-v1", "w", WorkflowStatus.PUBLISHED, "event", "t.jira"))
        routes = repo.list_trigger_routes(("event",))
        assert [(r.workflow_id, r.event_topic) for r in routes] == [("w-v1", "t.jira")]

    def test_draft_does_not_route(self):
        repo = MemoryWorkflowRepository()
        repo.save(self._wf("w-v1", "w", WorkflowStatus.DRAFT, "event", "t.jira"))
        assert repo.list_trigger_routes(("event",)) == []

    def test_stale_draft_behind_a_published_manual_does_not_route(self):
        repo = MemoryWorkflowRepository()
        repo.save(self._wf("w-old", "w", WorkflowStatus.DRAFT, "event", "t.jira"))
        repo.save(self._wf("w-new", "w", WorkflowStatus.PUBLISHED, "manual"))
        assert repo.list_trigger_routes(("event",)) == []

    def test_non_main_flow_does_not_route(self):
        repo = MemoryWorkflowRepository()
        repo.save(self._wf("s-v1", "s", WorkflowStatus.PUBLISHED, "event",
                           "t.jira", main=False))
        assert repo.list_trigger_routes(("event",)) == []
