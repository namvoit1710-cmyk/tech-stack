import asyncio
from types import SimpleNamespace

from app.layer2_application.features import revert_file as module


class _Llm:
    def __init__(self, response: str):
        self.response = response

    async def get_chat_completion(self, system_prompt, user_prompt):
        return self.response


def test_revert_file_returns_error_when_history_missing():
    result = asyncio.run(module.revert_file("previous", "", llm_service=_Llm("{}")))

    assert result["error"] == "Missing conversation history"


def test_revert_file_without_llm_returns_safe_error():
    result = asyncio.run(
        module.revert_file("previous", "turn 1 | role=assistant | file_id=file_old")
    )

    assert result["error"] == "Failed to determine previous file version"


def test_revert_file_strips_fenced_json_and_builds_download_url():
    settings_provider = SimpleNamespace(
        get_file_download_url=lambda file_id: f"http://files/api/v1/download/{file_id}"
    )
    llm = _Llm('```json\n{"file_id": "file_old", "message": ""}\n```')

    result = asyncio.run(
        module.revert_file(
            "",
            "turn 1 | role=assistant | file_id=file_old",
            llm_service=llm,
            settings_provider=settings_provider,
        )
    )

    assert result["file_id"] == "file_old"
    assert result["download_url"].endswith("/file_old")
    assert result["message"] == "Reverted to the requested file version."


def test_revert_file_returns_llm_error_payload_message():
    llm = _Llm('{"error": "No old file", "message": "Nothing to revert"}')

    result = asyncio.run(
        module.revert_file("previous", "turn 1 | role=user", llm_service=llm)
    )

    assert result == {"error": "No old file", "message": "Nothing to revert"}


def test_revert_file_handles_missing_file_id_and_bad_json():
    missing = asyncio.run(
        module.revert_file(
            "previous", "turn 1 | role=user", llm_service=_Llm('{"message": "ok"}')
        )
    )
    bad_json = asyncio.run(
        module.revert_file(
            "previous", "turn 1 | role=user", llm_service=_Llm("not-json")
        )
    )

    assert (
        missing["error"] == "No matching old file version found in conversation history"
    )
    assert bad_json["error"] == "Failed to parse revert_file response"
