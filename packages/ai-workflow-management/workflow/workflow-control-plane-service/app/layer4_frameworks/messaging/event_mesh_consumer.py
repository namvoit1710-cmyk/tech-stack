"""Consume side of the SAP Event Mesh broker.

Subscribes queues to topics (via the admin), then delivers messages through one
of two transports — AMQP 1.0 push (no consumption-endpoint 429s) or the legacy
REST poll loop — falling back from AMQP to REST transparently. Dispatch is
at-least-once: a message is acked only when every handler succeeds; on failure
the message is left for redelivery after its lock TTL. A 429 on the REST
consumption endpoint pauses ALL queue polling (the limit is per service
instance, not per queue).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.layer4_frameworks.messaging.event_mesh_admin import EventMeshAdmin
from app.layer4_frameworks.messaging.event_mesh_http import EventMeshHttpClients
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames
from app.layer4_frameworks.messaging.event_mesh_token_provider import (
    EventMeshTokenProvider,
)

logger = logging.getLogger(__name__)

# Consume-side rate limiting. Event Mesh throttles the REST messaging API per
# service instance; a 429 on one queue means ALL consumption calls are being
# rejected, so the poll loop pauses globally instead of moving on to hammer the
# next queue. Backoff honors Retry-After when present, else grows exponentially
# per consecutive-429 streak up to the cap.
_RATE_LIMIT_BASE_BACKOFF_S = 1.0
_RATE_LIMIT_MAX_BACKOFF_S = 30.0

# Messages drained per queue per poll pass. 1 restores the pre-2026-08-05
# behaviour (one message per queue per pass), which spread a burst of sibling
# orchestration actions across successive poll intervals. Bounded so one busy
# queue cannot starve the others in the round-robin, and so a drain still
# yields to the 429 cooldown check between messages.
_BATCH_MAX_DEFAULT = 10

# How often the dead-letter watch samples DMQ depth. One management GET per
# subscribed queue per pass, so a minute is cheap against six queues while
# still catching a loss burst inside the window an operator would care about.
_DMQ_CHECK_SECONDS_DEFAULT = 60.0

# Outcomes of an AMQP attach. These used to be a bare ``False`` plus a metric
# tag, so ``subscribe`` could not tell them apart — and they call for very
# different responses:
#
#   ATTACH_PEER         another pod already consumes this queue. Nothing is
#                       lost by standing down; standing down is CORRECT.
#   ATTACH_UNREACHABLE  endpoint/auth problem, possibly transient. Retry.
#   ATTACH_NO_LIBS      the image lacks proton. Permanent for this pod.
#
# Collapsing all three into "switch to REST on the bare queue" is what
# re-created the retired subscriptions on tenant-1 2026-08-11.
ATTACH_OK = "ok"
ATTACH_PEER = "peer_owns_queue"
ATTACH_NO_LIBS = "proton_missing"
ATTACH_UNREACHABLE = "amqp_unreachable"

# Conditions that can clear without a redeploy, so a stood-down topic is worth
# re-attempting. A failed import is not one of them.
_RETRYABLE_ATTACH = frozenset({ATTACH_PEER, ATTACH_UNREACHABLE})

# How often stood-down topics are re-attempted. Sized against the thing it
# waits for: a peer disappearing (scale-down, crash) or an endpoint coming
# back. Each pass costs one management GET per stood-down topic.
_STANDDOWN_RETRY_SECONDS_DEFAULT = 30.0


class EventMeshConsumer:
    """Callback-based consume via AMQP push (preferred) or REST poll loop."""

    def __init__(
        self,
        *,
        messaging_url: str,
        names: EventMeshNames,
        tokens: EventMeshTokenProvider,
        admin: EventMeshAdmin,
        clients: EventMeshHttpClients,
        poll_interval_s: float,
        lock_duration_ms: int,
        handler_timeout_s: float | None,
        consume_transport: str,
        amqp_host: str,
        amqp_port: int,
        amqp_ws_path: str,
        amqp_prefetch: int,
        amqp_concurrency: int,
        amqp_strip_namespace: bool,
        metrics: EventMeshMetrics | None = None,
        cooldown_scope: str = "instance",
        batch_max: int = _BATCH_MAX_DEFAULT,
        dmq_check_seconds: float = _DMQ_CHECK_SECONDS_DEFAULT,
        runtime_fallback: bool = False,
        standdown_retry_seconds: float = _STANDDOWN_RETRY_SECONDS_DEFAULT,
    ) -> None:
        self._messaging_url = messaging_url
        self._names = names
        self._tokens = tokens
        self._admin = admin
        self._clients = clients
        self._poll_interval_s = poll_interval_s
        self._lock_duration_ms = lock_duration_ms
        self._handler_timeout_s = handler_timeout_s
        self._batch_max = max(1, int(batch_max))
        self._dmq_check_seconds = max(10.0, float(dmq_check_seconds))

        self._callbacks: dict[str, list[Callable]] = {}
        self._sub_lock = threading.Lock()
        # SA-2319: one poll thread PER QUEUE. A single shared thread that
        # round-robined every queue meant a blocking handler on one queue
        # stopped every other queue being polled — see ``_poll_loop``.
        self._consumer_threads: dict[str, threading.Thread] = {}
        self._thread_lock = threading.Lock()
        self._running = False
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Dead-letter watch. A filling DMQ is THE signal that messages are
        # being lost, and until now nothing measured it: P4 covers links,
        # stalls, reconnects and deliveries, and the runbook's "check the DMQ"
        # only helps someone already looking. Deliberately here rather than in
        # the AMQP watchdog, because that watchdog does not run under REST —
        # and REST is the rollback target, i.e. exactly when you most need to
        # know whether messages are being dead-lettered.
        self._dmq_thread: threading.Thread | None = None
        self._dmq_stop = threading.Event()
        self._dmq_seen: dict[str, int] = {}

        # 429 cooldown state (SA-1470). Keyed per cooldown scope:
        #   "instance" (default) — one shared entry (key "") pauses every
        #   queue, matching SAP Event Mesh's per-service-instance REST limit;
        #   "queue" — each queue cools down alone, so a 429 caused by one
        #   noisy queue no longer stalls orchestration.actions consumption
        #   (use where the broker's limits are actually per-queue).
        # Entry = (monotonic resume deadline, consecutive-429 streak).
        self._metrics = metrics or EventMeshMetrics(None)
        self._cooldown_scope = (
            "queue" if str(cooldown_scope or "").lower() == "queue" else "instance"
        )
        self._cooldowns: dict[str, tuple[float, int]] = {}
        self._cooldown_lock = threading.Lock()

        self._consume_transport = consume_transport
        self._amqp_host = amqp_host
        self._amqp_port = amqp_port
        self._amqp_ws_path = amqp_ws_path
        self._amqp_prefetch = amqp_prefetch
        self._amqp_concurrency = amqp_concurrency
        self._amqp_strip_namespace = amqp_strip_namespace
        self._amqp_consumer: Any = None
        # None = undecided, "amqp" = committed to push, "rest" = fell back.
        # With ``runtime_fallback`` off there is nothing to fall back TO, so
        # this stays undecided on a failed attach and ``retry_stood_down`` can
        # re-probe. Committing to "rest" would make the retry a no-op.
        self._amqp_state: str | None = None
        self._amqp_fallback_reason: str = ""
        self._amqp_lock = threading.Lock()

        # Topics this pod deliberately does not consume, and why. Populated
        # instead of silently REST-polling a retired queue.
        self._runtime_fallback = bool(runtime_fallback)
        self._standdown_retry_seconds = max(1.0, float(standdown_retry_seconds))
        # topic -> (amqp_queue, callback, reason)
        self._stood_down: dict[str, tuple[str, Callable, str]] = {}
        self._standdown_lock = threading.Lock()
        self._standdown_thread: threading.Thread | None = None
        self._standdown_stop = threading.Event()
        # Queues whose peer-consumer refusal has already been logged loudly.
        # Cleared when the queue frees up, so a NEW refusal is loud again.
        self._refusal_logged: set[str] = set()

    @property
    def _http(self) -> httpx.Client:
        return self._clients.sync

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the main asyncio event loop for dispatching async handlers."""
        self._main_loop = loop

    # ------------------------------------------------------------------
    # subscribe
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, callback: Callable) -> None:
        """Register *callback* for *topic* and start consuming its queue.

        Creates a queue subscribed to *topic* (namespace-qualified), then
        consumes it via AMQP push when ``consume_transport == "amqp"`` and the
        AMQP endpoint is reachable, otherwise via the REST poll loop. The
        public contract is unchanged regardless of transport.
        """
        # Transport-scoped (P1): REST and AMQP never share a queue, so a
        # transport switch always lands on a queue with no consumers of the
        # other kind still bound to it.
        if self._consume_transport == "amqp":
            amqp_queue = self._provision(topic, transport="amqp")
            # Register BEFORE the link exists. `_start_amqp_consumer` probes and
            # then attaches a real receiver with real credit, so the broker can
            # deliver before this call returns — and a message landing on an
            # empty handler list used to be acked and destroyed. Measured on
            # tenant-1 2026-08-05: six `orchestration.actions` messages lost on
            # a single startup, where a discarded StartRunAction is a
            # permanently hung run. REST never had this window because it
            # registers before starting its poll loop.
            self._register(amqp_queue, callback)
            reason = self._start_amqp_consumer(amqp_queue)
            if reason == ATTACH_OK:
                self._start_dmq_watch()
                return
            # AMQP refused this queue, so nothing will consume it here. Take the
            # callback back off before deciding what to do instead.
            self._unregister(amqp_queue, callback)

            if not self._runtime_fallback:
                # STOP. The bare queue is RETIRED — P5.4 deleted its
                # subscription precisely so the topic stops fanning out.
                # ``_provision`` below would re-create it (``subscription_exists``
                # never short-circuits: the management API does not answer a
                # point GET on the subscription path), the topic would fan out
                # to two queues, and every message would be delivered twice.
                # Measured on tenant-1 2026-08-11: five topics, 3 stale
                # consumer flows each, from a 65-second two-instance window.
                #
                # The transport is a configuration decision. Stand the topic
                # down, say so, and retry — never improvise a different one.
                self._stand_down(topic, amqp_queue, callback, reason)
                return

            logger.warning(
                "AMQP unavailable for %s (%s) — EVENT_MESH_RUNTIME_FALLBACK is "
                "ON, so REST-polling %s instead. This RE-CREATES that queue's "
                "subscription and duplicates every message on the topic while "
                "another consumer holds the AMQP queue.",
                amqp_queue, reason, self._names.queue_local_from_topic(topic),
            )

        rest_queue = self._provision(topic)
        self._register(rest_queue, callback)
        # SA-2319 made this one thread PER QUEUE, so the queue is now an
        # argument. Every other reference was converted — the definition,
        # ``_poll_loop``, and all 13 call sites in the tests — but this, the
        # only production caller, was not: ``subscribe()`` raised
        # ``TypeError: _start_poll_loop() missing 1 required positional
        # argument`` for every REST-transport subscription, including the AMQP
        # fallback directly above.
        self._start_poll_loop(rest_queue)
        self._start_dmq_watch()

    def _provision(self, topic: str, transport: str | None = None) -> str:
        """Provision *topic*'s queue for *transport* and return its qualified name."""
        local_queue = self._names.queue_local_from_topic(topic, transport=transport)
        # Best-effort provisioning (self-heals namespace; tolerant of 403/409).
        self._admin.provision_subscription(local_queue, topic)
        # Re-qualify AFTER provisioning in case the namespace was just adopted,
        # so the callback key / consume / AMQP all use the corrected name.
        return self._names.qualify_queue(local_queue)

    def _register(self, queue_name: str, callback: Callable) -> None:
        with self._sub_lock:
            self._callbacks.setdefault(queue_name, []).append(callback)

    # -- dead-letter watch ----------------------------------------------------

    def sample_dead_letters(self) -> dict[str, int]:
        """Read every subscribed queue's DMQ depth; emit the deltas as metrics.

        Two signals, because they answer different questions:

        * ``eventmesh.dmq.deadlettered{queue}`` — a **counter** of newly
          dead-lettered messages. Rate-able and labelled, so it says *messages
          are being lost right now, on this queue*. This is the alert.
        * ``eventmesh.dmq.depth`` — a **gauge** of the total sitting across all
          DMQs. Depth stays high forever after one incident, so on its own it
          cannot tell "happening now" from "happened last week" — but it is the
          only thing that shows a backlog which predates this process.

        The first reading of each DMQ only seeds the baseline. Without that, a
        restart would report every message already sitting there as a fresh
        burst of loss, and the alert would cry wolf on every deploy.

        Returns ``{dmq_name: depth}`` for the DMQs that answered.
        """
        with self._sub_lock:
            queues = list(self._callbacks.keys())

        depths: dict[str, int] = {}
        for queue_name in queues:
            try:
                if self._admin.is_dmq(queue_name):
                    continue  # a DMQ has no DMQ of its own
                dmq = self._admin.dmq_name(queue_name)
                depth = self._admin.queue_depth(dmq)
            except Exception:
                logger.debug("DMQ depth unavailable for %s", queue_name, exc_info=True)
                continue
            if depth is None:
                continue

            depths[dmq] = depth
            previous = self._dmq_seen.get(dmq)
            self._dmq_seen[dmq] = depth
            if previous is None:
                if depth:
                    logger.warning(
                        "DMQ %s already holds %d message(s) at startup — these "
                        "are messages that were lost before this process began",
                        dmq, depth,
                    )
                continue
            if depth > previous:
                self._metrics.record_dead_lettered(dmq, depth - previous)
                logger.warning(
                    "DMQ %s grew by %d (now %d) — messages are being lost",
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
        while not self._dmq_stop.is_set():
            try:
                self.sample_dead_letters()
            except Exception:
                # Observability must never be the thing that stops consumption.
                logger.exception("DMQ watch failed")
            self._dmq_stop.wait(self._dmq_check_seconds)
        logger.info("DMQ watch stopped")

    def _unregister(self, queue_name: str, callback: Callable) -> None:
        """Undo a `_register` — used when AMQP declines the queue it was
        registered for, so a queue nothing consumes does not keep a handler."""
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

    def _start_poll_loop(self, queue_name: str) -> None:
        """Start a REST poll thread for *queue_name*, once per queue.

        SA-2319 — this used to start ONE thread for the whole consumer, which
        then round-robined every subscribed queue. That is the change; the
        reason is in ``_poll_loop``.

        Idempotent per queue: ``subscribe`` may be called twice for the same
        topic (two callbacks on one queue) and must not start a second thread.
        """
        with self._thread_lock:
            existing = self._consumer_threads.get(queue_name)
            if existing is not None and existing.is_alive():
                return
            first = not self._consumer_threads
            self._running = True
            thread = threading.Thread(
                target=self._poll_loop,
                args=(queue_name,),
                name=f"event-mesh-consumer-{queue_name}",
                daemon=True,
            )
            self._consumer_threads[queue_name] = thread
            thread.start()

        if first:
            # SA-1470: the REST poll loop is an alertable state, not just a log
            # line — 1.0 on the transport gauge (see runbook). Emitted once for
            # the consumer, not once per queue: it reports which transport is
            # in use, and that answer does not change as queues are added.
            self._metrics.record_consume_transport("rest")
        logger.info("Event Mesh consumer loop started (REST poll, queue=%s)", queue_name)

    def _queue_is_free(self, queue_name: str) -> bool:
        """G1 — refuse to attach AMQP to a queue that already has consumers.

        The queues are ``NON_EXCLUSIVE``, so the broker round-robins across
        everything bound to them. Attaching next to a consumer that isn't
        actively fetching means whatever round-robin hands to that consumer is
        stranded — measured on a scratch queue: 20 published, AMQP received 17,
        3 stuck ``unacked`` forever. A run only needs its ``StartRunAction``
        stranded to hang, which is what the three tenant-1 outages were.

        The policy is **refuse only on positive knowledge**: anything that is
        not a definite count of bound consumers — an unreadable management
        response, a missing field — is treated as free. A management-API blip
        must not stop consumption altogether; the guard exists to catch the
        *known* failure, not to invent a new one.
        """
        count = self._admin.consumer_count(queue_name)
        if not isinstance(count, int):
            logger.warning(
                "Could not read consumerCount for %s (got %r) — attaching AMQP "
                "anyway; an unreadable count must not stop consumption",
                queue_name, count,
            )
            return True
        if count == 0:
            self._refusal_logged.discard(queue_name)
            return True
        # Level and repetition both matter here. The caller retries every
        # ~30s for as long as a peer holds the queue, so logging this at
        # ERROR every pass produced 10 ERROR lines a minute on tenant-1 for a
        # condition that is HEALTHY at 2 instances — the fastest way to teach
        # an operator to ignore the log. First occurrence is worth seeing;
        # after that ``_stand_down`` has already stated the decision and its
        # consequence, so repeats drop to DEBUG.
        first = queue_name not in self._refusal_logged
        self._refusal_logged.add(queue_name)
        (logger.info if first else logger.debug)(
            "Not attaching AMQP to %s: %d consumer(s) already bound. The queue "
            "is NON_EXCLUSIVE, so the broker would round-robin between them and "
            "this link, and anything handed to a consumer that is no longer "
            "fetching is stranded (measured: 3 of 20 lost, permanently unacked). "
            "This topic is stood down here — NOT REST-polled: polling the "
            "retired queue would re-create its subscription and duplicate the "
            "topic (measured on tenant-1 2026-08-11, five topics).",
            queue_name, count,
        )
        self._metrics.record_consume_fallback("queue_has_consumers")
        return False

    def _start_amqp_consumer(self, queue_name: str) -> str:
        """Attach *queue_name* to the AMQP push consumer.

        Returns ``ATTACH_OK``, or the reason it could not attach. The caller
        needs the reason, not a bool: a peer already consuming the queue is a
        different situation from a broken endpoint, and only one of them is a
        fault. The first call builds the consumer and probes connectivity once.

        Process-level commitment to REST happens ONLY when
        ``runtime_fallback`` is on. Without it there is nothing to commit to,
        and leaving the state undecided is what lets ``retry_stood_down``
        re-probe when the condition clears.
        """
        with self._amqp_lock:
            if self._amqp_state == "rest":
                return self._amqp_fallback_reason or ATTACH_UNREACHABLE
            if self._amqp_state == "amqp" and self._amqp_consumer is not None:
                if not self._queue_is_free(queue_name):
                    return ATTACH_PEER
                self._amqp_consumer.add_queue(queue_name)
                return ATTACH_OK

            from app.layer4_frameworks.messaging.event_mesh_amqp_consumer import (
                EventMeshAMQPConsumer,
                amqp_available,
                amqp_import_error,
            )

            if not amqp_available():
                logger.warning(
                    "EVENT_MESH_CONSUME_TRANSPORT=amqp but proton is not "
                    "importable (%s) — this pod cannot consume over AMQP",
                    amqp_import_error(),
                )
                self._metrics.record_consume_fallback("proton_missing")
                self._commit_to_rest(ATTACH_NO_LIBS)
                return ATTACH_NO_LIBS

            # G1 runs BEFORE probe(), not just before add_queue: the probe
            # attaches a real receiver link and grants credit, so a queue with
            # other consumers can strand messages during the probe itself.
            if not self._queue_is_free(queue_name):
                return ATTACH_PEER

            consumer = EventMeshAMQPConsumer(
                host=self._amqp_host,
                port=self._amqp_port,
                ws_path=self._amqp_ws_path,
                get_token=self._tokens.amqp_token,
                message_handler=self._dispatch_to_callbacks,
                prefetch=self._amqp_prefetch,
                concurrency=self._amqp_concurrency,
                strip_namespace=self._amqp_strip_namespace,
                namespace=self._names.namespace,
                # G2: lets the watchdog tell "nothing to deliver" apart from
                # "attached but not delivering" — only the second is a stall.
                queue_depth=self._admin.queue_depth,
                metrics=self._metrics,
            )
            if not consumer.probe(queue_name):
                logger.warning(
                    "Event Mesh AMQP unreachable/unusable (host=%s)", self._amqp_host,
                )
                self._metrics.record_consume_fallback("amqp_unreachable")
                self._commit_to_rest(ATTACH_UNREACHABLE)
                return ATTACH_UNREACHABLE

            self._amqp_consumer = consumer
            self._amqp_state = "amqp"
            self._metrics.record_consume_transport("amqp")
            consumer.add_queue(queue_name)
            consumer.start()
            logger.info(
                "Event Mesh consume transport: AMQP push (host=%s) — first queue %s",
                self._amqp_host, queue_name,
            )
            return ATTACH_OK

    def _commit_to_rest(self, reason: str) -> None:
        """Pin the process to REST — only meaningful with the escape hatch on.

        With ``runtime_fallback`` off there is no REST path to pin to, and
        pinning would permanently short-circuit ``_start_amqp_consumer`` so a
        recovered endpoint could never be picked up. Caller holds ``_amqp_lock``.
        """
        self._amqp_fallback_reason = reason
        if self._runtime_fallback:
            self._amqp_state = "rest"

    # ------------------------------------------------------------------
    # Stand-down + retry (instead of improvising a transport)
    # ------------------------------------------------------------------

    def _stand_down(
        self, topic: str, amqp_queue: str, callback: Callable, reason: str,
    ) -> None:
        """Record that this pod is not consuming *topic*, and why.

        Loudness is graded by whose fault it is. A peer holding the queue is
        the system working as designed while the control plane is pinned to
        one consumer per queue — it is not an error and must not fail
        readiness, or a scaled deployment would report every pod but one as
        broken. The other two mean this pod cannot do its job.
        """
        with self._standdown_lock:
            self._stood_down[topic] = (amqp_queue, callback, reason)
        self._metrics.record_consume_standdown(amqp_queue, reason)

        if reason == ATTACH_PEER:
            logger.info(
                "Not consuming %s here: another instance holds %s. Standing "
                "down (the work is not lost — the peer has it) and re-checking "
                "every %.0fs in case it goes away.",
                topic, amqp_queue, self._standdown_retry_seconds,
            )
        elif reason == ATTACH_NO_LIBS:
            logger.error(
                "Not consuming %s: the AMQP libraries are missing from this "
                "image, which no retry can fix. Roll this deploy back or set "
                "EVENT_MESH_CONSUME_TRANSPORT=rest deliberately.", topic,
            )
        else:
            logger.error(
                "Not consuming %s: the AMQP endpoint is unreachable. Retrying "
                "every %.0fs. Nothing is being REST-polled — that would "
                "re-subscribe the retired queue and duplicate the topic.",
                topic, self._standdown_retry_seconds,
            )

        if reason in _RETRYABLE_ATTACH:
            self._start_standdown_retry()

    def stood_down(self) -> dict[str, str]:
        """``{topic: reason}`` for every topic this pod is not consuming."""
        with self._standdown_lock:
            return {t: reason for t, (_q, _cb, reason) in self._stood_down.items()}

    def retry_stood_down(self) -> list[str]:
        """Re-attempt every retryable stood-down topic. Returns those recovered.

        Public and side-effect-complete so it can be driven directly by a test
        rather than by waiting on the timer — the same shape as the AMQP
        consumer's ``check_liveness``.
        """
        with self._standdown_lock:
            pending = [
                (topic, queue, cb, reason)
                for topic, (queue, cb, reason) in self._stood_down.items()
                if reason in _RETRYABLE_ATTACH
            ]
        recovered: list[str] = []
        for topic, amqp_queue, callback, _reason in pending:
            # Register BEFORE attaching, exactly as ``subscribe`` does: the
            # broker can deliver before ``_start_amqp_consumer`` returns, and a
            # message landing on an empty handler list is acked and destroyed.
            self._register(amqp_queue, callback)
            try:
                outcome = self._start_amqp_consumer(amqp_queue)
            except Exception:
                logger.exception("AMQP re-attach failed (topic=%s)", topic)
                self._unregister(amqp_queue, callback)
                continue
            if outcome != ATTACH_OK:
                self._unregister(amqp_queue, callback)
                continue
            with self._standdown_lock:
                self._stood_down.pop(topic, None)
            recovered.append(topic)
            logger.info(
                "Recovered %s: attached AMQP to %s after standing down",
                topic, amqp_queue,
            )
            self._start_dmq_watch()
        return recovered

    def _start_standdown_retry(self) -> None:
        with self._thread_lock:
            if self._standdown_thread is not None:
                return
            self._standdown_stop.clear()
            thread = threading.Thread(
                target=self._standdown_loop,
                name="em-standdown-retry",
                daemon=True,
            )
            self._standdown_thread = thread
        thread.start()

    def _standdown_loop(self) -> None:
        while not self._standdown_stop.wait(self._standdown_retry_seconds):
            try:
                self.retry_stood_down()
            except Exception:
                # Recovery must never be the thing that kills the process.
                logger.exception("Stand-down retry pass failed")

    # ------------------------------------------------------------------
    # REST poll loop + dispatch
    # ------------------------------------------------------------------

    def _cooldown_key(self, queue_name: str) -> str:
        return queue_name if self._cooldown_scope == "queue" else ""

    def _cooldown_remaining(self, queue_name: str) -> float:
        with self._cooldown_lock:
            entry = self._cooldowns.get(self._cooldown_key(queue_name))
        return max(0.0, entry[0] - time.monotonic()) if entry else 0.0

    def _clear_cooldown(self, queue_name: str) -> None:
        """First non-429 response resets that scope's streak + deadline."""
        with self._cooldown_lock:
            self._cooldowns.pop(self._cooldown_key(queue_name), None)

    def _poll_loop(self, queue_name: str) -> None:
        """Background thread: poll ONE queue and dispatch its messages.

        SA-2319 — one thread per queue. This used to be a single thread that
        round-robined every subscribed queue, consuming one message per queue
        per pass and calling ``_dispatch_to_callbacks`` INLINE. Because that
        dispatch blocks until the handler returns, and ``WorkerResultsConsumer``
        registers a sync callback that waits on the whole of ``complete_task``,
        one task completion stopped every other queue from being polled at all:

            poll thread   [results POST][==== complete_task, 200-400ms ====][actions POST]
            actions queue               ....ContinueRunAction sits unread....
            workers 0-3                 ..........idle, nothing to dequeue........

        The four ``OrchestrationWorker`` coroutines starved behind the very
        message that was meant to feed them, so the pod's throughput ceiling was
        1/latency(complete_task) no matter how ORCHESTRATION_WORKER_COUNT was
        set. realtime.events, workflow.triggers, catalog.changed and the
        per-instance response topic all waited too, on a completion they have
        nothing to do with.

        Per-queue threads remove exactly that coupling and nothing else. The
        consume -> dispatch -> ack sequence within a queue is untouched, so
        at-least-once is unchanged: a handler that fails still skips the ack and
        the broker still redelivers after the lock TTL. Messages on ONE queue
        still serialise against each other — lifting that is a separate change
        with real delivery-semantics consequences, deliberately not made here.

        The AMQP consumer has always worked this way (``_spawn_queue_threads``);
        the REST fallback simply never got the same treatment.

        Idle REST cost barely moves, which is worth stating because the obvious
        fear is an Nx increase. The old loop slept ONCE PER PASS over all N
        queues, not once per queue: with N=6, poll_interval=0.5s and a ~16ms
        round trip, a pass was 6x0.016+0.5 = 0.596s carrying 6 POSTs = 10.1/s.
        Per-queue it is 0.516s carrying 1 POST per thread = 11.6/s. About 15%
        more, not 6x.

        429 cooldowns keep their scope semantics (SA-1470): under the default
        "instance" scope the cooldown map is shared, so a 429 on any queue still
        pauses all of them — correct, because the broker's REST limit is per
        service instance. Under "queue" scope only this queue pauses.
        """
        try:
            while self._running:
                # This thread serves EXACTLY ONE queue — the whole point of the
                # ticket. The body used to keep the old round-robin scaffolding:
                # it read every queue out of ``_callbacks`` and ran
                # ``for queue_name in queues``, which SHADOWED the parameter. So
                # every per-queue thread drained every queue, the blocking
                # ``complete_task`` handler stalled all of them exactly as
                # before, and the trailing consume/dispatch/ack block was a
                # second copy of ``_drain_queue`` aimed at whichever queue the
                # shadowed loop variable happened to be left holding.
                if self._cooldown_remaining(queue_name) > 0:
                    # A 429 is in effect for this queue (SA-1470: "instance"
                    # scope shares the map, so all queues pause; "queue" scope
                    # pauses only this one). Short slice, so shutdown stays
                    # responsive instead of waiting out the cooldown.
                    time.sleep(min(self._poll_interval_s, 1.0))
                    continue

                # Owns consume + dispatch + ack for up to ``_batch_max``
                # messages, including their error handling, and never raises.
                # A hot queue loops straight round without paying the poll
                # interval; an empty one backs off.
                if not self._drain_queue(queue_name):
                    time.sleep(self._poll_interval_s)
        except Exception:
            logger.exception(
                "Event Mesh consumer loop crashed (queue=%s)", queue_name,
            )

    def _drain_queue(self, queue_name: str) -> int:
        """Consume up to ``_batch_max`` messages from *queue_name*.

        The loop used to take exactly ONE message per queue per pass, so a
        burst arriving together was spread across successive passes — each
        message paying a poll interval before the next was even fetched.
        Measured on tenant-1 (2026-08-05): the engine correctly enqueues one
        ``ExecuteNodeAction`` per ready task, but two sibling actions
        published milliseconds apart were dispatched **13.5 s and 15.8 s**
        apart, because the second waited for a later pass.

        Draining changes throughput, not semantics. Messages are still
        handled **one at a time, in the order the broker returned them**, and
        each is still acked individually only after its handler succeeded —
        so per-queue FIFO and the at-least-once contract are unchanged, and
        no re-ordering is introduced. The win is that a hot queue no longer
        pays ``poll_interval`` per message.

        Stops early on: an empty queue, a poll error, a handler failure (do
        not pile onto a failing consumer — the unacked message redelivers),
        or a cooldown armed mid-drain by a 429.

        Returns the number of messages handled.
        """
        handled = 0
        for _ in range(max(1, self._batch_max)):
            if self._cooldown_remaining(queue_name) > 0:
                break  # a 429 landed mid-drain
            try:
                result = self.consume_from_queue(queue_name)
            except Exception:
                logger.exception("Poll error on queue %s", queue_name)
                break

            if result is None:
                break  # queue drained
            handled += 1

            all_ok = self._dispatch_to_callbacks(queue_name, result["data"])

            # Ack only on full handler success. On failure, skip ack
            # so Event Mesh redelivers after the message's lock TTL.
            msg_id = result.get("message_id")
            if msg_id and all_ok:
                try:
                    self.acknowledge(queue_name, msg_id)
                except Exception:
                    logger.exception("Ack error on queue %s", queue_name)
            elif msg_id and not all_ok:
                logger.warning(
                    "Skipping ack for queue %s — handler failure; "
                    "message will redeliver after lock TTL",
                    queue_name,
                )
            if not all_ok:
                break
        return handled

    def transport_state(self) -> dict:
        """Observability snapshot for health/readiness (SA-1470)."""
        now = time.monotonic()
        with self._cooldown_lock:
            active_cooldowns = {
                (key or "<instance>"): round(deadline - now, 1)
                for key, (deadline, _streak) in self._cooldowns.items()
                if deadline > now
            }
        if self._amqp_state == "amqp":
            active: str | None = "amqp"
        elif self._consumer_threads or self._amqp_state == "rest":
            active = "rest"
        else:
            active = None
        return {
            "configured": self._consume_transport,
            "active": active,
            "rest_fallback": self._consume_transport == "amqp" and active == "rest",
            "cooldown_scope": self._cooldown_scope,
            "active_cooldowns": active_cooldowns,
            # Deliberately NOT folded into ``rest_fallback``: standing down is
            # not a fallback, and the two send an operator to different
            # procedures. A peer-owned queue is healthy; an unreachable
            # endpoint is not.
            "stood_down": self.stood_down(),
        }

    def _dispatch_to_callbacks(self, queue_name: str, data: Any) -> bool:
        """Run every registered callback for *queue_name* against *data*.

        Returns ``True`` only if all handlers succeed (drives the
        ack/settle decision — at-least-once). Async handlers are dispatched
        onto the captured main event loop and awaited so the outcome is known
        before settling. Shared by the REST poll loop and the AMQP consumer so
        both transports honour identical dispatch + at-least-once semantics.
        """
        with self._sub_lock:
            handlers = list(self._callbacks.get(queue_name, []))
            known = list(self._callbacks.keys())

        if not handlers:
            # Do NOT ack. There are two ways to get here and redelivery is the
            # right answer to both:
            #
            #   * a race — the transport attached and the broker delivered
            #     before `subscribe` had registered the callback. Measured on
            #     tenant-1 2026-08-05: six `orchestration.actions` messages
            #     destroyed on one startup, each potentially a StartRunAction
            #     and therefore a permanently hung run. (The ordering is fixed
            #     in `subscribe`; this is the belt to that braces.)
            #   * a key mismatch between the name a subscription registered
            #     under and the name the transport dispatches with.
            #
            # Leaving the message unsettled lets the 60s lock expire so the
            # broker redelivers it — by which time a racing handler exists. A
            # genuine mismatch exhausts maxRedeliveryCount and lands in the
            # DMQ, which is alertable and recoverable. Acking destroyed it.
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
                    if self._main_loop is not None and not self._main_loop.is_closed():
                        fut = asyncio.run_coroutine_threadsafe(
                            handler(data), self._main_loop
                        )
                        fut.result(timeout=self._handler_timeout_s)
                    else:
                        logger.warning(
                            "No main event loop — dropping async handler for queue %s",
                            queue_name,
                        )
                        all_ok = False
                else:
                    handler(data)
            except Exception:
                logger.exception("Error in callback for queue %s", queue_name)
                all_ok = False
        return all_ok

    # ------------------------------------------------------------------
    # Queue consume / acknowledge
    # ------------------------------------------------------------------

    def _note_consume_rate_limited(self, queue_name: str, resp: httpx.Response) -> None:
        """Record a 429 on the consumption endpoint and start a cooldown.

        Scope-aware (SA-1470): "instance" pauses every queue (SAP Event
        Mesh's REST limit is per service instance); "queue" pauses only the
        throttled queue. Honors ``Retry-After`` when the broker sends it,
        else backs off exponentially per consecutive-429 streak up to the
        cap — every pause is time-bounded and now emits an alertable metric.
        """
        retry_after = 0.0
        try:
            retry_after = float(resp.headers.get("Retry-After", "") or 0)
        except ValueError:
            retry_after = 0.0
        key = self._cooldown_key(queue_name)
        with self._cooldown_lock:
            _deadline, streak = self._cooldowns.get(key, (0.0, 0))
            if retry_after <= 0:
                retry_after = min(
                    _RATE_LIMIT_MAX_BACKOFF_S,
                    _RATE_LIMIT_BASE_BACKOFF_S * (2 ** streak),
                )
            streak += 1
            self._cooldowns[key] = (time.monotonic() + retry_after, streak)
        self._metrics.record_consume_rate_limited(
            queue_name, retry_after, streak, self._cooldown_scope,
        )
        logger.warning(
            "Event Mesh rate limited (429) on queue %s — pausing %s "
            "consumption for %.1fs (streak=%d)",
            queue_name,
            "ALL queue" if self._cooldown_scope == "instance" else "this queue's",
            retry_after, streak,
        )

    def consume_from_queue(self, queue_name: str) -> Optional[dict]:
        """Consume one message from a queue.

        Returns ``{"data": <parsed>, "message_id": <id>}`` or ``None``.
        """
        qualified = self._names.qualify_queue(queue_name)
        encoded = self._names.encode_segment(qualified)
        url = f"{self._messaging_url}/messagingrest/v1/queues/{encoded}/messages/consumption"
        # ``x-lock-duration-in-ms`` extends how long Event Mesh holds the
        # message before redelivering. Without this, the broker default
        # (varies by tenant) can be short enough that slow downstream
        # handlers (push gateway, socket.io emit) trigger redelivery and
        # the consumer sees events out of order. See
        # ``socketio_consumer._flush_gaps`` — late events get force-emitted
        # past the reorder window when this happens.
        headers = {
            **self._tokens.auth_headers(),
            "x-qos": "1",
            "x-lock-duration-in-ms": str(self._lock_duration_ms),
        }

        try:
            resp = self._http.post(url, headers=headers)
            if resp.status_code == 204:
                self._clear_cooldown(queue_name)
                return None  # no message available
            if resp.status_code == 429:
                self._note_consume_rate_limited(queue_name, resp)
                return None  # message stays on the queue; retried after cooldown
            resp.raise_for_status()
            self._clear_cooldown(queue_name)
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
        qualified = self._names.qualify_queue(queue_name)
        encoded_queue = self._names.encode_segment(qualified)
        encoded_id = quote(message_id, safe="")
        url = (
            f"{self._messaging_url}/messagingrest/v1/queues/{encoded_queue}"
            f"/messages/{encoded_id}/acknowledgement"
        )
        headers = self._tokens.auth_headers()

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
    # close
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Stop the AMQP consumer / REST poll thread (does not close HTTP)."""
        self._running = False
        self._dmq_stop.set()
        if self._dmq_thread is not None:
            self._dmq_thread.join(timeout=5)
            self._dmq_thread = None

        self._standdown_stop.set()
        with self._thread_lock:
            standdown = self._standdown_thread
            self._standdown_thread = None
        if standdown is not None:
            standdown.join(timeout=5)

        if self._amqp_consumer is not None:
            try:
                self._amqp_consumer.stop()
            except Exception:
                logger.exception("Error stopping Event Mesh AMQP consumer")
            self._amqp_consumer = None

        with self._thread_lock:
            threads = list(self._consumer_threads.values())
            self._consumer_threads = {}
        # The 10s budget is per thread, matching the previous single-thread
        # behaviour. Threads are daemons and ``_running`` is already False, so a
        # thread still inside a long handler cannot block shutdown indefinitely.
        for thread in threads:
            thread.join(timeout=10)
