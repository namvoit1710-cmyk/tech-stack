"""Async publish pump for SAP Event Mesh.

Owns the non-blocking publish path: a bounded in-memory send buffer, a
background flusher that POSTs to Event Mesh per destination — sequential within
a destination, concurrent across them, exponential-backoff retry, a circuit breaker, and a dead-letter queue
for terminally-failed messages. ``publish_topic`` / ``publish_queue`` only
validate + enqueue; everything else happens off the caller's stack once
``start`` has run.

Distinct from ``EventMeshEventPublisher`` (the domain ``IEventPublisher``
adapter) — this is the broker-internal send machinery.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.layer4_frameworks.messaging.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    STATE_CLOSED,
)
from app.layer4_frameworks.messaging.event_mesh_dlq_spool import DlqSpool
from app.layer4_frameworks.messaging.event_mesh_errors import (
    EventMeshBufferFull,
    EventMeshPayloadTooLarge,
    EventMeshPublishFailed,
)
from app.layer4_frameworks.messaging.event_mesh_http import EventMeshHttpClients
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames
from app.layer4_frameworks.messaging.event_mesh_token_provider import (
    EventMeshTokenProvider,
)

logger = logging.getLogger(__name__)

# Retryable HTTP status codes (transient server issues / rate limiting)
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

# Internal retry/backoff + flush tuning (not user-configurable).
_DEFAULT_RETRY_BASE_DELAY_S = 0.5
_DEFAULT_RETRY_JITTER_RATIO = 0.2
_DEFAULT_FLUSH_POLL_TIMEOUT_S = 1.0
_DEFAULT_KEEPALIVE_EXPIRY_S = 30.0
_DLQ_QUEUE_NAME = "publisher.dlq"


@dataclass(frozen=True)
class _SendItem:
    """One buffered publish request awaiting dispatch by the async flusher."""
    kind: str  # "topic" or "queue"
    name: str  # topic or queue name (unqualified)
    payload: bytes
    key: Optional[str] = None
    # Monotonic timestamp captured at construction (i.e. immediately before
    # enqueue). The flusher sorts each drained batch by this so retries and
    # HTTP latency variance can't reorder messages on the wire.
    created_at: float = field(default_factory=time.monotonic)


class EventMeshPublisher:
    """Bounded send buffer + flusher + circuit breaker + DLQ."""

    def __init__(
        self,
        *,
        messaging_url: str,
        names: EventMeshNames,
        tokens: EventMeshTokenProvider,
        clients: EventMeshHttpClients,
        metrics: EventMeshMetrics,
        timeout: float,
        max_retries: int,
        max_payload_bytes: int,
        buffer_size: int,
        buffer_policy: str,
        flush_batch_size: int,
        flush_concurrency: int,
        max_connections: int,
        max_keepalive_connections: int,
        enable_dlq: bool,
        circuit_failure_threshold: int,
        circuit_recovery_timeout_s: float,
        dlq_spool_dir: str = "",
        dlq_best_effort_topics: str = "realtime.events",
        keepalive_expiry_s: float = _DEFAULT_KEEPALIVE_EXPIRY_S,
    ) -> None:
        self._messaging_url = messaging_url
        self._names = names
        self._tokens = tokens
        self._clients = clients
        self._metrics = metrics
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_payload_bytes = max_payload_bytes
        self._buffer_size = buffer_size
        self._buffer_policy = buffer_policy
        self._flush_batch_size = flush_batch_size
        self._flush_concurrency = flush_concurrency
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections
        self._enable_dlq = enable_dlq
        # Was a hardcoded 30 s. A cold connection costs 19.4 ms against a
        # 6.0 ms warm request, and grouping is the first thing that ever
        # asks this pool for more than one connection — so an idle gap
        # longer than the expiry now costs several handshakes, not one.
        self._keepalive_expiry_s = keepalive_expiry_s

        # SA-1471 — local durable spool + durability classification. The
        # spool parks durable envelopes the DLQ path can't take; best-effort
        # topics (realtime UI ticks) keep the historical counted-drop.
        self._spool = DlqSpool(dlq_spool_dir)
        self._best_effort_topics = frozenset(
            t.strip() for t in (dlq_best_effort_topics or "").split(",") if t.strip()
        )

        self._send_queue: asyncio.Queue | None = None
        self._replay_task: asyncio.Task | None = None
        self._flusher_task: asyncio.Task | None = None
        self._token_refresh_task: asyncio.Task | None = None
        self._running_async = False
        self._breaker = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            recovery_timeout_s=circuit_recovery_timeout_s,
            name="eventmesh-publish",
            on_state_change=self._on_circuit_state_change,
        )

    # ------------------------------------------------------------------
    # Public publish entry points (validate + enqueue; async-only)
    # ------------------------------------------------------------------

    def publish_topic(self, topic: str, message: Any, key: Optional[str] = None) -> None:
        payload = self._encode_and_validate(message, topic)
        if self._send_queue is None or not self._running_async:
            raise RuntimeError(
                "Event Mesh publish_to_topic() called before start_async(); "
                "this client publishes only via the async flusher"
            )
        self._enqueue(_SendItem(kind="topic", name=topic, payload=payload, key=key))

    def publish_queue(self, queue_name: str, message: Any) -> None:
        metrics_key = f"queue:{queue_name}"
        payload = self._encode_and_validate(message, metrics_key)
        if self._send_queue is None or not self._running_async:
            raise RuntimeError(
                "Event Mesh publish_to_queue() called before start_async(); "
                "this client publishes only via the async flusher"
            )
        self._enqueue(_SendItem(kind="queue", name=queue_name, payload=payload))

    def _encode_and_validate(self, message: Any, topic: str) -> bytes:
        """Serialize *message* as JSON bytes and enforce the size limit.

        Raises ``EventMeshPayloadTooLarge`` if the encoded payload exceeds
        ``max_payload_bytes``. This stops oversized events from reaching
        Event Mesh (which would return a confusing 413).
        """
        payload = json.dumps(message).encode("utf-8")
        if len(payload) > self._max_payload_bytes:
            self._metrics.record_dropped("oversize", topic)
            logger.error(
                "Event Mesh payload too large: %d bytes > %d (topic=%s) — dropping",
                len(payload), self._max_payload_bytes, topic,
            )
            raise EventMeshPayloadTooLarge(len(payload), self._max_payload_bytes)
        return payload

    def _enqueue(self, item: _SendItem) -> None:
        """Try to add *item* to the send buffer; apply backpressure policy on full.

        SA-1469: a full buffer no longer silently discards DURABLE domain
        events (run.status / node.* / audit — everything not in the
        best-effort list). They overflow into the SA-1471 local spool and are
        re-enqueued by the flusher as the buffer drains, preserving the
        original order. Best-effort realtime topics keep the counted drop;
        ``buffer_policy="raise"`` keeps its contract.
        """
        assert self._send_queue is not None
        try:
            self._send_queue.put_nowait(item)
            self._metrics.record_buffer_depth(self._send_queue.qsize())
        except asyncio.QueueFull:
            self._metrics.record_buffer_full(item.name)
            if self._buffer_policy == "raise":
                self._metrics.record_dropped("buffer_full", item.name)
                raise EventMeshBufferFull(
                    f"Event Mesh send buffer full (size={self._buffer_size})"
                )
            if self._is_durable(item):
                # Overflow to the durable spool instead of dropping; the
                # flush loop replays it once the buffer has capacity again.
                self._spool_or_lose(item, error="buffer_full", reason="buffer_full")
                return
            self._metrics.record_dropped("buffer_full", item.name)
            logger.error(
                "Event Mesh send buffer full — dropping best-effort message "
                "(topic=%s)", item.name,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the send-buffer flusher and proactive token refresher.

        Must be called from within a running asyncio event loop. Idempotent.
        """
        if self._running_async:
            return
        self._send_queue = asyncio.Queue(maxsize=self._buffer_size)
        self._tokens.prepare_async()
        self._clients.async_ = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_keepalive_connections,
                keepalive_expiry=self._keepalive_expiry_s,
            ),
        )
        self._running_async = True
        self._flusher_task = asyncio.create_task(
            self._flush_loop(), name="eventmesh-flusher",
        )
        self._token_refresh_task = asyncio.create_task(
            self._tokens.run_refresh_loop(lambda: self._running_async),
            name="eventmesh-token-refresh",
        )
        logger.info(
            "Event Mesh async flusher started (buffer=%d, batch=%d, concurrency=%d)",
            self._buffer_size, self._flush_batch_size, self._flush_concurrency,
        )
        # SA-1471: replay envelopes parked by a previous run (deploy/restart
        # during an outage) as soon as the pump is up.
        if self._spool.depth() > 0:
            logger.info(
                "DLQ spool holds %d envelope(s) from a previous run — replaying",
                self._spool.depth(),
            )
            self._schedule_spool_replay("startup")

    async def stop(self, drain_timeout_s: float = 10.0) -> None:
        """Stop the flusher, draining in-flight messages up to the timeout."""
        if not self._running_async:
            return
        self._running_async = False

        assert self._send_queue is not None
        try:
            await asyncio.wait_for(self._send_queue.join(), timeout=drain_timeout_s)
        except asyncio.TimeoutError:
            remaining = self._send_queue.qsize()
            logger.error(
                "Event Mesh drain timeout after %.1fs — %d messages lost",
                drain_timeout_s, remaining,
            )
            self._metrics.record_dropped("shutdown_timeout")

        for task in (self._flusher_task, self._token_refresh_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._flusher_task = None
        self._token_refresh_task = None

        if self._clients.async_ is not None:
            try:
                await self._clients.async_.aclose()
            except Exception:
                logger.exception("Error closing async HTTP client")
            self._clients.async_ = None

        logger.info("Event Mesh async flusher stopped")

    def close(self) -> None:
        """Best-effort sync close of the async client if stop() wasn't awaited."""
        if self._clients.async_ is None:
            return
        try:
            # No running loop required for aclose() — skip if none available.
            loop = None
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                pass
            if loop is not None and not loop.is_closed() and not loop.is_running():
                loop.run_until_complete(self._clients.async_.aclose())
        except Exception:
            logger.exception("Error closing async HTTP client from close()")
        finally:
            self._clients.async_ = None

    # ------------------------------------------------------------------
    # Flush loop
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Drain items from the send queue and dispatch them via async HTTP.

        Items are sorted by ``created_at`` and dispatched **sequentially** so
        publish order on the wire matches enqueue order. Why: previously the
        batch was fanned out via ``asyncio.gather`` with ``flush_concurrency``
        workers, so retry backoff or HTTP latency variance could make a later
        item (e.g. ``run.completed``) reach Event Mesh before an earlier one
        (e.g. ``run.started``) for the same run_id. This trades throughput
        for ordering — switch to per-topic groups if that becomes a problem.
        """
        sem = asyncio.Semaphore(self._flush_concurrency)
        try:
            # Keep running until stopped AND buffer is drained
            while self._running_async or (
                self._send_queue is not None and not self._send_queue.empty()
            ):
                batch = await self._drain_batch()
                if not batch:
                    # Idle tick: if buffer-full overflow parked envelopes in
                    # the spool, pull them back now that there is capacity
                    # (SA-1469). Guarded single-flight in the scheduler.
                    if (
                        self._spool.depth() > 0
                        and self._send_queue.qsize() < self._buffer_size // 2
                    ):
                        self._schedule_spool_replay("buffer_drained")
                    continue
                batch.sort(key=lambda item: item.created_at)
                await self._flush_batch(batch)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Event Mesh flush loop crashed — restart required")

    async def _drain_batch(self) -> list[_SendItem]:
        """Wait up to ``_DEFAULT_FLUSH_POLL_TIMEOUT_S`` for first item, then
        drain up to ``flush_batch_size`` additional items without waiting.
        """
        assert self._send_queue is not None
        batch: list[_SendItem] = []
        try:
            first = await asyncio.wait_for(
                self._send_queue.get(), timeout=_DEFAULT_FLUSH_POLL_TIMEOUT_S,
            )
            batch.append(first)
        except asyncio.TimeoutError:
            return batch
        while len(batch) < self._flush_batch_size:
            try:
                batch.append(self._send_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush_batch(self, batch: list[_SendItem]) -> None:
        """Dispatch one drained batch: sequential per destination, concurrent
        across destinations.

        The old behaviour sent the whole batch one message at a time, which
        bought ordering **across** destinations. No consumer can observe that:
        every consumer reads exactly one topic, and each topic is its own queue
        on its own AMQP link, so cross-topic delivery order is undefined however
        it was published. What it cost was measurable — on tenant-1 a node
        publishes 19 messages across 9 destinations at ~16 ms each, and the
        message that advances the run is enqueued last, so it waited behind the
        FE-facing telemetry (0.185 s, against 0.065 s for the same hop with one
        message ahead of it).

        Per-destination order is preserved exactly: sequential inside a group,
        and the caller awaits this whole batch before draining the next.
        """
        groups: dict[tuple[str, str], list[_SendItem]] = {}
        for item in batch:
            # (kind, name), not name: a topic and a queue may share a name and
            # are different destinations — ``_flush_one`` builds ``/topics/…``
            # vs ``/queues/…``.
            groups.setdefault((item.kind, item.name), []).append(item)

        # Shortest-job-first. NOT a priority policy: it names no topic and needs
        # no classification when someone adds one. It releases the small
        # run-advancing group ahead of the large telemetry group purely as a
        # consequence of size — in a node cycle that group has the *latest*
        # first item, so insertion order would dispatch it last.
        ordered = sorted(groups.values(), key=len)
        largest = len(ordered[-1])

        # DERIVED, not configured. Makespan is ``max(largest, total / width)``,
        # so width past ``ceil(total / largest)`` buys nothing — no concurrency
        # splits a group. A hardcoded width would be silently wrong the first
        # time an event type is added; this follows the distribution instead.
        # ``_flush_concurrency`` is therefore a CEILING on connections held, a
        # resource decision, not a latency knob.
        width = max(1, min(-(-len(batch) // largest), self._flush_concurrency))
        self._metrics.record_flush_shape(
            groups=len(ordered), largest=largest, width=width, batch=len(batch),
        )

        # ``asyncio.Semaphore`` is FIFO-fair and ``gather`` schedules in
        # argument order, so acquisition order IS ``ordered``. That is what
        # makes shortest-first real rather than decorative — gathering an
        # unordered collection here would quietly undo it.
        sem = asyncio.Semaphore(width)
        await asyncio.gather(*(self._flush_group(items, sem) for items in ordered))

    async def _flush_group(
        self, items: list[_SendItem], sem: asyncio.Semaphore,
    ) -> None:
        """One destination's slice, in order, holding ONE slot for the slice.

        The slot is per group rather than per item deliberately. Items here must
        run back to back anyway, so per-item acquisition buys nothing and costs
        two things: a group can be starved halfway through, and its warm pooled
        connection can be handed to another destination mid-slice. Holding it
        also means retry backoff (0.5 → 1.0 → 2.0 s) blocks only its own
        destination, which is correct — that destination's next message could
        not have gone first regardless.

        This must never propagate. Every item owes the send queue exactly one
        ``task_done()`` and ``stop()`` blocks on ``join()``, so an exception
        escaping mid-slice strands the tail's accounting and hangs shutdown —
        silently, and only at shutdown. ``_flush_one_tracked`` already routes
        publish failures to the DLQ, so anything reaching here is a bug: log it
        and keep the slice moving. On ``CancelledError`` the remainder is
        abandoned on purpose; that path is shutdown and ``stop()``'s drain
        timeout covers it.
        """
        async with sem:
            for item in items:
                try:
                    await self._flush_one_tracked(item)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Unexpected error dispatching %s=%s — continuing the "
                        "group so its remaining items are still accounted for",
                        item.kind, item.name,
                    )

    async def _flush_one_tracked(self, item: _SendItem) -> None:
        assert self._send_queue is not None
        try:
            # Circuit breaker gate: fail fast when OPEN
            try:
                await self._breaker.before_call()
            except CircuitBreakerOpen:
                self._metrics.record_dropped("circuit_open", item.name)
                logger.warning(
                    "Circuit OPEN — sending %s=%s straight to DLQ",
                    item.kind, item.name,
                )
                await self._dlq_send(item, error="circuit_open")
                return
            try:
                await self._flush_one(item)
                await self._breaker.on_success()
            except EventMeshPublishFailed as exc:
                await self._breaker.on_failure()
                logger.error(
                    "Event Mesh flush failed (%s=%s): %s",
                    item.kind, item.name, exc,
                )
                await self._dlq_send(item, error=str(exc))
            except Exception as exc:
                await self._breaker.on_failure()
                logger.exception(
                    "Unexpected flush error (%s=%s): %s",
                    item.kind, item.name, exc,
                )
                await self._dlq_send(item, error=f"unexpected: {exc}")
        finally:
            self._send_queue.task_done()

    async def _flush_one(self, item: _SendItem) -> None:
        """Dispatch one buffered item via async HTTP POST with retry."""
        metrics_key = item.name if item.kind == "topic" else f"queue:{item.name}"
        if item.kind == "topic":
            qualified = self._names.qualify_topic(item.name)
            path = "topics"
        else:
            qualified = self._names.qualify_queue(item.name)
            path = "queues"
        encoded = self._names.encode_segment(qualified)
        url = f"{self._messaging_url}/messagingrest/v1/{path}/{encoded}/messages"

        headers = {
            **(await self._tokens.auth_headers_async()),
            "Content-Type": "application/json",
            "x-qos": "1",
        }
        if item.key:
            headers["x-message-key"] = item.key

        await self._post_with_retry_async(
            url, content=item.payload, headers=headers, topic_for_metrics=metrics_key,
        )

    # ------------------------------------------------------------------
    # Async HTTP retry path
    # ------------------------------------------------------------------

    async def _post_with_retry_async(
        self, url: str, *, content: bytes, headers: dict[str, str],
        topic_for_metrics: str,
    ) -> httpx.Response:
        """POST with exponential backoff + jitter on transient failures.

        The single publish retry path (the synchronous twin was removed when
        publication became async-only). Returns the successful response; on
        terminal failure raises ``EventMeshPublishFailed`` with the attempt
        count and a descriptive ``last_error``. Records per-attempt retry
        metrics and success/failure summaries via ``self._metrics``.
        """
        assert self._clients.async_ is not None
        start_ts = time.monotonic()
        last_exc: Exception | None = None
        actual_attempts = 0
        for attempt in range(self._max_retries):
            actual_attempts = attempt + 1
            try:
                resp = await self._clients.async_.post(url, content=content, headers=headers)
                resp.raise_for_status()
                latency_ms = (time.monotonic() - start_ts) * 1000.0
                self._metrics.record_publish_success(
                    topic_for_metrics, latency_ms, actual_attempts,
                )
                return resp
            except Exception as exc:
                last_exc = exc
                reason = self._classify_reason(exc)
                terminal = (
                    not self._is_retryable(exc)
                    or attempt == self._max_retries - 1
                )
                if terminal:
                    self._metrics.record_publish_failure(
                        topic_for_metrics, reason, actual_attempts,
                    )
                    break
                self._metrics.record_publish_retry(
                    topic_for_metrics, actual_attempts, reason,
                )
                logger.warning(
                    "Event Mesh transient error (%s) on attempt %d/%d — retrying",
                    reason, actual_attempts, self._max_retries,
                )
                await asyncio.sleep(self._backoff_delay(attempt))
        assert last_exc is not None
        raise EventMeshPublishFailed(
            topic=topic_for_metrics,
            attempts=actual_attempts,
            last_error=self._describe_exception(last_exc),
        ) from last_exc

    def _is_retryable(self, exc: Exception) -> bool:
        """Return True if *exc* represents a transient error worth retrying."""
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in _RETRYABLE_STATUS_CODES
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
        return False

    def _backoff_delay(self, attempt: int) -> float:
        """Return exponential backoff delay with ±20% jitter for *attempt* (0-indexed)."""
        base = _DEFAULT_RETRY_BASE_DELAY_S * (2 ** attempt)
        jitter = base * _DEFAULT_RETRY_JITTER_RATIO * (random.random() * 2 - 1)
        return max(0.0, base + jitter)

    @staticmethod
    def _classify_reason(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"http_{exc.response.status_code}"
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.TransportError):
            return "transport"
        return "unknown"

    @staticmethod
    def _describe_exception(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            body = (exc.response.text or "")[:200]
            return f"http_{exc.response.status_code}: {body}"
        return f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # DLQ + circuit breaker + health
    # ------------------------------------------------------------------

    def _is_durable(self, item: _SendItem) -> bool:
        """Durable events must never be silently lost; best-effort topics
        (realtime UI ticks) keep the historical counted-drop (SA-1471)."""
        return item.name not in self._best_effort_topics

    @staticmethod
    def _dlq_envelope(item: _SendItem, error: str) -> dict:
        return {
            "original_kind": item.kind,
            "original_name": item.name,
            "error": error,
            "failed_at": time.time(),
            "payload_base64": base64.b64encode(item.payload).decode("ascii"),
            "payload_size_bytes": len(item.payload),
        }

    def _spool_or_lose(self, item: _SendItem, error: str, reason: str) -> None:
        """Park a durable envelope in the local spool; only count a drop
        when the spool can't take it either (true loss)."""
        if self._is_durable(item) and self._spool.append(self._dlq_envelope(item, error)):
            self._metrics.record_dlq_fallback(reason, item.name)
            self._metrics.record_spool_depth(self._spool.depth())
            logger.warning(
                "DLQ unavailable (%s) — parked %s=%s in local spool (depth=%d)",
                reason, item.kind, item.name, self._spool.depth(),
            )
            return
        self._metrics.record_dropped(reason, item.name)
        logger.error(
            "DLQ unavailable (%s) for %s=%s — message lost",
            reason, item.kind, item.name,
        )

    async def _dlq_send(self, item: _SendItem, error: str) -> None:
        """Send a failed publish to the DLQ queue (bypasses circuit).

        Direct HTTP POST bypasses the send buffer AND the circuit breaker.
        Rationale: DLQ must remain available precisely when the main
        publish path is failing; retrying through the circuit would either
        keep the circuit open or recursively fill the DLQ.

        SA-1471: no durable event is silently lost anymore — every path
        that previously dropped (disabled / no client / DLQ POST failure)
        now parks the envelope in the local spool, which is replayed when
        the circuit closes and at startup.
        """
        if not self._enable_dlq:
            self._spool_or_lose(item, error, reason="dlq_disabled")
            return
        if self._clients.async_ is None:
            logger.error("Cannot send to DLQ — async HTTP client not initialized")
            self._spool_or_lose(item, error, reason="dlq_no_client")
            return

        envelope = self._dlq_envelope(item, error)
        envelope_bytes = json.dumps(envelope).encode("utf-8")
        qualified = self._names.qualify_queue(_DLQ_QUEUE_NAME)
        encoded = self._names.encode_segment(qualified)
        url = f"{self._messaging_url}/messagingrest/v1/queues/{encoded}/messages"

        try:
            headers = {
                **(await self._tokens.auth_headers_async()),
                "Content-Type": "application/json",
                "x-qos": "1",
            }
            resp = await self._clients.async_.post(url, content=envelope_bytes, headers=headers)
            resp.raise_for_status()
            self._metrics._increment(
                "eventmesh.publish.dlq", {"original_name": item.name, "error": error[:64]},
            )
            logger.info("Sent failed publish to DLQ: %s=%s", item.kind, item.name)
        except Exception as exc:
            logger.error(
                "DLQ publish failed for %s=%s: %s",
                item.kind, item.name, exc,
            )
            self._spool_or_lose(item, error, reason="dlq_failed")

    def _on_circuit_state_change(self, old_state: str, new_state: str) -> None:
        """Record circuit transitions + gauge, and kick off spool replay
        when the circuit closes (the broker just proved healthy)."""
        self._metrics._increment(
            "eventmesh.circuit.state_change",
            {"from": old_state, "to": new_state},
        )
        # SA-1471: alertable gauge — 1.0 while OPEN/HALF_OPEN, 0.0 when CLOSED.
        self._metrics.record_circuit_state(new_state)
        if new_state == STATE_CLOSED and self._spool.depth() > 0:
            self._schedule_spool_replay("circuit_closed")

    def _schedule_spool_replay(self, trigger: str) -> None:
        """Create the replay task when an event loop is available. Safe to
        call from sync contexts (tests) — silently skipped without a loop.

        Single-flight (SA-1469): the flush loop's idle tick re-triggers this
        every poll interval while the spool holds buffer-full overflow, so a
        still-running replay task must not be duplicated.
        """
        if not self._running_async or not self._spool.enabled:
            return
        if self._replay_task is not None and not self._replay_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._replay_task = loop.create_task(
            self._replay_spool(trigger), name=f"eventmesh-dlq-replay-{trigger}",
        )

    async def _replay_spool(self, trigger: str) -> None:
        """Drain the local spool back into the send buffer (FIFO).

        Uses the blocking ``put`` (not ``put_nowait``): replay runs off the
        caller path, so waiting for buffer capacity is correct — dropping a
        replayed durable envelope would defeat the whole spool.
        """
        assert self._send_queue is not None
        replayed = 0
        while self._running_async and self._spool.depth() > 0:
            entries = self._spool.drain(max_entries=self._flush_batch_size)
            if not entries:
                break
            for env in entries:
                try:
                    payload = base64.b64decode(env["payload_base64"])
                    item = _SendItem(
                        kind=env.get("original_kind", "topic"),
                        name=env["original_name"],
                        payload=payload,
                    )
                except Exception:
                    logger.exception("DLQ spool entry malformed — skipped")
                    continue
                await self._send_queue.put(item)
                self._metrics.record_spool_replayed(item.name)
                replayed += 1
        self._metrics.record_spool_depth(self._spool.depth())
        if replayed:
            logger.info(
                "DLQ spool replay (%s): re-enqueued %d envelope(s), %d left",
                trigger, replayed, self._spool.depth(),
            )

    def health_check(self) -> dict:
        """Return a snapshot of publisher state for observability (no network)."""
        send_buffer_depth = (
            self._send_queue.qsize() if self._send_queue is not None else 0
        )
        token_valid_for_s = max(0.0, self._tokens.token_expires_at - time.time())
        circuit = self._breaker.snapshot()
        healthy = (
            self._running_async
            and circuit["state"] == STATE_CLOSED
            and send_buffer_depth < self._buffer_size
        )
        return {
            "healthy": healthy,
            "running_async": self._running_async,
            "circuit": circuit,
            "send_buffer": {
                "depth": send_buffer_depth,
                "max": self._buffer_size,
                "utilization": (
                    send_buffer_depth / self._buffer_size
                    if self._buffer_size > 0 else 0.0
                ),
                "policy": self._buffer_policy,
            },
            "token": {
                "has_token": bool(self._tokens.access_token),
                "valid_for_s": token_valid_for_s,
            },
            "dlq_enabled": self._enable_dlq,
            "dlq_spool": {
                "enabled": self._spool.enabled,
                "depth": self._spool.depth(),
            },
        }
