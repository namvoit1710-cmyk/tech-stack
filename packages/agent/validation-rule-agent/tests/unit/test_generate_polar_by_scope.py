from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


class MockScopeLlm:
    def __init__(self, response: str):
        self.response = response

    async def get_chat_completion(self, *args, **kwargs):
        return self.response


@pytest.mark.asyncio
async def test_generate_polar_by_scope_dispatches_row(monkeypatch):
    from app.layer4_frameworks import generate_polar_by_scope as module

    row_result = {"valid": "correct", "message": "row"}
    row_mock = AsyncMock(return_value=row_result)
    column_mock = AsyncMock(return_value={"valid": "correct", "message": "column"})

    monkeypatch.setattr(module, "generate_polar_row", row_mock)
    monkeypatch.setattr(module, "generate_polar_column", column_mock)

    result = await module.generate_polar_by_scope(
        "delete row 2",
        llm=MockScopeLlm('{"scope": "row"}'),
    )

    assert result == row_result
    row_mock.assert_awaited_once_with("delete row 2", header=None)
    column_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_polar_by_scope_dispatches_column(monkeypatch):
    from app.layer4_frameworks import generate_polar_by_scope as module

    row_mock = AsyncMock(return_value={"valid": "correct", "message": "row"})
    column_result = {"valid": "correct", "message": "column"}
    column_mock = AsyncMock(return_value=column_result)

    monkeypatch.setattr(module, "generate_polar_row", row_mock)
    monkeypatch.setattr(module, "generate_polar_column", column_mock)

    result = await module.generate_polar_by_scope(
        "rename column amount to total_amount",
        llm=MockScopeLlm('{"scope": "column"}'),
    )

    assert result == column_result
    column_mock.assert_awaited_once_with("rename column amount to total_amount", header=None)
    row_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_polar_use_case_success_shaping():
    # Migrated from the former ValidationRuleProcessor test: the generate_polar
    # flow now lives in GeneratePolarUseCase (SA-1444). Behaviour is unchanged.
    from app.layer2_application.features.validation_rule.use_cases.generate_polar_use_case import (
        GeneratePolarInput,
        GeneratePolarUseCase,
    )

    polar = AsyncMock()
    polar.generate = AsyncMock(return_value={"valid": "correct", "message": "Rule is valid"})

    use_case = GeneratePolarUseCase(polar, AsyncMock(), "validation-rule")
    result = (await use_case.execute(GeneratePolarInput(message="delete row 1"))).to_dict()

    polar.generate.assert_awaited_once_with("delete row 1", header=None)
    assert result["formatted_response"]["status"] == "success"
    assert result["formatted_response"]["agent_data"]["message"] == "Rule is valid"