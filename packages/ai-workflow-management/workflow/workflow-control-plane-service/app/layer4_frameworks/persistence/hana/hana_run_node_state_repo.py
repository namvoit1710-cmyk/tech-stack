"""SAP HANA store for the run's per-node control state — ``AIW_RUN_NODE_STATE``.

Design + every measurement: ``solace/specs/wcp-node-output-storage-redesign.md``
§13. Table from migrations ``055`` / ``058``.

Why this exists
===============

The §13 audit of all 12 ``RunContext`` fields recorded ``node_visits`` and
``pending_revisits`` as *"covered by migration 055"*. That is true of the DDL and
false of the code: grepping the service for ``AIW_RUN_NODE_STATE`` returned only
doc comments, and the table holds **0 rows on tenant-1**. Neither field appears
anywhere in ``run_projection.py`` either, so neither rebuilds from
``AIW_EVENT_LOG``.

``DROP CONTEXT`` therefore destroys both, and neither fails loudly:

* **``node_visits``** — ``TaskFactory.create_task`` mints ``{run}-{node}-v{n}``
  from it (``task_factory.py:77``). Lose the counter and a cycle's second
  iteration re-mints ``v0``, so every event id derived from that task id
  collides with the first iteration's and is swallowed by the
  ``(run_id, event_id)`` dedupe. The run does not error; it silently skips work.
* **``pending_revisits``** — a deferred arrival that is dropped simply never
  fires. The node does not re-run and nothing reports it.

Same class of field as ``loop_state``, so it gets the same treatment.

What it does NOT try to be
==========================

Migration 055 describes this table as replacing five fields. Only two of them
are gates: ``completed_nodes``, ``skipped_nodes`` and ``last_output_ports`` all
rebuild from the event log, so writing them here would duplicate an authoritative
source and create a second thing to keep in sync. ``STATE`` and
``LAST_OUTPUT_PORT`` are therefore populated **best-effort, for queryability**,
never read back into the aggregate — they make "what happened to this node"
answerable in SQL without parsing an NCLOB, which is the other half of why the
table exists.

Shape notes
===========

Follows ``HanaLoopStateRepository``: ``IDatabaseConnection`` in the constructor,
``:p0`` placeholders, and delete-then-insert **of the whole run**, because
``node_visits`` and ``pending_revisits`` are dicts/sets the engine mutates as a
whole — a node dropping out of ``pending_revisits`` has to drop out of the table,
and a per-node upsert would leave it behind as a revisit that never drains.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_TABLE = "AIW_RUN_NODE_STATE"

_COLUMNS = (
    "RUN_ID", "NODE_ID", "STATE", "VISITS", "PENDING_REVISIT",
    "LAST_OUTPUT_PORT", "UPDATED_AT",
)

_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(f':p{i}' for i in range(len(_COLUMNS)))})"
)

_DELETE_RUN_SQL = f"DELETE FROM {_TABLE} WHERE RUN_ID = :p0"

_SELECT_RUN_SQL = (
    f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE RUN_ID = :p0"
)

#: ``STATE`` is NVARCHAR(24) and is written for queryability only.
_STATE_COMPLETED = "completed"
_STATE_SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upper_keys(row: Any) -> dict[str, Any]:
    """``execute_query`` lowercases column names."""
    if not isinstance(row, dict):
        return {}
    return {str(k).upper(): v for k, v in row.items()}


class HanaRunNodeStateRepository:
    """One row per node the run has touched."""

    def __init__(self, db: Any) -> None:
        """``db`` exposes ``execute_query(sql, params)`` / ``execute_write(...)``."""
        self._db = db

    # ── write ────────────────────────────────────────────────────────────

    def save_run(self, run_id: str, context: Any) -> int:
        """Replace every node row for a run. Returns rows written.

        Takes the whole ``RunContext`` rather than five arguments so the caller
        cannot pass four of them and silently lose the fifth — this table is
        assembled from fields that live in different places, and the failure mode
        of forgetting one is invisible.

        **Only the two gate fields earn a row.** A node is written when it has
        ``visits > 0`` or a pending revisit — not because it completed, was
        skipped, or has a last output port. Those three are event-sourced, so a
        row for them buys queryability at the cost of making this table hot:
        they are non-empty on essentially every run, whereas measured on
        prod-br10 / tenant-1:

            node_visits      empty on 6,103 of 6,117 runs
            pending_revisits empty on 6,115 of 6,117 runs

        So gating on the two gate fields makes the whole write a no-op for
        **99.8% of runs**, the same shape as the ``loop_state`` mirror. Letting
        ``completed_nodes`` open a row would have inverted that and put a
        delete-plus-N-inserts on every save — the write amplification this
        redesign exists to remove.

        ``STATE`` and ``LAST_OUTPUT_PORT`` are still populated for the nodes that
        DO earn a row: free context for whoever is reading the table to work out
        why a cycle stalled.
        """
        if not run_id or context is None:
            return 0
        self._db.execute_write(_DELETE_RUN_SQL, (run_id,))

        visits = dict(getattr(context, "node_visits", None) or {})
        revisits = set(getattr(context, "pending_revisits", None) or set())
        completed = set(getattr(context, "completed_nodes", None) or set())
        skipped = set(getattr(context, "skipped_nodes", None) or set())
        ports = dict(getattr(context, "last_output_ports", None) or {})

        node_ids = {n for n, c in visits.items() if int(c or 0)} | revisits
        if not node_ids:
            return 0

        stamped = _now()
        written = 0
        # Sorted so two saves of the same state produce the same row order —
        # a diff of the table between two runs should show real differences.
        for node_id in sorted(node_ids):
            state = (_STATE_SKIPPED if node_id in skipped
                     else _STATE_COMPLETED if node_id in completed else "")
            self._db.execute_write(
                _INSERT_SQL,
                (
                    run_id, str(node_id)[:128], state[:24],
                    int(visits.get(node_id, 0) or 0),
                    1 if node_id in revisits else 0,
                    (str(ports[node_id])[:64] if node_id in ports else None),
                    stamped,
                ),
            )
            written += 1
        return written

    # ── read ─────────────────────────────────────────────────────────────

    def get_run(self, run_id: str) -> dict[str, Any]:
        """``{"node_visits": {...}, "pending_revisits": {...}}``.

        Only the two fields that are NOT rebuildable from the event log are
        returned. ``STATE`` and ``LAST_OUTPUT_PORT`` are stored for queryability
        and deliberately not handed back — the projector is authoritative for
        those, and a second source would eventually disagree with it.

        A visit count of 0 is omitted rather than returned: ``node_visits`` is
        read with ``.get(node_id, 0)`` everywhere, so an explicit 0 is the same
        answer with a larger dict.
        """
        if not run_id:
            return {"node_visits": {}, "pending_revisits": set()}
        raw = self._db.execute_query(_SELECT_RUN_SQL, (run_id,)) or []
        visits: dict[str, int] = {}
        revisits: set[str] = set()
        for row in (_upper_keys(r) for r in raw):
            node_id = row.get("NODE_ID")
            if not node_id:
                continue
            node_id = str(node_id)
            count = int(row.get("VISITS") or 0)
            if count:
                visits[node_id] = count
            if int(row.get("PENDING_REVISIT") or 0):
                revisits.add(node_id)
        return {"node_visits": visits, "pending_revisits": revisits}

    def delete_run(self, run_id: str) -> None:
        """Drop every node row for a run."""
        if not run_id:
            return
        self._db.execute_write(_DELETE_RUN_SQL, (run_id,))
