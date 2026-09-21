# from datetime import UTC, datetime
# from types import SimpleNamespace
# from typing import Any, cast
# from unittest.mock import AsyncMock, Mock

# import pytest

# from agent_sdk.layer1_domain.entities.agent_shared_state import Metadata, Task, TaskStatus
# from app.layer1_domain.entities.agent_hub import AgentHub, ToolHub, WorkflowHub, AgentHubDTO, ToolHubDTO, WorkflowHubDTO
# from app.layer1_domain.entities.planner import PlanStep, PlannerResponse, PlannerResponseType
# from app.layer1_domain.entities.planning_context import (
#     PlanningContextType,
#     SimpleNodePlanningContext,
# )
# from app.layer1_domain.entities.queue_event import QueueRequestType
# # from app.layer1_domain.entities.topic import Topic
# from app.layer1_domain.entities.ui_event import (
#     UIActionEvent,
#     UIEventType,
#     UIPayloadType,
#     UIWFInfo,
# )
# from app.layer1_domain.entities.workflow_event import (
#     PendingKind,
#     WorkflowEvent,
#     WorkflowInfo,
#     WorkflowStatus,
# )
# from app.layer1_domain.entities.workflow_entity import Workflow, WorkNodeStatus
# from app.layer2_application.features.main_agent.dto.event import EventDTO, PayloadDTO
# from app.layer2_application.features.main_agent.use_cases.create_plan_usecase import (
#     CreatePlanUseCase,
# )
# from agent_sdk import settings

# class RecordingLogger:
#     def __init__(self) -> None:
#         self.infos: list[str] = []
#         self.errors: list[str] = []

#     def info(self, message: str, **kwargs) -> None:
#         self.infos.append(message)

#     def error(self, message: str, **kwargs) -> None:
#         self.errors.append(message)

#     def warning(self, message: str, **kwargs) -> None:
#         pass

#     def debug(self, message: str, **kwargs) -> None:
#         pass

# from datetime import UTC, datetime

# from app.layer1_domain.entities.workflow_entity import (
#     WorkflowRunInfoData,
#     WorkflowRunInfoResponse,
# )

# def _workflow_run_info(
#     run_id: str = "run-1",
#     workflow_id: str = "workflow-1",
#     status: str = "started",
# ) -> WorkflowRunInfoResponse:
#     return WorkflowRunInfoResponse(
#         status="success",
#         data=WorkflowRunInfoData(
#             id=run_id,
#             workflow_id=workflow_id,
#             status=status,
#             started_at=datetime.now(UTC),
#             execution_mode="auto",
#         ),
#     )



# def _agent_hub(
#     agent_id: str = "agent-1",
#     *,
#     description: str = "Agent description",
#     tools: list[str] | None = None,
#     workflows: list[str] | None = None,
#     agents: list[str] | None = None,
# ) -> AgentHub:
#     timestamp = datetime(2024, 1, 1, tzinfo=UTC)
#     return AgentHub(
#         id=agent_id,
#         name=f"Agent {agent_id}",
#         kind="main",
#         status="ready",
#         is_published=True,
#         description=description,
#         healthcheck_endpoint="/health",
#         invoke_endpoint="/invoke",
#         is_alive=True,
#         last_health_check_at=timestamp,
#         version="1.0.0",
#         config_type="local",
#         capabilities=[],
#         agents=agents or [],
#         workflows=workflows or [],
#         tools=tools or [],
#         metadata={},
#         business="business",
#         created_at=timestamp,
#         updated_at=timestamp,
#     )


# def _tool_hub(tool_id: str = "tool-1") -> ToolHubDTO:
#     return ToolHubDTO(
#         id=tool_id,
#         name=f"Tool {tool_id}",
#         description="Tool description",
#         input_data={"field": "value"},
#         output_data={"result": "value"},
#     )


# def _workflow_hub(workflow_id: str = "workflow-1") -> WorkflowHubDTO:
#     return WorkflowHubDTO(
#         id=workflow_id,
#         name=f"Workflow {workflow_id}",
#         description="Workflow description",
#     )


# def _workflow(workflow_id: str = "workflow-1") -> Workflow:
#     return Workflow(
#         id=workflow_id,
#         name=f"Workflow {workflow_id}",
#         description="Workflow description",
#     )


# def _work_node_status(node_type: str = "FILE_UPLOAD") -> WorkNodeStatus:
#     return WorkNodeStatus(
#         run_id="run-1",
#         node_id="node-1",
#         node_name="Upload",
#         node_type=node_type,
#         status="waiting",
#         input_schema=[],
#         edit_mode="manual",
#         data_mode="single",
#         scalars={},
#         arrays=[],
#         main_file_id=None,
#         main_links={},
#         error=None,
#         error_code="",
#         error_details={},
#     )


# def _task(**updates) -> Task:
#     payload: dict[str, Any] = {
#         "task_id": "task-1",
#         "agent": "agent.agent-1",
#         "name": "Need help",
#         "status": TaskStatus.PENDING,
#         "next_task_id": "",
#         "depend_on": [],
#         "is_dynamic": False,
#     }
#     payload.update(updates)
#     return Task(**payload)


