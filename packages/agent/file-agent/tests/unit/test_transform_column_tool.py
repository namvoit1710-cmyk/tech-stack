from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Unit under test: the `transform_column` LangGraph tool (layer2 feature).
# Note: transform_column makes TWO sequential LLM calls (generate + verify),
# so the AsyncMock is driven with side_effect to return distinct payloads.
# Grounded in app/layer2_application/features/transform_column.py.


def _arrange(monkeypatch, *, llm_side_effect=None, llm_response="{}", df_return=None):
    from app.layer2_application.features import transform_column as mod

    llm = SimpleNamespace()
    if llm_side_effect is not None:
        llm.get_chat_completion = AsyncMock(side_effect=llm_side_effect)
    else:
        llm.get_chat_completion = AsyncMock(return_value=llm_response)
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)

    monkeypatch.setattr(
        mod, "_get_first_n_rows_impl", MagicMock(return_value={"data": []})
    )

    client = MagicMock()
    client.execute_column_payload = MagicMock(
        return_value=df_return
        if df_return is not None
        else {"file_id": "col-out", "download_url": "u", "message": "ok"}
    )
    monkeypatch.setattr(mod, "DataFactoryClient", lambda: client)

    return mod, llm, client


# --- invalid input --------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_column_missing_long_input_returns_error(monkeypatch):
    mod, _llm, client = _arrange(monkeypatch)
    result = await mod.transform_column.ainvoke({"long_input": "", "file_id": "f-1"})
    assert result == {"error": "Missing long_input"}
    client.execute_column_payload.assert_not_called()


@pytest.mark.asyncio
async def test_transform_column_missing_file_id_returns_error(monkeypatch):
    mod, _llm, client = _arrange(monkeypatch)
    result = await mod.transform_column.ainvoke(
        {"long_input": "drop col X", "file_id": ""}
    )
    assert result == {"error": "Missing file_id"}
    client.execute_column_payload.assert_not_called()


# --- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_column_happy_path_uses_verified_payload(monkeypatch):
    # Two LLM calls: first generates, second (verification) returns the final JSON.
    mod, llm, client = _arrange(
        monkeypatch,
        llm_side_effect=[
            '{"file_path": "raw", "rules": [{"rule_name": "r1"}]}',
            '{"file_path": "verified", "rules": [{"rule_name": "r1"}]}',
        ],
        df_return={"file_id": "cv2", "download_url": "http://x/cv2", "message": "done"},
    )
    result = await mod.transform_column.ainvoke(
        {"long_input": "drop col X", "file_id": "f-1.csv"}
    )
    assert result == {"file_id": "cv2", "download_url": "http://x/cv2", "message": "done"}
    # source parses the SECOND (verification) response, then overrides file_path.
    sent_payload = client.execute_column_payload.call_args.args[0]
    assert sent_payload["file_path"] == "f-1.csv"
    assert sent_payload["rules"] == [{"rule_name": "r1"}]
    assert llm.get_chat_completion.await_count == 2


# --- boundary -------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_column_empty_rules_boundary(monkeypatch):
    mod, _llm, client = _arrange(
        monkeypatch,
        llm_side_effect=['{"rules": []}', '{"rules": []}'],
    )
    result = await mod.transform_column.ainvoke(
        {"long_input": "no-op", "file_id": "f-9"}
    )
    assert result == {"file_id": "col-out", "download_url": "u", "message": "ok"}
    sent_payload = client.execute_column_payload.call_args.args[0]
    assert sent_payload == {"rules": [], "file_path": "f-9"}


# --- failure path ---------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_column_unparseable_verification_returns_parse_error(monkeypatch):
    # First call ok, verification returns junk -> json.loads raises in try-block.
    mod, _llm, client = _arrange(
        monkeypatch,
        llm_side_effect=['{"rules": []}', "not-json"],
    )
    result = await mod.transform_column.ainvoke(
        {"long_input": "drop col X", "file_id": "f-1"}
    )
    assert result["error"] == "Failed to parse LLM output"
    assert result["raw_response"] == "not-json"
    client.execute_column_payload.assert_not_called()
