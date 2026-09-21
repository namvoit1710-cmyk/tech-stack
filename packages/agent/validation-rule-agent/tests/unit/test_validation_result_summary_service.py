"""Unit tests for ValidationResultSummaryService.generate_summary (layer4).

Collaborator: an OpenAIService-like object with async get_chat_completion
-> AsyncMock (the method is `async def`). Grounded in
validation_result_summary_service.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.layer4_frameworks.validation_result_summary_service import (
    ValidationResultSummaryService,
)


def _service(llm):
    return ValidationResultSummaryService(open_ai_service=llm)


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_summary_returns_stripped_llm_text():
    llm = AsyncMock()
    llm.get_chat_completion = AsyncMock(return_value="  All good.  \n")
    svc = _service(llm)

    result = {
        "result": {
            "total_rows": 10, "valid_rows": 9, "invalid_rows": 1,
            "odata": {"data": [
                {"rule_name": "Req Name", "violate": 1,
                 "error_message": "missing", "error_code": {"required": ["name"]}}
            ]},
        }
    }

    summary = await svc.generate_summary(result)

    assert summary == "All good."  # stripped
    # LLM invoked as keyword call with json_mode=False (per source)
    llm.get_chat_completion.assert_awaited_once()
    assert llm.get_chat_completion.await_args.kwargs["json_mode"] is False
    # user prompt carries the row counts
    up = llm.get_chat_completion.await_args.kwargs["user_prompt"]
    assert "Total rows: 10" in up
    assert "Invalid rows: 1" in up


# --------------------------------------------------------------------------
# edge / boundary: no errors, zero rows -> still calls LLM with "No errors found"
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_errors_formats_no_errors_found():
    llm = AsyncMock()
    llm.get_chat_completion = AsyncMock(return_value="Nothing to validate.")
    svc = _service(llm)

    summary = await svc.generate_summary({"result": {}})

    assert summary == "Nothing to validate."
    up = llm.get_chat_completion.await_args.kwargs["user_prompt"]
    assert "No errors found" in up
    assert "Total rows: 0" in up


# --------------------------------------------------------------------------
# invalid input: result has no "result" key -> defaults, no crash
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_result_key_uses_defaults():
    llm = AsyncMock()
    llm.get_chat_completion = AsyncMock(return_value="ok")
    svc = _service(llm)

    summary = await svc.generate_summary({})
    assert summary == "ok"


# --------------------------------------------------------------------------
# failure path: LLM raises -> falls back to result["message"]
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_message():
    llm = AsyncMock()
    llm.get_chat_completion = AsyncMock(side_effect=RuntimeError("llm down"))
    svc = _service(llm)

    summary = await svc.generate_summary({"message": "fallback text", "result": {}})
    assert summary == "fallback text"


@pytest.mark.asyncio
async def test_llm_failure_no_message_uses_default():
    llm = AsyncMock()
    llm.get_chat_completion = AsyncMock(side_effect=RuntimeError("x"))
    svc = _service(llm)

    summary = await svc.generate_summary({"result": {}})
    assert summary == "Validation completed."


# --------------------------------------------------------------------------
# _format_errors_with_columns — pure helper branches
# --------------------------------------------------------------------------
def test_format_errors_with_columns_empty():
    svc = _service(AsyncMock())
    assert svc._format_errors_with_columns([]) == "No errors found"


def test_format_errors_with_columns_extracts_first_column():
    svc = _service(AsyncMock())
    out = svc._format_errors_with_columns([
        {"rule_name": "R", "error_message": "bad", "violate": 3,
         "error_code": {"required": ["email", "name"]}}
    ])
    assert "Rule: R" in out
    assert "Violations: 3 rows" in out
    assert "Column: email" in out  # first column extracted


def test_format_errors_with_columns_unknown_column_when_no_code():
    svc = _service(AsyncMock())
    out = svc._format_errors_with_columns([{"rule_name": "R"}])
    assert "Column: Unknown" in out


def test_format_errors_plain_helper():
    svc = _service(AsyncMock())
    assert svc._format_errors([]) == "No errors found"
    out = svc._format_errors([{"error_message": "oops", "violate": 2}])
    assert "oops" in out and "2 rows" in out
