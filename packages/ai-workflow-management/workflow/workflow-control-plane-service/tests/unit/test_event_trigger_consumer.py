"""Unit tests for the EventTriggerConsumer (SA-2059 B1–B6, B10).

Covers the acceptance criteria: 1→N fan-out, filter skip, redelivery dedup,
StartRun-failure redelivery vs ack, malformed ack, admission shed, tenant from
the envelope, and the derived predefined_run_id.
"""

import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer1_domain.value_objects.event_id import EventId
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)
from app.layer4_frameworks.messaging.event_trigger_consumer import (
    EventTriggerConsumer,
    _RateLimiter,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunResult,
)

pytestmark = pytest.mark.unit


# ── test doubles ──────────────────────────────────────────────────────

class _CapturingStartRun:
    def __init__(self, fail: bool = False) -> None:
        self.commands: list = []
        self._fail = fail

    async def execute(self, command):
        self.commands.append(command)
        if self._fail:
            return StartRunResult(error="boom")
        return StartRunResult(id=command.predefined_run_id or "run", workflow_id=command.workflow_id, status="RUNNING")


class _RunRepo:
    def __init__(self, existing: set[str] | None = None, active_count: int = 0) -> None:
        self._existing = existing or set()
        self._active_count = active_count

    def get_by_id(self, run_id: str):
        return object() if run_id in self._existing else None

    async def aget_by_id(self, run_id: str):  # F5: consumer awaits the async variant
        return self.get_by_id(run_id)

    async def alist_runs(self, filters):  # F4: in-flight count for the concurrency cap
        return SimpleNamespace(items=[], total=self._active_count)


class _AllowFilter:
    def __init__(self, result: bool = True, raises: bool = False) -> None:
        self._result = result
        self._raises = raises

    async def evaluate(self, expr, context) -> bool:
        if self._raises:
            raise RuntimeError("bad filter")
        return self._result


class _Monitor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def increment(self, metric, tags=None):
        self.calls.append((metric, tags or {}))


def _event_workflow(wf_id: str, topic: str, event_filter: str = "", main: bool = True,
                    status: WorkflowStatus = WorkflowStatus.PUBLISHED) -> Workflow:
    """PUBLISHED by default — publishing is what ARMS an automatic trigger.

    ``Workflow.status`` defaults to DRAFT, so every fixture here used to be a
    draft and still routed. That was the contract these tests encoded, and it
    was the bug: on tenant-1, 7 of 8 live event routes came from non-current
    rows. See ``test_trigger_routes_published_only.py`` and
    ``test_a_draft_workflow_does_not_start_a_run`` below.
    """
    node = WorkflowNode(
        workflow_id=wf_id, node_type=NodeType.TRIGGER,
        parameters={"trigger_type": "event", "event_topic": topic, "event_filter": event_filter},
    )
    return Workflow(id=wf_id, workflow_id=wf_id, name=wf_id, nodes=[node],
                    main_flow=main, status=status)


def _make(repo=None, start_run=None, run_repo=None, evaluator=None, monitor=None,
          max_per_min=60, max_conc=8):
    repo = repo if repo is not None else MemoryWorkflowRepository()
    container = {
        "workflow_repo": repo,
        "run_repo": run_repo if run_repo is not None else _RunRepo(),
        "start_run": start_run if start_run is not None else _CapturingStartRun(),
        "monitor": monitor,
    }
    return EventTriggerConsumer(
        container, broker=object(), condition_evaluator=evaluator or _AllowFilter(),
        max_starts_per_minute=max_per_min, max_concurrent_starts=max_conc,
        shed_backoff_s=0.0,  # no real backoff sleep in tests
    ), container


def _envelope(topic="orders.created", event_id="evt-1", tenant="acme", payload=None):
    return {"event_id": event_id, "event_topic": topic, "tenant_id": tenant,
            "source": "jira", "payload": payload or {"priority": "high"}}


# ── AC1: fan-out 1→N, once ────────────────────────────────────────────

async def test_event_starts_run_for_every_matching_workflow():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    repo.save(_event_workflow("wf-b", "orders.created"))
    repo.save(_event_workflow("wf-c", "other.topic"))  # non-match
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run)

    await consumer._handle(_envelope())

    started = {c.workflow_id for c in start_run.commands}
    assert started == {"wf-a", "wf-b"}


async def test_predefined_run_id_is_derived_and_tenant_from_envelope():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run)

    await consumer._handle(_envelope(event_id="evt-9", tenant="acme"))

    cmd = start_run.commands[0]
    assert cmd.predefined_run_id == EventId.derive("evt-9", "wf-a")
    assert cmd.tenant_id == "acme"  # from the envelope, never the payload


# ── AC2: filter false → no run, ack (no raise) ────────────────────────

async def test_filter_false_skips_and_acks():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created", event_filter="{{$event.priority}} == low"))
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run, evaluator=_AllowFilter(result=False))

    await consumer._handle(_envelope())  # must not raise

    assert start_run.commands == []


