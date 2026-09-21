"""Unit tests for generate_polar_row (layer4 Polars-row-payload generator).

Collaborator: agent_sdk.OpenAIService with async get_chat_completion.
Unlike the column generator this makes a SINGLE LLM call (no verification pass).
Expectations grounded in generate_polar_row.py.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


def _patched_llm(response):
    svc = AsyncMock()
    svc.get_chat_completion = AsyncMock(return_value=response)
    return svc


# --------------------------------------------------------------------------
# invalid input
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_long_input_returns_error_without_llm():
    from app.layer4_frameworks import generate_polar_row as module

    with patch.object(module, "OpenAIService") as ctor:
        result = await module.generate_polar_row(long_input=None)

    assert result == {"error": "Missing long_input"}
    ctor.assert_not_called()


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_happy_path_returns_success_payload():
    from app.layer4_frameworks import generate_polar_row as module

    rules = [{"scope": "row", "operation": "delete",
              "row_identifier": {"type": "index", "index": 1}}]
    svc = _patched_llm(json.dumps({"rules": rules}))

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_row(
            long_input="delete row 2", header={"id": ""}
        )

    assert result["valid"] == "correct"
    assert json.loads(result["expression"]) == rules
    # single LLM call (no verification stage)
    assert svc.get_chat_completion.await_count == 1


# --------------------------------------------------------------------------
# boundary: empty rules -> invalid
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_rules_returns_invalid():
    from app.layer4_frameworks import generate_polar_row as module

    svc = _patched_llm(json.dumps({"rules": []}))

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_row(long_input="nothing", header={})

    # generate_polar_row calls ValidationRuleAgentData.invalid(expression=[])
    assert result["valid"] == "incorrect"
    assert result["expression"] == ""


@pytest.mark.asyncio
async def test_rules_none_returns_invalid():
    from app.layer4_frameworks import generate_polar_row as module

    svc = _patched_llm(json.dumps({"something_else": 1}))

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_row(long_input="x", header={})

    assert result["valid"] == "incorrect"


# --------------------------------------------------------------------------
# edge: header defaults to {} when None (schema_json = "{}")
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_none_header_serializes_to_empty_object():
    from app.layer4_frameworks import generate_polar_row as module

    rules = [{"scope": "row", "operation": "insert"}]
    svc = _patched_llm(json.dumps({"rules": rules}))

    with patch.object(module, "OpenAIService", return_value=svc):
        await module.generate_polar_row(long_input="insert", header=None)

    system_prompt = svc.get_chat_completion.await_args.args[0]
    # json.dumps({}) == "{}" substituted for {DATASET_SCHEMA}
    assert "{}" in system_prompt


# --------------------------------------------------------------------------
# failure path: invalid JSON from LLM
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_json_output_returns_parse_error():
    from app.layer4_frameworks import generate_polar_row as module

    svc = _patched_llm("not json at all")

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_row(long_input="x", header={})

    assert result["error"] == "Failed to parse LLM output"
    assert result["raw_response"] == "not json at all"


@pytest.mark.asyncio
async def test_llm_raises_returns_parse_error_raw_none():
    from app.layer4_frameworks import generate_polar_row as module

    svc = AsyncMock()
    svc.get_chat_completion = AsyncMock(side_effect=ValueError("bad"))

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_row(long_input="x", header={})

    assert result["error"] == "Failed to parse LLM output"
    assert result["details"] == "bad"
    assert result["raw_response"] is None
