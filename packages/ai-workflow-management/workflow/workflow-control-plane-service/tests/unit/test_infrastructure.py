"""Unit tests for infrastructure adapters: logging, monitoring, realtime, persistence, messaging."""

from __future__ import annotations

import dataclasses
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.stage_record import StageRecord
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.logger.app_logger import StandardLogger, _safe_extra
from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository
from app.layer4_frameworks.messaging.callback_publisher import CallbackPublisher
from app.layer4_frameworks.messaging.kafka_event_publisher import KafkaEventPublisher


# ────────────── StandardLogger ──────────────


class TestStandardLogger:
    def test_info(self):
        logger = StandardLogger()
        logger.info("hello %s", "world", key="val")

    def test_error(self):
        logger = StandardLogger()
        logger.error("error message", key="val")

    def test_warning(self):
        logger = StandardLogger()
        logger.warning("warning message")

    def test_debug(self):
        logger = StandardLogger()
        logger.debug("debug message")

    def test_info_no_kwargs(self):
        logger = StandardLogger()
        logger.info("simple message")


class TestSafeExtra:
    def test_empty_kwargs(self):
        assert _safe_extra({}) is None

    def test_with_keys(self):
        result = _safe_extra({"foo": "bar", "baz": 42})
        assert result == {"ctx_foo": "bar", "ctx_baz": 42}


# ────────────── PrometheusMonitor ──────────────


class TestPrometheusMonitor:
    def test_track(self):
        """PrometheusMonitor.track() creates a gauge and sets its value."""
        monitor = PrometheusMonitor()
        monitor.track("latency", 1.5)
        # Gauge should be created in the registry
        assert "latency" in monitor._gauges

    def test_increment(self):
        """PrometheusMonitor.increment() creates a counter and increments it."""
        monitor = PrometheusMonitor()
        monitor.increment("counter", tags={"env": "test"})
        # Counter should be created in the registry
        assert "counter" in monitor._counters


# ────────────── MemoryRunRepository ──────────────


class TestMemoryRunRepo:
    def test_save_and_get(self):
        repo = MemoryRunRepository()
        run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
        repo.save(run)
        assert repo.get_by_id("r1") is run

    def test_get_missing(self):
        repo = MemoryRunRepository()
        assert repo.get_by_id("missing") is None

    def test_update_status(self):
        repo = MemoryRunRepository()
        run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
        repo.save(run)
        repo.update_status("r1", RunStatus.COMPLETED)
        assert repo.get_by_id("r1").status == RunStatus.COMPLETED

    def test_update_status_missing(self):
        repo = MemoryRunRepository()
        repo.update_status("missing", RunStatus.FAILED)
        # No error

    def test_list_by_workflow(self):
        repo = MemoryRunRepository()
        run1 = WorkflowRun(id="r1", workflow_id="wf-1")
        run2 = WorkflowRun(id="r2", workflow_id="wf-1")
        run3 = WorkflowRun(id="r3", workflow_id="wf-2")
        repo.save(run1)
        repo.save(run2)
        repo.save(run3)
        result = repo.list_by_workflow("wf-1")
        assert len(result) == 2

    def test_list_by_workflow_empty(self):
        repo = MemoryRunRepository()
        assert repo.list_by_workflow("wf-x") == []


# ────────────── MemoryTaskRepository ──────────────


class TestMemoryTaskRepo:
    def test_save_and_get(self):
        repo = MemoryTaskRepository()
        task = Task(id="t1", run_id="r1", node_id="n1")
        repo.save(task)
        assert repo.get_by_id("t1") is task

    def test_get_missing(self):
        repo = MemoryTaskRepository()
        assert repo.get_by_id("missing") is None

    def test_list_by_run(self):
        repo = MemoryTaskRepository()
        t1 = Task(id="t1", run_id="r1", node_id="n1")
        t2 = Task(id="t2", run_id="r1", node_id="n2")
        t3 = Task(id="t3", run_id="r2", node_id="n1")
        repo.save(t1)
        repo.save(t2)
        repo.save(t3)
        result = repo.list_by_run("r1")
        assert len(result) == 2

    def test_update_status(self):
        repo = MemoryTaskRepository()
        task = Task(id="t1", run_id="r1", node_id="n1", status=TaskStatus.READY)
        repo.save(task)
        repo.update_status("t1", TaskStatus.RUNNING)
        assert repo.get_by_id("t1").status == TaskStatus.RUNNING

    def test_update_status_missing(self):
        repo = MemoryTaskRepository()
        repo.update_status("missing", TaskStatus.FAILED)
        # No error


