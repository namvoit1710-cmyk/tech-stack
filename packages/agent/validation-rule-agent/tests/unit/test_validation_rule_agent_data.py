"""Unit tests for the ValidationRuleAgentData domain entity (layer1, pure).

Grounded strictly in validation_rule_agent_data.py. NOTE the observed contract:
`invalid()` IGNORES its expression/message-independent fields for expression and
file_id (always "") and forces validate_status=False regardless of the argument.
These asserts encode ACTUAL behaviour, not intended behaviour.
"""

from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.validation_rule_agent_data import ValidationRuleAgentData


# --------------------------------------------------------------------------
# happy path: success()
# --------------------------------------------------------------------------
def test_success_serializes_rules_and_marks_correct():
    rules = [{"rule_name": "r1"}, {"rule_name": "r2"}]
    data = ValidationRuleAgentData.success(rules)

    assert data.valid == "correct"
    assert data.message == "Rule is valid"
    assert data.agent_type == "validation-rule"
    assert json.loads(data.expression) == rules
    assert data.validate_status is True


def test_success_honours_overrides():
    data = ValidationRuleAgentData.success(
        [], agent_type="custom", message="ok", validate_status=False, file_id="f1"
    )
    assert data.agent_type == "custom"
    assert data.message == "ok"
    assert data.validate_status is False
    assert data.file_id == "f1"


# --------------------------------------------------------------------------
# edge: to_dict drops None-valued keys only
# --------------------------------------------------------------------------
def test_to_dict_drops_none_values():
    data = ValidationRuleAgentData(valid="correct", message="m", validate_status=None,
                                   file_id=None, expression=None)
    d = data.to_dict()
    # None-valued keys stripped; present keys kept
    assert "validate_status" not in d
    assert "file_id" not in d
    assert "expression" not in d
    assert d["valid"] == "correct"
    assert d["message"] == "m"


def test_success_to_dict_keeps_all_populated_keys():
    d = ValidationRuleAgentData.success([{"a": 1}], file_id="fid").to_dict()
    assert set(d) == {"agent_type", "expression", "valid", "message",
                      "validate_status", "file_id"}


# --------------------------------------------------------------------------
# invalid() — encodes the ACTUAL (quirky) behaviour of the classmethod
# --------------------------------------------------------------------------
def test_invalid_forces_empty_expression_and_false_status():
    # Even though a message + expression are passed, invalid() hard-codes
    # expression="" and file_id="" and validate_status=False (see source).
    data = ValidationRuleAgentData.invalid(
        message="Rule broke", expression=[{"x": 1}], validate_status=True, file_id="keep"
    )
    assert data.valid == "incorrect"
    assert data.message == "Rule broke"
    assert data.expression == ""       # argument ignored by source
    assert data.file_id == ""          # argument ignored by source
    assert data.validate_status is False


def test_invalid_default_message():
    data = ValidationRuleAgentData.invalid()
    assert data.valid == "incorrect"
    assert data.message == "Rule is invalid"


# --------------------------------------------------------------------------
# boundary: empty rules list still yields a valid "[]" expression on success
# --------------------------------------------------------------------------
def test_success_empty_rules_gives_empty_json_array():
    data = ValidationRuleAgentData.success([])
    assert data.expression == "[]"
    assert data.valid == "correct"
