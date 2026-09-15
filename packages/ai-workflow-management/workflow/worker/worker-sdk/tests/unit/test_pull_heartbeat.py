"""R8 (SA-2055) §3 / B-A — the highest-severity finding in SA-2047.

Today the pull heartbeat lives INSIDE `if not tasks:` (worker_pull:279), so a
continuously busy pull worker never heartbeats at all — and SA-2051 (R4)'s
"stale -> unhealthy -> remove" sweeper would remove a LIVE, BUSY worker
mid-task. R8 MUST land before R4.

The response was also discarded, so pull workers never saw `re_register` and
never self-healed after an executor restart; and the failure path was
`except Exception: pass`, which is how this survived to production.
"""

import asyncio

import pytest

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer3_adapters.controllers.worker_pull import _maybe_heartbeat


class _Reg:
    def __init__(self, hb_result=None, hb_raises=False, register_raises=False):
        self.hb_result = hb_result if hb_result is not None else {}
        self.hb_raises = hb_raises
        self.register_raises = register_raises
        self.heartbeats = 0
        self.registers = 0
    async def heartbeat(self, wid, status):
        self.heartbeats += 1
        if self.hb_raises:
            raise RuntimeError("hb boom")
        return self.hb_result
    async def register(self, reg):
        self.registers += 1
        if self.register_raises:
            raise RuntimeError("register boom")
        return "wid-2"


class _Logger:
    def __init__(self): self.events = []
    def info(self, msg, **kw): self.events.append(("info", str(msg)))
    def error(self, msg, **kw): self.events.append(("error", str(msg)))
    def warning(self, msg, **kw): self.events.append(("warning", str(msg)))


def _reg_entity():
    return WorkerRegistration(worker_type="pw1", version="1", endpoint="pull", delivery_mode="pull")


class TestGate:
    @pytest.mark.asyncio
    async def test_heartbeats_when_the_interval_elapsed(self):
        r = _Reg()
        wid, last = await _maybe_heartbeat(r, "wid-1", _reg_entity(), 0.0, 100.0, 30.0, None)
        assert r.heartbeats == 1
        assert wid == "wid-1"
        assert last == 100.0          # stamp advances to `now`

    @pytest.mark.asyncio
    async def test_silent_before_the_interval_elapses(self):
        """The `>= hb_interval` gate is unchanged by R8 — only the branch moved.
        Hoisting must not multiply the heartbeat rate."""
        r = _Reg()
        wid, last = await _maybe_heartbeat(r, "wid-1", _reg_entity(), 100.0, 110.0, 30.0, None)
        assert r.heartbeats == 0
        assert last == 100.0          # stamp NOT advanced

    @pytest.mark.asyncio
    async def test_no_registry_is_a_no_op(self):
        """No registry configured (registry=None) -> there is nothing to talk to,
        whatever the wid. This is the ONLY no-op that stays true forever.

        Deliberately NOT asserted here: that a blank wid is a no-op. It is one
        today, and that is precisely B-B — Task 13 makes a blank wid mean
        "register me". Pinning it as a contract here would mean writing a test
        this same plan has to un-write six tasks later."""
        r = _Reg()
        assert await _maybe_heartbeat(None, "wid-1", _reg_entity(), 0.0, 100.0, 0.0, None) == ("wid-1", 0.0)
        assert await _maybe_heartbeat(None, "", _reg_entity(), 0.0, 100.0, 0.0, None) == ("", 0.0)
        assert r.heartbeats == 0
        assert r.registers == 0


class TestReRegister:
    @pytest.mark.asyncio
    async def test_honors_re_register_and_adopts_the_new_wid(self):
        """§9 checkbox 7 — pull workers must self-heal after an executor restart.
        Same protocol SERVER mode has always honored (worker_server:149-153)."""
        r = _Reg(hb_result={"re_register": True})
        log = _Logger()
        wid, last = await _maybe_heartbeat(r, "wid-1", _reg_entity(), 0.0, 100.0, 30.0, log)
        assert r.registers == 1
        assert wid == "wid-2"
        assert last == 100.0

    @pytest.mark.asyncio
    async def test_no_re_register_when_not_signaled(self):
        for result in ({}, {"re_register": False}, None, "not-a-dict"):
            r = _Reg(hb_result=result)
            wid, _ = await _maybe_heartbeat(r, "wid-1", _reg_entity(), 0.0, 100.0, 30.0, None)
            assert r.registers == 0
            assert wid == "wid-1"


