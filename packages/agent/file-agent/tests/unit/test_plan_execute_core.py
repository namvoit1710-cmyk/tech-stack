from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.file_state import ConversationTurn
from app.layer2_application.features.execute_agent.nodes import plan_execute_nodes as pen


# ==========================================================================
# validate_plan — pure branch guards (no mocks)
# ==========================================================================


def test_validate_plan_happy_single_step():
    plan = {"steps": [{"order": 1, "tool": "plus", "input": {}}]}
    # source: valid plan returns None (no raise)
    assert pen.validate_plan(plan, {"plus": object()}) is None


def test_validate_plan_missing_steps_raises():
    with pytest.raises(ValueError, match="Invalid plan: missing steps"):
        pen.validate_plan({}, {})


def test_validate_plan_unknown_tool_raises():
    plan = {"steps": [{"order": 1, "tool": "ghost", "input": {}}]}
    with pytest.raises(ValueError, match="Unknown tool: ghost"):
        pen.validate_plan(plan, {"plus": object()})


def test_validate_plan_duplicate_order_raises():
    plan = {
        "steps": [
            {"order": 1, "tool": "plus", "input": {}},
            {"order": 1, "tool": "plus", "input": {}},
        ]
    }
    with pytest.raises(ValueError, match="Duplicate step order: 1"):
        pen.validate_plan(plan, {"plus": object()})


def test_validate_plan_forward_reference_raises():
    # Boundary: a step referencing a future step ($step_2 from step 1) is invalid.
    plan = {
        "steps": [
            {"order": 1, "tool": "plus", "input": {"x": "$step_2.file_id"}},
            {"order": 2, "tool": "plus", "input": {}},
        ]
    }
    with pytest.raises(ValueError, match="cannot reference future step 2"):
        pen.validate_plan(plan, {"plus": object()})


def test_validate_plan_non_int_order_raises():
    plan = {"steps": [{"order": "1", "tool": "plus", "input": {}}]}
    with pytest.raises(ValueError, match="Invalid step order"):
        pen.validate_plan(plan, {"plus": object()})


# ==========================================================================
# resolve_references — reference substitution
# ==========================================================================


@pytest.mark.asyncio
async def test_resolve_references_substitutes_prior_step_output():
    outputs = {1: {"file_id": "f-1"}}
    resolved = await pen.resolve_references({"file_id": "$step_1.file_id"}, outputs)
    assert resolved == {"file_id": "f-1"}


@pytest.mark.asyncio
async def test_resolve_references_missing_step_raises():
    with pytest.raises(ValueError, match="Missing step output: step 5"):
        await pen.resolve_references({"x": "$step_5.file_id"}, {})


@pytest.mark.asyncio
async def test_resolve_references_missing_field_raises():
    with pytest.raises(ValueError, match="Field 'file_id' not found in step 1"):
        await pen.resolve_references({"x": "$step_1.file_id"}, {1: {"other": "v"}})


@pytest.mark.asyncio
async def test_resolve_references_passthrough_plain_values():
    # Non-reference strings/values are returned unchanged.
    assert await pen.resolve_references("literal", {}) == "literal"
    assert await pen.resolve_references(["a", 1], {}) == ["a", 1]


# ==========================================================================
# create_plan_node — layer2 use-case (async LLM collaborator via deps)
# ==========================================================================


class _Tool:
    def __init__(self, description=""):
        self.description = description


@pytest.mark.asyncio
async def test_create_plan_node_happy_returns_execution_plan():
    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(
        return_value='{"goal": "g", "steps": [{"order": 1, "tool": "plus", "input": {}}]}'
    )
    state = {"message": "add", "parameters": {"file_id": "f-1"}}
    deps = {"open_ai_service": llm, "tool_registry": {"plus": _Tool("adds")}}

    result = await pen.create_plan_node(state, deps)
    assert result["execution_plan"]["steps"][0]["tool"] == "plus"
    assert result["tool_catalog"] == [{"name": "plus", "description": "adds"}]
    # source: the incoming user message is appended as a ConversationTurn
    assert any(t.role == "user" and t.message == "add" for t in result["conversation_history"])


