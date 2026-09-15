"""Prometheus-backed monitoring with real counters, gauges, and histograms."""

from __future__ import annotations


from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from app.layer2_application.interfaces.monitor_interface import IMonitor

# Shared registry so tests can create isolated instances.
_default_registry = CollectorRegistry()

# ── Pre-defined metrics ──────────────────────────────────────────────────

_TASKS_RECEIVED = Counter(
    "wes_tasks_received_total",
    "Total tasks received by the executor",
    ["worker_type"],
    registry=_default_registry,
)

_TASKS_COMPLETED = Counter(
    "wes_tasks_completed_total",
    "Total tasks completed successfully",
    ["worker_type"],
    registry=_default_registry,
)

_TASKS_FAILED = Counter(
    "wes_tasks_failed_total",
    "Total tasks that failed",
    ["worker_type", "error_code"],
    registry=_default_registry,
)

_TASKS_DISPATCHED_ASYNC = Counter(
    "wes_tasks_dispatched_async_total",
    "Total tasks dispatched asynchronously",
    ["worker_type"],
    registry=_default_registry,
)

_CALLBACKS_RECEIVED = Counter(
    "wes_callbacks_received_total",
    "Total async callbacks received from workers",
    ["status"],
    registry=_default_registry,
)

_TASK_DURATION = Histogram(
    "wes_task_duration_seconds",
    "End-to-end task execution duration",
    ["worker_type"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
    registry=_default_registry,
)

_WORKER_DISPATCH_DURATION = Histogram(
    "wes_worker_dispatch_seconds",
    "Time to dispatch task to worker (HTTP call)",
    ["worker_type"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=_default_registry,
)

_ACTIVE_TASKS = Gauge(
    "wes_active_tasks",
    "Number of tasks currently being executed",
    registry=_default_registry,
)

_PENDING_TASKS = Gauge(
    "wes_pending_tasks",
    "Number of async tasks awaiting callback",
    registry=_default_registry,
)

_REGISTERED_WORKERS = Gauge(
    "wes_registered_workers",
    "Number of workers currently registered",
    registry=_default_registry,
)

_CONCURRENCY_REJECTIONS = Counter(
    "wes_concurrency_rejections_total",
    "Tasks rejected due to concurrency limit",
    registry=_default_registry,
)

# B9 — ad-hoc named event counter (makes every increment() call real + queryable),
# plus per-(tenant, worker_type) pull queue-depth gauges (the autoscale signal).
_EVENTS = Counter(
    "wes_events_total",
    "Ad-hoc named event counters (pull_admission_full, tasks_leased, pull_task_poisoned, ...)",
    ["event"],
    registry=_default_registry,
)

_PULL_QUEUE_DEPTH = Gauge(
    "wes_pull_admitted_depth",
    "Pull ready backlog per partition (KEDA scale-from-zero signal)",
    ["worker_type", "tenant"],
    registry=_default_registry,
)

_PULL_LEASED = Gauge(
    "wes_pull_leased",
    "Pull tasks currently leased (in-flight) per partition",
    ["worker_type", "tenant"],
    registry=_default_registry,
)

_WORKER_REPLICAS_TOTAL = Gauge(
    "wes_worker_replicas_total",
    "Registered replicas per worker_type/delivery_mode",
    ["worker_type", "delivery_mode"],
    registry=_default_registry,
)

_WORKER_REPLICAS_HEALTHY = Gauge(
    "wes_worker_replicas_healthy",
    "Healthy replicas per worker_type/delivery_mode (broad HEALTHY_STATUSES set)",
    ["worker_type", "delivery_mode"],
    registry=_default_registry,
)

_WORKER_REPLICA_AGE = Gauge(
    "wes_worker_replica_heartbeat_age_seconds",
    "Oldest heartbeat age among healthy-and-fresh replicas of a worker_type",
    ["worker_type"],
    registry=_default_registry,
)

# ── Event Mesh consumption ───────────────────────────────────────────────
# Task intake had NO metrics at all: whether the executor is receiving work
# was only visible as the absence of task logs. These make the two failure
# modes the control plane hit visible here — a link attached and silent, and
# messages being dead-lettered — before they are inferred from a stalled run.

_EM_AMQP_DELIVERY = Counter(
    "wes_eventmesh_amqp_delivery_total",
    "Messages an AMQP receiver link actually delivered",
    ["queue"],
    registry=_default_registry,
)

_EM_AMQP_STALL = Counter(
    "wes_eventmesh_amqp_stall_total",
    "Links stood down by the watchdog: attached, queue non-empty, nothing delivered",
    ["queue"],
    registry=_default_registry,
)

_EM_AMQP_RECONNECT = Counter(
    "wes_eventmesh_amqp_reconnect_total",
    "Receiver links re-established (reason: token_expiry | watchdog_stall | disconnect)",
    ["queue", "reason"],
    registry=_default_registry,
)

_EM_TOKEN_REFRESH = Counter(
    "wes_eventmesh_token_refresh_total",
    "OAuth2 tokens minted for Event Mesh (outcome: success | failure)",
    ["outcome"],
    registry=_default_registry,
)

_EM_AMQP_LINKS_ATTACHED = Gauge(
    "wes_eventmesh_amqp_links_attached",
    "AMQP receiver links currently attached",
    registry=_default_registry,
)

_EM_AMQP_LINKS_STALLED = Gauge(
    "wes_eventmesh_amqp_links_stalled",
    "Of the attached links, how many are inside the stall window",
    registry=_default_registry,
)

_EM_CONSUME_TRANSPORT_REST = Gauge(
    "wes_eventmesh_consume_transport_rest",
    "1.0 while task intake runs on the REST poll loop, 0.0 on AMQP push",
    registry=_default_registry,
)

_EM_CONSUME_FALLBACK = Counter(
    "wes_eventmesh_consume_fallback_total",
    "Configured for AMQP but committed to REST (reason: proton_missing | "
    "amqp_unreachable | queue_has_consumers)",
    ["reason"],
    registry=_default_registry,
)

_EM_DMQ_DEADLETTERED = Counter(
    "wes_eventmesh_dmq_deadlettered_total",
    "Messages newly dead-lettered — tasks are being LOST",
    ["queue"],
    registry=_default_registry,
)

_EM_DMQ_DEPTH = Gauge(
    "wes_eventmesh_dmq_depth",
    "Messages sitting across every dead-message queue",
    registry=_default_registry,
)


class PrometheusMonitor(IMonitor):
    """Real Prometheus metrics collector."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry or _default_registry
        # (worker_type, tenant) partitions whose pull gauges we set last refresh —
        # used to zero a DRAINED partition so KEDA scales it back to zero (B9).
        self._pull_partitions_seen: set[tuple[str, str]] = set()
        # (worker_type, delivery_mode) count labels + worker_type age labels set
        # last refresh — used to zero a drained count and remove a dead age series
        # (SA-2054, R7). Same drained-series discipline as _pull_partitions_seen.
        self._replica_labels_seen: set[tuple[str, str]] = set()
        self._replica_age_types_seen: set[str] = set()

    def track(self, metric: str, value: float) -> None:
        """Record a value for a named metric (legacy interface)."""
        # Map well-known metric names to histograms/gauges
        if "duration" in metric:
            _TASK_DURATION.labels(worker_type="unknown").observe(value / 1000)
        elif "active" in metric:
            _ACTIVE_TASKS.set(value)
        elif "pending" in metric:
            _PENDING_TASKS.set(value)

    def increment(self, metric: str, value: float = 1.0) -> None:
        """Increment a named event counter by *value* (default 1). Every ad-hoc
        event (pull_admission_full, tasks_leased, pull_task_poisoned, …) becomes a
        real, queryable `wes_events_total{event=...}` series."""
        try:
            _EVENTS.labels(event=metric).inc(value)
        except Exception:
            pass

    def set_pull_queue_stats(self, worker_type: str, tenant: str, admitted_depth: int, leased: int) -> None:
        """Set the per-partition pull gauges. tenant '' is labelled '-' so an empty
        label is queryable."""
        t = tenant or "-"
        _PULL_QUEUE_DEPTH.labels(worker_type=worker_type, tenant=t).set(admitted_depth)
        _PULL_LEASED.labels(worker_type=worker_type, tenant=t).set(leased)

    def refresh_pull_gauges(self, stats_list: list[dict]) -> None:
        """Refresh ALL pull gauges from a stats_all() snapshot (B9) each periodic
        sweep, and ZERO any partition that drained since last refresh — otherwise a
        drained partition's gauge would hold its last non-zero value forever, so
        KEDA would never scale that worker back to zero."""
        current: set[tuple[str, str]] = set()
        for s in stats_list:
            wt = s.get("worker_type", "")
            t = s.get("tenant") or "-"
            current.add((wt, t))
            _PULL_QUEUE_DEPTH.labels(worker_type=wt, tenant=t).set(s.get("admitted_depth", 0))
            _PULL_LEASED.labels(worker_type=wt, tenant=t).set(s.get("leased", 0))
        for (wt, t) in (self._pull_partitions_seen - current):
            _PULL_QUEUE_DEPTH.labels(worker_type=wt, tenant=t).set(0)
            _PULL_LEASED.labels(worker_type=wt, tenant=t).set(0)
        self._pull_partitions_seen = current

    def refresh_replica_gauges(self, snapshot: dict) -> None:
        """Refresh the replica gauges from a replica_gauge_stats() snapshot each
        sweep (SA-2054, R7). Zero any (worker_type, delivery_mode) count that
        drained since last refresh; REMOVE any age series whose population emptied
        (an absent age = 'no measurably-live replica', mirroring R6's null — never
        0, which would read as 'just heartbeated')."""
        counts = snapshot.get("counts", {})
        current: set[tuple[str, str]] = set()
        for (wt, mode), c in counts.items():
            m = mode or "-"
            _WORKER_REPLICAS_TOTAL.labels(worker_type=wt, delivery_mode=m).set(c.get("total", 0))
            _WORKER_REPLICAS_HEALTHY.labels(worker_type=wt, delivery_mode=m).set(c.get("healthy", 0))
            current.add((wt, m))
        for (wt, m) in (self._replica_labels_seen - current):
            _WORKER_REPLICAS_TOTAL.labels(worker_type=wt, delivery_mode=m).set(0)
            _WORKER_REPLICAS_HEALTHY.labels(worker_type=wt, delivery_mode=m).set(0)
        self._replica_labels_seen = current

        age = snapshot.get("age", {})
        age_current: set[str] = set()
        for wt, value in age.items():
            if value is None:
                continue
            _WORKER_REPLICA_AGE.labels(worker_type=wt).set(value)
            age_current.add(wt)
        for wt in (self._replica_age_types_seen - age_current):
            try:
                _WORKER_REPLICA_AGE.remove(wt)
            except KeyError:
                pass
        self._replica_age_types_seen = age_current

    # ── Typed helpers for structured instrumentation ──

    def task_received(self, worker_type: str) -> None:
        _TASKS_RECEIVED.labels(worker_type=worker_type).inc()

    def task_completed(self, worker_type: str, duration_s: float) -> None:
        _TASKS_COMPLETED.labels(worker_type=worker_type).inc()
        _TASK_DURATION.labels(worker_type=worker_type).observe(duration_s)

    def task_failed(self, worker_type: str, error_code: str = "WORKER_ERROR") -> None:
        _TASKS_FAILED.labels(worker_type=worker_type, error_code=error_code).inc()

    def task_dispatched_async(self, worker_type: str) -> None:
        _TASKS_DISPATCHED_ASYNC.labels(worker_type=worker_type).inc()

    def callback_received(self, status: str) -> None:
        _CALLBACKS_RECEIVED.labels(status=status).inc()

    def worker_dispatch_duration(self, worker_type: str, duration_s: float) -> None:
        _WORKER_DISPATCH_DURATION.labels(worker_type=worker_type).observe(duration_s)

    def concurrency_rejected(self) -> None:
        _CONCURRENCY_REJECTIONS.inc()

    def set_active_tasks(self, count: int) -> None:
        _ACTIVE_TASKS.set(count)

    def set_pending_tasks(self, count: int) -> None:
        _PENDING_TASKS.set(count)

    def set_registered_workers(self, count: int) -> None:
        _REGISTERED_WORKERS.set(count)

    # ── Event Mesh consumption ──

    def eventmesh_amqp_delivery(self, queue: str, count: int = 1) -> None:
        """Messages a receiver link actually delivered.

        ``count`` is the AMOUNT, never a label. The control plane shipped it as
        a label first: the counter then reads 1 and every distinct delta forks
        a new series (measured on tenant-1 2026-08-05 — six queues, 19 series
        in five minutes), which reduces `rate()` — the positive health signal
        this metric exists for — to noise.
        """
        if count > 0:
            _EM_AMQP_DELIVERY.labels(queue=queue).inc(count)

    def eventmesh_amqp_stall(self, queue: str) -> None:
        _EM_AMQP_STALL.labels(queue=queue).inc()

    def eventmesh_amqp_reconnect(self, queue: str, reason: str) -> None:
        _EM_AMQP_RECONNECT.labels(queue=queue, reason=reason).inc()

    def eventmesh_amqp_links(self, attached: int, stalled: int) -> None:
        _EM_AMQP_LINKS_ATTACHED.set(attached)
        _EM_AMQP_LINKS_STALLED.set(stalled)

    def eventmesh_token_refresh(self, outcome: str) -> None:
        _EM_TOKEN_REFRESH.labels(outcome=outcome).inc()

    def eventmesh_consume_transport(self, active: str) -> None:
        _EM_CONSUME_TRANSPORT_REST.set(1.0 if active == "rest" else 0.0)

    def eventmesh_consume_fallback(self, reason: str) -> None:
        _EM_CONSUME_FALLBACK.labels(reason=reason).inc()

    def eventmesh_dead_lettered(self, queue: str, count: int) -> None:
        if count > 0:
            _EM_DMQ_DEADLETTERED.labels(queue=queue).inc(count)

    def eventmesh_dmq_depth(self, total: int) -> None:
        _EM_DMQ_DEPTH.set(total)

    def metrics_output(self) -> tuple[bytes, str]:
        """Return (body_bytes, content_type) for the /metrics endpoint."""
        return generate_latest(self._registry), CONTENT_TYPE_LATEST
