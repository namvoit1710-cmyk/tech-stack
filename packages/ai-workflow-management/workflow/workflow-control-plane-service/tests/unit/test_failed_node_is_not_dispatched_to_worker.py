"""D1 — a node whose input failed to resolve must not reach a worker.

Observed on tenant-1, run ``c9ef6ca1``, node ``comment_rc``:

    STARTED_AT = None        # task_handler.py never got past the raise
    INPUT_REFS = None        # no resolved input was ever attached
    retry      = 0           # not a retry
    ...and yet: "Dispatched task c9ef6ca1-...-comment_rc -> workflow.tasks"
    ...and the stored error is the WORKER's: "VALIDATION_ERROR: 'issue_key' is required"

A task with no resolved input was sent to a worker anyway. The worker then
failed on the empty input and its message overwrote the control plane's, which
is why every diagnosis started from the wrong field.

The mechanism, from the code:

1. ``TaskNodeHandler.handle`` raises ``ExpressionError`` when input resolution
   fails — before ``task.started_at`` is set and before ``resolved_input``
   reaches the task. That matches all three column values above.
2. ``OrchestrationEngine._exec_uniform`` catches it (~line 2408) and calls
   ``self.emit_node_dispatched(task, run, node)`` (~line 2421). That call is
   deliberate and correct in intent: SA-1977 requires a failed node to appear
   on the canvas, red, with its message, which needs a ``NodeDispatched``.
3. ``emit_node_dispatched`` (~line 1359) constructs ``NodeDispatched(...)``
   and never passes ``dispatch_to_worker``, so it takes the dataclass default
   — ``True`` (``events.py:257``).
4. Every dispatcher reads that flag to decide whether to forward to a worker.

So the event that means "show this node as failed" is indistinguishable from
the event that means "run this node". The flag that separates them exists and
is used correctly exactly once, by ``TaskCreatorConsumer`` — see the last test.

These tests assert the CORRECT behaviour. Before the fix they fail, and their
failure IS the reproduction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.events import NodeDispatched
from app.layer1_domain.exceptions import ExpressionError
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer4_frameworks.messaging.event_mesh_task_dispatcher import (
    EventMeshTaskDispatcher,
)
from app.layer4_frameworks.messaging.http_task_dispatcher import HttpTaskDispatcher

# The gRPC dispatcher needs generated protobuf stubs that only exist in the
# built image. Skip that transport locally rather than skip the whole file —
# two of the three transports are still worth asserting against here, and CI
# has the stubs.
try:
    from app.layer4_frameworks.messaging.grpc_task_dispatcher import GrpcTaskDispatcher
    _TRANSPORTS = [EventMeshTaskDispatcher, GrpcTaskDispatcher, HttpTaskDispatcher]
    _TRANSPORT_IDS = ["event_mesh", "grpc", "http"]
except ImportError:  # pragma: no cover - depends on build environment
    GrpcTaskDispatcher = None
    _TRANSPORTS = [EventMeshTaskDispatcher, HttpTaskDispatcher]
    _TRANSPORT_IDS = ["event_mesh", "http"]


class _CapturingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event) -> None:
        self.events.append((topic, event))


def _async_repo():
    repo = MagicMock()
    repo.aget_by_id = AsyncMock(side_effect=lambda *a, **kw: repo.get_by_id(*a, **kw))
    repo.asave = AsyncMock(side_effect=lambda *a, **kw: repo.save(*a, **kw))
    repo.alist_by_run = AsyncMock(side_effect=lambda *a, **kw: repo.list_by_run(*a, **kw))
    return repo


def _engine(publisher):
    return OrchestrationEngine(
        handler_registry=MagicMock(),
        task_factory=TaskFactory(),
        successor_processor=MagicMock(),
        stage_recorder=MagicMock(),
        task_repo=_async_repo(),
        run_repo=_async_repo(),
        event_publisher=publisher,
        workflow_repo=_async_repo(),
    )


def _fixtures():
    """A task in the exact state tenant-1 recorded for ``comment_rc``: the
    handler raised, so there is no resolved input and no start time."""
    node = WorkflowNode(
        id="comment_rc", name="comment_rc", node_type=NodeType.TASK,
        worker_type="jira-worker",
    )
    workflow = Workflow(
        id="215a8666-1ccf-4400-9d7f-7e06d8c74cbc", name="listen jira",
        nodes=[node], edges=[],
    )
    run = WorkflowRun(
        id="c9ef6ca1", workflow_id=workflow.id, status=RunStatus.RUNNING,
        context=RunContext(variables={}),
    )
    task = TaskFactory().create_task(node, run, resolved_input={})
    task.resolved_input = None      # <- the whole point: the raise came first
    task.started_at = None
    topology = GraphTopology.from_workflow(workflow)
    return task, run, node, workflow, topology


async def _emit_the_failure_path_event() -> NodeDispatched:
    """Drive the REAL path: ``_exec_uniform`` with a handler that raises
    ``ExpressionError``, exactly as ``TaskNodeHandler`` does when input
    resolution fails.

    Deliberately NOT a hand-written call to ``emit_node_dispatched`` with the
    expected argument — that version of this test passed the moment it was
    written and would keep passing if someone deleted the fix at the call site.
    The engine has to choose the flag itself.
    """
    publisher = _CapturingPublisher()
    engine = _engine(publisher)
    task, run, node, workflow, topology = _fixtures()

    handler = MagicMock()
    handler.handle = AsyncMock(
        side_effect=ExpressionError(
            "Node 'comment_rc': input resolution failed: 1 of 3 field(s) failed"
        )
    )
    engine._handler_registry.get = MagicMock(return_value=handler)

    await engine._exec_uniform(task, node, run, workflow, topology)

    dispatched = [
        e for t, e in publisher.events if isinstance(e, NodeDispatched)
    ]
    assert dispatched, (
        "the failure path emitted no NodeDispatched at all — the failed node "
        "would never appear on the canvas"
    )
    return dispatched[-1]


class TestTheFailurePathEventDoesNotReachAWorker:
    async def test_the_event_is_marked_lifecycle_only(self):
        event = await _emit_the_failure_path_event()
        assert event.dispatch_to_worker is False, (
            "the NodeDispatched emitted after input resolution FAILED is marked "
            "dispatch_to_worker=True, so every dispatcher will forward a task "
            "with no resolved input to a worker. This is tenant-1 run c9ef6ca1: "
            "the worker failed on empty input and its message overwrote the "
            "control plane's."
        )

    @pytest.mark.parametrize(
        "dispatcher",
        _TRANSPORTS,
        ids=_TRANSPORT_IDS,
    )
    async def test_no_transport_forwards_it(self, dispatcher):
        """Asserted against the real decision function of each transport, not a
        mock of it — the three are separate implementations and a fix applied to
        one proves nothing about the others."""
        event = await _emit_the_failure_path_event()
        assert dispatcher._should_dispatch_to_worker(event) is False, (
            f"{dispatcher.__name__} would send this failed node to a worker"
        )

    async def test_it_still_reaches_the_canvas(self):
        """The guard must not overshoot. SA-1977 requires the failed node to be
        visible and red; suppressing the event entirely would trade a wrong
        error message for a node that silently does nothing."""
        event = await _emit_the_failure_path_event()
        assert event.node_id == "comment_rc"


class TestTheMechanismAlreadyExists:
    """The fix is not a new concept — it is using the flag the codebase already
    has. ``TaskCreatorConsumer`` sets it correctly for the same reason
    (a lifecycle-only dispatch), which is why that path has never had this bug.
    """

    def test_the_flag_defaults_to_dispatching(self):
        """Why a missing argument is not a harmless omission: the default sends
        the task to a worker. Any emitter that forgets it fails OPEN."""
        assert NodeDispatched(
            task_id="t", run_id="r", workflow_id="w", node_id="n",
        ).dispatch_to_worker is True

    def test_an_explicit_lifecycle_event_is_excluded_by_every_transport(self):
        lifecycle = NodeDispatched(
            task_id="t", run_id="r", workflow_id="w", node_id="n",
            dispatch_to_worker=False,
        )
        for dispatcher in _TRANSPORTS:
            assert dispatcher._should_dispatch_to_worker(lifecycle) is False
