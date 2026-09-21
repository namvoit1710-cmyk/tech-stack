import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.layer1_domain.entities.agent_hub import AgentHub, ToolHub, WorkflowHub
from app.layer1_domain.entities.planner import PlannerResponseType
from app.layer1_domain.entities.planning_context import (
    PlanningContext,
    PlanningContextType,
    SimpleNodePlanningContext,
    UserIntent,
)
from app.layer4_frameworks.planners.llm_plan_generator import LLMPlanGenerator
from app.layer4_frameworks.planners.llm_plan_simple_node_generator import (
    LLMPlanSimpleNodeGenerator,
)
from app.layer4_frameworks.planners.planner_parser import (
    PlannerParser,
)
from app.layer4_frameworks.planners.planner_resolver import PlannerResolver


EXPECTED_DIRECT_ANSWER = {"type": "direct_answer", "answer": "ok", "steps": []}


# class _SerializableAgentHub(AgentHub):
#     def model_dump(self, *args, **kwargs):
#         kwargs.setdefault("mode", "json")
#         return super().model_dump(*args, **kwargs)


# def _tool(tool_id: str = "tool-1") -> ToolHub:
#     return ToolHub(
#         id=tool_id,
#         name="Tool",
#         description="Does work",
#         input_data={"message": "string"},
#         output_data={"result": "string"},
#     )


# def _workflow(workflow_id: str = "workflow-1") -> WorkflowHub:
#     return WorkflowHub(
#         id=workflow_id,
#         name="Workflow",
#         description="Workflow description",
#     )


# def _agent(agent_id: str = "agent-1") -> AgentHub:
#     return _SerializableAgentHub(
#         id=agent_id,
#         name="Agent",
#         kind="SERVICE",
#         status="READY",
#         is_published=True,
#         description="Agent description",
#         healthcheck_endpoint="/health",
#         invoke_endpoint="/invoke",
#         is_alive=True,
#         last_health_check_at=None,
#         version="1.0.0",
#         config_type="json",
#         capabilities=["plan"],
#         agents=[],
#         workflows=[],
#         tools=[],
#         metadata={},
#         business="business",
#         created_at="2024-01-01T00:00:00Z",
#         updated_at="2024-01-01T00:00:00Z",
#     )


# def _planning_context() -> PlanningContext:
#     return PlanningContext(
#         user_intent=UserIntent(message="Need help", payload={"id": 1}),
#         business_intent="Only use this business intent",
#         tools=[_tool()],
#         workflows_hub=[_workflow()],
#         agents=[_agent()],
#     )


# def _simple_node_context() -> SimpleNodePlanningContext:
#     return SimpleNodePlanningContext(
#         node_message="Please upload a file",
#         workflows_hub=[_workflow()],
#     )


@pytest.mark.parametrize(
    "response",
    [
        EXPECTED_DIRECT_ANSWER,
        json.dumps(EXPECTED_DIRECT_ANSWER),
        [{"type": "text", "text": json.dumps(EXPECTED_DIRECT_ANSWER)}],
        [
            {
                "type": "text",
                "text": '{\\"type\\": \\"direct_answer\\", \\"answer\\": \\"ok\\", \\"steps\\": []}',
            }
        ],
    ],
)
def test_normalize_planner_response_supports_known_shapes(response):
    assert PlannerParser().normalize_planner_response(response) == EXPECTED_DIRECT_ANSWER


@pytest.mark.parametrize(
    ("response", "match"),
    [
        ([{"type": "image", "text": json.dumps(EXPECTED_DIRECT_ANSWER)}], "No text block found"),
        (123, "Unsupported type"),
        ([{"type": "text", "text": "not-json"}], "Failed to parse inner JSON"),
    ],
)
def test_normalize_planner_response_rejects_invalid_input(response, match):
    with pytest.raises(ValueError, match=match):
        PlannerParser().normalize_planner_response(response)


