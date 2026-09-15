"""PULL for a worker that exposes several node types.

Before this, ``run_pull_worker`` announced and leased exactly ``settings.WORKER_TYPE``
and ran every task through the single exec core — a ``run_worker(node_types=[...])``
worker in PULL mode had its node types **silently dropped**: registered nothing they
could be targeted by, leased nothing for them, routed to no handler, and logged not
one word about it. The claude-worker shipped its own copy of the lease loop
(``app/pull_multi.py``) precisely to work around it.

Three properties here are the reason this lives in the SDK rather than in a worker,
because a hand-rolled copy gets each of them wrong in a way that only shows up under
load, minutes later, as a duplicate execution:

* **loops run concurrently, not round-robin** — the executor partitions the lease
  queue by ``(tenant, worker_type)``, so polling types in turn leaves every other
  type unpolled for the whole duration of a running task;
* **heartbeat runs on its own task** — with N loops there is no single loop
  guaranteed to tick, and a busy pull worker that never heartbeats is reaped
  mid-task (SA-2055 B-A);
* **batch renewal is the shared ``_drain_leased_batch``** — one copy, so the
  batch-deadline trap is fixed once.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer3_adapters.controllers import worker_pull


def _task(task_id: str, payload: dict | None = None, token: str = "tok",
          ttl: float = 60) -> dict:
    # ``ttl`` is what the renew interval is DERIVED from (ttl * safety_fraction),
    # so a renewal test has to shrink it — 60 s would mean a 30 s first renew.
    return {"task_id": task_id, "lease_token": token, "payload": payload or {},
            "node_config": {}, "run_id": "run-1", "lease_ttl_seconds": ttl}


class _Registry:
    def __init__(self, fail_for: set[str] | None = None,
                 fail_always: set[str] | None = None) -> None:
        self.registered: list[Any] = []
        self.heartbeats: list[str] = []
        self.deregistered: list[str] = []
        self._fail_for = fail_for or set()
        # An executor that stays down. `fail_for` recovers on the second attempt,
        # which cannot express "this type never registered at all" now that the
        # keeper retries on its first pass instead of one interval later.
        self._fail_always = fail_always or set()
        self._n = 0

    async def register(self, reg: Any) -> str:
        if reg.worker_type in self._fail_always:
            raise RuntimeError("executor down, and staying down")
        if reg.worker_type in self._fail_for:
            self._fail_for.discard(reg.worker_type)   # fail once, then recover
            raise RuntimeError("executor down")
        self.registered.append(reg)
        self._n += 1
        return f"wid-{reg.worker_type}-{self._n}"

    async def heartbeat(self, wid: str, status: Any) -> dict:
        self.heartbeats.append(wid)
        return {}

    async def deregister(self, wid: str) -> None:
        self.deregistered.append(wid)


class _Client:
    """One per worker_type, exactly as the runner builds them."""

    made: dict[str, "_Client"] = {}

    def __init__(self, base_url: str, tenant: str = "", worker_type: str = "",
                 client: Any = None) -> None:
        self.worker_type = worker_type
        self.batches: list[list[dict]] = []      # scripted, popped per lease
        self.leased_for: list[str] = []
        self.callbacks: list[tuple[str, Any]] = []
        self.renewed: list[str] = []
        _Client.made[worker_type] = self

    async def lease(self, worker_type: str, max_n: int, wait: float) -> list[dict]:
        self.leased_for.append(worker_type)
        if self.batches:
            return self.batches.pop(0)
        await asyncio.sleep(0.01)     # idle: let the loop breathe, never hot-spin
        return []

    async def post_callback(self, task_id: str, lease_token: str, result: Any) -> None:
        self.callbacks.append((task_id, result))

    async def renew(self, task_id: str, lease_token: str) -> str | None:
        self.renewed.append(task_id)
        return "2099-01-01T00:00:00Z"

    async def close(self) -> None:
        pass


def _nt(worker_type: str, handler: Any, **kw: Any) -> NodeTypeDefinition:
    return NodeTypeDefinition(worker_type=worker_type, handler=handler, **kw)


def _container(*node_types: NodeTypeDefinition, registry: Any = None) -> dict:
    return {
        "_dependencies": {
            "worker_registry": registry,
            "logger": None,
            "node_type_registry": {nt.worker_type: nt for nt in node_types},
        }
    }


async def _run_until(container: dict, predicate: Any, timeout: float = 3.0) -> None:
    """Start the runner, wait for *predicate*, then shut it down like Ctrl-C."""
    task = asyncio.create_task(worker_pull.run_pull_worker(container))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if task.done():
            await task           # surface the real exception, not a timeout
            raise AssertionError("runner exited before the predicate was met")
        if loop.time() > deadline:
            task.cancel()
            raise AssertionError("timed out waiting for the predicate")
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch):
    _Client.made = {}
    monkeypatch.setattr(worker_pull, "PullTaskClient", _Client)
    monkeypatch.setattr(worker_pull.settings, "LEASE_IDLE_FLOOR_SECONDS", 0.01)
    monkeypatch.setattr(worker_pull.settings, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(worker_pull.settings, "LEASE_RENEW_ENABLED", True)
    monkeypatch.setattr(worker_pull.settings, "LEASE_RENEW_MIN_INTERVAL_SECONDS", 0.02)


class TestRegistration:
    @pytest.mark.asyncio
    async def test_every_node_type_registers_as_its_own_pull_node(self):
        registry = _Registry()
        c = _container(
            _nt("alpha", lambda i, p: {}, name="Alpha", icon="Bot",
                input_schema=[{"key": "a"}], kind=NodeKind.READ),
            _nt("beta", lambda i, p: {}, name="Beta"),
            registry=registry,
        )
        await _run_until(c, lambda: len(registry.registered) == 2)

        by_type = {r.worker_type: r for r in registry.registered}
        assert set(by_type) == {"alpha", "beta"}
        for reg in by_type.values():
            assert reg.endpoint == "pull" and reg.delivery_mode == "pull"
        # Its OWN metadata reaches the palette, not the settings defaults — this is
        # what makes it a distinct node rather than a second name for the same one.
        assert by_type["alpha"].name == "Alpha"
        assert by_type["alpha"].icon == "Bot"
        assert by_type["alpha"].input_schema == [{"key": "a"}]
        assert by_type["alpha"].kind == NodeKind.READ

    @pytest.mark.asyncio
    async def test_one_types_failed_registration_does_not_skip_the_others(self):
        registry = _Registry(fail_for={"alpha"})
        c = _container(_nt("alpha", lambda i, p: {}), _nt("beta", lambda i, p: {}),
                       registry=registry)
        await _run_until(c, lambda: any(r.worker_type == "beta" for r in registry.registered))
        # The claim is that beta was never blocked behind alpha's failure. The
        # original assertion was `== ["beta"]`, which ALSO demanded alpha stay
        # unregistered — true only while a failed boot register waited a full
        # heartbeat interval to be retried. RegistrationKeeper retries on its
        # first pass, so alpha may legitimately be back already (the very next
        # test asserts that it is). Order is the honest invariant: beta is in,
        # and if alpha recovered it did so AFTER beta.
        types = [r.worker_type for r in registry.registered]
        assert "beta" in types, types
        if "alpha" in types:
            assert types.index("beta") < types.index("alpha"), types

    @pytest.mark.asyncio
    async def test_a_failed_boot_registration_is_retried_by_the_heartbeat(self):
        """SA-2055 B-B: without this the type stays unregistered forever."""
        registry = _Registry(fail_for={"alpha"})
        c = _container(_nt("alpha", lambda i, p: {}), registry=registry)
        await _run_until(c, lambda: any(r.worker_type == "alpha" for r in registry.registered))

    @pytest.mark.asyncio
    async def test_shutdown_deregisters_every_type_that_registered(self):
        registry = _Registry()
        c = _container(_nt("alpha", lambda i, p: {}), _nt("beta", lambda i, p: {}),
                       registry=registry)
        await _run_until(c, lambda: len(registry.registered) == 2)
        assert len(registry.deregistered) == 2

    @pytest.mark.asyncio
    async def test_shutdown_never_deregisters_a_type_that_never_registered(self):
        # `fail_always`, not `fail_for`: the executor has to STAY down for the wid
        # to stay blank. With a fail-once fake this test only passed because the
        # old heartbeat slept an interval before its first pass — it was asserting
        # the retry latency, not the shutdown rule.
        registry = _Registry(fail_always={"alpha"})
        c = _container(_nt("alpha", lambda i, p: {}), registry=registry)
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        await asyncio.sleep(0.05)          # several keeper passes, all failing
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert registry.registered == [], "the fake let a register through"
        assert registry.deregistered == []


class TestLeasingAndRouting:
    @pytest.mark.asyncio
    async def test_each_type_leases_its_own_partition(self):
        c = _container(_nt("alpha", lambda i, p: {}), _nt("beta", lambda i, p: {}))
        await _run_until(c, lambda: len(_Client.made) == 2
                         and all(cl.leased_for for cl in _Client.made.values()))
        assert set(_Client.made) == {"alpha", "beta"}
        # A client only ever leases the type it was built for — the executor's
        # queue is partitioned by (tenant, worker_type).
        for wt, cl in _Client.made.items():
            assert set(cl.leased_for) == {wt}

    @pytest.mark.asyncio
    async def test_a_task_is_routed_to_its_own_types_handler(self):
        seen: list[str] = []
        c = _container(
            _nt("alpha", lambda i, p: seen.append("alpha") or {"who": "alpha"}),
            _nt("beta", lambda i, p: seen.append("beta") or {"who": "beta"}),
        )
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while "beta" not in _Client.made:
            await asyncio.sleep(0.005)
        _Client.made["beta"].batches.append([_task("t-beta")])
        while not _Client.made["beta"].callbacks:
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert seen == ["beta"], "only beta's handler may run for a beta task"
        _tid, result = _Client.made["beta"].callbacks[0]
        assert result.outputs == {"who": "beta"}

    @pytest.mark.asyncio
    async def test_an_async_handler_is_awaited_not_returned_as_a_coroutine(self):
        """The trap a hand-rolled runner falls into.

        Calling a coroutine function without awaiting yields a coroutine OBJECT,
        which ships as the node's output and fails far from here. The HTTP path
        dispatches sync/async correctly; PULL must match.
        """
        async def handler(inputs, parameters):
            await asyncio.sleep(0)
            return {"async": True}

        c = _container(_nt("alpha", handler))
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while "alpha" not in _Client.made:
            await asyncio.sleep(0.005)
        _Client.made["alpha"].batches.append([_task("t1")])
        while not _Client.made["alpha"].callbacks:
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        _tid, result = _Client.made["alpha"].callbacks[0]
        assert result.outputs == {"async": True}
        assert not asyncio.iscoroutine(result.outputs)

    @pytest.mark.asyncio
    async def test_a_sync_handler_runs_off_the_event_loop(self):
        """A blocking handler must not run on the loop, or lease renewal and the
        heartbeat — both asyncio tasks — never get scheduled."""
        import threading
        thread_names: list[str] = []

        def handler(inputs, parameters):
            thread_names.append(threading.current_thread().name)
            return {}

        c = _container(_nt("alpha", handler))
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while "alpha" not in _Client.made:
            await asyncio.sleep(0.005)
        _Client.made["alpha"].batches.append([_task("t1")])
        while not _Client.made["alpha"].callbacks:
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert thread_names and thread_names[0] != threading.main_thread().name


class TestStatusVocabulary:
    """The callback pipeline accepts ``"success"`` and nothing else; every other
    value is reported to the control plane as WORKER_ERROR — with the outputs
    still attached, which is exactly how the bug looked live on the HTTP path."""

    @staticmethod
    async def _run_one(handler) -> Any:
        c = _container(_nt("alpha", handler))
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while "alpha" not in _Client.made:
            await asyncio.sleep(0.005)
        _Client.made["alpha"].batches.append([_task("t1")])
        while not _Client.made["alpha"].callbacks:
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return _Client.made["alpha"].callbacks[0][1]

    @pytest.mark.asyncio
    async def test_a_normal_return_is_success(self):
        result = await self._run_one(lambda i, p: {"ok": 1})
        assert result.status == TaskStatus.SUCCESS and result.outputs == {"ok": 1}

    @pytest.mark.asyncio
    async def test_a_raise_is_error_and_still_reports(self):
        def boom(inputs, parameters):
            raise RuntimeError("upstream refused")

        result = await self._run_one(boom)
        assert result.status == TaskStatus.ERROR
        assert "upstream refused" in result.error
        # Reported, not swallowed: a worker that drops its own result hangs the node.

    @pytest.mark.asyncio
    async def test_a_non_dict_return_is_passed_through_untouched(self):
        result = await self._run_one(lambda i, p: None)
        assert result.status == TaskStatus.SUCCESS and result.outputs == {}


class TestConcurrencyAndLiveness:
    @pytest.mark.asyncio
    async def test_a_long_task_on_one_type_does_not_block_another_types_poll(self):
        """The reason the loops are concurrent rather than round-robin.

        Round-robin polling leaves every other type unpolled for the whole
        duration of a running task — fatal for a worker whose tasks take minutes.
        """
        release = asyncio.Event()

        async def slow(inputs, parameters):
            await release.wait()
            return {"slow": True}

        c = _container(_nt("alpha", slow), _nt("beta", lambda i, p: {"fast": True}))
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while len(_Client.made) < 2:
            await asyncio.sleep(0.005)

        _Client.made["alpha"].batches.append([_task("t-slow")])
        await asyncio.sleep(0.05)                       # alpha is now stuck in `slow`
        _Client.made["beta"].batches.append([_task("t-fast")])

        for _ in range(200):                            # beta must complete anyway
            if _Client.made["beta"].callbacks:
                break
            await asyncio.sleep(0.01)
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert _Client.made["beta"].callbacks, "beta was blocked behind alpha"
        assert not _Client.made["alpha"].callbacks, "alpha should still have been running"

    @pytest.mark.asyncio
    async def test_it_heartbeats_while_every_type_is_busy(self):
        """SA-2055 B-A, multi-type shape: with N loops no single loop is guaranteed
        to tick, so the heartbeat cannot live inside one. A busy pull worker that
        never heartbeats is reaped mid-task by the executor's sweeper."""
        release = asyncio.Event()

        async def slow(inputs, parameters):
            await release.wait()
            return {}

        registry = _Registry()
        c = _container(_nt("alpha", slow), _nt("beta", slow), registry=registry)
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while len(_Client.made) < 2:
            await asyncio.sleep(0.005)
        _Client.made["alpha"].batches.append([_task("t1")])
        _Client.made["beta"].batches.append([_task("t2")])

        for _ in range(200):
            if registry.heartbeats:
                break
            await asyncio.sleep(0.01)
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert registry.heartbeats, "no heartbeat while busy — the sweeper would reap it"

    @pytest.mark.asyncio
    async def test_queued_tasks_in_a_batch_are_renewed_while_the_first_one_runs(self):
        """Inherited from the shared ``_drain_leased_batch``. The executor stamps one
        deadline on the WHOLE batch at claim time, so without this the queued tasks
        expire and get re-enqueued elsewhere — a duplicate execution."""
        release = asyncio.Event()

        async def slow(inputs, parameters):
            await release.wait()
            return {}

        c = _container(_nt("alpha", slow))
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while "alpha" not in _Client.made:
            await asyncio.sleep(0.005)
        _Client.made["alpha"].batches.append(
            [_task("t1", ttl=0.1), _task("t2", ttl=0.1), _task("t3", ttl=0.1)]
        )

        for _ in range(200):
            if "t3" in _Client.made["alpha"].renewed:
                break
            await asyncio.sleep(0.01)
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert "t3" in _Client.made["alpha"].renewed, (
            "the last task in the batch was never renewed while the first one ran"
        )


