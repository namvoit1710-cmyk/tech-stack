"""SA-2114 — route an /async request with type=AGENT directly to the business agent
named by parameters.agent_id (no LLM classification, no scoring, no fallback)."""

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
from app.layer1_domain.entities.exceptions import RequestValidationAppError
from app.layer1_domain.entities.orchestrator.task_type import TaskType
from app.layer2_application.features.orchestrate.use_cases.orchestrate_use_case import (
    OrchestrateUseCase,
)
from app.layer2_application.features.orchestrate_graph.use_cases import (
    orchestrate_graph_use_case as module,
)
from app.layer2_application.features.orchestrate_graph.use_cases.dtos.orchestration_parameters import (
    OrchestrationParameters,
)

TARGET_AGENT_ID = "agent-approval"


def _scored_agent(*, agent_id, name):
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
        workflows=[],
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
async def test_direct_agent_request_dispatches_directly_to_named_agent():
    # Registry holds several agents; the request must land on exactly the named one.
    agents = [
        _scored_agent(agent_id="agent-other", name="Other"),
        _scored_agent(agent_id=TARGET_AGENT_ID, name="Approval Agent"),
    ]
    registry = MagicMock(filter_agents=AsyncMock(return_value=agents))
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation()))
    publisher = MagicMock(publish=AsyncMock())

    use_case = _build_use_case(
        agent_registry_client=registry, orchestration_state_repo=repo, publisher=publisher
    )
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="chat with the approval agent",
        parameters={"type": "AGENT", "agent_id": TARGET_AGENT_ID},
    )

    await use_case._dispatch_to_specific_agent(
        conv_id="conv-1", command=command, agent_id=TARGET_AGENT_ID
    )

    # Only ACTIVE + BUSINESS + published agents are considered; no scoring/LLM.
    registry.filter_agents.assert_awaited_once_with(
        status=AgentStatus.ACTIVE, kind=AgentKind.BUSINESS, is_published=True
    )
    # A single-task plan was persisted, targeting the named agent (no workflow).
    repo.upsert_executions.assert_called_once()
    persisted_task = repo.upsert_executions.call_args.kwargs["executions"][0]
    assert persisted_task.agent_id == TARGET_AGENT_ID
    assert persisted_task.workflow_id == ""
    # The task was dispatched to the agent request topic.
    publisher.publish.assert_awaited_once()
    assert publisher.publish.call_args.args[0] == "agent.request"


@pytest.mark.asyncio
async def test_direct_agent_request_unknown_agent_does_not_dispatch_and_has_no_fallback():
    # Named agent is absent from the registry -> error, never re-routed to another agent.
    agents = [_scored_agent(agent_id="agent-other", name="Other")]
    registry = MagicMock(filter_agents=AsyncMock(return_value=agents))
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation()))
    publisher = MagicMock(publish=AsyncMock())

    use_case = _build_use_case(
        agent_registry_client=registry, orchestration_state_repo=repo, publisher=publisher
    )
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="chat with a missing agent",
        parameters={"type": "AGENT", "agent_id": "does-not-exist"},
    )

    await use_case._dispatch_to_specific_agent(
        conv_id="conv-1", command=command, agent_id="does-not-exist"
    )

    # No plan persisted, nothing dispatched, and no fallback agent selected.
    repo.upsert_executions.assert_not_called()
    publisher.publish.assert_not_awaited()


@pytest.mark.parametrize(
    "type_value, parameters",
    [
        ("AGENT", {}),                    # missing agent_id
        ("AGENT", {"agent_id": ""}),      # empty agent_id
        ("AGENT", {"agent_id": "   "}),   # whitespace-only agent_id
        ("Bogus", {}),                    # unknown type
        ("SMDG", {}),                     # legacy enum value no longer accepted
    ],
)
def test_validate_routing_rejects_invalid_payloads(type_value, parameters):
    with pytest.raises(RequestValidationAppError):
        OrchestrateUseCase._validate_routing(type_value, parameters)


@pytest.mark.parametrize(
    "type_value, parameters",
    [
        (None, {}),                          # absent type -> default routing
        ("", {}),                            # empty type -> default routing
        ("AGENT", {"agent_id": "a-1"}),      # valid AGENT
    ],
)
def test_validate_routing_accepts_valid_payloads(type_value, parameters):
    # Should not raise.
    OrchestrateUseCase._validate_routing(type_value, parameters)


@pytest.mark.parametrize(
    "type_value, expected",
    [
        (None, None),   # absent -> default routing
        ("", None),     # empty -> default routing
        ("AGENT", TaskType.AGENT),
    ],
)
def test_task_type_from_value_resolves_known_values(type_value, expected):
    assert TaskType.from_value(type_value) is expected


@pytest.mark.parametrize("type_value", ["SMDG", "AgentHub", "Bogus"])
def test_task_type_from_value_rejects_unknown_values(type_value):
    with pytest.raises(ValueError):
        TaskType.from_value(type_value)


@pytest.mark.parametrize(
    "parameters, expected",
    [
        (OrchestrationParameters(type="AGENT", agent_id=TARGET_AGENT_ID), True),
        (OrchestrationParameters(type="AGENT", agent_id=""), False),
        (OrchestrationParameters(type=None, agent_id=TARGET_AGENT_ID), False),
        (OrchestrationParameters(type="", agent_id=TARGET_AGENT_ID), False),
        (OrchestrationParameters(type="Bogus", agent_id=TARGET_AGENT_ID), False),
    ],
)
def test_is_direct_agent_request_matrix(parameters, expected):
    assert module.OrchestrateGraphUseCase._is_direct_agent_request(parameters) is expected
