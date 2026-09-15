"""Migration 055 — the node-output tables' load-bearing shape.

Design + measurements: ``solace/specs/wcp-node-output-storage-redesign.md``
(smdg-ai-hub). These tables move node output out of
``AIW_WORKFLOW_RUNS.CONTEXT``, which today is rewritten in full on every node
completion — 192 MB stored, 1.58 GB rewritten, 8.4x amplification, measured on
prod-br10 tenant-1 on 2026-08-06.

Nothing reads or writes them yet, so no runtime test can fail if the DDL is
wrong. What follows is therefore the *only* signal, and each assertion is here
because getting it wrong fails **silently or in production**, not in review.
``test_schema_artefact_parity`` already covers migration-vs-HDI parity; this
file covers the semantics parity cannot see.
"""
from __future__ import annotations

import re

from app.layer4_frameworks.persistence.hana.migrations import _MIGRATIONS_DIR

_HDI = _MIGRATIONS_DIR.parent / "hdi"
_MIGRATION = "055_create_run_node_output_tables.sql"


def _sql() -> str:
    return (_MIGRATIONS_DIR / _MIGRATION).read_text(encoding="utf-8")


def _hdi(table: str) -> str:
    return (_HDI / f"{table}.hdbtable").read_text(encoding="utf-8")


