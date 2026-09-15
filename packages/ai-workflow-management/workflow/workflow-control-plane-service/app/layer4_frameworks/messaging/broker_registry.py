"""Broker registry — route call sites to a specific broker by "purpose".

Background
----------

The control plane talks to a message broker for several distinct
purposes (orchestration queue, realtime events, per-instance response
topics, workflow event audit queue). Historically a single
``infra.broker`` served all of them.

When the primary broker fills its quota — common on shared SAP BTP
Event Mesh service plans — we want to relieve pressure by routing
specific purposes to a second broker without changing every call site.

The registry is the one configuration point for that mapping:

    registry = BrokerRegistry(
        primary=infra.broker,
        secondary=infra.broker_secondary,   # may be None
        purpose_routes={
            "response_topics":  "secondary",   # M.3.3 per-instance queues
            "orchestration":    "primary",     # default
            # ... add more purposes as needed
        },
    )

    broker = registry.get("response_topics")  # → secondary if configured

When ``secondary`` is ``None`` (single-broker deployments, mock mode,
tests), every ``.get(...)`` returns the primary. So adding new purposes
is safe — they default to the primary broker until ops decides to
split traffic.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

_logger = logging.getLogger(__name__)


class BrokerRegistry:
    """Maps a logical purpose name to an ``IBrokerClient`` instance.

    Two-broker case is the only one the codebase exercises today; the
    interface generalises to N brokers if we ever need it.
    """

    def __init__(
        self,
        primary: Optional[Any],
        secondary: Optional[Any] = None,
        purpose_routes: Optional[dict[str, str]] = None,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        # Logical purpose → which broker label to use ("primary" or "secondary").
        # Unknown / unmapped purposes fall through to the primary.
        self._routes: dict[str, str] = dict(purpose_routes or {})

    def get(self, purpose: str) -> Optional[Any]:
        """Return the broker for ``purpose``, falling back to primary.

        Returns ``None`` only when the chosen broker is itself unavailable
        (e.g. mock mode where both primary and secondary are ``None``).
        """
        label = self._routes.get(purpose, "primary")
        if label == "secondary":
            if self._secondary is not None:
                return self._secondary
            _logger.debug(
                "BrokerRegistry: purpose %r routed to 'secondary' but no "
                "secondary broker is configured — using primary",
                purpose,
            )
        return self._primary

    @property
    def primary(self) -> Optional[Any]:
        return self._primary

    @property
    def secondary(self) -> Optional[Any]:
        return self._secondary

    @property
    def has_secondary(self) -> bool:
        return self._secondary is not None

    def routes_summary(self) -> dict[str, str]:
        """Human-readable snapshot of the configured routing rules."""
        return {p: ("secondary" if label == "secondary" and self._secondary else "primary")
                for p, label in self._routes.items()}


# Canonical purpose-name constants — keep call sites stable and grep-able.
class BrokerPurpose:
    """Purpose names used by the codebase. Add new entries as the
    routing table grows.
    """

    RESPONSE_TOPICS = "response_topics"        # M.3.3 per-instance response listener
    ORCHESTRATION = "orchestration"            # main orchestration.actions topic
    REALTIME = "realtime"                       # Socket.IO realtime events
    WORKFLOW_EVENT_QUEUE = "workflow_event_queue"  # audit event sink
