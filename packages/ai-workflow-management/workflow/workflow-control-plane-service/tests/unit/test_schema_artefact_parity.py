"""Every migration must have an HDI artefact — SA-2275.

The schema is defined TWICE in this service and nothing kept the two in sync:

* ``db/migrations/*.sql`` — applied by the app at boot, but only when
  ``HANA_RUN_MIGRATIONS`` is true.
* ``db/hdi/*.hdbtable`` — deployed by the HDI deployer.

tenant-1 runs ``HANA_RUN_MIGRATIONS=false``, so **the .sql files never execute
there** and `AIW_SCHEMA_MIGRATIONS` does not even exist. A developer who adds a
migration sees it work locally and on any self-migrating environment, and has
no signal at all that the table will never appear on tenant-1.

Measured on tenant-1 on 2026-08-01 — three tables missing for exactly this
reason, one of them silently breaking a feature every five minutes:

    AIW_GATEWAY_INTERNAL_SERVICES   038   no .hdbtable
    AIW_TIMERS                      049   no .hdbtable   <- durable timers
    AIW_WORKER_REGISTRY_MIRROR      051   no .hdbtable

This test is the signal that was missing. It is deliberately a *parity* check
rather than a snapshot: it compares the two definitions to each other, so it
keeps working as the schema grows and needs no updating when a table is added
correctly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DB = Path(__file__).resolve().parents[2] / "db"
MIGRATIONS = _DB / "migrations"
HDI = _DB / "hdi"

# ``CREATE COLUMN TABLE "X"`` / ``CREATE TABLE "X"``. ALTER is excluded on
# purpose: it modifies a table another migration created, and the .hdbtable for
# that table carries the final shape.
_CREATE_TABLE = re.compile(
    r'CREATE\s+(?:COLUMN\s+)?TABLE\s+"([A-Z0-9_]+)"', re.IGNORECASE)
_CREATE_INDEX = re.compile(
    r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+"([A-Z0-9_]+)"', re.IGNORECASE)


def _sql_files() -> list[Path]:
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert files, f"no migrations found under {MIGRATIONS}"
    return files


def _declared(pattern: re.Pattern) -> dict[str, str]:
    """name -> migration filename that creates it."""
    out: dict[str, str] = {}
    for path in _sql_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in pattern.findall(text):
            out.setdefault(name.upper(), path.name)
    return out


def _artefacts(suffix: str) -> set[str]:
    return {p.stem.upper() for p in HDI.glob(f"*{suffix}")}


_INDEX_TARGET = re.compile(
    r'INDEX\s+"([A-Z0-9_]+)"\s+ON\s+"([A-Z0-9_]+)"\s*\(([^)]*)\)', re.IGNORECASE)


def _index_target(name: str) -> tuple[str | None, tuple[str, ...]]:
    """(table, columns) for the LAST migration definition of index *name*."""
    found: tuple[str | None, tuple[str, ...]] = (None, ())
    for path in _sql_files():
        for idx, table, cols in _INDEX_TARGET.findall(
                path.read_text(encoding="utf-8", errors="replace")):
            if idx.upper() != name.upper():
                continue
            found = (
                table.upper(),
                tuple(c.replace('"', " ").split()[0].upper()
                      for c in cols.split(",") if c.strip()),
            )
    return found


def _hdbtable_columns(table: str) -> set[str] | None:
    """Columns declared by ``<table>.hdbtable``, or None if there is no artefact."""
    path = HDI / f"{table}.hdbtable"
    if not path.is_file():
        return None
    body = path.read_text(encoding="utf-8", errors="replace")
    # Skip the leading COLUMN TABLE "NAME" so the table name is not read as a column.
    body = body.split("(", 1)[1] if "(" in body else body
    return {
        m.group(1).upper()
        for m in re.finditer(r'"([A-Z0-9_]+)"\s+[A-Za-z]', body)
    }


class TestEveryMigratedTableHasAnHdiArtefact:
    def test_no_table_is_migration_only(self):
        """A table with no .hdbtable can never reach an HDI-deployed tenant."""
        tables = _declared(_CREATE_TABLE)
        artefacts = _artefacts(".hdbtable")
        missing = {t: m for t, m in tables.items() if t not in artefacts}
        assert not missing, (
            "these tables are created by a migration but have no HDI artefact, "
            "so they will NEVER exist on a tenant that runs with "
            "HANA_RUN_MIGRATIONS=false (tenant-1 does):\n  "
            + "\n  ".join(f"{t}  (from {m})" for t, m in sorted(missing.items()))
            + "\n\nAdd db/hdi/<TABLE>.hdbtable alongside the migration."
        )

    def test_no_index_is_migration_only(self):
        """Same trap one level down — but only for indexes that CAN exist.

        An index is required to have an artefact only when every column it
        names is present in the table's own ``.hdbtable``. That qualifier is
        not a loophole; it is the rule. Migration 014 adds ``TIMEOUT_AT`` to
        ``AIW_TASKS`` by ALTER and indexes it, for a child-timeout sweeper the
        code has since REMOVED. ``AIW_TASKS.hdbtable`` never carried the
        column, so on an HDI tenant that column does not exist — and demanding
        the index anyway is how this guard's first version broke a production
        deploy:

            com.sap.hana.di.index: Database error 260:
            invalid column name: TIMEOUT_AT

        So: table shape decides. A migration index over columns the HDI table
        does not have is a DEAD artefact of a removed feature, and the correct
        state is for it to be absent.
        """
        artefacts = _artefacts(".hdbindex")
        missing, dead = {}, {}
        for name, source in _declared(_CREATE_INDEX).items():
            table, cols = _index_target(name)
            if table is None:
                continue
            available = _hdbtable_columns(table)
            if available is None:
                continue  # table itself has no artefact — the table test owns it
            if not set(cols) <= available:
                dead[name] = (source, sorted(set(cols) - available))
                continue
            if name not in artefacts:
                missing[name] = source

        assert not missing, (
            "these indexes are created by a migration, their columns DO exist "
            "in the HDI table, and yet they have no artefact — so an HDI tenant "
            "gets the table without its index, a silent performance cliff "
            "rather than an error:\n  "
            + "\n  ".join(f"{i}  (from {m})" for i, m in sorted(missing.items()))
        )

        # The mirror: a dead index must NOT have an artefact, or the deploy
        # fails on a column that does not exist.
        wrongly_present = {n: d for n, d in dead.items() if n in artefacts}
        assert not wrongly_present, (
            "these HDI index artefacts name columns their table does not "
            "have — HDI will refuse the whole deploy:\n  "
            + "\n  ".join(
                f"{n}: missing column(s) {cols} (migration {src})"
                for n, (src, cols) in sorted(wrongly_present.items()))
        )


class TestIndexArtefactsMatchTheirMigration:
    """Existence is not enough — the COLUMNS have to agree.

    Writing this guard, I created ``IDX_AIW_TASKS_TIMEOUT.hdbindex`` on
    ``("TIMEOUT_AT")`` when migration 014 declares
    ``("TIMEOUT_AT", "STATUS")``. The name-only check above passed happily. A
    single-column index where a composite was intended is not an error anywhere
    — it is just a slower sweep, which is precisely the kind of divergence that
    survives for years.
    """

    @staticmethod
    def _columns(text: str, name: str) -> tuple[str, ...] | None:
        """LAST definition of *name* in *text*, normalised.

        Last, not first: a migration file may drop and recreate an index, and
        039 does exactly that. Normalised because a column can carry a sort
        direction — ``"VERSION" DESC`` — and both sides must be reduced the
        same way before they are comparable.
        """
        matches = re.findall(
            rf'INDEX\s+"{re.escape(name)}"\s+ON\s+"[A-Z0-9_]+"\s*\(([^)]*)\)',
            text, re.IGNORECASE)
        if not matches:
            return None
        return tuple(
            " ".join(c.replace('"', " ").split()).upper()
            for c in matches[-1].split(",") if c.strip()
        )

    def test_index_columns_agree(self):
        # Migrations are a SEQUENCE: a later file supersedes an earlier one.
        # 034 created IDX_AIW_WORKFLOWS_GROUP_VERSION on ("GROUP_ID", ...) and
        # 039 dropped and recreated it on ("ID", ...). Comparing the artefact
        # against the FIRST definition reports a mismatch that is really just
        # history — so fold the migrations in order and keep the last word.
        latest: dict[str, tuple[str, ...]] = {}
        for path in _sql_files():
            sql = path.read_text(encoding="utf-8", errors="replace")
            for name in set(_CREATE_INDEX.findall(sql)):
                cols = self._columns(sql, name)
                if cols is not None:
                    latest[name] = cols

        mismatches = []
        for name, want in sorted(latest.items()):
            artefact = HDI / f"{name}.hdbindex"
            if not artefact.is_file():
                continue  # covered by the existence test
            got = self._columns(
                artefact.read_text(encoding="utf-8", errors="replace"), name)
            if want != got:
                mismatches.append(f"{name}: migration {want} vs hdi {got}")
        assert not mismatches, (
            "HDI index artefacts disagree with the migration that declares "
            "them — an HDI tenant silently gets a DIFFERENT index:\n  "
            + "\n  ".join(sorted(mismatches))
        )


class TestTheArtefactsAreNotOrphans:
    def test_every_hdbtable_traces_back_to_a_migration(self):
        """The mirror direction: an artefact nothing declares is either dead or
        a table whose migration was lost. Either way it should be explained."""
        tables = set(_declared(_CREATE_TABLE))
        # The ledger is created by the runner itself, never by a migration.
        allowed_extra = {"AIW_SCHEMA_MIGRATIONS"}
        orphans = _artefacts(".hdbtable") - tables - allowed_extra
        assert not orphans, (
            "HDI artefacts with no migration that creates them: "
            f"{sorted(orphans)}"
        )


class TestTheGuardItselfWorks:
    """A parity check that cannot fail would read as proof of parity."""

    def test_a_planted_migration_only_table_is_detected(self, tmp_path,
                                                        monkeypatch):
        mig = tmp_path / "migrations"
        hdi = tmp_path / "hdi"
        mig.mkdir()
        hdi.mkdir()
        (mig / "999_planted.sql").write_text(
            'CREATE COLUMN TABLE "AIW_PLANTED" ("ID" NVARCHAR(1));',
            encoding="utf-8")

        monkeypatch.setattr(
            "tests.unit.test_schema_artefact_parity.MIGRATIONS", mig)
        monkeypatch.setattr(
            "tests.unit.test_schema_artefact_parity.HDI", hdi)

        with pytest.raises(AssertionError, match="AIW_PLANTED"):
            TestEveryMigratedTableHasAnHdiArtefact().test_no_table_is_migration_only()

    def test_it_reads_the_real_directories(self):
        assert MIGRATIONS.is_dir(), MIGRATIONS
        assert HDI.is_dir(), HDI
        assert len(_sql_files()) > 20, "migration discovery looks wrong"
