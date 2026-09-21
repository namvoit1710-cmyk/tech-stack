from __future__ import annotations

from app.layer1_domain.entities.repair_policy import RepairDecision, RepairPolicy


def test_default_max_attempts_is_three() -> None:
    assert RepairPolicy().max_attempts == RepairPolicy.DEFAULT_MAX_ATTEMPTS == 3


def test_below_cap_should_repair_with_next_attempt() -> None:
    policy = RepairPolicy(max_attempts=3)
    decision = policy.evaluate(attempts_used=0)
    assert decision == RepairDecision(should_repair=True, next_attempt=1)


def test_one_below_cap_still_repairs() -> None:
    policy = RepairPolicy(max_attempts=3)
    decision = policy.evaluate(attempts_used=2)
    assert decision.should_repair is True
    assert decision.next_attempt == 3


def test_at_cap_does_not_repair() -> None:
    policy = RepairPolicy(max_attempts=3)
    decision = policy.evaluate(attempts_used=3)
    assert decision.should_repair is False
    assert decision.next_attempt == 0


def test_above_cap_does_not_repair() -> None:
    policy = RepairPolicy(max_attempts=3)
    assert policy.evaluate(attempts_used=10).should_repair is False


def test_zero_max_attempts_never_repairs() -> None:
    policy = RepairPolicy(max_attempts=0)
    assert policy.evaluate(attempts_used=0).should_repair is False


def test_custom_max_attempts_from_constructor() -> None:
    policy = RepairPolicy(max_attempts=5)
    assert policy.evaluate(attempts_used=4).should_repair is True
    assert policy.evaluate(attempts_used=5).should_repair is False