@pytest.mark.asyncio
async def test_create_plan_node_no_open_ai_service_raises():
    # invalid input: deps missing the LLM collaborator.
    deps = {"tool_registry": {"plus": _Tool()}}
    with pytest.raises(ValueError, match="open_ai_service is required"):
        await pen.create_plan_node({"message": "m"}, deps)


@pytest.mark.asyncio
async def test_create_plan_node_empty_tool_catalog_raises():
    # boundary: no tools registered -> "No tools available for planning."
    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value="{}")
    deps = {"open_ai_service": llm, "tool_registry": {}}
    with pytest.raises(ValueError, match="No tools available for planning"):
        await pen.create_plan_node({"message": "m"}, deps)


@pytest.mark.asyncio
async def test_create_plan_node_llm_failure_wrapped_as_value_error():
    # failure path: LLM raises -> wrapped "Failed to create execution plan..."
    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(side_effect=RuntimeError("api down"))
    deps = {"open_ai_service": llm, "tool_registry": {"plus": _Tool()}}
    with pytest.raises(ValueError, match="Failed to create execution plan"):
        await pen.create_plan_node({"message": "m"}, deps)


# ==========================================================================
# generate_friendly_message — LLM summariser with graceful fallback
# ==========================================================================


@pytest.mark.asyncio
async def test_generate_friendly_message_happy():
    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value='{"message": "All done!"}')
    msg = await pen.generate_friendly_message({"results": []}, llm)
    assert msg == "All done!"


@pytest.mark.asyncio
async def test_generate_friendly_message_falls_back_on_error():
    # failure path: any exception -> "Execution completed." fallback
    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
    msg = await pen.generate_friendly_message({"results": []}, llm)
    assert msg == "Execution completed."


# ==========================================================================
# execute_plan_node — orchestrates validate -> execute -> format
# ==========================================================================


@pytest.mark.asyncio
async def test_execute_plan_node_happy_reports_latest_file_id():
    # A single tool step producing a file_id; friendly-message LLM returns text.
    tool_obj = SimpleNamespace()
    tool_obj.ainvoke = AsyncMock(return_value={"file_id": "out-1"})

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value='{"message": "Nice"}')

    state = {
        "execution_plan": {
            "goal": "g",
            "steps": [{"order": 1, "tool": "transform_row", "input": {}}],
        }
    }
    deps = {
        "tool_registry": {"transform_row": tool_obj},
        "open_ai_service": llm,
        "app_settings": SimpleNamespace(AGENT_TYPE="modify-file"),
    }

    result = await pen.execute_plan_node(state, deps)
    fr = result["formatted_response"]
    assert fr["status"] == "success"
    assert fr["content"] == "Nice"
    assert fr["agent_type"] == "modify-file"
    assert fr["agent_data"]["file_id"] == "out-1"


@pytest.mark.asyncio
async def test_execute_plan_node_tool_error_marks_response_error():
    # failure path: the tool raises -> summary has a status=error result ->
    # response_status "error" and the generic apology content.
    tool_obj = SimpleNamespace()
    tool_obj.ainvoke = AsyncMock(side_effect=RuntimeError("kaboom"))

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value='{"message": "unused"}')

    state = {
        "execution_plan": {
            "goal": "g",
            "steps": [{"order": 1, "tool": "transform_row", "input": {}}],
        }
    }
    deps = {
        "tool_registry": {"transform_row": tool_obj},
        "open_ai_service": llm,
        "app_settings": SimpleNamespace(AGENT_TYPE="modify-file"),
    }

    result = await pen.execute_plan_node(state, deps)
    fr = result["formatted_response"]
    assert fr["status"] == "error"
    assert fr["content"] == (
        "Your request could not be completed due to a temporary issue. Please try again."
    )
    assert fr["agent_data"]["file_id"] == ""


@pytest.mark.asyncio
async def test_execute_plan_node_invalid_plan_propagates_value_error():
    # invalid input: unknown tool -> validate_plan raises before execution.
    state = {"execution_plan": {"steps": [{"order": 1, "tool": "ghost", "input": {}}]}}
    deps = {"tool_registry": {}, "open_ai_service": SimpleNamespace()}
    with pytest.raises(ValueError, match="Unknown tool: ghost"):
        await pen.execute_plan_node(state, deps)
