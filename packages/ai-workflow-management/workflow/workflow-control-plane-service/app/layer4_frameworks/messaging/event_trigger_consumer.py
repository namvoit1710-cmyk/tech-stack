"""Event-trigger consumer (SA-2059) — the ``event`` trigger's missing half.

Consumes the canonical inbox topic ``workflow.triggers``. Every message is a
topic-addressed **event envelope** (no ``workflow_id`` — Pattern 1 is retired,
see SA-2059 design decision 2026-07-16):

    {"event_id": "...", "event_topic": "mdg.part.created",
     "tenant_id": "...", "source": "jira", "occurred_at": "...",
     "correlation_id": "...", "payload": {...}}

Routing is uniform 1→N: every **PUBLISHED main flow** whose TRIGGER node has
``trigger_type=event`` and a matching ``event_topic`` gets a run — after its
``event_filter`` passes and admission control admits it. Idempotency: the run
id is ``EventId.derive(event_id, workflow_id)`` + an existence check, so a
redelivered event never creates a second run.

PUBLISHED is load-bearing, not incidental. ``AIW_WORKFLOWS`` holds one row per
VERSION and publish only demotes the previous row to DRAFT, so the earlier
``MAIN_FLOW = 1`` scan kept every version ever saved routable — 7 of the 8 live
routes on tenant-1 were stale rows. See
``HanaWorkflowRepository.list_trigger_routes``.

Mirrors ``WorkerResultsConsumer``: the broker poll thread invokes the
callback, which bridges onto the captured main event loop and WAITS, so the
broker only acks after processing. Exceptions PROPAGATE → no ack → redelivery
(at-least-once). Unroutable / filtered / duplicate / shed messages return
normally → ack, no redelivery. Multi-instance safe with NO leader gate:
competing consumers on the shared queue deliver each message once, and the
derived run id dedupes any overlap.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from typing import Any

from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer2_application.repositories.run_repository_interface import ListRunsFilter
from app.layer4_frameworks.caching.bounded_ttl_cache import (
    MISS,
    BoundedTTLCache,
    env_int,
)

logger = logging.getLogger(__name__)

TRIGGERS_TOPIC = "workflow.triggers"

# Start failures whose answer will be identical on every redelivery. The
# consumer's contract is at-least-once — an exception means no ack means the
# broker sends it again — and that is right for a HANA blip or a network
# fault. It is wrong for a verdict.
#
# Measured on tenant-1 (SA-2280): a vault 403 on a user-owned credential span
# every redelivery of the same event, forever, because an event-triggered run
# has no user and the owner check is user-scoped (SA-2279). Nothing
# dead-lettered it and no run existed to carry the error, so the workflow was
# simply silent while the retry loop drove the event-loop lag peak to 719ms.
#
# Deliberately a short list of *known-deterministic* failures rather than a
# catch-all: misclassifying a transient fault as terminal loses the event,
# which is worse than retrying it.
_TERMINAL_START_ERRORS: tuple[type[BaseException], ...]
try:
    from app.layer4_frameworks.clients.http_vault_credential_client import (
        CredentialNotFound,
        CredentialResolveAuthError,
    )
    _TERMINAL_START_ERRORS = (CredentialNotFound, CredentialResolveAuthError)
except ImportError:  # pragma: no cover - client optional in slim test builds
    _TERMINAL_START_ERRORS = ()

# Runs that still occupy a concurrency slot — anything not terminal
# (COMPLETED / FAILED / CANCELLED). Used by the F4 concurrency cap.
_ACTIVE_RUN_STATUSES = ["CREATED", "PENDING", "RUNNING", "PAUSED", "SUSPENDED"]

# tenant -> event trigger routes cache.
#
# The routing scan ran once per trigger message, synchronously, on the main
# event loop. Measured on tenant-1 (2026-08-05): 10,924 Jira events consumed
# and *filtered out*, each paying the scan; the orchestration queue is fed by
# the same single consumer thread, so sibling ``ExecuteNodeAction``s for one
# run were dispatched 13.5s and 15.8s apart while it ran. The scan's own author
# flagged this — "a definition-indexed HANA query only if event volume proves
# the scan too costly". The volume proved it.
#
# Keyed by TENANT, not (topic, tenant): one scan answers every topic, and the
# cache stays at one entry per tenant. That second part matters — the cap below
# is on ENTRIES, not bytes, and the value used to be full ``Workflow``
# entities (the live ``t.jira`` entry measured 384 KB). Routes are ~16 KB.
#
# TTL rather than event-driven invalidation: routing changes only when a
# workflow is published, and a bounded staleness window is the same trade the
# node-catalog cache already makes ("TTL is the fallback when an event is
# missed"). A newly published event trigger becomes routable within the TTL.
_ROUTING_CACHE_TTL_S = env_int("EVENT_TRIGGER_ROUTING_CACHE_TTL_S", 30, minimum=1)
_ROUTING_CACHE_MAXSIZE = env_int("EVENT_TRIGGER_ROUTING_CACHE_MAXSIZE", 256, minimum=1)


class _RateLimiter:
    """Per-workflow rolling-window start-rate cap (SA-2065, rate limb only).

    Concurrency is enforced separately by the consumer against the actual
    count of in-flight runs (F4) — an in-process counter can't track a run's
    lifetime because the run executes elsewhere after StartRun returns.

    ponytail: in-process and per-instance — a distributed limiter only if
    real storms outrun competing-consumer sharding.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max_per_minute = max_per_minute
        self._starts: dict[str, deque[float]] = {}

    def admit(self, workflow_id: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        window = self._starts.setdefault(workflow_id, deque())
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self._max_per_minute:
            return False
        window.append(now)
        return True


class EventTriggerConsumer:
    def __init__(
        self,
        container: dict,
        broker: Any,
        condition_evaluator: Any = None,
        max_starts_per_minute: int = 60,
        max_concurrent_starts: int = 8,
        shed_backoff_s: float = 2.0,
        handler_timeout_s: float = 30.0,
        routing_cache: Any = None,
    ) -> None:
        self._container = container
        self._broker = broker
        self._condition_evaluator = condition_evaluator
        self._rate = _RateLimiter(max_starts_per_minute)
        self._max_concurrent = max_concurrent_starts
        self._shed_backoff_s = shed_backoff_s
        self._handler_timeout_s = handler_timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribed = False
        # (topic, tenant_id) -> list[Workflow]. See ``_workflows_for_topic``.
        # Named so the cache joins the SA-2254 memory-budget export instead of
        # being another unobserved map. Injectable so the TTL can be tested
        # against a hand-cranked clock rather than a sleep.
        self._routing_cache = routing_cache if routing_cache is not None else BoundedTTLCache(
            maxsize=_ROUTING_CACHE_MAXSIZE,
            ttl_seconds=_ROUTING_CACHE_TTL_S,
            name="event_trigger_routing",
        )

    def start(self) -> None:
        """Subscribe to the triggers topic. Call from the main asyncio thread."""
        if self._subscribed:
            return
        self._subscribed = True
        self._loop = asyncio.get_running_loop()

        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(TRIGGERS_TOPIC)
            except Exception:
                logger.warning("Could not auto-create topic '%s'", TRIGGERS_TOPIC)

        self._broker.subscribe_to_topic(TRIGGERS_TOPIC, self._on_message)
        logger.info("Event trigger consumer subscribed to topic: %s", TRIGGERS_TOPIC)

    def _on_message(self, message: dict[str, Any]) -> None:
        if self._loop is None or self._loop.is_closed():
            logger.warning("Main event loop unavailable — dropping event: %s", message)
            return
        # Wait for the async handler so the broker only acks after processing.
        # Exceptions PROPAGATE → no ack → redelivery (at-least-once). Safe:
        # the derived predefined_run_id makes every redelivered start a no-op.
        # F11: bound the wait — a stalled handler must not pin the poll thread
        # forever. On timeout the future keeps running (orphaned, like the
        # EventMeshConsumer sibling) but we no-ack so the broker redelivers.
        future = asyncio.run_coroutine_threadsafe(self._handle(message), self._loop)
        future.result(timeout=self._handler_timeout_s)

    # -- helpers ----------------------------------------------------------

    def _metric(self, outcome: str, topic: str = "") -> None:
        monitor = self._container.get("monitor")
        if monitor is not None:
            try:
                monitor.increment(
                    "event_trigger_message",
                    tags={"outcome": outcome, "event_topic": topic},
                )
            except Exception:  # noqa: BLE001 - metrics must never break consumption
                pass

    @staticmethod
    def _envelope_of(message: dict[str, Any]) -> dict[str, Any]:
        """Accept a bare envelope or one wrapped as ``{message_type, payload}``."""
        if "event_topic" in message:
            return message
        payload = message.get("payload")
        if isinstance(payload, dict) and "event_topic" in payload:
            return payload
        return message

    async def _passes_filter(self, expr: str, envelope: dict[str, Any]) -> bool:
        if not expr or not expr.strip():
            return True
        if self._condition_evaluator is None:
            return True
        context = {"event": envelope.get("payload") or {}, "trigger": envelope}
        try:
            return await self._condition_evaluator.evaluate(expr, context)
        except Exception as exc:  # noqa: BLE001 - a bad filter must not poison-loop
            logger.warning("event_filter evaluation failed (treated as false): %s", exc)
            self._metric("filter_error", str(envelope.get("event_topic", "")))
            return False

    async def _active_run_count(self, run_repo: Any, workflow_id: str, tenant_id: str) -> int:
        """Count the workflow's in-flight (non-terminal) runs (F4 concurrency cap).

        Uses the async repo variant so the SELECT is offloaded off the loop (F5).
        ``.total`` is the full count regardless of the page size.
        """
        try:
            page = await run_repo.alist_runs(ListRunsFilter(
                workflow_id=workflow_id, tenant_id=tenant_id,
                statuses=_ACTIVE_RUN_STATUSES, limit=1,
            ))
            return page.total
        except Exception as exc:  # noqa: BLE001 - a count failure must not drop the event
            logger.warning("active-run count failed for %s (allowing start): %s", workflow_id, exc)
            return 0

    async def _routes_for_topic(
        self, workflow_repo: Any, topic: str, tenant_id: str,
    ) -> list:
        """Event routes listening on *topic* — PUBLISHED rows only, cached.

        Three properties, each of which was a live defect:

        1. **Published-only.** ``list_trigger_routes`` replaces the old
           ``list_by_event_topic`` -> ``list_main_flows`` -> ``SELECT * WHERE
           MAIN_FLOW = 1`` scan, which saw EVERY version row. Measured on
           tenant-1 2026-08-11: 8 live event routes, **7 of them stale rows**
           from versions whose author had already removed or retyped the
           trigger. See ``HanaWorkflowRepository.list_trigger_routes``.
        2. **Cached once for ALL topics.** The cache key is the tenant, not
           ``(topic, tenant)``: one scan now answers every topic instead of one
           scan per topic. It also bounds the cache — ``BoundedTTLCache``
           caps ENTRIES, not bytes (``bounded_ttl_cache.py:200``), and the old
           value was a list of full ``Workflow`` entities: the live ``t.jira``
           entry measured **384 KB**, so 256 entries was ~94 MB of headroom on
           a 1 GB pod. Routes for the same topic are **16 KB**.
        3. **Awaited.** The scan is a blocking ``hdbcli`` round-trip; called
           synchronously it ran on the event loop this service shares with
           FastAPI, Socket.IO, every broker consumer and the orchestration
           workers. ``AsyncRepoMixin`` generates ``alist_trigger_routes``.

        Falls back to the sync method when the repo has no async variant
        (memory repo, test doubles) so behaviour is unchanged there.

        TTL rather than event-driven invalidation is unchanged: routing now
        changes only on PUBLISH, which makes the bounded staleness window
        smaller and easier to explain than before.
        """
        cached = self._routing_cache.get(tenant_id)
        if cached is MISS:
            alist = getattr(workflow_repo, "alist_trigger_routes", None)
            if alist is not None and inspect.iscoroutinefunction(alist):
                cached = await alist(("event",))
            else:
                cached = workflow_repo.list_trigger_routes(("event",))
            cached = list(cached or [])
            self._routing_cache.put(tenant_id, cached)

        return [r for r in cached if r.event_topic == topic]

    # -- the loop ----------------------------------------------------------

    async def _handle(self, message: dict[str, Any]) -> None:
        envelope = self._envelope_of(message)
        topic = str(envelope.get("event_topic") or "").strip()
        event_id = str(envelope.get("event_id") or "").strip()
        if not topic or not event_id:
            # Malformed is terminal — ack, never redeliver poison.
            logger.warning("Malformed trigger event (missing event_topic/event_id): %s", message)
            self._metric("malformed", topic)
            return

        tenant_id = str(envelope.get("tenant_id") or "system")
        workflow_repo = self._container.get("workflow_repo")
        run_repo = self._container.get("run_repo")
        start_run = self._container.get("start_run")
        if workflow_repo is None or start_run is None:
            logger.error("workflow_repo/start_run unavailable — cannot route trigger event")
            self._metric("unwired", topic)
            return

        routes = await self._routes_for_topic(workflow_repo, topic, tenant_id)
        if not routes:
            self._metric("no_match", topic)
            return

        failures = 0
        shed = 0
        for route in routes:
            wf_id = route.workflow_id

            if not await self._passes_filter(route.event_filter, envelope):
                self._metric("filtered", topic)
                continue

            run_id = EventId.derive(event_id, wf_id)
            # Dedup first — a redelivered/duplicate event is not a shed and
            # must not consume a rate/concurrency slot. F5: async, off-loop.
            # Also what collapses a workflow carrying two TRIGGER nodes on the
            # same topic into a single run.
            if run_repo is not None and await run_repo.aget_by_id(run_id) is not None:
                self._metric("duplicate", topic)
                continue

            # F4: concurrency cap against ACTUAL in-flight runs (the old
            # in-process counter was released right after execute(), so it
            # never engaged). F1: shed → defer for redelivery, don't drop.
            if run_repo is not None and self._max_concurrent > 0:
                active = await self._active_run_count(run_repo, wf_id, tenant_id)
                if active >= self._max_concurrent:
                    logger.warning(
                        "Concurrency cap: workflow %s has %d in-flight runs — shedding event %s",
                        wf_id, active, event_id,
                    )
                    self._metric("shed", topic)
                    shed += 1
                    continue

            # Rate cap (rolling window). Also a shed → deferred, not dropped.
            if not self._rate.admit(wf_id):
                logger.warning("Rate cap shed event %s for workflow %s", event_id, wf_id)
                self._metric("shed", topic)
                shed += 1
                continue

            try:
                result = await start_run.execute(StartRunCommand(
                    workflow_id=wf_id,
                    input_data=envelope.get("payload") or {},
                    tenant_id=tenant_id,
                    correlation_id=str(envelope.get("correlation_id") or ""),
                    predefined_run_id=run_id,
                    metadata={
                        "trigger": "event",
                        "event_id": event_id,
                        "event_topic": topic,
                        "source": envelope.get("source", ""),
                        "trigger_node_id": route.node_id,
                    },
                ))
            except _TERMINAL_START_ERRORS as exc:
                # A start that failed for a reason the next attempt cannot
                # change. Retrying is not resilience here — it is an
                # infinite loop against a fixed answer.
                logger.error(
                    "Event-triggered run REFUSED and not retried: "
                    "workflow=%s event=%s topic=%s — %s: %s. This will not "
                    "succeed on redelivery; the event is acknowledged. "
                    "Fix the cause and re-emit the event.",
                    wf_id, event_id, topic, type(exc).__name__, exc,
                )
                self._metric("start_refused", topic)
                continue
            if getattr(result, "error", None):
                logger.error(
                    "Event-triggered run rejected: workflow=%s error=%s",
                    wf_id, result.error,
                )
                self._metric("failed", topic)
                failures += 1
                continue
            logger.info(
                "Event-triggered run started: workflow=%s version=%s run=%s topic=%s",
                wf_id, route.version, result.id, topic,
            )
            self._metric("started", topic)

        if failures:
            # No ack → the broker redelivers. Successful starts above are safe:
            # their derived run ids dedupe on the redelivery pass.
            raise RuntimeError(
                f"{failures} StartRun failure(s) for event {event_id} on topic {topic}"
            )
        if shed:
            # F1: a shed event must be RETRIED, not ack'd-and-lost. No-ack forces
            # redelivery; the backoff is crude in-process backpressure so we don't
            # hot-loop against a still-full cap. ponytail: a broker-native delayed
            # requeue (nack-with-delay) is the real fix if throughput demands it.
            # Already-started/duplicate workflows dedupe on redelivery.
            await asyncio.sleep(self._shed_backoff_s)
            raise RuntimeError(
                f"admission shed {shed} start(s) for event {event_id} on topic {topic}; "
                "deferring for redelivery"
            )
