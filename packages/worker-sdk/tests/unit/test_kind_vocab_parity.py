"""SA-1735 (A): pin the worker-sdk kind vocabulary.

`kind` is modelled as a per-layer copy (decision Q1) rather than a shared import.
This test pins the worker-sdk copy to the canonical vocabulary so drift across
worker-sdk / executor / control-plane / FE is caught (the SA-1558 case-drift lesson).
The control-plane copy is pinned by its own parity test (Plan B).

Code-review follow-up: `kind` is now `NodeKind(str, Enum)`, not a bare tuple of
raw strings — this pins BOTH the enum membership (closed vocabulary) and that
`VALID_NODE_KINDS` is *derived* from the enum rather than re-declared, so the
tuple can no longer drift from the enum that actually governs construction.
"""
import pytest

from worker_sdk.layer1_domain.entities.node_type_definition import VALID_NODE_KINDS
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind

CANONICAL = ("trigger", "action", "read", "logic", "human", "transform", "util")


def test_worker_sdk_kind_vocab_is_canonical():
    assert VALID_NODE_KINDS == CANONICAL


def test_valid_node_kinds_is_derived_from_node_kind_enum():
    """Nothing may re-declare the members: VALID_NODE_KINDS must equal a live
    projection of NodeKind, not a hand-copied tuple that could drift from it."""
    assert VALID_NODE_KINDS == tuple(k.value for k in NodeKind)


def test_node_kind_membership_accepts_every_canonical_value():
    for value in CANONICAL:
        assert NodeKind(value) == value


def test_node_kind_rejects_invented_kind_that_actually_shipped():
    """"write" is not a real kind -- it is the invented value the executor
    once documented despite it never existing in the vocabulary. Constructing
    NodeKind("write") must raise, the same way the old tuple-membership check
    did, so this drift class cannot silently reappear."""
    with pytest.raises(ValueError):
        NodeKind("write")


def test_node_kind_rejects_unknown_value():
    with pytest.raises(ValueError):
        NodeKind("banana")
