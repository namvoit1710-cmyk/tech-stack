"""Behaviour-parity tests for ExecuteValidationUseCase (SA-1444 refactor)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer2_application.features.validation_rule.use_cases.execute_validation_use_case import (
    ExecuteValidationInput,
    ExecuteValidationUseCase,
)


def _uc(**over):
    defaults = dict(
        file_service=MagicMock(),
        header_inference=MagicMock(get_header_by_llm=AsyncMock(return_value={"header": {"header_columns": []}})),
        planner=MagicMock(plan=AsyncMock(return_value={"execution_plan": []})),
        column_executor=MagicMock(),
        summary_service=MagicMock(generate_summary=AsyncMock(return_value="summary text")),
        governance=MagicMock(),
        agent_type="validation-rule",
    )
    defaults.update(over)
    return ExecuteValidationUseCase(**defaults)


@pytest.mark.asyncio
async def test_rule_only_no_file():
    uc = _uc()
    out = (await uc.execute(ExecuteValidationInput(rules=[{"scope": "column", "type": "unique"}]))).to_dict()

    fr = out["formatted_response"]
    assert fr["status"] == "success"
    assert fr["content"] == "Rules generated successfully."
    uc._summary_service.generate_summary.assert_not_awaited()  # result.result is {} -> no summary


@pytest.mark.asyncio
async def test_file_same_scope_execution():
    col = MagicMock()
    col.execute_column_operations = MagicMock(
        return_value={"result": {"invalid_rows": 0, "odata": {"result_file_id": "rf", "result_file_url": "http://x/rf"}}}
    )
    col.execute_batch = MagicMock()
    uc = _uc(column_executor=col)

    steps = [{"scope": "column", "type": "unique"}]
    out = (await uc.execute(ExecuteValidationInput(file_id="data.csv", rules=steps))).to_dict()

    col.execute_column_operations.assert_called_once_with("data.csv", "csv", steps)
    col.execute_batch.assert_not_called()
    fr = out["formatted_response"]
    assert fr["status"] == "success"
    assert fr["content"] == "summary text\n\nDownload detailed report: http://x/rf"
    assert fr["agent_data"]["validate_status"] is True
    assert fr["agent_data"]["file_id"] == "rf"


@pytest.mark.asyncio
async def test_planner_not_supported():
    uc = _uc(planner=MagicMock(plan=AsyncMock(return_value={"status": "not_supported", "message": "cannot"})))
    out = (await uc.execute(ExecuteValidationInput(message="do something weird"))).to_dict()

    fr = out["formatted_response"]
    assert fr["status"] == "error"
    assert fr["content"] == "cannot"


@pytest.mark.asyncio
async def test_error_path_wraps_exception():
    uc = _uc(planner=MagicMock(plan=AsyncMock(side_effect=RuntimeError("boom"))))
    out = (await uc.execute(ExecuteValidationInput(message="x"))).to_dict()

    fr = out["formatted_response"]
    assert fr["status"] == "error"
    assert fr["content"].startswith("Validation Rule Agent execute fail.")
