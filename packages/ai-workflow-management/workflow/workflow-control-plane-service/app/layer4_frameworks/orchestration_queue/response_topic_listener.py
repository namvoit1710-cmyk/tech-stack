"""Phase M.3.3 — cross-instance EditNodeDataAction response listener.

When multiple control-plane instances run against the same Kafka broker,
partition-by-run_id routing means the instance that enqueued an
``EditNodeDataAction`` is often NOT the one that processes it. The HTTP
caller's ``asyncio.Future`` lives on the enqueueing instance; the
processing instance has no way to resolve it directly.

This listener is the bridge. Each instance subscribes to its own
response topic (``orchestration.responses.{instance_id}``). The
processing instance's worker publishes onto the origin's topic; the
origin's listener picks it up and resolves the local
``ResponseRegistry`` future.

Single-instance deployments don't need this — the worker resolves the
local registry directly (fast path in ``OrchestrationWorker``).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataResult,
    OpResult,
)
from app.layer2_application.interfaces.broker_client_interface import IBrokerClient
from app.layer2_application.interfaces.monitor_interface import IMonitor
from app.layer4_frameworks.orchestration_queue.response_registry import ResponseRegistry

_logger = logging.getLogger(__name__)

RESPONSE_TOPIC_PREFIX = "orchestration.responses"

# SA-1466 — observability metric names. The gauge is 1.0 when this pod
# could NOT subscribe to its per-instance response topic (cross-instance
# response routing disabled here) and 0.0 when healthy. Alert on
# ``sum(orchestration_response_listener_degraded) > 0`` for a multi-pod
# deployment. The counter records each subscribe outcome.
_METRIC_DEGRADED = "orchestration.response_listener.degraded"
_METRIC_SUBSCRIBE = "orchestration.response_listener.subscribe"

# Event Mesh local-segment names cap at 60 chars; the qualified queue
# name is ``sub-<topic>`` plus the broker namespace prefix. With prefix
# ``orchestration.responses.`` (24 chars) + ``sub-`` (4 chars) we have
# ~32 chars left for the instance segment, so the shard is 8 chars.
#
# SA-2087 (R12): those 8 chars are a HASH PREFIX, not a truncation of the
# id. Truncating (the previous ``compact[:8]``) assumed instance ids differ
# in their FIRST bytes. That holds for a uuid4 and fails completely for a
# Kubernetes Deployment pod name: the k8s manifest that prompted this (since
# deleted — SA-2272; nothing deployed it) pinned INSTANCE_ID to
# ``metadata.name``, which is ``wio-executor-<rs-hash>-<rand>``,
# so EVERY replica truncated to the same shard ``wioexecu`` and subscribed to
# ONE shared topic. With ``num_partitions=1`` and a shared consumer group,
# a single pod then consumed every peer's responses and dropped them
# silently (``ResponseRegistry.resolve`` is a no-op for an unknown
# correlation-id), leaving the real origin to time out after 30s.
#
# Hashing removes the first-bytes assumption for every id shape — pod
# names, GUIDs, ``<app>_<CF_INSTANCE_INDEX>`` — at the same character cost.
# 2^32 over the handful of instances a deployment runs simultaneously is a
# comfortable collision space; a *guaranteed* collision was the bug.
_INSTANCE_SHARD_LEN = 8


def _instance_shard(instance_id: str) -> str:
    """Return a short, queue-name-safe, collision-resistant form of an id.

    ``sha256(instance_id)[:_INSTANCE_SHARD_LEN]`` — deterministic, so the
    publishing peer (``orchestration_queue/worker.py``) and the subscribing
    origin derive the same topic with no coordination. A blank id stays
    blank: "no instance" must not hash into a real-looking shard.
    """
    if not instance_id:
        return ""
    return hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:_INSTANCE_SHARD_LEN]


def response_topic_for(instance_id: str) -> str:
    """Per-instance topic name — publish here to deliver a response to
    the instance identified by ``instance_id``.

    The instance id is truncated to a short shard so the qualified
    Event Mesh queue name (``<namespace>/sub-<topic>``) fits under SAP
    Event Mesh's 60-char local-segment limit. See ``_instance_shard``
    for the shortening scheme.
    """
    return f"{RESPONSE_TOPIC_PREFIX}.{_instance_shard(instance_id)}"


class CrossInstanceExecutionError(Exception):
    """Raised on the origin instance when the processing instance
    reported an unexpected exception (not a domain-level result.error)."""

    def __init__(self, exception_class: str, message: str) -> None:
        super().__init__(f"{exception_class}: {message}")
        self.exception_class = exception_class
        self.original_message = message


class ResponseTopicListener:
    """Subscribes to this instance's response topic and resolves the
    local ``ResponseRegistry`` when messages arrive from peer instances.
    """

    def __init__(
        self,
        broker_client: IBrokerClient,
        instance_id: str,
        registry: ResponseRegistry,
        monitor: IMonitor | None = None,
    ) -> None:
        self._broker = broker_client
        self._instance_id = instance_id
        self._registry = registry
        # SA-1466 — optional metrics sink. When wired (bootstrap passes
        # infra.monitor), degradation is observable instead of only logged.
        # Safe no-op when None (tests / mock infra).
        self._monitor = monitor
        self._topic = response_topic_for(instance_id)
        self._started = False
        # Set to True only when subscribe_to_topic succeeds. When False,
        # cross-instance responses cannot be delivered to this instance —
        # but the local fast-path (worker on same pod resolves the local
        # registry directly) still works, so single-pod deployments
        # continue to function normally.
        self._degraded = False

    @property
    def degraded(self) -> bool:
        return self._degraded

    def health(self) -> dict[str, Any]:
        """SA-1466 — observability snapshot for the readiness/health endpoints.

        ``degraded=True`` means this pod could not subscribe to its
        per-instance response topic, so cross-instance EditNodeDataAction
        responses cannot be routed here (the local single-pod fast-path is
        unaffected). The readiness endpoint turns this into an optional
        NotReady signal when ``RESPONSE_LISTENER_READINESS_GATE_ENABLED``.
        """
        return {
            "present": True,
            "topic": self._topic,
            "instance_id": self._instance_id,
            "started": self._started,
            "degraded": self._degraded,
        }

    def _emit_degraded(self, is_degraded: bool, *, reason: str = "") -> None:
        """Emit the degraded gauge (0/1) + a subscribe-outcome counter.

        Guarded like ``EventMeshMetrics`` — monitor failures must never
        break startup, and this is a no-op when no monitor is wired.
        """
        if self._monitor is None:
            return
        shard = _instance_shard(self._instance_id)
        try:
            self._monitor.track(_METRIC_DEGRADED, 1.0 if is_degraded else 0.0)
            self._monitor.increment(
                _METRIC_SUBSCRIBE,
                tags={
                    "result": "degraded" if is_degraded else "ok",
                    "instance": shard,
                    "reason": reason,
                },
            )
        except Exception:  # pragma: no cover - metrics must never break startup
            pass

    def start(self) -> None:
        """Ensure the topic exists, then subscribe.

        Broker failures (e.g. SAP Event Mesh queue creation 422 from a
        plan-level quota or naming policy) are **non-fatal**: the
        listener logs a warning and sets ``degraded=True``. The control
        plane keeps starting up because cross-instance response routing
        is only required for multi-pod deployments; single-pod runs use
        the local-registry fast-path and don't need the broker
        subscription. This trade preserves uptime over silent multi-pod
        correctness — operators see the warning and investigate, instead
        of the whole pod crash-looping.
        """
        if self._started:
            return
        self._started = True
        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(self._topic)
            except Exception:
                _logger.warning(
                    "Could not auto-create topic '%s' — will subscribe anyway",
                    self._topic,
                )
        try:
            self._broker.subscribe_to_topic(self._topic, self._on_message)
            _logger.info("Response listener subscribed to %s", self._topic)
            self._emit_degraded(False)
        except Exception as exc:
            self._degraded = True
            _logger.error(
                "ResponseTopicListener degraded: broker subscribe failed for "
                "topic '%s' (instance_id=%s). Cross-instance response routing "
                "is DISABLED on this pod. Local fast-path (single-pod) "
                "responses continue to work. Cause: %s",
                self._topic,
                self._instance_id,
                exc,
                exc_info=True,
            )
            # SA-1466 — make the degradation observable (metric + alert),
            # not just a log line an operator has to notice.
            self._emit_degraded(True, reason=type(exc).__name__)

    def stop(self) -> None:
        """Release this instance's queue on the way down (SA-2087 R12).

        Without this, every boot provisioned a queue via ``ensure_topic`` and
        nothing ever reclaimed it: after a few dozen deploys the broker's
        plan quota fills with orphans whose pods are long gone, and
        ``create_queue`` starts returning 422 — at which point a NEW pod
        starts ``degraded`` and its cross-instance responses are dropped.
        (``tools/cleanup_event_mesh_queues.py`` is the manual broom for the
        orphans that already exist; this makes a graceful stop self-clean.)

        Crash / SIGKILL never reaches this, which is why the deployment
        manifests pin identity to something stable and bounded — a restarted
        instance then RE-USES its queue instead of minting another.

        Contract, mirroring ``start()``: never raise. A shutdown must not be
        blocked by a queue that is already gone, a broker that is already
        closed, or a transport (Kafka, dev/local) with no queue concept at
        all. Idempotent, and a no-op if ``start()`` never ran.
        """
        if not self._started:
            return
        self._started = False
        delete_topic_queue = getattr(self._broker, "delete_topic_queue", None)
        if delete_topic_queue is None:
            return
        try:
            delete_topic_queue(self._topic)
            _logger.info("Response listener released queue for %s", self._topic)
        except Exception as exc:
            _logger.warning(
                "Could not release the response queue for topic '%s' "
                "(instance_id=%s) — it becomes an orphan; reclaim it with "
                "tools/cleanup_event_mesh_queues.py. Cause: %s",
                self._topic, self._instance_id, exc,
            )

    def _on_message(self, message: dict[str, Any]) -> None:
        correlation_id = message.get("correlation_id")
        if not correlation_id:
            _logger.warning("Response message missing correlation_id: %s", message)
            return
        error = message.get("error")
        if error is not None:
            self._registry.fail(
                correlation_id,
                CrossInstanceExecutionError(
                    exception_class=error.get("class", "Exception"),
                    message=error.get("message", ""),
                ),
            )
            return
        result_dict = message.get("result")
        if result_dict is None:
            _logger.warning("Response missing both result and error: %s", message)
            return
        try:
            result = _rehydrate_edit_node_data_result(result_dict)
        except (TypeError, KeyError) as exc:
            _logger.exception("Failed to rehydrate EditNodeDataResult: %s", exc)
            self._registry.fail(correlation_id, exc)
            return
        self._registry.resolve(correlation_id, result)


def _rehydrate_edit_node_data_result(data: dict[str, Any]) -> EditNodeDataResult:
    """Reverse ``dataclasses.asdict`` for the nested result shape."""
    nested_results = [OpResult(**r) for r in data.get("results", [])]
    top = {k: v for k, v in data.items() if k != "results"}
    return EditNodeDataResult(results=nested_results, **top)
