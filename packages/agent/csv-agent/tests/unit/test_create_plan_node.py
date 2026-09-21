import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.layer1_domain.entities.execution_plan import ExecutionPlan, ExecutionStep
from app.layer2_application.graphs.nodes.plan_node import plan_node
from app.layer2_application.use_cases.plan_usecase import PlanOutputDto


class _ArgsSchema:
    @staticmethod
    def model_json_schema():
        return {"properties": {"file_id": {"type": "string"}}}


class _Tool:
    args_schema = _ArgsSchema()

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


def test_plan_node_builds_catalog_from_worker_tools_and_returns_plan():
    expected_plan = ExecutionPlan(
        goal="transform",
        steps=[
            ExecutionStep(
                order=1, tool="transform_column", input={}, reason="requested"
            )
        ],
        workflow_id=None,
    )
    mock_usecase = SimpleNamespace(
        execute=AsyncMock(
            return_value=PlanOutputDto(execution_plan=expected_plan, is_reviewed=False)
        )
    )

    state = {
        "message": "rename column A to B",
        "parameters": {"file_id": "file_123"},
        "conversation_history": [
            {"role": "user", "message": "do it", "file_id": "file_123"}
        ],
    }
    deps = {
        "plan_usecase": mock_usecase,
        "worker_tools": [_Tool("transform_column", "Transform columns")],
    }

    result = asyncio.run(plan_node(state, deps))

    assert result["is_reviewed"] is False
    assert result["execution_plan"]["goal"] == "transform"
    assert result["execution_plan"]["steps"][0]["tool"] == "transform_column"

    input_dto = mock_usecase.execute.await_args.args[0]
    assert input_dto.message == "rename column A to B"
    assert input_dto.file_id == "file_123"
    assert len(input_dto.conversation_history) == 1
    assert input_dto.conversation_history[0].file_id == "file_123"
    assert input_dto.tool_catalog == [
        {
            "name": "transform_column",
            "description": "Transform columns",
            "parameters": {"properties": {"file_id": {"type": "string"}}},
        }
    ]


def test_plan_node_uses_existing_tool_catalog_from_state():
    expected_plan = ExecutionPlan(
        goal="inspect",
        steps=[
            ExecutionStep(order=1, tool="inspect_file_mcp", input={}, reason="inspect")
        ],
        workflow_id=None,
    )
    mock_usecase = SimpleNamespace(
        execute=AsyncMock(
            return_value=PlanOutputDto(execution_plan=expected_plan, is_reviewed=False)
        )
    )

    state = {
        "message": "inspect file",
        "parameters": {},
        "tool_catalog": [
            {
                "name": "inspect_file_mcp",
                "description": "Inspect via MCP",
                "parameters": {},
            }
        ],
    }

    result = asyncio.run(
        plan_node(state, deps={"plan_usecase": mock_usecase, "worker_tools": []})
    )

    assert result["execution_plan"]["steps"][0]["tool"] == "inspect_file_mcp"
    input_dto = mock_usecase.execute.await_args.args[0]
    assert input_dto.tool_catalog[0]["name"] == "inspect_file_mcp"


def test_plan_node_requires_injected_usecase():
    try:
        asyncio.run(plan_node({"message": "x", "parameters": {}}, deps={}))
    except RuntimeError as exc:
        assert "plan_usecase" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