# ────────────── MemoryStageHistoryRepository ──────────────


class TestMemoryStageHistoryRepo:
    def test_save_and_list_by_run(self):
        repo = MemoryStageHistoryRepository()
        record = StageRecord(run_id="r1", event_type="run.started")
        repo.save(record)
        result = repo.list_by_run("r1")
        assert len(result) == 1
        assert result[0].run_id == "r1"

    def test_list_by_run_empty(self):
        repo = MemoryStageHistoryRepository()
        assert repo.list_by_run("missing") == []

    def test_list_by_task(self):
        repo = MemoryStageHistoryRepository()
        r1 = StageRecord(run_id="r1", task_id="t1", event_type="node.completed")
        r2 = StageRecord(run_id="r1", task_id="t2", event_type="node.completed")
        repo.save(r1)
        repo.save(r2)
        result = repo.list_by_task("t1")
        assert len(result) == 1

    def test_list_by_task_empty(self):
        repo = MemoryStageHistoryRepository()
        assert repo.list_by_task("missing") == []


# ────────────── CallbackPublisher ──────────────


class TestCallbackPublisher:
    def test_publish_success(self):
        broker = MagicMock()
        publisher = CallbackPublisher(broker)
        publisher.publish("topic.callback", {"correlation_id": "c1", "status": "COMPLETED"}, key="c1")
        broker.publish_to_topic.assert_called_once_with("topic.callback", {"correlation_id": "c1", "status": "COMPLETED"}, key="c1")

    def test_publish_failure(self):
        broker = MagicMock()
        broker.publish_to_topic.side_effect = Exception("connection error")
        publisher = CallbackPublisher(broker)
        # Should not raise
        publisher.publish("topic.callback", {"correlation_id": "c1"})


# ────────────── KafkaEventPublisher ──────────────


class TestKafkaEventPublisher:
    def test_publish_dataclass(self):
        @dataclasses.dataclass
        class TestEvent:
            run_id: str = "r1"
            status: str = "ok"

        client = MagicMock()
        publisher = KafkaEventPublisher(client)
        publisher.publish("test.topic", TestEvent())
        # Phase M.3.4: per-run partition key threaded through from run_id.
        client.publish_to_topic.assert_called_once_with(
            "test.topic", {"run_id": "r1", "status": "ok"}, key="r1",
        )

    def test_publish_dict(self):
        client = MagicMock()
        publisher = KafkaEventPublisher(client)
        publisher.publish("test.topic", {"key": "val"})
        # No run/workflow/root_run id in the payload → None key (round-robin).
        client.publish_to_topic.assert_called_once_with(
            "test.topic", {"key": "val"}, key=None,
        )

    def test_publish_non_dict(self):
        client = MagicMock()
        publisher = KafkaEventPublisher(client)
        publisher.publish("test.topic", "hello")
        client.publish_to_topic.assert_called_once_with(
            "test.topic", {"data": "hello"}, key=None,
        )

    @patch("app.layer4_frameworks.messaging.enriching_event_publisher.settings")
    def test_enriching_publisher_centralized_topic(self, mock_settings):
        """Centralized topic publishing is now handled by EnrichingEventPublisher."""
        from app.layer4_frameworks.messaging.enriching_event_publisher import EnrichingEventPublisher
        mock_settings.WORKFLOW_EVENT_TOPIC = "orchestrator.workflow_event"

        @dataclasses.dataclass
        class TestEvent:
            run_id: str = "child-run-uuid"
            root_run_id: str = "root-run-uuid"
            status: str = "ok"
            # Pass the centralized-topic source=agent gate
            metadata: dict = dataclasses.field(
                default_factory=lambda: {"source": "agent"}
            )

        inner_client = MagicMock()
        inner_publisher = KafkaEventPublisher(inner_client)
        broker = MagicMock()

        enriching = EnrichingEventPublisher(inner=inner_publisher, broker_client=broker)
        enriching.publish("test.topic", TestEvent())

        # Inner publisher: original topic. Phase M.3.4: event publisher
        # threads the partition key, preferring root_run_id for child runs.
        inner_client.publish_to_topic.assert_called_once_with(
            "test.topic",
            {
                "run_id": "child-run-uuid",
                "root_run_id": "root-run-uuid",
                "status": "ok",
                "metadata": {"source": "agent"},
            },
            key="root-run-uuid",
        )
        # Broker: centralized topic keeps the original run_id (root_run_id is
        # the correlation key) and stamps the per-run sequence on the root key.
        broker.publish_to_topic.assert_called_once()
        envelope = broker.publish_to_topic.call_args[0][1]
        assert envelope["run_id"] == "child-run-uuid"
        assert envelope["root_run_id"] == "root-run-uuid"
        assert envelope["_event"] == "test.topic"
        assert envelope["_run_key"] == "root-run-uuid"
        assert envelope["_run_seq"] == 1


