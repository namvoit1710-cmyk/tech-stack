"""Spec-driven split_parameters — routing, coercion, store_as fold.

The "intentional re-homing" tests double as the normalization spec for
the Phase 6 migration: keys that the legacy ``_CONFIG_KEYS`` split sent
to ``input_binding`` but the spec declares as config (and vice versa).
"""

from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.node_spec.parameter_mapper import (
    merge_parameters,
    split_parameters,
)
from app.layer1_domain.value_objects.node_type import NodeType


def _split(node_type: NodeType, params: dict) -> tuple[dict, dict]:
    return split_parameters(get_spec(node_type), params)


def test_empty_parameters() -> None:
    assert _split(NodeType.MERGE, {}) == ({}, {})


def test_unknown_keys_flow_to_binding() -> None:
    config, binding = _split(NodeType.COMPUTE, {
        "expression": "1 + 1", "custom_input": "{{$a.b}}",
    })
    assert config == {"expression": "1 + 1"}
    assert binding == {"custom_input": "{{$a.b}}"}


def test_number_and_boolean_coercion_from_spec_types() -> None:
    config, _ = _split(NodeType.MERGE, {
        "merge_strategy": "n_of_m", "wait_count": "2", "timeout_seconds": "30",
    })
    assert config["wait_count"] == 2
    assert config["timeout_seconds"] == 30

    config, _ = _split(NodeType.WORKFLOW, {"wait_for_completion": "true"})
    assert config["wait_for_completion"] is True


# ── intentional re-homings vs the legacy split (migration contract) ──


def test_trigger_event_topic_and_filter_now_config() -> None:
    # SA-2059: webhook trigger_type retired; event_topic + event_filter are the
    # event trigger's config keys. Both stay in config, never input_binding.
    config, binding = _split(NodeType.TRIGGER, {
        "trigger_type": "event",
        "event_topic": "orders.created",
        "event_filter": "$.a == 1",
    })
    assert config == {
        "trigger_type": "event",
        "event_topic": "orders.created",
        "event_filter": "$.a == 1",
    }
    assert binding == {}


def test_condition_data_now_config() -> None:
    rules = [{"key": "{{$a.b}}", "operations": "==", "value": "1"}]
    config, binding = _split(NodeType.CONDITION, {"data": rules})
    assert config == {"data": rules}
    assert binding == {}


def test_switch_data_now_config() -> None:
    rules = [{"key": "$.x", "operations": "==", "value": "1", "branch": "b1"}]
    config, binding = _split(NodeType.SWITCH, {"data": rules})
    assert config == {"data": rules}
    assert binding == {}


def test_merge_legacy_keys_no_longer_config() -> None:
    config, binding = _split(NodeType.MERGE, {
        "wait_strategy": "wait_all", "n_required": 2,
    })
    assert config == {}
    assert binding == {"wait_strategy": "wait_all", "n_required": 2}


def test_loop_items_expression_alias_dies() -> None:
    config, binding = _split(NodeType.LOOP, {
        "collection": "{{$input.items}}", "items_expression": "{{$x}}",
    })
    assert config == {"collection": "{{$input.items}}"}
    assert binding == {"items_expression": "{{$x}}"}


def test_workflow_input_mapping_rows_fold_to_dict() -> None:
    config, binding = _split(NodeType.WORKFLOW, {
        "workflow_id": "wf-1",
        "input_mapping": [
            {"param_name": "a", "param_value": "{{$n.out}}"},
            {"param_name": "b", "param_value": "literal"},
            {"not_a_row": True},
        ],
    })
    assert config["input_mapping"] == {"a": "{{$n.out}}", "b": "literal"}
    assert binding == {}


def test_workflow_input_mapping_dict_passthrough() -> None:
    config, _ = _split(NodeType.WORKFLOW, {
        "input_mapping": {"a": "{{$n.out}}"},
    })
    assert config["input_mapping"] == {"a": "{{$n.out}}"}


def test_workflow_legacy_input_binding_key_not_config() -> None:
    config, binding = _split(NodeType.WORKFLOW, {
        "input_binding": [{"param_name": "a", "param_value": "x"}],
    })
    assert "input_binding" not in config
    assert "input_binding" in binding


def test_data_edit_tab_fields_now_config() -> None:
    config, binding = _split(NodeType.DATA_EDIT, {
        "file_id": "{{$up.__file_id}}",
        "query": "$top=10",
        "readonly": "true",
        "custom_field": "stays-binding",
    })
    assert config["file_id"] == "{{$up.__file_id}}"
    assert config["query"] == "$top=10"
    assert config["readonly"] is True
    assert binding == {"custom_field": "stays-binding"}


# ── entity integration: __post_init__ uses the spec-driven split ──


def test_workflow_node_post_init_splits_via_spec() -> None:
    node = WorkflowNode(
        workflow_id="n1", node_type=NodeType.TRIGGER,
        parameters={"trigger_type": "event", "event_topic": "orders.created"},
    )
    assert node.config == {
        "trigger_type": "event", "event_topic": "orders.created",
    }
    assert node.input_binding == {}


def test_explicit_config_binding_bypass_split() -> None:
    node = WorkflowNode(
        workflow_id="n1", node_type=NodeType.MERGE,
        config={"merge_strategy": "all"},
        input_binding={"x": 1},
        parameters={"ignored": True},
    )
    assert node.config == {"merge_strategy": "all"}
    assert node.input_binding == {"x": 1}


def test_merge_parameters_round_trip_filters_internal() -> None:
    merged = merge_parameters(
        {"merge_strategy": "all", "_node_definition": {"x": 1}},
        {"extra": "{{$a.b}}"},
    )
    assert merged == {"merge_strategy": "all", "extra": "{{$a.b}}"}