@pytest.mark.parametrize(
    ("response", "expected_type", "expected_steps"),
    [
        (
            json.dumps({"type": "direct_answer", "answer": "ok", "steps": []}),
            PlannerResponseType.DIRECT_ANSWER,
            0,
        ),
        (
            json.dumps(
                {
                    "type": "execution_plan",
                    "goal": "Do work",
                    "steps": [
                        {
                            "order": 1,
                            "tool": "tool.tool-1",
                            "input_schema": {"message": "hi"},
                            "output_schema": {"result": "ok"},
                            "reason": "Because",
                            "ignored": True,
                        }
                    ],
                }
            ),
            PlannerResponseType.EXECUTION_PLAN,
            1,
        ),
        (
            json.dumps(
                {
                    "type": "execution_plan",
                    "goal": "Run workflow",
                    "steps": [
                        {
                            "order": 1,
                            "tool": "tool.tool-1",
                            "input_data": {"message": "hi"},
                            "output_data": {"result": "ok"},
                            "reason": "Because",
                        }
                    ],
                }
            ),
            PlannerResponseType.EXECUTION_PLAN,
            1,
        ),
        (
            json.dumps(
                {
                    "type": "execution_plan",
                    "goal": "Create a template",
                    "steps": [
                        {
                            "order": 1,
                            "tool": "tool.tool-1",
                            "input_data": {
                                "workflow_id": "workflow-1",
                                "predefined_run_id": "default",
                            },
                            "reason": "Start the workflow",
                        }
                    ],
                }
            ),
            PlannerResponseType.EXECUTION_PLAN,
            1,
        ),
    ],
)
def test_planner_parser_parses_responses(response, expected_type, expected_steps):
    parsed = PlannerParser()._parse_response(response)

    assert parsed.type == expected_type
    assert len(parsed.steps) == expected_steps


def test_planner_parser_defaults_missing_output_data_to_empty_dict():
    parsed = PlannerParser()._parse_response(
        json.dumps(
            {
                "type": "execution_plan",
                "goal": "Create a template",
                "steps": [
                    {
                        "order": 1,
                        "tool": "tool.tool-1",
                        "input_data": {
                            "workflow_id": "workflow-1",
                            "predefined_run_id": "default",
                        },
                        "reason": "Start the workflow",
                    }
                ],
            }
        )
    )

    assert parsed.steps[0].output_data == {}


@pytest.mark.parametrize(
    "response",
    [
        json.dumps({"answer": "ok", "steps": []}),
        json.dumps(
            {
                "type": "execution_plan",
                "steps": [{"order": 1, "input_schema": {}, "output_schema": {}}],
            }
        ),
    ],
)
def test_planner_parser_wraps_parse_errors(response):
    with pytest.raises(ValueError, match="Failed to parse planner response"):
        PlannerParser()._parse_response(response)


# @pytest.mark.parametrize(
#     ("planning_context_type", "expected_cls"),
#     [
#         (PlanningContextType.LLM, LLMPlanGenerator),
#         (PlanningContextType.LLM_WITH_SIMPLE_NODE, LLMPlanSimpleNodeGenerator),
#     ],
# )
# def test_planner_resolver_returns_expected_generator(planning_context_type, expected_cls):
#     resolver = PlannerResolver(llm=object())

#     assert isinstance(resolver.resolve(planning_context_type), expected_cls)


# @pytest.mark.parametrize(
#     ("planning_context_type", "message"),
#     [
#         (PlanningContextType.LLM, "No LLM provided for LLM planner"),
#         (
#             PlanningContextType.LLM_WITH_SIMPLE_NODE,
#             "No LLM provided for LLM planner",
#         ),
#     ],
# )
# def test_planner_resolver_requires_llm_for_llm_based_planners(
#     planning_context_type, message
# ):
#     resolver = PlannerResolver(llm=None)

#     with pytest.raises(ValueError, match=message):
#         resolver.resolve(planning_context_type)


