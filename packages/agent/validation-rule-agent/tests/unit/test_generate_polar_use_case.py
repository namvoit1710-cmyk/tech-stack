"""Behaviour-parity tests for GeneratePolarUseCase (SA-1444 refactor)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.template_field import TemplateField
from app.layer2_application.features.validation_rule.use_cases.generate_polar_use_case import (
    GeneratePolarInput,
    GeneratePolarUseCase,
)


@pytest.mark.asyncio
async def test_generate_polar_success_no_template():
    polar = MagicMock()
    polar.generate = AsyncMock(return_value={"valid": "correct", "message": "Rule is valid"})
    gov = MagicMock()

    uc = GeneratePolarUseCase(polar, gov, "validation-rule")
    out = (await uc.execute(GeneratePolarInput(message="delete row 1"))).to_dict()

    polar.generate.assert_awaited_once_with("delete row 1", header=None)
    gov.get_template_fields.assert_not_called()
    fr = out["formatted_response"]
    assert fr["status"] == "success"
    assert fr["agent_type"] == "validation-rule"
    assert fr["agent_data"]["message"] == "Rule is valid"


@pytest.mark.asyncio
async def test_generate_polar_with_template_header():
    polar = MagicMock()
    polar.generate = AsyncMock(return_value={"valid": "correct"})
    gov = MagicMock()
    gov.get_template_fields.return_value = [
        TemplateField("t", "f1", "A", "s"),
        TemplateField("t", "f2", "B", "s"),
    ]

    uc = GeneratePolarUseCase(polar, gov, "validation-rule")
    await uc.execute(GeneratePolarInput(message="map columns", template_id="tpl1"))

    gov.get_template_fields.assert_called_once_with("tpl1")
    polar.generate.assert_awaited_once_with("map columns", header={"A": "", "B": ""})


@pytest.mark.asyncio
async def test_generate_polar_error():
    polar = MagicMock()
    polar.generate = AsyncMock(return_value={"valid": "incorrect", "error": "bad scope"})

    uc = GeneratePolarUseCase(polar, MagicMock(), "validation-rule")
    out = (await uc.execute(GeneratePolarInput(message="weird"))).to_dict()

    fr = out["formatted_response"]
    assert fr["status"] == "error"
    assert fr["content"] == "bad scope"


@pytest.mark.asyncio
async def test_generate_polar_governance_failure_returns_error_envelope():
    # SA-1444: governance/LLM failures return a 200 error envelope, not a raw 500.
    polar = MagicMock()
    polar.generate = AsyncMock()
    gov = MagicMock()
    gov.get_template_fields = MagicMock(side_effect=RuntimeError("Governance API request failed (500)"))

    uc = GeneratePolarUseCase(polar, gov, "validation-rule")
    out = (await uc.execute(GeneratePolarInput(message="x", template_id="tpl1"))).to_dict()

    fr = out["formatted_response"]
    assert fr["status"] == "error"
    assert "Governance API request failed" in fr["content"]
    polar.generate.assert_not_awaited()
