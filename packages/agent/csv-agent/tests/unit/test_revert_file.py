from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock


class _SettingsProvider:
    def get_file_download_url(self, file_id: str) -> str:
        return f"http://file-service/api/v1/download/{file_id}"


def test_revert_file_impl_uses_explicit_revert_instruction_and_history() -> None:
    from app.layer2_application.features import revert_file as revert_module

    llm = type("MockLLM", (), {})()
    llm.get_chat_completion = AsyncMock(
        return_value='{"file_id": "old-file.csv", "message": "Reverted to the requested file version."}'
    )

    rendered_history = "\\n".join(
        [
            "Conversation history:",
            "- turn 1 | role=user | file_id=old-file.csv | message=Original file.",
            "- turn 2 | role=system | file_id=new-file.csv | message=Done! Download it here: https://example.com/api/v1/download/new-file.csv",
            "- turn 3 | role=user | file_id=new-file.csv | message=Revert to turn 1.",
        ]
    )

    result = asyncio.run(
        revert_module._revert_file_impl(
            "Revert to turn 1.",
            rendered_history,
            llm_service=llm,
            settings_provider=_SettingsProvider(),
        )
    )

    assert result["file_id"] == "old-file.csv"
    assert result["download_url"] == "http://file-service/api/v1/download/old-file.csv"
    assert result["message"] == "Reverted to the requested file version."


def test_revert_file_impl_uses_default_instruction_when_missing() -> None:
    from app.layer2_application.features import revert_file as revert_module

    llm = type("MockLLM", (), {})()
    llm.get_chat_completion = AsyncMock(
        return_value='{"file_id": "older-file.csv", "message": "Reverted to the requested file version."}'
    )

    rendered_history = "\\n".join(
        [
            "Conversation history:",
            "- turn 1 | role=user | file_id=older-file.csv | message=Original file.",
            "- turn 2 | role=system | file_id=new-file.csv | message=Updated file.",
        ]
    )

    result = asyncio.run(
        revert_module._revert_file_impl(
            "", rendered_history, llm_service=llm, settings_provider=_SettingsProvider()
        )
    )

    assert result == {
        "file_id": "older-file.csv",
        "download_url": "http://file-service/api/v1/download/older-file.csv",
        "message": "Reverted to the requested file version.",
    }
