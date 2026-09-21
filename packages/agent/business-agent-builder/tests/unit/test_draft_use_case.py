import asyncio

import pytest

from app.layer1_domain.entities.business_agent_draft import BusinessAgentDraft
from app.layer2_application.features.main_agent.use_cases.draft_business_agent_usecase import (
    DraftBusinessAgentUseCase,
    DraftInputDto,
)


class _FakeLogger:
    def __init__(self):
        self.messages: list[str] = []

    def info(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def warning(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def error(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def debug(self, message: str, **kwargs) -> None:
        self.messages.append(message)


class _FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def get_chat_completion(self, system_prompt, user_prompt, json_mode=True):
        self.calls.append((system_prompt, user_prompt))
        return self.response


def _run(coro):
    return asyncio.run(coro)


def test_empty_prompt_is_rejected():
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger())
    with pytest.raises(ValueError):
        _run(use_case.execute(DraftInputDto(prompt="   ")))


def test_heuristic_draft_when_no_llm():
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger())
    result = _run(
        use_case.execute(
            DraftInputDto(prompt="Create an agent to answer customer support email")
        )
    )
    assert result.source == "heuristic"
    assert isinstance(result.draft, BusinessAgentDraft)
    assert result.draft.name
    assert result.draft.business
    # keyword-derived capabilities
    assert "customer_support" in result.draft.capabilities
    assert "draft_email" in result.draft.capabilities


def test_llm_draft_parses_json_response():
    llm = _FakeLLM(
        '{"name": "Invoice Helper", "description": "Handles invoices.", '
        '"business": "You process invoices.", "capabilities": ["analyze_data"], '
        '"tools": ["ledger"], "workflows": []}'
    )
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger(), openai_service=llm)
    result = _run(use_case.execute(DraftInputDto(prompt="agent for invoices")))

    assert result.source == "llm"
    assert result.draft.name == "Invoice Helper"
    # Free-text tool names from the model are ignored — only validated catalog IDs are kept.
    assert result.draft.tools == []
    assert result.draft.capabilities == ["analyze_data"]
    assert llm.calls  # the LLM was actually consulted


def test_assigns_matching_tools_and_agents_from_catalog():
    tools = [
        {"id": "tool-email", "name": "Email Sender", "description": "Send emails to customers"},
        {"id": "tool-ledger", "name": "Ledger", "description": "Accounting ledger access"},
    ]
    agents = [
        {"id": "agent-crm", "name": "CRM Agent", "description": "Manages customer records"},
        {"id": "agent-weather", "name": "Weather Agent", "description": "Forecasts weather"},
    ]
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger())
    result = _run(
        use_case.execute(
            DraftInputDto(
                prompt="Answer customer support email",
                available_tools=tools,
                available_agents=agents,
            )
        )
    )
    # relevant tool/agent selected by token overlap; irrelevant ones excluded
    assert result.draft.tools == ["tool-email"]
    assert result.draft.agents == ["agent-crm"]
    # every assigned id exists in the supplied catalog (no hallucinated ids)
    assert all(t in {"tool-email", "tool-ledger"} for t in result.draft.tools)


def test_llm_selects_tools_and_agents_and_validates_ids():
    tools = [
        {"id": "t-email", "name": "Email", "description": "send email"},
        {"id": "t-ledger", "name": "Ledger", "description": "accounting"},
    ]
    agents = [{"id": "a-crm", "name": "CRM", "description": "customers"}]
    # LLM returns a valid pick plus a hallucinated id that must be dropped.
    llm = _FakeLLM('{"tool_ids": ["t-email", "ghost"], "agent_ids": ["a-crm"]}')
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger(), openai_service=llm)
    result = _run(
        use_case.execute(
            DraftInputDto(
                prompt="agent for customer email",
                available_tools=tools,
                available_agents=agents,
            )
        )
    )
    assert result.draft.tools == ["t-email"]  # "ghost" dropped (not in catalog)
    assert result.draft.agents == ["a-crm"]


def test_honors_llm_intent_all_tools_no_agents():
    # The LLM decides intent (any language). Here it returns every tool and no agents;
    # we assign exactly what it decided — no keyword override.
    tools = [{"id": "t1", "name": "Echo", "description": "echo"}, {"id": "t2", "name": "Ledger", "description": "acct"}]
    agents = [{"id": "a1", "name": "CRM", "description": "customer records and support"}]
    llm = _FakeLLM('{"tool_ids": ["t1","t2"], "agent_ids": []}')
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger(), openai_service=llm)
    result = _run(
        use_case.execute(
            DraftInputDto(
                # Vietnamese: "...integrate all existing tools, use no other agents."
                prompt="Trả lời email hỗ trợ khách hàng, tích hợp tất cả công cụ hiện có, không dùng agent nào khác",
                available_tools=tools,
                available_agents=agents,
            )
        )
    )
    assert result.draft.tools == ["t1", "t2"]  # LLM said all tools
    assert result.draft.agents == []           # LLM said no agents


def test_no_catalog_leaves_tools_and_agents_empty():
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger())
    result = _run(use_case.execute(DraftInputDto(prompt="answer customer email")))
    assert result.draft.tools == []
    assert result.draft.agents == []


def test_catalog_items_without_id_are_skipped():
    tools = [{"name": "Email Sender", "description": "send customer email"}]  # no id
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger())
    result = _run(
        use_case.execute(
            DraftInputDto(prompt="customer email", available_tools=tools)
        )
    )
    assert result.draft.tools == []


def test_llm_response_with_fences_and_prose_is_tolerated():
    llm = _FakeLLM(
        'Sure! Here is your config:\n```json\n'
        '{"name": "FAQ Bot", "description": "FAQ.", "business": "Answer FAQs."}\n```'
    )
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger(), openai_service=llm)
    result = _run(use_case.execute(DraftInputDto(prompt="faq agent")))
    assert result.source == "llm"
    assert result.draft.name == "FAQ Bot"


def test_llm_failure_falls_back_to_heuristic():
    llm = _FakeLLM("not json at all")
    logger = _FakeLogger()
    use_case = DraftBusinessAgentUseCase(logger=logger, openai_service=llm)
    result = _run(use_case.execute(DraftInputDto(prompt="research competitors")))

    assert result.source == "heuristic"
    assert "research" in result.draft.capabilities
    assert any("fall" in m.lower() or "fail" in m.lower() for m in logger.messages)


def test_llm_dict_response_is_accepted():
    llm = _FakeLLM(
        {"name": "Dict Agent", "description": "From a dict.", "business": "You help."}
    )
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger(), openai_service=llm)
    result = _run(use_case.execute(DraftInputDto(prompt="dict agent")))
    assert result.source == "llm"
    assert result.draft.name == "Dict Agent"


def test_llm_openai_style_object_response_is_accepted():
    class _Msg:
        content = '{"name": "Obj Agent", "description": "d.", "business": "b."}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger(), openai_service=_FakeLLM(_Resp()))
    result = _run(use_case.execute(DraftInputDto(prompt="obj agent")))
    assert result.source == "llm"
    assert result.draft.name == "Obj Agent"


def test_llm_partial_json_is_completed_from_heuristic_defaults():
    # LLM returns only a name; description/business filled from heuristic defaults.
    llm = _FakeLLM('{"name": "Partial Agent"}')
    use_case = DraftBusinessAgentUseCase(logger=_FakeLogger(), openai_service=llm)
    result = _run(use_case.execute(DraftInputDto(prompt="summarize meeting notes")))
    assert result.draft.name == "Partial Agent"
    assert result.draft.description  # filled
    assert result.draft.business  # filled