# ────────────── HttpWorkerRegistryClient ──────────────


class TestHttpWorkerRegistryClient:
    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_list_workers_success_list(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": "w1"}]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.list_workers()
        assert result == [{"id": "w1"}]

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_list_workers_success_dict(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"workers": [{"id": "w1"}]}
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.list_workers()
        assert result == [{"id": "w1"}]

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_list_workers_failure(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("timeout")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.list_workers()
        assert result == []

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_get_worker_success(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "w1", "name": "worker"}
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.get_worker("w1")
        assert result == {"id": "w1", "name": "worker"}

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_get_worker_not_found(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "not found"}
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.get_worker("w999")
        assert result is None

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_get_worker_failure(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("error")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.get_worker("w1")
        assert result is None

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_get_worker_schema_success(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"input_schema": {}, "output_schema": {}}
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.get_worker_schema("w1")
        assert result is not None

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_get_worker_schema_error(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "not found"}
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.get_worker_schema("w999")
        assert result is None

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_discover_workers_list(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": "w1"}]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.discover_workers("nlp")
        assert result == [{"id": "w1"}]

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_discover_workers_failure(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("error")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.discover_workers("nlp")
        assert result == []

    @patch("app.layer4_frameworks.clients.http_worker_registry_client.httpx.Client")
    def test_discover_workers_non_list(self, mock_client_cls):
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": "unknown"}
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = HttpWorkerRegistryClient("http://workers:8080")
        result = client.discover_workers("nlp")
        assert result == []


# ────────────── GetOutput (covers line 32) ──────────────


class TestGetOutputWithRun:
    @pytest.mark.asyncio
    async def test_get_output_found(self):
        from app.layer2_application.features.get_output.use_cases.get_output_use_case import (
            GetOutputCommand, GetOutputUseCase,
        )
        from tests.conftest import DummyLogger

        run_repo = MemoryRunRepository()
        run = WorkflowRun(id="r1", workflow_id="wf-1", output_data={"result": 42})
        run_repo.save(run)

        uc = GetOutputUseCase(logger=DummyLogger(), run_repo=run_repo)
        result = await uc.execute(GetOutputCommand(run_id="r1"))

        assert result.run_id == "r1"
        assert result.output_data == {"result": 42}
        assert result.error == ""


# ────────────── StageRecorder ──────────────


class TestStageRecorder:
    def test_record(self):
        from app.layer2_application.events.stage_recorder import StageRecorder

        repo = MemoryStageHistoryRepository()
        recorder = StageRecorder(repository=repo)
        record = recorder.record(
            run_id="r1",
            event_type="run.started",
            from_status="PENDING",
            to_status="RUNNING",
            node_id="n1",
            task_id="t1",
            metadata={"key": "val"},
        )
        assert record.run_id == "r1"
        assert record.event_type == "run.started"
        assert len(repo.list_by_run("r1")) == 1

    def test_record_minimal(self):
        from app.layer2_application.events.stage_recorder import StageRecorder

        repo = MemoryStageHistoryRepository()
        recorder = StageRecorder(repository=repo)
        record = recorder.record(
            run_id="r1",
            event_type="run.failed",
            from_status="RUNNING",
            to_status="FAILED",
        )
        assert record.metadata == {}
        assert record.node_id is None


# ────────────── TaskFactory ──────────────


class TestTaskFactory:
    def test_create_task(self):
        from app.layer2_application.orchestration.task_factory import TaskFactory
        from app.layer1_domain.entities.workflow import WorkflowNode
        from app.layer1_domain.entities.workflow_run import WorkflowRun

        factory = TaskFactory()
        node = WorkflowNode(id="n1", worker_type="w1")
        run = WorkflowRun(id="run-1")

        task = factory.create_task(node, run)
        assert task.id == "run-1-n1"
        assert task.node_id == "n1"
        assert task.worker_type == "w1"
        assert task.status == TaskStatus.READY

    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    def test_create_task_with_iteration(self):
        from app.layer2_application.orchestration.task_factory import TaskFactory
        from app.layer1_domain.entities.workflow import WorkflowNode
        from app.layer1_domain.entities.workflow_run import WorkflowRun

        factory = TaskFactory()
        node = WorkflowNode(id="n1", worker_type="w1")
        run = WorkflowRun(id="run-1")

        task = factory.create_task(
            node, run,
            iteration_index=3,
            loop_context={"index": 3, "item": "c"},
            resolved_input={"key": "val"},
        )
        assert task.id == "run-1-n1-iter-3"
        assert task.iteration_index == 3
        assert task.loop_context == {"index": 3, "item": "c"}
        assert task.resolved_input == {"key": "val"}


# ────────────── TopologyBuilder ──────────────


class TestWorkflowEntity:
    def test_get_node(self):
        from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
        wf = Workflow(id="wf", nodes=[WorkflowNode(id="n1", name="N1")])
        assert wf.get_node("n1").name == "N1"
        assert wf.get_node("missing") is None

    def test_get_outgoing_edges(self):
        from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
        wf = Workflow(
            id="wf",
            nodes=[WorkflowNode(id="A"), WorkflowNode(id="B")],
            edges=[WorkflowEdge(source_node_id="A", target_node_id="B")],
        )
        edges = wf.get_outgoing_edges("A")
        assert len(edges) == 1
        assert edges[0].target_node_id == "B"
        assert wf.get_outgoing_edges("B") == []

    def test_get_incoming_edges(self):
        from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
        wf = Workflow(
            id="wf",
            nodes=[WorkflowNode(id="A"), WorkflowNode(id="B")],
            edges=[WorkflowEdge(source_node_id="A", target_node_id="B")],
        )
        edges = wf.get_incoming_edges("B")
        assert len(edges) == 1
        assert edges[0].source_node_id == "A"
        assert wf.get_incoming_edges("A") == []

    def test_node_type_string_coercion(self):
        from app.layer1_domain.entities.workflow import WorkflowNode
        from app.layer1_domain.value_objects.node_type import NodeType
        node = WorkflowNode(id="n1", node_type="TASK")
        assert node.node_type == NodeType.TASK


class TestTopologyBuilder:
    def test_prepare(self):
        from app.layer1_domain.entities.workflow_run import GraphTopology
        from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
        from app.layer1_domain.value_objects.node_type import NodeType

        wf = Workflow(
            id="wf",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TRIGGER),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="B"),
                WorkflowEdge(source_node_id="B", target_node_id="C"),
            ],
        )

        dag = GraphTopology.from_workflow(wf)

        assert dag.entry_nodes == ["A"]
        assert dag.leaf_nodes == ["C"]
        assert "B" in dag.adjacency["A"]
        assert "A" in dag.predecessors["B"]
        assert len(dag.edge_map["A"]) == 1
