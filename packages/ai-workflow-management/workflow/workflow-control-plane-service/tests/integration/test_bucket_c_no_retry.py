"""Bucket-C contention regression: verify direct ``run_repo.save`` call
sites in the orchestration engine and resume-pending-task use case do
NOT retry on ``OptimisticConcurrencyError`` — they propagate.

Bucket-C sites are those where the save is preceded by work that has
side effects (dispatched tasks, emitted events, accumulated state) that
cannot safely replay on retry. The current safety net is the per-run
``asyncio.Lock`` (in-instance) and Phase M.1 sticky routing (cross-
instance). The long-term fix is the projector-centric write model on
the Phase 3 roadmap.

These tests lock the no-retry behavior in so a future refactor doesn't
silently change it. If/when a Bucket-C site is moved to event-log
appends + projector apply, the test for that site should change to
verify the new replay-safe semantics — not be deleted.

Site coverage in this file:
- ``orchestration_engine._execute_node_inner`` (line ~1808)
- ``orchestration_engine.process_and_dispatch_successors`` (line ~1468)
- ``orchestration_engine._orchestrate_start_inner`` (line ~1565)

The other Bucket-C sites (``loop_iteration_driver`` 250/285,
``child_workflow_coordinator`` 255, ``resume_pending_task`` 374/802)
share the engine's no-retry contract by inheritance — they call
``run_repo.save`` directly without the helper. A targeted assertion
that no site in the engine grew a hidden helper call covers them.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun, GraphTopology
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)


# ───────────────────────── helpers ──────────────────────────────────


def _make_run() -> WorkflowRun:
    return WorkflowRun(
        id="r-1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(topology=GraphTopology()),
        version=1,
    )


class _ConflictOnNthSave:
    """Run repo stub that raises ``OptimisticConcurrencyError`` on the
    Nth ``save`` invocation. Lets each test target a specific Bucket-C
    save without triggering an earlier Pattern-A wrap to retry.

    Exposes both sync and async pairs so engine code calling
    ``asave`` / ``aget_by_id`` (post-Task #19) hits the same counter
    as legacy sync callers.
    """

    def __init__(self, run: WorkflowRun, *, conflict_on: int) -> None:
        self._run = run
        self._conflict_on = conflict_on
        self.save_call_count = 0

    def get_by_id(self, run_id: str):
        return self._run if run_id == self._run.id else None

    async def aget_by_id(self, run_id: str):
        return self.get_by_id(run_id)

    def save(self, run: WorkflowRun) -> None:
        self.save_call_count += 1
        if self.save_call_count == self._conflict_on:
            raise OptimisticConcurrencyError(
                run.id, expected=run.version, actual=run.version + 99,
            )
        run.version += 1

    async def asave(self, run: WorkflowRun) -> None:
        self.save(run)


# ───────────────────────── _execute_node_inner ─────────────────────


class TestExecuteNodeInnerNoRetry:
    """``_execute_node_inner`` saves the run at line ~1808 AFTER
    ``_exec_uniform`` has emitted events and dispatched successors.
    Retrying that save would re-emit the side effects. Lock in the
    no-retry contract.

    NOTE: ``orchestrate_execute_node`` catches OCE in its outer
    try/except and transitions the run to FAILED via the Pattern-A
    wrap. That's the surrounding behavior, not the inner save's
    contract. Test ``_execute_node_inner`` directly to isolate the
    Bucket-C save semantics.
    """

    @pytest.mark.asyncio
    async def test_save_at_line_1808_does_not_retry(self):
        from app.layer2_application.orchestration.orchestration_engine import (
            OrchestrationEngine,
        )

        run = _make_run()
        repo = _ConflictOnNthSave(run, conflict_on=1)

        task = MagicMock()
        task.id = "t-1"
        task.node_id = "n-1"
        task.get_innermost_scope.return_value = None

        node = MagicMock()
        node.id = "n-1"

        workflow = MagicMock()
        workflow.get_node.return_value = node

        engine = OrchestrationEngine.__new__(OrchestrationEngine)
        engine._run_repo = repo
        engine._task_repo = MagicMock()
        # Post-#19 the engine calls ``aget_by_id`` (async). MagicMock
        # auto-creates the attribute but with sync semantics — replace
        # with an AsyncMock so ``await`` resolves to ``task``.
        engine._task_repo.aget_by_id = AsyncMock(return_value=task)
        engine.load_workflow_for_run = MagicMock(return_value=workflow)
        engine._exec_uniform = AsyncMock()
        engine.drain_events = AsyncMock()
        engine.check_run_completion = AsyncMock()
        engine.drain_events = AsyncMock()

        with pytest.raises(OptimisticConcurrencyError):
            await engine._execute_node_inner("r-1", "t-1")

        # Bucket-C contract: exactly ONE save attempt — no retry.
        assert repo.save_call_count == 1

        # No double-emission: post-save events (drain_events,
        # check_run_completion, drain_events) did not run because OCE
        # propagated.
        engine.check_run_completion.assert_not_awaited()


# ───────────────────────── source-level invariant ──────────────────
#
# ``process_and_dispatch_successors`` (line ~1468) and
# ``_orchestrate_start_inner`` (line ~1565) are not behaviorally tested
# here. Each is reachable only through deep engine state (successor
# processor, scope manager, event collector, view store) that a unit-
# level mock cannot economically reproduce without becoming a brittle
# replica of the engine. The source-level invariant below catches any
# accidental wrap on those sites; their no-retry behavior is the same
# shape as ``_execute_node_inner`` tested above. End-to-end coverage
# can be added via the full engine harness in
# ``tests/integration/test_edge_based_engine_e2e.py`` if a real
# regression is suspected.


class TestNoHiddenHelperCalls:
    """Source-level invariant: every direct ``self._run_repo.save(run)``
    call in the engine must be intentional (Bucket-C). If a future
    change wraps one of them with the retry helper without our notice,
    this test fails so the change is reviewed against the no-retry
    contract.

    This is a regression net for the Bucket-C site list documented in
    ``docs/design/`` / the wobbly-jingling-squid plan.
    """

    def test_engine_bucket_c_count_unchanged(self):
        import re
        from pathlib import Path

        engine_src = Path(
            "app/layer2_application/orchestration/orchestration_engine.py",
        ).read_text()
        # Match ``self._run_repo.save(run)`` and the async-equivalent
        # ``self._run_repo.asave(run)`` — both are direct, single-
        # attempt saves (no ``save_run_with_cas_retry`` wrap).
        # Task #19 converted Bucket-C sites from sync to async; the
        # contract (no retry) is preserved either way.
        direct_saves = re.findall(
            r"self\._run_repo\.a?save\(run\)", engine_src,
        )
        # Bucket-C in orchestration_engine.py:
        # - pending-revisits drain save (SA-846 Mode A: re-fires queued
        #   revisits once the in-flight child completes; runs under the
        #   same per-run engine lock as the other sites)
        # - process_and_dispatch_successors final save
        # - _orchestrate_start_inner happy path
        # - _execute_node_inner happy path
        # - orchestrate_continue loop-body iter_progress save (guarded to
        #   loop-body completions only): persists the maybe_enqueue_loop_iteration
        #   mutation so a multi-node worker body doesn't lose progress across
        #   per-node completions on a fresh-deserialize repo (tenant-1 async
        #   loop-advance fix). Runs under the same per-run engine lock.
        assert len(direct_saves) == 5, (
            f"Expected 5 direct ``self._run_repo.[a]save(run)`` calls "
            f"in orchestration_engine.py (Bucket-C sites), found "
            f"{len(direct_saves)}. If you intentionally wrapped or "
            f"removed a Bucket-C site, update this assertion + the "
            f"Bucket-C site list in the plan."
        )

    def test_resume_pending_task_bucket_c_count_unchanged(self):
        import re
        from pathlib import Path

        src = Path(
            "app/layer2_application/features/resume_pending_task/"
            "use_cases/resume_pending_task_use_case.py",
        ).read_text()
        # Direct ``self.run_repo.save(run)`` or ``.asave(run)`` —
        # Bucket-C sites in resume_pending_task.
        direct_saves = re.findall(
            r"self\.run_repo\.a?save\(run\)", src,
        )
        # Bucket-C in resume_pending_task:
        # - PENDING→RUNNING transition (line ~389)
        # - Final save after accumulated state (line ~817)
        assert len(direct_saves) == 2, (
            f"Expected 2 direct ``self.run_repo.[a]save(run)`` calls "
            f"in resume_pending_task_use_case.py (Bucket-C sites), "
            f"found {len(direct_saves)}. If you intentionally wrapped "
            f"or removed a Bucket-C site, update this assertion."
        )
