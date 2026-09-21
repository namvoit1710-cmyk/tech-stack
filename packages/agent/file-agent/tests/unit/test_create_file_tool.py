from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Unit under test: the `create_file` LangGraph tool (layer2 feature).
# Collaborators mocked: OpenAIService (async), AppSettingsAdapter (sync),
# requests.post (sync). Grounded in
# app/layer2_application/features/create_file.py.


def _arrange(monkeypatch, *, llm_response, post=None):
    from app.layer2_application.features import create_file as mod

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value=llm_response)
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)
    monkeypatch.setattr(
        mod,
        "AppSettingsAdapter",
        lambda: SimpleNamespace(FILE_URL="https://files.example/"),
    )
    if post is not None:
        monkeypatch.setattr(mod.requests, "post", post)
    return mod, llm


# --- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_create_file_happy_uploads_and_returns_download_url(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs["params"]
        captured["temp_path"] = kwargs["data"].name
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"file_id": "file-123"}
        return resp

    mod, _llm = _arrange(
        monkeypatch,
        llm_response='{"columns": ["a", "b"], "rows": [["1", "2"]]}',
        post=fake_post,
    )
    result = await mod.create_file.ainvoke(
        {"filename": "out", "description": "make a csv"}
    )
    # source: rstrip("/") on FILE_URL then f"{url}/api/v1/download/{file_id}"
    assert result == {
        "message": "File uploaded successfully",
        "download_url": "https://files.example/api/v1/download/file-123",
        "file_id": "file-123",
    }
    assert captured["url"] == "https://files.example/api/v1/upload"
    # source: filename gets ".csv" appended when missing
    assert captured["params"] == {"filename": "out.csv"}
    # source finally-block removes the temp file
    assert not os.path.exists(captured["temp_path"])


# --- edge: filename already ends with .csv is NOT double-suffixed ---------


@pytest.mark.asyncio
async def test_create_file_keeps_existing_csv_suffix(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["params"] = kwargs["params"]
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"file_id": "f2"}
        return resp

    mod, _llm = _arrange(
        monkeypatch,
        llm_response='{"columns": ["a"], "rows": []}',
        post=fake_post,
    )
    await mod.create_file.ainvoke({"filename": "report.csv", "description": "x"})
    assert captured["params"] == {"filename": "report.csv"}


# --- invalid input: LLM JSON missing required keys ------------------------


@pytest.mark.asyncio
async def test_create_file_missing_columns_key_returns_error(monkeypatch):
    # source raises ValueError("Invalid JSON structure...") which is caught by
    # the broad `except Exception` branch (NOT JSONDecodeError).
    mod, _llm = _arrange(monkeypatch, llm_response='{"rows": []}')
    result = await mod.create_file.ainvoke({"filename": "x", "description": "y"})
    assert result["message"] == "An error occurred"
    assert result["error"] == "Invalid JSON structure from LLM"


# --- boundary: LLM returns non-JSON text ----------------------------------


@pytest.mark.asyncio
async def test_create_file_invalid_json_from_llm(monkeypatch):
    # json.loads raises JSONDecodeError -> dedicated except branch.
    mod, _llm = _arrange(monkeypatch, llm_response="totally not json")
    result = await mod.create_file.ainvoke({"filename": "x", "description": "y"})
    assert result == {
        "message": "LLM returned invalid JSON",
        "error": "Failed to parse JSON response",
    }


# --- failure path: upload POST raises -------------------------------------


@pytest.mark.asyncio
async def test_create_file_upload_failure_returns_error_and_cleans_temp(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["temp_path"] = kwargs["data"].name
        raise RuntimeError("connection refused")

    mod, _llm = _arrange(
        monkeypatch,
        llm_response='{"columns": ["a"], "rows": [["1"]]}',
        post=fake_post,
    )
    result = await mod.create_file.ainvoke({"filename": "x", "description": "y"})
    # broad except returns {"message": "An error occurred", "error": str(e)}
    assert result["message"] == "An error occurred"
    assert result["error"] == "connection refused"
    # temp file still cleaned by the inner finally-block before the exception propagates
    assert not os.path.exists(captured["temp_path"])
