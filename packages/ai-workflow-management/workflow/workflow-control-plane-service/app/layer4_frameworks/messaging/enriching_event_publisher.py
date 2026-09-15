"""Decorator that publishes enriched events to the centralized workflow topic.

Wraps any ``IEventPublisher`` and, after delegating the original publish,
builds an enriched envelope and sends it to ``WORKFLOW_EVENT_TOPIC``.

Publisher chain: RealtimeBroadcasting → **EnrichingEventPublisher** → Kafka/EventMesh/Console
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import threading
from typing import Any, TYPE_CHECKING

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
from app.layer2_application.interfaces.broker_client_interface import IBrokerClient
from app.layer4_frameworks.config.app_config import settings

if TYPE_CHECKING:
    from app.layer4_frameworks.messaging.event_enricher import CentralizedEventEnricher

logger = logging.getLogger(__name__)

# Broker hard limit (SAP Event Mesh / Kafka default ~1 MB). Envelopes larger
# than this are rejected by the broker, so we proactively shrink them below
# the cap before publishing.
MAX_MESSAGE_SIZE = 1_048_576  # 1 MB

# Routing/context keys that must never be dropped while shrinking an oversized
# envelope — they are small and consumers need them to correlate the event and
# fetch the full payload (e.g. task input/output via ``task_id``).
_PROTECTED_KEYS = frozenset(
    {
        "_event",
        "run_id",
        "root_run_id",
        "workflow_id",
        "root_workflow_id",
        "node_id",
        "task_id",
        "_run",
        "_workflow",
        "_node",
        "_run_key",
        "_run_seq",
    }
)

# Run ``status`` values that end a run — once the ROOT run publishes a
# terminal ``run.status`` envelope its per-run sequence counter is released
# so the counter dict doesn't grow for the process lifetime. Mirrors
# ``RealtimeBroadcastingPublisher._TERMINAL_RUN_STATUSES``.
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Topic names that carry the run-lifecycle ``status`` field — canonical
# ``EventTopic.RUN_STATUS_CHANGED`` plus the lowercased class-name form
# accepted elsewhere in the chain.
_RUN_STATUS_TOPICS = frozenset({"run.status", "runstatuschanged"})

#target_topic
_METADATA_AGENT_TARGET_TOPIC_KEY = "target_topic"
_METADATA_AGENT_SOURCE_KEY = "source"
_METADATA_AGENT_SOURCE_AGENT = "agent"


class EnrichingEventPublisher(IEventPublisher):
    """Publishes to original topic via inner publisher, then publishes
    an enriched envelope to the centralized workflow event topic."""

    def __init__(
        self,
        inner: IEventPublisher,
        broker_client: IBrokerClient | None = None,
        enricher: CentralizedEventEnricher | None = None,
    ) -> None:
        self._inner = inner
        self._broker = broker_client
        self._enricher = enricher
        # Per-run contiguous sequence for the centralized topic. Event Mesh
        # delivery is at-least-once and can reorder on lock-TTL redelivery,
        # and the centralized envelope otherwise carries only event_id +
        # wall-clock timestamp — nothing a consumer can resequence on.
        # Counted only for envelopes that actually reach the centralized
        # topic, so the stream is contiguous 1..N per root run.
        self._run_seq: dict[str, int] = {}
        self._run_seq_lock = threading.Lock()

    def _next_run_seq(self, run_key: str) -> int:
        """Return the next contiguous sequence number for ``run_key``."""
        with self._run_seq_lock:
            nxt = self._run_seq.get(run_key, 0) + 1
            self._run_seq[run_key] = nxt
            return nxt

    def _release_run_seq(self, run_key: str) -> None:
        """Drop the counter for a finished run so it doesn't leak."""
        with self._run_seq_lock:
            self._run_seq.pop(run_key, None)

    # ------------------------------------------------------------------
    # Size helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _json_size(value: Any) -> int:
        """Return the encoded JSON byte length of ``value`` (matches broker size)."""
        try:
            return len(json.dumps(value, default=str).encode("utf-8"))
        except Exception:
            return -1

    @staticmethod
    def _top_key_sizes(payload: dict, top_n: int = 5) -> list[tuple[str, int]]:
        """Return the ``top_n`` largest top-level keys as ``(key, bytes)`` pairs.

        Used to diagnose oversized broker envelopes: tells you which field is
        responsible for the bulk without dumping the full payload.
        """
        if not isinstance(payload, dict):
            return []
        sizes = [
            (key, EnrichingEventPublisher._json_size(value))
            for key, value in payload.items()
        ]
        sizes.sort(key=lambda kv: kv[1], reverse=True)
        return sizes[:top_n]

    # ------------------------------------------------------------------
    # input_schema 'default' stripping
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_input_schema_defaults(payload: dict) -> None:
        """Remove every ``default`` key from the input_schema subtree, in place.

        The auto-form ``input_schema`` is a tree whose ``default`` keys carry
        bulky raw row data that pushes the broker envelope past the size limit.
        The FE already receives the full schema (with defaults) via the realtime
        Push Gateway path, so the broker topic only needs the structural shape.
        """
        input_schema = payload.get("input_schema")
        if input_schema is not None:
            EnrichingEventPublisher._strip_default_keys(input_schema)

    @staticmethod
    def _strip_default_keys(node: Any) -> None:
        """Recursively drop every ``default`` key from dicts inside ``node``."""
        if isinstance(node, dict):
            node.pop("default", None)
            for value in node.values():
                EnrichingEventPublisher._strip_default_keys(value)
        elif isinstance(node, list):
            for item in node:
                EnrichingEventPublisher._strip_default_keys(item)

    # ------------------------------------------------------------------
    # Size enforcement
    # ------------------------------------------------------------------

    def _enforce_size_limit(self, envelope: dict, topic: str) -> dict:
        """Shrink ``envelope`` below ``MAX_MESSAGE_SIZE``, in place.

        Bulky, fetch-able fields are dropped largest-first (everything except
        the small routing keys in ``_PROTECTED_KEYS``). Each dropped value is
        replaced with an omission marker so consumers can tell the data was
        elided (and refetch via ``task_id``) rather than missing. The protected
        routing keys are never removed, so the envelope stays correlatable even
        in the unlikely case it still exceeds the cap afterwards.
        """
        size = self._json_size(envelope)
        if size <= MAX_MESSAGE_SIZE:
            return envelope

        logger.warning(
            "Envelope %d bytes exceeds %d-byte limit for topic %s — shrinking. top_keys=%s",
            size,
            MAX_MESSAGE_SIZE,
            topic,
            self._top_key_sizes(envelope),
        )

        while size > MAX_MESSAGE_SIZE:
            candidates = [
                (key, self._json_size(value))
                for key, value in envelope.items()
                if key not in _PROTECTED_KEYS
            ]
            if not candidates:
                # Only protected routing keys remain; nothing left to safely drop.
                logger.error(
                    "Envelope still %d bytes after dropping all non-essential fields "
                    "for topic %s — broker may reject. top_keys=%s",
                    size,
                    topic,
                    self._top_key_sizes(envelope),
                )
                break

            largest_key, dropped_bytes = max(candidates, key=lambda kv: kv[1])
            envelope[largest_key] = {
                "_omitted": True,
                "_reason": "exceeds-size-limit",
                "_bytes": dropped_bytes,
            }
            logger.warning(
                "Dropped field %r (%d bytes) from envelope for topic %s to fit size limit",
                largest_key,
                dropped_bytes,
                topic,
            )
            size = self._json_size(envelope)

        return envelope

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_event(event: Any) -> Any:
        """Normalize an event into a JSON-friendly mapping.

        Dataclass events are converted via ``dataclasses.asdict`` (which
        deep-copies). Dict events are returned **as-is** so callers can detect
        ``payload is event`` and copy before mutating. Anything else is wrapped
        as ``{"data": str(event)}``.
        """
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            return dataclasses.asdict(event)
        if isinstance(event, dict):
            return event
        return {"data": str(event)}

    def publish(self, topic: str, event: Any) -> None:
        # Serialize once — used for both inner publisher and centralized topic.
        # dataclasses.asdict() deep-copies; for dicts we deep-copy input_schema
        # before stripping so the caller's original event is never mutated.
        payload = self._serialize_event(event)

        # Diagnostic: payload size BEFORE strip — confirms the input_schema
        # bulk before any reduction. Compare with the "after strip" line to
        # verify the strip is actually shrinking the payload.
        size_before_strip = self._json_size(payload)

        # Strip bulky 'default' rows from input_schema before any publish.
        # The upstream RealtimeBroadcastingPublisher already sent the full
        # event (with defaults) to Push Gateway / Socket.IO, so downstream
        # broker topics do not need the raw row data.
        if isinstance(payload, dict) and "input_schema" in payload:
            if payload is event:
                # Dict event — avoid mutating the caller's object
                payload = {**payload, "input_schema": copy.deepcopy(payload["input_schema"])}
            self._strip_input_schema_defaults(payload)

        size_after_strip = self._json_size(payload)
        logger.info(
            "Payload size for topic %s: before_strip=%d bytes, after_strip=%d bytes "
            "(delta=%d), top_keys=%s",
            topic,
            size_before_strip,
            size_after_strip,
            size_before_strip - size_after_strip,
            self._top_key_sizes(payload),
        )

        # 1. Delegate to inner publisher (original topic) with stripped payload
        self._inner.publish(topic, payload)

        # 2. Publish enriched envelope to centralized topic
        workflow_event_topic = settings.WORKFLOW_EVENT_TOPIC

        metadata = payload.get("metadata") if isinstance(payload, dict) else None

        if metadata:
            custom_publish_topic = metadata.get(_METADATA_AGENT_TARGET_TOPIC_KEY)
            if custom_publish_topic:
                workflow_event_topic = custom_publish_topic
                logger.info(
                    "Overriding centralized topic with caller-supplied metadata.%s: %s",
                    _METADATA_AGENT_TARGET_TOPIC_KEY,
                    workflow_event_topic,
                )

        if not workflow_event_topic or topic == workflow_event_topic or not self._broker:
            return

        # Centralized-topic gate: only runs whose caller-supplied metadata
        # (captured at ``POST /runs`` and stamped onto every event by
        # ``MetadataStampingPublisher``) declares ``"source": "agent"`` are
        # forwarded to the centralized topic. The original-topic publish
        # above is unaffected.
        source = metadata.get(_METADATA_AGENT_SOURCE_KEY) if isinstance(metadata, dict) else None
        if source != _METADATA_AGENT_SOURCE_AGENT:
            # Expected gating, not an anomaly: non-agent runs simply don't fan
            # out to the centralized topic. Logged at DEBUG so it doesn't spam
            # a WARNING for every event of every non-agent run.
            logger.debug(
                "Skipping centralized topic %s for event %s (run_id=%s): "
                "metadata.source=%r, expected 'agent'",
                workflow_event_topic,
                topic,
                payload.get("run_id", "") if isinstance(payload, dict) else "",
                source,
            )
            return

        try:
            # Serialize event. dataclasses.asdict() deep-copies; for dict events
            # we deep-copy input_schema before stripping so the caller's original
            # object is never mutated.
            if dataclasses.is_dataclass(event) and not isinstance(event, type):
                payload = dataclasses.asdict(event)
            elif isinstance(event, dict):
                payload = event
            else:
                payload = {"data": str(event)}

            # Strip bulky 'default' rows from input_schema before publishing.
            # The upstream RealtimeBroadcastingPublisher already pushed the full
            # event (with defaults) to the FE, so the broker topic only needs
            # the structural shape.
            if isinstance(payload, dict) and "input_schema" in payload:
                if payload is event:
                    payload = {**payload, "input_schema": copy.deepcopy(payload["input_schema"])}
                self._strip_input_schema_defaults(payload)

            envelope = {**payload, "_event": topic}

            #Current run id is not useful for consumers, since they need to correlate by root_run_id and fetch the full run metadata (including run_id → root_run_id mapping) themselves. Using root_run_id as the canonical run identifier in the envelope simplifies consumer logic and reduces confusion about which ID to correlate by. The original run_id is still included in the envelope for reference, but root_run_id is the primary key for correlation.
            temp_run_id = envelope.get("run_id", "")
            # Override run_id with root_run_id for top-level context
            root = envelope.get("root_run_id", "")
            if root:
                envelope["run_id"] = root

            # Enrich with run/workflow/node metadata
            size_before_enrich = self._json_size(envelope)
            if self._enricher:
                self._enricher.enrich(envelope)
            size_after_enrich = self._json_size(envelope)

            # Diagnostic: the enricher adds _run/_workflow/_node. Task
            # input/output are intentionally NOT enriched — they are
            # unbounded for upload-style nodes and pushed envelopes past
            # the Event Mesh 900 KB cap. Consumers use task_id to fetch.
            logger.info(
                "Envelope size for topic %s: before_enrich=%d bytes, "
                "after_enrich=%d bytes (delta=%d), top_keys=%s",
                workflow_event_topic,
                size_before_enrich,
                size_after_enrich,
                size_after_enrich - size_before_enrich,
                self._top_key_sizes(envelope),
            )

            max_message_size = 1_048_576  # 1 MB
            if size_after_enrich > max_message_size:
                logger.warning(
                    "Envelope size %d bytes exceeds max message size %d bytes "
                    "for topic %s. Broker will reject — top_keys=%s",
                    size_after_enrich,
                    max_message_size,
                    workflow_event_topic,
                    self._top_key_sizes(envelope),
                )
            
            envelope["run_id"] = temp_run_id

            # Per-run contiguous sequence, keyed by the ROOT run so child-run
            # events share the root's stream (same key precedence as the
            # realtime path). Stamped only on envelopes that actually publish
            # here, so consumers see contiguous 1..N per run and can detect
            # gaps / resequence after Event Mesh redelivery reorders.
            run_key = envelope.get("root_run_id") or temp_run_id or ""
            if run_key:
                envelope["_run_key"] = run_key
                envelope["_run_seq"] = self._next_run_seq(run_key)
                if (
                    topic.lower() in _RUN_STATUS_TOPICS
                    and str(envelope.get("status", "")).lower() in _TERMINAL_RUN_STATUSES
                    and temp_run_id == run_key
                ):
                    # ROOT run reached a terminal status — release the
                    # counter. A child run terminating must not reset the
                    # shared root counter, hence the run_id == run_key guard.
                    self._release_run_seq(run_key)

            # Ensure the envelope fits under the broker limit before publishing.
            self._enforce_size_limit(envelope, workflow_event_topic)

            # Publish to centralized topic or queue
            from app.layer2_application.interfaces.broker_client_interface import IQueueBrokerClient
            if isinstance(self._broker, IQueueBrokerClient):
                self._broker.publish_to_queue(workflow_event_topic, envelope)
            else:
                # Phase M.3.4: key centralized events by run so per-run
                # ordering is preserved end-to-end in Kafka partitions.
                from app.layer4_frameworks.messaging.kafka_event_publisher import (
                    extract_partition_key,
                )
                self._broker.publish_to_topic(
                    workflow_event_topic, envelope,
                    key=extract_partition_key(envelope),
                )

            logger.debug(
                "Published enriched event to centralized topic: %s (size=%d bytes)",
                workflow_event_topic,
                self._json_size(envelope),
            )
        except Exception as exc:
            logger.error("Failed to publish to centralized topic %s: %s", workflow_event_topic, exc)
