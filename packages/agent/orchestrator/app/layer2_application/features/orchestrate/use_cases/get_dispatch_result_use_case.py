from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from agent_sdk.layer2_application.interfaces.agent_shared_state_repository import IAgentSharedStateRepository
from agent_sdk.layer1_domain.entities.agent_shared_state import AgentSharedState, Task, TaskStatus


@dataclass
class GetDispatchResultInput:
    conv_id: str


@dataclass
class GetDispatchResultOutput:
    conv_id: str = ""
    shared_state: Optional[AgentSharedState] = None
    current_task: Optional[Task] = None
    error: Optional[str] = None


class GetDispatchResultUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        orchestration_state_repo: IAgentSharedStateRepository,
    ):
        self.logger = logger
        self.monitor = monitor
        self.orchestration_state_repo = orchestration_state_repo

    def execute(self, request: GetDispatchResultInput) -> GetDispatchResultOutput:
        conversation_state = self.orchestration_state_repo.get_conversation_by(request.conv_id)
        if conversation_state is None:
            return GetDispatchResultOutput(
                conv_id=request.conv_id,
                error="Shared state not found",
            )

        shared_state = conversation_state.shared_state
        executions = shared_state.executions
        current_task = self._select_current_task(executions)
        if not current_task:
            return GetDispatchResultOutput(
                conv_id=request.conv_id,
                shared_state=shared_state,
                error="No tasks found in shared state",
            )

        self.logger.info(
            "Loaded dispatch shared state",
            conv_id=request.conv_id,
            execution_count=len(executions),
        )
        return GetDispatchResultOutput(
            conv_id=conversation_state.conv_id,
            shared_state=shared_state,
            current_task=current_task,
        )

    @staticmethod
    def _select_current_task(executions: list[Task]) -> Task | None:
        for task in executions:
            if task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING}:
                return task
        return executions[-1] if executions else None