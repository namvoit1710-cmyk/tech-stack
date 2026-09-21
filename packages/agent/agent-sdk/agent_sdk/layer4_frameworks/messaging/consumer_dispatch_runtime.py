from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Coroutine


class _SlotReleaser:
    """Release one in-flight semaphore slot at most once.

    SA-892 M2: ``submit`` acquires a slot synchronously and the only release is
    in ``_run_and_release``'s ``finally``. If a turn's future is cancelled while
    its coroutine has not yet started, that ``finally`` never runs and the slot
    would leak -- at ``max_in_flight=1`` the consumer would wedge permanently.

    This releaser is invoked from BOTH the coroutine's ``finally`` AND the
    future's done-callback; whichever fires first releases the slot, the other
    is a no-op. The lock + flag make it exactly-once, so a BoundedSemaphore is
    never over-released (which would corrupt its bound).
    """

    __slots__ = ("_semaphore", "_lock", "_released")

    def __init__(self, semaphore: threading.BoundedSemaphore) -> None:
        self._semaphore = semaphore
        self._lock = threading.Lock()
        self._released = False

    def release(self, _future: Any = None) -> None:
        # ``_future`` lets this be used directly as a Future done-callback.
        with self._lock:
            if self._released:
                return
            self._released = True
        self._semaphore.release()


class ConsumerDispatchRuntime:
    def __init__(
        self,
        *,
        max_in_flight: int,
        drain_timeout_seconds: int,
        logger: Any,
    ) -> None:
        self._logger = logger
        self._max_in_flight = max(1, max_in_flight)
        self._drain_timeout_seconds = max(0, drain_timeout_seconds)
        self._closing = False
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self._max_in_flight)
        # Every in-flight future (slot-bound turns AND slot-free control work)
        # so ``drain_and_close`` can still cancel everything on shutdown.
        self._in_flight_futures: set[concurrent.futures.Future[None]] = set()
        # SA-892 (N3): conv_id -> in-flight futures, so a single conversation's
        # turn can be cooperatively cancelled. A conv_id may map to >1 future.
        self._futures_by_conv: dict[str, set[concurrent.futures.Future[None]]] = {}
        self._conv_by_future: dict[concurrent.futures.Future[None], str] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="consumer-dispatch-runtime",
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        handler_coro: Coroutine[Any, Any, None],
        conv_id: str | None = None,
    ) -> concurrent.futures.Future[None]:
        """Dispatch a turn, bounded by the in-flight semaphore.

        When ``conv_id`` is given the future is registered under it so
        ``cancel(conv_id)`` can interrupt this exact turn (SA-892 N3).
        """
        with self._lock:
            if self._closing:
                self._close_coroutine(handler_coro)
                raise RuntimeError("dispatch runtime is closing")

        self._slots.acquire()
        # SA-892 M2: one releaser per slot-bound submit, released exactly once
        # from whichever of the coroutine's ``finally`` or the done-callback
        # fires first (so a cancel-before-start still frees the slot).
        releaser = _SlotReleaser(self._slots)
        with self._lock:
            if self._closing:
                releaser.release()
                self._close_coroutine(handler_coro)
                raise RuntimeError("dispatch runtime is closing")
            future = asyncio.run_coroutine_threadsafe(
                self._run_and_release(handler_coro, releaser),
                self._loop,
            )
            self._register_future(future, conv_id)
        future.add_done_callback(self._discard_future)
        future.add_done_callback(releaser.release)
        return future

    def submit_control(
        self,
        handler_coro: Coroutine[Any, Any, None],
    ) -> concurrent.futures.Future[None]:
        """Dispatch control-plane work (e.g. a cancel) WITHOUT a slot.

        SEAM (SA-892 N4): the in-flight semaphore is the concurrency gate. A
        business agent runs with ``max_in_flight=1``, so a running turn holds
        the only slot; a normal ``submit`` for a stop would block on
        ``_slots.acquire()`` behind that turn and never run. Control work is
        cheap and must pre-empt, so it bypasses the semaphore entirely and runs
        directly on the loop thread — which is free while the turn is parked at
        its ``await``. It is still tracked for shutdown draining.
        """
        with self._lock:
            if self._closing:
                self._close_coroutine(handler_coro)
                raise RuntimeError("dispatch runtime is closing")
            future = asyncio.run_coroutine_threadsafe(
                self._run_control(handler_coro),
                self._loop,
            )
            self._register_future(future, None)
        future.add_done_callback(self._discard_future)
        return future

    def cancel(self, conv_id: str) -> int:
        """Cancel every in-flight turn for ``conv_id``; return how many.

        Cancelling the ``run_coroutine_threadsafe`` future schedules a
        ``CancelledError`` into the awaiting task, which propagates cleanly out
        of the turn's ``await graph.ainvoke(...)`` join point.
        """
        key = (conv_id or "").strip()
        if not key:
            return 0
        with self._lock:
            futures = list(self._futures_by_conv.get(key, ()))
        # Call cancel() outside the lock: it fires done-callbacks synchronously
        # for already-pending futures, and those callbacks re-take the lock.
        cancelled = 0
        for future in futures:
            if future.cancel() or future.cancelled():
                cancelled += 1
        return cancelled

    def close_for_new_work(self) -> None:
        with self._lock:
            self._closing = True

    def drain_and_close(self) -> None:
        self.close_for_new_work()
        with self._lock:
            pending = list(self._in_flight_futures)

        if pending:
            _, not_done = concurrent.futures.wait(
                pending,
                timeout=self._drain_timeout_seconds,
            )
            for future in not_done:
                future.cancel()
            if not_done:
                concurrent.futures.wait(not_done, timeout=1)

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    async def _run_and_release(
        self,
        handler_coro: Coroutine[Any, Any, None],
        releaser: _SlotReleaser,
    ) -> None:
        try:
            await handler_coro
        except Exception as exc:
            if self._logger is not None and hasattr(self._logger, "error"):
                # Use %-style args; passing an ``error=`` kwarg to a stdlib
                # Logger raises TypeError inside this handler and masks the
                # real failure.
                self._logger.error("Consumer dispatch handler failed: %s", str(exc))
        finally:
            # Idempotent: the done-callback may also call this; exactly one wins.
            releaser.release()

    async def _run_control(
        self,
        handler_coro: Coroutine[Any, Any, None],
    ) -> None:
        # No slot to release here — control work never acquired one.
        try:
            await handler_coro
        except Exception as exc:
            if self._logger is not None and hasattr(self._logger, "error"):
                self._logger.error("Consumer control handler failed: %s", str(exc))

    def _register_future(
        self,
        future: concurrent.futures.Future[None],
        conv_id: str | None,
    ) -> None:
        # Caller holds ``self._lock``.
        self._in_flight_futures.add(future)
        key = (conv_id or "").strip()
        if key:
            self._futures_by_conv.setdefault(key, set()).add(future)
            self._conv_by_future[future] = key

    def _discard_future(self, future: concurrent.futures.Future[None]) -> None:
        with self._lock:
            self._in_flight_futures.discard(future)
            key = self._conv_by_future.pop(future, None)
            if key is not None:
                bucket = self._futures_by_conv.get(key)
                if bucket is not None:
                    bucket.discard(future)
                    if not bucket:
                        self._futures_by_conv.pop(key, None)

    @staticmethod
    def _close_coroutine(handler_coro: Coroutine[Any, Any, None]) -> None:
        close = getattr(handler_coro, "close", None)
        if callable(close):
            close()
