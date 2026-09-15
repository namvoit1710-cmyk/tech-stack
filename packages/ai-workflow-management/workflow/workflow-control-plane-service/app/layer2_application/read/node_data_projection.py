"""Pure helpers for projecting a ``__file_ref`` tree into the read-side
descriptor shape returned by ``GET /data``, plus small utilities reused
across ``get_node_schema`` / ``get_node_data`` / ``get_run_artifacts``.

Scope
-----

- ``build_descriptor_tree(file_ref)`` — recursive walk producing the
  ``ArtifactDescriptor`` dict expected by the FE (identity only; NO
  ``row_count``, NO inline rows).
- ``synthesize_data_edit_schema(node, columns)`` — build the DATA_EDIT
  auto-form schema from a column list. Used by ``DataEditSchemaProvider``
  for runtime schema synthesis.
- ``compute_node_state(run, node_id, task)`` — maps a node to one of the
  public states (``PENDING``, ``SUBMITTED``, ``COMPLETED``, ``SKIPPED``,
  ``RUNNING``, ``FAILED``, ``PENDING_RERUN``).
- ``find_latest_task_for_node(task_repo, run_id, node_id)`` — "latest" =
  max created_at (same rule the old ``get_node_input_data`` used).

Note: the former ``resolve_schema`` free function was retired in the
node-handler ABC refactor. Schema resolution now lives on each
handler's :class:`SchemaProvider` (see
``app/layer2_application/node_handlers/schema_providers.py``).

All helpers are pure (no I/O beyond the injected repo).
"""

from __future__ import annotations

from typing import Any, Optional

from app.layer1_domain.entities.data_reference import (
    ROOT_ARTIFACT_ID,
    is_file_ref,
    walk_file_ref,
)
from app.layer2_application.orchestration.successor_processor import PENDING_NODE_TYPES
from app.layer1_domain.auto_form_mapper.helpers import (
    odata_array_field,
    text_field,
)


# ---------------------------------------------------------------------------
# Descriptor tree
# ---------------------------------------------------------------------------

def build_descriptor_tree(file_ref: Any) -> Optional[dict[str, Any]]:
    """Return the recursive descriptor dict for a ``__file_ref``, or None.

    Shape (matches the FE contract — see ``docs/design/node-input-api-reference.md``
    §5.1 ``ArtifactDescriptor``)::

        {
          "artifact_id": "__root__" | "orders" | "orders.line_items" | ...,
          "file_id": "f_main_abc",
          "columns": ["...", ...],       # includes __row_id; __parent_row_id on nested
          "parent_row_key_column": "__parent_row_id" | None,
          "children": [ ...recursive... ],
          "artifact_type": "array_field" | "nested_array_field" | None,
          "content_type": "text/csv" | None,
        }

    Non-file_ref inputs return ``None`` so callers can signal "no data".
    """
    if not is_file_ref(file_ref):
        return None

    def _node(ref: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": ref.get("artifact_id", ROOT_ARTIFACT_ID),
            "file_id": ref.get("file_id", ""),
            "columns": list(ref.get("columns") or []),
            "parent_row_key_column": ref.get("parent_row_key_column"),
            "children": [_child(c) for c in ref.get("artifacts") or []],
            "artifact_type": ref.get("artifact_type"),
            "content_type": ref.get("content_type"),
            # Preview rows are populated post-walk by the use case (it has the
            # file client). The placeholder here keeps the descriptor shape
            # uniform and lets the use case attach in-place without recreating
            # the dict.
            "preview_rows": [],
        }

    def _child(art: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": art.get("artifact_id") or art.get("name", ""),
            "file_id": art.get("file_id", ""),
            "columns": list(art.get("columns") or []),
            "parent_row_key_column": art.get("parent_row_key_column"),
            "children": [_child(c) for c in art.get("artifacts") or []],
            "artifact_type": art.get("artifact_type"),
            "content_type": art.get("content_type"),
            "preview_rows": [],
        }

    return _node(file_ref)


