"""parse_config — typed handler access: canonical keys, defaults, coercion."""

from dataclasses import dataclass

from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
    coerce_value,
    parse_config,
)
from app.layer1_domain.node_spec.specs import (
    LoopConfig,
    MergeConfig,
    TriggerConfig,
    WorkflowNodeConfig,
)
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType


@dataclass
class _Node:
    config: dict


def test_defaults_applied_when_keys_missing() -> None:
    cfg = MergeConfig.from_node(_Node(config={}))
    assert cfg.merge_strategy == "all"
    assert cfg.timeout_seconds == 3600
    assert cfg.on_branch_failure == "fail"


def test_string_numbers_coerced() -> None:
    cfg = MergeConfig.from_node(
        _Node(config={"wait_count": "3", "timeout_seconds": "60.5"}),
    )
    assert cfg.wait_count == 3
    assert cfg.timeout_seconds == 60.5


def test_string_booleans_coerced() -> None:
    cfg = WorkflowNodeConfig.from_node(
        _Node(config={"wait_for_completion": "false"}),
    )
    assert cfg.wait_for_completion is False


def test_legacy_keys_ignored() -> None:
    # Canonical keys only: the dead vocabulary is invisible to handlers.
    cfg = MergeConfig.from_node(
        _Node(config={"wait_strategy": "wait_any", "n_required": 2}),
    )
    assert cfg.merge_strategy == "all"
    assert cfg.wait_count == 0


def test_template_values_bypass_coercion() -> None:
    cfg = LoopConfig.from_node(
        _Node(config={"max_iterations": "{{$input.n}}"}),
    )
    assert cfg.max_iterations == "{{$input.n}}"


def test_explicit_value_wins_over_default() -> None:
    cfg = TriggerConfig.from_node(_Node(config={"trigger_type": "webhook"}))
    assert cfg.trigger_type == "webhook"


def test_unknown_config_keys_ignored() -> None:
    cfg = TriggerConfig.from_node(
        _Node(config={"trigger_type": "manual", "stray": 1}),
    )
    assert cfg.trigger_type == "manual"


def test_parse_config_skips_keys_missing_from_dataclass() -> None:
    spec = NodeSpec(
        node_type=NodeType.COMPUTE, name="X", description="", category="t",
        runtime="engine", node_class=NodeClass.TECHNICAL, icon="", color="",
        config_fields=(
            ConfigField(key="a", type=FieldType.STRING, label="A"),
            ConfigField(key="b", type=FieldType.STRING, label="B"),
        ),
    )

    @dataclass(frozen=True)
    class OnlyA:
        a: str = ""

    parsed = parse_config(spec, OnlyA, _Node(config={"a": "x", "b": "y"}))
    assert parsed == OnlyA(a="x")


def test_coerce_value_json_strings() -> None:
    f = ConfigField(key="j", type=FieldType.JSON, label="J")
    assert coerce_value(f, '[{"k": 1}]') == [{"k": 1}]
    assert coerce_value(f, "not json") == "not json"  # validation rejects later


def test_coerce_value_empty_and_none_pass_through() -> None:
    f = ConfigField(key="n", type=FieldType.NUMBER, label="N")
    assert coerce_value(f, None) is None
    assert coerce_value(f, "") == ""
    assert coerce_value(f, "abc") == "abc"  # uncoercible → original
