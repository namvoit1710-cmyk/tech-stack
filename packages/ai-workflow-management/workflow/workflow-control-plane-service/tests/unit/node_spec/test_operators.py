"""COMPARISON_OPERATOR_CHOICES ⇔ value_comparator alignment.

Every operator offered in CONDITION/SWITCH rule rows must be accepted
by ``compare_values`` (the runtime evaluator). ``node_spec.operators``
is the single definition since Phase 5 deleted the legacy copy in
``control_flow_schemas``.
"""

import pytest

from app.layer1_domain.node_spec.operators import COMPARISON_OPERATOR_CHOICES
from app.layer1_domain.value_objects.comparison import Comparison


@pytest.mark.parametrize(
    "op", [c.value for c in COMPARISON_OPERATOR_CHOICES],
)
def test_operator_accepted_by_value_comparator(op: str) -> None:
    # compare_values raises (or returns False) only for unknown operators;
    # a known operator on benign operands must not raise.
    Comparison.evaluate("a", op, "a")
