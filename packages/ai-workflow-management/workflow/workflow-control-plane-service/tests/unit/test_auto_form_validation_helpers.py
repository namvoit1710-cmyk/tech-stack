"""Unit specs for ``app.layer1_domain.auto_form_mapper.validation``.

Pure layer1 domain — validation-rule factory functions that build
``ValidationRule`` lists for the auto-form mapper. No collaborators, no mocks.

Gap-fill: the module sat at 41% (unit-test-coverage sweep 2026-07-02). Every
expectation below is grounded in the source of ``validation.py`` +
``models.ValidationRule`` — not from REST/theory (EDIT_UNVERIFIED forbidden).

Case types: happy · edge · invalid input · boundary · failure path.
"""
from __future__ import annotations

import re

import pytest

from app.layer1_domain.auto_form_mapper.models import ValidationRule
from app.layer1_domain.auto_form_mapper.validation import (
    optional_string,
    required,
    required_email,
    required_number,
    required_string,
    url_validation,
)


# ── required ──────────────────────────────────────────────────────────
# happy: exactly one rule, method="required", no value/message.
def test_required_yields_single_required_rule():
    rules = required()
    assert len(rules) == 1
    assert rules[0] == ValidationRule(method="required")
    assert rules[0].value is None
    assert rules[0].message is None


# ── required_string ───────────────────────────────────────────────────
# happy: default bounds → required + min(1) + max(255) with rendered messages.
def test_required_string_defaults():
    rules = required_string()
    assert [r.method for r in rules] == ["required", "min", "max"]
    min_rule = rules[1]
    max_rule = rules[2]
    assert min_rule.value == 1
    assert min_rule.message == "Minimum 1 characters"
    assert max_rule.value == 255
    assert max_rule.message == "Maximum 255 characters"


# edge/boundary: custom bounds flow through into value + message verbatim.
def test_required_string_custom_bounds_interpolated():
    rules = required_string(min_length=3, max_length=64)
    assert rules[1].value == 3
    assert rules[1].message == "Minimum 3 characters"
    assert rules[2].value == 64
    assert rules[2].message == "Maximum 64 characters"


# boundary: min_length may equal max_length (source imposes no cross-check).
def test_required_string_equal_bounds_allowed():
    rules = required_string(min_length=10, max_length=10)
    assert rules[1].value == 10
    assert rules[2].value == 10


# ── required_email ────────────────────────────────────────────────────
# happy: required + a pattern rule; the pattern actually matches an email.
def test_required_email_shape_and_pattern_matches_valid_email():
    rules = required_email()
    assert [r.method for r in rules] == ["required", "pattern"]
    pattern = rules[1].value
    assert rules[1].message == "Must be a valid email address"
    assert re.fullmatch(pattern, "laidon@simplemdg.com") is not None


# invalid input / failure path: the shipped regex rejects a non-email.
def test_required_email_pattern_rejects_non_email():
    pattern = required_email()[1].value
    # Source regex requires a TLD of >=2 letters after an '@host.' — a bare
    # local part with no '@' must NOT full-match.
    assert re.fullmatch(pattern, "not-an-email") is None
    assert re.fullmatch(pattern, "missing@tld") is None


# ── required_number ───────────────────────────────────────────────────
# happy/edge: with no bounds and integer=False → only the required rule.
def test_required_number_bare_is_required_only():
    rules = required_number()
    assert [r.method for r in rules] == ["required"]


# happy: full complement — min, max, and int flag all requested.
def test_required_number_all_options():
    rules = required_number(min_val=0, max_val=100, integer=True)
    assert [r.method for r in rules] == ["required", "gte", "lte", "int"]
    assert rules[1].value == 0
    assert rules[1].message == "Must be at least 0"
    assert rules[2].value == 100
    assert rules[2].message == "Must be at most 100"
    assert rules[3].message == "Must be a whole number"


# boundary: min_val=0 is a real bound (source guards on ``is not None``,
# so falsy-but-present 0 must still add a gte rule — the classic 0/None trap).
def test_required_number_zero_min_is_honored():
    rules = required_number(min_val=0)
    assert [r.method for r in rules] == ["required", "gte"]
    assert rules[1].value == 0


# edge: only max bound requested.
def test_required_number_only_max():
    rules = required_number(max_val=50)
    assert [r.method for r in rules] == ["required", "lte"]
    assert rules[1].value == 50


# ── optional_string ───────────────────────────────────────────────────
# happy: a single max rule, no required (it's optional).
def test_optional_string_default():
    rules = optional_string()
    assert [r.method for r in rules] == ["max"]
    assert rules[0].value == 255
    assert rules[0].message == "Maximum 255 characters"


# edge: custom max length flows through.
def test_optional_string_custom_max():
    rules = optional_string(max_length=1000)
    assert rules[0].value == 1000
    assert rules[0].message == "Maximum 1000 characters"


# ── url_validation ────────────────────────────────────────────────────
# happy: required + pattern; the pattern matches an https URL.
def test_url_validation_shape_and_matches_https():
    rules = url_validation()
    assert [r.method for r in rules] == ["required", "pattern"]
    pattern = rules[1].value
    assert rules[1].message == "Must be a valid URL"
    assert re.match(pattern, "https://simplemdg.com/path?q=1") is not None
    assert re.match(pattern, "http://example.org") is not None


# invalid input / failure path: a non-http scheme must not match.
def test_url_validation_rejects_non_http_scheme():
    pattern = url_validation()[1].value
    assert re.match(pattern, "ftp://example.org") is None
    assert re.match(pattern, "justtext") is None


# ── ValidationRule.to_dict wiring (round-trip the factory output) ──────
# Grounds that the produced rules serialise to the FE contract shape.
def test_produced_rules_serialise_to_expected_dicts():
    [req] = required()
    assert req.to_dict() == {"method": "required"}
    _, mn, mx = required_string(min_length=2, max_length=8)
    assert mn.to_dict() == {"method": "min", "value": 2, "message": "Minimum 2 characters"}
    assert mx.to_dict() == {"method": "max", "value": 8, "message": "Maximum 8 characters"}