# def _metadata(**updates) -> Metadata:
#     payload: dict[str, Any] = {
#         "conversation_id": "conv-1",
#         "checkpoint_id": "checkpoint-1",
#         "current_task_id": "task-1",
#     }
#     payload.update(updates)
#     return Metadata(**payload)


# def _event_dto(
#     event_type: QueueRequestType = QueueRequestType.CONVERSATION_PLAN_CREATED,
#     payload: PayloadDTO | None = None,
#     conv_id: str = "conv-1",
#     metadata: dict = {"source": "test"},
# ) -> EventDTO:
#     return EventDTO(
#         id="event-1",
#         type=event_type,
#         conv_id=conv_id,
#         correlation_id="corr-1",
#         payload=payload,
#         metadata=metadata,
#     )


# def _workflow_payload(
#     *,
#     workflow_id: str = "workflow-1",
#     pending_kind: PendingKind = PendingKind.INPUT,
#     instruction: str = "Need input",
#     run_id: str = "run-1",
#     root_run_id: str = "run-1",
#     node_id: str = "node-1",
#     task_id: str = "task-1",
#     confirm_label: str | None = None,
#     reject_label: str | None = None,
# ) -> PayloadDTO:
#     return PayloadDTO(
#         from_="workflow-control-plane",
#         to="business-agent",
#         workflow=WorkflowEvent(
#             event_id="workflow-event-1",
#             timestamp="2024-01-01T00:00:00Z",
#             source="workflow-control-plane",
#             root_run_id=root_run_id,
#             depth=0,
#             task_id=task_id,
#             run_id=run_id,
#             workflow_id=workflow_id,
#             node_id=node_id,
#             instruction=instruction,
#             pending_kind=pending_kind,
#             status=WorkflowStatus.STARTED,
#             event_type=QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED.value,
#             workflow=WorkflowInfo(
#                 name="Workflow 1",
#                 description="Workflow description",
#             ),
#             confirm_label=confirm_label,
#             reject_label=reject_label,
#         ),
#     )


# def _workflow_run_status_payload(
#     *,
#     run_id: str = "run-1",
#     root_run_id: str = "run-1",
#     status: WorkflowStatus = WorkflowStatus.STARTED,
#     workflow_id: str = "workflow-1",
#     event_type: str = QueueRequestType.WORKFLOW_RUN_STATUS.value,
#     node_id: str = "",
# ) -> PayloadDTO:
#     return PayloadDTO(
#         from_="workflow-control-plane",
#         to="business-agent",
#         workflow=WorkflowEvent(
#             event_id="workflow-event-1",
#             timestamp="2024-01-01T00:00:00Z",
#             source="workflow-control-plane",
#             root_run_id=root_run_id,
#             depth=0,
#             run_id=run_id,
#             workflow_id=workflow_id,
#             node_id=node_id,
#             status=status,
#             event_type=event_type,
#             workflow=WorkflowInfo(
#                 name="Workflow 1",
#                 description="Workflow description",
#             ),
#         ),
#     )


# def _plan_step(
#     order: int,
#     *,
#     tool: str = "tool.tool-1",
#     input_data: dict | None = None,
#     output_data: dict | None = None,
#     reason: str | None = "Execute step",
# ) -> PlanStep:
#     return PlanStep(
#         order=order,
#         tool=tool,
#         input_data=input_data or {},
#         output_data=output_data or {},
#         reason=reason,
#     )


# def _direct_answer_plan(answer: str = "Resolved") -> PlannerResponse:
#     return PlannerResponse(
#         type=PlannerResponseType.DIRECT_ANSWER,
#         answer=answer,
#     )


# def _execution_plan(*steps: PlanStep) -> PlannerResponse:
#     return PlannerResponse(
#         type=PlannerResponseType.EXECUTION_PLAN,
#         goal="Execute the request",
#         steps=list(steps),
#     )


