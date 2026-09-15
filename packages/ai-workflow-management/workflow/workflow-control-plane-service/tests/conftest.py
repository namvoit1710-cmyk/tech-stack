"""Shared test fixtures and dummy implementations."""

import sys
from unittest.mock import MagicMock

# Pre-mock confluent_kafka (not in requirements.txt) so @patch decorators
# in test_kafka_broker_client.py can traverse the module path.
_confluent_kafka_mock = MagicMock()
_confluent_kafka_mock.KafkaError._PARTITION_EOF = -191  # real sentinel value used in _consume_loop
sys.modules.setdefault("confluent_kafka", _confluent_kafka_mock)

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.stage_record import StageRecord
from app.layer1_domain.value_objects import WorkflowStatus, RunStatus, TaskStatus, NodeType
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository
from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import MemoryNodeDefinitionRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import MemoryWorkflowSnapshotRepository
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine


# ──────────────────────────────────────────────────────────────────
# Phase B+C workflow-snapshot test shim
#
# Production code requires every run to carry a ``workflow_snapshot_id``
# and the engine resolves the workflow definition through a
# ``workflow_snapshot_repo``. Most pre-existing tests build a
# ``WorkflowRun`` directly (no ``StartRunUseCase``), so they never
# capture a snapshot. To keep that ergonomics while preserving the
# production hard-fail behaviour, we install module-level patches at
# import time that:
#
# 1. Auto-default ``workflow_snapshot_repo`` on ``StartRunUseCase`` and
#    ``TestFullWorkflowUseCase`` when the caller omits it. Both use
#    cases capture a snapshot internally, so once they have a repo
#    everything downstream works.
# 2. Auto-default ``workflow_snapshot_repo`` on ``OrchestrationEngine``
#    when omitted.
# 3. Patch ``MemoryRunRepository.save`` to lazily capture a snapshot
#    from a registered ``workflow_repo`` whenever the run lacks one.
#    Tests that construct a ``WorkflowRun`` directly need only have
#    saved the workflow into a known repo first (the bulk pattern).
#
# The hard-fail policy is preserved in production — these patches are
# in test-side code only.
# ──────────────────────────────────────────────────────────────────


import weakref as _weakref

_TEST_SHARED_SNAPSHOT_REPO: MemoryWorkflowSnapshotRepository | None = None
# WeakValueDictionary keyed by id() — when a workflow_repo is GC'd it
# drops out automatically (avoids cross-test pollution when two tests
# reuse the same workflow_id). Iterated newest-first by insertion order
# so the most recently constructed repo (i.e. this test's fixture)
# wins when multiple instances are still alive.
_TEST_WORKFLOW_REPOS: "_weakref.WeakValueDictionary" = _weakref.WeakValueDictionary()
_TEST_WORKFLOW_REPOS_INSERTION: list[int] = []  # insertion order of id()s


def _get_shared_snapshot_repo() -> MemoryWorkflowSnapshotRepository:
    global _TEST_SHARED_SNAPSHOT_REPO
    if _TEST_SHARED_SNAPSHOT_REPO is None:
        _TEST_SHARED_SNAPSHOT_REPO = MemoryWorkflowSnapshotRepository()
    return _TEST_SHARED_SNAPSHOT_REPO


