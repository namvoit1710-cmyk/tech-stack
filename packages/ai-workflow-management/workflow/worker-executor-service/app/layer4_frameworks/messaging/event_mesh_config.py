"""Configuration value object for the worker's :class:`EventMeshBrokerClient`.

A single declarative object replacing the client's former 19-parameter
constructor. All input validation, clamping, and derivation lives here in
``__post_init__`` — so the client reads already-clean values and never
re-validates. Frozen: a config never changes after construction (the client
maintains its own mutable runtime state, e.g. an adopted namespace).

This is the worker (executor) variant — pure synchronous consume/publish, no
async flusher/circuit/DLQ. It is deliberately separate from the control-plane
``EventMeshConfig``, whose tunables differ.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Defaults previously spread across the client constructor signature.
_DEFAULT_POLL_BATCH_SIZE = 50
_DEFAULT_POLL_IDLE_SLEEP_S = 0.5
_DEFAULT_CONSUMER_THREADS = 4
_DEFAULT_LOCK_DURATION_MS = 60_000
_DEFAULT_AMQP_WS_PATH = "/protocols/amqp10ws"
# Prefetch is how many messages the broker may hand a link before any are
# settled. High prefetch on a *task* queue is actively harmful: the executor
# rejects work it has no capacity for by NOT acking (the backpressure rail in
# main.py), so every prefetched message is one the broker considers delivered
# and will not give to a peer that does have capacity. 10 keeps a link busy
# without hoarding a backlog behind one saturated instance.
_DEFAULT_AMQP_PREFETCH = 10


@dataclass(frozen=True)
class EventMeshConfig:
    """Validated, normalized configuration for the worker broker client.

    Field names match the legacy constructor keyword arguments one-for-one,
    so existing call sites only wrap their kwargs in ``EventMeshConfig(...)``.
    """

    # Connection (required).
    token_url: str
    messaging_url: str
    management_url: str
    client_id: str
    client_secret: str

    # Identity / transport.
    namespace: str = "default"
    timeout: float = 30.0

    # REST poll pool.
    poll_batch_size: int = _DEFAULT_POLL_BATCH_SIZE
    poll_idle_sleep_s: float = _DEFAULT_POLL_IDLE_SLEEP_S
    consumer_threads: int = _DEFAULT_CONSUMER_THREADS
    lock_duration_ms: int = _DEFAULT_LOCK_DURATION_MS

    # Dead-message queue policy. Without it a queue carries
    # ``maxRedeliveryCount = 0`` and no ``deadMsgQueue`` — measured on
    # tenant-1 2026-08-05 for the live ``sub-workflow.tasks`` — which means a
    # task that always fails its handler is redelivered forever with nowhere
    # to land and nothing to see it. That matters more here than on the
    # control plane: the executor deliberately skips the ack to signal
    # backpressure, so "redelivered again" is its normal, healthy state and a
    # genuinely poisonous task is indistinguishable from a busy one.
    dmq_enabled: bool = True
    dmq_suffix: str = "-dmq"
    # 50, NOT the control plane's 5 — and the difference is load-bearing.
    #
    # On the control plane a redelivery means a handler failed. Here it is also
    # the *backpressure rail*: when the executor is at capacity it deliberately
    # does not ack (main.py raises on CONCURRENCY_LIMIT / WORKER_SATURATED /
    # ADMISSION_FULL) so the broker re-offers the task once a slot frees. Each
    # of those bounces burns a redelivery.
    #
    # With a 60s lock TTL, a cap of N buys roughly N minutes of sustained
    # saturation before a *healthy* task is dead-lettered. 5 would mean five
    # minutes — well inside a normal batch on a 300-concurrency executor — so
    # the control plane's value would silently convert correct backpressure
    # into lost work. 50 keeps the cap (a genuinely poisonous task still stops
    # cycling forever, and now lands somewhere visible) while leaving the
    # backpressure rail an order of magnitude more room than it needs.
    max_redelivery_count: int = 50

    # AMQP push transport.
    consume_transport: str = "amqp"
    amqp_url: str = ""
    amqp_host: str = ""
    amqp_port: int = 443
    amqp_ws_path: str = _DEFAULT_AMQP_WS_PATH
    amqp_prefetch: int = _DEFAULT_AMQP_PREFETCH
    # 0 → derive from ``consumer_threads`` (resolved in __post_init__): task
    # consumption isn't order-sensitive, so AMQP runs as many competing
    # receiver links as the REST pool has threads.
    amqp_concurrency: int = 0
    amqp_strip_namespace: bool = False

    def __post_init__(self) -> None:
        # Normalize URLs and clamp every tunable to its valid range.
        object.__setattr__(self, "messaging_url", self.messaging_url.rstrip("/"))
        object.__setattr__(self, "management_url", self.management_url.rstrip("/"))
        object.__setattr__(self, "poll_batch_size", max(1, int(self.poll_batch_size)))
        object.__setattr__(self, "poll_idle_sleep_s", max(0.0, float(self.poll_idle_sleep_s)))
        object.__setattr__(self, "consumer_threads", max(1, int(self.consumer_threads)))
        object.__setattr__(self, "lock_duration_ms", max(1, int(self.lock_duration_ms)))
        object.__setattr__(self, "amqp_prefetch", max(1, int(self.amqp_prefetch)))
        object.__setattr__(self, "max_redelivery_count", max(1, int(self.max_redelivery_count)))
        object.__setattr__(self, "dmq_suffix", self.dmq_suffix or "-dmq")
        object.__setattr__(
            self, "consume_transport", (self.consume_transport or "amqp").lower()
        )

        # Derive AMQP host/port/ws_path from amqp_url when given; an explicit
        # amqp_host always wins over the URL's hostname.
        if self.amqp_url:
            parsed = urlparse(self.amqp_url)
            object.__setattr__(self, "amqp_host", self.amqp_host or parsed.hostname or "")
            if parsed.port:
                object.__setattr__(self, "amqp_port", parsed.port)
            if parsed.path and parsed.path != "/":
                object.__setattr__(self, "amqp_ws_path", parsed.path)

        # 0/negative means "match the REST pool's thread count".
        if int(self.amqp_concurrency) > 0:
            object.__setattr__(self, "amqp_concurrency", int(self.amqp_concurrency))
        else:
            object.__setattr__(self, "amqp_concurrency", self.consumer_threads)

        # AMQP requires a host; without one, fall back to REST consumption so
        # consume_transport=amqp never silently stops the poll loop.
        if self.consume_transport == "amqp" and not self.amqp_host:
            logger.warning(
                "EVENT_MESH_CONSUME_TRANSPORT=amqp but no AMQP host/url configured "
                "— using REST consumption",
            )
            object.__setattr__(self, "consume_transport", "rest")
