"""Unit spec — layer1_domain value objects MessageTypeSet + TransportSource.

Gap-fill sweep 2026-07-02 (unit-smith). Neither value object had a dedicated
test. Baseline: message_type_set.py 83% (missing None + non-collection),
transport_source.py 88% (missing kafka + mock alias groups). Cases grounded
in source:
``agent_sdk/layer1_domain/value_objects/message_type_set.py``,
``agent_sdk/layer1_domain/value_objects/transport_source.py``.

Five case types: happy · edge · invalid input · boundary · failure path.
"""

from agent_sdk.layer1_domain.value_objects.message_type_set import MessageTypeSet
from agent_sdk.layer1_domain.value_objects.transport_source import TransportSource


# ══ MessageTypeSet.coerce ═══════════════════════════════════════════════════
# ── happy path ──
def test_coerce_comma_string_splits_and_strips():
    assert MessageTypeSet.coerce("a, b ,c") == {"a", "b", "c"}


def test_coerce_list_stringifies_and_strips():
    assert MessageTypeSet.coerce([" x ", "y", 3]) == {"x", "y", "3"}


def test_coerce_tuple_and_set_and_frozenset():
    assert MessageTypeSet.coerce(("a",)) == {"a"}
    assert MessageTypeSet.coerce({"a", "b"}) == {"a", "b"}
    assert MessageTypeSet.coerce(frozenset({"z"})) == {"z"}


# ── edge / boundary ──
def test_coerce_none_returns_empty_set():
    # Source: value is None -> set().
    assert MessageTypeSet.coerce(None) == set()


def test_coerce_blank_string_yields_empty():
    # Source: split filters out empty items.
    assert MessageTypeSet.coerce("  ,  ,") == set()


def test_coerce_collection_drops_blank_items():
    assert MessageTypeSet.coerce(["", "  ", "keep"]) == {"keep"}


# ── invalid input / failure path: unsupported type -> empty set (no raise) ──
def test_coerce_unsupported_type_returns_empty_set():
    # Source: final fallthrough -> set().
    assert MessageTypeSet.coerce(42) == set()
    assert MessageTypeSet.coerce({"a": 1}) == set()  # dict is not list/tuple/set/frozenset


# ══ TransportSource.normalize ═══════════════════════════════════════════════
# ── happy path: each alias group ──
def test_normalize_event_mesh_aliases():
    for raw in ("sap", "eventmesh", "sap_event_mesh", "event_mesh", "SAP-EVENT-MESH"):
        assert TransportSource.normalize(raw) == TransportSource.EVENT_MESH


def test_normalize_kafka_aliases():
    # Source: {"local", "kafka"} -> KAFKA.
    assert TransportSource.normalize("local") == TransportSource.KAFKA
    assert TransportSource.normalize("KAFKA") == TransportSource.KAFKA


def test_normalize_mock_aliases():
    # Source: {"mock", "memory", "in_memory"} -> MOCK.
    for raw in ("mock", "memory", "in_memory", "in-memory"):
        assert TransportSource.normalize(raw) == TransportSource.MOCK


# ── edge: unknown non-empty value passes through as-is ──
def test_normalize_unknown_value_passes_through():
    # Source: `return source or default` — source truthy -> returned.
    assert TransportSource.normalize("weird_bus") == "weird_bus"


# ── boundary: None / empty -> default ──
def test_normalize_none_uses_default():
    # Source: str(value or "") -> "" -> falls to default.
    assert TransportSource.normalize(None) == TransportSource.BROKER


def test_normalize_empty_string_uses_custom_default():
    assert TransportSource.normalize("", default="fallback") == "fallback"


# ── invalid input: hyphen/casing normalization ──
def test_normalize_lowercases_and_replaces_hyphens():
    assert TransportSource.normalize("Event-Mesh") == TransportSource.EVENT_MESH