class TestSingleTypeIsUntouched:
    @pytest.mark.asyncio
    async def test_no_node_types_still_takes_the_single_type_path(self, monkeypatch):
        """The branch must be entered only by a genuinely multi-type worker."""
        called: list[str] = []

        async def _spy(container, ntr):
            called.append("multi")

        monkeypatch.setattr(worker_pull, "_run_multi_type_pull", _spy)
        container = {"_dependencies": {"worker_registry": None, "logger": None},
                     "execute_task": object()}
        task = asyncio.create_task(worker_pull.run_pull_worker(container))
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert called == []


# --- the shared concurrency gate (carried over from the pull_multi forks) -----
#
# claude-worker and log-worker each capped total in-flight tasks with
# `WorkerConcurrency`; N concurrent per-type lease loops cap nothing. Deleting
# those forks without this would have taken claude-worker from a configured 3 to
# 6 concurrent Claude CLI runs on one shared token pool, and log-worker from 1 to
# 3. The gate has to be SHARED across the type loops — a per-loop one multiplies
# by the node-type count, which is the very thing being bounded.


class _AlwaysBusyClient(_Client):
    """Never idle: every lease hands back exactly as many tasks as were asked for,
    so the gate — not a lack of work — is what bounds concurrency."""

    requested: list[int]

    async def lease(self, worker_type: str, max_n: int, wait: float) -> list[dict]:
        self.leased_for.append(worker_type)
        # How many the loop ASKED for — the capacity invariant is asserted on this.
        self.requested = getattr(self, "requested", [])
        self.requested.append(int(max_n))
        n = max(1, int(max_n))
        seq = len(self.leased_for)
        return [{"task_id": f"{worker_type}-{seq}-{i}", "lease_token": "L",
                 "payload": {}} for i in range(n)]