# Envelope keys ``complete_task`` wraps around every worker output. They carry
# the output reference + raw metadata, not the worker's own fields, so the
# inline Output panel hides them to show ``result`` / ``logs`` / ... directly.
_INLINE_ENVELOPE_KEYS = frozenset({"output", "metadata"})


def build_inline_descriptor(data: Any) -> Optional[dict[str, Any]]:
    """Return a root descriptor for an INLINE (non-file-backed) node output.

    Inline outputs (code nodes — see ``keeps_inline_output``) keep their
    original Python values instead of a ``__file_ref`` CSV pointer. The Output
    panel still expects the descriptor shape, so we synthesise a single-node
    tree whose ``preview_rows`` carries the data verbatim: arrays and objects
    stay intact (real JSON), never re-stringified or split into artifact
    ``file_id`` pointers. ``file_id`` is empty so the use case leaves these
    preview rows untouched (it only fetches previews for refs with a file_id).

    Non-dict input returns ``None`` so callers signal "no data".
    """
    if not isinstance(data, dict):
        return None
    visible = {k: v for k, v in data.items() if k not in _INLINE_ENVELOPE_KEYS}
    # Split file-backed fields out of the inline row. A field that is itself a
    # ``__file_ref`` (e.g. a code node's ``result`` array the worker streamed to
    # CSV — SA-1905 C2) becomes a file-backed CHILD descriptor so the data grid
    # renders it as a sub-table (the use case fetches its ``preview_rows``).
    # Leaving it in ``preview_rows`` would hand the FE a raw pointer object it
    # tries — and fails — to parse as an inline array.
    children: list[dict[str, Any]] = []
    inline_row: dict[str, Any] = {}
    for key, value in visible.items():
        if is_file_ref(value):
            child = build_descriptor_tree(value)
            if child is not None:
                child["artifact_id"] = key
                children.append(child)
                continue
        inline_row[key] = value
    return {
        "artifact_id": ROOT_ARTIFACT_ID,
        "file_id": "",
        "columns": list(inline_row.keys()),
        "parent_row_key_column": None,
        "children": children,
        "artifact_type": None,
        "content_type": "application/json",
        "preview_rows": [inline_row] if inline_row else [],
        "inline": True,
    }


def descendant_file_ids(file_ref: Any) -> list[str]:
    """Return every file_id in the tree, root first, depth-first, unsorted."""
    ids: list[str] = []
    if not is_file_ref(file_ref):
        return ids

    def _visit(node: dict[str, Any], _depth: int) -> None:
        fid = node.get("file_id", "")
        if fid:
            ids.append(fid)

    walk_file_ref(file_ref, _visit)
    return ids


def collect_file_ids(value: Any) -> list[str]:
    """Every file_id reachable in an arbitrary value tree, de-duplicated in
    first-seen order.

    Walks dicts/lists to any depth; at each ``__file_ref`` it collects that
    ref's whole subtree (main + nested artifacts) via ``descendant_file_ids``
    and does NOT recurse into the ref's metadata. Used by the run-retention
    path to delete a discarded run's file-backed outputs (SA-1905 E) — the run
    variables nest file_refs at varying depths (``$gen.result``,
    ``$out.output.du_lieu``, …), so a plain top-level check would miss most.
    """
    seen: dict[str, None] = {}   # insertion-ordered set
    visited: set[int] = set()    # cycle guard for shared/self-referential refs
    stack = [value]
    while stack:
        node = stack.pop()
        if is_file_ref(node):
            for fid in descendant_file_ids(node):
                if fid:
                    seen.setdefault(fid, None)
            continue
        if isinstance(node, (dict, list)):
            # Guard against reference cycles (shared/self-referential in-memory
            # structures) — without this the walk could loop forever. ids stay
            # valid because the stack holds live references throughout.
            if id(node) in visited:
                continue
            visited.add(id(node))
            stack.extend(node.values() if isinstance(node, dict) else node)
    return list(seen.keys())


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------

