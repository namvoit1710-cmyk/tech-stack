"""Migration 057 — ``AIW_RUN_LOOP_STATE.SEQ``, because a table has no
insertion order.

Design: ``solace/specs/wcp-node-output-storage-redesign.md`` §15. Amends 056.

``LOOP_CONTINUE`` and ``LOOP_EXIT`` resolve "which loop am I inside?" with

    for loop_node_id, loop_state in reversed(list(run.context.loop_state.items())):

(``loop_continue_handler.py:137``, ``loop_exit_handler.py:179``). Reverse
INSERTION order is how they pick the innermost enclosing loop — their docstrings
say so. Rebuilding ``loop_state`` from rows without an ordering column would
silently hand them a different answer, and the wrong answer is not an error: it
is a BREAK applied to the wrong loop.
"""
from __future__ import annotations

import re

from app.layer4_frameworks.persistence.hana.migrations import _MIGRATIONS_DIR

_HDI = _MIGRATIONS_DIR.parent / "hdi"
_MIGRATION = "057_add_seq_to_run_loop_state.sql"
_TABLE = "AIW_RUN_LOOP_STATE"


def _sql() -> str:
    return (_MIGRATIONS_DIR / _MIGRATION).read_text(encoding="utf-8")


def _hdi() -> str:
    return (_HDI / f"{_TABLE}.hdbtable").read_text(encoding="utf-8")


class TestTheColumnExistsInBothDefinitions:
    def test_the_migration_alters_the_right_table(self):
        assert re.search(
            rf'ALTER\s+TABLE\s+"{_TABLE}"\s+ADD\s*\(\s*"SEQ"', _sql(), re.IGNORECASE
        )

    def test_the_hdbtable_carries_it_too(self):
        """tenant-1 runs ``HANA_RUN_MIGRATIONS=false`` — the .sql never executes
        there, so an ALTER without the matching .hdbtable edit is a column that
        exists only on developer machines (SA-2275)."""
        assert re.search(r'"SEQ"\s+INTEGER', _hdi(), re.IGNORECASE)

    def test_it_is_an_integer(self):
        assert re.search(r'"SEQ"\s+INTEGER', _sql(), re.IGNORECASE)


class TestItCannotBeNull:
    """NULL sorts LAST in HANA. A row that lost its SEQ would therefore be read
    as the INNERMOST loop — precisely inverted — so the column that exists to
    make ordering deterministic must not have an undefined value."""

    def test_the_migration_says_not_null(self):
        assert re.search(r'"SEQ"[^,)]*NOT\s+NULL', _sql(), re.IGNORECASE)

    def test_the_hdbtable_says_not_null(self):
        assert re.search(r'"SEQ"[^,)]*NOT\s+NULL', _hdi(), re.IGNORECASE)

    def test_it_defaults_to_zero(self):
        """A writer that forgets SEQ degrades to 'all equal' — today's
        behaviour — instead of failing the insert."""
        assert re.search(r'"SEQ"[^,)]*DEFAULT\s+0', _sql(), re.IGNORECASE)
        assert re.search(r'"SEQ"[^,)]*DEFAULT\s+0', _hdi(), re.IGNORECASE)


class TestTheTwoDefinitionsAgree:
    def test_same_type_nullability_and_default(self):
        """056 and 057 define this table in two files that no compiler compares.
        The three tables missing from tenant-1 in SA-2275 were exactly this."""
        pattern = re.compile(
            r'"SEQ"\s+(INTEGER)\s+(NOT\s+NULL)\s+(DEFAULT\s+0)', re.IGNORECASE)
        in_sql = pattern.search(_sql())
        in_hdi = pattern.search(_hdi())
        assert in_sql and in_hdi
        assert [g.upper().split() for g in in_sql.groups()] == \
               [g.upper().split() for g in in_hdi.groups()]

    def test_the_key_is_untouched(self):
        """SEQ is an ORDERING column, not part of the identity. Promoting it
        into the key would let one loop node hold several rows per run — rows
        nothing can address, since the runtime keys loop_state by node id."""
        assert re.search(r'PRIMARY\s+KEY\s*\(\s*"RUN_ID",\s*"NODE_ID"\s*\)',
                         _hdi(), re.IGNORECASE)
        assert "SEQ" not in re.search(
            r'PRIMARY\s+KEY\s*\([^)]*\)', _hdi(), re.IGNORECASE).group(0).upper()


class TestTheMigrationIsWellFormed:
    def test_no_semicolon_inside_a_comment(self):
        """The migration runner splits on ``;``. A semicolon inside a ``--``
        comment cuts the file in half and the ALTER never runs — caught once
        already on migration 055."""
        for line in _sql().splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                assert ";" not in stripped, f"semicolon inside comment: {line}"

    def test_it_is_a_single_statement(self):
        statements = [s for s in _sql().split(";") if s.strip()
                      and not all(l.strip().startswith("--")
                                  for l in s.strip().splitlines())]
        assert len(statements) == 1
