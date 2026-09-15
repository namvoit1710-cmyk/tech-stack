"""Canonical comparison operators shared by CONDITION and SWITCH rule rows.

Single definition replacing the copy in ``control_flow_schemas.py``.
The values are the operator codes accepted by
``app.layer1_domain.value_objects.comparison.Comparison.evaluate``;
``tests/unit/node_spec/test_operators.py`` guards that alignment.
"""

from app.layer1_domain.node_spec.model import Choice

COMPARISON_OPERATOR_CHOICES: tuple[Choice, ...] = (
    Choice("==", "Equals"),
    Choice("!=", "Not Equals"),
    Choice(">", "Greater Than"),
    Choice(">=", "Greater or Equal"),
    Choice("<", "Less Than"),
    Choice("<=", "Less or Equal"),
    Choice("contains", "Contains"),
    Choice("not_contains", "Not Contains"),
    Choice("starts_with", "Starts With"),
    Choice("ends_with", "Ends With"),
    Choice("is_empty", "Is Empty"),
    Choice("is_not_empty", "Is Not Empty"),
    Choice("in", "In"),
    Choice("not_in", "Not In"),
    Choice("regex", "Matches Regex"),
)
