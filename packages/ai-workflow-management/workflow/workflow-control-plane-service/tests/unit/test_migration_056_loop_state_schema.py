"""Migration 056 — AIW_RUN_LOOP_STATE, the last blocker on DROP CONTEXT.

Design + measurements: ``solace/specs/wcp-node-output-storage-redesign.md``
sections 13 and 15 (smdg-ai-hub).

An audit of all 12 ``RunContext`` fields against ``ProjectedRunState`` found
exactly one that is neither event-sourced nor derivable from
``AIW_WORKFLOWS``, and is therefore destroyed by ``DROP CONTEXT``:
``loop_state``. This table is its home.

Nothing reads or writes it yet, so these assertions are the only signal that
the shape is right. Each one exists because getting it wrong fails silently or
in production.
"""
from __future__ import annotations

import re

from app.layer4_frameworks.persistence.hana.migrations import _MIGRATIONS_DIR

_HDI = _MIGRATIONS_DIR.parent / "hdi"
_MIGRATION = "056_create_run_loop_state_table.sql"
_TABLE = "AIW_RUN_LOOP_STATE"

# Every key ``loop_state`` carries, read off event_driven_loop_driver.py:274
# plus the setdefault sites. Grouped by how each one GROWS, because that is
# what decides which column it lands in.
_CLASS_A_SCALARS = (           # fixed size — measured 426 B across 4 loops
    "current_index", "total", "mode", "loop_type", "condition",
    "counter_variable", "next_dispatch_index", "invocation_id",
    "enclosing_scope_stack", "parallelism", "continue_requested",
    "break_requested",
)
_CLASS_B_BOUNDED = (           # pruned per iteration — measured 105 B
    "iter_progress", "iter_failed_nodes",
    "completed_iterations", "failed_iterations",
)
_CLASS_C_PAYLOAD = (           # 99.99% of all loop_state bytes ever stored
    "items", "iteration_results",
)


def _sql() -> str:
    return (_MIGRATIONS_DIR / _MIGRATION).read_text(encoding="utf-8")


def _hdi(table: str) -> str:
    return (_HDI / f"{table}.hdbtable").read_text(encoding="utf-8")


def _both() -> list[tuple[str, str]]:
    """(label, text) for the migration and the .hdbtable.

    Both, always. tenant-1 runs ``HANA_RUN_MIGRATIONS=false``, so the .sql never
    executes there and the .hdbtable is the only thing that creates the table.
    """
    return [(_MIGRATION, _sql()), (f"{_TABLE}.hdbtable", _hdi(_TABLE))]


def _width(text: str, column: str) -> int | None:
    m = re.search(rf'"{column}"\s+NVARCHAR\((\d+)\)', text, re.IGNORECASE)
    return int(m.group(1)) if m else None


class TestPayloadCannotComeBackInline:
    """``STATE_JSON NVARCHAR(5000)`` is an enforcement, not a fit.

    ``iteration_results`` (65%) and ``items`` (35%) are 99.99% of every byte of
    ``loop_state`` ever measured — 4.01 MB across four loops, against 426 B for
    the twelve scalars. Both already have a claim-check path. The cap is what
    makes putting them back a **loud write failure** rather than a silent
    return of the megabytes, exactly as ``OUTPUT_METADATA`` being NCLOB is how
    654 KB of payload got into a column named *metadata*.
    """

    def test_state_json_is_capped_not_a_clob(self):
        for label, text in _both():
            assert "STATE_JSON" in text, f"{label}: STATE_JSON missing"
            width = _width(text, "STATE_JSON")
            assert width is not None, (
                f"{label}: STATE_JSON must be NVARCHAR — an NCLOB here re-admits "
                "the megabytes this table exists to evict"
            )
            # HANA's NVARCHAR ceiling. Class A measures ~107 B, so anything in
            # this range is enforcement; the point is that a bound EXISTS.
            assert width <= 5000, f"{label}: NVARCHAR({width}) exceeds HANA's max"

    def test_payload_keys_have_ref_columns_not_value_columns(self):
        for label, text in _both():
            for column in ("ITEMS_REF", "RESULTS_REF"):
                assert f'"{column}"' in text, (
                    f"{label}: {column} missing — {_CLASS_C_PAYLOAD} must be "
                    "stored as pointers, never values"
                )
                width = _width(text, column)
                assert width is not None and width <= 256, (
                    f"{label}: {column} is not a narrow ref column "
                    f"(got {width}) — a wide one invites the value back"
                )


