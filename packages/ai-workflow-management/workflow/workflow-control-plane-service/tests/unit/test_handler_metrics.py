"""Unit tests for Phase 2 handler / resolver instrumentation.

Covers:
* ``InMemoryMonitor`` basic observation + query helpers.
* ``NodeHandlerShape`` annotations on every built-in handler.
* ``VariableResolver`` emits ``variable_resolve_duration_seconds`` with a
  strategy label and outcome on each try_resolve call.
* ``invoke_handler`` helper emits ``node_handler_duration_seconds`` with
  handler_shape + node_type + status tags on both success and failure.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import (
    NodeHandlerResult,
    NodeHandlerShape,
    invoke_handler,
)
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.data_edit_handler import DataEditNodeHandler
from app.layer2_application.node_handlers.file_upload_handler import FileUploadNodeHandler
from app.layer2_application.node_handlers.human_action_handler import HumanActionNodeHandler
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.parallel_handler import ParallelNodeHandler
from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects import NodeType
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.monitoring.in_memory import (
    CounterObservation,
    HistogramObservation,
    InMemoryMonitor,
)


# ────────────── InMemoryMonitor basics ──────────────


class TestInMemoryMonitor:
    def test_histogram_records_metric_value_and_tags(self):
        monitor = InMemoryMonitor()
        monitor.histogram("duration", 0.123, tags={"kind": "a"})
        monitor.histogram("duration", 0.456, tags={"kind": "b"})

        obs = monitor.histogram_observations("duration")
        assert len(obs) == 2
        assert obs[0] == HistogramObservation(metric="duration", value=0.123, tags={"kind": "a"})
        assert obs[1] == HistogramObservation(metric="duration", value=0.456, tags={"kind": "b"})

    def test_histogram_count_filters_by_tags(self):
        monitor = InMemoryMonitor()
        monitor.histogram("d", 1.0, tags={"a": "x", "b": "1"})
        monitor.histogram("d", 2.0, tags={"a": "x", "b": "2"})
        monitor.histogram("d", 3.0, tags={"a": "y", "b": "1"})

        assert monitor.histogram_count("d") == 3
        assert monitor.histogram_count("d", tags={"a": "x"}) == 2
        assert monitor.histogram_count("d", tags={"a": "x", "b": "1"}) == 1
        assert monitor.histogram_count("d", tags={"a": "z"}) == 0

    def test_increment_records_counter(self):
        monitor = InMemoryMonitor()
        monitor.increment("hits", tags={"path": "/a"})
        assert monitor.counters == [CounterObservation(metric="hits", tags={"path": "/a"})]

    def test_track_records_gauge(self):
        monitor = InMemoryMonitor()
        monitor.track("queue_depth", 7.0)
        assert len(monitor.gauges) == 1
        assert monitor.gauges[0].metric == "queue_depth"
        assert monitor.gauges[0].value == 7.0

    def test_clear_resets_all_collections(self):
        monitor = InMemoryMonitor()
        monitor.histogram("h", 1.0)
        monitor.increment("c")
        monitor.track("g", 2.0)
        monitor.clear()
        assert monitor.histograms == []
        assert monitor.counters == []
        assert monitor.gauges == []


# ────────────── Handler shape annotations ──────────────


class TestHandlerShapeAnnotations:
    def test_pure_inline_handlers(self):
        assert MergeNodeHandler.shape is NodeHandlerShape.PURE_INLINE
        assert TriggerNodeHandler.shape is NodeHandlerShape.PURE_INLINE

    def test_async_inline_handlers(self):
        # All six need a variable_resolver arg; the assertion is on the class.
        assert ConditionNodeHandler.shape is NodeHandlerShape.ASYNC_INLINE
        assert SwitchNodeHandler.shape is NodeHandlerShape.ASYNC_INLINE
        assert LoopNodeHandler.shape is NodeHandlerShape.ASYNC_INLINE
        assert ParallelNodeHandler.shape is NodeHandlerShape.ASYNC_INLINE
        assert WorkflowNodeHandler.shape is NodeHandlerShape.ASYNC_INLINE
        assert ComputeNodeHandler.shape is NodeHandlerShape.ASYNC_INLINE

    def test_dispatch_external_handler(self):
        assert TaskNodeHandler.shape is NodeHandlerShape.DISPATCH_EXTERNAL

    def test_pending_user_handlers(self):
        assert InputNodeHandler.shape is NodeHandlerShape.PENDING_USER
        assert HumanActionNodeHandler.shape is NodeHandlerShape.PENDING_USER
        assert DataEditNodeHandler.shape is NodeHandlerShape.PENDING_USER
        assert FileUploadNodeHandler.shape is NodeHandlerShape.PENDING_USER


# ────────────── VariableResolver histogram emission ──────────────


class TestVariableResolverMetrics:
    async def test_resolve_emits_histogram_with_strategy_label(self):
        monitor = InMemoryMonitor()
        resolver = VariableResolver(
            strategies=[TraversalResolverStrategy()],
            monitor=monitor,
        )

        await resolver.resolve("{{$input.name}}", {"input": {"name": "Alice"}})

        obs = monitor.histogram_observations("variable_resolve_duration_seconds")
        assert len(obs) == 1
        assert obs[0].tags["strategy"] == "TraversalResolverStrategy"
        assert obs[0].tags["outcome"] == "hit"
        assert obs[0].value > 0.0

    async def test_resolve_records_hit_for_file_backed_strategy(self):
        """Two strategies, hit on the second → only the second records "hit"."""
        monitor = InMemoryMonitor()
        file_client = MockFileServiceClient()
        store = DataStoreService(file_client, CsvConverter())

        resolver = VariableResolver(
            strategies=[
                FileBackedResolverStrategy(store, file_client=file_client),
                TraversalResolverStrategy(),
            ],
            monitor=monitor,
        )

        await resolver.resolve("{{$input.name}}", {"input": {"name": "Alice"}})

        obs = monitor.histogram_observations("variable_resolve_duration_seconds")
        # File-backed runs first, does not recognise the ref, returns UNRESOLVED.
        # Traversal runs second and returns the value.
        strategies = [o.tags["strategy"] for o in obs]
        outcomes = [o.tags["outcome"] for o in obs]
        assert "FileBackedResolverStrategy" in strategies
        assert "TraversalResolverStrategy" in strategies
        assert outcomes[strategies.index("FileBackedResolverStrategy")] == "unresolved"
        assert outcomes[strategies.index("TraversalResolverStrategy")] == "hit"

    async def test_resolve_records_error_outcome_on_strategy_failure(self):
        monitor = InMemoryMonitor()
        resolver = VariableResolver(
            strategies=[TraversalResolverStrategy()],
            monitor=monitor,
        )

        from app.layer2_application.resolve.variable_resolver import VariableResolutionError
        with pytest.raises(VariableResolutionError):
            await resolver.resolve("{{$missing.name}}", {"input": {"name": "Alice"}})

        # Source-not-found raises before the strategy loop, so no histogram is
        # recorded. Trigger an in-loop error: traversal tries the lookup and
        # raises "Key 'missing' not found" — that path goes through _timed_try_resolve.
        monitor.clear()
        with pytest.raises(VariableResolutionError):
            await resolver.resolve("{{$input.missing}}", {"input": {"name": "Alice"}})

        obs = monitor.histogram_observations("variable_resolve_duration_seconds")
        assert len(obs) == 1
        assert obs[0].tags["strategy"] == "TraversalResolverStrategy"
        assert obs[0].tags["outcome"] == "error"

    async def test_resolve_no_monitor_is_zero_overhead_path(self):
        """Without a monitor, resolve still works and records nothing."""
        resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
        result = await resolver.resolve("{{$input.name}}", {"input": {"name": "Alice"}})
        assert result == "Alice"


# ────────────── invoke_handler timing ──────────────


class _FakeHandler:
    """Minimal handler that records whether it was invoked and what shape to
    report. Avoids pulling in full TaskFactory / workflow entities."""

    shape = NodeHandlerShape.PURE_INLINE

    def __init__(self, *, should_raise: bool = False) -> None:
        self.called = False
        self._should_raise = should_raise

    async def handle(self, **kwargs) -> NodeHandlerResult:
        self.called = True
        if self._should_raise:
            raise RuntimeError("boom")
        return NodeHandlerResult()


def _stub_node(node_id: str = "n1", node_type: NodeType = NodeType.TRIGGER) -> WorkflowNode:
    return WorkflowNode(id=node_id, name=node_id, node_type=node_type)


def _stub_run() -> WorkflowRun:
    return WorkflowRun(id="r1", workflow_id="wf-1")


def _stub_workflow() -> Workflow:
    node = _stub_node()
    return Workflow(id="wf-1", name="Test", nodes=[node])


class TestInvokeHandlerMetrics:
    async def test_success_emits_histogram_with_ok_status(self):
        monitor = InMemoryMonitor()
        handler = _FakeHandler()
        node = _stub_node(node_type=NodeType.TRIGGER)
        run = _stub_run()
        workflow = _stub_workflow()

        result = await invoke_handler(
            handler,
            node=node,
            run=run,
            workflow=workflow,
            topology=GraphTopology(),
            completed_task=None,
            task_factory=TaskFactory(),
            monitor=monitor,
        )

        assert handler.called
        assert isinstance(result, NodeHandlerResult)
        obs = monitor.histogram_observations("node_handler_duration_seconds")
        assert len(obs) == 1
        assert obs[0].tags == {
            "handler_shape": "pure_inline",
            "node_type": "TRIGGER",
            "status": "ok",
        }
        assert obs[0].value >= 0.0

    async def test_handler_exception_emits_error_status_and_re_raises(self):
        monitor = InMemoryMonitor()
        handler = _FakeHandler(should_raise=True)
        node = _stub_node(node_type=NodeType.CONDITION)
        handler.shape = NodeHandlerShape.ASYNC_INLINE

        with pytest.raises(RuntimeError, match="boom"):
            await invoke_handler(
                handler,
                node=node,
                run=_stub_run(),
                workflow=_stub_workflow(),
                topology=GraphTopology(),
                completed_task=None,
                task_factory=TaskFactory(),
                monitor=monitor,
            )

        obs = monitor.histogram_observations("node_handler_duration_seconds")
        assert len(obs) == 1
        assert obs[0].tags["status"] == "error"
        assert obs[0].tags["handler_shape"] == "async_inline"
        assert obs[0].tags["node_type"] == "CONDITION"

    async def test_missing_monitor_is_zero_overhead_path(self):
        handler = _FakeHandler()
        result = await invoke_handler(
            handler,
            node=_stub_node(),
            run=_stub_run(),
            workflow=_stub_workflow(),
            topology=GraphTopology(),
            completed_task=None,
            task_factory=TaskFactory(),
            monitor=None,
        )
        assert handler.called
        assert isinstance(result, NodeHandlerResult)

    async def test_monitor_exception_does_not_break_orchestration(self):
        """If the monitor itself raises, handler result must still return."""

        class _BrokenMonitor:
            def histogram(self, *args, **kwargs):
                raise RuntimeError("metric backend down")

        handler = _FakeHandler()
        result = await invoke_handler(
            handler,
            node=_stub_node(),
            run=_stub_run(),
            workflow=_stub_workflow(),
            topology=GraphTopology(),
            completed_task=None,
            task_factory=TaskFactory(),
            monitor=_BrokenMonitor(),
        )
        assert handler.called
        assert isinstance(result, NodeHandlerResult)

    async def test_unknown_shape_fallback(self):
        """A handler without a shape attribute reports 'unknown'."""

        class _BareHandler:
            async def handle(self, **kwargs) -> NodeHandlerResult:
                return NodeHandlerResult()

        monitor = InMemoryMonitor()
        await invoke_handler(
            _BareHandler(),
            node=_stub_node(node_type=NodeType.MERGE),
            run=_stub_run(),
            workflow=_stub_workflow(),
            topology=GraphTopology(),
            completed_task=None,
            task_factory=TaskFactory(),
            monitor=monitor,
        )
        obs = monitor.histogram_observations("node_handler_duration_seconds")
        assert len(obs) == 1
        assert obs[0].tags["handler_shape"] == "unknown"
        assert obs[0].tags["node_type"] == "MERGE"
