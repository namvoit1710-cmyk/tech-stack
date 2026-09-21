from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Unit under test: _revert_file_impl (layer2 feature revert_file).
# Collaborators: OpenAIService (async get_chat_completion), AppSettingsAdapter.
# Grounded in app/layer2_application/features/revert_file.py.

HISTORY = "\n".join(
    [
        "Conversation history:",
        "- turn 1 | role=user | file_id=old.csv | message=Original file.",
        "- turn 2 | role=system | file_id=new.csv | message=Done.",
        "- turn 3 | role=user | file_id=new.csv | message=Revert to turn 1.",
    ]
)


def _patch_llm(monkeypatch, response):
    from app.layer2_application.features import revert_file as mod

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)
    monkeypatch.setattr(
        mod,
        "AppSettingsAdapter",
        lambda: SimpleNamespace(FILE_URL="https://files.example"),
    )
    return mod, llm


# --- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_revert_happy_builds_download_url(monkeypatch):
    mod, _llm = _patch_llm(
        monkeypatch,
        '{"file_id": "old.csv", "message": "Reverted to the requested file version."}',
    )
    result = await mod._revert_file_impl("Revert to turn 1.", HISTORY)
    # source: download_url = f"{FILE_URL}/api/v1/download/{file_id}"
    assert result == {
        "file_id": "old.csv",
        "download_url": "https://files.example/api/v1/download/old.csv",
        "message": "Reverted to the requested file version.",
    }


# --- edge: code-fenced JSON is stripped before parsing --------------------


@pytest.mark.asyncio
async def test_revert_strips_markdown_code_fence(monkeypatch):
    fenced = '```json\n{"file_id": "old.csv", "message": "ok"}\n```'
    mod, _llm = _patch_llm(monkeypatch, fenced)
    result = await mod._revert_file_impl("undo", HISTORY)
    # source strips leading ``` line and trailing ``` line, then finds {..}
    assert result["file_id"] == "old.csv"
    assert result["message"] == "ok"


# --- invalid input: empty conversation history short-circuits -------------


@pytest.mark.asyncio
async def test_revert_missing_history_returns_error_without_calling_llm(monkeypatch):
    mod, llm = _patch_llm(monkeypatch, "{}")
    result = await mod._revert_file_impl("undo", "   ")
    # source: `if not history_prompt: return {"error": "Missing conversation history"...}`
    assert result == {
        "error": "Missing conversation history",
        "message": "Missing conversation history",
    }
    llm.get_chat_completion.assert_not_awaited()


# --- boundary: LLM returns an explicit error payload ----------------------


@pytest.mark.asyncio
async def test_revert_llm_error_payload_is_propagated(monkeypatch):
    mod, _llm = _patch_llm(
        monkeypatch,
        '{"error": "No matching old file version found in conversation history",'
        ' "message": "No matching old file version found in conversation history"}',
    )
    result = await mod._revert_file_impl("undo", HISTORY)
    # source returns the error/message dict when payload.get("error") is truthy
    assert result == {
        "error": "No matching old file version found in conversation history",
        "message": "No matching old file version found in conversation history",
    }


@pytest.mark.asyncio
async def test_revert_success_payload_without_file_id_is_error(monkeypatch):
    # Boundary: no error key, but file_id is empty -> the "not isinstance/empty" guard.
    mod, _llm = _patch_llm(monkeypatch, '{"file_id": "", "message": "hmm"}')
    result = await mod._revert_file_impl("undo", HISTORY)
    assert result == {
        "error": "No matching old file version found in conversation history",
        "message": "No matching old file version found in conversation history",
    }


# --- failure path: LLM returns unparseable text ---------------------------


@pytest.mark.asyncio
async def test_revert_unparseable_response_returns_parse_error(monkeypatch):
    # No braces at all -> text stays as-is -> json.loads raises JSONDecodeError.
    mod, _llm = _patch_llm(monkeypatch, "no json here")
    result = await mod._revert_file_impl("undo", HISTORY)
    # source JSONDecodeError branch:
    assert result == {
        "error": "Failed to parse revert_file response",
        "message": "Failed to parse revert_file response",
    }


@pytest.mark.asyncio
async def test_revert_llm_transport_error_returns_generic_failure(monkeypatch):
    # failure path: get_chat_completion raises a non-JSON error -> broad
    # `except Exception` branch: {"error": str(exc), "message": "Failed to..."}.
    from app.layer2_application.features import revert_file as mod

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(side_effect=RuntimeError("api down"))
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)
    monkeypatch.setattr(
        mod, "AppSettingsAdapter", lambda: SimpleNamespace(FILE_URL="https://f")
    )
    result = await mod._revert_file_impl("undo", HISTORY)
    assert result == {
        "error": "api down",
        "message": "Failed to determine previous file version",
    }


@pytest.mark.asyncio
async def test_revert_file_tool_wrapper_delegates_to_impl(monkeypatch):
    # Exercises the @tool wrapper (revert_file) itself, not just the _impl.
    mod, _llm = _patch_llm(monkeypatch, '{"file_id": "old.csv", "message": "ok"}')
    result = await mod.revert_file.ainvoke(
        {"revert_instruction": "undo", "conversation_history": HISTORY}
    )
    assert result["file_id"] == "old.csv"
