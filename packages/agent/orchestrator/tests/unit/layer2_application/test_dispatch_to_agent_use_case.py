from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from app.layer2_application.interfaces.conversation_summary_repository import ConversationTitleSource
from app.layer1_domain.entities.orchestrator.ui_event import UIEvent, UIEventType
from app.layer1_domain.entities.planner.task_breakdown_response import ParsedSharedStateTaskModel, StrategicWorkflowPlan
from app.layer2_application.features.orchestrate_graph.use_cases import orchestrate_graph_use_case as module


# def _build_use_case(orchestration_state_repo=None, agent_event_repo=None):
#     uuid_builder = MagicMock()
#     uuid_builder.create_uuid7.return_value = "generated-event-id"

#     return module.OrchestrateGraphUseCase(
#         logger=MagicMock(),
#         monitor=MagicMock(),
#         permission_provider=MagicMock(),
#         input_guard=MagicMock(),
#         push_gateway_notifier=MagicMock(emit_conversation_notification=AsyncMock()),
#         agent_registry_client=MagicMock(),
#         workflow_client=MagicMock(),
#         llm_provider=MagicMock(),
#         publisher=MagicMock(publish=AsyncMock()),
#         orchestration_state_repo=orchestration_state_repo or MagicMock(),
#         agent_event_repo=agent_event_repo or MagicMock(),
#         response_formatter=MagicMock(),
#         uuid_builder=uuid_builder,
#     )


# @pytest.mark.asyncio
# async def test_dispatch_publishes_plan_created_event_and_returns_state(monkeypatch):
#     use_case = _build_use_case()
#     publisher = cast(AsyncMock, use_case.publisher.publish)
#     save_event = cast(MagicMock, use_case.agent_event_repo.save_event)
#     executions = [
#         ParsedSharedStateTaskModel(
#             id="task-1",
#             goal="Create change request",
#             execute_by="agent-1",
#             status=TaskStatus.PENDING,
#         )
#     ]

#     result = await use_case._dispatch_to_agent("conv-1", executions)

#     publisher.assert_awaited_once()
#     await_args = publisher.await_args
#     assert await_args is not None
#     assert await_args.args[0] == module.AGENT_REQUEST_TOPIC
#     message = await_args.kwargs["message"]
#     assert message["type"] == "conversation.plan.created"
#     assert message["conv_id"] == "conv-1"
#     assert message["payload"] == {
#         "from_": "orchestrator",
#         "to": "agent",
#         "steps": [],
#         "node_info": None,
#         "workflow_id": None,
#     }
#     assert isinstance(message["id"], str)
#     assert message["id"]
#     save_event.assert_called_once()
#     saved_event = save_event.call_args.args[0]
#     assert isinstance(saved_event, UIEvent)
#     assert saved_event.event_type == UIEventType.MAIN_USER_CHAT
#     assert saved_event.conv_id == "conv-1"
#     assert saved_event.correlation_id == message["id"]
#     assert result is True


# @pytest.mark.asyncio
# async def test_dispatch_ignores_selected_agent_details_when_emitting_plan_event(monkeypatch):
#     use_case = _build_use_case()
#     publisher = cast(AsyncMock, use_case.publisher.publish)

#     await use_case._dispatch_to_agent(
#         "conv-2",
#         [
#             ParsedSharedStateTaskModel(
#                 id="task-1",
#                 goal="Create change request",
#                 execute_by="agent-1",
#                 status=TaskStatus.PENDING,
#             )
#         ],
#     )

#     await_args = publisher.await_args
#     assert await_args is not None
#     published_message = await_args.kwargs["message"]
#     assert published_message["conv_id"] == "conv-2"
#     assert published_message["type"] == "conversation.plan.created"
#     assert published_message["payload"]["steps"] == []
#     assert "agent_type" not in published_message
#     assert "agent_name" not in published_message


# @pytest.mark.asyncio
# async def test_dispatch_saves_event_before_publish(monkeypatch):
#     agent_event_repo = MagicMock()
#     use_case = _build_use_case(agent_event_repo=agent_event_repo)
#     save_event_mock = cast(MagicMock, use_case.agent_event_repo.save_event)
#     publish_mock = cast(AsyncMock, use_case.publisher.publish)
#     call_order = []

#     def save_event(event):
#         call_order.append(("save", event))

#     async def publish(*args, **kwargs):
#         call_order.append(("publish", kwargs["message"]))

#     save_event_mock.side_effect = save_event
#     publish_mock.side_effect = publish

#     await use_case._dispatch_to_agent(
#         "conv-ordered",
#         [
#             ParsedSharedStateTaskModel(
#                 id="task-1",
#                 goal="Create change request",
#                 execute_by="agent-1",
#                 status=TaskStatus.PENDING,
#             )
#         ],
#     )