async def test_filter_error_treated_as_false():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created", event_filter="broken"))
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run, evaluator=_AllowFilter(raises=True))

    await consumer._handle(_envelope())

    assert start_run.commands == []


# ── AC3: redelivery dedup ─────────────────────────────────────────────

async def test_redelivered_event_does_not_start_second_run():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    run_id = EventId.derive("evt-1", "wf-a")
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run, run_repo=_RunRepo(existing={run_id}))

    await consumer._handle(_envelope(event_id="evt-1"))

    assert start_run.commands == []


# ── AC4: StartRun failure redelivers (raise); others ack ──────────────

async def test_start_run_failure_raises_for_redelivery():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    consumer, _ = _make(repo=repo, start_run=_CapturingStartRun(fail=True))

    with pytest.raises(RuntimeError):
        await consumer._handle(_envelope())


async def test_unroutable_event_acks_without_run():
    consumer, _ = _make(repo=MemoryWorkflowRepository())  # empty repo
    start_run = consumer._container["start_run"]

    await consumer._handle(_envelope(topic="nobody.listens"))

    assert start_run.commands == []


async def test_malformed_envelope_acks_without_run():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run)

    await consumer._handle({"payload": {"x": 1}})  # no event_topic / event_id

    assert start_run.commands == []


# ── envelope wrapped in {message_type, payload} still routes ──────────

async def test_wrapped_envelope_is_unwrapped():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run)

    await consumer._handle({"message_type": "event", "payload": _envelope()})

    assert [c.workflow_id for c in start_run.commands] == ["wf-a"]


# ── AC (metrics + admission) ──────────────────────────────────────────

async def test_rate_cap_sheds_and_defers_for_redelivery():
    # F1: a shed event must NOT be ack'd-and-lost — the handler raises so the
    # broker redelivers. max 1/min: first event starts; a second in the same
    # window is shed → raise.
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    monitor = _Monitor()
    consumer, _ = _make(repo=repo, monitor=monitor, max_per_min=1)

    await consumer._handle(_envelope(event_id="evt-1"))
    start_run = consumer._container["start_run"]
    assert len(start_run.commands) == 1

    with pytest.raises(RuntimeError):
        await consumer._handle(_envelope(event_id="evt-2"))
    assert len(start_run.commands) == 1  # second shed, not started
    assert any(m == "event_trigger_message" and t.get("outcome") == "shed"
               for m, t in monitor.calls)


async def test_concurrency_cap_sheds_when_in_flight_at_max():
    # F4: the cap engages against ACTUAL in-flight runs (alist_runs.total),
    # not an immediately-released in-process counter. At the cap → shed → raise.
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    consumer, _ = _make(repo=repo, run_repo=_RunRepo(active_count=8), max_conc=8)

    with pytest.raises(RuntimeError):
        await consumer._handle(_envelope())
    assert consumer._container["start_run"].commands == []  # not started


async def test_concurrency_under_cap_starts():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    consumer, _ = _make(repo=repo, run_repo=_RunRepo(active_count=7), max_conc=8)

    await consumer._handle(_envelope())
    assert [c.workflow_id for c in consumer._container["start_run"].commands] == ["wf-a"]


def test_rate_limiter_rolling_window():
    r = _RateLimiter(max_per_minute=2)
    assert r.admit("wf", now=1000.0)
    assert r.admit("wf", now=1000.5)
    assert not r.admit("wf", now=1001.0)  # 3rd in same minute
    assert r.admit("wf", now=1061.0)      # window rolled


# ── F11: thread→loop bridge (run_coroutine_threadsafe + future.result) ──

def _run_loop_in_thread():
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop, t


def _stop_loop(loop, t):
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


def test_on_message_bridges_to_loop_and_acks_on_success():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    start_run = _CapturingStartRun()
    consumer, _ = _make(repo=repo, start_run=start_run)
    loop, t = _run_loop_in_thread()
    consumer._loop = loop
    try:
        consumer._on_message(_envelope())  # blocks until handled (would raise → no ack)
        assert [c.workflow_id for c in start_run.commands] == ["wf-a"]
    finally:
        _stop_loop(loop, t)


def test_on_message_propagates_handler_error_for_no_ack():
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    consumer, _ = _make(repo=repo, start_run=_CapturingStartRun(fail=True))
    loop, t = _run_loop_in_thread()
    consumer._loop = loop
    try:
        with pytest.raises(RuntimeError):  # propagates → broker no-acks → redelivery
            consumer._on_message(_envelope())
    finally:
        _stop_loop(loop, t)


def test_on_message_times_out_on_stalled_handler():
    class _HangStartRun:
        commands: list = []

        async def execute(self, command):
            await asyncio.sleep(5)  # never completes within the timeout

    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-a", "orders.created"))
    consumer, _ = _make(repo=repo, start_run=_HangStartRun())
    consumer._handler_timeout_s = 0.1
    loop, t = _run_loop_in_thread()
    consumer._loop = loop
    try:
        with pytest.raises(FutureTimeoutError):  # F11: bounded wait, not forever
            consumer._on_message(_envelope())
    finally:
        _stop_loop(loop, t)


