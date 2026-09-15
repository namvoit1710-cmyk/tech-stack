"""Topic/queue name qualification + URL-segment encoding for SAP Event Mesh.

Holds the (mutable) service namespace. The broker can adopt the namespace at
runtime from a 403 self-heal, so a single shared ``EventMeshNames`` instance is
passed to the publisher, consumer, and admin — they all qualify against the
same, possibly-adopted, namespace.
"""

from __future__ import annotations

import logging
import re
import threading
from urllib.parse import quote

logger = logging.getLogger(__name__)

# When a queue name reaches the (shared) management backend without the
# service-instance namespace prefix, Event Mesh replies 403 with a body like:
#   "The queue=<q> is not in the namespace=<ns> of the service instance id=<id>"
# We parse <ns> out of that to self-heal a missing/empty configured namespace.
_NAMESPACE_FROM_ERROR_RE = re.compile(r"namespace=([^\s,]+)")


class EventMeshNames:
    """Namespace qualification + URL encoding; owns the mutable namespace.

    The one shared instance is read by publisher/consumer/admin threads while
    a 403 self-heal may adopt a new namespace concurrently — adoption is
    therefore guarded by a lock and double-checked so simultaneous 403s adopt
    exactly once (SA-1468).
    """

    def __init__(self, namespace: str = "default", *, allow_self_heal: bool = True) -> None:
        self._namespace = namespace
        self._allow_self_heal = allow_self_heal
        self._adopt_lock = threading.Lock()

    @property
    def namespace(self) -> str:
        return self._namespace

    @staticmethod
    def em_safe_topic(topic: str) -> str:
        """Rewrite a topic into an Event-Mesh-valid form.

        Classic SAP Event Mesh rejects an underscore in a topic level with
        ``400 INVALID_INPUT: "Topic is invalid"``. Every other topic in the
        system is already underscore-free dotted form (``run.status``,
        ``node.dispatched``, ``workflow.results`` …); only the ``child_run.*``
        lifecycle topics carried an underscore, so they were the only ones
        failing to publish. Map ``_`` → ``-`` so the domain ``EventTopic``
        vocabulary stays intact everywhere except on the Event Mesh wire.

        Applied through ``qualify_topic`` — the single chokepoint for both
        publish and subscription — so producers and consumers bind to the same
        wire name. Idempotent; a no-op for already-safe topics.
        """
        return topic.replace("_", "-")

    def qualify_topic(self, topic: str) -> str:
        """Prepend the namespace to a topic if not already qualified.

        SAP Event Mesh requires topics to be prefixed with the service
        namespace, e.g. ``simplemdg/event/core-2/run.started``.
        """
        topic = self.em_safe_topic(topic)
        if not self._namespace:
            return topic
        prefix = self._namespace.rstrip("/") + "/"
        if topic.startswith(prefix):
            return topic
        return prefix + topic

    def qualify_queue(self, queue_name: str) -> str:
        """Prepend the namespace to a queue name if not already qualified."""
        if not self._namespace:
            return queue_name
        prefix = self._namespace.rstrip("/") + "/"
        if queue_name.startswith(prefix):
            return queue_name
        return prefix + queue_name

    @staticmethod
    def encode_segment(value: str) -> str:
        """URL-encode a path segment for the REST API."""
        return quote(value, safe="")

    @staticmethod
    def queue_local_from_topic(
        topic: str,
        transport: str | None = None,
        scope: str | None = None,
    ) -> str:
        """Derive a queue local-name from a topic name.

        SAP Event Mesh's topic syntax uses dots as hierarchy separators
        (``a.b.c``) but the queue-name policy on most plans rejects dots
        in queue local-names — they're reserved for paths/hierarchy in
        the queue namespace. Mirror the topic with dots replaced by
        hyphens so the queue name stays slash-clean while remaining
        debuggable (the original topic words still appear).

        Two optional dimensions, both of which change *who competes for the
        messages* — which is why they belong in the name and not in config:

        ``transport``
            A queue may not be shared between REST polling and AMQP push.
            Measured on tenant-1 2026-08-05: SAP allocates **3 broker-side
            consumer flows per REST-polled queue**, and stopping the poller
            does **not** release them — they expire only after 30 minutes with
            *no publishing*, which a hot queue never has. The queues are
            ``NON_EXCLUSIVE``, so an AMQP link attaching to the same queue
            becomes a 4th round-robin consumer and whatever the broker hands
            to a stopped flow is stranded (reproduced: 20 published, AMQP got
            17, 3 stuck ``unacked`` forever). A run only needs its
            ``StartRunAction`` stranded to hang. Giving each transport its own
            queue makes that structurally impossible instead of a rule someone
            has to remember.

        ``scope``
            ``None`` = one shared queue, competing consumers — correct for
            *work* topics (orchestration.actions, workflow.results,
            workflow.triggers, timer.fired, node.input) where any instance may
            handle any message. An instance id = one queue per instance, which
            is required for *fan-out* topics (realtime.events, catalog.changed)
            where every instance must see every message: each instance owns its
            own Socket.IO clients and its own cache, so a shared queue means
            instance A eats an event whose client is on instance B and that
            client silently never sees it. Unused while the control plane is
            pinned to one instance — present now because adding it later means
            renaming live queues a second time, and renaming a live queue is
            the migration that caused three outages.

        Example:
            topic  = "orchestration.responses.7a2c7636"
            queue  = "sub-orchestration-responses-7a2c7636"
            (transport="amqp")            -> "...-7a2c7636-amqp"
            (transport="amqp", scope="i7") -> "...-7a2c7636-amqp-i7"
        """
        name = f"sub-{topic.replace('.', '-')}"
        # REST is the historical default and keeps the bare name, so existing
        # queues are untouched by this change.
        if transport and transport.lower() not in ("", "rest"):
            name = f"{name}-{transport.lower()}"
        if scope:
            name = f"{name}-{scope}"
        return name

    def adopt_namespace_from_error(self, body: str) -> bool:
        """Adopt the instance namespace revealed in a 403 management response.

        Event Mesh's shared management backend rejects an unqualified queue name
        with ``403 ... is not in the namespace=<ns> of the service instance``.
        When our configured namespace is empty (EVENT_MESH_NAMESPACE unset and
        no top-level ``namespace`` in the service key), every queue/topic name
        goes out unqualified and is 403-rejected. Parsing ``<ns>`` from the
        broker's own error lets the client self-heal: once adopted, both
        ``qualify_topic`` and ``qualify_queue`` prefix it everywhere.

        Returns ``True`` only when a new namespace was adopted (so the caller
        can retry the operation with correctly-qualified names).

        Thread-safe: parse happens lock-free, but the check-and-mutate is
        guarded + double-checked so N concurrent 403s adopt exactly once —
        the losers get ``False`` and re-qualify against the winner's value.
        When self-heal is disabled (prod hardening, SA-1468) this is a no-op
        that surfaces the 403 to the caller instead of mutating shared state.
        """
        if not self._allow_self_heal:
            logger.error(
                "Event Mesh 403 indicates unqualified names but namespace "
                "self-heal is disabled (EVENT_MESH_ALLOW_NAMESPACE_SELF_HEAL=0) "
                "— set EVENT_MESH_NAMESPACE explicitly. Body: %.200s", body or "",
            )
            return False
        if "not in the namespace" not in (body or ""):
            return False
        match = _NAMESPACE_FROM_ERROR_RE.search(body)
        if not match:
            return False
        discovered = match.group(1).strip().rstrip("./")
        if not discovered:
            return False
        with self._adopt_lock:
            if discovered == self._namespace:
                return False  # a concurrent 403 already adopted it
            logger.warning(
                "Event Mesh namespace was %r; adopting %r discovered from the "
                "broker's 403 response so queue/topic names qualify correctly",
                self._namespace, discovered,
            )
            self._namespace = discovered
            return True
