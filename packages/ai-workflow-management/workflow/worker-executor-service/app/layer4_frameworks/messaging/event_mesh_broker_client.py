"""SAP Event Mesh implementation of a message broker client.

Uses the Event Mesh REST API with OAuth2 client-credentials authentication.
Provides publish/subscribe and queue-based consume/acknowledge semantics.

SAP Event Mesh API reference:
- Messaging: POST /messagingrest/v1/topics/{topic}/messages
- Queue consume: POST /messagingrest/v1/queues/{queue}/messages/consumption
- Acknowledge: POST /messagingrest/v1/queues/{queue}/messages/{id}/acknowledgement
- Management: /hub/rest/api/v1/management/messaging/queues/...
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from typing import Any, Optional
from collections.abc import Callable
from urllib.parse import quote

import httpx

from app.layer1_domain.errors import MessageTooLargeError
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics

logger = logging.getLogger(__name__)

# SA-1941 — WARN (don't reject) when a broker message is large; almost always a
# large inline worker output that should be relayed as a pointer via worker-side
# streaming (SA-1944). Event Mesh also enforces its own per-message REST limit.
_MAX_BROKER_MESSAGE_BYTES = 1024 * 1024  # 1 MiB
logging.getLogger("httpx").setLevel(logging.WARNING)

_TOKEN_REFRESH_MARGIN_S = 60  # treat the cached token as stale this long before expiry

# Proactively re-mint the token once this fraction of its lifetime has elapsed,
# instead of waiting for a consumer to discover it is stale.
#
# WHY THIS EXISTS (measured on tenant-1, 2026-08-16): without it the AMQP
# receive loop reconnect-storms for the whole window between the two margins.
# The receiver stands its link down ``_TOKEN_RECONNECT_MARGIN_S`` (300s) before
# the true expiry, but ``_ensure_token`` only considered the cached token stale
# ``_TOKEN_REFRESH_MARGIN_S`` (60s) before it — so the reconnect got the SAME
# token back, the new link was born already inside its stand-down margin, and
# the loop span at full speed for 240s. Cost: 40,327 reconnects in 7 days on one
# queue, in ~12-hourly bursts, versus 8 on the control plane. The control plane
# never showed it only because its publisher pump runs an equivalent proactive
# refresh against the SAME token cache — i.e. the CP's consumer is protected by
# a side effect of the publisher being enabled, which is not a guarantee.
#
# 0.75 leaves a full quarter of the token's life as headroom for retries, which
# is comfortably more than the 300s reconnect margin at any realistic TTL.
_TOKEN_PROACTIVE_REFRESH_RATIO = 0.75
_TOKEN_REFRESH_CHECK_S = 60      # how often the background thread re-evaluates
_TOKEN_REFRESH_RETRY_S = 30      # after a failed proactive refresh

# The AMQP receiver must never be handed a token that is already inside its own
# stand-down margin — that is what makes the reconnect self-sustaining. Kept
# deliberately larger than ``event_mesh_amqp_consumer._TOKEN_RECONNECT_MARGIN_S``
# so a link always attaches with usable life left. Read from the same env var so
# the two cannot drift apart in a deployment.
_AMQP_TOKEN_MIN_REMAINING_S = max(
    int(os.getenv("EVENT_MESH_AMQP_TOKEN_MARGIN_S", "300") or 300), 30,
) + 60

# Event Mesh's shared management backend rejects an unqualified queue name with
# "403 ... is not in the namespace=<ns> of the service instance id=<id>". We
# parse <ns> from that body to self-heal a missing/empty configured namespace.
_NAMESPACE_FROM_ERROR_RE = re.compile(r"namespace=([^\s,]+)")

# Management-API existence checks retry a transient connection blip (e.g. a
# momentary DNS getaddrinfo failure) instead of logging a full traceback and
# falling through to a redundant create.
_MANAGEMENT_GET_ATTEMPTS = 3
_MANAGEMENT_GET_RETRY_DELAY_S = 0.3

# How often to read the dead-message queues. A DMQ growing is the only signal
# that says work is being LOST — every transport signal reads healthy while it
# happens, because the messages really were delivered before being thrown away.
_DMQ_CHECK_SECONDS = 60.0


class EventMeshBrokerClient:
    """Concrete message broker client backed by SAP Event Mesh REST API.

    Provides ``publish_to_topic``, ``subscribe_to_topic``, queue-based
    consume/acknowledge, and queue/subscription management.
    """

    def __init__(self, config: EventMeshConfig) -> None:
        # ``config`` carries already-validated, already-normalized values (see
        # EventMeshConfig.__post_init__): URLs are rstripped, every tunable is
        # clamped, the AMQP host/port/ws_path are derived, amqp_concurrency has
        # been resolved (0 → consumer_threads), and consume_transport has fallen
        # back to "rest" if AMQP has no host. The client just reads them.
        self._config = config
        self._token_url = config.token_url
        self._messaging_url = config.messaging_url
        self._management_url = config.management_url
        self._client_id = config.client_id
        self._client_secret = config.client_secret
        # Mutable at runtime: create_queue may adopt the instance namespace from
        # a 403 (_adopt_namespace_from_error), so this is not just config state.
        self._namespace = config.namespace
        self._timeout = config.timeout
        # Consumer poll-loop tuning: drain up to ``poll_batch_size`` messages
        # per queue per pass (no inter-message delay); sleep ``poll_idle_sleep_s``
        # only when every queue is empty. ``consumer_threads`` competing-consumer
        # threads run the poll loop in parallel. ``lock_duration_ms`` is the
        # per-consume message lock TTL.
        self._poll_batch_size = config.poll_batch_size
        self._poll_idle_sleep_s = config.poll_idle_sleep_s
        self._consumer_threads_count = config.consumer_threads
        self._lock_duration_ms = config.lock_duration_ms
        # Dead-message queue policy (see EventMeshConfig).
        self._dmq_enabled = config.dmq_enabled
        self._dmq_suffix = config.dmq_suffix
        self._max_redelivery_count = config.max_redelivery_count

        # OAuth2 token cache
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._token_issued_at: float = 0.0
        self._token_lifetime_s: float = 0.0
        self._token_lock = threading.RLock()
        self._token_refresh_thread: Optional[threading.Thread] = None
        self._token_refresh_stop = threading.Event()

        # Subscribe-based polling
        self._callbacks: dict[str, list[Callable]] = {}
        self._sub_lock = threading.Lock()
        self._consumer_pool: list[threading.Thread] = []
        self._running = False
        # Per-thread asyncio loop for running coroutine handlers from either the
        # REST poll threads or the AMQP receive threads.
        self._tls = threading.local()

        # Consume transport: "amqp" (push, no consumption-endpoint 429s) or
        # "rest" (legacy poll pool). AMQP auto-falls-back to REST when the AMQP libs are
        # unavailable or the endpoint can't be reached (see _start_amqp_consumer).
        self._consume_transport = config.consume_transport
        self._amqp_host = config.amqp_host
        self._amqp_port = config.amqp_port
        self._amqp_ws_path = config.amqp_ws_path
        self._amqp_prefetch = config.amqp_prefetch
        self._amqp_concurrency = config.amqp_concurrency
        self._amqp_strip_namespace = config.amqp_strip_namespace
        self._amqp_consumer: Any = None
        self._amqp_state: str | None = None
        self._amqp_lock = threading.Lock()

        # Metrics. The monitor is built after the broker in bootstrap, so it
        # arrives via ``set_monitor``; until then every call is a safe no-op.
        self._metrics = EventMeshMetrics(None)

        # Dead-message queue watch.
        self._dmq_check_seconds = _DMQ_CHECK_SECONDS
        self._dmq_thread: Optional[threading.Thread] = None
        self._dmq_stop = threading.Event()
        self._dmq_seen: dict[str, int] = {}

        # Reusable HTTP client
        self._http = httpx.Client(timeout=config.timeout)

    def set_monitor(self, monitor: Any) -> None:
        """Wire the monitor after construction (bootstrap builds it later)."""
        self._metrics = EventMeshMetrics(monitor)

    # ------------------------------------------------------------------
    # Dead-message queue watch
    # ------------------------------------------------------------------

    def sample_dead_letters(self) -> dict[str, int]:
        """Read every subscribed queue's DMQ depth; emit the deltas as metrics.

        Two signals, because they answer different questions:

        * ``dmq.deadlettered{queue}`` — a **counter** of newly dead-lettered
          messages. Rate-able and labelled, so it says *tasks are being lost
          right now, on this queue*. This is the alert.
        * ``dmq.depth`` — a **gauge** of the total across all DMQs. Depth stays
          high forever after one incident, so on its own it cannot tell
          "happening now" from "happened last week" — but it is the only thing
          that shows a backlog which predates this process.

        The first reading of each DMQ only seeds the baseline. Without that, a
        restart would report every message already sitting there as a fresh
        burst of loss, and the alert would cry wolf on every deploy.

        Runs under **both** transports, because it lives here rather than in
        the AMQP watchdog — and REST is the rollback target, i.e. exactly when
        you most need to know whether tasks are being dead-lettered.

        Returns ``{dmq_name: depth}`` for the DMQs that answered.
        """
        with self._sub_lock:
            queues = list(self._callbacks.keys())

        depths: dict[str, int] = {}
        for queue_name in queues:
            try:
                if self.is_dmq(queue_name):
                    continue  # a DMQ has no DMQ of its own
                dmq = self.dmq_name(queue_name)
                depth = self.queue_depth(dmq)
            except Exception:
                logger.debug("DMQ depth unavailable for %s", queue_name, exc_info=True)
                continue
            if depth is None:
                continue  # unreadable is not zero — calling it 0 invents a drain

            depths[dmq] = depth
            previous = self._dmq_seen.get(dmq)
            self._dmq_seen[dmq] = depth
            if previous is None:
                if depth:
                    logger.warning(
                        "DMQ %s already holds %d message(s) at startup — these "
                        "are tasks that were lost before this process began",
                        dmq, depth,
                    )
                continue
            if depth > previous:
                self._metrics.record_dead_lettered(dmq, depth - previous)
                logger.warning(
                    "DMQ %s grew by %d (now %d) — tasks are being lost",
                    dmq, depth - previous, depth,
                )

        if depths:
            self._metrics.record_dmq_depth(sum(depths.values()))
        return depths

    def _start_dmq_watch(self) -> None:
        if self._dmq_thread is not None:
            return
        self._dmq_stop.clear()
        self._dmq_thread = threading.Thread(
            target=self._dmq_watch_loop, name="event-mesh-dmq-watch", daemon=True,
        )
        self._dmq_thread.start()

    def _dmq_watch_loop(self) -> None:
        # Wait BEFORE the first sample. The watch starts inside the first
        # `subscribe_to_topic`, so sampling immediately would count only the
        # queues registered so far and publish a depth gauge that reads 0
        # because the rest have not subscribed yet — a reading that looks like
        # "nothing dead-lettered" and means "not counted yet". Observed on the
        # control plane, which samples first.
        while not self._dmq_stop.wait(self._dmq_check_seconds):
            try:
                self.sample_dead_letters()
            except Exception:
                # Observability must never be the thing that stops consumption.
                logger.exception("DMQ watch failed")
        logger.info("DMQ watch stopped")

    # ------------------------------------------------------------------
    # OAuth2 token management
    # ------------------------------------------------------------------

    def _ensure_token(self, min_remaining_s: float = 0.0) -> str:
        """Return an access token with at least *min_remaining_s* of life left.

        ``min_remaining_s`` is what stops a caller being handed a token that is
        already too stale for its own purposes. The AMQP receiver passes its
        stand-down margin here: without that, standing a link down before token
        expiry only to re-attach it with the very same token is an infinite
        loop (see ``_TOKEN_PROACTIVE_REFRESH_RATIO``).
        """
        with self._token_lock:
            if self._access_token and time.time() + min_remaining_s < self._token_expires_at:
                return self._access_token
            return self._refresh_token()

    def _refresh_token(self) -> str:
        """Fetch a new token via client-credentials grant.

        Caller must hold ``_token_lock``.
        """
        try:
            resp = self._http.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            body = resp.json()
            self._access_token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
            now = time.time()
            self._token_expires_at = now + expires_in - _TOKEN_REFRESH_MARGIN_S
            self._token_issued_at = now
            self._token_lifetime_s = float(expires_in)
            logger.info("Event Mesh OAuth2 token refreshed (expires_in=%ds)", expires_in)
            self._metrics.record_token_refresh("success")
            return self._access_token
        except Exception:
            self._metrics.record_token_refresh("failure")
            logger.exception("Failed to refresh Event Mesh OAuth2 token")
            raise

    # ------------------------------------------------------------------
    # Proactive refresh
    # ------------------------------------------------------------------

    def _token_due_for_refresh(self) -> bool:
        """True once the token has burned ``_TOKEN_PROACTIVE_REFRESH_RATIO``.

        Read without the lock on purpose: this is a scheduling hint sampled
        once a minute, and ``_ensure_token`` re-checks under the lock. A torn
        read costs at most one redundant wake-up.
        """
        if not self._access_token or not self._token_lifetime_s:
            return not self._access_token  # no token yet → mint one
        elapsed = time.time() - self._token_issued_at
        return elapsed >= self._token_lifetime_s * _TOKEN_PROACTIVE_REFRESH_RATIO

    def _start_token_refresh(self) -> None:
        if self._token_refresh_thread is not None:
            return
        self._token_refresh_stop.clear()
        self._token_refresh_thread = threading.Thread(
            target=self._token_refresh_loop,
            name="event-mesh-token-refresh",
            daemon=True,
        )
        self._token_refresh_thread.start()

    def _token_refresh_loop(self) -> None:
        """Re-mint the token before anything has to discover it is stale.

        Reactive refresh alone is not enough: every consumer that needs a token
        with headroom would otherwise stall or spin at the moment of rotation.
        Failures here are non-fatal — ``_ensure_token`` remains the safety net —
        but they are retried promptly because the whole point is to be early.
        """
        while not self._token_refresh_stop.wait(_TOKEN_REFRESH_CHECK_S):
            if not self._token_due_for_refresh():
                continue
            try:
                with self._token_lock:
                    self._refresh_token()
            except Exception:
                # Already logged + counted in _refresh_token. Retry sooner than
                # the normal cadence; do NOT raise — losing this thread would
                # silently restore the storm this fix exists to prevent.
                if self._token_refresh_stop.wait(_TOKEN_REFRESH_RETRY_S):
                    break
        logger.info("Event Mesh token refresh loop stopped")

    def _auth_headers(self) -> dict[str, str]:
        token = self._ensure_token()
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Topic / queue name helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _em_safe_topic(topic: str) -> str:
        """Rewrite a topic into an Event-Mesh-valid form.

        Classic SAP Event Mesh rejects an underscore in a topic level with
        ``400 INVALID_INPUT: "Topic is invalid"``. Every other topic in the
        system is already underscore-free dotted form (``run.status``,
        ``node.dispatched``, ``workflow.results`` …); only the ``child_run.*``
        lifecycle topics carried an underscore, so they were the only ones
        failing to publish. Map ``_`` → ``-`` so the domain ``EventTopic``
        vocabulary stays intact everywhere except on the Event Mesh wire.

        Applied through ``_qualify_topic`` — the single chokepoint for both
        publish and subscription — so producers and consumers bind to the same
        wire name. Idempotent; a no-op for already-safe topics.
        """
        return topic.replace("_", "-")

    def _qualify_topic(self, topic: str) -> str:
        """Prepend the namespace to a topic if not already qualified.

        SAP Event Mesh requires topics to be prefixed with the service
        namespace, e.g. ``simplemdg/event/core-2/run.started``.
        """
        topic = self._em_safe_topic(topic)
        if not self._namespace:
            return topic
        prefix = self._namespace.rstrip("/") + "/"
        if topic.startswith(prefix):
            return topic
        return prefix + topic

    def _qualify_queue(self, queue_name: str) -> str:
        """Prepend the namespace to a queue name if not already qualified."""
        if not self._namespace:
            return queue_name
        prefix = self._namespace.rstrip("/") + "/"
        if queue_name.startswith(prefix):
            return queue_name
        return prefix + queue_name

    def _adopt_namespace_from_error(self, body: str) -> bool:
        """Adopt the instance namespace revealed in a 403 management response.

        When EVENT_MESH_NAMESPACE is unset and the service key carries no
        top-level namespace, queue/topic names go out unqualified and the
        shared management backend 403-rejects them with
        ``... is not in the namespace=<ns> ...``. Parsing ``<ns>`` lets the
        client self-heal: once adopted, ``_qualify_topic``/``_qualify_queue``
        prefix it everywhere. Returns ``True`` if a new namespace was adopted.
        """
        if "not in the namespace" not in (body or ""):
            return False
        match = _NAMESPACE_FROM_ERROR_RE.search(body)
        if not match:
            return False
        discovered = match.group(1).strip().rstrip("./")
        if not discovered or discovered == self._namespace:
            return False
        logger.warning(
            "Event Mesh namespace was %r; adopting %r discovered from the "
            "broker's 403 response so queue/topic names qualify correctly",
            self._namespace, discovered,
        )
        self._namespace = discovered
        return True

    @staticmethod
    def _encode_segment(value: str) -> str:
        """URL-encode a path segment for the REST API."""
        return quote(value, safe="")

    # ------------------------------------------------------------------
    # publish_to_topic
    # ------------------------------------------------------------------

    def publish_to_topic(
        self, topic: str, message: Any, key: Optional[str] = None
    ) -> None:
        """Publish a JSON message to an Event Mesh topic."""
        qualified = self._qualify_topic(topic)
        encoded = self._encode_segment(qualified)
        url = f"{self._messaging_url}/messagingrest/v1/topics/{encoded}/messages"
        payload = json.dumps(message).encode("utf-8")
        if len(payload) > _MAX_BROKER_MESSAGE_BYTES:
            # SA-1951 — this used to WARN and fall through into a POST that Event
            # Mesh answers with a 413. Warning about a condition the very next
            # line hard-fails on gave the caller an httpx error it did not catch,
            # and the task result was lost. Fail here, typed, so the caller can
            # degrade to a small message instead.
            raise MessageTooLargeError(
                f"topic {qualified}", len(payload), _MAX_BROKER_MESSAGE_BYTES,
            )
        headers = {
            **self._auth_headers(),
            "Content-Type": "application/json",
            "x-qos": "1",
        }
        if key:
            headers["x-message-key"] = key

        try:
            resp = self._http.post(url, content=payload, headers=headers)
            resp.raise_for_status()
            logger.debug("Published to topic %s", topic)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Event Mesh publish failed: %s %s (topic=%s)",
                exc.response.status_code, exc.response.text[:200], topic,
            )
            raise
        except Exception:
            logger.exception("Event Mesh publish error (topic=%s)", topic)
            raise

    # ------------------------------------------------------------------
    # subscribe_to_topic (callback-based polling)
    # ------------------------------------------------------------------

    @staticmethod
    def queue_local_from_topic(topic: str, transport: str | None = None) -> str:
        """The queue local-name this client consumes *topic* from.

        A queue may not be shared between REST polling and AMQP push. Measured
        on tenant-1 2026-08-05: SAP allocates **3 broker-side consumer flows
        per REST-polled queue**, and stopping the poller does *not* release
        them — they expire only after 30 minutes with no publishing, which a
        hot queue never has. The queues are ``NON_EXCLUSIVE``, so an AMQP link
        attaching to the same queue becomes a 4th round-robin consumer and
        whatever the broker hands a stopped flow is stranded. On this service
        that means a dispatched task silently never runs.

        This is not hypothetical here: ``sub-workflow.tasks`` reads
        ``consumerCount = 3`` live today. Giving each transport its own queue
        makes the collision structurally impossible instead of a rule someone
        has to remember.

        REST keeps the historical name **exactly** — dots and all — because the
        live queue is called ``sub-workflow.tasks`` and renaming a live queue is
        the migration that caused three outages. The AMQP queue is new, so it
        takes the control plane's dot-free convention (``sub-workflow-tasks-amqp``,
        matching ``sub-workflow-results-amqp`` and its five siblings).
        """
        if transport and transport.lower() not in ("", "rest"):
            return f"sub-{topic.replace('.', '-')}-{transport.lower()}"
        return f"sub-{topic}"

    def subscribe_to_topic(self, topic: str, callback: Callable) -> None:
        """Register *callback* for *topic* and start consuming it.

        Internally creates a queue with a subscription to *topic*, then either
        attaches an AMQP receiver link (push) or polls it (REST). Both queue
        and topic names are namespace-qualified.
        """
        # Keep the token ahead of every consumer that will need one. Started
        # here rather than at construction so tests and publish-only paths
        # don't spawn a thread they never use; both transports go through this
        # method, which is the entry point the reconnect storm lived behind.
        self._start_token_refresh()

        # AMQP push consumption (no consumption-endpoint 429s) when configured
        # and reachable; otherwise fall through to the REST poll pool. The
        # fall-back is transparent so EVENT_MESH_CONSUME_TRANSPORT=amqp never
        # silently stops consumption.
        if self._consume_transport == "amqp":
            amqp_queue = self._provision_and_qualify(
                self.queue_local_from_topic(topic, transport="amqp"), topic,
            )
            # Register BEFORE the link exists. `_start_amqp_consumer` attaches
            # with real credit, so the broker may deliver during the call — and
            # a delivery that finds no handler used to be treated as success
            # and ACKed, destroying the message.
            self._register(amqp_queue, callback)
            if self._start_amqp_consumer(amqp_queue):
                self._start_dmq_watch()
                return
            self._unregister(amqp_queue, callback)

        queue_name = self._provision_and_qualify(
            self.queue_local_from_topic(topic, transport="rest"), topic,
        )
        self._register(queue_name, callback)
        self._metrics.record_consume_transport("rest")
        self._start_consumer_pool()
        self._start_dmq_watch()

    def _provision_and_qualify(self, local_queue: str, topic: str) -> str:
        """Provision *local_queue* for *topic* and return its qualified name.

        Re-qualifies AFTER provisioning: ``create_queue`` may adopt the
        instance namespace from a 403, and the callback key / consume / AMQP
        address must all use the corrected name.
        """
        self._provision_subscription(local_queue, topic)
        return self._qualify_queue(local_queue)

    def _register(self, queue_name: str, callback: Callable) -> None:
        with self._sub_lock:
            self._callbacks.setdefault(queue_name, []).append(callback)

    def _unregister(self, queue_name: str, callback: Callable) -> None:
        """Undo a `_register` — used when AMQP refused and REST takes over."""
        with self._sub_lock:
            handlers = self._callbacks.get(queue_name)
            if not handlers:
                return
            try:
                handlers.remove(callback)
            except ValueError:
                return
            if not handlers:
                self._callbacks.pop(queue_name, None)

    def _provision_subscription(self, local_queue: str, topic: str) -> None:
        """Idempotently ensure the queue + subscription for *topic* exist.

        Startup-safe: ``create_queue`` self-heals a missing namespace and
        tolerates 403/409, and a residual 403/409 on the subscription (it may
        already exist in this or another instance) is logged rather than
        raised. Names are re-qualified between steps because ``create_queue``
        may adopt the instance namespace from a 403.
        """
        # Upsert: creates a missing queue AND converges an existing one onto the
        # DMQ + redelivery policy, without ever deleting it.
        self.ensure_queue(self._qualify_queue(local_queue))

        queue_name = self._qualify_queue(local_queue)
        qualified_topic = self._qualify_topic(topic)
        if self.subscription_exists(queue_name, qualified_topic):
            return
        try:
            self.create_subscription(queue_name, qualified_topic)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (403, 409):
                raise
            logger.warning(
                "Event Mesh subscription %s -> %s not provisioned (status=%d) — "
                "continuing; it may already exist in this/another instance",
                queue_name, qualified_topic, exc.response.status_code,
            )

    def _start_consumer_pool(self) -> None:
        """Start the pool of competing-consumer REST poll threads once.

        They all poll the same queue(s); Event Mesh delivers each message to
        exactly one thread, so this parallelizes the consume+handle+ack REST
        round-trips (legacy consume path).
        """
        if not self._consumer_pool:
            self._running = True
            for i in range(self._consumer_threads_count):
                t = threading.Thread(
                    target=self._poll_loop,
                    name=f"event-mesh-consumer-{i}",
                    daemon=True,
                )
                t.start()
                self._consumer_pool.append(t)
            logger.info(
                "Event Mesh consumer pool started (%d threads, batch=%d, lock_ttl_ms=%d)",
                self._consumer_threads_count, self._poll_batch_size, self._lock_duration_ms,
            )

    def _thread_loop(self) -> asyncio.AbstractEventLoop:
        """Return this thread's asyncio loop (lazily created) for coroutine handlers."""
        loop = getattr(self._tls, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            self._tls.loop = loop
        return loop

    def _amqp_get_token(self) -> tuple[str, float]:
        """Return ``(access_token, expires_at_epoch)`` for the AMQP SASL auth.

        Reuses the broker's OAuth2 token cache; the proton consumer uses the
        token as the SASL PLAIN password.

        **The ``min_remaining_s`` argument is load-bearing.** The receiver
        stands a link down once the token is within
        ``_TOKEN_RECONNECT_MARGIN_S`` of expiry; handing the reconnect a token
        that is already inside that margin means the replacement link stands
        itself down on its first loop iteration, forever. Asking for strictly
        more life than that margin makes every reconnect terminate.
        """
        token = self._ensure_token(min_remaining_s=_AMQP_TOKEN_MIN_REMAINING_S)
        return token, self._token_expires_at + _TOKEN_REFRESH_MARGIN_S

    def _queue_is_free(self, queue_name: str) -> bool:
        """G1 — refuse to attach AMQP to a queue that already has consumers.

        The queues are ``NON_EXCLUSIVE``, so the broker round-robins across
        everything bound to them. Attaching next to a consumer that isn't
        actively fetching means whatever round-robin hands to that consumer is
        stranded — measured on a scratch queue: 20 published, AMQP received 17,
        3 stuck ``unacked`` forever. Here that is a dispatched task that never
        runs and a run that hangs with its node NOT_STARTED.

        The policy is **refuse only on positive knowledge**: anything that is
        not a definite count of bound consumers — an unreadable management
        response, a missing field — is treated as free. A management-API blip
        must not stop consumption altogether; the guard exists to catch the
        *known* failure, not to invent a new one.
        """
        count = self.consumer_count(queue_name)
        if not isinstance(count, int):
            logger.warning(
                "Could not read consumerCount for %s (got %r) — attaching AMQP "
                "anyway; an unreadable count must not stop consumption",
                queue_name, count,
            )
            return True
        if count == 0:
            return True
        logger.error(
            "Refusing to attach AMQP to %s: %d consumer(s) already bound. The "
            "queue is NON_EXCLUSIVE, so the broker would round-robin between "
            "them and this link, and anything handed to a consumer that is no "
            "longer fetching is stranded (measured: 3 of 20 lost, permanently "
            "unacked). Consuming via REST instead.",
            queue_name, count,
        )
        self._metrics.record_consume_fallback("queue_has_consumers")
        return False

    def _start_amqp_consumer(self, queue_name: str) -> bool:
        """Attach *queue_name* to the AMQP push consumer.

        Returns ``True`` if AMQP consumption is active for the queue, or
        ``False`` to fall back to the REST poll pool. The first call builds
        the consumer and probes connectivity once; on any failure the broker
        commits to REST for the process lifetime.
        """
        with self._amqp_lock:
            if self._amqp_state == "rest":
                return False
            if self._amqp_state == "amqp" and self._amqp_consumer is not None:
                if not self._queue_is_free(queue_name):
                    return False
                self._amqp_consumer.add_queue(queue_name)
                return True

            from app.layer4_frameworks.messaging.event_mesh_amqp_consumer import (
                EventMeshAMQPConsumer,
                amqp_available,
                amqp_import_error,
            )

            if not amqp_available():
                logger.warning(
                    "EVENT_MESH_CONSUME_TRANSPORT=amqp but proton is not importable "
                    "(%s) — falling back to REST consumption",
                    amqp_import_error(),
                )
                self._amqp_state = "rest"
                self._metrics.record_consume_fallback("proton_missing")
                return False

            # G1 runs BEFORE probe(), not just before add_queue: the probe
            # attaches a real receiver link and grants credit, so a queue with
            # other consumers can strand messages during the probe itself.
            if not self._queue_is_free(queue_name):
                return False

            consumer = EventMeshAMQPConsumer(
                host=self._amqp_host,
                port=self._amqp_port,
                ws_path=self._amqp_ws_path,
                get_token=self._amqp_get_token,
                message_handler=self._dispatch_to_callbacks,
                prefetch=self._amqp_prefetch,
                concurrency=self._amqp_concurrency,
                strip_namespace=self._amqp_strip_namespace,
                namespace=self._namespace,
                # G2: lets the watchdog tell "nothing to deliver" apart from
                # "attached but not delivering" — only the second is a stall.
                queue_depth=self.queue_depth,
                metrics=self._metrics,
            )
            if not consumer.probe(queue_name):
                logger.warning(
                    "Event Mesh AMQP unreachable/unusable — falling back to REST "
                    "consumption for all queues",
                )
                self._amqp_state = "rest"
                self._metrics.record_consume_fallback("amqp_unreachable")
                return False

            self._amqp_consumer = consumer
            self._amqp_state = "amqp"
            self._metrics.record_consume_transport("amqp")
            consumer.add_queue(queue_name)
            consumer.start()
            logger.info(
                "Event Mesh consume transport: AMQP push (host=%s, concurrency=%d) "
                "— first queue %s",
                self._amqp_host, self._amqp_concurrency, queue_name,
            )
            return True

    def _poll_loop(self) -> None:
        """Background thread: poll all subscribed queues and dispatch.

        Each pass drains up to ``poll_batch_size`` messages per queue
        back-to-back (no inter-message delay) and only sleeps when every queue
        is empty — so a backlog is consumed at HTTP speed instead of one
        message per ``poll_idle_sleep_s`` (the old loop's ~2 msg/s ceiling).
        """
        try:
            while self._running:
                if not self._poll_once():
                    # All queues empty this pass — back off to avoid a hot loop.
                    time.sleep(self._poll_idle_sleep_s)
        except Exception:
            logger.exception("Event Mesh consumer loop crashed")
        finally:
            loop = getattr(self._tls, "loop", None)
            if loop is not None and not loop.is_closed():
                loop.close()

    def _poll_once(self) -> bool:
        """Run one poll pass over all subscribed queues.

        Drains up to ``poll_batch_size`` messages per queue, stopping a queue
        early when it returns no message. Returns ``True`` if at least one
        message was handled this pass, so the caller skips the idle sleep while
        a backlog is still draining.
        """
        with self._sub_lock:
            queues = list(self._callbacks.keys())

        had_messages = False
        for queue_name in queues:
            for _ in range(self._poll_batch_size):
                if not self._running:
                    break
                try:
                    result = self.consume_from_queue(queue_name)
                except Exception:
                    logger.exception("Poll error on queue %s", queue_name)
                    break
                if result is None:
                    break  # queue drained for this pass
                had_messages = True
                self._dispatch_message(queue_name, result)
        return had_messages

    def _dispatch_to_callbacks(self, queue_name: str, data: Any) -> bool:
        """Run every registered callback for *queue_name* against *data*.

        Returns ``True`` only if all handlers succeed (drives the ack/settle
        decision — at-least-once). Coroutine handlers run on this thread's
        asyncio loop. Shared by the REST poll pool and the AMQP receive
        threads so both transports honour identical dispatch semantics.
        """
        with self._sub_lock:
            handlers = list(self._callbacks.get(queue_name, []))
            known = list(self._callbacks.keys())

        if not handlers:
            # Do NOT report success. An empty handler list used to fall through
            # the loop below with ``all_ok = True``, which ACKed the message and
            # destroyed it. There are two ways to get here and redelivery is the
            # right answer to both:
            #
            #   * a race — the transport attached and the broker delivered
            #     before `subscribe_to_topic` had registered the callback.
            #     Measured on the control plane (tenant-1 2026-08-05): six
            #     messages destroyed on one startup. Here that would be six
            #     dispatched tasks that never run.
            #   * a key mismatch between the name a subscription registered
            #     under and the name the transport dispatches with.
            #
            # Leaving the message unsettled lets the lock expire so the broker
            # redelivers it — by which time a racing handler exists. A genuine
            # mismatch exhausts maxRedeliveryCount and lands in the DMQ, which
            # is alertable and recoverable. Acking destroyed it.
            logger.warning(
                "No handler registered for queue %r — leaving the message "
                "UNSETTLED for redelivery. Registered keys: %r",
                queue_name, known,
            )
            return False

        all_ok = True
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    self._thread_loop().run_until_complete(handler(data))
                else:
                    handler(data)
            except Exception:
                logger.exception(
                    "Error in callback for queue %s — skipping ack (will redeliver)",
                    queue_name,
                )
                all_ok = False
        return all_ok

    def _dispatch_message(self, queue_name: str, result: dict) -> None:
        """Run all handlers for one consumed message, then acknowledge it.

        At-least-once: the message is acked ONLY if every handler succeeds. A
        handler exception (e.g. the executor's backpressure signal when at
        capacity) skips the ack so Event Mesh redelivers after the lock TTL,
        rather than acking and dropping the task. Per-message ack is preserved
        (consume → handle → ack), so batch draining never holds un-acked
        messages past their lock TTL. (Redelivery is made safe by the
        consumer's ``store_if_absent`` dedup.)
        """
        all_ok = self._dispatch_to_callbacks(queue_name, result["data"])

        msg_id = result.get("message_id")
        if msg_id and all_ok:
            try:
                self.acknowledge(queue_name, msg_id)
            except Exception:
                logger.exception("Ack error on queue %s", queue_name)
        elif msg_id and not all_ok:
            logger.warning(
                "Skipping ack for queue %s — handler failure; "
                "message redelivers after lock TTL",
                queue_name,
            )

    # ------------------------------------------------------------------
    # Queue-based methods
    # ------------------------------------------------------------------

    def publish_to_queue(self, queue_name: str, message: Any) -> None:
        """Publish directly to a queue (Event Mesh REST)."""
        qualified = self._qualify_queue(queue_name)
        encoded = self._encode_segment(qualified)
        # SA-1941 — do not log the full message (it can carry a large inline
        # result payload → duplicates it in RAM + floods logs).
        logger.info("Publishing to queue %s (qualified=%s)", queue_name, qualified)
        url = f"{self._messaging_url}/messagingrest/v1/queues/{encoded}/messages"
        payload = json.dumps(message).encode("utf-8")
        if len(payload) > _MAX_BROKER_MESSAGE_BYTES:
            logger.warning(
                "Event Mesh message to queue %s is %d bytes (> %d) — likely a "
                "large inline worker output; enable worker-side streaming "
                "(SA-1944) to relay a pointer instead",
                qualified, len(payload), _MAX_BROKER_MESSAGE_BYTES,
            )
        headers = {**self._auth_headers(), "Content-Type": "application/json", "x-qos": "1"}

        try:
            resp = self._http.post(url, content=payload, headers=headers)
            resp.raise_for_status()
            logger.debug("Published to queue %s", queue_name)
        except Exception:
            logger.exception("Event Mesh queue publish error (queue=%s)", queue_name)
            raise

    def consume_from_queue(self, queue_name: str) -> Optional[dict]:
        """Consume one message from a queue.

        Returns ``{"data": <parsed>, "message_id": <id>}`` or ``None``.
        """
        qualified = self._qualify_queue(queue_name)
        encoded = self._encode_segment(qualified)
        url = f"{self._messaging_url}/messagingrest/v1/queues/{encoded}/messages/consumption"
        # ``x-lock-duration-in-ms`` holds the message (no redelivery) until we
        # ack. Without it, the broker default can be short enough that under
        # concurrent consumers a still-being-processed message is redelivered to
        # a sibling thread/instance → duplicate dispatch (the ``store_if_absent``
        # dedup catches it, but the lock avoids the wasted round-trip).
        headers = {
            **self._auth_headers(),
            "x-qos": "1",
            "x-lock-duration-in-ms": str(self._lock_duration_ms),
        }

        try:
            resp = self._http.post(url, headers=headers)
            if resp.status_code == 204:
                return None  # no message available
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 204:
                return None
            logger.error(
                "Event Mesh consume failed: %s (queue=%s)",
                exc.response.status_code, queue_name,
            )
            raise
        except Exception:
            logger.exception("Event Mesh consume error (queue=%s)", queue_name)
            raise

        # Parse response
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            data = resp.text
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, ValueError):
                    data = {"raw": data}

        message_id = resp.headers.get("x-message-id", "")
        return {"data": data, "message_id": message_id}

    def acknowledge(self, queue_name: str, message_id: str) -> None:
        """Acknowledge a consumed message."""
        qualified = self._qualify_queue(queue_name)
        encoded_queue = self._encode_segment(qualified)
        encoded_id = quote(message_id, safe="")
        url = (
            f"{self._messaging_url}/messagingrest/v1/queues/{encoded_queue}"
            f"/messages/{encoded_id}/acknowledgement"
        )
        headers = self._auth_headers()

        try:
            resp = self._http.post(url, headers=headers)
            resp.raise_for_status()
            logger.debug("Acknowledged message %s on queue %s", message_id, queue_name)
        except Exception:
            logger.exception(
                "Event Mesh ack error (queue=%s, message_id=%s)",
                queue_name, message_id,
            )
            raise

    # ------------------------------------------------------------------
    # Queue / subscription management (Management API)
    # ------------------------------------------------------------------

    def _management_get(self, url: str) -> Optional[httpx.Response]:
        """GET a management endpoint, retrying transient connection blips.

        Returns the response (any status) or ``None`` if the endpoint stayed
        unreachable after ``_MANAGEMENT_GET_ATTEMPTS``. A momentary DNS/socket
        hiccup logs a concise WARNING (not a full traceback) and degrades to
        "unknown"; the idempotent create that follows recovers it.
        """
        last_exc: Exception | None = None
        for attempt in range(_MANAGEMENT_GET_ATTEMPTS):
            try:
                return self._http.get(url, headers=self._auth_headers())
            except httpx.TransportError as exc:  # ConnectError/timeout/etc — transient
                last_exc = exc
                if attempt < _MANAGEMENT_GET_ATTEMPTS - 1:
                    time.sleep(_MANAGEMENT_GET_RETRY_DELAY_S)
            except Exception as exc:  # unexpected — don't retry, don't raise
                logger.warning("Event Mesh management GET failed (%s): %s", url, exc)
                return None
        logger.warning(
            "Event Mesh management endpoint unreachable after %d attempts (%s): %s",
            _MANAGEMENT_GET_ATTEMPTS, url, last_exc,
        )
        return None

    def queue_exists(self, queue_name: str) -> bool:
        qualified = self._qualify_queue(queue_name)
        encoded = self._encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        resp = self._management_get(url)
        return resp is not None and resp.status_code == 200

    def get_queue(self, queue_name: str) -> Optional[dict]:
        """The management-API view of *queue_name*; None if unreadable."""
        qualified = self._qualify_queue(queue_name)
        encoded = self._encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        resp = self._management_get(url)
        if resp is None or resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    def consumer_count(self, queue_name: str) -> Optional[int]:
        """Consumers currently bound to *queue_name*; None if unknown.

        A LIVE count, not a configured limit — measured on tenant-1
        2026-08-05: a queue nothing had ever consumed from read 0, while every
        REST-polled production queue read 3 (SAP allocates 3 flows per
        REST-polled queue and releases them only after 30 minutes with no
        publishing).
        """
        body = self.get_queue(queue_name)
        if body is None:
            return None
        value = body.get("consumerCount")
        return int(value) if isinstance(value, (int, float)) else None

    def queue_depth(self, queue_name: str) -> Optional[int]:
        """Messages spooled on *queue_name*; None if unknown."""
        body = self.get_queue(queue_name)
        if body is None:
            return None
        value = body.get("messageCount")
        return int(value) if isinstance(value, (int, float)) else None

    def is_dmq(self, queue_name: str) -> bool:
        """True for a dead-message queue — it must never get a DMQ of its own.

        Public because callers outside provisioning need it too: ``dmq_name``
        APPENDS the suffix, so ``dmq_name(q) == q`` never holds and cannot be
        used as the test.
        """
        return queue_name.endswith(self._dmq_suffix)

    def dmq_name(self, queue_name: str) -> str:
        """Qualified name of the DMQ that backs *queue_name*."""
        return self._qualify_queue(queue_name) + self._dmq_suffix

    def _queue_body(self, qualified: str, with_dmq: Optional[bool] = None) -> dict:
        """Management-API body for a queue.

        Only the fields we deliberately own are sent — a PUT updates exactly
        what it carries, so omitting ``respectTtl``/``accessType``/sizes leaves
        the broker's values untouched.
        """
        body: dict = {
            "messageLockExpirationInSeconds": max(1, self._lock_duration_ms // 1000),
        }
        # A DMQ takes no redelivery cap and no DMQ of its own — otherwise a
        # message failing in the DMQ would need somewhere else to go.
        # ``with_dmq=None`` means "decide from config"; ``False`` is the caller
        # telling us the DMQ could not be provisioned.
        if with_dmq is None:
            with_dmq = self._dmq_enabled and not self.is_dmq(qualified)
        if with_dmq:
            body["maxRedeliveryCount"] = self._max_redelivery_count
            body["deadMsgQueue"] = self.dmq_name(qualified)
        return body

    def ensure_queue(self, queue_name: str) -> None:
        """Ensure *queue_name* exists, has a DMQ, and carries the redelivery cap.

        The management PUT is a true upsert: it creates a missing queue (201)
        **and updates an existing one** (200), changing exactly the fields the
        body carries. So this converges queues provisioned before the DMQ
        policy existed **without deleting them** — which matters, because a
        delete/re-create cycle drops every message published while no queue is
        subscribed to the topic, and the publisher still gets HTTP 204.
        """
        qualified = self._qualify_queue(queue_name)
        # The DMQ must exist before it can be referenced as ``deadMsgQueue``; a
        # dangling reference dead-letters into a 404. If it can't be
        # provisioned, provision the queue WITHOUT the cap rather than point it
        # at a queue that isn't there — unbounded redelivery is bad, but
        # dead-lettering into the void is worse.
        with_dmq = self._dmq_enabled and not self.is_dmq(qualified)
        if with_dmq and not self._ensure_dmq(qualified):
            logger.warning(
                "Event Mesh DMQ %s unavailable — provisioning %s without a "
                "redelivery cap rather than referencing a queue that does not "
                "exist",
                self.dmq_name(qualified), qualified,
            )
            with_dmq = False

        existed = self.queue_exists(qualified)
        try:
            self.create_queue(qualified, with_dmq=with_dmq)
        except Exception:
            # A first creation failing is fatal — there is nothing to consume
            # from. Converging an EXISTING queue is not: the queue is already
            # usable, only the policy update missed, and startup must not die
            # on a transient management-API blip.
            if not existed:
                raise
            logger.warning(
                "Event Mesh queue %s exists but its redelivery policy could "
                "not be converged this start — continuing", qualified,
            )

    def _ensure_dmq(self, qualified: str) -> bool:
        """Best-effort provision of the DMQ backing *qualified*. True if usable."""
        dmq = self.dmq_name(qualified)
        try:
            if not self.queue_exists(dmq):
                self.create_queue(dmq)
            return True
        except Exception:
            logger.exception("Event Mesh DMQ provisioning failed (queue=%s)", dmq)
            return False

    def create_queue(
        self,
        queue_name: str,
        *,
        with_dmq: Optional[bool] = None,
        _allow_namespace_heal: bool = True,
    ) -> None:
        qualified = self._qualify_queue(queue_name)
        encoded = self._encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        body = self._queue_body(qualified, with_dmq)
        headers = {**self._auth_headers(), "Content-Type": "application/json"}
        try:
            resp = self._http.put(url, headers=headers, json=body)
            resp.raise_for_status()
            logger.info(
                "Provisioned queue %s (%s)",
                qualified,
                ", ".join(f"{k}={v}" for k, v in body.items()),
            )
        except httpx.HTTPStatusError as exc:
            body_snippet = ""
            try:
                body_snippet = exc.response.text[:1000]
            except Exception:
                pass
            status = exc.response.status_code
            # Self-heal: a 403 revealing the instance namespace means the name
            # went out unqualified. Adopt the namespace and retry once.
            if (
                status == 403
                and _allow_namespace_heal
                and self._adopt_namespace_from_error(body_snippet)
            ):
                logger.warning(
                    "Retrying create_queue %s after adopting namespace %r",
                    queue_name, self._namespace,
                )
                self.create_queue(
                    queue_name, with_dmq=with_dmq, _allow_namespace_heal=False,
                )
                return
            # An already-existing queue (409) — or one owned by another instance
            # we can't (re)create here (403) — is not fatal: don't crash startup.
            if status in (403, 409):
                logger.warning(
                    "Event Mesh create_queue %s skipped (status=%d): %s — queue "
                    "may already exist in this/another instance; continuing",
                    qualified, status, body_snippet,
                )
                return
            logger.error(
                "Event Mesh create_queue %s failed: status=%d, body=%r, url=%s",
                qualified, status, body_snippet, url,
            )
            raise
        except Exception:
            logger.exception("Event Mesh create_queue error (queue=%s)", queue_name)
            raise

    def delete_queue(self, queue_name: str) -> None:
        qualified = self._qualify_queue(queue_name)
        encoded = self._encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        try:
            resp = self._http.delete(url, headers=self._auth_headers())
            resp.raise_for_status()
            logger.info("Deleted queue %s", queue_name)
        except Exception:
            logger.exception("Event Mesh delete_queue error (queue=%s)", queue_name)
            raise

    def subscription_exists(self, queue_name: str, topic: str) -> bool:
        encoded_queue = self._encode_segment(self._qualify_queue(queue_name))
        encoded_topic = self._encode_segment(self._qualify_topic(topic))
        url = (
            f"{self._management_url}/hub/rest/api/v1/management/messaging"
            f"/queues/{encoded_queue}/subscriptions/{encoded_topic}"
        )
        resp = self._management_get(url)
        return resp is not None and resp.status_code == 200

    def create_subscription(self, queue_name: str, topic: str) -> None:
        encoded_queue = self._encode_segment(self._qualify_queue(queue_name))
        encoded_topic = self._encode_segment(self._qualify_topic(topic))
        url = (
            f"{self._management_url}/hub/rest/api/v1/management/messaging"
            f"/queues/{encoded_queue}/subscriptions/{encoded_topic}"
        )
        try:
            resp = self._http.put(url, headers=self._auth_headers())
            resp.raise_for_status()
            logger.info("Created subscription: queue=%s topic=%s", queue_name, topic)
        except Exception:
            logger.exception(
                "Event Mesh create_subscription error (queue=%s, topic=%s)",
                queue_name, topic,
            )
            raise

    def delete_subscription(self, queue_name: str, topic: str) -> None:
        encoded_queue = self._encode_segment(self._qualify_queue(queue_name))
        encoded_topic = self._encode_segment(self._qualify_topic(topic))
        url = (
            f"{self._management_url}/hub/rest/api/v1/management/messaging"
            f"/queues/{encoded_queue}/subscriptions/{encoded_topic}"
        )
        try:
            resp = self._http.delete(url, headers=self._auth_headers())
            resp.raise_for_status()
            logger.info("Deleted subscription: queue=%s topic=%s", queue_name, topic)
        except Exception:
            logger.exception(
                "Event Mesh delete_subscription error (queue=%s, topic=%s)",
                queue_name, topic,
            )
            raise

    # ------------------------------------------------------------------
    # Dead-letter queue
    # ------------------------------------------------------------------

    def publish_to_dlq(self, original_topic: str, message: Any, error: str) -> None:
        """Publish a failed message to a ``.dlq`` queue for the topic."""
        dlq_queue = f"{original_topic}.dlq"  # qualify_queue applied inside publish_to_queue
        envelope = {
            "original_topic": original_topic,
            "error": error,
            "message": message,
        }
        self.publish_to_queue(dlq_queue, envelope)

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Stop the consumer pool / AMQP consumer and close the HTTP client."""
        self._running = False

        self._dmq_stop.set()
        if self._dmq_thread is not None:
            self._dmq_thread.join(timeout=5)
            self._dmq_thread = None

        self._token_refresh_stop.set()
        if self._token_refresh_thread is not None:
            self._token_refresh_thread.join(timeout=5)
            self._token_refresh_thread = None

        if self._amqp_consumer is not None:
            try:
                self._amqp_consumer.stop()
            except Exception:
                logger.exception("Error stopping Event Mesh AMQP consumer")
            self._amqp_consumer = None

        for t in self._consumer_pool:
            t.join(timeout=10)
        self._consumer_pool = []

        try:
            self._http.close()
        except Exception:
            logger.exception("Error closing Event Mesh HTTP client")