# ── routing cache: the per-message full scan (2026-08-05) ─────────────
#
# ``list_trigger_routes`` scans and deserialises every PUBLISHED main flow.
# It ran once per trigger message, synchronously, on the shared event loop.
# These cover the two fixes: cache the result, and await the async variant.

class _CountingWorkflowRepo:
    """Memory repo that counts scans and can expose an async variant."""

    def __init__(self, inner, with_async: bool = False) -> None:
        self._inner = inner
        self.sync_calls = 0
        self.async_calls = 0
        if with_async:
            self.alist_trigger_routes = self._alist_trigger_routes

    def list_trigger_routes(self, trigger_types=()):
        self.sync_calls += 1
        return self._inner.list_trigger_routes(trigger_types)

    async def _alist_trigger_routes(self, trigger_types=()):
        self.async_calls += 1
        return self._inner.list_trigger_routes(trigger_types)


async def test_routing_scan_runs_once_for_repeated_events_on_one_topic():
    """Second and third events on the same topic must not re-scan."""
    inner = MemoryWorkflowRepository()
    inner.save(_event_workflow("wf-a", "orders.created"))
    repo = _CountingWorkflowRepo(inner)
    consumer, _ = _make(repo=repo)

    for i in range(3):
        await consumer._handle(_envelope(event_id=f"evt-{i}"))

    assert repo.sync_calls == 1, "the full scan must be cached across messages"


async def test_routing_scan_prefers_the_async_repo_variant():
    """The scan is a blocking round-trip — it belongs on the executor."""
    inner = MemoryWorkflowRepository()
    inner.save(_event_workflow("wf-a", "orders.created"))
    repo = _CountingWorkflowRepo(inner, with_async=True)
    consumer, _ = _make(repo=repo)

    await consumer._handle(_envelope())

    assert repo.async_calls == 1
    assert repo.sync_calls == 0, "must not use the sync path when alist_* exists"


async def test_routing_scan_falls_back_to_sync_without_an_async_variant():
    """Memory repo / test doubles have no ``alist_*`` — behaviour unchanged."""
    inner = MemoryWorkflowRepository()
    inner.save(_event_workflow("wf-a", "orders.created"))
    repo = _CountingWorkflowRepo(inner, with_async=False)
    consumer, container = _make(repo=repo)

    await consumer._handle(_envelope())

    assert repo.sync_calls >= 1
    assert container["start_run"].commands, "the run must still start"


async def test_one_scan_serves_every_topic():
    """The cache is keyed by TENANT, not (topic, tenant).

    It used to be per-topic, so N topics paid N scans of the same table for the
    same answer, and the cache held N copies of full ``Workflow`` entities —
    the live ``t.jira`` entry measured 384 KB against a cap that counts ENTRIES,
    not bytes. One scan, filtered in memory, is both cheaper and bounded.
    """
    inner = MemoryWorkflowRepository()
    inner.save(_event_workflow("wf-a", "orders.created"))
    inner.save(_event_workflow("wf-b", "orders.shipped"))
    repo = _CountingWorkflowRepo(inner)
    consumer, container = _make(repo=repo)

    await consumer._handle(_envelope(topic="orders.created", event_id="e1"))
    await consumer._handle(_envelope(topic="orders.shipped", event_id="e2"))
    await consumer._handle(_envelope(topic="orders.created", event_id="e3"))

    assert repo.sync_calls == 1, "one scan answers every topic"
    started = {c.workflow_id for c in container["start_run"].commands}
    assert started == {"wf-a", "wf-b"}, "both topics must still route"


async def test_a_draft_workflow_does_not_start_a_run():
    """The reported bug, end to end through the consumer.

    Workflow 8728b765 on tenant-1: the PUBLISHED version's trigger was
    ``manual``, but three DRAFT rows still carried ``event``/``t.jira`` and kept
    matching. Only the stale rows' own ``event_filter`` — which the author can
    no longer see, because the editor shows the current version — stopped a run.
    """
    inner = MemoryWorkflowRepository()
    inner.save(_event_workflow("wf-draft", "orders.created",
                               status=WorkflowStatus.DRAFT))
    consumer, container = _make(repo=inner)

    await consumer._handle(_envelope(topic="orders.created"))

    assert container["start_run"].commands == []


async def test_routing_cache_expiry_rescans():
    """A newly published trigger becomes routable once the TTL lapses."""
    from app.layer4_frameworks.caching.bounded_ttl_cache import BoundedTTLCache

    now = {"t": 1000.0}
    cache = BoundedTTLCache(maxsize=16, ttl_seconds=30.0, clock=lambda: now["t"])
    inner = MemoryWorkflowRepository()
    inner.save(_event_workflow("wf-a", "orders.created"))
    repo = _CountingWorkflowRepo(inner)
    consumer, _ = _make(repo=repo)
    consumer._routing_cache = cache

    await consumer._handle(_envelope(event_id="e1"))
    assert repo.sync_calls == 1

    now["t"] += 31.0  # past the TTL
    await consumer._handle(_envelope(event_id="e2"))
    assert repo.sync_calls == 2, "expired entry must re-scan"
