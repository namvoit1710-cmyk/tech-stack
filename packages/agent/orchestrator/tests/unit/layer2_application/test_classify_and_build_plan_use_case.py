from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    Conversation,
    GlobalContext,
    Metadata,
)
from app.layer1_domain.entities.agent.scored_agent import ScoredAgent
from app.layer1_domain.entities.planner.task_breakdown_response import (
    ParsedSharedStateTaskModel,
    StrategicWorkflowPlan,
    TaskBreakdownResponse,
)
from app.layer2_application.features.orchestrate_graph.use_cases import orchestrate_graph_use_case as module


# def _build_scored_agent(*, agent_id="agent-1", name="Business Agent", workflow_id="wf-1", workflow_name="Default Workflow"):
#     return ScoredAgent(
#         id=agent_id,
#         name=name,
#         kind="workflow",
#         status="active",    
#         is_published=True,
#         description="",
#         healthcheck_endpoint="",
#         invoke_endpoint="",
#         is_alive=True,
#         last_heartbeat=None,
#         version="1.0",
#         config_type="workflow",
#         capabilities=[],
#         agents=[],
#         workflows=[{"id": workflow_id, "name": workflow_name}] if workflow_id else [],
#         tools=[],
#         metadata={},
#         business="mdg",
#         created_at=None,
#         updated_at=None,
#     )


# def _build_use_case(agent_registry_client, workflow_client, llm_provider=None, orchestration_state_repo=None):
#     if orchestration_state_repo is None:
#         orchestration_state_repo = MagicMock()

#     uuid_builder = MagicMock()
#     uuid_builder.create_uuid7.side_effect = ["generated-task-id", "generated-correlation-id"]

#     return module.OrchestrateGraphUseCase(
#         logger=MagicMock(),
#         monitor=MagicMock(),
#         permission_provider=MagicMock(),
#         input_guard=MagicMock(),
#         push_gateway_notifier=MagicMock(),
#         agent_registry_client=agent_registry_client,
#         workflow_client=workflow_client,
#         llm_provider=llm_provider or MagicMock(),
#         orchestration_state_repo=orchestration_state_repo,
#         agent_event_repo=MagicMock(),
#         publisher=MagicMock(publish=AsyncMock()),
#         response_formatter=MagicMock(),
#         uuid_builder=uuid_builder,
#     )


def _build_strategy(*, logical_steps=None, dependency_rules="Do tasks in order"):
    return StrategicWorkflowPlan(
        context_summary="Break the request down into executable tasks.",
        logical_steps=logical_steps or ["Review request", "Create execution tasks"],
        dependency_rules=dependency_rules,
    )


def _build_task_breakdown(*, reasoning="Use the selected agent to execute the task.", tasks=None):
    return TaskBreakdownResponse(
        reasoning=reasoning,
        tasks=tasks or [],
    )


# @pytest.mark.asyncio
# async def test_classify_and_build_plan_creates_strategy_and_plan_and_syncs_tasks():
#     agent_registry_client = MagicMock()
#     agent_registry_client.filter_agents = AsyncMock(return_value=[_build_scored_agent()])
#     workflow_client = MagicMock()
#     orchestration_state_repo = MagicMock()
#     orchestration_state_repo.get_conversation_by = MagicMock(return_value=Conversation(
#         conv_id="conv-1",
#         user_id="user-1",
#         shared_state=AgentSharedState(
#             metadata=Metadata(conversation_id="conv-1", checkpoint_id="CP-01"),
#             context=GlobalContext(user_id="user-1", user_intent=""),
#         ),
#     ))

#     llm_provider = MagicMock()
#     llm_provider.complete_with_format.side_effect = [
#         _build_strategy(),
#         _build_task_breakdown(
#             tasks=[
#                 ParsedSharedStateTaskModel(
#                     id="task-1",
#                     goal="Approve the invoice",
#                     execute_by="agent-1",
#                     workflow_id="wf-1",
#                     agent_id="agent-1",
#                 )
#             ]
#         ),
#     ]

#     use_case = _build_use_case(
#         agent_registry_client,
#         workflow_client,
#         llm_provider=llm_provider,
#         orchestration_state_repo=orchestration_state_repo,
#     )

#     result = await use_case._classify_and_build_plan(
#         conv_id="conv-1",
#         user_id="user-1",
#         files=["invoice.pdf"],
#         message="approve invoice",
#     )

#     agent_registry_client.filter_agents.assert_awaited_once_with(is_published=True)
#     assert llm_provider.complete_with_format.call_count == 2

#     first_call = llm_provider.complete_with_format.call_args_list[0]
#     assert first_call.kwargs["response_model"] is StrategicWorkflowPlan
#     strategy_prompt = first_call.kwargs["messages"]
#     assert strategy_prompt[1]["role"] == "user"
#     assert "approve invoice" in strategy_prompt[1]["content"]
#     assert "invoice.pdf" in strategy_prompt[1]["content"]

#     second_call = llm_provider.complete_with_format.call_args_list[1]
#     assert second_call.kwargs["response_model"] is TaskBreakdownResponse
#     breakdown_prompt = second_call.kwargs["messages"]
#     assert any("invoice.pdf" in message["content"] for message in breakdown_prompt if message["role"] == "user")

#     orchestration_state_repo.save_conversation.assert_called_once()


# @pytest.mark.asyncio
# async def test_classify_and_build_plan_raises_when_no_eligible_agents_exist():
#     agent_registry_client = MagicMock()
#     agent_registry_client.filter_agents = AsyncMock(return_value=[])
#     workflow_client = MagicMock()

#     llm_provider = MagicMock()
#     llm_provider.complete_with_format.return_value = _build_strategy()

#     use_case = _build_use_case(agent_registry_client, workflow_client, llm_provider=llm_provider)

#     with pytest.raises(Exception, match="No eligible agents found"):
#         await use_case._classify_and_build_plan(
#             conv_id="conv-2",
#             user_id="user-2",
#             files=[],
#             message="check the uploaded file and then create the change request",
#         )

#     assert llm_provider.complete_with_format.call_count == 1


# @pytest.mark.asyncio
# async def test_classify_and_build_plan_uses_default_plan_when_planning_fails():
#     agent_registry_client = MagicMock()
#     agent_registry_client.filter_agents = AsyncMock(return_value=[_build_scored_agent()])
#     workflow_client = MagicMock()
#     orchestration_state_repo = MagicMock()
#     orchestration_state_repo.get_conversation_by = MagicMock(return_value=Conversation(
#         conv_id="conv-3",
#         user_id="user-3",
#         shared_state=AgentSharedState(
#             metadata=Metadata(conversation_id="conv-3", checkpoint_id="CP-01"),
#             context=GlobalContext(user_id="user-3", user_intent="approve invoice"),
#         ),
#     ))
#     llm_provider = MagicMock()
#     llm_provider.complete_with_format.side_effect = [
#         _build_strategy(),
#         RuntimeError("planner unavailable"),
#     ]

#     use_case = _build_use_case(
#         agent_registry_client,
#         workflow_client,
#         llm_provider=llm_provider,
#         orchestration_state_repo=orchestration_state_repo,
#     )

#     result = await use_case._classify_and_build_plan(
#         conv_id="conv-3",
#         user_id="user-3",
#         files=[],
#         message="approve invoice",
#     )

#     assert result.tasks == []
#     assert result.reasoning == "Default planning: Planning failed: planner unavailable"
#     orchestration_state_repo.save_conversation.assert_called_once()