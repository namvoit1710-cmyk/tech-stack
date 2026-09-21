"""Unit specs for the LLM-backed plan generators (layer4 planners).

White-box, grounded in source:
- app/layer4_frameworks/planners/llm_plan_generator.py (LLMPlanGenerator)
- app/layer4_frameworks/planners/llm_plan_simple_node_generator.py
- app/layer4_frameworks/planners/llm_summary_context.py

Each generator takes one collaborator, `llm` (LangChainLLMService), whose
`get_chat_completion` is ASYNC -> AsyncMock (canon invariant: async collaborator
-> AsyncMock). `generate()` awaits get_chat_completion(system, user, json_mode=True)
then hands the raw content to PlannerParser._parse_response. The generator does
NOT interpret the content itself, so we feed it a valid planner JSON payload and
assert the parse + the exact call the generator made to the llm.

Prompt-builder assertions are grounded in the literal f-string sections in source.
Cases: happy, edge, invalid input, boundary, failure path.
"""

import json
from unittest.mock import AsyncMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    GlobalContext,
    Metadata,
    Task,
)
from app.layer1_domain.entities.planner import PlannerResponseType
from app.layer1_domain.entities.planning_context import (
    PlanningContext,
    SimpleNodePlanningContext,
    SummaryContext,
)
from app.layer1_domain.entities.agent_hub import (
    AgentHubDTO,
    ToolHubDTO,
    WorkflowHubDTO,
)
from app.layer4_frameworks.planners.llm_plan_generator import LLMPlanGenerator
from app.layer4_frameworks.planners.llm_plan_simple_node_generator import (
    LLMPlanSimpleNodeGenerator,
)
from app.layer4_frameworks.planners.llm_summary_context import (
    LLMSummaryContextGenerator,
)


DIRECT_ANSWER_JSON = json.dumps(
    {"type": "direct_answer", "answer": "ok", "steps": []}
)


def _tool(tool_id: str = "tool-1") -> ToolHubDTO:
    return ToolHubDTO(
        id=tool_id,
        name="My Tool",
        description="does work",
        input_data={"required": ["message"]},
        output_data={"result": "string"},
    )


def _workflow(wf_id: str = "workflow-1") -> WorkflowHubDTO:
    return WorkflowHubDTO(id=wf_id, name="My Workflow", description="a workflow")


def _agent(agent_id: str = "agent-1") -> AgentHubDTO:
    return AgentHubDTO(
        id=agent_id,
        name="My Agent",
        description="an agent",
        capabilities=["plan"],
        business="biz",
        metadata={},
    )


def _planning_context() -> PlanningContext:
    return PlanningContext(
        business_intent="Only use this business intent",
        global_context=GlobalContext(),
        current_task=Task(id="task-1", execute_by=None, goal="Need help"),
        metadata=Metadata(conv_id="conv-1", checkpoint_id="CP-01"),
        tools=[_tool()],
        workflows_hub=[_workflow()],
        agents=[_agent()],
    )


def _simple_node_context() -> SimpleNodePlanningContext:
    return SimpleNodePlanningContext(
        node_message="Please upload a file",
        workflows_hub=[_workflow()],
    )


# =====================================================================
# LLMPlanGenerator
# =====================================================================

# --- happy path: generate awaits the llm once and parses the response ---
@pytest.mark.asyncio
async def test_generate_calls_llm_and_parses_response():
    llm = AsyncMock()
    llm.get_chat_completion.return_value = DIRECT_ANSWER_JSON
    generator = LLMPlanGenerator(llm)
    context = _planning_context()

    result = await generator.generate(context)

    assert result.type == PlannerResponseType.DIRECT_ANSWER
    assert result.answer == "ok"
    llm.get_chat_completion.assert_awaited_once()
    call = llm.get_chat_completion.await_args
    # source: get_chat_completion(system_prompt, user_prompt, json_mode=True)
    assert call.kwargs["json_mode"] is True
    system_prompt, user_prompt = call.args[0], call.args[1]
    # user prompt carries the planning context, not instructions (source: _build_user_prompt)
    payload = json.loads(user_prompt)
    assert payload["business_intent"] == "Only use this business intent"
    assert set(payload) == {
        "business_intent",
        "global_context",
        "current_task",
        "metadata",
    }
    assert "planning and routing node" in system_prompt


# --- happy path: the system prompt embeds all context sections (source f-string) ---
@pytest.mark.parametrize(
    "fragment",
    [
        "BUSINESS_INTENT (STRICT KNOWLEDGE BASE)",
        "Only use this business intent",
        "WORKFLOWS (AUTOMATION SYSTEMS)",
        "TOOLS (ATOMIC CAPABILITIES)",
        "AGENTS",
        '"id": "tool-1"',
        '"id": "workflow-1"',
        '"id": "agent-1"',
        "MODE 1: direct_answer",
        "MODE 2: execution_plan",
        "WORKFLOWS are NOT executable steps",
    ],
)
def test_build_system_prompt_contains_sections(fragment):
    prompt = LLMPlanGenerator(llm=object())._build_system_prompt(_planning_context())

    assert fragment in prompt


# --- boundary: empty tools/workflows/agents render as empty JSON arrays ---
def test_build_system_prompt_with_empty_context():
    context = PlanningContext(
        business_intent="B",
        global_context=GlobalContext(),
        current_task=Task(id="task-1", execute_by=None, goal="hi"),
        metadata=Metadata(conv_id="conv-1", checkpoint_id="CP-01"),
    )
    prompt = LLMPlanGenerator(llm=object())._build_system_prompt(context)

    # empty lists serialize to "[]" (source: json.dumps([...]) over empty lists)
    assert "[]" in prompt
    assert "B" in prompt


