"""SA-1314 — route an /async request that names a specific workflow directly to the
first published, active Business Agent in Agent Hub that owns that workflow id."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    Conversation,
    GlobalContext,
    Metadata,
)
from app.layer1_domain.entities.agent.scored_agent import (
    AgentKind,
    AgentStatus,
    ScoredAgent,
)
from app.layer2_application.features.orchestrate_graph.use_cases.dtos.wf_info import WfInfo
from app.layer2_application.features.orchestrate_graph.use_cases.dtos.orchestration_parameters import (
    OrchestrationParameters,
)
from app.layer2_application.features.orchestrate_graph.use_cases import (
    orchestrate_graph_use_case as module,
)

TARGET_WF_ID = "edbd57af-4897-4a69-810e-ddc61be23c16"


def _scored_agent(*, agent_id, name, workflows):
    return ScoredAgent(
        id=agent_id,
        name=name,
        kind=AgentKind.BUSINESS,
        status=AgentStatus.ACTIVE,
        is_published=True,
        description="",
        healthcheck_endpoint="",
        invoke_endpoint="",
        is_alive=True,
        last_heartbeat=None,
        version="1.0",
        config_type="runtime",
        capabilities=[],
        agents=[],
        workflows=workflows,
        tools=[],
        metadata={},
        business="mdg",
        created_at=None,
        updated_at=None,
    )


def _conversation(conv_id="conv-1"):
    return Conversation(
        conv_id=conv_id,
        user_id="user-1",
        shared_state=AgentSharedState(
            metadata=Metadata(conv_id=conv_id, checkpoint_id="CP-01", current_message_id="msg-1"),
            context=GlobalContext(),
        ),
    )


def _build_use_case(*, agent_registry_client, orchestration_state_repo, publisher):
    uuid_builder = MagicMock()
    uuid_builder.create_uuid7.side_effect = [f"id-{i}" for i in range(20)]
    settings = MagicMock()
    settings.AGENT_REQUEST_TOPIC = "agent.request"
    return module.OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=MagicMock(),
        push_gateway_notifier=MagicMock(send_notification=AsyncMock()),
        agent_registry_client=agent_registry_client,
        workflow_client=MagicMock(),
        llm_provider=MagicMock(),
        orchestration_state_repo=orchestration_state_repo,
        agent_event_repo=MagicMock(),
        publisher=publisher,
        response_formatter=MagicMock(
            format_error_from_state=MagicMock(return_value=MagicMock(content="error"))
        ),
        uuid_builder=uuid_builder,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_dispatch_specific_workflow_selects_first_agent_owning_the_workflow():
    # First business agent does NOT own the workflow; the second one does.
    agents = [
        _scored_agent(agent_id="agent-other", name="Other", workflows=["wf-x"]),
        _scored_agent(agent_id="agent-approval", name="Approval Agent", workflows=[TARGET_WF_ID]),
    ]
    registry = MagicMock(filter_agents=AsyncMock(return_value=agents))
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation()))
    publisher = MagicMock(publish=AsyncMock())

    use_case = _build_use_case(
        agent_registry_client=registry, orchestration_state_repo=repo, publisher=publisher
    )
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="approve change request",
        parameters=OrchestrationParameters.from_dict(
            {"wf_info": {"id": TARGET_WF_ID, "name": "[NEWFLOW2] Approval Process single task"}}
        ),
    )

    await use_case._dispatch_specific_workflow(command)

    # Selected by workflow ownership (only ACTIVE+BUSINESS+published considered).
    registry.filter_agents.assert_awaited_once_with(
        status=AgentStatus.ACTIVE, kind=AgentKind.BUSINESS, is_published=True
    )
    # A single-task plan was persisted, targeting the owning agent + workflow.
    repo.upsert_executions.assert_called_once()
    persisted_task = repo.upsert_executions.call_args.kwargs["executions"][0]
    assert persisted_task.workflow_id == TARGET_WF_ID
    assert persisted_task.agent_id == "agent-approval"
    # input_data is empty by design (c02d71abb "remove send input to business"): the business
    # agent runs the flow off workflow_id, not an embedded message/wf_info payload.
    assert persisted_task.input_data == {}
    # The task was dispatched to the agent request topic carrying the workflow id.
    publisher.publish.assert_awaited_once()
    topic = publisher.publish.call_args.args[0]
    dispatched = publisher.publish.call_args.kwargs["message"]
    assert topic == "agent.request"
    assert dispatched["payload"]["workflow_id"] == TARGET_WF_ID


@pytest.mark.asyncio
async def test_dispatch_specific_workflow_no_owning_agent_does_not_dispatch():
    agents = [_scored_agent(agent_id="agent-other", name="Other", workflows=["wf-x"])]
    registry = MagicMock(filter_agents=AsyncMock(return_value=agents))
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation()))
    publisher = MagicMock(publish=AsyncMock())

    use_case = _build_use_case(
        agent_registry_client=registry, orchestration_state_repo=repo, publisher=publisher
    )
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="approve change request",
        parameters=OrchestrationParameters.from_dict({"wf_info": {"id": TARGET_WF_ID}}),
    )

    await use_case._dispatch_specific_workflow(command)

    # No owning agent -> no plan persisted, nothing dispatched to the agent topic.
    repo.upsert_executions.assert_not_called()
    publisher.publish.assert_not_awaited()


def test_wf_info_from_parameters_parses_valid_and_rejects_invalid():
    parsed = WfInfo.from_parameters(
        {"wf_info": {"id": TARGET_WF_ID, "name": "Approval", "description": "desc"}}
    )
    assert parsed == WfInfo(id=TARGET_WF_ID, name="Approval", description="desc")

    # Missing/malformed -> None (request does not name a runnable workflow).
    assert WfInfo.from_parameters({}) is None
    assert WfInfo.from_parameters({"wf_info": {"name": "no id"}}) is None
    assert WfInfo.from_parameters({"wf_info": {"id": ""}}) is None
    assert WfInfo.from_parameters({"wf_info": "not-a-dict"}) is None
    assert WfInfo.from_parameters(None) is None


def test_orchestration_parameters_from_dict_parses_raw_payload():
    # from_dict is the single boundary parser (called in main_node off the queue,
    # and at the orchestrate call site) — no __post_init__ coercion magic.
    params = OrchestrationParameters.from_dict(
        {
            "wf_info": {"id": TARGET_WF_ID, "name": "Flow"},
            "uploaded_file_ids": ["f1", "", None, "f2"],
        }
    )
    assert isinstance(params, OrchestrationParameters)
    assert params.wf_info == WfInfo(id=TARGET_WF_ID, name="Flow")
    assert params.uploaded_file_ids == ["f1", "f2"]

    # No wf_info -> None; empty/None input -> defaults.
    assert OrchestrationParameters.from_dict({}).wf_info is None
    assert OrchestrationParameters.from_dict(None).uploaded_file_ids == []
    # A default-constructed command carries an empty typed DTO, not a dict.
    assert isinstance(module.OrchestrationGraphCommand(conv_id="conv-1").parameters, OrchestrationParameters)
