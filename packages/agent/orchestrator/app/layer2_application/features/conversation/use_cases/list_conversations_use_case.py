from dataclasses import dataclass, field

from agent_sdk.layer2_application.interfaces.agent_shared_state_repository import IAgentSharedStateRepository
from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from app.layer2_application.interfaces.agent_event_repository import IAgentEventRepository
from app.layer2_application.interfaces.conversation_summary_repository import IConversationSummaryRepository


@dataclass
class ListConversationsInput:
    user_id: str = ""
    tenant_id: str = "system"
    page_index: int = 1
    page_size: int = 10


@dataclass
class ConversationSummaryItem:
    conversation_id: str
    title: str
    timestamp: str


@dataclass
class ListConversationsOutput:
    conversations: list[ConversationSummaryItem] = field(default_factory=list)
    page_index: int = 1
    page_size: int = 10


class ListConversationsUseCase:
    DEFAULT_PAGE_SIZE = 10

    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        agent_event_repo: IAgentEventRepository,
        conversation_summary_repo: IConversationSummaryRepository | None = None,
        orchestration_state_repo: IAgentSharedStateRepository | None = None,
    ):
        self.logger = logger
        self.monitor = monitor
        self.agent_event_repo = agent_event_repo
        self.conversation_summary_repo = conversation_summary_repo
        self.orchestration_state_repo = orchestration_state_repo

    def execute(self, request: ListConversationsInput) -> ListConversationsOutput:
        page_index = max(request.page_index, 1)
        page_size = max(request.page_size, 1)
        offset = (page_index - 1) * page_size
        latest_events = self.agent_event_repo.list_latest_conversation_events(
            skip=offset,
            top=page_size,
        )
        conv_ids = [event.conv_id for event in latest_events if event.conv_id]
        saved_titles = self.conversation_summary_repo.get_titles(conv_ids) if self.conversation_summary_repo is not None else {}

        return ListConversationsOutput(
            conversations=[
                ConversationSummaryItem(
                    conversation_id=event.conv_id,
                    title=self._title_for_event(event, saved_titles),
                    timestamp=event.timestamp.isoformat(),
                )
                for event in latest_events
            ],
            page_index=page_index,
            page_size=page_size,
        )

    def _title_for_event(self, event, saved_titles) -> str:
        saved_title = saved_titles.get(event.conv_id)
        if saved_title is not None and saved_title.title:
            return saved_title.title

        if self.orchestration_state_repo is not None:
            conversation = self.orchestration_state_repo.get_conversation_by(event.conv_id)
            if conversation is not None:
                conversation_context = getattr(conversation.shared_state.context, "conversation_context", None)
                first_message = (
                    conversation_context[0].message if conversation_context else None
                )
                if isinstance(first_message, str) and first_message:
                    return first_message

        payload = event.payload if isinstance(event.payload, dict) else {}
        content = payload.get("content")
        if isinstance(content, str) and content:
            return content

        return "Untitled conversation"
