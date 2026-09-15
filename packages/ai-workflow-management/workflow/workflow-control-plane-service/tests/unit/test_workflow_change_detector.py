"""Unit specs for ``app.layer2_application.definition.workflow_change_detector``.

``compare_changes`` is a pure recursive generator that yields
``(path, old_value, new_value)`` tuples for each difference between two objects.
No collaborators, no mocks.

Gap-fill: the module sat at 14% (sweep 2026-07-02). Every expectation is
grounded strictly in the source — including its deliberate quirks: it iterates
only ``obj1``'s dict keys (keys added in ``obj2`` are NOT reported), and a
type mismatch short-circuits.

Case types: happy · edge · invalid input · boundary · failure path.
"""
from __future__ import annotations

from app.layer2_application.definition.workflow_change_detector import compare_changes


def _changes(a, b):
    return list(compare_changes(a, b))


# ── happy: scalar difference at root ──────────────────────────────────
def test_scalar_change_yields_path_old_new():
    assert _changes(1, 2) == [("", 1, 2)]


# edge: identical scalars → no changes.
def test_identical_scalars_no_change():
    assert _changes("x", "x") == []


# ── type mismatch short-circuits (source: type(obj1) is not type(obj2)) ─
def test_type_mismatch_yields_single_change_and_stops():
    # int vs str at root: one tuple, no recursion into contents.
    assert _changes(1, "1") == [("", 1, "1")]


def test_dict_vs_list_type_mismatch():
    assert _changes({"a": 1}, [1]) == [("", {"a": 1}, [1])]


# ── dict traversal ────────────────────────────────────────────────────
# happy: a changed nested dict value is reported with a dotted path.
def test_nested_dict_change_dotted_path():
    a = {"user": {"name": "Ann", "age": 30}}
    b = {"user": {"name": "Bob", "age": 30}}
    assert _changes(a, b) == [("user.name", "Ann", "Bob")]


# edge (source quirk): a key present only in obj2 is NOT reported, because the
# generator iterates ``set(obj1.keys())`` only. This test pins that behaviour.
def test_key_added_in_obj2_is_not_reported():
    a = {"a": 1}
    b = {"a": 1, "b": 2}
    assert _changes(a, b) == []


# edge (source quirk): a key present in obj1 but NOT obj2 hits the
# ``elif k not in obj2`` branch, which yields ``(path, obj1[k], None)`` — the
# removal is reported with new_value None. (The ``if k not in obj1`` branch is
# dead code, since ``keys = set(obj1.keys())`` guarantees every k is in obj1.)
def test_key_removed_in_obj2_reported_with_none_new_value():
    a = {"a": 1, "gone": 9}
    b = {"a": 1}
    assert _changes(a, b) == [("gone", 9, None)]


# ── list traversal ────────────────────────────────────────────────────
# happy: element change reported with index path.
def test_list_element_change_indexed_path():
    assert _changes([1, 2, 3], [1, 9, 3]) == [("[1]", 2, 9)]


# boundary: differing lengths yield a "[length]" change; zip stops at shorter.
def test_list_length_change_and_common_prefix():
    a = [1, 2]
    b = [1, 2, 3]
    changes = _changes(a, b)
    # length delta reported first; no element diffs among the common prefix.
    assert ("[length]", 2, 3) in changes
    assert all(not c[0].endswith("[0]") and not c[0].endswith("[1]") for c in changes)


# edge: equal lists → no changes.
def test_equal_lists_no_change():
    assert _changes([1, 2], [1, 2]) == []


# ── boundary: empty containers ────────────────────────────────────────
def test_empty_dicts_no_change():
    assert _changes({}, {}) == []


def test_empty_lists_no_change():
    assert _changes([], []) == []


# ── deep mixed nesting (integration of the branches) ──────────────────
def test_deep_mixed_structure():
    a = {"cfg": {"items": [{"v": 1}, {"v": 2}]}}
    b = {"cfg": {"items": [{"v": 1}, {"v": 99}]}}
    assert _changes(a, b) == [("cfg.items[1].v", 2, 99)]
