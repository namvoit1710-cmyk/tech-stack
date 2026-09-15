"""PULL registration upkeep runs on its OWN clock — the executor-restart fix.

Measured 2026-08-24 on tenant-1. The worker-executor restarted; every DEPLOYED
worker re-registered off its own heartbeat and came back, and the local PULL
worker did not. Its lease calls 404'd forever while the process went on logging
"running", and `GET /api/v1/workers` showed zero rows for its type. Five runs
across three workflows hung at the first node handed to that worker.

The mechanism was one `continue`. The pull heartbeat lived INSIDE the lease loop,
after the lease call:

    try:
        tasks = await client.lease(...)
    except Exception:
        log(...); await sleep(backoff); continue      # <-- skipped the heartbeat
    wid, last = await _maybe_heartbeat(...)           # <-- the ONLY re_register path

So for exactly as long as the executor was unreachable — the restart window — the
worker never attempted the one call that carries `re_register`. The suite could
not catch it: every existing loop test drives a lease that SUCCEEDS.

These tests drive the failing lease, and the long batch drain that hid the same
hole a second way (one iteration = one whole batch, so a busy worker heartbeated
once per batch, not once per interval).
"""

import asyncio
import time

import pytest

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskResult,
)
from worker_sdk.layer3_adapters.controllers.worker_pull import (
    RegistrationKeeper,
    _maybe_heartbeat,
    _registration_gone,
)


# --- fakes -------------------------------------------------------------------


class _Registry:
    def __init__(self, hb_result=None, register_raises=False, register_fails_times=0,
                 hb_raises=None):
        self.hb_result = hb_result if hb_result is not None else {}
        self._register_raises = register_raises
        self._register_fails_times = register_fails_times
        self._hb_raises = hb_raises
        self.register_calls = 0
        self.registered_types = []
        self.heartbeats = 0
        self.heartbeat_wids = []
        self.deregister_calls = []
        self.closed = False

    async def register(self, reg):
        self.register_calls += 1
        if self._register_raises or self.register_calls <= self._register_fails_times:
            raise RuntimeError("register failed")
        self.registered_types.append(reg.worker_type)
        return f"wid-{self.register_calls}"

    async def heartbeat(self, wid, status):
        self.heartbeats += 1
        self.heartbeat_wids.append(wid)
        if self._hb_raises is not None:
            raise self._hb_raises
        return self.hb_result

    async def deregister(self, wid):
        self.deregister_calls.append(wid)

    async def close(self):
        self.closed = True


class _DeadLeaseClient:
    """Every lease raises — the executor is down / its route 404s."""

    def __init__(self, exc=None):
        self.lease_calls = 0
        self.closed = False
        self._exc = exc or RuntimeError("Client error '404 Not Found'")

    async def lease(self, worker_type, max_n, wait):
        self.lease_calls += 1
        raise self._exc

    async def post_callback(self, task_id, token, result):    # pragma: no cover
        raise AssertionError("no task can be reported when every lease fails")

    async def close(self):
        self.closed = True


class _OneSlowBatchClient:
    """One batch of slow tasks, then idle — a single lease-loop iteration that
    takes far longer than the heartbeat interval."""

    def __init__(self, n=2):
        self.lease_calls = 0
        self.callbacks = []
        self.closed = False
        self._n = n

    async def lease(self, worker_type, max_n, wait):
        self.lease_calls += 1
        if self.lease_calls == 1:
            return [{"task_id": f"t{i}", "lease_token": "L", "payload": {}}
                    for i in range(self._n)]
        return []

    async def post_callback(self, task_id, token, result):
        self.callbacks.append(task_id)

    async def close(self):
        self.closed = True


class _SlowExec:
    """Sync handler that blocks its worker thread, like a real one does."""

    def __init__(self, seconds=0.12):
        self._seconds = seconds
        self.calls = 0

    def execute(self, cmd):
        self.calls += 1
        time.sleep(self._seconds)
        return ExecuteTaskResult(task_id=cmd.task_id, status=TaskStatus.SUCCESS)


class _Logger:
    def __init__(self):
        self.events = []

    def info(self, msg, **kw):
        self.events.append(("info", str(msg)))

    def error(self, msg, **kw):
        self.events.append(("error", str(msg)))

    def warning(self, msg, **kw):
        self.events.append(("warning", str(msg)))


class _HttpError(Exception):
    """Shaped like httpx.HTTPStatusError: carries .response.status_code."""

    def __init__(self, status):
        super().__init__(f"Client error '{status}'")

        class _Resp:
            status_code = status

        self.response = _Resp()


def _reg(worker_type="pw1"):
    return WorkerRegistration(worker_type=worker_type, version="1", endpoint="pull",
                              delivery_mode="pull")