# def _usecase(
#     *,
#     planner=None,
#     planner_resolver=None,
#     agent_hub_client=None,
#     agent_shared_state_repository=None,
#     workflow_client=None,
#     push_gateway_notifier=None,
#     logger=None,
#     publisher=None,
#     uuid_builder=None,
# ):
#     planner = planner or SimpleNamespace(generate=AsyncMock())
#     planner_resolver = planner_resolver or SimpleNamespace(resolve=Mock(return_value=planner))
#     agent_hub_client = agent_hub_client or SimpleNamespace(
#         get=AsyncMock(
#             return_value=_agent_hub(
#                 tools=["tool-1"],
#                 workflows=["workflow-1"],
#                 agents=["agent-2"],
#             )
#         ),
#         get_tools_by_ids=AsyncMock(return_value=[_tool_hub()]),
#         get_tools_by_id=AsyncMock(return_value=_tool_hub()),
#         get_workflows_by_ids=AsyncMock(return_value=[_workflow_hub()]),
#         get_agents_by_ids=AsyncMock(return_value=[_agent_hub("agent-2")]),
#     )
#     agent_shared_state_repository = agent_shared_state_repository or SimpleNamespace(
#         upsert_executions=Mock(),
#         update_task=Mock(return_value=_task(status=TaskStatus.SUCCESS)),
#         update_metadata=Mock(),
#         get_task=Mock(return_value=_task()),
#         get_metadata=Mock(return_value=_metadata()),
#         get_conversation_id_by_run_id=Mock(return_value="conv-1"),
#         save_conversation=Mock(),
#     )
#     workflow_client = workflow_client or SimpleNamespace(
#         get_by_id=AsyncMock(return_value=_workflow()),
#         get_node_info=AsyncMock(return_value=_work_node_status()),
#         get_workflow_run=AsyncMock(return_value=_workflow_run_info()),
#     )
#     push_gateway_notifier = push_gateway_notifier or SimpleNamespace(
#         send_thinking_event=AsyncMock(),
#         send_task_summary_event=AsyncMock(),
#         send_task_status_event=AsyncMock(),
#         send_process_response_with_text_event=AsyncMock(),
#         send_tool_form_event=AsyncMock(),
#     )
#     logger = logger or RecordingLogger()
#     publisher = publisher or SimpleNamespace(publish=AsyncMock())
#     uuid_builder = uuid_builder or SimpleNamespace(
#         create_uuid7=Mock(side_effect=[f"uuid-{index}" for index in range(1, 50)])
#     )

#     return CreatePlanUseCase(
#         planner_resolver=cast(Any, planner_resolver),
#         agent_hub_client=cast(Any, agent_hub_client),
#         agent_shared_state_repository=cast(Any, agent_shared_state_repository),
#         workflow_client=cast(Any, workflow_client),
#         push_gateway_notifier=cast(Any, push_gateway_notifier),
#         logger=cast(Any, logger),
#         publisher=cast(Any, publisher),
#         uuid_builder=cast(Any, uuid_builder),
#         settings=settings,
#     ), {
#         "planner": planner,
#         "planner_resolver": planner_resolver,
#         "agent_hub_client": agent_hub_client,
#         "agent_shared_state_repository": agent_shared_state_repository,
#         "workflow_client": workflow_client,
#         "push_gateway_notifier": push_gateway_notifier,
#         "logger": logger,
#         "publisher": publisher,
#         "uuid_builder": uuid_builder,
#         "settings": settings,
#     }


# @pytest.mark.asyncio
# @pytest.mark.parametrize(
#     ("event_type", "handler_name"),
#     [
#         (QueueRequestType.SUB_USER_CHAT, "_execute_sub_user_chat"),
#         (QueueRequestType.USER_ACTION, "_execute_user_action"),
#         (QueueRequestType.CONVERSATION_PLAN_CREATED, "_execute_conversation_plan_created"),
#         (QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED, "_execute_workflow_request"),
#         (QueueRequestType.MAIN_USER_RERUN, "_execute_main_user_rerun"),
#         (QueueRequestType.WORKFLOW_NODE_COMPLETED, "_execute_workflow_node_status"),
#         (QueueRequestType.WORKFLOW_RUN_STATUS, "_execute_workflow_run_status"),
#     ],
# )
# async def test_execute_routes_known_plan_cases(event_type, handler_name):
#     subject, _ = _usecase()
#     handler = AsyncMock(return_value=handler_name)
#     setattr(subject, handler_name, handler)

#     result = await subject.execute(_event_dto(event_type=event_type))

#     assert result == handler_name
#     handler.assert_awaited_once()


# @pytest.mark.asyncio
# async def test_execute_sends_fallback_summary_when_handler_fails():
#     subject, deps = _usecase()
#     subject._execute_conversation_plan_created = AsyncMock(
#         side_effect=RuntimeError("boom")
#     )

#     await subject.execute(_event_dto())

#     assert deps["logger"].errors == ["Error executing CreatePlanUseCase"]

#     deps["push_gateway_notifier"].send_task_summary_event.assert_awaited_once_with(
#         conv_id="conv-1",
#         summary=(
#             "There was an error processing your request. "
#             "Please try again later. Error details: boom"
#         ),
#     )

# # @pytest.mark.asyncio
# # async def test_execute_sub_user_chat_returns_none():
# #     subject, _ = _usecase()

# #     assert await subject._execute_sub_user_chat(_event_dto()) is None


# @pytest.mark.asyncio
# async def test_execute_conversation_plan_created_loads_state_and_creates_plan():
#     subject, _ = _usecase()
#     metadata = _metadata()
#     current_task = _task()
#     subject._get_metadata = Mock(return_value=metadata)
#     subject._get_current_task = AsyncMock(return_value=current_task)
#     subject._create_plan = AsyncMock(return_value="plan")

#     await subject._execute_conversation_plan_created(_event_dto())

