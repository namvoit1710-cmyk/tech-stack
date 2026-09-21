import asyncio

from app.layer1_domain.entities.business_agent_draft import BusinessAgentDraft
from app.layer2_application.features.main_agent.use_cases.draft_business_agent_usecase import (
    DraftOutputDto,
)
from app.layer2_application.graphs.nodes import draft_node


class _StubUseCase:
    def __init__(self, draft):
        self._draft = draft
        self.received_prompt = None

    async def execute(self, input_dto):
        self.received_prompt = input_dto.prompt
        return DraftOutputDto(draft=self._draft, source="heuristic")


def _install(monkeypatch, draft):
    stub = _StubUseCase(draft)
    monkeypatch.setattr(draft_node, "get_usecase", lambda _name: stub)
    return stub


def test_node_reads_prompt_and_returns_register_payload(monkeypatch):
    draft = BusinessAgentDraft(
        name="Support Bot", description="Answers questions.", business="You help."
    )
    stub = _install(monkeypatch, draft)

    result = asyncio.run(draft_node.draft_business_agent_node({"prompt": "help agent"}))

    assert stub.received_prompt == "help agent"
    assert result["draft"] is draft
    # Payload is surfaced via formatted_response (the SDK's output channel).
    fr = result["formatted_response"]
    assert fr["status"] == "success"
    assert fr["data"]["payload"]["name"] == "Support Bot"
    assert fr["agent_data"]["payload"]["name"] == "Support Bot"
    assert fr["data"]["source"] == "heuristic"


def test_node_falls_back_to_parameters_prompt(monkeypatch):
    draft = BusinessAgentDraft(
        name="Support Bot", description="Answers questions.", business="You help."
    )
    stub = _install(monkeypatch, draft)

    asyncio.run(
        draft_node.draft_business_agent_node({"parameters": {"prompt": "from params"}})
    )
    assert stub.received_prompt == "from params"


def test_node_falls_back_to_message(monkeypatch):
    draft = BusinessAgentDraft(
        name="Support Bot", description="Answers questions.", business="You help."
    )
    stub = _install(monkeypatch, draft)

    asyncio.run(
        draft_node.draft_business_agent_node({"message": "from message"})
    )
    assert stub.received_prompt == "from message"