# --- edge: user prompt round-trips the planning context payload exactly ---
def test_build_user_prompt_round_trips_planning_context():
    context = _planning_context()

    user_prompt = LLMPlanGenerator(llm=object())._build_user_prompt(context)

    payload = json.loads(user_prompt)
    assert payload["business_intent"] == "Only use this business intent"
    # current_task is dumped by alias (source: model_dump(by_alias=True) -> goal is "name")
    assert payload["current_task"]["name"] == "Need help"
    assert payload["metadata"]["conv_id"] == "conv-1"


# --- failure path: llm error propagates out of generate ---
@pytest.mark.asyncio
async def test_generate_propagates_llm_error():
    llm = AsyncMock()
    llm.get_chat_completion.side_effect = RuntimeError("llm down")
    generator = LLMPlanGenerator(llm)

    with pytest.raises(RuntimeError, match="llm down"):
        await generator.generate(_planning_context())


# --- invalid input: non-JSON llm output is wrapped by the parser ---
@pytest.mark.asyncio
async def test_generate_wraps_unparseable_llm_output():
    llm = AsyncMock()
    llm.get_chat_completion.return_value = "not json"
    generator = LLMPlanGenerator(llm)

    with pytest.raises(ValueError, match="Failed to parse planner response"):
        await generator.generate(_planning_context())


# =====================================================================
# LLMPlanSimpleNodeGenerator
# =====================================================================

# --- happy path: generate awaits llm with EMPTY user prompt (source line 23-24) ---
@pytest.mark.asyncio
async def test_simple_node_generate_uses_empty_user_prompt():
    llm = AsyncMock()
    llm.get_chat_completion.return_value = DIRECT_ANSWER_JSON
    generator = LLMPlanSimpleNodeGenerator(llm)

    result = await generator.generate(_simple_node_context())

    assert result.type == PlannerResponseType.DIRECT_ANSWER
    llm.get_chat_completion.assert_awaited_once()
    call = llm.get_chat_completion.await_args
    assert call.args[1] == ""  # user prompt is the literal ""
    assert call.kwargs["json_mode"] is True


# --- happy path: system prompt contains the node message + workflow hub ---
@pytest.mark.parametrize(
    "fragment",
    [
        "You are a workflow assistant.",
        "Current node message: Please upload a file",
        '"id": "workflow-1"',
        '"type": "direct_answer"',
    ],
)
def test_simple_node_system_prompt_contains_sections(fragment):
    prompt = LLMPlanSimpleNodeGenerator(llm=object())._build_system_prompt(
        _simple_node_context()
    )

    assert fragment in prompt


# --- boundary: empty workflows_hub still renders (empty JSON array) ---
def test_simple_node_prompt_with_no_workflows():
    context = SimpleNodePlanningContext(node_message="Done", workflows_hub=[])

    prompt = LLMPlanSimpleNodeGenerator(llm=object())._build_system_prompt(context)

    assert "Current node message: Done" in prompt
    assert "[]" in prompt


# --- failure path: llm error propagates ---
@pytest.mark.asyncio
async def test_simple_node_generate_propagates_llm_error():
    llm = AsyncMock()
    llm.get_chat_completion.side_effect = TimeoutError("slow")
    generator = LLMPlanSimpleNodeGenerator(llm)

    with pytest.raises(TimeoutError):
        await generator.generate(_simple_node_context())


# =====================================================================
# LLMSummaryContextGenerator
# =====================================================================

# --- happy path: summary generate awaits llm with empty user prompt + parses ---
@pytest.mark.asyncio
async def test_summary_generate_calls_llm_with_empty_user_prompt():
    summary_json = json.dumps(
        {"type": "summary", "answer": "a summary", "steps": []}
    )
    llm = AsyncMock()
    llm.get_chat_completion.return_value = summary_json
    generator = LLMSummaryContextGenerator(llm)

    result = await generator.generate(SummaryContext(context="raw rows here"))

    assert result.type == PlannerResponseType.SUMMARY
    assert result.answer == "a summary"
    call = llm.get_chat_completion.await_args
    assert call.args[1] == ""
    assert call.kwargs["json_mode"] is True


# --- happy path: system prompt embeds the supplied context text ---
def test_summary_system_prompt_contains_context():
    prompt = LLMSummaryContextGenerator(llm=object())._build_system_prompt(
        SummaryContext(context="raw rows here")
    )

    assert "data summarization assistant" in prompt
    assert "Context: raw rows here" in prompt
    assert '"type": "summary"' in prompt


# --- boundary: empty context string still renders the prompt ---
def test_summary_system_prompt_with_empty_context():
    prompt = LLMSummaryContextGenerator(llm=object())._build_system_prompt(
        SummaryContext(context="")
    )

    assert "Context: " in prompt


# --- failure path: llm error propagates ---
@pytest.mark.asyncio
async def test_summary_generate_propagates_llm_error():
    llm = AsyncMock()
    llm.get_chat_completion.side_effect = RuntimeError("boom")
    generator = LLMSummaryContextGenerator(llm)

    with pytest.raises(RuntimeError, match="boom"):
        await generator.generate(SummaryContext(context="x"))
