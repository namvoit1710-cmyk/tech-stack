from app.layer1_domain.entities.csv_execution_result import CSVExecutionResult
from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer1_domain.exceptions import (
    FileProcessingError,
    InvalidInputError,
    MCPCommunicationError,
    TransformationError,
)


def test_execution_plan_from_dict_skips_invalid_steps_and_normalizes_order():
    plan = ExecutionPlan.from_dict(
        {
            "goal": "g",
            "workflow_id": "wf",
            "steps": [
                "bad",
                {"order": "x", "tool": "t", "input": None, "reason": "r"},
            ],
        }
    )

    assert plan.workflow_id == "wf"
    assert plan.steps[0].order == 2
    assert plan.steps[0].input == {}
    assert plan.to_dict()["steps"][0]["tool"] == "t"


def test_csv_execution_result_from_dict_skips_invalid_steps():
    result = CSVExecutionResult.from_dict(
        {
            "success": True,
            "final_file_id": "file_out",
            "step_results": [
                "bad",
                {"order": "1", "tool": "t", "success": True, "result": {"ok": True}},
            ],
        }
    )

    assert result.final_file_id == "file_out"
    assert len(result.step_results) == 1
    assert result.to_dict()["step_results"][0]["result"] == {"ok": True}


def test_workflow_definition_defaults_and_round_trip():
    assert WorkflowDefinition.from_mapping(None).workflow_id == "default"
    workflow = WorkflowDefinition.from_mapping(
        {
            "workflow_id": "wf",
            "workflow_type": "csv",
            "description": "d",
            "metadata": {"a": 1},
        }
    )
    assert workflow.to_dict()["metadata"] == {"a": 1}


def test_domain_exceptions_keep_safe_details():
    assert InvalidInputError("f", "bad", "x").details["field"] == "f"
    assert TransformationError("op", "bad").details["operation"] == "op"
    assert (
        FileProcessingError("file_1", "download", "bad").details["file_id"] == "file_1"
    )
    assert MCPCommunicationError("tool", "bad", {"x": 1}).details["mcp_response"] == {
        "x": 1
    }