#     assert [step[0] for step in call_order] == ["save", "publish"]
#     assert isinstance(call_order[0][1], UIEvent)
#     assert call_order[0][1].correlation_id == call_order[1][1]["id"]


# @pytest.mark.asyncio
# async def test_dispatch_does_not_schedule_agent_requests_for_ready_tasks(monkeypatch):
#     use_case = _build_use_case(orchestration_state_repo=MagicMock())
#     publisher = cast(AsyncMock, use_case.publisher.publish)

#     result = await use_case._dispatch_to_agent(
#         "conv-3",
#         [
#             ParsedSharedStateTaskModel(
#                 id="task-2",
#                 goal="Create change request",
#                 execute_by="agent-2",
#                 status=TaskStatus.PENDING,
#                 depend_on=["task-1"],
#             )
#         ],
#     )

#     assert result is False
#     publisher.assert_not_awaited()


def test_orchestrate_graph_use_case_saves_strategy_title():
    conversation_summary_repo = MagicMock()
    use_case = module.OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=MagicMock(),
        push_gateway_notifier=MagicMock(),
        agent_registry_client=MagicMock(),
        workflow_client=MagicMock(),
        llm_provider=MagicMock(),
        orchestration_state_repo=MagicMock(),
        agent_event_repo=MagicMock(),
        publisher=MagicMock(),
        response_formatter=MagicMock(),
        uuid_builder=MagicMock(),
        settings=MagicMock(),
        conversation_summary_repo=conversation_summary_repo,
    )

    use_case._save_conversation_title(
        "conv-1",
        "Summarize invoice approval workflow",
        ConversationTitleSource.STRATEGY,
    )

    saved_title = conversation_summary_repo.save_title.call_args.args[0]
    assert saved_title.conv_id == "conv-1"
    assert saved_title.title == "Summarize invoice approval workflow"
    assert saved_title.source == ConversationTitleSource.STRATEGY


def test_orchestrate_graph_use_case_resolves_title_from_context_summary_first():
    use_case = module.OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=MagicMock(),
        push_gateway_notifier=MagicMock(),
        agent_registry_client=MagicMock(),
        workflow_client=MagicMock(),
        llm_provider=MagicMock(),
        orchestration_state_repo=MagicMock(),
        agent_event_repo=MagicMock(),
        publisher=MagicMock(),
        response_formatter=MagicMock(),
        uuid_builder=MagicMock(),
        settings=MagicMock(),
        conversation_summary_repo=MagicMock(),
    )

    title, source = use_case._resolve_conversation_title(
        StrategicWorkflowPlan(
            context_summary="Create workflow plan",
            logical_steps=["Fallback step"],
            dependency_rules="Do in order",
        ),
        "raw message",
    )

    assert title == "Create workflow plan"
    assert source == ConversationTitleSource.STRATEGY


def test_orchestrate_graph_use_case_resolves_title_from_first_logical_step_when_summary_blank():
    use_case = module.OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=MagicMock(),
        push_gateway_notifier=MagicMock(),
        agent_registry_client=MagicMock(),
        workflow_client=MagicMock(),
        llm_provider=MagicMock(),
        orchestration_state_repo=MagicMock(),
        agent_event_repo=MagicMock(),
        publisher=MagicMock(),
        response_formatter=MagicMock(),
        uuid_builder=MagicMock(),
        settings=MagicMock(),
        conversation_summary_repo=MagicMock(),
    )

    title, source = use_case._resolve_conversation_title(
        StrategicWorkflowPlan(
            context_summary="",
            logical_steps=["Plan button group workflow"],
            dependency_rules="Do in order",
        ),
        "raw message",
    )

    assert title == "Plan button group workflow"
    assert source == ConversationTitleSource.STRATEGY


def test_orchestrate_graph_use_case_resolves_title_from_message_when_strategy_empty():
    use_case = module.OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=MagicMock(),
        push_gateway_notifier=MagicMock(),
        agent_registry_client=MagicMock(),
        workflow_client=MagicMock(),
        llm_provider=MagicMock(),
        orchestration_state_repo=MagicMock(),
        agent_event_repo=MagicMock(),
        publisher=MagicMock(),
        response_formatter=MagicMock(),
        uuid_builder=MagicMock(),
        settings=MagicMock(),
        conversation_summary_repo=MagicMock(),
    )

    title, source = use_case._resolve_conversation_title(
        StrategicWorkflowPlan(
            context_summary="",
            logical_steps=[],
            dependency_rules="",
        ),
        "run workflow zennie button group",
    )

    assert title == "run workflow zennie button group"
    assert source == ConversationTitleSource.USER_MESSAGE
