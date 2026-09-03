"""B3 (SA-2028) SDK half — PULL run mode: registration field, lease client, loop body."""

import json

import httpx
import pytest

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskResult,
)
from worker_sdk.layer3_adapters.controllers.worker_pull import (
    PullTaskClient, process_leased_task,
)


# --- registration field ---

def test_registration_delivery_mode_default_push():
    r = WorkerRegistration(worker_type="w", version="1", endpoint="pull")
    assert r.delivery_mode == "push"

def test_registration_delivery_mode_pull():
    r = WorkerRegistration(worker_type="w", version="1", endpoint="pull", delivery_mode="pull")
    assert r.delivery_mode == "pull"

def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_http_register_sends_delivery_mode():
    """The SDK's register() must actually PUT delivery_mode on the wire so a pull
    worker registers as pull (guards the SA-1735-style 'field silently dropped' gap)."""
    from worker_sdk.layer4_frameworks.providers.registry.http_worker_registry import (
        HttpWorkerRegistry,
    )
    captured = {}
    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"status": "ok", "data": {"worker_id": "wid-1"}})
    reg_client = HttpWorkerRegistry()
    await reg_client._client.aclose()
    reg_client._client = _mock_client(handler)
    reg = WorkerRegistration(worker_type="w", version="1", endpoint="pull", delivery_mode="pull")
    wid = await reg_client.register(reg)
    assert wid == "wid-1"
    assert captured["body"]["delivery_mode"] == "pull"
    await reg_client._client.aclose()


# --- PullTaskClient ---

@pytest.mark.asyncio
async def test_lease_unwraps_envelope_and_sends_tenant():
    seen = {}
    def handler(req):
        seen["url"] = str(req.url)
        seen["tenant"] = req.headers.get("X-Tenant-Id")
        return httpx.Response(200, json={"status": "ok", "data": [
            {"task_id": "t1", "lease_token": "L", "payload": {"k": 1}},
        ]})
    client = PullTaskClient("http://exec", tenant="t-A", client=_mock_client(handler))
    got = await client.lease("pw1", 3, 0)
    assert [t["task_id"] for t in got] == ["t1"]
    assert "workers/type/pw1/tasks/lease" in seen["url"]
    assert "max=3" in seen["url"]
    assert seen["tenant"] == "t-A"

@pytest.mark.asyncio
async def test_lease_empty_returns_empty_list():
    client = PullTaskClient("http://exec",
                            client=_mock_client(lambda r: httpx.Response(200, json={"status": "ok", "data": []})))
    assert await client.lease("pw1", 1, 0) == []

@pytest.mark.asyncio
async def test_lease_non_list_data_is_tolerated():
    """An out-of-contract body (dict/null under `data`) must NOT crash the worker —
    a non-list means 'no tasks' (red-team B3 LOW)."""
    for payload in ({"status": "ok", "data": {"oops": 1}},
                    {"status": "ok", "data": None},
                    {"unexpected": True}):
        client = PullTaskClient("http://exec",
                                client=_mock_client(lambda r, p=payload: httpx.Response(200, json=p)))
        assert await client.lease("pw1", 1, 0) == []

@pytest.mark.asyncio
async def test_post_callback_maps_status_and_carries_lease_token_in_header():
    captured = {}
    def handler(req):
        captured["body"] = json.loads(req.content)
        captured["url"] = str(req.url)
        captured["lease_hdr"] = req.headers.get("X-Lease-Token")
        captured["tenant_hdr"] = req.headers.get("X-Tenant-Id")
        return httpx.Response(200, json={"status": "ok", "data": {}})
    client = PullTaskClient("http://exec", tenant="t-A", worker_type="pw1", client=_mock_client(handler))

    ok = ExecuteTaskResult(task_id="t1", status=TaskStatus.SUCCESS, outputs={"o": 1}, duration_ms=5.0)
    await client.post_callback("t1", "LEASE-1", ok)
    b = captured["body"]
    assert b["task_id"] == "t1"
    assert b["status"] == "success"
    assert b["outputs"] == {"o": 1}
    assert b["worker_type"] == "pw1"                 # triple-binding claim in body
    assert captured["lease_hdr"] == "LEASE-1"        # lease token HEADER-only (SPEC §5)
    assert "lease_token" not in b                     # never in the body
    assert captured["tenant_hdr"] == "t-A"
    assert "tasks/callback" in captured["url"]

    err = ExecuteTaskResult(task_id="t2", status=TaskStatus.ERROR, error="boom")
    await client.post_callback("t2", "LEASE-2", err)
    assert captured["body"]["status"] == "error"
    assert captured["body"]["error"] == "boom"


