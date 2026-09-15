"""SAP HANA store for ``run.context.loop_state`` — ``AIW_RUN_LOOP_STATE``.

Design + every measurement: ``solace/specs/wcp-node-output-storage-redesign.md``
§13 and §15. Tables from migrations ``056`` / ``057``.

Why this exists
===============

An audit of all 12 ``RunContext`` fields against ``ProjectedRunState`` found
exactly ONE that is neither event-sourced nor derivable from ``AIW_WORKFLOWS``:
``loop_state``. Everything else rebuilds — ``variables``, ``completed_nodes``,
``skipped_nodes``, ``child_runs`` and the port mirrors from ``AIW_EVENT_LOG``;
``topology`` from the workflow; ``node_visits`` / ``pending_revisits`` from
``AIW_RUN_NODE_STATE`` (migration 055); ``current_node`` by ALTER at cutover.
Grepping ``run_projection.py``, ``rebuild.py`` and ``run_context/`` for
``loop_state`` returns zero hits and ``IRunContextView`` has no property for it.

So this is the last gate on ``DROP CONTEXT``, and it is a **correctness** gate,
not a volume win: 23 loops across 6,060 runs, only 4 of the 200 heaviest runs
carry any ``loop_state`` at all.

Shape notes
===========

Follows ``HanaNodeOutputRepository``: ``IDatabaseConnection`` in the constructor,
``:p0`` placeholders, delete-then-insert because HANA has no portable
``ON CONFLICT``. Three things are specific to this table:

* **The unit of write is the RUN, not the loop.** ``run.context.loop_state`` is
  one dict the drivers mutate as a whole, and the handlers scan it as a whole.
  Writing one loop at a time would let a run's rows disagree with each other
  between two saves.
* **``SEQ`` is written and every read orders by it.** Reverse insertion order is
  how ``LOOP_CONTINUE`` / ``LOOP_EXIT`` find the innermost enclosing loop
  (``loop_continue_handler.py:137``). A table has no insertion order — see
  migration 057.
* **``to_row`` may refuse.** An oversized ``STATE_JSON`` raises
  ``LoopStateTooLarge`` — the cap is what keeps the payload off the row.
  ``save_run`` catches it per loop, skips that row and logs at WARNING: a shadow
  write must never fail a run, and must never lose a loop silently either. The
  driver's ``_claim_check_items`` is what normally keeps it from firing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.data.loop_state_rows import (
    LoopStateTooLarge,
    from_row,
    to_row,
)
from app.layer2_application.data.schema_dictionary import enforcing, expand

logger = logging.getLogger(__name__)

_TABLE = "AIW_RUN_LOOP_STATE"

_COLUMNS = (
    "RUN_ID", "NODE_ID", "INVOCATION_ID", "SEQ", "STATE_JSON",
    "ITEMS_REF", "RESULTS_REF", "PROGRESS_JSON", "TOTAL", "UPDATED_AT",
)

_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(f':p{i}' for i in range(len(_COLUMNS)))})"
)

_DELETE_RUN_SQL = f"DELETE FROM {_TABLE} WHERE RUN_ID = :p0"

_SELECT_RUN_SQL = (
    f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} "
    f"WHERE RUN_ID = :p0 ORDER BY SEQ ASC"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upper_keys(row: Any) -> dict[str, Any]:
    """``execute_query`` lowercases column names; ``to_row`` emits upper."""
    if not isinstance(row, dict):
        return {}
    return {str(k).upper(): v for k, v in row.items()}


class HanaLoopStateRepository:
    """One row per loop node per run."""

    def __init__(self, db: Any, schema_dictionary: Any = None) -> None:
        """``db`` exposes ``execute_query(sql, params)`` / ``execute_write(...)``.

        ``schema_dictionary`` is what makes the stored pointer stop scaling with
        the user's column count. A ref costs ``392 B + 40 B x columns``; with the
        schema replaced by the ``schema_hash`` that names it in
        ``AIW_SCHEMA_DEF`` it costs ``392 B + 0.2 B x columns`` — 93x smaller at
        2 000 columns, and near-constant. Optional, and the coupling is
        deliberately one-directional: **the row is only written thin when this
        can read it back**, because a hash whose entry is absent decodes with
        guessed types (``"007"`` → ``7``, the SA-1977 defect).
        """
        self._db = db
        self._schema_dictionary = schema_dictionary

    # ── write ────────────────────────────────────────────────────────────

    def save_run(self, run_id: str, loop_state: dict[str, Any]) -> int:
        """Replace every loop row for a run. Returns rows written.

        Delete-then-insert of the WHOLE run, not an upsert per loop: a loop that
        disappears from ``loop_state`` (``reset_test_run`` and
        ``test_full_workflow`` both call ``loop_state.clear()``) has to disappear
        from the table too, and a per-loop upsert would leave it behind as a
        loop that looks active forever.
        """
        if not run_id:
            return 0
        self._db.execute_write(_DELETE_RUN_SQL, (run_id,))
        if not isinstance(loop_state, dict) or not loop_state:
            return 0

        stamped = _now()
        written = 0
        # enumerate() over the dict — its order IS the semantics being preserved.
        thin = self._schema_dictionary is not None and enforcing()
        for seq, (node_id, state) in enumerate(loop_state.items()):
            try:
                row = to_row(state, thin=thin)
            except LoopStateTooLarge as exc:
                # Skip this loop, keep the rest of the run. Logged at WARNING
                # because it means the row is NOT a faithful copy — the thing a
                # shadow write exists to detect.
                logger.warning(
                    "loop_state row skipped for run=%s node=%s: %s",
                    run_id, node_id, exc,
                )
                continue
            if not row:
                continue
            self._db.execute_write(
                _INSERT_SQL,
                (
                    run_id, str(node_id)[:128],
                    str(state.get("invocation_id") or "")[:64] if isinstance(state, dict) else "",
                    int(seq), row["STATE_JSON"], row["ITEMS_REF"], row["RESULTS_REF"],
                    row["PROGRESS_JSON"], int(row["TOTAL"]), stamped,
                ),
            )
            written += 1
        return written

    # ── read ─────────────────────────────────────────────────────────────

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Rebuild a run's ``loop_state``, in its original insertion order.

        The payload pointers come back WHOLE — they ride ``PROGRESS_JSON``, so
        no file-service client is needed and none is used: a ref is the pointer
        AT a file, not the file. Rows written before that change carry only an
        id in the retired ``*_REF`` columns; those keys stay ABSENT rather than
        ``None``, because 20 call sites read ``loop_state.get("items")`` where
        ``None`` means "this loop has no items" and absent means "not loaded".

        A thin ref (schema replaced by its hash) is expanded here, once per
        load, before any consumer sees it — ``item_at`` → ``materialize_range``
        decodes rows through ``column_types``, and a ref that reaches it without
        them casts by guesswork.
        """
        if not run_id:
            return {}
        raw = self._db.execute_query(_SELECT_RUN_SQL, (run_id,)) or []
        out: dict[str, Any] = {}
        # Ordered in Python as well as in SQL, for the same reason
        # HanaNodeOutputRepository picks its winner in Python: the ORDER BY is an
        # optimisation, and correctness must not depend on the order a driver or
        # a query plan happens to return rows in. Here the cost of being wrong is
        # a BREAK applied to the wrong loop.
        rows = sorted((_upper_keys(r) for r in raw),
                      key=lambda r: int(r.get("SEQ") or 0))
        for row in rows:
            node_id = row.get("NODE_ID")
            if not node_id:
                continue
            out[str(node_id)] = self._expanded(from_row(row))
        return out

    def _expanded(self, state: dict[str, Any]) -> dict[str, Any]:
        """Restore any schema a thin ref left behind. In place, and never
        raises: a dictionary outage must degrade to "the ref keeps whatever it
        carries", never to a failed run load.

        ``schema_dictionary.expand`` skips a node that already has ``columns``,
        so this is a no-op on a fat ref — both shapes are in the table at once
        (rows are rewritten only when their run saves) and stay that way.
        """
        if self._schema_dictionary is None or not isinstance(state, dict):
            return state
        for key in ("items", "iteration_results"):
            value = state.get(key)
            if isinstance(value, dict) and value.get("__file_ref"):
                try:
                    expand(value, self._schema_dictionary)
                except Exception:  # noqa: BLE001 — see the docstring
                    logger.warning("loop_state schema expand failed for %s",
                                   key, exc_info=True)
        return state

    def delete_run(self, run_id: str) -> None:
        """Drop every loop row for a run."""
        if not run_id:
            return
        self._db.execute_write(_DELETE_RUN_SQL, (run_id,))