#     subject._get_metadata.assert_called_once_with("conv-1")
#     subject._get_current_task.assert_awaited_once_with("conv-1", metadata)
#     subject._create_plan.assert_awaited_once_with("conv-1", metadata, current_task)


# @pytest.mark.asyncio
# async def test_create_plan_builds_llm_context_and_handles_direct_answer():
#     planner = SimpleNamespace(generate=AsyncMock(return_value=_direct_answer_plan("Final answer")))
#     subject, deps = _usecase(planner=planner)
#     subject._handle_direct_answer = AsyncMock()
#     subject._handle_execution_plan = AsyncMock()
#     metadata = _metadata()
#     current_task = _task(name="Plan this request")

#     plan = await subject._create_plan("conv-1", metadata, current_task)

#     assert plan.answer == "Final answer"
#     deps["agent_hub_client"].get.assert_awaited_once_with("agent-1")
#     deps["planner_resolver"].resolve.assert_called_once_with(PlanningContextType.LLM)
#     planning_context = planner.generate.await_args.args[0]
#     assert planning_context.conversation_context.message == "Plan this request"
#     # assert planning_context.business_intent == "Agent description"
#     assert planning_context.tools[0].id == "tool-1"
#     assert planning_context.workflows_hub[0].id == "workflow-1"
#     assert planning_context.agents[0].id == "agent-2"
#     assert deps["push_gateway_notifier"].send_thinking_event.await_count == 2
#     subject._handle_direct_answer.assert_awaited_once_with(
#         "conv-1", metadata, current_task, plan
#     )
#     subject._handle_execution_plan.assert_not_awaited()


# @pytest.mark.asyncio
# async def test_create_plan_routes_execution_plan_to_execution_handler():
#     planner = SimpleNamespace(
#         generate=AsyncMock(return_value=_execution_plan(_plan_step(1, tool="tool.tool-1")))
#     )
#     subject, deps = _usecase(planner=planner)
#     subject._handle_direct_answer = AsyncMock()
#     subject._handle_execution_plan = AsyncMock()
#     current_task = _task(name="Run an action")

#     plan = await subject._create_plan("conv-1", _metadata(), current_task)

#     deps["planner_resolver"].resolve.assert_called_once_with(PlanningContextType.LLM)
#     subject._handle_execution_plan.assert_awaited_once_with("conv-1", current_task, plan)
#     subject._handle_direct_answer.assert_not_awaited()


# @pytest.mark.asyncio
# async def test_handle_direct_answer_marks_terminal_task_complete():
#     repository = SimpleNamespace(
#         upsert_executions=Mock(),
#         update_task=Mock(return_value=_task(status=TaskStatus.SUCCESS)),
#         update_metadata=Mock(),
#     )
#     notifier = SimpleNamespace(
#         send_task_summary_event=AsyncMock(),
#     )
#     subject, _ = _usecase(
#         agent_shared_state_repository=repository,
#         push_gateway_notifier=SimpleNamespace(
#             send_thinking_event=AsyncMock(),
#             send_task_summary_event=notifier.send_task_summary_event,
#             send_task_status_event=AsyncMock(),
#             send_process_response_with_text_event=AsyncMock(),
#             send_tool_form_event=AsyncMock(),
#         ),
#     )
#     metadata = _metadata(current_task_id="task-1")
#     current_task = _task(task_id="task-1", next_task_id="")

#     await subject._handle_direct_answer(
#         "conv-1",
#         metadata,
#         current_task,
#         _direct_answer_plan("All done"),
#     )

#     saved_tasks = repository.upsert_executions.call_args.kwargs["executions"]
#     assert len(saved_tasks) == 1
#     assert saved_tasks[0].status == TaskStatus.SUCCESS
#     assert saved_tasks[0].parent_task_id == "task-1"
#     notifier.send_task_summary_event.assert_awaited_once_with(
#         conv_id="conv-1", summary="All done"
#     )
#     repository.update_task.assert_called_once_with(
#         "conv-1", "task-1", status=TaskStatus.SUCCESS
#     )
#     assert metadata.current_task_id == ""
#     repository.update_metadata.assert_called_once_with(conv_id="conv-1", metadata=metadata)


# @pytest.mark.asyncio
# async def test_handle_direct_answer_advances_to_next_task_when_present():
#     repository = SimpleNamespace(upsert_executions=Mock())
#     subject, _ = _usecase(agent_shared_state_repository=repository)
#     subject._handle_next_task = AsyncMock()
#     metadata = _metadata(current_task_id="task-1")
#     current_task = _task(task_id="task-1", next_task_id="task-2")

#     await subject._handle_direct_answer(
#         "conv-1",
#         metadata,
#         current_task,
#         _direct_answer_plan("Continue"),
#     )

#     subject._handle_next_task.assert_awaited_once_with("conv-1", metadata, current_task)


# @pytest.mark.asyncio
# async def test_handle_direct_answer_requires_answer_for_summary_event():
#     subject, _ = _usecase()