# --- loop body ---

class _FakeExec:
    def __init__(self, result):
        self.result = result
        self.calls = []
    def execute(self, cmd):
        self.calls.append(cmd)
        return self.result

class _FakeClient:
    def __init__(self):
        self.callbacks = []
    async def post_callback(self, task_id, token, result):
        self.callbacks.append((task_id, token, result))

@pytest.mark.asyncio
async def test_process_leased_task_runs_core_and_reports():
    result = ExecuteTaskResult(task_id="t1", status=TaskStatus.SUCCESS, outputs={"o": 1}, duration_ms=3.0)
    ex, cl = _FakeExec(result), _FakeClient()
    task = {"task_id": "t1", "lease_token": "L", "payload": {"in": 1},
            "node_config": {"p": 2}, "run_id": "r1"}
    await process_leased_task(ex, cl, task)
    cmd = ex.calls[0]
    assert cmd.task_id == "t1"
    assert cmd.inputs == {"in": 1}
    assert cmd.parameters == {"p": 2}
    assert cmd.correlation_id == "r1"
    assert cl.callbacks == [("t1", "L", result)]

@pytest.mark.asyncio
async def test_renew_returns_deadline_and_none_on_409():
    def handler(req):
        if req.url.path.endswith("/lease/renew"):
            if req.headers.get("X-Lease-Token") == "good":
                return httpx.Response(200, json={"status": "ok", "data": {"deadline": "2026-07-15T13:00:00+00:00"}})
            return httpx.Response(409, json={"status": "error", "error": {"code": "TASK_SUPERSEDED"}})
        return httpx.Response(404)
    client = PullTaskClient("http://exec", client=_mock_client(handler))
    assert await client.renew("t1", "good") == "2026-07-15T13:00:00+00:00"
    assert await client.renew("t1", "bad") is None


class _SlowExec:
    def __init__(self, seconds):
        self.seconds = seconds
    def execute(self, cmd):
        import time
        time.sleep(self.seconds)
        return ExecuteTaskResult(task_id=cmd.task_id, status=TaskStatus.SUCCESS)

class _RenewingClient(_FakeClient):
    def __init__(self):
        super().__init__()
        self.renew_calls = 0
    async def renew(self, task_id, lease_token):
        self.renew_calls += 1
        return "2026-07-15T13:00:00+00:00"

@pytest.mark.asyncio
async def test_process_leased_task_renews_during_long_task():
    """With renew_interval set, a long-running task's lease is renewed while it
    runs, and renewal stops once it finishes (B4)."""
    ex, cl = _SlowExec(0.25), _RenewingClient()
    task = {"task_id": "t1", "lease_token": "L", "payload": {}}
    await process_leased_task(ex, cl, task, renew_interval=0.05)
    assert cl.renew_calls >= 1          # renewed while running
    assert cl.callbacks and cl.callbacks[0][0] == "t1"
    # renewal stopped: no further renews after completion
    before = cl.renew_calls
    await asyncio.sleep(0.15)
    assert cl.renew_calls == before

@pytest.mark.asyncio
async def test_process_leased_task_no_renew_when_interval_zero():
    ex, cl = _SlowExec(0.05), _RenewingClient()
    await process_leased_task(ex, cl, {"task_id": "t1", "lease_token": "L"}, renew_interval=0.0)
    assert cl.renew_calls == 0          # renewal disabled


@pytest.mark.asyncio
async def test_process_leased_task_cancel_stops_renewal(monkeypatch):
    """Cancelling a running task (Ctrl-C) must stop the renewal — no orphan."""
    ex, cl = _SlowExec(2.0), _RenewingClient()
    task = asyncio.create_task(
        process_leased_task(ex, cl, {"task_id": "t1", "lease_token": "L"}, renew_interval=0.05)
    )
    await asyncio.sleep(0.12)           # let renewal fire at least once
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    before = cl.renew_calls
    await asyncio.sleep(0.15)
    assert cl.renew_calls == before     # renewal did NOT outlive the cancelled task


# --- _renew_loop: transient vs supersede (red-team B4 HIGH) ---

