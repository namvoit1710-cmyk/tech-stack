from enum import Enum
from pydantic import BaseModel
from agent_sdk.layer1_domain.entities.conversation_context import MessageHistory, TaskHistory
from app.layer1_domain.entities.queue_event import (
    QueueEvent,
    QueuePayload,
    QueuePayloadTo,
    QueuePayloadType,
    QueueRequestType,
    QueueStep,
)
from app.layer1_domain.entities.planning_context import (
    PlanningContext,
    PlanningContextType,
    SubUserChatPlanningContext,
    UserIntent,
    SimpleNodePlanningContext,
)

from app.layer2_application.features.main_agent.dto.event import EventDTO
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.planner_resolver_interface import (
    IPlannerResolver,
)
from app.layer2_application.interfaces.workflow_client_interface import IWorkflowClient
from app.layer2_application.interfaces.push_gateway_notifier_interface import (
    IPushGatewayNotifier,
)
from app.layer1_domain.entities.agent_hub import AgentHub, AgentHubDTO, ToolHubDTO, WorkflowHub, ToolHub, WorkflowHubDTO
from app.layer1_domain.entities.rag_context import (
    CreateKnowledgeBaseRequest,
    IngestionRequest,
    RagCandidate,
)
from app.layer1_domain.entities.workflow_entity import WorkflowRunInfoResponse
from app.layer2_application.interfaces.agent_hub_interface import IAgentHub
from app.layer2_application.interfaces.rag_client_interface import IRagClient
from app.layer2_application.interfaces.business_agent_shared_state_repository_interface import (
    IBusinessAgentSharedStateRepository,
)
from agent_sdk.layer2_application.interfaces.message_publisher import (
    IMessagePublisher,
)
from agent_sdk.layer1_domain.entities.agent_shared_state import (
    Metadata,
    Task,
    TaskStatus,
    Conversation,
    AgentSharedState,
    MetadataStatus,
    GlobalContext,
)
from app.layer2_application.interfaces.plan_generator_interface import IPlanGenerator
from app.layer1_domain.entities.planner import PatchInputMode, PlannerResponse, PlannerResponseType
from app.layer1_domain.entities.workflow_event import (
    HttpRequestInput,
    PendingKind,
    WorkflowEvent,
    WorkflowStatus,
)
from app.layer1_domain.entities.ui_event import (
    UIPayload,
    UIPayloadType,
    UIWFInfo,
    UIPayloadText,
)
from app.layer1_domain.entities.turn_meter import TurnMeter, reset_turn, start_turn
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder
from app.layer1_domain.entities.workflow_node import InputNodeResponse, SchemaField, build_artiface_payload
from typing import Dict, List, Tuple
from app.layer1_domain.entities.planning_context import (
    PatchInputNodePlanningContext,
    SummaryContext,
    ExecutionSummaryContext,

)
from app.layer2_application.interfaces.workflow_input_node_interface import IWorkflowInputNode
import json
from dataclasses import asdict
from agent_sdk import Settings
from app.layer2_application.interfaces.file_client_interface import IFileClient
from app.layer2_application.features.main_agent.helpers.ai_feed_helpers import AIFeedDeps, execute_ai_feed_pipeline
from app.layer1_domain.entities.planner import PatchInputNodeResult
class UnsupportedEventTypeError(ValueError):
    pass


# SA-892 (task-scoped stop): a Task in any of these states is TERMINAL — it must
# never be flipped back to PROCESSING or run again. CANCELLED is the stop signal;
# the success/error/fail states mean the task already finished.
_TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.CANCELLED,
        TaskStatus.SUCCESS,
        TaskStatus.ERROR,
        TaskStatus.FAIL,
    }
)


class TaskNotRunnableError(Exception):
    """SA-892 (task-scoped stop): the resolved current task is in a terminal
    state (e.g. CANCELLED by a racing Stop) and must not be flipped to PROCESSING
    or run. ``execute()`` treats this as a DROP: log and return, no plan/graph,
    no next task, no dispatch."""

    pass


class PlanCaseName(str, Enum):
    SUB_USER_CHAT = "sub_user_chat"
    USER_ACTION = "user_action"
    CONVERSATION_PLAN_CREATED = "conversation_plan_created"
    WORKFLOW_REQUEST = "workflow_request"
    WORKFLOW_SUBMIT = "workflow_submit"
    WORKFLOW_NODE_STATUS = "workflow_node_status"
    WORKFLOW_NODE_DISPATCH_STATUS = "workflow_node_dispatch_status"
    MAIN_USER_RERUN = "main_user_rerun"
    WORKFLOW_RUN_STATUS = "workflow_run_status"
    WORKFLOW_NODE_AI_FEED = "workflow_node_ai_feed"
    WORKFLOW_NODE_FLOW_VIEWER = "workflow_node_flow_viewer"


class PlanCase(BaseModel):
    name: PlanCaseName
    event_types: frozenset[str]
    conversation_scope: str


PLAN_CASES: tuple[PlanCase, ...] = (
    PlanCase(
        name=PlanCaseName.SUB_USER_CHAT,
        event_types=frozenset({QueueRequestType.SUB_USER_CHAT.value}),
        conversation_scope="child",
    ),
    PlanCase(
        name=PlanCaseName.USER_ACTION,
        event_types=frozenset({QueueRequestType.USER_ACTION.value}),
        conversation_scope="parent&child",
    ),
    PlanCase(
        name=PlanCaseName.CONVERSATION_PLAN_CREATED,
        event_types=frozenset({QueueRequestType.CONVERSATION_PLAN_CREATED.value}),
        conversation_scope="parent",
    ),
    PlanCase(
        name=PlanCaseName.WORKFLOW_REQUEST,
        event_types=frozenset(
            {
                QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED.value,
            }
        ),
        conversation_scope="parent",
    ),
    # PlanCase(
    #     name=PlanCaseName.WORKFLOW_SUBMIT,
    #     event_types=frozenset(
    #         {
    #             QueueRequestType.WORKFLOW_NODE_INPUT_SUBMITTED.value,
    #         }
    #     ),
    #     conversation_scope="parent",
    # ),
    PlanCase(
        name=PlanCaseName.WORKFLOW_SUBMIT,
        event_types=frozenset(
            {
                QueueRequestType.WORKFLOW_NODE_INPUT_SUBMITTED.value,
            }
        ),
        conversation_scope="parent",
    ),
    PlanCase(
        name=PlanCaseName.MAIN_USER_RERUN,
        event_types=frozenset({QueueRequestType.MAIN_USER_RERUN.value}),
        conversation_scope="parent",
    ),
    PlanCase(
        name=PlanCaseName.WORKFLOW_NODE_DISPATCH_STATUS,
        event_types=frozenset(
            {
                QueueRequestType.WORKFLOW_NODE_DISPATCHED.value,
            }
        ),
        conversation_scope="parent",
    ),
    PlanCase(
        name=PlanCaseName.WORKFLOW_NODE_AI_FEED,
        event_types=frozenset(
            {
                QueueRequestType.WORKFLOW_NODE_AI_FEED.value,
            }
        ),
        conversation_scope="parent",
    ),
    PlanCase(
        name=PlanCaseName.WORKFLOW_NODE_STATUS,
        event_types=frozenset(
            {
                QueueRequestType.WORKFLOW_NODE_COMPLETED.value,
            }
        ),
        conversation_scope="parent",
    ),
    PlanCase(
        name=PlanCaseName.WORKFLOW_RUN_STATUS,
        event_types=frozenset(
            {
                QueueRequestType.WORKFLOW_RUN_STATUS.value,
            }
        ),
        conversation_scope="parent",
    ),
    PlanCase(
        name=PlanCaseName.WORKFLOW_NODE_FLOW_VIEWER,
        event_types=frozenset(
            {
                QueueRequestType.WORKFLOW_NODE_APPROVAL_FLOW_RESULT.value,
            }
        ),
        conversation_scope="parent",
    ),
)