#     with pytest.raises(ValueError, match="plan.answer is required"):
#         await subject._handle_direct_answer(
#             "conv-1",
#             _metadata(),
#             _task(next_task_id=""),
#             PlannerResponse(type=PlannerResponseType.DIRECT_ANSWER),
#         )


# @pytest.mark.asyncio
# async def test_handle_direct_answer_requires_updated_task_when_terminal_task_missing():
#     repository = SimpleNamespace(
#         upsert_executions=Mock(),
#         update_task=Mock(return_value=None),
#         update_metadata=Mock(),
#     )
#     subject, _ = _usecase(agent_shared_state_repository=repository)

#     with pytest.raises(ValueError, match="Task not found"):
#         await subject._handle_direct_answer(
#             "conv-1",
#             _metadata(current_task_id="task-1"),
#             _task(task_id="task-1", next_task_id=""),
#             _direct_answer_plan("Done"),
#         )


# # @pytest.mark.asyncio
# # async def test_handle_execution_plan_persists_steps_and_publishes_first_batch():
# #     repository = SimpleNamespace(
# #         upsert_executions=Mock(),
# #         get_metadata=Mock(return_value=_metadata(current_task_id="parent-task")),
# #         update_metadata=Mock(),
# #     )
# #     publisher = SimpleNamespace(publish=AsyncMock())
# #     notifier = SimpleNamespace(
# #         send_thinking_event=AsyncMock(),
# #         send_task_summary_event=AsyncMock(),
# #         send_task_status_event=AsyncMock(),
# #         send_process_response_with_text_event=AsyncMock(),
# #         send_tool_form_event=AsyncMock(),
# #     )
# #     subject, _ = _usecase(
# #         agent_shared_state_repository=repository,
# #         publisher=publisher,
# #         push_gateway_notifier=notifier,
# #     )
# #     plan = _execution_plan(
# #         _plan_step(1, tool="tool.tool-1", input_data={"x": 1}, output_data={"y": 2}, reason="First step"),
# #         _plan_step(2, tool="agent.agent-2", reason="Second step"),
# #     )

# #     await subject._handle_execution_plan("conv-1", _task(task_id="parent-task"), plan)

# #     saved_tasks = repository.upsert_executions.call_args.kwargs["executions"]
# #     assert len(saved_tasks) == 2
# #     assert saved_tasks[0].execute_by == "tool.tool-1"
# #     assert saved_tasks[0].next_task_id == saved_tasks[1].id
# #     assert saved_tasks[0].parent_task_id == "parent-task"
# #     assert notifier.send_task_status_event.await_count == 2
# #     publish_kwargs = publisher.publish.await_args.kwargs
# #     assert publish_kwargs["topic"] == settings.EXECUTOR_TOPIC
# #     assert publish_kwargs["message"]["conv_id"] == "conv-1"
# #     assert publish_kwargs["message"]["payload"]["steps"][0]["execute_by"] == "tool.tool-1"


# @pytest.mark.asyncio
# async def test_handle_execution_plan_requires_first_executor():
#     repository = SimpleNamespace(
#         upsert_executions=Mock(),
#         get_metadata=Mock(return_value=_metadata()),
#         update_metadata=Mock(),
#     )
#     subject, _ = _usecase(agent_shared_state_repository=repository)
#     plan = cast(
#         Any,
#         SimpleNamespace(
#             steps=[
#                 SimpleNamespace(
#                     tool=None,
#                     input_data={},
#                     output_data={},
#                     reason="Step without executor",
#                 )
#             ]
#         ),
#     )

#     with pytest.raises(ValueError, match="execute_by is None"):
#         await subject._handle_execution_plan("conv-1", _task(), plan)


# # @pytest.mark.asyncio
# # async def test_handle_next_task_updates_metadata_and_publishes_business_event():
# #     repository = SimpleNamespace(update_metadata=Mock())
# #     publisher = SimpleNamespace(publish=AsyncMock())
# #     subject, _ = _usecase(agent_shared_state_repository=repository, publisher=publisher)
# #     metadata = _metadata(current_task_id="task-1")
# #     current_task = _task(task_id="task-1", next_task_id="task-2")

# #     await subject._handle_next_task("conv-1", metadata, current_task)

# #     assert metadata.current_task_id == "task-2"
# #     repository.update_metadata.assert_called_once_with(conv_id="conv-1", metadata=metadata)
# #     publish_kwargs = publisher.publish.await_args.kwargs
# #     assert publish_kwargs["topic"] == settings.BUSINESS_TOPIC
# #     assert publish_kwargs["message"]["type"] == QueueRequestType.CONVERSATION_PLAN_CREATED
# #     assert publish_kwargs["key"]