def test_derive_renew_interval():
    from worker_sdk.layer3_adapters.controllers.worker_pull import _derive_renew_interval
    assert _derive_renew_interval({"lease_ttl_seconds": 60}, True, 0.5, 1.0) == 30.0
    assert _derive_renew_interval({"lease_ttl_seconds": 60}, False, 0.5, 1.0) == 0.0   # disabled
    assert _derive_renew_interval({}, True, 0.5, 1.0) == 0.0                            # no ttl
    assert _derive_renew_interval({"lease_ttl_seconds": 1}, True, 0.5, 1.0) == 1.0      # floored

@pytest.mark.asyncio
async def test_renew_loop_stops_on_supersede():
    from worker_sdk.layer3_adapters.controllers.worker_pull import _renew_loop
    class C:
        def __init__(self): self.calls = 0
        async def renew(self, tid, tok):
            self.calls += 1
            return None                 # explicit 409 supersede
    c = C()
    await asyncio.wait_for(_renew_loop(c, "t1", "L", asyncio.Event(), interval=0.05), timeout=2)
    assert c.calls == 1                 # one renew → superseded → stop

@pytest.mark.asyncio
async def test_renew_loop_continues_on_transient_error():
    """The HIGH: a transient renew error must NOT stop renewal (only a real 409 does)."""
    from worker_sdk.layer3_adapters.controllers.worker_pull import _renew_loop
    class C:
        def __init__(self): self.calls = 0
        async def renew(self, tid, tok):
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError("connection reset")   # transient
            return None                                  # finally supersede to end the loop
    c = C()
    await asyncio.wait_for(_renew_loop(c, "t1", "L", asyncio.Event(), interval=0.05), timeout=2)
    assert c.calls == 3                 # 2 transient errors CONTINUED, 3rd (409) stopped

@pytest.mark.asyncio
async def test_process_leased_task_missing_optional_fields():
    result = ExecuteTaskResult(task_id="t1", status=TaskStatus.SUCCESS)
    ex, cl = _FakeExec(result), _FakeClient()
    await process_leased_task(ex, cl, {"task_id": "t1"})   # no payload/config/token
    assert ex.calls[0].inputs == {}
    assert ex.calls[0].parameters == {}
    assert cl.callbacks[0][1] == ""     # empty lease_token tolerated


# --- run_pull_worker loop lifecycle (red-team B3 MEDIUM: was untested) ---

import asyncio


class _FakeRegistry:
    # B-B (SA-2055): `register_calls` and `register_fails_times` are new. The old
    # fake counted heartbeats and deregisters but NOT registers, so a retry was
    # invisible through it — which is how test_loop_survives_register_failure
    # came to accept "boots against a dead executor, never recovers" as passing.
    def __init__(self, register_raises=False, register_fails_times=0):
        self._register_raises = register_raises          # down forever
        self._register_fails_times = register_fails_times  # down for the first N calls, then up
        self.register_calls = 0
        self.deregister_calls = []
        self.heartbeats = 0
        self.closed = False
    async def register(self, reg):
        self.register_calls += 1
        if self._register_raises or self.register_calls <= self._register_fails_times:
            raise RuntimeError("register failed")
        return "wid-1"
    async def heartbeat(self, wid, status):
        self.heartbeats += 1
        return {}
    async def deregister(self, wid):
        self.deregister_calls.append(wid)
    async def close(self):
        self.closed = True


class _FakeLeaseClient:
    def __init__(self, scripts):
        self._scripts = scripts          # callable -> list, or list-of-lists
        self.lease_calls = 0
        self.callbacks = []
        self.closed = False
    async def lease(self, worker_type, max_n, wait):
        self.lease_calls += 1
        if callable(self._scripts):
            return self._scripts()
        idx = min(self.lease_calls - 1, len(self._scripts) - 1)
        return self._scripts[idx]
    async def post_callback(self, task_id, token, result):
        self.callbacks.append((task_id, token, result))
    async def close(self):
        self.closed = True


class _LoopFakeExec:
    def __init__(self, raises=False):
        self.raises = raises
        self.calls = 0
    def execute(self, cmd):
        self.calls += 1
        if self.raises:
            raise RuntimeError("exec boom")
        return ExecuteTaskResult(task_id=cmd.task_id, status=TaskStatus.SUCCESS)