def _width(text: str, table: str, column: str) -> int | None:
    """NVARCHAR width of *column* in *table*'s definition inside *text*."""
    body = re.search(
        rf'TABLE\s+"{table}"\s*\((.*?)\n\)', text, re.IGNORECASE | re.DOTALL)
    if not body:
        return None
    m = re.search(
        rf'"{column}"\s+NVARCHAR\((\d+)\)', body.group(1), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _both() -> list[tuple[str, str]]:
    """(label, text) for the migration and every new .hdbtable.

    Every structural rule must hold in BOTH definitions. tenant-1 runs with
    ``HANA_RUN_MIGRATIONS=false``, so the .sql never executes there and the
    .hdbtable is the only thing that creates the table — a rule asserted
    against the migration alone would be asserted against the file that does
    not run in production.
    """
    return [
        (_MIGRATION, _sql()),
        ("AIW_RUN_NODE_OUTPUT.hdbtable", _hdi("AIW_RUN_NODE_OUTPUT")),
    ]


class TestRunIdIsWideEnoughForNestedRuns:
    """A composite child-run ID encodes its whole ancestor chain.

    Migration 026 widened ``AIW_WORKFLOW_RUNS.ID`` and ``AIW_TASKS.RUN_ID`` to
    NVARCHAR(1024) *after* production hit HANA error 274 on 277-char IDs at 5+
    levels of nesting. A new table keyed by run id that is narrower than the
    run id itself rejects every nested run's output — and the sealed design for
    these tables said NVARCHAR(36).

    Asserted as a comparison against the runs table rather than the literal
    1024, so it stays true if the run id is ever widened again.
    """

    def test_matches_the_runs_table_in_every_new_table(self):
        want = _width(_hdi("AIW_WORKFLOW_RUNS"), "AIW_WORKFLOW_RUNS", "ID")
        assert want, "could not read AIW_WORKFLOW_RUNS.ID width"

        sql = _sql()
        for table in (
            "AIW_RUN_NODE_OUTPUT",
            "AIW_RUN_NODE_STATE",
            "AIW_RUN_VARIABLE",
        ):
            for label, text in ((_MIGRATION, sql),
                                (f"{table}.hdbtable", _hdi(table))):
                got = _width(text, table, "RUN_ID")
                assert got is not None, f"{label}: {table}.RUN_ID not found"
                assert got >= want, (
                    f"{label}: {table}.RUN_ID is NVARCHAR({got}) but a run id "
                    f"is NVARCHAR({want}) — nested runs overflow it "
                    f"(HANA error 274, exactly what migration 026 fixed)"
                )


class TestTheKeyCarriesGeneration:
    """Re-executing a run keeps the RUN_ID and bumps GENERATION.

    The G-guard (migrations 029/030) makes ``GENERATION`` the discriminator
    between two executions of the same run. Leave it out of the key and
    generation 1's first attempt UPSERTs over generation 0's — the row count
    stays plausible, the ITERATION/ATTEMPT history is quietly wrong, and
    nothing anywhere raises.
    """

    def test_generation_is_in_the_primary_key(self):
        for label, text in _both():
            pk = re.search(r'PRIMARY KEY \(([^)]*)\)', text)
            assert pk, f"{label}: no PRIMARY KEY on AIW_RUN_NODE_OUTPUT"
            keys = [c.strip().strip('"') for c in pk.group(1).split(",")]
            assert "GENERATION" in keys, (
                f"{label}: PRIMARY KEY is {keys} — without GENERATION a re-run "
                "silently overwrites the previous execution's output"
            )
            # Order matters for the prefix reads: run, then node, then the
            # discriminators newest-last.
            assert keys[:2] == ["RUN_ID", "NODE_ID"], (
                f"{label}: PK must lead with (RUN_ID, NODE_ID) — every read is "
                f"that prefix, and it is why no secondary index is needed. Got {keys}"
            )


class TestTheArtifactTreeCanBeRebuilt:
    """The columns ``_reconstruct_recursive`` cannot work without.

    A child artifact is re-attached with
    ``set_by_path(parent_row, art["name"], rows)`` and ``register_artifacts``
    keys the run-level ``{{$artifacts.<name>}}`` registry off the same field —
    so NAME is data, not decoration. ``artifact_id`` is NOT a substitute: it is
    a *relative* segment that collides (55 distinct ids across 2,688 children
    in the 60 heaviest runs).

    ``PARENT_ROW_KEY_COLUMN`` carries ``add_artifact``'s ``__parent_row_id``
    marker, which is what tells the rebuild a child is a nested array rather
    than a sibling table.
    """

    def test_name_and_parent_row_key_survive(self):
        for label, text in _both():
            for column in ("ARTIFACT_NAME", "PARENT_ROW_KEY_COLUMN"):
                assert f'"{column}"' in text, (
                    f"{label}: {column} is missing — the artifact tree cannot "
                    "be rebuilt without it"
                )

    def test_adjacency_not_a_materialized_path(self):
        """Depth must live in rows. A path column grows with nesting depth, so
        'unlimited nesting' eventually overflows whatever width it is given;
        adjacency keeps every column fixed-width. Observed depth is already 6.
        """
        for label, text in _both():
            for column in ("ARTIFACT_SEQ", "PARENT_SEQ", "ORDINAL", "DEPTH"):
                assert f'"{column}"' in text, f"{label}: {column} is missing"


class TestThereIsNoInlineBranch:
    """The branch was the defect, not a missing size check.

    ``keeps_inline_output`` ignored SIZE and broke both ways — SA-1949 with the
    flag on, SA-1951 with it off — and its 256 KB ceiling has never fired: 0
    events above it out of 15,586. ``FILE_ID NOT NULL`` is the schema-level
    statement that a row cannot mean 'inline instead'.

    The empty string is still legal and means 'no CSV was produced' —
    ``_store_rows_recursive`` sets ``file_id = ""`` for an empty payload.
    """

    def test_file_id_is_not_null(self):
        for label, text in _both():
            assert re.search(r'"FILE_ID"\s+NVARCHAR\(\d+\)\s+NOT NULL', text), (
                f"{label}: FILE_ID must be NOT NULL — a nullable FILE_ID "
                "re-admits the inline branch this design removes"
            )

    def test_no_preview_or_inline_payload_column(self):
        """GET /data already contracts 'identity only — the frontend queries
        the file service directly', and /artifacts/{id}/odata already exists.
        A preview column is the control plane guessing which 3 of up to 537
        columns the user wanted.
        """
        for label, text in _both():
            for banned in ("PREVIEW", "PAYLOAD_INLINE", "INLINE_DATA"):
                assert f'"{banned}"' not in text.upper(), (
                    f"{label}: {banned} re-introduces the inline branch"
                )


class TestTheFrontendContractIsSatisfiable:
    """The FE needs THREE things, not two.

    ``get_variable_suggestions_use_case.py`` does
    ``ref_columns = list(ctx_value.get("columns") or [])`` — without columns the
    suggestion dropdown is **silently empty**, which reads as 'this node has no
    output' rather than as an error. Type and size alone are not enough.
    """

    def test_type_size_and_columns_are_all_reachable(self):
        text = _sql()
        for column in ("VALUE_KIND", "ITEM_KIND", "PAYLOAD_BYTES", "SCHEMA_HASH"):
            assert f'"{column}"' in text, f"{column} missing from 055"
        assert 'CREATE COLUMN TABLE "AIW_SCHEMA_DEF"' in text
        assert '"COLUMNS_JSON"' in text, (
            "columns must be reachable from a node-output row or the variable "
            "suggestion dropdown is silently empty"
        )


class TestQueryByNameCostsAnIndexNotACopy:
    """The operator's requirement was 'keep querying nodes by id AND by name'.

    Today that is met by storing the whole payload twice —
    ``variables[node.id]`` and ``variables[node.name]`` — which was 46.9% of one
    real run's context. One row plus one index meets it for free.
    """

    def test_node_name_is_indexed_not_duplicated(self):
        sql = _sql()
        assert 'ON "AIW_RUN_NODE_OUTPUT" ("RUN_ID", "NODE_NAME")' in sql
        assert (_HDI / "IDX_AIW_RNO_NAME.hdbindex").is_file(), (
            "without the HDI artefact tenant-1 gets the table and not the "
            "index — a silent performance cliff, not an error"
        )