# @pytest.mark.asyncio
# async def test_get_current_task_marks_task_processing_and_sends_thinking_event():
#     repository = SimpleNamespace(
#         get_task=Mock(return_value=_task(task_id="task-1")),
#         update_task=Mock(),
#     )
#     notifier = SimpleNamespace(
#         send_thinking_event=AsyncMock(),
#         send_task_summary_event=AsyncMock(),
#         send_task_status_event=AsyncMock(),
#         send_process_response_with_text_event=AsyncMock(),
#         send_tool_form_event=AsyncMock(),
#     )
#     subject, _ = _usecase(
#         agent_shared_state_repository=repository,
#         push_gateway_notifier=notifier,
#     )

#     task = await subject._get_current_task("conv-1", _metadata(current_task_id="task-1"))

#     assert task.id == "task-1"
#     repository.update_task.assert_called_once_with(
#         "conv-1", "task-1", status=TaskStatus.PROCESSING
#     )
#     notifier.send_thinking_event.assert_awaited_once_with(
#         conv_id="conv-1", thinking_message="Evaluating the best way to assist you..."
#     )


# @pytest.mark.asyncio
# async def test_get_current_task_requires_current_task_id():
#     subject, _ = _usecase()

#     with pytest.raises(ValueError, match="No current task found for conversation conv-1"):
#         await subject._get_current_task("conv-1", _metadata(current_task_id=""))


# @pytest.mark.asyncio
# async def test_get_current_task_requires_existing_task():
#     repository = SimpleNamespace(get_task=Mock(return_value=None), update_task=Mock())
#     subject, _ = _usecase(agent_shared_state_repository=repository)

#     with pytest.raises(ValueError, match="Task not found: task-1"):
#         await subject._get_current_task("conv-1", _metadata(current_task_id="task-1"))


# # @pytest.mark.asyncio
# # async def test_execute_workflow_request_routes_input_requests():
# #     subject, deps = _usecase()
# #     subject._send_workflow_form_tool_payload = AsyncMock(return_value="input")

# #     result = await subject._execute_workflow_request(
# #         _event_dto(
# #             event_type=QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED,
# #             payload=_workflow_payload(pending_kind=PendingKind.INPUT),
# #             conv_id="",
# #         )
# #     )

#     # assert result is None
#     # deps["planner_resolver"].resolve.assert_called_once_with(
#     #     PlanningContextType.LLM_WITH_SIMPLE_NODE
#     # )
#     # subject._send_workflow_form_tool_payload.assert_awaited_once()
#     # await_args = subject._send_workflow_form_tool_payload.await_args
#     # assert await_args is not None
#     # workflow, conv_id, planner = await_args.args
#     # assert workflow.pending_kind == PendingKind.INPUT
#     # assert conv_id == "conv-1"
#     # assert planner is deps["planner"]


# @pytest.mark.asyncio
# async def test_execute_workflow_request_routes_data_edit_requests():
#     subject, deps = _usecase()
#     subject._send_workflow_open_workspace_payload = AsyncMock(return_value="date")

#     result = await subject._execute_workflow_request(
#         _event_dto(
#             event_type=QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED,
#             payload=_workflow_payload(pending_kind=PendingKind.DATA_EDIT),
#             conv_id="",
#         )
#     )

#     assert result is None
#     deps["planner_resolver"].resolve.assert_called_once_with(
#         PlanningContextType.LLM_WITH_SIMPLE_NODE
#     )
#     subject._send_workflow_open_workspace_payload.assert_awaited_once()
#     await_args = subject._send_workflow_open_workspace_payload.await_args
#     assert await_args is not None
#     workflow, conv_id, planner = await_args.args
#     assert workflow.pending_kind == PendingKind.DATA_EDIT
#     assert conv_id == "conv-1"
#     assert planner is deps["planner"]


# @pytest.mark.asyncio
# async def test_execute_workflow_request_routes_human_action_requests():
#     subject, deps = _usecase()
#     subject._send_workflow_button_group_payload = AsyncMock(return_value="human")

#     result = await subject._execute_workflow_request(
#         _event_dto(
#             event_type=QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED,
#             payload=_workflow_payload(
#                 pending_kind=PendingKind.HUMAN_ACTION,
#                 confirm_label="Approve",
#                 reject_label="Reject",
#             ),
#             conv_id="",
#         )
#     )

#     assert result is None
#     deps["planner_resolver"].resolve.assert_called_once_with(
#         PlanningContextType.LLM_WITH_SIMPLE_NODE
#     )
#     subject._send_workflow_button_group_payload.assert_awaited_once()
#     await_args = subject._send_workflow_button_group_payload.await_args
#     assert await_args is not None
#     workflow, conv_id, planner = await_args.args
#     assert workflow.pending_kind == PendingKind.HUMAN_ACTION
#     assert conv_id == "conv-1"
#     assert planner is deps["planner"]


# @pytest.mark.asyncio
# async def test_execute_workflow_request_requires_workflow_payload():
#     subject, _ = _usecase()

#     with pytest.raises(
#         ValueError,
#         match="Workflow data is required for workflow run status event",
#     ):
#         await subject._execute_workflow_request(
#             _event_dto(
#                 event_type=QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED,
#                 payload=None,
#                 conv_id="",
#             )
#         )


