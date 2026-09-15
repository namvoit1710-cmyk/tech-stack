"""BrokerRegistry routing rules + secondary fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.layer4_frameworks.messaging.broker_registry import (
    BrokerPurpose,
    BrokerRegistry,
)


class TestRoutingRules:
    def test_purpose_routed_to_secondary_returns_secondary(self):
        primary = MagicMock(name="primary")
        secondary = MagicMock(name="secondary")
        reg = BrokerRegistry(
            primary=primary,
            secondary=secondary,
            purpose_routes={BrokerPurpose.RESPONSE_TOPICS: "secondary"},
        )
        assert reg.get(BrokerPurpose.RESPONSE_TOPICS) is secondary

    def test_purpose_routed_to_primary_returns_primary(self):
        primary = MagicMock(name="primary")
        secondary = MagicMock(name="secondary")
        reg = BrokerRegistry(
            primary=primary,
            secondary=secondary,
            purpose_routes={BrokerPurpose.ORCHESTRATION: "primary"},
        )
        assert reg.get(BrokerPurpose.ORCHESTRATION) is primary

    def test_unknown_purpose_falls_back_to_primary(self):
        primary = MagicMock(name="primary")
        secondary = MagicMock(name="secondary")
        reg = BrokerRegistry(primary=primary, secondary=secondary, purpose_routes={})
        assert reg.get("not_configured") is primary


class TestSecondaryFallback:
    def test_secondary_routed_falls_back_to_primary_when_secondary_none(self):
        """When secondary is None (single-broker deployment), purposes
        routed to 'secondary' transparently fall back to primary."""
        primary = MagicMock(name="primary")
        reg = BrokerRegistry(
            primary=primary,
            secondary=None,
            purpose_routes={BrokerPurpose.RESPONSE_TOPICS: "secondary"},
        )
        assert reg.get(BrokerPurpose.RESPONSE_TOPICS) is primary

    def test_has_secondary_reflects_actual_secondary(self):
        primary = MagicMock(name="primary")
        assert BrokerRegistry(primary, None).has_secondary is False
        assert BrokerRegistry(primary, MagicMock()).has_secondary is True

    def test_both_none_returns_none(self):
        """Mock-mode / test fixtures with no broker at all."""
        reg = BrokerRegistry(primary=None, secondary=None)
        assert reg.get(BrokerPurpose.RESPONSE_TOPICS) is None


class TestRoutesSummary:
    def test_summary_shows_effective_routes(self):
        primary = MagicMock(name="primary")
        secondary = MagicMock(name="secondary")
        reg = BrokerRegistry(
            primary=primary,
            secondary=secondary,
            purpose_routes={
                BrokerPurpose.RESPONSE_TOPICS: "secondary",
                BrokerPurpose.ORCHESTRATION: "primary",
            },
        )
        summary = reg.routes_summary()
        assert summary[BrokerPurpose.RESPONSE_TOPICS] == "secondary"
        assert summary[BrokerPurpose.ORCHESTRATION] == "primary"

    def test_summary_collapses_to_primary_when_secondary_unavailable(self):
        """Routes claim 'secondary' but secondary is None — summary
        reflects the EFFECTIVE routing, not the configured one."""
        primary = MagicMock(name="primary")
        reg = BrokerRegistry(
            primary=primary,
            secondary=None,
            purpose_routes={BrokerPurpose.RESPONSE_TOPICS: "secondary"},
        )
        assert reg.routes_summary()[BrokerPurpose.RESPONSE_TOPICS] == "primary"


class TestBrokerPurposeConstants:
    def test_canonical_purpose_names_exist(self):
        """Constants stay stable so call sites stay grep-able."""
        assert BrokerPurpose.RESPONSE_TOPICS == "response_topics"
        assert BrokerPurpose.ORCHESTRATION == "orchestration"
        assert BrokerPurpose.REALTIME == "realtime"
        assert BrokerPurpose.WORKFLOW_EVENT_QUEUE == "workflow_event_queue"