def _wire(monkeypatch, fake_client, fake_reg):
    import worker_sdk.layer3_adapters.controllers.worker_pull as wp
    monkeypatch.setattr(wp, "PullTaskClient", lambda *a, **k: fake_client)
    monkeypatch.setattr(wp.settings, "LEASE_IDLE_FLOOR_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(wp.settings, "LEASE_WAIT_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(wp.settings, "HEARTBEAT_INTERVAL_SECONDS", 0.0, raising=False)
    return wp


def _container(fake_reg, fake_exec):
    return {"execute_task": fake_exec,
            "_dependencies": {"worker_registry": fake_reg, "logger": None}}


@pytest.mark.asyncio
async def test_loop_does_not_hot_spin_on_empty(monkeypatch):
    """The HIGH: with the server returning [] instantly (wait=0 / pull disabled),
    the idle floor must cap the poll rate — NOT spin thousands of times."""
    fake_client = _FakeLeaseClient(scripts=lambda: [])
    fake_reg = _FakeRegistry()
    wp = _wire(monkeypatch, fake_client, fake_reg)
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, _LoopFakeExec())))
    await asyncio.sleep(0.3)
    task.cancel()
    await task
    # floor 0.05s over ~0.3s → ~a handful of polls. Without the floor: thousands.
    assert fake_client.lease_calls <= 15
    assert fake_client.closed


@pytest.mark.asyncio
async def test_loop_survives_a_task_error(monkeypatch):
    fake_client = _FakeLeaseClient(scripts=[[{"task_id": "t1", "lease_token": "L", "payload": {}}], []])
    fake_reg = _FakeRegistry()
    fake_exec = _LoopFakeExec(raises=True)     # process_leased_task will raise
    wp = _wire(monkeypatch, fake_client, fake_reg)
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, fake_exec)))
    await asyncio.sleep(0.25)
    task.cancel()
    await task
    assert fake_exec.calls >= 1                 # attempted the failing task
    assert fake_client.lease_calls >= 2         # loop CONTINUED past the error
    assert fake_client.closed


@pytest.mark.asyncio
async def test_loop_cancel_deregisters_and_closes(monkeypatch):
    fake_client = _FakeLeaseClient(scripts=lambda: [])
    fake_reg = _FakeRegistry()
    wp = _wire(monkeypatch, fake_client, fake_reg)
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, _LoopFakeExec())))
    await asyncio.sleep(0.1)
    task.cancel()
    await task
    assert fake_reg.deregister_calls == ["wid-1"]   # clean shutdown
    assert fake_client.closed
    assert fake_reg.closed


@pytest.mark.asyncio
async def test_loop_retries_a_failed_boot_register(monkeypatch):
    """R8 (SA-2055) B-B — REWRITTEN. Was `test_loop_survives_register_failure`.

    The old test asserted `lease_calls >= 1  # loop still ran despite no wid` and
    stopped there: it treated "polls forever, unregistered, forever" as SUCCESS.
    But the loop surviving IS the zombie — wid stayed None (never rebound by the
    boot register's except branch), so the heartbeat helper's `and wid` gate was
    False on every iteration and the worker never heartbeated and never
    recovered until redeploy. The old fake had no register counter, so the
    retry it should have demanded was not even expressible.

    New contract: the loop still survives (unchanged), AND it keeps trying to
    register. The executor never comes back here, so no wid is ever obtained ->
    still nothing to deregister.
    """
    fake_client = _FakeLeaseClient(scripts=lambda: [])
    fake_reg = _FakeRegistry(register_raises=True)      # executor down, and it stays down
    wp = _wire(monkeypatch, fake_client, fake_reg)
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, _LoopFakeExec())))
    await asyncio.sleep(0.15)
    task.cancel()
    await task
    assert fake_client.lease_calls >= 1        # loop still ran despite no wid (kept)
    # THE new demand: 1 boot register + >=1 retry from the loop. This is the
    # assertion whose absence let B-B live in PULL.
    assert fake_reg.register_calls >= 2, "the boot register was never retried (B-B)"
    assert fake_reg.deregister_calls == []     # never got a wid -> nothing to deregister
    assert fake_client.closed                  # client still closed in finally


@pytest.mark.asyncio
async def test_loop_recovers_once_the_executor_comes_back(monkeypatch):
    """§9 checkbox 11. The half the old test could not express: the worker booted
    against a dead executor and RECOVERS — no redeploy."""
    fake_client = _FakeLeaseClient(scripts=lambda: [])
    fake_reg = _FakeRegistry(register_fails_times=1)    # boot fails, next attempt succeeds
    wp = _wire(monkeypatch, fake_client, fake_reg)
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, _LoopFakeExec())))
    await asyncio.sleep(0.15)
    task.cancel()
    await task
    assert fake_reg.register_calls >= 2, "the boot failure was never retried (B-B)"
    # It adopted the recovered wid: shutdown deregisters a REAL id, not "".
    assert fake_reg.deregister_calls == ["wid-1"]


