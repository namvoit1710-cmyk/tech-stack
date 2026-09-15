"""The per-run lock backstop must never evict a lock someone still needs.

Background. ``_evict_unheld_locks`` is a size backstop I added so runs that
never reach a terminal state stop leaving one ``asyncio.Lock`` resident per run
for the pod's lifetime. Its first version decided with ``lock.locked()``.

That is the wrong question. ``asyncio.Lock.locked()`` goes False the instant
``release()`` returns, but the coroutine that release just woke has not been
scheduled yet and still holds a reference to that lock object. Evicting there
hands the next caller a BRAND NEW lock — and the per-run mutex, the single
thing keeping same-run orchestration actions from interleaving, stops working.

The window is production-reachable, not contrived:

    OrchestrationWorker.run_forever:
        release  ->  await self._queue.dequeue()  ->  get_lock(next run)

``dequeue`` is ``await asyncio.Queue.get()`` (kafka_queue.py:128), and a
non-empty asyncio.Queue returns WITHOUT yielding to the loop. Under backlog —
the only condition in which the backstop fires at all — those three steps are
one uninterrupted step.

So these tests set ``MAX_RUN_LOCKS`` low and reproduce the sequence, rather
than asserting that a size cap exists.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture()
def manager(monkeypatch):
    """A RunScopeManager whose backstop fires after 2 locks."""
    monkeypatch.setenv("MAX_RUN_LOCKS", "2")
    module = importlib.import_module(
        "app.layer2_application.orchestration.run_scope_manager")
    importlib.reload(module)          # _MAX_RUN_LOCKS is read at import time
    try:
        yield module.RunScopeManager(run_repo=None), module
    finally:
        monkeypatch.delenv("MAX_RUN_LOCKS", raising=False)
        importlib.reload(module)


class TestTheEvictionWindow:
    @pytest.mark.asyncio
    async def test_a_lock_with_a_waiter_is_never_evicted(self, manager):
        """The exact sequence the orchestration worker performs."""
        mgr, _ = manager
        lock = mgr.get_lock("r")
        await lock.acquire()

        waiter_entered = asyncio.Event()

        async def waiter() -> None:
            async with mgr.get_lock("r"):
                waiter_entered.set()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)            # let the waiter reach acquire()
        assert not waiter_entered.is_set()

        # A backlog of other runs, delivered the way the worker gets them.
        backlog: asyncio.Queue = asyncio.Queue()
        for other in ("s", "t", "u"):
            backlog.put_nowait(other)

        lock.release()
        assert not lock.locked(), "precondition: locked() reads False here"
        assert lock.in_use(), "but somebody is still waiting on it"

        while not backlog.empty():
            # ``await`` on a non-empty queue does not yield — same step.
            mgr.get_lock(await backlog.get())

        assert mgr._run_locks.get("r") is lock, (
            "the backstop evicted a lock that had a waiter; the next caller "
            "would get a different lock object and both would run"
        )
        await task

    @pytest.mark.asyncio
    async def test_two_coroutines_never_share_a_run_critical_section(
        self, manager,
    ):
        """The consequence, stated as the property that actually matters."""
        mgr, _ = manager
        inside: list[str] = []
        overlaps: list[list[str]] = []

        async def critical(name: str) -> None:
            async with mgr.get_lock("r"):
                inside.append(name)
                if len(inside) > 1:
                    overlaps.append(list(inside))
                await asyncio.sleep(0.01)
                inside.remove(name)

        held = mgr.get_lock("r")
        await held.acquire()
        b = asyncio.create_task(critical("B"))
        await asyncio.sleep(0)

        backlog: asyncio.Queue = asyncio.Queue()
        for other in ("s", "t", "u"):
            backlog.put_nowait(other)
        held.release()
        while not backlog.empty():
            mgr.get_lock(await backlog.get())

        c = asyncio.create_task(critical("C"))
        await asyncio.gather(b, c)

        assert not overlaps, f"same-run mutual exclusion broken: {overlaps}"

    @pytest.mark.asyncio
    async def test_release_lock_refuses_a_lock_with_a_waiter(self, manager):
        """``release_lock`` reads the same signal, so it needs the same fix."""
        mgr, _ = manager
        lock = mgr.get_lock("r")
        await lock.acquire()

        async def waiter() -> None:
            async with mgr.get_lock("r"):
                pass

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)

        lock.release()
        assert mgr.release_lock("r") is False
        assert mgr._run_locks.get("r") is lock
        await task


class TestTheBackstopStillWorks:
    """Closing the window must not disable the cap it was added for."""

    @pytest.mark.asyncio
    async def test_idle_locks_are_still_evicted(self, manager):
        mgr, module = manager
        for run_id in ("a", "b", "c", "d", "e"):
            mgr.get_lock(run_id)
        assert mgr.lock_count() <= module._MAX_RUN_LOCKS

    @pytest.mark.asyncio
    async def test_an_idle_lock_is_still_released_on_terminal(self, manager):
        mgr, _ = manager
        mgr.get_lock("r")
        assert mgr.release_lock("r") is True
        assert mgr.lock_count() == 0


class TestTheContractCallersDependOn:
    @pytest.mark.asyncio
    async def test_the_lock_is_still_an_asyncio_lock(self, manager):
        """``OrchestrationWorker._dispatch_edit_node_data`` guards with
        ``isinstance(candidate, asyncio.Lock)`` and falls back to running
        WITHOUT the lock when that check fails — so a non-Lock subclass here
        would silently drop same-run serialisation for PATCH /data."""
        mgr, _ = manager
        assert isinstance(mgr.get_lock("r"), asyncio.Lock)

    @pytest.mark.asyncio
    async def test_in_use_tracks_waiters_and_survives_cancellation(
        self, manager,
    ):
        mgr, _ = manager
        lock = mgr.get_lock("r")
        await lock.acquire()
        assert lock.in_use()

        async def waiter() -> None:
            async with lock:
                pass

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        lock.release()
        assert not lock.in_use(), (
            "a cancelled waiter must not pin the lock forever — that would "
            "turn the correctness fix back into the leak it replaced"
        )

    @pytest.mark.asyncio
    async def test_budget_snapshot_reports_contention(self, manager):
        mgr, _ = manager
        lock = mgr.get_lock("r")
        await lock.acquire()

        async def waiter() -> None:
            async with mgr.get_lock("r"):
                pass

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        snap = mgr.budget_snapshot()
        assert snap["held"] == 1
        assert snap["in_use"] == 1
        lock.release()
        await task
