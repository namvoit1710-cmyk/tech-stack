"""Tests for EnrichingEventPublisher — ensures 'default' is stripped from input_schema."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging.enriching_event_publisher import EnrichingEventPublisher


def _build_input_schema_with_defaults() -> list[dict]:
    """Build a realistic input_schema containing bulky 'default' row data."""
    row = {"ID": "1", "Name": "Test User"}
    row.update({f"Column{i}": f"val{i:04d}" for i in range(3, 201)})

    return [
        {
            "key": "table_data",
            "outputType": "array",
            "fieldConfig": {
                "fieldWrapper": "TableWrapper",
                "wrapperProps": {"label": "Review data", "isAdd": True, "isDelete": True},
            },
            "fields": [
                {
                    "key": "0",
                    "outputType": "object",
                    "fieldConfig": {
                        "fieldWrapper": "TableRowWrapper",
                        "wrapperProps": {"isAdd": True, "isDelete": True},
                    },
                    "fields": [
                        {
                            "key": col,
                            "outputType": "string",
                            "fieldConfig": {
                                "fieldWrapper": "TableCellWrapper",
                                "wrapperProps": {"label": col},
                                "fieldControl": "InputControl",
                            },
                        }
                        for col in row
                    ],
                }
            ],
            "default": [row] * 10,  # 10 rows of bulky data
        }
    ]


@pytest.fixture()
def envelope() -> dict:
    """Build an envelope dict resembling a DataEditRequested payload."""
    return {
        "event_id": "test-event-id",
        "timestamp": "2026-05-27T12:00:00+00:00",
        "source": "workflow-control-plane",
        "root_run_id": "root-run-1",
        "depth": 1,
        "task_id": "task-1",
        "run_id": "run-1",
        "workflow_id": "wf-1",
        "node_id": "node-1",
        "input_schema": _build_input_schema_with_defaults(),
        "instruction": "Review and edit data",
        "parent_run_id": "root-run-1",
        "source_file_id": "file-1",
        "source_node_id": "artifacts",
        "source_node_name": "",
        # Centralized-topic gate: only runs whose POST /runs metadata declares
        # source=agent are forwarded to WORKFLOW_EVENT_TOPIC.
        "metadata": {"source": "agent"},
    }


@pytest.fixture()
def publisher() -> EnrichingEventPublisher:
    """Build a publisher with mocked inner publisher and broker."""
    inner = MagicMock()
    broker = MagicMock()
    broker.__class__ = type("FakeBroker", (), {})
    return EnrichingEventPublisher(inner=inner, broker_client=broker)


class TestStripDefaultFromInputSchema:
    """Verify that the 'default' key is removed from every input_schema item."""

    def test_envelope_has_default_before_publish(self, envelope: dict) -> None:
        """Sanity-check: the raw payload contains 'default' in input_schema."""
        input_schema = envelope["input_schema"]
        assert any("default" in item for item in input_schema if isinstance(item, dict))

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_default_stripped_after_publish(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher, envelope: dict
    ) -> None:
        """After publish, the broker receives an envelope with no 'default' in input_schema."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        publisher.publish("data_edit.requested", envelope)

        publisher._broker.publish_to_topic.assert_called_once()
        published_envelope = publisher._broker.publish_to_topic.call_args[0][1]

        for item in published_envelope["input_schema"]:
            if isinstance(item, dict):
                assert "default" not in item, (
                    f"'default' was not stripped from input_schema item with key={item.get('key')}"
                )

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_non_default_fields_preserved(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher, envelope: dict
    ) -> None:
        """Stripping 'default' must not remove other fields like 'key', 'fields', etc."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        publisher.publish("data_edit.requested", envelope)

        published_envelope = publisher._broker.publish_to_topic.call_args[0][1]
        schema_item = published_envelope["input_schema"][0]

        assert "key" in schema_item
        assert "outputType" in schema_item
        assert "fields" in schema_item
        assert "fieldConfig" in schema_item

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_envelope_size_reduced_after_strip(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher, envelope: dict
    ) -> None:
        """The envelope size should be smaller after stripping 'default' data."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        size_before = len(json.dumps(envelope))

        publisher.publish("data_edit.requested", envelope)

        published_envelope = publisher._broker.publish_to_topic.call_args[0][1]
        size_after = len(json.dumps(published_envelope))

        assert size_after < size_before

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_inner_publisher_also_receives_stripped_payload(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher, envelope: dict
    ) -> None:
        """The inner publisher (Event Mesh) must also receive stripped payload
        to avoid 'payload too large' errors from the broker."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        publisher.publish("data_edit.requested", envelope)

        publisher._inner.publish.assert_called_once()
        inner_payload = publisher._inner.publish.call_args[0][1]

        # Inner publisher should get a dict (serialized), not the raw event
        assert isinstance(inner_payload, dict)

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_default_stripped_recursively_from_nested_fields(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """Nested ``default`` keys inside ``fields`` arrays must also be stripped.

        Regression: real ``data_edit.requested`` envelopes were 1.2 MB+ (above
        the Event Mesh 900 KB limit) because nested per-column defaults
        survived the top-level-only strip and were dropped by the broker.
        """
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        envelope = {
            "task_id": "task-1",
            "run_id": "run-1",
            "root_run_id": "root-1",
            "node_id": "node-1",
            "metadata": {"source": "agent"},
            "input_schema": [
                {
                    "key": "table_data",
                    "default": [{"id": 1}] * 5,  # top-level default
                    "fields": [
                        {
                            "key": "row_template",
                            "default": [{"id": 1}],  # nested default
                            "fields": [
                                {"key": "col_a", "default": "huge-cell-default-a"},
                                {"key": "col_b", "default": "huge-cell-default-b"},
                            ],
                        }
                    ],
                }
            ],
        }

        publisher.publish("data_edit.requested", envelope)

        published_envelope = publisher._broker.publish_to_topic.call_args[0][1]
        serialized = json.dumps(published_envelope)

        # No 'default' key anywhere in the published envelope's input_schema
        assert '"default"' not in json.dumps(published_envelope["input_schema"]), (
            "Recursive strip failed — nested 'default' keys still present"
        )
        # Structural keys preserved
        assert "fields" in serialized
        assert "col_a" in serialized and "col_b" in serialized

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_default_stripped_recursively_in_inner_payload(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """Inner publisher (Kafka/EventMesh fanout) must also see fully stripped payload."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        envelope = {
            "task_id": "task-1",
            "run_id": "run-1",
            "root_run_id": "root-1",
            "input_schema": [
                {
                    "key": "table",
                    "default": [{"id": 1}],
                    "fields": [
                        {
                            "key": "row",
                            "fields": [{"key": "col", "default": "nested"}],
                        }
                    ],
                }
            ],
        }

        publisher.publish("data_edit.requested", envelope)

        inner_payload = publisher._inner.publish.call_args[0][1]
        assert '"default"' not in json.dumps(inner_payload["input_schema"])

        # 'default' must be stripped from the inner publisher's payload too
        inner_schema = inner_payload.get("input_schema")
        assert inner_schema is not None
        for item in inner_schema:
            if isinstance(item, dict):
                assert "default" not in item, (
                    "Inner publisher (Event Mesh) received unstripped payload — "
                    "this would cause 'payload too large' errors"
                )

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_publish_does_not_mutate_original_dict_event(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher, envelope: dict
    ) -> None:
        """Stripping must not mutate the original event dict."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        original = copy.deepcopy(envelope)

        publisher.publish("data_edit.requested", envelope)

        assert envelope["input_schema"] == original["input_schema"]

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_default_stripped_for_dataclass_event(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher, envelope: dict
    ) -> None:
        """Dataclass events (e.g. DataEditRequested) also get 'default' stripped."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        @dataclass
        class FakeDataEditRequested:
            task_id: str = ""
            run_id: str = ""
            root_run_id: str = ""
            workflow_id: str = ""
            node_id: str = ""
            input_schema: list = field(default_factory=list)
            source_file_id: str = ""
            # Pass the centralized-topic source=agent gate
            metadata: dict = field(default_factory=lambda: {"source": "agent"})

        dc_event = FakeDataEditRequested(
            task_id="task-1",
            run_id="run-1",
            root_run_id="root-1",
            workflow_id="wf-1",
            node_id="node-1",
            input_schema=envelope["input_schema"],
            source_file_id="file-1",
        )

        publisher.publish("data_edit.requested", dc_event)

        # Both inner and centralized topic should be stripped
        publisher._broker.publish_to_topic.assert_called_once()
        centralized = publisher._broker.publish_to_topic.call_args[0][1]
        for item in centralized["input_schema"]:
            if isinstance(item, dict):
                assert "default" not in item

        inner_payload = publisher._inner.publish.call_args[0][1]
        for item in inner_payload["input_schema"]:
            if isinstance(item, dict):
                assert "default" not in item

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_dataclass_event_not_mutated_after_publish(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher, envelope: dict
    ) -> None:
        """The original dataclass event must not be mutated by publish()."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        @dataclass
        class FakeInputRequested:
            task_id: str = ""
            run_id: str = ""
            root_run_id: str = ""
            workflow_id: str = ""
            node_id: str = ""
            input_schema: list = field(default_factory=list)

        dc_event = FakeInputRequested(
            task_id="task-1",
            run_id="run-1",
            root_run_id="root-1",
            workflow_id="wf-1",
            node_id="node-1",
            input_schema=envelope["input_schema"],
        )

        publisher.publish("input.requested", dc_event)

        assert any(
            "default" in item
            for item in dc_event.input_schema
            if isinstance(item, dict)
        ), "Original dataclass event should still have 'default' in input_schema"


class TestDispatchChainCompatibility:
    """Regression: EnrichingEventPublisher now passes a flattened dict to its
    inner publisher (HttpTaskDispatcher / GrpcTaskDispatcher). Both dispatchers
    must extract dispatch fields from that flattened shape — not from a nested
    ``payload`` key — otherwise task.dispatch events never reach the executor.
    """

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_http_dispatcher_receives_flattened_dict_and_submits(
        self, mock_settings: MagicMock
    ) -> None:
        from app.layer1_domain.events import NodeDispatched
        from app.layer4_frameworks.messaging.http_task_dispatcher import HttpTaskDispatcher

        mock_settings.WORKFLOW_EVENT_TOPIC = ""

        kafka_fallback = MagicMock()
        dispatcher = HttpTaskDispatcher(kafka_fallback, "http://worker:8080")
        dispatcher._executor = MagicMock()

        enriching = EnrichingEventPublisher(inner=dispatcher, broker_client=None)

        event = NodeDispatched(
            task_id="t1", run_id="r1", node_id="n1",
            worker_type="http-request", input={"key": "val"},
            node_config={"timeout": 30}, idempotency_key="ik1",
            root_run_id="r1",
        )

        enriching.publish("node.dispatched", event)

        dispatcher._executor.submit.assert_called_once()
        payload = dispatcher._executor.submit.call_args[0][1]
        assert payload["task_id"] == "t1"
        assert payload["worker_type"] == "http-request"
        assert payload["input"] == {"key": "val"}

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_grpc_dispatcher_receives_flattened_dict_and_submits(
        self, mock_settings: MagicMock
    ) -> None:
        pytest.importorskip("grpc", reason="grpcio not installed")
        pytest.importorskip(
            "workflow_proto.v1.task_dispatch_pb2", reason="workflow_proto not installed"
        )
        from app.layer1_domain.events import NodeDispatched
        from app.layer4_frameworks.messaging.grpc_task_dispatcher import GrpcTaskDispatcher

        mock_settings.WORKFLOW_EVENT_TOPIC = ""

        kafka_fallback = MagicMock()
        dispatcher = GrpcTaskDispatcher(kafka_fallback, "localhost:50052")
        dispatcher._executor = MagicMock()

        enriching = EnrichingEventPublisher(inner=dispatcher, broker_client=None)

        event = NodeDispatched(
            task_id="t1", run_id="r1", node_id="n1",
            worker_type="http-request", input={"key": "val"},
            node_config={"timeout": 30}, idempotency_key="ik1",
            root_run_id="r1",
        )

        enriching.publish("node.dispatched", event)

        dispatcher._executor.submit.assert_called_once()
        payload = dispatcher._executor.submit.call_args[0][1]
        assert payload["task_id"] == "t1"
        assert payload["worker_type"] == "http-request"
        assert payload["input"] == {"key": "val"}


def _build_realistic_data_edit_input_schema(default_row_count: int = 50) -> list[dict]:
    """Mirror the shape that ``build_data_edit_table_schema`` produces for
    the DATA_EDIT node — the path that produced the failing 1.2 MB envelope.

    Structure (auto-form table with OData rows):
      [
        {
          "key": "table_data",
          "outputType": "array",
          "fieldConfig": { "fieldWrapper": "OdataWrapper", "wrapperProps": {...} },
          "fields": [
            {                                             # row template
              "key": "0",
              "outputType": "object",
              "fieldConfig": { "fieldWrapper": "TableRowWrapper", ... },
              "fields": [...per-column cells...]
            },
            {...optional extra_settings items, may carry nested 'default'...}
          ],
          "default": [...bulky raw rows...]               # set by build_data_edit_table_schema
        }
      ]
    """
    columns = [f"Column{i}" for i in range(1, 21)]
    row = {col: f"val-{col}-x" * 8 for col in columns}

    cells = [
        {
            "key": col,
            "outputType": "string",
            "fieldConfig": {
                "fieldWrapper": "TableCellWrapper",
                "wrapperProps": {"label": col},
                "fieldControl": "InputControl",
            },
        }
        for col in columns
    ]

    extra_setting_with_nested_default = {
        "key": "metadata",
        "outputType": "object",
        "fieldConfig": {"fieldWrapper": "FormItemWrapper"},
        # Worst case: user-supplied extra_settings can have its own nested
        # default rows. Recursive strip MUST descend into this too.
        "fields": [
            {
                "key": "summary",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "InputControl"},
                "default": "x" * 200,
            }
        ],
        "default": [{"summary": "x" * 200}] * default_row_count,
    }

    return [
        {
            "key": "table_data",
            "outputType": "array",
            "fieldConfig": {
                "fieldWrapper": "OdataWrapper",
                "wrapperProps": {
                    "label": "Review data",
                    "isAdd": True,
                    "isDelete": True,
                },
            },
            "fields": [
                {
                    "key": "0",
                    "outputType": "object",
                    "fieldConfig": {
                        "fieldWrapper": "TableRowWrapper",
                        "wrapperProps": {"isAdd": True, "isDelete": True},
                    },
                    "fields": cells,
                },
                extra_setting_with_nested_default,
            ],
            "default": [row] * default_row_count,
        }
    ]


@dataclass
class _FakeDataEditRequested:
    """Stand-in for the retired ``DataEditRequested`` event.

    The data_edit pending event was folded into ``InputRequested`` (Phase:
    unified pending events), which no longer carries ``input_schema`` — that
    moved to the task's ``output_metadata``. These round-trip tests still
    exercise the publisher's recursive ``default`` strip against a realistic
    data_edit schema, so we keep a local dataclass carrying ``input_schema``,
    mirroring the ``FakeDataEditRequested`` pattern used above.
    """
    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    input_schema: list = field(default_factory=list)
    instruction: str = ""
    parent_run_id: str = ""
    source_file_id: str = ""
    source_node_id: str = ""
    source_node_name: str = ""
    root_run_id: str = ""
    depth: int = 0
    source: str = ""
    # Run-level caller metadata: these round-trip tests model agent-sourced
    # runs, which is what the centralized-topic gate forwards.
    metadata: dict = field(default_factory=lambda: {"source": "agent"})


class TestDataEditRequestedRoundTrip:
    """Regression: DataEditNodeHandler fires DataEditRequested with an
    ``input_schema`` produced by ``build_data_edit_table_schema``. That schema
    carries a bulky ``default`` (the OData rows) at the table level — and
    user-supplied ``extra_settings`` items can carry their own ``default``
    keys at deeper levels. EnrichingEventPublisher must strip every one of
    them before the envelope reaches the broker, or the message exceeds the
    Event Mesh 900 KB cap and is dropped.

    These tests use the real ``DataEditRequested`` dataclass and the real
    auto-form schema shape so any divergence between strip logic and handler
    output trips the suite.
    """

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_handler_event_has_no_default_after_publish(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        input_schema = _build_realistic_data_edit_input_schema()
        event = _FakeDataEditRequested(
            task_id="task-1",
            run_id="run-1",
            workflow_id="wf-1",
            node_id="node-1",
            input_schema=input_schema,
            instruction="Review and edit data",
            parent_run_id="root-1",
            source_file_id="file-1",
            source_node_id="src-node",
            source_node_name="Upstream",
            root_run_id="root-1",
            depth=1,
            source="workflow-control-plane",
        )

        publisher.publish("data_edit.requested", event)

        publisher._broker.publish_to_topic.assert_called_once()
        published = publisher._broker.publish_to_topic.call_args[0][1]

        # No 'default' key anywhere in input_schema, regardless of depth
        serialized_schema = json.dumps(published["input_schema"])
        assert '"default"' not in serialized_schema, (
            "Recursive strip missed a 'default' key — broker would drop this"
        )

        # Inner publisher must see the same stripped payload
        inner_mock: MagicMock = publisher._inner  # type: ignore[assignment]
        inner_mock.publish.assert_called_once()
        inner = inner_mock.publish.call_args[0][1]
        assert '"default"' not in json.dumps(inner["input_schema"])

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_handler_event_structural_fields_preserved(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """Strip removes only ``default`` — every other auto-form key must survive."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        event = _FakeDataEditRequested(
            task_id="task-1",
            run_id="run-1",
            workflow_id="wf-1",
            node_id="node-1",
            input_schema=_build_realistic_data_edit_input_schema(),
            instruction="Review and edit data",
            parent_run_id="root-1",
            source_file_id="file-1",
            root_run_id="root-1",
            source="workflow-control-plane",
        )

        publisher.publish("data_edit.requested", event)

        published = publisher._broker.publish_to_topic.call_args[0][1]
        table = published["input_schema"][0]

        # Table-level structure
        assert table["key"] == "table_data"
        assert table["outputType"] == "array"
        assert table["fieldConfig"]["fieldWrapper"] == "OdataWrapper"

        # Row template + cells survive
        row_template = table["fields"][0]
        assert row_template["key"] == "0"
        assert row_template["fieldConfig"]["fieldWrapper"] == "TableRowWrapper"
        assert len(row_template["fields"]) == 20  # all column cells preserved
        assert {c["key"] for c in row_template["fields"]} == {
            f"Column{i}" for i in range(1, 21)
        }

        # extra_settings item survives (its 'default' is stripped, but the
        # field itself and its nested fields are intact)
        extras = table["fields"][1]
        assert extras["key"] == "metadata"
        assert "default" not in extras
        assert extras["fields"][0]["key"] == "summary"
        assert "default" not in extras["fields"][0]

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_handler_event_envelope_below_event_mesh_cap(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """Realistic event must fit under the 900 KB Event Mesh cap after strip.

        The handler produces input_schemas that, with bulky default rows,
        exceeded 1.2 MB and were dropped. After recursive strip the envelope
        must be well below the broker limit.
        """
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        # Heavy load: 500 rows of 20 columns each — would be > 900 KB raw
        event = _FakeDataEditRequested(
            task_id="task-1",
            run_id="run-1",
            workflow_id="wf-1",
            node_id="node-1",
            input_schema=_build_realistic_data_edit_input_schema(default_row_count=500),
            instruction="Review and edit data",
            parent_run_id="root-1",
            source_file_id="file-1",
            root_run_id="root-1",
            source="workflow-control-plane",
        )

        raw_size = len(json.dumps(event.input_schema))
        publisher.publish("data_edit.requested", event)

        published = publisher._broker.publish_to_topic.call_args[0][1]
        stripped_size = len(json.dumps(published))

        event_mesh_cap = 900_000
        assert raw_size > event_mesh_cap, (
            f"Test fixture too small to be a meaningful regression "
            f"(raw_size={raw_size} bytes, cap={event_mesh_cap})"
        )
        assert stripped_size < event_mesh_cap, (
            f"Stripped envelope still exceeds Event Mesh cap "
            f"({stripped_size} bytes > {event_mesh_cap} bytes)"
        )

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_handler_event_original_dataclass_not_mutated(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """The handler's event object must keep its 'default' rows intact —
        downstream code (Push Gateway, audit log) still reads them."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        original_schema = _build_realistic_data_edit_input_schema()
        snapshot = copy.deepcopy(original_schema)

        event = _FakeDataEditRequested(
            task_id="task-1",
            run_id="run-1",
            workflow_id="wf-1",
            node_id="node-1",
            input_schema=original_schema,
            instruction="Review and edit data",
            parent_run_id="root-1",
            source_file_id="file-1",
            root_run_id="root-1",
            source="workflow-control-plane",
        )

        publisher.publish("data_edit.requested", event)

        assert event.input_schema == snapshot, (
            "EnrichingEventPublisher mutated the caller's input_schema — "
            "downstream consumers will see a stripped event"
        )

class TestCentralizedTopicAgentGate:
    """Only runs whose caller-supplied metadata (POST /runs) declares
    ``"source": "agent"`` are forwarded to the centralized topic. Everything
    else is skipped with a warning; the original-topic publish is unaffected.
    """

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_agent_source_publishes_to_centralized_topic(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"
        envelope = {"run_id": "r1", "metadata": {"source": "agent"}}

        publisher.publish("node.completed", envelope)

        publisher._broker.publish_to_topic.assert_called_once()
        assert publisher._broker.publish_to_topic.call_args[0][0] == "test-workflow-topic"

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_non_agent_source_skips_centralized_topic_quietly(
        self,
        mock_settings: MagicMock,
        publisher: EnrichingEventPublisher,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"
        envelope = {"run_id": "r1", "metadata": {"source": "user"}}

        with caplog.at_level(
            logging.DEBUG,
            logger="app.layer4_frameworks.messaging.enriching_event_publisher",
        ):
            publisher.publish("node.completed", envelope)

        publisher._broker.publish_to_topic.assert_not_called()
        # Original-topic publish still happens
        publisher._inner.publish.assert_called_once()
        skip_records = [
            r for r in caplog.records if "Skipping centralized topic" in r.message
        ]
        assert skip_records, "expected the skip to be logged"
        # Expected gating — logged at DEBUG, never WARNING (avoids per-event spam).
        assert all(r.levelname == "DEBUG" for r in skip_records)

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_missing_metadata_skips_centralized_topic_quietly(
        self,
        mock_settings: MagicMock,
        publisher: EnrichingEventPublisher,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"
        envelope = {"run_id": "r1"}

        with caplog.at_level(
            logging.DEBUG,
            logger="app.layer4_frameworks.messaging.enriching_event_publisher",
        ):
            publisher.publish("node.completed", envelope)

        publisher._broker.publish_to_topic.assert_not_called()
        publisher._inner.publish.assert_called_once()
        skip_records = [
            r for r in caplog.records if "Skipping centralized topic" in r.message
        ]
        assert skip_records, "expected the skip to be logged"
        assert all(r.levelname == "DEBUG" for r in skip_records)

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_empty_metadata_dataclass_event_skips_centralized_topic(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """Real DomainEvents default to metadata={} — those must be skipped."""
        from app.layer1_domain.events import NodeCompleted

        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"
        event = NodeCompleted(task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1")

        publisher.publish("node.completed", event)

        publisher._broker.publish_to_topic.assert_not_called()
        publisher._inner.publish.assert_called_once()

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_agent_metadata_dataclass_event_publishes(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """Stamped DomainEvents (MetadataStampingPublisher) pass the gate."""
        from app.layer1_domain.events import NodeCompleted

        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"
        event = NodeCompleted(
            task_id="t1", run_id="r1", workflow_id="wf-1", node_id="n1",
            metadata={"source": "agent", "agent_id": "a-1"},
        )

        publisher.publish("node.completed", event)

        publisher._broker.publish_to_topic.assert_called_once()
        envelope = publisher._broker.publish_to_topic.call_args[0][1]
        assert envelope["metadata"] == {"source": "agent", "agent_id": "a-1"}

class TestCentralizedRunSequence:
    """Centralized envelopes carry a contiguous per-run ``_run_seq`` keyed by
    the root run (``_run_key``) so external consumers can resequence after
    Event Mesh redelivery reorders delivery."""

    @staticmethod
    def _publish(publisher: EnrichingEventPublisher, topic: str, **fields) -> dict:
        envelope = {"metadata": {"source": "agent"}, **fields}
        publisher.publish(topic, envelope)
        return publisher._broker.publish_to_topic.call_args[0][1]

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_sequence_is_contiguous_per_run(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        seqs = [
            self._publish(publisher, "node.completed", run_id="r1")["_run_seq"]
            for _ in range(3)
        ]

        assert seqs == [1, 2, 3]
        assert publisher._broker.publish_to_topic.call_args[0][1]["_run_key"] == "r1"

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_child_run_events_share_root_counter(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        root = self._publish(publisher, "node.completed", run_id="r-root")
        child = self._publish(
            publisher, "node.completed", run_id="r-child", root_run_id="r-root"
        )

        assert root["_run_seq"] == 1
        assert child["_run_seq"] == 2
        assert child["_run_key"] == "r-root"

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_independent_runs_have_independent_counters(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        a1 = self._publish(publisher, "node.completed", run_id="run-a")
        b1 = self._publish(publisher, "node.completed", run_id="run-b")
        a2 = self._publish(publisher, "node.completed", run_id="run-a")

        assert (a1["_run_seq"], b1["_run_seq"], a2["_run_seq"]) == (1, 1, 2)

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_terminal_root_run_status_releases_counter(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        self._publish(publisher, "node.completed", run_id="r1")
        terminal = self._publish(
            publisher, "run.status", run_id="r1", status="COMPLETED"
        )

        assert terminal["_run_seq"] == 2
        assert "r1" not in publisher._run_seq

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_child_terminal_status_does_not_release_root_counter(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        self._publish(publisher, "node.completed", run_id="r-root")
        self._publish(
            publisher, "run.status",
            run_id="r-child", root_run_id="r-root", status="COMPLETED",
        )

        assert publisher._run_seq.get("r-root") == 2

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_skipped_non_agent_events_do_not_consume_sequence(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """The gate runs before stamping — a skipped run never starts a counter."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        self._publish(publisher, "node.completed", run_id="r-agent")
        publisher.publish("node.completed", {"run_id": "r-ui"})  # gated out
        second = self._publish(publisher, "node.completed", run_id="r-agent")

        assert second["_run_seq"] == 2
        assert "r-ui" not in publisher._run_seq

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_run_seq_survives_size_enforcement(
        self, mock_settings: MagicMock, publisher: EnrichingEventPublisher
    ) -> None:
        """_run_seq/_run_key are protected keys — never dropped while shrinking."""
        mock_settings.WORKFLOW_EVENT_TOPIC = "test-workflow-topic"

        published = self._publish(
            publisher, "node.completed",
            run_id="r1", expression="x" * 2_000_000,
        )

        assert published["expression"].get("_omitted") is True
        assert published["_run_seq"] == 1
        assert published["_run_key"] == "r1"
