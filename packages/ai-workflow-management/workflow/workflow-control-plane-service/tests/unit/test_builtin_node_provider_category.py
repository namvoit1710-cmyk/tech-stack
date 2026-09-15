import logging
import pytest

from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.providers.builtin_node_provider import (
    _build_summary,
    _builtin_kind,
    _catalog_specs,
)


def test_kind_derives_from_the_node_type_not_a_category_string():
    """FIX2: _builtin_kind used to be keyed by ``spec.category`` (a raw string)
    via a private ``_CATEGORY_KIND`` map that has been deleted. It is now keyed
    by the NodeType enum, delegating to the single domain source of truth
    (``node_kind.kind_for_node_type``)."""
    assert _builtin_kind(NodeType.TRIGGER) == "trigger"
    assert _builtin_kind(NodeType.CONDITION) == "logic"
    assert _builtin_kind(NodeType.SWITCH) == "logic"
    assert _builtin_kind(NodeType.LOOP) == "logic"
    assert _builtin_kind(NodeType.PARALLEL) == "logic"
    assert _builtin_kind(NodeType.MERGE) == "logic"
    assert _builtin_kind(NodeType.WAIT) == "logic"
    assert _builtin_kind(NodeType.COMPUTE) == "transform"
    assert _builtin_kind(NodeType.SET_VARIABLE) == "transform"
    assert _builtin_kind(NodeType.OUTPUT) == "util"
    assert _builtin_kind(NodeType.APPROVAL_FLOW) == "human"
    assert _builtin_kind(NodeType.DATA_EDIT) == "human"
    assert _builtin_kind(NodeType.TEMPLATE_DATA_EDIT) == "human"
    assert _builtin_kind(NodeType.FILE_UPLOAD) == "human"
    assert _builtin_kind(NodeType.HUMAN_ACTION) == "human"
    assert _builtin_kind(NodeType.INPUT) == "human"
    assert _builtin_kind(NodeType.PROMPT) == "action"


def test_workflow_sub_workflow_is_logic_not_action():
    """Regression: the old ``_CATEGORY_KIND`` map was missing the
    ``control_flow.composition`` key entirely, so the WORKFLOW (sub-workflow)
    node silently fell back to kind='action' and rendered under 'Data & Files'
    instead of 'Flow & Logic'."""
    assert _builtin_kind(NodeType.WORKFLOW) == "logic"


def test_every_catalog_spec_has_a_static_kind():
    """Totality: a builtin spec with no entry in _KIND_BY_NODE_TYPE must raise
    loudly (via _builtin_kind) rather than silently render under the wrong
    palette group — this is exactly the bug _builtin_kind(NodeType.WORKFLOW)
    used to hide."""
    for spec in _catalog_specs():
        assert _builtin_kind(spec.node_type)  # raises ValueError if unmapped


def test_unmapped_node_type_degrades_loudly_instead_of_taking_the_catalog_down(caplog):
    """An unmapped node type must LOG an error and fall back — never raise.

    The gap is gated in CI by test_node_kind_totality, so it cannot merge. But if one
    ever reached runtime, raising here would kill list_summaries() -> /api/v2/node-catalog
    500s -> every tenant gets an empty palette. The cost of falling back is one node in the
    wrong bucket. A cosmetic bug must never be paid for with an outage.
    """
    with caplog.at_level(logging.ERROR):
        kind = _builtin_kind(NodeType.TASK)  # worker-backed; never in the static map

    assert kind == "action"
    assert any("_KIND_BY_NODE_TYPE" in r.message for r in caplog.records), (
        "the fallback must be loud in the logs, not silent"
    )


def test_summary_emits_core_dot_kind():
    s = _build_summary(get_spec(NodeType.PARALLEL))   # category control_flow.concurrency
    assert s.category == "Core.logic"
    assert s.kind == "logic"
    assert s.collection == "Core"