def columns_from_working_ref(file_ref: Any) -> Optional[list[str]]:
    """Return the live column list from the first nested artifact, or None.

    Reads ``file_ref.artifacts[0].columns`` — DATA_EDIT today always seeds
    a single ``table_data`` array under ``__root__``. Returns ``None`` when
    the ref isn't a file_ref or has no artifacts so callers can fall back
    to cached schemas (vs. ``[]`` which would mean "schema with zero
    user columns" — a valid post-PATCH state).
    """
    if not is_file_ref(file_ref):
        return None
    artifacts = file_ref.get("artifacts") or []
    if not artifacts:
        return None
    return list(artifacts[0].get("columns") or [])


def resolve_selection_mode(
    explicit: Optional[str],
    config_value: Any = None,
    *,
    readonly: bool = False,
) -> Optional[str]:
    """Resolve the DATA_EDIT row-selection mode to emit, or ``None`` to omit.

    Shared by the dispatch-time builder (``data_edit_handler._build_dynamic_
    table_schema``) and the runtime builder (``synthesize_data_edit_schema``)
    so the two schemas can never drift — a drift between them was the original
    SA-1239 bug (dispatch emitted no ``selectionMode`` at all).

    Precedence: an ``explicit`` value (runtime arg / already-merged config)
    wins, then the raw ``node.config`` value, then the readonly default —
    approval "Input Requested" tables select a single row → radio. Only the
    two FE-understood enum values are ever returned; ``"none"`` (the designer
    opted out) and any junk/legacy value collapse to ``None`` so wrapperProps
    carries no ``selectionMode``.
    """
    resolved = explicit or config_value or ("single" if readonly else None)
    return resolved if resolved in ("single", "multiple") else None


