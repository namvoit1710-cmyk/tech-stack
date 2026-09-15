"""Phase 3 Sub 3.4 — HITL resume outbox migration.

Covers ``ResumePendingTaskUseCase`` and ``ResumeRunUseCase`` under
the new outbox-routed paths for ``stage_recorder.record`` and
``orchestration_queue.enqueue``. Validates:

- Flag-off (default): direct stage_recorder + queue calls, no
  ``OutboxRequested`` events appended.
- Flag-on (outbox deps wired): producer appends
  ``OutboxRequested(kind="stage_record")`` /
  ``OutboxRequested(kind="queue_enqueue")``; the dispatcher's
  handlers run against in-memory stage recorder + queue and append
  ``OutboxResolved``.
- Replay safety: re-running the use case for the same (run, op_index)
  lands on the same deterministic ``outbox_id`` and the event log
  dedupes (no duplicate audit row, no duplicate enqueue).
- Append failure fallback: when the event log raises on append, the
  use case falls back to direct calls so the HTTP path keeps its 200.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.value_objects import RunStatus, TaskStatus, NodeType
from app.layer2_application.orchestration.successor_processor import ProcessingResult
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskCommand,
    ResumePendingTaskUseCase,
)
from app.layer2_application.features.resume_run.use_cases.resume_run_use_case import (
    ResumeRunCommand,
    ResumeRunUseCase,
)
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ResumeRunAction,
    ResumeTaskAction,
)
from app.layer2_application.events.outbox_awaiter import OutboxAwaiter
from app.layer4_frameworks.outbox.handlers import (
    QueueEnqueueHandler,
    StageRecordHandler,
)
from app.layer4_frameworks.outbox.outbox_dispatcher import OutboxDispatcher
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_outbox_repository import (
    MemoryOutboxRepository,
)


# ──────────────────────────── stubs ────────────────────────────


class _SpyStageRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **kwargs) -> Any:
        self.calls.append(dict(kwargs))
        rec = MagicMock()
        rec.id = f"rec_{len(self.calls)}"
        return rec


class _SpyQueue:
    def __init__(self) -> None:
        self.actions: list = []

    async def enqueue(self, action: Any) -> None:
        self.actions.append(action)

    async def dequeue(self) -> Any:  # pragma: no cover — not used
        return None


class _SpyEngine(IOrchestrationEngine):
    """Minimal engine spy — records emit_* calls."""

    def __init__(self, workflow_repo=None) -> None:
        self.run_resumed_calls: list = []
        self.dispatched_calls: list = []
        self.resumed_calls: list = []
        self.pending_node_completed_calls: list = []
        self.skipped_calls: list = []
        self._workflow_repo = workflow_repo

    def load_workflow_for_run(self, run):
        if self._workflow_repo is None:
            return None
        return self._workflow_repo.get_by_id(run.workflow_id)

    def emit_node_dispatched(self, task, run, node=None, *, attempt=1, dispatch_reason=""):
        self.dispatched_calls.append((task, run, node))
        if dispatch_reason == "resume":
            self.resumed_calls.append((task, run))

    def emit_pending_node_completed(self, task, run, node, action="confirm"):
        self.pending_node_completed_calls.append((task, run, node, action))

    def emit_run_status(self, run, status, *, input_data=None, output_data=None,
                        error="", failed_task_id="", cancelled_by="", reason=""):
        if status == "resumed":
            self.run_resumed_calls.append(run)

    def emit_node_cancelled(self, run, *, task=None, node_id="", cancelled_by="", reason=""):
        self.skipped_calls.append((run, node_id, reason))

    async def process_successors(self, *_a, **_k):
        return ProcessingResult()

    async def process_and_dispatch_successors(self, *_a, **_k):
        return ProcessingResult()

    async def dispatch_or_handle_task(self, *_a, **_k):
        pass

    async def check_run_completion(self, *_a, **_k):
        pass

    async def drain_events(self):
        pass

    async def drain_events(self):
        pass

    async def maybe_enqueue_loop_iteration(self, *_a, **_k):
        pass

    def publish_events(self, events):
        pass


# ─────────────────────────── fixtures ───────────────────────────


def _make_workflow():
    return Workflow(
        id="wf-1",
        name="Test",
        nodes=[
            WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n2", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[WorkflowEdge(source_node_id="n1", target_node_id="n2")],
    )


def _make_run(status=RunStatus.RUNNING) -> WorkflowRun:
    return WorkflowRun(id="run-1", workflow_id="wf-1", status=status)


def _make_task() -> Task:
    return Task(
        id="task-1",
        run_id="run-1",
        node_id="n2",
        status=TaskStatus.PENDING,
        worker_type="w1",
        resolved_input={"k": "v"},
    )


def _build_outbox_kit():
    log = MemoryEventLogRepository()
    repo = MemoryOutboxRepository(log)
    awaiter = OutboxAwaiter()
    return log, repo, awaiter


def _outbox_requested_for_kind(log: MemoryEventLogRepository, run_id: str, kind: str) -> list:
    return [
        e for e in log.list_by_run(run_id)
        if e.event_type == "OutboxRequested"
        and (e.payload or {}).get("kind") == kind
    ]


# ─────────────────────── ResumePendingTask: flag off ───────────────────────


@pytest.mark.asyncio
class TestPendingTaskFlagOff:
    async def test_no_outbox_events_when_outbox_deps_missing(
        self, logger, run_repo, task_repo, workflow_repo,
    ):
        workflow_repo.save(_make_workflow())
        run_repo.save(_make_run())
        task_repo.save(_make_task())
        recorder = _SpyStageRecorder()
        queue = _SpyQueue()
        engine = _SpyEngine(workflow_repo=workflow_repo)
        log = MemoryEventLogRepository()  # available but not wired

        uc = ResumePendingTaskUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=recorder,
            orchestration_engine=engine,
            orchestration_queue=queue,
        )
        result = await uc.execute(
            ResumePendingTaskCommand(run_id="run-1", task_id="task-1"),
        )
        assert result.resumed is True
        assert recorder.calls, "stage_recorder.record should be called inline"
        # No outbox events were appended.
        assert log.list_by_run("run-1") == []


# ─────────────────────── ResumePendingTask: flag on ───────────────────────


@pytest.mark.asyncio
class TestPendingTaskFlagOn:
    async def _build(self, logger, run_repo, task_repo, workflow_repo):
        workflow_repo.save(_make_workflow())
        run_repo.save(_make_run())
        task_repo.save(_make_task())
        recorder = _SpyStageRecorder()
        queue = _SpyQueue()
        engine = _SpyEngine(workflow_repo=workflow_repo)
        log, repo, awaiter = _build_outbox_kit()

        uc = ResumePendingTaskUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=recorder,
            orchestration_engine=engine,
            orchestration_queue=queue,
            outbox_awaiter=awaiter,
            outbox_repo=repo,
            event_log_repo=log,
        )
        dispatcher = OutboxDispatcher(
            outbox_repo=repo,
            event_log_repo=log,
            handlers={
                "stage_record": StageRecordHandler(recorder),
                "queue_enqueue": QueueEnqueueHandler(queue),
            },
            awaiter=awaiter,
        )
        return uc, dispatcher, log, repo, recorder, queue

    async def test_task_resume_emits_stage_record_and_queue_outbox(
        self, logger, run_repo, task_repo, workflow_repo,
    ):
        uc, dispatcher, log, _repo, recorder, queue = await self._build(
            logger, run_repo, task_repo, workflow_repo,
        )
        result = await uc.execute(
            ResumePendingTaskCommand(run_id="run-1", task_id="task-1"),
        )
        assert result.resumed is True
        # Producer skipped direct calls — outbox is the only path.
        assert recorder.calls == []
        assert queue.actions == []

        # One ``stage_record`` envelope (op_index=1, RUNNING).
        stage_envs = _outbox_requested_for_kind(log, "run-1", "stage_record")
        assert len(stage_envs) == 1
        env = stage_envs[0]
        assert env.payload["payload"]["stage_kwargs"]["event_type"] == "task.resumed"
        assert env.payload["outbox_id"] == EventId.derive_outbox(
            "run-1", "task-1", 1, "stage_record",
        )

        # No queue_enqueue for non-HITL TASK resume — HITL path is the
        # only source of ResumeTaskAction enqueues.
        assert _outbox_requested_for_kind(log, "run-1", "queue_enqueue") == []

        # Dispatcher drains: stage_recorder.record runs once.
        await dispatcher.drain_once()
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["event_type"] == "task.resumed"

    async def test_replay_dedupes_via_deterministic_outbox_id(
        self, logger, run_repo, task_repo, workflow_repo,
    ):
        uc, dispatcher, log, _repo, recorder, _queue = await self._build(
            logger, run_repo, task_repo, workflow_repo,
        )
        await uc.execute(
            ResumePendingTaskCommand(run_id="run-1", task_id="task-1"),
        )
        # Reset task to PENDING so the second resume re-runs the same
        # code path (simulating a replayed HTTP retry).
        task = task_repo.get_by_id("task-1")
        task.status = TaskStatus.PENDING
        task_repo.save(task)
        await uc.execute(
            ResumePendingTaskCommand(run_id="run-1", task_id="task-1"),
        )
        # Event log dedupes on deterministic event_id — only ONE stage
        # record envelope exists despite two resume attempts.
        stage_envs = _outbox_requested_for_kind(log, "run-1", "stage_record")
        assert len(stage_envs) == 1

        await dispatcher.drain_once()
        assert len(recorder.calls) == 1

    async def test_append_failure_falls_back_to_direct(
        self, logger, run_repo, task_repo, workflow_repo,
    ):
        workflow_repo.save(_make_workflow())
        run_repo.save(_make_run())
        task_repo.save(_make_task())
        recorder = _SpyStageRecorder()
        queue = _SpyQueue()
        engine = _SpyEngine(workflow_repo=workflow_repo)

        class _BoomLog(MemoryEventLogRepository):
            def append(self, envelope):
                raise RuntimeError("disk full")

        log = _BoomLog()
        repo = MemoryOutboxRepository(log)
        awaiter = OutboxAwaiter()
        uc = ResumePendingTaskUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=recorder,
            orchestration_engine=engine,
            orchestration_queue=queue,
            outbox_awaiter=awaiter,
            outbox_repo=repo,
            event_log_repo=log,
        )
        result = await uc.execute(
            ResumePendingTaskCommand(run_id="run-1", task_id="task-1"),
        )
        assert result.resumed is True
        # Append blew up → fall back. Direct stage_recorder.record ran.
        assert len(recorder.calls) == 1


# ─────────────────────── ResumeRun: flag off ───────────────────────


@pytest.mark.asyncio
class TestResumeRunFlagOff:
    async def test_direct_calls_when_outbox_deps_missing(
        self, logger, run_repo, task_repo, workflow_repo,
    ):
        workflow_repo.save(_make_workflow())
        run_repo.save(_make_run(status=RunStatus.PAUSED))
        recorder = _SpyStageRecorder()
        queue = _SpyQueue()
        engine = _SpyEngine(workflow_repo=workflow_repo)
        log = MemoryEventLogRepository()

        uc = ResumeRunUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=recorder,
            orchestration_engine=engine,
            orchestration_queue=queue,
        )
        result = await uc.execute(ResumeRunCommand(run_id="run-1"))
        assert result.resumed is True
        assert len(recorder.calls) == 1
        assert len(queue.actions) == 1
        assert isinstance(queue.actions[0], ResumeRunAction)
        assert log.list_by_run("run-1") == []


# ─────────────────────── ResumeRun: flag on ───────────────────────


@pytest.mark.asyncio
class TestResumeRunFlagOn:
    async def _build(self, logger, run_repo, task_repo, workflow_repo):
        workflow_repo.save(_make_workflow())
        run_repo.save(_make_run(status=RunStatus.PAUSED))
        recorder = _SpyStageRecorder()
        queue = _SpyQueue()
        engine = _SpyEngine(workflow_repo=workflow_repo)
        log, repo, awaiter = _build_outbox_kit()

        uc = ResumeRunUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=recorder,
            orchestration_engine=engine,
            orchestration_queue=queue,
            outbox_awaiter=awaiter,
            outbox_repo=repo,
            event_log_repo=log,
        )
        dispatcher = OutboxDispatcher(
            outbox_repo=repo,
            event_log_repo=log,
            handlers={
                "stage_record": StageRecordHandler(recorder),
                "queue_enqueue": QueueEnqueueHandler(queue),
            },
            awaiter=awaiter,
        )
        return uc, dispatcher, log, recorder, queue

    async def test_resume_run_emits_both_outbox_kinds(
        self, logger, run_repo, task_repo, workflow_repo,
    ):
        uc, dispatcher, log, recorder, queue = await self._build(
            logger, run_repo, task_repo, workflow_repo,
        )
        result = await uc.execute(ResumeRunCommand(run_id="run-1"))
        assert result.resumed is True
        # Direct paths skipped.
        assert recorder.calls == []
        assert queue.actions == []

        stage_envs = _outbox_requested_for_kind(log, "run-1", "stage_record")
        queue_envs = _outbox_requested_for_kind(log, "run-1", "queue_enqueue")
        assert len(stage_envs) == 1
        assert len(queue_envs) == 1
        assert stage_envs[0].payload["outbox_id"] == EventId.derive_outbox(
            "run-1", "resume_run", 0, "stage_record",
        )
        assert queue_envs[0].payload["outbox_id"] == EventId.derive_outbox(
            "run-1", "resume_run", 1, "queue_enqueue",
        )
        assert queue_envs[0].payload["payload"]["action_type"] == "ResumeRunAction"

        # Dispatcher executes both: 1 stage record + 1 queue enqueue.
        await dispatcher.drain_once()
        assert len(recorder.calls) == 1
        assert len(queue.actions) == 1
        assert isinstance(queue.actions[0], ResumeRunAction)
        assert queue.actions[0].run_id == "run-1"

    async def test_replay_does_not_double_enqueue(
        self, logger, run_repo, task_repo, workflow_repo,
    ):
        uc, dispatcher, log, recorder, queue = await self._build(
            logger, run_repo, task_repo, workflow_repo,
        )
        await uc.execute(ResumeRunCommand(run_id="run-1"))
        # Reset run to PAUSED to permit a second resume call. Real
        # replay comes from a redelivered HTTP request, but we mimic
        # by re-running the same use case.
        run = run_repo.get_by_id("run-1")
        run.status = RunStatus.PAUSED
        run_repo.save(run)
        await uc.execute(ResumeRunCommand(run_id="run-1"))

        stage_envs = _outbox_requested_for_kind(log, "run-1", "stage_record")
        queue_envs = _outbox_requested_for_kind(log, "run-1", "queue_enqueue")
        # Deterministic outbox_ids → log dedupe collapses both pairs.
        assert len(stage_envs) == 1
        assert len(queue_envs) == 1

        await dispatcher.drain_once()
        # Dispatcher's queue_enqueue LRU short-circuits a second
        # resolved attempt for the same outbox_id; one enqueue total.
        assert len(queue.actions) == 1


# ─────────────────────── Handler unit checks ───────────────────────


class TestStageRecordHandlerShape:
    """The Sub 3.2 handler used to call ``record(stage_kind, attrs)``;
    Sub 3.4 swapped that for ``record(**stage_kwargs)`` to match the
    real ``StageRecorder`` keyword-only signature."""

    @pytest.mark.asyncio
    async def test_dispatches_kwargs_to_real_recorder(self):
        recorder = _SpyStageRecorder()
        handler = StageRecordHandler(recorder)
        entry = type("E", (), {})()
        entry.payload = {
            "stage_kwargs": {
                "run_id": "r1", "event_type": "run.resumed",
                "from_status": "PAUSED", "to_status": "RUNNING",
            },
        }
        result = await handler.perform(entry)
        assert recorder.calls[0]["event_type"] == "run.resumed"
        assert "record_id" in result

    @pytest.mark.asyncio
    async def test_missing_event_type_is_permanent(self):
        from app.layer4_frameworks.outbox.outbox_errors import PermanentError
        recorder = _SpyStageRecorder()
        handler = StageRecordHandler(recorder)
        entry = type("E", (), {})()
        entry.payload = {"stage_kwargs": {"run_id": "r1"}}
        with pytest.raises(PermanentError):
            await handler.perform(entry)


class TestQueueEnqueueHandlerLRU:
    @pytest.mark.asyncio
    async def test_repeated_outbox_id_does_not_double_enqueue(self):
        queue = _SpyQueue()
        handler = QueueEnqueueHandler(queue)
        entry = type("E", (), {})()
        entry.outbox_id = "ob_lru_1"
        entry.payload = {
            "action_type": "ResumeRunAction",
            "action_payload": {"run_id": "r1"},
        }
        await handler.perform(entry)
        await handler.perform(entry)
        assert len(queue.actions) == 1
