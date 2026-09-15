"""SA-1983 (A6) — the edit path must not make the declared kind a LIE.

A1–A5 ship a field that every reader trusts. A6 is the answer to "what stops a
single UI row-add from falsifying it?" — because row editing is not an edge case
here, it is *the reason the CSV format was chosen*.

Two mechanisms, and a finding worth stating plainly:

* **Promote, don't refuse** (``reconcile_after_edit``): a 1-row file declared
  ``object`` that grows a second row becomes an ``array`` of what it was. An
  array is NEVER demoted — an array of one is still an array, which is the whole
  distinction this Story restores.

* **Refuse where promotion is meaningless** (``VALUE_KIND_CONFLICT``): a
  scalar-kind ref keeps its value in the reserved ``__value`` column. Adding
  another column to it would leave a ref saying "I am a string" pointing at a
  two-column table — and ``upsert_scalar_row`` auto-expands the schema, so it
  would have done exactly that, quietly.

**Reachability, stated rather than assumed:** the specific vector the Story names
— a steward adding a row to an object-kind ROOT — is *not reachable through
today's routing*. A root ref carries no ``artifact_type``, so PATCH sends it to
``_apply_object_ops``, whose only two operations both **upsert row 1** and keep
the rest; ``delete`` is rejected outright. So the root's row count cannot change.
``reconcile_after_edit`` is therefore an INVARIANT, not a fix for a live bug: it
is tested directly, and it holds the line if the routing ever changes. The
scalar-column conflict, by contrast, IS reachable — and it is reachable because of
A4, i.e. this Story opened it, so this Story closes it.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    OBJECT,
    STRING,
    kind_of,
    item_kind_of,
    reconcile_after_edit,
)
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataUseCase,
    _OpError,
)


# ---------------------------------------------------------------------------
# Promote — an object that grew a row is not an object any more
# ---------------------------------------------------------------------------


def test_object_that_grows_a_row_is_promoted_to_array():
    ref = build_file_ref("f1", row_count=1, value_kind=OBJECT)
    reconcile_after_edit(ref, row_count=2)
    assert kind_of(ref) == ARRAY
    assert item_kind_of(ref) == OBJECT, "it is an array OF what it used to be"
    assert ref["row_count"] == 2


def test_scalar_that_grows_a_row_is_promoted_to_array_of_that_scalar():
    ref = build_file_ref("f1", row_count=1, value_kind=STRING)
    reconcile_after_edit(ref, row_count=3)
    assert kind_of(ref) == ARRAY and item_kind_of(ref) == STRING


def test_an_array_is_never_demoted():
    """An array of ONE is still an array. Demoting it would re-introduce the
    exact bug this Story exists to remove."""
    ref = build_file_ref("f1", row_count=5, value_kind=ARRAY, item_kind=OBJECT)
    reconcile_after_edit(ref, row_count=1)
    assert kind_of(ref) == ARRAY and ref["row_count"] == 1

    reconcile_after_edit(ref, row_count=0)
    assert kind_of(ref) == ARRAY, "an EMPTY array is still an array, not an object"


def test_object_that_stays_one_row_is_untouched():
    ref = build_file_ref("f1", row_count=1, value_kind=OBJECT)
    reconcile_after_edit(ref, row_count=1)
    assert kind_of(ref) == OBJECT and ref["row_count"] == 1


def test_legacy_ref_has_nothing_to_falsify():
    ref = build_file_ref("f1", row_count=1)          # no declared kind
    reconcile_after_edit(ref, row_count=4)
    assert ref["value_kind"] == "", "no declaration → nothing to promote"
    assert ref["row_count"] == 4, "the stale row_count is still refreshed"


def test_row_count_is_refreshed_even_when_the_kind_does_not_change():
    """The edit paths historically left ``row_count`` stale. It is now the number
    a lazy LOOP iterates (``_items_total`` reads it without downloading), so a
    stale count means a loop that skips rows or runs off the end."""
    ref = build_file_ref("f1", row_count=3, value_kind=ARRAY, item_kind=OBJECT)
    reconcile_after_edit(ref, row_count=7)
    assert ref["row_count"] == 7


# ---------------------------------------------------------------------------
# Refuse — a scalar has no fields to set
# ---------------------------------------------------------------------------


def _guard(ref, data):
    EditNodeDataUseCase._guard_declared_kind("__root__", ref, data, 0)


def test_adding_a_column_to_a_scalar_ref_is_refused():
    ref = build_file_ref("f1", row_count=1, value_kind=STRING, columns=["__value"])
    with pytest.raises(_OpError) as exc:
        _guard(ref, {"foo": "bar"})
    assert exc.value.code_override == "VALUE_KIND_CONFLICT"
    # The message must tell the steward what to do instead.
    assert "__value" in exc.value.message


def test_adding_a_column_to_an_array_of_scalars_is_refused():
    """Found by red-teaming: the guard first fired only for SCALAR kinds, but an
    ``array<number>`` lives in the ``__value`` column too. Adding a field to it
    would leave a ref declaring "array of numbers" over a two-column table, and
    materialize would hand back dicts where numbers were promised."""
    ref = build_file_ref(
        "f1", row_count=3, value_kind=ARRAY, item_kind="number", columns=["__value"],
    )
    with pytest.raises(_OpError) as exc:
        _guard(ref, {"foo": "bar"})
    assert exc.value.code_override == "VALUE_KIND_CONFLICT"
    assert "array of number" in exc.value.message


def test_editing_the_value_column_of_a_scalar_ref_is_allowed():
    ref = build_file_ref("f1", row_count=1, value_kind=STRING, columns=["__value"])
    _guard(ref, {"__value": "hello"})          # must not raise


def test_object_and_array_of_objects_refs_are_not_constrained():
    """A real table — the ordinary case — must stay freely editable. This is the
    feature the storage format exists to serve; the guard must not touch it."""
    _guard(build_file_ref("f1", row_count=1, value_kind=OBJECT), {"anything": 1})
    _guard(
        build_file_ref("f1", row_count=2, value_kind=ARRAY, item_kind=OBJECT),
        {"anything": 1},
    )


def test_legacy_ref_is_not_constrained():
    ref = build_file_ref("f1", row_count=1)    # no declared kind
    _guard(ref, {"anything": 1})               # must not raise


# ---------------------------------------------------------------------------
# One rule, one expression (found in red-team round 3)
# ---------------------------------------------------------------------------


def test_undeclared_item_kind_is_not_a_declaration():
    """``item_kind_of`` normalises "undeclared" (``""``) to ``UNKNOWN`` — and
    ``UNKNOWN`` is a REAL declaration: a *mixed* list genuinely declares
    ``item_kind: "unknown"`` and IS stored in ``__value``. So re-assembling the
    storage rule from ``kind_of`` + ``item_kind_of`` flips it::

        uses_value_column("array", "")                       -> False   # correct
        uses_value_column(kind_of(ref), item_kind_of(ref))   -> True    # WRONG

    Same rule, two expressions, disagreeing — the defect this whole Story exists
    to remove, in the code that removes it. ``ref_uses_value_column`` is the only
    expression of it."""
    from app.layer1_domain.value_objects.value_kind import ref_uses_value_column

    ref = build_file_ref("f1", row_count=2, value_kind=ARRAY)   # item_kind == ""
    assert ref_uses_value_column(ref) is False, "absence of a declaration is not one"

    assert ref_uses_value_column(
        build_file_ref("f2", value_kind=ARRAY, item_kind="number")) is True
    assert ref_uses_value_column(
        build_file_ref("f3", value_kind=ARRAY, item_kind="unknown")) is True
    assert ref_uses_value_column(
        build_file_ref("f4", value_kind=ARRAY, item_kind=OBJECT)) is False
    assert ref_uses_value_column(build_file_ref("f5")) is False  # legacy
