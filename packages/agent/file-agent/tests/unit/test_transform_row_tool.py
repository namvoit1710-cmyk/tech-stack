from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Unit under test: the `transform_row` LangGraph tool (layer2 feature).
# Collaborators mocked at the module boundary:
#   - OpenAIService (async get_chat_completion) -> AsyncMock via a factory lambda
#   - _get_first_n_rows_impl (sync) -> MagicMock
#   - DataFactoryClient (sync execute_row_payload) -> MagicMock instance
# Grounded in app/layer2_application/features/transform_row.py.


def _arrange(monkeypatch, *, llm_response="{}", df_return=None, preview=None):
    from app.layer2_application.features import transform_row as mod

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value=llm_response)
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)

    monkeypatch.setattr(
        mod, "_get_first_n_rows_impl", MagicMock(return_value=preview or {"data": []})
    )

    client = MagicMock()
    client.execute_row_payload = MagicMock(
        return_value=df_return
        if df_return is not None
        else {"file_id": "new-file", "download_url": "u", "message": "ok"}
    )
    monkeypatch.setattr(mod, "DataFactoryClient", lambda: client)

    return mod, llm, client


# --- invalid input --------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_row_missing_long_input_returns_error(monkeypatch):
    mod, _llm, client = _arrange(monkeypatch)
    result = await mod.transform_row.ainvoke({"long_input": "", "file_id": "f-1"})
    # source: `if not long_input: return {"error": "Missing long_input"}`
    assert result == {"error": "Missing long_input"}
    client.execute_row_payload.assert_not_called()


@pytest.mark.asyncio
async def test_transform_row_missing_file_id_returns_error(monkeypatch):
    mod, _llm, client = _arrange(monkeypatch)
    result = await mod.transform_row.ainvoke({"long_input": "delete row 1", "file_id": ""})
    # source: `if not file_id: return {"error": "Missing file_id"}`
    assert result == {"error": "Missing file_id"}
    client.execute_row_payload.assert_not_called()


# --- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_row_happy_path_overrides_file_path_and_delegates(monkeypatch):
    mod, llm, client = _arrange(
        monkeypatch,
        llm_response='{"file_path": "WRONG", "operations": [{"operation": "delete"}]}',
        df_return={"file_id": "v2", "download_url": "http://x/v2", "message": "done"},
    )
    result = await mod.transform_row.ainvoke(
        {"long_input": "delete row 1", "file_id": "f-1.csv"}
    )
    # source: payload["file_path"] = file_id  (safety override) then delegate
    assert result == {"file_id": "v2", "download_url": "http://x/v2", "message": "done"}
    sent_payload = client.execute_row_payload.call_args.args[0]
    assert sent_payload["file_path"] == "f-1.csv"  # override, not "WRONG"
    llm.get_chat_completion.assert_awaited_once()


# --- boundary -------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_row_empty_operations_still_overrides_and_delegates(monkeypatch):
    # Boundary: LLM returns a structurally-valid payload with zero operations.
    mod, _llm, client = _arrange(
        monkeypatch, llm_response='{"file_path": "x", "operations": []}'
    )
    result = await mod.transform_row.ainvoke(
        {"long_input": "no-op", "file_id": "f-9"}
    )
    assert result == {"file_id": "new-file", "download_url": "u", "message": "ok"}
    sent_payload = client.execute_row_payload.call_args.args[0]
    assert sent_payload == {"file_path": "f-9", "operations": []}


# --- failure path ---------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_row_unparseable_llm_output_returns_parse_error(monkeypatch):
    mod, _llm, client = _arrange(monkeypatch, llm_response="not-json")
    result = await mod.transform_row.ainvoke(
        {"long_input": "delete row 1", "file_id": "f-1"}
    )
    # source except-branch: json.loads raises -> "Failed to parse LLM output"
    assert result["error"] == "Failed to parse LLM output"
    assert result["raw_response"] == "not-json"
    assert "details" in result
    client.execute_row_payload.assert_not_called()