def synthesize_data_edit_schema(
    node: Any,
    columns: list[str],
    *,
    readonly: bool = False,
    custom_data_update: bool = False,
    actions: Optional[list[dict[str, Any]]] = None,
    extra_settings: Optional[list[dict[str, Any]]] = None,
    instruction: Optional[str] = None,
    query_params: Optional[dict[str, Any]] = None,
    selection_mode: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Build the DATA_EDIT auto-form schema from a column list, no file IO.

    Mirrors the body of ``data_edit_handler._build_dynamic_table_schema``
    minus the ``controlProps.value`` default-row baking — rows come from
    OData on the FE side, not the schema. Skips reserved columns
    (``__row_id``, ``__parent_row_id``) and any leading-underscore field.

    The render-config keyword args carry the palette's Tab-2 settings —
    the same values the handler stamped onto ``task.output_metadata`` at
    dispatch. Honoring them here keeps a runtime ``GET /schema`` byte-for-
    byte aligned with the dispatch-time schema instead of silently dropping
    the configurable action buttons, settings, readonly state, and the
    custom-update table key:

    * ``readonly`` — every cell renders read-only and the table-level
      ``wrapperProps`` carries ``readonly: true`` (no add/delete affordance).
      Otherwise the table gets ``isAdd``/``isDelete``.
    * ``custom_data_update`` — prefixes the table key with ``__`` so the FE
      routes row edits through a bespoke updater instead of the default
      OData mutator.
    * ``actions`` — injected at ``wrapperProps.actions`` (kept verbatim; the
      FE owns the button semantics).
    * ``extra_settings`` — appended as siblings of the row template inside
      the table's ``fields`` array, matching the runtime shape the FE expects.
    * ``instruction`` — overrides the table label.
    * ``query_params`` — the parsed OData query (``$filter``/``$orderby``/…);
      emitted at ``wrapperProps.query`` so the FE seeds its row fetch with the
      author's filter instead of pulling the full dataset.
    """
    user_columns = [c for c in (columns or []) if c and not c.startswith("_")]
    # Honor the OData ``$select``: the working ref tracks every file column,
    # but the author asked for these specifically — restrict to them (in
    # $select order) so runtime GET /schema matches the dispatch-time schema
    # instead of widening to every field.
    select = (query_params or {}).get("$select")
    if select:
        present = set(user_columns)
        user_columns = [
            c for c in (s.strip() for s in str(select).split(","))
            if c and c in present
        ]
    if not user_columns:
        return []
    cols = [
        text_field(key=c, label=c, readonly=True if readonly else None)
        for c in user_columns
    ]
    node_cfg = getattr(node, "config", None) or {}
    label = (
        instruction
        or node_cfg.get("instruction")
        or getattr(node, "name", None)
        or "Data Table"
    )
    table_key = "__table_data" if custom_data_update else "table_data"
    table = odata_array_field(key=table_key, label=label, columns=cols)
    table_dict = table.to_dict()

    wrapper_props = table_dict.setdefault("fieldConfig", {}).setdefault(
        "wrapperProps", {}
    )
    if readonly:
        wrapper_props["readonly"] = True
    else:
        wrapper_props["isAdd"] = True
        wrapper_props["isDelete"] = True

    # SA-1239: the per-row three-dots "More Action" menu is no longer schema-flagged —
    # the FE gates it purely on row-more-action slot presence (+ contextReadonly), so the
    # backend emits no isShowRowMoreAction.
    # Single ⇄ multiple selection toggle. Readonly (approval "Input Requested") selects a
    # single row by default → radio; editable tables keep the historic FE multi-select
    # (no prop emitted) for backward compatibility. An explicit arg/config value wins.
    # The designer can also pick "none" to force NO row selection — that explicitly
    # suppresses the prop (and overrides the readonly→single default), so wrapperProps
    # carries no selectionMode at all. Shared with the dispatch builder via
    # resolve_selection_mode so the two paths never drift. NOTE: the node.config fallback
    # here reads node.config only (not input_binding); post-fix dispatch always stamps the
    # merged value into output_metadata and passes it as ``selection_mode``, which wins.
    resolved_selection = resolve_selection_mode(
        selection_mode, node_cfg.get("selectionMode"), readonly=readonly,
    )
    if resolved_selection:
        wrapper_props["selectionMode"] = resolved_selection

    if actions:
        wrapper_props["actions"] = actions
    # Mirror the dispatch-time builder: surface the configured OData query so a
    # runtime GET /schema keeps the author's filter instead of dropping it.
    if query_params:
        wrapper_props["query"] = dict(query_params)
    if extra_settings:
        table_dict.setdefault("fields", []).extend(extra_settings)

    return [table_dict]


# ---------------------------------------------------------------------------
# Node state
# ---------------------------------------------------------------------------

# Derived once at import time from the canonical enum-based set in
# ``successor_processor.PENDING_NODE_TYPES``. Callers may hand us either an
# enum member or a bare string, so we build the string mirror here and
# normalise each input via ``.value``-or-self at call time.
_PENDING_NODE_TYPE_VALUES: frozenset[str] = frozenset(
    getattr(t, "value", str(t)) for t in PENDING_NODE_TYPES
)


def is_pending_node_type(node_type: Any) -> bool:
    """True when the node kind is one of the user-waiting types."""
    value = getattr(node_type, "value", node_type)
    return str(value) in _PENDING_NODE_TYPE_VALUES


def compute_node_state(source: Any, node_id: str, task: Any) -> str:
    """Return a coarse node state string for the FE.

    Rules (checked in order):
      - skipped_nodes        → "SKIPPED"
      - latest task FAILED    → "FAILED"
      - completed_nodes      → "COMPLETED"
      - task exists and task.status is a useful hint → map via ``_task_status_to_state``
      - nothing has happened yet                     → "PENDING"

    The FAILED check precedes ``completed_nodes`` on purpose: the projector
    folds ``NodeFailed`` into ``completed_nodes`` (it drives successor
    routing the same as a completion), so a failed node IS a member of
    ``completed_nodes``. Trusting the latest task's terminal status first
    keeps a failed node reporting "FAILED" — matching
    ``aggregate_node_status`` rather than masquerading as "COMPLETED".

    Accepts either a ``WorkflowRun`` aggregate (with
    ``run.context.{skipped,completed}_nodes``) or an ``IRunContextView``
    (top-level ``skipped_nodes`` / ``completed_nodes``). The view is
    authoritative post-Phase 3b.2a.
    """
    skipped = getattr(source, "skipped_nodes", None)
    completed = getattr(source, "completed_nodes", None)
    if skipped is None or completed is None:
        context = getattr(source, "context", None)
        if skipped is None:
            skipped = getattr(context, "skipped_nodes", None)
        if completed is None:
            completed = getattr(context, "completed_nodes", None)
    skipped = skipped or set()
    completed = completed or set()

    if node_id in skipped:
        return "SKIPPED"
    status = getattr(task, "status", None) if task is not None else None
    status_value = str(getattr(status, "value", status)).upper() if status is not None else ""
    if status_value in {"FAILED", "ERROR"}:
        return "FAILED"
    if node_id in completed:
        return "COMPLETED"
    if task is None:
        return "PENDING"
    return _task_status_to_state(status)


def _task_status_to_state(status: Any) -> str:
    """Map a TaskStatus enum (or string) to the FE-facing node state."""
    value = getattr(status, "value", status)
    s = str(value).upper() if value is not None else ""
    if s in {"PENDING", "PENDING_RERUN"}:
        return s
    if s == "RUNNING":
        return "RUNNING"
    if s == "COMPLETED":
        return "COMPLETED"
    if s in {"FAILED", "ERROR"}:
        return "FAILED"
    if s == "SUBMITTED":
        return "SUBMITTED"
    if s in {"SKIPPED", "CANCELLED"}:
        return "SKIPPED"
    return "PENDING"


# ---------------------------------------------------------------------------
# Task selection
# ---------------------------------------------------------------------------

def find_latest_task_for_node(
    tasks: list[Any],
    node_id: str,
) -> Optional[Any]:
    """Return the current-generation task for ``node_id``, or None.

    A permanent test run accumulates one task row per cycle (reset paths
    CANCEL, never delete; the ``-v{n}`` visit suffix keeps growing), so a plain
    ``max(created_at)`` would fall back to a SUPERSEDED cycle's row whenever the
    current cycle didn't re-dispatch this node — serving stale ``resolved_input``
    to the input panel.

    Scope to the run's current generation (``run_generation`` — the authoritative
    monotonic discriminator the row already carries) instead of trusting a string
    timestamp. ``created_at`` is only the tiebreak WITHIN that generation.

    If the node has no task in the current generation it did not run this cycle.
    Two sub-cases, told apart by task STATUS (generation alone can't):
      - full reset (test_full / reset_test_run) CANCELS every prior task → no
        live row survives → None (honest empty panel — the original fix).
      - partial rerun (restart-from-failure) leaves preserved upstream tasks
        COMPLETED → fall back to the latest non-cancelled prior row so the
        upstream input panel still resolves instead of blanking.
    """
    rows = tasks or []
    matching = [t for t in rows if getattr(t, "node_id", "") == node_id]
    if not matching:
        return None
    # ponytail: current generation = newest gen that produced ANY task this run,
    # a proxy for run.generation (the read path doesn't carry it). Upgrade to a
    # projected view.generation only if this proxy ever misleads.
    current_gen = max((getattr(t, "run_generation", 0) or 0) for t in rows)
    in_gen = [
        t for t in matching
        if (getattr(t, "run_generation", 0) or 0) == current_gen
    ]
    if in_gen:
        return max(in_gen, key=lambda t: getattr(t, "created_at", "") or "")
    live = [t for t in matching if _status_str(t) != "CANCELLED"]
    if not live:
        return None
    return max(live, key=lambda t: getattr(t, "created_at", "") or "")


def _status_str(task: Any) -> str:
    """Task status as a plain string, accepting either an enum or a raw str."""
    status = getattr(task, "status", "")
    return getattr(status, "value", status) or ""