# # @pytest.mark.asyncio
# # async def test_send_workflow_form_tool_payload_sends_direct_answer_and_tool_form():
# #     planner = SimpleNamespace(generate=AsyncMock(return_value=_direct_answer_plan("Please fill the fields")))
# #     subject, deps = _usecase()
# #     workflow = _workflow_payload(
# #         pending_kind=PendingKind.FILE_UPLOAD,
# #         instruction="Provide the missing fields",
# #     ).workflow

# #     assert workflow is not None

# #     result = await subject._send_workflow_form_tool_payload(workflow, "conv-1", planner)

# #     assert result is None
# #     planning_context = planner.generate.await_args.args[0]
# #     assert isinstance(planning_context, SimpleNodePlanningContext)
# #     assert "Provide the missing fields" in planning_context.node_message
# #     assert planning_context.workflows_hub[0].id == "workflow-1"
# #     deps["agent_shared_state_repository"].save_conversation.assert_called_once()
# #     saved_conversation = deps["agent_shared_state_repository"].save_conversation.call_args.args[0]
# #     assert saved_conversation.conv_id == "uuid-1"
# #     assert saved_conversation.shared_state.metadata.main_conv_id == "conv-1"
# #     deps["push_gateway_notifier"].send_tool_form_event.assert_awaited_once()
# #     notification = deps["push_gateway_notifier"].send_tool_form_event.await_args.kwargs
# #     assert notification["conv_id"] == "conv-1"
# #     assert notification["ui_payload"].type == UIPayloadType.FORM_TOOL
# #     assert notification["ui_payload"].content == "Please fill the fields"
# #     assert notification["ui_payload"].parent_id == "task-1"
# #     assert notification["ui_payload"].wf_info.model_dump() == {
# #         "node_id": "node-1",
# #         "task_id": "task-1",
# #         "run_id": "run-1",
# #     }


# # @pytest.mark.asyncio
# # async def test_send_workflow_open_workspace_payload_builds_open_workspace_payload():
# #     planner = SimpleNamespace(generate=AsyncMock(return_value=_direct_answer_plan("Please update the data")))
# #     subject, deps = _usecase()
# #     workflow = _workflow_payload(
# #         pending_kind=PendingKind.DATA_EDIT,
# #         instruction="Update the provided values",
# #     ).workflow

# #     assert workflow is not None

# #     result = await subject._send_workflow_open_workspace_payload(workflow, "conv-1", planner)

# #     assert result is None
# #     planning_context = planner.generate.await_args.args[0]
# #     assert isinstance(planning_context, SimpleNodePlanningContext)
# #     assert planning_context.workflows_hub[0].id == "workflow-1"
# #     deps["push_gateway_notifier"].send_tool_form_event.assert_awaited_once()
# #     notification = deps["push_gateway_notifier"].send_tool_form_event.await_args.kwargs
# #     assert notification["ui_payload"].type == UIPayloadType.OPEN_WORKSPACE
# #     assert notification["ui_payload"].content == "Please update the data"


# # @pytest.mark.asyncio
# # async def test_send_workflow_button_group_payload_builds_button_group_payload():
# #     planner = SimpleNamespace(generate=AsyncMock(return_value=_direct_answer_plan("Please choose an action")))
# #     subject, deps = _usecase()
# #     workflow = _workflow_payload(
# #         pending_kind=PendingKind.HUMAN_ACTION,
# #         instruction="Choose how to proceed",
# #         confirm_label="Approve",
# #         reject_label="Reject",
# #     ).workflow

# #     assert workflow is not None

# #     result = await subject._send_workflow_button_group_payload(workflow, "conv-1", planner)

# #     assert result is None
# #     planning_context = planner.generate.await_args.args[0]
# #     assert isinstance(planning_context, SimpleNodePlanningContext)
# #     assert planning_context.workflows_hub[0].id == "workflow-1"
# #     deps["push_gateway_notifier"].send_tool_form_event.assert_awaited_once()
# #     notification = deps["push_gateway_notifier"].send_tool_form_event.await_args.kwargs
# #     assert notification["ui_payload"].type == UIPayloadType.BUTTON_GROUP
# #     assert notification["ui_payload"].content == "Please choose an action"
# #     assert notification["ui_payload"].text == ["Approve", "Reject"]


# @pytest.mark.asyncio
# async def test_execute_user_action_returns_none_and_logs():
#     subject, deps = _usecase()

#     result = await subject._execute_user_action(
#         _event_dto(event_type=QueueRequestType.USER_ACTION)
#     )

#     assert result is None
#     assert deps["logger"].infos[-1] == "Generating user action plan"


# @pytest.mark.asyncio
# async def test_execute_main_user_rerun_returns_none_and_logs():
#     subject, deps = _usecase()

#     result = await subject._execute_main_user_rerun(_event_dto())

#     assert result is None
#     assert deps["logger"].infos[-1] == "Generating main user rerun plan"


