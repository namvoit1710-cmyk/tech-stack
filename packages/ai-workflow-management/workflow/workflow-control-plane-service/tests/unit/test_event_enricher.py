"""Contract tests for CentralizedEventEnricher.

Every node-scoped event published to the centralized workflow topic
(``WORKFLOW_EVENT_TOPIC``) must carry ``_node`` metadata with the node
name and node type so external consumers can identify the node without
extra API calls.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.events import (
    NodeCancelled,
    NodeCompleted,
    NodeDispatched,
    NodeFailed,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer4_frameworks.messaging.enriching_event_publisher import EnrichingEventPublisher
from app.layer4_frameworks.messaging.event_enricher import CentralizedEventEnricher

CENTRALIZED_TOPIC = "orchestrator.workflow_event"

# Caller-supplied run metadata that passes the centralized-topic gate
# (EnrichingEventPublisher forwards only metadata.source == "agent").
_AGENT_META = {"source": "agent"}


def _build_workflow() -> Workflow:
    node = WorkflowNode(
        id="n1",
        workflow_id="n1",
        name="Fetch Orders",
        node_type=NodeType.TASK,
        worker_type="http-request",
        description="fetches orders",
    )
    return Workflow(id="wf-1", workflow_id="wf-1", name="My WF", nodes=[node])


def _build_publisher(workflow=None, workflow_repo=None, run_repo=None) -> EnrichingEventPublisher:
    if workflow_repo is None:
        workflow_repo = MagicMock()
        workflow_repo.get_by_id.return_value = workflow
    if run_repo is None:
        run_repo = MagicMock()
        run_repo.get_by_id.return_value = None
    enricher = CentralizedEventEnricher(workflow_repo=workflow_repo, run_repo=run_repo)
    return EnrichingEventPublisher(
        inner=MagicMock(),
        broker_client=MagicMock(),
        enricher=enricher,
    )


def _published_envelope(publisher: EnrichingEventPublisher) -> dict:
    publisher._broker.publish_to_topic.assert_called_once()
    args = publisher._broker.publish_to_topic.call_args[0]
    assert args[0] == CENTRALIZED_TOPIC
    return args[1]


_NODE_EVENTS = [
    NodeDispatched(
        task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
        node_name="Fetch Orders", worker_type="http-request",
        metadata=_AGENT_META,
    ),
    NodeCompleted(
        task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
        metadata=_AGENT_META,
    ),
    NodeFailed(
        task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
        error_message="boom", metadata=_AGENT_META,
    ),
    NodeCancelled(
        task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
        reason="skipped", metadata=_AGENT_META,
    ),
]


class TestNodeMetadataContract:
    """Centralized envelopes must contain _node.name and _node.node_type."""

    @pytest.mark.parametrize("event", _NODE_EVENTS, ids=lambda e: type(e).__name__)
    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_envelope_contains_node_name_and_type(self, mock_settings: MagicMock, event) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = CENTRALIZED_TOPIC
        publisher = _build_publisher(workflow=_build_workflow())

        publisher.publish("node.event", event)

        envelope = _published_envelope(publisher)
        assert envelope["_node"]["name"] == "Fetch Orders"
        assert envelope["_node"]["node_type"] == "TASK"
        assert envelope["_node"]["worker_type"] == "http-request"

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_node_block_survives_size_enforcement(self, mock_settings: MagicMock) -> None:
        """_node is a protected key: oversized envelopes keep it after shrinking."""
        mock_settings.WORKFLOW_EVENT_TOPIC = CENTRALIZED_TOPIC
        publisher = _build_publisher(workflow=_build_workflow())
        event = NodeCompleted(
            task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
            # Bulky non-protected field that forces the shrink loop to run.
            expression="x" * 2_000_000,
            metadata=_AGENT_META,
        )

        publisher.publish("node.completed", event)

        envelope = _published_envelope(publisher)
        assert envelope["expression"].get("_omitted") is True
        assert envelope["_node"]["name"] == "Fetch Orders"
        assert envelope["_node"]["node_type"] == "TASK"


class TestRootWorkflowIdContract:
    """Centralized envelopes must carry top-level root_workflow_id."""

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_root_run_envelope_carries_root_workflow_id(self, mock_settings: MagicMock) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = CENTRALIZED_TOPIC
        run_repo = MagicMock()
        run_repo.get_by_id.return_value = WorkflowRun(id="r1", workflow_id="wf-1")
        publisher = _build_publisher(workflow=_build_workflow(), run_repo=run_repo)
        event = NodeCompleted(
            task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
            metadata=_AGENT_META,
        )

        publisher.publish("node.completed", event)

        envelope = _published_envelope(publisher)
        assert envelope["root_workflow_id"] == "wf-1"

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_child_run_envelope_carries_root_workflow_id(self, mock_settings: MagicMock) -> None:
        """Child-run events resolve root_workflow_id from the ROOT run, while
        workflow_id stays the child workflow's id."""
        mock_settings.WORKFLOW_EVENT_TOPIC = CENTRALIZED_TOPIC
        run_repo = MagicMock()
        run_repo.get_by_id.return_value = WorkflowRun(id="r-root", workflow_id="wf-root")
        publisher = _build_publisher(workflow=_build_workflow(), run_repo=run_repo)
        event = NodeCompleted(
            task_id="t1", run_id="r-child", root_run_id="r-root",
            workflow_id="wf-child", node_id="n1", metadata=_AGENT_META,
        )

        publisher.publish("node.completed", event)

        envelope = _published_envelope(publisher)
        # The publisher overrides run_id with root_run_id, so the run repo is
        # asked for the root run and its workflow_id wins.
        run_repo.get_by_id.assert_called_once_with("r-root")
        assert envelope["root_workflow_id"] == "wf-root"
        assert envelope["workflow_id"] == "wf-child"

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_root_workflow_id_survives_size_enforcement(self, mock_settings: MagicMock) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = CENTRALIZED_TOPIC
        run_repo = MagicMock()
        run_repo.get_by_id.return_value = WorkflowRun(id="r1", workflow_id="wf-1")
        publisher = _build_publisher(workflow=_build_workflow(), run_repo=run_repo)
        event = NodeCompleted(
            task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
            expression="x" * 2_000_000, metadata=_AGENT_META,
        )

        publisher.publish("node.completed", event)

        envelope = _published_envelope(publisher)
        assert envelope["expression"].get("_omitted") is True
        assert envelope["root_workflow_id"] == "wf-1"


