"""Configuration value object for :class:`EventMeshBrokerClient`.

A single declarative object replacing the client's former 30-parameter
constructor. All input validation, clamping, and derivation lives here in
``__post_init__`` — so the client reads already-clean values and never
re-validates. Frozen: a config never changes after construction (the client
maintains its own mutable runtime state, e.g. an adopted namespace).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Defaults previously spread across the client constructor signature.
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_MAX_PAYLOAD_BYTES = 900_000  # 900KB — Event Mesh cap is ~1MB
_DEFAULT_BUFFER_SIZE = 10_000
_DEFAULT_BUFFER_POLICY = "drop"  # or "raise"
_DEFAULT_FLUSH_BATCH_SIZE = 50
_DEFAULT_FLUSH_CONCURRENCY = 10
_DEFAULT_MAX_CONNECTIONS = 100
_DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
_DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5
_DEFAULT_CIRCUIT_RECOVERY_TIMEOUT_S = 30.0
_DEFAULT_LOCK_DURATION_MS = 60_000
_DEFAULT_POLL_INTERVAL_S = 0.5
_DEFAULT_CONSUME_BATCH_MAX = 10
_DEFAULT_AMQP_WS_PATH = "/protocols/amqp10ws"
# Queue-level redelivery cap + dead-message queue. SAP's own guidance: leaving
# maxRedeliveryCount empty/0 means INDEFINITE, IMMEDIATE redelivery. Under REST
# polling that merely wastes polls; under AMQP push, where a handler failure
# settles the delivery as RELEASED, one poison message re-loops at full speed
# forever. Measured live 2026-08-05: every WCP queue has maxRedeliveryCount=0
# and deadMsgQueue=None.
_DEFAULT_MAX_REDELIVERY_COUNT = 5
_DEFAULT_DMQ_SUFFIX = "-dmq"


class EventMeshConfigError(ValueError):
    """Explicit Event Mesh misconfiguration that must FAIL boot.

    Distinct from ValueError so the broker factory can re-raise it instead of
    falling back to the mock broker — a pod silently running on mock in prod
    is strictly worse than one that refuses to start (SA-1468).
    """


@dataclass(frozen=True)
class EventMeshConfig:
    """Validated, normalized configuration for the Event Mesh broker client.

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

    # Namespace hardening (SA-1468). ``require_namespace`` makes an unset/empty
    # namespace a boot-time EventMeshConfigError instead of a runtime 403
    # self-heal — flip on in prod manifests. ``allow_namespace_self_heal``
    # keeps the 403-adoption path as a dev-only convenience; when False the
    # 403 surfaces to the caller and shared state is never mutated at runtime.
    require_namespace: bool = False
    allow_namespace_self_heal: bool = True

    # Publish + retry.
    max_retries: int = _DEFAULT_MAX_RETRIES
    max_payload_bytes: int = _DEFAULT_MAX_PAYLOAD_BYTES

    # Async send buffer + flusher.
    buffer_size: int = _DEFAULT_BUFFER_SIZE
    buffer_policy: str = _DEFAULT_BUFFER_POLICY
    flush_batch_size: int = _DEFAULT_FLUSH_BATCH_SIZE
    flush_concurrency: int = _DEFAULT_FLUSH_CONCURRENCY
    max_connections: int = _DEFAULT_MAX_CONNECTIONS
    max_keepalive_connections: int = _DEFAULT_MAX_KEEPALIVE_CONNECTIONS

    # Circuit breaker + DLQ.
    circuit_failure_threshold: int = _DEFAULT_CIRCUIT_FAILURE_THRESHOLD
    circuit_recovery_timeout_s: float = _DEFAULT_CIRCUIT_RECOVERY_TIMEOUT_S
    enable_dlq: bool = True

    # DLQ durability (SA-1471). ``dlq_spool_dir`` enables a local JSONL spool
    # that parks durable envelopes whenever the DLQ path can't take them
    # (disabled / no client / broker down) — replayed automatically when the
    # circuit closes and at startup. Empty = disabled (legacy behaviour).
    # ``dlq_best_effort_topics`` is a comma-separated list of topic names
    # whose events MAY be dropped (realtime UI ticks); everything else is
    # treated as durable and must reach the DLQ or the spool.
    dlq_spool_dir: str = ""
    dlq_best_effort_topics: str = "realtime.events"

    # Queue redelivery policy + dead-message queue (P2).
    # ``dmq_enabled`` provisions ``<queue><dmq_suffix>`` alongside every queue
    # and points the queue's ``deadMsgQueue`` at it, so a message that fails
    # ``max_redelivery_count`` times is parked instead of re-looping forever.
    # A redelivery cap WITHOUT a DMQ discards the message, so the two are
    # deliberately coupled: turning the DMQ off restores the legacy (unbounded)
    # behaviour rather than silently enabling a lossy cap.
    dmq_enabled: bool = True
    dmq_suffix: str = _DEFAULT_DMQ_SUFFIX
    max_redelivery_count: int = _DEFAULT_MAX_REDELIVERY_COUNT

    # Consume + handler.
    handler_timeout_s: float | None = None
    lock_duration_ms: int = _DEFAULT_LOCK_DURATION_MS
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S
    consume_transport: str = "amqp"
    # 429 cooldown scope (SA-1470): "instance" (default — SAP Event Mesh's
    # REST limit is per service instance, one cooldown pauses every queue)
    # or "queue" (only the throttled queue pauses; use where broker limits
    # are actually per-queue).
    consume_429_scope: str = "instance"
    # Messages drained per queue per REST poll pass. 1 is the pre-2026-08-05
    # behaviour: a burst of sibling orchestration actions was then spread
    # across successive poll intervals. Ordering and ack semantics are
    # unaffected — the drain is still sequential and still acks per message.
    consume_batch_max: int = _DEFAULT_CONSUME_BATCH_MAX
    # Emergency escape hatch. When AMQP cannot be attached, the consumer used
    # to switch itself to the REST poll loop on the RETIRED bare queue —
    # provisioning it, which re-creates the subscription P5.4 deleted, which
    # fans the topic out to two queues, which duplicates every message.
    # Reproduced on tenant-1 2026-08-11 across five topics in 65 seconds.
    # The transport is now a configuration decision only: a failed attach
    # stands the topic down and retries. Set this to restore the old
    # behaviour without a redeploy if standing down is ever worse.
    runtime_fallback: bool = False
    keepalive_expiry_s: float = 30.0

    # AMQP push transport.
    amqp_url: str = ""
    amqp_host: str = ""
    amqp_port: int = 443
    amqp_ws_path: str = _DEFAULT_AMQP_WS_PATH
    # Messages the broker may have in flight to one receiver link before it
    # settles them. With ``amqp_concurrency = 1`` the handler is serial, so
    # prefetch is the depth of the queue BEHIND that single handler — and the
    # broker starts its redelivery clock when it hands a message over, not when
    # the handler reaches it. At 50, the last message in a batch waits for 49
    # handler runs before anyone looks at it; against the queue-level lock TTL
    # (``lock_duration_ms``, 60 s by default) that only needs ~1.2 s per
    # message to breach, and a breach means redelivery of work already in
    # progress. 10 leaves the same headroom at ~6 s per message. Raise it on
    # measurement, not on hope.
    amqp_prefetch: int = 10
    amqp_concurrency: int = 1
    amqp_strip_namespace: bool = False

    def __post_init__(self) -> None:
        # Fail fast on an explicit misconfiguration BEFORE any normalization:
        # with require_namespace on, an empty namespace means every queue/topic
        # name would go out unqualified and 403 — refuse to boot instead.
        if self.require_namespace and not (self.namespace or "").strip():
            raise EventMeshConfigError(
                "EVENT_MESH_NAMESPACE is required (EVENT_MESH_REQUIRE_NAMESPACE=1) "
                "but is unset/empty — set it explicitly (see the service key's "
                "'namespace' field) or unset EVENT_MESH_REQUIRE_NAMESPACE for "
                "dev-only 403 self-heal"
            )
        # Normalize URLs and clamp every tunable to its valid range. Event Mesh
        # rejects 0/negative lock TTLs and the flusher needs >=1 worker/batch.
        object.__setattr__(self, "messaging_url", self.messaging_url.rstrip("/"))
        object.__setattr__(self, "management_url", self.management_url.rstrip("/"))
        object.__setattr__(self, "max_retries", max(1, int(self.max_retries)))
        object.__setattr__(self, "lock_duration_ms", max(1, int(self.lock_duration_ms)))
        object.__setattr__(self, "poll_interval_s", max(0.05, float(self.poll_interval_s)))
        object.__setattr__(self, "flush_batch_size", max(1, int(self.flush_batch_size)))
        object.__setattr__(self, "consume_batch_max", max(1, int(self.consume_batch_max)))
        object.__setattr__(self, "flush_concurrency", max(1, int(self.flush_concurrency)))
        object.__setattr__(self, "amqp_prefetch", max(1, int(self.amqp_prefetch)))
        # A DMQ with a redelivery cap of 0 is the worst of both worlds: the cap
        # never trips, so the DMQ is never used and the poison loop survives.
        # Clamp to >=1 whenever the DMQ is on; with it off, leave the value
        # alone so the legacy unbounded behaviour is explicit, not accidental.
        object.__setattr__(self, "dmq_suffix", (self.dmq_suffix or _DEFAULT_DMQ_SUFFIX))
        if self.dmq_enabled:
            object.__setattr__(
                self, "max_redelivery_count", max(1, int(self.max_redelivery_count))
            )
        else:
            object.__setattr__(
                self, "max_redelivery_count", max(0, int(self.max_redelivery_count))
            )
        # Control-plane consumers are order-sensitive (per-run reorder buffer in
        # socketio_consumer): one receiver link per queue with serial settlement
        # preserves on-the-wire order. Don't raise concurrency above 1.
        object.__setattr__(self, "amqp_concurrency", max(1, int(self.amqp_concurrency)))
        if self.buffer_policy not in ("drop", "raise"):
            object.__setattr__(self, "buffer_policy", "drop")
        object.__setattr__(
            self, "consume_transport", (self.consume_transport or "amqp").lower()
        )
        if (self.consume_429_scope or "instance").lower() not in ("instance", "queue"):
            object.__setattr__(self, "consume_429_scope", "instance")
        else:
            object.__setattr__(
                self, "consume_429_scope", self.consume_429_scope.lower()
            )

        # Derive AMQP host/port/ws_path from amqp_url when an explicit URL is
        # given; explicit amqp_host always wins over the URL's hostname.
        if self.amqp_url:
            parsed = urlparse(self.amqp_url)
            object.__setattr__(self, "amqp_host", self.amqp_host or parsed.hostname or "")
            if parsed.port:
                object.__setattr__(self, "amqp_port", parsed.port)
            if parsed.path and parsed.path != "/":
                object.__setattr__(self, "amqp_ws_path", parsed.path)

        # AMQP requires a host; without one, fall back to REST consumption so
        # consume_transport=amqp never silently stops the poll loop.
        if self.consume_transport == "amqp" and not self.amqp_host:
            logger.warning(
                "EVENT_MESH_CONSUME_TRANSPORT=amqp but no AMQP host/url configured "
                "— using REST consumption",
            )
            object.__setattr__(self, "consume_transport", "rest")