class TestFailureIsLoggedNotSwallowed:
    @pytest.mark.asyncio
    async def test_failure_is_logged(self):
        """§9 checkbox 8. The old code was `except Exception: pass`."""
        r = _Reg(hb_raises=True)
        log = _Logger()
        wid, last = await _maybe_heartbeat(r, "wid-1", _reg_entity(), 0.0, 100.0, 30.0, log)
        assert any(lvl == "error" and "pw1" in msg for lvl, msg in log.events), log.events
        assert wid == "wid-1"

    @pytest.mark.asyncio
    async def test_failure_advances_the_stamp_to_throttle_retries(self):
        """A failed heartbeat still advances the stamp to `now`, same as success,
        so a sustained outage attempts (and logs) at most one heartbeat per
        `hb_interval` instead of retrying every busy-loop iteration."""
        r = _Reg(hb_raises=True)
        _, last = await _maybe_heartbeat(r, "wid-1", _reg_entity(), 0.0, 100.0, 30.0, None)
        assert last == 100.0

    @pytest.mark.asyncio
    async def test_failure_without_a_logger_does_not_crash(self):
        r = _Reg(hb_raises=True)
        assert await _maybe_heartbeat(r, "wid-1", _reg_entity(), 0.0, 100.0, 30.0, None) == ("wid-1", 100.0)


class TestBlankWidIsARegistrationTodo:
    """B-B for PULL. A blank wid is not 'nothing to do' — it is a registration TODO.

    Today a failed boot register leaves wid=None, and the gate in
    `_maybe_heartbeat` (`registry is not None and wid and ...`) is therefore
    False on every iteration, forever: no heartbeat, no re_register, no
    recovery. The spec's "the loop's re_register path IS the retry" cannot save
    it — re_register only ever arrives on a heartbeat RESPONSE, and you cannot
    heartbeat a wid you never received.
    """

    @pytest.mark.asyncio
    async def test_blank_wid_registers_instead_of_heartbeating(self):
        r = _Reg()
        wid, last = await _maybe_heartbeat(r, "", _reg_entity(), 0.0, 100.0, 30.0, None)
        assert r.registers == 1
        assert r.heartbeats == 0, "you cannot heartbeat a wid you never received"
        assert wid == "wid-2"
        assert last == 100.0          # talked to the executor -> stamp advances

    @pytest.mark.asyncio
    async def test_none_wid_is_treated_exactly_like_blank(self):
        """`not wid` covers both. This is why the guard change fixes B-A's
        left-over on its own, and why parking `""` (rather than `None`) is
        hygiene rather than the fix."""
        r = _Reg()
        wid, _ = await _maybe_heartbeat(r, None, _reg_entity(), 0.0, 100.0, 30.0, None)
        assert r.registers == 1
        assert wid == "wid-2"

    @pytest.mark.asyncio
    async def test_blank_wid_still_respects_the_interval(self):
        """The retry rides the SAME gate as the heartbeat: a worker booting
        against a dead executor must not re-register on every lease poll."""
        r = _Reg()
        wid, last = await _maybe_heartbeat(r, "", _reg_entity(), 100.0, 110.0, 30.0, None)
        assert r.registers == 0
        assert wid == ""
        assert last == 100.0

    @pytest.mark.asyncio
    async def test_no_registry_with_a_blank_wid_is_still_a_no_op(self):
        """The one no-op that survives B-B: registry=None means nothing to call."""
        assert await _maybe_heartbeat(None, "", _reg_entity(), 0.0, 100.0, 0.0, None) == ("", 0.0)

    @pytest.mark.asyncio
    async def test_a_failed_retry_stays_blank_and_is_logged(self):
        """Executor still down. wid stays blank -> retried next interval, forever,
        instead of never. And it is LOGGED — B-A's rule applies to the retry too."""
        r = _Reg(register_raises=True)
        log = _Logger()
        wid, last = await _maybe_heartbeat(r, "", _reg_entity(), 0.0, 100.0, 30.0, log)
        assert wid == ""
        assert last == 0.0, "a failed retry must not consume the interval"
        assert any(lvl == "error" and "pw1" in msg for lvl, msg in log.events), log.events

    @pytest.mark.asyncio
    async def test_the_retry_failure_is_not_reported_as_a_heartbeat_failure(self):
        """PULL can afford an honest message where SERVER cannot: no test pins
        PULL's log strings (test_pull_worker.py's `_container` passes
        logger=None), whereas SERVER's presenter test forces it to print
        "Heartbeat failed". Calling a failed REGISTER a failed heartbeat is the
        same species of lie as "continuing anyway" (spec §4)."""
        r = _Reg(register_raises=True)
        log = _Logger()
        await _maybe_heartbeat(r, "", _reg_entity(), 0.0, 100.0, 30.0, log)
        assert not any("Heartbeat failed" in msg for _, msg in log.events), log.events


# --- the loop: reuses the fakes test_pull_worker.py already established ---

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskResult,
)


class _LoopRegistry:
    def __init__(self, hb_result=None):
        self.hb_result = hb_result if hb_result is not None else {}
        self.registers = 0
        self.heartbeats = 0
        self.heartbeat_wids = []
        self.deregisters = []
        self.closed = False
    async def register(self, reg):
        self.registers += 1
        return f"wid-{self.registers}"
    async def heartbeat(self, wid, status):
        self.heartbeats += 1
        self.heartbeat_wids.append(wid)
        return self.hb_result
    async def deregister(self, wid):
        self.deregisters.append(wid)
    async def close(self):
        self.closed = True