class TestEnrichmentMissVisibility:
    """A missing _node must be loud (warning), and never block publishing."""

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_unknown_node_warns_and_still_publishes(
        self, mock_settings: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = CENTRALIZED_TOPIC
        publisher = _build_publisher(workflow=_build_workflow())
        event = NodeCompleted(
            task_id="t1", run_id="r1", workflow_id="wf-1", node_id="ghost",
            metadata=_AGENT_META,
        )

        with caplog.at_level(logging.WARNING, logger="app.layer4_frameworks.messaging.event_enricher"):
            publisher.publish("node.completed", event)

        envelope = _published_envelope(publisher)
        assert "_node" not in envelope
        assert any("Node enrichment skipped" in r.message for r in caplog.records)

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_workflow_lookup_failure_warns_and_still_publishes(
        self, mock_settings: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = CENTRALIZED_TOPIC
        workflow_repo = MagicMock()
        workflow_repo.get_by_id.side_effect = RuntimeError("db down")
        publisher = _build_publisher(workflow_repo=workflow_repo)
        event = NodeCompleted(
            task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
            metadata=_AGENT_META,
        )

        with caplog.at_level(logging.WARNING, logger="app.layer4_frameworks.messaging.event_enricher"):
            publisher.publish("node.completed", event)

        envelope = _published_envelope(publisher)
        assert "_node" not in envelope
        assert any("Workflow enrichment failed" in r.message for r in caplog.records)
