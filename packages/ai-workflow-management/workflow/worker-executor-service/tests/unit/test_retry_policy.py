"""Unit spec for RetryPolicy (layer2 service — pure decision logic).

Grounds every assertion in app/layer2_application/services/retry_policy.py:
- NON_RETRYABLE_ERRORS = {VALIDATION_ERROR, EXPRESSION_ERROR, PERMISSION_DENIED}
- RETRYABLE_ERRORS      = {WORKER_TIMEOUT, WORKER_ERROR, INFRASTRUCTURE_ERROR}
- evaluate() short-circuits non-retryable, then caps at max_retries, then
  returns should_retry=True with an exponential (or linear) backoff delay.

No collaborators to mock — the policy is pure. Matches the house style of the
sibling service specs (module-level ``test_*`` functions, direct construction).
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.services.retry_policy import (
    RetryPolicy,
    RetryDecision,
    NON_RETRYABLE_ERRORS,
    RETRYABLE_ERRORS,
)


# --- Happy path: a retryable error below the cap retries with a delay ---

def test_retryable_error_below_cap_should_retry():
    policy = RetryPolicy(max_retries=3, base_delay_ms=1000, backoff="exponential")
    decision = policy.evaluate(attempt=0, error_code="WORKER_TIMEOUT")
    assert decision.should_retry is True
    assert decision.next_attempt == 1
    # attempt 0 → base * 2**0 / 1000 = 1000 * 1 / 1000 = 1.0s
    assert decision.delay_seconds == 1.0


def test_retryable_error_returns_retry_decision_instance():
    policy = RetryPolicy()
    decision = policy.evaluate(attempt=1, error_code="WORKER_ERROR")
    assert isinstance(decision, RetryDecision)
    assert decision.should_retry is True


# --- Exponential backoff growth across attempts (source line 57-58) ---

def test_exponential_backoff_doubles_each_attempt():
    policy = RetryPolicy(max_retries=5, base_delay_ms=1000, backoff="exponential")
    # base_delay_ms * 2**attempt / 1000
    assert policy.evaluate(0, "WORKER_ERROR").delay_seconds == 1.0   # 1000*1/1000
    assert policy.evaluate(1, "WORKER_ERROR").delay_seconds == 2.0   # 1000*2/1000
    assert policy.evaluate(2, "WORKER_ERROR").delay_seconds == 4.0   # 1000*4/1000
    assert policy.evaluate(3, "WORKER_ERROR").delay_seconds == 8.0   # 1000*8/1000


# --- Invalid input / edge: unknown error code is NOT in either set ---

def test_unknown_error_code_still_retries_below_cap():
    """An error code in neither NON_RETRYABLE nor RETRYABLE falls through the
    guard and is treated as retryable (only the attempt cap stops it)."""
    policy = RetryPolicy(max_retries=3)
    decision = policy.evaluate(attempt=0, error_code="SOMETHING_UNMAPPED")
    assert decision.should_retry is True
    assert decision.next_attempt == 1


def test_empty_error_code_is_not_non_retryable():
    policy = RetryPolicy(max_retries=3)
    decision = policy.evaluate(attempt=0, error_code="")
    assert decision.should_retry is True


# --- Failure path / non-retryable errors (source line 43-44) ---

def test_validation_error_never_retries():
    policy = RetryPolicy(max_retries=99)
    decision = policy.evaluate(attempt=0, error_code="VALIDATION_ERROR")
    assert decision.should_retry is False
    # RetryDecision default delay/next_attempt when not retrying
    assert decision.delay_seconds == 0.0
    assert decision.next_attempt == 0


def test_expression_error_never_retries():
    policy = RetryPolicy(max_retries=99)
    assert policy.evaluate(0, "EXPRESSION_ERROR").should_retry is False


def test_permission_denied_never_retries():
    policy = RetryPolicy(max_retries=99)
    assert policy.evaluate(0, "PERMISSION_DENIED").should_retry is False


def test_non_retryable_takes_precedence_over_attempt_room():
    """Even at attempt 0 with a huge cap, a non-retryable code short-circuits
    before the exponential-delay computation is ever reached."""
    policy = RetryPolicy(max_retries=100)
    decision = policy.evaluate(attempt=0, error_code="PERMISSION_DENIED")
    assert decision.should_retry is False


# --- Boundary: the attempt cap (source line 46-47) ---

def test_at_max_retries_does_not_retry():
    """attempt >= max_retries → no retry (boundary: attempt == max)."""
    policy = RetryPolicy(max_retries=3)
    decision = policy.evaluate(attempt=3, error_code="WORKER_ERROR")
    assert decision.should_retry is False


def test_one_below_max_retries_still_retries():
    """Boundary just under the cap: attempt == max-1 still retries."""
    policy = RetryPolicy(max_retries=3)
    decision = policy.evaluate(attempt=2, error_code="WORKER_ERROR")
    assert decision.should_retry is True
    assert decision.next_attempt == 3


def test_above_max_retries_does_not_retry():
    policy = RetryPolicy(max_retries=3)
    assert policy.evaluate(attempt=10, error_code="WORKER_ERROR").should_retry is False


def test_zero_max_retries_never_retries():
    """Boundary: max_retries=0 means attempt 0 >= 0 → no retry at all."""
    policy = RetryPolicy(max_retries=0)
    assert policy.evaluate(attempt=0, error_code="WORKER_ERROR").should_retry is False


# --- Linear backoff branch (source line 59) ---

def test_linear_backoff_ignores_attempt_number():
    """Non-'exponential' backoff returns a flat base_delay_ms/1000 regardless
    of attempt (source: _compute_delay else-branch)."""
    policy = RetryPolicy(max_retries=5, base_delay_ms=2000, backoff="linear")
    d0 = policy.evaluate(0, "WORKER_ERROR")
    d3 = policy.evaluate(3, "WORKER_ERROR")
    assert d0.delay_seconds == 2.0   # 2000/1000
    assert d3.delay_seconds == 2.0   # flat, not scaled by attempt


def test_unrecognized_backoff_falls_back_to_linear():
    """Any backoff string other than 'exponential' takes the flat branch."""
    policy = RetryPolicy(max_retries=5, base_delay_ms=500, backoff="fibonacci")
    assert policy.evaluate(2, "WORKER_ERROR").delay_seconds == 0.5


# --- Guard the module-level error sets against silent drift ---

def test_error_classification_sets_are_disjoint():
    assert NON_RETRYABLE_ERRORS.isdisjoint(RETRYABLE_ERRORS)
    assert "VALIDATION_ERROR" in NON_RETRYABLE_ERRORS
    assert "WORKER_TIMEOUT" in RETRYABLE_ERRORS
