from enum import Enum
import json
from pydantic import BaseModel
from app.layer1_domain.entities.queue_event import (
    QueueEvent,
    QueueRequestType,
    QueuePayloadTo,
    QueuePayloadType,
    QueueStep,
    QueuePayload,
)
from agent_sdk import Settings
from app.layer2_application.features.main_agent.dto.event import EventDTO, FileSummaryOutputDTO 
from app.layer2_application.interfaces.logger_interface import ILogger

from app.layer2_application.interfaces.reviewer_resolver_interface import (
    IReviewerResolver,
)
from app.layer2_application.interfaces.planner_resolver_interface import (
    IPlannerResolver,
)
from app.layer2_application.interfaces.push_gateway_notifier_interface import (
    IPushGatewayNotifier,
)

from app.layer2_application.interfaces.business_agent_shared_state_repository_interface import (
    IBusinessAgentSharedStateRepository,
)
from agent_sdk.layer1_domain.entities.agent_shared_state import (
    Metadata,
    Task,
    TaskStatus,
)
from agent_sdk.layer1_domain.entities.conversation_context import KnowledgeItem
from agent_sdk.layer2_application.interfaces.message_publisher import (
    IMessagePublisher,
)
from app.layer1_domain.entities.review_context import (
    ReviewContext, ReviewStatus
)
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder
from app.layer2_application.interfaces.review_generator_interface import IReviewGenerator
from app.layer2_application.interfaces.plan_generator_interface import IPlanGenerator
from app.layer1_domain.entities.planner import PlannerResponse
from app.layer1_domain.entities.planning_context import (
    PlanningContextType,
    SummaryContext,
)
from app.layer1_domain.entities.review_context import ReviewContextType
from app.layer1_domain.entities.reviewer import ReviewResponse

class UnsupportedEventTypeError(ValueError):
    pass


class PlanCaseName(str, Enum):
    EXECUTOR_STEP_STATUS = "executor_step_status"


class PlanCase(BaseModel):
    name: PlanCaseName
    event_types: frozenset[str]
    conversation_scope: str


PLAN_CASES: tuple[PlanCase, ...] = (
    PlanCase(
        name=PlanCaseName.EXECUTOR_STEP_STATUS,
        event_types=frozenset({QueueRequestType.EXECUTOR_STEP_STATUS.value}),
        conversation_scope="child",
    ),
)


