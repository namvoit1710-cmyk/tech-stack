"""Regression test for the "save then F5 shows a stale workflow" bug.

A logical workflow ("ID") has many version rows (one per "WORKFLOW_ID"). The old
HanaWorkflowRepository.get_by_id resolved "current" with
``ORDER BY UPDATED_AT DESC, VERSION DESC LIMIT 1``. That returned a STALE row when:
  - UPDATED_AT tied (same-microsecond saves / coarse clocks), because the
    tiebreak VERSION is a random uuid (not a recency signal); or
  - UPDATED_AT skewed across Cloud Run instances (a later write carrying an
    earlier wall-clock string).

The fix: a monotonic per-ID REVISION assigned on every save, and resolution by
highest REVISION (HanaWorkflowRepository._pick_current). These tests pin that
behaviour against the exact failure scenarios — they FAIL on the old code and
pass on the fix. (The in-memory repo overwrites by id, so it could never
reproduce this; that's why the bug shipped.)
"""
from __future__ import annotations

from app.layer4_frameworks.persistence.hana.hana_workflow_repo import HanaWorkflowRepository


def _row(*, workflow_id: str, revision, updated_at: str, version: str,
         id: str = "wf", status: str = "DRAFT", name: str = "n") -> dict:
    """A DB row dict shaped like HANA's lowercase-keyed output."""
    return {
        "id": id, "workflow_id": workflow_id, "name": name, "version": version,
        "description": "", "input_schema": None, "output_schema": None,
        "nodes": None, "edges": None, "status": status, "created_at": updated_at,
        "metadata": None, "updated_at": updated_at, "schema_version": "1.0.0",
        "purpose": "", "test_run_id": None, "routing_path": "/", "main_flow": 0,
        "action": "", "instruction": "", "agents_json": None, "variables": None,
        "revision": revision,
    }


class _FakeDb:
    """Minimal stand-in for the HANA db handle used by HanaWorkflowRepository."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])
        self.writes: list[tuple[str, tuple]] = []

    def execute_query(self, sql: str, params: tuple = ()):
        if "MAX(" in sql and "REVISION" in sql:  # _next_revision
            wid = params[0]
            revs = [r["revision"] for r in self.rows
                    if r.get("id") == wid and r.get("revision") is not None]
            return [{"next_rev": (max(revs) if revs else 0) + 1}]
        # SELECT * FROM AIW_WORKFLOWS WHERE ID = :p0  (get_by_id / publish fetch)
        wid = params[0] if params else None
        rows = [r for r in self.rows if r.get("id") == wid]
        if "STATUS IN" in sql:
            rows = [r for r in rows if (r.get("status") or "").upper() in ("PUBLISHED", "DRAFT")]
        return rows

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        self.writes.append((sql, params))


# --- _pick_current: the pure resolution at the heart of the fix ---

def test_pick_current_beats_clock_skew():
    """Highest REVISION wins even when its UPDATED_AT is EARLIER (clock skew)."""
    older_but_higher_ts = _row(workflow_id="A", revision=1, updated_at="2026-06-27T10:00:05+00:00", version="v-a")
    newest_but_lower_ts = _row(workflow_id="B", revision=2, updated_at="2026-06-27T10:00:01+00:00", version="v-b")
    picked = HanaWorkflowRepository._pick_current([older_but_higher_ts, newest_but_lower_ts])
    assert picked["workflow_id"] == "B"  # old code returned "A" (max UPDATED_AT) -> stale


def test_pick_current_breaks_updated_at_tie_by_revision_not_random_uuid():
    """On an UPDATED_AT tie, REVISION decides — not the random-uuid VERSION."""
    ts = "2026-06-27T10:00:00+00:00"
    # Newer row (revision 2) deliberately has the lexicographically SMALLER version,
    # so the old `VERSION DESC` tiebreak would have picked the older row.
    older = _row(workflow_id="A", revision=1, updated_at=ts, version="zzzz")
    newer = _row(workflow_id="B", revision=2, updated_at=ts, version="aaaa")
    picked = HanaWorkflowRepository._pick_current([older, newer])
    assert picked["workflow_id"] == "B"


def test_pick_current_real_revision_beats_unbackfilled_null():
    """A NULL revision (pre-backfill / transition row) never wins over a real one."""
    backfilled = _row(workflow_id="A", revision=5, updated_at="2026-06-27T10:00:00+00:00", version="v-a")
    legacy_null = _row(workflow_id="B", revision=None, updated_at="2026-06-27T23:59:59+00:00", version="v-b")
    picked = HanaWorkflowRepository._pick_current([backfilled, legacy_null])
    assert picked["workflow_id"] == "A"


# --- get_by_id: end-to-end through the repo's fetch + pick ---

def test_get_by_id_returns_highest_revision_row():
    rows = [
        _row(workflow_id="A", revision=1, updated_at="2026-06-27T10:00:09+00:00", version="v-a"),
        _row(workflow_id="B", revision=2, updated_at="2026-06-27T10:00:01+00:00", version="v-b"),  # latest by revision
    ]
    repo = HanaWorkflowRepository(db=_FakeDb(rows))
    wf = repo.get_by_id("wf")
    assert wf is not None
    assert wf.workflow_id == "B"


def test_get_by_id_none_when_absent():
    repo = HanaWorkflowRepository(db=_FakeDb([]))
    assert repo.get_by_id("missing") is None


# --- get_by_latest_publish_version: PUBLISHED preferred, then highest revision ---

def test_get_by_latest_publish_prefers_published_then_revision():
    rows = [
        _row(workflow_id="D1", revision=9, updated_at="2026-06-27T10:00:09+00:00", version="d1", status="DRAFT"),
        _row(workflow_id="P1", revision=2, updated_at="2026-06-27T10:00:01+00:00", version="p1", status="PUBLISHED"),
        _row(workflow_id="P2", revision=3, updated_at="2026-06-27T10:00:00+00:00", version="p2", status="PUBLISHED"),
    ]
    repo = HanaWorkflowRepository(db=_FakeDb(rows))
    wf = repo.get_by_latest_publish_version("wf")
    assert wf is not None
    assert wf.workflow_id == "P2"  # highest-revision PUBLISHED, despite the DRAFT being newer


# --- save: REVISION assigned monotonically per logical id ---

def test_save_assigns_next_revision_per_id():
    from app.layer1_domain.entities.workflow import Workflow

    existing = [
        _row(workflow_id="A", revision=1, updated_at="2026-06-27T10:00:00+00:00", version="v-a"),
        _row(workflow_id="B", revision=2, updated_at="2026-06-27T10:00:01+00:00", version="v-b"),
    ]
    db = _FakeDb(existing)
    repo = HanaWorkflowRepository(db=db)
    repo.save(Workflow(id="wf", workflow_id="C", version="v-c", name="n"))

    upserts = [(sql, params) for sql, params in db.writes if "UPSERT" in sql]
    assert len(upserts) == 1
    # REVISION is appended last in save()'s column/value lists.
    assert upserts[0][1][-1] == 3  # max(1, 2) + 1
