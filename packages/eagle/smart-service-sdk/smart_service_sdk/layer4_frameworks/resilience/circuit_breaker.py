from __future__ import annotations

import time
from enum import Enum
from typing import Callable


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_seconds: float,
        now_provider: Callable[[], float] | None = None,
    ):
        self._failure_threshold = max(1, int(failure_threshold))
        self._reset_seconds = float(reset_seconds)
        self._now_provider = now_provider or time.monotonic
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def before_request(self) -> None:
        if self._state is CircuitBreakerState.OPEN:
            now = self._now_provider()
            if (
                self._opened_at is not None
                and now - self._opened_at >= self._reset_seconds
            ):
                self._state = CircuitBreakerState.HALF_OPEN
                self._probe_in_flight = True
                return
            raise CircuitBreakerOpenError("File service circuit is open")
        if self._state is CircuitBreakerState.HALF_OPEN:
            if self._probe_in_flight:
                raise CircuitBreakerOpenError("File service circuit probe is in flight")
            self._probe_in_flight = True

    def record_success(self) -> None:
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> bool:
        now = self._now_provider()
        if self._state is CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            self._failure_count = self._failure_threshold
            self._opened_at = now
            self._probe_in_flight = False
            return True

        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitBreakerState.OPEN
            self._opened_at = now
            self._probe_in_flight = False
            return True
        return False