class ReviewPlanUseCase:
    def __init__(
        self,
        reviewer_resolver: IReviewerResolver,
        push_gateway_notifier: IPushGatewayNotifier,
        agent_shared_state_repository: IBusinessAgentSharedStateRepository,
        publisher: IMessagePublisher,
        uuid_builder: IUuidBuilder,
        settings: Settings | None = None,
        logger: ILogger | None = None,
        planner_resolver: IPlannerResolver | None = None,
    ):
        self.reviewer_resolver = reviewer_resolver
        self.push_gateway_notifier = push_gateway_notifier
        self.agent_shared_state_repository = agent_shared_state_repository
        self.logger = logger
        self.publisher = publisher
        self.uuid_builder = uuid_builder
        self.settings = settings
        self.planner_resolver = planner_resolver
        self.reviewer: IReviewGenerator = self.reviewer_resolver.resolve(ReviewContextType.LLM)

    async def execute(self, event: EventDTO) -> None:
        event_entity = event.to_entity()  # validate and convert DTO to Entity
        if self.logger:
            self.logger.info(
                "Executing ReviewPlanUseCase with event entity",
                event_entity=event_entity,
            )
        plan_case = self._resolve_plan_case(event_entity.type)
        if self.logger:
            self.logger.info(
                "Resolved plan case",
                plan_case=plan_case.name.value,
            )

        match plan_case.name:
            case PlanCaseName.EXECUTOR_STEP_STATUS:
                return await self._execute_executor_step_status(event_entity)

            case _:
                raise UnsupportedEventTypeError(
                    f"Unsupported plan case: {plan_case.name}"
                )

    def _resolve_plan_case(self, event_type: str) -> PlanCase:
        for plan_case in PLAN_CASES:
            if event_type in plan_case.event_types:
                return plan_case

        raise UnsupportedEventTypeError(f"Unsupported event type: {event_type}")

    def _incoming_step_task_is_cancelled(self, event: QueueEvent) -> bool:
        """SA-892 (AC4): True iff the step-result's OWN task is CANCELLED.

        A Stop cancels the current message's tasks (Task.status=CANCELLED). The
        ``executor.step.status`` payload carries the step id, which equals the
        ``Task.id`` in the conversation's executions (a step / technical-agent
        result callback). Look it up; if that task is CANCELLED, this is a LATE
        callback for a stopped turn and must be dropped — no review, no
        ``update_task`` (which would flip it PROCESSING/ERROR), no push (which
        would re-enable the chat or show a Retry button). Mirrors the
        CreatePlanUseCase per-task guard for the step-result path. Best-effort:
        any missing id / unresolved task / read error → NOT cancelled, so the
        guard never blocks a legitimate result on infrastructure noise.
        """
        try:
            conv_id = (event.conv_id or "").strip()
            steps = event.payload.steps if event.payload else None
            task_id = (steps[0].id if steps else "") or ""
            if not conv_id or not task_id:
                return False
            task = self.agent_shared_state_repository.get_task(conv_id, task_id)
            if task is None:
                return False
            return task.status == TaskStatus.CANCELLED
        except Exception as exc:  # never block a legitimate result on noise
            if self.logger:
                self.logger.warning(
                    "SA-892 review-plan stop guard task lookup failed",
                    error=str(exc),
                )
            return False

    async def _execute_executor_step_status(self, event: QueueEvent) -> None:
        # SA-892 (AC4): drop a late step / technical-agent result whose own task a
        # Stop already CANCELLED — otherwise ReviewPlanUseCase would review it,
        # flip the task via update_task, and push chat:enable + a Retry button to
        # the stopped conversation (a late-result leak / partial resurrection).
        if self._incoming_step_task_is_cancelled(event):
            if self.logger:
                first = event.payload.steps[0] if (event.payload and event.payload.steps) else None
                self.logger.info(
                    "SA-892: dropping executor.step.status for CANCELLED task",
                    conv_id=event.conv_id,
                    task_id=(first.id if first else None),
                )
            return None

        # TODO: implement the logic to review execution plan for executor step status event
        status = event.payload.steps[0].status.lower()

        await self._update_output_data_current_task(event)

        if status == TaskStatus.ERROR:
            await self._handle_tool_failure(event)
        elif status == TaskStatus.SUCCESS:
            await self._handle_tool_success(event)
        else:
            await self._handle_tool_progressing(event)

        return None
    
    def _is_main_chat(self, conv_id):
        try:
            meta_data = self.agent_shared_state_repository.get_metadata(conv_id)
        except:
            raise ValueError(
                f"Metadata not found for conversation {conv_id}"
            )

        return meta_data.main_conv_id is None or not meta_data.main_conv_id.strip()


    async def _handle_tool_failure(self, event):
        """Tool crashed / System error branch"""
        conv_id = event.conv_id
        first_step = event.payload.steps[0]
        message = first_step.message
        task_id = first_step.id

        if self._is_main_chat(conv_id=conv_id):
            #Update step status in share state
            updated_task: Task | None = (
                self.agent_shared_state_repository.update_task(
                    conv_id, task_id, status=TaskStatus.ERROR
                )
            )
            
            await self.push_gateway_notifier.send_task_status_event(
                conv_id=conv_id, task=updated_task
            )
            await self.push_gateway_notifier.send_task_summary_event(
                conv_id=conv_id, summary=message
            )
            # Return retry button
            await self.push_gateway_notifier.send_retry_button(
                conv_id=conv_id, task=updated_task
            )
        else:
            #Update step status in share state
            updated_task: Task | None = (
                self.agent_shared_state_repository.update_task(
                    conv_id, task_id, status=TaskStatus.ERROR
                )
            )
            # reviewer: IReviewGenerator = self.reviewer_resolver.resolve(ReviewContextType.LLM)
            # # Fetch updated plan
            # conversation = self.agent_shared_state_repository.get_conversation(conv_id)
            # plan = conversation.shared_state.executions
            # filtered_tasks = [task for task in plan if task.status != TaskStatus.PENDING]
            
            # # Update fail message in task list

            # # Send prompt to summarize
            # summarized_context = await reviewer.summarize(filtered_tasks)

            await self.push_gateway_notifier.send_task_summary_event(
                conv_id=conv_id, summary=message
            )
        return None

    async def _handle_tool_success(self, event):
        conv_id = event.conv_id
        first_step: QueueStep = event.payload.steps[0]

        if self._is_file_summary_step(first_step):
            first_step.output_data = await self._build_file_summary_output_data(
                conv_id, first_step
            )
            self._save_file_summary_to_task_knowledge(conv_id, first_step)
        await self._update_output_data_current_task(event)

        actual_output: str = first_step.message
        expect_output: str = first_step.goal
        
        if self._is_main_chat(conv_id=conv_id):
            # Get node information 
            # node_information = ""
            # context_data = ReviewContext(
            #     node_information=node_information,
            #     expected_output=expect_output,
            #     actual_output=actual_output
            # )

            # # Send prompt to summarize
            # response = await self.reviewer.generate(context_data)
            # first_step.output_data.id
            review_output = ReviewResponse(evaluation = ReviewStatus.SUCCESS, reason="")

            await self._validate_response_from_main_chat(event, response=review_output)
        else:
            node_information = ""
            context_data = ReviewContext(
                node_information=node_information,
                expected_output=expect_output,
                actual_output=actual_output
            )
            # Send prompt to summarize
            response = await self.reviewer.generate(context_data)
            # Parse & validate JSON. The plain-language per-step summary is produced
            # INSIDE _validate_response_from_node_chat, after the task is marked SUCCESS,
            # so it reads as done rather than "processing". (post-discuss change)
            await self._validate_response_from_node_chat(event, response=response)

        return None

    async def _handle_tool_progressing(self, event):
        conv_id = event.conv_id
        task_id = event.payload.steps[0].id
        #Update step status in share state
        updated_task: Task | None = (
            self.agent_shared_state_repository.update_task(
                conv_id, task_id, status=TaskStatus.PROCESSING
            )
        )
        # Emit event to FE
        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id, task=updated_task
        )
        return None
    
    async def _validate_response_from_node_chat(self, event, response: ReviewResponse):
        conv_id = event.conv_id
        task_id = event.payload.steps[0].id
        if response.evaluation == ReviewStatus.FAIL:
            updated_task: Task | None = (
                self.agent_shared_state_repository.update_task(
                    conv_id, task_id, status=TaskStatus.FAIL
                )
            )
            # Get summarized plan
            
            # Send summarized context
            await self.push_gateway_notifier.send_task_summary_event(
                conv_id=conv_id, summary=response.reason
            )
        elif response.evaluation == ReviewStatus.NEED_USER_INPUT:
            updated_task: Task | None = (
                self.agent_shared_state_repository.update_task(
                    conv_id, task_id, status=TaskStatus.SUCCESS
                )
            )
            await self.push_gateway_notifier.send_task_summary_event(
                conv_id=conv_id, summary=response.reason
            )
        elif response.evaluation == ReviewStatus.SUCCESS:
            updated_task: Task | None = (
                self.agent_shared_state_repository.update_task(
                    conv_id, task_id, status=TaskStatus.SUCCESS
                )
            )

            current_task = self.agent_shared_state_repository.get_task(conv_id, task_id)

            if current_task is None:
                raise ValueError(
                    f"Not found Task: {task_id}"
                )

            # Summarize the step AFTER it is marked SUCCESS, so the plain-language reply
            # reports it as done (not "processing"). (post-discuss change)
            summarized_message = await self.reviewer.summarize_step(current_task)

            # Push action to agent_queue
            if current_task.next_task_id:
                next_task = self.agent_shared_state_repository.get_task(conv_id, current_task.next_task_id)
                if next_task is None:
                    raise ValueError(
                        f"Next task not found: conv_id={conv_id}, next_task_id={current_task.next_task_id}"
                    )
                
                await self.push_gateway_notifier.send_thinking_event(
                    conv_id=conv_id,
                    thinking_message=summarized_message,
                )
                
                generated_id = self.uuid_builder.create_uuid7()
                new_message_id = self.uuid_builder.create_uuid7()
                new_correlation_id = self.uuid_builder.create_uuid7()
                payload = QueueEvent(
                    message_id=new_message_id,
                    correlation_id=new_correlation_id,
                    type=QueueRequestType.AGENT_REQUEST_EXECUTOR,
                    conv_id=conv_id,
                    payload=QueuePayload(
                        from_=self._get_agent_id_from_task(current_task),
                        to=QueuePayloadTo.EXECUTOR_RUNTIME,
                        type=QueuePayloadType.STEP_BATCH,
                        batch_id=generated_id,
                        steps=[
                            QueueStep(
                                id=next_task.id,
                                execute_by=next_task.execute_by,
                                next_step_id=next_task.next_task_id,
                                goal=next_task.goal,
                                status=next_task.status,
                                message=next_task.goal,
                                uploaded_file_ids=[],
                                input_data=next_task.input_data or {},
                                output_data=next_task.output_data or {},
                            )
                        ],
                    ),
                )

                self._sync_current_run_id_from_task(conv_id, next_task)
                await self.publisher.publish(
                    topic=self.settings.EXECUTOR_TOPIC,
                    message=payload.to_dict(),
                    key=generated_id,
                )
            else:
                await self._mark_parent_task_success_if_needed(conv_id, current_task)

                await self._send_summarization_to_ui(conv_id=conv_id)


    async def _validate_response_from_main_chat(self, event, response: ReviewResponse):
        conv_id = event.conv_id
        task_id = event.payload.steps[0].id
        
        if response.evaluation == ReviewStatus.FAIL:
            updated_task: Task | None = (
                self.agent_shared_state_repository.update_task(
                    conv_id, task_id, status=TaskStatus.FAIL
                )
            )

            # Fetch updated plan
            conversation = self.agent_shared_state_repository.get_conversation(conv_id)
            plan = conversation.shared_state.executions
            filtered_tasks = [task for task in plan if task.status != TaskStatus.PENDING]
            
            # Send prompt to summarize
            # summarized_context = await self.reviewer.summarize(filtered_tasks)
            # await self.push_gateway_notifier.send_task_summary_event(
            #     conv_id=conv_id, summary=summarized_context
            # )

            # Return step status
            await self.push_gateway_notifier.send_task_status_event(
                conv_id=conv_id, task=updated_task
            )
            
            # Send fail message
            await self.push_gateway_notifier.send_task_summary_event(
                conv_id=conv_id, summary=response.reason
            )

            # Send retry button
            await self.push_gateway_notifier.send_retry_button(
                conv_id=conv_id, task=updated_task
            )

        elif response.evaluation == ReviewStatus.SUCCESS:
            if (event.payload.steps[0].input_data or {}).get("predefined_run_id") is not None:
                return
            
            updated_task: Task | None = (
                self.agent_shared_state_repository.update_task(
                    conv_id, task_id, status=TaskStatus.SUCCESS
                )
            )

            await self.push_gateway_notifier.send_task_status_event(
                conv_id=conv_id, task=updated_task
            )
            current_task = self.agent_shared_state_repository.get_task(conv_id, task_id)

            if current_task is None:
                raise ValueError(
                    f"Not found Task: {task_id}"
                )

            # Push action to agent_queue
            if current_task.next_task_id:
                next_task = self.agent_shared_state_repository.get_task(
                    conv_id,
                    current_task.next_task_id,
                )
                if next_task is None:
                    await self._send_summarization_to_ui(conv_id=conv_id)
                    return
                generated_id = self.uuid_builder.create_uuid7()
                new_message_id = self.uuid_builder.create_uuid7()
                new_correlation_id = self.uuid_builder.create_uuid7()
                payload = QueueEvent(
                    message_id=new_message_id,
                    correlation_id=new_correlation_id,
                    type=QueueRequestType.AGENT_REQUEST_EXECUTOR,
                    conv_id=conv_id,
                    payload=QueuePayload(
                        from_=self._get_agent_id_from_task(current_task),
                        to=QueuePayloadTo.EXECUTOR_RUNTIME,
                        type=QueuePayloadType.STEP_BATCH,
                        batch_id=generated_id,
                        steps=[
                            QueueStep(
                                id=next_task.id,
                                execute_by=next_task.execute_by,
                                next_step_id=next_task.next_task_id,
                                goal=next_task.goal,
                                status=next_task.status,
                                message=next_task.goal,
                                uploaded_file_ids=[],
                                input_data=next_task.input_data or {},
                                output_data=next_task.output_data or {},
                            )
                        ],
                    ),
                )

                self._sync_current_run_id_from_task(conv_id, next_task)
                await self.publisher.publish(
                    topic=self.settings.EXECUTOR_TOPIC,
                    message=payload.to_dict(),
                    key=generated_id,
                )
            else:
                await self._send_summarization_to_ui(conv_id=conv_id)

    def _sync_current_run_id_from_task(self, conv_id: str, task: Task) -> None:
        input_data = task.input_data or {}
        predefined_run_id = input_data.get("predefined_run_id")
        if not isinstance(predefined_run_id, str) or not predefined_run_id.strip():
            return
        metadata = self._get_metadata(conv_id)
        metadata.current_run_id = predefined_run_id
        self.agent_shared_state_repository.update_metadata(conv_id, metadata)
                
    def _is_file_summary_step(self, step: QueueStep) -> bool:
        input_data = step.input_data or {}
        return input_data.get("_business_agent_file_summary") is True

    async def _build_file_summary_output_data(
        self,
        conv_id: str,
        step: QueueStep,
    ) -> dict:
        input_data = step.input_data or {}
        file_id = str(input_data.get("file_id") or "")
        content_type = str(input_data.get("content_type") or "")
        raw_output = step.output_data or {}
        fallback = step.message or json.dumps(raw_output, default=str, ensure_ascii=False)
        summary = fallback or "No summary available"

        if self.planner_resolver is not None:
            try:
                planner: IPlanGenerator = self.planner_resolver.resolve(
                    PlanningContextType.LLM_SUMMARY_CONTEXT_GENERATOR
                )
                plan: PlannerResponse = await planner.generate(
                    SummaryContext(
                        context=json.dumps(
                            {
                                "file_id": file_id,
                                "content_type": content_type,
                                "raw_agent_message": step.message,
                                "raw_agent_output": raw_output,
                            },
                            default=str,
                            ensure_ascii=False,
                        )
                    )
                )
                summary = plan.answer or summary
            except Exception as exc:
                if self.logger:
                    self.logger.warning(
                        "Failed to summarize file-agent output",
                        file_id=file_id,
                        error=str(exc),
                    )

        return FileSummaryOutputDTO(
            file_id=file_id,
            content_type=content_type,
            summary=summary,
            raw_output=raw_output,
            rag=input_data.get("rag") or {},
        ).to_dict()

    def _save_file_summary_to_task_knowledge(
        self, conv_id: str, step: QueueStep
    ) -> None:
        output_data = step.output_data or {}
        summary = str(output_data.get("summary") or "").strip()
        if not summary:
            return

        global_context = self.agent_shared_state_repository.get_context_by_conv_id(
            conv_id
        )
        if global_context is None:
            return

        task_history = None
        for message_history in global_context.conversation_context or []:
            for candidate in message_history.tasks or []:
                if candidate.task_id == step.id:
                    task_history = candidate
                    break
            if task_history is not None:
                break

        if task_history is None:
            return

        rag_info = output_data.get("rag") or {}
        file_id = str(output_data.get("file_id") or "")
        document_id = str(rag_info.get("document_id") or file_id)
        knowledge_summary = f"Uploaded file {file_id} summary: {summary}"

        task_history.knowledge = task_history.knowledge or []
        if any(
            item.document_id == document_id and item.summary == knowledge_summary
            for item in task_history.knowledge
        ):
            return

        task_history.knowledge.append(
            KnowledgeItem(document_id=document_id, summary=knowledge_summary)
        )
        self.agent_shared_state_repository.update_context(
            conv_id=conv_id,
            context=global_context,
        )

    async def _update_output_data_current_task(self, event: QueueEvent):
        conv_id: str = event.conv_id
        if event.payload is None or not event.payload.steps:
            return

        first_step = event.payload.steps[0]
        task_id = first_step.id
        if not task_id:
            metadata = self._get_metadata(conv_id)
            task_id = metadata.current_task_id

        if not task_id:
            return

        self.agent_shared_state_repository.update_task(
            conv_id, task_id, output_data=first_step.output_data or {}
        )

    async def _mark_parent_task_success_if_needed(self, conv_id: str, task: Task) -> None:
        parent_task_id = task.parent_task_id
        if not parent_task_id:
            return

        parent_task = self.agent_shared_state_repository.get_task(conv_id, parent_task_id)
        if parent_task is None:
            raise ValueError(f"Parent task not found: {parent_task_id}")

        updated_parent = self.agent_shared_state_repository.update_task(
            conv_id, parent_task_id, status=TaskStatus.SUCCESS
        )
        await self.push_gateway_notifier.send_task_status_event(
            conv_id=conv_id, task=updated_parent
        )

    async def _send_summarization_to_ui(self, conv_id: str) -> None:
        metadata = self._get_metadata(conv_id)
        message_id = metadata.current_message_id
        if not message_id:
            raise ValueError(f"No current_message_id found for conversation {conv_id}")

        plan = self.agent_shared_state_repository.get_executions_by_message_id(
            conv_id, message_id
        )

        if self.logger:
            self.logger.info(
                "Plan snapshot before summarize",
                plan=plan,
            )

        summarized_context = await self.reviewer.summarize(plan)
        await self.push_gateway_notifier.send_task_summary_event(
            conv_id=conv_id, summary=summarized_context
        )

    def _get_agent_id_from_task(self, current_task: Task) -> str:
        if current_task.execute_by is None:
            raise ValueError("execute_by is None")
        agent_id: str = current_task.execute_by.split(".")[-1]
        return agent_id

    def _get_metadata(self, conv_id: str) -> Metadata:
        metadata: Metadata | None = self.agent_shared_state_repository.get_metadata(
            conv_id
        )

        if not metadata:
            raise ValueError(f"No metadata found for conversation {conv_id}")
        return metadata

