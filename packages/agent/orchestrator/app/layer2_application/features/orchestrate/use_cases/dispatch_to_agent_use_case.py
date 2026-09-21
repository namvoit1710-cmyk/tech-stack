import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from agent_sdk.layer2_application.interfaces.message_publisher import IMessagePublisher
from agent_sdk.layer2_application.interfaces.agent_shared_state_repository import Conversation, IAgentSharedStateRepository
from agent_sdk.layer1_domain.entities.agent_shared_state import AgentSharedState, GlobalContext, Metadata, MetadataStatus, Task
from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import IUuidBuilder
from app.layer2_application.interfaces.conversation_summary_repository import ConversationTitleSource, ConversationTitleSummary, IConversationSummaryRepository
from agent_sdk import Settings

@dataclass
class DispatchToAgentInput:
    message: str
    conv_id: str = ""
    user_id: str = ""
    user_email: str = ""
    tenant_id: str = "default"
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchToAgentOutput:
    status: str = "queued"
    conv_id: str = ""
    correlation_id: str = ""
    request_id: str = ""
    error: Optional[str] = None


@dataclass(frozen=True)
class AgentRequestMessage:
    """L4 -> L3 agent request. Inlined from shared.layer1_domain.message_contracts."""
    agent_type: str = ""
    agent_name: str = ""
    timestamp: str = ""
    message: str = ""
    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
            "message": self.message,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentRequestMessage":
        return cls(
            agent_type=data.get("agent_type", ""),
            agent_name=data.get("agent_name", ""),
            message=data.get("message", ""),
            timestamp=data.get("timestamp", ""),
            parameters=data.get("parameters", {}),
        )


class DispatchToAgentUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        publisher: IMessagePublisher,
        settings: Settings,
        orchestration_state_repo: IAgentSharedStateRepository,
        uuid_builder: IUuidBuilder,
        conversation_summary_repo: IConversationSummaryRepository | None = None,
    ):
        self.logger = logger
        self.monitor = monitor
        self.publisher = publisher
        self.settings = settings
        self.orchestration_state_repo = orchestration_state_repo
        self.uuid_builder = uuid_builder
        self.conversation_summary_repo = conversation_summary_repo

    async def execute(self, request: DispatchToAgentInput) -> DispatchToAgentOutput:
        conv_id = request.conv_id or self.uuid_builder.create_uuid7()
        correlation_id = self.uuid_builder.create_uuid7()
        request_id = self.uuid_builder.create_uuid7()

        agent_type = self._get_parameter(request.parameters, "agent_type") or "workflow"
        agent_name = self._get_parameter(request.parameters, "agent_name")
        agent_id = self._get_parameter(request.parameters, "agent_id")

        self._persist_initial_shared_state(
            request=request,
            conv_id=conv_id,
            correlation_id=correlation_id,
            agent_type=agent_type,
            agent_name=agent_name,
            agent_id=agent_id,
        )
        self._save_fallback_title(conv_id, request.message)

        agent_message = AgentRequestMessage(
            agent_type=agent_type,
            agent_name=agent_name,
            message=request.message,
            timestamp=datetime.now(timezone.utc).isoformat(),
            parameters=dict(request.parameters),
        )

        asyncio.create_task(self.publisher.publish(self.settings.AGENT_REQUEST_TOPIC, message=asdict(agent_message)))
        self.logger.info(
            "Dispatch-to-agent request queued",
            conv_id=conv_id,
            correlation_id=correlation_id,
            agent_type=agent_type,
            agent_name=agent_name,
        )

        return DispatchToAgentOutput(
            status="queued",
            conv_id=conv_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )

    def _persist_initial_shared_state(
        self,
        *,
        request: DispatchToAgentInput,
        conv_id: str,
        correlation_id: str,
        agent_type: str,
        agent_name: str,
        agent_id: str,
    ) -> None:
        if self.orchestration_state_repo is None:
            return

        workflow_id = self._get_parameter(request.parameters, "workflow_id")
        generated_id = self.uuid_builder.create_uuid7()

        conversation = Conversation(
            conv_id=conv_id,
            user_id=request.user_id,
            shared_state=AgentSharedState(
                context=GlobalContext(
                    conversation_context=request.message
                ),
                executions=[
                    Task(
                        task_id=generated_id,
                        input_data=request.parameters,
                        agent=agent_name,
                        name=f"Dispatch message to {agent_name or agent_id or agent_type}",
                        parent_task_id=None,
                        next_task_id=None,
                        output_data={},
                        error_detail=None,
                        workflow_id=workflow_id,
                        agent_id=agent_id,
                        # SA-892 (conversation_context source): this dispatch creates a
                        # fresh single-task conversation; the task is not registered in
                        # conversation_context, so a Stop scopes via the fallback
                        # (cancel all non-terminal tasks) — correct here as this task
                        # IS the turn.
                    )
                ],
                metadata=Metadata(
                    conv_id=conv_id,
                    main_conv_id=conv_id,
                    checkpoint_id="CP-00",
                    parent_checkpoint=None,
                    last_event_id=None,
                    correlation_id=correlation_id,
                    status=MetadataStatus.IN_PROGRESS,
                    current_task_id=generated_id,
                    # SA-892 (task-scoped stop): the current message/turn id. Matches
                    # the dispatch task's ``message_id`` so a Stop resolves and
                    # cancels exactly this turn's task.
                    current_message_id=generated_id,
                    current_run_id="",
                    current_agent=agent_name or agent_id or agent_type or "workflow",
                ),
            )
        )

        self.orchestration_state_repo.save_conversation(conversation)

    def _save_fallback_title(self, conv_id: str, message: str) -> None:
        if self.conversation_summary_repo is None:
            return
        title = message.strip()
        if not conv_id or not title:
            return
        self.conversation_summary_repo.save_title(
            ConversationTitleSummary(
                conv_id=conv_id,
                title=title,
                source=ConversationTitleSource.USER_MESSAGE,
            )
        )


    @staticmethod
    def _get_parameter(parameters: Dict[str, Any], key: str) -> str:
        value = parameters.get(key, "")
        if value is None:
            return ""
        return str(value)
