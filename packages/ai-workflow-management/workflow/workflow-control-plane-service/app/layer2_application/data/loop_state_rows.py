"""``loop_state`` ⇄ ``AIW_RUN_LOOP_STATE`` — the last ``DROP CONTEXT`` gate.

Design + every measurement: ``solace/specs/wcp-node-output-storage-redesign.md``
§13 and §15. Pure functions, no database.

Why this is the gate
====================

Auditing all 12 ``RunContext`` fields against ``ProjectedRunState`` found exactly
one that is neither event-sourced nor derivable from ``AIW_WORKFLOWS``:
``loop_state``. ``variables``, ``completed_nodes``, ``skipped_nodes``,
``child_runs`` and the port mirrors all rebuild from ``AIW_EVENT_LOG``;
``topology`` rebuilds from the workflow; ``node_visits`` and
``pending_revisits`` moved to ``AIW_RUN_NODE_STATE`` in migration 055;
``current_node`` gets an ALTER at cutover. ``loop_state`` had nowhere to go, so
dropping ``CONTEXT`` would delete the only copy — 20 call sites across 7 files
read it, and a loop that loses it does not fail loudly, it hangs.

Why one row and not one per iteration
=====================================

The sealed design proposed a per-iteration table because ``completed_iterations``
looked unbounded. Every ``LoopStarted`` ever written to tenant-1 — **23 of them,
across 6,060 runs** — says otherwise: nesting depth **0** for all 23, ``total``
p50 **4** and max **100**, none unbounded, one loop node per run. That axis does
not exist, so a per-iteration table would serve a workload that has never
occurred.

What DOES vary is the SHAPE of the keys, and they fall into three classes with
three different homes:

===========  =========================================  ====================
class        keys                                       column
===========  =========================================  ====================
fixed        the 12 scalars (total, mode, condition,    STATE_JSON
             counter_variable, parallelism, …)          NVARCHAR(5000)
             — measured **426 B across four loops**
bounded      iter_progress, iter_failed_nodes,          PROGRESS_JSON
             completed_iterations, failed_iterations    NCLOB
             — **105 B**; iter_progress is pruned per
             iteration at loop_iteration_driver.py:424
             — but only since the int-key fix below;
             11/11 production runs show it never fired
payload      items (35%) + iteration_results (65%)      STATE_JSON when
             — **99.99% of every byte ever stored**     inline, else
                                                        PROGRESS_JSON
===========  =========================================  ====================

A note on class B, because building this mapper is what exposed it: those two
dicts are keyed by ITERATION INDEX — an ``int`` at runtime, a ``str`` after any
JSON round-trip. ``CONTEXT`` has been silently flipping the type on every reload
in production (11 of 11 runs measured), which stalls multi-node loop bodies and
makes the per-iteration prune a no-op. ``RunContext.__post_init__`` now
normalises it and ``from_row`` below inherits that, so the new table does not
recreate the bug in a new column. See ``normalize_loop_state_keys``.

``STATE_JSON``'s cap is the ENFORCEMENT, not a fit: class A measures ~107 B per
loop, so 5000 is 45× headroom, and re-inlining a payload key fails the write
loudly instead of silently restoring the megabytes. Same device as the
``OUTPUT_METADATA_V2`` cap — ``OUTPUT_METADATA`` was NCLOB, which is how 654 KB
of payload ended up in a column named *metadata*.

A payload key is a ``file_ref`` only sometimes, and the two shapes have
different bounds, so they get different homes.

An INLINE ``items`` is bounded by construction — ``item_at`` reads a pointer one
row at a time through OData, so claim-checking a 4-item loop would trade zero
HTTP calls for four and ``total`` is p50 **4** here; the driver therefore
claim-checks only past ~4 KB, a threshold derived from ``STATE_JSON`` itself, and
the cap is the backstop. It keeps ``STATE_JSON``. An inline ``iteration_results``
goes to ``PROGRESS_JSON``: its spill gate lets the window reach 256 KB.

A ``file_ref`` goes to ``PROGRESS_JSON`` **whole**. It used to be reduced to its
``file_id``, which cost the tenant every file-backed loop: nothing can restore a
ref from an id, because ``materialize_range`` decodes rows through
``column_types`` / ``value_kind`` — the fields the reduction dropped. And it must
not ride ``STATE_JSON``, because a ref costs ``392 B + 40 B x columns``
(measured over 614 live refs) and so overflows ``NVARCHAR(5000)`` at ~65 columns,
where ``save_run`` skips the row and the loop loses everything. ``thin`` then
removes the column term as well, by storing the ``schema_hash`` in place of the
schema it names.
"""