class CreatePlanUseCase:
    # Registry short names (without the ``agent__{service}__...__{version}`` qualifier) of the
    # workflow-node tools allowed to receive run_id/node_id injection in the child-chat flow.
    # Add new workflow-node tools here as they are introduced.
    WORKFLOW_TOOL_NAMES: set[str] = {"workflow_patch_data"}

    def __init__(
        self,
        planner_resolver: IPlannerResolver,
        agent_hub_client: IAgentHub,
        agent_shared_state_repository: IBusinessAgentSharedStateRepository,
        workflow_client: IWorkflowClient,
        push_gateway_notifier: IPushGatewayNotifier,
        logger: ILogger,
        publisher: IMessagePublisher,
        uuid_builder: IUuidBuilder,
        settings: Settings,
        file_client: IFileClient,
        rag_client: IRagClient | None = None,
        workflow_input_node: IWorkflowInputNode | None = None,

    ):
        self.planner_resolver = planner_resolver
        self.agent_hub_client = agent_hub_client
        self.agent_shared_state_repository = agent_shared_state_repository
        self.workflow_client = workflow_client
        self.push_gateway_notifier = push_gateway_notifier
        self.logger = logger
        self.publisher = publisher
        self.uuid_builder = uuid_builder
        self.settings = settings
        self.file_client = file_client
        self.rag_client = rag_client
        self.workflow_input_node = workflow_input_node

    async def execute(self, event: EventDTO) -> None:
        # SA-2079: one TurnMeter per turn — allocated here (message_id = uuid7), read+updated by
        # the PushGatewayUsageRecorder seam on every LLM call, and reused by the final
        # send_task_summary_event as its own event id.
        #
        # Workflow-control-plane events carry NO conv_id on the DTO (EventDTO.workflow_event_to_dto
        # hardcodes conv_id=""); the conversation is only discoverable via the run id. The cooking
        # emitters key on `meter.conv_id or record.conversation_id` — and the workflow planners
        # pass no conversation_id either — so a "" meter conv_id sends every "Cooking…"/token_info
        # frame to an empty conversation the FE never renders (while the narration, sent with the
        # run-id-resolved conv_id via send_thinking_event, still shows). Seed the meter with the
        # resolved conv_id so cooking lands on the right conversation. _resolve_conv_id_for_guard is
        # best-effort (returns event.conv_id when present — no change for normal chat — else resolves
        # from payload.workflow.root_run_id, never raising), so an unresolved id just falls back to "".
        turn_meter_token = start_turn(
            TurnMeter.start_now(
                message_id=self.uuid_builder.create_uuid7(),
                conv_id=self._resolve_conv_id_for_guard(event),
            )
        )
        try:
            # SA-892 (task-scoped stop) PRIMARY guard: the per-task mirror of the
            # workflow engine's CANCELLED short-circuit, agent-side. A Stop cancels
            # the CURRENT MESSAGE's tasks (status=CANCELLED). A turn arriving here is
            # a LATE continuation / step-result callback (e.g. a CONVERSATION_PLAN_
            # CREATED continuation self-published after Stop, or a step-result
            # re-published on business.topic) — auto-continuing it would resurrect a
            # stopped message. Resolve the task THIS event pertains to and, if it is
            # CANCELLED, DROP the turn: no plan/graph, no _handle_next_task, no
            # executor dispatch, no follow-up publish. A NEW user message's task is
            # never CANCELLED (a different message_id), so it processes normally; and
            # if the task can't be resolved we fall through rather than wrongly drop
            # a legit turn.
            if self._incoming_task_is_cancelled(event):
                self.logger.info(
                    "SA-892: dropping continuation whose task is CANCELLED",
                    conv_id=self._resolve_conv_id_for_guard(event),
                    event_type=str(getattr(event, "type", "")),
                )
                return
            # event_entity = event.to_entity()  # validate and convert DTO to Entity
            if self.logger:
                self.logger.info(
                    "Executing CreatePlanUseCase with event entity",
                    event_entity=event,
                )
            plan_case = self._resolve_plan_case(event.type)
            if self.logger:
                self.logger.info(
                    "Resolved plan case",
                    plan_case=plan_case.name.value,
                )

            match plan_case.name:
                case PlanCaseName.SUB_USER_CHAT:
                    return await self._execute_sub_user_chat(event)

                case PlanCaseName.CONVERSATION_PLAN_CREATED:
                    return await self._execute_conversation_plan_created(event)

                case PlanCaseName.WORKFLOW_REQUEST:
                    return await self._execute_workflow_request(event)

                # case PlanCaseName.WORKFLOW_SUBMIT:
                #     return await self._execute_workflow_submit(event)

                case PlanCaseName.MAIN_USER_RERUN:
                    return await self._execute_main_user_rerun(event)

                case PlanCaseName.WORKFLOW_NODE_STATUS:
                    return await self._execute_workflow_node_status(event)

                case PlanCaseName.WORKFLOW_RUN_STATUS:
                    return await self._execute_workflow_run_status(event)

                case PlanCaseName.WORKFLOW_NODE_DISPATCH_STATUS:
                    return await self._execute_workflow_node_dispatch_status(event)  

                case PlanCaseName.USER_ACTION:
                    return await self._execute_user_action(event)
                case PlanCaseName.WORKFLOW_NODE_AI_FEED:
                    return await self._execute_workflow_node_ai_feed(event)

                case PlanCaseName.WORKFLOW_NODE_FLOW_VIEWER:
                    return await self._execute_workflow_node_flow_viewer(event)
                case _:
                    self.logger.info(
                        "Unsupported plan case",
                        plan_case=plan_case.name.value,
                    )
        except TaskNotRunnableError as e:
            # SA-892 (task-scoped stop): the current task went terminal (e.g. a
            # racing Stop cancelled it) between the entry guard and the task read.
            # DROP the turn — no plan, no next task, no dispatch, no error summary.
            if self.logger:
                self.logger.info(
                    "SA-892: dropping turn — current task not runnable",
                    conv_id=self._resolve_conv_id_for_guard(event),
                    event_type=str(getattr(event, "type", "")),
                    detail=str(e),
                )
            return
        except Exception as e:
            if self.logger:
                self.logger.error("Error executing CreatePlanUseCase", error=str(e))
            conv_id = event.conv_id
            wf_payload = event.payload
            workflow = wf_payload.workflow if wf_payload else None
            if workflow:
                conv_id = self._get_conversation_id_by_run_id(workflow.root_run_id)
            await self.push_gateway_notifier.send_task_summary_event(
                conv_id=conv_id,
                summary="There was an error processing your request. Please try again later. Error details: " + str(e),
            )
        finally:
            reset_turn(turn_meter_token)

    def _resolve_conv_id_for_guard(self, event: EventDTO) -> str:
        """Best-effort conv_id for the SA-892 stop guard.

        Most continuations carry ``event.conv_id`` directly (e.g. the self-published
        CONVERSATION_PLAN_CREATED continuation). Workflow-originated callbacks instead
        carry the conversation via the run id, so fall back to resolving it from the
        payload's ``root_run_id``. Never raises — an unresolved conv_id simply means
        the guard does not fire.
        """
        conv_id = (getattr(event, "conv_id", "") or "").strip()
        if conv_id:
            return conv_id
        payload = getattr(event, "payload", None)
        workflow = getattr(payload, "workflow", None) if payload else None
        root_run_id = getattr(workflow, "root_run_id", "") if workflow else ""
        if not root_run_id:
            return ""
        try:
            resolved = self.agent_shared_state_repository.get_conversation_id_by_run_id(
                root_run_id
            )
        except Exception:
            return ""
        return (resolved or "").strip()

    def _incoming_task_is_cancelled(self, event: EventDTO) -> bool:
        """True iff the task THIS continuation pertains to is CANCELLED (SA-892).

        A Stop cancels the current message's tasks (Task.status=CANCELLED); this
        guard drops a late continuation for such a task. Resolution of the event's
        task id:

        * PREFERRED — a step id carried on the event payload
          (``event.payload.steps[0].id``), which is the executor step id and equals
          the ``Task.id`` in the conversation's executions (a step-result callback).
        * FALLBACK — ``Metadata.current_task_id``. The primary continuation
          (``CONVERSATION_PLAN_CREATED``, self-published by ``_handle_next_task``)
          carries NO task id in its payload; the business-agent maps it to a task
          via ``current_task_id`` (see ``_get_current_task``), so that is the
          authoritative link for it.

        The resolved id is looked up in the conversation's executions
        (``get_task``); if that task exists AND is CANCELLED -> DROP. Best-effort:
        a missing conv_id / task id, an unresolved task, or a failing read is
        treated as NOT cancelled (logged at debug) so the guard never blocks a
        legitimate turn on infrastructure noise or a task it cannot resolve.
        """
        conv_id = self._resolve_conv_id_for_guard(event)
        if not conv_id:
            return False

        task_id, used_current_task_id_fallback = self._resolve_event_task_id(
            event, conv_id
        )
        if not task_id:
            if self.logger:
                self.logger.debug(
                    "SA-892 stop guard: no task id resolved for event; processing",
                    conv_id=conv_id,
                    event_type=str(getattr(event, "type", "")),
                )
            return False

        try:
            task = self.agent_shared_state_repository.get_task(conv_id, task_id)
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    "SA-892 stop guard task lookup failed",
                    conv_id=conv_id,
                    task_id=task_id,
                    error=str(exc),
                )
            return False

        if task is None:
            if self.logger:
                self.logger.debug(
                    "SA-892 stop guard: task not found; processing",
                    conv_id=conv_id,
                    task_id=task_id,
                )
            return False

        # SA-892 (M): stale self-published continuation. When we resolved the task
        # via the mutable Metadata.current_task_id (no payload step id — the
        # CONVERSATION_PLAN_CREATED shape), a NEWER user message may have advanced
        # current_task_id to ITS OWN task. Such a task is not cancelled, so the
        # cancelled-check below would let the stale continuation double-process the
        # new message's task. If the continuation carries an origin message id and
        # it disagrees with the resolved task's message_id, the continuation is
        # STALE → DROP it. If either id is missing (older event/task shape) we
        # cannot prove staleness, so we fall through to the cancelled-check.
        #
        # SA-892 (conversation_context source): the task→message link is NOT stored
        # on the Task; it lives in conversation_context (message.tasks). We reverse
        # -look-up the resolved task's origin message from there.
        if used_current_task_id_fallback:
            origin_message_id = str(getattr(event, "origin_message_id", "") or "")
            task_message_id = str(self._message_id_of_task(conv_id, task_id) or "")
            if (
                origin_message_id
                and task_message_id
                and origin_message_id != task_message_id
            ):
                if self.logger:
                    self.logger.info(
                        "SA-892: dropping STALE continuation "
                        "(origin message_id != resolved task message_id)",
                        conv_id=conv_id,
                        task_id=task_id,
                        origin_message_id=origin_message_id,
                        task_message_id=task_message_id,
                    )
                return True

        return task.status == TaskStatus.CANCELLED

    def _resolve_event_task_id(
        self, event: EventDTO, conv_id: str
    ) -> tuple[str, bool]:
        """Resolve the executions Task.id this event pertains to (SA-892).

        Returns ``(task_id, used_current_task_id_fallback)``:

        * Prefer a step id on the event payload (maps 1:1 to Task.id) →
          ``(step_id, False)``.
        * Otherwise fall back to the conversation's ``Metadata.current_task_id`` —
          the link used by the self-published ``CONVERSATION_PLAN_CREATED``
          continuation, which carries no payload step id → ``(current_task_id, True)``.
          The ``True`` flag tells the caller the resolution went through the
          mutable ``current_task_id``, which a newer message may have advanced, so
          it must additionally check the continuation's origin message id for
          staleness.

        Never raises; an unresolved id is returned as ``("", ...)``.
        """
        payload = getattr(event, "payload", None)
        steps = getattr(payload, "steps", None) if payload else None
        if steps:
            step_id = getattr(steps[0], "id", "") or ""
            if step_id:
                return str(step_id), False
        try:
            metadata = self.agent_shared_state_repository.get_metadata(conv_id)
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    "SA-892 stop guard metadata lookup failed",
                    conv_id=conv_id,
                    error=str(exc),
                )
            return "", True
        return str(getattr(metadata, "current_task_id", "") or ""), True

    def _message_id_of_task(self, conv_id: str, task_id: str) -> str:
        """Reverse-lookup a task's origin message id from conversation_context.

        SA-892 (conversation_context source): the task→message link is the
        ``TaskHistory`` registered under a ``MessageHistory`` in
        ``conversation_context`` (written by ``upsert_task_histories`` at plan
        time), NOT a field on the Task. Scan the conversation's context for the
        message whose ``tasks`` contains ``task_id``. Best-effort: any missing
        id / unresolved task / read error → "" (never blocks a turn on noise).
        """
        if not conv_id or not task_id:
            return ""
        try:
            context = self.agent_shared_state_repository.get_context_by_conv_id(
                conv_id
            )
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    "SA-892 message_id_of_task lookup failed",
                    conv_id=conv_id,
                    task_id=task_id,
                    error=str(exc),
                )
            return ""
        if context is None:
            return ""
        for message in getattr(context, "conversation_context", None) or []:
            for task_history in getattr(message, "tasks", None) or []:
                if getattr(task_history, "task_id", None) == task_id:
                    return str(getattr(message, "message_id", "") or "")
        return ""

    async def _handle_error_propagation(self, metadata: Metadata) -> None:
        if not metadata or not metadata.current_task_id:
            return
        conv_id = metadata.conv_id
        task = self.agent_shared_state_repository.get_task(
            conv_id=conv_id,
            task_id=metadata.current_task_id,
        )

        if not task:
            return

        await self._handle_current_child_task(
            conv_id=conv_id,
            current_child_task=task,
            task_status=TaskStatus.ERROR,
        )

        await self._handle_parent_chain(
            metadata=metadata,
            current_child_task=task,
            task_status=TaskStatus.ERROR,
        )

    def _resolve_plan_case(self, event_type: str) -> PlanCase:
        for plan_case in PLAN_CASES:
            if event_type in plan_case.event_types:
                return plan_case

        raise UnsupportedEventTypeError(f"Unsupported event type: {event_type}")

    async def _execute_sub_user_chat(self, event: EventDTO) -> None:
        conv_id: str = event.conv_id
        await self.push_gateway_notifier.send_chat_disable_event(conv_id)
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message="I'm reviewing information from multiple sources to provide the best possible assistance...",
        )

        metadata = self._get_metadata(conv_id)
        current_task = await self._get_current_task(conv_id, metadata)

        conversation_context = self._get_conversation_context_from_task(current_task)
        if metadata.current_agent is None:
            raise ValueError("current_agent is required in metadata for sub user chat")
        
        # current_agent is stored prefixed (e.g. "agent.<id>"); the registry client expects
        # the bare id, same as the main flow (_get_agent_id_from_task).
        agent_ref = metadata.current_agent.split(".")[-1]
        current_agent: AgentHub | None = await self.agent_hub_client.get(agent_ref)
        if current_agent is None:
            raise ValueError(
                f"Planned agent '{metadata.current_agent}' was not found in the registry"
            )

        tools: list[ToolHub] = await self.agent_hub_client.get_tools_by_ids(current_agent.tools)
        agents: list[AgentHub] = await self.agent_hub_client.get_agents_by_ids(
            current_agent.agents
        )
        if metadata.current_run_id is None:
            raise ValueError("current_run_id is required in metadata for sub user chat")
        if metadata.current_node_id is None:
            raise ValueError("current_node_id is required in metadata for sub user chat")

        node_info = await self.workflow_client.get_node_info(run_id=metadata.current_run_id, node_id=metadata.current_node_id)

        planner: IPlanGenerator = self.planner_resolver.resolve(PlanningContextType.LLM_WITH_SUB_USER_CHAT)
        planning_context = SubUserChatPlanningContext(
            conversation_context=UserIntent(message=conversation_context),
            business_intent=f"{current_agent.description} {current_agent.business}",
            node_info=node_info,
            tools=[ToolHubDTO.to_dto(t) for t in (tools or [])],
            agents=[AgentHubDTO.to_dto(a) for a in (agents or [])],
        )
        self.logger.info(
            "Generated planning context for sub-user chat",
            conv_id=conv_id,
            planning_context=asdict(planning_context),
        )
        plan: PlannerResponse = await planner.generate(planning_context)
        if plan.type == PlannerResponseType.DIRECT_ANSWER:
            await self._handle_direct_answer(conv_id, metadata, current_task, plan)
        else:  # execution_plan
            form_field_keys = {
                item.key
                for item in (node_info.input_schema if node_info else [])
                if item.key
            }
            await self._handle_sub_chat_execution_plan(
                conv_id,
                current_task,
                plan,
                available_tools=tools,
                available_agents=agents,
                form_field_keys=form_field_keys,
            )

        

    async def _execute_conversation_plan_created(self, event: EventDTO) -> None:
        conv_id: str = event.conv_id
        await self.push_gateway_notifier.send_chat_disable_event(conv_id)
        metadata = self._get_metadata(conv_id)

        current_task = await self._get_current_task(conv_id, metadata)
    
        await self._create_plan(conv_id, metadata, current_task)

    async def _create_plan(
        self, conv_id: str, metadata: Metadata, current_task: Task
    ) -> PlannerResponse:
        agent_id: str = self._get_agent_id_from_task(current_task)
        task_goal: str = self._get_conversation_context_from_task(current_task)
        metatata: Metadata = self._get_metadata(conv_id=conv_id)
        agent: AgentHub = await self.agent_hub_client.get(agent_id)
        if agent is None:
            raise ValueError(f"Planned agent '{agent_id}' was not found in the registry")
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message="I'm collecting and reviewing information from multiple sources to build a complete picture and provide the best possible assistance...",
        )
        tools: list[ToolHub] = await self.agent_hub_client.get_tools_by_ids(agent.tools)
        workflows: list[WorkflowHub] = await self.agent_hub_client.get_workflows_by_ids(
            agent.workflows
        )
        agents: list[AgentHub] = await self.agent_hub_client.get_agents_by_ids(
            agent.agents
        )
        global_context: GlobalContext | None = self.agent_shared_state_repository.get_context_by_conv_id(
            conv_id
        )
        file_summary_task = await self._build_file_summary_task_from_context(
            conv_id=conv_id,
            current_task=current_task,
            assigned_agents=agents,
            global_context=global_context,
        )
        rag_candidates = await self._get_rag_candidates(
            conversation_context=task_goal,
            knowledge_base_ids=agent.knowledge_base,
        )

        planner: IPlanGenerator = self.planner_resolver.resolve(PlanningContextType.LLM)
        if global_context is None:
            raise ValueError(f"No global context found for conversation {conv_id}")
        planning_context = PlanningContext(
            global_context=global_context,
            current_task=current_task,
            metadata=metatata,
            business_intent=(
                f"Agent Description:\n{agent.description}\n\n"
                f"Business Context:\n{agent.business or 'N/A'}"
            ),
            tools=[ToolHubDTO.to_dto(t) for t in (tools or [])],
            workflows_hub=[WorkflowHubDTO.to_dto(w) for w in (workflows or [])],
            agents=[AgentHubDTO.to_dto(a) for a in (agents or [])],
            rag_candidates=rag_candidates,
        )
        self.logger.info(
            "Generated planning context for conversation plan",
            conv_id=conv_id,
            planning_context=asdict(planning_context),
        )
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message="I'm taking a moment to organize the information and create a clear plan...",
        )

        plan: PlannerResponse = await planner.generate(planning_context)
        if plan.type == PlannerResponseType.DIRECT_ANSWER:
            await self._handle_direct_answer(conv_id, metadata, current_task, plan)
        else:  # execution_plan
            await self._handle_execution_plan(
                conv_id,
                current_task,
                plan,
                file_summary_task=file_summary_task,
                available_tools=tools,
                available_agents=agents,
            )
        return plan

    async def _get_rag_candidates(
        self,
        *,
        conversation_context: str,
        knowledge_base_ids: list[str] | None,
    ) -> list[RagCandidate]:
        if self.rag_client is None:
            return []

        normalized_kb_ids = [
            kb_id.strip()
            for kb_id in (knowledge_base_ids or [])
            if isinstance(kb_id, str) and kb_id.strip()
        ]
        if not normalized_kb_ids:
            self.logger.info(
                "Agent has no registry knowledge_base ids. Skipping RAG retrieval."
            )
            return []

        try:
            candidates = await self.rag_client.query_candidates(
                query_text=conversation_context,
                knowledge_base_ids=normalized_kb_ids,
            )
            self.logger.info(
                "Loaded RAG candidates for planning context",
                candidate_count=len(candidates),
                knowledge_base_ids=normalized_kb_ids,
            )
            return candidates
        except Exception as exc:
            self.logger.warning(
                "Failed to load RAG candidates for planning context",
                error=str(exc),
            )
            return []

    def _get_conversation_context_from_task(self, current_task: Task) -> str:
        goal: str = current_task.goal
        if goal is None:
            raise ValueError("Task goal is required for planning context")
        return goal

    async def _handle_direct_answer(
        self,
        conv_id: str,
        metadata: Metadata,
        current_task: Task,
        plan: PlannerResponse,
    ) -> None:
        execution_plans: list[Task] = [
            Task(
                task_id=self.uuid_builder.create_uuid7(),
                agent=f"agent.{self._get_agent_id_from_task(current_task)}",
                name=f"Direct answer for {self._get_conversation_context_from_task(current_task)}",
                status=TaskStatus.SUCCESS,
                next_task_id="",
                depend_on=[],
                is_dynamic=False,
                input_data=None,
                output_data=None,
                error_detail=None,
                workflow_id=None,
                agent_id=self._get_agent_id_from_task(current_task),
                parent_task_id=current_task.id,
            )
        ]

        self.agent_shared_state_repository.upsert_executions(
            conv_id=conv_id, executions=execution_plans
        )
        if plan.answer is None:
            raise ValueError("plan.answer is required for task summary event")
        await self.push_gateway_notifier.send_task_summary_event(
            conv_id=conv_id, summary=plan.answer, suggestions=plan.suggestions
        )
        if self.logger:
            self.logger.info(
                "Generated next-step suggestions for direct answer",
                conv_id=conv_id,
                suggestion_count=len(plan.suggestions),
            )

        if not current_task.next_task_id:
            updated_task: Task | None = self.agent_shared_state_repository.update_task(
                conv_id, current_task.id, status=TaskStatus.SUCCESS
            )
            metadata.current_task_id = ""
            self.agent_shared_state_repository.update_metadata(
                conv_id=conv_id, metadata=metadata
            )

            if updated_task is None:
                raise ValueError("Task not found")

        else:
            await self._handle_next_task(conv_id, metadata, current_task)

    def _get_agent_id_from_task(self, current_task: Task) -> str:
        if current_task.execute_by is None:
            raise ValueError("execute_by is None")
        agent_id: str = current_task.execute_by.split(".")[-1]
        return agent_id

    def _get_tool_id_from_task(self, current_task: Task) -> str:
        if current_task.execute_by is None:
            raise ValueError("execute_by is None")
        tool_id: str = current_task.execute_by.split(".")[-1]
        return tool_id
    def _sync_current_run_id_from_task(self, conv_id: str, task: Task) -> None:
        input_data = task.input_data or {}
        predefined_run_id = input_data.get("predefined_run_id")
        if not isinstance(predefined_run_id, str) or not predefined_run_id.strip():
            return
        metadata = self._get_metadata(conv_id=conv_id)
        metadata.current_run_id = predefined_run_id
        self.agent_shared_state_repository.update_metadata(conv_id, metadata)

    async def _build_file_summary_task_from_context(
        self,
        *,
        conv_id: str,
        current_task: Task,
        assigned_agents: list[AgentHub],
        global_context: GlobalContext | None,
    ) -> Task | None:
        file_id = self._find_first_file_id(global_context)
        if not file_id:
            return None

        content_type = (
            await self.file_client.get_content_type(file_id)
        ) or "application/octet-stream"

        rag_info = await self._push_context_file_to_rag(
            conv_id=conv_id,
            file_id=file_id,
            content_type=content_type,
        )

        file_agent = self._find_file_agent(content_type, assigned_agents)
        if file_agent is None:
            self.logger.info(
                "File summary step skipped: no assigned agent for file type",
                file_id=file_id,
                content_type=content_type,
            )
            return None

        return Task(
            task_id=self.uuid_builder.create_uuid7(),
            agent=f"agent.{file_agent.id}",
            name="Read raw values from the uploaded file.",
            status=TaskStatus.PENDING,
            next_task_id="",
            depend_on=[],
            is_dynamic=False,
            input_data={
                "_business_agent_file_summary": True,
                "file_id": file_id,
                "content_type": content_type,
                "rag": rag_info,
                "message": (
                    "Read the uploaded file and return raw values only. "
                ),
                "top": 50,
            },
            output_data={},
            error_detail=None,
            workflow_id=None,
            agent_id=self._get_agent_id_from_task(current_task),
            parent_task_id=current_task.id,
        )

    async def _push_context_file_to_rag(
        self,
        *,
        conv_id: str,
        file_id: str,
        content_type: str,
    ) -> dict[str, str]:
        if self.rag_client is None or not file_id:
            return {}

        tenant_id = getattr(self.settings, "RAG_TENANT_ID", "local-dev-tenant")
        try:
            kb = await self.rag_client.get_knowledge_base_by_name(name=conv_id)
            if kb is None:
                kb = await self.rag_client.create_knowledge_base(
                    CreateKnowledgeBaseRequest(
                        tenant_id=tenant_id,
                        name=conv_id,
                        description=f"Knowledge base for conversation {conv_id}",
                    )
                )
            if kb is None:
                return {}

            global_context = self.agent_shared_state_repository.get_context_by_conv_id(
                conv_id
            )
            if global_context is not None:
                global_context.knowledge_base_id = kb.knowledge_base_id
                self.agent_shared_state_repository.update_context(
                    conv_id=conv_id,
                    context=global_context,
                )

            ingestion = await self.rag_client.ingestions(
                IngestionRequest(
                    tenant_id=tenant_id,
                    knowledge_base_ids=[kb.knowledge_base_id],
                    file_id=file_id,
                    filename=f"conv_{conv_id}_file_{file_id}",
                    media_type=content_type or "application/octet-stream",
                    metadata={"source": "uploaded_file"},
                )
            )

            result = {"knowledge_base_id": kb.knowledge_base_id}
            if ingestion is not None:
                result.update(
                    {
                        "document_id": ingestion.document.document_id,
                        "ingestion_status": ingestion.document.ingestion_status,
                    }
                )
            return result
        except Exception as exc:
            self.logger.warning(
                "Failed to ingest uploaded file into RAG",
                file_id=file_id,
                error=str(exc),
            )
            return {}

    def _find_first_file_id(self, global_context: GlobalContext | None) -> str | None:
        if global_context is None:
            return None

        for message_history in reversed(global_context.conversation_context or []):
            for file_object in message_history.files or []:
                file_id = (file_object.file_id or "").strip()
                if file_id:
                    return file_id

        return None

    def _find_file_agent(
        self,
        content_type: str,
        assigned_agents: list[AgentHub],
    ) -> AgentHub | None:
        value = content_type.lower()
        if "csv" in value:
            tokens = ["csv"]
        elif "pdf" in value:
            tokens = ["pdf"]
        elif "spreadsheet" in value or "excel" in value or "sheet" in value:
            tokens = ["spreadsheet", "excel", "xlsx", "sheet"]
        elif "json" in value:
            tokens = ["json"]
        elif "text" in value or "plain" in value:
            tokens = ["text", "txt"]
        elif "image" in value:
            tokens = ["image", "vision"]
        else:
            return None

        for agent in assigned_agents or []:
            agent_name = (agent.name or "").lower()
            if any(token in agent_name for token in tokens):
                return agent

        return None

    def _normalize_plan_executor(
        self,
        *,
        raw_executor: str,
        tool_ids: set[str],
        agent_ids: set[str],
    ) -> str:
        value = (raw_executor or "").strip()
        if not value:
            raise ValueError("Plan step executor is empty")

        prefix, separator, executor_id = value.partition(".")
        has_prefix = bool(separator) and prefix in {"tool", "agent"}
        candidate_id = executor_id if has_prefix else value

        if has_prefix and prefix == "tool" and candidate_id in tool_ids:
            return value
        if has_prefix and prefix == "agent" and candidate_id in agent_ids:
            return value

        if candidate_id in tool_ids:
            corrected = f"tool.{candidate_id}"
            self.logger.warning(
                "Corrected planner executor target",
                raw_executor=value,
                corrected_executor=corrected,
            )
            return corrected

        if candidate_id in agent_ids:
            corrected = f"agent.{candidate_id}"
            self.logger.warning(
                "Corrected planner executor target",
                raw_executor=value,
                corrected_executor=corrected,
            )
            return corrected

        if not tool_ids and not agent_ids and has_prefix:
            return value

        raise ValueError(
            f"Planner selected unknown executor '{value}'. "
            "The step must use one of the available tool or agent ids."
        )

    async def _handle_execution_plan(
        self,
        conv_id: str,
        current_task: Task,
        plan: PlannerResponse,
        file_summary_task: Task | None = None,
        available_tools: list[ToolHub] | None = None,
        available_agents: list[AgentHub] | None = None,
    ) -> None:
        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id, task=current_task
        )

        tool_ids = {tool.id for tool in (available_tools or []) if tool.id}
        agent_ids = {agent.id for agent in (available_agents or []) if agent.id}

        # SA-892 (conversation_context source): tasks are NOT stamped with a
        # message id; the task→message link is registered in conversation_context
        # via ``upsert_task_histories`` below, keyed by the turn's current message.
        execution_plans: list[Task] = []
        for step in plan.steps:
            execute_by = self._normalize_plan_executor(
                raw_executor=step.tool,
                tool_ids=tool_ids,
                agent_ids=agent_ids,
            )
            execution_plans.append(
                Task(
                    task_id=self.uuid_builder.create_uuid7(),
                    agent=execute_by,
                    name=step.reason or f"Execute {execute_by}",
                    status=TaskStatus.PENDING,
                    next_task_id="",
                    depend_on=[],
                    is_dynamic=False,
                    input_data=step.input_data,
                    output_data=step.output_data,
                    error_detail=None,
                    workflow_id=None,
                    agent_id=self._get_agent_id_from_task(current_task),
                    parent_task_id=current_task.id,
                )
            )

        if file_summary_task is not None:
            execution_plans.insert(0, file_summary_task)

        await self.pre_check_step(conv_id, execution_plans)

        self.agent_shared_state_repository.upsert_executions(
            conv_id=conv_id, executions=execution_plans
        )
        first_task = execution_plans[0]
        metadata = self._get_metadata(conv_id)  # validate metadata existence
        task_histories: list[TaskHistory] = [
            TaskHistory(
                task_id=task.id,
                knowledge=[]
            )
            for task in execution_plans
        ]
        self.agent_shared_state_repository.upsert_task_histories(
            conv_id=conv_id, message_id=metadata.current_message_id, tasks=task_histories
        )
        metadata.current_task_id = first_task.id
        self.agent_shared_state_repository.update_metadata(
            conv_id=conv_id, metadata=metadata
        )
        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id, task=first_task
        )

        await self.send_task_to_executor(conv_id, current_task.execute_by or "", first_task)

    async def send_task_to_executor(self, conv_id: str, source: str, task: Task):
        if task.execute_by is None:
            raise ValueError("execute_by is required")
        self._sync_current_run_id_from_task(conv_id, task)
        generated_id = self.uuid_builder.create_uuid7()
        event = QueueEvent(
            message_id=generated_id,
            correlation_id=generated_id,
            type=QueueRequestType.AGENT_REQUEST_EXECUTOR,
            conv_id=conv_id,
            payload=QueuePayload(
                from_=source,
                to=QueuePayloadTo.EXECUTOR_RUNTIME,
                type=QueuePayloadType.STEP_BATCH,
                batch_id=generated_id,
                steps=[
                    QueueStep(
                        id=task.id,
                        execute_by=task.execute_by,
                        next_step_id=task.next_task_id,
                        goal=task.goal,
                        message=task.goal,
                        uploaded_file_ids=[],
                        input_data=task.input_data or {},
                        output_data=task.output_data or {},
                    )
                ],
            ),
        )
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message="I've handed your request over to the appropriate agent for follow-up...",
            task_id=task.id,
        )
        await self.publisher.publish(
            topic=self.settings.EXECUTOR_TOPIC, message=event.to_dict()
        )

    async def _handle_sub_chat_execution_plan(
        self,
        conv_id: str,
        current_task: Task,
        plan: PlannerResponse,
        available_tools: list[ToolHub] | None = None,
        available_agents: list[AgentHub] | None = None,
        form_field_keys: set[str] | None = None,
    ) -> None:
        """Child-chat variant of _handle_execution_plan (SA-890).

        Same flow as _handle_execution_plan, but a step whose tool declares run_id/node_id
        (a workflow-node tool, e.g. workflow_patch_data) gets those ids backfilled from the
        sub-conversation metadata — the sub-chat planner cannot know the runtime ids — and
        its patch ``data`` is filtered to the node's real form-field keys (``form_field_keys``
        from the node input_schema) so a hedged synonym key (e.g. ``name`` next to
        ``template_name``) is dropped. Kept as a separate method so the main flow is untouched.
        """
        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id, task=current_task
        )

        tool_ids = {tool.id for tool in (available_tools or []) if tool.id}
        agent_ids = {agent.id for agent in (available_agents or []) if agent.id}
        # tool id -> its declared input-parameter names; a tool declaring run_id/node_id is a
        # workflow-node tool whose ids we backfill from the sub-conversation metadata.
        tool_input_keys_by_id = {
            tool.id: set(((tool.input_data or {}).get("properties") or {}).keys())
            for tool in (available_tools or [])
            if tool.id
        }
        # tool id -> its registry short name, used to gate injection to the allow-listed
        # workflow-node tools (WORKFLOW_TOOL_NAMES).
        tool_name_by_id = {
            tool.id: self._registry_short_name(tool.name)
            for tool in (available_tools or [])
            if tool.id
        }
        workflow_metadata = None  # loaded lazily, only if a step's tool needs the run ids

        execution_plans: list[Task] = []
        for step in plan.steps:
            execute_by = self._normalize_plan_executor(
                raw_executor=step.tool,
                tool_ids=tool_ids,
                agent_ids=agent_ids,
            )
            resolved_tool_id = (
                execute_by.split(".", 1)[1] if execute_by.startswith("tool.") else ""
            )
            tool_keys = tool_input_keys_by_id.get(resolved_tool_id, set())
            is_workflow_tool = (
                tool_name_by_id.get(resolved_tool_id, "") in self.WORKFLOW_TOOL_NAMES
            )
            if is_workflow_tool and ("run_id" in tool_keys or "node_id" in tool_keys):
                if workflow_metadata is None:
                    workflow_metadata = self._get_metadata(conv_id)
                input_data = self._inject_workflow_context(
                    step.input_data,
                    run_id=workflow_metadata.current_run_id if "run_id" in tool_keys else None,
                    node_id=workflow_metadata.current_node_id  if "node_id" in tool_keys else None,
                )
                input_data = self._filter_patch_data_keys(input_data, form_field_keys)
            else:
                input_data = step.input_data
            execution_plans.append(
                Task(
                    task_id=self.uuid_builder.create_uuid7(),
                    agent=execute_by,
                    name=step.reason or f"Execute {execute_by}",
                    status=TaskStatus.PENDING,
                    next_task_id="",
                    depend_on=[],
                    is_dynamic=False,
                    input_data=input_data,
                    output_data=step.output_data,
                    error_detail=None,
                    workflow_id=None,
                    agent_id=self._get_agent_id_from_task(current_task),
                    parent_task_id=current_task.id,
                )
            )

        await self.pre_check_step(conv_id, execution_plans)

        self.agent_shared_state_repository.upsert_executions(
            conv_id=conv_id, executions=execution_plans
        )
        first_task = execution_plans[0]
        metadata = self._get_metadata(conv_id)  # validate metadata existence
        task_histories: list[TaskHistory] = [
            TaskHistory(
                task_id=task.id,
                knowledge=[]
            )
            for task in execution_plans
        ]
        self.agent_shared_state_repository.upsert_task_histories(
            conv_id=conv_id, message_id=metadata.current_message_id, tasks=task_histories
        )
        metadata.current_task_id = first_task.id
        self.agent_shared_state_repository.update_metadata(
            conv_id=conv_id, metadata=metadata
        )
        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id, task=first_task
        )

        await self.send_task_to_executor(conv_id, current_task.execute_by or "", first_task)

    @staticmethod
    def _latest_uploaded_file_id(conversation) -> str | None:
        """Return the file_id of the most recently uploaded file in a conversation.

        Uploads are recorded on each message's ``files`` list (FileObject) by
        the orchestrator. We scan message history newest-first and return the
        first attached file_id so a workflow's ``{{$input.file_id}}`` binds to
        the CSV the user actually uploaded. Tolerates both FileObject instances
        and raw dicts (defensive against serialization drift).
        """
        shared_state = getattr(conversation, "shared_state", None)
        context = getattr(shared_state, "context", None)
        messages = getattr(context, "conversation_context", None) or []
        for message in reversed(messages):
            files = getattr(message, "files", None) or []
            for file_obj in files:
                file_id = (
                    file_obj.get("file_id")
                    if isinstance(file_obj, dict)
                    else getattr(file_obj, "file_id", None)
                )
                if file_id:
                    return file_id
        return None

    async def pre_check_step(self, conv_id, execution_plans):
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message="I'm validating the details and will proceed with the next steps shortly...",
            task_id=getattr(execution_plans[0], "id")
        )
        conversation = self.agent_shared_state_repository.get_conversation(conv_id)
        for i, current_step in enumerate(execution_plans):
            if i < len(execution_plans) - 1:
                current_step.next_task_id = execution_plans[i + 1].id

            if current_step.agent_id is None:
                continue

            if current_step.execute_by is not None and current_step.execute_by.startswith("agent."):
                continue

            tool_hub = await self.agent_hub_client.get_tools_by_id(
                self._get_tool_id_from_task(current_step)
            )
            metadata = self._get_metadata(conv_id=conv_id)
            if not tool_hub:
                await self._handle_error_propagation(metadata=metadata)
                raise ValueError(f"Tool not found for agent_id {current_step.agent_id}")
            if tool_hub.name and "workflow_start_run" not in tool_hub.name:
                continue

            predefined_run_id = self.uuid_builder.create_uuid7()
            current_step.input_data = current_step.input_data or {}
            current_step.input_data["predefined_run_id"] = predefined_run_id
            self._sync_current_run_id_from_task(conv_id, current_step)
            nested_input_data = current_step.input_data.get("input_data") or {}
            nested_input_data["user_email"] = conversation.user_id
            # SA-1612: thread the uploaded file into the workflow input so
            # {{$input.file_id}} resolves to the user's uploaded CSV rather
            # than a synthesized user_email-only file. The homepage stores
            # uploads on the message history (see orchestrate_use_case); pick
            # the newest attached file when the planner did not already bind one.
            if not nested_input_data.get("file_id"):
                uploaded_file_id = self._latest_uploaded_file_id(conversation)
                if uploaded_file_id:
                    nested_input_data["file_id"] = uploaded_file_id
                    nested_input_data.setdefault("file_format", "csv")
            current_step.input_data["input_data"] = nested_input_data

    async def _handle_next_task(
        self, conv_id: str, metadata: Metadata, current_task: Task
    ) -> None:
        metadata.current_task_id = current_task.next_task_id
        self.agent_shared_state_repository.update_metadata(
            conv_id=conv_id, metadata=metadata
        )
        generated_id = self.uuid_builder.create_uuid7()

        event = QueueEvent(
            message_id=generated_id,
            correlation_id=generated_id,
            type=QueueRequestType.CONVERSATION_PLAN_CREATED,
            conv_id=conv_id,
            # SA-892 (task-scoped stop): stamp the origin message/turn this
            # continuation belongs to. The guard drops it as STALE if, by the time
            # it is processed, Metadata.current_task_id has advanced to a task
            # created by a NEWER message (a different message_id) — preventing the
            # continuation from double-processing that newer message's task.
            origin_message_id=metadata.current_message_id,
        )

        await self.publisher.publish(
            topic=self.settings.BUSINESS_TOPIC,
            message=event.to_dict(),
            key=generated_id,
        )

    async def _get_current_task(self, conv_id: str, metadata: Metadata) -> Task:
        current_task_id: str | None = metadata.current_task_id
        if not current_task_id:
            raise ValueError(f"No current task found for conversation {conv_id}")

        first_task: Task | None = self.agent_shared_state_repository.get_task(
            conv_id, current_task_id
        )
        if first_task is None:
            raise ValueError(f"Task not found: {current_task_id}")

        # SA-892 (task-scoped stop): a CANCELLED (or otherwise terminal) task must
        # stay TERMINAL — never flipped back to PROCESSING. The turn-entry guard
        # already drops continuations for a cancelled task, but a Stop can race:
        # if it cancelled this task AFTER the entry guard read it, flipping it to
        # PROCESSING here would erase the stop signal and run a stopped turn. Read
        # the fresh status first; if it is terminal, do NOT overwrite it and DROP
        # the turn (execute() catches TaskNotRunnableError). The happy path
        # (PENDING/PROCESSING) is unchanged.
        if first_task.status in _TERMINAL_TASK_STATUSES:
            raise TaskNotRunnableError(
                f"current task {current_task_id} is {first_task.status.value}"
            )

        self.agent_shared_state_repository.update_task(
            conv_id, current_task_id, status=TaskStatus.PROCESSING
        )

        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message="Evaluating the best way to assist you...",
        )

        return first_task

    def _get_metadata(self, conv_id: str) -> Metadata:
        metadata: Metadata | None = self.agent_shared_state_repository.get_metadata(
            conv_id
        )

        if not metadata:
            raise ValueError(f"No metadata found for conversation {conv_id}")
        return metadata

    @staticmethod
    def _registry_short_name(tool_name: str) -> str:
        """Return a registry tool's short name from its qualified name.

        Registry names look like ``agent__{service}__{tool}__{version}`` (e.g.
        ``agent__executor-runtime__workflow_patch_data__0.1.5``); the tool segment is
        second-to-last. Fall back to the raw value if it is not qualified.
        """
        parts = (tool_name or "").split("__")
        return parts[-2] if len(parts) >= 2 else (tool_name or "")

    @staticmethod
    def _inject_workflow_context(
        input_data: dict | None,
        *,
        run_id: str | None,
        node_id: str | None,
    ) -> dict | None:
        """Backfill run_id/node_id on a workflow-node tool step from the run context.

        The planner LLM produces tool arguments but cannot know the runtime
        ``run_id``/``node_id`` of the current form node, so it emits them empty or omits
        them entirely — the workflow tool then rejects the call ("run_id Field required").
        Backfill each id from the sub-conversation metadata (``current_run_id`` /
        ``current_node_id``, set when the child conversation was created). The caller only
        invokes this for an allow-listed workflow-node tool (WORKFLOW_TOOL_NAMES) that
        declares the ids, so no per-field schema re-check is done here. (SA-890)
        """
        if not isinstance(input_data, dict):
            return input_data
        patched = dict(input_data)
        if not patched.get("run_id") and run_id:
            patched["run_id"] = run_id
        if not patched.get("node_id") and node_id:
            patched["node_id"] = node_id
        return patched

    @staticmethod
    def _filter_patch_data_keys(
        input_data: dict | None, form_field_keys: set[str] | None
    ) -> dict | None:
        """Drop patch ``data`` keys that are not real form-field keys.

        The sub-chat planner sometimes hedges by adding the user's wording as an extra key
        (e.g. ``name`` alongside the real ``template_name``). The workflow node ignores such
        unknown keys on write, but the reply summary echoes them ("Updated *name* … and
        *template name* …"). Keep only keys present in the node's input_schema
        (``form_field_keys``) so the patch — and its confirmation — reference real fields
        only. No-op if the schema keys are unknown (empty), to avoid dropping everything.
        (SA-890)
        """
        if not isinstance(input_data, dict) or not form_field_keys:
            return input_data
        data = input_data.get("data")
        if not isinstance(data, dict):
            return input_data
        filtered = {k: v for k, v in data.items() if k in form_field_keys}
        if filtered == data:
            return input_data
        patched = dict(input_data)
        patched["data"] = filtered
        return patched

    def _get_conversation_id_by_run_id(self, run_id: str) -> str:
        conv_id = self.agent_shared_state_repository.get_conversation_id_by_run_id(
            run_id
        )
        if not conv_id:
            raise ValueError(f"No conversation found for run_id {run_id}")
        return conv_id

    async def _build_workflow_run_status_context(
        self, workflow_event: WorkflowEvent
    ) -> SimpleNodePlanningContext:

        if workflow_event is not None:
            workflow_run_info: WorkflowRunInfoResponse | None = await self.workflow_client.get_workflow_run(workflow_event.root_run_id)
            if not workflow_run_info:
                raise ValueError(f"Workflow run not found: {workflow_event.root_run_id}")

            workflows_hub: list[WorkflowHub] = (
                await self.agent_hub_client.get_workflows_by_ids(
                    [workflow_run_info.data.workflow_id]
                )
            )
            if not workflows_hub:
                raise ValueError(f"Workflow not found: {workflow_run_info.data.workflow_id}")
            return SimpleNodePlanningContext(
                workflows_hub=[WorkflowHubDTO.to_dto(w) for w in workflows_hub],
                node_message=f"The workflow {workflow_event.workflow.name if workflow_event.workflow else 'unknown'} with description '{workflow_event.workflow.description if workflow_event.workflow else 'unknown'}' has been {workflow_event.status}.",
            )

    async def _build_node_run_status_context(
        self, workflow_event: WorkflowEvent
    ) -> SimpleNodePlanningContext:

        if workflow_event is not None:
            workflow_run_info: WorkflowRunInfoResponse | None = await self.workflow_client.get_workflow_run(workflow_event.root_run_id)
            if not workflow_run_info:
                raise ValueError(f"Workflow run not found: {workflow_event.root_run_id}")

            workflows_hub: list[WorkflowHub] = (
                await self.agent_hub_client.get_workflows_by_ids(
                    [workflow_run_info.data.workflow_id]
                )
            )
            if not workflows_hub:
                raise ValueError(f"Workflow not found: {workflow_run_info.data.workflow_id}")

            return SimpleNodePlanningContext(
                workflows_hub=[WorkflowHubDTO.to_dto(w) for w in workflows_hub],
                node_message=f"Workflow node '{workflow_event.node_id}' is currently in '{workflow_event.status}'",
            )

    async def _build_node_dispatch_status_context(
        self, workflow_event: WorkflowEvent
    ) -> SimpleNodePlanningContext:

        if workflow_event is not None:

            return SimpleNodePlanningContext(
                node_message=f"Workflow node '{workflow_event.node_id}' and node name is '{workflow_event.node_name}' is dispatched with output port '{workflow_event.output_port}'",
            )

    async def _build_form_input_requested_context(
        self, workflow_event: WorkflowEvent
    ) -> SimpleNodePlanningContext:

        if workflow_event is not None:
            workflow_run_info: WorkflowRunInfoResponse | None = await self.workflow_client.get_workflow_run(workflow_event.root_run_id)
            if not workflow_run_info:
                raise ValueError(f"Workflow run not found: {workflow_event.root_run_id}")

            workflows_hub: list[WorkflowHub] = (
                await self.agent_hub_client.get_workflows_by_ids(
                    [workflow_run_info.data.workflow_id]
                )
            )
            if not workflows_hub:
                raise ValueError(f"Workflow not found: {workflow_run_info.data.workflow_id}")

            return SimpleNodePlanningContext(
                workflows_hub=[WorkflowHubDTO.to_dto(w) for w in workflows_hub],
                node_message=(
                    f"Workflow node '{workflow_event.node_id}' is waiting for user input. "
                    f"Please provide the required '{workflow_event.pending_kind}' input "
                    f"based on the following instruction: '{workflow_event.instruction}'."
                ),
            )

    def extract_schema_keys(self, schema: List[SchemaField]) -> List[str]:
        return [
            field.key.strip()
            for field in schema
            if isinstance(field.key, str) and field.key.strip()
        ]

    def validate_filled_data(self,
    schema_keys: List[str],
    filled_data: Dict[str, str]
) -> Tuple[int, int, List[str]]:
        """
        Returns:
        - filled_count
        - missing_count
        - missing_keys
        """

        missing = []

        for key in schema_keys:
            if key not in filled_data or filled_data[key] in [None, ""]:
                missing.append(key)

        filled_count = len(schema_keys) - len(missing)

        return filled_count, len(missing), missing

    async def is_fill_all_node_data(
        self,
        workflow: WorkflowEvent,
        global_context: GlobalContext,
        executions: List[Task],
        conv_id: str
    )-> bool:
        run_id: str = workflow.run_id
        node_id: str = workflow.node_id
        instruction: str = workflow.instruction
        input_node_data: InputNodeResponse | None = await self.workflow_client.get_input_node(run_id=run_id, node_id=node_id)
        if input_node_data is None:
            return False
        # 1. Build prompt
        planning_context = PatchInputNodePlanningContext(
            schema=input_node_data.data.schema,
            global_context=global_context,
            instruction=instruction,
            executions=executions
        )

        # 2. Call LLM
        if self.workflow_input_node is None:
            return False
        patch_input_node_result: PatchInputNodeResult = await self.workflow_input_node.generate(planning_context)

        if patch_input_node_result and patch_input_node_result.mode == PatchInputMode.RAG_REQUEST:
            if self.rag_client:
                candidates: list[RagCandidate] = await self.rag_client.query_candidates(
                    query_text=f"{patch_input_node_result.suggesion_rag_query} and {global_context.model_dump_json()} ",
                    document_ids=patch_input_node_result.document_ids,
                    knowledge_base_ids=patch_input_node_result.knowledge_base_ids
                )
                if candidates or len(candidates) > 0:
                    await self.push_gateway_notifier.send_thinking_event(
                        conv_id=conv_id,
                        thinking_message="I'm looking for documents that may be relevant to your request......",
                    )
                    planning_context_with_rag = PatchInputNodePlanningContext(
                        schema=input_node_data.data.schema,
                        global_context=global_context,
                        instruction=instruction,
                        rag_candidates=candidates,
                        old_patch_input_node_result=patch_input_node_result,
                        executions=executions,)
                    patch_input_node_result: PatchInputNodeResult = await self.workflow_input_node.generate(planning_context_with_rag)

        # 3. Validate
        keys = self.extract_schema_keys(input_node_data.data.schema)
        filled_data = patch_input_node_result.filled_fields
        filled, missing_count, missing_keys = self.validate_filled_data(keys, filled_data)
        
        lines = []
        for key in keys:
            value = filled_data.get(key, None)
            lines.append(f"Key: {key}, Value: {value}")

        result = "\n".join(lines)
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message=f"I've filled in the following information based on the context you've provided:\n\n{result}"
        )

        # 4. Build payload
        payload = build_artiface_payload(filled_data)

        # 5. Submit API
        await self.workflow_client.patch_node_data(run_id, node_id, payload)

        if missing_count > 0:
            self.logger.info("Missing keys", extra={"missing_keys": missing_keys})

            return False
        await self.workflow_client.submit_node_data(run_id, node_id)
        return True

    async def _execute_workflow_request(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating workflow request status plan",
            event=asdict(event),
        )
        payload = event.payload
        workflow: WorkflowEvent | None = payload.workflow if payload else None
        if workflow is None:
            raise ValueError("Workflow data is required for workflow run status event")

        conv_id = self._get_conversation_id_by_run_id(workflow.root_run_id)

        planner: IPlanGenerator = self.planner_resolver.resolve(
            PlanningContextType.LLM_WITH_SIMPLE_NODE
        )

        if workflow.pending_kind in {PendingKind.INPUT}:
            if "agent-show-ui" in workflow.node_name.lower():
                await self._send_workflow_open_workspace_payload(workflow, conv_id, planner)
                return None
            context: GlobalContext | None = (
                self.agent_shared_state_repository.get_context_by_conv_id(conv_id)
            )
            is_filled: bool = False
            metadata = self._get_metadata(conv_id=conv_id)
            current_task_id = metadata.current_task_id
            if context is not None and context.conversation_context is not None:
                await self.push_gateway_notifier.send_thinking_event(
                    conv_id=conv_id,
                    thinking_message="I'm continuing the process by updating the required information based on the context you've provided...",
                    task_id=current_task_id,
                )

                executions: List[Task] = self.agent_shared_state_repository.get_executions_by_conv_id(conv_id)

                is_filled: bool = await self.is_fill_all_node_data(
                    workflow=workflow,
                    global_context=context,
                    conv_id=conv_id,
                    executions=executions

                )
            if not is_filled:
                await self.push_gateway_notifier.send_thinking_event(
                    conv_id=conv_id,
                    thinking_message="It looks like some required information is still missing, so I’m unable to complete all the necessary steps...",
                    task_id=current_task_id,
                )
                await self._send_workflow_form_tool_payload(workflow, conv_id, planner)
            else:
                await self.push_gateway_notifier.send_thinking_event(
                    conv_id=conv_id,
                    thinking_message="Great! I've received all the necessary information and have submitted it to continue the flow.",
                    task_id=current_task_id,
                )
        elif workflow.pending_kind in {PendingKind.FILE_UPLOAD}:
            await self._send_workflow_form_tool_payload(workflow, conv_id, planner)
        elif workflow.pending_kind in {PendingKind.DATA_EDIT}:
            await self._send_workflow_open_workspace_payload(workflow, conv_id, planner)
        elif workflow.pending_kind in {PendingKind.HUMAN_ACTION}:
            await self._send_workflow_button_group_payload(workflow, conv_id, planner)
        return None

    async def _execute_workflow_submit(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating workflow submit plan",
            event=asdict(event),
        )
        payload = event.payload
        workflow: WorkflowEvent | None = payload.workflow if payload else None
        if workflow is None:
            raise ValueError("Workflow data is required for workflow run status event")

        conv_id = self._get_conversation_id_by_run_id(workflow.root_run_id)

        planner: IPlanGenerator = self.planner_resolver.resolve(
            PlanningContextType.LLM_WITH_SIMPLE_NODE
        )
        if workflow.pending_kind in {PendingKind.INPUT}:
            await self._send_workflow_open_workspace_payload(workflow, conv_id, planner)

        return None

    async def _send_workflow_form_tool_payload(self, workflow: WorkflowEvent, conv_id: str, planner: IPlanGenerator):
        planning_context: SimpleNodePlanningContext = (
            await self._build_form_input_requested_context(workflow)
        )
        plan: PlannerResponse = await planner.generate(planning_context)

        if plan.type != PlannerResponseType.DIRECT_ANSWER:
            raise ValueError("Workflow run status requires a direct answer plan")
        if plan.answer is None:
            raise ValueError("plan.answer is required for workflow run status event")
        metadata = self._get_metadata(conv_id)
        generated_id = self.uuid_builder.create_uuid7()
        current_run_id = workflow.run_id
        new_conv_metadata = Metadata(
            conv_id=generated_id,
            main_conv_id=conv_id,
            checkpoint_id=generated_id,
            parent_checkpoint=metadata.checkpoint_id,
            last_event_id=None,
            correlation_id=generated_id,
            branch="sub",
            current_agent=metadata.current_agent,
            status=MetadataStatus.IN_PROGRESS,
            current_task_id=None,
            current_run_id=current_run_id,
            current_node_id=workflow.node_id,
        )
        new_global_context = GlobalContext(
             conversation_context=[], knowledge_base_id=""
        )
        new_conv_agent_share_state = AgentSharedState(
            metadata=new_conv_metadata,
            context=new_global_context,
            executions=[],
        )
        new_conv = Conversation(
            conv_id=generated_id, shared_state=new_conv_agent_share_state, user_id=""
        )
        self.agent_shared_state_repository.save_conversation(new_conv)
        # metadata = self._get_metadata(conv_id)
        wf_info = UIWFInfo(
            node_id=workflow.node_id,
            task_id=workflow.task_id,
            run_id=current_run_id,
        )
        ui_payload = UIPayload(
            id=generated_id,
            parent_id=metadata.current_task_id or "",
            type=UIPayloadType.FORM_TOOL,
            content=plan.answer,
            wf_info=wf_info,
            sub_conv_id=generated_id,
            status=TaskStatus.PROCESSING,
        )
        await self.push_gateway_notifier.send_tool_form_event(
            conv_id=conv_id,
            ui_payload=ui_payload,
        )

    async def _send_workflow_open_workspace_payload(self, workflow: WorkflowEvent, conv_id: str, planner: IPlanGenerator):
        planning_context: SimpleNodePlanningContext = (
            await self._build_form_input_requested_context(workflow)
        )
        plan: PlannerResponse = await planner.generate(planning_context)

        if plan.type != PlannerResponseType.DIRECT_ANSWER:
            raise ValueError("Workflow run status requires a direct answer plan")
        if plan.answer is None:
            raise ValueError("plan.answer is required for workflow run status event")
        metadata = self._get_metadata(conv_id)
        current_run_id = workflow.run_id
        generated_id = self.uuid_builder.create_uuid7()
        new_conv_metadata = Metadata(
            conv_id=generated_id,
            main_conv_id=conv_id,
            checkpoint_id=generated_id,
            parent_checkpoint=metadata.checkpoint_id,
            last_event_id=None,
            correlation_id=generated_id,
            branch="sub",
            current_agent=metadata.current_agent,
            status=MetadataStatus.IN_PROGRESS,
            current_task_id=None,
            current_run_id=current_run_id,
            current_node_id=workflow.node_id,
        )
        new_global_context = GlobalContext(
           conversation_context=[], knowledge_base_id=""
        )
        new_conv_agent_share_state = AgentSharedState(
            metadata=new_conv_metadata,
            context=new_global_context,
            executions=[],
        )
        new_conv = Conversation(
            conv_id=generated_id, shared_state=new_conv_agent_share_state, user_id=""
        )
        self.agent_shared_state_repository.save_conversation(new_conv)
        metadata = self._get_metadata(conv_id)
        wf_info = UIWFInfo(
            node_id=workflow.node_id,
            task_id=workflow.task_id,
            run_id=workflow.run_id,
        )
        ui_payload = UIPayload(
            id=generated_id,
            parent_id=metadata.current_task_id or "",
            type=UIPayloadType.OPEN_WORKSPACE,
            content=plan.answer,
            wf_info=wf_info,
            text=[UIPayloadText.OPEN_WORKSPACE],
            sub_conv_id=generated_id,
            status=TaskStatus.PROCESSING,
        )
        await self.push_gateway_notifier.send_tool_form_event(
            conv_id=conv_id,
            ui_payload=ui_payload,
        )

    async def _send_workflow_button_group_payload(self, workflow: WorkflowEvent, conv_id: str, planner: IPlanGenerator):
        planning_context: SimpleNodePlanningContext = (
            await self._build_form_input_requested_context(workflow)
        )
        plan: PlannerResponse = await planner.generate(planning_context)

        if plan.type != PlannerResponseType.DIRECT_ANSWER:
            raise ValueError("Workflow run status requires a direct answer plan")
        if plan.answer is None:
            raise ValueError("plan.answer is required for workflow run status event")
        generated_id = self.uuid_builder.create_uuid7()
        metadata = self._get_metadata(conv_id)
        wf_info = UIWFInfo(
            node_id=workflow.node_id,
            task_id=workflow.task_id,
            run_id=workflow.run_id,
        )
        ui_payload = UIPayload(
            id=generated_id,
            parent_id=metadata.current_task_id or "",
            type=UIPayloadType.BUTTON_GROUP,
            content=plan.answer,
            wf_info=wf_info,
            text=[workflow.confirm_label or "Yes", workflow.reject_label or "No"],
        )
        await self.push_gateway_notifier.send_tool_form_event(
            conv_id=conv_id,
            ui_payload=ui_payload,
        )

    async def _execute_main_user_rerun(self, event: EventDTO) -> None:
        await self.push_gateway_notifier.send_chat_disable_event(event.conv_id)
        self.logger.info(
            "Generating main user rerun plan",
            event=asdict(event),
        )
        # later:
        # likely rule-based planner
        return None

    async def _execute_workflow_node_status(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating workflow node run status plan",
            event=asdict(event),
        )
        payload = event.payload
        workflow = payload.workflow if payload else None
        if workflow is None:
            raise ValueError("Workflow data is required for workflow run status event")

        conv_id = self._get_conversation_id_by_run_id(workflow.root_run_id)

        planner: IPlanGenerator = self.planner_resolver.resolve(
            PlanningContextType.LLM_WITH_SIMPLE_NODE
        )
        planning_context: SimpleNodePlanningContext = (
            await self._build_node_run_status_context(workflow)
        )
        plan: PlannerResponse = await planner.generate(planning_context)

        if plan.type != PlannerResponseType.DIRECT_ANSWER:
            raise ValueError("Workflow run status requires a direct answer plan")
        if plan.answer is None:
            raise ValueError("plan.answer is required for workflow run status event")

        metadata = self._get_metadata(conv_id=conv_id)
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message=plan.answer,
            task_id=metadata.current_task_id,
        )

        return None

    async def _execute_workflow_node_ai_feed(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating workflow node AI feed plan",
            event=asdict(event),
        )

        workflow_event = event.payload.workflow if event.payload else None
        if workflow_event is None:
            raise ValueError("Workflow data is required")

        conv_id: str = self._get_conversation_id_by_run_id(
            workflow_event.root_run_id
        )

        if self.rag_client is None:
            self.logger.info("RAG client is not initialized")
            return

        deps = AIFeedDeps(
            rag_client=self.rag_client,
            workflow_client=self.workflow_client,
            file_client=self.file_client,
            logger=self.logger,
            push_gateway=self.push_gateway_notifier,
            planner_resolver=self.planner_resolver,
            agent_shared_state_repository=self.agent_shared_state_repository,
            uuid_builder=self.uuid_builder,
            settings=self.settings,
        )

        await execute_ai_feed_pipeline(
            event=event,
            conv_id=conv_id,
            workflow_event=workflow_event,
            deps=deps,
        )

    async def _execute_workflow_node_dispatch_status(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating workflow node dispatch status plan",
            event=json.dumps(asdict(event)),
        )
        payload = event.payload
        workflow = payload.workflow if payload else None
        if workflow is None:
            raise ValueError("Workflow data is required for workflow dispatch status event")

        conv_id = self._get_conversation_id_by_run_id(workflow.root_run_id)

        planner: IPlanGenerator = self.planner_resolver.resolve(
            PlanningContextType.LLM_WITH_SIMPLE_NODE
        )
        planning_context: SimpleNodePlanningContext = (
            await self._build_node_dispatch_status_context(workflow)
        )
        if workflow.node is not None and workflow.node.name == "view_space":
            plan: PlannerResponse = await planner.generate(planning_context)

            if plan.type != PlannerResponseType.DIRECT_ANSWER:
                raise ValueError("Workflow run status requires a direct answer plan")
            if plan.answer is None:
                raise ValueError("plan.answer is required for workflow run status event")

            metadata = self._get_metadata(conv_id=conv_id)
            await self.push_gateway_notifier.send_thinking_event(
                conv_id=conv_id,
                thinking_message=plan.answer,
                task_id=metadata.current_task_id,
            )
            input: HttpRequestInput | None= workflow.input
            import re
            url = input.url if input else ""
            match = re.search(r"/records/(\d+)/", url)

            if match:
                record_id = match.group(1)
                print(record_id)  # 1935
                metadata = self._get_metadata(conv_id)
                await self.push_gateway_notifier.send_view_space_event(
                    conv_id=conv_id,
                    record_id=record_id,
                    content=plan.answer,
                    parent_id=metadata.current_task_id or "",
                    
                )

        return None

    async def _execute_workflow_run_status(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating workflow run status plan",
            event=asdict(event),
        )
        payload = event.payload
        workflow = payload.workflow if payload else None
        if workflow is None:
            raise ValueError("Workflow data is required for workflow run status event")

        conv_id = self._get_conversation_id_by_run_id(workflow.root_run_id)
        metadata = self._get_metadata(conv_id)
        if workflow.run_id != workflow.root_run_id:
            return None

        planner: IPlanGenerator = self.planner_resolver.resolve(
            PlanningContextType.LLM_WITH_SIMPLE_NODE
        )

        if workflow.status in (WorkflowStatus.STARTED):
            await self._handle_active_workflow(workflow, conv_id, metadata, planner)

        elif workflow.status in (WorkflowStatus.FAILED):
            await self._handle_error_propagation(metadata=metadata)
            raise ValueError(
                f"Workflow {workflow.workflow.name if workflow.workflow else ''} has failed or been canceled, so I'm unable to proceed with the related tasks. Please let me know if you'd like to try something else or need help with anything different."
            )
        elif workflow.status in (WorkflowStatus.SUSPENDED):
            await self._handle_error_propagation(metadata=metadata)
            raise ValueError(
                f"Workflow {workflow.workflow.name if workflow.workflow else ''} has been suspended, so I'm unable to proceed with the related tasks. Please let me know if you'd like to try something else or need help with anything different."
            )

        elif workflow.status == WorkflowStatus.COMPLETED:
            await self._handle_workflow_completed(
                conv_id=conv_id,
                metadata=metadata,
                workflow=workflow,
            )

        return None

    async def _handle_active_workflow(
        self,
        workflow: WorkflowEvent,
        conv_id: str,
        metadata: Metadata,
        planner: IPlanGenerator,
    ):

        planning_context: SimpleNodePlanningContext = (
            await self._build_workflow_run_status_context(workflow)
        )

        plan = await planner.generate(planning_context)

        if not plan.answer:
            raise ValueError("plan.answer is required")

        ui_payload = UIPayload(
            id=self.uuid_builder.create_uuid7(),
            parent_id=metadata.current_task_id,
            type=UIPayloadType.TEXT,
            content=plan.answer,
        )

        await self.push_gateway_notifier.send_process_response_with_text_event(
            conv_id=conv_id,
            ui_payload=ui_payload,
        )

    async def _handle_current_child_task(
        self, conv_id: str, current_child_task: Task, task_status: TaskStatus = TaskStatus.SUCCESS
    ) -> None:
        current_child_task.status = task_status

        self.agent_shared_state_repository.update_task(
            conv_id=conv_id,
            task_id=current_child_task.id,
            status=task_status,
        )

        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id, task=current_child_task
        )

    async def _handle_child_chain(
        self, metadata: Metadata, current_child_task: Task
    ):

        if not current_child_task.next_task_id:
            return False

        conv_id = metadata.conv_id
        next_child = self.agent_shared_state_repository.get_task(
            conv_id=conv_id, task_id=current_child_task.next_task_id
        )

        if next_child is None:
            raise ValueError("Next child task not found")

        metadata.current_task_id = next_child.id

        self.agent_shared_state_repository.update_metadata(
            conv_id=conv_id, metadata=metadata
        )

        await self.send_task_to_executor(
            conv_id, current_child_task.execute_by or "", next_child
        )

        return True

    async def _handle_parent_chain(
        self, metadata: Metadata, current_child_task: Task, task_status: TaskStatus = TaskStatus.SUCCESS
    ):
        conv_id = metadata.conv_id
        parent_task_id = current_child_task.parent_task_id
        if not parent_task_id:
            # Top-level task (e.g. a workflow-only plan) has no parent to
            # propagate completion up to. Treat as the root and let the caller
            # proceed to finalize the workflow.
            return False

        parent = self.agent_shared_state_repository.get_task(
            conv_id=conv_id, task_id=parent_task_id
        )

        if parent is None:
            raise ValueError("Parent task not found")

        # parent has next sibling
        if parent.next_task_id:

            next_parent = self.agent_shared_state_repository.get_task(
                conv_id=conv_id, task_id=parent.next_task_id
            )

            if next_parent is None:
                raise ValueError("Next parent task not found")

            if task_status == TaskStatus.SUCCESS:
                await self._handle_next_task(
                    conv_id=conv_id, metadata=metadata, current_task=next_parent
                )
                return True

        # last parent
        parent.status = task_status

        self.agent_shared_state_repository.update_task(
            conv_id=conv_id,
            task_id=parent.id,
            status=task_status,
        )

        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id,
            task=parent,
        )

        return False

    async def _finalize_workflow(
        self,
        metadata: Metadata,
    ) -> None:

        metadata.current_task_id = ""
        conv_id: str = metadata.conv_id
        self.agent_shared_state_repository.update_metadata(
            conv_id=conv_id, metadata=metadata
        )

        summary_context: ExecutionSummaryContext = (
            await self._build_execution_summary_context(metadata)
        )

        summary_planner: IPlanGenerator = self.planner_resolver.resolve(
            PlanningContextType.LLM_EXECUTION_SUMMARY
        )
        plan: PlannerResponse = await summary_planner.generate(summary_context)

        if plan.answer is not None:
            await self.push_gateway_notifier.send_task_summary_event(
                conv_id=conv_id,
                summary=plan.answer,
                suggestions=plan.suggestions,
            )
            if self.logger:
                self.logger.info(
                    "Generated next-step suggestions after task completion",
                    conv_id=conv_id,
                    suggestion_count=len(plan.suggestions),
                )

    async def _build_execution_summary_context(
        self, metadata: Metadata
    ) -> ExecutionSummaryContext:
        conv_id: str = metadata.conv_id

        global_context: GlobalContext | None = (
            self.agent_shared_state_repository.get_context_by_conv_id(conv_id)
        )
        if global_context is None:
            raise ValueError(
                f"Global context not found for conversation: {conv_id}"
            )

        current_message: MessageHistory | None = next(
            (
                message
                for message in global_context.conversation_context
                if message.message_id == metadata.current_message_id
            ),
            None,
        )
        if current_message is None:
            raise ValueError(
                f"Current message not found for message_id: {metadata.current_message_id}"
            )

        executions: List[Task] = (
            self.agent_shared_state_repository.get_executions_by_message_id(
                conv_id, metadata.current_message_id
            )
        )

        return ExecutionSummaryContext(
            current_message=current_message,
            executions=executions,
            global_context=global_context,
            metadata=metadata,
        )

    async def _handle_workflow_completed(
        self,
        conv_id: str,
        metadata: Metadata,
        workflow: WorkflowEvent,
    ) -> None:

        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message=f"I've successfully completed the {workflow.workflow.name if workflow.workflow else ''} workflow.",
            task_id=metadata.current_task_id,
        )
        if metadata.current_task_id is None:
            raise ValueError("current_task_id is None")

        current_child_task = self.agent_shared_state_repository.get_task(
            conv_id=conv_id, task_id=metadata.current_task_id
        )

        if current_child_task is None:
            raise ValueError("Child task not found")

        # mark child
        await self._handle_current_child_task(conv_id, current_child_task)

        # Refresh the run output on the task that started this workflow. The
        # workflow_start_run tool persisted output_data with status RUNNING when it
        # kicked the run off; if we leave it stale the execution summary reads the
        # run as still in progress even though it has just completed.
        self._mark_run_output_completed(
            conv_id, metadata, current_child_task, workflow
        )

        # child chain
        if await self._handle_child_chain(metadata, current_child_task):
            return

        # parent chain
        await self._handle_parent_chain(metadata, current_child_task)

        # finalize
        await self._finalize_workflow(metadata)

    def _mark_run_output_completed(
        self,
        conv_id: str,
        metadata: Metadata,
        task: Task,
        workflow: WorkflowEvent,
    ) -> None:
        """Sync the started run's persisted output_data with the task outcome.

        The workflow_start_run step is now ``success``, but its output_data still
        carries the run's ``RUNNING`` status from kick-off time. This task owns the
        finished run when ``metadata.current_run_id`` matches its
        ``input_data["predefined_run_id"]``; when it does, rewrite the output_data
        ``status``/``completed_at`` so the finalize summary reads a succeeded run
        instead of a running one.
        """
        run_id = metadata.current_run_id
        if not run_id:
            return

        input_data = task.input_data or {}
        if input_data.get("predefined_run_id") != run_id:
            return

        updated_output = dict(task.output_data or {})
        updated_output["status"] = TaskStatus.SUCCESS.value
        if workflow.timestamp:
            updated_output["completed_at"] = workflow.timestamp

        task.output_data = updated_output
        self.agent_shared_state_repository.update_task(
            conv_id=conv_id,
            task_id=task.id,
            output_data=updated_output,
        )

    async def _execute_user_action(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating user action plan",
            event=asdict(event),
        )

        # later:
        # likely rule-based planner
        return None

    async def _execute_workflow_node_flow_viewer(self, event: EventDTO) -> None:
        self.logger.info(
            "Generating workflow request status plan",
            event=asdict(event),
        )
        payload = event.payload
        workflow: WorkflowEvent | None = payload.workflow if payload else None
        if workflow is None:
            raise ValueError("Workflow data is required for workflow run status event")

        conv_id = self._get_conversation_id_by_run_id(workflow.root_run_id)

        planner: IPlanGenerator = self.planner_resolver.resolve(
            PlanningContextType.LLM_WITH_SIMPLE_NODE
        )

        await self._send_workflow_open_viewer_payload(workflow, conv_id, planner)

        return None

    async def _send_workflow_open_viewer_payload(
        self, workflow: WorkflowEvent, conv_id: str, planner: IPlanGenerator
    ):
        planning_context: SimpleNodePlanningContext = (
            await self._build_node_viewer_context(workflow)
        )
        metadata = self._get_metadata(conv_id)
        await self.push_gateway_notifier.send_thinking_event(
            conv_id=conv_id,
            thinking_message=planning_context.node_message,
            task_id=metadata.current_task_id,
        )
        plan: PlannerResponse = await planner.generate(planning_context)

        if plan.type != PlannerResponseType.DIRECT_ANSWER:
            raise ValueError("Workflow run status requires a direct answer plan")
        if plan.answer is None:
            raise ValueError("plan.answer is required for workflow run status event")
        
        generated_id = self.uuid_builder.create_uuid7()

        wf_info = UIWFInfo(
            node_id=workflow.node_id,
            task_id=workflow.task_id,
            run_id=workflow.run_id,
        )
        ui_payload = UIPayload(
            id=generated_id,
            parent_id=metadata.current_task_id,
            type=UIPayloadType.OPEN_VIEWER,
            content=plan.answer,
            wf_info=wf_info,
            text=[UIPayloadText.OPEN_VIEWER],
            sub_conv_id=generated_id,
            status=TaskStatus.PROCESSING,
            change_request_id=workflow.change_request_id,
        )
        await self.push_gateway_notifier.send_viewer_event(
            conv_id=conv_id,
            ui_payload=ui_payload,
        )

    async def _build_node_viewer_context(
        self, workflow_event: WorkflowEvent
    ) -> SimpleNodePlanningContext:

        if workflow_event is not None:
            workflow_run_info: WorkflowRunInfoResponse | None = (
                await self.workflow_client.get_workflow_run(workflow_event.root_run_id)
            )
            if not workflow_run_info:
                raise ValueError(
                    f"Workflow run not found: {workflow_event.root_run_id}"
                )

            workflows_hub: list[WorkflowHub] = (
                await self.agent_hub_client.get_workflows_by_ids(
                    [workflow_run_info.data.workflow_id]
                )
            )
            if not workflows_hub:
                raise ValueError(
                    f"Workflow not found: {workflow_run_info.data.workflow_id}"
                )

            return SimpleNodePlanningContext(
                workflows_hub=[WorkflowHubDTO.to_dto(w) for w in workflows_hub],
                node_message=f"Node name '{workflow_event.node_name}' with instruction '{workflow_event.instruction}' is ready for review.",
            )
