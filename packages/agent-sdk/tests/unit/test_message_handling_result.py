"""Unit spec — layer1_domain MessageHandlingResult (pure value object).

Gap-fill sweep 2026-07-02 (unit-smith). Baseline coverage of
``layer1_domain/entities/message_handling_result.py`` was 61% (missing the
classmethod constructors + every ``from_value`` branch). No existing test
referenced this type. Cases grounded in source:
``agent_sdk/layer1_domain/entities/message_handling_result.py``.

Five case types: happy · edge · invalid input · boundary · failure path.
"""

import pytest

from agent_sdk.layer1_domain.entities.message_handling_result import (
    MessageHandlingResult,
)


# ── happy path: the classmethod constructors ──────────────────────────────
def test_ack_defaults_to_ack_no_requeue():
    result = MessageHandlingResult.ack()
    assert result.action == "ack"
    assert result.requeue is False
    assert result.reason == ""


def test_nack_defaults_to_requeue_true():
    # Source: nack() signature defaults requeue=True.
    result = MessageHandlingResult.nack()
    assert result.action == "nack"
    assert result.requeue is True


def test_nack_can_disable_requeue():
    result = MessageHandlingResult.nack(requeue=False, reason="poison")
    assert result.action == "nack"
    assert result.requeue is False
    assert result.reason == "poison"


def test_reject_and_none_never_requeue():
    reject = MessageHandlingResult.reject(reason="bad")
    none = MessageHandlingResult.none(reason="ignored")
    assert reject.action == "reject" and reject.requeue is False
    assert none.action == "none" and none.requeue is False


def test_frozen_dataclass_is_immutable():
    # Source: @dataclass(frozen=True, slots=True).
    result = MessageHandlingResult.ack()
    with pytest.raises(Exception):  # FrozenInstanceError
        result.action = "nack"  # type: ignore[misc]


# ── from_value: happy for each supported representation ────────────────────
def test_from_value_passthrough_existing_instance():
    existing = MessageHandlingResult.nack(reason="keep me")
    assert MessageHandlingResult.from_value(existing) is existing


def test_from_value_string_nack_sets_requeue_true():
    # Source: requeue=(action == "nack").
    result = MessageHandlingResult.from_value("NACK")  # upper + coerced lower
    assert result.action == "nack"
    assert result.requeue is True


def test_from_value_string_ack_no_requeue():
    result = MessageHandlingResult.from_value("  ack  ")  # whitespace stripped
    assert result.action == "ack"
    assert result.requeue is False


def test_from_value_dict_full():
    result = MessageHandlingResult.from_value(
        {"action": "nack", "requeue": False, "reason": "explicit"}
    )
    assert result.action == "nack"
    assert result.requeue is False  # explicit override beats action-default
    assert result.reason == "explicit"


def test_from_value_dict_requeue_defaults_from_action():
    # Source: requeue default = (action == "nack") when key absent.
    result = MessageHandlingResult.from_value({"action": "nack"})
    assert result.requeue is True


# ── edge / boundary ───────────────────────────────────────────────────────
def test_from_value_none_is_ack():
    # Source: value is None -> cls.ack().
    result = MessageHandlingResult.from_value(None)
    assert result.action == "ack"
    assert result.requeue is False


def test_from_value_empty_dict_defaults_to_ack():
    # Source: dict path defaults action to "ack".
    result = MessageHandlingResult.from_value({})
    assert result.action == "ack"
    assert result.requeue is False


# ── invalid input ─────────────────────────────────────────────────────────
def test_from_value_unknown_string_is_rejected_with_reason():
    # Source: unknown string -> reject(reason=...).
    result = MessageHandlingResult.from_value("explode")
    assert result.action == "reject"
    assert "Unsupported handler result string" in result.reason


def test_from_value_dict_unknown_action_is_rejected():
    # Source: action not in set -> reject.
    result = MessageHandlingResult.from_value({"action": "kaboom"})
    assert result.action == "reject"
    assert "Unsupported handler result action" in result.reason


# ── failure path: unsupported type ────────────────────────────────────────
def test_from_value_unsupported_type_is_rejected_with_type_name():
    # Source: fallthrough -> reject(reason=f"...type: {type(value).__name__}").
    result = MessageHandlingResult.from_value(42)
    assert result.action == "reject"
    assert "int" in result.reason