def _wire(monkeypatch, fake_client, hb_interval=0.0):
    import worker_sdk.layer3_adapters.controllers.worker_pull as wp
    monkeypatch.setattr(wp, "PullTaskClient", lambda *a, **k: fake_client)
    monkeypatch.setattr(wp.settings, "LEASE_IDLE_FLOOR_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(wp.settings, "LEASE_WAIT_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(wp.settings, "HEARTBEAT_INTERVAL_SECONDS", hb_interval,
                        raising=False)
    return wp


def _container(registry, execute_uc):
    return {"execute_task": execute_uc,
            "_dependencies": {"worker_registry": registry, "logger": None}}


async def _run_briefly(coro_fn, container, seconds=0.25):
    task = asyncio.create_task(coro_fn(container))
    await asyncio.sleep(seconds)
    task.cancel()
    await task


# --- THE regression: a failing lease must not silence the heartbeat ----------


class TestLeaseFailureDoesNotGateRegistration:
    @pytest.mark.asyncio
    async def test_worker_still_heartbeats_while_every_lease_fails(self, monkeypatch):
        """THE bug, stated as a test. Before the fix this asserts 0 heartbeats:
        the lease raises, the loop `continue`s, and `_maybe_heartbeat` is never
        reached — for the entire executor outage."""
        client = _DeadLeaseClient()
        registry = _Registry()
        wp = _wire(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker, _container(registry, _SlowExec(0.0)))
        assert client.lease_calls >= 1, "the loop never polled — test proves nothing"
        assert registry.heartbeats >= 1, \
            "a pull worker whose leases are failing did not heartbeat — it cannot " \
            "learn re_register, so it never comes back from an executor restart"

    @pytest.mark.asyncio
    async def test_re_register_is_honored_while_every_lease_fails(self, monkeypatch):
        """The executor restarted with an empty registry: it answers the heartbeat
        with re_register while the lease route is still 404ing. The worker must
        re-register anyway — that is the whole recovery."""
        client = _DeadLeaseClient()
        registry = _Registry(hb_result={"re_register": True})
        wp = _wire(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker, _container(registry, _SlowExec(0.0)))
        assert registry.register_calls >= 2, \
            "re_register was never acted on while leases were failing"
        # Each heartbeat carries the wid adopted from the previous re-register,
        # proving the new id was kept rather than register merely being re-called.
        assert registry.heartbeat_wids == \
            [f"wid-{i + 1}" for i in range(len(registry.heartbeat_wids))], \
            registry.heartbeat_wids

    @pytest.mark.asyncio
    async def test_failed_boot_register_is_retried_while_every_lease_fails(self, monkeypatch):
        """Boot against a dead executor AND leases failing — the worst case, and
        the one observed. The register retry must keep running."""
        client = _DeadLeaseClient()
        registry = _Registry(register_raises=True)
        wp = _wire(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker, _container(registry, _SlowExec(0.0)))
        assert registry.register_calls >= 2, \
            "the boot register was never retried while leases were failing"
        assert registry.deregister_calls == []   # never held a wid
        assert client.closed

    @pytest.mark.asyncio
    async def test_recovers_once_the_executor_returns_even_if_leases_never_do(self, monkeypatch):
        """Registration recovery must not depend on the lease endpoint healing —
        the two are different routes and, during a restage, heal at different
        times. The worker is back in the registry the moment register succeeds."""
        client = _DeadLeaseClient()
        registry = _Registry(register_fails_times=1)
        wp = _wire(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker, _container(registry, _SlowExec(0.0)))
        assert registry.register_calls >= 2
        assert registry.deregister_calls == ["wid-2"], \
            "the recovered registration was not adopted (or not handed back)"


# --- the second hole: one iteration is one whole BATCH -----------------------


class TestBatchDrainDoesNotGateRegistration:
    @pytest.mark.asyncio
    async def test_heartbeat_keeps_its_cadence_during_a_long_batch_drain(self, monkeypatch):
        """A lease-loop iteration drains a whole batch sequentially, so the old
        per-iteration heartbeat was really once per BATCH: four five-minute tasks
        meant one heartbeat per twenty minutes, and R4's sweeper reaps on
        staleness. Cadence is wall-clock now, so the tasks cannot slow it."""
        client = _OneSlowBatchClient(n=4)
        registry = _Registry()
        wp = _wire(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker, _container(registry, _SlowExec(0.12)),
                           seconds=0.3)
        assert client.callbacks, "no task ran — the drain path was not exercised"
        assert registry.heartbeats >= 3, \
            f"heartbeat stalled behind the batch drain ({registry.heartbeats} beats)"


# --- "the executor does not know me" is not a transient error ----------------


class TestRegistrationGone:
    def test_404_and_410_are_gone_everything_else_is_transient(self):
        assert _registration_gone(_HttpError(404))
        assert _registration_gone(_HttpError(410))
        assert not _registration_gone(_HttpError(503))
        assert not _registration_gone(RuntimeError("connection reset"))
        assert not _registration_gone(asyncio.TimeoutError())

    @pytest.mark.asyncio
    async def test_heartbeat_404_clears_the_wid_and_does_not_burn_the_interval(self):
        """`re_register` only ever arrives on a 200 body. An executor — or the CF
        router in front of a restarting one — that answers 404 instead used to
        leave the stale wid in place forever. Clearing it makes the next pass
        register; not advancing the stamp makes that pass happen now, not one
        full interval later."""
        registry = _Registry(hb_raises=_HttpError(404))
        wid, last = await _maybe_heartbeat(registry, "wid-1", _reg(), 0.0, 100.0, 30.0, None)
        assert wid == "", "a 404 heartbeat left the dead registration id in place"
        assert last == 0.0, "the re-register retry was gated behind another interval"

    @pytest.mark.asyncio
    async def test_a_transient_heartbeat_error_keeps_the_wid(self):
        """Regression guard on the conservative half: only a definite 'no such
        worker' clears the id. A reset/timeout must not throw away a live
        registration and churn the registry."""
        registry = _Registry(hb_raises=_HttpError(503))
        wid, last = await _maybe_heartbeat(registry, "wid-1", _reg(), 0.0, 100.0, 30.0, None)
        assert wid == "wid-1"
        assert last == 100.0     # unchanged: throttles a sustained outage


# --- the keeper itself, multi-type (what the pull_multi forks use) -----------


class TestKeeperMultiType:
    @pytest.mark.asyncio
    async def test_one_types_register_failure_does_not_skip_the_others(self):
        """A shared try around the register calls meant the first failure skipped
        every remaining node type. jira-worker registers 22 from one process."""
        class _FirstFails(_Registry):
            async def register(self, reg):
                self.register_calls += 1
                if reg.worker_type == "a":
                    raise RuntimeError("down for this one")
                self.registered_types.append(reg.worker_type)
                return f"wid-{reg.worker_type}"

        registry = _FirstFails()
        keeper = RegistrationKeeper(registry, [_reg("a"), _reg("b"), _reg("c")], 30.0, None)
        await keeper.register_all()
        assert keeper.wids == ["", "wid-b", "wid-c"]
        assert registry.registered_types == ["b", "c"]

    @pytest.mark.asyncio
    async def test_deregister_skips_blank_wids(self):
        """A type that never registered has nothing to undo, and deregistering ""
        would 404 against the executor."""
        class _FirstFails(_Registry):
            async def register(self, reg):
                self.register_calls += 1
                if reg.worker_type == "a":
                    raise RuntimeError("down for this one")
                return f"wid-{reg.worker_type}"

        registry = _FirstFails()
        keeper = RegistrationKeeper(registry, [_reg("a"), _reg("b")], 30.0, None)
        await keeper.register_all()
        await keeper.deregister_all()
        assert registry.deregister_calls == ["wid-b"]

    @pytest.mark.asyncio
    async def test_run_retries_every_missing_registration(self):
        registry = _Registry(register_fails_times=2)     # both boot calls fail
        keeper = RegistrationKeeper(registry, [_reg("a"), _reg("b")], 0.0, None)
        await keeper.register_all()
        assert keeper.wids == ["", ""]
        stop = asyncio.Event()
        task = asyncio.create_task(keeper.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await task
        assert all(keeper.wids), f"a missing registration was never retried: {keeper.wids}"

    @pytest.mark.asyncio
    async def test_run_is_a_noop_without_a_registry(self):
        """An embedder with no registry must not be given a task that spins."""
        keeper = RegistrationKeeper(None, [_reg()], 0.0, None)
        await keeper.register_all()
        stop = asyncio.Event()
        await asyncio.wait_for(keeper.run(stop), timeout=1.0)   # returns immediately
        await keeper.deregister_all()
        assert keeper.wids == [""]

    @pytest.mark.asyncio
    async def test_keeper_logs_a_registration_failure_rather_than_swallowing_it(self):
        """`except Exception: pass` is how this class of bug survived to
        production — the process looked healthy the whole time."""
        log = _Logger()
        registry = _Registry(register_raises=True)
        keeper = RegistrationKeeper(registry, [_reg()], 30.0, log)
        await keeper.register_all()
        assert any(kind == "error" and "registration failed" in msg.lower()
                   for kind, msg in log.events), log.events


class TestKeeperShutdownOrdering:
    @pytest.mark.asyncio
    async def test_no_registration_survives_shutdown(self, monkeypatch):
        """The keeper must be stopped BEFORE the deregister: a pass landing after
        it would re-register on the way out and leave a ghost row that only the
        sweeper could clear."""
        client = _DeadLeaseClient()
        registry = _Registry(hb_result={"re_register": True})
        wp = _wire(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker, _container(registry, _SlowExec(0.0)))
        assert registry.deregister_calls, "shutdown handed nothing back"
        assert registry.deregister_calls[-1] == f"wid-{registry.register_calls}", \
            "a register landed after the final deregister — ghost row left behind"