# def test_planner_resolver_rejects_rule_planner_without_implementation():
#     resolver = PlannerResolver(llm=object())

#     with pytest.raises(ValueError, match="No rule planner provided"):
#         resolver.resolve(PlanningContextType.RULE)


# def test_planner_resolver_rejects_unknown_planner_type():
#     resolver = PlannerResolver(llm=object())

#     with pytest.raises(ValueError, match="No planner registered"):
#         resolver.resolve("custom")


# def test_llm_plan_generator_build_user_prompt_round_trips_json():
#     generator = LLMPlanGenerator(llm=object())
#     planning_context = _planning_context()

#     prompt = generator._build_user_prompt(planning_context)

#     assert json.loads(prompt) == planning_context.user_intent.model_dump()


# @pytest.mark.parametrize(
#     "expected_fragment",
#     [
#         "BUSINESS_INTENT (STRICT KNOWLEDGE BASE)",
#         "WORKFLOWS (AUTOMATION SYSTEMS)",
#         "TOOLS (ATOMIC CAPABILITIES)",
#         "AGENTS",
#         '"id": "tool-1"',
#         '"id": "workflow-1"',
#         '"id": "agent-1"',
#         "MODE 1: direct_answer",
#         "MODE 2: execution_plan",
#         "DEPENDENCY RULES:",
#         "tool.<id>",
#         "agent.<id>",
#         "WORKFLOWS are NOT executable steps",
#         "Every field listed in input_data.required for the selected tool must be present",
#         "Every execution step must include a non-empty reason field",
#         "the only explanation text allowed is the short user-facing reason field in each step",
#         "If only a workflow matches but no available tool/agent can execute it",
#     ],
# )
# def test_llm_plan_generator_system_prompt_contains_expected_sections(
#     expected_fragment,
# ):
#     generator = LLMPlanGenerator(llm=object())

#     prompt = generator._build_system_prompt(_planning_context())

#     assert expected_fragment in prompt


# @pytest.mark.parametrize(
#     "expected_fragment",
#     [
#         "You are a workflow assistant.",
#         "Friendly but not overly enthusiastic.",
#         '"id": "workflow-1"',
#         "Current node message: Please upload a file",
#         '"type": "direct_answer"',
#         "<user-facing message written in Markdown>",
#     ],
# )
# def test_llm_plan_simple_node_generator_prompt_contains_expected_sections(
#     expected_fragment,
# ):
#     generator = LLMPlanSimpleNodeGenerator(llm=object())

#     prompt = generator._build_system_prompt(_simple_node_context())

#     assert expected_fragment in prompt


# @pytest.mark.asyncio
# async def test_llm_plan_generator_generate_calls_llm_and_parses_response():
#     llm = SimpleNamespace(
#         get_chat_completion=AsyncMock(return_value=json.dumps(EXPECTED_DIRECT_ANSWER))
#     )
#     generator = LLMPlanGenerator(llm)
#     planning_context = _planning_context()

#     result = await generator.generate(planning_context)

#     assert result.type == PlannerResponseType.DIRECT_ANSWER
#     llm.get_chat_completion.assert_awaited_once()
#     call = llm.get_chat_completion.await_args
#     assert call.kwargs["json_mode"] is True
#     assert json.loads(call.args[1]) == planning_context.user_intent.model_dump()


# @pytest.mark.asyncio
# async def test_llm_plan_simple_node_generator_generate_calls_llm_with_empty_user_prompt():
#     llm = SimpleNamespace(
#         get_chat_completion=AsyncMock(return_value=json.dumps(EXPECTED_DIRECT_ANSWER))
#     )
#     generator = LLMPlanSimpleNodeGenerator(llm)

#     result = await generator.generate(_simple_node_context())

#     assert result.type == PlannerResponseType.DIRECT_ANSWER
#     llm.get_chat_completion.assert_awaited_once()
#     call = llm.get_chat_completion.await_args
#     assert call.args[1] == ""
#     assert call.kwargs["json_mode"] is True