class TestBoundedStateHasAnUncappedHome:
    """Class B is bounded, but by a value the USER sets.

    ``iter_progress`` is pruned per iteration (``loop_iteration_driver.py:424-427``
    pops the finished bucket), so it is bounded by ``min(total, parallelism)``,
    itself bounded by ``max_iterations`` (default 10000). Bounded is not the
    same as small, and capping a legitimately variable field breaks a valid
    workload rather than catching a bug — so this one is deliberately NCLOB.
    """

    def test_progress_json_is_nclob(self):
        for label, text in _both():
            assert re.search(r'"PROGRESS_JSON"\s+NCLOB', text, re.IGNORECASE), (
                f"{label}: PROGRESS_JSON must be NCLOB — capping it would reject "
                f"a valid high-parallelism loop. Class B keys: {_CLASS_B_BOUNDED}"
            )


class TestTheKeyMatchesTheRuntime:
    """PK is ``(RUN_ID, NODE_ID)`` because ``run.context.loop_state`` is.

    ``derive_invocation_id`` returns a different id per outer iteration of a
    nested loop — its docstring calls that "the critical property" — while the
    runtime dict is keyed by node id alone. Measured nesting depth is **0
    across all 23 LoopStarted invocations ever written**, so the aliasing is
    theoretical. INVOCATION_ID is stored as data; promoting it into the key
    would mint rows nothing can address.
    """

    def test_primary_key_is_run_and_node_only(self):
        for label, text in _both():
            pk = re.search(r'PRIMARY KEY \(([^)]*)\)', text)
            assert pk, f"{label}: no PRIMARY KEY"
            keys = [c.strip().strip('"') for c in pk.group(1).split(",")]
            assert keys == ["RUN_ID", "NODE_ID"], (
                f"{label}: PK is {keys}. It must match the runtime dict key "
                "exactly — a richer key creates unaddressable rows, a poorer "
                "one merges two loops"
            )

    def test_invocation_id_is_stored_but_not_keyed(self):
        for label, text in _both():
            assert '"INVOCATION_ID"' in text, f"{label}: INVOCATION_ID missing"

    def test_run_id_fits_a_nested_child_run(self):
        """Same trap migration 026 fixed: composite child-run ids reach 277
        chars and ``AIW_WORKFLOW_RUNS.ID`` is NVARCHAR(1024)."""
        want = _width(_hdi("AIW_WORKFLOW_RUNS"), "ID")
        assert want, "could not read AIW_WORKFLOW_RUNS.ID width"
        for label, text in _both():
            got = _width(text, "RUN_ID")
            assert got is not None and got >= want, (
                f"{label}: RUN_ID is NVARCHAR({got}) but a run id is "
                f"NVARCHAR({want}) — nested runs overflow it (HANA error 274)"
            )


class TestNoPerIterationTable:
    """The measurement killed the per-iteration table; keep it dead.

    It was proposed because ``completed_iterations`` is append-only and looked
    unbounded. Across the whole event log — 23 ``LoopStarted`` invocations in
    6,060 runs — ``total`` peaks at **100**, none are unbounded, and none nest.
    ~100 integers is ~600 bytes. Re-adding the table would serve a workload
    that has never occurred; if one ever does, the evidence must come first.
    """

    def test_migration_creates_exactly_one_table(self):
        tables = re.findall(
            r'CREATE\s+(?:COLUMN\s+)?TABLE\s+"([A-Z0-9_]+)"', _sql(), re.IGNORECASE)
        assert tables == [_TABLE], (
            f"056 should create exactly {_TABLE}, got {tables}"
        )

    def test_no_iteration_column_on_the_loop_row(self):
        for label, text in _both():
            assert '"ITERATION"' not in text, (
                f"{label}: an ITERATION column means one row per iteration. "
                "Measured max total is 100 — that table has no workload"
            )


class TestTheGuardItselfWorks:
    """A schema test that cannot fail would read as proof."""

    def test_a_widened_state_json_is_rejected(self):
        bad = _hdi(_TABLE).replace(
            '"STATE_JSON"    NVARCHAR(5000)', '"STATE_JSON"    NCLOB')
        assert _width(bad, "STATE_JSON") is None, (
            "the cap check reads the declared type — if this stops holding, "
            "TestPayloadCannotComeBackInline is no longer checking anything"
        )

    def test_a_richer_primary_key_is_rejected(self):
        bad = _sql().replace(
            'PRIMARY KEY ("RUN_ID", "NODE_ID")',
            'PRIMARY KEY ("RUN_ID", "NODE_ID", "INVOCATION_ID")')
        pk = re.search(r'PRIMARY KEY \(([^)]*)\)', bad)
        keys = [c.strip().strip('"') for c in pk.group(1).split(",")]
        assert keys != ["RUN_ID", "NODE_ID"]
