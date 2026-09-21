"""Additional branch coverage for generate_polar_by_scope.

The nearest sibling (test_generate_polar_by_scope.py) already covers the two
happy dispatch paths (row / column). This file adds the invalid-input,
unknown-scope, and LLM-failure branches. Grounded in generate_polar_by_scope.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


class _MockScopeLlm:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def get_chat_completion(self, *args, **kwargs):
        self.calls += 1
        return self.response


# --------------------------------------------------------------------------
# invalid input
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_long_input_returns_error():
    from app.layer4_frameworks import generate_polar_by_scope as module

    result = await module.generate_polar_by_scope(long_input=None)

    assert result == {"error": "Missing long_input"}


# --------------------------------------------------------------------------
# boundary / edge: unknown scope classifier value -> invalid, no dispatch
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_scope_returns_invalid(monkeypatch):
    from app.layer4_frameworks import generate_polar_by_scope as module

    row_mock = AsyncMock()
    column_mock = AsyncMock()
    monkeypatch.setattr(module, "generate_polar_row", row_mock)
    monkeypatch.setattr(module, "generate_polar_column", column_mock)

    result = await module.generate_polar_by_scope(
        "make it sparkle", llm=_MockScopeLlm('{"scope": "sparkle"}')
    )

    assert result["valid"] == "incorrect"
    assert "sparkle" in result["message"]
    row_mock.assert_not_awaited()
    column_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_scope_key_reports_unknown(monkeypatch):
    from app.layer4_frameworks import generate_polar_by_scope as module

    monkeypatch.setattr(module, "generate_polar_row", AsyncMock())
    monkeypatch.setattr(module, "generate_polar_column", AsyncMock())

    # scope defaults to "" -> "unknown" in message
    result = await module.generate_polar_by_scope(
        "x", llm=_MockScopeLlm('{"other": 1}')
    )

    assert result["valid"] == "incorrect"
    assert "unknown" in result["message"]


# --------------------------------------------------------------------------
# edge: scope value normalized (upper/whitespace) still dispatches
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scope_is_normalized_lower_stripped(monkeypatch):
    from app.layer4_frameworks import generate_polar_by_scope as module

    row_result = {"valid": "correct", "message": "row"}
    row_mock = AsyncMock(return_value=row_result)
    monkeypatch.setattr(module, "generate_polar_row", row_mock)
    monkeypatch.setattr(module, "generate_polar_column", AsyncMock())

    result = await module.generate_polar_by_scope(
        "delete a row", llm=_MockScopeLlm('{"scope": "  ROW  "}')
    )

    assert result == row_result
    row_mock.assert_awaited_once_with("delete a row", header=None)


# --------------------------------------------------------------------------
# failure path: classifier LLM returns non-JSON
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_json_classifier_returns_scope_error():
    from app.layer4_frameworks import generate_polar_by_scope as module

    result = await module.generate_polar_by_scope(
        "x", llm=_MockScopeLlm("not json")
    )

    assert result["error"] == "Failed to determine polar scope"
    assert result["raw_response"] == "not json"


@pytest.mark.asyncio
async def test_header_forwarded_to_dispatcher(monkeypatch):
    from app.layer4_frameworks import generate_polar_by_scope as module

    column_mock = AsyncMock(return_value={"valid": "correct", "message": "column"})
    monkeypatch.setattr(module, "generate_polar_column", column_mock)
    monkeypatch.setattr(module, "generate_polar_row", AsyncMock())

    header = {"name": ""}
    await module.generate_polar_by_scope(
        "rename column", llm=_MockScopeLlm('{"scope": "column"}'), header=header
    )

    column_mock.assert_awaited_once_with("rename column", header=header)
