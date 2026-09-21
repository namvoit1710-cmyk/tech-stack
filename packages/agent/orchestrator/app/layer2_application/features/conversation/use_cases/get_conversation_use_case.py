from dataclasses import dataclass, field
from typing import Optional

from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
from app.layer2_application.interfaces.agent_event_repository import IAgentEventRepository
from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor


@dataclass
class GetConversationInput:
    conversation_id: str


@dataclass
class GetConversationError:
    conversation_id: str = ""
    error: Optional[str] = None


@dataclass
class GetConversationOutput:
    conversation_id: str
    events: list[AgentEvent] = field(default_factory=list)


class GetConversationUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        agent_event_repo: IAgentEventRepository,
    ):
        self.logger = logger
        self.monitor = monitor
        self.agent_event_repo = agent_event_repo

    def execute(self, request: GetConversationInput) -> GetConversationOutput | GetConversationError:
        if self.agent_event_repo is None:
            raise RuntimeError("agent_event_repo is required but was not provided")
        events = self.agent_event_repo.get_events_by_conversation_id(request.conversation_id)
        if not events:
            return GetConversationError(
                error="Conversation not found",
                conversation_id=request.conversation_id,
            )
        return GetConversationOutput(
            conversation_id=request.conversation_id,
            events=events,
        )
