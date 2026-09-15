"""Unit tests for the asyncio CircuitBreaker."""

from __future__ import annotations

import pytest

from app.layer4_frameworks.messaging.circuit_breaker import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    CircuitBreaker,
    CircuitBreakerOpen,
)


class TestCircuitBreaker:

    @pytest.mark.asyncio
    async def test_initial_state_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=10.0)
        assert cb.state == STATE_CLOSED
        assert cb.failures == 0
        # before_call does not raise in CLOSED state
        await cb.before_call()

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=10.0)
        await cb.on_failure()
        assert cb.state == STATE_CLOSED
        await cb.on_failure()
        assert cb.state == STATE_CLOSED
        await cb.on_failure()
        assert cb.state == STATE_OPEN

    @pytest.mark.asyncio
    async def test_open_state_blocks_calls(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=10.0)
        await cb.on_failure()
        assert cb.state == STATE_OPEN
        with pytest.raises(CircuitBreakerOpen):
            await cb.before_call()

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_recovery_timeout(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=5.0)
        await cb.on_failure()
        assert cb.state == STATE_OPEN

        # Simulate 6s elapsed
        original_opened_at = cb._opened_at
        cb._opened_at = original_opened_at - 6.0

        # before_call should transition to HALF_OPEN and allow the probe
        await cb.before_call()
        assert cb.state == STATE_HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=5.0)
        await cb.on_failure()
        cb._opened_at -= 6.0  # make recovery timeout elapsed
        await cb.before_call()  # → HALF_OPEN
        assert cb.state == STATE_HALF_OPEN

        await cb.on_success()
        assert cb.state == STATE_CLOSED
        assert cb.failures == 0

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=5.0)
        await cb.on_failure()
        cb._opened_at -= 6.0
        await cb.before_call()  # → HALF_OPEN
        assert cb.state == STATE_HALF_OPEN

        await cb.on_failure()
        assert cb.state == STATE_OPEN
        # Next call still blocked
        with pytest.raises(CircuitBreakerOpen):
            await cb.before_call()

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=10.0)
        await cb.on_failure()
        await cb.on_failure()
        assert cb.failures == 2
        await cb.on_success()
        assert cb.failures == 0
        assert cb.state == STATE_CLOSED

    @pytest.mark.asyncio
    async def test_snapshot_returns_state(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=10.0, name="test")
        snap = cb.snapshot()
        assert snap["name"] == "test"
        assert snap["state"] == STATE_CLOSED
        assert snap["failures"] == 0
        assert snap["failure_threshold"] == 3

    @pytest.mark.asyncio
    async def test_state_change_callback_fired(self):
        transitions = []
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_s=10.0,
            on_state_change=lambda old, new: transitions.append((old, new)),
        )
        await cb.on_failure()  # CLOSED → OPEN
        assert transitions == [(STATE_CLOSED, STATE_OPEN)]

        cb._opened_at -= 20.0
        await cb.before_call()  # OPEN → HALF_OPEN
        assert transitions == [(STATE_CLOSED, STATE_OPEN), (STATE_OPEN, STATE_HALF_OPEN)]

        await cb.on_success()  # HALF_OPEN → CLOSED
        assert transitions[-1] == (STATE_HALF_OPEN, STATE_CLOSED)

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_breaker(self):
        def bad_callback(old, new):
            raise RuntimeError("broken")

        cb = CircuitBreaker(
            failure_threshold=1, recovery_timeout_s=10.0, on_state_change=bad_callback,
        )
        # on_failure should still transition correctly despite the callback raising
        await cb.on_failure()
        assert cb.state == STATE_OPEN
