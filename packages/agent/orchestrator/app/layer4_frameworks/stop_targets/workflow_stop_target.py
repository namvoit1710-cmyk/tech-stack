"""SA-892 stop target: stop every in-flight workflow run of the conversation (phase 1).

Resolves run ids from the AUTHORITATIVE agent shared state
(``Metadata.current_run_id``) unioned with the orchestrator's in-process
``conv_id -> set[run_id]`` index, then dispatches a ``workflow_stop`` STEP BATCH to
the executor runtime per run (the executor resolves the tool + performs the
control-plane cancel). ``runs_cancelled`` records the run ids for which a stop step
was successfully DISPATCHED. Best-effort: read/dispatch failures are recorded, never
raised; one dispatch failure never stops the others.

The workflow-stop ``execute_by`` is discovered by NAME from the registry at runtime
(``WORKFLOW_STOP_EXECUTE_BY`` override wins) — no per-environment tool id in config.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from agent_sdk.layer2_application.interfaces.message_publisher import IMessagePublisher
from app.layer2_application.interfaces.stop_target import (
    StopContext,
    StopTargetResult,
)

EXECUTOR_STEP_EVENT_TYPE = "agent.request.executor"
EXECUTOR_STEP_PAYLOAD_TYPE = "step_batch"
EXECUTOR_STEP_PAYLOAD_TO = "executor.runtime"
ORCHESTRATOR_STOP_FROM = "orchestrator.stop"
DEFAULT_EXECUTOR_TOPIC = "executor.topic"
DEFAULT_WORKFLOW_STOP_TOOL_NAME = "workflow_stop"


class WorkflowStopTarget:
    """Dispatch a ``workflow_stop`` step per in-flight run of the conversation."""

    name = "workflow_stop"
    phase = 1

    def __init__(
        self,
        publisher: IMessagePublisher,
        settings: Any,
        logger: Any,
        agent_registry_client: Optional[Any] = None,
        uuid_builder: Optional[Any] = None,
        conversation_run_index: Optional[Any] = None,
        orchestration_state_repo: Optional[Any] = None,
    ) -> None:
        self._publisher = publisher
        self._settings = settings
        self._logger = logger
        self._agent_registry_client = agent_registry_client
        self._uuid_builder = uuid_builder
        self._conversation_run_index = conversation_run_index
        self._repo = orchestration_state_repo

    async def stop(self, ctx: StopContext) -> StopTargetResult:
        result = StopTargetResult(target=self.name)
        conv_id = ctx.conv_id

        to_cancel: list[str] = []  # dispatch order, deduped below
        current_run_id = self._current_run_id(conv_id, result)
        if current_run_id:
            to_cancel.append(current_run_id)
        to_cancel.extend(sorted(self._index_run_ids(conv_id)))

        # Resolve the workflow-stop execute_by ONCE (override, else registry-by-name).
        # If unresolved, SKIP the workflow half and record why (never silently drop).
        execute_by = await self._resolve_workflow_stop_execute_by()
        if not execute_by:
            if any(r for r in to_cancel):
                msg = (
                    "workflow-stop capability unresolved (no WORKFLOW_STOP_EXECUTE_BY "
                    "override and registry lookup returned no workflow_stop tool); "
                    "workflow runs NOT stopped"
                )
                self._logger.warning(
                    msg, conv_id=conv_id, runs=[r for r in to_cancel if r]
                )
                result.errors.append(msg)
            return result

        dispatched: set[str] = set()
        for run_id in to_cancel:
            if not run_id or run_id in dispatched:
                continue  # dedupe union
            dispatched.add(run_id)
            try:
                await self._dispatch_workflow_stop_step(conv_id, run_id, execute_by)
            except Exception as exc:  # one failure must not stop the others
                self._logger.warning(
                    "Workflow-stop step dispatch failed",
                    conv_id=conv_id,
                    run_id=run_id,
                    error=str(exc),
                )
                result.errors.append(f"run {run_id} stop dispatch failed: {exc}")
                continue
            result.runs_cancelled.append(run_id)
        return result

    async def _resolve_workflow_stop_execute_by(self) -> Optional[str]:
        """Resolve the workflow-stop step's ``execute_by`` capability id.

        1. EXPLICIT OVERRIDE — non-empty ``settings.WORKFLOW_STOP_EXECUTE_BY`` used
           verbatim (tests / pinning a ``tool.<uuid>``).
        2. REGISTRY DISCOVERY — resolve the ``workflow_stop`` tool id BY NAME via
           ``agent_registry_client.get_tool_id_by_name`` and build ``tool.<id>``
           (mirrors how ``workflow_start_run`` is discovered; no per-env config).
        3. UNRESOLVED — return ``None``; caller skips the workflow half. Never raises.
        """
        override = (
            getattr(self._settings, "WORKFLOW_STOP_EXECUTE_BY", "") or ""
        ).strip()
        if override:
            return override

        client = self._agent_registry_client
        getter = getattr(client, "get_tool_id_by_name", None)
        if client is None or not callable(getter):
            return None

        tool_name = (
            getattr(self._settings, "WORKFLOW_STOP_TOOL_NAME", "")
            or DEFAULT_WORKFLOW_STOP_TOOL_NAME
        )
        try:
            tool_id = await getter(tool_name)
        except Exception as exc:  # lookup failure must not break the stop
            self._logger.warning(
                "workflow-stop tool lookup failed",
                tool_name=tool_name,
                error=str(exc),
            )
            return None

        if not tool_id:
            return None
        return f"tool.{tool_id}"

    async def _dispatch_workflow_stop_step(
        self, conv_id: str, run_id: str, execute_by: str
    ) -> None:
        """Publish a ``workflow_stop`` STEP BATCH to the executor runtime.

        Mirrors the business-agent's ``send_task_to_executor`` envelope (executor
        ``validate_step_batch`` contract): a step_batch event keyed by ``conv_id``
        with one step whose ``execute_by`` is the resolved workflow-stop capability
        and ``input_data`` carries the ``run_id``.
        """
        topic = (
            getattr(self._settings, "EXECUTOR_TOPIC", "") or DEFAULT_EXECUTOR_TOPIC
        )
        message_id = self._new_id()
        event = {
            "message_id": message_id,
            "type": EXECUTOR_STEP_EVENT_TYPE,
            "conv_id": conv_id,
            "correlation_id": conv_id,
            "payload": {
                "from": ORCHESTRATOR_STOP_FROM,
                "to": EXECUTOR_STEP_PAYLOAD_TO,
                "type": EXECUTOR_STEP_PAYLOAD_TYPE,
                "batch_id": message_id,
                "steps": [
                    {
                        "id": self._new_id(),
                        "execute_by": execute_by,
                        "goal": "Stop workflow run",
                        "message": "Stop workflow run",
                        "next_step_id": None,
                        "status": "PENDING",
                        "uploaded_file_ids": [],
                        "input_data": {"run_id": run_id},
                        "output_data": {},
                    }
                ],
            },
        }
        await self._publisher.publish(topic, message=event, key=conv_id)

    def _new_id(self) -> str:
        """A unique id for the step-batch envelope (uuid7 when a builder is wired,
        else uuid4). Never raises — id generation must not break the stop."""
        builder = self._uuid_builder
        if builder is not None:
            create = getattr(builder, "create_uuid7", None)
            if callable(create):
                try:
                    return str(create())
                except Exception:  # fall through to uuid4
                    pass
        return str(uuid.uuid4())

    def _current_run_id(self, conv_id: str, result: StopTargetResult) -> str:
        """The conversation's active run id from agent shared state (authoritative).

        Best-effort: a missing repo / conversation / failing read never breaks the
        stop — the failure is recorded in ``result.errors`` and logged.
        """
        repo = self._repo
        if repo is None:
            return ""
        try:
            metadata = repo.get_metadata(conv_id)
        except Exception as exc:  # record, never abort the fan-out
            self._logger.warning(
                "Shared-state metadata lookup failed", conv_id=conv_id, error=str(exc)
            )
            result.errors.append(f"shared-state lookup failed: {exc}")
            return ""
        if metadata is None:
            return ""
        return str(getattr(metadata, "current_run_id", "") or "")

    def _index_run_ids(self, conv_id: str) -> set[str]:
        """Run ids observed for the conversation, from the orchestrator index.

        Best-effort: a missing index or a failing read never breaks the stop.
        """
        index = self._conversation_run_index
        if index is None:
            return set()
        try:
            return set(index.get_run_ids(conv_id) or set())
        except Exception as exc:  # record, never abort the fan-out
            self._logger.warning(
                "Run index lookup failed", conv_id=conv_id, error=str(exc)
            )
            return set()
