"""SA-1977 — the root kind of a node value: declared once, never re-inferred.

The CSV serialization boundary throws away the root type of every node output.
Five readers then each guess it back — from the row count, from the absence of
artifacts, from an ``isinstance`` after a full download, from an assumption that
it is a list. Every bug in that family is two of those guesses disagreeing
(``KeyError: 0``; a 1-element list that reads back as an object; ``[]`` and
``{}`` collapsing to the same bytes).

This module is the single answer. The vocabulary is deliberately **the root of a
JSON-Schema descriptor** (``type`` ∈ array|object|string|number|boolean|null,
plus ``items.type`` for arrays) rather than a private 3-value enum, so that when
a full response schema is persisted later the two cannot drift: they are the
same field.

``UNKNOWN`` is not a kind — it means *"this reference predates the contract"*.
Each reader decides explicitly what to do with a legacy value (see ``kind_of``);
none of them silently pretend to know.
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.data_reference import is_file_ref

ARRAY = "array"
OBJECT = "object"
STRING = "string"
NUMBER = "number"
BOOLEAN = "boolean"
NULL = "null"

# Not a kind — the absence of a declaration (a ref written before SA-1977).
UNKNOWN = "unknown"

SCALAR_KINDS = frozenset({STRING, NUMBER, BOOLEAN, NULL})
KINDS = frozenset({ARRAY, OBJECT}) | SCALAR_KINDS

# Reserved column holding the value of a scalar / list-of-scalar root (A4).
# Joins the existing reserved namespace (``__row_id``, ``__parent_row_id``),
# which is why supporting these shapes needs no storage-format change: a
# ``list[scalar]`` is still N CSV rows, so it stays OData-pageable, lazily
# row-readable, and steward-editable.
VALUE_COLUMN = "__value"

# The element kinds an ARRAY stores in ``__value``: everything a CSV row cannot
# be. ``object`` is absent (a list of objects IS a table of columns), and so is
# the empty string — an undeclared element kind must never be read as "unwrap".
_VALUE_COLUMN_ITEM_KINDS = SCALAR_KINDS | {ARRAY, UNKNOWN}


def kind_of_inline(value: Any) -> str:
    """The kind of an *inline* Python value — ``isinstance``, free and exact.

    An inline value never needs a stored kind; the object itself is the truth.
    Callers holding a value that may be a file_ref must use :func:`kind_of`,
    which checks the marker first (a file_ref is, structurally, a ``dict``).
    """
    if value is None:
        return NULL
    if isinstance(value, bool):  # before int — bool is an int subclass
        return BOOLEAN
    if isinstance(value, (int, float)):
        return NUMBER
    if isinstance(value, str):
        return STRING
    if isinstance(value, (list, tuple)):
        return ARRAY
    if isinstance(value, dict):
        return OBJECT
    # Exotic values (datetime, Decimal, …) are stringified by the writer
    # (``json.dumps(default=str)``), so ``string`` is what round-trips.
    return STRING


def item_kind_of_inline(values: Any) -> str:
    """The element kind of a list: the one kind every element shares.

    ``UNKNOWN`` for an empty list (nothing to observe) or a genuinely mixed one.

    **``object`` is returned only when EVERY element is a dict** — and that is a
    load-bearing asymmetry, not a wart. ``object`` is the one element kind that
    means *"the rows ARE the columns"*, i.e. a real CSV table; every other kind
    is stored in the reserved ``__value`` column. So ``item_kind == object`` is
    exactly the storage decision, and the two must be the same rule or they will
    disagree — as they did:

        [{"id": 1}, None, {"id": 3}]        # a sparse array — ordinary API data

    …stored in ``__value`` (an element that is not a dict), yet declared
    ``array<object>`` (a rule that let nulls abstain). The reader believed the
    declaration, did not unwrap, and handed back
    ``[{"__value": {...}}, {"__value": None}, ...]`` — a phantom key on every
    element, and ``$loop.item.id`` resolving to nothing.

    A ``null`` element still abstains for every OTHER kind: ``[1, None, 2]`` is
    ``array<number>``, which is what a sparse column of numbers should be, and it
    is stored in ``__value`` either way — so nothing can disagree.
    """
    if not isinstance(values, (list, tuple)):
        return UNKNOWN
    if not values:
        return UNKNOWN
    if all(isinstance(v, dict) for v in values):
        return OBJECT
    kinds = {kind_of_inline(v) for v in values} - {NULL}
    if not kinds:
        return NULL  # every element was null
    if len(kinds) == 1 and OBJECT not in kinds:
        return next(iter(kinds))
    return UNKNOWN  # mixed — including objects mixed with anything else


def kind_of(value: Any) -> str:
    """The root kind of *value*, whether it is inline or file-backed.

    * **file_ref** → the declared ``value_kind``. ``UNKNOWN`` when the ref
      predates SA-1977; the caller decides what a legacy ref means *in its own
      terms* (``materialize`` falls back to the row-count heuristic so old data
      reads exactly as before; a LOOP reads a legacy table as an array of its
      rows). The fallback is per-site and written down — never a hidden guess.
    * **inline** → ``isinstance``.

    This is the whole contract. A consumer that branches on ``is_file_ref`` to
    decide *how to ask* has rebuilt the defect this module exists to remove.
    """
    if is_file_ref(value):
        declared = value.get("value_kind")
        return declared if declared in KINDS else UNKNOWN
    return kind_of_inline(value)


def item_kind_of(value: Any) -> str:
    """The element kind of an array *value* (inline or file-backed)."""
    if is_file_ref(value):
        declared = value.get("item_kind")
        return declared if declared in KINDS else UNKNOWN
    return item_kind_of_inline(value)


def declared_kind(node: Any) -> str:
    """The kind declared on a NODE OF A REF TREE — a root file_ref *or* one of
    its artifact dicts.

    Not the same question as :func:`kind_of`, and the difference bites: an
    artifact dict (built by ``add_artifact``) carries no ``__file_ref`` marker,
    so ``kind_of`` would fall through to ``isinstance`` and answer ``object`` —
    for a child table of N rows. It is a *node*, not a *value*; ask it what it
    declares, and take ``UNKNOWN`` when it declares nothing.
    """
    if not isinstance(node, dict):
        return UNKNOWN
    d = node.get("value_kind")
    return d if d in KINDS else UNKNOWN


def declared_item_kind(node: Any) -> str:
    """The element kind a ref-tree node declares — VERBATIM, ``""`` if it says
    nothing.

    Deliberately NOT :func:`item_kind_of`, which normalises "undeclared" to
    ``UNKNOWN`` — and ``UNKNOWN`` is a real, load-bearing declaration here: a
    *mixed* list genuinely declares ``item_kind: "unknown"`` and IS stored in the
    ``__value`` column. So the two are not interchangeable for a STORAGE
    decision::

        ref = build_file_ref("f", value_kind="array")   # item_kind == ""
        uses_value_column("array", "")                    -> False   # correct
        uses_value_column(kind_of(ref), item_kind_of(ref)) -> True   # WRONG

    That is the same rule written twice and disagreeing — the defect this module
    exists to remove. Storage decisions go through :func:`ref_uses_value_column`,
    which is the only expression of it.
    """
    if not isinstance(node, dict):
        return ""
    return str(node.get("item_kind") or "")


def ref_uses_value_column(node: Any) -> bool:
    """Does THIS ref-tree node store its value in the reserved ``__value`` column?

    The single expression of the rule, for every reader. Callers must not
    re-assemble it from ``kind_of``/``item_kind_of`` — see
    :func:`declared_item_kind` for what happens when they do.
    """
    return uses_value_column(declared_kind(node), declared_item_kind(node))


def is_collection(value: Any) -> bool:
    """True when *value* IS (or points at) an array — **without downloading it**.

    The predicate a LOOP needs. ``_coerce_collection`` used to answer this by
    calling ``materialize()`` purely to run ``isinstance`` on the result, paying
    the exact OOM the lazy path exists to avoid.
    """
    return kind_of(value) == ARRAY


def reconcile_after_edit(ref: dict[str, Any], row_count: int) -> None:
    """Keep a ref's DECLARATION true after a steward edits its rows (A6).

    The declared kind is now load-bearing — every reader trusts it — so the one
    thing that must never happen is for an edit to falsify it. Row editing is not
    an edge case here: it is the reason the CSV format was chosen in the first
    place (tabular steward editing, cell/col/row + OData).

    A ``1``-row file declared ``object`` that grows a second row is no longer an
    object. Rather than refuse a legitimate edit, **promote** it: the value
    becomes an ``array`` of what it used to be, versioned like any other edit.
    An ``array`` is never demoted — an array of one is still an array, and that
    is precisely the distinction this Story exists to preserve.

    Mutates *ref* in place; also refreshes ``row_count``, which the edit paths
    historically left stale.
    """
    if not isinstance(ref, dict):
        return
    ref["row_count"] = int(row_count)
    kind = ref.get("value_kind")
    if kind in ("", None, UNKNOWN, ARRAY):
        return  # legacy (nothing to falsify) or already an array (never demote)
    if row_count > 1:
        ref["item_kind"] = kind
        ref["value_kind"] = ARRAY


def uses_value_column(value_kind: str, item_kind: str = "") -> bool:
    """True when a value of this kind is STORED in the reserved ``__value`` column.

    This must be answered from the DECLARATION, never from the shape of the
    stored rows. Sniffing the columns instead — *"the only column is ``__value``,
    so it must be a scalar wrapper"* — silently destroys a perfectly ordinary
    node output that happens to be a list of one-key dicts::

        [{"__value": 1}, {"__value": 2}]   ->   [1, 2]        # dicts gone

    …and it does so while the ref sits there declaring ``item_kind: "object"``.
    Re-deriving from the bytes what the writer already wrote down IS the bug this
    module exists to remove; it does not stop being the bug when the new code
    does it.

    ``__value`` is written by ``DataStoreService._store_values``, and only for a
    scalar root or a list whose elements are not all dicts — which is exactly
    what these two declared fields say.

    An array must name its element kind EXPLICITLY to be unwrapped. "Anything
    that isn't ``object``" would be the wrong test: an EMPTY list has no element
    kind to observe, so it declares none — and if rows were later added to that
    ref (a steward edit), a not-object test would start unwrapping real dicts.
    Absence of a declaration is not a declaration.
    """
    if value_kind in SCALAR_KINDS:
        return True
    return value_kind == ARRAY and item_kind in _VALUE_COLUMN_ITEM_KINDS


def empty_for_kind(kind: str) -> Any:
    """The empty value of *kind* — what a 0-row file materializes to.

    This is the whole of the ``[]``-vs-``{}`` fix: both stored ``b""`` and both
    read back as ``{}``, because the bytes could not say which they were.
    """
    if kind == ARRAY:
        return []
    if kind == OBJECT:
        return {}
    if kind in SCALAR_KINDS:
        return None
    return {}  # UNKNOWN → legacy behaviour (csv_to_dict returned {})