def _install_workflow_snapshot_shims():
    from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
        StartRunUseCase,
    )
    from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import (
        TestFullWorkflowUseCase,
    )
    from app.layer2_application.features.test_node.use_cases.test_node_use_case import (
        TestNodeUseCase,
    )
    from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import (
        CreateWorkflowUseCase,
    )
    from app.layer2_application.features.rerun_task.use_cases.rerun_task_use_case import (
        RerunTaskUseCase,
    )
    from app.layer2_application.orchestration import orchestration_engine as _oe_mod
    from app.layer4_frameworks.persistence.memory import (
        memory_run_repo as _mrr_mod,
        memory_workflow_repo as _mwr_mod,
    )
    from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot

    # Register every MemoryWorkflowRepository instance so the
    # save-time shim can find the workflow for a given run.
    _MWR = _mwr_mod.MemoryWorkflowRepository
    _orig_mwr_init = _MWR.__init__

    def _mwr_init(self, *args, **kwargs):
        _orig_mwr_init(self, *args, **kwargs)
        _TEST_WORKFLOW_REPOS[id(self)] = self
        _TEST_WORKFLOW_REPOS_INSERTION.append(id(self))

    _MWR.__init__ = _mwr_init

    if getattr(StartRunUseCase, "_phaseBC_test_shim", False):
        return  # already installed

    # 1. StartRunUseCase / TestFullWorkflowUseCase — default the repo.
    # Use ``__signature__`` preservation so bootstrap's
    # smart-injection (``inspect.signature(UseCaseClass)``) still sees
    # the real parameter list — wrapping with ``*args, **kwargs``
    # naively would hide the required-deps set.
    import functools as _functools
    import inspect as _inspect
    for _cls in (
        StartRunUseCase,
        TestFullWorkflowUseCase,
        TestNodeUseCase,
        CreateWorkflowUseCase,
        # RerunTask re-captures a fresh snapshot before re-dispatch; it
        # must write to the same store the (shimmed) engine reads from,
        # exactly like StartRun — omitting it 500s every rerun test.
        RerunTaskUseCase,
    ):
        _orig_init = _cls.__init__
        _orig_sig = _inspect.signature(_orig_init)

        def _make_wrapper(orig, orig_sig):
            @_functools.wraps(orig)
            def _wrapped(self, *args, **kwargs):
                # Force the shared snapshot repo so capture in the use
                # case writes to the same store the engine reads from.
                kwargs["workflow_snapshot_repo"] = _get_shared_snapshot_repo()
                return orig(self, *args, **kwargs)
            _wrapped.__signature__ = orig_sig
            return _wrapped

        _cls.__init__ = _make_wrapper(_orig_init, _orig_sig)
    StartRunUseCase._phaseBC_test_shim = True
    TestFullWorkflowUseCase._phaseBC_test_shim = True
    TestNodeUseCase._phaseBC_test_shim = True
    CreateWorkflowUseCase._phaseBC_test_shim = True
    RerunTaskUseCase._phaseBC_test_shim = True

    # 2. OrchestrationEngine — force the shared snapshot repo so it
    # reads from the same store the ``MemoryRunRepository.save`` shim
    # writes to. Different ``MemoryWorkflowSnapshotRepository``
    # instances would otherwise diverge.
    _OE = _oe_mod.OrchestrationEngine
    _orig_oe_init = _OE.__init__
    _orig_oe_sig = _inspect.signature(_orig_oe_init)

    @_functools.wraps(_orig_oe_init)
    def _oe_init(self, *args, **kwargs):
        kwargs["workflow_snapshot_repo"] = _get_shared_snapshot_repo()
        return _orig_oe_init(self, *args, **kwargs)

    _oe_init.__signature__ = _orig_oe_sig
    _OE.__init__ = _oe_init

    # 3. MemoryRunRepository.save — auto-capture snapshot when missing.
    _MRR = _mrr_mod.MemoryRunRepository
    _orig_save = _MRR.save

    def _save(self, run):
        if not getattr(run, "workflow_snapshot_id", ""):
            # Walk registered workflow_repos newest-first to find the
            # workflow this run refers to and capture a snapshot. Falls
            # through to the unmodified save() if nothing matches
            # (preserves the hard-fail path for tests that
            # intentionally exercise it).
            wf_id = getattr(run, "workflow_id", "")
            if wf_id:
                # Iterate newest-first so this-test's freshly-created
                # workflow_repo wins over any GC-pending repos from a
                # prior test that re-used the same workflow_id.
                for rid in reversed(_TEST_WORKFLOW_REPOS_INSERTION):
                    wr = _TEST_WORKFLOW_REPOS.get(rid)
                    if wr is None:
                        continue
                    try:
                        wf = wr.get_by_id(wf_id)
                    except Exception:
                        wf = None
                    if wf is not None:
                        run.workflow_snapshot_id = capture_snapshot(
                            wf, _get_shared_snapshot_repo(),
                        )
                        break
        return _orig_save(self, run)

    _MRR.save = _save


_install_workflow_snapshot_shims()


def assert_ok(response, status_code=200):
    assert response.status_code == status_code
    body = response.json()
    assert body["status"] == "ok"
    return body["data"]


