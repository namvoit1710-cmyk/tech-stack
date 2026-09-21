"""SA-892 stop-target adapters (layer4) — concrete ``StopTarget`` implementations.

Also the composition-root factory for the DEFAULT stop fan-out, so the use case
(layer2) never imports these concrete adapters — it receives the list.
"""

from __future__ import annotations

from typing import Any, Optional

from app.layer2_application.interfaces.stop_target import StopTarget
from app.layer4_frameworks.stop_targets.agent_control_stop_target import (
    AgentControlStopTarget,
)
from app.layer4_frameworks.stop_targets.task_cancel_stop_target import (
    TaskCancelStopTarget,
)
from app.layer4_frameworks.stop_targets.workflow_stop_target import (
    WorkflowStopTarget,
)

__all__ = [
    "AgentControlStopTarget",
    "TaskCancelStopTarget",
    "WorkflowStopTarget",
    "build_default_stop_targets",
]


def build_default_stop_targets(
    *,
    publisher: Any,
    settings: Any,
    logger: Any,
    agent_registry_client: Optional[Any] = None,
    uuid_builder: Optional[Any] = None,
    conversation_run_index: Optional[Any] = None,
    orchestration_state_repo: Optional[Any] = None,
) -> list[StopTarget]:
    """The default SA-892 stop fan-out, ordered by phase (task-cancel first).

    Built at the composition root from the wired collaborators and injected into
    ``StopConversationUseCase`` — keeps the use case free of any layer4 dependency.
    """
    return [
        TaskCancelStopTarget(
            orchestration_state_repo=orchestration_state_repo, logger=logger
        ),
        AgentControlStopTarget(publisher=publisher, settings=settings, logger=logger),
        WorkflowStopTarget(
            publisher=publisher,
            settings=settings,
            logger=logger,
            agent_registry_client=agent_registry_client,
            uuid_builder=uuid_builder,
            conversation_run_index=conversation_run_index,
            orchestration_state_repo=orchestration_state_repo,
        ),
    ]