from __future__ import annotations

import json
from typing import Any

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.entities.workflow_run import normalize_loop_state_keys
from app.layer2_application.data.schema_dictionary import strip as _strip_schema

#: Class A — the fixed-size scalars the drivers write today. This tuple is
#: DOCUMENTATION, not a filter: ``to_row`` routes by exclusion, so a key added
#: to the drivers tomorrow lands in ``STATE_JSON`` and survives instead of being
#: silently dropped. If it turns out to be payload-shaped it trips the cap, which
#: is the loud failure this design wants.
SCALAR_KEYS = (
    "current_index", "total", "mode", "loop_type", "condition",
    "counter_variable", "next_dispatch_index", "invocation_id",
    "enclosing_scope_stack", "parallelism", "continue_requested",
    "break_requested", "iteration_result_chunks", "iteration_results_final",
)

#: Class B — bounded but user-scalable. ``PROGRESS_JSON``, deliberately NCLOB:
#: ``iter_progress`` is bounded by ``min(total, parallelism)`` and parallelism is
#: whatever the author typed, so capping it would reject a valid workload rather
#: than catch a bug.
PROGRESS_KEYS = (
    "iter_progress", "iter_failed_nodes",
    "completed_iterations", "failed_iterations",
)

#: Class C — the payload. Two homes, chosen by what bounds each shape:
#:
#: * an INLINE value goes to the column named in :data:`INLINE_PAYLOAD_HOME` —
#:   ``items`` to ``STATE_JSON`` (the driver claim-checks it above
#:   ``_LOOP_ITEMS_MAX_INLINE_BYTES``, ~4 KB derived from this very column, so
#:   what stays inline fits by construction), ``iteration_results`` to
#:   ``PROGRESS_JSON`` (its spill gate lets the window reach 256 KB).
#: * a ``file_ref`` goes to ``PROGRESS_JSON`` **whole** — see :func:`to_row`.
#:
#: The paired ``*_REF`` columns below are therefore RETIRED and always written
#: NULL. They are ``NVARCHAR(128)``: wide enough for an id, and an id is not a
#: pointer. Kept (rather than dropped) so this needs no migration; a later one
#: can remove them once no deployed row still carries a legacy id.
PAYLOAD_KEYS = {"items": "ITEMS_REF", "iteration_results": "RESULTS_REF"}

#: Where an inline (non-``file_ref``) payload value goes.
INLINE_PAYLOAD_HOME = {"items": "state", "iteration_results": "progress"}

#: Matches AIW_RUN_LOOP_STATE.STATE_JSON. Exceeding it means a payload key came
#: back inline, which is the thing this design exists to prevent.
STATE_JSON_MAX = 5000


class LoopStateTooLarge(ValueError):
    """``STATE_JSON`` would exceed its column. Loud on purpose.

    The cap is 45× the measured size of the scalars, so hitting it does not mean
    "a loop got big" — it means something that belongs in the file service is
    riding the row again.
    """


