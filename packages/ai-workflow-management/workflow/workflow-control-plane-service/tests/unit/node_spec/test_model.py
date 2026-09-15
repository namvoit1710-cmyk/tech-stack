"""NodeSpec model invariants — malformed specs fail at construction."""

import pytest

from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    FormGroup,
    InputField,
    NodeSpec,
    VisibleWhen,
)
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType


def _spec(**overrides):
    base = dict(
        node_type=NodeType.COMPUTE,
        name="X",
        description="",
        category="test",
        runtime="engine",
        node_class=NodeClass.TECHNICAL,
        icon="",
        color="",
    )
    base.update(overrides)
    return NodeSpec(**base)


def test_valid_minimal_spec() -> None:
    spec = _spec(config_fields=(
        ConfigField(key="a", type=FieldType.STRING, label="A"),
    ))
    assert spec.config_keys == frozenset({"a"})
    assert spec.config_field("a").label == "A"
    assert spec.config_field("missing") is None


def test_duplicate_config_keys_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate config field keys"):
        _spec(config_fields=(
            ConfigField(key="a", type=FieldType.STRING, label="A"),
            ConfigField(key="a", type=FieldType.NUMBER, label="A2"),
        ))


def test_visible_when_must_reference_sibling() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        _spec(config_fields=(
            ConfigField(key="a", type=FieldType.STRING, label="A",
                        visible_when=VisibleWhen(field="ghost", equals="x")),
        ))


def test_requires_one_of_must_reference_known_keys() -> None:
    with pytest.raises(ValueError, match="requires_one_of"):
        _spec(
            config_fields=(
                ConfigField(key="a", type=FieldType.STRING, label="A"),
            ),
            requires_one_of=(("a", "ghost"),),
        )


def test_config_and_input_keys_disjoint() -> None:
    with pytest.raises(ValueError, match="both config and input"):
        _spec(
            config_fields=(
                ConfigField(key="a", type=FieldType.STRING, label="A"),
            ),
            input_fields=(InputField(key="a"),),
        )


def test_tab_must_exist() -> None:
    with pytest.raises(ValueError, match="unknown tab"):
        _spec(config_fields=(
            ConfigField(key="a", type=FieldType.STRING, label="A", tab="t1"),
        ))


def test_tabbed_spec_requires_tab_on_every_rendered_field() -> None:
    with pytest.raises(ValueError, match="lack a tab"):
        _spec(
            form_tabs=(FormGroup(key="t1", label="T1"),),
            config_fields=(
                ConfigField(key="a", type=FieldType.STRING, label="A", tab="t1"),
                ConfigField(key="b", type=FieldType.STRING, label="B"),
            ),
        )


def test_internal_fields_exempt_from_tab_rule() -> None:
    spec = _spec(
        form_tabs=(FormGroup(key="t1", label="T1"),),
        config_fields=(
            ConfigField(key="a", type=FieldType.STRING, label="A", tab="t1"),
            ConfigField(key="b", type=FieldType.STRING, label="B",
                        internal=True),
        ),
    )
    assert spec.config_keys == frozenset({"a", "b"})


def test_invalid_runtime_rejected() -> None:
    with pytest.raises(ValueError, match="invalid runtime"):
        _spec(runtime="banana")


def test_duplicate_choice_values_rejected() -> None:
    from app.layer1_domain.node_spec.model import Choice

    with pytest.raises(ValueError, match="duplicate choice values"):
        _spec(config_fields=(
            ConfigField(key="a", type=FieldType.STRING, label="A",
                        choices=(Choice("x", "X"), Choice("x", "X2"))),
        ))


def test_visible_when_matches() -> None:
    vw = VisibleWhen(field="mode", equals="parallel")
    assert vw.matches({"mode": "parallel"})
    assert not vw.matches({"mode": "sequential"})
    assert not vw.matches({})
    multi = VisibleWhen(field="mode", equals=("a", "b"))
    assert multi.matches({"mode": "b"})
    assert not multi.matches({"mode": "c"})
