"""Tests for RetryPolicy — deterministic (injected sleep + jitter, no real waiting)."""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.step import StepResult, StepStatus
from app.layer2_application.services.retry_policy import RetryPolicy


class _Sleeps:
    """Records backoff delays instead of sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _attempts(*results: StepResult):
    """Return an async attempt() that yields the given results in order."""
    seq = list(results)
    calls = {"n": 0}

    async def attempt() -> StepResult:
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    attempt.calls = calls  # type: ignore[attr-defined]
    return attempt


def _ok():
    return StepResult(step_id="s", status=StepStatus.SUCCESS, output={"r": 1})


def _transient(msg="boom"):
    return StepResult(step_id="s", status=StepStatus.ERROR, error=msg, retryable=True)


def _terminal(msg="nope"):
    return StepResult(step_id="s", status=StepStatus.ERROR, error=msg, retryable=False)


@pytest.mark.asyncio
async def test_success_first_attempt_no_sleep():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=3, sleep=sleeps)
    attempt = _attempts(_ok())
    result = await policy.run(attempt, step_id="s")
    assert result.status == StepStatus.SUCCESS
    assert attempt.calls["n"] == 1
    assert sleeps.delays == []


@pytest.mark.asyncio
async def test_terminal_failure_not_retried():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=3, sleep=sleeps)
    attempt = _attempts(_terminal())
    result = await policy.run(attempt, step_id="s")
    assert result.status == StepStatus.ERROR
    assert result.error == "nope"
    assert attempt.calls["n"] == 1  # not retried
    assert sleeps.delays == []


@pytest.mark.asyncio
async def test_retryable_then_success():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=1.0, jitter=False, sleep=sleeps)
    attempt = _attempts(_transient(), _ok())
    result = await policy.run(attempt, step_id="s")
    assert result.status == StepStatus.SUCCESS
    assert attempt.calls["n"] == 2
    assert sleeps.delays == [1.0]  # one backoff before the successful retry


@pytest.mark.asyncio
async def test_budget_exhausted():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=1.0, jitter=False, sleep=sleeps)
    attempt = _attempts(_transient("e1"), _transient("e2"), _transient("e3"))
    result = await policy.run(attempt, step_id="s")
    assert result.status == StepStatus.ERROR
    assert result.retryable is False
    assert "Retry budget exhausted after 3" in result.error
    assert "e3" in result.error  # carries the last error
    assert attempt.calls["n"] == 3
    assert sleeps.delays == [1.0, 2.0]  # 2 backoffs between 3 attempts


@pytest.mark.asyncio
async def test_backoff_sequence_exponential():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=4, backoff_base_seconds=1.0, backoff_cap_seconds=100.0,
                         jitter=False, sleep=sleeps)
    attempt = _attempts(_transient(), _transient(), _transient(), _transient())
    await policy.run(attempt, step_id="s")
    assert sleeps.delays == [1.0, 2.0, 4.0]  # base * 2^(n-1)


@pytest.mark.asyncio
async def test_backoff_capped():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=5, backoff_base_seconds=1.0, backoff_cap_seconds=3.0,
                         jitter=False, sleep=sleeps)
    attempt = _attempts(*[_transient() for _ in range(5)])
    await policy.run(attempt, step_id="s")
    assert sleeps.delays == [1.0, 2.0, 3.0, 3.0]  # capped at 3.0


@pytest.mark.asyncio
async def test_full_jitter_applied():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=2, backoff_base_seconds=4.0, jitter=True,
                         rand=lambda: 0.25, sleep=sleeps)
    attempt = _attempts(_transient(), _ok())
    await policy.run(attempt, step_id="s")
    assert sleeps.delays == [1.0]  # full jitter: 0.25 * min(4*2^0, cap) = 1.0


@pytest.mark.asyncio
async def test_max_attempts_one_disables_retry_passthrough():
    sleeps = _Sleeps()
    policy = RetryPolicy(max_attempts=1, sleep=sleeps)
    attempt = _attempts(_transient("orig"))
    result = await policy.run(attempt, step_id="s")
    assert attempt.calls["n"] == 1
    assert sleeps.delays == []
    assert result.error == "orig"  # passthrough, not wrapped
