"""Tests for the dedicated ValidateByRulesUseCase (SA-1444)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer2_application.features.validation_rule.use_cases.execute_validation_use_case import (
    ExecuteValidationInput,
)
from app.layer2_application.features.validation_rule.use_cases.validate_by_rules_use_case import (
    ValidateByRulesInput,
    ValidateByRulesUseCase,
)


@pytest.mark.asyncio
async def test_missing_rules_returns_error_without_calling_engine():
    engine = MagicMock(execute=AsyncMock())
    uc = ValidateByRulesUseCase(engine, "validation-rule")

    out = (await uc.execute(ValidateByRulesInput(file_id="f.csv"))).to_dict()

    engine.execute.assert_not_awaited()
    fr = out["formatted_response"]
    assert fr["status"] == "error"
    assert "rules is required" in fr["content"]


@pytest.mark.asyncio
async def test_delegates_to_engine_when_rules_present():
    sentinel = {"formatted_response": {"status": "success"}}
    engine = MagicMock(execute=AsyncMock(return_value=sentinel))
    uc = ValidateByRulesUseCase(engine, "validation-rule")

    rules = [{"scope": "column"}]
    out = await uc.execute(ValidateByRulesInput(rules=rules, file_id="f.csv"))

    engine.execute.assert_awaited_once_with(
        ExecuteValidationInput(message="", file_id="f.csv", rules=rules, header=None, template_id="")
    )
    assert out is sentinel