class _BusyLeaseClient:
    """Never idle: every lease returns work, so the loop NEVER enters `if not tasks:`."""
    def __init__(self):
        self.lease_calls = 0
        self.callbacks = []
        self.closed = False
    async def lease(self, worker_type, max_n, wait):
        self.lease_calls += 1
        return [{"task_id": f"t{self.lease_calls}", "lease_token": "L", "payload": {}}]
    async def post_callback(self, task_id, token, result):
        self.callbacks.append(task_id)
    async def close(self):
        self.closed = True


class _Exec:
    def execute(self, cmd):
        return ExecuteTaskResult(task_id=cmd.task_id, status=TaskStatus.SUCCESS)


def _wire_busy(monkeypatch, fake_client):
    import worker_sdk.layer3_adapters.controllers.worker_pull as wp
    monkeypatch.setattr(wp, "PullTaskClient", lambda *a, **k: fake_client)
    monkeypatch.setattr(wp.settings, "LEASE_IDLE_FLOOR_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(wp.settings, "LEASE_WAIT_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(wp.settings, "HEARTBEAT_INTERVAL_SECONDS", 0.0, raising=False)
    return wp


async def _run_briefly(coro_fn, container, seconds=0.25):
    task = asyncio.create_task(coro_fn(container))
    await asyncio.sleep(seconds)
    task.cancel()
    await task
    return task


class TestBusyWorkerStillHeartbeats:
    @pytest.mark.asyncio
    async def test_continuously_busy_pull_worker_heartbeats(self, monkeypatch):
        """§9 checkbox 6 — THE bug. Before the fix this asserts 0 heartbeats:
        the lease list is never empty, so `if not tasks:` never runs, so the
        heartbeat never fires. R4 would then reap this live worker."""
        client = _BusyLeaseClient()
        registry = _LoopRegistry()
        wp = _wire_busy(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker,
                           {"execute_task": _Exec(),
                            "_dependencies": {"worker_registry": registry, "logger": None}})
        assert client.lease_calls >= 2, "the loop never iterated — test is not exercising the path"
        assert client.callbacks, "no task was processed — the worker was not busy"
        assert registry.heartbeats >= 1, "a BUSY pull worker did not heartbeat (B-A)"

    @pytest.mark.asyncio
    async def test_busy_worker_honors_re_register(self, monkeypatch):
        """§9 checkbox 7, end-to-end through the loop.

        `registers >= 2` alone can't tell fixed from broken: a loop that
        ignores the wid `_maybe_heartbeat` returns and just keeps calling
        `register` with the same stale local `wid` produces the SAME count.
        The fake now records the wid it receives on every heartbeat call, so
        we can assert those wids actually track each re-register — proving the
        loop adopted the new wid, not merely that `register` fired repeatedly
        — all the way through to the final deregister."""
        client = _BusyLeaseClient()
        registry = _LoopRegistry(hb_result={"re_register": True})
        wp = _wire_busy(monkeypatch, client)
        await _run_briefly(wp.run_pull_worker,
                           {"execute_task": _Exec(),
                            "_dependencies": {"worker_registry": registry, "logger": None}})
        # 1 boot register + at least one re-register driven by the heartbeat.
        assert registry.heartbeats >= 1
        assert registry.registers >= 2, "re_register was ignored (B-A)"
        # Every heartbeat call after a re-register must carry the NEW wid, not
        # the stale one from before it.
        assert registry.heartbeat_wids == [f"wid-{i + 1}" for i in range(len(registry.heartbeat_wids))], \
            registry.heartbeat_wids
        # ...and the final deregister must carry the latest adopted wid too.
        assert registry.deregisters and registry.deregisters[-1] == f"wid-{registry.registers}", \
            "final deregister did not carry the latest re-registered wid"


class TestLoopThrottleWriteBack:
    @pytest.mark.asyncio
    async def test_busy_worker_heartbeats_once_per_interval_not_once_per_iteration(self, monkeypatch):
        """Every other loop test in this file sets HEARTBEAT_INTERVAL_SECONDS
        to 0.0, which disables the throttle entirely — none of them would
        notice a regression that discards the stamp `_maybe_heartbeat` returns
        (e.g. `await _maybe_heartbeat(...)` instead of
        `wid, last_heartbeat = await _maybe_heartbeat(...)`): that regression
        heartbeats every iteration and the rest of this suite still passes.

        Here HEARTBEAT_INTERVAL_SECONDS is a REAL, non-zero value bigger than
        the whole run window, driving the busy loop through several iterations
        within a single interval. It must heartbeat exactly once — if the loop
        stops writing the returned stamp back, `last_heartbeat` stays frozen
        and every iteration re-heartbeats, failing this assertion."""
        client = _BusyLeaseClient()
        registry = _LoopRegistry()
        wp = _wire_busy(monkeypatch, client)
        monkeypatch.setattr(wp.settings, "HEARTBEAT_INTERVAL_SECONDS", 5.0, raising=False)
        await _run_briefly(wp.run_pull_worker,
                           {"execute_task": _Exec(),
                            "_dependencies": {"worker_registry": registry, "logger": None}})
        assert client.lease_calls >= 3, "the loop did not iterate enough to exercise the throttle"
        assert registry.heartbeats == 1, \
            "throttle write-back is broken: heartbeat fired more than once within one interval"
