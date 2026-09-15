"""validate_node — one test per violation code, plus the conditional matrices."""

from dataclasses import dataclass, field

from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.node_spec.expression_syntax import validate_expression_syntax
from app.layer1_domain.node_spec.validation import validate_node
from app.layer1_domain.value_objects.node_type import NodeType


@dataclass
class _Node:
    config: dict = field(default_factory=dict)
    id: str = "n1"


def _codes(violations) -> list[tuple[str, str]]:
    return [(v.key, v.code) for v in violations]


# ── unknown_key ──


def test_unknown_config_key_flagged() -> None:
    v = validate_node(get_spec(NodeType.TRIGGER), _Node(
        config={"trigger_type": "manual", "trigegr_typo": 1},
    ))
    assert _codes(v) == [("trigegr_typo", "unknown_key")]


def test_underscore_keys_exempt_until_phase_4() -> None:
    v = validate_node(get_spec(NodeType.TASK), _Node(
        config={"_node_definition": {"input_schema": []}},
    ))
    assert v == []


# ── missing_required (visibility-aware) ──


def test_required_field_missing() -> None:
    v = validate_node(get_spec(NodeType.APPROVAL_FLOW), _Node(config={}))
    assert _codes(v) == [("change_request_id", "missing_required")]


def test_conditionally_required_enforced_only_when_visible() -> None:
    spec = get_spec(NodeType.TRIGGER)
    # schedule trigger without a cron → violation
    v = validate_node(spec, _Node(config={"trigger_type": "schedule"}))
    assert ("schedule_cron", "missing_required") in _codes(v)
    # manual trigger → no cron needed
    v = validate_node(spec, _Node(config={"trigger_type": "manual"}))
    assert v == []
    # SA-2059: event trigger needs the topic, not the cron
    v = validate_node(spec, _Node(config={"trigger_type": "event"}))
    assert _codes(v) == [("event_topic", "missing_required")]
    # webhook is no longer a valid trigger_type
    v = validate_node(spec, _Node(config={"trigger_type": "webhook"}))
    assert ("trigger_type", "invalid_enum") in _codes(v)


# ── invalid_enum ──


def test_invalid_enum_value() -> None:
    v = validate_node(get_spec(NodeType.MERGE), _Node(
        config={"merge_strategy": "wait_some"},
    ))
    assert _codes(v) == [("merge_strategy", "invalid_enum")]
    assert "all|any|n_of_m" in v[0].message


def test_legacy_merge_vocabulary_rejected() -> None:
    # The old stored values are invalid post-migration — caught at save.
    v = validate_node(get_spec(NodeType.MERGE), _Node(
        config={"merge_strategy": "wait_all"},
    ))
    assert _codes(v) == [("merge_strategy", "invalid_enum")]


# ── invalid_type ──


def test_uncoercible_number_flagged() -> None:
    v = validate_node(get_spec(NodeType.MERGE), _Node(
        config={"merge_strategy": "all", "timeout_seconds": "soon"},
    ))
    assert _codes(v) == [("timeout_seconds", "invalid_type")]


def test_array_field_requires_list() -> None:
    v = validate_node(get_spec(NodeType.CONDITION), _Node(
        config={"data": "not-a-list"},
    ))
    assert ("data", "invalid_type") in _codes(v)


def test_item_fields_recursion() -> None:
    v = validate_node(get_spec(NodeType.SET_VARIABLE), _Node(config={
        "variables": [
            {"name": "ok", "type": "string"},
            {"type": "banana"},  # missing name + bad enum
        ],
    }))
    assert ("variables[1].name", "missing_required") in _codes(v)
    assert ("variables[1].type", "invalid_enum") in _codes(v)


# ── invalid_expression ──


def test_malformed_template_flagged() -> None:
    v = validate_node(get_spec(NodeType.COMPUTE), _Node(
        config={"expression": "{{ $a.b }}"},  # spaces inside braces
    ))
    assert _codes(v) == [("expression", "invalid_expression")]


def test_wellformed_template_skips_enum_and_type() -> None:
    v = validate_node(get_spec(NodeType.LOOP), _Node(config={
        "loop_type": "collection",
        "collection": "{{$input.items}}",
    }))
    assert v == []


# ── exclusive_group ──


def test_condition_exclusive_group_matrix() -> None:
    spec = get_spec(NodeType.CONDITION)
    # neither set — WARN only: a bare just-dragged node must still save
    # in enforce mode (it evaluates the empty-expression default).
    v = validate_node(spec, _Node(config={}))
    assert [(x.key, x.code, x.severity) for x in v] == [
        ("data", "exclusive_group", "warn"),
    ]
    # both set — hard error
    v = validate_node(spec, _Node(config={
        "data": [{"key": "$.x", "operations": "==", "value": "1"}],
        "expression": "x == 1",
    }))
    assert [(x.key, x.code, x.severity) for x in v] == [
        ("data", "exclusive_group", "error"),
    ]
    # exactly one — clean
    v = validate_node(spec, _Node(config={
        "data": [{"key": "$.x", "operations": "==", "value": "1"}],
    }))
    assert v == []


# ── expression_syntax unit coverage ──


def test_expression_syntax_checker() -> None:
    assert validate_expression_syntax("plain text") is None
    assert validate_expression_syntax("{{$node.out}} suffix") is None
    assert validate_expression_syntax("{{$wf-node.output.f}}") is None
    assert validate_expression_syntax("{{$a.b[0].c}}") is None
    assert validate_expression_syntax("{{ $a.b }}") is not None
    assert validate_expression_syntax("{{$a.b}") is not None
    assert validate_expression_syntax("{{missing_dollar}}") is not None
    assert validate_expression_syntax("}} stray") is not None
