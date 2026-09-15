"""Lease renewal is bounded — an uncapped renewer disarms the executor's net.

`_renew_loop` renews while "the exec core has not returned", which is NOT the same
as "the task is still progressing". A wedged handler (socket read with no timeout,
hung subprocess) never returns, so before this cap the loop renewed forever.

That is worse than one stuck task. The executor's recovery is
`memory_ready_task_store.sweep_expired_leases`, which only acts on rows matching
`state == LEASED and deadline < now`. Renewal keeps pushing `deadline` forward, so
that condition is never true: `attempt_count` never increments, MAX_LEASE_ATTEMPTS
is never reached, and the task is never re-enqueued or POISONED. The whole B8 net
is switched off by the thing that was supposed to be protected by it.

The property under test is therefore simply: **a renew loop against a client that
always says yes must still terminate on its own.** Every test here is a variation
on that, plus the two boundaries that keep the cap honest — it must not fire early
on a legitimately long task, and the opt-out must still exist for someone who
knowingly wants the old behaviour.
"""

import asyncio
import time

import pytest

from worker_sdk.layer3_adapters.controllers.worker_pull import (
    _DEFAULT_RENEW_MAX_SECONDS,
    _batch_renew_loop,
    _renew_budget_spent,
    _renew_loop,
    process_leased_task,
)
from worker_sdk.layer4_frameworks.config.app_config import Settings

from .test_pull_worker import _RenewingClient, _SlowExec


class _AlwaysRenews:
    """The shape that used to loop forever: never 409s, never errors."""

    def __init__(self):
        self.calls = 0

    async def renew(self, task_id, lease_token):
        self.calls += 1
        return "2026-07-15T13:00:00+00:00"


class TestBudgetPredicate:
    def test_not_spent_before_the_budget(self):
        assert _renew_budget_spent(time.monotonic(), 60.0) is False

    def test_spent_after_the_budget(self):
        assert _renew_budget_spent(time.monotonic() - 61.0, 60.0) is True

    def test_spent_exactly_at_the_boundary(self):
        assert _renew_budget_spent(time.monotonic() - 60.0, 60.0) is True

    @pytest.mark.parametrize("disabled", [0, 0.0, -1])
    def test_non_positive_budget_is_the_opt_out(self, disabled):
        """Documented escape hatch: <= 0 restores unbounded renewal."""
        assert _renew_budget_spent(time.monotonic() - 10_000.0, disabled) is False


class TestTheDefaultIsARealCap:
    """A safety mechanism that defaults to off is the bug it was written to stop."""

    def test_module_default_is_finite_and_positive(self):
        assert 0 < _DEFAULT_RENEW_MAX_SECONDS < float("inf")

    def test_settings_default_is_finite_and_positive(self):
        assert 0 < Settings().LEASE_RENEW_MAX_SECONDS < float("inf")

    def test_settings_default_covers_the_executor_ceiling(self):
        """Shorter than the executor's TASK_TIMEOUT_MAX_SECONDS (3600) would cut a
        legitimately long task short before the executor would have."""
        assert Settings().LEASE_RENEW_MAX_SECONDS >= 3600.0

    @pytest.mark.asyncio
    async def test_a_caller_that_passes_no_budget_still_gets_one(self):
        """Guards the signature: if the default ever became 0/None, an omitted
        argument would silently restore unbounded renewal."""
        import inspect

        default = inspect.signature(_renew_loop).parameters["max_seconds"].default
        assert default == _DEFAULT_RENEW_MAX_SECONDS
        assert _renew_budget_spent(time.monotonic() - 10_000.0, default) is True