# @pytest.mark.asyncio
# async def test_execute_workflow_node_status_returns_none_and_logs():
#     planner = SimpleNamespace(generate=AsyncMock(return_value=_direct_answer_plan("Node update")))
#     planner_resolver = SimpleNamespace(resolve=Mock(return_value=planner))
#     subject, deps = _usecase(planner_resolver=planner_resolver)

#     result = await subject._execute_workflow_node_status(
#         _event_dto(
#             event_type=QueueRequestType.WORKFLOW_NODE_COMPLETED,
#             payload=_workflow_run_status_payload(
#                 event_type=QueueRequestType.WORKFLOW_NODE_COMPLETED.value,
#                 node_id="node-1",
#             ),
#             conv_id="",
#         )
#     )

#     assert result is None
#     assert deps["logger"].infos[-1] == "Generating workflow node run status plan"
#     planning_context = planner.generate.await_args.args[0]
#     assert planning_context.workflows_hub[0].id == "workflow-1"
#     deps["push_gateway_notifier"].send_thinking_event.assert_awaited_once_with(
#         conv_id="conv-1",
#         thinking_message="Node update",
#     )


# # @pytest.mark.asyncio
# # async def test_execute_workflow_run_status_sends_direct_answer_for_started_root_run():
# #     planner = SimpleNamespace(
# #         generate=AsyncMock(return_value=_direct_answer_plan("Your workflow has started."))
# #     )
# #     planner_resolver = SimpleNamespace(resolve=Mock(return_value=planner))
# #     repository = SimpleNamespace(
# #         upsert_executions=Mock(),
# #         update_task=Mock(return_value=_task(status=TaskStatus.SUCCESS)),
# #         update_metadata=Mock(),
# #         get_task=Mock(return_value=_task()),
# #         get_metadata=Mock(return_value=_metadata(conversation_id="conv-root")),
# #         get_conversation_id_by_run_id=Mock(return_value="conv-root"),
# #     )
# #     notifier = SimpleNamespace(
# #         send_thinking_event=AsyncMock(),
# #         send_task_summary_event=AsyncMock(),
# #         send_task_status_event=AsyncMock(),
# #         send_process_response_with_text_event=AsyncMock(),
# #         send_tool_form_event=AsyncMock(),
# #     )
# #     subject, _ = _usecase(
# #         planner_resolver=planner_resolver,
# #         agent_shared_state_repository=repository,
# #         push_gateway_notifier=notifier,
# #     )
# #     event = _event_dto(
# #         event_type=QueueRequestType.WORKFLOW_RUN_STATUS,
# #         payload=_workflow_run_status_payload(),
# #         conv_id="",
# #     )

# #     result = await subject._execute_workflow_run_status(event)

# #     assert result is None
# #     repository.get_conversation_id_by_run_id.assert_called_once_with("run-1")
# #     repository.get_metadata.assert_called_once_with("conv-root")
# #     planner_resolver.resolve.assert_called_once_with(PlanningContextType.LLM_WITH_SIMPLE_NODE)
# #     planning_context = planner.generate.await_args.args[0]
# #     assert planning_context.node_message == f"The workflow has been {WorkflowStatus.STARTED}."
# #     assert planning_context.workflows_hub[0].id == "workflow-1"
# #     notifier.send_process_response_with_text_event.assert_awaited_once()
# #     notification = notifier.send_process_response_with_text_event.await_args.kwargs
# #     assert notification["conv_id"] == "conv-root"
# #     assert notification["ui_payload"].type == UIPayloadType.TEXT
# #     assert notification["ui_payload"].content == "Your workflow has started."
# #     assert notification["ui_payload"].parent_id == "task-1"


# @pytest.mark.asyncio
# async def test_execute_workflow_run_status_ignores_child_runs():
#     planner_resolver = SimpleNamespace(resolve=Mock())
#     repository = SimpleNamespace(
#         upsert_executions=Mock(),
#         update_task=Mock(return_value=_task(status=TaskStatus.SUCCESS)),
#         update_metadata=Mock(),
#         get_task=Mock(return_value=_task()),
#         get_metadata=Mock(return_value=_metadata(conversation_id="conv-root")),
#         get_conversation_id_by_run_id=Mock(return_value="conv-root"),
#     )
#     subject, deps = _usecase(
#         planner_resolver=planner_resolver,
#         agent_shared_state_repository=repository,
#     )

#     result = await subject._execute_workflow_run_status(
#         _event_dto(
#             event_type=QueueRequestType.WORKFLOW_RUN_STATUS,
#             payload=_workflow_run_status_payload(
#                 run_id="child-run-1",
#                 root_run_id="root-run-1",
#             ),
#             conv_id="",
#         )
#     )

#     assert result is None
#     repository.get_conversation_id_by_run_id.assert_called_once_with("root-run-1")
#     repository.get_metadata.assert_called_once_with("conv-root")
#     planner_resolver.resolve.assert_not_called()
#     deps["push_gateway_notifier"].send_process_response_with_text_event.assert_not_awaited()
