"""
Orchestrate Use Case — routes user requests through the LangGraph state machine.
"""

from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from typing import Any, Dict

from agent_sdk.layer1_domain.entities.conversation_context import FileObject, FileStatus, MessageHistory
from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from agent_sdk.layer2_application.interfaces.agent_shared_state_repository import IAgentSharedStateRepository
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder
from app.layer1_domain.interfaces.logger_interface import ILogger
from agent_sdk.layer2_application.interfaces.message_publisher import IMessagePublisher
from app.layer2_application.features.orchestrate_graph.use_cases.orchestrate_graph_use_case import AgentSharedState, Conversation, OrchestrationGraphCommand
from app.layer2_application.features.orchestrate_graph.use_cases.dtos.orchestration_parameters import OrchestrationParameters
from agent_sdk.layer1_domain.entities.agent_shared_state import (
    GlobalContext,
    Metadata,
)
from app.layer1_domain.entities.orchestrator.task_type import TaskType
from app.layer1_domain.entities.exceptions import RequestValidationAppError
from agent_sdk import Settings

 # 5 minutes = 300 seconds
ORCHESTRATION_TIMEOUT = 300

@dataclass
class OrchestrateInput:
    message: str
    conv_id: str = ""
    user_id: str = ""
    user_email: str = ""
    tenant_id: str = ""
    source: str = "rest"
    # SA-2114/SA-2168: routing discriminator (absent/empty default, "AGENT", or
    # "RENAME"); the target agent id rides inside parameters (parameters.agent_id).
    # RENAME needs no parameters — the new title rides in message.
    type: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class OrchestrateAsyncOutput:
    message: str = ""
    conv_id: str = ""
    uploaded_file_ids: list[str] = field(default_factory=list)



class OrchestrateUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        orchestration_state_repo: IAgentSharedStateRepository,
        publisher: IMessagePublisher,
        uuid_builder: IUuidBuilder,
        settings: Settings,
    ):
        self.logger = logger
        self.monitor = monitor
        self.publisher = publisher
        self.orchestration_state_repo = orchestration_state_repo
        self.uuid_builder = uuid_builder
        self.settings = settings


    async def execute_fire_and_forget(
        self, request: OrchestrateInput,
    ) -> OrchestrateAsyncOutput:
        chat_id: str = request.conv_id
        user_id: str = request.user_id
        user_message: str = request.message
        user_email: str = request.user_email

        # SA-2114: validate the routing type before accepting/publishing the request.
        self._validate_routing(request.type, request.parameters)

        self.logger.info(
            "Async orchestration accepted",
            conv_id=chat_id,
            user_id=user_id,
            user_email=user_email,
        )
        
        conversation: Conversation | None = self.orchestration_state_repo.get_conversation_by(conv_id=chat_id)
        generated_id: str = self.uuid_builder.create_uuid7()
        message_history = MessageHistory(
                message_id=generated_id,
                message=user_message,
                files=self._get_uploaded_file_ids(request.parameters)
                )
        if not conversation:
            
            chat_id: str = generated_id
            self.logger.info(
                "Conversation not found, creating new conversation",
                conv_id=generated_id,
            )
            metadata = Metadata(
                    conv_id=chat_id,
                    main_conv_id=None,
                    checkpoint_id=generated_id,
                    parent_checkpoint=None,
                    last_event_id=None,
                    correlation_id=generated_id,
                    current_task_id=None,
                    current_run_id=None,
                    current_node_id=None,
                    current_agent=None,
                    current_message_id=generated_id,
                )
            
            global_context = GlobalContext(
                conversation_context=[message_history]
            )
            conversation = Conversation(
                conv_id=generated_id,
                user_id=user_email,
                shared_state=AgentSharedState(
                    metadata=metadata,
                    context=global_context
                )
            )
            self.orchestration_state_repo.save_conversation(conversation)
        else:
            shared_state: AgentSharedState = conversation.shared_state
            if shared_state.metadata:
                shared_state.metadata.current_message_id = generated_id
                shared_state.context.conversation_context.append(message_history)
                # SA-892 (task-scoped stop): a new user message just RUNS. There is
                # no conversation-level stop flag to clear — Stop only cancels the
                # prior message's tasks, and this fresh turn creates its own tasks
                # (a new message_id) that were never cancelled. Plain existing-
                # conversation persist: update metadata + context.
                self.orchestration_state_repo.update_metadata(
                    conv_id=chat_id,
                    metadata=shared_state.metadata
                )
                self.orchestration_state_repo.update_context(
                    conv_id=chat_id,
                    context=shared_state.context
                )
            
        # SA-2114: carry the routing type inside parameters (agent_id already rides
        # there from the request body) so the graph reads both from parameters.
        command_parameters = {**(request.parameters or {}), "type": request.type}
        orchestrator_message = OrchestrationGraphCommand(
            correlation_id=generated_id,
            event_type=self.settings.ORCHESTRATOR_EVENT_TYPE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            message=user_message,
            conv_id=chat_id,
            user_id=user_id,
            source=request.source,
            parameters=OrchestrationParameters.from_dict(command_parameters),
            message_id=message_history.message_id,
            # SA-2168: carried so _handle_rename_request can check ownership against
            # Conversation.user_id, which holds the owner's email, not the uuid in user_id.
            user_email=user_email,
        )

        # # After dispatch, _on_handle_orchestration_request() will be triggered by the message consumer and run the graph in the background
        await self.publisher.publish(self.settings.ORCHESTRATOR_REQUEST_TOPIC, message=asdict(orchestrator_message))

        return OrchestrateAsyncOutput(
            message=request.message,
            conv_id=chat_id,
        )


    @staticmethod
    def _validate_routing(type_value: object, parameters: object) -> None:
        """SA-2114/SA-2168: reject an invalid async routing payload before publishing.

        - ``type`` must be absent/empty (default routing), ``AGENT``, or ``RENAME``.
        - when ``type`` is ``AGENT``, ``parameters.agent_id`` must be a non-empty string.
        - ``RENAME`` needs no ``parameters`` — the new title rides in ``message``.
        """
        try:
            task_type = TaskType.from_value(type_value)
        except ValueError as error:
            raise RequestValidationAppError() from error

        if task_type is TaskType.AGENT:
            agent_id = parameters.get("agent_id") if isinstance(parameters, dict) else None
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise RequestValidationAppError()

    @staticmethod
    def _get_uploaded_file_ids(parameters: Dict[str, Any]) -> list[FileObject]:
        if not isinstance(parameters, dict):
            return []

        uploaded_file_ids = parameters.get("uploaded_file_ids", [])
        if not isinstance(uploaded_file_ids, list):
            return []

        return [
            FileObject(file_id=str(file_id), file_path=None, status=FileStatus.UPLOADED)
            for file_id in uploaded_file_ids
            if file_id not in (None, "")
        ]

