"""Unit tests for generate_polar_column (layer4 Polars-payload generator).

Collaborator: agent_sdk.OpenAIService with async get_chat_completion.
The generator makes TWO sequential LLM calls (system+user, then verification).
Expectations are grounded in generate_polar_column.py, not theory.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


def _patched_llm(*responses):
    """Return an AsyncMock OpenAIService whose get_chat_completion yields
    the given responses in order (one per await)."""
    svc = AsyncMock()
    svc.get_chat_completion = AsyncMock(side_effect=list(responses))
    return svc


# --------------------------------------------------------------------------
# invalid input
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_long_input_returns_error_without_calling_llm():
    from app.layer4_frameworks import generate_polar_column as module

    # guard: `if not long_input` returns before OpenAIService() is constructed
    with patch.object(module, "OpenAIService") as ctor:
        result = await module.generate_polar_column(long_input=None)

    assert result == {"error": "Missing long_input"}
    ctor.assert_not_called()


@pytest.mark.asyncio
async def test_empty_string_long_input_returns_error():
    from app.layer4_frameworks import generate_polar_column as module

    with patch.object(module, "OpenAIService") as ctor:
        result = await module.generate_polar_column(long_input="")

    assert result == {"error": "Missing long_input"}
    ctor.assert_not_called()


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_happy_path_returns_success_payload():
    from app.layer4_frameworks import generate_polar_column as module

    rules = [{"scope": "column", "rule_name": "r1", "type": "filter",
              "params": {"expression": 'pl.col("status") == "active"'}}]
    verified = json.dumps({"rules": rules})
    svc = _patched_llm('{"rules": []}', verified)  # 2nd (verification) response wins

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_column(
            long_input="filter active rows",
            header={"status": ""},
        )

    # ValidationRuleAgentData.success -> valid=correct, expression=json.dumps(rules)
    assert result["valid"] == "correct"
    assert result["message"] == "Rule is valid"
    assert json.loads(result["expression"]) == rules
    # two LLM calls: generate then verify
    assert svc.get_chat_completion.await_count == 2


# --------------------------------------------------------------------------
# edge: schema passed through as DATASET_SCHEMA (header serialized to system prompt)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_header_is_serialized_into_system_prompt():
    from app.layer4_frameworks import generate_polar_column as module

    rules = [{"scope": "column", "rule_name": "r", "type": "drop_columns",
              "params": {"columns_to_drop": ["a"]}}]
    svc = _patched_llm('{"rules": []}', json.dumps({"rules": rules}))

    with patch.object(module, "OpenAIService", return_value=svc):
        await module.generate_polar_column(long_input="drop a", header={"a": "", "b": ""})

    first_call = svc.get_chat_completion.await_args_list[0]
    system_prompt = first_call.args[0]
    # header dict was json.dumps-ed and substituted for {DATASET_SCHEMA}
    assert '"a"' in system_prompt and '"b"' in system_prompt


# --------------------------------------------------------------------------
# boundary: empty rules -> invalid
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_rules_returns_invalid():
    from app.layer4_frameworks import generate_polar_column as module

    svc = _patched_llm('{"rules": []}', json.dumps({"rules": [], "error": "nothing to do"}))

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_column(long_input="do nothing", header={})

    # ValidationRuleAgentData.invalid -> valid=incorrect, expression=""
    assert result["valid"] == "incorrect"
    assert result["message"] == "nothing to do"
    assert result["expression"] == ""


@pytest.mark.asyncio
async def test_rules_key_missing_returns_invalid():
    from app.layer4_frameworks import generate_polar_column as module

    # payload has no "rules" key -> rules is None -> invalid branch
    svc = _patched_llm('{}', json.dumps({}))

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_column(long_input="x", header={})

    assert result["valid"] == "incorrect"


# --------------------------------------------------------------------------
# failure path: LLM output is not valid JSON
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_json_llm_output_returns_parse_error():
    from app.layer4_frameworks import generate_polar_column as module

    # verification response is not JSON -> json.loads raises -> caught
    svc = _patched_llm('{"rules": []}', "this is not json")

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_column(long_input="x", header={})

    assert result["error"] == "Failed to parse LLM output"
    assert "details" in result
    assert result["raw_response"] == "this is not json"


@pytest.mark.asyncio
async def test_llm_raises_returns_parse_error_with_raw_none():
    from app.layer4_frameworks import generate_polar_column as module

    svc = AsyncMock()
    # first call raises before `response` is bound in the except handler's locals()
    svc.get_chat_completion = AsyncMock(side_effect=RuntimeError("boom"))

    with patch.object(module, "OpenAIService", return_value=svc):
        result = await module.generate_polar_column(long_input="x", header={})

    assert result["error"] == "Failed to parse LLM output"
    assert result["details"] == "boom"
    assert result["raw_response"] is None
