"""SA-1736 (B): pin the control-plane kind vocabulary.

`kind` is a per-layer tuple (decision Q1), not a shared import. This pins the CP copy
to the canonical vocabulary; the worker-sdk copy is pinned by Plan A's parity test.
The two canonical tuples MUST be identical strings (SA-1558 case-drift lesson).
"""

import pytest

from app.layer1_domain.value_objects.node_kind import NodeKind, VALID_NODE_KINDS

CANONICAL = ("trigger", "action", "read", "logic", "human", "transform", "util")


def test_cp_kind_vocab_is_canonical():
    assert VALID_NODE_KINDS == CANONICAL


def test_membership_is_enum_construction_not_a_tuple_scan():
    """FIX1: the vocabulary is now a closed ``NodeKind`` enum, so "is this a
    valid kind?" is an enum construction (``NodeKind(value)``, raises
    ``ValueError`` on drift) rather than a ``value in VALID_NODE_KINDS`` scan
    against a bare tuple nothing binds to. Every canonical value constructs;
    anything outside the vocabulary — including the drifted ``"write"`` the
    executor's docstring invented inside this same PR — is rejected."""
    for value in CANONICAL:
        assert NodeKind(value).value == value
    with pytest.raises(ValueError):
        NodeKind("write")
