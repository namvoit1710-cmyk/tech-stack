"""SA-892 stop target: cancel the CURRENT MESSAGE's in-flight tasks (phase 0).

Runs FIRST so that, before the external fan-out, the current message's non-terminal
tasks are marked CANCELLED — a late continuation / step-result is then dropped by the
business-agent's per-task guard rather than resurrecting the message. Scope is the
task ids the message registered in ``conversation_context`` (message.tasks); if the
message is unresolvable there, fall back to all non-terminal tasks. Best-effort:
every read/write failure is recorded in the result, never raised.
"""

from __future__ import annotations

from typing import Any, Optional

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from app.layer2_application.interfaces.stop_target import (
    StopContext,
    StopTargetResult,
)

_TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCESS,
        TaskStatus.ERROR,
        TaskStatus.FAIL,
        TaskStatus.CANCELLED,
    }
)


class TaskCancelStopTarget:
    """Mark the current message's non-terminal tasks CANCELLED (task-scoped stop)."""

    name = "task_cancel"
    phase = 0

    def __init__(self, orchestration_state_repo: Optional[Any], logger: Any) -> None:
        self._repo = orchestration_state_repo
        self._logger = logger

    async def stop(self, ctx: StopContext) -> StopTargetResult:
        result = StopTargetResult(target=self.name)
        conv_id = ctx.conv_id
        repo = self._repo
        if repo is None:
            return result

        get_conversation = getattr(repo, "get_conversation", None)
        if not callable(get_conversation):
            return result
        try:
            conversation = get_conversation(conv_id)
        except Exception as exc:  # record, never abort the stop
            self._logger.warning(
                "Cancel tasks lookup failed", conv_id=conv_id, error=str(exc)
            )
            result.errors.append(f"cancel tasks lookup failed: {exc}")
            return result
        if conversation is None:
            return result

        shared_state = getattr(conversation, "shared_state", None)
        metadata = getattr(shared_state, "metadata", None)
        executions = list(getattr(shared_state, "executions", None) or [])
        if not executions:
            return result

        current_message_id = str(getattr(metadata, "current_message_id", "") or "")
        scoped_task_ids = self._current_message_task_ids(
            shared_state, current_message_id
        )
        use_fallback = scoped_task_ids is None
        if use_fallback:
            self._logger.info(
                "SA-892 stop: current message not resolvable in conversation_context; "
                "falling back to cancelling all non-terminal tasks",
                conv_id=conv_id,
                message_id=current_message_id,
            )

        for task in executions:
            status = getattr(task, "status", None)
            if status in _TERMINAL_TASK_STATUSES:
                continue
            task_id = getattr(task, "id", None)
            if not task_id:
                continue
            if not use_fallback and str(task_id) not in scoped_task_ids:
                continue
            try:
                repo.update_task(conv_id, task_id, status=TaskStatus.CANCELLED)
            except Exception as exc:  # per-task best-effort; never abort the others
                self._logger.warning(
                    "Cancel task failed",
                    conv_id=conv_id,
                    task_id=task_id,
                    error=str(exc),
                )
                result.errors.append(f"cancel task {task_id} failed: {exc}")
                continue
            result.tasks_cancelled.append(task_id)

        if result.tasks_cancelled:
            self._logger.info(
                "SA-892 stop: current-message tasks cancelled",
                conv_id=conv_id,
                message_id=current_message_id,
                tasks_cancelled=len(result.tasks_cancelled),
            )
        return result

    @staticmethod
    def _current_message_task_ids(
        shared_state: Any, message_id: str
    ) -> Optional[set[str]]:
        """Task ids the current message registered in conversation_context.

        Returns the message's ``task_id`` set when present (possibly empty — the
        message exists but registered no tasks yet -> cancel nothing); returns
        ``None`` when the message cannot be resolved (no id / absent / no context)
        so the caller falls back to all non-terminal tasks. Never raises.
        """
        if not message_id:
            return None
        context = getattr(shared_state, "context", None)
        conversation_context = getattr(context, "conversation_context", None)
        if not conversation_context:
            return None
        for message in conversation_context:
            if getattr(message, "message_id", None) != message_id:
                continue
            return {
                str(getattr(th, "task_id", "") or "")
                for th in (getattr(message, "tasks", None) or [])
                if getattr(th, "task_id", None)
            }
        return None
