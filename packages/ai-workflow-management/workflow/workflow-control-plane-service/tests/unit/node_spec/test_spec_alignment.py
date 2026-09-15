"""Drift guard: typed XxxConfig dataclasses ⇔ spec config keys.

Replaces the comment-based conventions of the five-catalog era: a spec
field without a typed-config attribute (or vice versa) fails here, at
unit-test time, instead of silently misrouting at runtime.
"""

import dataclasses

import pytest

from app.layer1_domain.node_spec import BUILTIN_SPECS, get_spec
from app.layer1_domain.node_spec.specs import TYPED_CONFIGS


@pytest.mark.parametrize(
    "node_type", sorted(TYPED_CONFIGS.keys(), key=lambda t: t.value),
)
def test_typed_config_matches_spec_keys(node_type) -> None:
    spec = BUILTIN_SPECS[node_type]
    cls = TYPED_CONFIGS[node_type]
    cls_fields = {f.name for f in dataclasses.fields(cls)}
    assert cls_fields == set(spec.config_keys), (
        f"{cls.__name__} fields and {node_type.value} spec config keys "
        f"diverged: only-in-class={sorted(cls_fields - spec.config_keys)} "
        f"only-in-spec={sorted(spec.config_keys - cls_fields)}"
    )


def test_every_spec_with_config_has_typed_class() -> None:
    missing = [
        t.value for t, spec in BUILTIN_SPECS.items()
        if spec.config_fields and t not in TYPED_CONFIGS
    ]
    assert not missing, f"specs without a typed config class: {missing}"


def test_typed_configs_are_frozen() -> None:
    for cls in TYPED_CONFIGS.values():
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} must be frozen"


def test_every_node_type_has_a_spec() -> None:
    from app.layer1_domain.value_objects.node_type import NodeType

    missing = [t.value for t in NodeType if t not in BUILTIN_SPECS]
    assert not missing, f"NodeType members without a registered spec: {missing}"
    # get_spec never returns None — unknown lookups fall back to the
    # minimal TASK spec (config = input_types only).
    for t in NodeType:
        assert get_spec(t) is not None