class TestRenewLoopTerminates:
    @pytest.mark.asyncio
    async def test_always_successful_renewal_still_ends(self):
        """The regression. Before the cap this call never returned."""
        client = _AlwaysRenews()
        await asyncio.wait_for(
            _renew_loop(client, "t1", "L", asyncio.Event(), 0.02, None, 0.15),
            timeout=3,
        )
        assert client.calls >= 1, "it should have renewed before giving up"

    @pytest.mark.asyncio
    async def test_it_stops_renewing_once_the_budget_is_spent(self):
        client = _AlwaysRenews()
        await asyncio.wait_for(
            _renew_loop(client, "t1", "L", asyncio.Event(), 0.02, None, 0.15),
            timeout=3,
        )
        after_return = client.calls
        await asyncio.sleep(0.15)
        assert client.calls == after_return, "no renewal may outlive the budget"

    @pytest.mark.asyncio
    async def test_a_generous_budget_does_not_cut_a_long_task_short(self):
        """The cap must be a backstop, not a second timeout. With room to spare the
        loop keeps renewing and only stops when the task signals done."""
        client = _AlwaysRenews()
        stop = asyncio.Event()
        task = asyncio.create_task(
            _renew_loop(client, "t1", "L", stop, 0.02, None, 30.0)
        )
        await asyncio.sleep(0.15)
        assert client.calls >= 2, "still renewing while the task runs"
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, timeout=3)

    @pytest.mark.asyncio
    async def test_opt_out_restores_unbounded_renewal(self):
        client = _AlwaysRenews()
        task = asyncio.create_task(
            _renew_loop(client, "t1", "L", asyncio.Event(), 0.02, None, 0)
        )
        await asyncio.sleep(0.2)
        assert not task.done(), "max_seconds<=0 must never stop on its own"
        assert client.calls >= 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_finishing_first_still_wins_over_the_budget(self):
        """stop.set() is checked before the budget — a task that completes must not
        be reported as having blown its renewal budget."""
        client = _AlwaysRenews()
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(
            _renew_loop(client, "t1", "L", stop, 0.02, None, 0.01), timeout=3
        )
        assert client.calls == 0


class TestBatchRenewLoopTerminates:
    @pytest.mark.asyncio
    async def test_always_successful_batch_renewal_still_ends(self):
        client = _AlwaysRenews()
        superseded: set[str] = set()
        await asyncio.wait_for(
            _batch_renew_loop(
                client, {"t1": "L1", "t2": "L2"}, asyncio.Event(), 0.02,
                superseded, None, 0.15,
            ),
            timeout=3,
        )
        assert client.calls >= 1

    @pytest.mark.asyncio
    async def test_budget_exhaustion_does_not_mark_tasks_superseded(self):
        """The semantic that matters. `superseded` means "the executor reclaimed
        this, do not run it" and makes the caller SKIP the task. A spent budget is
        different: the task is still running on a lease that has not lapsed yet, so
        its result may well still be accepted. Marking it superseded would throw
        away a good result and burn the work."""
        client = _AlwaysRenews()
        superseded: set[str] = set()
        await asyncio.wait_for(
            _batch_renew_loop(
                client, {"t1": "L1", "t2": "L2"}, asyncio.Event(), 0.02,
                superseded, None, 0.15,
            ),
            timeout=3,
        )
        assert superseded == set()

    @pytest.mark.asyncio
    async def test_supersede_still_retires_a_single_task(self):
        """The pre-existing 409 behaviour is untouched by the cap."""

        class _SupersedesT1:
            def __init__(self):
                self.calls = 0

            async def renew(self, task_id, lease_token):
                self.calls += 1
                return None if task_id == "t1" else "2026-07-15T13:00:00+00:00"

        client = _SupersedesT1()
        superseded: set[str] = set()
        await asyncio.wait_for(
            _batch_renew_loop(
                client, {"t1": "L1", "t2": "L2"}, asyncio.Event(), 0.02,
                superseded, None, 0.15,
            ),
            timeout=3,
        )
        assert superseded == {"t1"}


class TestProcessLeasedTaskThreadsTheBudget:
    @pytest.mark.asyncio
    async def test_budget_stops_renewal_while_the_task_is_still_running(self):
        """A wedged handler must not keep its lease alive to the end of time."""
        exec_uc, client = _SlowExec(0.4), _RenewingClient()
        await process_leased_task(
            exec_uc, client, {"task_id": "t1", "lease_token": "L", "payload": {}},
            renew_interval=0.02, renew_max_seconds=0.1,
        )
        assert client.renew_calls >= 1
        # Renewal gave up partway through a 0.4s task, so it cannot have renewed
        # for the whole run: far fewer calls than 0.4s / 0.02s of ticking.
        assert client.renew_calls < 10

    @pytest.mark.asyncio
    async def test_a_generous_budget_renews_for_the_whole_task(self):
        exec_uc, client = _SlowExec(0.25), _RenewingClient()
        await process_leased_task(
            exec_uc, client, {"task_id": "t1", "lease_token": "L", "payload": {}},
            renew_interval=0.05, renew_max_seconds=30.0,
        )
        assert client.renew_calls >= 2
        assert client.callbacks and client.callbacks[0][0] == "t1"
