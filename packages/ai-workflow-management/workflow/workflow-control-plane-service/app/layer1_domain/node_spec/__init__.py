"""Single-source node configuration specs.

Design: docs/design/node-spec-single-source.md
Tasks:  docs/design/node-spec-single-source-tasks.md (Phase 1)

Importing this package loads every builtin spec, so ``BUILTIN_SPECS``
is always populated.
"""

from app.layer1_domain.node_spec.model import (
    Choice,
    ConfigField,
    FieldType,
    FormGroup,
    InputField,
    NodeSpec,
    OutputField,
    VisibleWhen,
    Widget,
    coerce_to_type,
    coerce_value,
    parse_config,
)
from app.layer1_domain.node_spec.operators import COMPARISON_OPERATOR_CHOICES
from app.layer1_domain.node_spec.registry import BUILTIN_SPECS, get_spec, register

# Load and register all builtin specs.
from app.layer1_domain.node_spec import specs as _specs  # noqa: E402,F401
from app.layer1_domain.node_spec.specs import TYPED_CONFIGS

__all__ = [
    "BUILTIN_SPECS",
    "COMPARISON_OPERATOR_CHOICES",
    "Choice",
    "ConfigField",
    "FieldType",
    "FormGroup",
    "InputField",
    "NodeSpec",
    "OutputField",
    "TYPED_CONFIGS",
    "VisibleWhen",
    "Widget",
    "coerce_to_type",
    "coerce_value",
    "get_spec",
    "parse_config",
    "register",
]