def assert_error(response, code=None):
    body = response.json()
    assert body["status"] == "error"
    assert "code" in body["error"]
    assert "message" in body["error"]
    if code:
        assert body["error"]["code"] == code
    return body["error"]


class DummyLogger:
    """Captures log calls for assertions."""

    def __init__(self):
        self.messages = []

    def info(self, message, *args, **kwargs):
        self.messages.append(("INFO", message))

    def error(self, message, *args, **kwargs):
        self.messages.append(("ERROR", message))

    def warning(self, message, *args, **kwargs):
        self.messages.append(("WARNING", message))

    def debug(self, message, *args, **kwargs):
        self.messages.append(("DEBUG", message))

    def log(self, level, message, *args, **kwargs):
        self.messages.append((level.upper() if isinstance(level, str) else str(level), message))


class DummyMonitor:
    def __init__(self):
        self.metrics = []

    def track(self, metric, value):
        self.metrics.append((metric, value))

    def increment(self, metric, tags=None):
        self.metrics.append((metric, 1))


class DummyEventPublisher:
    def __init__(self):
        self.events = []

    def publish(self, topic, event):
        self.events.append((topic, event))


class DummyStageRecorder:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


class DummyOrchestrationEngine(IOrchestrationEngine):
    """Minimal stub for OrchestrationEngine.

    Carries a noop ``_queue`` so use cases that fall back to
    ``engine._queue`` when no explicit queue is passed don't crash.
    Also records every ``emit_*`` call keyed by method name so tests
    can assert on status transitions without inspecting the
    (post-3b.2c) stale aggregate.
    """

    def __init__(self):
        class _NoopQueue:
            async def enqueue(self, action):
                pass
            async def dequeue(self):
                raise NotImplementedError
        self._queue = _NoopQueue()
        self.emitted: dict[str, list] = {}
        # Tests that call into write-path use cases through this dummy
        # need ``load_workflow_for_run`` to resolve. We default to a
        # newest-first lookup against all live ``MemoryWorkflowRepository``
        # instances (the conftest shim already tracks them).
        self._workflow_repo = None

    def load_workflow_for_run(self, run):
        # Honour an explicitly attached workflow_repo first; otherwise
        # iterate registered repos newest-first so tests just need to
        # save their workflow before the run runs.
        repos = ([self._workflow_repo] if self._workflow_repo is not None else [])
        for rid in reversed(_TEST_WORKFLOW_REPOS_INSERTION):
            wr = _TEST_WORKFLOW_REPOS.get(rid)
            if wr is not None and wr is not self._workflow_repo:
                repos.append(wr)
        for wr in repos:
            try:
                wf = wr.get_by_id(run.workflow_id)
            except Exception:
                wf = None
            if wf is not None:
                return wf
        return None

    def _record(self, name: str, payload) -> None:
        self.emitted.setdefault(name, []).append(payload)

    def publish_event(self, topic, event):
        pass

    def publish_events(self, events):
        pass

    async def check_run_completion(self, run, workflow):
        pass

    async def process_successors(self, task, run, workflow, topology):
        from app.layer2_application.orchestration.successor_processor import ProcessingResult
        return ProcessingResult()

    async def process_and_dispatch_successors(self, completed_task, run, workflow, topology, test_mode=None):
        from app.layer2_application.orchestration.successor_processor import ProcessingResult
        return ProcessingResult()

    async def orchestrate_start(self, run_id):
        pass

    async def orchestrate_start_locked(self, run_id):
        pass

    def run_lock(self, run_id):
        # Tests don't need real locking — return a no-op async ctx mgr.
        import contextlib
        return contextlib.nullcontext()

    def locked_run(self, run_id):
        # Reentrant variant used by /test/full's reuse branch.
        import contextlib
        return contextlib.nullcontext()

    async def orchestrate_resume_run(self, run_id):
        pass

    async def drain_events(self):
        pass

    async def drain_events(self):
        pass

    async def maybe_enqueue_loop_iteration(self, task, run):
        pass

    async def fail_parent_tracking_task(self, child_run):
        pass

    def publish_callback(self, run, status):
        pass

    # Semantic emit helpers
    def emit_run_status(
        self,
        run,
        status,
        *,
        input_data=None,
        output_data=None,
        error="",
        failed_task_id="",
        cancelled_by="",
        reason="",
    ):
        if status == "cancelled":
            self._record(
                "emit_run_cancelled",
                {"run_id": run.id, "cancelled_by": cancelled_by, "reason": reason},
            )
        elif status == "resumed":
            self._record("emit_run_resumed", {"run_id": run.id})

    def emit_variables_seeded(self, run, state_updates, *, seed_id="variables_seeded"):
        pass

    def emit_node_cancelled(self, run, *, task=None, node_id="", cancelled_by="", reason=""):
        pass

    def emit_child_run_cancelled(self, parent_run, child_run, *, reason=""):
        pass

    def emit_node_dispatched(self, task, run, node=None, *, attempt=1, dispatch_reason=""):
        pass

    def emit_node_completed(self, task, run, node=None):
        pass

    def emit_node_failed(self, task, run, error_code="WORKER_ERROR", error_msg=""):
        pass

    def emit_pending_node_completed(self, task, run, node, action="confirm"):
        pass

    def emit_artifact_added(self, run, node_id, file_id, name, content_type, size_bytes, artifact_type):
        pass

    def emit_node_input_edited(self, run, node_id, task_id, previous_file_id, new_file_id):
        pass

    def emit_node_data_edited(
        self, run, workflow, *, node_id, new_ref,
        task_id="", edited_by="", reason="",
    ):
        pass

    async def apply_state_updates_as_files(
        self, state_updates, run, workflow,
        *, source_node_id="", causation_id="", output_port="",
        taken_edge_ports=None, attempt_id="", task=None,
    ):
        # Dummy: apply straight to run.context.variables — mirrors the
        # engine's mutation half without emitting events (the dummy's
        # role is test stubbing, not event capture).
        for k, v in (state_updates or {}).items():
            run.context.variables[k] = v
            try:
                node = workflow.get_node(k)
                if node and node.name and node.name != k:
                    run.context.variables[node.name] = v
            except Exception:
                pass


