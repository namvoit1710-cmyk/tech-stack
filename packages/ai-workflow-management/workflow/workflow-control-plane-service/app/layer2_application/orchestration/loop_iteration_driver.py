"""Event-driven loop iteration lifecycle — extracted from OrchestrationEngine.

A LOOP node executes its body once per item in the iterable. Two
engine hooks coordinate that:

* :meth:`maybe_enqueue_loop_iteration` — body-task completion hook.
  Every body task that finishes is fed through this method; when it
  detects that the iteration is complete (every reachable body node
  is tracked in ``loop_state["iter_progress"][iter]``), it enqueues
  a ``LoopIterationAction`` for the worker to dispatch.
* :meth:`orchestrate_iteration` — invoked by the worker when it
  dequeues a ``LoopIterationAction``. Calls the loop handler to
  decide what comes next (more body tasks for the next iteration, or
  exit tasks once all iterations are done) and dispatches accordingly.

The driver is coupled to the engine via ``self._engine`` for
orchestration callbacks (publishing events, applying state,
dispatching successors, run-completion checks). Same pattern as
:class:`ChildWorkflowCoordinator` — the coupling is intentional
because the loop driver is one of several collaborators the engine
composes around the same set of repos + publishers.
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.events import (
    EventTopic,
    LoopBodyDispatched,
    LoopExitDispatched,
)
from app.layer1_domain.exceptions import ExpressionError, InputRequirementError, LoopCollectionTypeError
from app.layer2_application.node_handlers.base import invoke_handler
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.resolution_scope import get_current_scope
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    LoopIterationAction,
)


class LoopIterationDriver:
    """Owns the LOOP node ↔ body-iteration coordination."""

    def __init__(
        self,
        *,
        engine: Any,  # OrchestrationEngine — for orchestration callbacks
        run_repo: Any,
        task_repo: Any,
        handler_registry: Any,
        task_factory: Any,
        monitor: Any = None,
        event_driven_loop_driver: Any = None,
    ) -> None:
        self._engine = engine
        self._run_repo = run_repo
        self._task_repo = task_repo
        self._handler_registry = handler_registry
        self._task_factory = task_factory
        self._monitor = monitor
        self._event_driven_loop_driver = event_driven_loop_driver

    # ------------------------------------------------------------------
    # Body-completion hook
    # ------------------------------------------------------------------

    async def maybe_enqueue_loop_iteration(
        self, completed_task: Task, run: WorkflowRun,
    ) -> None:
        """Body-completion hook for event-driven loops.

        Gate conditions (all must hold for the hook to act):
        - The engine has an event-driven loop driver wired.
        - The completed task has a ``scope_stack`` with a non-empty
          innermost ``invocation_id`` (i.e., it's a task inside an
          event-driven loop's body).
        - The innermost scope's ``loop_node_id`` maps to an active
          loop in ``run.context.loop_state`` (present AND
          ``len(completed_iterations) < total``).

        When the gate passes, the helper:
        1. Records the task's ``node_id`` in
           ``loop_state["iter_progress"][iter_idx]``.
        2. Sweeps any cascade-skipped nodes in
           ``run.context.skipped_nodes`` into the same progress
           list — handles CONDITION / SWITCH gated bodies where the
           unchosen branch is skipped and must count as "done".
        3. Runs the runtime BFS via ``is_iteration_done`` to check
           if every reachable body node is tracked in the progress
           list.
        4. If yes, enqueues a ``LoopIterationAction`` so the worker
           dispatches :meth:`orchestrate_iteration` to advance.

        No-op for:
        - Non-loop-body tasks (no scope_stack or empty invocation_id).
        - Tasks whose scope doesn't match an active loop.
        - Already-terminated loops.
        """
        if self._event_driven_loop_driver is None:
            return
        # TERMINAL-STATUS GATE. This hook is called from several sites; two of
        # them pass a task that is NOT yet terminal:
        #   * orchestrate_execute_node (~2309) fires at body DISPATCH, when a
        #     WORKER body is only RUNNING (it was sent to the worker, hasn't run).
        # Because ``iteration_done`` decides purely from ``iter_progress`` (it
        # never checks completed_nodes), recording a RUNNING body there would
        # advance the loop BEFORE the body actually ran — and, combined with the
        # real completion firing again via orchestrate_continue, enqueue the
        # iteration TWICE (completed_iterations = [0, 1, 1] → corruption / hang).
        # Only a genuinely finished body (COMPLETED / FAILED) may advance the
        # loop. An inline BUILTIN body is already COMPLETED at its dispatch-site
        # call, so it still advances; a worker body advances only via its
        # completion path.
        status = getattr(completed_task, "status", None)
        if status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return
        scope = completed_task.get_innermost_scope()
        if scope is None or not scope.invocation_id:
            return
        loop_state = run.context.loop_state.get(scope.loop_node_id)
        if loop_state is None:
            return
        total = loop_state.get("total", 0)
        if len(loop_state.get("completed_iterations", [])) >= total:
            return  # loop already done

        iter_idx = int(scope.iter)
        iter_progress = loop_state.setdefault("iter_progress", {})
        progress = iter_progress.setdefault(iter_idx, [])
        if completed_task.node_id not in progress:
            progress.append(completed_task.node_id)

        # Track failed body tasks per iteration so iteration_complete
        # can set outcome="failed" on LoopIterationCompleted.
        if completed_task.status == TaskStatus.FAILED:
            iter_failed = loop_state.setdefault("iter_failed_nodes", {})
            failed_list = iter_failed.setdefault(iter_idx, [])
            if completed_task.node_id not in failed_list:
                failed_list.append(completed_task.node_id)

        # Sweep any cascade-skipped nodes into progress. The runtime
        # BFS (below) only visits actual body nodes, so adding
        # non-body skipped nodes is harmless — they're never checked.
        for skipped_id in self._engine._view_skipped(run):
            if skipped_id not in progress:
                progress.append(skipped_id)

        # Runtime BFS to check if the iteration is done.
        workflow = (
            self._engine.load_workflow_for_run(run)
            if self._engine._workflow_snapshot_repo else None
        )
        topology = run.context.topology
        if workflow is None or topology is None:
            return

        if run.decide_loop_iteration(
            loop_node_id=scope.loop_node_id,
            iter_idx=iter_idx,
            workflow=workflow,
            topology=topology,
        ):
            await self._engine._queue.enqueue(
                LoopIterationAction(
                    run_id=run.id,
                    task_id=completed_task.id,
                    loop_node_id=scope.loop_node_id,
                    iteration_index=iter_idx,
                )
            )

    # ------------------------------------------------------------------
    # Iteration advancement (called from the worker)
    # ------------------------------------------------------------------

    async def orchestrate_iteration(
        self,
        run_id: str,
        task_id: str,
        loop_node_id: str,
        iteration_index: int,
    ) -> None:
        """Feed a completed loop iteration back to the loop handler.

        The handler decides what comes next: more body tasks (next
        iteration) or exit tasks (loop done). Called by the
        ``OrchestrationWorker`` when a ``LoopIterationAction`` is
        dequeued.
        """
        async with self._engine.locked_run(run_id):
            run = await self._run_repo.aget_by_id(run_id)
            if not run or run.status in {
                RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
            }:
                return
            workflow = self._engine.load_workflow_for_run(run)
            if not workflow:
                return
            loop_node = workflow.get_node(loop_node_id)
            if not loop_node:
                return

            # Idempotency guard. The orchestration queue is at-least-once, so a
            # LoopIterationAction for an ALREADY-advanced iteration can be
            # redelivered — or double-enqueued by two hook sites firing for one
            # completion (e.g. an inline-completing sub-workflow body on an
            # aliasing repo). Advancing again would append a duplicate index in
            # ``iteration_complete`` (event_driven_loop_driver.py: completed.append
            # is unguarded), double-counting completed_iterations → skipped items
            # / premature exit / non-terminating run. Drop the duplicate.
            _ls = run.context.loop_state.get(loop_node_id)
            if _ls is not None and iteration_index in _ls.get(
                "completed_iterations", []
            ):
                return

            topology = run.context.topology
            handler = self._handler_registry.get(loop_node.node_type)

            synthetic = self._build_synthetic_iteration_task(
                run_id, run, loop_node_id, iteration_index,
            )

            # Phase 5.2.2 — reconstruct the enclosing scope stack from
            # loop_state so successor tasks created during
            # iteration_complete (exit task, next body) inherit the
            # OUTER scopes via the contextvar. The next body task
            # itself explicitly overrides its scope_stack in
            # ``_dispatch_iteration_body``; the exit task inherits the
            # outer scopes so it continues running in the correct
            # parent-loop context when the LOOP is nested.
            enclosing_payload = run.context.loop_state.get(
                loop_node_id, {},
            ).get("enclosing_scope_stack", [])
            enclosing_scopes: list[LoopScope] = (
                [LoopScope.from_dict(d) for d in enclosing_payload]
                if enclosing_payload else []
            )

            # Phase 3 Gate 3 — under executor mode the Executor pod
            # invokes the loop iteration handler and emits the
            # ``LoopIterationCompleted`` / ``NodeOutputProduced`` events.
            # The engine returns here; iteration coordination resumes
            # when the projector folds those events.
            if self._engine._is_executor_mode():
                self._engine._emit_node_input_for_executor(
                    node=loop_node, run=run, workflow=workflow,
                    task=synthetic, iteration_index=iteration_index,
                )
                await self._engine.drain_events()
                return

            # Hydrate the projector view into the aggregate, then activate
            # the run ResolutionScope before the loop handler RE-resolves
            # the continuation condition / items. ``orchestrate_iteration``
            # is a worker-dequeued ``orchestrate_*`` entry that takes
            # ``locked_run`` but — like the old ``orchestrate_execute_node``
            # before its fix — set no ambient scope. The loop driver's
            # resolver explicitly relies on it (see
            # ``EventDrivenLoopDriver._variables_view``: "consults the ambient
            # ResolutionScope set by the engine before each orchestrate_*
            # pass" for ``$variables`` / ``$variable`` / ``$parent`` /
            # ``$root``), so a while-condition loop gated on
            # ``{{$variables.x}}`` resolved fine on the first pass (init runs
            # under ``_exec_uniform``'s scope) but silently failed on every
            # later iteration here. ``__vars`` is view-authoritative (popped
            # from the aggregate at publish), so the scope must be built AFTER
            # this hydration — mirrors ``_exec_uniform`` steps 1b + scope-entry.
            if self._engine._view_store is not None:
                try:
                    _view = self._engine._view_store.get(run.id)
                except Exception:
                    _view = None
                if _view is not None:
                    _ctx_vars = run.context.variables
                    for _vk, _vv in _view.variables.items():
                        _ctx_vars.setdefault(_vk, _vv)
            iter_scope_tokens = None
            if get_current_scope() is None:
                try:
                    iter_scope_tokens = self._engine._enter_run_scope(run.id, run)
                except Exception:
                    iter_scope_tokens = None
            try:
                iter_result = await invoke_handler(
                    handler,
                    monitor=self._monitor,
                    node=loop_node, run=run, workflow=workflow,
                    topology=topology, completed_task=synthetic,
                    task_factory=self._task_factory,
                    current_scope_stack=enclosing_scopes,
                )
            except (
                ExpressionError, InputRequirementError, LoopCollectionTypeError,
            ) as exc:
                # SA-1977 — a LOOP pointed at a non-collection is an AUTHORING
                # error, exactly like an unresolvable expression, so it must land
                # the same way: NodeDispatched + NodeFailed, node red on the
                # canvas with the message. Escaping to the run-level catch-all
                # instead failed the RUN while leaving the LOOP node READY and
                # non-terminal — a change whose whole purpose is to POINT AT the
                # mistake must not leave the mistake unmarked.
                loop_task = await self._engine._find_tracking_task(
                    run_id, loop_node_id,
                )
                if loop_task:
                    await self._engine._mark_task_failed(
                        loop_task, loop_node, run, str(exc),
                    )
                await self._run_repo.asave(run)
                await self._engine.drain_events()
                await self._engine.check_run_completion(run, workflow)
                await self._engine.drain_events()
                return
            finally:
                if iter_scope_tokens is not None:
                    self._engine._exit_run_scope(iter_scope_tokens)

            if iter_result.loop_state_updates:
                run.context.loop_state.update(iter_result.loop_state_updates)
            await self._engine.apply_state_updates_as_files(
                iter_result.state_updates, run, workflow,
                source_node_id=loop_node_id,
                output_port=iter_result.output_port or "",
                attempt_id=synthetic.id,
            )
            self._engine.publish_events(iter_result.events)

            loop_state = run.context.loop_state.get(loop_node_id, {})
            completed_count = len(loop_state.get("completed_iterations", []))
            total = loop_state.get("total", 0)

            self._cleanup_iteration_state(
                loop_state, iteration_index, total, loop_node_id,
                topology, workflow, run, completed_count,
            )

            if completed_count >= total:
                await self._dispatch_exit_tasks(
                    run, loop_node_id, task_id, iter_result.tasks_to_dispatch,
                )
                run.context.completed_nodes.add(loop_node_id)
                # Drive the loop node's own tracking task terminal. The
                # loop-body runner (``_run_loop_body``) leaves it READY — it
                # only ships the body tasks — so without this the run never
                # quiesces (``decide_completion`` sees a non-terminal task)
                # and the canvas shows the loop node stuck READY even though
                # every iteration + the exit node finished. All done-reasons
                # (all_done / condition_false / break) converge here because
                # they set ``total`` so ``completed_count >= total``.
                loop_task = await self._engine._find_tracking_task(
                    run_id, loop_node_id,
                )
                if loop_task is not None:
                    await self._engine._mark_task_completed(loop_task, run)
            else:
                await self._dispatch_next_iteration_body(
                    run, loop_node_id, task_id, iter_result.tasks_to_dispatch,
                )

            await self._run_repo.asave(run)
            await self._engine.drain_events()
            await self._engine.check_run_completion(run, workflow)
            await self._engine.drain_events()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_synthetic_iteration_task(
        self,
        run_id: str,
        run: WorkflowRun,
        loop_node_id: str,
        iteration_index: int,
    ) -> Task:
        """Build a fake completed task representing the iteration.

        ``scope_stack`` is set so ``iteration_complete`` (the loop
        handler) can read the iter index without needing a real body
        task to inspect.
        """
        loop_state = run.context.loop_state.get(loop_node_id, {})
        return Task(
            id=f"{run_id}-{loop_node_id}-iter-{iteration_index}",
            node_id=loop_node_id,
            run_id=run_id,
            status=TaskStatus.COMPLETED,
            scope_stack=[LoopScope(
                loop_node_id=loop_node_id,
                invocation_id=loop_state.get("invocation_id", ""),
                iter=iteration_index,
                total=loop_state.get("total", 0),
            )],
            run_generation=run.generation,
        )

    def _cleanup_iteration_state(
        self,
        loop_state: dict,
        iteration_index: int,
        total: int,
        loop_node_id: str,
        topology: Any,
        workflow: Any,
        run: WorkflowRun,
        completed_count: int,
    ) -> None:
        """Phase 5.2.2 iter-boundary cleanup.

        Drop the finished iteration's iter_progress bucket. If
        advancing to a new iteration, BFS the body subgraph and wipe
        every reached node from ``completed_nodes`` + ``skipped_nodes``
        so they're re-runnable (fixes the latent multi-node-body
        re-execution bug where the engine's completion guard refused
        to re-dispatch nodes already in ``completed_nodes``).
        """
        iter_progress = loop_state.setdefault("iter_progress", {})
        iter_progress.pop(iteration_index, None)
        iter_failed = loop_state.get("iter_failed_nodes", {})
        iter_failed.pop(iteration_index, None)

        if completed_count < total:
            # Initialise next iter's progress bucket.
            new_iter = loop_state.get("current_index", iteration_index + 1)
            iter_progress.setdefault(new_iter, [])

            # Iter-boundary body clear: wipe body nodes so they're
            # re-runnable next iteration.
            body_nodes = topology.body_subgraph(loop_node_id, workflow,
            )
            for body_nid in body_nodes:
                run.context.completed_nodes.discard(body_nid)
                run.context.skipped_nodes.discard(body_nid)

    async def _dispatch_exit_tasks(
        self,
        run: WorkflowRun,
        loop_node_id: str,
        causation_task_id: str,
        exit_tasks: list,
    ) -> None:
        """All iterations done — enqueue exit tasks and announce LOOP-exit
        fan-out. The DispatcherConsumer emits EdgeTraversed for each
        LOOP → exit edge."""
        view_completed = self._engine._view_completed(run)
        view_skipped = self._engine._view_skipped(run)
        dispatched_exit_ids: list[str] = []
        for exit_task in exit_tasks:
            if (
                exit_task.node_id not in view_completed
                and exit_task.node_id not in view_skipped
            ):
                await self._task_repo.asave(exit_task)
                dispatched_exit_ids.append(exit_task.node_id)
                await self._engine._queue.enqueue(
                    ExecuteNodeAction(run_id=run.id, task_id=exit_task.id),
                )
        if dispatched_exit_ids:
            self._engine.publish_event(
                EventTopic.LOOP_EXIT_DISPATCHED,
                LoopExitDispatched(
                    run_id=run.id,
                    workflow_id=run.workflow_id,
                    loop_node_id=loop_node_id,
                    exit_node_ids=list(dispatched_exit_ids),
                    causation_id=causation_task_id,
                    root_run_id=run.root_run_id or run.id,
                    depth=run.depth_level,
                    source="workflow-control-plane",
                ),
            )

    async def _dispatch_next_iteration_body(
        self,
        run: WorkflowRun,
        loop_node_id: str,
        causation_task_id: str,
        body_tasks: list,
    ) -> None:
        """More iterations remain — enqueue next body tasks and announce
        the LOOP-body fan-out. Causation is the triggering body task id
        so deterministic event ids differ per iteration."""
        next_iter_skipped = self._engine._view_skipped(run)
        dispatched_body_ids: list[str] = []
        next_iter_index: int | None = None
        for new_body in body_tasks:
            if new_body.node_id in next_iter_skipped:
                continue
            # Allow re-executing the same body node on each iteration.
            run.context.completed_nodes.discard(new_body.node_id)
            await self._task_repo.asave(new_body)
            dispatched_body_ids.append(new_body.node_id)
            if next_iter_index is None and new_body.scope_stack:
                next_iter_index = new_body.scope_stack[-1].iter
            await self._engine._queue.enqueue(
                ExecuteNodeAction(run_id=run.id, task_id=new_body.id),
            )
        if dispatched_body_ids:
            self._engine.publish_event(
                EventTopic.LOOP_BODY_DISPATCHED,
                LoopBodyDispatched(
                    run_id=run.id,
                    workflow_id=run.workflow_id,
                    loop_node_id=loop_node_id,
                    body_node_ids=list(dispatched_body_ids),
                    iteration_index=next_iter_index,
                    causation_id=causation_task_id,
                    root_run_id=run.root_run_id or run.id,
                    depth=run.depth_level,
                    source="workflow-control-plane",
                ),
            )
