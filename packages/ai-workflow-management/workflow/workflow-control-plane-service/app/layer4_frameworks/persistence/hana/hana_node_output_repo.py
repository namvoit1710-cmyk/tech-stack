"""SAP HANA store for node output descriptors — ``AIW_RUN_NODE_OUTPUT``.

Design + every measurement: ``solace/specs/wcp-node-output-storage-redesign.md``.
Tables from migrations ``055`` / ``056``, live on tenant-1 since 2026-08-07.

Why this exists
===============

Node output lives in ``AIW_WORKFLOW_RUNS.CONTEXT``, one NCLOB that also holds
loop state, topology and the control sets — and the optimistic-CAS save rewrites
all 24 columns every time. Measured on prod-br10 / tenant-1: **192.43 MB stored,
1.58 GB rewritten across 43,199 saves — 8.4x write amplification**, with one node
completion costing three round-trips carrying 2x the full CONTEXT.

Here a completion is an **append**: no CAS, no read-modify-write, and the row is
~200 bytes because the payload lives in the file service and the schema lives
once in ``AIW_SCHEMA_DEF`` (2,702 refs collapse to 481 distinct pairs — 5.6x,
31.51 MB down to 7.908 MB).

Pattern notes
=============

Follows the template of ``HanaTimerRepository`` / ``HanaEventLogRepository``:
``IDatabaseConnection`` in the constructor, ``:p0``-style placeholders, and
delete-then-insert for upsert because HANA has no portable ``ON CONFLICT``.

Two things are deliberately NOT symmetric with that template:

* **The schema dictionary is insert-if-absent, never delete-then-insert.** Rows
  are immutable by construction — the key IS the content hash — so re-writing
  one can only produce the bytes already there, while deleting it would orphan
  every other row pointing at it.
* **A node output is delete-then-insert of the whole ARTIFACT TREE**, keyed by
  (run, node, generation, iteration, attempt). A partial overwrite would leave
  orphan children from the previous descriptor whose ``PARENT_SEQ`` still
  resolves, and ``rebuild`` would happily return a tree mixing both.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.data.node_output_rows import flatten, rebuild

logger = logging.getLogger(__name__)

_TABLE = "AIW_RUN_NODE_OUTPUT"
_SCHEMA_TABLE = "AIW_SCHEMA_DEF"

#: Column order used by both the INSERT and every SELECT, so a row dict from
#: ``execute_query`` and a row dict from ``flatten`` stay interchangeable.
_COLUMNS = (
    "RUN_ID", "NODE_ID", "GENERATION", "ITERATION", "ATTEMPT", "ARTIFACT_SEQ",
    "NODE_NAME", "PARENT_SEQ", "ARTIFACT_PATH", "ARTIFACT_NAME", "ARTIFACT_TYPE",
    "PARENT_ROW_KEY_COLUMN", "ORDINAL", "DEPTH", "FILE_ID", "CONTENT_TYPE",
    "VALUE_KIND", "ITEM_KIND", "ROW_COUNT", "PAYLOAD_BYTES", "SCHEMA_HASH",
    "REF_VERSION", "ETAG", "CREATED_AT",
)

_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(f':p{i}' for i in range(len(_COLUMNS)))})"
)

_DELETE_ATTEMPT_SQL = (
    f"DELETE FROM {_TABLE} WHERE RUN_ID = :p0 AND NODE_ID = :p1 "
    f"AND GENERATION = :p2 AND ITERATION = :p3 AND ATTEMPT = :p4"
)

_SELECT_COLS = ", ".join(_COLUMNS)

#: The identity read. ``NODE_ID = ? OR NODE_NAME = ?`` is how "query a node by
#: id AND by name" costs one stored payload instead of two — the alias was 46.9%
#: of one real run's CONTEXT. IDX_AIW_RNO_NAME serves the name half.
_SELECT_LATEST_SQL = (
    f"SELECT {_SELECT_COLS} FROM {_TABLE} "
    f"WHERE RUN_ID = :p0 AND (NODE_ID = :p1 OR NODE_NAME = :p2) "
    f"ORDER BY GENERATION DESC, ITERATION DESC, ATTEMPT DESC, ARTIFACT_SEQ ASC"
)

_SELECT_SCHEMAS_SQL = (
    f"SELECT SCHEMA_HASH, COLUMNS_JSON FROM {_SCHEMA_TABLE} "
    f"WHERE SCHEMA_HASH IN ({{placeholders}})"
)

_COUNT_SCHEMA_SQL = f"SELECT COUNT(*) AS CNT FROM {_SCHEMA_TABLE} WHERE SCHEMA_HASH = :p0"

_INSERT_SCHEMA_SQL = (
    f"INSERT INTO {_SCHEMA_TABLE} (SCHEMA_HASH, COLUMNS_JSON, COLUMN_COUNT, CREATED_AT) "
    f"VALUES (:p0, :p1, :p2, :p3)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lower_keys(row: Any) -> dict[str, Any]:
    """``execute_query`` lowercases column names; ``flatten`` emits upper.

    Normalising here keeps ``rebuild`` indifferent to which side a row came
    from, which is what makes the round-trip test meaningful against real rows.
    """
    if not isinstance(row, dict):
        return {}
    return {str(k).upper(): v for k, v in row.items()}


class HanaNodeOutputRepository:
    """Append-only descriptor store for node output."""

    def __init__(self, db: Any) -> None:
        """``db`` exposes ``execute_query(sql, params)`` / ``execute_write(...)``."""
        self._db = db

    # ── write ────────────────────────────────────────────────────────────

    def save(
        self,
        *,
        run_id: str,
        node_id: str,
        node_name: str = "",
        file_ref: dict[str, Any],
        generation: int = 0,
        iteration: int = 0,
        attempt: int = 0,
    ) -> int:
        """Store one node output. Returns the number of artifact rows written.

        ``node_name`` is an INDEX, never a second copy of the payload. It is
        truncated rather than allowed to overflow: the column is NVARCHAR(256)
        (matching ``AIW_NODE_DEFINITIONS.NAME``) and node names are user-typed
        with nothing validating their length, so an over-long display label
        would otherwise fail the insert with HANA error 274 and take the run
        down with it. Losing the alias degrades a lookup; losing the run does not.
        """
        rows, schemas = flatten(file_ref)
        if not rows:
            return 0

        for digest, schema in schemas.items():
            self._ensure_schema(digest, schema)

        self._db.execute_write(
            _DELETE_ATTEMPT_SQL,
            (run_id, node_id, int(generation), int(iteration), int(attempt)),
        )
        created = _now()
        alias = (node_name or "")[:256]
        for row in rows:
            self._db.execute_write(
                _INSERT_SQL,
                (
                    run_id, node_id, int(generation), int(iteration), int(attempt),
                    int(row["ARTIFACT_SEQ"]), alias, row["PARENT_SEQ"],
                    row["ARTIFACT_PATH"], row["ARTIFACT_NAME"], row["ARTIFACT_TYPE"],
                    row["PARENT_ROW_KEY_COLUMN"], int(row["ORDINAL"]), int(row["DEPTH"]),
                    row["FILE_ID"], row["CONTENT_TYPE"], row["VALUE_KIND"],
                    row["ITEM_KIND"], int(row["ROW_COUNT"]), int(row["PAYLOAD_BYTES"]),
                    row["SCHEMA_HASH"], int(row["REF_VERSION"]), row["ETAG"],
                    row["CREATED_AT"] or created,
                ),
            )
        return len(rows)

    def ensure_schemas(self, file_ref: dict[str, Any]) -> int:
        """Store just the SCHEMA half of a descriptor. Returns entries written.

        Split out of :meth:`save` for one reason, and it is an ordering one.
        Under ``SCHEMA_REF_MODE=on`` the event carries a ``schema_hash`` instead
        of the schema, so a consumer that reads the event before the dictionary
        entry exists cannot expand it — and the reader's fallback is to GUESS
        the types, silently. ``save()`` runs after the publish (deliberately:
        the row is a derived index and must never lead the event stream), which
        would leave exactly that window open.

        Writing the schema early closes it and costs nothing, because a schema
        entry is not run-scoped state: it is content-addressed and immutable, so
        it cannot lead anything or be wrong about a run. Only the ROW has to
        wait for the event.
        """
        rows, schemas = flatten(file_ref)
        if not rows:
            return 0
        written = 0
        for digest, schema in schemas.items():
            self._ensure_schema(digest, schema)
            written += 1
        return written

    def _ensure_schema(self, digest: str, schema: dict[str, Any]) -> None:
        """Insert a schema row if this hash is not already stored.

        Immutable by construction — the key is the content hash — so an existing
        row already holds exactly these bytes and re-writing it would be a no-op
        that costs a delete. A concurrent insert of the same hash races to the
        same value, so a duplicate-key error here is success, not failure.
        """
        if not digest:
            return
        try:
            rows = self._db.execute_query(_COUNT_SCHEMA_SQL, (digest,))
            if rows and int(list(rows[0].values())[0] or 0) > 0:
                return
        except Exception:  # pragma: no cover — read failure falls through to insert
            logger.debug("schema-dict probe failed for %s", digest, exc_info=True)
        columns = list(schema.get("columns") or [])
        payload = json.dumps(
            {"columns": columns, "column_types": dict(schema.get("column_types") or {})},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        try:
            self._db.execute_write(
                _INSERT_SCHEMA_SQL, (digest, payload, len(columns), _now()))
        except Exception as exc:
            # 301 = unique constraint violated: another writer won the race with
            # identical content. Anything else is real.
            if getattr(exc, "errorcode", None) != 301:
                raise
            logger.debug("schema %s already inserted concurrently", digest)

    # ── read ─────────────────────────────────────────────────────────────

    def get_latest(
        self, run_id: str, node_ref: str,
    ) -> dict[str, Any] | None:
        """Newest descriptor for a node, addressed by id **or** name.

        Returns the rebuilt ``file_ref``, or ``None`` when the node has no
        output. Newest = highest (generation, iteration, attempt): GENERATION is
        in the key because re-executing a run keeps its RUN_ID and bumps it
        (the G-guard), so without it a re-run's first attempt would look like
        the original's.
        """
        raw = self._db.execute_query(_SELECT_LATEST_SQL, (run_id, node_ref, node_ref))
        rows = [_lower_keys(r) for r in (raw or [])]
        if not rows:
            return None

        def key(r: dict[str, Any]) -> tuple[int, int, int]:
            return (int(r.get("GENERATION") or 0), int(r.get("ITERATION") or 0),
                    int(r.get("ATTEMPT") or 0))

        # Picked HERE rather than trusted from the ORDER BY. The SQL still sorts
        # so the database does the work when it can, but correctness must not
        # depend on the order rows come back in — a driver, a query-plan change
        # or a partitioned scan reordering them would otherwise hand back a
        # DIFFERENT attempt's tree with nothing raising.
        newest = max(key(r) for r in rows)
        tree = [r for r in rows if key(r) == newest]
        return rebuild(tree, self._load_schemas(tree))

    def get_schemas(self, digests: list[str]) -> dict[str, dict]:
        """``schema_hash`` → ``{"columns": [...], "column_types": {...}}``.

        The public, digest-keyed face of :meth:`_load_schemas`, for
        ``SchemaDictionary`` (see ``data/schema_dictionary.py``). Batched for the
        same reason ``get_latest_many`` is: a descriptor averages ~8 artifacts,
        and one query per artifact would trade the saving for a round-trip.

        Misses are simply absent — the caller must treat that as "keep the
        inline copy", never as "this ref has no schema".
        """
        wanted = sorted({d for d in (digests or []) if d})
        if not wanted:
            return {}
        return self._load_schemas([{"SCHEMA_HASH": d} for d in wanted])

    def _load_schemas(self, rows: list[dict[str, Any]]) -> dict[str, dict]:
        """Fetch the ``AIW_SCHEMA_DEF`` entries these rows point at — one query.

        The dictionary is the reason a descriptor stops being 4,977 bytes on the
        row, so resolving it must not cost a query per artifact.
        """
        digests = sorted({r.get("SCHEMA_HASH") for r in rows if r.get("SCHEMA_HASH")})
        if not digests:
            return {}
        sql = _SELECT_SCHEMAS_SQL.format(
            placeholders=", ".join(f":p{i}" for i in range(len(digests))))
        out: dict[str, dict] = {}
        for row in self._db.execute_query(sql, tuple(digests)) or []:
            r = _lower_keys(row)
            try:
                out[r["SCHEMA_HASH"]] = json.loads(r["COLUMNS_JSON"] or "{}")
            except (TypeError, ValueError):
                logger.warning("undecodable schema row %s", r.get("SCHEMA_HASH"))
        return out

    def get_latest_many(
        self, run_id: str, node_refs: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Newest descriptor for SEVERAL nodes, in one query.

        ``GET /nodes/{id}/variable-suggestions`` walks every upstream node on
        each click. Today that costs **N×M** file-service round-trips —
        ``_fetch_columns_and_preview`` has 11 call sites and each is an HTTP
        ``query_file_data($top=5)``. Reading one node at a time here would
        merely trade N HTTP calls for N SQL ones; the point is to make it one.

        Returned keyed by the ref the CALLER asked for, so a mix of ids and
        names round-trips cleanly. A node with no output is simply absent —
        never a ``None`` entry, so ``.get()`` behaves like the ``variables``
        dict it replaces.
        """
        refs = [r for r in dict.fromkeys(node_refs or []) if r]
        if not refs:
            return {}
        ids = ", ".join(f":p{i + 1}" for i in range(len(refs)))
        names = ", ".join(f":p{i + 1 + len(refs)}" for i in range(len(refs)))
        sql = (
            f"SELECT {_SELECT_COLS} FROM {_TABLE} "
            f"WHERE RUN_ID = :p0 AND (NODE_ID IN ({ids}) OR NODE_NAME IN ({names})) "
            f"ORDER BY GENERATION DESC, ITERATION DESC, ATTEMPT DESC, ARTIFACT_SEQ ASC"
        )
        raw = self._db.execute_query(sql, (run_id, *refs, *refs))
        rows = [_lower_keys(r) for r in (raw or [])]
        if not rows:
            return {}

        schemas = self._load_schemas(rows)
        out: dict[str, dict[str, Any]] = {}
        for ref in refs:
            mine = [r for r in rows
                    if r.get("NODE_ID") == ref or r.get("NODE_NAME") == ref]
            if not mine:
                continue
            # Same winner rule as get_latest, and computed the same way, so the
            # two cannot disagree about which attempt is current.
            newest = max(
                (int(r.get("GENERATION") or 0), int(r.get("ITERATION") or 0),
                 int(r.get("ATTEMPT") or 0)) for r in mine
            )
            tree = rebuild(
                [r for r in mine
                 if (int(r.get("GENERATION") or 0), int(r.get("ITERATION") or 0),
                     int(r.get("ATTEMPT") or 0)) == newest],
                schemas,
            )
            if tree is not None:
                out[ref] = tree
        return out

    def list_iterations(
        self, run_id: str, node_ref: str,
    ) -> list[tuple[int, int, int, dict[str, Any]]]:
        """Every stored descriptor for a node, as ``(generation, iteration, attempt, ref)``.

        Ordered oldest-first so a caller can render iteration 0, 1, 2 … in
        order. ``GET /nodes/{id}/output`` shows a loop node's iterations
        side by side, which is the reason this exists alongside
        :meth:`get_latest`: today that history is only reconstructable by
        replaying one ``NodeOutputProduced`` event per iteration, whereas the
        table keeps them as rows.

        Grouped in Python for the same reason ``get_latest`` picks its winner
        there — the SQL ordering is an optimisation, and a scan that hands rows
        back in another order must not silently regroup artifacts under the
        wrong iteration.
        """
        raw = self._db.execute_query(_SELECT_LATEST_SQL, (run_id, node_ref, node_ref))
        rows = [_lower_keys(r) for r in (raw or [])]
        if not rows:
            return []

        grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
        for r in rows:
            key = (int(r.get("GENERATION") or 0), int(r.get("ITERATION") or 0),
                   int(r.get("ATTEMPT") or 0))
            grouped.setdefault(key, []).append(r)

        schemas = self._load_schemas(rows)
        out: list[tuple[int, int, int, dict[str, Any]]] = []
        for key in sorted(grouped):
            tree = rebuild(grouped[key], schemas)
            if tree is not None:
                out.append((*key, tree))
        return out

    def delete_run(self, run_id: str) -> None:
        """Drop every descriptor for a run. Schema rows are shared — never touched."""
        self._db.execute_write(f"DELETE FROM {_TABLE} WHERE RUN_ID = :p0", (run_id,))
