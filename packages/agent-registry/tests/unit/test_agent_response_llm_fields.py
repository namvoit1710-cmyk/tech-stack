"""SA-1938: provider / model / temperature must be returned by the agent APIs.

The write path already accepted these three fields on create and update, but the
shared response contract (AgentResponseDTO -> AgentResponse) dropped them, so
POST /register, PUT /{agent_id} and GET /{agent_id} all answered without them.
Without the read-back the Agent Hub FE cannot pre-fill the provider on the edit
form (SA-2089).
"""

from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.layer1_domain.entities.agent import Agent
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer3_presentation.apis.v1.agents import (
    get_agent_by_id,
    register_agent,
    update_agent,
)
from app.layer3_presentation.schemas.agent_schema import (
    AgentResponse,
    RegisterAgentRequest,
    UpdateAgentRequest,
)

LLM_FIELDS = ("provider", "model", "temperature")


@pytest.fixture
def agent_dto(sample_agent_data) -> AgentResponseDTO:
    """A response DTO built from the shared agent fixture (provider=openai, gpt-4, 0.7)."""
    return AgentResponseDTO.from_entity(Agent.create(**sample_agent_data))


class TestAgentResponseDTO:
    """The application-layer output contract carries the three LLM fields."""

    def test_from_entity_maps_llm_fields(self, agent_dto, sample_agent_data):
        assert agent_dto.provider == sample_agent_data["provider"]
        assert agent_dto.model == sample_agent_data["model"]
        assert agent_dto.temperature == sample_agent_data["temperature"]

    def test_provider_is_a_plain_string(self, agent_dto):
        """provider is declared str and carried through verbatim."""
        assert isinstance(agent_dto.provider, str)
        assert agent_dto.provider == "openai"

    def test_a_provider_outside_the_old_enum_survives_the_round_trip(
        self, sample_agent_data
    ):
        """SA-1938: 'aiml' used to 422; the registry now just stores what it is given."""
        agent = Agent.create(**{**sample_agent_data, "provider": "aiml", "model": "aiml/dall-e-2"})

        dto = AgentResponseDTO.from_entity(agent)

        assert dto.provider == "aiml"
        assert dto.model == "aiml/dall-e-2"


class TestAgentResponseSchema:
    """The presentation-layer response exposes and serializes the three fields."""

    def test_from_dto_carries_llm_fields(self, agent_dto):
        response = AgentResponse.from_dto(agent_dto)

        assert response.provider == agent_dto.provider
        assert response.model == agent_dto.model
        assert response.temperature == agent_dto.temperature

    def test_llm_fields_are_present_in_the_serialized_payload(self, agent_dto):
        payload = AgentResponse.from_dto(agent_dto).model_dump()

        for field in LLM_FIELDS:
            assert field in payload, f"{field} missing from the response payload"
        assert payload["provider"] == "openai"
        assert payload["model"] == "gpt-4"
        assert payload["temperature"] == 0.7


class TestOmittedTemperatureStaysUnset:
    """SA-1938: an omitted temperature must not come back as an invented default."""

    def test_entity_keeps_temperature_none_when_not_supplied(self, sample_agent_data):
        agent = Agent.create(**{**sample_agent_data, "temperature": None})

        assert agent.temperature is None

    def test_response_returns_null_temperature(self, sample_agent_data):
        """The bedrock_converse case: the payload carries no temperature at all."""
        agent = Agent.create(
            **{
                **sample_agent_data,
                "provider": "bedrock_converse",
                "model": "writer.palmyra-x4-v1:0",
                "temperature": None,
            }
        )

        payload = AgentResponse.from_dto(AgentResponseDTO.from_entity(agent)).model_dump()

        assert payload["provider"] == "bedrock_converse"
        assert payload["model"] == "writer.palmyra-x4-v1:0"
        assert payload["temperature"] is None

    def test_all_three_stay_null_when_none_is_supplied(self, sample_agent_data):
        """SA-1938: omitted means omitted for provider and model too, not just temperature."""
        agent = Agent.create(
            **{**sample_agent_data, "provider": None, "model": None, "temperature": None}
        )

        payload = AgentResponse.from_dto(AgentResponseDTO.from_entity(agent)).model_dump()

        assert payload["provider"] is None
        assert payload["model"] is None
        assert payload["temperature"] is None

    def test_zero_is_kept_and_not_confused_with_unset(self, sample_agent_data):
        """0.0 is a real, meaningful temperature (deterministic) - not 'missing'."""
        agent = Agent.create(**{**sample_agent_data, "temperature": 0.0})

        assert agent.temperature == 0.0


class TestProviderIsNoLongerAnAllowlist:
    """SA-1938: the FE picks the provider; the registry validates shape, not membership."""

    @pytest.mark.parametrize("provider", ["aiml", "anthropic", "azure_openai", "vercel_ai_gateway"])
    def test_register_request_accepts_any_provider(self, provider):
        request = RegisterAgentRequest(
            name="test-agent",
            description="A test agent",
            kind="business",
            status="active",
            config_type="default",
            business="You are a helpful assistant",
            provider=provider,
        )

        assert request.provider == provider

    def test_update_request_accepts_any_provider(self):
        assert UpdateAgentRequest(provider="aiml").provider == "aiml"

    def test_provider_is_trimmed_and_lowercased(self):
        assert UpdateAgentRequest(provider="  AIML  ").provider == "aiml"

    def test_blank_provider_is_still_rejected(self):
        with pytest.raises(ValidationError):
            UpdateAgentRequest(provider="   ")


class TestAgentApiHandlersReturnLlmFields:
    """All three routes share AgentResponse, so all three must answer with the fields."""

    @pytest.mark.asyncio
    async def test_register_agent_returns_llm_fields(self, agent_dto):
        use_case = Mock()
        use_case.execute = AsyncMock(return_value=agent_dto)
        http_request = Mock()
        http_request.state.jwt_token = "token"
        request = RegisterAgentRequest(
            name="test-agent",
            description="A test agent",
            kind="business",
            status="active",
            config_type="default",
            business="You are a helpful assistant",
            provider="openai",
            model="gpt-4",
            temperature=0.7,
        )

        result = await register_agent(
            http_request=http_request, request=request, use_case=use_case
        )

        assert result.provider == "openai"
        assert result.model == "gpt-4"
        assert result.temperature == 0.7

    @pytest.mark.asyncio
    async def test_update_agent_returns_llm_fields(self, agent_dto):
        use_case = Mock()
        use_case.execute = AsyncMock(return_value=agent_dto)
        request = UpdateAgentRequest(provider="openai", model="gpt-4", temperature=0.7)

        result = await update_agent(
            agent_id=agent_dto.id, request=request, use_case=use_case
        )

        assert result.provider == "openai"
        assert result.model == "gpt-4"
        assert result.temperature == 0.7

    @pytest.mark.asyncio
    async def test_get_agent_by_id_returns_llm_fields(self, agent_dto):
        use_case = Mock()
        use_case.execute.return_value = agent_dto

        result = await get_agent_by_id(
            agent_id=agent_dto.id, visible_ids=None, use_case=use_case
        )

        assert result.provider == "openai"
        assert result.model == "gpt-4"
        assert result.temperature == 0.7
