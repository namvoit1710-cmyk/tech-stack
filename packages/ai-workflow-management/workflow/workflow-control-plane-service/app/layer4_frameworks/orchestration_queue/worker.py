"""Background worker that dequeues orchestration actions and processes them.

Runs as an ``asyncio.Task`` in the FastAPI lifespan.  Each action is
dispatched to the corresponding ``OrchestrationEngine.orchestrate_*``
method.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any, Optional

from app.layer2_application.interfaces.broker_client_interface import IBrokerClient
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    CheckRunCompletionAction,
    ContinueRunAction,
    EditNodeDataAction,
    ExecuteNodeAction,
    FailTaskAction,
    LoopIterationAction,
    ResumeRunAction,
    ResumeTaskAction,
    StartRunAction,
)
from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
    response_topic_for,
)

_logger = logging.getLogger(__name__)


class OrchestrationWorker:
    """Continuously dequeues and processes orchestration actions.

    Phase 3c: also dispatches ``EditNodeDataAction`` so PATCH
    ``/runs/{id}/nodes/{node_id}/data`` serialises at this single
    consumer, replacing the former ``RunLockRegistry`` in-process
    lock. The HTTP caller awaits a future in ``response_registry``
    keyed on the action's ``correlation_id``.
    """

    def __init__(
        self,
        queue: IOrchestrationQueue,
        engine: IOrchestrationEngine,
        *,
        edit_node_data_use_case: Any = None,
        response_registry: Optional[Any] = None,
        instance_id: str = "",
        broker_client: Optional[IBrokerClient] = None,
    ) -> None:
        self._queue = queue
        self._engine = engine
        self._edit_node_data = edit_node_data_use_case
        self._response_registry = response_registry
        # Phase M.3.3: when the action's ``origin_instance_id`` doesn't
        # match ours, the HTTP caller's future lives on a peer instance.
        # Publish the response onto that peer's response topic via the
        # broker; the peer's ``ResponseTopicListener`` resolves its local
        # registry. Single-instance deployments leave ``broker_client``
        # None and always take the local fast path.
        self._instance_id = instance_id
        self._broker = broker_client

    async def run_forever(self) -> None:
        """Main loop — call this from ``asyncio.create_task``."""
        _logger.info("Orchestration worker started")
        while True:
            try:
                action = await self._queue.dequeue()
                await self._dispatch(action)
            except Exception:
                _logger.exception("Orchestration worker error")

    async def _dispatch(self, action: Any) -> None:
        _logger.info(
            "Processing %s for run %s",
            action.action_type, action.run_id,
        )
        try:
            if isinstance(action, StartRunAction):
                await self._engine.orchestrate_start(action.run_id)

            elif isinstance(action, ContinueRunAction):
                await self._engine.orchestrate_continue(
                    action.run_id, action.task_id,
                    test_mode=action.test_mode,
                )

            elif isinstance(action, FailTaskAction):
                await self._engine.orchestrate_fail(
                    action.run_id, action.task_id,
                )

            elif isinstance(action, ResumeTaskAction):
                await self._engine.orchestrate_resume(
                    action.run_id, action.task_id,
                    test_mode=action.test_mode,
                )

            elif isinstance(action, ExecuteNodeAction):
                await self._engine.orchestrate_execute_node(
                    action.run_id, action.task_id,
                )

            elif isinstance(action, LoopIterationAction):
                await self._engine.orchestrate_loop_iteration(
                    action.run_id, action.task_id,
                    action.loop_node_id, action.iteration_index,
                )

            elif isinstance(action, ResumeRunAction):
                await self._engine.orchestrate_resume_run(action.run_id)

            elif isinstance(action, CheckRunCompletionAction):
                await self._engine.orchestrate_check_completion(action.run_id)

            elif isinstance(action, EditNodeDataAction):
                await self._dispatch_edit_node_data(action)

            else:
                _logger.warning("Unknown action type: %s", action.action_type)

        except Exception as e:
            # Preserve the request/response contract for EditNodeDataAction:
            # the HTTP caller is awaiting a future; surface the error there
            # rather than letting the worker loop swallow it.
            if isinstance(action, EditNodeDataAction):
                self._fail_edit_node_data(action, e)
            else:
                # Safety net for run-advancing actions. A thrown
                # ``orchestrate_*`` otherwise leaves the just-completed task
                # terminal but the run stuck RUNNING forever: this action was
                # already acked by the broker (no redelivery), and shadow mode
                # wires no completion backstop, so nothing else will ever
                # re-evaluate the run. Force the run terminal so it doesn't
                # hang — and so a hung child fails its parent tracking task.
                await self._force_fail_run_safely(action, e)
            _logger.exception(
                "Failed to process %s for run %s",
                action.action_type, action.run_id,
            )

    async def _force_fail_run_safely(self, action: Any, exc: Exception) -> None:
        """Force the run terminal after a failed orchestration action, never
        raising out of the worker loop.

        Skips actions with no ``run_id`` and engines that don't implement
        ``force_fail_run`` (e.g. ``MagicMock`` test doubles). If the safety net
        itself fails, the run may remain non-terminal — no worse than the
        pre-existing behaviour — so it is logged, not propagated.
        """
        run_id = getattr(action, "run_id", "")
        if not run_id:
            return
        force_fail = getattr(self._engine, "force_fail_run", None)
        if not callable(force_fail):
            return
        try:
            await force_fail(
                run_id,
                f"Orchestration failed while processing "
                f"{action.action_type}: {exc}",
            )
        except Exception:
            _logger.exception(
                "force_fail_run safety net failed for run %s — run may "
                "remain non-terminal",
                run_id,
            )

    async def _dispatch_edit_node_data(self, action: EditNodeDataAction) -> None:
        """Execute ``EditNodeDataCommand`` serially and resolve the HTTP
        caller's future.

        Same-run PATCHes serialise via the engine's per-run ``asyncio.Lock``.

        The default is ``ORCHESTRATION_WORKER_COUNT=4`` (app_config.py) — this
        docstring used to say 1, and read as "the queue is the serialisation
        point, the lock is belt and braces". It is not: with four workers the
        lock is the ONLY thing serialising same-run actions, and anything that
        writes the run aggregate without taking it races them. That misreading
        is how the completion callback came to write the aggregate unlocked;
        see ``run_save_retry.set_lock_provider``.
        """
        if self._edit_node_data is None:
            # Fail the HTTP caller's future immediately. Without this the
            # controller's ``asyncio.wait_for(future, timeout=30.0)`` ticks
            # the full 30s and surfaces an opaque TimeoutError — making it
            # look like a slow backend when the real cause is a missing
            # bootstrap dependency (check the startup logs for a
            # ``Feature 'edit_node_data' missing dependencies`` warning).
            _logger.error(
                "EditNodeDataAction received but worker was not wired "
                "with an edit_node_data use case (run_id=%s) — failing "
                "future immediately to avoid 30s HTTP timeout",
                action.run_id,
            )
            self._fail_edit_node_data(
                action,
                RuntimeError(
                    "edit_node_data use case is not configured on this "
                    "control-plane instance; check bootstrap logs for a "
                    "missing-dependency warning."
                ),
            )
            return
        try:
            # Acquire per-run lock only when the engine exposes a real
            # ``asyncio.Lock`` — tests with a ``MagicMock`` engine return a
            # mock that can't be entered, so we fall back to direct execution
            # (single-consumer queue serialises anyway in those cases).
            run_lock_obj: Any = None
            run_lock_fn = getattr(self._engine, "run_lock", None)
            if callable(run_lock_fn):
                candidate = run_lock_fn(action.run_id)
                if isinstance(candidate, asyncio.Lock):
                    run_lock_obj = candidate
            if run_lock_obj is not None:
                async with run_lock_obj:
                    result = await self._edit_node_data.execute(action.command)
            else:
                result = await self._edit_node_data.execute(action.command)
            self._resolve_edit_node_data(action, result)
        except Exception as exc:  # noqa: BLE001
            self._fail_edit_node_data(action, exc)
            raise

    def _resolve_edit_node_data(
        self, action: EditNodeDataAction, result: Any,
    ) -> None:
        if self._is_local_origin(action):
            if self._response_registry is not None:
                self._response_registry.resolve(action.correlation_id, result)
            return
        # Cross-instance: publish response to the origin's response topic.
        payload = {
            "correlation_id": action.correlation_id,
            "result": dataclasses.asdict(result),
            "error": None,
        }
        self._publish_response(action.origin_instance_id, payload)

    def _fail_edit_node_data(
        self, action: EditNodeDataAction, exc: BaseException,
    ) -> None:
        if self._is_local_origin(action):
            if self._response_registry is not None:
                self._response_registry.fail(action.correlation_id, exc)
            return
        payload = {
            "correlation_id": action.correlation_id,
            "result": None,
            "error": {
                "class": type(exc).__name__,
                "message": str(exc),
            },
        }
        self._publish_response(action.origin_instance_id, payload)

    def _is_local_origin(self, action: EditNodeDataAction) -> bool:
        # Empty origin means the action was enqueued by something that
        # doesn't know about cross-instance routing (tests, single-
        # instance deployments) — keep the local fast path.
        if not action.origin_instance_id:
            return True
        if not self._instance_id or self._broker is None:
            return True
        return action.origin_instance_id == self._instance_id

    def _publish_response(
        self, origin_instance_id: str, payload: dict,
    ) -> None:
        assert self._broker is not None  # guarded by _is_local_origin
        topic = response_topic_for(origin_instance_id)
        try:
            self._broker.publish_to_topic(
                topic, payload, key=payload.get("correlation_id"),
            )
            _logger.debug(
                "Published edit_node_data response %s to %s",
                payload.get("correlation_id"), topic,
            )
        except Exception:
            _logger.exception(
                "Failed to publish edit_node_data response to %s — origin "
                "instance %s will time out",
                topic, origin_instance_id,
            )
