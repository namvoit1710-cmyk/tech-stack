from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.agent.scored_agent import ScoredAgent

from app.layer2_application.features.orchestrate.use_cases.get_available_agents_use_case import (
    GetAvailableAgentsUseCase,
)
from app.layer2_application.features.orchestrate.use_cases.get_dispatch_result_use_case import (
    GetDispatchResultInput,
    GetDispatchResultUseCase,
)


async def test_get_available_agents_use_case_filters_published_agents():
    agent_registry_client = MagicMock()
    agent_registry_client.filter_agents = AsyncMock(
        return_value=[
            ScoredAgent(
                id="agent-1",
                name="Business Agent",
                kind="workflow",
                status="active",
                is_published=True,
                description="Published business agent",
                healthcheck_endpoint="",
                invoke_endpoint="",
                is_alive=True,
                last_heartbeat=None,
                version="1.0",
                config_type="workflow",
                capabilities=[],
                agents=[],
                workflows=[],
                tools=[],
                metadata={},
                business="mdg",
                created_at=None,
                updated_at=None,
            )
        ]
    )
    use_case = GetAvailableAgentsUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        agent_registry_client=agent_registry_client,
    )

    result = await use_case.execute()

    agent_registry_client.filter_agents.assert_awaited_once_with(is_published=True)
    assert result.agents == [
        {
            "id": "agent-1",
            "name": "Business Agent",
            "kind": "workflow",
            "description": "Published business agent",
            "status": "active",
            "is_published": True,
        }
    ]


def test_get_dispatch_result_use_case_returns_not_found_error():
    orchestration_state_repo = MagicMock()
    orchestration_state_repo.get_conversation_by.return_value = None
    use_case = GetDispatchResultUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        orchestration_state_repo=orchestration_state_repo,
    )

    result = use_case.execute(GetDispatchResultInput(conv_id="missing-conv"))

    assert result.conv_id == "missing-conv"
    assert result.error == "Shared state not found"
    assert result.shared_state is None
    assert result.current_task is None