@pytest.mark.asyncio
async def test_shutdown_never_deregisters_a_blank_wid(monkeypatch):
    """§9 checkbox 12 (B-B, all three) for PULL.

    Unlike SERVER/HEADLESS — whose bare `for wid, reg in registrations:` loops
    needed a NEW guard — worker_pull's shutdown already reads
    `if registry is not None and wid:`, and `and wid` is falsy for "" and None
    alike. So this is a REGRESSION GUARD, not a fix: it keeps that line honest
    now that the boot path parks "" instead of None.
    """
    fake_client = _FakeLeaseClient(scripts=lambda: [])
    fake_reg = _FakeRegistry(register_raises=True)
    wp = _wire(monkeypatch, fake_client, fake_reg)
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, _LoopFakeExec())))
    await asyncio.sleep(0.15)
    task.cancel()
    await task
    assert fake_reg.deregister_calls == [], 'shutdown deregistered a blank wid'
    assert fake_reg.closed


# --- batch-wide lease renewal (the executor stamps ONE deadline per batch) ---
#
# memory_ready_task_store.claim() computes `deadline_iso` ONCE, before its loop,
# and assigns that same absolute value to every claimed row — while this SDK
# drains the batch sequentially. Renewing only the running task therefore let
# every queued task's lease expire, be re-enqueued to another worker (duplicate
# execution), and made this worker's eventual callback fail on a dead token.


class _BatchRenewClient(_FakeLeaseClient):
    """Records renew calls per task_id; optionally 409s a chosen task."""

    def __init__(self, scripts, supersede: str = ""):
        super().__init__(scripts)
        self.renew_calls: dict[str, int] = {}
        self._supersede = supersede

    async def renew(self, task_id, lease_token):
        self.renew_calls[task_id] = self.renew_calls.get(task_id, 0) + 1
        if task_id == self._supersede:
            return None                      # explicit 409 TASK_SUPERSEDED
        return "2026-07-15T13:00:00+00:00"


class _SlowLoopExec:
    def __init__(self, seconds):
        self.seconds = seconds
        self.ran: list[str] = []

    def execute(self, cmd):
        import time
        self.ran.append(cmd.task_id)
        time.sleep(self.seconds)
        return ExecuteTaskResult(task_id=cmd.task_id, status=TaskStatus.SUCCESS)


def _wire_renew(monkeypatch, fake_client, fraction=0.5, floor=0.02):
    wp = _wire(monkeypatch, fake_client, None)
    monkeypatch.setattr(wp.settings, "LEASE_RENEW_ENABLED", True, raising=False)
    monkeypatch.setattr(wp.settings, "LEASE_RENEW_SAFETY_FRACTION", fraction, raising=False)
    monkeypatch.setattr(wp.settings, "LEASE_RENEW_MIN_INTERVAL_SECONDS", floor, raising=False)
    return wp


def _batch(n, ttl=0.1):
    return [{"task_id": f"t{i}", "lease_token": f"L{i}", "payload": {},
             "lease_ttl_seconds": ttl} for i in range(1, n + 1)]


@pytest.mark.asyncio
async def test_queued_tasks_are_renewed_while_the_first_one_runs(monkeypatch):
    """THE regression. Batch of 3, the first task runs long enough that the
    others' (shared, claim-time) deadline would have lapsed. Every task in the
    batch must be renewed — not just the one executing."""
    fake_client = _BatchRenewClient(scripts=[_batch(3), []])
    fake_reg = _FakeRegistry()
    wp = _wire_renew(monkeypatch, fake_client)
    slow = _SlowLoopExec(0.20)               # >> the 0.05s renew interval
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, slow)))
    await asyncio.sleep(0.35)
    task.cancel()
    await task
    # t2/t3 were still QUEUED while t1 ran — under the old per-task renewal they
    # received zero renews and their leases expired.
    assert fake_client.renew_calls.get("t2", 0) >= 1, "queued task t2 was never renewed"
    assert fake_client.renew_calls.get("t3", 0) >= 1, "queued task t3 was never renewed"
    assert fake_client.renew_calls.get("t1", 0) >= 1, "the running task lost its renewal"


