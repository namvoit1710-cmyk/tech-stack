"""FIX2: one kind derivation, keyed by the NodeType enum — totality guards.

Before this fix, two derivations shipped in the same PR
(``builtin_node_provider._builtin_kind`` keyed by category string, and
``node_kind.default_kind`` keyed by a hand-picked ``_LOGIC_BASE_TYPES`` set)
and disagreed on 11 of 18 catalog specs. This file pins the properties that
make that class of bug impossible going forward: every catalog spec has a
static kind entry, the WORKFLOW correction landed, and all 7 vocabulary
members are reachable from ``default_kind`` (the old ``default_kind`` could
only ever return logic/read/action).
"""

from app.layer1_domain.value_objects.node_kind import (
    NodeKind,
    VALID_NODE_KINDS,
    _KIND_BY_NODE_TYPE,
    default_kind,
    kind_for_node_type,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.providers.builtin_node_provider import _catalog_specs


def test_every_catalog_spec_node_type_has_a_static_kind():
    catalog_types = {spec.node_type for spec in _catalog_specs()}
    assert catalog_types <= set(_KIND_BY_NODE_TYPE)


def test_workflow_is_logic():
    assert kind_for_node_type(NodeType.WORKFLOW) == NodeKind.LOGIC


def test_task_has_no_static_kind_it_is_worker_backed():
    assert kind_for_node_type(NodeType.TASK) is None


def test_all_seven_kinds_are_reachable_from_default_kind():
    """The old default_kind's ``_LOGIC_BASE_TYPES`` set + GET/POST inference
    could only ever produce logic / read / action — trigger, human, transform,
    and util were unreachable from it no matter what a business definition's
    base_type/base_worker_id/locked_defaults were."""
    reachable = set()
    for node_type in _KIND_BY_NODE_TYPE:
        reachable.add(default_kind(node_type, {}).value)
    reachable.add(default_kind(NodeType.TASK, {"method": "GET"}).value)   # read
    reachable.add(default_kind(NodeType.TASK, {"method": "POST"}).value)  # action
    assert reachable == set(VALID_NODE_KINDS)