class _Probe:
    """Records the high-water mark of handlers running at the same instant."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.done = 0

    async def handler(self, inputs: Any, parameters: Any) -> dict:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        self.done += 1
        return {}


class TestConcurrencyGate:
    @staticmethod
    def _wire(monkeypatch, max_concurrent):
        monkeypatch.setattr(worker_pull, "PullTaskClient", _AlwaysBusyClient)
        monkeypatch.setattr(worker_pull.settings, "WORKER_MAX_CONCURRENT",
                            max_concurrent, raising=False)
        probe = _Probe()
        return probe, _container(*[_nt(f"t{i}", probe.handler) for i in range(3)])

    @pytest.mark.asyncio
    async def test_the_gate_caps_total_in_flight_across_every_type(self, monkeypatch):
        """Three saturated type loops, a cap of one: exactly one handler at a time."""
        probe, c = self._wire(monkeypatch, 1)
        await _run_until(c, lambda: probe.done >= 4)
        assert probe.peak == 1, f"gate did not hold: {probe.peak} handlers at once"

    @pytest.mark.asyncio
    async def test_without_a_cap_the_type_loops_do_run_concurrently(self, monkeypatch):
        """The control. Without this the test above would pass on a runner that is
        merely serial — proving nothing about the gate."""
        probe, c = self._wire(monkeypatch, 0)
        await _run_until(c, lambda: probe.peak > 1 or probe.done >= 12)
        assert probe.peak > 1, "type loops never overlapped — the cap test is vacuous"

    @pytest.mark.asyncio
    async def test_tasks_of_one_type_overlap_when_gated(self, monkeypatch):
        """Ported from claude-worker's deleted fork test, which called this "THE
        feature": a batch of one type must overlap, not run one after another.
        Without it, migrating that worker off pull_multi.py would have serialised
        every node type's queue behind itself."""
        monkeypatch.setattr(worker_pull, "PullTaskClient", _AlwaysBusyClient)
        monkeypatch.setattr(worker_pull.settings, "WORKER_MAX_CONCURRENT", 3,
                            raising=False)
        monkeypatch.setattr(worker_pull.settings, "LEASE_MAX_BATCH", 3, raising=False)
        probe = _Probe()
        c = _container(_nt("solo", probe.handler))          # ONE type, so any overlap
        await _run_until(c, lambda: probe.peak >= 2 or probe.done >= 9)
        assert probe.peak >= 2, (
            f"handlers never overlapped (peak={probe.peak}) — one type's queue is "
            f"being drained strictly sequentially")
        assert probe.peak <= 3, f"gate breached: {probe.peak} at once (max 3)"

    @pytest.mark.asyncio
    async def test_ungated_batches_still_drain_sequentially(self, monkeypatch):
        """The other half of the rule: a gate is what makes concurrency safe, so a
        gate is what turns it on. Uncapped, a batch must NOT fan out — PULL has no
        unlimited mode, and an unbounded batch is exactly that."""
        monkeypatch.setattr(worker_pull, "PullTaskClient", _AlwaysBusyClient)
        monkeypatch.setattr(worker_pull.settings, "WORKER_MAX_CONCURRENT", 0,
                            raising=False)
        monkeypatch.setattr(worker_pull.settings, "LEASE_MAX_BATCH", 4, raising=False)
        probe = _Probe()
        c = _container(_nt("solo", probe.handler))          # ONE type -> one loop
        await _run_until(c, lambda: probe.done >= 6)
        assert probe.peak == 1, f"an uncapped batch fanned out to {probe.peak}"

    @pytest.mark.asyncio
    async def test_lease_never_asks_for_more_than_free_capacity(self, monkeypatch):
        """Ported from the deleted fork test. This is the invariant that makes
        batch-wide renewal SUFFICIENT rather than merely helpful: a task leased but
        not started is a lease already ticking against the executor's batch deadline.
        Ask only for what can start now and nothing sits idle."""
        monkeypatch.setattr(worker_pull, "PullTaskClient", _AlwaysBusyClient)
        monkeypatch.setattr(worker_pull.settings, "WORKER_MAX_CONCURRENT", 2,
                            raising=False)
        monkeypatch.setattr(worker_pull.settings, "LEASE_MAX_BATCH", 8, raising=False)
        probe = _Probe()
        c = _container(_nt("solo", probe.handler))
        await _run_until(c, lambda: probe.done >= 4)
        client = _Client.made["solo"]
        assert client.requested, "never leased"
        assert max(client.requested) <= 2, (
            f"asked the executor for {max(client.requested)} tasks with a gate of 2 — "
            f"the surplus would sit leased and unrenewed until its deadline expired")

    @pytest.mark.asyncio
    async def test_shutdown_cancels_in_flight_tasks_and_frees_their_slots(self, monkeypatch):
        """Ported from the deleted fork test. A gated batch runs as real asyncio
        tasks, so Ctrl-C mid-batch must cancel and collect them — otherwise a handler
        keeps reporting through a client the runner is closing, and a slot the gate
        can never recover is leaked."""
        monkeypatch.setattr(worker_pull, "PullTaskClient", _AlwaysBusyClient)
        monkeypatch.setattr(worker_pull.settings, "WORKER_MAX_CONCURRENT", 2,
                            raising=False)
        monkeypatch.setattr(worker_pull.settings, "LEASE_MAX_BATCH", 2, raising=False)
        probe = _Probe()
        probe_slow = _Probe()

        async def slow(inputs: Any, parameters: Any) -> dict:
            probe_slow.active += 1
            try:
                await asyncio.sleep(30)      # still running when we cancel
            finally:
                probe_slow.active -= 1
            return {}

        c = _container(_nt("slow", slow))
        task = asyncio.create_task(worker_pull.run_pull_worker(c))
        while probe_slow.active < 1:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.wait_for(task, timeout=2.0)   # must not hang on the 30s sleep
        assert probe_slow.active == 0, "an in-flight handler outlived the shutdown"
        assert probe.done == 0