class DummyWorkerRegistryClient:
    def __init__(self, workers=None):
        self._workers = workers or []

    def list_workers(self):
        return self._workers

    def get_worker(self, worker_id):
        return next((w for w in self._workers if w.get("id") == worker_id), None)

    def get_worker_schema(self, worker_id):
        w = self.get_worker(worker_id)
        if w is None:
            return None
        return {
            "worker_id": w.get("id"),
            "worker_type": w.get("worker_type"),
            "version": w.get("version"),
            "input_schema": w.get("input_schema"),
            "output_schema": w.get("output_schema"),
        }

    def discover_workers(self, capability):
        return [
            w for w in self._workers
            if any(
                c.get("domain") == capability or c.get("action") == capability
                for c in w.get("capabilities", [])
            )
        ]

    def list_worker_functions(self, worker_type):
        w = next((w for w in self._workers if w.get("worker_type") == worker_type), None)
        if w is None:
            return []
        return w.get("functions", [])

    def invoke_worker_function(self, worker_type, function_name, params):
        return {"status": "error", "error": "Not available in mock mode"}


@pytest.fixture
def logger():
    return DummyLogger()


@pytest.fixture
def monitor():
    return DummyMonitor()


@pytest.fixture
def workflow_repo():
    return MemoryWorkflowRepository()


@pytest.fixture
def run_repo():
    return MemoryRunRepository()


@pytest.fixture
def task_repo():
    return MemoryTaskRepository()


@pytest.fixture
def stage_history_repo():
    return MemoryStageHistoryRepository()


@pytest.fixture
def node_definition_repo():
    return MemoryNodeDefinitionRepository()


@pytest.fixture
def mock_file_service_client():
    return MockFileServiceClient()


@pytest.fixture
def event_publisher():
    return DummyEventPublisher()


@pytest.fixture
def stage_recorder():
    return DummyStageRecorder()


@pytest.fixture
def orchestration_engine():
    return DummyOrchestrationEngine()


@pytest.fixture
def sample_workflow():
    """A simple 2-node workflow for testing."""
    return Workflow(
        id="wf-1",
        name="Test Workflow",
        description="A test workflow",
        nodes=[
            WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n2", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="n1", target_node_id="n2"),
        ],
    )


@pytest.fixture
def sample_run():
    """A sample run in RUNNING status."""
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
    )
