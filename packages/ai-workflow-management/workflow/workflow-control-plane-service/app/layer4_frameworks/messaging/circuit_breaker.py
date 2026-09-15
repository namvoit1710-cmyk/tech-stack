"""Asyncio-native circuit breaker for the Event Mesh publisher.

Prevents a storm of retry attempts when Event Mesh is unavailable. States:

    CLOSED    — normal operation, failures counted
    OPEN      — fast-fail all calls for ``recovery_timeout_s``
    HALF_OPEN — probe: let one call through; success → CLOSED, failure → OPEN

State transitions are guarded by a lock so the flusher's concurrent calls
don't race each other when flipping states.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional
from collections.abc import Callable

logger = logging.getLogger(__name__)

STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Async circuit breaker for outbound HTTP calls."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
        name: str = "eventmesh",
        on_state_change: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_timeout_s = max(1.0, recovery_timeout_s)
        self._name = name
        self._on_state_change = on_state_change

        self._state = STATE_CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    async def before_call(self) -> None:
        """Raise ``CircuitBreakerOpen`` when the circuit is open.

        When the recovery timeout has elapsed in OPEN state, transitions to
        HALF_OPEN and allows one probe call through.
        """
        async with self._lock:
            if self._state == STATE_OPEN:
                if time.time() - self._opened_at > self._recovery_timeout_s:
                    self._transition(STATE_HALF_OPEN)
                else:
                    raise CircuitBreakerOpen(self._name, self._opened_at)

    async def on_success(self) -> None:
        async with self._lock:
            # Successful probe closes the circuit
            if self._state in (STATE_HALF_OPEN, STATE_OPEN):
                self._transition(STATE_CLOSED)
            self._failures = 0

    async def on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._state == STATE_HALF_OPEN:
                # Probe failed → back to OPEN for another recovery window
                self._opened_at = time.time()
                self._transition(STATE_OPEN)
                return
            if self._state == STATE_CLOSED and self._failures >= self._failure_threshold:
                self._opened_at = time.time()
                self._transition(STATE_OPEN)

    def _transition(self, new_state: str) -> None:
        """Change state and fire the notify callback (called under lock)."""
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        logger.info("Circuit %s: %s → %s", self._name, old_state, new_state)
        if self._on_state_change is not None:
            try:
                self._on_state_change(old_state, new_state)
            except Exception:
                logger.exception("Circuit breaker state-change callback failed")

    def snapshot(self) -> dict:
        """Return current state for health checks (no lock — best-effort read)."""
        return {
            "name": self._name,
            "state": self._state,
            "failures": self._failures,
            "failure_threshold": self._failure_threshold,
            "recovery_timeout_s": self._recovery_timeout_s,
            "opened_at": self._opened_at if self._state == STATE_OPEN else 0,
        }


class CircuitBreakerOpen(Exception):
    """Raised by ``before_call()`` when the circuit is OPEN."""

    def __init__(self, name: str, opened_at: float) -> None:
        self.name = name
        self.opened_at = opened_at
        super().__init__(
            f"Circuit {name!r} is OPEN "
            f"(opened {time.time() - opened_at:.1f}s ago)"
        )