def to_row(loop_state: dict[str, Any], *, thin: bool = False) -> dict[str, Any]:
    """One ``loop_state`` entry → the columns of ``AIW_RUN_LOOP_STATE``.

    A ``file_ref`` payload is stored WHOLE, in ``PROGRESS_JSON``. It used to be
    reduced to ``value["file_id"]``, which is not a shortening but a loss:
    ``materialize_range`` decodes every row through ``column_types`` /
    ``value_kind`` / ``item_kind``, and those live on the ref, not in the file —
    one CSV decodes to three different values depending on the ref that names
    it. So the reduced form could not be restored by anything, and measurably
    was not: ``$loop.item`` came back ``None`` on every file-backed collection
    (tenant-1 runs ``a522ee2c`` / ``88661263`` / ``33f1818a``), which surfaces
    one node later as ``Cannot traverse '<field>' on NoneType``.

    ``PROGRESS_JSON`` and not ``STATE_JSON``, because a ref costs ``392 B +
    40 B x columns`` (measured over 614 live refs) and therefore overflows
    ``NVARCHAR(5000)`` at ~65 columns — at which point ``save_run`` skips the
    row and the loop loses ALL its state, which is worse than the bug. An inline
    list has no such tail: the claim-check bounds it at ~4 KB, so it keeps its
    existing home and the cap keeps meaning what it meant.

    ``thin`` replaces each ref's inline schema with the ``schema_hash`` that
    names it in ``AIW_SCHEMA_DEF``, which removes the column term entirely
    (~392 B + 0.2 B x columns — 93x smaller at 2 000 columns). Only pass it when
    the READ side can expand: a hash whose entry is absent decodes with guessed
    types, and ``"007"`` becoming ``7`` is the SA-1977 defect returning.
    ``schema_dictionary.strip`` declines on a ref that carries no hash.

    An oversized ``STATE_JSON`` still raises: the cap is the enforcement that
    keeps a payload from creeping back onto the row, and the driver's
    claim-check is what normally keeps it from firing.
    """
    if not isinstance(loop_state, dict):
        return {}
    scalars = {k: v for k, v in loop_state.items()
               if k not in PROGRESS_KEYS and k not in PAYLOAD_KEYS}
    progress = {k: loop_state[k] for k in PROGRESS_KEYS if k in loop_state}

    row: dict[str, Any] = {}
    for key, column in PAYLOAD_KEYS.items():
        row[column] = None      # retired — see PAYLOAD_KEYS
        value = loop_state.get(key)
        if not value:           # absent or empty
            continue
        if is_file_ref(value):
            progress[key] = _strip_schema(value) if thin else value
        elif INLINE_PAYLOAD_HOME[key] == "state":
            scalars[key] = value
        else:
            progress[key] = value

    row["STATE_JSON"] = _dumps(scalars)
    row["PROGRESS_JSON"] = _dumps(progress) if progress else None
    row["TOTAL"] = _int(loop_state.get("total"))

    if len(row["STATE_JSON"].encode("utf-8")) > STATE_JSON_MAX:
        raise LoopStateTooLarge(
            f"STATE_JSON is {len(row['STATE_JSON'])} chars, over {STATE_JSON_MAX}. "
            f"The scalars measure ~107 B in production, so this means a payload "
            f"key is inline again. Keys present: {sorted(scalars)}"
        )
    return row


def from_row(row: dict[str, Any], *, items: Any = None,
             iteration_results: Any = None) -> dict[str, Any]:
    """Columns → the ``loop_state`` dict the drivers mutate in place.

    ``items`` / ``iteration_results`` are passed back in by the caller, which
    owns resolving ``ITEMS_REF`` / ``RESULTS_REF`` — this module does no I/O.
    Omitted ⇒ the key is absent rather than ``None``: 20 call sites do
    ``loop_state.get(...)`` and a ``None`` there reads as "the loop has no
    items", which is a different thing from "not loaded".
    """
    out: dict[str, Any] = {}
    out.update(_loads(row.get("STATE_JSON")))
    out.update(_loads(row.get("PROGRESS_JSON")))
    if items is not None:
        out["items"] = items
    if iteration_results is not None:
        out["iteration_results"] = iteration_results
    # PROGRESS_JSON is JSON, so it loses int iteration keys exactly the way
    # CONTEXT did. Moving loop_state to its own table does not fix that on its
    # own — normalising here is what stops the new home inheriting the old bug.
    return normalize_loop_state_keys({"_": out})["_"]


def _int(value: Any) -> int:
    """``TOTAL`` is a convenience mirror of a JSON field, not the source of
    truth — ``STATE_JSON`` still carries whatever the runtime actually wrote.
    So a malformed ``total`` degrades to 0 instead of failing the write and
    costing the run its whole loop state."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _dumps(value: Any) -> str:
    # sort_keys so a row does not churn just because a dict was rebuilt in a
    # different order — the same reason schema_hash sorts.
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _loads(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
