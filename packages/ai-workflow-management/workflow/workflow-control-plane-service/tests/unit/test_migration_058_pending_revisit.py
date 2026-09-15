"""Migration 058 — ``AIW_RUN_NODE_STATE.PENDING_REVISIT``.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13. Amends 055.

055 gave this table ``STATE NVARCHAR(24)`` and said it replaced five ``RunContext``
fields. Four fit. ``pending_revisits`` does not, because it is not mutually
exclusive with the others — SA-846 Mode A records a revisit for a node that has
**already completed**, so the normal shape is *completed AND pending*, which one
``STATE`` column cannot express.
"""
from __future__ import annotations

import re

from app.layer4_frameworks.persistence.hana.migrations import _MIGRATIONS_DIR

_HDI = _MIGRATIONS_DIR.parent / "hdi"
_MIGRATION = "058_add_pending_revisit_to_run_node_state.sql"
_TABLE = "AIW_RUN_NODE_STATE"


def _sql() -> str:
    return (_MIGRATIONS_DIR / _MIGRATION).read_text(encoding="utf-8")


def _hdi() -> str:
    return (_HDI / f"{_TABLE}.hdbtable").read_text(encoding="utf-8")


class TestTheColumnExistsInBothDefinitions:
    def test_the_migration_alters_the_right_table(self):
        assert re.search(
            rf'ALTER\s+TABLE\s+"{_TABLE}"\s+ADD\s*\(\s*"PENDING_REVISIT"',
            _sql(), re.IGNORECASE)

    def test_the_hdbtable_carries_it_too(self):
        """tenant-1 runs ``HANA_RUN_MIGRATIONS=false`` — the .sql never executes
        there, so an ALTER without the matching .hdbtable edit is a column that
        exists only on developer machines (SA-2275)."""
        assert re.search(r'"PENDING_REVISIT"\s+INTEGER', _hdi(), re.IGNORECASE)

    def test_it_is_an_integer_like_every_other_flag_here(self):
        """HANA has BOOLEAN, but ``IS_TEST`` is written as ``1 if … else 0`` and
        every other flag in this schema is an INTEGER. Consistency with the
        eleven tables already here beats the marginally better type."""
        assert re.search(r'"PENDING_REVISIT"\s+INTEGER', _sql(), re.IGNORECASE)


class TestItCannotBeNull:
    def test_the_migration_says_not_null(self):
        assert re.search(r'"PENDING_REVISIT"[^,)]*NOT\s+NULL', _sql(), re.IGNORECASE)

    def test_the_hdbtable_says_not_null(self):
        assert re.search(r'"PENDING_REVISIT"[^,)]*NOT\s+NULL', _hdi(), re.IGNORECASE)

    def test_it_defaults_to_not_pending(self):
        """A writer that forgets it degrades to the state a node is in 99% of
        the time, rather than to NULL — which every comparison would then have
        to special-case."""
        assert re.search(r'"PENDING_REVISIT"[^,)]*DEFAULT\s+0', _sql(), re.IGNORECASE)
        assert re.search(r'"PENDING_REVISIT"[^,)]*DEFAULT\s+0', _hdi(), re.IGNORECASE)


class TestTheTwoDefinitionsAgree:
    def test_same_type_nullability_and_default(self):
        pattern = re.compile(
            r'"PENDING_REVISIT"\s+(INTEGER)\s+(NOT\s+NULL)\s+(DEFAULT\s+0)',
            re.IGNORECASE)
        in_sql, in_hdi = pattern.search(_sql()), pattern.search(_hdi())
        assert in_sql and in_hdi
        assert [g.upper().split() for g in in_sql.groups()] == \
               [g.upper().split() for g in in_hdi.groups()]

    def test_055s_columns_are_all_still_there(self):
        """An ALTER migration and a declarative .hdbtable are edited in two
        different files; dropping a column from the latter is a silent data
        loss on the next HDI deploy."""
        for column in ("RUN_ID", "NODE_ID", "STATE", "VISITS",
                       "LAST_OUTPUT_PORT", "UPDATED_AT"):
            assert f'"{column}"' in _hdi(), column

    def test_the_key_is_untouched(self):
        assert re.search(r'PRIMARY\s+KEY\s*\(\s*"RUN_ID",\s*"NODE_ID"\s*\)',
                         _hdi(), re.IGNORECASE)


class TestTheMigrationIsWellFormed:
    def test_no_semicolon_inside_a_comment(self):
        """The runner splits on ``;``. One inside a ``--`` comment cuts the file
        in half and the ALTER never runs — caught on 055 and again on 057."""
        for line in _sql().splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                assert ";" not in stripped, f"semicolon inside comment: {line}"

    def test_it_is_a_single_statement(self):
        statements = [s for s in _sql().split(";") if s.strip()
                      and not all(l.strip().startswith("--")
                                  for l in s.strip().splitlines())]
        assert len(statements) == 1