@pytest.mark.asyncio
async def test_superseded_task_is_skipped_not_executed(monkeypatch):
    """A 409 during the drain means the executor reclaimed that lease and
    re-enqueued it. Running it anyway burns the full task duration for a
    callback that will be rejected — so it must be skipped."""
    fake_client = _BatchRenewClient(scripts=[_batch(2), []], supersede="t2")
    fake_reg = _FakeRegistry()
    wp = _wire_renew(monkeypatch, fake_client)
    slow = _SlowLoopExec(0.15)
    task = asyncio.create_task(wp.run_pull_worker(_container(fake_reg, slow)))
    await asyncio.sleep(0.4)
    task.cancel()
    await task
    assert "t1" in slow.ran
    assert "t2" not in slow.ran, "a superseded task was executed anyway"


@pytest.mark.asyncio
async def test_batch_renew_loop_supersede_drops_only_that_task():
    from worker_sdk.layer3_adapters.controllers.worker_pull import _batch_renew_loop
    class C:
        def __init__(self): self.calls = {}
        async def renew(self, tid, tok):
            self.calls[tid] = self.calls.get(tid, 0) + 1
            return None if tid == "t2" else "deadline"
    c, sup, stop = C(), set(), asyncio.Event()
    t = asyncio.create_task(_batch_renew_loop(c, {"t1": "L1", "t2": "L2"}, stop, 0.02, sup))
    await asyncio.sleep(0.12)
    stop.set()
    await t
    assert sup == {"t2"}                       # only the 409'd one retired
    assert c.calls["t2"] == 1                  # and it stopped being renewed
    assert c.calls["t1"] >= 2                  # the rest kept going


@pytest.mark.asyncio
async def test_batch_renew_loop_transient_error_keeps_the_task():
    """Same HIGH as the per-task loop: a blip must NOT drop a lease."""
    from worker_sdk.layer3_adapters.controllers.worker_pull import _batch_renew_loop
    class C:
        def __init__(self): self.calls = 0
        async def renew(self, tid, tok):
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError("connection reset")
            return "deadline"
    c, sup, stop = C(), set(), asyncio.Event()
    t = asyncio.create_task(_batch_renew_loop(c, {"t1": "L1"}, stop, 0.02, sup))
    await asyncio.sleep(0.12)
    stop.set()
    await t
    assert sup == set(), "a transient error retired the lease"
    assert c.calls >= 3                        # kept retrying past the blips


@pytest.mark.asyncio
async def test_batch_renew_loop_stops_when_batch_finishes():
    from worker_sdk.layer3_adapters.controllers.worker_pull import _batch_renew_loop
    class C:
        def __init__(self): self.calls = 0
        async def renew(self, tid, tok):
            self.calls += 1
            return "deadline"
    c, stop = C(), asyncio.Event()
    t = asyncio.create_task(_batch_renew_loop(c, {"t1": "L1"}, stop, 0.02, set()))
    await asyncio.sleep(0.07)
    stop.set()
    await t
    before = c.calls
    await asyncio.sleep(0.1)
    assert c.calls == before                   # no orphan renewer


def test_batch_renew_interval_takes_the_shortest():
    from worker_sdk.layer3_adapters.controllers.worker_pull import _batch_renew_interval
    tasks = [{"lease_ttl_seconds": 60}, {"lease_ttl_seconds": 20}]
    assert _batch_renew_interval(tasks, True, 0.5, 1.0) == 10.0     # min(30, 10)
    assert _batch_renew_interval(tasks, False, 0.5, 1.0) == 0.0     # disabled
    assert _batch_renew_interval([{}, {}], True, 0.5, 1.0) == 0.0   # no ttl anywhere
    # a task with no TTL must not drag the batch interval to 0
    assert _batch_renew_interval([{}, {"lease_ttl_seconds": 60}], True, 0.5, 1.0) == 30.0


# --- event-loop guard ---

@pytest.mark.asyncio
async def test_exec_core_guard_raises_when_run_on_the_event_loop():
    """Blocking the loop silently kills renewal + heartbeat; make it loud."""
    from worker_sdk.layer3_adapters.controllers.worker_pull import _run_exec_core
    ex = _FakeExec(ExecuteTaskResult(task_id="t1", status=TaskStatus.SUCCESS))
    with pytest.raises(RuntimeError, match="event loop"):
        _run_exec_core(ex, object())           # called ON the loop → refused
    assert ex.calls == []                      # and the handler never ran


@pytest.mark.asyncio
async def test_exec_core_guard_allows_the_thread_path():
    from worker_sdk.layer3_adapters.controllers.worker_pull import _run_exec_core
    result = ExecuteTaskResult(task_id="t1", status=TaskStatus.SUCCESS)
    ex = _FakeExec(result)
    got = await asyncio.to_thread(_run_exec_core, ex, object())
    assert got is result                       # the normal path is unaffected
